"""
dashboard_extras.py
===================
Hai lớp bản đồ bổ sung cho Dashboard, dùng lại dữ liệu & hàm sẵn có của nền tảng:

CACHE RIÊNG CỦA DASHBOARD: dashboard KHÔNG đọc/ghi cache của công cụ Visualization hay Flow Estimation,
không dùng khoá hay bộ nhớ đệm của movement_router / flow_router. Đổi độ phân giải, lớp hay dựng lại
ở dashboard không ảnh hưởng tới các công cụ đó và ngược lại (đổi lại: tốn thêm dung lượng đĩa).

1) MOVEMENT (dòng chảy dạng hạt, từ kết quả HYD)
   Cache: <project>/output/movement/{meta.json, frames.npy, mask.npy, domain.geojson}
   (công cụ Visualization dùng <project>/output/movement/). Dựng bằng movement_functions.init_data
   từ file HYD map của dự án; frontend đọc qua GET /dashboard/movement/{mask,frame/{i}}.

2) WFLOW (kết quả sau khi chạy mô hình Wflow)
   Kết quả: <project>/flows/<flow>/wflow_model/run_default/output.nc (chỉ đọc, dùng chung)
   Cache: <project>/flows/<flow>/wflow_model/viewer/ (công cụ Flow Estimation dùng viewer/).
   Dựng bằng flow_connection.build_cache(..., view_dir=DASH_VIEW_DIR).

"""
from __future__ import annotations

import importlib, json, os, threading, traceback
from typing import Any, Optional

import numpy as np

from config import PROJECT_ROOT
from services.dashboard_results import ProjectResults, _crs_of

VELOCITY_PAIRS = [("mesh2d_ucxa", "mesh2d_ucya"), ("ucxa", "ucya"), ("mesh2d_ucx", "mesh2d_ucy"), ("ucx", "ucy")]
_LOCKS: dict[str, threading.Lock] = {}
_WFLOW_META: dict[str, dict] = {}        # cache meta.json Wflow theo thư mục mô hình
DASH_MOVE_DIR, DASH_VIEW_DIR = "movement",  "viewer"


def _lock(key: str) -> threading.Lock:
    return _LOCKS.setdefault(key, threading.Lock())


def _lazy(name: str):
    """Import services.<name> khi cần. Trả (module, None) hoặc (None, lý do)."""
    try:
        return importlib.import_module(f"services.{name}"), None
    except Exception as e:  # noqa: BLE001
        return None, f"{name} cannot be loaded ({type(e).__name__}: {e})"


# ======================================================================
# Movement
# ======================================================================
def _movement_files(project: str) -> dict[str, str]:
    d = os.path.join(PROJECT_ROOT, project, "output", DASH_MOVE_DIR)
    return {"dir": d, "meta": os.path.join(d, "meta.json"), "frames": os.path.join(d, "frames.npy"),
            "mask": os.path.join(d, "mask.npy"), "polygon": os.path.join(d, "domain.geojson")}


# Đọc trực tiếp frames.npy / mask.npy của cache movement (không phụ thuộc bộ nhớ đệm trong
# movement_router). Mỗi lần đọc mở rồi đóng file -> không giữ khoá file trên Windows khi dựng lại.
_NPY_HDR: dict[str, tuple] = {}


def _npy_header(path: str) -> tuple:
    mt = os.path.getmtime(path)
    h = _NPY_HDR.get(path)
    if h and h[0] == mt:
        return h
    with open(path, "rb") as f:
        major, _ = np.lib.format.read_magic(f)
        reader = np.lib.format.read_array_header_1_0 if major == 1 else np.lib.format.read_array_header_2_0
        shape, fortran, dtype = reader(f)
        h = (mt, f.tell(), tuple(shape), np.dtype(dtype), bool(fortran))
    _NPY_HDR[path] = h
    return h


