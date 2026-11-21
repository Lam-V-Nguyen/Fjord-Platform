"""
dashboard_wflow3d.py
====================
Dữ liệu bổ sung cho chế độ hiển thị "Wflow 3D" trên Dashboard.

Module này ĐỘC LẬP với workflow hiện có:
  - chỉ ĐỌC output Wflow, static grid, forcing và cache viewer của dashboard
    (<project>/flows/<flow>/wflow_model/<DASH_VIEW_DIR>/, dựng bởi POST /dashboard/wflow/init);
  - chỉ GHI thêm các file riêng có tiền tố "w3d_" trong thư mục cache đó
    (w3d_terrain.npy, w3d_scene.json). Xoá chúng không ảnh hưởng tới lớp "Wflow results" 2D.

Hàm công khai:
    terrain(project, flow)        -> (bytes float32 (ny, nx), info)  độ cao trên lưới viewer, hàng 0 = phía nam
    scene(project, flow)          -> dict: cửa ra, hydrograph tại cửa ra, mưa trung bình lưu vực
    river_series(project, flow, river_id, var=None) -> dict: chuỗi thời gian tại một ô sông
"""
from __future__ import annotations
import glob, json, os, threading, tomllib
from typing import Optional
import numpy as np, pandas as pd, xarray as xr
from pyproj import Transformer
from scipy import ndimage
from services import constants
from services import dashboard_extras as dext

_LOCKS: dict[str, threading.Lock] = {}

def _lock(key: str) -> threading.Lock:
    return _LOCKS.setdefault(key, threading.Lock())


# helpers
def _ctx(project: str, flow: str):
    """(model_dir, meta, fc, paths) – meta là meta.json của cache viewer dashboard."""
    model, meta, fc = dext.wflow_context(project, flow)
    return model, meta, fc, fc.model_paths(model, constants.DASH_VIEW_DIR)

def _xy_names(ds) -> tuple[str, str]:
    for xn, yn in (("x", "y"), ("lon", "lat"), ("longitude", "latitude")):
        if xn in ds.coords and yn in ds.coords: return xn, yn
    raise ValueError("Static grid has no x/y (or lon/lat) coordinates")

def _pick(ds, names):
    return next((n for n in names if n in ds), None)

def _read_toml(model_dir: str) -> dict:
    path = os.path.join(model_dir, "wflow_sbm.toml")
    if not os.path.exists(path): return {}
    with open(path, "rb") as f: return tomllib.load(f)

def _fill_nearest(a: np.ndarray) -> np.ndarray:
    bad = ~np.isfinite(a)
    if not bad.any(): return a
    if bad.all(): return np.zeros_like(a)
    idx = ndimage.distance_transform_edt(bad, return_distances=False, return_indices=True)
    return a[tuple(idx)]

def _viewer_lonlat(g: dict):
    lon = g["lon0"] + np.arange(g["nx"]) * g["dlon"]
    lat = g["lat0"] + np.arange(g["ny"]) * g["dlat"]
    return np.meshgrid(lon, lat)                          # (ny, nx), hàng 0 = phía nam

def _sample_static(da: xr.DataArray, xn: str, yn: str, PX: np.ndarray, PY: np.ndarray, order=1) -> np.ndarray:
    """Nội suy (bilinear) một biến static 2D tại toạ độ PX, PY (cùng CRS với static)."""
    a = np.asarray(da.values, dtype=np.float64)
    xs, ys = np.asarray(da[xn].values, float), np.asarray(da[yn].values, float)
    fx = (PX - xs[0]) / (xs[1] - xs[0])
    fy = (PY - ys[0]) / (ys[1] - ys[0])
    valid = np.isfinite(a)
    fill = np.nanmean(a) if valid.any() else 0.0
    out = ndimage.map_coordinates(np.where(valid, a, fill), [fy.ravel(), fx.ravel()], order=order, mode="nearest")
    w = ndimage.map_coordinates(valid.astype(float), [fy.ravel(), fx.ravel()], order=order, mode="nearest")
    inside = (fx.ravel() >= -0.5) & (fx.ravel() <= a.shape[1] - 0.5) & (fy.ravel() >= -0.5) & (fy.ravel() <= a.shape[0] - 0.5)
    out = np.where((w > 0.5) & inside, out, np.nan)
    return out.reshape(PX.shape)

