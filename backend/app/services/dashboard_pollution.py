"""
dashboard_pollution.py
======================
Dashboard map layer "Pollutant spread (WAQ)": pollution LEVEL (WAQ concentration on the computational mesh)
and spreading DIRECTION / SPEED (particles drifting with the HYD velocity, only where there is pollution).

    pollution_status(project) -> {"available", "reason"}                  (for the checkbox: enabled / greyed out)
    pollution_init(project)   -> substances, layers, WAQ times, regular grid for particles
    pollution_frame(project, name, layer, time) -> concentration + velocity per cell (base64 float32)

Conventions
- Values in the CELL ORDER OF THE DISPLAY MESH (mesh of /dashboard/mesh, built from the HYD map): WAQ concentrations are
  re-ordered with ProjectResults.waq_face_order() because the DELWAQ map file may use a different cell order.
- layer: "max" (maximum over depth – default, shows the plume at ANY depth), "average" (VOLUME-weighted
  cell average if the WAQ file has 'volume', otherwise the mean of the wet layers) or a WAQ layer index "0".."n-1"
  (0 = surface layer, as in DELWAQ). Inactive cells (z-layers: layers below the bed, fill value -999) are excluded.
  Note: depth averaging dilutes a surface plume in DEEP areas (mid-lake) but not in SHALLOW areas (near shore)
  -> the plume may "disappear" mid-lake and "reappear" on the opposite shore. Use "max" or the surface layer to follow the plume.
- Time: nearest WAQ step (<=) the requested time; velocity at the HYD step nearest to that WAQ step.
- Colour scale and particle speed are FIXED over the whole period (cmax = P99.5 of positive values,
  speed_max = P99.5 of speed); users cannot change particle density/speed, to avoid misinterpretation.
"""
from __future__ import annotations
import base64, threading
import numpy as np
from services import constants
from services.dashboard_results import (ProjectResults, time_dim, face_dim, layer_dim, var_meta, _iso, _mtime)

_GUARD = threading.Lock()
_GRID_CACHE: dict[str, dict] = {}
_RANGE_CACHE: dict[str, float] = {}


def _b64(a) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode()

def _velocity_vars(ds, layer: str):
    """(u, v) in the HYD map: prefer the depth-averaged variables for 'average'."""
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
    """Select layer: 'average' -> mean; number -> WAQ layer k (0 = surface), converted to HYD if needed."""
    ld = layer_dim(da, exclude=[d for d in (time_dim(da), face_dim(da)) if d])
    if not ld or ld not in da.dims: return da                              # 2D variable
    n = da.sizes[ld]
    if layer == "average": return da.mean(dim=ld, skipna=True)
    k = min(max(int(layer), 0), max(n_waq - 1, 0))
    if source == "waq": return da.isel({ld: min(k, n - 1)})
    frac = (k + 0.5) / max(n_waq, 1)                                         # relative depth from the surface
    return da.isel({ld: n - 1 - min(n - 1, int(frac * n))})               # HYD: 0 = bottom

def _valid(da):
    """Remove fill values (-999…) / NaN of inactive cells."""
    return da.where(np.isfinite(da) & (da > constants.FILL_LIMIT))

def _volume_like(ds, da, tidx):
    """WAQ cell volume at the same time & layer ('volume' / 'mesh2d_volume' variable if the file has one), or None."""
    for vn in ("mesh2d_volume", "volume", "mesh2d_Volume"):
        if vn in ds.data_vars and ds[vn].dims == da.dims:
            v = ds[vn].isel({time_dim(ds[vn]): tidx})
            return v.where(np.isfinite(v) & (v > 0))
    return None

def _waq_conc(pr: ProjectResults, name: str, layer: str, tidx, n_waq: int):
    ds = pr.ds("waq_map")
    da = ds[name]
    ld = layer_dim(da, exclude=[d for d in (time_dim(da), face_dim(da)) if d])
    layered = bool(ld) and ld in da.dims
    if layer == "max":
        c = _valid(da.isel({time_dim(da): tidx}))
        return c.max(dim=ld, skipna=True) if layered else c
    if layer == "average":
        c = _valid(da.isel({time_dim(da): tidx}))
        if not layered: return c
        vol = _volume_like(ds, da, tidx)
        if vol is not None:                                    # VOLUME-weighted mean: Σ c·V / Σ V
            w = vol.where(c.notnull())
            return (c * w).sum(dim=ld, skipna=True) / w.sum(dim=ld, skipna=True)
        alt = name.replace("mesh2d_", "mesh2d_2d_", 1)          # depth average already written by DELWAQ
        if alt in ds.data_vars: return _valid(ds[alt].isel({time_dim(ds[alt]): tidx}))
        return c.mean(dim=ld, skipna=True)                     # wet layers only
    return _valid(_pick_layer(da.isel({time_dim(da): tidx}), "waq", layer, n_waq))

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
    """Regular lon/lat grid covering the domain + the computational cell containing each cell centre (for particles)."""
    mesh = pr.mesh()
    path = pr.paths.get(mesh["source"]) if mesh else None
    key = f"{path}|{_mtime(path)}"
    with _GUARD:
        if key in _GRID_CACHE: return _GRID_CACHE[key]
    import shapely
    (s, w), (n, e) = mesh["bounds"]
    lat_mid = np.radians((s + n) / 2)
    span = max((e - w) * np.cos(lat_mid), n - s)
    d = span / constants.GRID_CELLS                                                    # cells ~ square in metres
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
    pr.waq_face_order()                         # fail early if the WAQ mesh does not match the HYD mesh
    n_waq = _waq_layers(pr)
    layers = [("max", "Max over depth"), ("average", "Depth average")] if n_waq > 1 else [("average", "Depth average")]
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
    return np.unique(np.linspace(0, n - 1, min(constants.SAMPLE_STEPS, n)).astype(int))

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
    if layer == "max": layer = "average"                     # 'max over depth' -> particles follow the depth-averaged flow
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
    layer = layer if layer in ("max", "average") else "average" if layer in (None, "", "-1") else str(int(layer))
    n_waq = _waq_layers(pr)
    tw = pr.time_index("waq", when)
    t_waq = pr.times("waq")[tw]
    conc = np.asarray(_waq_conc(pr, name, layer, tw, n_waq).values, float).ravel()
    order = pr.waq_face_order()
    if order is not None: conc = conc[order]                                  # -> cell order of the display mesh
    th = pr.time_index("hyd", t_waq)
    u, v = _hyd_velocity(pr, layer, th, n_waq)
    out = {"name": name, **var_meta("waq", name), "layer": layer, "time": _iso(t_waq), "index": int(tw),
        "conc": _b64(conc), "cmax": _conc_max(pr, name, layer, n_waq), "n_faces": int(conc.size)
    }
    if u is not None:
        out.update({"hyd_time": _iso(pr.times("hyd")[th]), "u": _b64(u.ravel()), 
            "v": _b64(v.ravel()), "speed_max": _speed_max(pr, layer, n_waq)
        })
    return out