import glob, json, math, os, time, shapely
import numpy as np, pandas as pd, xugrid as xu
import geopandas as gpd, xarray as xr
from services import functions

NODATA, M_PER_DEG, FORMAT_VERSION = -32768, 111_320.0, 2
UV_SCALE = 0.001     # 1 unit int16 = 1 mm/s
DEPTH_SCALE = 0.01   # 1 unit = 1 cm
U_NAMES, V_NAMES = ["mesh2d_ucx", "ucx"], ["mesh2d_ucy", "ucy"]
U_NAMES_AVG, V_NAMES_AVG = ["mesh2d_ucxa", "ucxa"], ["mesh2d_ucya", "ucya"]
DEPTH_NAMES = ["mesh2d_waterdepth", "waterdepth"]
S1_NAMES = ["mesh2d_s1", "s1"]
BED_NAMES = ["mesh2d_flowelem_bl", "flowelem_bl"]

def needs_rebuild(meta_path, frame_path, mask_path, res, layer):
    for p in (meta_path, frame_path, mask_path):
        if not os.path.exists(p): return f"Cannot find: {os.path.basename(p)}"
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception: return "meta.json broken."
    if res not in (None, "", "auto", 0) and abs(float(res) - float(meta.get("res_m", -1))) > 1e-6:
        return f"Resolution changed from {meta.get('res_m')} -> {res} m"
    if res in (None, "", "auto", 0) and not meta.get("res_auto"):
        return "Change to auto resolution"
    if (layer or "mean") != meta.get("layer", "mean"): return f"Layer changed from {meta.get('layer')} -> {layer}"
    return None

def open_map(map_path: str):
    files = sorted(glob.glob(map_path))
    if not files and os.path.exists(map_path): files = [map_path]
    if not files: return None
    parts = []
    try:
        for path in files:
            if os.path.isdir(path) or path.lower().endswith(".zarr"):
                parts.append(xu.open_zarr(path))
            elif path.lower().endswith(".nc"):
                parts.append(xu.open_dataset(path))
        if not parts: return None
        return parts[0] if len(parts) == 1 else xu.merge_partitions(parts)
    except Exception:
        for ds in parts:
            try: ds.close()
            except Exception: pass
        raise

def pick(ds, names):
    for n in names:
        if n in ds.data_vars: return n
    return None

def load_polygon(path):
    gdf = gpd.read_file(path)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty: raise ValueError(f"No geometry in {path}")
    if gdf.crs is None: raise ValueError(f"{path} no CRS. Please set CRS first.")
    if gdf.crs.to_epsg() != 4326: gdf = gdf.to_crs(epsg=4326)
    geom = shapely.force_2d(gdf.geometry.make_valid().union_all())
    if geom.geom_type == "GeometryCollection":
        geom = shapely.union_all([g for g in shapely.get_parts(geom) if g.geom_type in ("Polygon", "MultiPolygon")])
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"Need Polygon/MultiPolygon, got {geom.geom_type}")
    geojson = json.loads(gpd.GeoSeries([geom], crs="EPSG:4326").to_json())
    return geom, geojson

def nice_ceil(x):
    if x <= 0: return 1.0
    e = 10 ** math.floor(math.log10(x))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= x * 0.999: return m * e
    return 10 * e

def auto_resolution(grid, polygon, T, budget_gb=2.0, max_frame_mb=1.5, quantile=0.25, min_across=150):
    west, south, east, north = polygon.bounds
    kx = M_PER_DEG * math.cos(math.radians(0.5 * (south + north)))
    area_m2 = polygon.area * kx * M_PER_DEG
    inside = shapely.contains_xy(polygon, grid.face_x, grid.face_y)
    cell = np.sqrt(grid.area[inside] * kx * M_PER_DEG) if inside.any() else np.array([50.0])
    r_detail = float(np.quantile(cell, quantile))
    r_storage = math.sqrt(area_m2 * T * 6 / (budget_gb * 1e9))
    r_frame = math.sqrt(area_m2 * 6 / (max_frame_mb * 1e6))
    r_sparse = min((east - west) * kx, (north - south) * M_PER_DEG) / min_across
    r_budget = max(r_storage, r_frame)
    r = max(r_detail, r_budget)
    if r > r_sparse and r_budget <= r_sparse: r = r_sparse
    return nice_ceil(r), {"r_detail": r_detail, "r_storage": r_storage, "r_frame": r_frame,
        "r_sparse": r_sparse, "cell_p25": float(np.quantile(cell, .25)), "cell_median": float(np.median(cell))}

