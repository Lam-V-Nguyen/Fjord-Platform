import json, math, os, time, tomllib, gzip
import numpy as np, pandas as pd, xarray as xr, geopandas as gpd
from pyproj import CRS, Transformer
from fastapi import Response
from fastapi.responses import JSONResponse
from config import PROJECT_ROOT
from services import functions
from scipy.ndimage import gaussian_filter
from rasterio import features as rio_features
from rasterio.transform import from_origin


M_PER_DEG, FORMAT, VIEW_DIR = 111_320.0, 1, "viewer"
# PCRaster LDD: 1 SW 2 S 3 SE 4 W 5 pit 6 E 7 NW 8 N 9 NE
LDD = {1: (-1, -1), 2: (0, -1), 3: (1, -1), 4: (-1, 0), 6: (1, 0), 7: (-1, 1), 8: (0, 1), 9: (1, 1)}

VAR_INFO = {
    "overland_flow": ("m³/s", "Overland discharge", False, "flow", "Overland flow"),
    "overland_depth": ("m", "Overland water depth", False, "depth", "Overland flow"),
    "overland_volume": ("m³", "Overland water volume", False, "depth", "Overland flow"),
    "soil_evapotranspiration": ("mm/Δt", "Actual evapotranspiration", True, "runoff", "Soil"),
    "soil_storage_total": ("mm", "Total water storage", False, "depth", "Soil"),
    "soil_infiltration_volume": ("mm/Δt", "Infiltration", True, "depth", "Soil"),
    "soil_transpiration_volume": ("mm/Δt", "Transpiration", True, "runoff", "Soil"),
    "soil_runoff": ("mm/Δt", "Total surface runoff", True, "runoff", "Soil"),
    "soil_net_runoff": ("mm/Δt", "Net surface runoff", True, "runoff", "Soil"),
    "soil_water_volume_fraction": ("-", "Water volume fraction", False, "depth", "Soil"),
    "soil_water_volume_percentage": ("%", "Water volume percentage", False, "depth", "Soil"),
    "soil_water_rootzone_volume_fraction": ("-", "Root-zone water fraction", False, "depth", "Soil"),
    "soil_water_rootzone_volume_percentage": ("%", "Root-zone water percentage", False, "depth", "Soil"),
    "soil_water_rootzone_depth": ("mm", "Root-zone water storage", False, "depth", "Soil"),
    "soil_water_unsatzone_depth": ("mm", "Unsaturated store", False, "depth", "Soil"),
    "soil_water_satzone_capillary_volume_flux": ("mm/Δt", "Capillary rise", True, "depth", "Groundwater"),
    "soil_water_satzone_recharge_volume_flux": ("mm/Δt", "Recharge to saturated zone", True, "depth", "Groundwater"),
    "soil_water_satzone_net_recharge_volume_flux": ("mm/Δt", "Net recharge", True, "depth", "Groundwater"),
    "soil_water_satzone_leakage_volume_flux": ("mm/Δt", "Leakage", True, "depth", "Groundwater"),
    "soil_water_satzone_depth": ("mm", "Water table depth", False, "hours", "Groundwater"),
    "river_discharge": ("m³/s", "River discharge", False, "flow", "River"),
    "river_depth": ("m", "River depth", False, "depth", "River"),
    "river_volume": ("m³", "River volume", False, "depth", "River"),
    "river_lateral_inflow": ("m³/s", "Lateral inflow to river", False, "flow", "River"),
    "lake_volume": ("m³", "Lake volume", False, "depth", "Lakes & reservoirs"),
    "lake_level": ("m", "Lake level", False, "depth", "Lakes & reservoirs"),
    "lake_inflow": ("m³/s", "Lake inflow", False, "flow", "Lakes & reservoirs"),
    "lake_outflow": ("m³/s", "Lake outflow", False, "flow", "Lakes & reservoirs"),
    "reservoir_volume": ("m³", "Reservoir volume", False, "depth", "Lakes & reservoirs"),
    "reservoir_inflow": ("m³/s", "Reservoir inflow", False, "flow", "Lakes & reservoirs"),
    "reservoir_outflow": ("m³/s", "Reservoir outflow", False, "flow", "Lakes & reservoirs"),
    "snow_water": ("mm", "Snow water equivalent", False, "depth", "Snow & glacier"),
    "snow_melt": ("mm/Δt", "Snow melt", True, "depth", "Snow & glacier"),
    "snow_runoff": ("mm/Δt", "Snowpack runoff", True, "depth", "Snow & glacier"),
    "glacier_melt": ("mm/Δt", "Glacier melt", True, "depth", "Snow & glacier"),
    "vegetation_stemflow": ("mm/Δt", "Stemflow", True, "depth", "Vegetation"),
    "vegetation_throughfall": ("mm/Δt", "Throughfall", True, "depth", "Vegetation"),
}
LAYER_SEP = "__layer"
LDD_NAMES = ["local_drain_direction", "wflow_ldd", "ldd"]
RIVER_NAMES = ["river_mask", "wflow_river"]
BASIN_NAMES = ["subcatchment", "wflow_subcatch", "basins"]
SLOPE_NAMES = ["land_slope", "Slope", "slope"]
LAND_N_NAMES = ["land_manning_n", "N"]
RIVER_W_NAMES = ["river_width", "wflow_riverwidth"]
RIVER_N_NAMES = ["river_manning_n", "N_River"]
RIVER_S_NAMES = ["river_slope", "RiverSlope"]
LDD_UNIT = {k: (dx / math.hypot(dx, dy), dy / math.hypot(dx, dy)) for k, (dx, dy) in LDD.items()}

