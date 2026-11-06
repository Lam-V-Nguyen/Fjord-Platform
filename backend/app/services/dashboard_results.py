"""
dashboard_results.py
====================
Đọc kết quả mô phỏng THỦY ĐỘNG LỰC (HYD, D-Flow FM) và CHẤT LƯỢNG NƯỚC (WAQ, D-Water Quality)
của một dự án cho Dashboard. Đây là nguồn dữ liệu CHÍNH của trang Overview.

Quy ước file (giống functions.project_reader):
    <project>/output/HYD/<name>_his.zarr, <name>_map.zarr
    <project>/output/WAQ/<waq>_his.zarr,  <waq>_map.zarr   (+ <waq>.json, hoặc output/config/config.json)

Tự nhận dạng chiều:
    thời gian : 'time' (HYD) | 'nTimesDlwq' (WAQ)
    trạm      : 'stations' | 'nStations'
    ô lưới    : '*nFaces*'
    lớp       : 'laydim' | '*nLayers*' | dim còn lại
Thứ tự lớp: HYD (sigma) đánh số từ đáy -> mặt (mặt = -1); WAQ (DELWAQ) từ mặt -> đáy (mặt = 0).

Mọi Dataset/lưới được cache theo (đường dẫn, mtime) nên mở lại rất nhanh.
"""
from __future__ import annotations
import json, math, os, threading, traceback
from datetime import datetime
import numpy as np, pandas as pd, xarray as xr
from config import PROJECT_ROOT
from services import constants

_LOCK = threading.Lock()
_MESH_CACHE: dict[str, tuple[float, dict]] = {}
_RANGE_CACHE: dict[str, tuple[float, list]] = {}

# Biến bản đồ HYD hiển thị trên Dashboard (tên trong map file -> nhãn, đơn vị, thang màu)
HYD_MAP_VARS = {
    "mesh2d_tem1": {"label": "Temperature", "unit": "°C", "palette": "thermal"},
    "mesh2d_sa1": {"label": "Salinity", "unit": "ppt", "palette": "haline"},
    "mesh2d_ucmag": {"label": "Current speed", "unit": "m/s", "palette": "speed"},
    "mesh2d_s1": {"label": "Water level", "unit": "m", "palette": "level"},
    "mesh2d_waterdepth": {"label": "Water depth", "unit": "m", "palette": "depth"},
    "mesh2d_Contaminant": {"label": "Contaminant", "unit": "mg/m³", "palette": "quality"},
}
# Biến trạm HYD (his)
HYD_HIS_VARS = {
    "temperature": {"label": "Temperature", "unit": "°C"},
    "salinity": {"label": "Salinity", "unit": "ppt"},
    "velocity_magnitude": {"label": "Current speed", "unit": "m/s"},
    "waterlevel": {"label": "Water level", "unit": "m"},
    "Contaminant": {"label": "Contaminant", "unit": "mg/m³"},
}
HIS_TO_MAP = {"temperature": "mesh2d_tem1", "salinity": "mesh2d_sa1", "velocity_magnitude": 
    "mesh2d_ucmag", "waterlevel": "mesh2d_s1", "Contaminant": "mesh2d_Contaminant"}
SKIP_WAQ = {"station_name", "station_x", "station_y", "station_z", "nTimesDlwqBnd", "mesh2d_layer_dlwq"}


# ======================================================================
# helpers
# ======================================================================
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
    """Mở Zarr (hoặc NetCDF) với cache theo mtime."""
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
    """Chuẩn hoá tên trạm để so khớp (bỏ khoảng trắng, không phân biệt hoa thường)."""
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
        # Có thể đã là độ; nếu số lớn thì không reproject được -> trả nguyên
        return x, y
    try:
        from pyproj import Transformer
        tf = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        return tf.transform(x, y)
    except Exception:
        traceback.print_exc()
        return x, y

def surface_index(source: str, n_layers: int) -> int:
    return n_layers - 1 if source == "hyd" else 0

def bottom_index(source: str, n_layers: int) -> int:
    return 0 if source == "hyd" else n_layers - 1

