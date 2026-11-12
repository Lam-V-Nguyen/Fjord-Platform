"""
Dashboard functions
===================
Tổng hợp cho trang Overview, lấy KẾT QUẢ MÔ PHỎNG THỦY ĐỘNG LỰC (HYD) VÀ CHẤT LƯỢNG NƯỚC (WAQ)
làm nội dung chính (đọc qua dashboard_results.ProjectResults).

Gồm:
- Ngưỡng cảnh báo (backend/src/dashboard_thresholds.json), phân loại high/medium/low
- Kiểm kê dự án (pipeline: dữ liệu → lưới → HYD → WAQ → flow)
- summarize(): KPI, gauges, trạng thái chất lượng nước, nguồn/xả, checks, phân bố rủi ro
- Lớp bản đồ ngữ cảnh (ranh giới hồ, lưu vực, sông, độ sâu mẫu, lớp GIS của dự án)
- Cảnh báo từ checks

Các phần của mô hình Flow / tải dữ liệu / hiệu chỉnh KHÔNG nằm ở đây – chúng hiển thị trong
công cụ tương ứng (Workspace) và có thể thêm thành module dashboard riêng sau này.
"""
from __future__ import annotations
import glob, json, math, os, traceback
from datetime import datetime
from pyproj import Transformer
from functools import lru_cache
import numpy as np, pandas as pd
from config import PROJECT_ROOT, SOURCE_BACKEND
from services.dashboard_results import ProjectResults, select_layer, time_dim, station_dim, _decode

SAMPLE_DIR = os.path.normpath(os.path.join(SOURCE_BACKEND, "samples"))
FLOW_SAMPLE_DIR = os.path.normpath(os.path.join(SOURCE_BACKEND, "flow_samples"))
THRESHOLD_PATH = os.path.normpath(os.path.join(SOURCE_BACKEND, "dashboard_thresholds.json"))
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
PERIODS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "all": None}

# Helpers
def _layer_values(da, source, layer):
    if da is None: return None
    try: sel = select_layer(da, source, layer)
    except Exception: return None
    if sel is None: return None
    vals = np.asarray(sel.values, float)
    if vals.size == 0: return None
    return vals

def _round(value, n=3):
    try:
        v = float(value)
        return None if (math.isnan(v) or math.isinf(v)) else round(v, n)
    except Exception: return None

def _iso(ts) -> str | None:
    if ts is None: return None
    try: return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception: return None

def _mtime(path: str) -> str | None:
    try: return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception: return None

@lru_cache(maxsize=1)
def load_thresholds() -> dict:
    try:
        with open(THRESHOLD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"substances": data.get("substances", {}), "physics": data.get("physics", {})}
    except Exception:
        traceback.print_exc()
        return {"substances": {}, "physics": {}}

def reload_thresholds() -> dict:
    load_thresholds.cache_clear()
    return load_thresholds()

def classify(value, rule: dict) -> str:
    if value is None or rule is None: return "info"
    if rule.get("direction", "above") == "below":
        if value <= rule["high"]: return "high"
        if value <= rule["medium"]: return "medium"
        return "low"
    if value >= rule["high"]: return "high"
    if value > rule["medium"]: return "medium"
    return "low"

def find_rule(name: str) -> dict | None:
    subs = load_thresholds()["substances"]
    key = name.replace("mesh2d_2d_", "").replace("mesh2d_", "")
    if key in subs: return subs[key]
    low = key.lower()
    for k, v in subs.items():
        if k.lower() == low: return v
    return None

def limit_ratio(value, rule) -> float | None:
    if value is None or rule is None: return None
    if rule.get("direction") == "below":
        return _round(100 * rule["high"] / value, 1) if value else None
    return _round(100 * value / rule["high"], 1) if rule["high"] else None

def resolve_window(times: pd.DatetimeIndex, end: str | None, period: str | None):
    if not len(times): return None, None, 0
    t0, t1 = times[0], times[-1]
    try: end_ts = pd.Timestamp(end) if end else t1
    except Exception: end_ts = t1
    if len(str(end or "")) == 10: end_ts = end_ts + pd.Timedelta(hours=23, minutes=59)
    end_ts = min(max(end_ts, t0), t1)
    days = PERIODS.get(period or "7d", 7)
    start_ts = t0 - pd.Timedelta(seconds=1) if days is None else max(t0 - pd.Timedelta(seconds=1), end_ts - pd.Timedelta(days=days))
    span = (end_ts - start_ts).total_seconds() / 86400
    return start_ts, end_ts, round(span, 1)