class NpyRows:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            version = np.lib.format.read_magic(f)
            reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
            self.shape, _, dtype = reader(f)
            self.offset = f.tell()
        self.dtype = np.dtype(dtype)
        self.row_bytes = int(np.prod(self.shape[1:])) * self.dtype.itemsize
    def row_bytes_at(self, i):
        with open(self.path, "rb") as f:
            f.seek(self.offset + i * self.row_bytes)
            return f.read(self.row_bytes)
    def column(self, j):
        m = np.load(self.path, mmap_mode="r")
        try: return np.array(m[:, j], dtype=np.float32)
        finally:
            mm = getattr(m, "_mmap", None)
            del m
            if mm is not None: mm.close()
    def iter_chunks(self, chunk=96):
        m = np.load(self.path, mmap_mode="r")
        try:
            for k in range(0, self.shape[0], chunk):
                yield np.array(m[k:k + chunk], dtype=np.float32)
        finally:
            mm = getattr(m, "_mmap", None)
            del m
            if mm is not None: mm.close()

def wflow_dir(project_name, flow_name, model_folder=None):
    return os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "flows", flow_name, model_folder or "wflow_model"))

def water_file(model_dir):
    water_dir = os.path.join(os.path.dirname(os.path.normpath(model_dir)), "water_area")
    if not os.path.isdir(water_dir): return None
    files = sorted(f for f in os.listdir(water_dir) if f.lower().endswith(".geojson"))
    return os.path.join(water_dir, files[0]) if files else None

def water_signature(path):
    if not path or not os.path.isfile(path): return None
    return {"file": os.path.basename(path), "mtime": round(os.path.getmtime(path), 3)}

def water_on_display(path, west, south, dlon, dlat, nx, ny):
    if not path or not os.path.isfile(path): return None
    gdf = gpd.read_file(path)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty: return None
    gdf = gdf.set_crs(4326) if gdf.crs is None else gdf.to_crs(4326)
    tr = from_origin(west, south + ny * dlat, dlon, dlat)
    w = rio_features.rasterize(((geom, 1) for geom in gdf.geometry.make_valid()),
        out_shape=(ny, nx), transform=tr, fill=0, dtype="uint8")
    if w is None: return False
    return w[::-1].astype(bool)

