import os, shutil, traceback, requests, uuid
from config import PROJECT_ROOT
from services import flow_functions, functions, era5_function
import pandas as pd, numpy as np, geopandas as gpd
from datetime import datetime, timedelta, timezone

dataset, resolution, delta = 'reanalysis-era5-single-levels', 0.25, 0.125
METEO_VARIABLES = {
    '2m_temperature': 't2m', # Air Temperature
    '2m_dewpoint_temperature': 'd2m', # Dew Point Temperature
    'total_cloud_cover': 'tcc', # Cloud Cover
}
WIND_VARIABLES = {
    "10m_u_component_of_wind": "u10", "10m_v_component_of_wind": "v10",
}
NVE_THEMES = {"windSpeed10m1h": "Wind Speed_10m (m/s)", "windDirection10m1h": "Wind Direction_10m (°)"}


def _run_job(name, project_name, processes, process_key, key, log_file, job, **kwargs):
    project_dir = os.path.join(PROJECT_ROOT, project_name)
    log_dir = os.path.join(project_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, os.path.basename(log_file))
    try:
        if os.path.exists(log_path): os.remove(log_path)
    except OSError: pass
    logger = flow_functions.setup_logger(f"{key}.{process_key}", log_path)
    workdir = os.path.join(project_dir, f"download_{key}_{uuid.uuid4().hex[:8]}")
    csv_path = os.path.normpath(os.path.join(project_dir, f"{key}.csv"))
    try:
        era5_function.set_status(processes, process_key, "running", f"{name} started.")
        era5_function.check_cancel(processes, process_key)
        logger.info(f"{name} started")
        message = job(logger, workdir, csv_path, processes=processes, process_key=process_key, **kwargs)
        era5_function.set_status(processes, process_key, "finished", message)
    except era5_function.DownloadCancelled:
        logger.info("=" * 70)
        logger.info("Download cancelled by user. No data was saved.")
        era5_function.set_status(processes, process_key, "failed", "Download cancelled by user.", cancelled=True)
    except (era5_function.ERA5Error, requests.HTTPError) as e:
        logger.error(f"FAILED: {e}")
        era5_function.set_status(processes, process_key, "failed", str(e))
    except Exception as e:
        logger.error(f"{name} failed:\n" + traceback.format_exc())
        era5_function.set_status(processes, process_key, "failed", str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if os.path.exists(csv_path + ".part"):
            try: os.remove(csv_path + ".part")
            except OSError: pass
        for h in logger.handlers[:]:
            try:
                h.flush()
                h.close()
            except Exception: pass
            logger.removeHandler(h)

def _save_csv(df, csv_path, index_label=None):
    tmp = csv_path + ".part"
    if index_label is None: df.to_csv(tmp, index=False)
    else: df.to_csv(tmp, index_label=index_label)
    os.replace(tmp, csv_path)

def _relative_humidity(t_c, td_c):
    """Relative humidity (%) from temperature and dew point (°C), Magnus equation (Bolton 1980)."""
    es = 6.112 * np.exp(17.67 * t_c / (t_c + 243.5))
    e = 6.112 * np.exp(17.67 * td_c / (td_c + 243.5))
    return np.clip(100.0 * e / es, 0.0, 100.0)

def _check_location(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 360):
        raise era5_function.ERA5Error(f"Invalid location: lat={lat}, lon={lon}")
    return lat, lon

def _parse_period(start, end, time_zone):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    s = s.tz_localize(time_zone) if s.tzinfo is None else s.tz_convert(time_zone)
    e = (e.tz_localize(time_zone) if e.tzinfo is None else e.tz_convert(time_zone)) + pd.Timedelta(days=1)
    start_utc, end_utc = s.tz_convert("UTC"), e.tz_convert("UTC")
    if start_utc >= end_utc: raise era5_function.ERA5Error("Start time must be before end time.")
    return start_utc, end_utc

def _check_latency(end_utc):
    latest = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=era5_function.ERA5_LATENCY_DAYS))
    if end_utc - pd.Timedelta(hours=1) > latest:
        raise era5_function.ERA5Error(
            f"ERA5 is only available until about {latest:%Y-%m-%d} (≈{era5_function.ERA5_LATENCY_DAYS} days delay).")

