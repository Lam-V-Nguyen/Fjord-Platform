"""
Reads the HYDRODYNAMIC (HYD, D-Flow FM) and WATER QUALITY (WAQ, D-Water Quality) simulation results
of a project for the Dashboard. This is the MAIN data source of the Overview page.

File conventions (same as functions.project_reader):
    <project>/output/HYD/<name>_his.zarr, <name>_map.zarr
    <project>/output/WAQ/<waq>_his.zarr,  <waq>_map.zarr   (+ <waq>.json, or output/config/config.json)

Automatic dimension detection:
    time      : 'time' (HYD) | 'nTimesDlwq' (WAQ)
    station   : 'stations' | 'nStations'
    face      : '*nFaces*'
    layer     : 'laydim' | '*nLayers*' | remaining dim
Layer order: HYD (sigma) numbered bottom -> surface (surface = -1); WAQ (DELWAQ) surface -> bottom (surface = 0).

All Datasets/meshes are cached by (path, mtime), so reopening is very fast.
"""
from __future__ import annotations
import json, math, os, threading, traceback
from datetime import datetime
import numpy as np, pandas as pd, xarray as xr
from config import PROJECT_ROOT
from services import constants
from pyproj import Transformer
from scipy.spatial import KDTree

_LOCK = threading.Lock()
_MESH_CACHE: dict[str, tuple[float, dict]] = {}
_RANGE_CACHE: dict[str, tuple[float, list]] = {}
_ORDER_CACHE: dict[str, np.ndarray | None] = {}

# Helpers
def _r(v, n=3):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, n)
    except Exception: return None

def _iso(t) -> str | None:
    try: return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception: return None

def _mtime(path: str) -> float:
    try: return os.path.getmtime(path)
    except Exception: return 0.0

def _open(path: str | None) -> xr.Dataset | None:
    """Open Zarr (or NetCDF) with cache and mtime"""
    if not path or not os.path.exists(path): return None
    try: ds = xr.open_zarr(path) if path.endswith(".zarr") else xr.open_dataset(path)
    except Exception: return None
    if isinstance(ds, xr.DataArray): ds = ds.to_dataset(name=ds.name or "data")
    return ds

def _decode(values) -> list[str]:
    out = []
    for v in np.asarray(values).ravel():
        if isinstance(v, (bytes, np.bytes_)): 
            out.append(v.decode("utf-8", "ignore").strip())
        else: out.append(str(v).strip())
    return out

def _name_key(name) -> str:
    """Normalize station name (Remove space, ignore lower or cap)"""
    if isinstance(name, (bytes, np.bytes_)):
        name = name.decode("utf-8", "ignore")
    return str(name).strip().lower()

def _dim(dims, *keys):
    for d in dims:
        for k in keys:
            if k.lower() in d.lower(): return d
    return None

def time_dim(da):
    return _dim(da.dims, "time", "nTimesDlwq")

def station_dim(da):
    return _dim(da.dims, "station")

def face_dim(da):
    return _dim(da.dims, "nFaces")

def layer_dim(da, exclude=()):
    d = _dim(da.dims, "laydim", "nLayers", "layer")
    if d: return d
    rest = [x for x in da.dims if x not in exclude]
    return rest[0] if rest else None

def _crs_of(ds) -> str | None:
    for var in ("projected_coordinate_system", "crs", "wgs84"):
        if var in ds.variables:
            attrs = ds[var].attrs
            code = attrs.get("EPSG_code") or attrs.get("epsg")
            if code:
                code = str(code)
                return code if code.upper().startswith("EPSG") else f"EPSG:{code}"
            if attrs.get("crs_wkt"): return attrs["crs_wkt"]
    return None

def _to_wgs84(x, y, crs):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if crs is None or str(crs).upper() in ("EPSG:4326", "EPSG:4258"):
        return x, y
    try:
        tf = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        return tf.transform(x, y)
    except Exception:
        traceback.print_exc()
        return x, y

def _face_geometry(ds: xr.Dataset):
    """(node_x, node_y, faces) – faces: (nFaces, nMax) 0-based node indices, -1 = empty; None if the file has none."""
    for nxk, nyk, fk in (("mesh2d_node_x", "mesh2d_node_y", "mesh2d_face_nodes"),
                         ("NetNode_x", "NetNode_y", "NetElemNode")):
        if fk in ds.variables and nxk in ds.variables:
            nx, ny = np.asarray(ds[nxk].values, float).ravel(), np.asarray(ds[nyk].values, float).ravel()
            fn = np.asarray(ds[fk].values, float)
            start = int(ds[fk].attrs.get("start_index", 1))
            valid = np.isfinite(fn) & (fn >= start) & (fn < start + len(nx))
            return nx, ny, np.where(valid, fn - start, -1).astype(np.int64)
    return None

def _vertex_mean(geom):
    nx, ny, faces = geom
    valid = faces >= 0
    idx, n = np.where(valid, faces, 0), np.maximum(valid.sum(axis=1), 1)
    return (np.where(valid, nx[idx], 0).sum(axis=1) / n, np.where(valid, ny[idx], 0).sum(axis=1) / n)