def needs_rebuild(model_dir, res=None):
    p = model_paths(model_dir)
    if not os.path.exists(p["output"]):
        raise FileNotFoundError(f"Wflow output not found: {p['output']}. Run the wflow model first.")
    meta_path = os.path.join(p["cache"], "meta.json")
    if not os.path.exists(meta_path):
        return "No viewer cache yet"
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return "Viewer meta.json is broken"
    if meta.get("format") != FORMAT:
        return "Old viewer cache format"
    if os.path.getmtime(p["output"]) > float(meta.get("output_mtime", 0)) + 1:
        return "Model output is newer than the viewer cache"
    if res in (None, ""): pass
    elif str(res).strip().lower() == "auto":
        if not meta["grid"].get("res_auto"):
            return f"Resolution changed {meta['grid']['res_m']} m -> auto"
    elif abs(float(res) - float(meta["grid"]["res_m"])) > 1e-6:
        return f"Resolution changed {meta['grid']['res_m']} -> {res} m"
    if "velocity" not in meta:
        return "Viewer cache has no velocity field (particles)"
    if meta.get("velocity") and not os.path.exists(os.path.join(p["cache"], meta["velocity"]["file"])):
        return "Velocity cache file is missing"
    for v, info in meta.get("variables", {}).items():
        if not os.path.exists(os.path.join(p["cache"], f"{info['kind']}_{v}.npy")):
            return f"Cache file for {v} is missing"
    if meta.get("water") != water_signature(water_file(model_dir)):
        return "Water area changed"
    return None

def wflow_ctx(project_name, flow_name, model_folder, user, META):
    project_name, _ = functions.project_definer(project_name, user)
    directory = wflow_dir(project_name, flow_name, model_folder)
    meta = META.get(directory)
    if meta is not None: return directory, meta, None
    meta_path = os.path.join(model_paths(directory)["cache"], "meta.json")
    try:
        meta = META[directory] = load_meta(directory)
        return directory, meta, None
    except FileNotFoundError:
        return directory, None, f"Meta.json not found at {meta_path}"
    except Exception as e:
        return directory, None, f"Cannot read {meta_path}: {e}"

def not_ready(detail=""):
    return JSONResponse(status_code=404, content={"status": "error",
        "message": f"Viewer data not initialized. {detail} (pid {os.getpid()})".strip()})

def model_paths(model_dir):
    cfg = {}
    toml = os.path.join(model_dir, "wflow_sbm.toml")
    if os.path.exists(toml):
        with open(toml, "rb") as f:
            cfg = tomllib.load(f)
    run_dir = os.path.join(model_dir, cfg.get("dir_output", "run_default"))
    run_toml = os.path.join(run_dir, "wflow_sbm.toml")
    if os.path.exists(run_toml):
        with open(run_toml, "rb") as f:
            run_cfg = tomllib.load(f)
        cfg = {**cfg, "output": run_cfg.get("output", cfg.get("output", {}))}
    grid_name = (cfg.get("output", {}).get("netcdf_grid") or {}).get("path", "output.nc")
    static_name = (cfg.get("input", {}) or {}).get("path_static", "static_grid.nc")
    return {
        "output": os.path.join(run_dir, grid_name),
        "static": os.path.join(model_dir, static_name),
        "basins": os.path.join(model_dir, "staticgeoms", "basins.geojson"),
        "rivers_src": os.path.join(model_dir, "staticgeoms", "rivers.geojson"),
        "cache": os.path.join(model_dir, VIEW_DIR),
    }

def get_crs(*datasets, default="EPSG:25833"):
    for ds in datasets:
        if ds is None: continue
        for v in ("spatial_ref", "crs"):
            if v in ds.variables:
                a = ds[v].attrs
                for key in ("crs_wkt", "spatial_ref", "epsg"):
                    if key in a:
                        try: return CRS.from_user_input(a[key])
                        except Exception: pass
    return CRS.from_user_input(default)

def _pick(ds, names):
    return next((n for n in names if n in ds), None)

def _nice(x):
    if x <= 0: return 1.0
    e = 10.0 ** math.floor(math.log10(x))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= x * 0.999: return float(m * e)
    return float(10.0 * e)

