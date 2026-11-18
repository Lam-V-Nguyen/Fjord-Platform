"""
dashboard_pollution.py
======================
Lớp bản đồ "Pollutant spread (WAQ)" của Dashboard: MỨC ĐỘ ô nhiễm (nồng độ WAQ trên lưới tính toán)
và HƯỚNG / TỐC ĐỘ lan truyền (hạt trôi theo vận tốc HYD, chỉ trong vùng có ô nhiễm).

    pollution_status(project) -> {"available", "reason"}                  (cho checkbox: sáng / mờ)
    pollution_init(project)   -> chất, lớp, thời gian WAQ, lưới đều cho hạt
    pollution_frame(project, name, layer, time) -> nồng độ + vận tốc theo ô (base64 float32)

Quy ước
- Giá trị theo THỨ TỰ Ô CỦA LƯỚI HIỂN THỊ (mesh của /dashboard/mesh, dựng từ HYD map): nồng độ WAQ được
  sắp xếp lại bằng ProjectResults.waq_face_order() vì file map DELWAQ có thể dùng thứ tự ô khác.
- layer: "average" (trung bình theo độ sâu) hoặc chỉ số lớp WAQ "0".."n-1" (0 = lớp mặt, như DELWAQ).
  Vận tốc lấy ở lớp HYD tương ứng (HYD đánh số đáy -> mặt), cùng độ sâu tương đối nếu số lớp khác nhau.
- Thời gian: bước WAQ gần nhất (<=) thời điểm được hỏi; vận tốc ở bước HYD gần nhất với bước WAQ đó.
- Thang màu và tốc độ hạt CỐ ĐỊNH theo toàn bộ thời gian (cmax = P99.5 các giá trị dương,
  speed_max = P99.5 tốc độ), không cho người dùng chỉnh mật độ/tốc độ hạt để tránh hiểu nhầm.
"""
from __future__ import annotations
import base64, threading
import numpy as np, pandas as pd
from services.dashboard_results import (ProjectResults, time_dim, face_dim, layer_dim, var_meta, _iso, _mtime)

_GUARD = threading.Lock()
_GRID_CACHE: dict[str, dict] = {}
_RANGE_CACHE: dict[str, float] = {}
GRID_CELLS = 320          # số ô lưới hạt theo cạnh dài của miền
SAMPLE_STEPS = 24         # số bước thời gian lấy mẫu để tính thang cố định

def _b64(a) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode()

def _velocity_vars(ds, layer: str):
    """(u, v) trong HYD map: ưu tiên biến trung bình độ sâu cho 'average'."""
    if layer == "average":
        for u, v in (("mesh2d_ucxa", "mesh2d_ucya"), ("ucxa", "ucya")):
            if u in ds.data_vars and v in ds.data_vars: return u, v
    for u, v in (("mesh2d_ucx", "mesh2d_ucy"), ("ucx", "ucy"), ("mesh2d_ucxa", "mesh2d_ucya"), ("ucxa", "ucya")):
        if u in ds.data_vars and v in ds.data_vars: return u, v
    return None

def _n_layers(da) -> int:
    ld = layer_dim(da, exclude=[d for d in (time_dim(da), face_dim(da)) if d])
    return int(da.sizes[ld]) if ld and ld in da.dims else 1

def _pick_layer(da, source: str, layer: str, n_waq: int):
    """Chọn lớp: 'average' -> trung bình; số -> lớp WAQ k (0 = mặt), quy đổi sang HYD nếu cần."""
    ld = layer_dim(da, exclude=[d for d in (time_dim(da), face_dim(da)) if d])
    if not ld or ld not in da.dims: return da                              # biến 2D
    n = da.sizes[ld]
    if layer == "average": return da.mean(dim=ld, skipna=True)
    k = min(max(int(layer), 0), max(n_waq - 1, 0))
    if source == "waq": return da.isel({ld: min(k, n - 1)})
    frac = (k + 0.5) / max(n_waq, 1)                                         # độ sâu tương đối từ mặt
    return da.isel({ld: n - 1 - min(n - 1, int(frac * n))})               # HYD: 0 = đáy