def _face_centroids(ds: xr.Dataset):
    """Face centres in the file's own coordinates (no reprojection), or None.
    Prefer the vertex mean (computed THE SAME WAY for every file). D-Flow FM mesh2d_face_x/y are the CIRCUMCIRCLE
    CENTRES (may lie outside the triangle) – not comparable with the vertex mean of another file."""
    geom = _face_geometry(ds)
    if geom is not None: return _vertex_mean(geom)
    for xk, yk in (("mesh2d_face_x", "mesh2d_face_y"), ("FlowElem_xcc", "FlowElem_ycc")):
        if xk in ds.variables and yk in ds.variables:
            return np.asarray(ds[xk].values, float).ravel(), np.asarray(ds[yk].values, float).ravel()
    return None

def _same_frame(display_ds: xr.Dataset, value_ds: xr.Dataset, a, b):
    """Bring two coordinate sets into the same system: different CRS (or one file in degrees, one in metres) -> both to WGS84."""
    ca, cb = _crs_of(display_ds), _crs_of(value_ds)
    geo = lambda xy: np.nanmax(np.abs(xy[0])) <= 360 and np.nanmax(np.abs(xy[1])) <= 90
    if (ca and cb and ca != cb) or geo(a) != geo(b):
        a = _to_wgs84(a[0], a[1], None if geo(a) else (ca or cb))
        b = _to_wgs84(b[0], b[1], None if geo(b) else (cb or ca))
    return (np.asarray(a[0], float), np.asarray(a[1], float)), (np.asarray(b[0], float), np.asarray(b[1], float))

def _vertex_keys(geom, q):
    """Geometric key of each cell: its set of vertex coordinates (quantised by q, sorted) – independent of cell/vertex order."""
    nx, ny, faces = geom
    qx, qy = np.round(nx / q).astype(np.int64), np.round(ny / q).astype(np.int64)
    return [tuple(sorted(zip(qx[r[r >= 0]].tolist(), qy[r[r >= 0]].tolist()))) for r in faces]

def face_order(display_ds: xr.Dataset, value_ds: xr.Dataset):
    """
    Cell order of `value_ds` (e.g. DELWAQ map, written on the waqgeom grid) relative to the mesh drawn
    from `display_ds` (e.g. D-Flow FM map). Returns idx (len = number of display cells) so that values_of_value_ds[..., idx]
    is in display order, or None when both files already use the same order.
    Steps (stops at the first one that succeeds):
      1. EXACT match on the cell vertex coordinates (independent of cell order, vertex order and centre definition)
      2. 1–1 match on cell centres (vertex mean, computed the same way for both files) within 1/4 of the cell size
      3. Aggregated / different WAQ mesh: each display cell takes the WAQ cell CONTAINING its centre (else the nearest of 2 cells)
    Raises only when the two meshes barely overlap.
    """
    gd, gv = _face_geometry(display_ds), _face_geometry(value_ds)
    cd, cv = _face_centroids(display_ds), _face_centroids(value_ds)
    if cd is None or cv is None:
        raise ValueError("Cannot read face coordinates of the HYD/WAQ map files to align the grids")
    cd, cv = _same_frame(display_ds, value_ds, cd, cv)
    n_d, n_v = len(cd[0]), len(cv[0])
    ext = max(np.nanmax(cd[0]) - np.nanmin(cd[0]), np.nanmax(cd[1]) - np.nanmin(cd[1]), 1e-12)
    cell = ext / max(np.sqrt(n_v), 1.0)                      # typical cell size
    ident = np.arange(n_d)
    # 1. Match on vertices
    if gd is not None and gv is not None:
        (dx, dy), (vx, vy) = _same_frame(display_ds, value_ds, gd[:2], gv[:2])
        q = 1e-5 * ext
        kv = {k: i for i, k in enumerate(_vertex_keys((vx, vy, gv[2]), q))}
        hits = [kv.get(k, -1) for k in _vertex_keys((dx, dy, gd[2]), q)]
        order = np.asarray(hits, np.int64)
        if (order >= 0).all() and len(np.unique(order)) == n_d:
            if n_d == n_v and (order == ident).all(): 
                print(f"[results] WAQ and HYD map grids are identical ({n_d} cells, same order) - no reordering")
                return None
            print(f"[results] WAQ cells matched to the HYD mesh by vertices "
                f"({int((order != ident[:len(order)]).sum()) if n_d == n_v else n_d} of {n_d} cells reordered)")
            return order
    
    ok_v = np.isfinite(cv[0]) & np.isfinite(cv[1])
    tree, vid = KDTree(np.column_stack([cv[0][ok_v], cv[1][ok_v]])), np.nonzero(ok_v)[0]
    pd_ = np.column_stack([np.nan_to_num(cd[0]), np.nan_to_num(cd[1])])
    dist, j = tree.query(pd_)
    order = vid[j].astype(np.int64)
    # 2. 1–1 on cell centres
    if n_d == n_v and len(np.unique(order)) == n_d and np.nanmax(dist) < 0.25 * cell:
        if (order == ident).all(): return None
        print(f"[results] WAQ cells matched to the HYD mesh by centroids ({int((order != ident).sum())} of {n_d} reordered)")
        return order
    # 3. Cell containing the centre (aggregated / different mesh)
    inside = np.zeros(n_d, bool)
    if gv is not None:
        try:
            from shapely import points, polygons, STRtree
            vx, vy = _same_frame(display_ds, value_ds, cd, gv[:2])[1]
            rings = [np.column_stack([vx[r[r >= 0]], vy[r[r >= 0]]]) for r in gv[2]]
            ok = [len(r) >= 3 for r in rings]
            polys = polygons([r if k else np.zeros((3, 2)) for r, k in zip(rings, ok)])
            pi, fi = STRtree(polys).query(points(pd_), predicate="within")
            sel = np.asarray(ok)[fi]
            first = {}
            for p_, f_ in zip(pi[sel], fi[sel]): first.setdefault(int(p_), int(f_))
            for p_, f_ in first.items(): order[p_], inside[p_] = f_, True
        except Exception as e:
            print(f"[results] polygon matching unavailable ({e}) - using nearest WAQ cells")
    near = inside | (dist < 2 * cell)
    if near.mean() < 0.9:
        raise ValueError(f"HYD and WAQ map grids do not overlap ({int(near.sum())} of {n_d} HYD cells lie inside "
            f"or next to a WAQ cell; HYD {n_d} cells, WAQ {n_v} cells) - cannot align WAQ results."
        )
    print(f"[results] WAQ grid ({n_v} cells) differs from the HYD mesh ({n_d} cells) - approximate mapping: "
        f"{int(inside.sum())} HYD cells inside a WAQ cell, {int((~inside).sum())} by nearest WAQ cell"
    )
    return order