def build_velocity(cache_dir, ds, static, flip, cell, src_crs, x, ys, src, 
    domain, T, chunk, depth_threshold=1e-4, max_speed=5.0, smooth_cells=1.5):
    if "overland_flow" not in ds and "river_discharge" not in ds: return None
    def rd(name, sl):
        a = np.asarray(ds[name].isel(time=sl).transpose("time", "y", "x").values, dtype=np.float32)
        return a[:, ::-1] if flip else a
    ldd = static(LDD_NAMES)
    if ldd is not None:
        code = np.nan_to_num(ldd.astype(float)).astype(int)
        ux, uy = np.zeros(code.shape), np.zeros(code.shape)
        for c, (dx, dy) in LDD_UNIT.items():
            m = code == c; ux[m], uy[m] = dx, dy
        how = "LDD"
    else:
        dem = static(["land_elevation", "wflow_dem", "elevtn"])
        if dem is None:
            raise ValueError("Static grid has no local_drain_direction or land_elevation")
        z = gaussian_filter(np.nan_to_num(dem.astype(float), nan=np.nanmax(dem)), 1.0)
        gy, gx = np.gradient(z, cell)
        n = np.hypot(gx, gy) + 1e-12
        ux, uy, how = -gx / n, -gy / n, "terrain slope"
    first = slice(0, min(T, chunk))
    wgt = np.zeros(domain.shape)
    for name in ("overland_flow", "river_discharge"):
        if name in ds:
            with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
                __import__("warnings").simplefilter("ignore", RuntimeWarning)
                wgt += np.nan_to_num(np.nanmean(np.abs(rd(name, first)), 0))
    wgt += 1e-9
    sx, sy = gaussian_filter(ux * wgt, smooth_cells), gaussian_filter(uy * wgt, smooth_cells)
    nrm = np.hypot(sx, sy); okd = nrm > 1e-12
    ux = np.where(okd, sx / np.where(okd, nrm, 1), ux); uy = np.where(okd, sy / np.where(okd, nrm, 1), uy)
    to_ll = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
    ii, jj = np.nonzero(domain)
    xm, ym = float(x[jj].mean()), float(ys[ii].mean())
    lo1, la1 = to_ll.transform(xm, ym); lo2, la2 = to_ll.transform(xm, ym + 100.0)
    g = math.atan2((lo2 - lo1) * math.cos(math.radians(la1)), la2 - la1)
    ue, vn = ux * math.cos(g) + uy * math.sin(g), -ux * math.sin(g) + uy * math.cos(g)
    ue, vn = ue.ravel()[src].astype(np.float32), vn.ravel()[src].astype(np.float32)
    def arr(names, default):
        a = static(names)
        return np.full(domain.shape, default) if a is None else np.nan_to_num(a.astype(float), nan=default)
    has_h, has_hr = "overland_depth" in ds, "river_depth" in ds
    S_land = np.maximum(arr(SLOPE_NAMES, 1e-3), 1e-4) if not has_h else None
    n_land = arr(LAND_N_NAMES, 0.1) if not has_h else None
    rmask = static(RIVER_NAMES)
    rmask = (np.nan_to_num(rmask.astype(float)) > 0) if rmask is not None else np.zeros(domain.shape, bool)
    use_river = "river_discharge" in ds and rmask.any()
    W_r = np.maximum(arr(RIVER_W_NAMES, cell), 0.5) if use_river else None
    S_r = np.maximum(arr(RIVER_S_NAMES, 1e-3), 1e-4) if use_river and not has_hr else None
    n_r = arr(RIVER_N_NAMES, 0.036) if use_river and not has_hr else None
    r_sel = rmask.ravel()[src] if use_river else None
    Nv = src.size
    path = os.path.join(cache_dir, "velocity.npy")
    out = np.lib.format.open_memmap(path, mode="w+", dtype="<f2", shape=(T, 2, Nv))
    rng, samples = np.random.default_rng(0), []
    for k in range(0, T, chunk):
        sl = slice(k, min(k + chunk, T)); n = sl.stop - sl.start
        spd = np.full((n, Nv), np.nan, np.float32)
        with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
            if "overland_flow" in ds:
                Q = np.abs(rd("overland_flow", sl).reshape(n, -1)[:, src])
                if n_land is None or S_land is None: raise ValueError("n_land and S_land must not be None.")
                if has_h: H = rd("overland_depth", sl).reshape(n, -1)[:, src]
                else: H = (Q * n_land.ravel()[src] / (cell * np.sqrt(S_land.ravel()[src]))) ** 0.6
                wet = np.isfinite(Q) & np.isfinite(H) & (H > depth_threshold)
                spd = np.where(wet, Q / (np.where(wet, H, 1) * cell), spd)
            if use_river:
                Qr = np.abs(rd("river_discharge", sl).reshape(n, -1)[:, src])
                if W_r is None or n_r is None or S_r is None or r_sel is None:
                    raise ValueError("W_r, n_r, S_r and r_sel must not be None.")
                W = W_r.ravel()[src]
                if has_hr: Hr = rd("river_depth", sl).reshape(n, -1)[:, src]
                else: Hr = (Qr * n_r.ravel()[src] / (W * np.sqrt(S_r.ravel()[src]))) ** 0.6
                wet_r = r_sel[None] & np.isfinite(Qr) & np.isfinite(Hr) & (Hr > depth_threshold)
                spd = np.where(wet_r, Qr / (np.where(wet_r, Hr, 1) * W), spd)
        spd = np.clip(spd, 0, max_speed)
        out[sl, 0] = (spd * ue).astype("<f2")
        out[sl, 1] = (spd * vn).astype("<f2")
        v = spd[np.isfinite(spd) & (spd > 0)]
        if v.size: samples.append(rng.choice(v, min(v.size, 20000), replace=False))
    out.flush(); del out
    allv = np.concatenate(samples) if samples else np.array([0.01])
    return {
        "file": "velocity.npy", "dtype": "<f2", "n": int(Nv),
        "speed_max": max(float(np.percentile(allv, 99.5)), 1e-3),
        "speed_p50": float(np.percentile(allv, 50)), "direction": how,
        "meridian_convergence_deg": math.degrees(g),
        "note": "u = east, v = north (m/s); NaN = dry cell"
    }

