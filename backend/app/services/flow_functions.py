import os, dotenv, rasterio, zipfile, rioxarray, sys, pyflwdir, rioxarray, stat
import logging, gc, shutil, traceback, subprocess, threading, re, time, psutil
import geopandas as gpd, numpy as np, pandas as pd, xarray as xr
from shapely.geometry import shape
from scipy.spatial import KDTree
from scipy.ndimage import distance_transform_edt
from rasterio.io import MemoryFile
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes
from services import functions, flow_functions, \
    wflow_config, era5_function, weather_functions
from pyflwdir import dem
from config import PROJECT_ROOT, WFLOW_PATH
from dask.distributed import get_client
from dask import config
from pyproj import CRS, Transformer


try: HAS_DASK = True
except ImportError: HAS_DASK = False


dotenv.load_dotenv()
MET_url, MET_client_id = os.getenv('MET_ProstAPI_URL'), os.getenv('MET_ProstAPI_CLIENT_ID')
NVE_url, NVE_client_id = os.getenv('NVE_URL'), os.getenv('NVE_API_KEY')
NODATA_DEM, NODATA_INT, NODATA_SOIL_THICKNESS, G = -9999.0, 0, -99999, 9.80665
CHUNK_T, CHUNK_XY, CACHE_MAX_AGE_DAYS = 24, 128, 2
LAPSE_T = 0.0065                       # K/m
ISRIC_URL = "https://files.isric.org/soilgrids/former/2017-03-10/data/"
DOWNLOAD_RETRIES, RETRY_WAIT_S = 3, (30, 120, 300)
FORCING = {
    "precip": ("mm",    lambda s: np.clip(s["tp"] * 1000.0, 0.0, None)),
    "temp": ("degC",  lambda s: s["t2m"] - 273.15),
    "kin": ("W/m^2", lambda s: np.clip(s["ssrd"] / 3600.0, 0.0, None)),
    "kout": ("W/m^2", lambda s: np.clip(s["tisr"] / 3600.0, 0.0, None)),
    "wind": ("m/s",   lambda s: np.hypot(s["u10"], s["v10"])),
    "press_msl": ("hPa",   lambda s: s["msl"] / 100.0),
}
FORCING_SOURCES = {
    "total_precipitation": "tp", # Precipitation
    "2m_temperature": "t2m", # Temperature
    "surface_solar_radiation_downwards": "ssrd", # Shortwave radiation
    "toa_incident_solar_radiation": "tisr", # Top-of-atmosphere incident solar radiation
    "10m_u_component_of_wind": "u10", # U-component of wind
    "10m_v_component_of_wind": "v10", # V-component of wind
    "mean_sea_level_pressure": "msl", # Mean sea level pressure
}


soils = [
    ['Clay', 'Sand', 'Silt', 'Bulk density', 'Soil organic carbon', 'Soil pH'],
    ['0', '5', '15', '30', '60', '100', '200']
]
soil_types = {
    'bulk density': ['BLDFIE_M', 'bd', 1000, np.float32, -9999.0, "g/cm3"],
    'clay': ['CLYPPT_M', 'clyppt', 1, np.float32, -9999.0, "%"], 
    'sand': ['SNDPPT_M', 'sndppt', 1, np.float32, -9999.0, "%"],
    'silt': ['SLTPPT_M', 'sltppt', 1, np.float32, -9999.0, "%"],  
    'organic carbon': ['ORCDRC_M', 'oc', 10, np.float32, -9999.0, "%"], 
    'pH': ['PHIHOX_M', 'ph', 10, np.float32, -9999.0, "-"]
}
soil_depths = {
    '0cm': 'sl1', '5cm': 'sl2', '15cm': 'sl3', '30cm': 'sl4', 
    '60cm': 'sl5', '100cm': 'sl6', '200cm': 'sl7'
}
soil_type_reverse = {
    'clyppt': 'Clay', 'sndppt': 'Sand', 'sltppt': 'Silt', 
    'bd': 'Bulk density', 'oc': 'Soil organic carbon', 'ph': 'Soil pH'
}
soil_depth_reverse = {v: k for k, v in soil_depths.items()}


class StreamToLogger:
    PROGRESS_PATTERN = re.compile(r'\[\s*[#=]*\s*\]\s*\|\s*\d+%\s*Completed\s*\|')
    def __init__(self, logger, level=logging.INFO, log_path=None):
        self.logger = logger
        self.level = level
        self.log_path = log_path
    def update_logger(self, new_logger):
        self.logger = new_logger
    def write(self, buf):
        if not buf: return
        if '\r' in buf or self.PROGRESS_PATTERN.search(buf):
            progress = buf.split('\r')[-1].strip()
            if progress and self.PROGRESS_PATTERN.search(progress):
                self._update_progress(progress)
                return
        buf = buf.strip()
        if not buf: return
        for line in buf.splitlines():
            self.logger.log(self.level, line.rstrip())
    def _update_progress(self, progress):
        if not self.log_path: return
        # Format exactly like logging.Formatter
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        record = logging.LogRecord(
            name=self.logger.name, level=self.level, pathname="",
            lineno=0, msg=progress, args=(), exc_info=None
        )
        new_line = formatter.format(record)
        try:
            with open(self.log_path, "r+", encoding="utf-8") as f:
                lines = f.readlines()
                progress_index = None
                for i in range(len(lines) - 1, -1, -1):
                    if self.PROGRESS_PATTERN.search(lines[i]):
                        progress_index = i
                        break
                if progress_index is not None:
                    lines[progress_index] = new_line + "\n"
                else: lines.append(new_line + "\n")
                f.seek(0)
                f.writelines(lines)
                f.truncate()
        except OSError: pass
    def flush(self):
        pass

