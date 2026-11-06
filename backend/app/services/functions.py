import os, json, chardet, asyncio, stat, time, re, shapely
import shutil, base64, signal, subprocess, gc
from config import ALLOWED_USERS
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi import Depends, HTTPException, status
from redis.asyncio.lock import Lock
from scipy.spatial import KDTree
from scipy.ndimage import distance_transform_edt, gaussian_filter
from config import PROJECT_ROOT
from uuid import uuid4
from shapely.geometry import Polygon, MultiPolygon
import numpy as np, xarray as xr, pandas as pd
import geopandas as gpd, dask.array as da
from geopandas.array import GeometryArray
from services import constants
from zoneinfo import available_timezones, ZoneInfo
from functools import lru_cache
from datetime import datetime, timezone
from typing import Sequence, overload, Union


_SKIP_PREFIXES = ("posix/", "right/", "SystemV/", "US/", "Etc/")
_SKIP_EXACT = {
    "Factory", "localtime", "GMT", "GMT+0", "GMT-0", "GMT0",
    "Greenwich", "Universal", "UCT", "Zulu",
}
@lru_cache(maxsize=1)
def _all_timezones() -> list[str]:
    return [
        name for name in sorted(available_timezones())
        if not name.startswith(_SKIP_PREFIXES) and name not in _SKIP_EXACT
    ]
_ALL_TZ = sorted(_all_timezones())

variablesNames, units = constants.variablesNames, constants.units


def encoding_detect(file_path: str) -> str:
    encoding = 'utf-8'
    if not os.path.exists(file_path) or not os.path.isfile(file_path): return encoding
    with open(file_path, 'rb') as f:
        raw_data = f.read()
        result = chardet.detect(raw_data)
        detected = result.get('encoding')
        if detected: encoding = detected
    return encoding

USERS = json.load(open(ALLOWED_USERS, "r", encoding=encoding_detect(ALLOWED_USERS)))

def encode_array(arr: np.ndarray) -> str:
    arr = arr.astype(np.float32)
    return base64.b64encode(arr.tobytes()).decode()

def decode_array(b64_str: str, shape, dtype=np.float32) -> np.ndarray:
    arr = np.frombuffer(base64.b64decode(b64_str), dtype=dtype)
    return arr.reshape(shape)

def basic_auth(credentials: HTTPBasicCredentials=Depends(HTTPBasic())):
    username, password = credentials.username, credentials.password
    if username not in USERS or USERS[username] != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authorized", headers={"WWW-Authenticate": "Basic"}
        )
    return username

def project_definer(old_name, username='admin'):
    new_name = f'{username}/{old_name}' if username!='admin' else 'demo'
    name_id = f'{new_name}/{uuid4()}'
    if old_name == '': new_name = new_name.rstrip('/')
    return new_name, name_id

def project_reader(user_name, project_name):
    output_dir = os.path.join(PROJECT_ROOT, user_name, project_name, 'output')
    hyd_dir, waq_dir = os.path.join(output_dir, 'HYD'), os.path.join(output_dir, 'WAQ')
    hyd_files = [f for f in os.listdir(hyd_dir) if f.endswith(".zarr")]
    current_params = sorted(hyd_files, key=lambda x: not x.endswith('_his.zarr'))
    current_params.extend(['', '']) # Make sure there are at least 2 positions
    if not current_params[0] or not current_params[1]:
        raise ValueError(
            f"Project '{project_name}' does not contain enough HYD configuration files."
        )
    config_path = os.path.join(output_dir, 'config', 'config.json')
    waq_name, waq_model = '', ''
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding=encoding_detect(config_path)) as f:
            config = json.load(f)
        waq_model, waq_name = config.get("model_type", ''), config.get("model_name", '')
        if waq_name != '':
            current_params[2], current_params[3] = f"{waq_name}_his.zarr", f"{waq_name}_map.zarr"
        else: current_params[2], current_params[3] = '', ''
    else:
        if os.path.exists(waq_dir):
            waq_files = [f for f in os.listdir(waq_dir) if f.endswith(".zarr")]
            waq_names = list(dict.fromkeys(file.rsplit("_", 1)[0] for file in waq_files))
            if len(waq_names) > 0:
                waq_name = waq_names[0]
                temp_path = os.path.join(waq_dir, f"{waq_name}.json")
                if os.path.exists(temp_path):
                    with open(temp_path, 'r', encoding=encoding_detect(temp_path)) as f:
                        config = json.load(f)
                    waq_model = config.get("model_type", '')
                temp_files = [f for f in waq_files if waq_name in f]
                temp_files = sorted(temp_files, key=lambda x: not x.endswith('_his.zarr'))
                current_params[2:4] = temp_files
    return {
        "current_project": project_name, "waq_name": waq_name,
        "current_params": current_params, "waq_model": waq_model
    }

def time_zone_get(time_zone:str, count:int):
    result, n = [], 0
    q_lower = time_zone.strip().lower()
    if not q_lower: return _ALL_TZ[:count]
    for name in _ALL_TZ:
        if q_lower in name.lower():
            result.append(name)
            n += 1
            if n > count: break
    return sorted(result)

@overload
def local_to_utc(local_time: pd.Series, tz_name: str) -> pd.Series: ...
@overload
def local_to_utc(local_time: str | datetime | pd.Timestamp, tz_name: str) -> datetime: ...
def local_to_utc(local_time, tz_name: str) -> datetime | pd.Series | pd.Timestamp:
    tz = ZoneInfo(tz_name)
    # Series
    if isinstance(local_time, pd.Series):
        s = local_time.copy()
        if not pd.api.types.is_datetime64_any_dtype(s):
            s = pd.to_datetime(s, errors="coerce")
        if s.dt.tz is None: aware = s.dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
        else: aware = s.dt.tz_convert(tz)
        return aware.dt.tz_convert("UTC")
    # Scalar
    if isinstance(local_time, str): local = datetime.fromisoformat(local_time)
    else: local = local_time
    if local.tzinfo is None: local = local.replace(tzinfo=tz)
    else: local = local.astimezone(tz)
    return local.astimezone(timezone.utc)

@overload
def utc_to_local(utc_time: pd.Series, tz_name: str, fmt: str = ...) -> list[str]: ...
@overload
def utc_to_local(utc_time: pd.DatetimeIndex, tz_name: str, fmt: str = ...) -> list[str]: ...
@overload
def utc_to_local(utc_time: str | int | float | datetime, tz_name: str, fmt: str = ...) -> str: ...
@overload
def utc_to_local(utc_time: pd.Index, tz_name: str, fmt: str = ...) -> list[str]: ...
def utc_to_local(utc_time, tz_name: str, fmt: str="%Y-%m-%d %H:%M:%S") -> str | list[str]:
    if isinstance(utc_time, pd.Series):
        if utc_time.empty: return []
        if pd.api.types.is_numeric_dtype(utc_time):
            s = pd.to_datetime(utc_time, unit="s", utc=True)
        else: s = pd.to_datetime(utc_time, utc=True)
        return s.dt.tz_convert(tz_name).dt.strftime(fmt).tolist()
    if isinstance(utc_time, pd.DatetimeIndex):
        if utc_time.empty: return []
        idx = utc_time
        if idx.tz is None: idx = idx.tz_localize("UTC")
        else: idx = idx.tz_convert("UTC")
        return idx.tz_convert(tz_name).strftime(fmt).tolist()
    if isinstance(utc_time, (int, float)):
        ts = pd.Timestamp(utc_time, unit="s", tz="UTC")
    else: 
        ts = pd.Timestamp(utc_time)
        if ts.tzinfo is None: ts = ts.tz_localize("UTC")
        else: ts = ts.tz_convert("UTC")
    return ts.tz_convert(tz_name).strftime(fmt)

