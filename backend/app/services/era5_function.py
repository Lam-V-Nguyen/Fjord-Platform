import os, math, requests, cdsapi, threading, time, \
    zipfile, glob, shutil, hashlib, uuid, traceback
from dateutil.relativedelta import relativedelta
from datetime import datetime, timedelta, timezone
import xarray as xr, numpy as np, pandas as pd, geopandas as gpd
from typing import cast, Any
from services import functions, weather_functions, constants

try: from ecmwf.datastores import Client as DSClient
except ImportError: DSClient = None


_ACCOUNT_LOCK: dict = {}
_ACCOUNT_LOCK_GUARD = threading.Lock()
ERA5_GRID = constants.ERA5_GRID_DEG

class ERA5Error(Exception):
    pass
 
class DownloadCancelled(ERA5Error):
    pass

def to_naive_utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo is not None else ts

def set_status(processes, key, status, message=None, **extra):
    info = processes.setdefault(key, {})
    info["status"] = status
    if status == "running" and info.get("cancel"):
        message = "Cancelling download..."
    if message is not None: info["message"] = message
    info.update(extra)
    info["heartbeat"] = time.time()

def is_cancel_requested(processes, key):
    return bool(processes.get(key, {}).get("cancel"))

def account_lock(api_key):
    k = hashlib.sha256(api_key.strip().encode()).hexdigest()
    with _ACCOUNT_LOCK_GUARD:
        return _ACCOUNT_LOCK.setdefault(k, threading.Lock())

def check_cancel(processes, key):
    if is_cancel_requested(processes, key):
        raise DownloadCancelled("Download cancelled by user.")

def make_client(api_key):
    url = os.getenv("CDS_URL") or "https://cds.climate.copernicus.eu/api"
    if DSClient is not None:
        return DSClient(url=url, key=api_key.strip(), progress=False, timeout=120,
            maximum_tries=10, sleep_max=60, retry_after=60)
    try:
        return cdsapi.Client(url=url, key=api_key.strip(), quiet=False, 
            progress=False, timeout=120, retry_max=10, sleep_max=60)
    except TypeError:
        return cdsapi.Client(url=url, key=api_key.strip(), quiet=False)

def retrieve(client, dataset, request, target, logger, processes, process_key,
    thread_name, max_wait_s, log_every_s=60):
    result = {}
    def _run():
        try: client.retrieve(dataset, request, target)
        except BaseException as e: result["error"] = e
    worker = threading.Thread(target=_run, name=thread_name, daemon=True)
    t0 = last_log = time.monotonic()
    worker.start()
    while worker.is_alive():
        worker.join(timeout=1)
        if not worker.is_alive(): break
        if is_cancel_requested(processes, process_key):
            if logger:
                logger.info("Cancel requested -> stop waiting for CDS. "
                    "The request may still be queued at CDS (see your CDS 'Requests' page).")
            raise DownloadCancelled("Download cancelled by user.")
        now = time.monotonic()
        elapsed = now - t0
        if elapsed > max_wait_s:
            raise ERA5Error(
                f"CDS did not deliver data within {max_wait_s / 3600:.1f} h (still queued). "
                "Check https://cds.climate.copernicus.eu/requests and try again later.")
        msg = f"Waiting for CDS (queued/running)... {elapsed / 60:.0f} min elapsed"
        if now - last_log >= log_every_s:
            if logger: logger.info(msg)
            last_log = now
        set_status(processes, process_key, "running", msg)
    if "error" in result:
        raise result["error"]
    if not os.path.exists(target) or os.path.getsize(target) == 0:
        raise ERA5Error(f"CDS returned an empty file: {os.path.basename(target)}")

def var_detector(input_var:list, logger=None):
    selected = list(dict.fromkeys(input_var or []))
    unknown = [v for v in selected if v not in constants.VARIABLES and v not in constants.DERIVED]
    if unknown and logger: logger.warning(f"Ignoring unknown variables: {unknown}")
    cds_vars = {
        v: constants.VARIABLES[v] for v in selected if v in constants.VARIABLES and v not in constants.DERIVED
    }
    if not cds_vars: return [], False, {}
    wants_wind = any(v in constants.DERIVED for v in selected)
    wants_wind = True if wants_wind else False
    wanted = list(dict.fromkeys(cds_vars.values()))
    return wanted, wants_wind, cds_vars