def movement_frame_bytes(project: str, i: int) -> bytes:
    """Khung i của frames.npy (T, 3, Nv) -> int16 little-endian: u | v | depth."""
    path = _movement_files(project)["frames"]
    if not os.path.exists(path):
        raise FileNotFoundError("Movement data not built yet")
    _, off, shape, dtype, fortran = _npy_header(path)
    if not 0 <= i < shape[0]:
        raise IndexError(f"Frame {i} outside 0..{shape[0] - 1}")
    if fortran:                                            # hiếm gặp – đọc chậm nhưng đúng
        arr = np.load(path, mmap_mode="r")[i]
        out = np.ascontiguousarray(arr).astype("<i2").tobytes()
        del arr
        return out
    row = int(np.prod(shape[1:])) * dtype.itemsize
    with open(path, "rb") as f:
        f.seek(off + i * row)
        data = f.read(row)
    if dtype != np.dtype("<i2"):
        data = np.frombuffer(data, dtype=dtype).astype("<i2").tobytes()
    return data


def movement_mask_bytes(project: str) -> bytes:
    path = _movement_files(project)["mask"]
    if not os.path.exists(path):
        raise FileNotFoundError("Movement data not built yet")
    return np.ascontiguousarray(np.load(path)).astype(np.uint8).tobytes()


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _hyd_movement_source(project: str) -> dict[str, Any]:
    """Có dựng được movement từ HYD map không (vận tốc + WGS84)? Kèm số lớp để chọn 'layer'."""
    pr = ProjectResults(project)
    ds = pr.ds("hyd_map")
    if ds is None:
        return {"ok": False, "reason": "No HYD map file (output/HYD/*_map.zarr) yet."}
    pair = next((p for p in VELOCITY_PAIRS if p[0] in ds.data_vars and p[1] in ds.data_vars), None)
    if pair is None:
        return {"ok": False, "reason": "HYD map has no velocity components (mesh2d_ucx/ucy). Enable them in the HYD output settings."}
    crs = _crs_of(ds)
    if crs and "4326" not in str(crs):
        return {"ok": False, "reason": f"Movement needs a WGS84 model grid; this map uses {crs}."}
    n_layers = next((int(n) for d, n in ds.sizes.items() if "nLayers" in d or d == "laydim"), 1)
    return {"ok": True, "velocity": list(pair), "n_layers": n_layers, "map_path": pr.paths["hyd_map"]}


def movement_status(project: str) -> dict[str, Any]:
    """available = đã có cache riêng của dashboard hoặc dựng được từ HYD map."""
    f = _movement_files(project)
    mf, err = _lazy("movement_functions")
    src = _hyd_movement_source(project) if mf is not None else {"ok": False, "reason": err}
    meta = _read_json(f["meta"]) if all(os.path.exists(f[k]) for k in ("meta", "frames", "mask")) else None
    cfg = {"n_layers": src.get("n_layers", 1), "can_rebuild": bool(src.get("ok"))}
    if meta is not None and mf is not None:
        return {"available": True, "ready": True, "source": "cache", "reason": None, **cfg,
                "res_m": meta.get("res_m"), "res_auto": meta.get("res_auto"),
                "layer": _file_to_ui_layer(meta.get("layer", "mean"), int(src.get("n_layers") or 1))}
    if mf is None or not src.get("ok"):
        return {"available": False, "ready": False, "reason": src.get("reason") or err, **cfg}
    return {"available": True, "ready": False, "source": "hyd_map", "velocity": src["velocity"], **cfg,
            "reason": "Movement data will be prepared from the HYD map on first use."}


def movement_lock(project: str):
    """Khoá asyncio RIÊNG của dashboard (không dùng khoá của movement_router)."""
    import asyncio
    return _ASYNC_LOCKS.setdefault(project, asyncio.Lock())


_ASYNC_LOCKS: dict[str, Any] = {}


def hyd_layer_labels(project: str) -> list:
    """[(khoá, nhãn)] các lớp HYD từ output/config/layers_hyd.json (do project_router tạo bằng layerCounter):
    {"-1": "Average", "0": "Depth: -0.5 m (surface)", ...} – khoá k = thứ tự từ mặt xuống."""
    cfg = os.path.join(PROJECT_ROOT, project, "output", "config")
    for name in ("layers_hyd.json", "layer_hyd.json", "layer_hy.json", "layers_hy.json"):
        data = _read_json(os.path.join(cfg, name))
        if isinstance(data, dict) and data:
            out = []
            for k, v in data.items():
                try:
                    if int(k) >= 0:
                        out.append((str(int(k)), str(v)))
                except (TypeError, ValueError):
                    continue
            return sorted(out, key=lambda kv: int(kv[0]))
    return []