def era5_point_series(api_key, lat, lon, start_utc, end_utc, cds_vars, processes, process_key,
    workdir, logger, method="bilinear", h_site=None, max_wait_s=6 * 3600):
    if method not in ("bilinear", "nearest"):
        raise era5_function.ERA5Error(f"Unknown interpolation method: {method}")
    t0 = start_utc.tz_convert(None).to_pydatetime()
    t1 = (end_utc - pd.Timedelta(hours=1)).tz_convert(None).to_pydatetime()
    wanted = list(dict.fromkeys(cds_vars.values()))
    do_elev = h_site is not None and bool(era5_function.ELEV_SENSITIVE & set(wanted))
    glat, glon = era5_function.snap_to_era5_grid(lat, lon)
    if method == "bilinear":
        for (la, lo), w in era5_function.bilinear_weights(lat, lon).items():
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
            ts = era5_function.fetch(
                dataset=era5_function.DATASET_POINT, tag="timeseries", request=req, names=wanted, **common
            )
            if do_elev:
                area, _, _ = era5_function.single_point_area(lat, lon)
                era5_function.fetch(dataset=era5_function.DATASET_GRID, tag="orography",
                    request=era5_function.orography_request(t0, area), names=["z"], **common)
            frames, done = [ts], True
        except era5_function.DownloadCancelled: raise
        except Exception as e:
            logger.warning(f"Time-series dataset failed ({e}). Falling back to gridded download.")
            frames.clear()
            h_era.clear()
    if not done:
        area = era5_function.cell_area(lat, lon) if method == "bilinear" else era5_function.single_point_area(lat, lon)[0]
        logger.info(f"Download area [N, W, S, E]: {area}")
        req_vars = list(cds_vars) + (["geopotential"] if do_elev else [])
        names = wanted + (["z"] if do_elev else [])
        months = list(era5_function.month_requests(t0, t1, req_vars, area))
        for i, (tag, req) in enumerate(months, 1):
            logger.info(f"[{i}/{len(months)}] {tag}")
            df = era5_function.fetch(dataset=era5_function.DATASET_GRID, tag=tag, request=req, names=names, **common)
            frames.append(df.drop(columns=["z"], errors="ignore"))
    era5_function.check_cancel(processes, process_key)
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.loc[(df.index >= t0) & (df.index <= t1)]
    if df.empty: raise era5_function.ERA5Error("No data in the requested time range.")
    expected = int((t1 - t0).total_seconds() // 3600) + 1
    if len(df) < expected: logger.warning(f"{expected - len(df)} hourly step(s) missing from ERA5 response.")
    if df.isna().any().any():
        logger.info(f"Missing values per column: {df.isna().sum()[lambda s: s > 0].to_dict()}")
        df = df.interpolate(limit_direction="both")
    if do_elev:
        if h_era: df = era5_function.apply_elevation_correction(df, float(np.mean(h_era)), h_site, logger)
        else: logger.warning("ERA5 orography not available -> no elevation correction.")
    df.index = pd.DatetimeIndex(df.index).tz_localize("UTC")
    return df

def _nve_gts_wind(lat, lon, start_utc, end_utc, processes, process_key, logger):
    url = os.getenv("NVE_GTS_URL")
    if not url: raise era5_function.ERA5Error("NVE_GTS_URL is not configured.")
    gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy([lon], [lat]), crs="EPSG:4326").to_crs("EPSG:25833")
    x, y = int(round(gdf.geometry.x.iloc[0])), int(round(gdf.geometry.y.iloc[0]))
    start_date = start_utc.strftime("%Y-%m-%d")
    end_date = (end_utc - pd.Timedelta(seconds=1)).strftime("%Y-%m-%d")
    logger.info(f"NVE GTS cell (EPSG:25833): x={x}, y={y} | {start_date} -> {end_date}")
    series = {}
    with requests.Session() as s:
        for theme_id, label in NVE_THEMES.items():
            era5_function.check_cancel(processes, process_key)
            era5_function.set_status(processes, process_key, "running", f"Downloading NVE GTS {theme_id}...")
            logger.info(f"Downloading theme: {theme_id}")
            r = s.get(f"{url.rstrip('/')}/{x}/{y}/{start_date}/{end_date}/{theme_id}.json", timeout=120)
            r.raise_for_status()
            data = r.json()
            values = data.get("Data") or []
            if not values: raise era5_function.ERA5Error(f"NVE GTS returned no data for {theme_id} (location outside Norway?).")
            t_start = pd.to_datetime(data["StartDate"], format="%d.%m.%Y %H:%M:%S")
            step = pd.Timedelta(minutes=int(data["TimeResolution"]))
            idx = pd.date_range(start=t_start, periods=len(values), freq=step, tz="UTC")
            ser = pd.Series(values, index=idx, dtype="float64")
            nodata = data.get("NoDataValue")
            if nodata is not None: ser = ser.mask(ser == float(nodata))
            series[label] = ser
    df = pd.DataFrame(series).sort_index()
    df = df[(df.index >= start_utc) & (df.index < end_utc)]
    if df.empty: raise era5_function.ERA5Error("NVE GTS returned no data in the requested period.")
    n_missing = int(df.isna().sum().sum())
    if n_missing: logger.warning(f"{n_missing} missing value(s) in NVE GTS data (left empty).")
    return df

