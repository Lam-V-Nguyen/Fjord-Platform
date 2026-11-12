from config import DELFT_PATH, PROJECT_ROOT
from services import functions
import os, glob, shutil, subprocess, pickle
import warnings, logging, optuna, re, json
from datetime import datetime, timedelta, timezone
from functools import partial
import numpy as np, pandas as pd, xarray as xr
from scipy.stats import qmc
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import KFold, LeaveOneOut, cross_val_predict
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import (
    RBF, Matern, RationalQuadratic, WhiteKernel, ConstantKernel as C
)
from optuna.importance import MeanDecreaseImpurityImportanceEvaluator
from sklearn.ensemble import RandomForestRegressor
warnings.filterwarnings('ignore', category=UserWarning)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)


HIS_VARS_TO_KEEP = {"wrihis_temperature", "wrihis_waterlevel_s1", "wrihis_zcor"}
SCENARIO_MDU, TARGET_METHODS = "FlowFM.mdu", ("RMSE", "R2")
SURROGATE_MODELS, COMPLETED_FLAG = ("gaussian-process",), ".completed"
FAILED_CASES_FILE, SCENARIO_OUTPUT_DIR = "failed_cases.json", "output"
STALE_RESULTS = (
    "scenarios", "summary", "model", "calibration_summary.csv",
    FAILED_CASES_FILE, "optuna.csv", "optuna_importance.csv",
)
OVERRIDES_FILE, FEATURES_FILE, FACTOR_SUFFIX = "base_overrides.json", "features.json", "_factor"
_ERROR_RE = re.compile(r"\*\*\s*error|forrtl:|^\s*error\s*:", re.IGNORECASE)
_INDEXED = re.compile(r"^(?P<key>[A-Za-z0-9_]+)\[(?P<i>\d+)\]$")
INTERVAL_KEYS = ("HisInterval", "MapInterval", "WaqInterval", "ClassMapInterval", "RstInterval")


# MDU helpers
def find_mdu(input_dir: str) -> str:
    files = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".mdu"))
    if not files: raise FileNotFoundError(f"MDU file not found in {input_dir}")
    return os.path.join(input_dir, files[0])

def load_overrides(calibration_dir: str) -> dict:
    path = os.path.join(calibration_dir, OVERRIDES_FILE)
    if not os.path.exists(path): return {}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return data.get("mdu", {}) if isinstance(data, dict) else {}

def modify_mdu_key(mdu_lines: list, key: str, value: str = '', max_length=None) -> list:
    mdu, k = mdu_lines.copy(), key.lower()
    for i, line in enumerate(mdu):
        p = _split_line(line)
        if not p or p[0].strip().lower() != k: continue
        key_part, value_part, has_comment, comment = p
        nl = '\n' if line.endswith('\n') else ''
        width = max((max_length - 2) if max_length else len(value_part) - 2, len(value))
        new = f"{key_part}= {value.ljust(width)}"
        if has_comment: new += f" #{comment.rstrip(chr(10)).rstrip(chr(13))}"
        mdu[i] = new.rstrip() + nl
        return mdu

def _get_mdu_tokens(mdu_content: list, key: str) -> list:
    return _get_values_from_mdu(mdu_content, key).split()

def apply_overrides(mdu_lines: list, overrides: dict) -> list:
    lines = mdu_lines
    for key, value in overrides.items():
        lines = modify_mdu_key(lines, key, str(value))
    if any(k.lower() in ("tstart", "tstop") for k in overrides):
        for k in INTERVAL_KEYS:
            toks = _get_mdu_tokens(lines, k)
            if len(toks) > 1: lines = modify_mdu_key(lines, k, toks[0])
    return lines

def _mdu_number(token) -> float:
    return float(str(token).strip().lower().replace("d", "e"))

def _get_values_from_mdu(mdu_content: list, key: str):
    k = key.lower()
    for line in mdu_content:
        p = _split_line(line)
        if p and p[0].strip().lower() == k: return p[1].strip()
    return ''