def _ui_to_file_layer(layer, n_layers: int) -> str:
    """Lớp theo giao diện dashboard -> giá trị cho movement_functions.init_data.
    Giao diện (giống layers_hyd.json): "mean"/"-1"/"Average", "surface", k = thứ tự TỪ MẶT XUỐNG.
    init_data/reduce_layers: "mean", "surface", hoặc chỉ số lớp trong file D-Flow FM (từ ĐÁY lên).
    Chuyển đổi ở đây để không phải sửa movement_functions (công cụ Visualization giữ nguyên)."""
    s = str(layer if layer is not None else "mean").strip().lower()
    if s in ("", "-1", "mean", "average", "avg"): return "mean"
    if s in ("surface", "top"): return "surface"
    k = int(float(s))
    return str(min(max(n_layers - 1 - k, 0), max(n_layers - 1, 0)))


def _file_to_ui_layer(layer, n_layers: int) -> str:
    s = str(layer if layer is not None else "mean")
    if s in ("mean", "surface"): return s
    try:
        return str(min(max(n_layers - 1 - int(s), 0), max(n_layers - 1, 0)))
    except ValueError:
        return "mean"


def movement_init(project: str, res=None, layer: Optional[str] = None, rebuild: bool = False) -> dict[str, Any]:
    """Nạp cache movement; chỉ dựng lại khi chưa có cache, khi rebuild=True, hoặc khi người dùng
    chọn độ phân giải/lớp KHÁC cấu hình hiện tại. Chạy trong thread, bên trong movement_lock()."""
    mf, err = _lazy("movement_functions")
    if mf is None:
        return {"error": err}
    f = _movement_files(project)
    os.makedirs(f["dir"], exist_ok=True)
    current = _read_json(f["meta"]) if all(os.path.exists(f[k]) for k in ("meta", "frames", "mask")) else None
    explicit = res not in (None, "") or layer not in (None, "")
    src = _hyd_movement_source(project)
    n_layers = int(src.get("n_layers") or 1)
    if layer in (None, ""):
        layer = (current or {}).get("layer", "mean")          # đã ở dạng của init_data
    else:
        layer = _ui_to_file_layer(layer, n_layers)
    resolution = _parse_res(res)                              # None = auto
    if current is None:
        reason = "No dashboard movement data yet"
    elif not explicit and not rebuild:
        reason = None                                         # dùng cache riêng đang có
    elif rebuild:
        reason = "Rebuild requested"
    else:
        reason = mf.needs_rebuild(f["meta"], f["frames"], f["mask"], resolution, layer)
    if reason:
        if not src.get("ok"):
            return {"error": src.get("reason")}
        out = mf.init_data(f["meta"], f["polygon"], src["map_path"], f["frames"], f["mask"], resolution, layer)
        if out is not None:
            return {"error": out}
    m = _read_json(f["meta"]) or {}
    if not m:
        return {"error": "Movement meta.json missing or broken – rebuild movement data."}
    T = _npy_header(f["frames"])[2][0]
    keys = ("nx", "ny", "lon0", "lat0", "dlon", "dlat", "bounds", "times", "scale", "nodata",
            "speed_max", "depth_max", "n_valid", "res_m", "res_auto", "layer", "version")
    meta = {k: m.get(k) for k in keys}
    meta["layer"] = _file_to_ui_layer(meta.get("layer"), n_layers)   # về quy ước giao diện (từ mặt xuống)
    return {"meta": meta, "frame_count": T, "rebuilt": bool(reason), "reason": reason,
            "n_layers": n_layers, "can_rebuild": bool(src.get("ok")),
            "layers": hyd_layer_labels(project)}


