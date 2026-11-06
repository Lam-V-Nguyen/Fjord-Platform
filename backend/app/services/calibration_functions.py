import os, shutil, subprocess, pickle, psutil
import warnings, logging, optuna, re, json
from datetime import datetime, timedelta
from config import DELFT_PATH, PROJECT_ROOT
from services import functions
from sklearn.model_selection import KFold
from functools import partial
from typing import overload, Any
import numpy as np, pandas as pd, xarray as xr
from scipy.stats import qmc
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, r2_score
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

_TUNIT_SECONDS = {"S": 1, "M": 60, "H": 3600, "D": 86400}
FAILED_CASES_FILE, SCENARIO_OUTPUT_DIR = "failed_cases.json", "output"
STALE_RESULTS = (
    "scenarios", "summary", "model", "calibration_summary.csv",
    FAILED_CASES_FILE, "optuna.csv", "optuna_importance.csv",
)
HIS_VARS_TO_KEEP = {"wrihis_temperature", "wrihis_waterlevel_s1", "wrihis_zcor"}
SCENARIO_MDU, TARGET_METHODS = "FlowFM.mdu", ("RMSE", "R2")
SURROGATE_MODELS = ("gaussian-process",)
WEIGHT_METHODS, MAXIMIZE_TARGETS = ("equal", "surface", "deep"), {"R2"}
_ERROR_RE = re.compile(r"\*\*\s*error|forrtl:|^\s*error\s*:", re.IGNORECASE)
_PERCENT_RE = re.compile(r'(?P<percent>\d{1,3}(?:\.\d+)?)\s*%')
_DATE_FORMATS = {14: "%Y%m%d%H%M%S", 12: "%Y%m%d%H%M", 8: "%Y%m%d"}
RUN_LOCK_FILE = ".simulation.lock"


def resolve_base_project(body: dict, project_name: str, user):
    """Uncalibrated project: 'baseProject' from the request, otherwise the source recorded
    in calibration_source.json when the calibrated scenario was saved."""
    name = str(body.get('baseProject') or '').strip()
    if not name:
        meta_path = os.path.join(PROJECT_ROOT, project_name, 'calibration_source.json')
        if os.path.exists(meta_path):
            with open(meta_path, 'r', encoding='utf-8') as f:
                name = str(json.load(f).get('base_project') or '').strip()
    if not name: return None, None
    base_name, _ = functions.project_definer(name, user)
    if base_name == project_name: return None, None
    return name, base_name

def his_zarr(project_name: str) -> str:
    return os.path.join(PROJECT_ROOT, project_name, 'output', 'HYD', 'FlowFM_his.zarr')

def safe_path(base_dir: str, filename) -> str:
    """Join a user-supplied file name to base_dir without allowing path traversal."""
    name = os.path.basename(str(filename or '').replace('\\', '/')).strip()
    if name in ('', '.', '..'): raise ValueError(f"Invalid file name: '{filename}'.")
    base = os.path.abspath(base_dir)
    path = os.path.abspath(os.path.join(base, name))
    if os.path.dirname(path) != base: raise ValueError(f"Invalid file name: '{filename}'.")
    return path