def _on_rm_error(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception: pass

def replace_dir(src, dst, retries=10, delay=1.0):
    last_err = None
    for _ in range(retries):
        try:
            safe_remove(dst, retries=1, delay=0)
            os.rename(src, dst)
            return
        except Exception as e:
            last_err = e
            gc.collect()
            time.sleep(delay)
    try:
        safe_remove(dst)
        shutil.copytree(src, dst)
        safe_remove(src)
    except Exception as e:
        raise RuntimeError(f"Cannot replace '{dst}': {e}") from last_err
    
def safe_remove(path, retries=10, delay=1):
    if not os.path.exists(path): return
    last_err = None
    for _ in range(retries):
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, onerror=_on_rm_error)
            else:
                os.chmod(path, stat.S_IWRITE)  # clear read-only
                os.remove(path)
            return
        except FileNotFoundError: return
        except (PermissionError, OSError) as e:
            last_err = e
            time.sleep(delay)
    raise Exception(f"Cannot delete path: {path}. Last error: {last_err}")

async def delete_process(processes, process_key, delay):
    await asyncio.sleep(delay)
    processes.pop(process_key, None)

def numberFormatter(arr: np.ndarray, decimals: int=2) -> np.ndarray:
    try:
        arr = np.asarray(arr, dtype=float)
        result = np.empty(arr.shape, dtype=object)
        finite_mask = np.isfinite(arr)
        abs_arr = np.abs(arr)
        # Make a mask for large numbers
        large_mask = finite_mask & (abs_arr >= 1)
        result[large_mask] = np.round(arr[large_mask], decimals)
        # Make a mask for small numbers
        small_mask = finite_mask & (abs_arr < 1) & (arr != 0)
        fmt = f"%.{decimals}e"
        result[small_mask] = [float(fmt % v) for v in arr[small_mask]]
        # Make a mask for zero
        zero_mask = finite_mask & (arr == 0)
        result[zero_mask] = 0.0
        # NaN -> None
        nan_mask = ~finite_mask
        result[nan_mask] = None
        return np.reshape(result, arr.shape)
    except: return arr

def seconds_datetime(total_seconds: int) -> tuple:
    total_seconds = int(round(float(total_seconds)))
    days = total_seconds // 86400
    remainder = total_seconds % 86400
    hours = remainder // 3600
    minutes = (remainder % 3600) // 60
    seconds = remainder % 60
    return days, f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def parse_float(s: str) -> float:
    return float(s.strip().lower().replace('d', 'e'))

async def auto_extend(lock: Lock, interval: int = 10):
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                if not await lock.locked(): break
            except Exception: break
            try: await lock.extend(30)
            except Exception: break
    except asyncio.CancelledError: pass

def remove_readonly(func, path, excinfo):
    # Change the readonly bit, but not the file contents
    os.chmod(path, stat.S_IWRITE)
    func(path)

def append_log(log_path, text):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding=encoding_detect(log_path), errors="replace") as f:
        f.write(text.strip() + "\n")
        f.flush()

def interpolation_Z(grid_net: gpd.GeoDataFrame, x_coords: np.ndarray, y_coords: np.ndarray,
    z_values: np.ndarray, n_neighbors: int=2, geo_type: str='polygon') -> np.ndarray:
    gdf_known = gpd.GeoDataFrame(geometry=gpd.points_from_xy(x_coords, y_coords), crs = grid_net.crs)
    gdf_known = gdf_known.to_crs(gdf_known.estimate_utm_crs())
    gdf_points = grid_net.copy().to_crs(grid_net.estimate_utm_crs())
    if geo_type == 'polygon': gdf_points['geometry'] = gdf_points['geometry'].centroid
    tree = KDTree(list(zip(gdf_known['geometry'].x, gdf_known['geometry'].y)))
    dists, idx = tree.query(list(zip(gdf_points['geometry'].x, gdf_points['geometry'].y)), k = n_neighbors)
    weight = 1 / (dists + 1e-10)**2
    value = np.sum(weight * z_values[idx], axis=1)/np.sum(weight, axis=1)
    return numberFormatter(value)

def unstructuredGridCreator(data_map: xr.Dataset) -> gpd.GeoDataFrame:
    # Use dask array to speed up, keep lazy-load
    if 'mesh2d_node_x' in data_map and 'mesh2d_node_y' in data_map:
        node_x = data_map['mesh2d_node_x'].data
        node_y = data_map['mesh2d_node_y'].data
        face_nodes = data_map['mesh2d_face_nodes'].data
    elif 'NetNode_x' in data_map and 'NetNode_y' in data_map:
        node_x = data_map['NetNode_x'].data
        node_y = data_map['NetNode_y'].data
        face_nodes = data_map['NetElemNode'].data
    else: return gpd.GeoDataFrame()
    coords = da.stack([node_x, node_y], axis=1)
    faces = xr.where(np.isnan(face_nodes), 0, face_nodes).astype(int)-1
    counts = da.sum(faces != -1, axis=1)
    if hasattr(coords, 'compute'): coords = coords.compute()
    if hasattr(faces, 'compute'): faces = faces.compute()
    if hasattr(counts, 'compute'): counts = counts.compute()
    # Compute to create polygons
    polygons = [
        shapely.geometry.Polygon(coords[face[:count]]) 
        for face, count in zip(faces, counts)
    ]
    # Check coordinate reference system
    if 'projected_coordinate_system' in data_map:
        crs_code = data_map['projected_coordinate_system'].attrs.get('EPSG_code')
        # Convert to WGS84 if not already
        grid = gpd.GeoDataFrame(geometry=polygons, crs=crs_code).to_crs(epsg=4326)
    elif 'crs' in data_map:
        crs_wkt = data_map['crs'].attrs.get('crs_wkt')
        if crs_wkt: 
            grid = gpd.GeoDataFrame(geometry=polygons, crs=crs_wkt).to_crs(epsg=4326)
        elif 'EPSG_code' in data_map['crs'].attrs:
            grid = gpd.GeoDataFrame(geometry=polygons, crs=data_map['crs'].attrs['EPSG_code']).to_crs(epsg=4326)
        else: grid = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
    else: grid = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
    return grid