# Project inventory
def project_dir(project_name: str) -> str:
    return os.path.normpath(os.path.join(PROJECT_ROOT, project_name))

def list_projects(user_dir_name: str) -> list[str]:
    root = os.path.normpath(os.path.join(PROJECT_ROOT, user_dir_name))
    if not os.path.isdir(root): return []
    return sorted(p.name for p in os.scandir(root) if p.is_dir())

def project_inventory(project_name: str) -> dict:
    """Quickly summarise from the project (don't open big files)."""
    pdir = project_dir(project_name)
    out = os.path.join(pdir, "output")
    hyd_dir, waq_dir = os.path.join(out, "HYD"), os.path.join(out, "WAQ")
    scen_dir, gis_dir = os.path.join(out, "scenarios"), os.path.join(pdir, "GIS")
    def _ls(folder, suffix):
        if not os.path.isdir(folder): return []
        return sorted(f for f in os.listdir(folder) if f.endswith(suffix))
    hyd_his, hyd_map = _ls(hyd_dir, "_his.zarr"), _ls(hyd_dir, "_map.zarr")
    waq_his = _ls(waq_dir, "_his.zarr")
    waq_scen = _ls(scen_dir, ".json")
    gis_layers = [f[:-8] for f in _ls(gis_dir, ".geojson")]
    flows_dir = os.path.join(pdir, "flows")
    flows = sorted(p.name for p in os.scandir(flows_dir) if p.is_dir()) if os.path.isdir(flows_dir) else []
    input_dir = os.path.join(pdir, "input")
    n_inputs = sum(len(fs) for _, _, fs in os.walk(input_dir)) if os.path.isdir(input_dir) else 0
    grid_files = glob.glob(os.path.join(pdir, "**", "*_net.nc"), recursive=True)
    stamps = [t for t in (_mtime(os.path.join(hyd_dir, f)) for f in hyd_his + hyd_map) if t]
    stamps += [t for t in (_mtime(os.path.join(waq_dir, f)) for f in waq_his) if t]
    pipeline = [
        {"key": "data", "label": "Input data", "done": n_inputs > 0, "tool": "preparation-hyd"},
        {"key": "grid", "label": "Grid", "done": len(grid_files) > 0 or len(hyd_map) > 0, "tool": "grid-generation"},
        {"key": "hyd", "label": "HYD run", "done": len(hyd_his) > 0, "tool": "run-hyd"},
        {"key": "waq", "label": "WAQ run", "done": len(waq_his) > 0, "tool": "run-waq"},
        {"key": "calibration", "label": "Calibration", "done": os.path.isdir(os.path.join(out, "calibration")), "tool": "calibration"},
    ]
    return {
        "exists": os.path.isdir(pdir),
        "hyd_results": len(hyd_his), "waq_results": len(waq_his),
        "waq_scenarios": len(waq_scen), "gis_layers": gis_layers,
        "flow_projects": flows, "input_files": n_inputs, "grids": len(grid_files),
        "pipeline": pipeline,
        "pipeline_progress": round(100 * sum(p["done"] for p in pipeline) / len(pipeline)),
        "last_result": max(stamps) if stamps else None,
    }

# Simulation summary
def _his_window(pr: ProjectResults, source: str, name: str, start, end):
    """DataArray (time, stations, [layer]) of *.his variables."""
    ds = pr.ds(f"{source}_his")
    if ds is None or name not in ds.data_vars:
        return None, None
    da = ds[name]; td = time_dim(da)
    if td is None: return None, None
    t = pr.times(source)
    if t is None or len(t) == 0: return None, None
    idx = np.where((t > start) & (t <= end))[0]
    if not len(idx): idx = np.array([len(t) - 1])
    return da.isel({td: idx}), t[idx]