def _waq_conc(pr: ProjectResults, name: str, layer: str, tidx, n_waq: int):
    ds = pr.ds("waq_map")
    if layer == "average":
        alt = name.replace("mesh2d_", "mesh2d_2d_", 1)
        if alt in ds.data_vars: return ds[alt].isel({time_dim(ds[alt]): tidx})
    da = ds[name]
    return _pick_layer(da.isel({time_dim(da): tidx}), "waq", layer, n_waq)

def _waq_layers(pr: ProjectResults) -> int:
    ds = pr.ds("waq_map")
    for var in ds.data_vars:
        da = ds[var]
        if time_dim(da) and face_dim(da) and len(da.dims) >= 3: return _n_layers(da)
    return 1

# Status / init
def pollution_status(project: str) -> dict:
    pr = ProjectResults(project)
    if not pr.paths.get("waq_map"):
        return {"available": False, "ready": False, "reason": "No WAQ map output yet – run a water-quality (WAQ) simulation first."}
    if not pr.paths.get("hyd_map"):
        return {"available": False, "ready": False, "reason": "No HYD map output – the pollutant layer needs the HYD mesh and velocities."}
    if not pr.map_variables("waq"):
        return {"available": False, "ready": False, "reason": "The WAQ map file has no substance on the model grid."}
    vel = _velocity_vars(pr.ds("hyd_map"), "layer") is not None
    return {"available": True, "ready": True, "has_velocity": vel,
        "reason": None if vel else "HYD map has no velocities (mesh2d_ucx/ucy): only concentrations are shown."
    }

def _particle_grid(pr: ProjectResults) -> dict:
    """Lưới đều kinh/vĩ độ phủ miền + ô lưới tính toán chứa tâm mỗi ô (cho hạt)."""
    mesh = pr.mesh()
    path = pr.paths.get(mesh["source"]) if mesh else None
    key = f"{path}|{_mtime(path)}"
    with _GUARD:
        if key in _GRID_CACHE: return _GRID_CACHE[key]
    import shapely
    (s, w), (n, e) = mesh["bounds"]
    lat_mid = np.radians((s + n) / 2)
    span = max((e - w) * np.cos(lat_mid), n - s)
    d = span / GRID_CELLS                                                    # ô ~ vuông theo mét
    dlat, dlon = d, d / max(np.cos(lat_mid), 1e-6)
    nx, ny = int(np.ceil((e - w) / dlon)) + 1, int(np.ceil((n - s) / dlat)) + 1
    lon0, lat0 = w + dlon / 2, s + dlat / 2
    ix, iy = np.meshgrid(np.arange(nx), np.arange(ny))                        # k = iy * nx + ix
    pts = shapely.points(lon0 + ix.ravel() * dlon, lat0 + iy.ravel() * dlat)
    faces, polys = [], []
    for i, f in enumerate(mesh["features"]):
        ring = f["geometry"]["coordinates"][0]
        if len(ring) >= 4: faces.append(i); polys.append(shapely.Polygon(ring))
    tree = shapely.STRtree(polys)
    p_idx, t_idx = tree.query(pts, predicate="within")
    face_of = np.full(nx * ny, -1, np.int64)
    face_of[p_idx] = np.asarray(faces, np.int64)[t_idx]
    cells = np.nonzero(face_of >= 0)[0]
    out = {"grid": {"nx": nx, "ny": ny, "lon0": float(lon0), "lat0": float(lat0), 
        "dlon": float(dlon), "dlat": float(dlat)}, "cells": cells.tolist(), "cell_face": face_of[cells].tolist()
    }
    with _GUARD: _GRID_CACHE[key] = out
    return out