def mdu_time_info(mdu_lines: list) -> dict:
    ref = datetime.strptime(_get_values_from_mdu(mdu_lines, 'RefDate')[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    unit = (_get_values_from_mdu(mdu_lines, 'Tunit') or 'S').upper()[:1]
    sec = {'S': 1, 'M': 60, 'H': 3600, 'D': 86400}.get(unit, 1)
    start = ref + timedelta(seconds=_mdu_number(_get_values_from_mdu(mdu_lines, 'TStart')) * sec)
    stop = ref + timedelta(seconds=_mdu_number(_get_values_from_mdu(mdu_lines, 'TStop')) * sec)
    return {"ref": ref, "unit": unit, "unit_seconds": sec, "start": start, "stop": stop}

def get_all_mdu_keys(mdu_lines: list, include_section: bool = False) -> list:
    """
    Return every key of an MDU file as [key, value, comment] (or [section, key, value, comment]).
    - Section headers ([General], [physics], ...) are not returned as rows; with
      include_section=True each row starts with the section name.
    - Comment-only lines that follow a key (e.g. the option list under BedlevType)
      are appended to that key's comment.
    - Keys without a value (e.g. 'IniFieldFile =') are returned with value ''.
    """
    rows, section, last = [], '', None
    for raw in mdu_lines:
        line = raw.rstrip('\r\n')
        stripped = line.strip()
        if not stripped:
            last = None; continue
        if stripped.startswith('[') and ']' in stripped:          # [section]
            section, last = stripped[1:stripped.index(']')].strip(), None
            continue
        if stripped.startswith('#'):                              # comment-only line
            if last is not None:                                  # continuation of the previous key
                extra = stripped.lstrip('#').strip()
                if extra: last[-1] = f"{last[-1]} | {extra}" if last[-1] else extra
            continue
        if '=' not in stripped:
            last = None; continue
        key, rest = stripped.split('=', 1)
        value, _, comment = rest.partition('#')
        row = [key.strip(), value.strip(), comment.strip()]
        if include_section: row.insert(0, section)
        rows.append(row); last = row
    return rows

def clip_data(df: pd.DataFrame, time_column:str, start:str=None, end:str=None) -> dict:
    if df.empty: return {}
    content = {}
    if not start is None and not end is None:
        df = df[(df[time_column] >= start) & (df[time_column] <= end)]
    if df.empty: return {}
    start_time, end_time = df[time_column].iloc[0], df[time_column].iloc[-1]
    content['start'], content['end'] = start_time, end_time
    df = df.replace([np.inf, -np.inf], np.nan)
    content['data'] = df.astype(object).where(df.notna(), None).to_numpy().tolist()
    return content

def measured_utc(obs_data, time_zone: str, time_col: str = "Time") -> pd.DataFrame:
    df = pd.DataFrame(data=obs_data, columns=[time_col, "temperature", "depth"])
    df[time_col] = functions.local_to_utc(df[time_col], time_zone)
    return df.dropna(subset=[time_col])

def read_mdu(path: str) -> list:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.readlines()

def _profile_mean(prof: pd.DataFrame) -> float:
    if len(prof) == 1: return float(prof["temperature"].iloc[0])
    d, t = prof["depth"].to_numpy(float), prof["temperature"].to_numpy(float)
    if d[-1] <= d[0]: return float(t.mean())
    return float(np.sum((t[1:] + t[:-1]) / 2 * np.diff(d)) / (d[-1] - d[0]))

def _load_overrides_display(calibration_dir: str) -> list:
    path = os.path.join(calibration_dir, OVERRIDES_FILE)
    if not os.path.exists(path): return []
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return data.get("display", []) if isinstance(data, dict) else []

def _read_obs_stations(input_dir: str, mdu_lines: list) -> list:
    out = []
    for name in _get_values_from_mdu(mdu_lines, 'ObsFile').split():
        path = os.path.join(input_dir, name)
        if not os.path.exists(path): continue
        with open(path, encoding='utf-8', errors='ignore') as f:
            for line in f:
                parts = line.strip().split(None, 2)
                if len(parts) == 3: out.append(parts[2].strip().strip("'\""))
    return out

def read_and_interpolate_his(his_path: str, station_name: str, depths: list, time_col="Time") -> pd.DataFrame:
    try:
        if not his_path or not os.path.exists(his_path): return pd.DataFrame()
        names = get_station_from_his_file(his_path)
        if station_name not in names: return pd.DataFrame()
        st_idx = names.index(station_name)
        ds = xr.open_zarr(his_path) if his_path.endswith(".zarr") else xr.open_dataset(his_path)
        with ds:
            t_his = ds['temperature'].isel(stations=st_idx).values
            z_his = ds['zcoordinate_c'].isel(stations=st_idx).values
            wl_his = ds['waterlevel'].isel(stations=st_idx).values
            sim_times = pd.to_datetime(ds['time'].values, utc=True)
        depth_num = np.asarray(depths, dtype=float)
        n_steps = len(sim_times)
        sim_interp = np.full((n_steps, len(depths)), np.nan)
        for i in range(n_steps):
            t_row, z_row, wl = t_his[i, :], z_his[i, :], wl_his[i]
            tgt_elev = wl - depth_num
            mask = np.isfinite(t_row) & np.isfinite(z_row)
            if mask.sum() < 2: continue
            z_valid, t_valid = z_row[mask], t_row[mask]
            order = np.argsort(z_valid)
            z_sorted, t_sorted = z_valid[order], t_valid[order]
            in_bounds = (tgt_elev >= z_sorted.min()) & (tgt_elev <= z_sorted.max())
            sim_interp[i, in_bounds] = np.interp(tgt_elev[in_bounds], z_sorted, t_sorted)
        return pd.DataFrame(sim_interp, columns=[f"sim_{d}" for d in depths], index=pd.Index(sim_times, name=time_col))
    except Exception as e:
        logging.warning(f"Read_and_interpolate_his({his_path}, {station_name}): {e}")
        return pd.DataFrame()

def initial_check(project_dir: str, project_name: str, measured_utc: pd.DataFrame, station: str, time_zone: str) -> dict:
    input_dir = os.path.join(project_dir, "input")
    calibration_dir = os.path.join(project_dir, "calibration")
    lines = read_mdu(find_mdu(input_dir))
    info = mdu_time_info(lines)
    g = lambda k: _get_values_from_mdu(lines, k)
    def num(k, d=None):
        try: return _mdu_number(g(k).split()[0]) if g(k) else d
        except ValueError: return d
    init_t, kmx, dtuser = num('InitialTemperature'), int(num('Kmx', 0) or 0), num('DtUser')
    model = {
        "start": functions.utc_to_local(pd.Series([info["start"]]), time_zone)[0],
        "end": functions.utc_to_local(pd.Series([info["stop"]]), time_zone)[0],
        "initial_temperature": init_t, "temperature_model": g('Temperature'), "kmx": kmx, "layertype": g('Layertype'),
        "dtuser": dtuser, "anglat": num('AngLat'), "anglon": num('AngLon'), "secchidepth": num('Secchidepth'),
        "stanton": num('Stanton'), "dalton": num('Dalton'), "cdbreakpoints": g('Cdbreakpoints'),
        "windspeedbreakpoints": g('Windspeedbreakpoints'), "idensform": g('Idensform'), "tzone": g('Tzone'),
        "restartfile": g('RestartFile'), "inifieldfile": g('IniFieldFile'),
    }
    start_utc = pd.Timestamp(info["start"])
    t0, prof = _first_profile(measured_utc, start_utc)
    warns, suggestions, obs = [], [], None
    if t0 is not None and not prof.empty:
        mean_t = _profile_mean(prof)
        gap_days = (t0 - start_utc).total_seconds() / 86400
        obs = {"time": functions.utc_to_local(pd.Series([t0]), time_zone)[0], "gap_days": round(gap_days, 2), 
            "mean": round(mean_t, 3), "min": float(prof["temperature"].min()), 
            "max": float(prof["temperature"].max()), "max_depth": float(prof["depth"].max()), 
            "profile": [[float(d), round(float(t), 3)] for d, t in zip(prof["depth"], prof["temperature"])]}
        if gap_days > 3:
            warns.append(f"The first observed profile is {gap_days:.0f} days after the model start: the initial "
                f"temperature ({init_t}°C) is not constrained by observations.")
            t_start = pd.Timestamp(t0).tz_convert(time_zone).floor("D")
            suggestions.append({"key": "TStart", "value": t_start.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "Start at the first observed profile (local time). Remove this row to keep the spin-up period."})
        if init_t is None or abs(init_t - mean_t) > 0.3:
            warns.append(f"InitialTemperature = {init_t}°C but the first observed profile averages {mean_t:.2f}°C "
                f"(range {obs['min']:.2f} - {obs['max']:.2f}°C).")
            suggestions.append({"key": "InitialTemperature", "value": f"{mean_t:.2f}",
                "note": "Depth-averaged first observed profile"})
        if obs["max"] - obs["min"] > 1.0:
            warns.append("The first observed profile is stratified (>1°C top–bottom): a uniform initial temperature "
                "cannot represent it – use a 3D initial field (IniFieldFile) or a restart from a spin-up run.")
    else:
        warns.append("No observation after the model start time – upload observations first.")
    if model["temperature_model"].split()[:1] != ['5']:
        warns.append(f"Temperature = {model['temperature_model']}: use 5 (composite heat flux model) for lakes.")
    if dtuser and dtuser > 3600:
        warns.append(f"DtUser = {dtuser:g} s: meteorological forcing is only updated every {dtuser / 3600:g} h, "
            "which removes the daily heating/cooling cycle.")
        suggestions.append({"key": "DtUser", "value": "3600.", "note": "Update external forcing every hour"})
    if model["anglon"] in (0, 0.0) and model["anglat"]:
        lonlat = None
        try:
            from services.dashboard_results import ProjectResults
            lonlat = ProjectResults(project_name).station_lonlat(station, "hyd") if station else None
        except Exception: lonlat = None
        warns.append("AngLon = 0: the longitude is used for the solar position in the heat flux model.")
        suggestions.append({"key": "AngLon", "value": f"{lonlat[0]:.3f}" if lonlat else "",
            "note": "Longitude of the lake (deg E)" + ("" if lonlat else " – please fill in")})
    if kmx and kmx < 20:
        depth_hint = f" (observations go down to {obs['max_depth']:.0f} m)" if obs else ""
        warns.append(f"Kmx = {kmx} vertical layers{depth_hint}: too coarse for a stratified lake – "
            "numerical mixing carries surface heat downwards.")
        suggestions.append({"key": "Kmx", "value": "25", "note": "More vertical layers (longer run time)"})
    if model["restartfile"]:
        warns.append(f"RestartFile = {model['restartfile']} is removed in calibration runs – "
            "initial conditions then come from InitialTemperature only.")
    if obs and obs["mean"] < 4.0 and info["start"].month in (11, 12, 1, 2, 3, 4):
        warns.append(f"Cold winter/spring water: the model has no ice cover (Tempmin = {g('Tempmin')}). "
            "Starting the model after ice-off avoids heating an ice-covered lake.")
    existing = None
    his = sorted(glob.glob(os.path.join(project_dir, "output", "HYD", "*_his.zarr")) +
        glob.glob(os.path.join(project_dir, "output", "HYD", "*_his.nc")))
    if his and station and obs:
        depths = [f"{d:g}" for d, _ in obs["profile"]]
        sim = read_and_interpolate_his(his[0], station, depths)
        if not sim.empty:
            i = int(np.argmin(np.abs((sim.index - t0).total_seconds())))
            rows = []
            for (d, t_obs), dcol in zip(obs["profile"], depths):
                s = sim.iloc[i][f"sim_{dcol}"]
                ok = np.isfinite(s)
                rows.append([d, t_obs, round(float(s), 3) if ok else None, round(float(s) - t_obs, 3) if ok else None])
            existing = {"his": os.path.basename(his[0]),
                "time": functions.utc_to_local(pd.Series([sim.index[i]]), time_zone)[0], "rows": rows}
    return {"model": model, "obs": obs, "existing_run": existing, "warnings": warns,
        "suggestions": suggestions, "overrides": _load_overrides_display(calibration_dir),
        "stations": _read_obs_stations(input_dir, lines)}

def has_mdu_key(mdu_lines: list, key: str) -> bool:
    k = key.lower()
    for line in mdu_lines:
        p = _split_line(line)
        if p and p[0].strip().lower() == k: return True
    return False

def _to_mdu_time(value, mdu_lines: list, time_zone: str) -> str:
    s = str(value).strip()
    try: _mdu_number(s); return s
    except ValueError: pass
    info = mdu_time_info(mdu_lines)
    t = functions.local_to_utc(s, time_zone)
    if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
    return f"{(t - info['ref']).total_seconds() / info['unit_seconds']:.0f}."

def save_overrides(project_dir: str, rows: list, time_zone: str) -> dict:
    input_dir = os.path.join(project_dir, "input")
    calibration_dir = os.path.join(project_dir, "calibration")
    os.makedirs(calibration_dir, exist_ok=True)
    lines = read_mdu(find_mdu(input_dir))
    mdu, display, errors = {}, [], []
    for r in rows:
        key = str(r[0] or "").strip()
        value = str(r[1] if len(r) > 1 and r[1] is not None else "").strip()
        note = str(r[2]) if len(r) > 2 and r[2] else ""
        if not key: continue
        if not has_mdu_key(lines, key): errors.append(f"'{key}' is not in the MDU file"); continue
        if value == "": errors.append(f"'{key}' has no value"); continue
        try:
            if key.lower() in ("tstart", "tstop"): mdu_value = _to_mdu_time(value, lines, time_zone)
            else:
                if key.lower() not in ("restartfile", "inifieldfile"): 
                    [_mdu_number(t) for t in value.split()]
                mdu_value = value
        except Exception as e: errors.append(f"'{key}' = '{value}': {e}"); continue
        mdu[key] = mdu_value; display.append([key, value, note])
    if errors: return {"status": "error", "message": "; ".join(errors)}
    with open(os.path.join(calibration_dir, OVERRIDES_FILE), "w", encoding="utf-8") as f:
        json.dump({"mdu": mdu, "display": display}, f, indent=2, ensure_ascii=False)
    return {"status": "ok", "message": f"Saved {len(mdu)} override(s). They are applied to the baseline run, "
        "every calibration scenario and the final calibrated scenario."}

def scenario_run_status(scenario_dir: str) -> dict:
    """Which calibration scenarios finished (COMPLETED_FLAG) and which still have to be run."""
    ids = sorted(int(x) for x in os.listdir(scenario_dir)
        if x.isdigit() and os.path.isdir(os.path.join(scenario_dir, x))) if os.path.isdir(scenario_dir) else []
    done = [i for i in ids if os.path.exists(os.path.join(scenario_dir, str(i), COMPLETED_FLAG))]
    done_set = set(done)
    pending = [i for i in ids if i not in done_set]
    return {"total": len(ids), "first": ids[0] if ids else None, "last": ids[-1] if ids else None,
        "done": done, "pending": pending, "next": pending[0] if pending else None}

def find_his(case_dir: str):
    for pat in ("output/*_his.nc", "DFM_OUTPUT*/*_his.nc", "*/*_his.nc"):
        hits = sorted(glob.glob(os.path.join(case_dir, pat)))
        if hits: return hits[0]
    return None

def calibration_data_status(project_dir: str) -> dict:
    calibration_dir = os.path.join(project_dir, "calibration")
    mtime = lambda p: datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
    ov_path = os.path.join(calibration_dir, OVERRIDES_FILE)
    overrides = load_overrides(calibration_dir) if os.path.exists(ov_path) else {}
    case_dir = os.path.join(calibration_dir, "baseline", "scenarios", "0")
    his = find_his(case_dir) if os.path.isdir(case_dir) else None
    return {
        "overrides": {"exists": bool(overrides), "count": len(overrides), "keys": list(overrides),
            "modified": mtime(ov_path) if overrides else None},
        "baseline": {"exists": his is not None, "case_exists": os.path.isdir(case_dir),
            "his": os.path.basename(his) if his else None, "modified": mtime(his) if his else None},
    }

def copy_model_inputs(src_dir: str, dst_dir: str) -> None:
    os.makedirs(dst_dir, exist_ok=True)
    for f in os.listdir(src_dir):
        s = os.path.join(src_dir, f)
        if os.path.isfile(s):
            if not f.lower().endswith('.mdu'): shutil.copy(s, os.path.join(dst_dir, f))
        elif os.path.isdir(s) and not f.lower().startswith(('output', 'dfm_output', 'dfm_delwaq')):
            shutil.copytree(s, os.path.join(dst_dir, f), dirs_exist_ok=True)

def _calibration_output_settings(mdu_lines: list, his_interval: str, write_map: bool) -> list:
    lines = mdu_lines
    for key, val in (
        ('WaqInterval', '0'), ('RstInterval', '0'), ('RestartFile', ''),
        ('HisInterval', str(his_interval)), ('OutputDir', 'output'),
        ('MapInterval', str(his_interval) if write_map else '0')):
        if has_mdu_key(lines, key): lines = modify_mdu_key(lines, key, val)
    keep = {'wrihis_temperature', 'wrihis_waterlevel_s1', 'wrihis_zcor'}
    for line in list(lines):
        p = _split_line(line)
        if not p: continue
        name = p[0].strip()
        if name.lower().startswith(('wrihis_', 'wrimap_')):
            lines = modify_mdu_key(lines, name, '1' if name.lower() in keep else '0')
    return lines

# Validation parameters
def _param_target(name: str):
    if name.endswith(FACTOR_SUFFIX): return name[:-len(FACTOR_SUFFIX)], "factor", None
    m = _INDEXED.match(name)
    if m: return m.group("key"), "index", int(m.group("i"))
    return name, "value", None

def _fmt_mdu(value) -> str:
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool): return str(int(value))
    try: v = float(value)
    except (TypeError, ValueError): return str(value)
    return f"{v:.5e}"

def apply_param(mdu_lines: list, base_lines: list, name: str, value) -> list:
    key, mode, idx = _param_target(name)
    base = _get_mdu_tokens(base_lines, key)
    if mode == "factor":
        if not base: raise KeyError(f"Key '{key}' has no value to scale")
        new = " ".join(_fmt_mdu(_mdu_number(b) * float(value)) for b in base)
    elif mode == "index":
        if idx >= len(base): raise KeyError(f"Key '{key}' has only {len(base)} value(s); index {idx} is invalid")
        toks = list(base); toks[idx] = _fmt_mdu(value); new = " ".join(toks)
    else: new = " ".join([_fmt_mdu(value)] + base[1:]) if len(base) > 1 else _fmt_mdu(value)
    return modify_mdu_key(mdu_lines, key, new)

def _prepare_case_mdu(base_lines: list, overrides: dict, params: dict, his_interval: str, write_map: bool) -> list:
    ov_lines = apply_overrides(base_lines, overrides)            # Key_factor / Key[i] act on the overridden values
    lines = _calibration_output_settings(ov_lines, his_interval, write_map)
    for p_name, p_val in params.items():
        lines = apply_param(lines, ov_lines, p_name, p_val)
    return lines

def create_baseline(directory: str) -> str:
    calibration_dir = os.path.join(directory, "calibration")
    input_dir = os.path.join(directory, "input")
    mdu_path = find_mdu(input_dir); base_lines = read_mdu(mdu_path)
    case_dir = os.path.join(calibration_dir, "baseline", "scenarios", "0")
    if os.path.exists(case_dir): shutil.rmtree(case_dir)
    copy_model_inputs(input_dir, case_dir)
    his_interval = (_get_mdu_tokens(base_lines, 'HisInterval') or ['21600'])[0]
    lines = _prepare_case_mdu(base_lines, load_overrides(calibration_dir), {}, his_interval, write_map=False)
    with open(os.path.join(case_dir, os.path.basename(mdu_path)), 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return case_dir

def run_calibration(processes: dict, key: str, scenario_dir: str, scenarios: list, log_path: str) -> None:
    try:
        fc = functions.append_log
        bat_path = os.path.normpath(os.path.join(DELFT_PATH, "x64/dflowfm/scripts/run_dflowfm.bat"))
        if not os.path.exists(bat_path):
            processes[key]["status"], processes[key]["message"] = "error", "Executable file not found"
            fc(log_path, f"[ERROR] Executable file not found: {bat_path}")
            return
        percent_re = re.compile(r'(?P<percent>\d{1,3}(?:\.\d+)?)\s*%')
        correct_cases, error_cases, total_simulations = [], [], len(scenarios)
        fc(log_path, ""); fc(log_path, "=" * 80)
        fc(log_path, "[SIMULATION STARTED]"); fc(log_path, "=" * 80)
        fc(log_path, f"[INFO] Total simulations: {total_simulations}"); fc(log_path, "")
        for index, case_id in enumerate(scenarios, start=1):
            if key not in processes:
                fc(log_path, "[WARNING] Calibration process was removed."); return
            processes[key]["current"], processes[key]["progress"] = index, 0.0
            c_dir = os.path.join(scenario_dir, str(case_id))
            mdus = sorted(f for f in os.listdir(c_dir) if f.lower().endswith(".mdu")) if os.path.isdir(c_dir) else []
            mdu_path = os.path.join(c_dir, mdus[0] if mdus else "FlowFM.mdu")
            fc(log_path, ""); fc(log_path, "=" * 80)
            fc(log_path, f"[SCENARIO {index}/{total_simulations}] Starting scenario {case_id}")
            fc(log_path, f"[STARTING] {mdu_path}"); fc(log_path, "=" * 80)
            if not os.path.exists(mdu_path):
                error_message = f"MDU file not found in scenario {case_id}"
                processes[key]["message"] = error_message
                fc(log_path, f"[ERROR] {error_message}")
                error_cases.append(mdu_path); continue
            flag = os.path.join(c_dir, COMPLETED_FLAG)
            if os.path.exists(flag): os.remove(flag)
            command = ["cmd.exe", "/c", bat_path, "--autostartstop", mdu_path]
            process, error_detected = None, False
            try:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=c_dir)
                for line in process.stdout:
                    if key not in processes:
                        fc(log_path, "[WARNING] Calibration process was removed.")
                        functions.kill_process(process); return
                    line = line.strip()
                    if not line: continue
                    fc(log_path, line)
                    if _ERROR_RE.search(line):
                        error_detected = True
                        processes[key]["message"] = f"Scenario {case_id} failed: {line}"
                        fc(log_path, f"[ERROR] Scenario {case_id}: {line}")
                        res = functions.kill_process(process)
                        fc(log_path, res["message"])
                        break
                    match_pct = percent_re.search(line)
                    if match_pct: processes[key]["progress"] = float(match_pct.group("percent"))
                return_code = process.wait()
            except Exception as e:
                error_detected, return_code = True, -1
                fc(log_path, f"[ERROR] Exception while running scenario {case_id}: {e}")
                if process is not None:
                    try:
                        res = functions.kill_process(process)
                        fc(log_path, res["message"])
                    except Exception: pass
            fc(log_path, "=" * 80)
            if return_code == 0 and not error_detected:
                correct_cases.append(mdu_path)
                with open(flag, 'w', encoding='utf-8') as f:
                    f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                fc(log_path, f"[COMPLETED] {mdu_path}")
            else:
                error_cases.append(mdu_path)
                fc(log_path, f"[FAILED] {mdu_path}, exit code={return_code}")
            fc(log_path, "=" * 80)
        fc(log_path, "\n\n"); fc(log_path, "=" * 80)
        fc(log_path, "[SIMULATION SUMMARY]"); fc(log_path, "=" * 80)
        fc(log_path, f"[SUMMARY] Total simulations : {len(scenarios)}")
        fc(log_path, f"[SUMMARY] Successful        : {len(correct_cases)}")
        fc(log_path, f"[SUMMARY] Failed            : {len(error_cases)}")
        if error_cases:
            fc(log_path, "[SUMMARY] Failed scenarios:")
            for case in error_cases:
                fc(log_path, f"  - {case}")
        fc(log_path, "=" * 80); fc(log_path, "[SIMULATION COMPLETED]")
        fc(log_path, "=" * 80); fc(log_path, "\n\n")
        processes[key]["status"], processes[key]["progress"] = "finished", 100.0
        processes[key]["message"] = f"Simulations completed: {len(correct_cases)} ok, {len(error_cases)} failed."
    except Exception as e:
        if processes.get(key):
            processes[key]["status"], processes[key]["message"] = "failed", f"Internal error: {e}"
        fc(log_path, f"[INTERNAL ERROR] {e}")

def _set_state(processes: dict, key: str, message: str = None, 
    progress: float = None, status: str = None, history: bool = True) -> None:
    info = processes.get(key)
    if info is None: return
    if status is not None: info["status"] = status
    if progress is not None: info["progress"] = round(float(progress), 1)
    if message is not None:
        info["message"] = message
        if history: info["history"] = info.get("history", "") + message + "\n"

def _normalize_params(values) -> dict:
    out = {}
    for v in values:
        name, lo, hi = v[0], float(v[1]), float(v[2])
        scale = (v[3] if len(v) > 3 and v[3] else "linear").lower()
        if not (np.isfinite(lo) and np.isfinite(hi)) or lo == hi:
            raise ValueError(f"Invalid bounds for '{name}': lower must be smaller than upper.")
        if lo > hi: lo, hi = hi, lo
        if scale == "log" and lo <= 0:
            raise ValueError(f"Parameter '{name}': log scale needs a lower bound > 0 (got {lo})")
        out[name] = {"bounds": [lo, hi], "scale": scale}
    if not out: raise ValueError("No parameter selected for calibration.")
    return out

def _find_mdu_file(input_dir: str):
    if not os.path.isdir(input_dir): return None
    mdu_files = sorted(
        f for f in os.listdir(input_dir)
        if f.lower().endswith('.mdu') and os.path.isfile(os.path.join(input_dir, f))
    )
    return os.path.join(input_dir, mdu_files[0]) if mdu_files else None

def _validate_params(base_lines: list, names) -> list:
    errors = []
    for n in names:
        key, mode, idx = _param_target(n)
        if not has_mdu_key(base_lines, key):
            errors.append(f"'{key}' is not in the MDU file"); continue
        toks = _get_mdu_tokens(base_lines, key)
        if mode == "index" and idx >= len(toks):
            errors.append(f"'{key}' has {len(toks)} value(s): index [{idx}] is invalid")
        if mode == "factor" and not toks: errors.append(f"'{key}' has no value to scale")
    return errors

def _clean_previous_results(calibration_dir: str) -> list:
    removed = []
    for name in STALE_RESULTS:
        path = os.path.join(calibration_dir, name)
        if os.path.isdir(path): shutil.rmtree(path); removed.append(name)
        elif os.path.isfile(path): os.remove(path); removed.append(name)
    return removed

def _param_spec(spec):
    if isinstance(spec, dict):
        lo, hi = spec["bounds"]; return float(lo), float(hi), spec.get("scale") == "log"
    return float(spec[0]), float(spec[1]), False

def _generate_lhs_samples(parameters_range, n_samples, seed=42) -> pd.DataFrame:
    param_names = list(parameters_range.keys())
    specs = [_param_spec(parameters_range[p]) for p in param_names]
    sampler = qmc.LatinHypercube(d=len(param_names), seed=seed, optimization='random-cd')
    u = sampler.random(n=n_samples); cols = {}
    for j, (p, (lo, hi, log)) in enumerate(zip(param_names, specs)):
        cols[p] = 10 ** (np.log10(lo) + u[:, j] * (np.log10(hi) - np.log10(lo))) if log else lo + u[:, j] * (hi - lo)
    return pd.DataFrame(cols, columns=param_names, index=np.arange(1, n_samples + 1))

def _copy_input_tree(src_dir: str, dst_dir: str, exclude_mdu: bool = True) -> None:
    """Copy the model input (including sub-folders), skipping output folders and optionally MDU files."""
    src_norm = os.path.normcase(os.path.normpath(src_dir))
    def ignore(path, names):
        if os.path.normcase(os.path.normpath(path)) != src_norm: return set()
        skipped = set()
        for n in names:
            upper = n.upper()
            if upper.startswith(('DFM_OUTPUT', 'DFM_DELWAQ')) or n.lower() == SCENARIO_OUTPUT_DIR: skipped.add(n)
            elif exclude_mdu and n.lower().endswith('.mdu'): skipped.add(n)
        return skipped
    shutil.copytree(src_dir, dst_dir, ignore=ignore, dirs_exist_ok=True)

def run_iteration(directory: str, processes: dict, key_process:str, n_samples:int, params:list) -> None:
    try:
        _set_state(processes, key_process, "Preparing data for calibration ...", 0.0, "running")
        calibration_dir = os.path.join(directory, "calibration")
        scenario_dir = os.path.join(calibration_dir, "scenarios")
        os.makedirs(calibration_dir, exist_ok=True)
        params = _normalize_params(params)
        # Read MDU file
        input_dir = os.path.join(directory, "input")
        mdu_path = _find_mdu_file(input_dir)
        if mdu_path is None:
            _set_state(processes, key_process, f"MDU file not found in project.", status="failed")
            return
        base_lines = read_mdu(mdu_path)
        # Overrides of the initial check (same as the baseline run)
        overrides = load_overrides(calibration_dir)
        ov_lines = apply_overrides(base_lines, overrides)
        errors = _validate_params(ov_lines, params)
        if errors:
            _set_state(processes, key_process, "Invalid parameter(s): " + "; ".join(errors) +
                ". Please add the key(s) to the MDU file first.", status="failed")
            return
        clash = [n for n in params if _param_target(n)[0].lower() in {k.lower() for k in overrides}]
        if clash:
            _set_state(processes, key_process, "[WARNING] Parameter(s) also set in the overrides: " + ", ".join(clash) +
                " - the sampled value replaces the override (Key_factor scales the overridden value).")
        if overrides: _set_state(processes, key_process, f"Applying base overrides: {overrides}")
        # Remove results of a previous resampling (they do not match the new samples)
        removed = _clean_previous_results(calibration_dir)
        if removed: _set_state(processes, key_process, "Removed previous results: " + ", ".join(removed))
        os.makedirs(scenario_dir, exist_ok=True)
        # Store parameters
        with open(os.path.join(calibration_dir, 'parameters.json'), 'w', encoding='utf-8') as f:
            json.dump(params, f, indent=4)
        # Latin Hypercube Sampling
        _set_state(processes, key_process, "Creating parameter samples using Latin Hypercube Sampling "
            "(log10 for log-scale parameters) ...", 0.0)
        df_params = _generate_lhs_samples(parameters_range=params, n_samples=n_samples)
        df_params.to_csv(os.path.join(calibration_dir, 'parameter_samples.csv'))
        _set_state(processes, key_process, "Parameter samples created.", 100.0)
        # Create scenarios: overrides + calibration output settings + sampled parameters
        his_interval = (_get_mdu_tokens(base_lines, 'HisInterval') or ['21600'])[0]
        mdu_name = os.path.basename(mdu_path)
        _set_state(processes, key_process, f"Creating {n_samples} scenarios ...\n", 0.0)
        for idx, (case_id, row) in enumerate(df_params.iterrows(), start=1):
            case_dir = os.path.join(scenario_dir, str(case_id))
            _copy_input_tree(input_dir, case_dir, exclude_mdu=True)
            mdu_case = _prepare_case_mdu(base_lines, overrides, row.to_dict(), his_interval, write_map=(case_id == 1))
            with open(os.path.join(case_dir, mdu_name), 'w', encoding='utf-8') as f:
                f.writelines(mdu_case)
            keys = dict.fromkeys(_param_target(k)[0] for k in params)
            _set_state(processes, key_process, f"Scenario {case_id} MDU: " +
                ", ".join(f"{k} = {_get_values_from_mdu(mdu_case, k)}" for k in keys), progress=idx / n_samples * 100)
        _set_state(processes, key_process, f"\nSuccessfully created {n_samples} resamples.", 100.0, "finished")
    except Exception as e:
        logger.exception("run_iteration failed")
        _set_state(processes, key_process, f"Error: {e}", status="failed")

def _interpolate_profile_to_depths(group: pd.DataFrame, depths: list, time_col="Time") -> pd.DataFrame:
    g = group.sort_values("depth").drop_duplicates("depth").reset_index(drop=True)
    d_arr, t_arr = g["depth"].to_numpy(), g["temperature"].to_numpy()
    if len(d_arr) < 2: return pd.DataFrame()
    t_start, rows = g[time_col].iloc[0], []
    dt_sec = (g[time_col] - t_start).dt.total_seconds().to_numpy()
    for d in depths:
        d_num = float(d)
        if d_arr.min() <= d_num <= d_arr.max():
            temp_interp = float(np.interp(d_num, d_arr, t_arr))
            el_sec = float(np.interp(d_num, d_arr, dt_sec))
            ts = t_start + pd.to_timedelta(el_sec, unit="s").round("1s")
            row = {time_col: ts}
            for depth_col in depths:
                row[f"obs_{depth_col}"] = temp_interp if depth_col == d else np.nan
            rows.append(row)
    return pd.DataFrame(rows)

def split_temp_from_depth(df:pd.DataFrame, depths:list, time_col:str, upper:float=5.0, lower:float=1.5) -> pd.DataFrame:
    try:
        df["temperature"] = pd.to_numeric(df["temperature"], errors='coerce')
        df["depth"] = pd.to_numeric(df["depth"], errors='coerce')
        df = df[[time_col, "temperature", "depth"]].dropna().sort_values(time_col, kind="stable").copy()
        jump_up = df["depth"].diff() < -(upper - lower)
        gap = pd.to_datetime(df[time_col]).diff() > pd.Timedelta(hours=1)
        df['new_profile'] = jump_up | gap
        df['profile_id'] = df['new_profile'].cumsum()
        obs_df = (
            df.groupby("profile_id", group_keys=False)
            .apply(lambda g: _interpolate_profile_to_depths(g, depths, time_col))
            .sort_values(time_col).reset_index(drop=True)
        )
        return obs_df.replace({np.nan: None})
    except Exception: return pd.DataFrame()

def hyd_his(project_name: str):
    d = os.path.join(PROJECT_ROOT, project_name, "output", "HYD")
    hits = sorted(glob.glob(os.path.join(d, "*_his.zarr"))) + sorted(glob.glob(os.path.join(d, "*_his.nc")))
    return hits[0] if hits else None

def _remove_rolling_outliers(df: pd.DataFrame, column: str, window: int=20, n_std: float=3.0) -> pd.DataFrame:
    df_clean = df.copy()
    if df_clean[column].notna().sum() < 5: return df_clean        # too few points to judge outliers
    rolling_mean = df_clean[column].rolling(window=window, center=True, min_periods=3).mean()
    rolling_std = df_clean[column].rolling(window=window, center=True, min_periods=3).std()
    lower = rolling_mean - n_std * rolling_std
    upper = rolling_mean + n_std * rolling_std
    mask = ((df_clean[column] >= lower) & (df_clean[column] <= upper)) | df_clean[column].isna() | rolling_std.isna()
    return df_clean[mask].copy()

def score_depths(df_sim: pd.DataFrame, obs_df: pd.DataFrame, depths: list, time_col: str = "Time") -> list:
    out, sim = [], df_sim.copy()
    sim.index = pd.to_datetime(sim.index, utc=True)
    for d in depths:
        obs_col, sim_col = f"obs_{d}", f"sim_{d}"
        row = {"depth": d, "n": 0, "rmse": np.nan, "bias": np.nan, "mae": np.nan, "r2": np.nan}
        if obs_col in obs_df and sim_col in sim:
            obs_series = obs_df[[time_col, obs_col]].dropna().copy()
            obs_series[time_col] = pd.to_datetime(obs_series[time_col], utc=True)
            obs_series = obs_series.set_index(time_col).sort_index()
            obs_series = obs_series[~obs_series.index.duplicated(keep='first')]
            obs_series[obs_col] = obs_series[obs_col].astype(float)
            obs_clean = _remove_rolling_outliers(obs_series, obs_col)
            comb = sim[[sim_col]].reindex(sim.index.union(obs_clean.index)).sort_index()
            comb[sim_col] = comb[sim_col].interpolate(method="time", limit_area="inside")
            aligned = comb.loc[obs_clean.index, [sim_col]].join(obs_clean).dropna()
            if len(aligned) > 0:
                diff = aligned[sim_col] - aligned[obs_col]
                row.update(n=int(len(aligned)), rmse=float(np.sqrt((diff ** 2).mean())), bias=float(diff.mean()),
                    mae=float(diff.abs().mean()), r2=float(r2_score(aligned[obs_col], aligned[sim_col])) 
                        if len(aligned) > 1 else np.nan
                )
        out.append(row)
    return out

def compute_weighted_rmse(depths: list, target: list, method: str = 'equal', kind: str = 'RMSE') -> float:
    depth_arr, val_arr = np.array([float(d) for d in depths]), np.array(target, dtype=float)
    valid = np.isfinite(depth_arr) & np.isfinite(val_arr)
    if kind != 'R2': valid &= val_arr >= 0
    if not valid.any(): return np.nan
    v_depths, v_vals = depth_arr[valid], val_arr[valid]
    if method == 'surface': weights = 1.0 / np.maximum(v_depths, 0.5)
    elif method == 'deep': weights = v_depths
    else: weights = np.ones(len(v_depths))
    weights = weights / weights.sum()
    if kind == 'R2': return float(np.sum(weights * v_vals))
    return float(np.sqrt(np.sum(weights * v_vals ** 2)))

def match_diagnostics(df_sim: pd.DataFrame, obs_df: pd.DataFrame, depths: list, time_col: str = "Time") -> str:
    """Explain why observations and simulation could not be matched (empty evaluation)."""
    if df_sim.empty: return "No simulated values at this station."
    t0, t1 = df_sim.index.min(), df_sim.index.max()
    obs_t = pd.to_datetime(obs_df[time_col], utc=True) if not obs_df.empty else pd.Series(dtype="datetime64[ns, UTC]")
    parts = [f"simulation {t0:%Y-%m-%d} → {t1:%Y-%m-%d}"]
    if len(obs_t): parts.append(f"observations {obs_t.min():%Y-%m-%d} → {obs_t.max():%Y-%m-%d}")
    else: parts.append("no observations inside the simulation period")
    for d in depths:
        n_obs = int(pd.to_numeric(obs_df[f"obs_{d}"], errors="coerce").notna().sum()) if f"obs_{d}" in obs_df else 0
        n_sim = int(df_sim[f"sim_{d}"].notna().sum()) if f"sim_{d}" in df_sim else 0
        parts.append(f"{d} m: {n_obs} obs / {n_sim} sim values")
    return "; ".join(parts)













def _split_line(line: str):
    if '=' not in line or line.lstrip().startswith(('#', '[')): return None
    key_part, rest = line.split('=', 1)
    value_part, sep, comment = rest.partition('#')
    return key_part, value_part, bool(sep), comment


def _first_profile(measured_utc: pd.DataFrame, after, window_hours: float = 12.0, time_col: str = "Time"):
    df = measured_utc.copy()
    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["depth"] = pd.to_numeric(df["depth"], errors="coerce")
    df = df.dropna(subset=[time_col, "temperature", "depth"]).sort_values(time_col)
    if after is not None: df = df[df[time_col] >= after]
    if df.empty: return None, pd.DataFrame()
    t0 = df[time_col].iloc[0]
    w = df[df[time_col] <= t0 + pd.Timedelta(hours=window_hours)].copy()
    w["d"] = (w["depth"] * 2).round() / 2
    prof = w.groupby("d", as_index=False)["temperature"].mean().rename(columns={"d": "depth"}).sort_values("depth")
    return t0, prof

def get_station_from_his_file(his_path: str) -> list:
    if not his_path or not os.path.exists(his_path): return []
    ds = xr.open_zarr(his_path) if his_path.endswith(".zarr") else xr.open_dataset(his_path)
    with ds:
        names = [
            n.decode('utf-8').strip() if isinstance(n, bytes) else str(n).strip()
            for n in ds['station_name'].values
        ]
    return names


def target_score(target_method: str, obs: pd.Series, sim: pd.Series) -> float:
    if target_method == "R2": return r2_score(obs, sim)
    if target_method == "MAE": return float(np.mean(np.abs(np.asarray(sim) - np.asarray(obs))))
    return np.sqrt(mean_squared_error(obs, sim))

# Surrogate & optimisation
def _feature_transform(X: pd.DataFrame, log_cols: list) -> pd.DataFrame:
    X = X.copy()
    for c in log_cols:
        if c in X: X[c] = np.log10(np.clip(X[c].astype(float), 1e-300, None))
    return X

def surrogate_model(processes: dict, key: str, df: pd.DataFrame,
    model: str, directory: str, log_path: str) -> None:
    fc = functions.append_log
    try:
        fc(log_path, "Building surrogate model...")
        fc(log_path, "===============================")
        col_name = df.columns.tolist()[-1]
        X, y = df.drop(columns=[col_name]), df[col_name].to_numpy(float)
        # Tham số thang log -> log10 trước khi chuẩn hoá
        log_cols = []
        params_path = os.path.join(directory, "parameters.json")
        if os.path.exists(params_path):
            with open(params_path, encoding="utf-8") as f:
                params = json.load(f)
            log_cols = [c for c in X.columns if c in params and _param_spec(params[c])[2]]
        if log_cols: fc(log_path, f"Log10-transformed inputs: {log_cols}")
        Xt = _feature_transform(X, log_cols)
        if model == "gaussian-process":
            scaler_X = StandardScaler()
            X_scaled = scaler_X.fit_transform(Xt)
            d = X.shape[1]
            kernels_to_test = [
                (C(1.0, (1e-3, 1e3)) * RBF(np.ones(d), (1e-3, 1e2)), 'RBF (ARD)'),
                (C(1.0, (1e-3, 1e3)) * RBF(np.ones(d), (1e-2, 1e2)) + WhiteKernel(1e-3, (1e-10, 1e-1)), 'RBF (ARD) + White'),
                (C(1.0, (1e-3, 1e3)) * Matern(np.ones(d), nu=1.5, length_scale_bounds=(1e-3, 1e2)), 'Matern 1.5 (ARD)'),
                (C(1.0, (1e-3, 1e3)) * Matern(np.ones(d), nu=2.5, length_scale_bounds=(1e-3, 1e2)) + WhiteKernel(1e-3, (1e-10, 1e-1)), 'Matern 2.5 (ARD) + White'),
                (C(1.0, (1e-3, 1e3)) * RationalQuadratic(1.0, 1.0, length_scale_bounds=(1e-9, 1e6)), 'RationalQuadratic'),
            ]
            best_ll, best_kernel, best_name = -np.inf, None, None
            for kernel_obj, k_name in kernels_to_test:
                fc(log_path, f"Testing kernel: {k_name}...")
                gpr = GaussianProcessRegressor(
                    kernel=kernel_obj, n_restarts_optimizer=10, alpha=1e-6, normalize_y=True, random_state=42
                )
                gpr.fit(X_scaled, y)
                fc(log_path, f"  log-marginal-likelihood = {gpr.log_marginal_likelihood_value_:.3f}")
                if gpr.log_marginal_likelihood_value_ > best_ll:
                    best_ll, best_kernel, best_name = gpr.log_marginal_likelihood_value_, kernel_obj, k_name
            fc(log_path, f"Best kernel: {best_name}")
            def make():
                return GaussianProcessRegressor(
                    kernel=best_kernel, n_restarts_optimizer=30, alpha=1e-6, normalize_y=True, random_state=42
                )
            cv = LeaveOneOut() if len(y) <= 60 else KFold(n_splits=5, shuffle=True, random_state=42)
            cv_rmse = cv_r2 = None
            try:
                y_cv = cross_val_predict(make(), X_scaled, y, cv=cv)
                cv_rmse, cv_r2 = float(np.sqrt(np.mean((y_cv - y) ** 2))), float(r2_score(y, y_cv))
                fc(log_path, f"Cross-validation ({type(cv).__name__}): RMSE = {cv_rmse:.4f}, "
                    f"R² = {cv_r2:.3f} (target range {y.min():.4f} – {y.max():.4f})")
                if cv_r2 < 0.5:
                    fc(log_path, "[WARNING] The surrogate explains < 50% of the variance: the optimum is "
                        "unreliable. Add samples or choose more influential parameters.")
            except Exception as e:
                fc(log_path, f"[WARNING] Cross-validation failed: {e}")
            fc(log_path, "Training the final model with the best kernel...")
            final_gpr = make()
            final_gpr.fit(X_scaled, y)
            model_dir = os.path.normpath(os.path.join(directory, 'model'))
            if os.path.exists(model_dir):
                fc(log_path, f"Removing old model: {model_dir}.")
                shutil.rmtree(model_dir)
            os.makedirs(model_dir, exist_ok=True)
            for name, data in (('scaler_X', scaler_X), ('gpr_model', final_gpr)):
                with open(os.path.join(model_dir, f'{name}.pkl'), "wb") as f:
                    pickle.dump(data, f)
            with open(os.path.join(model_dir, FEATURES_FILE), "w", encoding="utf-8") as f:
                json.dump({"columns": list(X.columns), "log10": log_cols, "kernel": best_name,
                           "cv_rmse": cv_rmse, "cv_r2": cv_r2, "target": col_name}, f, indent=2)
            fc(log_path, f"Saved model: {model_dir}.")
            fc(log_path, "Model training completed.")
            fc(log_path, "===============================")
            processes[key]["status"] = "finished"
            processes[key]["message"] = "Model training completed." + (f" CV R² = {cv_r2:.3f}." if cv_r2 is not None else "")
        else:
            raise ValueError(f"Unsupported surrogate model: '{model}'.")
    except Exception as e:
        if processes.get(key):
            processes[key]["status"] = "failed"
            processes[key]["message"] = f"Internal error: {e}"
        fc(log_path, f"[INTERNAL ERROR] {e}")

def optuna_objective(trial, scaler_x, model, p_ranges, log_cols=()):
    trial_params = {}
    for p, spec in p_ranges.items():
        lo, hi, log = _param_spec(spec)
        trial_params[p] = trial.suggest_float(p, lo, hi, log=log)
    df_in = _feature_transform(pd.DataFrame([trial_params], columns=list(p_ranges.keys())), list(log_cols))
    return float(model.predict(scaler_x.transform(df_in))[0])

def optuna_callback(study, trial, processes, key, log_path):
    if trial.value is None:
        functions.append_log(log_path, f"Trial {trial.number}: {trial.state.name}")
    else:
        functions.append_log(log_path,
            f"Trial {trial.number}: value={trial.value:.6f}, "
            f"best={study.best_value:.6f}, params={trial.params}"
        )
    info = processes.get(key)
    if info is None: study.stop(); return
    info["current"] = trial.number + 1
    info["progress"] = round(min(100.0, (trial.number + 1) / (info.get("total") or 1) * 100), 1)

def run_optuna(processes: dict, key: str, direction: str,
    iterations: int, params_path: str, optuna_path: str, importance_path: str,
    scaler_path: str, model_path: str, log_path: str) -> None:
    try:
        with open(params_path, "r", encoding="utf-8") as f:
            parameters = json.load(f)
        with open(scaler_path, "rb") as f:
            scaler = pickle.load(f)
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        features_path = os.path.join(os.path.dirname(model_path), FEATURES_FILE)
        log_cols = []
        if os.path.exists(features_path):
            with open(features_path, encoding="utf-8") as f:
                log_cols = json.load(f).get("log10", [])
        params = {name: parameters[name] for name in scaler.feature_names_in_}
        optuna_func = partial(optuna_objective, scaler_x=scaler, model=model, p_ranges=params, log_cols=log_cols)
        study = optuna.create_study(
            sampler=optuna.samplers.TPESampler(seed=42), direction=direction,
            study_name='delft3d_fm_calibration'
        )
        callback = partial(optuna_callback, processes=processes, key=key, log_path=log_path)
        study.optimize(optuna_func, n_trials=iterations, callbacks=[callback])
        df_trials = study.trials_dataframe()
        df_trials.to_csv(optuna_path)
        try:
            importance = optuna.importance.get_param_importances(
                study, evaluator=MeanDecreaseImpurityImportanceEvaluator()
            )
        except Exception:
            param_cols = [c for c in df_trials.columns if c.startswith('params_')]
            X_tr = df_trials[param_cols].fillna(df_trials[param_cols].mean())
            y_tr = df_trials['value'].fillna(df_trials['value'].max())
            rf = RandomForestRegressor(n_estimators=100, random_state=42)
            rf.fit(X_tr, y_tr)
            clean_names = [c.replace('params_', '') for c in param_cols]
            importance = dict(zip(clean_names, rf.feature_importances_))
        sorted_importance = dict(sorted(importance.items(), key=lambda item: item[1]))
        with open(importance_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_importance, f, indent=4)
        processes[key]["best_params"] = study.best_params
        processes[key]["status"] = "finished"
        processes[key]["message"] = "Optimization completed."
    except Exception as e:
        if processes.get(key):
            processes[key]["status"] = "failed"
            processes[key]["message"] = f"Internal error: {e}"
        functions.append_log(log_path, f"[INTERNAL ERROR] {e}")