def surface_index(source: str, n_layers: int) -> int:
    return n_layers - 1 if source == "hyd" else 0

def bottom_index(source: str, n_layers: int) -> int:
    return 0 if source == "hyd" else n_layers - 1

def _edge_layer(da, ld: str, source: str, which: str):
    """Surface/bottom layer THAT HAS VALUES at each point & time.
    z-layers (Layertype = 2): layers below the bed / above the water surface are NaN -> a fixed index (0 or n-1) gives NaN
    at most stations -> empty Stratification ΔT and bottom temperature."""
    n = da.sizes[ld]
    fixed = bottom_index(source, n) if which == "bottom" else surface_index(source, n)
    try:
        idx = xr.DataArray(np.arange(n), dims=ld)
        pos = idx.where(da.notnull())
        pos = pos.min(ld) if fixed == 0 else pos.max(ld)        # search inward from the fixed-index side
        pos = pos.fillna(fixed).astype(int).compute()
        return da.isel({ld: pos})
    except Exception:
        return da.isel({ld: fixed})

def select_layer(da, source: str, layer: str):
    """layer: 'surface' | 'bottom' | 'average' | order."""
    ld = layer_dim(da, exclude=[d for d in (time_dim(da), station_dim(da), face_dim(da)) if d])
    if ld is None or ld not in da.dims: return da
    if layer == "average": return da.mean(dim=ld, skipna=True)
    if layer == "bottom": return _edge_layer(da, ld, source, "bottom")
    if layer not in (None, "", "surface"):
        try: return da.isel({ld: int(layer)})
        except Exception: pass
    return _edge_layer(da, ld, source, "surface")

def var_meta(source: str, name: str) -> dict:
    if source == "hyd":
        meta = constants.HYD_MAP_VARS.get(name) or constants.HYD_HIS_VARS.get(name)
        if meta: return dict(meta)
        label = constants.VARIABLES_NAME.get(name, name)
        return {"label": label, "unit": ""}
    key = name.replace("mesh2d_2d_", "").replace("mesh2d_", "")
    full, unit = constants.UNITS.get(key, key), ""
    if "(" in full and full.endswith(")"):
        unit = full[full.rfind("(") + 1:-1]
        full = full[:full.rfind("(")].strip()
    return {"label": full, "unit": unit, "key": key, "palette": "quality"}