# ======================================================================
# Wflow
# ======================================================================
def wflow_status(project: str) -> dict[str, Any]:
    """Liệt kê kịch bản Wflow đã chạy (có run_default/output.nc) và cache viewer đã sẵn sàng chưa."""
    flows_dir = os.path.join(PROJECT_ROOT, project, "flows")
    if not os.path.isdir(flows_dir):
        return {"available": False, "ready": False, "scenarios": [], "reason": "No Wflow scenario in this project."}
    fc, err = _lazy("flow_connection")
    if fc is None:
        return {"available": False, "ready": False, "scenarios": [], "reason": err}
    scenarios = []
    for name in sorted(p.name for p in os.scandir(flows_dir) if p.is_dir()):
        model = fc.wflow_dir(project, name)
        if not os.path.isdir(model):
            continue
        try:
            paths = fc.model_paths(model, DASH_VIEW_DIR)
        except Exception:  # noqa: BLE001  (toml hỏng, ...)
            continue
        has_output = os.path.exists(paths["output"])
        ready = False
        if has_output:
            try:
                ready = fc.needs_rebuild(model, None, DASH_VIEW_DIR) is None
            except Exception:  # noqa: BLE001
                ready = False
        scenarios.append({"name": name, "has_output": has_output, "ready": ready,
                          "mtime": os.path.getmtime(paths["output"]) if has_output else 0})
    runs = [s for s in scenarios if s["has_output"]]
    if not runs:
        return {"available": False, "ready": False, "scenarios": scenarios,
                "reason": "No Wflow run output (run_default/output.nc) yet. Run the flow model first."}
    best = sorted(runs, key=lambda s: (not s["ready"], -s["mtime"]))[0]
    return {"available": True, "ready": best["ready"], "scenarios": scenarios, "default": best["name"],
            "reason": None if best["ready"] else "Viewer data will be prepared on first use."}


def pick_wflow_variable(meta: dict) -> Optional[str]:
    """Biến mặc định: lưu lượng sông (river, nguồn có 'q') -> biến river khác -> biến lưới đầu tiên."""
    vars_ = meta.get("variables", {})
    rivers = [k for k, v in vars_.items() if v.get("kind") == "river"]
    q = [k for k in rivers if "q" in (vars_[k].get("source") or k).lower()]
    return (q or rivers or list(vars_) or [None])[0]


def _wflow_lock(model: str) -> threading.Lock:
    """Khoá RIÊNG của dashboard (cache viewer không dùng chung với Flow Estimation)."""
    return _lock(f"wflow:{model}")


def _parse_res(res) -> Optional[float]:
    """Độ phân giải từ giao diện -> None (auto) hoặc float (m).
    Không truyền chuỗi "auto" xuống build_cache: một số phiên bản flow_connection định dạng
    f"{res:g}" và sẽ lỗi với chuỗi."""
    if res is None: return None
    if isinstance(res, str):
        s = res.strip().lower()
        if s in ("", "auto", "none", "null"): return None
        res = s
    try:
        v = float(res)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _build_wflow_cache(fc, model: str, res: Optional[float]) -> None:
    """Dựng cache RIÊNG của dashboard (wflow_model/viewer). res = None -> tự chọn."""
    fc.build_cache(model, res, view_dir=DASH_VIEW_DIR)