def velocity_bytes(model_dir, meta, i):
    vel = meta.get("velocity")
    if not vel:
        raise FileNotFoundError("No velocity field in the viewer cache")
    return NpyRows(os.path.join(model_paths(model_dir)["cache"], vel["file"])).row_bytes_at(i)

def build_cache(model_dir:str, res:str, budget_mb_per_var=400, chunk=48, 
    crs=None, progress=None, only=None, water_path="auto"):
    p = model_paths(model_dir)
    if not os.path.exists(p["output"]):
        raise FileNotFoundError(f"Wflow output not found: {p['output']} (run the model first).")
    os.makedirs(p["cache"], exist_ok=True)
    ds = xr.open_dataset(p["output"])
    st = xr.open_dataset(p["static"]) if os.path.exists(p["static"]) else None
    try:
        if not {"x", "y", "time"} <= set(ds.dims):
            raise ValueError(f"output.nc must have time, y, x dimensions (got {dict(ds.sizes)}).\n"
                "Grid output is required – scalar/point outputs cannot be mapped.")
        src_crs = CRS.from_user_input(crs) if crs else get_crs(ds, st)
        x, y = ds.x.values.astype(float), ds.y.values.astype(float)
        flip = y[0] > y[-1]
        ys = y[::-1] if flip else y
        cell = float(abs(x[1] - x[0]))
        ny0, nx0 = ys.size, x.size
        times = pd.to_datetime(ds.time.values)
        T, layer_of = len(times), {}
        for v in ds.data_vars:
            dims = set(ds[v].dims)
            if not {"time", "y", "x"} <= dims: continue
            extra = [d for d in ds[v].dims if d not in ("time", "y", "x")]
            if not extra: layer_of[v] = (v, {}, None)
            elif len(extra) == 1:
                for n in range(ds.sizes[extra[0]]):
                    layer_of[f"{v}{LAYER_SEP}{n + 1}"] = (v, {extra[0]: n}, n + 1)
        if only: layer_of = {k: val for k, val in layer_of.items() if k in only or val[0] in only}
        variables = list(layer_of)
        if not variables: raise ValueError("No (time, y, x) variables in output.nc")
        def static(names):
            if st is None: return None
            n = _pick(st, names)
            if n is None: return None
            a = np.asarray(st[n].reindex(y=ds.y, x=ds.x, method="nearest", tolerance=cell / 2).values)
            return a[::-1] if flip else a
        def read(v, sl):
            src, sel, _ = layer_of[v]
            a = np.asarray(ds[src].isel(time=sl, **sel).transpose("time", "y", "x").values, dtype=np.float32)
            return a[:, ::-1] if flip else a
        # Valid area on the original grid
        basin = static(BASIN_NAMES)
        if basin is not None:
            domain = np.nan_to_num(basin.astype(float)) > 0
        else:
            first = read(variables[0], slice(0, 1))[0]
            domain = np.isfinite(first)
        river = static(RIVER_NAMES)
        river = (np.nan_to_num(river.astype(float)) > 0) & domain if river is not None else np.zeros_like(domain)
        ldd = static(LDD_NAMES)
        to_ll = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
        to_xy = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True)
        ii, jj = np.nonzero(domain)
        bx = np.array([x[jj].min() - cell, x[jj].max() + cell]); by = np.array([ys[ii].min() - cell, ys[ii].max() + cell])
        corners = to_ll.transform(np.r_[bx, bx], np.r_[by[0], by[0], by[1], by[1]])
        west, east, south, north = min(corners[0]), max(corners[0]), min(corners[1]), max(corners[1])
        lat_mid = 0.5 * (south + north)
        kx = M_PER_DEG * math.cos(math.radians(lat_mid))
        n_dom = int(domain.sum())
        res_auto = res in (None, "", "auto", 0) 
        if res in (None, "", "auto", 0):
            area = n_dom * cell * cell
            res_val: float = _nice(max(cell, math.sqrt(area * T * 2 / (budget_mb_per_var * 1e6))))
        else: res_val = float(res)
        dlat, dlon = res_val / M_PER_DEG, res_val / kx
        nx, ny = int(math.ceil((east - west) / dlon)), int(math.ceil((north - south) / dlat))
        lon = west + (np.arange(nx) + 0.5) * dlon; lat = south + (np.arange(ny) + 0.5) * dlat
        LON, LAT = np.meshgrid(lon, lat)
        PX, PY = to_xy.transform(LON.ravel(), LAT.ravel())
        ci = np.floor((PX - (x.min() - cell / 2)) / cell).astype(int)
        cj = np.floor((PY - (ys.min() - cell / 2)) / cell).astype(int)
        ok = (ci >= 0) & (ci < nx0) & (cj >= 0) & (cj < ny0)
        ok[ok] &= domain[cj[ok], ci[ok]]
        vflat = np.flatnonzero(ok)
        src = cj[ok] * nx0 + ci[ok]
        Nv = vflat.size
        mask_out = ok.reshape(ny, nx).astype(np.uint8)
        wpath = water_file(model_dir) if water_path == "auto" else water_path
        water = water_on_display(wpath, west, south, dlon, dlat, nx, ny)
        if water is not None: mask_out[(mask_out == 1) & water] = 2
        n_water = int((mask_out == 2).sum())
        np.save(os.path.join(p["cache"], "mask.npy"), mask_out)
        src_idx = src.astype(np.int64)
        np.save(os.path.join(p["cache"], "src_index.npy"), src_idx)
        # River
        r_i, r_j = np.nonzero(river)
        r_flat = r_i * nx0 + r_j
        feats = []
        for k, (i, j) in enumerate(zip(r_i, r_j)):
            x0, y0 = x[j], ys[i]
            d = LDD.get(int(ldd[i, j])) if ldd is not None else None
            if d: x1, y1 = x0 + d[0] * cell, y0 + d[1] * cell
            else: x1, y1 = x0, y0
            (lo0, lo1), (la0, la1) = to_ll.transform([x0, x1], [y0, y1])
            feats.append({"type": "Feature", "properties": {"id": k},
                "geometry": {"type": "LineString", "coordinates": [[lo0, la0], [lo1, la1]]}})
        with open(os.path.join(p["cache"], "rivers.geojson"), "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": feats}, f)
        Nr = len(feats)
        kinds = {v: ("river" if layer_of[v][0].startswith("river_") else "grid") for v in variables}
        if not Nr:
            for v in variables:
                if kinds[v] == "river": kinds[v] = "grid"
        idx = {"grid": src, "river": r_flat}
        sizes = {"grid": Nv, "river": Nr}
        probe = {v: np.nanmax(np.abs(read(v, slice(0, min(T, chunk))))) for v in variables}
        dtypes = {v: ("<f2" if kinds[v] == "grid" and np.nan_to_num(probe[v]) < 3e4 else "<f4") for v in variables}
        out = {v: np.lib.format.open_memmap(os.path.join(p["cache"], f"{kinds[v]}_{v}.npy"), 
            mode="w+", dtype=dtypes[v], shape=(T, sizes[kinds[v]])) for v in variables}
        stats = {v: {"max": np.full(sizes[kinds[v]], -np.inf, np.float32),
            "tmax": np.zeros(sizes[kinds[v]], np.int32),
            "sum": np.zeros(sizes[kinds[v]], np.float64),
            "n": np.zeros(sizes[kinds[v]], np.int32)} for v in variables
        }
        samples = {v: [] for v in variables}
        rng = np.random.default_rng(0)
        for k in range(0, T, chunk):
            sl = slice(k, min(k + chunk, T))
            for v in variables:
                a = read(v, sl).reshape(sl.stop - sl.start, -1)[:, idx[kinds[v]]]
                out[v][sl] = a.astype(dtypes[v])
                s = stats[v]
                fin = np.isfinite(a)
                a0 = np.where(fin, a, -np.inf)
                cm = a0.max(0); ca = a0.argmax(0) + k
                upd = cm > s["max"]
                s["max"][upd] = cm[upd]; s["tmax"][upd] = ca[upd]
                s["sum"] += np.where(fin, a, 0).sum(0); s["n"] += fin.sum(0)
                vals = a[fin]
                if vals.size: samples[v].append(rng.choice(vals, min(vals.size, 20000), replace=False))
            if progress: progress(sl.stop, T)
        meta_vars = {}
        for v in variables:
            out[v].flush()
            s = stats[v]
            mx = np.where(np.isfinite(s["max"]), s["max"], np.nan)
            mean = np.where(s["n"] > 0, s["sum"] / np.maximum(s["n"], 1), np.nan)
            total = np.where(s["n"] > 0, s["sum"], np.nan)
            np.savez(os.path.join(p["cache"], f"stats_{v}.npz"), max=mx.astype(np.float32),
                mean=mean.astype(np.float32), sum=total.astype(np.float32),
                tmax=np.where(s["n"] > 0, s["tmax"], -1).astype(np.int32)
            )
            smp = np.concatenate(samples[v]) if samples[v] else np.array([0.0])
            pos = smp[smp > 0]
            src, _, layer = layer_of[v]
            unit, desc, flux, ramp, group = VAR_INFO.get(
                src, (ds[src].attrs.get("units", ""), ds[src].attrs.get("long_name", src), False, "depth", "Other")
            )
            if layer: desc = f"{desc} – layer {layer}"
            p99 = float(np.percentile(smp, 99)) if smp.size else 1.0
            skew = (p99 / max(float(np.percentile(pos, 50)), 1e-12)) if pos.size else 1.0
            meta_vars[v] = {"kind": kinds[v], "units": unit, "description": desc, "flux": flux,
                "group": group, "source": src, "layer": layer,
                "ramp": ramp, "dtype": dtypes[v], "n": sizes[kinds[v]],
                "vmin": float(np.nanmin(smp)), "p99": p99 if p99 > 0 else float(np.nanmax(smp) or 1),
                "log": bool(skew > 50), "max": float(np.nanmax(mx)) if np.isfinite(mx).any() else 0.0
            }
        step_h = float((times[1] - times[0]).total_seconds() / 3600) if T > 1 else 1.0
        meta = {
            "format": FORMAT, "version": f"{int(time.time())}-{res:g}",
            "output_mtime": os.path.getmtime(p["output"]), "crs": src_crs.to_string(),
            "grid": {"nx": nx, "ny": ny, "lon0": float(lon[0]), "lat0": float(lat[0]), "dlon": dlon, "dlat": dlat,
                "bounds": [[south, west], [south + ny * dlat, west + nx * dlon]], "res_m": res,
                "res_auto": res_auto, "n_valid": int(Nv), "source_cell_m": cell, "n_water": n_water
            }, "times": [t.isoformat() for t in times], "step_hours": step_h,
            "variables": meta_vars, "river_cells": int(Nr), "water": water_signature(wpath),
        }
        # Particle
        try:
            meta["velocity"] = build_velocity(p["cache"], ds, static, flip, cell, src_crs, x, ys, src_idx, domain, T, chunk)
        except Exception as e:
            meta["velocity"] = None
            raise ValueError(f"build_cache: velocity field skipped – {e}")
        with open(os.path.join(p["cache"], "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        return meta
    finally:
        ds.close()
        if st is not None:
            st.close()

def load_meta(model_dir):
    with open(os.path.join(model_paths(model_dir)["cache"], "meta.json"), encoding="utf-8") as f:
        return json.load(f)

def get_bin(request, data, headers=None):
    headers = {**(headers or {}), "Cache-Control": "private, max-age=86400"}
    if "gzip" in request.headers.get("accept-encoding", "") and len(data) > 2048:
        data = gzip.compress(data, compresslevel=1, mtime=0)
        headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(data, media_type="application/octet-stream", headers=headers)

def frame_bytes(model_dir, meta, var, i):
    info = meta["variables"][var]
    path = os.path.join(model_paths(model_dir)["cache"], f"{info['kind']}_{var}.npy")
    return NpyRows(path).row_bytes_at(i)

def point_series(model_dir, meta, lat, lon, variables=None, river_radius_cells=3):
    g = meta["grid"]
    cache = model_paths(model_dir)["cache"]
    ix = int(math.floor((lon - g["lon0"]) / g["dlon"] + 0.5))
    iy = int(math.floor((lat - g["lat0"]) / g["dlat"] + 0.5))
    if not (0 <= ix < g["nx"] and 0 <= iy < g["ny"]): return None
    mask = np.load(os.path.join(cache, "mask.npy")).ravel()
    flat = iy * g["nx"] + ix
    if not mask[flat]: return None
    j_grid = int((mask[:flat] > 0).sum())
    out = {
        "lat": g["lat0"] + iy * g["dlat"], "lon": g["lon0"] + ix * g["dlon"], "series": {}, "river": None
    }
    river_j = None
    if meta.get("river_cells"):
        with open(os.path.join(cache, "rivers.geojson"), encoding="utf-8") as f:
            feats = json.load(f)["features"]
        pts = np.array([ft["geometry"]["coordinates"][0] for ft in feats])
        d = np.hypot((pts[:, 0] - lon) / g["dlon"], (pts[:, 1] - lat) / g["dlat"])
        k = int(np.argmin(d))
        cells = d[k] * g["res_m"] / meta["grid"]["source_cell_m"]
        if cells <= river_radius_cells:
            river_j = k
            out["river"] = {"id": k, "lon": float(pts[k, 0]), "lat": float(pts[k, 1])}
    for v, info in meta["variables"].items():
        if variables and v not in variables: continue
        j = j_grid if info["kind"] == "grid" else river_j
        if j is None: continue
        s = NpyRows(os.path.join(cache, f"{info['kind']}_{v}.npy")).column(j)
        out["series"][v] = [None if not np.isfinite(x) else round(float(x), 6) for x in s]
    return out

def stat_array(model_dir, meta, var, stat, threshold=None):
    info = meta["variables"][var]
    cache = model_paths(model_dir)["cache"]
    if stat == "above":
        if threshold is None:
            raise ValueError("threshold is required for stat=above")
        rows = NpyRows(os.path.join(cache, f"{info['kind']}_{var}.npy"))
        count = np.zeros(rows.shape[1], np.float32)
        for block in rows.iter_chunks():
            count += (np.nan_to_num(block, nan=-np.inf) > threshold).sum(0)
        return count * meta["step_hours"]
    with np.load(os.path.join(cache, f"stats_{var}.npz")) as z:
        if stat == "tmax":
            t = z["tmax"].astype(np.float32)
            return np.where(t >= 0, t * meta["step_hours"], np.nan).astype(np.float32)
        if stat not in ("max", "mean", "sum"):
            raise ValueError(f"Unknown stat: {stat}")
        return z[stat].astype(np.float32)