# terrain
def terrain(project: str, flow: str) -> tuple[bytes, dict]:
    model, meta, fc, p = _ctx(project, flow)
    g = meta["grid"]; cache = p["cache"]
    path, info_path = os.path.join(cache, constants.TERRAIN_FILE), os.path.join(cache, constants.TERRAIN_FILE + ".json")
    with _lock(f"terrain:{model}"):
        info = None
        if os.path.exists(path) and os.path.exists(info_path):
            with open(info_path, encoding="utf-8") as f: info = json.load(f)
            if info.get("version") != meta.get("version"): info = None
        if info is None:
            if not os.path.exists(p["static"]):
                raise FileNotFoundError(f"Static grid not found: {p['static']}")
            with xr.open_dataset(p["static"]) as st:
                name = _pick(st, constants.ELEV_NAMES)
                if name is None:
                    raise ValueError(f"Static grid has no elevation variable ({', '.join(constants.ELEV_NAMES)})")
                xn, yn = _xy_names(st)
                da = st[name].squeeze(drop=True).transpose(yn, xn)
                LON, LAT = _viewer_lonlat(g)
                to_xy = Transformer.from_crs("EPSG:4326", meta.get("crs") or "EPSG:25833", always_xy=True)
                PX, PY = to_xy.transform(LON, LAT)
                z = _sample_static(da, xn, yn, np.asarray(PX), np.asarray(PY))
            nodata = float(np.isnan(z).mean())
            z = _fill_nearest(z).astype(np.float32)
            np.save(path, z)
            info = {"version": meta.get("version"), "source": name, "zmin": float(np.nanmin(z)),
                    "zmax": float(np.nanmax(z)), "filled_fraction": round(nodata, 4),
                    "nx": g["nx"], "ny": g["ny"]}
            with open(info_path, "w", encoding="utf-8") as f: json.dump(info, f)
        z = np.load(path)
    return np.ascontiguousarray(z, dtype="<f4").tobytes(), info

#  scene
def _river_cells(model: str, meta: dict, fc, p: dict):
    """Dựng lại danh sách ô sông ĐÚNG thứ tự như flow_connection.build_cache
    (feature id k trong rivers.geojson = cột k của river_<var>.npy)."""
    with xr.open_dataset(p["output"]) as ds:
        x, y = ds.x.values.astype(float), ds.y.values.astype(float)
        dsx, dsy = ds.x, ds.y
        flip = y[0] > y[-1]; cell = float(abs(x[1] - x[0]))
        if not os.path.exists(p["static"]): return None
        with xr.open_dataset(p["static"]) as st:
            def static(names):
                n = _pick(st, names)
                if n is None: return None
                a = np.asarray(st[n].reindex(y=dsy, x=dsx, method="nearest", tolerance=cell / 2).values)
                return a[::-1] if flip else a
            basin = static(constants.BASIN_NAMES)
            river = static(constants.RIVER_NAMES)
            uparea = static(constants.UPAREA_NAMES)
    if river is None or basin is None: return None
    domain = np.nan_to_num(basin.astype(float)) > 0
    river = (np.nan_to_num(river.astype(float)) > 0) & domain
    r_i, r_j = np.nonzero(river)
    if r_i.size != int(meta.get("river_cells") or 0): return None          # cache khác cấu hình -> bỏ qua
    sub = np.nan_to_num(basin.astype(float))[r_i, r_j].astype(int)
    up = np.nan_to_num(uparea.astype(float), nan=-1)[r_i, r_j] if uparea is not None else None
    return {"sub": sub, "uparea": up, "n_domain": int(domain.sum()), "cell_m": cell}

def _discharge_var(meta: dict) -> Optional[str]:
    vs = meta.get("variables", {})
    if "river_discharge" in vs and vs["river_discharge"]["kind"] == "river": return "river_discharge"
    return dext.pick_wflow_variable(meta) if any(v["kind"] == "river" for v in vs.values()) else None