def pollution_init(project: str) -> dict:
    st = pollution_status(project)
    if not st["available"]: return {"error": st["reason"]}
    pr = ProjectResults(project)
    pr.waq_face_order()                         # báo lỗi sớm nếu lưới WAQ không khớp lưới HYD
    n_waq = _waq_layers(pr)
    layers = [("average", "Depth average")]
    if n_waq > 1:
        layers += [(str(k), f"Layer {k + 1}" + (" (surface)" if k == 0 else " (bottom)" 
            if k == n_waq - 1 else "")) for k in range(n_waq)
        ]
    substances = [{"name": v["name"], "label": v["label"], "unit": v["unit"], "layered": v["layered"]}
                  for v in pr.map_variables("waq")]
    return {"substances": substances, "layers": layers, "n_layers": n_waq,
        "times": [_iso(t) for t in pr.times("waq")], "has_velocity": st["has_velocity"], **_particle_grid(pr)
    }

# Fixed scales over the whole simulation
def _steps(n: int) -> np.ndarray:
    return np.unique(np.linspace(0, n - 1, min(SAMPLE_STEPS, n)).astype(int))

def _conc_max(pr: ProjectResults, name: str, layer: str, n_waq: int) -> float:
    key = f"c|{pr.paths['waq_map']}|{_mtime(pr.paths['waq_map'])}|{name}|{layer}"
    with _GUARD:
        if key in _RANGE_CACHE: return _RANGE_CACHE[key]
    vals = np.asarray(_waq_conc(pr, name, layer, _steps(len(pr.times("waq"))), n_waq).values, float)
    pos = vals[np.isfinite(vals) & (vals > 0)]
    cmax = float(np.percentile(pos, 99.5)) if pos.size else 1.0
    with _GUARD: _RANGE_CACHE[key] = cmax
    return cmax

def _hyd_velocity(pr: ProjectResults, layer: str, tidx, n_waq: int):
    ds = pr.ds("hyd_map"); pair = _velocity_vars(ds, layer)
    if pair is None: return None, None
    out = []
    for name in pair:
        da = ds[name]
        out.append(np.asarray(_pick_layer(da.isel({time_dim(da): tidx}), "hyd", layer, n_waq).values, float))
    return out[0], out[1]

def _speed_max(pr: ProjectResults, layer: str, n_waq: int) -> float:
    key = f"v|{pr.paths['hyd_map']}|{_mtime(pr.paths['hyd_map'])}|{layer}|{n_waq}"
    with _GUARD:
        if key in _RANGE_CACHE: return _RANGE_CACHE[key]
    u, v = _hyd_velocity(pr, layer, _steps(len(pr.times("hyd"))), n_waq)
    sp = np.hypot(u, v) if u is not None else np.array([])
    sp = sp[np.isfinite(sp)]
    smax = max(float(np.percentile(sp, 99.5)), 1e-3) if sp.size else 0.05
    with _GUARD: _RANGE_CACHE[key] = smax
    return smax

# Frame
def pollution_frame(project: str, name: str, layer: str, when) -> dict:
    pr = ProjectResults(project)
    if pr.ds("waq_map") is None or name not in pr.ds("waq_map").data_vars:
        raise ValueError(f"Substance '{name}' not found in the WAQ map")
    layer = "average" if layer in (None, "", "average", "-1") else str(int(layer))
    n_waq = _waq_layers(pr)
    tw = pr.time_index("waq", when)
    t_waq = pr.times("waq")[tw]
    conc = np.asarray(_waq_conc(pr, name, layer, tw, n_waq).values, float).ravel()
    order = pr.waq_face_order()
    if order is not None: conc = conc[order]                                  # -> thứ tự ô của lưới hiển thị
    th = pr.time_index("hyd", t_waq)
    u, v = _hyd_velocity(pr, layer, th, n_waq)
    out = {"name": name, **var_meta("waq", name), "layer": layer, "time": _iso(t_waq), "index": int(tw),
           "conc": _b64(conc), "cmax": _conc_max(pr, name, layer, n_waq), "n_faces": int(conc.size)}
    if u is not None:
        out.update({"hyd_time": _iso(pr.times("hyd")[th]), "u": _b64(u.ravel()), 
            "v": _b64(v.ravel()), "speed_max": _speed_max(pr, layer, n_waq)
        })
    return out