def select_layer(da, source: str, layer: str):
    """layer: 'surface' | 'bottom' | 'average' | số thứ tự."""
    ld = layer_dim(da, exclude=[d for d in (time_dim(da), station_dim(da), face_dim(da)) if d])
    if ld is None or ld not in da.dims:
        return da
    n = da.sizes[ld]
    if layer == "average":
        return da.mean(dim=ld, skipna=True)
    if layer == "bottom":
        return da.isel({ld: bottom_index(source, n)})
    if layer not in (None, "", "surface"):
        try: return da.isel({ld: int(layer)})
        except Exception: pass
    return da.isel({ld: surface_index(source, n)})

def var_meta(source: str, name: str) -> dict:
    if source == "hyd":
        meta = HYD_MAP_VARS.get(name) or HYD_HIS_VARS.get(name)
        if meta: return dict(meta)
        label = constants.variablesNames.get(name, name)
        return {"label": label, "unit": ""}
    key = name.replace("mesh2d_2d_", "").replace("mesh2d_", "")
    full = constants.units.get(key, key)
    unit = ""
    if "(" in full and full.endswith(")"):
        unit = full[full.rfind("(") + 1:-1]
        full = full[:full.rfind("(")].strip()
    return {"label": full, "unit": unit, "key": key, "palette": "quality"}