def nodes_from_grid(grid: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    points = []
    for geometry in grid.geometry:
        if isinstance(geometry, Polygon): points.extend(geometry.exterior.coords)
        elif isinstance(geometry, MultiPolygon):
            for polygon in geometry.geoms:
                points.extend(polygon.exterior.coords)
    unique_points = list(set(points))
    points = gpd.GeoDataFrame(
        geometry=[shapely.geometry.Point(x, y) for x, y in unique_points], crs=grid.crs
    )
    return points

def fileWriter(template_path: str, params: dict) -> str:
    # Open the file and read its contents
    with open(template_path, 'r', encoding=encoding_detect(template_path)) as file:
        file_content = file.read()
    # Replace placeholders with actual values
    for key, value in params.items():
        file_content = file_content.replace(f'{{{key}}}', str(value))
    # Adjust the structure
    lines, result = [], []
    for line in file_content.split('\n'):
        line_new = line.strip()
        if '#' in line_new or line_new.startswith('#'):
            if line.startswith('#'):
                lines.append((line, "", ""))
                continue
            elif line_new.startswith('#') and not line.startswith('#'):
                lines.append(("", "", line_new))
                continue
            temp, right = line_new.split('#', 1)
            temp = temp.split('=')
            if len(temp) == 0: left = middle = ''
            elif len(temp) == 1: left = temp[0].strip(); middle = ''
            else: left, middle = temp[0].strip(), temp[1].strip()
            lines.append((left.strip(), "= " + middle.strip(), '  # ' + right.strip()))
        else:
            temp = line_new.split('=')
            if len(temp) <= 1: left = line_new; middle = right = ''
            else: left = temp[0].strip(); middle = '= ' + temp[1].strip(); right = ''
            lines.append((left.strip(), middle.strip(), right))
    max_left = max(len(left) for left, _, _ in lines if not left.startswith('#'))
    max_middle = max(len(middle) for _, middle, _ in lines)
    for left, middle, right in lines:
        if left != '' and middle == right == '':
            result.append(left.ljust(max_left))
        else:
            result.append(left.ljust(max_left) + middle.ljust(max_middle) + right)
    result = "\n".join(result)
    return result

def contentWriter(project_name: str, filename: str, 
    data: list, time_zone: str, content: str) -> tuple:
    try:
        path = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "input"))
        # Write weather.tim file
        tim_path = os.path.normpath(os.path.join(path, filename))
        with open(tim_path, 'w', encoding=encoding_detect(tim_path)) as f:
            for row in data:
                row[0] = int(local_to_utc(row[0], time_zone).timestamp()/60.0)
                temp = '  '.join([str(r) for r in row])
                f.write(f"{temp}\n")
        # Add weather data to FlowFM.ext file
        ext_path = os.path.normpath(os.path.join(path, "FlowFM.ext"))
        if os.path.exists(ext_path):
            with open(ext_path, 'r', encoding=encoding_detect(ext_path)) as f:
                update_content = f.read()
            parts = re.split(r'\n\s*\n', update_content)
            parts = [p.strip() for p in parts if p.strip()]
            if (any(filename in part for part in parts)): 
                index = parts.index([part for part in parts if filename in part][0])
                parts[index] = content
            else: parts.append(content)
            with open(ext_path, 'w', encoding=encoding_detect(ext_path)) as file:
                joined_parts = '\n\n'.join(parts)
                file.write(f"\n{joined_parts}\n")
        else:
            with open(ext_path, 'w', encoding=encoding_detect(ext_path)) as f:
                f.write(f"\n{content}\n")
        status, message = 'ok', "Data is saved successfully."
    except Exception as e:
        status, message = 'error', f"Error: {str(e)}"
    return status, message

def postProcess(directory: str) -> dict:
    try:
        parent_path = os.path.dirname(directory)
        output_folder = os.path.normpath(os.path.join(parent_path, 'output'))
        os.makedirs(output_folder, exist_ok=True)
        output_HYD_path = os.path.normpath(os.path.join(output_folder, 'HYD'))
        # Create the directory output_HYD_path
        if os.path.exists(output_HYD_path): shutil.rmtree(output_HYD_path, onerror=remove_readonly)
        os.makedirs(output_HYD_path, exist_ok=True)
        subdirs = [d for d in os.listdir(directory) if os.path.isdir(os.path.normpath(os.path.join(directory, d)))]
        if not subdirs: 
            return {'status': 'error', 'message': f'No simulation output folders found: {subdirs}.'}
        # Copy folder DFM_DELWAQ to the parent directory
        DFM_DELWAQ_from = os.path.normpath(os.path.join(directory, 'DFM_DELWAQ'))
        DFM_DELWAQ_to = os.path.normpath(os.path.join(parent_path, 'DFM_DELWAQ'))
        if os.path.exists(DFM_DELWAQ_to): shutil.rmtree(DFM_DELWAQ_to, onerror=remove_readonly)
        if os.path.exists(DFM_DELWAQ_from):
            shutil.copytree(DFM_DELWAQ_from, DFM_DELWAQ_to)
            shutil.rmtree(DFM_DELWAQ_from, onerror=remove_readonly)
        # Copy files to the directory output
        DFM_OUTPUT_folder = os.path.normpath(os.path.join(directory, 'DFM_OUTPUT'))
        if not os.path.exists(DFM_OUTPUT_folder):
            return {'status': 'error', 'message': 'No output folder found'}
        select_files = ['FlowFM.dia', 'FlowFM_his.nc', 'FlowFM_map.nc']
        found_files = [f for f in os.listdir(DFM_OUTPUT_folder) if f in select_files]
        if len(found_files) == 0: 
            return {'status': 'error', 'message': 'No required files found in the output folder'}
        # Copy and Remove the outputs
        for f in found_files:
            src = os.path.normpath(os.path.join(DFM_OUTPUT_folder, f))
            # # Using .nc format
            # shutil.copy2(src, output_HYD_path)
            # Using .zarr format
            if f.endswith('.nc'):
                zarr_path = os.path.normpath(os.path.join(output_HYD_path, f.replace('.nc', '.zarr')))
                tmp_path = zarr_path + "_tmp"
                if os.path.exists(tmp_path): shutil.rmtree(tmp_path, onerror=remove_readonly)
                with xr.open_dataset(src, chunks='auto') as ds:
                    ds.to_zarr(tmp_path, mode='w', consolidated=True, compute=True)
                os.rename(tmp_path, zarr_path)
            else: shutil.copy2(src, output_HYD_path)
            safe_remove(src)
        # Clean DFM_OUTPUT folder
        if os.path.exists(DFM_OUTPUT_folder): shutil.rmtree(DFM_OUTPUT_folder, onerror=remove_readonly)
        return {'status': 'ok', 'message': 'Simulation completed successfully'}
    except Exception as e: return {'status': 'error', 'message': str(e)}

def kill_process(process):
    if not process: return {"status": "ok", "message": "No process to kill"}
    try:
        if process.poll() is not None: return {"status": "ok", "message": "Simulation stopped"}
        # Try terminate
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=5)
            return {"status": "ok", "message": "Simulation stopped"}
        except Exception: pass
        try:
            process.terminate()
            process.wait(timeout=5)
            return {"status": "ok", "message": "Simulation terminated."}
        except Exception: pass
        # Force kill for Windows
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return {"status": "ok", "message": "Simulation force killed"}
    except Exception as e: 
        return {"status": "error", "message": str(e)}

def checkVariables(data: xr.Dataset, variablesNames: str) -> bool:
    if variablesNames not in data.variables: return False
    var = data[variablesNames]
    if var.size == 0: return False # Empty variable
    # Check if all values are NaN in a sample slice
    if np.isnan(var.data.compute()).all(): return False
    # Check if min and max are the same value
    vmin, vmax = var.min(skipna=True).compute(), var.max(skipna=True).compute()
    if float(vmin) < -900 and float(vmax) < -900: return False
    return bool(float(vmin) != float(vmax))

