import os, pickle, json, traceback, threading, requests, ast, rasterio, time, asyncio
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, Response, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from services import functions, data_functions, hyd_functions, flow_functions, yml_update, era5_function
from config import PROJECT_ROOT, SOURCE_BACKEND
import geopandas as gpd, pandas as pd, numpy as np
from services.data_functions import Regnbyge as regnbyge
from shapely.geometry import box
from rio_tiler.io import Reader as _Reader
from typing import Any, cast
from rio_tiler.colormap import cmap
from rio_tiler.utils import render
from rio_tiler.errors import TileOutsideBounds

STALE_AFTER_S, CANCEL_WAIT_S  = 15 * 60, 20

router, processes = APIRouter(), {}


def _is_active(info):
    return bool(info) and info.get("status") == "running" and \
        time.time() - info.get("heartbeat", 0) < STALE_AFTER_S

@router.get("/log_tail_download/{project_name}")
async def log_tail_download(project_name: str, offset: int = Query(0),
    log_file: str = Query(""), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    log_dir = os.path.join(PROJECT_ROOT, project_name, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.normpath(os.path.join(log_dir, log_file))
    if not os.path.exists(log_path): return {"lines": [], "offset": 0, "reset": offset > 0}
    file_size = os.path.getsize(log_path)
    reset = offset > file_size
    if reset: offset = 0
    with open(log_path, "rb") as f:
        f.seek(offset)
        raw = f.read()
    cut = raw.rfind(b"\n") + 1
    data, new_offset = raw[:cut], offset + cut
    return {"lines": data.decode("utf-8", errors="replace").splitlines(), "offset": new_offset, "reset": reset}

# Check if download is running
@router.post("/check_download_status")
async def check_download_status(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    info = processes.get(f"{project_name}:{body.get('key')}")
    if info is None: return JSONResponse({"status": "idle", "message": "No download running."})
    status, message = info.get("status", "idle"), info.get("message", "")
    if status == "running" and not _is_active(info):
        status, message = "failed", "Previous download stopped responding. You can start again."
    return JSONResponse({
        "status": status, "message": message, "results": info.get("results", []),
        "cancelled": bool(info.get("cancelled")),
        "cancelling": status == "running" and bool(info.get("cancel")),
    })

@router.post("/save_client")
async def save_client(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        path = os.path.join(PROJECT_ROOT, project_name, 'credentials.json')
        if not os.path.exists(path):
            with open(path, 'w', encoding='utf-8', errors='ignore') as f:
                json.dump({}, f)
        with open(path, 'r', encoding='utf-8') as f:
            content = json.load(f)
        client_name, client_secret = body.get('clientName', ''), body.get('clientSecret', '')
        client_username, client_password = body.get('clientUsername', ''), body.get('clientPassword', '')
        content[body.get('key')] = {'client_id': client_name, 'client_secret': client_secret,
            'client_username': client_username, 'client_password': client_password}
        with open(path, 'w', encoding='utf-8', errors='ignore') as f:
            json.dump(content, f)
        return JSONResponse({'message': 'Registration information is saved successfully.'})
    except Exception as e:
        print('/save_client:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/load_client")
async def load_client(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    path = os.path.join(PROJECT_ROOT, project_name, 'credentials.json')
    temp = {'client_id': '', 'client_secret': '', 'client_username': '', 'client_password': ''}
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            content = json.load(f)
        content = content.get(body.get('key'), temp)
    else: content = temp
    return JSONResponse({'content': content})
    
@router.post("/reset_station")
async def reset_station(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        source_dir = os.path.join(PROJECT_ROOT, project_name, body.get('keyType'))
        flow_checked, level_checked, rain_checked = body.get('flow'), body.get('level'), body.get('rain')
        if flow_checked: functions.safe_remove(os.path.join(source_dir, 'flow.pkl'))
        if level_checked: functions.safe_remove(os.path.join(source_dir, 'level.pkl'))
        if rain_checked: functions.safe_remove(os.path.join(source_dir, 'rain.pkl'))
        return JSONResponse({'message': 'Station(s) reset successfully.'})
    except Exception as e:
        print('/reset_station:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/init_station")
async def init_station(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        key, client_name = body.get('key'), body.get('clientName', '')
        client_secret, client_username = body.get('clientSecret', ''), body.get('clientUserName', '')
        client_password = body.get('clientPassword', '')
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        source_dir = os.path.join(PROJECT_ROOT, project_name, body.get('keyType'))
        if not os.path.exists(source_dir): os.makedirs(source_dir)
        path = os.path.normpath(os.path.join(source_dir, f'{key}.pkl'))
        if not os.path.exists(path):
            obj = regnbyge(client_name, client_secret, client_username, client_password)
            print("Loading data from Regnbyge.no ...")
            token = obj.get_Token()
            if token is None: return JSONResponse({'status': 'error', 'message': f"Error: Could not get token."})
            df = obj.get_Station(key)
            if df.empty: return JSONResponse({'status': 'error', 'message': f"No '{key}' data available."})
            geometry = gpd.points_from_xy(df['x'], df['y'])
            station = gpd.GeoDataFrame(df, geometry=geometry, crs='EPSG:32633')
            station = station.drop(columns=['x', 'y'])
            station['mode'] = key
            if 'geoX' in station.columns: station = station.drop(columns=['geoX'])
            if 'geoY' in station.columns: station = station.drop(columns=['geoY'])
            station = station.to_crs('EPSG:4326')
            station = station[
                station['name'].notna() & station['type'].notna() &
                (station['name'] != '') & (station['type'] != '')
            ]
            station = station.dropna(subset=['name', 'type'], how='any')
            with open(path, 'wb') as f: pickle.dump(station, f)
        else:
            with open(path, 'rb') as f: station = pickle.load(f)
        if station.empty: return JSONResponse({'status': 'error', 'message': f"No '{key}' data available."})
        name = station[['name', 'type']].values.tolist()
        content = {'name': name, 'point': json.loads(station.to_json())}
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e:
        print('/init_station:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/plot_station")
async def plot_station(request: Request):
    try:
        body = await request.json()
        id, mode, client_name = body.get('id'), body.get('mode'), body.get('clientName', '')
        name, time_zone, client_password = body.get('name'), body.get('timeZone'), body.get('clientPassword', '')
        client_secret, client_username = body.get('clientSecret', ''), body.get('clientUserName', '')
        start, end, interval = body.get('startTime'), body.get('endTime'), body.get('interval')
        start_utc = functions.local_to_utc(start, time_zone)
        end_utc = functions.local_to_utc(end, time_zone)
        if start_utc >= end_utc:
            return JSONResponse({'status': 'error', 'message': "Error: 'Start time' must be earlier than 'End time'."})
        obj = regnbyge(client_name, client_secret, client_username, client_password)
        df = obj.get_Values(mode, id, start_utc, end_utc, interval)
        if df.empty:
            return JSONResponse({
                'status': 'error', 'message': f"No data available for station '{name}' between '{start}' and '{end}'."
            })
        if 'id' in df.columns: df = df.drop(columns=['id'])
        # Convert time back to local time zone
        df['timestamp'] = functions.utc_to_local(df['timestamp'], time_zone)
        content = {'columns': df.columns.tolist(), 'rows': df.values.tolist()}
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e:
        print('/plot_station:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/download_station")
async def download_station(request: Request):
    try:
        body = await request.json()
        mode, download_interval = body.get('mode'), body.get('downloadInterval')
        start, end = body.get('startTime'), body.get('endTime')
        id, time_zone = body.get('id'), body.get('timeZone')
        client_name, client_secret = body.get('clientName', ''), body.get('clientSecret', '')
        client_username, client_password = body.get('clientUserName', ''), body.get('clientPassword', '')
        start_utc = functions.local_to_utc(start, time_zone)
        end_utc = functions.local_to_utc(end, time_zone)
        if start_utc >= end_utc:
            return JSONResponse({'status': 'error', 'message': "Error: 'Start time' must be earlier than 'End time'"})
        obj = regnbyge(client_name, client_secret, client_username, client_password)
        df = obj.get_Values(mode, id, start_utc, end_utc, download_interval)
        if df.empty: 
            return JSONResponse({'status': 'error', 'message': f"No data available between '{start}' and '{end}'."})
        if 'id' in df.columns: df = df.drop(columns=['id'])
        df['timestamp'] = functions.utc_to_local(df['timestamp'], time_zone)
        csv_string = df.to_csv(index=False)
        return JSONResponse({'status': 'ok', 'content': csv_string})
    except Exception as e:
        print('/download_station:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/data_upload_gis")
async def data_upload_gis(file: UploadFile = File(...)):
    try:
        gdf = gpd.read_file(file.file)
        if gdf.crs is None or gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")
        return JSONResponse({"status": "ok", "content": json.loads(gdf.to_json())})
    except Exception as e:
        print('/data_upload_gis:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": str(e)})
    finally: await file.close()

@router.post("/download_era5")
async def download_era5(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    project_dir = os.path.join(PROJECT_ROOT, project_name)
    key = body.get("key") or "era5"
    key_process = f"{project_name}:{key}"
    required = ("api_key", "lat", "lon", "startTime", "endTime", "timeZone", "variables", "logFile")
    missing = [k for k in required if body.get(k) in (None, "", [])]
    if missing: return JSONResponse({"status": "error", "message": f"Missing fields: {', '.join(missing)}"})
    try:
        lat, lon, elevation = float(body["lat"]), float(body["lon"]), body.get("elevation")
        elevation = float(elevation) if elevation not in (None, "") else None
    except (TypeError, ValueError):
        return JSONResponse({"status": "error", "message": "Latitude, longitude and elevation must be numbers."})
    redis = request.app.state.redis
    lock = redis.lock(key_process, timeout=60, blocking_timeout=10)
    async with lock:
        if _is_active(processes.get(key_process)):
            return JSONResponse({"status": "running", "message": "Data downloading in progress."})
        now = time.time()
        processes[key_process] = {"status": "running", "message": "Preparing download...",
            "started_at": now, "heartbeat": now, "cancel": False}
        threading.Thread(
            target=data_functions.era5_downloader, 
            args=(key, body["api_key"], project_dir, processes, key_process, body["variables"], 
                lat, lon, body["startTime"], body["endTime"], body["timeZone"], body["logFile"]),
            kwargs={"method": body.get("method") or "bilinear", "elevation": elevation,
                "auto_elevation": bool(body.get("autoElevation", True))}, name=f"era5:{key_process}", daemon=True
        ).start()
    return JSONResponse({"status": "ok", "message": "Weather downloading started"})

@router.post("/cancel_download_era5")
async def cancel_download_era5(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get("projectName"), user)
    info = processes.get(f"{project_name}:{body.get('key')}")
    if info is None or not _is_active(info): return JSONResponse({"status": "idle", "message": "No download running."})
    if info.get("cancel"): return JSONResponse({"status": "ok", "message": "Already cancelling..."})
    info["cancel"], info["message"] = True, "Cancelling download..."
    return JSONResponse({"status": "ok", "message": "Cancelling download..."})

def _downloads_to_cancel(project_name, main_key):
    keys = []
    for k, info in list(processes.items()):
        if not k.startswith(f"{project_name}:") or not _is_active(info): continue
        if k == main_key or info.get("cds_request_id"): keys.append(k)
    return keys
 
async def _cancel_and_wait(keys, timeout=CANCEL_WAIT_S):
    for k in keys:
        info = processes.get(k)
        if info is not None:
            info["cancel"] = True
            info["message"] = "Cancelling download (clearing CDS queue)..."
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_is_active(processes.get(k)) for k in keys): return []
        await asyncio.sleep(0.5)
    return [k for k in keys if _is_active(processes.get(k))]

@router.post("/cds_clear_queue")
async def cds_clear_queue(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get("projectName"), user)
    api_key = (body.get("api_key") or "").strip()
    if not api_key:
        return JSONResponse({"status": "error", "message": "Please enter your ERA5 API key."})
    main_key = f"{project_name}:{body.get('key') or 'era5'}"
    to_cancel = _downloads_to_cancel(project_name, main_key)
    still_running = await _cancel_and_wait(to_cancel) if to_cancel else []
    cancelled = [k.split(":", 1)[1] for k in to_cancel if k not in still_running]
    try:
        n = await run_in_threadpool(era5_function.delete_active_jobs, api_key)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Could not clear CDS queue: {e}"})
    parts = []
    if cancelled: parts.append(f"Cancelled running download(s): {', '.join(cancelled)}.")
    if still_running:
        parts.append("Some downloads are still stopping and will end shortly: "
            f"{', '.join(k.split(':', 1)[1] for k in still_running)}.")
    parts.append(f"Removed {n} queued/running ERA5 request(s) from CDS.")
    return JSONResponse({"status": "ok", "message": " ".join(parts),
        "cancelled": cancelled, "removed": n})

@router.post("/upload_era5_csv")
async def upload_era5_csv(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        path = os.path.join(PROJECT_ROOT, project_name, body.get('fileName'))
        if not path.endswith(".csv"):
            return JSONResponse({"status": "error", "message": "Invalid file name."})
        if not os.path.exists(path): 
            return JSONResponse({'status': 'error', 'message': 'No data found.\nPlease download data first.'})
        df = pd.read_csv(path)
        df['Time'] = pd.to_datetime(df['Time']).dt.strftime('%Y-%m-%d %H:%M:%S')
        columns = [era5_function.var_revert[x] for x in df.columns]
        content = df.astype(object).where(pd.notna(df), None).values.tolist()
        functions.safe_remove(path)
        return JSONResponse({'status': 'ok', 'columns': columns, 'content': content})
    except Exception as e:
        print('/upload_era5_csv:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/save_era5")
async def save_era5(request: Request):
    try:
        body = await request.json()
        data = body.get('data')
        df = pd.DataFrame(data['rows'], columns=data['columns'])
        df = df.dropna(subset=['Time'], how='any')
        df = df.replace([np.inf, -np.inf], np.nan)
        csv_string = df.to_csv(index=False)
        return JSONResponse({'status': 'ok', 'message': 'Saved successfully.', 'content': csv_string})
    except Exception as e:
        print('/save_era5:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/stations_met")
async def stations_met(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        key, client_name = body.get('key'), body.get('clientName', '')
        url = f"{os.getenv('MET_ProstAPI_URL')}/sources/v0.jsonld"
        redis, key_process = request.app.state.redis, f"{project_name}:met_stations"
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            # Check if process already running
            if key_process in processes and processes[key_process]["status"] == "running":
                return JSONResponse({"status": "running", "message": 'Data downloading in progress.'})
            params = {"types": "SensorSystem"}
            response = requests.get(url, params=params, auth=(client_name, ""))
            response.raise_for_status()
            data = response.json()
            df = pd.DataFrame(data["data"]).drop(columns=['@type'])
            df['geometry'] = df['geometry'].apply(lambda x: x['coordinates'] if isinstance(x, dict) and 'coordinates' in x else None)
            gdf = gpd.GeoDataFrame(
                df, geometry=gpd.points_from_xy(
                    df['geometry'].apply(lambda x: x[0] if x else None), df['geometry'].apply(lambda x: x[1] if x else None)
                ), crs='EPSG:4326'
            )
            gdf = gdf.dropna(subset=['geometry'], how='any')
        if gdf.empty: return JSONResponse({'status': 'error', 'message': f"No '{key}' data available."})
        content = {'length': len(gdf), 'point': functions.clean_json_value(json.loads(gdf.to_json()))}
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e:
        print('/stations_met:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/met_parameters")
async def met_parameters(request: Request):
    try:
        body = await request.json()
        url = f"{os.getenv('MET_ProstAPI_URL')}/observations/availableTimeSeries/v0.jsonld"
        params, api_key = {"sources": ",".join(body.get('ids')) }, body.get('apiKey')
        redis, key_process = request.app.state.redis, f"met_parameters"
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            response = requests.get(url, params=params, auth=(api_key, ""), timeout=30)
            response.raise_for_status()
            data = response.json()
            df, vars = pd.DataFrame(data["data"]), set()
            for i in df['elementId'].values:
                vars.add(i)
        if df.empty: return JSONResponse({'status': 'ok', 'content': []})
        df = df.loc[df['elementId'].isin(vars)]
        df = df[['elementId', 'unit']].dropna().drop_duplicates()
        content = df.values.tolist()
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e:
        print('/met_parameters:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/upload_weather_csv")
async def upload_weather_csv(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        path = os.path.join(PROJECT_ROOT, project_name, body.get('fileName'))
        if not os.path.exists(path): 
            return JSONResponse({'status': 'error', 'message': 'No data found.\nPlease download data first.'})
        df = pd.read_csv(path)
        df['Time'] = pd.to_datetime(df['Time']).dt.strftime('%Y-%m-%d %H:%M:%S')
        if body.get('ignore_stationName'): df = df.drop(columns=['stationId'])
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.astype(object).where(pd.notna(df), None)
        columns, content = df.columns.tolist(), df.values.tolist()
        functions.safe_remove(path)
        return JSONResponse({'status': 'ok', 'columns': columns, 'content': content})
    except Exception as e:
        print('/upload_weather_csv:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/save_weather_csv")
async def save_weather_csv(request: Request):
    try:
        body = await request.json()
        ids, data = body.get('ids'), body.get('data')
        df = pd.DataFrame(data['rows'], columns=data['columns'])
        df = df.dropna(subset=['Time'], how='any')
        df = df.replace([np.inf, -np.inf], np.nan)
        if 'stationId' not in df.columns:
            return JSONResponse({'status': 'error', 'message': 'Column "stationId" not found.'})
        df = df.dropna(subset=['stationId'], how='any')
        stations = {x[0]: (x[1], x[2]) for x in ids}
        # Create ZIP in memory
        zip_buffer = BytesIO()
        with ZipFile(zip_buffer, mode='w', compression=ZIP_DEFLATED) as zip_file:
            # Group by station
            for station_id, station_df in df.groupby('stationId', sort=True):
                # Convert DataFrame -> CSV string
                station_df = station_df.drop(columns=['stationId'])
                csv_string = station_df.to_csv(index=False)
                station_info = stations.get(station_id)
                if station_info is None: continue
                csv_string = f"{station_id},{station_info[0]},{station_info[1]}\n{csv_string}"
                file_name = f"{station_id}.csv"
                zip_file.writestr(file_name, csv_string)
        # Move pointer to beginning
        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer, media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="weather_data.zip"'}
        )
    except Exception as e:
        print('/save_weather_csv:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/nve_parameters")
async def nve_parameters(request: Request):
    try:
        body = await request.json()
        url_param, ids = f"{os.getenv('NVE_URL')}/Parameters", body.get('ids')
        headers = {"X-API-Key": body.get('apiKey'), "Accept": "application/json"}
        redis, key_process = request.app.state.redis, f"nve_parameters"
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            response = requests.get(url_param, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            df, vars = pd.DataFrame(data["data"]), set()
            url_series = f"{os.getenv('NVE_URL')}/Series"
            for id in ids:
                params = {'StationId': id}
                response = requests.get(url_series, headers=headers, params=params, timeout=30)
                response.raise_for_status()
                data_series = response.json()
                records = data_series.get('data', [])
                df_series = pd.DataFrame(records)
                for i in df_series['parameter'].values:
                    vars.add(i)
        if df.empty: return JSONResponse({'status': 'ok', 'content': []})
        df = df.loc[df['parameter'].isin(vars)]
        return JSONResponse({'status': 'ok', 'content': df.values.tolist()})
    except Exception as e:
        print('/nve_parameters:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/stations_nve")
async def stations_nve(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        key, client_name = body.get('key'), body.get('clientName', '')
        url = f"{os.getenv('NVE_URL')}/Stations"
        redis, key_process = request.app.state.redis, f"{project_name}:nve_stations"
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            # Check if process already running
            if key_process in processes and processes[key_process]["status"] == "running":
                return JSONResponse({"status": "running", "message": 'Data downloading in progress.'})
            headers = {"X-API-Key": client_name, "Accept": "application/json"}
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            df = pd.DataFrame(data["data"])
            gdf = gpd.GeoDataFrame(
                df, geometry=gpd.points_from_xy(x=df['longitude'], y=df['latitude']), crs='EPSG:4326'
            )
            gdf = gdf.dropna(subset=['geometry'], how='any')
        if gdf.empty: return JSONResponse({'status': 'error', 'message': f"No '{key}' data available."})
        content = {'length': len(gdf), 'point': functions.clean_json_value(json.loads(gdf.to_json()))}
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e:
        print('/stations_nve:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/download_nve")
async def download_nve(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    dir, api_key, key = os.path.join(PROJECT_ROOT, project_name), body.get('apiKey'), body.get('key')
    start, end, time_zone = body.get('startTime'), body.get('endTime'), body.get('timeZone')
    variables, ids, columns = body.get('variables'), body.get('ids'), body.get('columns')
    url, interval = f"{os.getenv('NVE_URL')}/Observations", int(body.get('interval'))
    redis, key_process = request.app.state.redis, f"{project_name}:{key}"
    lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
    async with lock:
        if _is_active(processes.get(key_process)):
            return JSONResponse({"status": "running", "message": "Data downloading in progress."})
        now = time.time()
        processes[key_process] = {"status": "running", "message": "Preparing NVE data download...",
            "started_at": now, "heartbeat": now}
        threading.Thread(
            target=data_functions.nve_downloader, 
            args=(key, api_key, url, dir, processes, key_process, ids, 
                columns, variables, interval, start, end, time_zone, body.get('logFile')
            ), daemon=True
        ).start()
    return JSONResponse({"status": "ok", "message": "Weather downloading started"})

@router.post("/download_met")
async def download_met(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    dir, api_key = os.path.join(PROJECT_ROOT, project_name), body.get('api_key')
    start, end, time_zone = body.get('startTime'), body.get('endTime'), body.get('timeZone')
    variables, ids, columns = body.get('variables'), body.get('ids'), body.get('columns')
    url, key = f"{os.getenv('MET_ProstAPI_URL')}/observations/v0.jsonld", body.get('key')
    redis, key_process = request.app.state.redis, f"{project_name}:{key}"
    lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
    async with lock:
        if _is_active(processes.get(key_process)):
            return JSONResponse({"status": "running", "message": "Data downloading in progress."})
        now = time.time()
        processes[key_process] = {"status": "running", "message": "Preparing download...",
            "started_at": now, "heartbeat": now}
        threading.Thread(
            target=data_functions.met_downloader, 
            args=(key, api_key, url, dir, processes, key_process, ids, columns, 
                variables, start, end, time_zone, body.get('logFile')
            ), daemon=True
        ).start()
    return JSONResponse({"status": "ok", "message": "Weather downloading started"})

@router.post("/start_meteo")
async def start_meteo(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, start, end = request.app.state.redis, body.get('start'), body.get('end')
        lat, lon, log_file = body.get('lat'), body.get('lon'), body.get('logFile')
        key, data_source, api_key = body.get('key'), body.get('dataService'), body.get('apiKey')
        key_process, time_zone = f"{project_name}:{key}", body.get('timeZone')
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            # Check if process already running
            if key_process in processes and processes[key_process]["status"] == "running":
                return JSONResponse({"status": "running", "message": 'Data downloading is in progress.'})
            processes[key_process] = {"status": "running", "message": "Preparing download meteo..."}
            if key == 'meteo': target = hyd_functions.meteo_downloader
            elif key == 'wind': target = hyd_functions.wind_downloader
            else: return JSONResponse({"status": "error", "message": f"Unknown download type: '{key}'."})
            threading.Thread(
                target=target, args=(
                    project_name, api_key, data_source, processes, key_process, 
                    lat, lon, start, end, time_zone, key, log_file
                ), daemon=True
            ).start()
        return JSONResponse({"status": "ok", "message": "Weather downloading started"})
    except Exception as e:
        print('/start_meteo:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": 'error', "message": f"Error: {str(e)}"})

@router.post("/download_dem")
async def download_dem(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        directory = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, body.get('folder')))
        os.makedirs(directory, exist_ok=True)
        resolution, key = int(body.get('resolution')), body.get('key')
        top_left = ast.literal_eval(body.get('topLeft'))
        bottom_right = ast.literal_eval(body.get('bottomRight'))
        wgs84_crs, crs = "EPSG:4326", "EPSG:25833"
        lat1, lon1 = map(float, top_left)
        lat2, lon2 = map(float, bottom_right)
        xmin, xmax = sorted([lon1, lon2])
        ymin, ymax = sorted([lat1, lat2])
        gdf_WGS84 = gpd.GeoDataFrame(geometry=[box(xmin, ymin, xmax, ymax)], crs=wgs84_crs)
        target_crs = gdf_WGS84.estimate_utm_crs()
        if target_crs is None:
            return JSONResponse({"status": 'error', "message": "Cannot estimate a suitable UTM CRS for the study area."})
        gdf_WCS = gdf_WGS84.to_crs(crs)
        redis, key_process = request.app.state.redis, f"{project_name}:{key}"
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            content = data_functions.dem_downloader(directory, key, resolution, gdf_WCS, target_crs)
            return JSONResponse(content)
    except Exception as e:
        print('/download_dem:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": 'error', "message": f"Error: {str(e)}"})

@router.post("/upload_terrain")
async def upload_terrain(projectName: str = Form(...), folder: str = Form(...), 
    fileName: str = Form(...), user=Depends(functions.basic_auth)):
    try:
        project_name, _ = functions.project_definer(projectName, user)
        dem_dir = os.path.join(PROJECT_ROOT, project_name, folder)
        if not os.path.exists(dem_dir): os.makedirs(dem_dir, exist_ok=True)
        terrain_path = os.path.normpath(os.path.join(dem_dir, f"{fileName}.tif"))
        content = data_functions.prepare_terrain(project_name, terrain_path)
        return JSONResponse(content)
    except Exception as e:
        print('/upload_terrain:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.get("/{project_name}/{file_path:path}/{z}/{x}/{y}.png")
def hyd_terrain_tiles(project_name: str, file_path: str, z: int, x: int, y: int):
    cog_path = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, file_path))
    project_root = os.path.abspath(os.path.join(PROJECT_ROOT, project_name))
    cog_path = os.path.abspath(cog_path)
    if os.path.commonpath([project_root, cog_path]) != project_root:
        return JSONResponse({"status": "error", "message": "Invalid file path."}, status_code=400)
    if not os.path.isfile(cog_path):
        return JSONResponse({"status": "error", "message": f"File not found: {cog_path}"}, status_code=404)
    try:
        Reader = cast(Any, _Reader)
        with Reader(cog_path) as src:
            try: tile = src.tile(x, y, z)
            except TileOutsideBounds:
                empty_data = np.zeros((1, 256, 256), dtype=np.uint8)
                empty_mask = np.zeros((256, 256), dtype=np.uint8)
                image = render(empty_data, mask=empty_mask)
                return Response(content=image, media_type="image/png")
            image = render(tile.data, mask=tile.mask, img_format="png", colormap=cmap.get("terrain"))
        return Response(content=image, media_type="image/png")
    except Exception as e:
        print('/hyd_terrain_tiles:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/hyd_stream_upload")
async def hyd_stream_upload(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        folder, file_name, threshold = body.get('folder'), body.get('fileName'), int(body.get('threshold'))
        content = data_functions.stream_detector(project_name, folder, file_name, threshold)
        return JSONResponse(content)
    except Exception as e:
        print('/hyd_stream_upload:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/start_download_soil")
async def start_download_soil(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    flow_name, water_area, soil_source = body.get('flowName'), body.get('waterArea'), body.get('soilSource')
    flow_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "flows", flow_name))
    terrain_path = os.path.normpath(os.path.join(flow_dir, 'raw', 'dtm_raw.tif'))
    if not os.path.exists(terrain_path):
        return JSONResponse({'status': 'error', 'message': "Cannot find terrain data in the scenario.\nProcess terrain first."})
    catchment_path = os.path.normpath(os.path.join(flow_dir, "catchment", 'catchment.geojson'))
    if not os.path.exists(catchment_path):
        return JSONResponse({'status': 'error', 'message': "Cannot find catchment data in the scenario.\nUpload terrain and define catchment first."})
    redis, key_process = request.app.state.redis, f"{project_name}:{body.get('key')}"
    lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
    async with lock:
        if _is_active(processes.get(key_process)):
            return JSONResponse({"status": "running", "message": "Soil downloading in progress."})
        now = time.time()
        processes[key_process] = {"status": "running", "message": "Preparing soil data download ...",
            "started_at": now, "heartbeat": now}
        threading.Thread(
            target=flow_functions.soil_downloader, 
            args=(project_name, processes, key_process, flow_name, 
                soil_source, water_area, terrain_path, catchment_path, body.get('logFile')
            ), daemon=True
        ).start()
    return JSONResponse({'status': 'ok', 'message': "Soil downloading started."})

@router.post("/start_download_weather")
async def start_download_weather(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    redis, key_process = request.app.state.redis, f"{project_name}:{body.get('key')}"
    lock = redis.lock(key_process, timeout=60, blocking_timeout=10)
    async with lock:
        if _is_active(processes.get(key_process)):
            return JSONResponse({"status": "running", "message": "Data downloading in progress."})
        now = time.time()
        processes[key_process] = {"status": "running", "message": "Preparing weather data download...",
            "started_at": now, "heartbeat": now, "cancel": False}
        start, end, flow_name = body.get('start'), body.get('end'), body.get('flowName')
        api_key, time_zone, log_file = body.get('apiKey'), body.get('timeZone'), body.get('logFile')
        threading.Thread(
            target=flow_functions.weather_downloader, 
            args=(project_name, flow_name, processes, key_process, api_key, start, end, time_zone, log_file), daemon=True
        ).start()
    return JSONResponse({"status": "ok", "message": "Weather downloading started."})

@router.post("/cancel_download_weather")
async def cancel_download_weather(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get("projectName"), user)
    info = processes.get(f"{project_name}:{body.get('key')}")
    if info is None or not _is_active(info):
        return JSONResponse({"status": "idle", "message": "No download running."})
    info["cancel"] = True
    return JSONResponse({"status": "ok", "message": "Cancelling... (takes effect within a few seconds)"})

@router.post("/wflow_model")
async def wflow_model(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    key, flow_name, wflow_model = body.get('key'), body.get('flowName'), 'wflow_model'
    flow_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "flows", flow_name))
    lib_path = os.path.normpath(os.path.join(SOURCE_BACKEND, 'flow_samples', 'config.yml'))
    des_path = os.path.normpath(os.path.join(flow_dir, 'config.yml'))
    wflow_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "flows", wflow_model))
    try:
        redis, key_process = request.app.state.redis, f"{project_name}:{body.get('keyChecker')}"
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            if key == "check":
                if _is_active(processes.get(key_process)):
                    return JSONResponse({"status": "running", "message": "Checking inputs for Wflow is in progress."})
                now = time.time()
                processes[key_process] = {"status": "running", "message": "Checking inputs for Wflow...",
                    "started_at": now, "heartbeat": now}
                thread = threading.Thread(
                    target=flow_functions.wflow_check, 
                    args=(project_name, processes, key_process, flow_name, body.get('logFile')), daemon=True
                )
                thread.start(); thread.join()
                result = yml_update.YML_updater(lib_path, wflow_dir, des_path)
                if result is None: 
                    point_path = os.path.join(flow_dir, "catchment", "outlet.geojson")
                    if os.path.exists(point_path):
                        gdf = gpd.read_file(point_path)
                        lat, lon = gdf.geometry.y[0], gdf.geometry.x[0]
                    else: lat, lon = '', ''
                    return JSONResponse({"status": "ok", 'content': {'lat_lon': [lat, lon]}})
                else: return JSONResponse({"status": "error", "message": f"Error when updating YML file:\n\n{result}"})
            elif key == "run":
                if _is_active(processes.get(key_process)):
                    return JSONResponse({"status": "running", "message": "Running Wflow model is in progress."})
                now = time.time()
                processes[key_process] = {"status": "running", "message": "Running Wflow model...",
                    "started_at": now, "heartbeat": now}
                step, start, end = int(body.get('step')), body.get('start'), body.get('end')
                params_input, params_output = body.get('params_input'), body.get('params_output')
                lat, lon, time_zone = float(body.get('lat')), float(body.get('lon')), body.get('timeZone')
                df = pd.DataFrame({'id': ['outlet'], 'lat': [lat], 'lon': [lon]})
                df = df[['id','lat','lon']]
                df['id'] = df['id'].astype(str)
                data_lib = [os.path.normpath(des_path)]
                lulc_fn, lulc_mapping, lai_fn = 'corine', 'corine_mapping', 'lai_corine'
                strord_path = os.path.normpath(os.path.join(flow_dir, 'hydro', "strord.tif"))
                with rasterio.open(strord_path) as strord_src:
                    resolution, src_crs = int(abs(strord_src.transform.a)), strord_src.crs
                soil_layers = [50, 100, 150, 300, 400, 600]
                threading.Thread(target=flow_functions.wflow_run,
                    args=(project_name, processes, key_process, flow_name, wflow_model, df, src_crs, 
                        start, end, step, time_zone, data_lib, resolution, soil_layers, 
                        params_input, params_output, lulc_fn, lulc_mapping, lai_fn, body.get('logFile')
                    ), daemon=False
                ).start()
                return JSONResponse({"status": "ok", "message": "Model run started."})
    except Exception as e:
        print('/wflow_model:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})