# ======================================================================
# Project results
# ======================================================================
class ProjectResults:
    def __init__(self, project_name: str):
        self.project = project_name
        self.root = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
        out = os.path.join(self.root, "output")
        self.hyd_dir, self.waq_dir = os.path.join(out, "HYD"), os.path.join(out, "WAQ")
        self.hyd_name, self.waq_name, self.waq_model = None, None, None
        self.paths: dict[str, str | None] = {"hyd_his": None, "hyd_map": None, "waq_his": None, "waq_map": None}
        self._locate(out)

    # ------------------------------------------------------------ locate files
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
        # WAQ: ưu tiên kịch bản đang chọn trong output/config/config.json
        cfg_path = os.path.join(out, "config", "config.json")
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
        """Chuỗi phiên bản cho cache: đổi khi file kết quả thay đổi."""
        return "|".join(f"{k}:{int(_mtime(p))}" for k, p in self.paths.items() if p)

    # ------------------------------------------------------------ time
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

    # ------------------------------------------------------------ stations
    def stations(self, source: str) -> list[dict]:
        """Trạm quan trắc (WGS84).
        HYD: D-Flow FM ghi station_x_coordinate/station_y_coordinate + biến CRS -> đọc trực tiếp
             (giống functions.hydCreator).
        WAQ: wqPreparation chỉ đưa SỐ SEGMENT của điểm quan trắc vào DELWAQ; station_x/station_y
             trong WAQ his do DELWAQ sinh ra, không có biến CRS -> không tin cậy. Toạ độ đúng là
             bảng người dùng nhập, lưu trong output/WAQ/<waq>.json, khoá 'wq_obs' = [[tên, lat, lon], ...].
             Thứ tự ưu tiên: wq_obs (khớp theo tên) -> station_x/y đã kiểm tra hợp lệ -> không có toạ độ.
        Mỗi trạm có 'coord_source' ('his' | 'scenario' | 'his-checked' | None) để dễ kiểm tra.
        """
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
        """{tên chuẩn hoá: (lat, lon)} từ output/WAQ/<waq>.json -> 'wq_obs'."""
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
        """[[lat_min, lon_min], [lat_max, lon_max]] của lưới (nới 2 km) để kiểm tra toạ độ."""
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
        # Phương án dự phòng: station_x/y của DELWAQ, thử các cách hiểu và chỉ giữ cách nằm trong miền tính
        his_ll = [None] * len(names)
        if "station_x" in ds.variables and "station_y" in ds.variables:
            x = np.asarray(ds["station_x"].values, float).ravel()
            y = np.asarray(ds["station_y"].values, float).ravel()
            candidates = []
            if np.nanmax(np.abs(x)) <= 180 and np.nanmax(np.abs(y)) <= 90:
                candidates.append((x, y))                         # đã là độ
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
            if hit is None:   # DELWAQ cắt tên còn 20 ký tự
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
        # Toạ độ nguồn (nếu có)
        for xk, yk in (("source_sink_x_coordinate", "source_sink_y_coordinate"),):
            if xk in ds.variables:
                x = np.asarray(ds[xk].values, float)
                y = np.asarray(ds[yk].values, float)
                if x.ndim > 1: x, y = np.nanmean(x, axis=-1), np.nanmean(y, axis=-1)
                lon, lat = _to_wgs84(x, y, _crs_of(ds))
                for o, lo, la in zip(out, np.ravel(lon), np.ravel(lat)):
                    o["lon"], o["lat"] = _r(lo, 6), _r(la, 6)
        return out

    # ------------------------------------------------------------ variables
    def map_variables(self, source: str) -> list[dict]:
        ds = self.ds(f"{source}_map")
        if ds is None: return []
        out = []
        if source == "hyd":
            for name, meta in HYD_MAP_VARS.items():
                if name in ds.data_vars:
                    da = ds[name]
                    out.append({"source": "hyd", "name": name, **meta, "layered": len(da.dims) >= 3})
            return out
        for var in ds.data_vars:
            name = str(var)
            if name in SKIP_WAQ or "_2d_" in name: continue
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
            if name in SKIP_WAQ or name.startswith(("source_sink", "cross_section", "water_balance", "station")): continue
            da = ds[name]
            if not (time_dim(da) and station_dim(da)): continue
            if source == "hyd" and name not in HYD_HIS_VARS: continue
            meta = HYD_HIS_VARS.get(name, {}) if source == "hyd" else var_meta("waq", name) or {}
            out.append({"source": source, "name": name, **meta, "layered": len(da.dims) >= 3})
        return out

    # ------------------------------------------------------------ mesh
    def mesh(self) -> dict | None:
        """GeoJSON của lưới (WGS84) + toạ độ tâm ô. Ưu tiên HYD map, nếu không có dùng WAQ map."""
        key = "hyd_map" if self.paths["hyd_map"] else "waq_map"
        path = self.paths.get(key)
        if not path:
            return None
        m = _mtime(path)
        with _LOCK:
            hit = _MESH_CACHE.get(path)
            if hit and hit[0] == m:
                return hit[1]
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
            if crs is None and np.nanmax(np.abs(nx)) > 360:
                crs = _crs_of(self.ds("hyd_his"))
            lon, lat = _to_wgs84(nx, ny, crs)
            lon, lat = np.round(np.asarray(lon), 6), np.round(np.asarray(lat), 6)
            fn = np.where(np.isnan(fn), 0, fn).astype(int) - 1
            # Một số file lưu face_nodes bắt đầu từ 0 (có -1 làm fill)
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

    # ------------------------------------------------------------ field (map)
    def field(self, source: str, name: str, layer: str, when) -> dict:
        ds = self.ds(f"{source}_map")
        if ds is None: raise ValueError(f"No {source.upper()} map file in project")
        if name not in ds.data_vars: raise ValueError(f"Variable '{name}' not found in {source.upper()} map")
        da = ds[name]
        td = time_dim(da)
        tidx = self.time_index(source, when)
        da_t = da.isel({td: tidx}) if td else da
        # WAQ: biến trung bình độ sâu có sẵn (mesh2d_2d_X)
        if source == "waq" and layer == "average":
            alt = name.replace("mesh2d_", "mesh2d_2d_", 1)
            if alt in ds.data_vars: da_t = ds[alt].isel({time_dim(ds[alt]): tidx})
        vals = np.asarray(select_layer(da_t, source, layer).values, dtype=float).ravel()
        vmin, vmax = self.value_range(source, name, layer)
        times = self.times(source)
        return {
            "source": source, "name": name, "layer": layer, **var_meta(source, name),
            "time": _iso(times[tidx]) if len(times) else None, "index": tidx,
            "values": [None if not np.isfinite(v) else round(float(v), 4) for v in vals],
            "min": vmin, "max": vmax,
        }

    def value_range(self, source, name, layer) -> list:
        """Min/max (P2–P98) trên tối đa 24 bước thời gian -> thang màu ổn định khi chạy animation."""
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

    # ------------------------------------------------------------ station series / profile
    def station_series(self, source: str, name: str, layer: str, start, end, stations=None, max_points=600) -> dict:
        ds = self.ds(f"{source}_his")
        if ds is None or name not in ds.data_vars: return {"series": [], **var_meta(source, name)}
        da = ds[name]
        td, sd = time_dim(da), station_dim(da)
        t = self.times(source)
        mask = np.ones(len(t), bool)
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
        da = ds[name]
        td, sd = time_dim(da), station_dim(da)
        ld = layer_dim(da, exclude=[td, sd])
        if ld is None or ld not in da.dims:
            return f"{source.upper()} his file has no vertical layers for '{name}'"
        names = _decode(ds["station_name"].values)
        if station not in names:
            if station:
                return f"Station '{station}' is not in the {source.upper()} his file"
            station = names[0]
        j, tidx = names.index(station), self.time_index(source, when)
        vals = np.asarray(da.isel({td: tidx, sd: j}).values, float)
        n = len(vals)
        depth = None
        # Độ sâu tâm lớp: HYD his có zcoordinate_c (cao độ tâm lớp) và waterlevel
        zds = ds if "zcoordinate_c" in ds.variables else self.ds("hyd_his")
        if zds is not None and "zcoordinate_c" in zds.variables:
            try:
                znames = _decode(zds["station_name"].values)
                jz = znames.index(station)
                tz = self.time_index("hyd", self.times(source)[tidx])
                z = np.asarray(zds["zcoordinate_c"].isel(time=tz, stations=jz).values, float)
                wl = float(zds["waterlevel"].isel(time=tz, stations=jz).values) if "waterlevel" in zds.variables else float(np.nanmax(z))
                d = wl - z  # HYD: đáy -> mặt
                depth = d if source == "hyd" else d[::-1]
            except Exception:
                depth = None
        if depth is None or len(depth) != n:
            frac = (np.arange(n) + 0.5) / n
            depth = (1 - frac) * 100 if source == "hyd" else frac * 100
            unit_depth = "% depth"
        else:
            unit_depth = "m"
        order = np.argsort(depth)
        times = self.times(source)
        return {"station": station, "time": _iso(times[tidx]), "depth": [_r(depth[i], 2) for i in order],
            "values": [_r(vals[i], 4) for i in order], "depth_unit": unit_depth, "from": "his",
            "source": source, "name": name, **var_meta(source, name)}

    def map_name_for(self, source: str, his_name: str) -> str | None:
        """Tên biến trong file map tương ứng với biến his."""
        ds = self.ds(f"{source}_map")
        if ds is None: return None
        cands = [HIS_TO_MAP.get(his_name)] if source == "hyd" else []
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
        vals = np.asarray(da.isel({td: tidx, fd: i}).values, float).ravel()
        n = len(vals)
        frac = (np.arange(n) + 0.5) / n                 # tâm lớp, lớp đều (sigma)
        rel = (1 - frac) if source == "hyd" else frac   # HYD: đáy->mặt; WAQ: mặt->đáy
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
        """Profile thẳng đứng tại trạm.
        1) Từ file his nếu biến có chiều lớp và trạm có trong file his của nguồn đó.
        2) Nếu không (vd. WAQ: wqPreparation chỉ đặt MỘT segment lớp mặt cho mỗi điểm quan trắc,
           nên WAQ his không có chiều lớp), lấy cột giá trị từ file map tại ô lưới gần trạm nhất.
        3) Không lấy được -> {"empty": True, "reason": ...} (không phải lỗi).
        """
        res = self._profile_his(source, name, station, when)
        if isinstance(res, dict): return res
        alt = self._profile_map(source, name, station, when)
        if isinstance(alt, dict): return alt
        return {"empty": True, "reason": alt or res, "station": station, "source": source, "name": name}

    # ------------------------------------------------------------ sources / sinks
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

    # ------------------------------------------------------------ info
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

def catalog(project_name: str) -> dict:
    """Mọi thứ frontend cần để dựng bộ chọn biến/thời gian/trạm."""
    pr = ProjectResults(project_name)
    info = pr.info()
    return {
        "project": project_name, "info": info, "has_results": pr.has_hyd or pr.has_waq,
        "map_variables": pr.map_variables("hyd") + pr.map_variables("waq"),
        "his_variables": pr.his_variables("hyd") + pr.his_variables("waq"),
        "stations": {"hyd": pr.stations("hyd"), "waq": pr.stations("waq")},
        "sources": pr.sources(), "version": pr.version(), 
        "times": {s: [_iso(t) for t in pr.times(s)] for s in ("hyd", "waq")},
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