def read_log_tail(log_path: str, offset: int) -> dict:
    """Return the complete new lines after `offset` (bytes) and the new offset."""
    if not os.path.exists(log_path): return {"lines": [], "offset": 0}
    size = os.path.getsize(log_path)
    if offset < 0 or offset > size: offset = 0   # file was recreated
    with open(log_path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    last_nl = chunk.rfind(b"\n")
    if last_nl == -1: return {"lines": [], "offset": offset}   # wait for a complete line
    text = chunk[:last_nl + 1].decode("utf-8", errors="replace")
    return {"lines": [line.rstrip("\r") for line in text.split("\n")[:-1]], "offset": offset + last_nl + 1}

def get_log_path(project_name: str, log_file) -> str:
    log_dir = os.path.join(PROJECT_ROOT, project_name, "calibration", "logs")
    os.makedirs(log_dir, exist_ok=True)
    return safe_path(log_dir, log_file)

def get_records(df: pd.DataFrame):
    record = df.replace([np.inf, -np.inf], np.nan)
    record = record.astype(object).where(record.notna(), None).to_dict(orient="records")
    return record

def find_mdu_file(input_dir: str):
    if not os.path.isdir(input_dir): return None
    mdu_files = sorted(
        f for f in os.listdir(input_dir)
        if f.lower().endswith('.mdu') and os.path.isfile(os.path.join(input_dir, f))
    )
    return os.path.join(input_dir, mdu_files[0]) if mdu_files else None

def safe_token(text) -> str:
    return re.sub(r'[^\w.-]', '_', str(text)).strip('._') or 'station'

def to_utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')

def num(value, digits: int = 3):
    try: value = float(value)
    except (TypeError, ValueError): return None
    return round(value, digits) if np.isfinite(value) else None

def obs_series(obs_df: pd.DataFrame, depth: str) -> pd.Series:
    """Observation at one depth as a float Series with a unique, sorted UTC DatetimeIndex."""
    col = f"obs_{depth}"
    if obs_df.empty or col not in obs_df.columns: return pd.Series(dtype=float, name=col)
    s = obs_df[['Time', col]].copy()
    s[col] = pd.to_numeric(s[col], errors='coerce')
    s = s.dropna()
    series = pd.Series(
        s[col].to_numpy(dtype=float),
        index=pd.DatetimeIndex(pd.to_datetime(s['Time'], utc=True)), name=col
    )
    return series[~series.index.duplicated(keep='first')].sort_index()

def _find_key_index(mdu_lines: list, key: str):
    key_lower = key.strip().lower()
    for i, line in enumerate(mdu_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(('#', '[')) or '=' not in stripped: continue
        if stripped.split('=', 1)[0].strip().lower() == key_lower: return i
    return None

def _try_parse_mdu_datetime(raw: str):
    """Parse yyyymmdd[hhmm[ss]] (optionally with '.0' or a trailing '.'); None when not a date."""
    if not raw: return None
    token = raw.strip().split()[0]
    if token.endswith('.'): token = token[:-1]
    elif re.fullmatch(r"\d+\.0+", token): token = token.split('.')[0]
    fmt = _DATE_FORMATS.get(len(token)) if token.isdigit() else None
    if fmt is None: return None
    try:
        dt = datetime.strptime(token, fmt)
    except ValueError: return None
    return dt if 1800 <= dt.year <= 2300 else None

def get_values_from_mdu(mdu_content: list, key: str) -> str:
    index = _find_key_index(mdu_content, key)
    if index is None: return ''
    return mdu_content[index].split('=', 1)[1].split('#', 1)[0].strip()

def _parse_mdu_datetime(raw: str) -> datetime:
    raw = raw.strip().split()[0]
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try: return datetime.strptime(raw, fmt)
        except ValueError: continue
    raise ValueError(f"Cannot parse MDU date/time '{raw}'.")

def get_simulation_period(mdu_content: list):
    """Return (start, end) of the simulation as naive UTC datetimes.
    Uses StartDateTime/StopDateTime when they hold real dates (yyyymmddHHMMSS),
    otherwise RefDate + TStart/TStop (in Tunit)."""
    start_dt = _try_parse_mdu_datetime(get_values_from_mdu(mdu_content, 'StartDateTime'))
    stop_dt = _try_parse_mdu_datetime(get_values_from_mdu(mdu_content, 'StopDateTime'))
    if start_dt is not None and stop_dt is not None and stop_dt > start_dt:
        return start_dt, stop_dt
    ref_date = get_values_from_mdu(mdu_content, 'RefDate')
    if not ref_date: raise ValueError("'RefDate' not found in MDU file.")
    ref_dt = _parse_mdu_datetime(ref_date)
    unit = (get_values_from_mdu(mdu_content, 'Tunit') or 'S').strip().upper()
    if unit not in _TUNIT_SECONDS: raise ValueError(f"Unsupported 'Tunit' value: '{unit}'.")
    factor = _TUNIT_SECONDS[unit]
    def convert(key):
        raw = get_values_from_mdu(mdu_content, key)
        if not raw: raise ValueError(f"'{key}' not found in MDU file.")
        return ref_dt + timedelta(seconds=float(raw.split()[0]) * factor)
    return convert('TStart'), convert('TStop')

def _set_state(processes: dict, key: str, message: str = '', 
    progress: float = 0, status: str = '', history: bool = True) -> None:
    info = processes.get(key)
    if info is None: return
    if status is not None: info["status"] = status
    if progress is not None: info["progress"] = round(float(progress), 1)
    if message is not None:
        info["message"] = message
        if history: info["history"] = info.get("history", "") + message + "\n"

def _parse_parameter_bounds(values: list) -> dict:
    params = {}
    for name, lower, upper in values:
        lower, upper = float(lower), float(upper)
        if not (np.isfinite(lower) and np.isfinite(upper)) or lower >= upper:
            raise ValueError(f"Invalid bounds for '{name}': lower must be smaller than upper.")
        params[name] = [lower, upper]
    if not params: raise ValueError("No parameter selected for calibration.")
    return params

def has_mdu_key(mdu_lines: list, key: str) -> bool:
    return _find_key_index(mdu_lines, key) is not None

def _clean_previous_results(calibration_dir: str) -> list:
    removed = []
    for name in STALE_RESULTS:
        path = os.path.join(calibration_dir, name)
        if os.path.isdir(path): shutil.rmtree(path); removed.append(name)
        elif os.path.isfile(path): os.remove(path); removed.append(name)
    return removed

def _write_failed_cases(path: str, cases: list) -> None:
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(sorted(int(c) for c in cases), f)
    except Exception: logger.exception("Cannot write failed cases file")

def generate_lhs_samples(parameters_range, n_samples, seed=42) -> pd.DataFrame:
    param_names = list(parameters_range.keys())
    sampler = qmc.LatinHypercube(d=len(param_names), rng=np.random.default_rng(seed), optimization='random-cd')
    sample_lhs = sampler.random(n=n_samples)
    lower_bounds = [parameters_range[p][0] for p in param_names]
    upper_bounds = [parameters_range[p][1] for p in param_names]
    sample_scaled = qmc.scale(sample_lhs, lower_bounds, upper_bounds)
    return pd.DataFrame(sample_scaled, columns=param_names, index=np.arange(1, n_samples + 1))

def modify_mdu_key(mdu_lines: list, key: str, value: str = '', max_length=None, required: bool = True) -> list:
    """Replace the value of an MDU key, keeping the comment and the line ending.
    Raises KeyError when the key is missing and required=True; otherwise returns the lines unchanged."""
    mdu = list(mdu_lines)
    index = _find_key_index(mdu, key)
    if index is None:
        if required: raise KeyError(f"Key '{key}' not found in MDU file.")
        return mdu
    line = mdu[index]
    body = line.rstrip('\r\n')
    eol = line[len(body):]
    lhs, rhs = body.split('=', 1)
    old_value, sep, comment = rhs.partition('#')
    value = '' if value is None else str(value)
    width = (max_length - 2) if max_length is not None else (len(old_value) - 2)
    width = max(width, len(value))
    new_body = f"{lhs}= {value.ljust(width)}"
    new_body = f"{new_body} #{comment}" if sep else new_body.rstrip()
    mdu[index] = new_body + eol
    return mdu

def copy_input_tree(src_dir: str, dst_dir: str, exclude_mdu: bool = True) -> None:
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

def run_iteration(directory: str, processes: dict, key_process:str, n_samples:int, params_list:list) -> None:
    try:
        _set_state(processes, key_process, "Preparing data for calibration ...", 0.0, "running")
        calibration_dir = os.path.join(directory, "calibration")
        scenario_dir = os.path.join(calibration_dir, "scenarios")
        os.makedirs(calibration_dir, exist_ok=True)
        params = _parse_parameter_bounds(params_list)
        # Read MDU file
        input_dir = os.path.join(directory, "input")
        mdu_path = find_mdu_file(input_dir)
        if mdu_path is None:
            _set_state(processes, key_process, f"MDU file not found in project.", status="failed")
            return
        with open(mdu_path, 'r', encoding='utf-8', errors='ignore') as f:
            mdu_content = f.readlines()
        missing = [p for p in params if not has_mdu_key(mdu_content, p)]
        if missing:
            _set_state(processes, key_process,
                "Parameter(s) not found in the MDU file: " + ", ".join(missing) +
                ". Please add them to the MDU file first.", status="failed")
            return
        # Remove results of a previous resampling (they do not match the new samples)
        removed = _clean_previous_results(calibration_dir)
        if removed: _set_state(processes, key_process, "Removed previous results: " + ", ".join(removed))
        os.makedirs(scenario_dir, exist_ok=True)
        # Store parameters
        with open(os.path.join(calibration_dir, 'parameters.json'), 'w', encoding='utf-8') as f:
            json.dump(params, f, indent=4)
        # Latin Hypercube Sampling
        _set_state(processes, key_process, "Creating parameter samples using Latin Hypercube Sampling ...", 0.0)
        df_params = generate_lhs_samples(parameters_range=params, n_samples=n_samples)
        df_params.to_csv(os.path.join(calibration_dir, 'parameter_samples.csv'))
        _set_state(processes, key_process, "Parameter samples created.", 100.0)
        # Optimize MDU settings for calibration runs
        _set_state(processes, key_process, "Optimizing MDU output settings ...", 0.0)
        his_interval = get_values_from_mdu(mdu_content, 'HisInterval')
        his_interval = his_interval.split()[0] if his_interval else '21600'
        settings = [
            ('WaqInterval', '0'), ('RstInterval', '0'), ('RestartFile', ''), ('HisInterval', his_interval),
        ]
        for name, val in settings:
            mdu_content = modify_mdu_key(mdu_content, name, val, required=False)
        output_keys = [
            line.split('=', 1)[0].strip() for line in mdu_content
            if line.strip().lower().startswith(('wrihis_', 'wrimap_')) and '=' in line
        ]
        for name in output_keys:
            val = '1' if name.lower() in HIS_VARS_TO_KEEP else '0'
            mdu_content = modify_mdu_key(mdu_content, name, val, required=False)
        _set_state(processes, key_process, "MDU output settings updated.", 100.0)
        # Create scenarios
        _set_state(processes, key_process, f"Creating {n_samples} scenarios ...", 0.0)
        for idx, (case_id, row) in enumerate(df_params.iterrows(), start=1):
            case_dir = os.path.join(scenario_dir, str(case_id))
            copy_input_tree(input_dir, case_dir, exclude_mdu=True)
            mdu_case = modify_mdu_key(
                mdu_content, 'MapInterval', his_interval if case_id == 1 else '0', required=False
            )
            for p_name, p_val in row.items():
                mdu_case = modify_mdu_key(mdu_case, str(p_name), f"{float(p_val):.5e}")
            with open(os.path.join(case_dir, SCENARIO_MDU), 'w', encoding='utf-8') as f:
                f.writelines(mdu_case)
            _set_state(processes, key_process, progress=idx / n_samples * 100)
        _set_state(processes, key_process, f"Successfully created {n_samples} resamples.", 100.0, "finished")
    except Exception as e:
        logger.exception("run_iteration failed")
        _set_state(processes, key_process, f"Error: {e}", status="failed")

def _pid_alive(pid: int) -> bool:
    try: return psutil.pid_exists(pid)
    except ImportError: pass
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle: return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259

def read_run_lock(calibration_dir: str):
    """Owner info of an active run, or None. Stale locks (dead process) are removed."""
    path = os.path.join(calibration_dir, RUN_LOCK_FILE)
    if not os.path.exists(path): return None
    try:
        with open(path, 'r', encoding='utf-8') as f: info = json.load(f)
        if _pid_alive(int(info.get('pid', -1))): return info
    except Exception: pass
    try: os.remove(path)
    except OSError: pass
    return None

def acquire_run_lock(calibration_dir: str) -> bool:
    """Atomically create the lock file; False when another live run holds it."""
    read_run_lock(calibration_dir)   # clears a stale lock
    path = os.path.join(calibration_dir, RUN_LOCK_FILE)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({"pid": os.getpid(), "started": datetime.now().isoformat(timespec='seconds')}, f)
    return True

def release_run_lock(calibration_dir: str) -> None:
    path = os.path.join(calibration_dir, RUN_LOCK_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            if int(json.load(f).get('pid', -1)) != os.getpid(): return
        os.remove(path)
    except Exception: pass

def _scenario_output_settings(case_dir: str, mdu_name: str = SCENARIO_MDU):
    """OutputDir and HisFile as written in the scenario MDU ('' when not set)."""
    mdu_path = os.path.join(case_dir, mdu_name)
    if not os.path.exists(mdu_path): return '', ''
    with open(mdu_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    return get_values_from_mdu(lines, 'OutputDir'), get_values_from_mdu(lines, 'HisFile')

def find_his_file(case_dir: str, mdu_name: str = SCENARIO_MDU):
    """Locate <run>_his.nc: OutputDir/HisFile from the scenario MDU, default folders, then any *_his.nc."""
    base = os.path.splitext(mdu_name)[0]
    output_dir, his_name = _scenario_output_settings(case_dir, mdu_name)
    his_name = his_name or f"{base}_his.nc"
    candidates = []
    if output_dir:
        out = output_dir if os.path.isabs(output_dir) else os.path.join(case_dir, output_dir)
        candidates.append(os.path.join(out, his_name))
    candidates += [
        os.path.join(case_dir, SCENARIO_OUTPUT_DIR, his_name),
        os.path.join(case_dir, f"DFM_OUTPUT_{base}", his_name),
        os.path.join(case_dir, "DFM_OUTPUT", his_name),
        os.path.join(case_dir, his_name),
    ]
    found = next((os.path.normpath(c) for c in candidates if os.path.exists(c)), None)
    if found: return found
    for root, _, files in os.walk(case_dir):
        for f in files:
            if f.lower().endswith('_his.nc'): return os.path.join(root, f)
    return None

def run_calibration(processes: dict, key_process: str, scenario_dir: str, scenarios: list, log_path: str) -> None:
    calibration_dir = os.path.dirname(scenario_dir)
    if not acquire_run_lock(calibration_dir):
        owner = read_run_lock(calibration_dir) or {}
        _set_state(processes, key_process,
            f"Another simulation run is already active (pid {owner.get('pid')}, started {owner.get('started')}).",
            status="failed", history=False)
        return
    try: _run_calibration(processes, key_process, scenario_dir, scenarios, log_path)
    finally: release_run_lock(calibration_dir)

def _run_calibration(processes: dict, key_process: str, scenario_dir: str, scenarios: list, log_path: str) -> None:
    # Remove old log and the list of failed cases
    if os.path.exists(log_path): os.remove(log_path)
    failed_path = os.path.join(os.path.dirname(scenario_dir), FAILED_CASES_FILE)
    if os.path.exists(failed_path): os.remove(failed_path)
    log = partial(functions.append_log, log_path)
    correct_cases, error_cases, total = [], [], len(scenarios)
    try:
        bat_path = os.path.normpath(os.path.join(DELFT_PATH, "x64/dflowfm/scripts/run_dflowfm.bat"))
        if not os.path.exists(bat_path):
            _set_state(processes, key_process, "Executable file not found.", status="failed", history=False)
            log(f"[ERROR] Executable file not found: {bat_path}")
            return
        log(""); log("=" * 80); log("[SIMULATION STARTED]"); log("=" * 80)
        log(f"[INFO] Total simulations: {total}"); log("")
        mdu_base = os.path.splitext(SCENARIO_MDU)[0]
        for index, case_id in enumerate(scenarios, start=1):
            if key_process not in processes:
                log("[WARNING] Calibration process was removed."); return
            info = processes[key_process]
            info["current"], info["progress"] = index, 0.0
            info["message"] = f"Running scenario {case_id} ({index}/{total}) ..."
            c_dir = os.path.join(scenario_dir, str(case_id))
            mdu_path = os.path.join(c_dir, SCENARIO_MDU)
            log(""); log("=" * 80)
            log(f"[SCENARIO {index}/{total}] Starting scenario {case_id}")
            log(f"[STARTING] {mdu_path}"); log("=" * 80)
            if not os.path.exists(mdu_path):
                log(f"[ERROR] MDU file not found in scenario {case_id}")
                error_cases.append(case_id); _write_failed_cases(failed_path, error_cases)
                continue
            # Remove output of an earlier run so that a failed run never leaves stale results behind
            out_cfg, _ = _scenario_output_settings(c_dir)
            for out_name in {SCENARIO_OUTPUT_DIR, f"DFM_OUTPUT_{mdu_base}", "DFM_OUTPUT", out_cfg}:
                out_path = os.path.normpath(os.path.join(c_dir, out_name)) if out_name else None
                if out_path is None: raise ValueError("Path cannot be None")
                try:
                    inside = out_path and out_path != os.path.normpath(c_dir) and \
                        os.path.commonpath([out_path, os.path.normpath(c_dir)]) == os.path.normpath(c_dir)
                except ValueError: inside = False
                if inside: shutil.rmtree(out_path, ignore_errors=True)
            command = ["cmd.exe", "/c", bat_path, "--autostartstop", mdu_path]
            process, error_detected, return_code = None, False, -1
            try:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=c_dir)
                if process.stdout is None: raise RuntimeError("Failed to capture process stdout")
                for line in process.stdout:
                    if key_process not in processes:
                        log("[WARNING] Calibration process was removed.")
                        functions.kill_process(process)
                        return
                    line = line.strip()
                    if not line: continue
                    log(line)
                    if _ERROR_RE.search(line):
                        error_detected = True
                        processes[key_process]["message"] = f"Scenario {case_id} failed: {line}"
                        log(f"[ERROR] Scenario {case_id}: {line}")
                        res = functions.kill_process(process)
                        log(res["message"])
                        break
                    match_pct = _PERCENT_RE.search(line)
                    if match_pct:
                        processes[key_process]["progress"] = min(100.0, float(match_pct.group("percent")))
                return_code = process.wait()
            except Exception as e:
                error_detected = True
                log(f"[ERROR] Exception while running scenario {case_id}: {e}")
                if process is not None:
                    try: log(functions.kill_process(process)["message"])
                    except Exception: pass
            has_output = find_his_file(c_dir) is not None
            log("=" * 80)
            if return_code == 0 and not error_detected and has_output:
                correct_cases.append(case_id)
                log(f"[COMPLETED] {mdu_path}")
            else:
                error_cases.append(case_id)
                reason = "no HIS output" if (return_code == 0 and not error_detected) else f"exit code={return_code}"
                log(f"[FAILED] {mdu_path}, {reason}")
            log("=" * 80)
            _write_failed_cases(failed_path, error_cases)
        # Summary
        log("\n\n"); log("=" * 80); log("[SIMULATION SUMMARY]"); log("=" * 80)
        log(f"[SUMMARY] Total simulations : {total}")
        log(f"[SUMMARY] Successful        : {len(correct_cases)}")
        log(f"[SUMMARY] Failed            : {len(error_cases)}")
        if error_cases:
            log("[SUMMARY] Failed scenarios: " + ", ".join(str(c) for c in error_cases))
        log("=" * 80); log("[SIMULATION COMPLETED]"); log("=" * 80); log("\n\n")
        status = "finished" if correct_cases else "failed"
        _set_state(processes, key_process,
            f"\nSimulation completed: {len(correct_cases)}/{total} successful, {len(error_cases)} failed.",
            100.0, status, history=False)
    except Exception as e:
        _write_failed_cases(failed_path, error_cases)
        _set_state(processes, key_process, f"Internal error: {e}", status="failed", history=False)
        log(f"[INTERNAL ERROR] {e}")

@overload
def _wall_clock(values: pd.Series) -> pd.Series: ...
@overload
def _wall_clock(values: str) -> pd.Timestamp: ...

def _wall_clock(values: pd.Series | str | pd.Timestamp) -> pd.Series | pd.Timestamp:
    if isinstance(values, pd.Series):
        times = pd.to_datetime(values, errors="coerce")
        if isinstance(times.dtype, pd.DatetimeTZDtype):
            times = times.dt.tz_localize(None)
        return times
    ts = pd.Timestamp(values)
    return ts.tz_localize(None) if ts.tzinfo is not None else ts

def clip_data(df: pd.DataFrame, time_column: str, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    if df.empty: return {}
    if time_column not in df.columns: raise ValueError(f"Column '{time_column}' not found in the uploaded file.")
    # Convert time column to wall-clock time
    times = _wall_clock(df[time_column])
    # Ensure times is a Series
    if not isinstance(times, pd.Series): raise TypeError("Expected a pandas Series for the time column.")
    if times.isna().all(): raise ValueError(f"Cannot parse values in column '{time_column}'.")
    # Filter by start and end time
    mask = pd.Series(True, index=df.index)
    if start is not None:
        start_time = _wall_clock(start)
        if not isinstance(start_time, pd.Timestamp): raise TypeError("Invalid start time.")
        mask &= times >= start_time
    if end is not None:
        end_time = _wall_clock(end)
        if not isinstance(end_time, pd.Timestamp): raise TypeError("Invalid end time.")
        mask &= times <= end_time
    df = df.loc[mask].copy()
    if df.empty: return {}
    # Prepare output
    content: dict[str, Any] = {"start": df[time_column].iloc[0], "end": df[time_column].iloc[-1]}
    # Replace infinite and missing values with None
    df = df.replace([np.inf, -np.inf], np.nan)
    content["data"] = (df.astype(object).where(df.notna(), None).to_numpy().tolist())
    return content

def read_failed_cases(calibration_dir: str) -> set:
    path = os.path.join(calibration_dir, FAILED_CASES_FILE)
    if not os.path.exists(path): return set()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return {int(x) for x in json.load(f)}
    except Exception: return set()

def _open_his(path: str):
    if path.endswith('.nc'): return xr.open_dataset(path)
    if path.endswith('.zarr'): return xr.open_zarr(path)
    return None

def _station_names(ds) -> list:
    return [
        n.decode('utf-8').strip() if isinstance(n, bytes) else str(n).strip()
        for n in ds['station_name'].values
    ]

def get_station_from_his_file(his_path: str) -> list:
    if not his_path or not os.path.exists(his_path): return []
    ds = _open_his(his_path)
    if ds is None: return []
    with ds: return _station_names(ds)

def parse_depths(depth_selection: str) -> list:
    depths = [x.strip() for x in str(depth_selection or '').split(",") if x.strip()]
    if not depths: raise ValueError("Please specify at least one depth.")
    for d in depths:
        try: float(d)
        except ValueError: raise ValueError(f"Invalid depth value: '{d}'.")
    return depths

def interpolate_profile_to_depths(group: pd.DataFrame, depths: list, time_col="Time") -> pd.DataFrame:
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

def split_temp_from_depth(df: pd.DataFrame, depths: list, time_col: str,
    upper: float = 5.0, lower: float = 1.5) -> pd.DataFrame:
    try:
        df = df.copy()
        df["temperature"] = pd.to_numeric(df["temperature"], errors='coerce')
        df["depth"] = pd.to_numeric(df["depth"], errors='coerce')
        df = df[[time_col, "temperature", "depth"]].dropna()
        if df.empty: return pd.DataFrame()
        df['new_profile'] = (df["depth"] < lower) & (df["depth"].shift(1) > upper)
        df['profile_id'] = df['new_profile'].cumsum()
        obs_df = (
            df.groupby("profile_id", group_keys=False)
            .apply(lambda g: interpolate_profile_to_depths(g, depths, time_col))
        )
        if obs_df.empty: return pd.DataFrame()
        obs_df = obs_df.sort_values(time_col).reset_index(drop=True)
        return obs_df.replace({np.nan: None})
    except Exception:
        logger.exception("split_temp_from_depth failed")
        return pd.DataFrame()

def remove_rolling_outliers(df: pd.DataFrame, column: str, window: int = 20, n_std: float = 3.0) -> pd.DataFrame:
    df_clean = df.copy()
    rolling = df_clean[column].rolling(window=window, center=True, min_periods=3)
    rolling_mean, rolling_std = rolling.mean(), rolling.std()
    lower = rolling_mean - n_std * rolling_std
    upper = rolling_mean + n_std * rolling_std
    mask = ((df_clean[column] >= lower) & (df_clean[column] <= upper)) | df_clean[column].isna() | rolling_std.isna()
    return df_clean[mask].copy()

def read_and_interpolate_his(his_path: str, station_name: str, depths: list, time_col="Time") -> pd.DataFrame:
    """Interpolate simulated temperature to fixed depths below the water surface.
    Returns a DataFrame indexed by UTC time with columns sim_<depth>."""
    try:
        if not his_path or not os.path.exists(his_path): return pd.DataFrame()
        ds = _open_his(his_path)
        if ds is None: return pd.DataFrame()
        with ds:
            names = _station_names(ds)
            if station_name not in names:
                logger.warning("Station '%s' not found in %s", station_name, his_path)
                return pd.DataFrame()
            missing = [v for v in ('temperature', 'zcoordinate_c', 'waterlevel') if v not in ds]
            if missing:
                logger.warning("Variables %s missing in %s (check Wrihis_* settings)", missing, his_path)
                return pd.DataFrame()
            st_idx = names.index(station_name)
            t_his = np.asarray(ds['temperature'].isel(stations=st_idx).values, dtype=float)
            z_his = np.asarray(ds['zcoordinate_c'].isel(stations=st_idx).values, dtype=float)
            wl_his = np.asarray(ds['waterlevel'].isel(stations=st_idx).values, dtype=float)
            sim_times = pd.to_datetime(ds['time'].values, utc=True)
        if t_his.ndim == 1: t_his, z_his = t_his[:, None], z_his[:, None]
        depth_num = np.asarray(depths, dtype=float)
        sim_interp = np.full((len(sim_times), len(depths)), np.nan)
        for i in range(len(sim_times)):
            t_row, z_row, wl = t_his[i, :], z_his[i, :], wl_his[i]
            mask = np.isfinite(t_row) & np.isfinite(z_row)
            if mask.sum() < 2 or not np.isfinite(wl): continue
            order = np.argsort(z_row[mask])
            z_sorted, t_sorted = z_row[mask][order], t_row[mask][order]
            tgt_elev = wl - depth_num
            in_bounds = (tgt_elev >= z_sorted[0]) & (tgt_elev <= z_sorted[-1])
            sim_interp[i, in_bounds] = np.interp(tgt_elev[in_bounds], z_sorted, t_sorted)
        return pd.DataFrame(
            sim_interp, columns=[f"sim_{d}" for d in depths], index=pd.Index(sim_times, name=time_col)
        )
    except Exception:
        logger.exception("read_and_interpolate_his failed for %s", his_path)
        return pd.DataFrame()

def align_sim_to_obs(sim: pd.Series, obs: pd.Series) -> pd.DataFrame:
    """Interpolate a simulated series (UTC DatetimeIndex) to observation times (UTC DatetimeIndex).
    Observations outside the simulated period are dropped (no extrapolation)."""
    empty = pd.DataFrame(columns=['obs', 'sim'])
    if obs is None or len(obs) == 0 or sim is None or len(sim) == 0: return empty
    sim = pd.to_numeric(sim, errors='coerce').dropna()
    sim = sim[~sim.index.duplicated(keep='first')].sort_index()
    if len(sim) < 2: return empty
    union = sim.index.union(obs.index)
    sim_interp = sim.reindex(union).interpolate(method='time', limit_area='inside')
    aligned = pd.DataFrame(
        {'obs': obs.to_numpy(dtype=float), 'sim': sim_interp.loc[obs.index].to_numpy(dtype=float)},
        index=obs.index
    )
    return aligned.dropna()

def target_score(target_method: str, obs: pd.Series, sim: pd.Series) -> float:
    if len(obs) < 2: return np.nan
    if target_method == "RMSE": return float(np.sqrt(mean_squared_error(obs, sim)))
    if target_method == "R2": return float(r2_score(obs, sim))
    raise ValueError(f"Unknown target method: '{target_method}'.")

def compute_weighted_score(depths: list, scores: list, method: str = 'equal', target: str = 'RMSE') -> float:
    """Combine per-depth scores. RMSE: sqrt(sum(w * rmse^2)); R2: sum(w * r2)."""
    depth_arr = np.array([float(d) for d in depths])
    score_arr = np.array(scores, dtype=float)
    valid = np.isfinite(depth_arr) & np.isfinite(score_arr)
    if target == 'RMSE': valid &= score_arr >= 0
    if method == 'surface': valid &= depth_arr > 0
    if not valid.any(): return np.nan
    v_depths, v_scores = depth_arr[valid], score_arr[valid]
    if method == 'surface': weights = 1.0 / v_depths
    elif method == 'deep': weights = v_depths.copy()
    elif method == 'equal': weights = np.ones(len(v_depths))
    else: raise ValueError(f"Unknown weight method: '{method}'.")
    if weights.sum() <= 0: return np.nan
    weights = weights / weights.sum()
    if target == 'RMSE': return float(np.sqrt(np.sum(weights * v_scores ** 2)))
    return float(np.sum(weights * v_scores))

# Surrogate model
def _cross_validate_gpr(fitted_gpr, X: np.ndarray, y: np.ndarray, max_folds: int = 5) -> dict:
    """K-fold CV with the fitted (fixed) kernel to estimate surrogate accuracy."""
    n_folds = min(max_folds, len(y))
    kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)
    pred = np.empty_like(y, dtype=float)
    for train, test in kf.split(X):
        model = GaussianProcessRegressor(
            kernel=fitted_gpr.kernel_, optimizer=None, alpha=fitted_gpr.alpha, normalize_y=True
        )
        model.fit(X[train], y[train])
        pred[test] = model.predict(X[test])
    return {"folds": n_folds, "rmse": float(np.sqrt(mean_squared_error(y, pred))), "q2": float(r2_score(y, pred))}

def surrogate_model(processes: dict, process_key: str, df: pd.DataFrame,
    model: str, directory: str, log_path: str) -> None:
    log = partial(functions.append_log, log_path)
    try:
        if model not in SURROGATE_MODELS: raise ValueError(f"Unsupported surrogate model: '{model}'.")
        log("Building surrogate model..."); log("===============================")
        target = df.columns[-1]
        df = df.replace([np.inf, -np.inf], np.nan).dropna()
        X, y = df.drop(columns=[target]), df[target].to_numpy(dtype=float)
        n_samples = len(df)
        if n_samples < max(5, X.shape[1] + 2):
            raise ValueError(f"Not enough valid samples ({n_samples}) to train the surrogate model.")
        log(f"Samples: {n_samples} | Parameters: {', '.join(X.columns)} | Target: {target}")
        scaler_X = StandardScaler()
        X_scaled = scaler_X.fit_transform(X)
        kernels_to_test = [
            (C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-3, 1e2)), 'RBF'),
            (C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2)) + WhiteKernel(1e-3, (1e-10, 1e-1)), 'RBF + White'),
            (C(1.0, (1e-3, 1e3)) * Matern(1.0, nu=1.5, length_scale_bounds=(1e-3, 1e2)), 'Matern_1.5'),
            (C(1.0, (1e-3, 1e3)) * Matern(1.0, nu=2.5, length_scale_bounds=(1e-3, 1e2)), 'Matern_2.5'),
            (C(1.0, (1e-3, 1e3)) * RationalQuadratic(1.0, 1.0, length_scale_bounds=(1e-9, 1e6)), 'RationalQuadratic'),
        ]
        best_ll, best_gpr, best_name = -np.inf, None, None
        for kernel_obj, k_name in kernels_to_test:
            if process_key not in processes:
                log("[WARNING] Surrogate process was removed."); return
            log(f"Testing kernel: {k_name} ...")
            try:
                gpr = GaussianProcessRegressor(
                    kernel=kernel_obj, n_restarts_optimizer=30,
                    alpha=1e-6, normalize_y=True, random_state=42
                )
                gpr.fit(X_scaled, y)
            except Exception as e:
                log(f"  Kernel {k_name} failed: {e}"); continue
            ll = gpr.log_marginal_likelihood_value_
            log(f"  log-marginal-likelihood = {ll:.3f} | fitted: {gpr.kernel_}")
            if ll > best_ll: best_ll, best_gpr, best_name = ll, gpr, k_name
        if best_gpr is None: raise RuntimeError("All kernels failed to fit.")
        log(f"Best kernel: {best_name} - {best_gpr.kernel_}")
        cv = _cross_validate_gpr(best_gpr, X_scaled, y)
        log(f"Cross-validation ({cv['folds']}-fold): RMSE = {cv['rmse']:.4f}, Q2 = {cv['q2']:.3f}")
        if cv['q2'] < 0.5:
            log("[WARNING] Low surrogate accuracy (Q2 < 0.5). Consider more resamples or narrower bounds.")
        # Save model
        model_dir = os.path.normpath(os.path.join(directory, 'model'))
        if os.path.exists(model_dir):
            log(f"Removing old model: {model_dir}."); shutil.rmtree(model_dir)
        os.makedirs(model_dir, exist_ok=True)
        for name, data in (('scaler_X', scaler_X), ('gpr_model', best_gpr)):
            log(f"Saving {name}...")
            with open(os.path.join(model_dir, f'{name}.pkl'), "wb") as f:
                pickle.dump(data, f)
        with open(os.path.join(model_dir, 'model_info.json'), 'w', encoding='utf-8') as f:
            json.dump({
                "kernel": best_name, "fitted_kernel": str(best_gpr.kernel_), "target": target,
                "parameters": list(X.columns), "n_samples": n_samples,
                "log_marginal_likelihood": float(best_ll), "cv": cv
            }, f, indent=4)
        log(f"Saved model: {model_dir}."); log("Model training completed."); log("===============================")
        _set_state(processes, process_key, "Model training completed.", 100.0, "finished", history=False)
    except Exception as e:
        _set_state(processes, process_key, f"Internal error: {e}", status="failed", history=False)
        log(f"[INTERNAL ERROR] {e}")