# Project results
class ProjectResults:
    def __init__(self, project_name: str):
        self.project = project_name
        self.root = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
        out = os.path.join(self.root, "output")
        self.hyd_dir, self.waq_dir = os.path.join(out, "HYD"), os.path.join(out, "WAQ")
        self.hyd_name, self.waq_name, self.waq_model = None, None, None
        self.paths: dict[str, str | None] = {"hyd_his": None, "hyd_map": None, "waq_his": None, "waq_map": None}
        self._locate(out)
    
    # Locate files
    def _locate(self, out):
        if os.path.isdir(self.hyd_dir):
            files = sorted(os.listdir(self.hyd_dir))
            his = [f for f in files if f.endswith("_his.zarr") or f.endswith("_his.nc")]
            mp = [f for f in files if f.endswith("_map.zarr") or f.endswith("_map.nc")]
            if his:
                self.paths["hyd_his"] = os.path.join(self.hyd_dir, his[0])
                self.hyd_name = his[0].rsplit("_his", 1)[0]
            if mp:
                pref = [f for f in mp if self.hyd_name and f.startswith(self.hyd_name)] or mp
                self.paths["hyd_map"] = os.path.join(self.hyd_dir, pref[0])
                self.hyd_name = self.hyd_name or pref[0].rsplit("_map", 1)[0]
        # WAQ: use selected scenarioin: output/config/config.json
        # per-user copy on sample / shared projects: another user's WAQ choice does not change this one
        from services.functions import user_config_path
        cfg_path = user_config_path(self.project)
        if os.path.exists(cfg_path):
            try:
                cfg = json.load(open(cfg_path, encoding="utf-8"))
                self.waq_name = cfg.get("model_name") or None
                self.waq_model = cfg.get("model_type") or None
            except Exception: pass
        if os.path.isdir(self.waq_dir):
            files = sorted(os.listdir(self.waq_dir))
            if not self.waq_name:
                names = [f.rsplit("_", 1)[0] for f in files if f.endswith(".zarr")]
                self.waq_name = names[0] if names else None
            if self.waq_name:
                for kind in ("his", "map"):
                    for ext in (".zarr", ".nc"):
                        p = os.path.join(self.waq_dir, f"{self.waq_name}_{kind}{ext}")
                        if os.path.exists(p):
                            self.paths[f"waq_{kind}"] = p
                            break
                meta = os.path.join(self.waq_dir, f"{self.waq_name}.json")
                if not self.waq_model and os.path.exists(meta):
                    try: self.waq_model = json.load(open(meta, encoding="utf-8")).get("model_type")
                    except Exception: pass

    def ds(self, key) -> xr.Dataset | None:
        return _open(self.paths.get(key))

    @property
    def has_hyd(self):
        return self.paths["hyd_his"] is not None or self.paths["hyd_map"] is not None

    @property
    def has_waq(self):
        return self.paths["waq_his"] is not None or self.paths["waq_map"] is not None

    def version(self) -> str:
        return "|".join(f"{k}:{int(_mtime(p))}" for k, p in self.paths.items() if p)

    # Time
    def times(self, source: str) -> pd.DatetimeIndex:
        ds = self.ds(f"{source}_his") or self.ds(f"{source}_map")
        if ds is None: return pd.DatetimeIndex([])
        for name in ("time", "nTimesDlwq"):
            if name in ds.variables or name in ds.coords:
                try: return pd.DatetimeIndex(pd.to_datetime(ds[name].values))
                except Exception: pass
        return pd.DatetimeIndex([])

    def time_index(self, source: str, when) -> int:
        t = self.times(source)
        if not len(t): return 0
        if when is None: return len(t) - 1
        ts = pd.Timestamp(when)
        idx = int(t.searchsorted(ts, side="right") - 1)
        return max(0, min(len(t) - 1, idx))

    # Stations
    def stations(self, source: str) -> list[dict]:
        ds = self.ds(f"{source}_his")
        if ds is None or "station_name" not in ds.variables: return []
        names = _decode(ds["station_name"].values)
        if source == "hyd": return self._hyd_stations(ds, names)
        return self._waq_stations(ds, names)

    def _hyd_stations(self, ds, names):
        xk = "station_x_coordinate" if "station_x_coordinate" in ds.variables else "station_x"
        yk = "station_y_coordinate" if "station_y_coordinate" in ds.variables else "station_y"
        if xk not in ds.variables:
            return [{"name": n, "lat": None, "lon": None, "source": "hyd", "coord_source": None} for n in names]
        x, y = np.asarray(ds[xk].values, float).ravel(), np.asarray(ds[yk].values, float).ravel()
        lon, lat = _to_wgs84(x, y, _crs_of(ds))
        return [{"name": n, "lon": _r(lo, 6), "lat": _r(la, 6), "source": "hyd", "coord_source": "his"}
            for n, lo, la in zip(names, np.ravel(lon), np.ravel(lat))]

    def waq_obs_points(self) -> dict:
        if not self.waq_name: return {}
        path = os.path.join(self.waq_dir, f"{self.waq_name}.json")
        if not os.path.exists(path): return {}
        try: data = json.load(open(path, encoding="utf-8"))
        except Exception:
            traceback.print_exc()
            return {}
        out = {}
        for p in data.get("wq_obs") or []:
            try:
                if isinstance(p, dict):
                    name = p.get("name")
                    lat = p.get("lat", p.get("latitude"))
                    lon = p.get("lon", p.get("longitude"))
                else: name, lat, lon = p[0], p[1], p[2]
                if lat is None or lon is None: continue
                lat_f, lon_f = float(lat), float(lon)
            except Exception: continue
            if abs(lat_f) > 90 and abs(lon_f) <= 90: lat_f, lon_f = lon_f, lat_f
            if abs(lat_f) <= 90 and abs(lon_f) <= 180: out[_name_key(name)] = (lat_f, lon_f)
        return out

    def _domain_bounds(self):
        try: mesh = self.mesh()
        except Exception: mesh = None
        if not mesh: return None
        (a, b), (c, d) = mesh["bounds"]
        pad = 0.02
        return a - pad, b - pad * 2, c + pad, d + pad * 2

    def _waq_stations(self, ds, names):
        obs = self.waq_obs_points()
        bounds = self._domain_bounds()
        def inside(lat, lon):
            if lat is None or lon is None or not (np.isfinite(lat) and np.isfinite(lon)):
                return False
            if bounds is None: return abs(lat) <= 90 and abs(lon) <= 180
            return bounds[0] <= lat <= bounds[2] and bounds[1] <= lon <= bounds[3]
        his_ll = [None] * len(names)
        if "station_x" in ds.variables and "station_y" in ds.variables:
            x = np.asarray(ds["station_x"].values, float).ravel()
            y = np.asarray(ds["station_y"].values, float).ravel()
            candidates = []
            if np.nanmax(np.abs(x)) <= 180 and np.nanmax(np.abs(y)) <= 90:
                candidates.append((x, y))                         # already in degrees
            for crs in dict.fromkeys(c for c in (_crs_of(ds), _crs_of(self.ds("waq_map")),
                _crs_of(self.ds("hyd_map")), _crs_of(self.ds("hyd_his"))) if c):
                lo, la = _to_wgs84(x, y, crs)
                candidates.append((np.ravel(lo), np.ravel(la)))
            for lo, la in candidates:
                ok = [inside(float(b), float(a)) for a, b in zip(lo, la)]
                if any(ok):
                    his_ll = [(float(b), float(a)) if k else None for a, b, k in zip(lo, la, ok)]
                    break
        out = []
        for i, n in enumerate(names):
            hit = obs.get(_name_key(n))
            if hit is None:   # DELWAQ using 20 characters
                hit = next((v for k, v in obs.items() if k[:20] == _name_key(n)[:20]), None)
            if hit is not None: lat, lon, src = hit[0], hit[1], "scenario"
            else: 
                pos = his_ll[i]
                if pos is not None:
                    lat, lon = pos
                    src = "his-checked"
                else: lat = lon = src = None
            out.append({"name": n, "lat": _r(lat, 6), "lon": _r(lon, 6), "source": "waq", "coord_source": src})
        missing = [o["name"] for o in out if o["lat"] is None]
        if missing: print(f"[dashboard] WAQ stations without reliable coordinates: {missing}")
        return out

    def sources(self) -> list[dict]:
        ds = self.ds("hyd_his")
        if ds is None or "source_sink_name" not in ds.variables: return []
        names = _decode(ds["source_sink_name"].values)
        out: list[dict[str, str | float | None]] = [{"name": n, "lon": None, "lat": None} for n in names]
        for xk, yk in (("source_sink_x_coordinate", "source_sink_y_coordinate"),):
            if xk in ds.variables:
                x = np.asarray(ds[xk].values, float)
                y = np.asarray(ds[yk].values, float)
                if x.ndim > 1: x, y = np.nanmean(x, axis=-1), np.nanmean(y, axis=-1)
                lon, lat = _to_wgs84(x, y, _crs_of(ds))
                for o, lo, la in zip(out, np.ravel(lon), np.ravel(lat)):
                    o["lon"], o["lat"] = _r(lo, 6), _r(la, 6)
        return out

    # Variables
    def map_variables(self, source: str) -> list[dict]:
        ds = self.ds(f"{source}_map")
        if ds is None: return []
        out = []
        if source == "hyd":
            for name, meta in constants.HYD_MAP_VARS.items():
                if name in ds.data_vars:
                    da = ds[name]
                    out.append({"source": "hyd", "name": name, **meta, "layered": len(da.dims) >= 3})
            return out
        for var in ds.data_vars:
            name = str(var)
            if name in constants.SKIP_WAQ or "_2d_" in name: continue
            da = ds[name]
            if not (time_dim(da) and face_dim(da)): continue
            meta = var_meta("waq", name)
            out.append({"source": "waq", "name": name, **meta, "layered": len(da.dims) >= 3})
        return sorted(out, key=lambda v: v["label"])

    def his_variables(self, source: str) -> list[dict]:
        ds = self.ds(f"{source}_his")
        if ds is None: return []
        out = []
        for var in ds.data_vars:
            name = str(var)
            if name in constants.SKIP_WAQ or name.startswith(("source_sink", "cross_section", "water_balance", "station")): continue
            da = ds[name]
            if not (time_dim(da) and station_dim(da)): continue
            if source == "hyd" and name not in constants.HYD_HIS_VARS: continue
            meta = constants.HYD_HIS_VARS.get(name, {}) if source == "hyd" else var_meta("waq", name) or {}
            out.append({"source": source, "name": name, **meta, "layered": len(da.dims) >= 3})
        return out

    # Mesh
    def mesh(self) -> dict | None:
        key = "hyd_map" if self.paths["hyd_map"] else "waq_map"
        path = self.paths.get(key)
        if not path: return None
        m = _mtime(path)
        with _LOCK:
            hit = _MESH_CACHE.get(path)
            if hit and hit[0] == m: return hit[1]
        ds = self.ds(key)
        if ds is None: return None
        try:
            if "mesh2d_node_x" in ds.variables:
                nx, ny = ds["mesh2d_node_x"].values, ds["mesh2d_node_y"].values
                fn = ds["mesh2d_face_nodes"].values
            elif "NetNode_x" in ds.variables:
                nx, ny = ds["NetNode_x"].values, ds["NetNode_y"].values
                fn = ds["NetElemNode"].values
            else: return None
            crs = _crs_of(ds)
            if crs is None and np.nanmax(np.abs(nx)) > 360: crs = _crs_of(self.ds("hyd_his"))
            lon, lat = _to_wgs84(nx, ny, crs)
            lon, lat = np.round(np.asarray(lon), 6), np.round(np.asarray(lat), 6)
            fn = np.where(np.isnan(fn), 0, fn).astype(int) - 1
            if fn.min() < -1: fn = fn + 1
            features, cx, cy = [], [], []
            for i, row in enumerate(fn):
                idx = row[row >= 0]
                if len(idx) < 3: ring = []
                else:
                    ring = [[float(lon[j]), float(lat[j])] for j in idx]
                    ring.append(ring[0])
                features.append({"type": "Feature", "id": i, "properties": {"i": i},
                    "geometry": {"type": "Polygon", "coordinates": [ring]}})
                if len(idx): cx.append(float(lon[idx].mean())); cy.append(float(lat[idx].mean()))
                else: cx.append(None); cy.append(None)
            valid_lon = lon[np.isfinite(lon)]
            valid_lat = lat[np.isfinite(lat)]
            result = {
                "type": "FeatureCollection", "features": features,
                "bounds": [[float(valid_lat.min()), float(valid_lon.min())], [float(valid_lat.max()), float(valid_lon.max())]],
                "n_faces": len(features), "source": key, "centroids": [cx, cy],
            }
        except Exception:
            traceback.print_exc()
            return None
        with _LOCK:
            _MESH_CACHE[path] = (m, result)
        return result

    # WAQ -> display-mesh face order
    def waq_face_order(self):
        """
        The map is drawn with the HYD (D-Flow FM) mesh, but the WAQ (DELWAQ) map file is written on the
        waqgeom grid, whose faces are not guaranteed to be in the same order. Plotting WAQ values by
        index on the HYD mesh then puts concentrations on the wrong cells (e.g. the opposite shore).
        Returns idx so that waq_values[idx] is in display-mesh order, or None if the order is identical
        (or the display mesh is the WAQ mesh itself).
        """
        hp, wp = self.paths.get("hyd_map"), self.paths.get("waq_map")
        if not hp or not wp: return None
        key = f"{hp}|{_mtime(hp)}|{wp}|{_mtime(wp)}"
        with _LOCK:
            if key in _ORDER_CACHE: return _ORDER_CACHE[key]
        order = face_order(self.ds("hyd_map"), self.ds("waq_map"))
        with _LOCK:
            _ORDER_CACHE[key] = order
        return order

    def waq_face_index(self, display_index: int) -> int:
        order = self.waq_face_order() if self.paths.get("hyd_map") else None
        return int(order[display_index]) if order is not None else display_index

    # Field (map)
    def field(self, source: str, name: str, layer: str, when) -> dict:
        ds = self.ds(f"{source}_map")
        if ds is None: raise ValueError(f"No {source.upper()} map file in project")
        if name not in ds.data_vars: raise ValueError(f"Variable '{name}' not found in {source.upper()} map")
        da = ds[name]
        td = time_dim(da)
        tidx = self.time_index(source, when)
        da_t = da.isel({td: tidx}) if td else da
        if source == "waq" and layer == "average":
            alt = name.replace("mesh2d_", "mesh2d_2d_", 1)
            if alt in ds.data_vars: da_t = ds[alt].isel({time_dim(ds[alt]): tidx})
        vals = np.asarray(select_layer(da_t, source, layer).values, dtype=float).ravel()
        if source == "waq" and self.mesh() and self.mesh()["source"] == "hyd_map":
            order = self.waq_face_order()
            if order is not None: vals = vals[order]     # WAQ order -> display (HYD) mesh order
        vmin, vmax = self.value_range(source, name, layer)
        times = self.times(source)
        return {
            "source": source, "name": name, "layer": layer, **var_meta(source, name),
            "time": _iso(times[tidx]) if len(times) else None, "index": tidx,
            "values": [None if not np.isfinite(v) else round(float(v), 4) for v in vals],
            "min": vmin, "max": vmax,
        }

    def value_range(self, source, name, layer) -> list:
        path = self.paths.get(f"{source}_map")
        key = f"{path}|{name}|{layer}"
        if path is None: return [0, 1]
        m = _mtime(path)
        hit = _RANGE_CACHE.get(key)
        if hit and hit[0] == m: return hit[1]
        ds = self.ds(f"{source}_map")
        if ds is None or name not in ds.data_vars: return [0, 1]
        da = ds[name]
        td = time_dim(da)
        n = da.sizes.get(td, 1) if td else 1
        steps = np.unique(np.linspace(0, n - 1, min(24, n)).astype(int))
        sample = select_layer(da.isel({td: steps}) if td else da, source, layer).values
        sample = np.asarray(sample, float)
        sample = sample[np.isfinite(sample)]
        rng = [(_r(np.percentile(sample, 2), 4) if sample.size else 0), (_r(np.percentile(sample, 98), 4) if sample.size else 1)]
        if rng[0] == rng[1]: rng[1] = (rng[0] or 0) + 1e-6
        _RANGE_CACHE[key] = (m, rng)
        return rng

    # Station series / profile
    def station_series(self, source: str, name: str, layer: str, start, end, stations=None, max_points=600) -> dict:
        ds = self.ds(f"{source}_his")
        if ds is None or name not in ds.data_vars: return {"series": [], **var_meta(source, name)}
        da = ds[name]; td, sd = time_dim(da), station_dim(da)
        t = self.times(source); mask = np.ones(len(t), bool)
        if start is not None: mask &= t > pd.Timestamp(start)
        if end is not None: mask &= t <= pd.Timestamp(end)
        idx = np.where(mask)[0]
        if not len(idx): idx = np.arange(len(t))
        if len(idx) > max_points: idx = idx[np.linspace(0, len(idx) - 1, max_points).astype(int)]
        sub = select_layer(da.isel({td: idx}), source, layer)
        arr = np.asarray(sub.transpose(td, sd).values, float)
        names = _decode(ds["station_name"].values)
        series = []
        for j, n in enumerate(names):
            if stations and n not in stations: continue
            series.append({"name": n, "x": [_iso(x) for x in t[idx]],
                "y": [None if not np.isfinite(v) else round(float(v), 4) for v in arr[:, j]]})
        return {"series": series, "source": source, "name": name, "layer": layer, **var_meta(source, name)}

    def _profile_his(self, source, name, station, when):
        ds = self.ds(f"{source}_his")
        if ds is None or name not in ds.data_vars:
            return f"'{name}' not found in {source.upper()} his file"
        da = ds[name]; td, sd = time_dim(da), station_dim(da)
        ld = layer_dim(da, exclude=[td, sd])
        if ld is None or ld not in da.dims:
            return f"{source.upper()} his file has no vertical layers for '{name}'"
        names = _decode(ds["station_name"].values)
        if station not in names:
            if station: return f"Station '{station}' is not in the {source.upper()} his file"
            station = names[0]
        j, tidx = names.index(station), self.time_index(source, when)
        vals = np.asarray(da.isel({td: tidx, sd: j}).values, float)
        n = len(vals)
        depth = None
        zds = ds if "zcoordinate_c" in ds.variables else self.ds("hyd_his")
        if zds is not None and "zcoordinate_c" in zds.variables:
            try:
                znames = _decode(zds["station_name"].values)
                jz = znames.index(station)
                tz = self.time_index("hyd", self.times(source)[tidx])
                z = np.asarray(zds["zcoordinate_c"].isel(time=tz, stations=jz).values, float)
                wl = float(zds["waterlevel"].isel(time=tz, stations=jz).values) if "waterlevel" in zds.variables else float(np.nanmax(z))
                d = wl - z  # HYD: bottom -> surface
                depth = d if source == "hyd" else d[::-1]
            except Exception:
                depth = None
        if depth is None or len(depth) != n:
            frac = (np.arange(n) + 0.5) / n
            depth = (1 - frac) * 100 if source == "hyd" else frac * 100
            unit_depth = "% depth"
        else: unit_depth = "m"
        order = np.argsort(depth)
        times = self.times(source)
        return {"station": station, "time": _iso(times[tidx]), "depth": [_r(depth[i], 2) for i in order],
            "values": [_r(vals[i], 4) for i in order], "depth_unit": unit_depth, "from": "his",
            "source": source, "name": name, **var_meta(source, name)}

    def map_name_for(self, source: str, his_name: str) -> str | None:
        ds = self.ds(f"{source}_map")
        if ds is None: return None
        cands = [constants.HIS_TO_MAP.get(his_name)] if source == "hyd" else []
        cands += [his_name, f"mesh2d_{his_name}"]
        return next((c for c in cands if c and c in ds.data_vars), None)

    def station_lonlat(self, station: str, prefer: str):
        order = [prefer, "waq" if prefer == "hyd" else "hyd"]
        for src in order:
            for s in self.stations(src):
                if s["name"] == station and s["lat"] is not None:
                    return s["lon"], s["lat"]
        return None

    def nearest_face(self, lon: float, lat: float):
        mesh = self.mesh()
        if not mesh: return None, None
        cx = np.array([np.nan if v is None else v for v in mesh["centroids"][0]], float)
        cy = np.array([np.nan if v is None else v for v in mesh["centroids"][1]], float)
        dx = (cx - lon) * 111320 * np.cos(np.radians(lat))
        dy = (cy - lat) * 110540
        d = np.hypot(dx, dy)
        if not np.isfinite(d).any(): return None, None
        i = int(np.nanargmin(d))
        return i, float(d[i])

    def _profile_map(self, source, name, station, when):
        mname = self.map_name_for(source, name)
        if mname is None: return f"No {source.upper()} map variable for '{name}'"
        ds = self.ds(f"{source}_map")
        if ds is None: raise ValueError(f"Cannot open dataset")
        da = ds[mname]
        td, fd = time_dim(da), face_dim(da)
        ld = layer_dim(da, exclude=[td, fd])
        if fd is None or ld is None or ld not in da.dims:
            return f"'{name}' has no vertical layers (2D result)"
        ll = self.station_lonlat(station, source)
        if ll is None: return f"Station '{station}' has no coordinates"
        i, dist = self.nearest_face(*ll)
        dist = dist if dist is not None else 0.0
        if i is None: return "No mesh to locate the station"
        mesh = self.mesh()
        if mesh is None: raise ValueError("Mesh is not available")
        if da.sizes[fd] != len(mesh["features"]):
            return f"{source.upper()} map grid differs from the display mesh"
        if dist > 1000: return f"Station '{station}' is {dist / 1000:.1f} km outside the model grid"
        tidx = self.time_index(source, when)
        fi = self.waq_face_index(i) if source == "waq" and mesh["source"] == "hyd_map" else i
        vals = np.asarray(da.isel({td: tidx, fd: fi}).values, float).ravel()
        n = len(vals)
        frac = (np.arange(n) + 0.5) / n 
        rel = (1 - frac) if source == "hyd" else frac   # HYD: botom -> surface; WAQ: surface -> bottom
        wd, unit_depth = None, "% depth"
        hmap = self.ds("hyd_map")
        if hmap is not None and "mesh2d_waterdepth" in hmap.data_vars and hmap.sizes.get("mesh2d_nFaces") == da.sizes[fd]:
            try:
                tz = self.time_index("hyd", self.times(source)[tidx])
                wd = float(hmap["mesh2d_waterdepth"].isel(time=tz, mesh2d_nFaces=i).values)
            except Exception: wd = None
        if wd is not None and np.isfinite(wd) and wd > 0: depth, unit_depth = rel * wd, "m"
        else: depth = rel * 100
        order = np.argsort(depth)
        times = self.times(source)
        return {"station": station, "time": _iso(times[tidx]), "depth": [_r(depth[k], 2) for k in order],
            "values": [_r(vals[k], 4) for k in order], "depth_unit": unit_depth, "from": "map",
            "cell": i, "cell_distance_m": _r(dist, 0), "source": source, "name": name, **var_meta(source, name)}
    
    def profile(self, source: str, name: str, station: str, when) -> dict | None:
        res = self._profile_his(source, name, station, when)
        if isinstance(res, dict): return res
        alt = self._profile_map(source, name, station, when)
        if isinstance(alt, dict): return alt
        return {"empty": True, "reason": alt or res, "station": station, "source": source, "name": name}

    # Sources / sinks
    def source_discharge(self, start, end) -> list[dict]:
        ds = self.ds("hyd_his")
        if ds is None: return []
        var = next((v for v in ("source_sink_discharge_average", "source_sink_current_discharge",
            "source_sink_prescribed_discharge") if v in ds.data_vars), None)
        if var is None: return []
        t = self.times("hyd")
        mask = (t > pd.Timestamp(start)) & (t <= pd.Timestamp(end)) if start is not None else np.ones(len(t), bool)
        idx = np.where(mask)[0]
        if not len(idx): idx = np.arange(len(t))
        arr = np.asarray(ds[var].isel(time=idx).values, float)
        if arr.ndim > 2: arr = np.nanmean(arr, axis=tuple(range(2, arr.ndim)))
        names = _decode(ds["source_sink_name"].values)
        mean = np.nanmean(arr, axis=0)
        out = [{"name": n, "value": _r(v, 4), "kind": "sink" if v < 0 else "source"} for n, v in zip(names, mean)]
        total_in = sum(o["value"] for o in out if o["value"] and o["value"] > 0) or 1.0
        for o in out: o["share"] = _r(100 * o["value"] / total_in, 1) if o["value"] and o["value"] > 0 else None
        return sorted(out, key=lambda o: -(o["value"] or 0))

    # Info
    def info(self) -> dict:
        def block(source):
            his, mp = self.ds(f"{source}_his"), self.ds(f"{source}_map")
            t = self.times(source)
            n_layers = None
            for d in (his, mp):
                if d is not None:
                    for dim, size in d.sizes.items():
                        dim_name = str(dim)
                        if dim_name in ("laydim",) or "nLayers" in dim_name: n_layers = int(size)
            return {
                "available": his is not None or mp is not None,
                "scenario": self.hyd_name if source == "hyd" else self.waq_name,
                "model_type": self.waq_model if source == "waq" else "D-Flow FM",
                "start": _iso(t[0]) if len(t) else None, "end": _iso(t[-1]) if len(t) else None,
                "n_times": int(len(t)), "n_layers": n_layers,
                "step_minutes": _r((t[1] - t[0]).total_seconds() / 60, 1) if len(t) > 1 else None,
                "has_his": his is not None, "has_map": mp is not None,
                "files": {k: os.path.basename(v) for k, v in self.paths.items() if v and k.startswith(source)},
                "synthetic": bool(source == "waq" and self._synthetic()),
            }
        return {"hyd": block("hyd"), "waq": block("waq")}

    def _synthetic(self):
        meta = os.path.join(self.waq_dir, f"{self.waq_name}.json") if self.waq_name else None
        try: return bool(meta and os.path.exists(meta) and json.load(open(meta)).get("synthetic"))
        except Exception: return False

