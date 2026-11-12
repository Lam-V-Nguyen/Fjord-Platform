"""
Weather forcing for the flow (Wflow) model.

    flow_functions.weather_downloader(..., source, ...)  ->  weather_functions.download_forcing(source, ...)

Every source is first converted to a "source dataset" in ERA5 convention
(variables named like the values of FORCING_SOURCES, ERA5 units, regular latitude/longitude grid,
optional orography as geopotential). The common step `build_forcing_file` then interpolates it to the
DEM grid (Regridder, lapse-rate correction) and writes flows/<flow>/forcing/weather_forcing.nc –
exactly as before for ERA5.

Sources
-------
era5             CDS ERA5 single levels (gridded, request queue)                          – CDS API key
era5-timeseries  CDS ERA5 hourly time series (point = catchment centre, fast)             – CDS API key
met-nordic       MET Norway MET Nordic analysis, 1 km, hourly, 2012-09 → now (Nordic)     – no key
open-meteo       Open-Meteo historical API (point = catchment centre, non-commercial)     – no key
"""
import os, gc, glob, json, shutil, time, threading, hashlib, zipfile, uuid, stat, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import cast, Any
from scipy.spatial import KDTree
import numpy as np, pandas as pd, xarray as xr
import rasterio, geopandas as gpd
from netCDF4 import Dataset, date2num
from services import era5_function, flow_functions
from dask import delayed, compute

try: HAS_DASK = True
except ImportError:
    delayed = compute = None
    HAS_DASK = False

ERA5Error, DownloadCancelled = era5_function.ERA5Error, era5_function.DownloadCancelled
G, MET_GRID_DEG = era5_function.G, 0.01
SIGMA = 5.670374419e-8
_MET_BASE = "https://thredds.met.no/thredds/dodsC"
_MET_URLS = (
    _MET_BASE + "/metpparchivev3/{t:%Y/%m/%d}/met_analysis_1_0km_nordic_{t:%Y%m%dT%H}Z.nc",
    _MET_BASE + "/metpparchive/{t:%Y/%m/%d}/met_analysis_1_0km_nordic_{t:%Y%m%dT%H}Z.nc",
    _MET_BASE + "/metpplatest/met_analysis_1_0km_nordic_{t:%Y%m%dT%H}Z.nc",
)
MET_HOUR_RETRIES = 3; MET_MAX_GAP_H = 6; MET_MAX_MISSING_SHARE = 0.2
_met_url_cache, _met_url_lock = {}, threading.Lock()
SOURCES = {
    "era5": {"label": "ERA5 reanalysis (CDS)", "needs_key": True, "min_date": "1940-01-01", "latency_days": era5_function.ERA5_LATENCY_DAYS},
    "era5-timeseries": {"label": "ERA5 hourly time series (CDS)", "needs_key": True, "min_date": "1940-01-01", "latency_days": era5_function.ERA5_LATENCY_DAYS},
    "met-nordic": {"label": "MET Nordic analysis (MET Norway)", "needs_key": False, "min_date": "2012-09-01", "latency_days": 0},
    "open-meteo": {"label": "Open-Meteo historical API", "needs_key": False, "min_date": "1940-01-01", "latency_days": 2},
}
_MET_VARS = {"precipitation_amount": "precip_mm", "air_temperature_2m": "t_k", "relative_humidity_2m": "rh",
    "air_pressure_at_sea_level": "msl_pa", "wind_speed_10m": "ws", "wind_direction_10m": "wd",
    "cloud_area_fraction": "cloud", "integral_of_surface_downwelling_shortwave_flux_in_air_wrt_time": "sw_j",
    "integral_of_surface_downwelling_longwave_flux_in_air_wrt_time": "lw_j"
}

class MetHourMissing(Exception):
    """This time is not available (404) - don't try again."""

def _remove_dir(path, retries=5, delay=1.0, logger=None):
    """rmtree that also works on Windows: close cached NetCDF handles, clear read-only flags, retry."""
    if not path or not os.path.exists(path): return True
    def _onerror(func, p, _):
        try: os.chmod(p, stat.S_IWRITE); func(p)
        except Exception: pass
    for i in range(retries):
        gc.collect()
        try: xr.backends.file_manager.FILE_CACHE.clear()      # closes files xarray keeps open
        except Exception: pass
        shutil.rmtree(path, onerror=_onerror)
        if not os.path.exists(path): return True
        time.sleep(delay * (i + 1))
    if logger: logger.warning(f"Could not remove {path} (files still in use) – it is removed at the next download.")
    return False

def _clean_stale_downloads(forcing_dir, logger=None):
    """Remove download_* folders left by a failed/cancelled/killed run.
    Safe: only one weather download per project can run at a time (redis lock + _is_active)."""
    for d in glob.glob(os.path.join(forcing_dir, "download_*")):
        if os.path.isdir(d) and _remove_dir(d, retries=2, delay=0.5) and logger:
            logger.info(f"Removed leftover folder: {os.path.basename(d)}")
    for f in glob.glob(os.path.join(forcing_dir, "*.part")):
        try: os.remove(f)
        except OSError: pass

def _validate_request(source, api_key, start_time, end_time):
    if source not in SOURCES:
        raise ERA5Error(f"Unknown weather source '{source}'. Available: {', '.join(SOURCES)}")
    cfg = SOURCES[source]
    if cfg["needs_key"] and (not api_key or not str(api_key).strip()):
        raise ERA5Error("Missing CDS API key.")
    if start_time >= end_time: raise ERA5Error("Start time must be before end time.")
    if start_time < datetime.fromisoformat(cfg["min_date"]):
        raise ERA5Error(f"{cfg['label']} starts on {cfg['min_date']}.")
    latest = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=cfg["latency_days"])
    if end_time > latest:
        raise ERA5Error(f"{cfg['label']} is only available until about {latest:%Y-%m-%d} "
            f"(≈{cfg['latency_days']} days delay).")