# Optuna optimization on the surrogate
def optuna_objective(trial, scaler_x, model, p_ranges):
    trial_params = {p: trial.suggest_float(p, bounds[0], bounds[1]) for p, bounds in p_ranges.items()}
    df_in = pd.DataFrame([trial_params], columns=list(p_ranges.keys()))
    return float(model.predict(scaler_x.transform(df_in))[0])

def optuna_callback(study, trial, processes, key, log_path):
    if trial.value is None:
        functions.append_log(log_path, f"Trial {trial.number}: {trial.state.name}")
    else:
        try: best = f"{study.best_value:.6f}"
        except ValueError: best = "n/a"
        functions.append_log(log_path,
            f"Trial {trial.number}: value={trial.value:.6f}, best={best}, params={trial.params}")
    info = processes.get(key)
    if info is None: study.stop(); return
    total = info.get("total") or 1
    info["current"] = trial.number + 1
    info["progress"] = round(min(100.0, (trial.number + 1) / total * 100), 1)

def run_optuna(processes: dict, key: str, direction: str,
    iterations: int, params_path: str, optuna_path: str, importance_path: str,
    scaler_path: str, model_path: str, log_path: str) -> None:
    log = partial(functions.append_log, log_path)
    try:
        with open(params_path, "r", encoding="utf-8") as f: parameters = json.load(f)
        with open(scaler_path, "rb") as f: scaler = pickle.load(f)
        with open(model_path, "rb") as f: model = pickle.load(f)
        feature_names = list(scaler.feature_names_in_)
        missing = [p for p in feature_names if p not in parameters]
        if missing:
            raise ValueError("Surrogate model does not match parameters.json (missing: " +
                ", ".join(missing) + "). Please rebuild the surrogate model.")
        params = {name: parameters[name] for name in feature_names}
        log(f"Optimization started: {iterations} trials, direction = {direction}.")
        study = optuna.create_study(
            sampler=optuna.samplers.TPESampler(seed=42), direction=direction,
            study_name='delft3d_fm_calibration'
        )
        study.optimize(
            partial(optuna_objective, scaler_x=scaler, model=model, p_ranges=params),
            n_trials=iterations,
            callbacks=[partial(optuna_callback, processes=processes, key=key, log_path=log_path)]
        )
        completed = [t for t in study.trials if t.value is not None]
        if not completed: raise RuntimeError("No trial completed.")
        df_trials = study.trials_dataframe()
        df_trials.to_csv(optuna_path)
        # Parameter importance
        try:
            importance = optuna.importance.get_param_importances(
                study, evaluator=MeanDecreaseImpurityImportanceEvaluator()
            )
        except Exception:
            param_cols = [c for c in df_trials.columns if c.startswith('params_')]
            X_tr = df_trials[param_cols].fillna(df_trials[param_cols].mean())
            fill = df_trials['value'].max() if direction == 'minimize' else df_trials['value'].min()
            y_tr = df_trials['value'].fillna(fill)
            rf = RandomForestRegressor(n_estimators=100, random_state=42).fit(X_tr, y_tr)
            importance = dict(zip([c.replace('params_', '') for c in param_cols], rf.feature_importances_))
        sorted_importance = {k: float(v) for k, v in sorted(importance.items(), key=lambda item: item[1])}
        with open(importance_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_importance, f, indent=4)
        best_params = {k: float(v) for k, v in study.best_params.items()}
        log(f"Best value: {study.best_value:.6f} | Best parameters: {best_params}")
        info = processes.get(key)
        if info: info["best_params"] = best_params
        _set_state(processes, key, "Optimization completed.", 100.0, "finished", history=False)
    except Exception as e:
        _set_state(processes, key, f"Internal error: {e}", status="failed", history=False)
        log(f"[INTERNAL ERROR] {e}")