def hyd_stats(pr: ProjectResults, start, end) -> dict:
    out = {}
    temp, tt = _his_window(pr, "hyd", "temperature", start, end)
    if temp is not None:
        surf, bott = _layer_values(temp, "hyd", "surface"), _layer_values(temp, "hyd", "bottom")
        if surf is not None:
            out["surface_temperature"] = _round(np.nanmean(surf[-1]), 2)
            out["surface_temperature_start"] = _round(np.nanmean(surf[0]), 2)
            out["max_surface_temperature"] = _round(np.nanmax(surf), 2)
        if bott is not None: out["bottom_temperature"] = _round(np.nanmean(bott[-1]), 2)
        if tt is not None and len(tt): out["temperature_time"] = _iso(tt[-1])
        if surf is not None and bott is not None and surf.shape == bott.shape:
            strat = surf - bott
            out["stratification"] = _round(np.nanmean(strat[-1]), 2)
            out["stratification_start"] = _round(np.nanmean(strat[0]), 2)
    wl, _ = _his_window(pr, "hyd", "waterlevel", start, end)
    if wl is not None:
        arr = np.asarray(wl.values, float)
        if arr.size:
            out["water_level"] = _round(np.nanmean(arr[-1]), 3)
            out["water_level_range"] = _round(np.nanmax(arr) - np.nanmin(arr), 3)
    vel, _ = _his_window(pr, "hyd", "velocity_magnitude", start, end)
    if vel is not None:
        arr = np.asarray(vel.values, float)
        if arr.size:
            out["max_velocity"] = _round(np.nanmax(arr), 3)
            out["mean_velocity"] = _round(np.nanmean(arr), 3)
        names_ds = pr.ds("hyd_his")
        names = _decode(names_ds["station_name"].values) if names_ds is not None and "station_name" in names_ds else None
        sd = station_dim(vel)
        if names is not None and sd is not None and arr.size:
            st_axis = list(vel.dims).index(sd)
            per_station = np.nanmax(np.moveaxis(arr, st_axis, 0).reshape(len(names), -1), axis=1)
            if np.isfinite(per_station).any():
                out["max_velocity_station"] = names[int(np.nanargmax(per_station))]
    elif (hm := pr.ds("hyd_map")) is not None and "mesh2d_ucmag" in hm.data_vars:
        tidx = pr.time_index("hyd", end)
        if tidx is not None:
            arr = np.asarray(hm["mesh2d_ucmag"].isel(time=tidx).values, float)
            if arr.size: out["max_velocity"] = _round(np.nanmax(arr), 3)
    sal, _ = _his_window(pr, "hyd", "salinity", start, end)
    if sal is not None:
        s = _layer_values(sal, "hyd", "surface")
        if s is not None: out["surface_salinity"] = _round(np.nanmean(s[-1]), 3)
    return out

def waq_checks(pr: ProjectResults, start, end) -> list[dict]:
    """Each substance has threshold: the worst value in the slide window, station, time, % above threshold"""
    ds = pr.ds("waq_his")
    if ds is None: return []
    names: list[str] = []
    if "station_name" in ds.variables:
        decoded = _decode(ds["station_name"].values)
        if decoded is not None: names = list(decoded)
    checks = []
    for var in ds.data_vars:
        var_name = str(var)
        rule = find_rule(var_name)
        if rule is None: continue
        da, tt = _his_window(pr, "waq", var_name, start, end)
        if da is None or tt is None: continue
        if len(tt) == 0: continue
        td, sd = time_dim(da), station_dim(da)
        if sd is None or td is None: continue
        other = [d for d in da.dims if d not in (td, sd)]
        below = rule.get("direction") == "below"
        red = (da.min(dim=other) if below else da.max(dim=other)) if other else da   # (t, st)
        arr = np.asarray(red.transpose(td, sd).values, float)
        if arr.size == 0 or not np.isfinite(arr).any(): continue
        flat = np.nanargmin(arr) if below else np.nanargmax(arr)
        ti, si = (int(x) for x in np.unravel_index(flat, arr.shape))
        if ti >= len(tt): continue
        worst = _round(arr[ti, si], 4)
        exceed = (arr <= rule["medium"]) if below else (arr > rule["medium"])
        last = arr[-1]
        last_worst = _round(np.nanmin(last) if below else np.nanmax(last), 4)
        pct = _round(100 * np.nanmean(np.any(exceed, axis=1)), 1)
        location = names[si] if 0 <= si < len(names) else f"Station {si + 1}"
        checks.append({
            "source": "waq", "key": var_name, "label": rule["label"], "unit": rule["unit"],
            "category": rule.get("category", "Water quality"), "value": worst,
            "current": last_worst, "location": location, "time": _iso(tt[ti]), 
            "severity": classify(worst, rule), "weight": rule.get("weight", 10), "exceed_pct": pct,
            "ratio": limit_ratio(worst, rule), "scenario": pr.waq_name,
            "threshold": {"medium": rule["medium"], "high": rule["high"], "direction": rule.get("direction", "above")},
            "detail": f"Exceeds medium limit {_round(100 * np.nanmean(np.any(exceed, axis=1)), 1)}% of the time",
        })
    return checks