def _basin_mean(da: xr.DataArray, ds, p: dict) -> pd.Series:
    """Trung bình không gian trên lưu vực (mask subcatchment của static grid nếu khớp lưới)."""
    xn, yn = _xy_names(ds)
    mask = None
    if os.path.exists(p["static"]):
        with xr.open_dataset(p["static"]) as st:
            b = _pick(st, constants.BASIN_NAMES)
            if b is not None:
                sx, sy = _xy_names(st)
                m = (st[b].fillna(0) > 0)
                if (sx, sy) != (xn, yn): m = m.rename({sx: xn, sy: yn})
                try:
                    tol = abs(float(ds[xn][1] - ds[xn][0])) / 2
                    mask = m.reindex({xn: ds[xn], yn: ds[yn]}, method="nearest", tolerance=tol).fillna(False).astype(bool).load()
                    if not bool(mask.any()): mask = None
                except Exception: mask = None
    v = da.where(mask) if mask is not None else da
    s = v.mean(dim=[d for d in da.dims if d != "time"], skipna=True).to_series()
    s.index = pd.to_datetime(s.index)
    return s

def _precip_series(model: str, meta: dict, p: dict) -> dict:
    """Mưa trung bình lưu vực (mm/h) tại các bước thời gian của output Wflow.
    Thứ tự tìm: biến mưa trong output.nc -> file forcing (input.path_forcing trong wflow_sbm.toml)."""
    times = pd.to_datetime(meta["times"]); step_h = float(meta.get("step_hours") or 24)
    inp = (_read_toml(model).get("input", {}) or {})
    sources = [("output", [p["output"]], None)]
    if inp.get("path_forcing"):
        pat = os.path.join(model, str(inp["path_forcing"])).replace("{year}", "*").replace("{YEAR}", "*")
        files = sorted(glob.glob(pat))
        fz = inp.get("forcing")
        mapped = next((v for k, v in fz.items() if "precip" in k.lower() and isinstance(v, str)), None) if isinstance(fz, dict) else None
        if files: sources.append(("forcing", files, mapped))
    for kind, files, name in sources:
        try:
            ds = xr.open_mfdataset(files, combine="by_coords") if len(files) > 1 else xr.open_dataset(files[0])
        except Exception as e:
            print(f"wflow3d precip: cannot open {files[0]}: {e}"); continue
        try:
            vn = name if name and name in ds else _pick(ds, constants.PRECIP_NAMES)
            if vn is None or "time" not in ds[vn].dims: continue
            s = _basin_mean(ds[vn], ds, p)
            tf = s.index
            fstep_h = float((tf[1] - tf[0]).total_seconds() / 3600) if tf.size > 1 else step_h
            mmh = (s / max(fstep_h, 1e-9)).astype(float)        # Wflow: mm/Δt -> mm/h
            if fstep_h < step_h:                                  # forcing mịn hơn output -> trung bình theo bước output
                mmh = mmh.resample(pd.Timedelta(hours=step_h), label="right", closed="right").mean()
            agg = mmh.reindex(times, method="nearest", tolerance=pd.Timedelta(hours=max(step_h, fstep_h)))
            vals = [None if not np.isfinite(x) else round(float(x), 4) for x in agg.values]
            if not any(x is not None and x > 0 for x in vals): continue
            return {"source": f"{kind}:{os.path.basename(files[0])}:{vn}", "mm_h": vals,
                    "max_mm_h": float(max(x or 0 for x in vals))}
        except Exception as e:                                   # mưa chỉ là phần phụ
            print(f"wflow3d precip: {files[0]}: {e}")
        finally:
            ds.close()
    return {"source": None, "mm_h": None, "max_mm_h": 0.0}