def get_site_elevation_kartverket(lat, lon, timeout=15):
    """Surface elevation (m) from Kartverket DTM 1 m. In case of Na Uy; exception -> None."""
    try:
        r = requests.get(os.getenv("KARTVERKET_URL").strip(), params={
            "koordsys": 4258, "nord": lat, "ost": lon, "geojson": "false"}, timeout=timeout)
        r.raise_for_status()
        pts = r.json().get("punkter") or []
        z = pts[0].get("z") if pts else None
        return float(z) if z is not None else None
    except Exception: return None

def elevation_detector(wanted:list[str], lat:float, lon:float, elevation:float | None, auto_elevation:bool, logger=None) -> float | None:
    if not wanted or not (constants.ELEV_SENSITIVE & set(wanted)): return None
    if not auto_elevation and elevation is None:
        raise ERA5Error("Elevation must be provided when auto_elevation is False.")
    if elevation is not None:
        h_site = float(elevation)
        if logger: logger.info(f"Site elevation (user): {h_site:.1f} m")
        return h_site
    if auto_elevation:
        h_site = get_site_elevation_kartverket(lat, lon)
        if logger:
            logger.info(
                f"Site elevation (Kartverket DTM): {h_site:.1f} m" if h_site is not None
                else "Site elevation not available (outside Norway?) -> no elevation correction."
            )
        return h_site
    return None

def snap_to_era5_grid(lat, lon, grid=ERA5_GRID):
    glat = round(round(lat / grid) * grid, 4)
    glon = round(round(lon / grid) * grid, 4)
    return glat, glon

def single_point_area(lat, lon, grid=ERA5_GRID):
    glat, glon = snap_to_era5_grid(lat, lon, grid)
    d = grid * 0.4
    return [glat + d, glon - d, glat - d, glon + d], glat, glon

def enclosing_cell(lat, lon, grid=constants.ERA5_GRID_DEG):
    lat_s = math.floor(lat / grid) * grid
    lon_w = math.floor(lon / grid) * grid
    return round(lat_s, 4), round(lat_s + grid, 4), round(lon_w, 4), round(lon_w + grid, 4)

def cell_area(lat, lon, grid=ERA5_GRID):
    lat_s, lat_n, lon_w, lon_e = enclosing_cell(lat, lon, grid)
    e = grid * 0.2
    return [lat_n + e, lon_w - e, lat_s - e, lon_e + e]

def bilinear_weights(lat, lon, grid=ERA5_GRID):
    lat_s, lat_n, lon_w, lon_e = enclosing_cell(lat, lon, grid)
    fy, fx = (lat - lat_s) / grid, (lon - lon_w) / grid
    return {
        (lat_s, lon_w): (1 - fy) * (1 - fx), (lat_s, lon_e): (1 - fy) * fx,
        (lat_n, lon_w): fy * (1 - fx), (lat_n, lon_e): fy * fx,
    }

def month_requests(start_time, end_time, cds_names, area):
    current = datetime(start_time.year, start_time.month, 1)
    while current <= end_time:
        month_end = current + relativedelta(months=1) - timedelta(hours=1)
        a0, a1 = max(start_time, current), min(end_time, month_end)
        hours = range(a0.hour, a1.hour + 1) if a0.date() == a1.date() else range(24)
        yield f"{current.year}_{current.month:02d}", {
            "product_type": ["reanalysis"], "variable": cds_names,
            "year": [str(current.year)], "month": [f"{current.month:02d}"],
            "day": [f"{d:02d}" for d in range(a0.day, a1.day + 1)],
            "time": [f"{h:02d}:00" for h in hours],
            "area": area, "data_format": "netcdf", "download_format": "zip",
        }
        current += relativedelta(months=1)

def orography_request(start_time, area):
    return {
        "product_type": ["reanalysis"], "variable": ["geopotential"],
        "year": [str(start_time.year)], "month": [f"{start_time.month:02d}"],
        "day": [f"{start_time.day:02d}"], "time": [f"{start_time.hour:02d}:00"],
        "area": area, "data_format": "netcdf", "download_format": "zip",
    }