def getVariablesNames(out_files: list, model_type: str='', filename: str='') -> dict:
    result = {}
    for data in out_files:
        if data is None: continue
        # This is a hydrodynamic his file
        if 'time' in data.sizes and any(k in data.sizes for k in ['stations', 'cross_section', 'source_sink']):
            print(f"- Checking Hydrodynamic Simulation: His file...")
            # Prepare data for hydrodynamic options
            result['hyd_obs'] = data.sizes['stations'] > 0 if ('stations' in data.sizes) else False
            result['cross_sections'] = False
            if 'cross_section' in data.sizes and data.sizes['cross_section'] > 0:
                x = da.unique(data['cross_section_geom_node_coordx'].data).compute()
                y = da.unique(data['cross_section_geom_node_coordy'].data).compute()
                if (x.shape[0] > 1 and y.shape[0] > 1): result['cross_sections'] = True
            result['sources'] = data.sizes['source_sink'] > 0 if ('source_sink' in data.sizes) else False
            # Prepare data for measured locations
            # 1. Observation points
            # 1.1. Hydrodynamics
            result['hyd_waterlevel'] = checkVariables(data, 'waterlevel')
            result['hyd_waterdepth'] = checkVariables(data, 'waterdepth')
            # 1.2. Meteorology
            result['hyd_total_heat_flux'] = checkVariables(data, 'Qtot')
            result['hyd_precipitation_rate'] = checkVariables(data, 'rain')
            result['hyd_wind_speed'] = checkVariables(data, 'wind')
            result['hyd_air_temperature'] = checkVariables(data, 'Tair')
            result['hyd_relative_humidity'] = checkVariables(data, 'rhum')
            result['hyd_solar_influx'] = checkVariables(data, 'Qsun')
            result['hyd_evaporative_heat_flux'] = checkVariables(data, 'Qeva')
            result['hyd_free_convection_evaporative_heat_flux'] = checkVariables(data, 'Qfreva')
            result['hyd_sensible_heat_flux'] = checkVariables(data, 'Qcon')
            result['hyd_free_convection_sensible_heat_flux'] = checkVariables(data, 'Qfrcon')
            result['hyd_long_wave_back_radiation'] = checkVariables(data, 'Qlong')
            result['hyd_cloudiness'] = checkVariables(data, 'clou')
            result['hyd_meteorology'] = True if (result['hyd_total_heat_flux'] or
                result['hyd_precipitation_rate'] or result['hyd_wind_speed'] or
                result['hyd_air_temperature'] or result['hyd_relative_humidity'] or
                result['hyd_solar_influx'] or result['hyd_evaporative_heat_flux'] or
                result['hyd_free_convection_evaporative_heat_flux'] or
                result['hyd_sensible_heat_flux'] or result['hyd_free_convection_sensible_heat_flux'] or
                result['hyd_long_wave_back_radiation'] or result['hyd_cloudiness']) else False
            # 2. Sources/Sinks Points
            if result['sources']:
                result['source_prescribed_discharge'] = checkVariables(data, 'source_sink_prescribed_discharge')
                result['source_prescribed_salinity'] = checkVariables(data, 'source_sink_prescribed_salinity_increment')
                result['source_prescribed_temperature'] = checkVariables(data, 'source_sink_prescribed_temperature_increment')
                result['source_current_discharge'] = checkVariables(data, 'source_sink_current_discharge')
                result['source_cumulative_volume'] = checkVariables(data, 'source_sink_cumulative_volume')
                result['source_average_discharge'] = checkVariables(data, 'source_sink_discharge_average')
            # 3. Cross sections
            if result['cross_sections']:
                result['cross_sections_velocity'] = checkVariables(data, 'cross_section_velocity')
                result['cross_sections_area'] = checkVariables(data, 'cross_section_area')
                result['cross_sections_discharge'] = checkVariables(data, 'cross_section_discharge')
                result['cross_sections_cumulative_discharge'] = checkVariables(data, 'cross_section_cumulative_discharge')
                result['cross_section_salt'] = checkVariables(data, 'cross_section_salt')
                result['cross_sections_cumulative_salt'] = checkVariables(data, 'cross_section_cumulative_salt')
                result['cross_section_temperature'] = checkVariables(data, 'cross_section_temperature')
                result['cross_section_cumulative_temperature'] = checkVariables(data, 'cross_section_cumulative_temperature')
                result['cross_section_contaminant'] = checkVariables(data, 'cross_section_Contaminant')
                result['cross_section_cumulative_contaminant'] = checkVariables(data, 'cross_section_cumulative_Contaminant')
            # 4. Hydrodynamic Water balance
            result['hyd_wb_total_volume'] = checkVariables(data, 'water_balance_total_volume')
            result['hyd_wb_storage'] = checkVariables(data, 'water_balance_storage')
            result['hyd_wb_inflow_boundaries'] = checkVariables(data, 'water_balance_boundaries_in')
            result['hyd_wb_outflow_boundaries'] = checkVariables(data, 'water_balance_boundaries_out')
            result['hyd_wb_total_boundaries'] = checkVariables(data, 'water_balance_boundaries_total')
            result['hyd_wb_total_precipitation'] = checkVariables(data, 'water_balance_precipitation_total')
            result['hyd_wb_total_evaporation'] = checkVariables(data, 'water_balance_evaporation')
            result['hyd_wb_source_sink'] = checkVariables(data, 'water_balance_source_sink')
            result['hyd_wb_inflow_groundwater'] = checkVariables(data, 'water_balance_groundwater_in')
            result['hyd_wb_outflow_groundwater'] = checkVariables(data, 'water_balance_groundwater_out')
            result['hyd_wb_total_groundwater'] = checkVariables(data, 'water_balance_groundwater_total')
            result['hyd_wb_ground_precipitation'] = checkVariables(data, 'water_balance_precipitation_on_ground')
            result['hyd_wb_volume_error'] = checkVariables(data, 'water_balance_volume_error')
            result['hyd_water_balance'] = True if (result['hyd_wb_total_volume'] or
                result['hyd_wb_inflow_boundaries'] or result['hyd_wb_outflow_boundaries'] or
                result['hyd_wb_total_boundaries'] or result['hyd_wb_total_precipitation'] or
                result['hyd_wb_total_evaporation'] or result['hyd_wb_source_sink'] or
                result['hyd_wb_inflow_groundwater'] or result['hyd_wb_outflow_groundwater'] or
                result['hyd_wb_total_groundwater'] or result['hyd_wb_ground_precipitation'] or
                result['hyd_wb_storage'] or result['hyd_wb_volume_error']) else False
        # This is a hydrodynamic map file
        elif ('time' in data.sizes and any(k in data.sizes for k in ['mesh2d_nNodes', 'mesh2d_nEdges'])):
            print(f"- Checking Hydrodynamic Simulation: Map file...")
            result['z_layers'] = checkVariables(data, 'mesh2d_layer_z')
            # Prepare data for thermocline parameters
            # 1. Thermocline
            result['thermocline_hyd'] = checkVariables(data, 'mesh2d_tem1')
            # 2. Spatial single layer hydrodynamic maps
            result['hyd_wl_dynamic'] = checkVariables(data, 'mesh2d_s1')
            result['hyd_wd_dynamic'] = checkVariables(data, 'mesh2d_waterdepth')
            result['single_layer'] = True if (result['hyd_wl_dynamic'] or result['hyd_wd_dynamic']) else False
            # 3. Spatial multi layer hydrodynamic maps
            result['spatial_salinity'] = checkVariables(data, 'mesh2d_sa1')
            result['spatial_contaminant'] = checkVariables(data, 'mesh2d_Contaminant')
            result['multi_layer'] = True if (result['thermocline_hyd'] or
                result['spatial_salinity'] or result['spatial_contaminant']) else False
            # 4. Spatial static maps
            result['waterdepth_static'] = checkVariables(data, 'mesh2d_waterdepth')
            result['spatial_static'] = True if (result['waterdepth_static']) else False
            result['spatial_map'] = True if (result['single_layer'] or result['multi_layer']) else False
            result['hide_map'] = result['spatial_map']
        # This is a water quality his file
        elif ('nTimesDlwq' in data.sizes and not any(k in data.sizes for k in ['mesh2d_nNodes', 'mesh2d_nEdges'])):
            print(f"- Checking Water Quality Simulation: His file...")
            variables = set(data.variables.keys()) - set(['nTimesDlwqBnd', 'station_name', 'station_x', 'station_y', 'station_z', 'nTimesDlwq'])
            result['waq_his'] = False
            # Prepare data for Physical option
            # 1. Conservative and Decaying Tracers
            if model_type == 'conservative-tracers':
                result['waq_his_conservative_selector'] = []
                for item in variables:
                    if checkVariables(data, item): result['waq_his_conservative_selector'].append(item)
                if len(result['waq_his_conservative_selector']) > 0:
                    result['waq_his_conservative_decay'] = True
                    result['waq_his_conservative_decay_name'] = filename
                else:
                    result['waq_his_conservative_decay'] = False
                    result['waq_his_conservative_decay_name'] = 'Conservative and decaying tracers'
                result['waq_his'] = result['waq_his_conservative_decay']
            # 2. Suspended Sediment
            elif model_type == 'suspend-sediment':
                result['waq_his_suspended_sediment_selector'] = []
                for item in variables:
                    if checkVariables(data, item): result['waq_his_suspended_sediment_selector'].append(item)
                if len(result['waq_his_suspended_sediment_selector']) > 0:
                    result['waq_his_suspended_sediment'] = True
                    result['waq_his_suspended_sediment_name'] = filename
                else:
                    result['waq_his_suspended_sediment'] = False
                    result['waq_his_suspended_sediment_name'] = 'Suspended Sediment (three fractions)'
                result['waq_his'] = result['waq_his_suspended_sediment']
            # Prepare data for Chemical option
            # 1. Simple Oxygen
            elif model_type == 'simple-oxygen':
                result['waq_his_simple_oxygen_selector'] = []
                for item in variables:
                    if checkVariables(data, item): result['waq_his_simple_oxygen_selector'].append(item)
                if len(result['waq_his_simple_oxygen_selector']) > 0:
                    result['waq_his_simple_oxygen'] = True
                    result['waq_his_simple_oxygen_name'] = filename
                else:
                    result['waq_his_simple_oxygen'] = False
                    result['waq_his_simple_oxygen_name'] = 'Simple Oxygen'
                result['waq_his'] = result['waq_his_simple_oxygen']
            # 2. Oxygen and BOD (water phase only)
            elif model_type == 'oxygen-bod-water':
                result['waq_his_oxygen_bod_selector'] = []
                for item in variables:
                    if checkVariables(data, item): result['waq_his_oxygen_bod_selector'].append(item)            
                if len(result['waq_his_oxygen_bod_selector']) > 0:
                    result['waq_his_oxygen_bod'] = True
                    result['waq_his_oxygen_bod_name'] = filename
                else:
                    result['waq_his_oxygen_bod'] = False
                    result['waq_his_oxygen_bod_name'] = 'Oxygen and BOD (water phase only)'
                result['waq_his'] = result['waq_his_oxygen_bod']
            # 3. Cadmium
            elif model_type == 'cadmium':
                result['waq_his_cadmium_selector'] = []
                for item in variables:
                    if checkVariables(data, item): result['waq_his_cadmium_selector'].append(item)
                if len(result['waq_his_cadmium_selector']) > 0:
                    result['waq_his_cadmium'] = True
                    result['waq_his_cadmium_name'] = filename
                else:
                    result['waq_his_cadmium'] = False
                    result['waq_his_cadmium_name'] = 'Cadmium'
                result['waq_his'] = result['waq_his_cadmium']
            # 4. Eutrophication
            elif model_type == 'eutrophication':
                result['waq_his_eutrophication_selector'] = []
                for item in variables:
                    if checkVariables(data, item): result['waq_his_eutrophication_selector'].append(item)
                if len(result['waq_his_eutrophication_selector']) > 0:
                    result['waq_his_eutrophication'] = True
                    result['waq_his_eutrophication_name'] = filename
                else:
                    result['waq_his_eutrophication'] = False
                    result['waq_his_eutrophication_name'] = 'Eutrophication'
                result['waq_his'] = result['waq_his_eutrophication']
            # 5. Trace Metals
            elif model_type == 'trace-metals':
                result['waq_his_trace_metals_selector'] = []
                for item in variables:
                    if checkVariables(data, item): result['waq_his_trace_metals_selector'].append(item)
                if len(result['waq_his_trace_metals_selector']) > 0:
                    result['waq_his_trace_metals'] = True
                    result['waq_his_trace_metals_name'] = filename
                else:
                    result['waq_his_trace_metals'] = False
                    result['waq_his_trace_metals_name'] = 'Trace Metals'
                result['waq_his'] = result['waq_his_trace_metals']
            # Prepare data for Microbial option
            elif model_type == 'coliform':
                result['waq_his_coliform_selector'] = []
                for item in variables:
                    if checkVariables(data, item): result['waq_his_coliform_selector'].append(item)
                if len(result['waq_his_coliform_selector']) > 0:
                    result['waq_his_coliform'] = True
                    result['waq_his_coliform_name'] = filename
                else:
                    result['waq_his_coliform'] = False
                    result['waq_his_coliform_name'] = 'Coliform Bacteria'
                result['waq_his'] = result['waq_his_coliform']
        # This is a water quality map file      
        elif ('nTimesDlwq' in data.sizes and any(k in data.sizes for k in ['mesh2d_nNodes', 'mesh2d_nEdges'])):
            print(f'- Checking Water Quality Simulation: Map file...')
            result['wq_map'] = result['thermocline_waq'] = False
            variables = set(data.variables.keys()) - set(['mesh2d', 'mesh2d_node_x', 'mesh2d_node_y', 'mesh2d_edge_x',
                'mesh2d_edge_y', 'mesh2d_face_x_bnd', 'mesh2d_face_y_bnd', 'mesh2d_edge_nodes', 'mesh2d_edge_faces',
                'mesh2d_face_nodes', 'mesh2d_layer_dlwq', 'nTimesDlwqBnd', 'mesh2d_face_x', 'mesh2d_face_y', 'nTimesDlwq'])
            # Prepare data for Physical option
            # 1. Conservative and Decaying Tracers
            if model_type == 'conservative-tracers':
                result['waq_map_conservative_selector'], result['waq_map_conservative_decay'] = [], False
                result['waq_map_conservative_decay_name'] = 'Conservative and decaying tracers'
                for item in variables:
                    item1 = item.replace('mesh2d_', '').replace('2d_', '')
                    if checkVariables(data, item): 
                        elements_check = {x[0] for x in result['waq_map_conservative_selector']}
                        if item1 not in elements_check: result['waq_map_conservative_selector'].append(item1)
                result['waq_map_conservative_selector'] = list(dict.fromkeys(result['waq_map_conservative_selector']))
                if len(result['waq_map_conservative_selector']) > 0:
                    result['wq_map'] = result['waq_map_conservative_decay'] = result['thermocline_waq'] = True
                    result['waq_map_conservative_decay_name'] = filename
            # 2. Suspended Sediment
            elif model_type == 'suspend-sediment':
                result['waq_map_suspended_sediment_selector'], result['waq_map_suspended_sediment'] = [], False
                result['waq_map_suspended_sediment_name'] = 'Suspended sediment (three fractions)'
                for item in variables:
                    item1 = item.replace('mesh2d_', '').replace('2d_', '')
                    if checkVariables(data, item):
                        elements_check = {x[0] for x in result['waq_map_suspended_sediment_selector']}
                        if item1 not in elements_check: result['waq_map_suspended_sediment_selector'].append(item1)
                result['waq_map_suspended_sediment_selector'] = list(dict.fromkeys(result['waq_map_suspended_sediment_selector']))
                if len(result['waq_map_suspended_sediment_selector']) > 0:
                    result['wq_map'] = result['waq_map_suspended_sediment'] = result['thermocline_waq'] = True
                    result['waq_map_suspended_sediment_name'] = filename
            # Prepare data for Chemical option
            # 1. Simple Oxygen
            elif model_type == 'simple-oxygen':
                result['waq_map_simple_oxygen_selector'], result['waq_map_simple_oxygen'] = [], False
                result['waq_map_simple_oxygen_name'] = 'Simple Oxygen'
                for item in variables:
                    item1 = item.replace('mesh2d_', '').replace('2d_', '')
                    if checkVariables(data, item):
                        elements_check = {x[0] for x in result['waq_map_simple_oxygen_selector']}
                        if item1 not in elements_check: result['waq_map_simple_oxygen_selector'].append(item1)
                result['waq_map_simple_oxygen_selector'] = list(dict.fromkeys(result['waq_map_simple_oxygen_selector']))
                if len(result['waq_map_simple_oxygen_selector']) > 0:
                    result['wq_map'] = result['waq_map_simple_oxygen'] = result['thermocline_waq'] = True
                    result['waq_map_simple_oxygen_name'] = filename
            # 2. Oxygen and BOD (water phase only)
            elif model_type == 'oxygen-bod-water':
                result['waq_map_oxygen_bod_selector'], result['waq_map_oxygen_bod'] = [], False
                result['waq_map_oxygen_bod_name'] = 'Oxygen and BOD (water phase only)'
                for item in variables:
                    item1 = item.replace('mesh2d_', '').replace('2d_', '')
                    if checkVariables(data, item):
                        elements_check = {x[0] for x in result['waq_map_oxygen_bod_selector']}
                        if item1 not in elements_check: result['waq_map_oxygen_bod_selector'].append(item1)
                result['waq_map_oxygen_bod_selector'] = list(dict.fromkeys(result['waq_map_oxygen_bod_selector']))
                if len(result['waq_map_oxygen_bod_selector']) > 0:  
                    result['wq_map'] = result['waq_map_oxygen_bod'] = result['thermocline_waq'] = True
                    result['waq_map_oxygen_bod_name'] = filename
            # 3. Cadmium
            elif model_type == 'cadmium':
                result['waq_map_cadmium_selector'], result['waq_map_cadmium'] = [], False
                result['waq_map_cadmium_name'] = 'Cadmium'
                for item in variables:
                    item1 = item.replace('mesh2d_', '').replace('2d_', '')
                    if checkVariables(data, item):
                        elements_check = {x[0] for x in result['waq_map_cadmium_selector']}
                        if item1 not in elements_check: result['waq_map_cadmium_selector'].append(item1)
                result['waq_map_cadmium_selector'] = list(dict.fromkeys(result['waq_map_cadmium_selector']))
                if len(result['waq_map_cadmium_selector']) > 0:
                    result['wq_map'] = result['waq_map_cadmium'] = result['thermocline_waq'] = True
                    result['waq_map_cadmium_name'] = filename
            # 4. Eutrophication
            elif model_type == 'eutrophication':
                result['waq_map_eutrophication_selector'], result['waq_map_eutrophication'] = [], False
                result['waq_map_eutrophication_name'] = 'Eutrophication'
                for item in variables:
                    item1 = item.replace('mesh2d_', '').replace('2d_', '')
                    if checkVariables(data, item):
                        elements_check = {x[0] for x in result['waq_map_eutrophication_selector']}
                        if item1 not in elements_check: result['waq_map_eutrophication_selector'].append(item1)
                result['waq_map_eutrophication_selector'] = list(dict.fromkeys(result['waq_map_eutrophication_selector']))
                if len(result['waq_map_eutrophication_selector']) > 0:
                    result['wq_map'] = result['waq_map_eutrophication'] = result['thermocline_waq'] = True
                    result['waq_map_eutrophication_name'] = filename
            # 5. Trace Metals
            elif model_type == 'trace-metals':
                result['waq_map_trace_metals_selector'], result['waq_map_trace_metals'] = [], False
                result['waq_map_trace_metals_name'] = 'Trace Metals'
                for item in variables:
                    item1 = item.replace('mesh2d_', '').replace('2d_', '')
                    if checkVariables(data, item):
                        elements_check = {x[0] for x in result['waq_map_trace_metals_selector']}
                        if item1 not in elements_check: result['waq_map_trace_metals_selector'].append(item1)
                result['waq_map_trace_metals_selector'] = list(dict.fromkeys(result['waq_map_trace_metals_selector']))
                if len(result['waq_map_trace_metals_selector']) > 0:
                    result['wq_map'] = result['waq_map_trace_metals'] = result['thermocline_waq'] = True
                    result['waq_map_trace_metals_name'] = filename
            # Prepare data for Microbial option
            elif model_type == 'coliform':
                result['waq_map_coliform_selector'], result['waq_map_coliform'] = [], False
                result['waq_map_coliform_name'] = 'Coliform Bacteria'
                for item in variables:
                    item1 = item.replace('mesh2d_', '').replace('2d_', '')
                    if checkVariables(data, item):
                        elements_check = {x for x in result['waq_map_coliform_selector']}
                        if item1 not in elements_check: result['waq_map_coliform_selector'].append(item1)
                result['waq_map_coliform_selector'] = list(dict.fromkeys(result['waq_map_coliform_selector']))
                if len(result['waq_map_coliform_selector']) > 0:
                    result['wq_map'] = result['waq_map_coliform'] = result['thermocline_waq'] = True
                    result['waq_map_coliform_name'] = filename
            result['spatial_map'] = result['single_layer'] = result['multi_layer'] = result['wq_map']
            result['hide_map'] = result['spatial_map']
    return result