class Regridder:
    def __init__(self, lat_src, lon_src, x_coords, y_coords, dem_crs, method="bilinear",
        k_idw=4, z_src=None, cache_dir=None, cache_limit_mb=1024):
        self.lat = np.asarray(lat_src, dtype="float64").ravel()
        self.lon = np.asarray(lon_src, dtype="float64").ravel()
        self.nlat, self.nlon = self.lat.size, self.lon.size
        self.npts = self.nlat * self.nlon
        self.single = self.npts == 1
        if method not in ("bilinear", "idw", "nearest"):
            raise era5_function.ERA5Error(f"Unknown interpolation method: {method}")
        self.method = "single" if self.single else method
        self.k = {"single": 1, "bilinear": 4, "nearest": 1, "idw": min(k_idw, self.npts)}[self.method]
        self.x = np.asarray(x_coords, dtype="float64")
        self.y = np.asarray(y_coords, dtype="float64")
        self.crs = CRS.from_user_input(dem_crs)
        self.to_ll = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        self.z_src = None if z_src is None else np.asarray(z_src, dtype="float64").ravel() / G
        nbytes = self.y.size * self.x.size * self.k * 8
        self.in_memory = cache_dir is None or nbytes <= cache_limit_mb * 1e6
        self.cache_dir = cache_dir
        self._cache, self._lock = {}, threading.Lock()
        if self.method == "idw":
            lat0, lon0 = float(self.lat.mean()), float(self.lon.mean())
            self.metric = CRS.from_proj4(f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +units=m +ellps=WGS84")
            to_m = Transformer.from_crs("EPSG:4326", self.metric, always_xy=True)
            lon2d, lat2d = np.meshgrid(self.lon, self.lat)
            sx, sy = to_m.transform(lon2d.ravel(), lat2d.ravel())
            self.tree = KDTree(np.column_stack([sx, sy]))
            self.dem_to_m = Transformer.from_crs(self.crs, self.metric, always_xy=True)
    def describe(self):
        if self.single:
            return "1 ERA5 point -> uniform value over the whole grid"
        return f"{self.nlat}×{self.nlon} ERA5 points -> {self.method} interpolation (k={self.k})"
    @staticmethod
    def _axis(values, coords):
        order = np.argsort(coords)
        asc = coords[order]
        n = asc.size
        if n == 1:
            z = np.zeros(values.size, dtype=np.int64)
            return z, z, np.zeros(values.size)
        f = np.interp(values, asc, np.arange(n, dtype="float64"))
        i0 = np.clip(np.floor(f).astype(np.int64), 0, n - 2)
        return order[i0], order[i0 + 1], f - i0
    def _compute(self, r0, r1):
        xx, yy = np.meshgrid(self.x, self.y[r0:r1])
        if self.single:
            idx = np.zeros((xx.size, 1), dtype=np.int32)
            w = np.ones((xx.size, 1), dtype=np.float32)
        elif self.method == "idw":
            mx, my = self.dem_to_m.transform(xx.ravel(), yy.ravel())
            dist, idx = self.tree.query(np.column_stack([mx, my]), k=self.k)
            dist, idx = dist.reshape(-1, self.k), idx.reshape(-1, self.k)
            w = 1.0 / (dist + 1e-6) ** 2
            w /= w.sum(axis=1, keepdims=True)
            idx, w = idx.astype(np.int32), w.astype(np.float32)
        else:
            lon, lat = self.to_ll.transform(xx.ravel(), yy.ravel())
            if self.lon.max() > 180 and lon.min() < 0:
                lon = np.where(lon < 0, lon + 360.0, lon)
            i0, i1, fi = self._axis(lat, self.lat)
            j0, j1, fj = self._axis(lon, self.lon)
            if self.method == "nearest":
                i = np.where(fi >= 0.5, i1, i0)
                j = np.where(fj >= 0.5, j1, j0)
                idx = (i * self.nlon + j)[:, None].astype(np.int32)
                w = np.ones_like(idx, dtype=np.float32)
            else:
                idx = np.stack([i0 * self.nlon + j0, i0 * self.nlon + j1,
                    i1 * self.nlon + j0, i1 * self.nlon + j1], axis=1).astype(np.int32)
                w = np.stack([(1 - fi) * (1 - fj), (1 - fi) * fj, fi * (1 - fj), fi * fj],
                    axis=1).astype(np.float32)
        zi = None if self.z_src is None else (self.z_src[idx] * w).sum(axis=1).astype(np.float32)
        return idx, w, zi
    def get(self, r0, r1):
        key = (r0, r1)
        with self._lock:
            if key in self._cache: return self._cache[key]
        if self.cache_dir is None: raise ValueError("cache_dir is not initialized")
        if not self.in_memory:
            base = os.path.join(self.cache_dir, f"w_{r0}_{r1}")
            if os.path.exists(base + "_w.npy"):
                zi = np.load(base + "_z.npy") if os.path.exists(base + "_z.npy") else None
                return (np.load(base + "_i.npy", mmap_mode="r"), np.load(base + "_w.npy", mmap_mode="r"), zi)
        idx, w, zi = self._compute(r0, r1)
        if self.in_memory:
            with self._lock:
                self._cache[key] = (idx, w, zi)
        else:
            base = os.path.join(self.cache_dir, f"w_{r0}_{r1}")
            np.save(base + "_i.npy", idx)
            if zi is not None: np.save(base + "_z.npy", zi)
            np.save(base + "_w.npy", w)
        return idx, w, zi
    def apply(self, src, idx, w, rows, nx):
        t = src.shape[0]
        if self.single:
            return np.repeat(src[:, :1], rows * nx, axis=1).reshape(t, rows, nx).astype(np.float32)
        out = np.zeros((t, idx.shape[0]), dtype=np.float32)
        for k in range(idx.shape[1]):
            out += src[:, idx[:, k]] * w[:, k]
        return out.reshape(t, rows, nx)


def _interpolate_extrapolate(data, mask_nan, get_nearest=False, power=2, max_neighbors=8):
    h, w = data.shape; mask_valid = ~mask_nan
    y_valid, x_valid = np.where(mask_valid)
    valid_values = data[mask_valid]
    y_fill, x_fill = np.where(mask_nan)
    n_points, n_fill = len(valid_values), len(y_fill)
    result = data.copy()
    if n_points == 0: return np.full((h, w), np.nan)
    if n_fill == 0: return result
    points_valid = np.column_stack((x_valid, y_valid))
    points_fill = np.column_stack((x_fill, y_fill))
    tree = KDTree(points_valid)
    if not get_nearest: # interpolate using IDW
        k = min(max_neighbors, n_points)
        distances, indices = tree.query(points_fill, k=k)
        if k == 1: filled_values = valid_values[indices]
        else:
            distances = np.maximum(distances, 1e-8)
            weights = 1.0 / (distances ** power)
            weights = weights / weights.sum(axis=1, keepdims=True)
            neighbor_values = valid_values[indices]
            filled_values = np.sum(weights * neighbor_values, axis=1)
        result[y_fill, x_fill] = filled_values
    else: # get nearest neighbor
        distances, indices = tree.query(points_fill, k=1)
        nearest_values = valid_values[indices]
        result[y_fill, x_fill] = nearest_values
    return result

def write_geotif(array, profile, output_path, nodata=NODATA_DEM, units=None):
    temp_path = output_path + ".tmp.tif"
    try:
        array[np.isnan(array)] = nodata
        array = array.astype(profile["dtype"])
        output_profile = profile.copy()
        output_profile.update({
            "driver": "GTiff", "count": 1,
            "dtype": array.dtype, "nodata": nodata
        })
        with rasterio.open(temp_path, "w", **output_profile) as dst:
            dst.write(array, 1)
            if units: dst.update_tags(units=units)
        os.replace(temp_path, output_path)
    finally:
        if os.path.exists(temp_path):
            try: functions.safe_remove(temp_path)
            except OSError: traceback.print_exc()

def create_LAI(arr_2D, terrain, out_path, nodata=255):
    # Compute LAI
    lai_path = r"backend\src\flow_samples\landcover\LAI_Norway.zip"
    if os.path.exists(out_path): os.remove(out_path)
    land_flat, water_classes = arr_2D.ravel(), [40, 41, 44]
    valid_mask = (land_flat != nodata)
    land_valid = land_flat[valid_mask]
    unique_classes = np.unique(land_valid)
    df = pd.DataFrame(index=unique_classes.astype(int))
    with zipfile.ZipFile(lai_path, "r") as zip_ref:
        tif_names = [n for n in zip_ref.namelist() if n.endswith(".tif")]
        for name in tif_names:
            with zip_ref.open(name) as f:
                with MemoryFile(f.read()) as memfile:
                    month = name.split("_")[2].replace(".tif", "")
                    result = rioxarray.open_rasterio(memfile)
                    if isinstance(result, list): lai = result[0].squeeze()
                    else: lai = result.squeeze()
                    lai_match = lai.rio.reproject_match(terrain, resampling=Resampling.nearest)
                    lai_arr = lai_match.values.astype(np.float32)
                    lai_arr[lai_arr < -1000] = np.nan
                    lai_flat = lai_arr.ravel()
                    lai_valid = lai_flat[valid_mask]
                    lai_valid[np.isin(land_valid, water_classes)] = 0
                    tmp = pd.DataFrame({"type": land_valid, "lai": lai_valid})
                    df[int(month)] = np.round(tmp.groupby("type")["lai"].mean(), 3)
    df = df.reindex(sorted(df.columns), axis=1)
    df.index.name = os.path.basename(out_path).replace("_lai.csv", "")
    df.to_csv(out_path, index=True)

def setup_logger(name, log_path: str):
    log_path = os.path.abspath(log_path)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Remove old handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.flush(); handler.close()
    # Create a new file handler
    file_handler = logging.FileHandler(
        log_path, mode="a", encoding="utf-8", delay=False
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(file_handler)
    logger.info("Logger initialized: %s", log_path)
    file_handler.flush()
    return logger

def weather_downloader(project_name, flow_name, processes, process_key, 
    source, api_key, start, end, time_zone, log_file, **options):
    # Prepare forcing data from the global model ARE5
    # Source: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=download
    project_dir = os.path.join(PROJECT_ROOT, project_name)
    log_dir = os.path.join(project_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, os.path.basename(log_file))
    try:
        if os.path.exists(log_path): os.remove(log_path)
    except OSError: pass
    flow_dir = os.path.normpath(os.path.join(project_dir, "flows", os.path.basename(flow_name)))
    logger = setup_logger(f"{process_key}:weather", log_path)
    label = weather_functions.SOURCES.get(source, {}).get("label", source)
    try:
        era5_function.set_status(processes, process_key, "running", f"Weather downloader started ({label}).")
        era5_function.check_cancel(processes, process_key)
        start_time = functions.local_to_utc(start, time_zone).replace(tzinfo=None)
        end_time = functions.local_to_utc(end, time_zone).replace(tzinfo=None)
        logger.info(f"Weather downloader started – source: {label}")
        logger.info(f"Local: {start} -> {end} ({time_zone}) | UTC: {start_time} -> {end_time}")
        n_total = weather_functions.download_forcing(
            source, flow_dir, api_key, start_time, end_time, processes, process_key, logger, time_zone, **options)
        era5_function.set_status(processes, process_key, "finished",
            f"Weather download completed successfully ({label}, {n_total} hourly steps).\n\n\n")
    except era5_function.DownloadCancelled as e:
        logger.info("=" * 70)
        logger.info(f"{e} Previous forcing file (if any) was kept. "
            "Downloaded variables are cached - starting again resumes the download.")
        era5_function.set_status(processes, process_key, "failed", "Download cancelled by user.", cancelled=True)
    except era5_function.ERA5Error as e:
        logger.error(f"FAILED: {e}")
        logger.info("Downloaded variables are cached - starting again resumes the download.")
        era5_function.set_status(processes, process_key, "failed", str(e))
    except Exception as e:
        logger.error("Weather download failed:\n" + traceback.format_exc())
        era5_function.set_status(processes, process_key, "failed", str(e))
    finally:
        gc.collect()
        for h in logger.handlers[:]:
            try: h.flush(); h.close()
            except Exception: pass
            logger.removeHandler(h)

def soil_downloader(project_name:str, processes:dict, process_key:str, flow_name:str, 
    soil_source:str, water:str, dtm_path:str, catchment_path:str, log_file:str):
    project_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
    flow_dir = os.path.normpath(os.path.join(project_dir, "flows", flow_name))
    soil_dir = os.path.normpath(os.path.join(flow_dir, "soil"))
    os.makedirs(soil_dir, exist_ok=True)
    # Work with log
    log_dir = os.path.join(project_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.normpath(os.path.join(log_dir, log_file))
    if os.path.exists(log_path): os.remove(log_path)
    logger, ref = None, None
    try:
        if os.path.exists(log_path): os.remove(log_path)
        logger = setup_logger("soil", log_path)
        # Download soil data 2017 from ISRIC: https://files.isric.org/soilgrids/former/2017-03-10/
        logger.info("Preparing data download...")
        logger.info("Reading dtm data...")
        with rasterio.open(dtm_path) as src:
            dtm = src.read(1, masked=True)
            nodata, profile = src.nodata, src.profile
            crs, transform = src.crs, src.transform
            height, width = src.height, src.width
            resolution_x, resolution_y = src.res
        if os.path.exists(catchment_path) and os.path.getsize(catchment_path) > 0:
            catchment = gpd.read_file(catchment_path)
            catchment_projected = catchment.to_crs(crs)
            buffer = max(abs(resolution_x), abs(resolution_y))
            catchment_buffer = catchment_projected.buffer(buffer)
            catch_array = rasterize(
                shapes=[geom for geom in catchment_buffer.geometry], dtype=np.uint8,
                out_shape=(height, width), transform=transform, fill=0, default_value=1
            )
        else:
            valid_mask = ~np.ma.getmaskarray(dtm)
            if nodata is None: valid_mask = np.isfinite(dtm.data)
            polygons = [shape(geom)
                for geom, value in shapes(
                    valid_mask.astype(np.uint8), mask=valid_mask, transform=transform
                ) if value == 1
            ]
            if not polygons:
                logger.error(f"DTM has no valid cells: {dtm_path}")
                raise ValueError(f"DTM has no valid cells: {dtm_path}")
            catchment_buffer = gpd.GeoDataFrame(geometry=[gpd.GeoSeries(polygons).union_all()], crs=crs)
            catch_array = rasterize(
                shapes=catchment_buffer.geometry, out_shape=(height, width),
                transform=transform, dtype=np.uint8, fill=0, default_value=1
            )
        mask_catchment: np.ndarray = np.asarray(catch_array == 1, dtype=np.bool_)
        catchment_buffer_wgs84 = catchment_buffer.to_crs("EPSG:4326")
        pad = 0.01
        min_lon, min_lat, max_lon, max_lat = catchment_buffer_wgs84.total_bounds
        water_path, mask_lake = os.path.normpath(os.path.join(flow_dir, 'water_area', water)), None
        if water and os.path.exists(water_path) and os.path.getsize(water_path) > 0:
            lake = gpd.read_file(water_path)
            lake_reproj = lake.to_crs(crs)
            lake_array = rasterize(
                shapes=[geom for geom in lake_reproj.geometry], dtype=np.uint8,
                out_shape=(height, width), transform=transform, fill=0, default_value=1
            )
            mask_lake = lake_array == 1
        def read_isric(url):
            result = rioxarray.open_rasterio(url)
            if isinstance(result, list): src = result[0]
            else: src = result
            data_xr = src.rio.clip_box(
                minx=min_lon-pad, miny=min_lat-pad, maxx=max_lon+pad, maxy=max_lat+pad
            )
            data_xr = data_xr.rio.reproject_match(ref, resampling=Resampling.nearest)
            isric_nodata = data_xr.rio.nodata
            data_xr = data_xr.load()
            arr = data_xr[0].values.astype(np.float64)
            valid = np.isfinite(arr)
            if isric_nodata is not None: valid &= arr != isric_nodata
            if not valid.any():
                logger.error(f"No valid ISRIC data in area: {url}")
                raise ValueError(f"No valid ISRIC data in area: {url}")
            return arr, valid
        if soil_source == "isric":
            soil_depths = flow_functions.soil_depths
            soil_types, depths = flow_functions.soil_types, list(soil_depths.values())
            # Get raster information
            result = rioxarray.open_rasterio(dtm_path)
            if isinstance(result, list): ref = result[0].squeeze()
            else: ref = result.squeeze()
            # Soil thickness
            logger.info("Creating soil thickness data...")
            soil_thickness_url = f"{ISRIC_URL}BDRICM_M_250m_ll.tif"
            soil_thickness_array, mask_valid = read_isric(soil_thickness_url)
            # Interpolate data
            logger.info("Interpolating soil thickness data...")
            soil_thickness_values = _interpolate_extrapolate(soil_thickness_array, ~mask_valid, True)
            soil_thickness_values = np.where(np.isfinite(soil_thickness_values), soil_thickness_values, NODATA_SOIL_THICKNESS)
            soil_thickness_values = soil_thickness_values.astype(np.int32)
            # Clip to catchment & lake
            soil_thickness_values[~mask_catchment] = NODATA_SOIL_THICKNESS
            if mask_lake is not None: soil_thickness_values[mask_lake] = NODATA_SOIL_THICKNESS
            soil_thickness_path = os.path.normpath(os.path.join(soil_dir, 'soilthickness.tif'))
            profile_writer = profile.copy()
            profile_writer.update(dtype=np.int32)
            write_geotif(soil_thickness_values, profile_writer, soil_thickness_path, NODATA_SOIL_THICKNESS, 'cm')
            logger.info(f"Saving soil thickness data to: {soil_thickness_path}")
            # Download soil data
            for _, values in soil_types.items():
                file, name, scale, dtype, nodata_soil, unit = values[0], values[1], values[2], values[3], values[4], values[5]
                for depth in depths:
                    file_url = f'{ISRIC_URL}{file}_{depth}_250m_ll.tif'
                    logger.info(f"Downloading: {file}_{depth}_250m_ll.tif...")
                    soil_array, mask_valid = read_isric(file_url)
                    # Interpolate data
                    logger.info(f"Interpolating {file}_{depth}_250m_ll.tif...")
                    soil_values = _interpolate_extrapolate(soil_array, ~mask_valid, True)
                    soil_values = soil_values / scale
                    soil_values = np.where(np.isfinite(soil_values), soil_values, nodata_soil)
                    if mask_lake is not None: soil_values[mask_lake] = nodata_soil
                    soil_values[~mask_catchment] = nodata_soil
                    path = os.path.normpath(os.path.join(soil_dir, f'{name}_{depth}.tif'))
                    profile_writer.update(dtype=dtype)
                    write_geotif(soil_values, profile_writer, path, nodata_soil, unit)
                    logger.info(f"Saved downloaded data to: {path}")
        else:
            logger.error(f"Unsupported soil_source: {soil_source}")
            raise ValueError(f"Unsupported soil_source: {soil_source}")
        logger.handlers[0].flush()
        processes[process_key] = {
            "status": "finished", "message": "\nSoil data downloaded successfully.\n\n"
        }
    except Exception as e:
        if logger: logger.exception("Data download failed.")
        processes[process_key] = {"status": "failed", "message": str(e)}
    finally:
        if ref is not None:
            try: ref.close()
            except Exception: pass
        if logger:
            for h in logger.handlers[:]:
                try: h.flush(); h.close()
                finally: logger.removeHandler(h)

def hydro_creator(directory:str, dem_path:str, area_km2:float, 
    logger:logging.Logger, river_path:str, water_path:str | None):
    with rasterio.open(dem_path) as src:
        dem_array = src.read(1)
        crs, nodata = src.crs, src.nodata
        transform, profile = src.transform, src.profile
        height, width = src.height, src.width
    nodata_f = float(nodata) if nodata is not None else -9999.0
    dem_array[np.isnan(dem_array)] = nodata_f
    mask_lake = None
    if water_path is not None:
        logger.info("Masking lake area...")
        lake = gpd.read_file(water_path)
        lake_reproj = lake.to_crs(crs)
        lake_array = np.full((height, width), nodata_f, dtype=np.float32)
        rasterize(
            shapes=[geom for geom in lake_reproj.geometry],
            out=lake_array, transform=transform,
        )
        mask_lake = (lake_array != nodata)
    logger.info("Filling depressions...")
    filled_array, flwdir_array = dem.fill_depressions(elevtn=dem_array, max_depth=-1, nodata=nodata_f)
    if mask_lake is not None: filled_array[mask_lake] = dem_array[mask_lake]
    logger.info("Creating flow directions...")
    flw = pyflwdir.from_dem(filled_array, transform=transform, latlon=crs.is_geographic, nodata=nodata_f)
    NODATA_FLWDR, NODATA_BASIN = 255, 0
    # Create basins
    logger.info("Creating basins...")
    basins_array = flw.basins()
    unique, counts = np.unique(basins_array, return_counts=True)
    largest_basin_id = unique[np.argmax(counts)]
    basins_mask = (basins_array == largest_basin_id)
    basins_array[basins_mask], basins_array[~basins_mask] = 1, NODATA_BASIN
    logger.info("Creating elevation profile...")
    elevtn_array = filled_array.copy()
    # Create slope
    logger.info("Creating slope...")
    dx, dy = transform.a, abs(transform.e)
    valid = filled_array != nodata
    if (~valid).any():
        idx = distance_transform_edt(~valid, return_distances=False, return_indices=True)
        idx_arr = np.asarray(idx)
        if idx_arr.ndim == 3: idx_arr = idx_arr.reshape(2, -1) 
        grad_src = filled_array[tuple(idx_arr)]
    else: grad_src = filled_array
    # Gradient elevation
    gy, gx = np.gradient(grad_src, dy, dx)
    slope_array = np.sqrt(gx**2 + gy**2).astype(np.float32)
    slope_array[~valid] = nodata
    # Create stream order
    logger.info("Creating stream order...")
    uparea_array = flw.upstream_area(unit='km2')
    # Create stream mask and stream order
    stream_mask = (uparea_array >= area_km2)
    strord_array = flw.stream_order(type='strahler', mask=stream_mask)
    # Create upstream grid
    logger.info("Creating upstream grid...")
    upstream_array = flw.upstream_area(unit='cell')
    # Create river width
    logger.info("Creating river width...")
    river = gpd.read_file(river_path).to_crs(crs)
    shape = ((geom, value) for geom, value in zip(river.geometry, river["rivwth"]))
    rivwth_array = np.full((profile["height"], profile["width"]), nodata_f, dtype=np.float32)
    rasterize(shapes=shape, out=rivwth_array, transform=transform)
    files_float = {
        'elevtn.tif': [elevtn_array, nodata, np.float32],
        'flwdir.tif': [flwdir_array, NODATA_FLWDR, np.uint8],
        'lndslp.tif': [slope_array, nodata, np.float32],
        'basins.tif': [basins_array, NODATA_BASIN, np.int32], 
        'uparea.tif': [uparea_array, nodata, np.float32],
        'strord.tif': [strord_array, NODATA_BASIN, np.int16],
        'upgrid.tif': [upstream_array, NODATA_BASIN, np.int32],
        'rivwth.tif': [rivwth_array, nodata, np.float32]
    }
    profile_writer = profile.copy()
    for file, array in files_float.items():
        profile_writer.update({'dtype': array[2]})
        file_path = os.path.normpath(os.path.join(directory, file))
        logger.info(f"Creating data: {file_path}")
        logger.info(f"Writing data to: {file_path}")
        write_geotif(array[0], profile_writer, file_path, array[1])

def wflow_check(project_name:str, processes:dict, process_key:str, flow_name:str, log_file:str, area:float=10):
    project_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
    model_dir = os.path.normpath(os.path.join(project_dir, "flows", flow_name))
    # Work with log
    log_dir = os.path.join(project_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.normpath(os.path.join(log_dir, log_file))
    if os.path.exists(log_path): os.remove(log_path)
    logger = setup_logger("wflow", log_path)
    try:
        logger.info("Checking wflow inputs..."); logger.info("="*60)
        logger.info("Checking weather forcing data...")
        forcing_path = os.path.normpath(os.path.join(model_dir, 'forcing', 'weather_forcing.nc'))
        if not os.path.exists(forcing_path):
            logger.info("Weather forcing data not found.")
            processes[process_key] = {"status": "failed", "message": "Weather forcing data not found."}
            return
        logger.info(f"Found weather forcing data at: {forcing_path}"); logger.info("="*60)
        logger.info("Checking terrain data...")
        terrain_path = os.path.normpath(os.path.join(model_dir, 'raw', 'dtm_raw.tif'))
        if not os.path.exists(terrain_path):
            logger.info("Terrain data not found.")
            processes[process_key] = {"status": "failed", "message": "Terrain data not found."}
            return
        logger.info(f"Found terrain data at: {terrain_path}"); logger.info("="*60)
        logger.info("Checking water area data...")
        water_dir = os.path.join(model_dir, 'water_area')
        water_files = [f for f in os.listdir(water_dir) if f.endswith(".geojson")] if os.path.isdir(water_dir) else []
        if len(water_files) == 0:
            logger.warning("Water area data not found. Water area will not be masked.")
            water_path = None
        else:
            water_path = os.path.normpath(os.path.join(water_dir, water_files[0]))
            logger.info(f"Found water area data at: {water_path}")
        logger.info("="*60); logger.info("Checking river data...")
        river_dir = os.path.normpath(os.path.join(model_dir, 'river'))
        river_files = [f for f in os.listdir(river_dir)]
        if len(river_files) != 2:
            logger.info("Number of river files is not equal to 2.")
            processes[process_key] = {"status": "failed", "message": "Please upload river data."}
            return
        river_path = os.path.normpath(os.path.join(river_dir, 'river.gpkg'))
        logger.info(f"Found river data at: {river_dir}"); logger.info("="*60)
        logger.info("Checking template hydro data...")
        # Prepare template raster dataset
        hydro_dir = os.path.normpath(os.path.join(model_dir, 'hydro'))
        if os.path.exists(hydro_dir):
            logger.info(f"Deleting hydro data at: {hydro_dir}")
            shutil.rmtree(hydro_dir)
        os.makedirs(hydro_dir, exist_ok=True)
        logger.info(f"Creating template hydro data at: {hydro_dir}")
        hydro_creator(hydro_dir, terrain_path, area, logger, river_path, water_path)
        logger.info("Write hydro data completed."); logger.info("=" * 60)
        logger.info("Checking landcover data...")
        landcover_dir = os.path.normpath(os.path.join(model_dir, 'landcover'))
        landcover_files = [f for f in os.listdir(landcover_dir)]
        if len(landcover_files) != 3:
            logger.info("Number of landcover files is not equal to 3.")
            processes[process_key] = {"status": "failed", "message": "Please download landcover data."}
            return
        logger.info(f"Found landcover data at: {landcover_dir}"); logger.info("=" * 60)
        logger.info("Checking soil data...")
        soil_dir = os.path.normpath(os.path.join(model_dir, 'soil'))
        soil_files = [f for f in os.listdir(soil_dir) if f.endswith('.tif')]
        if len(soil_files) != 43:
            message = """
                *******************************************************************************
                *  NUMBER OF SOIL FILES ARE NOT EQUAL TO 43. PLEASE DOWNLOAD SOIL DATA AGAIN  *
                *******************************************************************************
            """
            logger.info(f"\n\n\n{message}\n")
            processes[process_key] = {"status": "failed", "message": "Please download soil data."}
            return
        logger.info(f"Found soil data at: {soil_dir}"); logger.info("=" * 60); logger.handlers[0].flush()
        processes[process_key] = {"status": "finished", "message": "\nChecking Wflow inputs completed.\n\n"}
    except Exception as e:
        logger.exception("Check of wflow failed.")
        processes[process_key] = {"status": "failed", "message": str(e)}
    finally:
        for h in logger.handlers[:]:
            h.close(); logger.removeHandler(h)

def _safe_rmtree(path, max_retries=5, delay=1.0, logger=None):
    if not os.path.exists(path): return True
    def _log(msg):
        if logger:
            try: logger.info(msg)
            except: pass
    for attempt in range(max_retries):
        try:
            gc.collect()
            try: get_client().close()
            except Exception: pass
            gc.collect()
            def _on_error(func, path, exc_info):
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception: pass
            shutil.rmtree(path, onerror=_on_error)
            _log(f"Deleted {path}")
            return True
        except PermissionError as e:
            _log(f"Attempt {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1: time.sleep(delay * (attempt + 1))
            else: return False
        except FileNotFoundError: return True
        except Exception as e:
            _log(f"Attempt {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1: time.sleep(delay)
            else: return False
    return False

def _cleanup_model(model, logger=None):
    if model is None: return
    def _log(msg):
        if logger:
            try: logger.info(msg)
            except: pass
    try:
        for attr in ("staticmaps", "forcing", "states", "geoms", "tables", "config"):
            obj = getattr(model, attr, None)
            if obj is None: continue
            if hasattr(obj, "close"):
                try: 
                    obj.close()
                    _log(f"Closed {attr}")
                except Exception as e: _log(f"Error closing {attr}: {e}")
            data = getattr(obj, "data", None)
            if data is not None and hasattr(data, "close"):
                try: 
                    data.close()
                    _log(f"Closed {attr}.data")
                except Exception as e:
                    _log(f"Error closing {attr}.data: {e}")
        try:
            dc = getattr(model, '_data_catalog', None)
            if dc is not None and hasattr(dc, "close"):
                dc.close()
        except Exception: pass
        if hasattr(model, "_datasets"):
            try:
                for ds in model._datasets.values():
                    if hasattr(ds, "close"):
                        ds.close()
            except Exception: pass
    except Exception as e:
        _log(f"Cleanup model error: {e}")
    finally:
        try: del model
        except Exception: pass
        gc.collect()
        try:
            client = get_client(); client.close()
        except Exception: pass
        gc.collect()

def _cleanup_after_failure(model_dir, model_folder, logger=None):
    def _log(msg):
        if logger:
            try: logger.info(msg)
            except: pass
    mod_path = os.path.normpath(os.path.join(model_dir, model_folder))
    try:
        client = get_client(); client.close()
    except Exception: pass
    for _ in range(3):
        gc.collect()
    try:
        target = os.path.normpath(mod_path).lower()
        open_files = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                for f in proc.open_files():
                    if target in os.path.normpath(f.path).lower():
                        open_files.append((proc.info['pid'], proc.info['name'], f.path))
            except (psutil.NoSuchProcess, psutil.AccessDenied): pass
        if open_files:
            _log(f"{len(open_files)} file handles still open:")
            for pid, name, path in open_files[:10]:
                _log(f"  PID {pid} ({name}): {path}")
    except ImportError: pass
    except Exception: pass

def prepare_hydromt(project_name, flow_name, model_folder, start, end, step, time_zone, data_lib, 
    region, uparea, resolution, soil_layers, params_input, params_output, log_path, 
    lulc_function, lulc_mapping_fn, lai_fn, logger=None):
    project_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
    model_dir = os.path.normpath(os.path.join(project_dir, "flows", flow_name))
    mod_path = os.path.normpath(os.path.join(model_dir, model_folder))
    if logger is None: logger = setup_logger("hydromt", log_path)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    stream_logger = StreamToLogger(logger, log_path=log_path)
    sys.stdout, sys.stderr = stream_logger, stream_logger
    # Clean up existing model directory
    if os.path.exists(mod_path): _safe_rmtree(mod_path, logger=logger)
    os.makedirs(mod_path, exist_ok=True)
    error, model, configs, dropped = None, None, {}, {}
    try:
        logger.info("Starting hydromt for preparation...")
        # Prepare model
        logger.info("Creating model instance...")
        model = wflow_config.model_init(mod_path, data_lib)
        # Setup configurations
        configs = {}
        # Setup time
        logger.info(f"Model time configuration.")
        configs['time'] = wflow_config.model_time(start, end, step, time_zone)
        logger.info(f"Model Settings.")
        configs['model'] = wflow_config.model_setting(step)
        # ========== Output variables ==========
        logger.info(f"Setup outputs configuration.")
        configs['dir_output'] = 'run_default'
        model.setup_config(configs)
        # Setup base map
        logger.info(f"Setup basemaps configuration.")
        model = wflow_config.model_basemap(model, region, resolution)
        # Setup rivers
        logger.info(f"Setup river configuration.")
        model = wflow_config.model_river(model, uparea)
        # Setup soil maps
        logger.info(f"Setup soil configuration.")
        model = wflow_config.model_soil(model, soil_layers)
        # Setup land use
        logger.info(f"Setup land use configuration.")
        model = wflow_config.model_landuse(model, lulc_function, lulc_mapping_fn, lai_fn)
        # Setup forcing
        logger.info(f"Setup weather forcing.")
        model = wflow_config.model_forcing(model)
        # === Constant parameters ===
        logger.info(f"Setup constant parameters.")
        model = wflow_config.model_constant_pars(model, params_input)
        # === Cold states ===
        logger.info(f"Setup cold states.")
        model.setup_cold_states()
        # === Remove invalid values ===
        wflow_config.sanitize_model(model, logger)
        # === Write model ===
        logger.info(f"Model writer configurations.")
        dropped = wflow_config.model_writer(model, configs, params_output, logger)
        if dropped: logger.warning(f"{len(dropped)} selected output(s) were skipped: {sorted(dropped)}")
        logger.info(f"Validating model 'wflow_sbm.toml'.")
        toml_path = os.path.join(mod_path, 'wflow_sbm.toml')
        wflow_config.model_validate_config(toml_path, logger)
        logger.info(f"Prepare HydroMT completed.")
        logger.handlers[0].flush()
    except Exception as e:
        logger.exception("Prepare HydroMT failed: %s", e)
        error = e
    finally:
        _cleanup_model(model, logger)
        model = None
        for h in logger.handlers:
            try: h.flush()
            except Exception: pass
        sys.stdout, sys.stderr = old_stdout, old_stderr
    if error is not None: raise error
    return dropped

def run_hydromt(project_name: str, flow_name:str, model_folder:str, log_path:str, logger=None):
    project_dir = os.path.join(PROJECT_ROOT, project_name)
    model_dir = os.path.normpath(os.path.join(project_dir, "flows", flow_name, model_folder))
    if logger is None: logger = setup_logger("hydromt", log_path)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    stream_logger = StreamToLogger(logger, log_path=log_path)
    sys.stdout, sys.stderr = stream_logger, stream_logger
    error = None
    try:
        logger.info("Running HydroMT..."); logger.info("=" * 36)
        wflow_path = os.path.normpath(os.path.join(WFLOW_PATH, "wflow_cli", "bin", "wflow_cli.exe"))
        toml_path = os.path.normpath(os.path.join(model_dir, "wflow_sbm.toml"))
        if not os.path.isfile(wflow_path):
            message = f"Wflow executable not found: {wflow_path}"
            logger.info("=" * 36); logger.info(message); logger.info("=" * 36)
            raise FileNotFoundError(message)
        if not os.path.isfile(toml_path):
            message = f"TOML config not found: {toml_path}"
            logger.info("=" * 36); logger.info(message); logger.info("=" * 36)
            raise FileNotFoundError(message)
        if not os.path.isdir(model_dir):
            message = f"Model dir not found: {model_dir}"
            logger.info("=" * 36); logger.info(message); logger.info("=" * 36)
            raise FileNotFoundError(message)
        cmd = [wflow_path, toml_path]
        process = subprocess.Popen(
            cmd, cwd=model_dir, stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        if process.stdout is None: 
            raise RuntimeError("stdout is not available; did you pass stdout=PIPE?")
        for line in process.stdout: 
            logger.info(line.strip()); logger.handlers[0].flush()
        process.wait(); logger.info("=" * 36)
        if process.returncode == 0:
            logger.info(f"Run HydroMT completed successfully.")
        else: 
            logger.info(f"Run HydroMT failed.")
            error = RuntimeError(f"wflow_cli exited with code: {process.returncode}")
        logger.info("=" * 36); logger.handlers[0].flush()
    except Exception as e:
        logger.exception("Prepare HydroMT failed: %s", e); error = e
    finally:
        for h in logger.handlers[:]:
            try: h.flush()
            except Exception: pass
            h.close(); logger.removeHandler(h)
        sys.stdout, sys.stderr = old_stdout, old_stderr
    if error is not None: raise error

def wflow_run(project_name:str, processes:dict, process_key:str, flow_name:str, model_folder:str, df:pd.DataFrame, 
    crs:str, start:str, end:str, step:int, time_zone:str, data_lib:list, resolution:int, soil_layers:list, 
    params_input:dict, params_output:dict, lulc_function:str, lulc_mapping_fn:str, lai_fn:str, log_file:str, uparea:float=10.0):
    # lulc_function, lulc_mapping_fn, lai_fn = 'esa_worldcover', 'esa_worldcover_mapping', 'lai_esa'
    project_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
    model_dir = os.path.normpath(os.path.join(project_dir, "flows", flow_name))
    mod_path = os.path.normpath(os.path.join(model_dir, model_folder))
    # Set up logger
    log_dir = os.path.join(project_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.normpath(os.path.join(log_dir, log_file))
    if os.path.exists(log_path): os.remove(log_path)
    logger = setup_logger("hydromt", log_path)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    stream_logger = StreamToLogger(logger, log_path=log_path)
    sys.stdout, sys.stderr = stream_logger, stream_logger
    # Clean up existing model directory
    if os.path.exists(mod_path): _safe_rmtree(mod_path, logger=logger)
    os.makedirs(mod_path, exist_ok=True)
    candidate_results, success_point = [], None
    def _reset_log_file():
        try:
            for h in logger.handlers[:]:
                try: h.flush(); h.close()
                except Exception: pass
                logger.removeHandler(h)
            if os.path.exists(log_path): os.remove(log_path)
            new_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
            new_handler.setLevel(logging.INFO)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            new_handler.setFormatter(formatter)
            logger.addHandler(new_handler)
            stream_logger.update_logger(logger)
            logger.handlers[0].flush()
        except Exception: pass
    try:
        for i, (_, row) in enumerate(df.iterrows()):
            temp_name, temp_lat, temp_lon = row['id'], row['lat'], row['lon']
            if i > 0: _reset_log_file()
            logger.info("="*70)
            logger.info(f"Checking candidate: {temp_name} [{temp_lat}, {temp_lon}]")
            logger.info("="*70)
            try:
                client = get_client(); client.close(); del client
            except Exception: pass
            gc.collect(); config.set(scheduler='threads'); prepare_ok = False
            try:
                gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy([temp_lon], [temp_lat]), crs='EPSG:4326')
                if gdf.crs != crs: gdf = gdf.to_crs(crs)
                x, y = round(gdf.geometry.x[0], 1), round(gdf.geometry.y[0], 1)
                # https://deltares.github.io/hydromt/v0.8.0/_generated/hydromt.workflows.basin_mask.parse_region.html
                region = {'subbasin': [x, y], 'uparea': uparea}
                # Prepare model
                dropped = prepare_hydromt(project_name, flow_name, model_folder, 
                    start, end, step, time_zone, data_lib, region, uparea,
                    resolution, soil_layers, params_input, params_output, log_path,
                    lulc_function, lulc_mapping_fn, lai_fn, logger
                )
                if dropped: processes[process_key] = {"status": "failed", "message": dropped}
                prepare_ok = True
                # Check parameters
                path = os.path.normpath(os.path.join(model_dir, model_folder, "static_grid.nc"))
                if not os.path.exists(path): raise FileNotFoundError(f"Static grid not found: {path}")
                nan_vars = []
                with xr.open_dataset(path) as ds:
                    for item in ds.data_vars:
                        vals = np.unique(ds[item].values)
                        if vals.size == 1 and np.isnan(vals[0]):
                            nan_vars.append(item)                
                if nan_vars: raise ValueError(f"NaN values in: {nan_vars}")
                # Run model
                logger.info("="*70)
                run_hydromt(project_name, flow_name, model_folder, log_path)
                logger.info(f"Candidate '{temp_name}' SUCCESS.")
                candidate_results.append({"point": temp_name, "lat": temp_lat, "lon": temp_lon, "status": "success"})
                success_point = temp_name; logger.info("="*70)
                logger.info(f"Model run SUCCEEDED with candidate: {temp_name} [{temp_lat}, {temp_lon}]")
                logger.info("Process completed. Stopping candidate iteration."); logger.info("="*70); break
            except Exception as e:
                logger.exception("Run HydroMT failed for candidate '%s'.",temp_name )
                candidate_results.append({
                    "point": temp_name, "lat": temp_lat, "lon": temp_lon,
                    "status": "failed", "error": str(e), "traceback": traceback.format_exc()
                })
                if not prepare_ok:
                    logger.error(f"Prepare data for hydromt failed - Skipping run_hydromt for candidate '{temp_name}'.")
                    logger.info("=" * 70); logger.info("")
                _cleanup_after_failure(model_dir, model_folder, logger)
                gc.collect()
                continue
        if success_point is not None:
            processes[process_key] = { "status": "finished",
                "message": f"\n\nProcess completed successfully with candidate '{success_point}'.\n"
                    f"Total candidates tried: {len(candidate_results)}.",
                "success_point": success_point, "results": candidate_results,
            }
        else:
            n_failed = len(candidate_results)
            last_error = (
                candidate_results[-1].get("error", "Unknown error") if candidate_results else "No candidate was processed."
            )
            processes[process_key] = {
                "status": "failed", "message": (
                    f"All {n_failed} candidates failed. No valid model could be run.\n"f"Last error: {last_error}"
                ), "results": candidate_results,
            }
    except Exception as e:
        processes[process_key] = {"status": "failed", "message": str(e)}
    finally:
        try:
            client = get_client(); client.close()
        except Exception: pass
        gc.collect()
        sys.stdout, sys.stderr = old_stdout, old_stderr
        for h in logger.handlers[:]:
            h.close(); logger.removeHandler(h)