def normalize_dataset(ds, processes, key_process, logger=None):
    if "valid_time" in ds.variables and "time" not in ds.dims:
        ds = ds.rename({"valid_time": "time"})
    if "expver" in ds.dims: ds = ds.max("expver", skipna=True)
    ds = ds.drop_vars([v for v in ("number", "expver") if v in ds.variables])
    ren = {a: b for a, b in (("lat", "latitude"), ("lon", "longitude")) if a in ds.variables}
    if ren: ds = ds.rename(ren)
    if "time" not in ds.dims:
        msg = "Downloaded file has no time dimension."
        if logger: logger.error(msg)
        processes[key_process] = {"status": "failed", "message": msg}
    ds = ds.sortby("time")
    _, idx = np.unique(ds["time"].values, return_index=True)
    return ds.isel(time=idx)
                  
def open_download(path, extract_dir, processes, key_process, logger=None):
    if zipfile.is_zipfile(path):
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(extract_dir)
        nc_files = sorted(glob.glob(os.path.join(extract_dir, "**", "*.nc"), recursive=True))
    else: nc_files = [path]
    if not nc_files:
        msg = f"No NetCDF found in {os.path.basename(path)}"
        if logger: logger.error(msg)
        processes[key_process] = {"status": "failed", "message": msg}
    parts = []
    for f in nc_files:
        with xr.open_dataset(f) as d:
            parts.append(normalize_dataset(d.load(), processes, key_process, logger))
    return xr.merge(parts, compat="override", join="outer")

def interpolate_to_point(ds, lat, lon, processes, key_process, method="bilinear", logger=None):
    if not ("latitude" in ds.dims and "longitude" in ds.dims): return ds
    nearest = ds.sel(latitude=lat, longitude=lon, method="nearest")
    if method == "nearest" or ds.sizes["latitude"] < 2 or ds.sizes["longitude"] < 2: return nearest
    lat_min, lat_max = float(ds.latitude.min()), float(ds.latitude.max())
    lon_min, lon_max = float(ds.longitude.min()), float(ds.longitude.max())
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        msg = f"Point ({lat}, {lon}) outside downloaded grid [{lat_min}-{lat_max}, {lon_min}-{lon_max}]."
        if logger: logger.error(msg)
        processes[key_process] = {"status": "failed", "message": msg}
    point = ds.interp(latitude=lat, longitude=lon, method="linear")
    return point.fillna(nearest)

def resolve_names(ds, wanted, processes, key_process, logger=None):
    data_vars = [v for v in ds.data_vars if "time" in ds[v].dims]
    found, missing = {}, []
    for s in wanted:
        if s in ds.data_vars:
            found[s] = s
            continue
        alias = next((a for a in constants.ALIASES.get(s, ()) if a in ds.data_vars), None)
        if alias: found[s] = alias
        else: missing.append(s)
    leftover = [v for v in data_vars if v not in found.values() and v != "z"]
    if len(missing) == 1 and len(leftover) == 1:
        found[missing[0]] = leftover[0]
        missing = []
    if missing:
        msg = f"Variables not found in ERA5 file: {missing}. Available: {data_vars}"
        if logger: logger.error(msg)
        processes[key_process] = {"status": "failed", "message": msg}
    return found

def block_to_frame(ds, wanted, lat, lon, method, processes, key_process, logger=None):
    pt = interpolate_to_point(ds, lat, lon, processes, key_process, method, logger)
    names = resolve_names(pt, wanted, processes, key_process, logger)
    cols, units = {}, {}
    for short, real in names.items():
        da = pt[real]
        extra = [d for d in da.dims if d != "time"]
        if extra:
            da = da.isel({d: 0 for d in extra})
        cols[short] = da.values.astype("float64")
        units[short] = ds[real].attrs.get("units", "")
    df = pd.DataFrame(cols, index=pd.DatetimeIndex(pt["time"].values, name="time"))
    h_era = None
    if "z" in pt.data_vars:
        z = pt["z"]
        z = z.isel({d: 0 for d in z.dims}) if z.dims else z
        h_era = float(z.values) / constants.G
    return df, units, h_era