def layerCounter(data_map: xr.Dataset, type: str='hyd') -> dict:
    layers = {}
    if type == 'hyd':
        z_layer = [round(x, 2) for x in data_map['mesh2d_layer_z'].values]
        # Add depth-average if available
        if {'mesh2d_ucxa', 'mesh2d_ucya'}.issubset(data_map.variables.keys()): layers['-1'] = 'Average'
        # Iterate from bottom to surface
        ucx = data_map['mesh2d_ucx'].data
        ucy = data_map['mesh2d_ucy'].data
        ucm = data_map['mesh2d_ucmag'].data
        for i in reversed(range(len(z_layer))):
            # Use dask to speed up, keep lazy-load
            note, counter = '', len(z_layer)-i-1
            if counter == 0: note = ' (surface)'
            elif counter == len(z_layer)-1: note = ' (bottom)'
            ucx_i = ucx[:, :, i].compute()
            ucy_i = ucy[:, :, i].compute()
            ucm_i = ucm[:, :, i].compute()
            # Check if all values are nan
            if (np.isnan(ucx_i).all() or np.isnan(ucy_i).all() or np.isnan(ucm_i).all()): continue
            layers[str(counter)] = f'Depth: {z_layer[i]} m{note}'
    else:
        z_layer = np.round([100*x for x in data_map['mesh2d_layer_dlwq'].data.compute()], 0)
        layers['-1'] = 'Average'
        for i in reversed(range(len(z_layer))):
            layers[str(len(z_layer)-i-1)] = f'Sigma: {z_layer[i]} %'
    return layers