def compare_simulation(his_file, station, depths, obs_df, t_start, t_end, time_zone):
    """Metrics of one simulation against the observations; None when there is no usable output."""
    sim_df = read_and_interpolate_his(his_file, station, depths, 'Time')
    if sim_df.empty: return None
    sim_df = sim_df[(sim_df.index >= to_utc(t_start)) & (sim_df.index <= to_utc(t_end))]
    if sim_df.empty: return None
    metrics, merge, rmses = {}, {}, []
    lo, hi = np.inf, -np.inf
    for depth in depths:
        obs_col, sim_col = f"obs_{depth}", f"sim_{depth}"
        aligned = align_sim_to_obs(sim_df[sim_col], obs_series(obs_df, depth))
        if aligned.empty:
            metrics[str(depth)], merge[str(depth)] = [None, None], []
            rmses.append(np.nan); continue
        merge_df = pd.DataFrame({'Time': aligned.index, obs_col: aligned['obs'].to_numpy(), sim_col: aligned['sim'].to_numpy()})
        merge_df['Time'] = functions.utc_to_local(merge_df['Time'], time_zone)
        merge[str(depth)] = get_records(merge_df)
        rmse = float(np.sqrt(mean_squared_error(aligned['obs'], aligned['sim'])))
        r2 = float(r2_score(aligned['obs'], aligned['sim'])) if len(aligned) >= 2 else np.nan
        metrics[str(depth)] = [num(rmse), num(r2)]
        lo = min(lo, aligned['obs'].min(), aligned['sim'].min())
        hi = max(hi, aligned['obs'].max(), aligned['sim'].max())
        rmses.append(rmse)
    sim_out = sim_df.reset_index()
    sim_out['Time'] = functions.utc_to_local(sim_out['Time'], time_zone)
    total_rmse = compute_weighted_score(depths, rmses, 'equal', 'RMSE')
    return {
        'sim': get_records(sim_out), 'metrics': metrics, 'merge': merge,
        'rmse': num(total_rmse, 4),
        'min_max': [float(lo), float(hi)] if np.isfinite(lo) else None
    }