def fetch(*, dataset, tag, api_key, request, names, lat, lon, method, processes, key_process,
    download_dir, all_units, h_era_values, max_wait_s, logger=None):
    check_cancel(processes, key_process)
    client = make_client(api_key)
    target = os.path.join(download_dir, f"{tag}.download")
    msg = f"Requesting ERA5 {tag} ({len(request['variable'])} variables in 1 request)..."
    if logger: logger.info(msg)
    set_status(processes, key_process, "running", msg)
    retrieve(client, dataset, request, target, logger, processes, key_process, f"cds:{key_process}", max_wait_s)
    ds = open_download(target, os.path.join(download_dir, tag), processes, key_process, logger)
    try: df, units, h_era = block_to_frame(ds, names, lat, lon, method, processes, key_process, logger)
    finally: ds.close()
    all_units.update(units)
    if h_era is not None: h_era_values.append(h_era)
    if logger: logger.info(f"{tag}: {len(df)} hourly steps")
    set_status(processes, key_process, "running", f"Downloaded {tag}")
    return df

def apply_elevation_correction(df, h_era, h_site, logger):
    dh = h_site - h_era
    logger.info(f"Elevation correction: site {h_site:.1f} m, ERA5 terrain {h_era:.1f} m, Δh = {dh:+.1f} m")
    t_era = df["t2m"].copy() if "t2m" in df else None
    for col, lapse in (("t2m", constants.LAPSE_T), ("skt", constants.LAPSE_T), ("d2m", constants.LAPSE_TD)):
        if col in df:
            df[col] = df[col] - lapse * dh
            logger.info(f"  {col}: {-lapse * dh:+.2f} K")
    if "d2m" in df and "t2m" in df:
        df["d2m"] = np.minimum(df["d2m"], df["t2m"])
    if "sp" in df:
        t_ref = t_era if t_era is not None else pd.Series(288.15 - constants.LAPSE_T * h_era, index=df.index)
        t_mean = t_ref - constants.LAPSE_T * dh / 2.0
        factor = np.exp(-constants.G * dh / (constants.RD * t_mean))
        df["sp"] = df["sp"] * factor
        logger.info(f"  sp: ×{float(factor.mean()):.4f} (mean)")
    return df

def convert_units(df, units):
    for col in df.columns:
        u = (units.get(col) or "").replace(" ", "")
        if col in constants.TO_MM: df[col] = df[col] * 1000.0
        elif col in constants.TO_DEGC: df[col] = df[col] - 273.15
        elif col in constants.TO_HPA: df[col] = df[col] / 100.0
        elif col in constants.TO_PERCENT: df[col] = df[col] * 100.0
        elif u in ("Jm**-2", "Jm-2"): df[col] = df[col] / 3600.0
    return df

def add_wind(df):
    u, v = df["u10"], df["v10"]
    df["wind_speed"] = np.hypot(u, v)
    df["wind_direction"] = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
    return df

def active_job_ids(client, limit=100):
    if DSClient is None or not isinstance(client, DSClient):
        return []
    return client.get_jobs(limit=limit, status=cast(Any, constants.ACTIVE_STATES)).request_ids

def delete_active_jobs(api_key, datasets=(constants.DATASET_GRID, constants.DATASET_POINT), logger=None):
    client, deleted = make_client(api_key), 0
    for rid in active_job_ids(client):
        try:
            remote = client.get_remote(rid) # type: ignore[attr-defined]
            if datasets and remote.collection_id not in datasets: continue
            remote.delete()
            deleted += 1
            if logger: logger.info(f"Deleted CDS request {rid} ({remote.collection_id})")
        except Exception as e:
            if logger:
                logger.warning(f"Could not delete CDS request {rid}: {e}")
    return deleted

def _check_location(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 360):
        raise ERA5Error(f"Invalid location: lat={lat}, lon={lon}")
    return lat, lon

def _parse_period(start, end, time_zone):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    end_is_date = len(str(end).strip()) <= 10
    s = s.tz_localize(time_zone) if s.tzinfo is None else s.tz_convert(time_zone)
    e = e.tz_localize(time_zone) if e.tzinfo is None else e.tz_convert(time_zone)
    if end_is_date: e = e + pd.Timedelta(days=1)
    start_utc, end_utc = s.tz_convert("UTC"), e.tz_convert("UTC")
    if start_utc >= end_utc: raise ERA5Error("Start time must be before end time.")
    return start_utc, end_utc

def _check_latency(end_utc):
    latest = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=constants.ERA5_LATENCY_DAYS))
    if end_utc - pd.Timedelta(hours=1) > latest:
        raise ERA5Error(f"ERA5 is only available until about {latest:%Y-%m-%d} (≈{constants.ERA5_LATENCY_DAYS} days delay).")

