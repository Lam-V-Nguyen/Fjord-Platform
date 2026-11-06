import os, math, requests, cdsapi, threading, time, zipfile, glob, shutil, hashlib
from dateutil.relativedelta import relativedelta
from datetime import datetime, timedelta
import xarray as xr, numpy as np, pandas as pd
from typing import cast, Any

try: from ecmwf.datastores import Client as DSClient
except ImportError: DSClient = None

DATASET_GRID = "reanalysis-era5-single-levels"
DATASET_POINT = "reanalysis-era5-single-levels-timeseries"
KARTVERKET_URL = "https://ws.geonorge.no/hoydedata/v1/punkt"
ERA5_GRID_DEG, ERA5_LATENCY_DAYS, CHUNK_T = 0.25, 6, 24
DERIVED = {"wind_speed", "wind_direction"}
WIND_COMPONENTS = {"10m_u_component_of_wind": "u10", "10m_v_component_of_wind": "v10"}
ELEV_SENSITIVE = {"t2m", "d2m", "skt", "sp"}
_TO_MM = {"tp", "cp", "lsp", "sf", "e", "pev", "ro", "sro", "ssro", "smlt", "es"}
_TO_DEGC = {"t2m", "d2m", "skt", "sst", "stl1", "stl2", "stl3", "stl4"}
_TO_HPA = {"msl", "sp"}
_TO_PERCENT = {"tcc", "lcc", "mcc", "hcc"}
_ALIASES = {"fg10": ("i10fg",), "i10fg": ("fg10",)}
EXTERNAL_LOGGERS = ("cdsapi", "datapi", "ecmwf", "multiurl")
_ACTIVE_STATES = ["accepted", "running"]
G = 9.80665          # m/s²
RD = 287.05          # J/(kg·K)
LAPSE_T = 0.0065     # K/m  (temperature)
LAPSE_TD = 0.0018    # K/m  (dew point temperature)
_account_locks: dict = {}
_account_locks_guard = threading.Lock()


variables = {
    'total_precipitation': 'tp', # Precipitation
    '2m_temperature': 't2m', # Temperature
    '10m_u_component_of_wind': 'u10', '10m_v_component_of_wind': 'v10', # Wind
    "mean_sea_level_pressure": "msl",  # Pressure
    'surface_solar_radiation_downwards': 'ssrd', # Surface shortwave radiation (downward)
    'toa_incident_solar_radiation': 'tisr', # TOA incident solar radiation (shortwave)
    'total_cloud_cover': 'tcc', # Cloud cover
}

var_revert = {
    'tp': 'Total Precipitation (mm)', 't2m': 'Temperature (°C)',
    'u10': 'Wind (u Component) (m/s)', 'v10': 'Wind (v Component) (m/s)',
    'msl': 'Mean Sea Level Pressure (hPa)', 'tcc': 'Total Cloud Cover (%)',
    'ssrd': 'Shortwave Radiation (W/m²)', 'tisr': 'TOA Incident Solar Radiation (W/m²)',
    'Time': 'Time', 'wind_speed': 'Wind Speed (m/s)',
    'wind_direction': 'Wind Direction (°)',
}

class ERA5Error(Exception):
    pass
 
class DownloadCancelled(ERA5Error):
    pass

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
    with _account_locks_guard:
        return _account_locks.setdefault(k, threading.Lock())


def check_cancel(processes, key):
    if is_cancel_requested(processes, key):
        raise DownloadCancelled("Download cancelled by user.")

def make_client(api_key):
    url = os.getenv("CDS_URL")
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
    unknown = [v for v in selected if v not in variables and v not in DERIVED]
    if unknown and logger: logger.warning(f"Ignoring unknown variables: {unknown}")
    cds_vars = {
        v: variables[v] for v in selected if v in variables and v not in DERIVED
    }
    if not cds_vars: return [], False, {}
    wants_wind = any(v in DERIVED for v in selected)
    wants_wind = True if wants_wind else False
    wanted = list(dict.fromkeys(cds_vars.values()))
    return wanted, wants_wind, cds_vars

def get_site_elevation_kartverket(lat, lon, timeout=15):
    """Surface elevation (m) from Kartverket DTM 1 m. In case of Na Uy; exception -> None."""
    try:
        r = requests.get(KARTVERKET_URL, params={
            "koordsys": 4258, "nord": lat, "ost": lon, "geojson": "false"}, timeout=timeout)
        r.raise_for_status()
        pts = r.json().get("punkter") or []
        z = pts[0].get("z") if pts else None
        return float(z) if z is not None else None
    except Exception: return None

def elevation_detector(wanted:list[str], lat:float, lon:float, elevation:float | None, auto_elevation:bool, logger=None) -> float | None:
    if not wanted or not (ELEV_SENSITIVE & set(wanted)): return None
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

def snap_to_era5_grid(lat, lon, grid=ERA5_GRID_DEG):
    glat = round(round(lat / grid) * grid, 4)
    glon = round(round(lon / grid) * grid, 4)
    return glat, glon

def single_point_area(lat, lon, grid=ERA5_GRID_DEG):
    glat, glon = snap_to_era5_grid(lat, lon, grid)
    d = grid * 0.4
    return [glat + d, glon - d, glat - d, glon + d], glat, glon

def enclosing_cell(lat, lon, grid=ERA5_GRID_DEG):
    lat_s = math.floor(lat / grid) * grid
    lon_w = math.floor(lon / grid) * grid
    return round(lat_s, 4), round(lat_s + grid, 4), round(lon_w, 4), round(lon_w + grid, 4)

def cell_area(lat, lon, grid=ERA5_GRID_DEG):
    lat_s, lat_n, lon_w, lon_e = enclosing_cell(lat, lon, grid)
    e = grid * 0.2
    return [lat_n + e, lon_w - e, lat_s - e, lon_e + e]

def bilinear_weights(lat, lon, grid=ERA5_GRID_DEG):
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
        alias = next((a for a in _ALIASES.get(s, ()) if a in ds.data_vars), None)
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
        h_era = float(z.values) / G
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
    for col, lapse in (("t2m", LAPSE_T), ("skt", LAPSE_T), ("d2m", LAPSE_TD)):
        if col in df:
            df[col] = df[col] - lapse * dh
            logger.info(f"  {col}: {-lapse * dh:+.2f} K")
    if "d2m" in df and "t2m" in df:
        df["d2m"] = np.minimum(df["d2m"], df["t2m"])
    if "sp" in df:
        t_ref = t_era if t_era is not None else pd.Series(288.15 - LAPSE_T * h_era, index=df.index)
        t_mean = t_ref - LAPSE_T * dh / 2.0
        factor = np.exp(-G * dh / (RD * t_mean))
        df["sp"] = df["sp"] * factor
        logger.info(f"  sp: ×{float(factor.mean()):.4f} (mean)")
    return df

def convert_units(df, units):
    for col in df.columns:
        u = (units.get(col) or "").replace(" ", "")
        if col in _TO_MM: df[col] = df[col] * 1000.0
        elif col in _TO_DEGC: df[col] = df[col] - 273.15
        elif col in _TO_HPA: df[col] = df[col] / 100.0
        elif col in _TO_PERCENT: df[col] = df[col] * 100.0
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
    return client.get_jobs(limit=limit, status=cast(Any, _ACTIVE_STATES)).request_ids

def delete_active_jobs(api_key, datasets=(DATASET_GRID, DATASET_POINT), logger=None):
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