def wflow_init(project: str, flow: str, res=None, rebuild: bool = False) -> dict[str, Any]:
    """Nạp meta viewer Wflow; dựng lại (flow_connection.build_cache) khi chưa có cache, khi output mới hơn,
    khi rebuild=True hoặc khi đổi độ phân giải. Chạy trong thread."""
    fc, err = _lazy("flow_connection")
    if fc is None:
        return {"error": err}
    model = fc.wflow_dir(project, flow)
    lock = _wflow_lock(model)
    if not lock.acquire(timeout=1800):
        return {"error": "Viewer data is being prepared by another tool, try again later."}
    try:
        try:
            res = _parse_res(res)                         # None = auto, ngược lại float (m)
            reason = "Rebuild requested" if rebuild else fc.needs_rebuild(model, res, DASH_VIEW_DIR)
        except FileNotFoundError as e:
            return {"error": str(e)}
        if reason:
            _WFLOW_META.pop(model, None)
            _build_wflow_cache(fc, model, res)
        meta = _WFLOW_META[model] = fc.load_meta(model, DASH_VIEW_DIR)
    finally:
        lock.release()
    cache = fc.model_paths(model, DASH_VIEW_DIR)["cache"]
    variables = {}
    for k, v in meta.get("variables", {}).items():
        variables[k] = {kk: v.get(kk) for kk in ("kind", "units", "description", "vmin", "p99", "max", "log",
                                                 "dtype", "n", "group", "flux")}
        variables[k]["stats"] = os.path.exists(os.path.join(cache, f"stats_{k}.npz"))
    rivers = [k for k, v in variables.items() if v["kind"] == "river"]
    grids = [k for k, v in variables.items() if v["kind"] == "grid"]
    status = wflow_status(project)
    return {"flow": flow, "variable": pick_wflow_variable(meta), "river_default": pick_wflow_variable(meta) if rivers else None,
            "grid_default": grids[0] if grids else None, "variables": variables,
            "grid": meta.get("grid"), "times": meta.get("times"), "step_hours": meta.get("step_hours"),
            "river_cells": meta.get("river_cells", 0), "synthetic": bool(meta.get("synthetic")),
            "scenarios": [s_["name"] for s_ in status.get("scenarios", []) if s_["has_output"]],
            "velocity": ({k: vel.get(k) for k in ("n", "dtype", "speed_max", "speed_p50", "direction")}
                         if (vel := meta.get("velocity")) else None),
            "version": meta.get("version"), "rebuilt": bool(reason), "reason": reason}


def wflow_stat(project: str, flow: str, var: str, stat: str, threshold=None):
    """Thống kê theo ô cho cả kỳ chạy: max | mean | sum | tmax (giờ đến đỉnh) | above (số giờ vượt ngưỡng)."""
    import numpy as np
    model, meta, fc = wflow_context(project, flow)
    arr = np.asarray(fc.stat_array(model, meta, var, stat, threshold, view_dir=DASH_VIEW_DIR), dtype="<f4")
    fin = arr[np.isfinite(arr)]
    lo = float(fin.min()) if fin.size else 0.0
    hi = float(np.percentile(fin, 99)) if fin.size else 1.0
    return np.ascontiguousarray(arr).tobytes(), lo, hi


def wflow_context(project: str, flow: str):
    """(thư mục mô hình, meta, flow_connection) cho các endpoint đọc nhị phân."""
    fc, err = _lazy("flow_connection")
    if fc is None:
        raise RuntimeError(err)
    model = fc.wflow_dir(project, flow)
    meta = _WFLOW_META.get(model)
    if meta is None:
        meta = _WFLOW_META[model] = fc.load_meta(model, DASH_VIEW_DIR)
    return model, meta, fc


def wflow_geometry(project: str, flow: str, kind: str) -> dict:
    """Đường viền lưu vực (kind='basins') hoặc vùng nước (kind='water') – như /wflow_basins, /wflow_water
    của Flow Estimation, để hạt được cắt theo lưu vực và xoá trên mặt nước giống hệt công cụ đó."""
    import geopandas as gpd
    from shapely.geometry import box
    model, meta, fc = wflow_context(project, flow)
    empty = {"type": "FeatureCollection", "features": []}
    if kind == "basins":
        path = fc.model_paths(model, DASH_VIEW_DIR)["basins"]
        if not os.path.exists(path):
            return empty
        gdf = gpd.read_file(path)
        if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(4326)
        return json.loads(gdf.to_json())
    path = fc.water_file(model)
    if not path:
        return empty
    gdf = gpd.read_file(path)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    gdf = gdf.set_crs(4326) if gdf.crs is None else gdf.to_crs(4326)
    (south, west), (north, east) = meta["grid"]["bounds"]
    gdf = gpd.clip(gdf[["geometry"]], box(west, south, east, north))
    return json.loads(gdf.to_json())


def extras_status(project: str) -> dict[str, Any]:
    out = {}
    for key, fn in (("movement", movement_status), ("wflow", wflow_status)):
        try:
            out[key] = fn(project)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            out[key] = {"available": False, "ready": False, "reason": f"Error: {e}"}
    return out