def _nve_gts_wind(lat, lon, start_utc, end_utc, processes, process_key, logger):
    url = os.getenv("NVE_GTS_URL")
    if not url: raise ERA5Error("NVE_GTS_URL is not configured.")
    gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy([lon], [lat]), crs="EPSG:4326").to_crs("EPSG:25833")
    x, y = int(round(gdf.geometry.x.iloc[0])), int(round(gdf.geometry.y.iloc[0]))
    start_date = start_utc.strftime("%Y-%m-%d")
    end_date = (end_utc - pd.Timedelta(seconds=1)).strftime("%Y-%m-%d")
    logger.info(f"NVE GTS cell (EPSG:25833): x={x}, y={y} | {start_date} -> {end_date}")
    series = {}
    with requests.Session() as s:
        for theme_id, label in constants.NVE_THEMES.items():
            check_cancel(processes, process_key)
            set_status(processes, process_key, "running", f"Downloading NVE GTS {theme_id}...")
            logger.info(f"Downloading theme: {theme_id}")
            r = s.get(f"{url.rstrip('/')}/{x}/{y}/{start_date}/{end_date}/{theme_id}.json", timeout=120)
            r.raise_for_status(); data = r.json(); values = data.get("Data") or []
            if not values: raise ERA5Error(f"NVE GTS returned no data for {theme_id} (location outside Norway?).")
            t_start = pd.to_datetime(data["StartDate"], format="%d.%m.%Y %H:%M:%S")
            step = pd.Timedelta(minutes=int(data["TimeResolution"]))
            idx = pd.date_range(start=t_start, periods=len(values), freq=step, tz="UTC")
            ser = pd.Series(values, index=idx, dtype="float64")
            nodata = data.get("NoDataValue")
            if nodata is not None: ser = ser.mask(ser == float(nodata))
            series[label] = ser
    df = pd.DataFrame(series).sort_index()
    df = df[(df.index >= start_utc) & (df.index < end_utc)]
    if df.empty: raise ERA5Error("NVE GTS returned no data in the requested period.")
    n_missing = int(df.isna().sum().sum())
    if n_missing: logger.warning(f"{n_missing} missing value(s) in NVE GTS data (left empty).")
    return df