def physics_checks(stats: dict) -> list[dict]:
    phys, out = load_thresholds()["physics"], []
    t = stats.get("max_surface_temperature")
    if t is not None and "surface_temperature" in phys:
        rule = phys["surface_temperature"]
        out.append({"source": "hyd", "key": "surface_temperature", "label": rule["label"], "unit": rule["unit"],
            "category": rule["category"], "value": t, "current": stats.get("surface_temperature"),
            "location": "Lake surface", "time": stats.get("temperature_time"),
            "severity": classify(t, rule), "weight": rule.get("weight", 10),
            "ratio": limit_ratio(t, rule), "threshold": {"medium": rule["medium"], "high": rule["high"], "direction": "above"}}
        )
    v = stats.get("max_velocity")
    if v is not None and "current_speed" in phys:
        rule = phys["current_speed"]
        out.append({"source": "hyd", "key": "current_speed", "label": rule["label"], "unit": rule["unit"],
            "category": rule["category"], "value": v, "location": stats.get("max_velocity_station", "Lake"),
            "time": stats.get("temperature_time"), "severity": classify(v, rule), "weight": rule.get("weight", 5),
            "ratio": limit_ratio(v, rule), "threshold": {"medium": rule["medium"], "high": rule["high"], "direction": "above"}}
        )
    return out

def water_quality_index(checks: list[dict]) -> dict:
    """Simple WQI index 0–100: substract by weight * penalty."""
    factor = {"high": 1.0, "medium": 0.45, "low": 0.0, "info": 0.0}
    worst: dict[str, dict] = {}
    for c in checks:
        if c["severity"] not in factor or not c.get("weight"): continue
        if c["key"] not in worst or SEVERITY_ORDER[c["severity"]] < SEVERITY_ORDER[worst[c["key"]]["severity"]]:
            worst[c["key"]] = c
    if not worst: return {"score": None, "label": "No WAQ data"}
    total_w = sum(c["weight"] for c in worst.values()) or 1
    penalty = sum(c["weight"] * factor[c["severity"]] for c in worst.values())
    score = max(0.0, 100.0 - 100.0 * penalty / total_w)
    label = "Excellent" if score >= 85 else "Good" if score >= 65 else "Fair" if score >= 45 else "Poor"
    return {"score": round(score), "label": label}

def risk_level(checks: list[dict]) -> dict:
    n_high = sum(c["severity"] == "high" for c in checks)
    n_med = sum(c["severity"] == "medium" for c in checks)
    if n_high >= 2: level = "High"
    elif n_high == 1 or n_med >= 3: level = "Moderate"
    elif n_med > 0: level = "Low–Moderate"
    else: level = "Low"
    return {"level": level, "high": n_high, "medium": n_med}

def _strat_label(dt):
    if dt is None: return "—"
    return "Stratified" if dt >= 3 else "Weak layering" if dt >= 1 else "Well mixed"