def meteo_job(logger, workdir, csv_path, **kwargs):
    api_key, source = kwargs["api_key"], kwargs["source"]
    processes, process_key = kwargs["processes"], kwargs["process_key"]
    lat, lon = kwargs["lat"],  kwargs["lon"]
    start, end, time_zone = kwargs["start"],  kwargs["end"], kwargs["time_zone"]
    method, elevation = kwargs["method"], kwargs["elevation"]
    auto_elevation, max_wait_hours = kwargs["auto_elevation"], kwargs["max_wait_hours"]
    if source != "era5": raise era5_function.ERA5Error(f"Unsupported meteo source: {source}")
    cds_key = (api_key or os.getenv("CDS_API_KEY") or "").strip()
    if not cds_key: raise era5_function.ERA5Error("Missing CDS API key.")
    la, lo = _check_location(lat, lon)
    start_utc, end_utc = _parse_period(start, end, time_zone)
    _check_latency(end_utc)
    logger.info(f"Location: lat={la}, lon={lo} | method: {method}")
    logger.info(f"Local period: {start} 00:00 -> {end} 23:00 ({time_zone})")
    h_site = era5_function.elevation_detector(list(METEO_VARIABLES.values()), la, lo, elevation, auto_elevation, logger)
    df = era5_point_series(cds_key, la, lo, start_utc, end_utc, METEO_VARIABLES, processes,
        process_key, workdir, logger, method=method, h_site=h_site, max_wait_s=max_wait_hours * 3600)
    t = df["t2m"] - 273.15
    td = np.minimum(df["d2m"] - 273.15, t)
    out = pd.DataFrame(index=df.index)
    out["Humidity (%)"] = _relative_humidity(t, td)
    out["Air temperature (°C)"] = t
    out["Cloud coverage (%)"] = np.clip(df["tcc"] * 100.0, 0.0, 100.0)
    out = out.round(3)
    out.index = functions.utc_to_local(out.index, time_zone)
    era5_function.check_cancel(processes, process_key)
    _save_csv(out, csv_path, index_label="Time")
    logger.info(f"Meteo saved: {csv_path} ({len(out)} rows)")
    return "\nMeteo download completed.\n\n"

def wind_job(logger, workdir, csv_path, **kwargs):
    api_key, source = kwargs["api_key"], kwargs["source"]
    start, end, time_zone = kwargs["start"],  kwargs["end"], kwargs["time_zone"]
    lat, lon = kwargs["lat"],  kwargs["lon"]
    processes, process_key = kwargs["processes"], kwargs["process_key"]
    method, max_wait_hours = kwargs["method"], kwargs["max_wait_hours"]
    la, lo = _check_location(lat, lon)
    start_utc, end_utc = _parse_period(start, end, time_zone)
    logger.info(f"Location: lat={la}, lon={lo} | source: {source}")
    logger.info(f"Local period: {start} 00:00 -> {end} 23:00 ({time_zone})")
    if source == "nve-gts":
        df = _nve_gts_wind(la, lo, start_utc, end_utc, processes, process_key, logger)
        df.index = functions.utc_to_local(df.index, time_zone)
        df.index.name = "Time"
        era5_function.check_cancel(processes, process_key)
        _save_csv(df.reset_index(), csv_path)
        logger.info(f"Wind saved: {csv_path} ({len(df)} rows)")
        return "\nWeather download completed successfully.\n\n"
    if source != "era5": raise era5_function.ERA5Error(f"Unsupported wind source: {source}")
    cds_key = (api_key or os.getenv("CDS_API_KEY") or "").strip()
    if not cds_key: raise era5_function.ERA5Error("Missing CDS API key.")
    _check_latency(end_utc)
    logger.info(f"Interpolation method: {method}")
    df = era5_point_series(cds_key, la, lo, start_utc, end_utc, WIND_VARIABLES, processes,
        process_key, workdir, logger, method=method, h_site=None, max_wait_s=max_wait_hours * 3600)
    out = pd.DataFrame(index=df.index)
    out["Magnitude (m/s)"] = np.hypot(df["u10"], df["v10"]).round(3)
    out["Angle (°)"] = ((np.degrees(np.arctan2(-df["u10"], -df["v10"])) + 360.0) % 360.0).round(1)
    out.index = functions.utc_to_local(out.index, time_zone)
    era5_function.check_cancel(processes, process_key)
    _save_csv(out, csv_path, index_label="Time")
    logger.info(f"Wind saved: {csv_path} ({len(out)} rows)")
    return "\nWind download completed.\n\n"

def meteo_downloader(project_name, api_key, source, processes, process_key, lat, lon, start, end, 
    time_zone, key, log_file, method="bilinear", elevation=None, auto_elevation=True, max_wait_hours=2):
    _run_job("Meteo downloader", project_name, processes, process_key, key, log_file, meteo_job,
        api_key=api_key, source=source, lat=lat, lon=lon, start=start, end=end, time_zone=time_zone, 
        method=method, elevation=elevation, auto_elevation=auto_elevation, max_wait_hours=max_wait_hours
    )
    
def wind_downloader(project_name, api_key, source, processes, process_key, lat, lon, 
    start, end, time_zone, key, log_file, method="bilinear", max_wait_hours=2):
    _run_job("Wind downloader", project_name, processes, process_key, key, log_file, wind_job,
        api_key=api_key, source=source, lat=lat, lon=lon, start=start, end=end, 
        time_zone=time_zone, method=method, max_wait_hours=max_wait_hours
    )