def era5_point_series(api_key, lat, lon, start_utc, end_utc, cds_vars, processes, process_key,
    workdir, logger, method="bilinear", h_site=None, max_wait_s=6 * 3600):
    if method not in ("bilinear", "nearest"):
        raise ERA5Error(f"Unknown interpolation method: {method}")
    t0 = start_utc.tz_convert(None).to_pydatetime()
    t1 = (end_utc - pd.Timedelta(hours=1)).tz_convert(None).to_pydatetime()
    wanted = list(dict.fromkeys(cds_vars.values()))
    do_elev = h_site is not None and bool(constants.ELEV_SENSITIVE & set(wanted))
    glat, glon = snap_to_era5_grid(lat, lon)
    if method == "bilinear":
        for (la, lo), w in bilinear_weights(lat, lon).items():
            logger.info(f"  ERA5 corner ({la}, {lo}) weight {w:.3f}")
    else: logger.info(f"  Nearest ERA5 grid point: ({glat}, {glon})")
    logger.info(f"UTC period: {t0} -> {t1} | variables: {', '.join(cds_vars)}")
    os.makedirs(workdir, exist_ok=True)
    all_units, h_era, frames, done = {}, [], [], False
    common = dict(api_key=api_key, lat=lat, lon=lon, method=method, processes=processes,
        key_process=process_key, download_dir=workdir, all_units=all_units,
        h_era_values=h_era, max_wait_s=max_wait_s, logger=logger)
    if method == "nearest":
        try:
            req = {"variable": list(cds_vars), "location": {"latitude": glat, "longitude": glon},
                "date": [f"{t0:%Y-%m-%d}/{t1:%Y-%m-%d}"], "data_format": "netcdf"}
            ts = fetch(dataset=constants.DATASET_POINT, tag="timeseries", request=req, names=wanted, **common)
            if do_elev:
                area, _, _ = single_point_area(lat, lon)
                fetch(dataset=constants.DATASET_GRID, tag="orography", request=orography_request(t0, area), names=["z"], **common)
            frames, done = [ts], True
        except DownloadCancelled: raise
        except Exception as e:
            logger.warning(f"Time-series dataset failed ({e}). Falling back to gridded download.")
            frames.clear(); h_era.clear()
    if not done:
        area = cell_area(lat, lon) if method == "bilinear" else single_point_area(lat, lon)[0]
        logger.info(f"Download area [N, W, S, E]: {area}")
        req_vars = list(cds_vars) + (["geopotential"] if do_elev else [])
        names = wanted + (["z"] if do_elev else [])
        months = list(month_requests(t0, t1, req_vars, area))
        for i, (tag, req) in enumerate(months, 1):
            logger.info(f"[{i}/{len(months)}] {tag}")
            df = fetch(dataset=constants.DATASET_GRID, tag=tag, request=req, names=names, **common)
            frames.append(df.drop(columns=["z"], errors="ignore"))
    check_cancel(processes, process_key)
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.loc[(df.index >= t0) & (df.index <= t1)]
    if df.empty: raise ERA5Error("No data in the requested time range.")
    expected = int((t1 - t0).total_seconds() // 3600) + 1
    if len(df) < expected: logger.warning(f"{expected - len(df)} hourly step(s) missing from ERA5 response.")
    if df.isna().any().any():
        logger.info(f"Missing values per column: {df.isna().sum()[lambda s: s > 0].to_dict()}")
        df = df.interpolate(limit_direction="both")
    if do_elev:
        if h_era: df = apply_elevation_correction(df, float(np.mean(h_era)), h_site, logger)
        else: logger.warning("ERA5 orography not available -> no elevation correction.")
    df.index = pd.DatetimeIndex(df.index).tz_localize("UTC")
    return df

def _save_csv(df, csv_path, index_label=None):
    tmp = csv_path + ".part"
    if index_label is None: df.to_csv(tmp, index=False)
    else: df.to_csv(tmp, index_label=index_label)
    os.replace(tmp, csv_path)

def _meteo_wind(directory, source, lat, lon, hours, processes, process_key, logger, 
    elevation=None, auto_elevation=True, elevation_correction=True, workers=6, om_model=None):
    from services import data_functions
    set_status(processes, process_key, "running", f"Point download started.")
    check_cancel(processes, process_key)
    weather_functions.validate_request(source, None, hours[0].to_pydatetime(), hours[-1].to_pydatetime())
    columns = list(constants.DERIVED)
    if not columns: raise ERA5Error("Please select at least one variable.")
    logger.info(f"Variables: {', '.join(columns)}"); h_site = None
    if elevation_correction and {"t2m", "rh"} & set(columns):
        h_site = elevation_detector(["t2m"], lat, lon, elevation, auto_elevation, logger)
    logger.info("=" * 100)
    if source == "open-meteo":
        df, h_src, cell = weather_functions.open_meteo_point(lat, lon, hours, logger, processes, process_key, model=om_model)
        logger.info(f"Open-Meteo grid cell ({cell[0]}, {cell[1]}), terrain {h_src} m. Free use is non-commercial only.")
    else:
        cache_dir = os.path.join(directory, f"metpoint_cache_{weather_functions.short_hash([round(lat, 3), round(lon, 3)])}")
        df, h_src, cell = weather_functions.met_nordic_point(lat, lon, hours, logger, processes, process_key, cache_dir, workers)
    check_cancel(processes, process_key)
    set_status(processes, process_key, "running", "Processing data...")
    if df.isna().all().all(): raise ERA5Error("No data in the requested time range.")
    if h_site is not None and h_src is None:
        logger.warning("Source terrain height unknown -> no elevation correction.")
    out = data_functions.point_table(df, hours, lat, lon, ["wind_speed", "wind_direction"], h_src, h_site, logger)
    out.index = pd.DatetimeIndex(out.index).tz_localize("UTC")
    return out.rename(columns={
        "wind_speed": constants.NVE_THEMES["windSpeed10m1h"],
        "wind_direction": constants.NVE_THEMES["windDirection10m1h"]}
    )

def wind_job(directory, logger, workdir, csv_path, **kwargs):
    api_key, source = kwargs["api_key"], kwargs["source"]
    start, end, time_zone = kwargs["start"], kwargs["end"], kwargs["time_zone"]
    processes, process_key = kwargs["processes"], kwargs["process_key"]
    method, max_wait_hours = kwargs["method"], kwargs["max_wait_hours"]
    la, lo = _check_location(kwargs["lat"], kwargs["lon"])
    start_utc, end_utc = _parse_period(start, end, time_zone)
    t0 = start_utc.tz_convert(None)
    t1 = (end_utc - pd.Timedelta(hours=1)).tz_convert(None)
    hours = weather_functions.hours(t0, t1)
    if len(hours) == 0: raise ERA5Error("No full hour in the requested time range.")
    label = constants.SOURCES[source]["label"]
    logger.info(f"Source: {label} | location lat={la}, lon={lo}")
    logger.info(f"Local: {start} -> {end} ({time_zone}) | UTC: {hours[0]} -> {hours[-1]} ({len(hours)} hours)")
    logger.info("=" * 70)
    if source == "open-meteo":
        out = _meteo_wind(directory, source, la, lo, hours, processes, process_key, logger)
    elif source == "nve-gts":
        out = _nve_gts_wind(la, lo, start_utc, end_utc, processes, process_key, logger)
    elif source == "era5":
        cds_key = (api_key or os.getenv("CDS_API_KEY") or "").strip()
        if not cds_key: raise ERA5Error("Missing CDS API key.")
        _check_latency(end_utc)
        logger.info(f"Interpolation method: {method}")
        df = era5_point_series(cds_key, la, lo, start_utc, end_utc, constants.WIND_COMPONENTS, processes,
            process_key, workdir, logger, method=method, h_site=None, max_wait_s=max_wait_hours * 3600)
        out = pd.DataFrame(index=df.index)
        out[constants.NVE_THEMES["windSpeed10m1h"]] = np.hypot(df["u10"], df["v10"])
        out[constants.NVE_THEMES["windDirection10m1h"]] = (np.degrees(np.arctan2(-df["u10"], -df["v10"])) + 360.0) % 360.0
    else: raise ERA5Error(f"Unsupported wind source: {source}")
    check_cancel(processes, process_key)
    out = out.round({constants.NVE_THEMES["windSpeed10m1h"]: 3, constants.NVE_THEMES["windDirection10m1h"]: 1})
    out.index = functions.utc_to_local(out.index, time_zone)
    out.index.name = "Time"
    _save_csv(out.reset_index(), csv_path)
    logger.info(f"Wind saved: {csv_path} ({len(out)} rows)")
    logger.info("Weather download completed successfully.")
    return "Weather download completed successfully.\n\n"

def _run_job(directory, processes, process_key, key, logger, job, **kwargs):
    workdir = os.path.join(directory, f"download_{key}_{uuid.uuid4().hex[:8]}")
    csv_path = os.path.normpath(os.path.join(directory, f"{key}.csv"))
    try:
        if os.path.exists(csv_path): os.remove(csv_path)
        set_status(processes, process_key, "running", f"Wind downloader started.")
        check_cancel(processes, process_key); logger.info(f"Wind downloader started.")
        message = job(directory, logger, workdir, csv_path, processes=processes, process_key=process_key, **kwargs)
        set_status(processes, process_key, "finished", message)
    except DownloadCancelled:
        logger.info("=" * 70); logger.info("Download cancelled by user. No data was saved.")
        set_status(processes, process_key, "failed", "Download cancelled by user.", cancelled=True)
    except (ERA5Error, requests.HTTPError) as e:
        logger.error(f"FAILED: {e}"); set_status(processes, process_key, "failed", str(e))
    except Exception as e:
        logger.error("Wind downloader failed:\n" + traceback.format_exc())
        set_status(processes, process_key, "failed", str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if os.path.exists(csv_path + ".part"):
            try: os.remove(csv_path + ".part")
            except OSError: pass
        for h in logger.handlers[:]:
            try: h.flush(); h.close()
            except Exception: pass
            logger.removeHandler(h)

def wind_downloader(directory, api_key, source, processes, process_key, key, lat, lon, 
    start, end, time_zone, logger, method="bilinear", max_wait_hours=2):
    _run_job(directory, processes, process_key, key, logger, wind_job,
        api_key=api_key, source=source, lat=lat, lon=lon, start=start, end=end, 
        time_zone=time_zone, method=method, max_wait_hours=max_wait_hours
    )