def getVectorNames() -> list:
    result = [(0,'Velocity')]
    return result

def dialogReader(dialog_file: str, time_zone:str) -> dict:
    # Check if the dialog file exists
    if not os.path.exists(dialog_file): return {}
    result = {}
    with open(f'{dialog_file}', 'r', encoding=encoding_detect(dialog_file)) as f:
        content = f.read()
    content = content.split('\n')
    for line in content:
        if "Computation started" in line:
            temp = pd.to_datetime(line.split(': ')[2], utc=True, format='%H:%M:%S, %d-%m-%Y')
            result["computation_start"] = utc_to_local(temp, time_zone)
        if "Computation finished" in line:
            temp = pd.to_datetime(line.split(': ')[2], utc=True, format='%H:%M:%S, %d-%m-%Y')
            result["computation_finish"] = utc_to_local(temp, time_zone)
        if "my model area" in line:
            temp = line.split(': ')[2]
            result["area"] = float(temp.strip())
        if "my model volume" in line:
            temp = line.split(': ')[2]
            result["volume"] = float(temp.strip())
    return result

def getSummary(dialog_path: str, out_files: list, time_zone: str) -> list:
    dialog, result = dialogReader(dialog_path, time_zone), []
    # --- Dialog info ---
    if len(dialog) > 0:
        result.append({'parameter': 'Computation started', 'value': dialog['computation_start']})
        result.append({'parameter': 'Computation finished', 'value': dialog['computation_finish']})
        result.append({'parameter': 'Area (m2)', 'value': dialog['area']})
        result.append({'parameter': 'Volume (m3)', 'value': dialog['volume']})
    if len(out_files) == 0: return result
    for data_his in out_files:
        if data_his is None: continue
        sizes = data_his.sizes
        # --- Hydrodynamic ---
        if 'time' in sizes:
            time_var = data_his['time']
            start = pd.to_datetime(time_var.isel(time=0).values, utc=True)
            end = pd.to_datetime(time_var.isel(time=-1).values, utc=True)
            start_hyd, end_hyd = utc_to_local(start, time_zone), utc_to_local(end, time_zone)
            result.append({'parameter': 'Start Date (Hydrodynamic Simulation)', 'value': start_hyd})
            result.append({'parameter': 'Stop Date (Hydrodynamic Simulation)', 'value': end_hyd})
            result.append({'parameter': 'Number of Time Steps', 'value': sizes['time']})
        if ('laydim' in sizes): result.append({'parameter': 'Number of Layers', 'value': sizes['laydim']})
        if ('stations' in sizes and sizes['stations'] > 0): result.append({'parameter': 'Number of Observation Stations', 'value': sizes['stations']})
        if ('cross_section' in sizes and sizes['cross_section'] > 0): result.append({'parameter': 'Number of Cross Sections', 'value': sizes['cross_section']})
        if ('source_sink' in sizes and sizes['source_sink'] > 0): result.append({'parameter': 'Number of Sources/Sinks', 'value': sizes['source_sink']})
        # --- Water Quality ---
        if 'nTimesDlwq' in sizes:
            waq_time = data_his['nTimesDlwq']
            start = pd.to_datetime(waq_time.isel(nTimesDlwq=0).values, utc=True)
            end = pd.to_datetime(waq_time.isel(nTimesDlwq=-1).values, utc=True)
            start_waq, end_waq = utc_to_local(start, time_zone), utc_to_local(end, time_zone)
            result.append({'parameter': f'Start Date (Water Quality Simulation)', 'value': start_waq})
            result.append({'parameter': f'Stop Date (Water Quality Simulation)', 'value': end_waq})
            result.append({'parameter': f'Number of Time Steps (Water Quality Simulation)', 'value': sizes['nTimesDlwq']})
        if ('nStations' in sizes): result.append({'parameter': f'Number of Observation Stations (Water Quality Simulation)', 'value': sizes['nStations']})
    return result