def _read_dem(flow_dir, logger):
    raw_path = os.path.join(flow_dir, "raw", "dtm_raw.tif")
    if not os.path.exists(raw_path): raise ERA5Error("No DEM found. Please process terrain first.")
    with rasterio.open(raw_path) as srcr:
        dem = srcr.read(1).astype("float32")
        dem_crs, transform, nodata = srcr.crs, srcr.transform, srcr.nodata
    mask_nan = np.isnan(dem)
    if nodata is not None: mask_nan |= dem == nodata
    dem = np.where(mask_nan, 0.0, dem).astype("float32")
    ny, nx = dem.shape
    x_coords = transform.c + (np.arange(nx) + 0.5) * transform.a
    y_coords = transform.f + (np.arange(ny) + 0.5) * transform.e
    logger.info(f"DEM grid: {ny} × {nx} cells ({ny * nx / 1e6:.2f} M), CRS {dem_crs.to_string()}")
    return dict(dem=dem, mask_nan=mask_nan, crs=dem_crs, x=x_coords, y=y_coords, ny=ny, nx=nx)

def _catchment_bbox(flow_dir):
    catchment_path = os.path.join(flow_dir, "catchment", "catchment.geojson")
    if not os.path.exists(catchment_path):
        raise ERA5Error("No catchment found. Please process terrain first.")
    catchment = gpd.read_file(catchment_path)
    if catchment.crs is None or catchment.crs.to_epsg() != 4326:
        catchment = catchment.to_crs("EPSG:4326")
    return tuple(float(v) for v in catchment.total_bounds)   # west, south, east, north