def waq_scenarios(project_name: str) -> dict:
    """WAQ scenarios with results (output/WAQ/<name>_his|_map.zarr|.nc + <name>.json) for the quick switch on the dashboard.
    hyd_params = [HYD his, HYD map] – the same parameters Visualization sends to /setup_database."""
    pr = ProjectResults(project_name)
    out = []
    if os.path.isdir(pr.waq_dir):
        files = set(os.listdir(pr.waq_dir))
        names = sorted({f.rsplit("_", 1)[0] for f in files
                        if f.endswith(("_his.zarr", "_map.zarr", "_his.nc", "_map.nc"))})
        for n in names:
            meta = os.path.join(pr.waq_dir, f"{n}.json")
            if not os.path.exists(meta): continue                 # Visualization also lists only scenarios that have <name>.json
            try: model = json.load(open(meta, encoding="utf-8")).get("model_type", "")
            except Exception: model = ""
            out.append({"name": n, "model_type": model,
                "his": f"{n}_his.zarr" if f"{n}_his.zarr" in files else "",
                "map": f"{n}_map.zarr" if f"{n}_map.zarr" in files else ""})
    hyd = sorted(f for f in os.listdir(pr.hyd_dir) if f.endswith(".zarr")) if os.path.isdir(pr.hyd_dir) else []
    his = next((f for f in hyd if f.endswith("_his.zarr")), "")
    mp = next((f for f in hyd if f.endswith("_map.zarr")), "")
    return {"scenarios": out, "current": pr.waq_name, "hyd_params": [his, mp]}

def catalog(project_name: str) -> dict:
    pr = ProjectResults(project_name); info = pr.info()
    return {
        "project": project_name, "info": info, "has_results": pr.has_hyd or pr.has_waq,
        "map_variables": pr.map_variables("hyd") + pr.map_variables("waq"),
        "his_variables": pr.his_variables("hyd") + pr.his_variables("waq"),
        "stations": {"hyd": pr.stations("hyd"), "waq": pr.stations("waq")},
        "sources": pr.sources(), "version": pr.version(), 
        "times": {s: [_iso(t) for t in pr.times(s)] for s in ("hyd", "waq")},
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }