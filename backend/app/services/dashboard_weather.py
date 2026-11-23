"""
Dashboard weather
=================
REAL-TIME weather data for the Overview page (independent of the simulation results).

Sources (all free):
    frost      MET Norway Frost API – MEASUREMENTS at the nearest station, hourly, ~1 h delay.
               Needs a free client ID (environment variable MET_ProstAPI_CLIENT_ID – already used by Data Download).
    metno      MET Norway Locationforecast 2.0 – point FORECAST, hourly (first ~60 h), updated ~every hour.
               No key needed; requires an identifying User-Agent and coordinates with at most 4 decimals.
    openmeteo  Open-Meteo – model (analysis + forecast), hourly, global, no key needed.
    SEVERAL sources can be selected at once (overlaid); default: frost + metno with a client ID, otherwise -> metno.

Normalised variables: temperature (°C), precipitation (mm/h), pressure (hPa, reduced to sea level), humidity (%).
Returned times: ISO UTC ('...Z'); the frontend converts them to the user's time zone.
Unified precipitation convention: the value at hour t = precipitation of the PREVIOUS hour (t-1 -> t) – as in Frost & Open-Meteo;
Locationforecast (next hour) is shifted by +1 h.
In-memory cache per (source, location) with a source-specific lifetime -> frequent frontend refreshes
do not call the external APIs excessively (MET Norway / Open-Meteo terms of use).
Frost station: the NEAREST station with measurements (no distance limit; the WEATHER_STATION_COUNT nearest stations are checked).
Data is always available: no Frost station, a station without measurements, or a variable
without measurements -> a fallback source is used automatically (WEATHER_FALLBACK_ORDER) and noted in 'fallback' / 'site'.
"""
from __future__ import annotations
import os, threading, time, requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import numpy as np
from services.dashboard_results import ProjectResults
from services import constants


_CACHE: dict[tuple, tuple[float, float, dict]] = {}    # key -> (fetched_at, ttl, payload)
_LOCK = threading.Lock()

# helpers
def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _parse_time(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    if len(s) == 16:                      # Open-Meteo: 'YYYY-MM-DDTHH:MM' (GMT)
        s += ":00+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _num(v):
    try:
        f = float(v)
        return None if not np.isfinite(f) else round(f, 2)
    except (TypeError, ValueError):
        return None

def frost_client_id() -> str:
    return (os.getenv("MET_ProstAPI_CLIENT_ID") or "").strip()

def available_sources() -> list[dict]:
    out = []
    for key, s in constants.WEATHER_SOURCES.items():
        ok = key != "frost" or bool(frost_client_id())
        out.append({"key": key, "label": s["label"], "kind": s["kind"], "available": ok,
            "note": "" if ok else "needs MET_ProstAPI_CLIENT_ID"}
        )
    return out

def project_location(project_name: str) -> dict | None:
    """Representative coordinates of the model area: mean of the HYD (or WAQ) stations, otherwise the mesh centre."""
    pr = ProjectResults(project_name)
    for src in ("hyd", "waq"):
        try:
            pts = [(s["lon"], s["lat"]) for s in pr.stations(src) if s.get("lat") is not None and s.get("lon") is not None]
        except Exception:
            pts = []
        if pts:
            lon, lat = np.mean(np.array(pts, float), axis=0)
            return {"lon": round(float(lon), 4), "lat": round(float(lat), 4), "basis": f"mean of {len(pts)} {src.upper()} stations"}
    try:
        mesh = pr.mesh()
        if mesh:
            cx = np.array([np.nan if v is None else v for v in mesh["centroids"][0]], float)
            cy = np.array([np.nan if v is None else v for v in mesh["centroids"][1]], float)
            if np.isfinite(cx).any():
                return {"lon": round(float(np.nanmean(cx)), 4), "lat": round(float(np.nanmean(cy)), 4), "basis": "model grid centre"}
    except Exception:
        pass
    return None

def _empty_series() -> dict:
    return {v["key"]: {"x": [], "y": []} for v in constants.WEATHER_VARIABLES}

def _finish(series: dict, now: datetime, extra: dict) -> dict:
    """Add the 'current' value (latest value <= now) for each variable."""
    current = {}
    for key, s in series.items():
        best = None
        for x, y in zip(s["x"], s["y"]):
            if y is None:
                continue
            t = _parse_time(x)
            if t <= now + timedelta(minutes=5):
                best = (x, y)
        if best is None:               # forecast only -> first value
            best = next(((x, y) for x, y in zip(s["x"], s["y"]) if y is not None), None)
        current[key] = {"time": best[0], "value": best[1]} if best else {"time": None, "value": None}
    return {"series": series, "current": current, **extra}

def _distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance (km) from the model area to the station."""
    if None in (lat1, lon1, lat2, lon2): return None
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return round(float(2 * 6371.0 * np.arcsin(np.sqrt(a))), 1)

# MET Norway Frost (observations)
def _fetch_frost(lat: float, lon: float, now: datetime) -> dict:
    cid = frost_client_id()
    if not cid:
        raise RuntimeError("MET_ProstAPI_CLIENT_ID is not set")
    base = (os.getenv("MET_ProstAPI_URL") or constants.WEATHER_SOURCES["frost"]["url"]).rstrip("/")
    auth, headers = (cid, ""), {"User-Agent": constants.WEATHER_USER_AGENT}
    r = requests.get(f"{base}/sources/v0.jsonld", auth=auth, headers=headers, 
        timeout=constants.WEATHER_TIMEOUT, params={
        "types": "SensorSystem", "geometry": f"nearest(POINT({lon:.4f} {lat:.4f}))",
        "nearestmaxcount": constants.WEATHER_STATION_COUNT, "validtime": "now"}
    )
    r.raise_for_status(); stations = []
    for s in r.json().get("data", []):
        coords = (s.get("geometry") or {}).get("coordinates") or [None, None]
        st_lon, st_lat = _num(coords[0]), _num(coords[1])
        dist = _distance_km(lat, lon, st_lat, st_lon)          # computed here; Frost 'distance' is only used when coordinates are missing
        stations.append({"id": s["id"], "name": s.get("shortName") or s.get("name", s["id"]),
            "distance_km": dist if dist is not None else _num(s.get("distance")),
            "lon": st_lon, "lat": st_lat, "elevation": _num(s.get("masl"))})
    if not stations: raise RuntimeError("No Frost station found")
    # Nearest first – no distance limit
    stations.sort(key=lambda s: s["distance_km"] if s["distance_km"] is not None else float("inf"))
    start = (now - timedelta(hours=constants.WEATHER_PAST_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(f"{base}/observations/v0.jsonld", auth=auth, headers=headers, 
        timeout=constants.WEATHER_TIMEOUT, params={
        "sources": ",".join(s["id"] for s in stations), "referencetime": f"{start}/{_iso(now)}",
        "elements": ",".join(constants.FROST_ELEMENTS.values()), "timeresolutions": "PT1H"}
    )
    if r.status_code in (404, 412):
        f = stations[0]
        raise RuntimeError(f"No recent hourly observations at the {len(stations)} nearest Frost stations "
            f"(nearest: {f['name']}, {f['distance_km']} km)"
        )
    r.raise_for_status()
    # {element: {station_id: {time: value}}}
    raw: dict[str, dict[str, dict[str, float]]] = {}
    for item in r.json().get("data", []):
        sid, t = item["sourceId"].split(":")[0], item["referenceTime"]
        for ob in item.get("observations", []):
            if ob.get("timeOffset", "PT0H") != "PT0H": continue
            raw.setdefault(ob["elementId"], {}).setdefault(sid, {}).setdefault(t, ob.get("value"))
    series, used = _empty_series(), {}
    for key, element in constants.FROST_ELEMENTS.items():
        by_station = raw.get(element, {})
        st = next((s for s in stations if by_station.get(s["id"])), None)     # nearest station WITH measurements
        if st is None: continue
        pts = sorted(by_station[st["id"]].items())
        series[key] = {"x": [_iso(_parse_time(t)) for t, _ in pts], "y": [_num(v) for _, v in pts]}
        used[key] = st
    if not used: raise RuntimeError("No hourly observations of the selected elements nearby")
    return {"series": series, "stations": used}

# MET Norway Locationforecast (forecast)
def _fetch_metno(lat: float, lon: float, now: datetime) -> dict:
    r = requests.get(os.getenv('MET_NO'), timeout=constants.WEATHER_TIMEOUT,
        headers={"User-Agent": constants.WEATHER_USER_AGENT}, params={"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}
    )
    r.raise_for_status(); ttl = None
    try:
        expires = parsedate_to_datetime(r.headers.get("Expires"))
        ttl = max(600.0, (expires - datetime.now(timezone.utc)).total_seconds())
    except Exception: pass
    js = r.json(); props = js["properties"]
    g = (js.get("geometry") or {}).get("coordinates") or [lon, lat, None]
    point = {"lon": _num(g[0]), "lat": _num(g[1]), "elevation": _num(g[2]) if len(g) > 2 else None}
    series = _empty_series()
    stop = now + timedelta(hours=constants.WEATHER_FORECAST_HOURS)
    for step in props.get("timeseries", []):
        t = _parse_time(step["time"])
        nxt = step["data"].get("next_1_hours")
        if t > stop or nxt is None:          # hourly part only
            continue
        d = step["data"]["instant"]["details"]; x = _iso(t)
        for key, val in (("temperature", d.get("air_temperature")),
            ("pressure", d.get("air_pressure_at_sea_level")),
            ("humidity", d.get("relative_humidity"))):
            series[key]["x"].append(x); series[key]["y"].append(_num(val))
        # next_1_hours = precipitation for hour t -> t+1: assigned to the end of the interval (t+1), same convention as Frost/Open-Meteo
        series["precipitation"]["x"].append(_iso(t + timedelta(hours=1)))
        series["precipitation"]["y"].append(_num(nxt.get("details", {}).get("precipitation_amount")))
    return {"series": series, "ttl": ttl, "point": point, "model_updated": props.get("meta", {}).get("updated_at")}

#  Open-Meteo (model)
def _fetch_openmeteo(lat: float, lon: float, now: datetime) -> dict:
    hourly = "temperature_2m,precipitation,pressure_msl,relative_humidity_2m"
    r = requests.get(os.getenv('MET_FORECAST'), timeout=constants.WEATHER_TIMEOUT, 
        headers={"User-Agent": constants.WEATHER_USER_AGENT}, params={"latitude": f"{lat:.4f}", 
        "longitude": f"{lon:.4f}", "hourly": hourly, "past_days": max(1, constants.WEATHER_PAST_HOURS // 24),
        "forecast_days": max(1, constants.WEATHER_FORECAST_HOURS // 24) + 1, "timezone": "GMT"})
    r.raise_for_status(); js = r.json(); h = js["hourly"]; times = [_parse_time(t) for t in h["time"]]
    point = {"lat": _num(js.get("latitude")), "lon": _num(js.get("longitude")), "elevation": _num(js.get("elevation"))}
    keep = [i for i, t in enumerate(times) 
        if now - timedelta(hours=constants.WEATHER_PAST_HOURS) <= t <= now + timedelta(hours=constants.WEATHER_FORECAST_HOURS)
    ]
    x = [_iso(times[i]) for i in keep]
    mapping = {"temperature": "temperature_2m", "precipitation": "precipitation",
        "pressure": "pressure_msl", "humidity": "relative_humidity_2m"
    }
    return {"series": {k: {"x": x, "y": [_num(h[v][i]) for i in keep]} for k, v in mapping.items()}, "point": point}

FETCHERS = {"frost": _fetch_frost, "metno": _fetch_metno, "openmeteo": _fetch_openmeteo}

def default_sources() -> list[str]:
    """Default: measurements + MET forecast (comparison) with a Frost client ID, otherwise MET forecast only."""
    return ["frost", "metno"] if frost_client_id() else ["metno"]

def _source_meta(src: str) -> dict:
    s = constants.WEATHER_SOURCES[src]
    return {"key": src, "label": s["label"], "kind": s["kind"], 
        "resolution": s["resolution"], "license": s["license"], "url": s["url"]
    }

def _fetch_cached(src: str, loc: dict, force: bool) -> dict:
    """One source: data from the cache (still valid) or an API call. Error -> {'status': 'error', 'message'}."""
    key = (src, round(loc["lat"], 3), round(loc["lon"], 3))
    with _LOCK: hit = _CACHE.get(key)
    age = time.time() - hit[0] if hit else None
    if hit and (age < hit[1] if not force else age < 60):
        return {"status": "ok", "source": _source_meta(src), **hit[2]}
    now = _utc_now()
    try: res = FETCHERS[src](loc["lat"], loc["lon"], now)
    except Exception as e: return {"status": "error", "source": _source_meta(src), "message": str(e)}
    ttl = res.pop("ttl", None) or constants.WEATHER_SOURCES[src]["ttl"]
    series = res.pop("series")
    payload = _finish(series, now, {"fetched_at": _iso(now), "ttl": int(ttl), "now": _iso(now), **res})
    with _LOCK:
        _CACHE[key] = (time.time(), ttl, payload)
        if len(_CACHE) > 64:
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:16]:
                _CACHE.pop(k, None)
    return {"status": "ok", "source": _source_meta(src), **payload}

def _has_value(res: dict, var: str) -> bool:
    return res.get("status") == "ok" and (res.get("current", {}).get(var) or {}).get("value") is not None

def get_weather(project_name: str, sources: list[str] | None = None, force: bool = False) -> dict:
    """sources: sources chosen by the user (1 = single, several = overlaid). None -> default.
    Also returns:
      shown    : sources actually displayed (= selected + fallback sources added automatically when needed)
      fallback : {"from", "to", "reason"} when there are no station measurements (or a variable is missing) -> fallback source used
      current  : current value of each variable with its source (station measurements first, else the fallback source)
      site     : where the data comes from – Frost station (name, id, coordinates, distance) or the model grid point"""
    loc = project_location(project_name)
    base = {"variables": constants.WEATHER_VARIABLES, "sources": available_sources(), "server_now": _iso(_utc_now())}
    if loc is None:
        return {"status": "error", "message": "No model location (run HYD/WAQ to get station coordinates).", **base}
    avail = {s["key"] for s in base["sources"] if s["available"]}
    selected = [s for s in (sources or default_sources()) if s in FETCHERS and s in avail] or default_sources()
    results = {src: _fetch_cached(src, loc, force) for src in selected}
    ok = [src for src in selected if results[src]["status"] == "ok"]
    model_ok = [s for s in ok if s != "frost"]
    frost = results.get("frost")
    var_keys = [v["key"] for v in constants.WEATHER_VARIABLES]
    missing = [v for v in var_keys if frost and frost["status"] == "ok" and not _has_value(frost, v)]
    # Fallback needed: no source worked, the Frost station failed / has no measurements, or a variable is missing
    need = not ok or (frost is not None and frost["status"] != "ok") or bool(missing)
    fallback, shown = None, list(ok)
    if need:
        fb = model_ok[0] if model_ok else None
        if fb is None:
            for cand in constants.WEATHER_FALLBACK_ORDER:
                if cand in results: continue                      # already tried and failed
                res = _fetch_cached(cand, loc, force)
                results[cand] = {**res, "auto": True}
                if res["status"] == "ok":
                    fb = cand; shown.append(cand); break
        if fb is not None and (frost is not None or not ok):
            reason = (frost["message"] if frost is not None and frost["status"] != "ok" else
                f"No station observations of: {', '.join(missing)}" if missing else
                " | ".join(r.get("message", "") for r in results.values() if r["status"] != "ok")
            )
            fallback = {"from": "frost" if frost is not None else None, "to": fb, "reason": reason, "variables": missing or var_keys}
    if not shown:
        msg = " | ".join(f"{constants.WEATHER_SOURCES[k]['label']}: {r['message']}" for k, r in results.items())
        return {"status": "error", "message": f"Weather data unavailable – {msg}",
            "location": loc, "selected": selected, "results": results, **base
        }
    # Current value: station measurements first, else the first model source with a value
    order, current = (["frost"] if "frost" in shown else []) + [s for s in shown if s != "frost"], {}
    for v in var_keys:
        src = next((s for s in order if _has_value(results[s], v)), None)
        if src is None: current[v] = {"time": None, "value": None, "source": None}; continue
        c = results[src]["current"][v]
        current[v] = {**c, "source": src, "station": (results[src].get("stations") or {}).get(v)}
    primary = order[0]
    # Data location for the subtitle line
    if "frost" in shown and results["frost"].get("stations"):
        st = results["frost"]["stations"]
        main = st.get("temperature") or next(iter(st.values()))
        others = {x["id"]: x for x in st.values() if x["id"] != main["id"]}
        site = {"kind": "station", **main, "others": list(others.values()),
            "per_variable": {k: {"id": x["id"], "name": x["name"], "distance_km": x["distance_km"]} for k, x in st.items()}
        }
    else:
        src = primary; pt = results[src].get("point") or {"lat": loc["lat"], "lon": loc["lon"]}
        site = {"kind": "model", "source": src, "label": constants.WEATHER_SOURCES[src]["label"], **pt}
    return {"status": "ok", "location": loc, "selected": selected, "shown": order, "primary": primary,
        "fallback": fallback, "current": current, "site": site, "results": results, **base
    }