def summarize(project_name: str, end: str | None, period: str | None) -> dict:
    inventory = project_inventory(project_name)
    pr = ProjectResults(project_name); info = pr.info()
    base = {"project": project_name, "inventory": inventory, "simulation": info,
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }
    if not (pr.has_hyd or pr.has_waq):
        return {**base, "mode": "empty", "kpis": [], "checks": [], "contributions": [],
            "risk_distribution": {"high": 0, "medium": 0, "low": 0}, "gauges": {}, "quality": [],
            "data_source": "No simulation results"
        }
    times = pr.times("hyd") if pr.has_hyd else pr.times("waq")
    start, end_ts, days = resolve_window(times, end, period)
    stats = hyd_stats(pr, start, end_ts) if pr.has_hyd else {}
    checks = waq_checks(pr, start, end_ts) + physics_checks(stats)
    if not pr.has_waq:
        checks.append({"source": "model", "key": "no_waq", "label": "No WAQ results", "unit": "", "category": "Model",
            "value": None, "location": "Project", "time": None, "severity": "info", "weight": 0,
            "detail": "Create and run a WAQ scenario to evaluate water-quality thresholds."
        })
    if info["waq"].get("synthetic"):
        checks.append({"source": "model", "key": "synthetic", "label": "Synthetic demo results", "unit": "",
            "category": "Model", "value": None, "location": "Project", "time": None, "severity": "info",
            "weight": 0, "detail": "Generated by backend/tools/make_demo_results.py – not a real simulation."
        })
    wqi, risk = water_quality_index(checks), risk_level(checks)
    span = f"{days:g} d"
    def delta(a, b): return _round(a - b, 2) if (a is not None and b is not None) else None
    kpis = [
        {"key": "surface_temperature", "label": "Surface Temperature", "value": stats.get("surface_temperature"),
         "unit": "°C", "decimals": 1, "icon": "temperature", "delta": delta(stats.get("surface_temperature"), stats.get("surface_temperature_start")),
         "delta_unit": "°C", "delta_label": f"over {span}", "hint": "Mean of HYD stations, top layer"},
        {"key": "stratification", "label": "Stratification ΔT", "value": stats.get("stratification"), "unit": "°C",
         "decimals": 1, "icon": "thermo", "status": _strat_label(stats.get("stratification")),
         "hint": f"Surface {stats.get('surface_temperature', '—')}°C − bottom {stats.get('bottom_temperature', '—')}°C"},
        {"key": "water_level", "label": "Water Level", "value": stats.get("water_level"), "unit": "m", "decimals": 2,
         "icon": "level", "status": f"Range {stats.get('water_level_range', '—')} m", "hint": "Mean of HYD stations"},
        {"key": "max_velocity", "label": "Max Current Speed", "value": stats.get("max_velocity"), "unit": "m/s",
         "decimals": 3, "icon": "waves", "status": stats.get("max_velocity_station") or f"over {span}",
         "hint": f"Maximum over stations and layers, {span}"},
        {"key": "wqi", "label": "Water Quality Index", "value": wqi["score"], "unit": "", "decimals": 0,
         "status": wqi["label"], "icon": "quality", "hint": f"{sum(1 for c in checks if c['source'] == 'waq')} substances"},
        {"key": "risk", "label": "Risk Level (Overall)", "value": risk["level"], "unit": "",
         "status": f"{risk['high']} high · {risk['medium']} medium", "icon": "risk",
         "severity": "high" if risk["level"] == "High" else "medium" if "Moderate" in risk["level"] else "low"},
    ]
    quality = sorted([c for c in checks if c.get("ratio") is not None],
        key=lambda c: (SEVERITY_ORDER[c["severity"]], -(c["ratio"] or 0))
    )
    critical = [c for c in quality if c["source"] == "waq"][:2]
    gauges = {
        "wqi": {"value": wqi["score"], "label": wqi["label"]},
        "stratification": {"value": stats.get("stratification"), "surface": stats.get("surface_temperature"),
            "bottom": stats.get("bottom_temperature"), "label": _strat_label(stats.get("stratification"))},
        "critical": [{"label": c["label"], "value": c["value"], "unit": c["unit"], "severity": c["severity"],
            "location": c["location"], "key": c["key"]} for c in critical],
    }
    hyd_label = info["hyd"]["scenario"] or "—"
    waq_label = info["waq"]["scenario"] or "no WAQ"
    return {
        **base, "mode": "simulation",
        "data_source": f"HYD {hyd_label} · WAQ {waq_label}" + (" (synthetic)" if info["waq"].get("synthetic") else ""),
        "window": {"start": _iso(start), "end": _iso(end_ts), "days": days, "period": period or "7d"},
        "range": {"start": _iso(times[0]) if len(times) else None, "end": _iso(times[-1]) if len(times) else None},
        "kpis": kpis, "gauges": gauges, "stats": stats,
        "contributions": pr.source_discharge(start, end_ts) if pr.has_hyd else [], "quality": quality,
        "checks": sorted(checks, key=lambda c: (SEVERITY_ORDER.get(c["severity"], 9), c["label"])),
        "risk_distribution": {sev: sum(c["severity"] == sev for c in checks) for sev in ("high", "medium", "low")},
    }