def reduce_layers(da, face_dim, layer):
    for dim in [d for d in da.dims if d not in ("time", face_dim)]:
        if layer == "mean":
            da = da.mean(dim, skipna=True)
        elif layer == "surface": da = da.isel({dim: -1})
        else: da = da.isel({dim: int(layer)})
    return da

def map_mtime(map_path):
    files = sorted(glob.glob(map_path)) or ([map_path] if os.path.exists(map_path) else [])
    return max((os.path.getmtime(f) for f in files), default=0.0)

def init_data(meta_path: str, polygon_path: str, map_path: str, frame_path: str, mask_path: str,
    res=None, layer: str = "mean", chunk: int = 24, dry_threshold: float = 0.05,
    budget_gb: float = 2.0, max_frame_mb: float = 1.5):
    layer = layer or "mean"
    uds = open_map(map_path)
    if uds is None: return f"Cannot open: {map_path}"
    out, tmp_frames = None, frame_path + ".tmp.npy"
    try:
        if "time" not in uds.coords and "time" not in uds.dims: return "No time in data."
        grid = uds.ugrid.grid
        face_dim = grid.face_dimension
        uvar = vvar = None
        if layer == "mean":
            uvar, vvar = pick(uds, U_NAMES_AVG), pick(uds, V_NAMES_AVG)
        if uvar is None or vvar is None:
            uvar, vvar = pick(uds, U_NAMES), pick(uds, V_NAMES)
        if uvar is None or vvar is None:
            return f"Could not find velocity variables. ({U_NAMES + U_NAMES_AVG}). Found: {list(uds.data_vars)}"
        dvar = pick(uds, DEPTH_NAMES)
        s1var = pick(uds, S1_NAMES) if dvar is None else None
        bedvar = pick(uds, BED_NAMES) if dvar is None else None
        times = pd.to_datetime(uds["time"].values)
        T = len(times)
        if T == 0: return "No time steps in data."
        if not os.path.exists(polygon_path):
            if map_path.lower().endswith('.zarr'): ds = xr.open_zarr(map_path)
            else: ds = xr.open_dataset(map_path)
            with ds:
                gdf = functions.unstructuredGridCreator(ds)
                gdf = gdf.dissolve()
                if gdf.crs != 'EPSG:4326': gdf = gdf.to_crs('EPSG:4326')
                gdf.to_file(polygon_path, driver='GeoJSON')
        polygon, polygon_geojson = load_polygon(polygon_path)
        auto_info = None
        if res in (None, "", "auto", 0):
            res, auto_info = auto_resolution(grid, polygon, T, budget_gb, max_frame_mb)
        res = float(res)
        if res <= 0: return "Resolution must be > 0."
        west, south, east, north = polygon.bounds
        cos_lat = math.cos(math.radians(0.5 * (south + north)))
        dlat, dlon = res / M_PER_DEG, res / (M_PER_DEG * cos_lat)
        nx, ny = int(math.ceil((east - west) / dlon)), int(math.ceil((north - south) / dlat))
        if nx <= 0 or ny <= 0: return "Invalid polygon area."
        if nx * ny > 60_000_000: return f"Grid {nx} x {ny} is too large for resolution {res:g} m. Please increase resolution (m)."
        xc = west + (np.arange(nx) + 0.5) * dlon
        yc = south + (np.arange(ny) + 0.5) * dlat
        X, Y = np.meshgrid(xc, yc)
        px, py = X.ravel(), Y.ravel()
        inside = shapely.contains_xy(polygon, px, py)
        face_idx = np.full(px.size, -1, dtype=np.int64)
        if inside.any():
            face_idx[inside] = np.asarray(
                grid.locate_points(np.column_stack((px[inside], py[inside]))), dtype=np.int64).reshape(-1)
        valid = inside & (face_idx >= 0)
        if not valid.any(): return ("Polygon do not intersect with model grid. Please check polygon and grid in the same EPSG:4326.")
        vflat = np.flatnonzero(valid)
        vface = face_idx[vflat]
        Nv = vflat.size
        out = np.lib.format.open_memmap(tmp_frames, mode="w+", dtype="<i2", shape=(T, 3, Nv))
        def get(var_name, tsel):
            da = reduce_layers(uds[var_name], face_dim, layer)
            if "time" in da.dims:
                arr = np.asarray(da.isel(time=tsel).transpose("time", face_dim).values, dtype=np.float32)
            else:
                arr = np.asarray(da.transpose(face_dim).values, dtype=np.float32)
                arr = np.broadcast_to(arr[None, :], (len(tsel), arr.size))
            return arr[:, vface]
        rng = np.random.default_rng(0)
        speed_samples, depth_max = [], 0.0
        for k in range(0, T, chunk):
            tsel = np.arange(k, min(k + chunk, T))
            u, v = get(uvar, tsel), get(vvar, tsel)
            if dvar is not None: d = get(dvar, tsel)
            elif s1var is not None and bedvar is not None: d = get(s1var, tsel) - get(bedvar, tsel)
            else: d = np.full_like(u, 99.0)
            dry = ~np.isfinite(u) | ~np.isfinite(v) | ~np.isfinite(d) | (d <= dry_threshold)
            block = np.empty((len(tsel), 3, Nv), dtype="<i2")
            for c, values, scale in ((0, u, UV_SCALE), (1, v, UV_SCALE), (2, d, DEPTH_SCALE)):
                q = np.clip(np.round(np.nan_to_num(values, nan=0.0) / scale), -32767, 32767).astype("<i2")
                q[dry] = NODATA
                block[:, c] = q
            out[k:k + len(tsel)] = block
            wet = ~dry
            if wet.any():
                s = np.hypot(u, v)[wet]
                speed_samples.append(rng.choice(s, min(s.size, 20_000), replace=False))
                depth_max = max(depth_max, float(np.percentile(d[wet], 99)))
        out.flush()
        del out
        out = None
        os.replace(tmp_frames, frame_path)
        np.save(mask_path, valid.reshape(ny, nx).astype(np.uint8))
        allspd = np.concatenate(speed_samples) if speed_samples else np.array([1.0])
        meta = {
            "format": FORMAT_VERSION, "name": "Velocity",
            "version": f"{int(time.time())}-{res:g}-{layer}", "nx": nx, "ny": ny, "n_valid": int(Nv),
            "lon0": float(xc[0]), "lat0": float(yc[0]), "dlon": dlon, "dlat": dlat,
            "bounds": [[south, west], [south + ny * dlat, west + nx * dlon]],
            "res_m": res, "res_auto": auto_info is not None, "auto_info": auto_info,
            "layer": layer, "dry_threshold": dry_threshold, "velocity_vars": [uvar, vvar],
            "map_path": map_path, "map_mtime": map_mtime(map_path),
            "times": [t.isoformat() for t in times], "channels": ["u", "v", "depth"],
            "scale": {"u": UV_SCALE, "v": UV_SCALE, "depth": DEPTH_SCALE}, "nodata": NODATA,
            "speed_max": max(float(np.percentile(allspd, 99.5)), 0.01),
            "depth_max": max(depth_max, 0.5), "polygon": polygon_geojson,
            "size_mb": round(T * 3 * Nv * 2 / 1e6, 1),
        }
        tmp_meta = meta_path + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp_meta, meta_path)
        return None
    except Exception: raise
    finally:
        if out is not None: del out
        if os.path.exists(tmp_frames):
            try: os.remove(tmp_frames)
            except OSError: pass
        try: uds.close()
        except Exception: pass