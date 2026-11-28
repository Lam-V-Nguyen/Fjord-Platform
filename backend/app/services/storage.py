"""
Per-user storage quota.

Usage of a user = size of PROJECT_ROOT/<username> (all own projects; a shared project counts for its OWNER).
- Quota: USER_QUOTA_GB in .env (0 = unlimited), per-user override in DATA_DIR/quotas.json (set on the /admin page).
  Administrators are unlimited unless an override is set for them.
- Above QUOTA_WARN_PERCENT: a warning banner in the browser. At 100 %: routes that ADD data (start a simulation,
  upload, download, create / copy a project ...) answer 507 until old scenarios / projects are deleted.
  Deleting, viewing and editing small settings stay allowed.
- Folder sizes are cached (TTL) and refreshed in a background thread, so a request never waits for a large
  folder walk except the very first time.
"""
import os, json, threading, time
from fastapi import HTTPException
from config import PROJECT_ROOT, DATA_DIR, USER_QUOTA_GB, QUOTA_WARN_PERCENT

GB = 1024 ** 3
TTL = 60                                   # seconds a measured size is considered fresh
_QUOTA_FILE = os.path.join(DATA_DIR, "quotas.json")
_cache: dict[str, tuple[float, int]] = {}  # owner -> (measured at, bytes)
_refreshing: set[str] = set()
_lock = threading.Lock()

# Routes that add data to the project folder: blocked when the owner's quota is full
GROWTH_PREFIXES = ("/start_", "/upload_", "/download_")
GROWTH_SUFFIXES = ("_upload",)
GROWTH_ROUTES = {
    "/setup_new_project", "/copy_project", "/clone_waq", "/generate_mdu", "/create_baseline_calibration",
    "/save_scenario_calibration", "/finish_calibration", "/wflow_model", "/hyd_weather_download", "/clip_dem",
    "/catchment_definer", "/grid_saver", "/grid_interpolation", "/save_era5", "/save_weather", "/save_weather_csv",
    "/save_forcing_weather", "/waq_config_writer", "/river_saver",
}
NOT_GROWTH = {"/download_station"}         # returns the data to the browser, stores nothing

def is_growth_route(path: str) -> bool:
    if path in NOT_GROWTH: return False
    return path in GROWTH_ROUTES or path.startswith(GROWTH_PREFIXES) or path.endswith(GROWTH_SUFFIXES)

# ---------------------------------------------------------------- sizes
def folder_size(path: str) -> int:
    total, stack = 0, [path]
    while stack:
        try: it = os.scandir(stack.pop())
        except OSError: continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False): stack.append(e.path)
                    elif e.is_file(follow_symlinks=False): total += e.stat(follow_symlinks=False).st_size
                except OSError: pass
    return total

def _measure(owner: str) -> int:
    size = folder_size(os.path.join(PROJECT_ROOT, owner))
    with _lock:
        _cache[owner] = (time.time(), size); _refreshing.discard(owner)
    return size

def usage(owner: str) -> int:
    """Bytes used by `owner`. Fresh value from the cache; a stale value is returned at once and refreshed in the
    background; only the first call for an owner measures synchronously."""
    with _lock:
        hit = _cache.get(owner)
        if hit and time.time() - hit[0] < TTL: return hit[1]
        if hit and owner in _refreshing: return hit[1]
        if hit: _refreshing.add(owner)
    if hit is None: return _measure(owner)
    threading.Thread(target=_measure, args=(owner,), daemon=True, name=f"storage-{owner}").start()
    return hit[1]

def invalidate(owner: str | None = None) -> None:
    """Forget the cached size (after deleting data) so the next check measures again."""
    with _lock:
        if owner is None: _cache.clear()
        elif owner in _cache: _cache[owner] = (0.0, _cache[owner][1])     # stale -> refreshed on next use

# ---------------------------------------------------------------- quotas
def _overrides() -> dict:
    try:
        with open(_QUOTA_FILE, encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError): return {}

def quota_gb(owner: str, is_admin: bool = False) -> float | None:
    """Quota in GB, None = unlimited."""
    over = _overrides()
    if owner in over:
        v = over[owner]
        return None if v is None or float(v) <= 0 else float(v)
    if is_admin or USER_QUOTA_GB <= 0: return None
    return USER_QUOTA_GB

def set_quota(owner: str, gb) -> None:
    """gb: number (GB), 0 = unlimited, None / '' = back to the default of .env."""
    with _lock:
        over = _overrides()
        if gb is None or gb == "": over.pop(owner, None)
        else: over[owner] = max(0.0, float(gb))
        os.makedirs(os.path.dirname(_QUOTA_FILE), exist_ok=True)
        tmp = _QUOTA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(over, f, indent=2)
        os.replace(tmp, _QUOTA_FILE)

def _fmt(b: float) -> str:
    return f"{b / GB:.1f} GB" if b >= 0.1 * GB else f"{b / 1024 ** 2:.0f} MB"

def status(owner: str, is_admin: bool = False, viewer: str | None = None) -> dict:
    """{'used', 'quota', 'percent', 'level': ok|warn|full, 'message', 'override'} – sizes in bytes."""
    used, q = usage(owner), quota_gb(owner, is_admin)
    over = owner in _overrides()
    if q is None:
        return {"used": used, "quota": None, "percent": None, "level": "ok", "message": "", "override": over}
    quota = q * GB
    pct = 100.0 * used / quota if quota else 100.0
    who = "You are" if viewer in (None, owner) else f"The owner of this project ('{owner}') is"
    whose = "your" if viewer in (None, owner) else "their"
    if pct >= 100:
        level = "full"
        msg = (f"Storage quota reached – {who[0].lower() + who[1:]} using {_fmt(used)} of {whose} {_fmt(quota)}. New simulations, uploads and "
               "downloads are blocked. Delete old scenarios or projects you no longer need to free up space.")
    elif pct >= QUOTA_WARN_PERCENT:
        level = "warn"
        msg = (f"{who} using {_fmt(used)} of {whose} {_fmt(quota)} storage quota ({pct:.0f}%). "
               "Consider deleting old scenarios or projects you no longer need.")
    else: level, msg = "ok", ""
    return {"used": used, "quota": quota, "percent": round(pct, 1), "level": level, "message": msg, "override": over}

def check(owner: str, is_admin: bool = False, viewer: str | None = None) -> None:
    """507 when `owner` has no storage left (called for routes that add data)."""
    st = status(owner, is_admin, viewer)
    if st["level"] == "full": raise HTTPException(status_code=507, detail=st["message"])