def alerts_from_checks(checks: list[dict], limit: int = 20) -> list[dict]:
    alerts = []
    for c in checks:
        if c["severity"] not in ("high", "medium", "info"): continue
        msg = f"{c['label']}: {c['value']} {c['unit']}".strip() if c["value"] is not None else c["label"]
        alerts.append({
            "id": f"{c['source']}-{c['key']}-{c.get('location')}", "severity": c["severity"], "title": msg,
            "category": c["category"], "location": c.get("location"), "time": c.get("time"),
            "detail": c.get("detail"), "scenario": c.get("scenario"), "key": c["key"], "source": c["source"],
        })
    alerts.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], -(pd.Timestamp(a["time"]).value if a["time"] else 0)))
    return alerts[:limit]

# Context map layers (boundary, catchment, river, sample depth, project GIS)
def _transformer_for(crs_name: str | None):
    if not crs_name or "CRS84" in crs_name or crs_name.endswith("4326"): return None
    try: return Transformer.from_crs(f"EPSG:{crs_name.split(':')[-1]}", "EPSG:4326", always_xy=True)
    except Exception:
        traceback.print_exc()
        return None

def _transform_coords(coords, tf, step=1):
    if not coords: return coords
    if isinstance(coords[0], (int, float)):
        x, y = coords[0], coords[1]
        if tf is not None: x, y = tf.transform(x, y)
        return [round(x, 6), round(y, 6)]
    if isinstance(coords[0][0], (int, float)) and step > 1 and len(coords) > 8:
        thinned = coords[::step]
        if thinned[-1] != coords[-1]: thinned.append(coords[-1])
        coords = thinned
    return [_transform_coords(c, tf, step) for c in coords]

def read_geojson(path: str, step: int = 1) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            gj = json.load(f)
        tf = _transformer_for((gj.get("crs") or {}).get("properties", {}).get("name"))
        for feat in gj.get("features", []):
            geom = feat.get("geometry")
            if geom and geom.get("coordinates") is not None:
                geom["coordinates"] = _transform_coords(geom["coordinates"], tf, step)
        gj.pop("crs", None)
        return gj
    except Exception:
        traceback.print_exc()
        return None

@lru_cache(maxsize=8)
def sample_geojson(name: str) -> dict | None:
    steps = {"catchment": 3, "river": 2, "BrusdalLake": 1}
    return read_geojson(os.path.join(FLOW_SAMPLE_DIR, f"{name}.geojson"), steps.get(name, 1))

@lru_cache(maxsize=1)
def load_depth_points(max_points: int = 4000) -> list[list[float]]:
    try:
        df = pd.read_csv(os.path.join(SAMPLE_DIR, "depth.csv"), encoding="utf-8-sig").dropna()
        if len(df) > max_points:
            df = df.sample(max_points, random_state=7)
        df["Depth"] = df["Depth"].abs()
        return df[["Lat", "Lon", "Depth"]].round(5).values.tolist()
    except Exception:
        traceback.print_exc()
        return []

def build_layers(project_name: str, inventory: dict) -> dict:
    layers = {
        "lake": sample_geojson("BrusdalLake"), "catchment": sample_geojson("catchment"),
        "rivers": sample_geojson("river"), "bathymetry": load_depth_points(), "gis": {},
    }
    gis_dir = os.path.join(project_dir(project_name), "GIS")
    for name in inventory.get("gis_layers", []):
        gj = read_geojson(os.path.join(gis_dir, f"{name}.geojson"))
        if gj: layers["gis"][name] = gj
    return layers