def scene(project: str, flow: str) -> dict:
    model, meta, fc, p = _ctx(project, flow)
    cache = p["cache"]; path = os.path.join(cache, constants.SCENE_FILE)
    with _lock(f"scene:{model}"):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f: out = json.load(f)
            if out.get("version") == meta.get("version") and out.get("format") == constants.SCENE_VERSION: return out
        out = {"format": constants.SCENE_VERSION, "version": meta.get("version"), "flow": flow,
               "times": meta["times"], "step_hours": meta.get("step_hours"),
               "outlets": [], "discharge_var": None, "precip": None, "area_km2": None, "notes": []}
        g = meta["grid"]
        mask = np.load(os.path.join(cache, "mask.npy"))
        out["area_km2"] = round(int((mask > 0).sum()) * g["res_m"] ** 2 / 1e6, 2)
        qv = _discharge_var(meta); out["discharge_var"] = qv
        if qv and meta.get("river_cells"):
            with open(os.path.join(cache, "rivers.geojson"), encoding="utf-8") as f:
                feats = json.load(f)["features"]
            rc = None
            try: rc = _river_cells(model, meta, fc, p)
            except Exception as e: out["notes"].append(f"River/sub-basin lookup failed: {e}")
            stats = os.path.join(cache, f"stats_{qv}.npz")
            meanq = None
            if os.path.exists(stats):
                with np.load(stats) as z: meanq = np.nan_to_num(z["mean"].astype(float), nan=-1)
            score = rc["uparea"] if rc is not None and rc["uparea"] is not None else meanq
            if score is None: score = np.zeros(len(feats))
            groups = rc["sub"] if rc is not None else np.ones(len(feats), int)
            picks = []
            for sid in np.unique(groups):
                ks = np.flatnonzero(groups == sid)
                if ks.size: picks.append((int(sid), int(ks[np.argmax(score[ks])]), float(np.max(score[ks]))))
            picks.sort(key=lambda t: -t[2]); picks = picks[:constants.MAX_OUTLETS]
            if len(picks) < len(np.unique(groups)):
                out["notes"].append(f"Only the {constants.MAX_OUTLETS} largest sub-basin outlets are listed.")
            rows = fc.NpyRows(os.path.join(cache, f"river_{qv}.npy"))
            for n, (sid, k, sc) in enumerate(picks):
                lon, lat = feats[k]["geometry"]["coordinates"][0]
                s = rows.column(k)
                out["outlets"].append({
                    "id": n + 1, "subcatchment": sid, "river_id": k, "lon": lon, "lat": lat,
                    "upstream_area_km2": round(sc, 3) if rc is not None and rc["uparea"] is not None else None,
                    "series": [None if not np.isfinite(x) else round(float(x), 5) for x in s],
                    "peak": float(np.nanmax(s)) if np.isfinite(s).any() else None,
                    "t_peak": meta["times"][int(np.nanargmax(s))] if np.isfinite(s).any() else None,
                })
            if rc is None: out["notes"].append("Sub-basin map not matched: outlets chosen by mean discharge.")
        else:
            out["notes"].append("No river variable in the Wflow output: no hydrographs.")
        try: out["precip"] = _precip_series(model, meta, p)
        except Exception as e: out["notes"].append(f"Precipitation not available: {e}")
        with open(path, "w", encoding="utf-8") as f: json.dump(out, f)
        return out

def river_series(project: str, flow: str, river_id: int, var: Optional[str] = None) -> dict:
    _, meta, fc, p = _ctx(project, flow)
    var = var or _discharge_var(meta)
    if not var or meta["variables"].get(var, {}).get("kind") != "river":
        raise ValueError(f"Unknown river variable: {var}")
    if not 0 <= river_id < int(meta.get("river_cells") or 0):
        raise ValueError(f"River cell {river_id} out of range")
    with open(os.path.join(p["cache"], "rivers.geojson"), encoding="utf-8") as f:
        lon, lat = json.load(f)["features"][river_id]["geometry"]["coordinates"][0]
    s = fc.NpyRows(os.path.join(p["cache"], f"river_{var}.npy")).column(river_id)
    return {"river_id": river_id, "var": var, "units": meta["variables"][var].get("units"), "lon": lon, "lat": lat,
            "series": [None if not np.isfinite(x) else round(float(x), 5) for x in s]}