def checkCoordinateReferenceSystem(name: Sequence[str], geometry: GeometryArray | gpd.GeoSeries, 
    data_his: xr.Dataset) -> gpd.GeoDataFrame:
    # Check coordinate reference system
    if 'wgs84' in data_his.variables:
        crs_code = data_his['wgs84'].attrs.get('EPSG_code', 'EPSG:4326')
        result = gpd.GeoDataFrame(data={'name': name, 'geometry': geometry}, crs=crs_code)
    elif 'projected_coordinate_system' in data_his.variables:
        crs_code = data_his['projected_coordinate_system'].attrs.get('EPSG_code', 'EPSG:4326')
        result = gpd.GeoDataFrame(data={'name': name, 'geometry': geometry}, crs=crs_code)
        result = result.to_crs(epsg=4326)  # Convert to WGS84 if not already
    else: result = gpd.GeoDataFrame(data={'name': name, 'geometry': geometry}, crs='EPSG:4326')
    return result

def hydCreator(data_his: xr.Dataset) -> tuple[gpd.GeoDataFrame, list]:
    target_dims = ('time', 'stations', 'laydim')
    names, listPoints = [name.decode('utf-8').strip() for name in data_his['station_name'].data.compute()], []
    x, y = data_his['station_x_coordinate'].data.compute(), data_his['station_y_coordinate'].data.compute()
    gdf = checkCoordinateReferenceSystem(names, gpd.points_from_xy(x, y), data_his)
    vars = [var for var in data_his.data_vars if data_his[var].dims == target_dims]
    for name in names:
        vars = [str(var) for var in data_his.data_vars if data_his[var].dims == target_dims]
        station_dict = {name: [{var: variablesNames.get(var, var)} for var in vars]}
        listPoints.append(station_dict)
    return gdf, listPoints

def obsCreator(points: list) -> gpd.GeoDataFrame:
    df = pd.DataFrame(points, columns=['name', 'latitude', 'longitude'])
    geometry = gpd.points_from_xy(df['longitude'], df['latitude'])
    return gpd.GeoDataFrame({'name': df['name'], 'geometry': geometry}, crs="EPSG:4326")

def linearCreator(x_coords: np.ndarray, y_coords: np.ndarray) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    if (len(x_coords) < 2 and len(y_coords) < 2):
        print('Not enough points to create a linear.')
        return None, None
    a, b = np.polyfit(x_coords, y_coords, 1)
    # Get bounding box
    x_min, x_max = x_coords.min(), x_coords.max()
    y_min, y_max = y_coords.min(), y_coords.max()
    # Intersect with bounding box
    candidates = []
    # Intersect with x = x_min, x = x_max
    y_left, y_right = a * x_min + b, a * x_max + b
    if y_min <= y_left <= y_max: candidates.append((float(x_min), float(y_left)))
    if y_min <= y_right <= y_max: candidates.append((float(x_max), float(y_right)))
    if len(candidates) == 0: candidates.append((x_coords[0], y_coords[0]))
    # Intersect with y = y_min, y = y_max
    if abs(a) > 1e-12:  # avoid division by zero
        x_bottom, x_top = (y_min - b) / a, (y_max - b) / a
        if x_min <= x_bottom <= x_max: candidates.append((float(x_bottom), float(y_min)))
        if x_min <= x_top <= x_max: candidates.append((float(x_top), float(y_max)))
    if len(candidates) == 1: candidates.append((x_coords[-1], y_coords[-1]))
    return candidates[0], candidates[1]

def crosssectionCreator(data_his: xr.Dataset) -> tuple[gpd.GeoDataFrame, list]:
    names, listAttributes = [name.decode('utf-8').strip() for name in data_his['cross_section_name'].data.compute()], []
    x = data_his['cross_section_geom_node_coordx'].data.compute()
    y = data_his['cross_section_geom_node_coordy'].data.compute()
    p1, p2 = linearCreator(x, y)
    if p1 is None or p2 is None:
        print('Not enough points to create a cross-section.')
        return gpd.GeoDataFrame(), []
    geometry = gpd.GeoSeries([shapely.geometry.LineString([p1, p2])])
    gdf = checkCoordinateReferenceSystem(names, geometry, data_his)
    crsValues = [
        'cross_section_velocity', 'cross_section_area', 'cross_section_discharge', 'cross_section_cumulative_discharge',
        'cross_section_temperature', 'cross_section_cumulative_temperature', 'cross_section_salt',
        'cross_section_cumulative_salt', 'cross_section_Contaminant', 'cross_section_cumulative_Contaminant'
    ]
    listAttributes = [{item: variablesNames[item] if item in variablesNames else item} for item in crsValues]
    return gdf, listAttributes

def timeseriesCreator(data_his: xr.Dataset, key: str, time_zone: str, timeColumn: str='time') -> pd.DataFrame:
    name = 'source_sink_name' if key.endswith('_source') else 'station_name'
    columns = [i.decode('utf-8').strip() for i in data_his[name].data.compute()]
    temp = variablesNames.get(key, key)
    if key.startswith('wb_'): columns = ['Water balance'] # Used for water balance
    elif key.endswith('_crs'): 
        columns = ['Cross-section'] # Used for cross-section
        temp = key.replace('_crs', '')
    if name not in data_his.variables.keys(): return pd.DataFrame()
    index = [
        utc_to_local(pd.to_datetime(i, utc=True), time_zone) 
        for i in data_his[timeColumn].data
    ]
    df = pd.DataFrame(
        index=index, data=numberFormatter(data_his[temp].data.compute()), columns=columns
    ).reset_index()
    return df

def valueToKeyConverter(values: list, dict: dict=units) -> list:
    if not isinstance(values, list): values = [values]
    result = []
    for value in values:
        result.append(dict.get(value, value))
    return result

def vectorComputer(data_map: xr.Dataset, value_type: str, 
    row_idx: int, time_zone: str, step: int) -> dict:
    if value_type == 'Average':
        # Average velocity in each layer
        ucx = data_map['mesh2d_ucxa'].isel(time=step).values
        ucy = data_map['mesh2d_ucya'].isel(time=step).values
        ucm = data_map['mesh2d_ucmaga'].isel(time=step).values
    else:
        # Velocity for specific layer
        ucx = data_map['mesh2d_ucx'].isel(time=step).values[:, row_idx]
        ucy = data_map['mesh2d_ucy'].isel(time=step).values[:, row_idx]
        ucm = data_map['mesh2d_ucmag'].isel(time=step).values[:, row_idx]
    # Get indices of non-nan values
    col_idx = np.where(~np.isnan(ucx) & ~np.isnan(ucy) & ~np.isnan(ucm))
    # Coordinates (filtered)
    x_coords = data_map['mesh2d_face_x'].values[col_idx]
    y_coords = data_map['mesh2d_face_y'].values[col_idx]
    # Values (filtered)
    ucx_valid = np.round(ucx[col_idx].astype(np.float64), 5)
    ucy_valid = np.round(ucy[col_idx].astype(np.float64), 5)
    ucm_valid = np.round(ucm[col_idx].astype(np.float64), 2)
    result = {"time": pd.to_datetime(data_map['time'].values[step], utc=True),
        "coordinates": np.column_stack((x_coords, y_coords)).tolist(),
        "values": np.column_stack((ucx_valid, ucy_valid, ucm_valid)).tolist()
    }
    result['time'] = utc_to_local(result['time'], time_zone)
    return result

def selectInsitu(data_his: xr.Dataset, data_map: xr.Dataset, 
    name: str, stationId: str, type: str, time_zone: str) -> pd.DataFrame:
    names = [x.decode('utf-8').strip() for x in data_his[type].data.compute()]
    if stationId not in names: return pd.DataFrame()
    idx = names.index(stationId)
    index = [
        utc_to_local(pd.to_datetime(id, utc=True), time_zone) 
        for id in data_his['time'].data
    ]
    if type == 'station_name':
        result = pd.DataFrame(index=index)
        z_layer = numberFormatter(data_map['mesh2d_layer_z'].data.compute())
        arr = data_his[name].data[:, idx, :].compute()
        for i in range(arr.shape[1]):
            i_rev = -(i+1)
            result[f'Depth: {z_layer[i_rev]} m'] = numberFormatter(arr[:, i_rev])
    else:
        temp = pd.DataFrame(
            data_his[variablesNames[name]].values, columns=names, index=index
        )
        result = temp[[stationId]]
    return result.dropna(axis=1, how='all').reset_index()

def sourceCreator(data_his: xr.Dataset) -> gpd.GeoDataFrame:
    names = [
        name.decode('utf-8').strip() 
        for name in data_his['source_sink_name'].data.compute()
    ]
    x = data_his['source_sink_x_coordinate'].data.compute()[0]
    y = data_his['source_sink_y_coordinate'].data.compute()[0]
    geometry = gpd.points_from_xy(x, y)
    return checkCoordinateReferenceSystem(names, geometry, data_his)

def meshProcess(is_hyd: bool, arr: np.ndarray, cache: dict) -> np.ndarray:
    cache_copy = cache.copy()
    data = cache_copy["df"]
    df = pd.DataFrame(data=data["data"], columns=data["columns"], index=data["index"])
    df_depth = np.array(df["depth"].values, dtype=float)
    depth_values = np.array(cache_copy["depth_values"], dtype=float)
    depth_rounded, n_rows = abs(np.round(depth_values, 0)), cache_copy["n_rows"]
    if is_hyd: 
        index_map = {
            int(v): len(depth_rounded)-i-1 for i, v in enumerate(depth_rounded)
        }
    else: index_map = {int(v): i for i, v in enumerate(depth_rounded)}
    # Pre-allocate frame
    frame = np.full((len(df), abs(n_rows)), np.nan, float)
    values_filtered = arr[df.index.values, :]
    depth_int = depth_rounded.astype(int)
    valid_depth = np.unique(depth_int[depth_int < abs(n_rows)])
    col_idx = np.array([index_map[d] for d in valid_depth])
    mask = df_depth[:, None] <= -valid_depth[None, :]
    vals = values_filtered[:, col_idx]
    frame[:, valid_depth] = np.where(mask, vals, frame[:, valid_depth])
    # Interpolate and fill missing values row-wise
    mask = ~np.isnan(frame)
    result = distance_transform_edt(~mask, return_distances=False, return_indices=True)
    if result is None: raise RuntimeError("distance_transform_edt returned None unexpectedly")
    ix, iy = result[0], result[1]
    frame_filled = gaussian_filter(frame[ix, iy], sigma=(1.2, 0.6))
    frame = np.clip(frame_filled, 0, None)
    mask_valid = -np.arange(abs(n_rows))[None, :] >= df_depth[:, None]
    max_row = np.max(np.where(mask_valid.T)[0])
    frame[~mask_valid] = np.nan
    smoothed_transpose = frame.T[:max_row + 2, :]
    return smoothed_transpose

def clean_json_value(value):
    if isinstance(value, dict):
        return {k: clean_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json_value(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value