def _format_interval(seconds):
    seconds = int(round(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds % size == 0: return f"{seconds // size}{unit}"
    return f"{seconds}s"

def _write_forcing_meta(json_path, times_utc, time_zone=None):
    t = pd.DatetimeIndex(times_utc)
    if len(t) == 0: raise ValueError("No time steps to describe.")
    if len(t) > 1:
        steps = pd.Series(t).diff().dt.total_seconds().dropna().to_numpy()
        vals, counts = np.unique(steps, return_counts=True)
        interval = _format_interval(vals[counts.argmax()])
    else: interval = "1h"
    t = t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")
    if time_zone: t = t.tz_convert(time_zone)
    t = t.tz_localize(None)
    meta = {"start": f"{t[0]:%Y-%m-%d %H:%M:%S}", "end": f"{t[-1]:%Y-%m-%d %H:%M:%S}", "interval": interval}
    tmp = json_path + ".part"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(meta, f)
    os.replace(tmp, json_path)
    return meta

def _open_sources(files, time_window):
    if flow_functions.HAS_DASK:
        return xr.open_mfdataset(files, combine="nested", concat_dim="time", chunks={"time": time_window},
            data_vars="minimal", coords=cast(Any, "minimal"), compat="override", parallel=False)
    parts = []
    for f in files:
        with xr.open_dataset(f) as d: parts.append(d.load())
    return xr.concat(parts, dim="time")

def _fill_time_gaps(a):
    if not np.isnan(a).any(): return a
    df = pd.DataFrame(a).interpolate(axis=0, limit_direction="both")
    return df.fillna(0.0).to_numpy(dtype=np.float32)

def _source_window(src, t0, t1):
    win = src.isel(time=slice(t0, t1))
    raw = {}
    for short in flow_functions.FORCING_SOURCES.values():
        da = win[short]
        extra = [d for d in da.dims if d not in ("time", "latitude", "longitude")]
        if extra: da = da.isel({d: 0 for d in extra})
        dims = [d for d in ("time", "latitude", "longitude") if d in da.dims]
        arr = da.transpose(*dims).values.astype(np.float32)
        raw[short] = _fill_time_gaps(arr.reshape(arr.shape[0], -1))
    return {var: fn(raw).astype(np.float32) for var, (_, fn) in flow_functions.FORCING.items()}

def _build_forcing_file(src_info, dem_info, forcing_path, tmp_path, download_dir, processes, process_key, logger,
    method="bilinear", k_idw=4, block_mb=128, n_workers=None, temp_lapse_correction=True):
    files, n_total = src_info["files"], src_info["n_total"]
    lat_src, lon_src, z_src = src_info["lat"], src_info["lon"], src_info.get("z_src")
    dem, mask_nan, dem_crs = dem_info["dem"], dem_info["mask_nan"], dem_info["crs"]
    x_coords, y_coords, ny, nx = dem_info["x"], dem_info["y"], dem_info["ny"], dem_info["nx"]
    n_workers = n_workers or min(4, os.cpu_count() or 1)
    if z_src is not None and z_src.shape != (lat_src.size, lon_src.size):
        logger.warning("Source orography grid differs from data grid -> no temperature lapse-rate correction.")
        z_src = None
    era5_function.check_cancel(processes, process_key)
    era5_function.set_status(processes, process_key, "running", "Preparing interpolation...")
    cy = min(flow_functions.CHUNK_XY, ny)
    tw = int(block_mb * 1e6 // (cy * nx * 4) // flow_functions.CHUNK_T * flow_functions.CHUNK_T)
    tw = max(flow_functions.CHUNK_T, min(tw, 24 * 31, -(-n_total // flow_functions.CHUNK_T) * flow_functions.CHUNK_T))
    nc, src = None, None; regridder = win = None
    try:
        src = _open_sources(files, tw)
        if src.sizes["time"] != n_total:
            raise ERA5Error("Inconsistent number of time steps after merging months.")
        times = pd.to_datetime(src["time"].to_index())
        use_lapse = temp_lapse_correction and z_src is not None
        regridder = flow_functions.Regridder(lat_src, lon_src, x_coords, y_coords, dem_crs, method=method, k_idw=k_idw,
            z_src=z_src if use_lapse else None, cache_dir=download_dir, cache_limit_mb=1024)
        logger.info(f"Interpolation: {regridder.describe()}")
        if use_lapse and regridder.z_src is not None:
            zs = regridder.z_src
            logger.info(f"Temperature lapse-rate correction ON (source terrain {zs.min():.0f}–{zs.max():.0f} m, "
                f"DEM {dem[~mask_nan].min():.0f} - {dem[~mask_nan].max():.0f} m)")
        logger.info(f"Processing: {n_total} hours × {ny}×{nx} cells | window {tw} h × {cy} rows | "
            f"{'dask, ' + str(n_workers) + ' threads' if flow_functions.HAS_DASK else 'sequential (dask not installed)'}")
        if os.path.exists(tmp_path): os.remove(tmp_path)
        nc = Dataset(tmp_path, "w", format="NETCDF4")
        nc.createDimension("time", None); nc.createDimension("y", ny); nc.createDimension("x", nx)
        time_var = nc.createVariable("time", "f8", ("time",))
        time_var.units = "hours since 1900-01-01 00:00:00"
        time_var.calendar, time_var.time_zone = "proleptic_gregorian", "UTC"
        nc.createVariable("y", "f8", ("y",))[:] = y_coords
        nc.createVariable("x", "f8", ("x",))[:] = x_coords
        crs_var = nc.createVariable("crs", "i4")
        crs_var.grid_mapping_name = "latitude_longitude" if dem_crs.is_geographic else "transverse_mercator"
        crs_var.crs_wkt = dem_crs.to_wkt()
        epsg = dem_crs.to_epsg()
        if epsg is not None: crs_var.epsg_code = epsg
        chunks = (flow_functions.CHUNK_T, cy, min(flow_functions.CHUNK_XY, nx))
        nc_vars = {}
        for var, (unit, _) in flow_functions.FORCING.items():
            v = nc.createVariable(var, "f4", ("time", "y", "x"), zlib=True, complevel=4,
                fill_value=flow_functions.NODATA_DEM, shuffle=True, chunksizes=chunks, least_significant_digit=2)
            v.units, v.grid_mapping = unit, "crs"
            nc_vars[var] = v
        nc.source = src_info["label"]
        nc.interpolation = regridder.describe()
        nc.temperature_lapse_correction = f"{flow_functions.LAPSE_T * 1000} K/km" if use_lapse else "none"
        write_lock = threading.Lock()
        row_blocks = [(r0, min(r0 + cy, ny)) for r0 in range(0, ny, cy)]
        def process_block(r0, r1, win, g0, g1):
            era5_function.check_cancel(processes, process_key)
            idx, w, zi = regridder.get(r0, r1)
            rows = r1 - r0; mask_b = mask_nan[r0:r1]; lapse = None
            if use_lapse and zi is not None:
                lapse = (flow_functions.LAPSE_T * (zi.reshape(rows, nx) - dem[r0:r1])).astype(np.float32)
            for var in flow_functions.FORCING:
                out = regridder.apply(win[var], idx, w, rows, nx)
                if var == "temp" and lapse is not None: out += lapse[None, :, :]
                out[:, mask_b] = flow_functions.NODATA_DEM
                with write_lock:
                    nc_vars[var][g0:g1, r0:r1, :] = out
                del out
            return rows
        for w0 in range(0, n_total, tw):
            era5_function.check_cancel(processes, process_key)
            w1 = min(w0 + tw, n_total)
            win = _source_window(src, w0, w1)
            if flow_functions.HAS_DASK:
                tasks = [delayed(process_block)(r0, r1, win, w0, w1) for r0, r1 in row_blocks]
                compute(*tasks, scheduler="threads", num_workers=n_workers)
            else:
                for r0, r1 in row_blocks: process_block(r0, r1, win, w0, w1)
            with write_lock:
                time_var[w0:w1] = date2num(list(times[w0:w1].to_pydatetime()), time_var.units, time_var.calendar)
            msg = f"Interpolating forcing: {100.0 * w1 / n_total:.0f}% ({times[w1 - 1]:%Y-%m-%d %H:%M} UTC)"
            logger.info(msg); era5_function.set_status(processes, process_key, "running", msg)
            del win; gc.collect()
        era5_function.check_cancel(processes, process_key)
        nc.close(); nc = None; src.close(); src = None
        os.replace(tmp_path, forcing_path)
        logger.info(f"Saved forcing file: {forcing_path} ({n_total} hourly steps, UTC)")
        return n_total, times
    finally:
        for o in (nc, src):
            if o is not None:
                try: o.close()
                except Exception: pass
        if regridder is not None and hasattr(regridder, "close"):
            try: regridder.close()    # releases memmap cache files, if implemented
            except Exception: pass
        nc = src = regridder = win = None   # the traceback must not keep these alive
        gc.collect()

# ERA5 grid (CDS)
def era5_area(west, south, east, north, snap=True, buffer=0.01, grid=None):
    grid = grid or era5_function.ERA5_GRID_DEG
    if not snap: return [north + buffer, west - buffer, south - buffer, east + buffer]
    n, s = np.ceil(north / grid) * grid, np.floor(south / grid) * grid
    w, e = np.floor(west / grid) * grid, np.ceil(east / grid) * grid
    if n == s: n += grid
    if e == w: e += grid
    eps = grid * 0.2
    return [round(n + eps, 4), round(w - eps, 4), round(s - eps, 4), round(e + eps, 4)]

def _short_hash(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:8]

def _cache_dir_for(forcing_dir, area, prefix="era5_cache"):
    return os.path.join(forcing_dir, f"{prefix}_{_short_hash([round(a, 4) for a in area])}")

def _clean_old_caches(forcing_dir, keep, logger, prefix="era5_cache"):
    for d in glob.glob(os.path.join(forcing_dir, f"{prefix}_*")):
        if os.path.abspath(d) == os.path.abspath(keep): continue
        try:
            if time.time() - os.path.getmtime(d) > flow_functions.CACHE_MAX_AGE_DAYS * 86400:
                shutil.rmtree(d, ignore_errors=True)
                logger.info(f"Removed old cache: {os.path.basename(d)}")
        except OSError: pass

def _read_single_var(path, short):
    aliases = getattr(flow_functions, "_ALIASES", era5_function._ALIASES)
    try:
        with xr.open_dataset(path) as d: ds = d.load()
    except Exception as e:
        raise ERA5Error(f"{os.path.basename(path)}: cannot open NetCDF ({e})") from e
    if "valid_time" in ds.variables and "time" not in ds.dims: ds = ds.rename({"valid_time": "time"})
    if "expver" in ds.dims: ds = ds.max("expver", skipna=True)
    ds = ds.drop_vars([v for v in ("number", "expver") if v in ds.variables])
    ren = {a: b for a, b in (("lat", "latitude"), ("lon", "longitude")) if a in ds.variables}
    if ren: ds = ds.rename(ren)
    if "time" not in ds.dims: raise ERA5Error(f"{os.path.basename(path)}: no time dimension")
    cands = [v for v in ds.data_vars if "time" in ds[v].dims]
    name = short if short in ds.data_vars else next((a for a in aliases.get(short, ()) if a in ds.data_vars), None)
    if name is None and len(cands) == 1: name = cands[0]
    if name is None: raise ERA5Error(f"{os.path.basename(path)}: variable '{short}' not found (has {cands})")
    da = ds[name]
    extra = [d for d in da.dims if d not in ("time", "latitude", "longitude")]
    if extra: da = da.isel({d: 0 for d in extra})
    if "latitude" not in da.dims or "longitude" not in da.dims:
        raise ERA5Error(f"{os.path.basename(path)}: missing latitude/longitude dimension")
    da = da.transpose("time", "latitude", "longitude").sortby("time")
    _, idx = np.unique(da["time"].values, return_index=True)
    da = da.isel(time=idx)
    if da.sizes["time"] == 0: raise ERA5Error(f"{os.path.basename(path)}: no time steps")
    if bool(np.isnan(da.values).all()): raise ERA5Error(f"{os.path.basename(path)}: all values are missing")
    return da.rename(short)

def _unpack(part_path):
    if not zipfile.is_zipfile(part_path): return part_path
    xdir = part_path + "_x"
    shutil.rmtree(xdir, ignore_errors=True); os.makedirs(xdir)
    with zipfile.ZipFile(part_path) as zf: zf.extractall(xdir)
    ncs = sorted(glob.glob(os.path.join(xdir, "**", "*.nc"), recursive=True))
    if len(ncs) != 1: raise ERA5Error(f"Expected 1 NetCDF in archive, found {len(ncs)}")
    return ncs[0]

def _wait_with_cancel(seconds, processes, process_key, message):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        era5_function.check_cancel(processes, process_key)
        era5_function.set_status(processes, process_key, "running", message)
        time.sleep(1)

def _download_variable(client, api_key, cds_name, short, request, target,
    processes, process_key, logger, max_wait_s, label):
    if os.path.exists(target):
        try:
            _read_single_var(target, short); logger.info(f"{label}: found in cache, skipped"); return target
        except ERA5Error: os.remove(target)
    req = dict(request, variable=[cds_name], download_format="unarchived")
    part = target + ".part"; last_err = None
    for attempt in range(1, flow_functions.DOWNLOAD_RETRIES + 1):
        era5_function.check_cancel(processes, process_key)
        msg = f"Downloading {label} (attempt {attempt}/{flow_functions.DOWNLOAD_RETRIES})..."
        logger.info(msg); era5_function.set_status(processes, process_key, "running", msg)
        lock = era5_function.account_lock(api_key)
        while not lock.acquire(timeout=1):
            era5_function.check_cancel(processes, process_key)
            era5_function.set_status(processes, process_key, "running",
                "Waiting for another ERA5 download with the same CDS account...")
        try:
            for p in (part, part + "_x"):
                if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
                elif os.path.exists(p): os.remove(p)
            era5_function.retrieve(client, era5_function.DATASET_GRID,
                req, part, logger, processes, process_key, f"cds:{process_key}", max_wait_s)
            nc_path = _unpack(part)
            _read_single_var(nc_path, short)
            os.replace(nc_path, target)
            logger.info(f"{label}: OK ({os.path.getsize(target) / 1024:.0f} kB)")
            return target
        except DownloadCancelled: raise
        except Exception as e:
            last_err = e
            if "did not deliver data within" in str(e): raise
            logger.warning(f"{label}: attempt {attempt} failed: {e}")
        finally:
            lock.release()
            shutil.rmtree(part + "_x", ignore_errors=True)
            if os.path.exists(part):
                try: os.remove(part)
                except OSError: pass
        if attempt < flow_functions.DOWNLOAD_RETRIES:
            wait = flow_functions.RETRY_WAIT_S[min(attempt - 1, len(flow_functions.RETRY_WAIT_S) - 1)]
            _wait_with_cancel(wait, processes, process_key, f"{label}: retrying in {wait} s...")
    raise ERA5Error(f"{label}: failed after {flow_functions.DOWNLOAD_RETRIES} attempts ({last_err})")

def _download_orography(client, api_key, start_time, area, cache_dir, processes, process_key, logger, max_wait_s):
    request = era5_function.orography_request(start_time, area)
    target = os.path.join(cache_dir, f"orography_{_short_hash(area)}.nc")
    _download_variable(client, api_key, "geopotential", "z", request,
        target, processes, process_key, logger, max_wait_s, label="orography z")
    da = _read_single_var(target, "z").isel(time=0)
    return da["latitude"].values, da["longitude"].values, da.values

def _download_month_by_variable(client, api_key, tag, request, cache_dir, download_dir,
    start_time, end_time, processes, process_key, logger, max_wait_s):
    rkey = _short_hash({k: v for k, v in request.items() if k != "variable"})
    arrays, n_vars = [], len(flow_functions.FORCING_SOURCES)
    for j, (cds_name, short) in enumerate(flow_functions.FORCING_SOURCES.items(), 1):
        target = os.path.join(cache_dir, f"{tag}_{short}_{rkey}.nc")
        _download_variable(client, api_key, cds_name, short, request,
            target, processes, process_key, logger, max_wait_s, label=f"{tag} {short} ({j}/{n_vars})")
        arrays.append(_read_single_var(target, short))
    ref = arrays[0]
    for k, da in enumerate(arrays[1:], 1):
        same = all(da.sizes[c] == ref.sizes[c] and np.allclose(da[c].values, ref[c].values, atol=1e-6)
            for c in ("latitude", "longitude"))
        if not same: raise ERA5Error(f"{tag}: grid of '{da.name}' differs from '{ref.name}'")
        arrays[k] = da.assign_coords(latitude=ref["latitude"], longitude=ref["longitude"])
    ds = xr.merge(arrays, join="outer", compat="override")
    lacking = [da.name for da in arrays if da.sizes["time"] != ds.sizes["time"]]
    if lacking: logger.warning(f"{tag}: {lacking} miss some hours -> filled by time interpolation.")
    ds = ds.sel(time=slice(start_time, end_time))
    if ds.sizes["time"] == 0: raise ERA5Error(f"{tag}: no time steps in the requested range.")
    out = os.path.join(download_dir, f"src_{tag}.nc")
    ds.to_netcdf(out); n = ds.sizes["time"]
    lat, lon = ds["latitude"].values.copy(), ds["longitude"].values.copy()
    ds.close(); logger.info(f"{tag}: merged {n_vars} variables, {n} hourly steps, ERA5 grid {lat.size}×{lon.size}")
    return out, n, lat, lon

def prepare_era5(ctx):
    logger = ctx["logger"]
    processes, process_key, api_key = ctx["processes"], ctx["process_key"], ctx["api_key"]
    start_time, end_time, max_wait_s = ctx["start_time"], ctx["end_time"], ctx["max_wait_s"]
    logger.info("ERA5: one request per variable and month.")
    area = era5_area(*ctx["bbox"], snap=ctx["snap_area"], buffer=ctx["buffer"])
    logger.info(f"ERA5 area [N, W, S, E]: {area}")
    cache_dir = _cache_dir_for(ctx["forcing_dir"], area)
    os.makedirs(cache_dir, exist_ok=True)
    _clean_old_caches(ctx["forcing_dir"], cache_dir, logger)
    client, z_src = era5_function.make_client(api_key), None
    if ctx["temp_lapse_correction"]:
        try:
            _, _, z_src = _download_orography(client, api_key, start_time, area, cache_dir,
                processes, process_key, logger, max_wait_s)
        except DownloadCancelled: raise
        except Exception as e:
            logger.warning(f"ERA5 orography not available ({e}) -> no temperature lapse-rate correction.")
    months = list(era5_function.month_requests(start_time, end_time, list(flow_functions.FORCING_SOURCES), area))
    logger.info(f"{len(months)} month(s) × {len(flow_functions.FORCING_SOURCES)} variables = "
        f"{len(months) * len(flow_functions.FORCING_SOURCES)} requests")
    files, n_total, grid_ref = [], 0, None
    for i, (tag, request) in enumerate(months, 1):
        logger.info(f"===== [{i}/{len(months)}] {tag} =====")
        f, n, lat, lon = _download_month_by_variable(client, api_key, tag, request, cache_dir, ctx["download_dir"],
            start_time, end_time, processes, process_key, logger, max_wait_s)
        if grid_ref is None: grid_ref = (lat, lon)
        elif lat.shape != grid_ref[0].shape or lon.shape != grid_ref[1].shape \
            or not (np.allclose(lat, grid_ref[0]) and np.allclose(lon, grid_ref[1])):
            raise ERA5Error(f"ERA5 grid of {tag} differs from previous months.")
        files.append(f); n_total += n
    if n_total == 0 or grid_ref is None: raise ERA5Error("No data in the requested time range.")
    return {"files": files, "n_total": n_total, "lat": grid_ref[0], "lon": grid_ref[1], "z_src": z_src,
        "cache_dir": cache_dir, "label": "ERA5 reanalysis (Copernicus Climate Change Service)"}

# ERA5 point/time-series
def _hours(start_time, end_time):
    return pd.date_range(pd.Timestamp(start_time).ceil("h"), pd.Timestamp(end_time).floor("h"), freq="h")

def _magnus_es(t_c):
    return 6.112 * np.exp(17.67 * t_c / (t_c + 243.5))   # hPa

def _longwave_estimate(t_k, rh, cloud):
    """Incoming longwave [W/m²]: Brutsaert clear-sky emissivity + linear cloud correction."""
    ea = _magnus_es(t_k - 273.15) * np.clip(rh, 0, 1)
    eps = 1.24 * (ea / t_k) ** (1 / 7) * (1 - np.clip(cloud, 0, 1)) + np.clip(cloud, 0, 1)
    return eps * SIGMA * t_k ** 4

def toa_hourly(times, lat2d, lon2d, substeps=6):
    """TOA incident solar radiation accumulated over the hour ending at each time [J/m²] (as ERA5 'tisr')."""
    lat = np.radians(lat2d)[None]; out = np.zeros((len(times),) + lat2d.shape, np.float32)
    for k in range(substeps):
        t = pd.DatetimeIndex(times) - pd.Timedelta(minutes=60 * (k + 0.5) / substeps)
        doy = t.dayofyear.values[:, None, None]; hr = (t.hour + t.minute / 60).values[:, None, None]
        g = 2 * np.pi * (doy - 1 + (hr - 12) / 24) / 365
        decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
        eot = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
            - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))                     # minutes
        e0 = 1.00011 + 0.034221 * np.cos(g) + 0.00128 * np.sin(g) + 0.000719 * np.cos(2 * g) + 0.000077 * np.sin(2 * g)
        ha = np.radians((hr + lon2d[None] / 15 + eot / 60 - 12) * 15)
        cosz = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha)
        out += (1361.0 * e0 * np.clip(cosz, 0, None) * 3600 / substeps).astype(np.float32)
    return out

def _required_shorts() -> list:
    return list(dict.fromkeys(flow_functions.FORCING_SOURCES.values()))

def to_era5_fields(base, times, lat2d, lon2d, z_m, source_label):
    t_k = base.get("t_k")
    def td():
        if "td_k" in base: return base["td_k"]
        rh = np.clip(base["rh"], 1e-3, 1); t_c = t_k - 273.15
        a = np.log(rh) + 17.67 * t_c / (t_c + 243.5)
        return 243.5 * a / (17.67 - a) + 273.15
    def rh():
        if "rh" in base: return base["rh"]
        return _magnus_es(base["td_k"] - 273.15) / _magnus_es(t_k - 273.15)
    def uv():
        if "u" in base: return base["u"], base["v"]
        wd = np.radians(base["wd"]); return -base["ws"] * np.sin(wd), -base["ws"] * np.cos(wd)
    def msl():
        if "msl_pa" in base: return base["msl_pa"]
        return base["sp_pa"] * np.exp(G * z_m / (287.05 * (t_k + 0.0065 * z_m / 2)))
    def sp():
        if "sp_pa" in base: return base["sp_pa"]
        return base["msl_pa"] * np.exp(-G * z_m / (287.05 * (t_k + 0.0065 * z_m / 2)))
    def strd():
        if "lw_j" in base: return base["lw_j"]
        return _longwave_estimate(t_k, rh(), base["cloud"]) * 3600
    makers = {
        "tp": lambda: np.clip(base["precip_mm"], 0, None) / 1000.0, "t2m": lambda: t_k, "d2m": td,
        "ssrd": lambda: np.clip(base["sw_j"], 0, None), "strd": strd,
        "tisr": lambda: toa_hourly(times, lat2d, lon2d), "msl": msl, "sp": sp,
        "u10": lambda: uv()[0], "v10": lambda: uv()[1], "tcc": lambda: base["cloud"],
    }
    out = {}
    for short in _required_shorts():
        if short not in makers:
            raise ERA5Error(f"{source_label} cannot provide the ERA5 variable '{short}' required by FORCING_SOURCES.")
        try: out[short] = np.asarray(makers[short](), dtype=np.float32)
        except KeyError as e:
            raise ERA5Error(f"{source_label} has no data to derive '{short}' (missing {e}).")
    return out

def _write_source_nc(path, times, lat, lon, fields):
    order = np.argsort(lat)[::-1]
    ds = xr.Dataset({k: (("time", "latitude", "longitude"), v[:, order, :]) for k, v in fields.items()},
        coords={"time": pd.DatetimeIndex(times), "latitude": lat[order], "longitude": lon})
    ds.to_netcdf(path); ds.close()
    return lat[order]

def _point_source(ctx, times, base, label, z_m=None):
    """Point series -> 2×2 grid with identical values around the catchment centre."""
    west, south, east, north = ctx["bbox"]
    clat, clon, d = (south + north) / 2, (west + east) / 2, 0.05
    lat, lon = np.array([clat - d, clat + d]), np.array([clon - d, clon + d])
    LAT, LON = np.meshgrid(lat, lon, indexing="ij")
    grid = {k: np.repeat(np.repeat(np.asarray(v, np.float32)[:, None, None], 2, 1), 2, 2) for k, v in base.items()}
    fields = to_era5_fields(grid, times, LAT, LON, float(z_m or 0.0), label)
    path = os.path.join(ctx["download_dir"], "src_point.nc")
    lat_desc = _write_source_nc(path, times, lat, lon, fields)
    z_src = np.full((2, 2), z_m * G, np.float32) if z_m is not None else None
    return {"files": [path], "n_total": len(times), "lat": lat_desc, "lon": lon, "z_src": z_src,
        "cache_dir": None, "label": label}

def prepare_era5_timeseries(ctx):
    logger = ctx["logger"]
    west, south, east, north = ctx["bbox"]
    lat, lon = round((south + north) / 2, 3), round((west + east) / 2, 3)
    hours = _hours(ctx["start_time"], ctx["end_time"])
    cds_vars = [c for c, s in flow_functions.FORCING_SOURCES.items() if s != "tisr"]       # tisr is computed
    logger.info(f"ERA5 time series: point lat={lat}, lon={lon} (catchment centre), {len(hours)} hours")
    logger.info(f"Variables: {cds_vars}")
    request = {"variable": cds_vars, "location": {"latitude": lat, "longitude": lon},
        "date": [f"{hours[0]:%Y-%m-%d}/{hours[-1]:%Y-%m-%d}"], "data_format": "netcdf"}
    target = os.path.join(ctx["download_dir"], "era5_timeseries.download")
    client = era5_function.make_client(ctx["api_key"])
    era5_function.retrieve(client, era5_function.DATASET_POINT, request, target, logger,
        ctx["processes"], ctx["process_key"], f"cds:{ctx['process_key']}", ctx["max_wait_s"])
    ds = era5_function.open_download(target, os.path.join(ctx["download_dir"], "ts"),
        ctx["processes"], ctx["process_key"], logger)
    try:
        if "latitude" in ds.dims or "longitude" in ds.dims:
            ds = ds.isel({d: 0 for d in ("latitude", "longitude") if d in ds.dims})
        ds = ds.sel(time=slice(hours[0], hours[-1]))
        series = {}
        for _, short in flow_functions.FORCING_SOURCES.items():
            if short == "tisr": continue
            name = short if short in ds.data_vars else next(
                (a for a in era5_function._ALIASES.get(short, ()) if a in ds.data_vars), None)
            if name is None: raise ERA5Error(f"ERA5 time series has no '{short}' ({list(ds.data_vars)})")
            series[short] = pd.Series(ds[name].values.astype(float), index=pd.DatetimeIndex(ds["time"].values))
    finally: ds.close()
    df = pd.DataFrame(series).groupby(level=0).mean().reindex(hours).interpolate(limit_direction="both")
    # Already in ERA5 convention -> pass through (to_era5_fields only adds tisr)
    base = {"precip_mm": df["tp"] * 1000 if "tp" in df else None, "t_k": df.get("t2m"), "td_k": df.get("d2m"),
        "sw_j": df.get("ssrd"), "lw_j": df.get("strd"), "u": df.get("u10"), "v": df.get("v10"),
        "msl_pa": df.get("msl"), "sp_pa": df.get("sp"), "cloud": df.get("tcc")}
    base = {k: v.values for k, v in base.items() if v is not None}
    return _point_source(ctx, hours, base, "ERA5 hourly time series (CDS, point series)")

# MET Nordic
def _met_url(t, timeout=30):
    key = f"{pd.Timestamp(t):%Y%m%d%H}"
    with _met_url_lock:
        if key in _met_url_cache: return _met_url_cache[key]
    net_error = None
    for pattern in _MET_URLS:
        url = pattern.format(t=pd.Timestamp(t))
        try:
            with requests.get(url + ".dds", timeout=timeout, stream=True) as r:
                head = next(r.iter_content(64), b"") if r.status_code == 200 else b""
                if r.status_code >= 500: net_error = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            net_error = e; continue
        if head.lstrip().startswith(b"Dataset"):
            with _met_url_lock: _met_url_cache[key] = url
            return url
    if net_error is not None: raise ConnectionError(f"thredds.met.no not reachable ({net_error})")
    with _met_url_lock: _met_url_cache[key] = None
    return None

def _open_met(t):
    url = _met_url(t)
    if url is None: raise MetHourMissing(f"{t:%Y-%m-%d %H}Z not found on thredds.met.no (404)")
    return xr.open_dataset(url)

def _months(hours):
    for _, grp in pd.Series(hours, index=hours).groupby([hours.year, hours.month]):
        yield f"{grp.index[0].year}_{grp.index[0].month:02d}", pd.DatetimeIndex(grp.index)

def _met_hour(t, win, idx, shape, retries=MET_HOUR_RETRIES):
    y0, y1, x0, x1 = win; last = None
    for k in range(retries):
        try:
            with _open_met(t) as ds:
                sub = ds[list(_MET_VARS)].isel(time=0, y=slice(y0, y1), x=slice(x0, x1)).load()
            return {name: sub[v].values.ravel()[idx].reshape(shape) for v, name in _MET_VARS.items()}
        except MetHourMissing: raise
        except Exception as e:
            last = e; time.sleep(2 * (k + 1))
    raise RuntimeError(f"read failed after {retries} attempts: {last}")

def _max_run(index, missing):
    flags = np.isin(index.values, pd.DatetimeIndex(missing).values)
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best
 
def _fill_time_gaps_3d(a, prev=None):
    if not np.isnan(a).any(): return a
    t, ny, nx = a.shape
    flat = a.reshape(t, -1)
    if prev is not None: flat = np.vstack([np.asarray(prev, np.float32).reshape(1, -1), flat])
    df = pd.DataFrame(flat).interpolate(axis=0, limit_direction="both")
    out = df.to_numpy(dtype=np.float32)
    if prev is not None: out = out[1:]
    return out.reshape(t, ny, nx)
 
def _cached_tail(path):
    try:
        with xr.open_dataset(path) as ds:
            last = ds.isel(time=-1).load()
            asc = np.argsort(last["latitude"].values)
            return {k: last[k].values[asc, :].astype(np.float32) for k in _required_shorts() if k in last}
    except Exception: return None

def prepare_met_nordic(ctx, workers=6):
    logger, processes, process_key = ctx["logger"], ctx["processes"], ctx["process_key"]
    west, south, east, north = ctx["bbox"]; pad = max(ctx["buffer"], 0.02)
    lat_t = np.arange(np.floor((south - pad) / MET_GRID_DEG), np.ceil((north + pad) / MET_GRID_DEG) + 1) * MET_GRID_DEG
    lon_t = np.arange(np.floor((west - pad) / MET_GRID_DEG), np.ceil((east + pad) / MET_GRID_DEG) + 1) * MET_GRID_DEG
    hours = _hours(ctx["start_time"], ctx["end_time"])
    if len(hours) == 0: raise ERA5Error("No full hour in the requested time range.")
    era5_function.set_status(processes, process_key, "running", "MET Nordic: checking data availability...")
    probe = pd.DatetimeIndex([hours[0], hours[len(hours) // 2], hours[-1]]).unique()
    available = []
    for t in probe:
        try:
            if _met_url(t.to_pydatetime()) is not None: available.append(t)
        except ConnectionError as e:
            raise ERA5Error(f"MET Nordic: {e}. Try again later or check https://status.met.no.")
    if not available:
        raise ERA5Error(
            "MET Nordic data are not available for the requested period on thredds.met.no. "
            "Choose another source (ERA5, ERA5 time series or Open-Meteo) or check https://status.met.no.")
    era5_function.set_status(processes, process_key, "running", "MET Nordic: reading the grid...")
    with _open_met(available[0].to_pydatetime()) as ds:
        lat, lon, alt = ds["latitude"].values, ds["longitude"].values, ds["altitude"].values
    inside = (lat >= lat_t.min() - 0.02) & (lat <= lat_t.max() + 0.02) & (lon >= lon_t.min() - 0.04) & (lon <= lon_t.max() + 0.04)
    if not inside.any(): raise ERA5Error("The catchment lies outside the MET Nordic domain (Nordic countries).")
    ys, xs = np.where(inside); win = (ys.min(), ys.max() + 1, xs.min(), xs.max() + 1)
    wl, wn = lat[win[0]:win[1], win[2]:win[3]], lon[win[0]:win[1], win[2]:win[3]]
    coslat = np.cos(np.radians((south + north) / 2))
    tree = KDTree(np.column_stack([wl.ravel(), wn.ravel() * coslat]))
    LAT, LON = np.meshgrid(lat_t, lon_t, indexing="ij")
    _, idx = tree.query(np.column_stack([LAT.ravel(), LON.ravel() * coslat]))
    shape = LAT.shape
    z_m = alt[win[0]:win[1], win[2]:win[3]].ravel()[idx].reshape(shape).astype(np.float32)
    logger.info(f"MET Nordic: window {win[1]-win[0]}×{win[3]-win[2]} cells (1 km) -> regular {shape[0]}×{shape[1]} "
        f"grid of {MET_GRID_DEG}°, terrain {z_m.min():.0f}–{z_m.max():.0f} m, {len(hours)} hours")
    cache_dir = _cache_dir_for(ctx["forcing_dir"], [west, south, east, north], prefix="metnordic_cache")
    os.makedirs(cache_dir, exist_ok=True)
    _clean_old_caches(ctx["forcing_dir"], cache_dir, logger, prefix="metnordic_cache")
    files, n_total, done, prev_tail, n_missing_all = [], 0, 0, None, 0
    for tag, mh in _months(hours):
        target = os.path.join(cache_dir, f"{tag}_{_short_hash([str(mh[0]), str(mh[-1]), _required_shorts()])}.nc")
        if os.path.exists(target):
            logger.info(f"{tag}: found in cache, skipped")
            files.append(target); n_total += len(mh); done += len(mh)
            prev_tail = _cached_tail(target); continue
        data, errors = {}, {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_met_hour, t.to_pydatetime(), win, idx, shape): t for t in mh}
            for f in as_completed(futs):
                if era5_function.is_cancel_requested(processes, process_key):
                    for g in futs: g.cancel()
                    era5_function.check_cancel(processes, process_key)
                t = futs[f]
                try: data[t] = f.result()
                except Exception as e: errors[t] = f"{type(e).__name__}: {str(e)[:150]}"  # noqa: BLE001
                done += 1
                if done % 24 == 0 or done == len(hours):
                    msg = f"MET Nordic: {done}/{len(hours)} hours ({100 * done / len(hours):.0f}%)"
                    logger.info(msg); era5_function.set_status(processes, process_key, "running", msg)
        n_bad, run = len(errors), _max_run(mh, list(errors))
        if n_bad:
            examples = "; ".join(f"{t:%Y-%m-%d %H}Z -> {msg}" for t, msg in sorted(errors.items())[:3])
            logger.warning(f"{tag}: {n_bad}/{len(mh)} hour(s) could not be read (longest gap {run} h). Examples: {examples}")
        too_long = run > MET_MAX_GAP_H
        too_many = n_bad > MET_MAX_GAP_H and n_bad / len(mh) > MET_MAX_MISSING_SHARE
        no_anchor = not data and prev_tail is None
        if too_long or too_many or no_anchor:
            raise ERA5Error(f"{tag}: {n_bad}/{len(mh)} MET Nordic hours could not be read "
                f"(longest gap {run} h) - too many to fill by interpolation. "
                f"First error: {next(iter(sorted(errors.items())))[1] if errors else '-'}")
        n_missing_all += n_bad
        nan = np.full(shape, np.nan, np.float32)
        base = {name: np.stack([data.get(t, {}).get(name, nan) for t in mh]).astype(np.float32)
            for name in _MET_VARS.values()}
        wd = np.radians(base.pop("wd")); ws = base.pop("ws")
        base["u"], base["v"] = -ws * np.sin(wd), -ws * np.cos(wd)
        fields = to_era5_fields(base, mh, LAT, LON, z_m[None], "MET Nordic")
        if n_bad:
            fields = {k: _fill_time_gaps_3d(v, None if prev_tail is None else prev_tail.get(k))
                for k, v in fields.items()}
            if not data:
                logger.warning(f"{tag}: no hour readable -> {len(mh)} hour(s) filled from the previous hour.")
        prev_tail = {k: v[-1].copy() for k, v in fields.items()}
        part = os.path.join(ctx["download_dir"], f"met_{tag}.nc")
        _write_source_nc(part, mh.tz_localize(None) if mh.tz else mh, lat_t, lon_t, fields)
        os.replace(part, target); files.append(target); n_total += len(mh)
        logger.info(f"{tag}: {len(mh)} hourly steps written" + (f" ({n_bad} interpolated)" if n_bad else ""))
    if n_missing_all:
        logger.info(f"MET Nordic: {n_missing_all}/{len(hours)} hour(s) in total were filled by time interpolation.")
    return {"files": files, "n_total": n_total, "lat": lat_t[::-1].copy(), "lon": lon_t,
        "z_src": (z_m[::-1] * G).astype(np.float32), "cache_dir": cache_dir,
        "label": "MET Nordic analysis 1 km (MET Norway)"}

def prepare_open_meteo(ctx):
    import requests
    logger = ctx["logger"]; west, south, east, north = ctx["bbox"]
    lat, lon = round((south + north) / 2, 4), round((west + east) / 2, 4)
    hours = _hours(ctx["start_time"], ctx["end_time"])
    logger.info(f"Open-Meteo: point lat={lat}, lon={lon} (catchment centre), {len(hours)} hours. "
        "Free use is non-commercial only.")
    era5_function.check_cancel(ctx["processes"], ctx["process_key"])
    r = requests.get(METEO_URL, timeout=180, params={
        "latitude": lat, "longitude": lon, "start_date": f"{hours[0]:%Y-%m-%d}", "end_date": f"{hours[-1]:%Y-%m-%d}",
        "hourly": "precipitation,temperature_2m,relative_humidity_2m,shortwave_radiation,wind_speed_10m,"
            "wind_direction_10m,pressure_msl,surface_pressure,cloud_cover",
        "wind_speed_unit": "ms", "timezone": "GMT"})
    if r.status_code == 429: raise ERA5Error("Open-Meteo: request limit reached (HTTP 429). Try again later.")
    if r.status_code != 200: raise ERA5Error(f"Open-Meteo: HTTP {r.status_code} – {r.text[:200]}")
    js = r.json(); h = js["hourly"]
    df = pd.DataFrame(h).assign(time=lambda d: pd.to_datetime(d["time"])).set_index("time")
    df = df.reindex(hours).interpolate(limit_direction="both")
    base = {"precip_mm": df["precipitation"], "t_k": df["temperature_2m"] + 273.15,
        "rh": df["relative_humidity_2m"] / 100, "sw_j": df["shortwave_radiation"] * 3600,
        "ws": df["wind_speed_10m"], "wd": df["wind_direction_10m"], "msl_pa": df["pressure_msl"] * 100,
        "sp_pa": df["surface_pressure"] * 100, "cloud": df["cloud_cover"] / 100}
    if "strd" in _required_shorts():
        logger.info("Open-Meteo has no longwave radiation -> estimated from temperature, humidity and cloud cover.")
    elev = js.get("elevation")
    return _point_source(ctx, hours, {k: v.values for k, v in base.items()},
        "Open-Meteo historical weather API (point series)", z_m=float(elev) if elev is not None else None)

def download_forcing(source, flow_dir, api_key, start_time, end_time, processes, process_key, 
    logger, time_zone, buffer=0.01, method="bilinear", snap_area=True, temp_lapse_correction=True, 
    max_wait_hours=6, block_mb=128, n_workers=None, k_idw=4):
    _validate_request(source, api_key, start_time, end_time)
    forcing_dir = os.path.join(flow_dir, "forcing")
    os.makedirs(forcing_dir, exist_ok=True)
    _clean_stale_downloads(forcing_dir, logger)  # leftovers of earlier failed/cancelled runs
    download_dir = os.path.join(forcing_dir, f"download_{uuid.uuid4().hex[:8]}")
    forcing_path = os.path.join(forcing_dir, "weather_forcing.nc")
    meta_path = os.path.splitext(forcing_path)[0] + ".json"
    tmp_path, success, cache_dir = forcing_path + ".part", False, None
    src_info = dem_info = None
    try:
        dem_info = _read_dem(flow_dir, logger)
        bbox = _catchment_bbox(flow_dir)
        os.makedirs(download_dir, exist_ok=True)
        ctx = dict(source=source, api_key=api_key, start_time=start_time, end_time=end_time, 
            bbox=bbox, forcing_dir=forcing_dir, download_dir=download_dir, processes=processes, 
            process_key=process_key, logger=logger, max_wait_s=max_wait_hours * 3600, 
            buffer=buffer, snap_area=snap_area, temp_lapse_correction=temp_lapse_correction)
        logger.info(f"Source: {SOURCES[source]['label']} | UTC {start_time} -> {end_time}")
        logger.info(f"Source: Catchment bbox {np.round(bbox, 4).tolist()}")
        src_info = PROVIDERS[source](ctx)
        cache_dir = src_info.get("cache_dir")
        n_total, times = _build_forcing_file(src_info, dem_info, forcing_path, tmp_path, download_dir, 
            processes, process_key, logger, method=method, k_idw=k_idw, block_mb=block_mb, 
            n_workers=n_workers, temp_lapse_correction=temp_lapse_correction
        )
        try:
            meta = _write_forcing_meta(meta_path, times, time_zone)
            logger.info(f"Saved time info: {meta_path} {meta}"
                + ("" if time_zone else " (no time zone given -> UTC)"))
        except Exception as e:
            try: os.remove(meta_path)
            except OSError: pass
            logger.warning(f"Could not write {os.path.basename(meta_path)}: {e}")
        success = True
        return n_total
    finally:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except OSError: pass
        ctx = src_info = dem_info = None       # drop references before deleting the files
        _remove_dir(download_dir, logger=logger)
        if success and cache_dir: _remove_dir(cache_dir, logger=logger)

PROVIDERS = {"era5": prepare_era5, "era5-timeseries": prepare_era5_timeseries,
    "met-nordic": prepare_met_nordic, "open-meteo": prepare_open_meteo
}