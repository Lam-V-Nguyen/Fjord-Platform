import os, shutil, json, asyncio
from fastapi import APIRouter, Request, Depends, Query
from fastapi import UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from config import PROJECT_ROOT
from services import calibration_functions, functions
import pandas as pd, numpy as np

router, processes = APIRouter(), {}


def _running_simulation(project_name: str):
    """Key ('baseline' / 'calibration') of a simulation that is still running for this project, or None.
    Checks this worker's memory AND the lock file on disk (a run started by another uvicorn worker,
    or orphaned solvers after a server restart)."""
    for k in ('baseline', 'calibration'):
        info = processes.get(f"{project_name}:{k}")
        if info and info.get('status') == 'running': return k
    other = calibration_functions.active_run(os.path.join(PROJECT_ROOT, project_name, "calibration"))
    if other: return other.get('key') or 'calibration'
    return None

def _progress_message(key: str, info: dict) -> str:
    progress, current, total = info.get("progress", 0.0), info.get("current", 0), info.get("total", 0)
    if key == 'baseline': return f'Baseline simulation: {progress}% completed.'
    msg = f'Simulation ({current}/{total} finished): {progress}% completed.'
    running_ids = info.get("running") or []
    return msg + (f" Running: {', '.join(map(str, running_ids))}." if running_ids else "")

def _disk_status(project_name: str, key: str):
    """Status of a batch this worker does not hold in `processes` (other worker / after a restart)."""
    cal_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
    st = calibration_functions.run_state(cal_dir)
    if st and st.get("key") == key:
        s = st.get("state") or {}
        if st["owner_alive"]:
            return {"status": "running", "progress": s.get("progress", 0.0), "attached": True,
                "logFile": s.get("log_file"), "message": _progress_message(key, s) if s else
                f"The {key} simulation started {st.get('started')} is running in another server process."
            }
        pids = ', '.join(map(str, st["solvers"]))
        return {"status": "orphaned", "progress": s.get("progress", 0.0), "solvers": st["solvers"], "message":
            f"The server process that started the {key} simulation ({st.get('started')}) is no longer running, "
            f"but {len(st['solvers'])} D-Flow FM process(es) are still active (PID {pids}). "
            "Their output is no longer read, so they cannot be completed: stop them and resume."
        }
    s = calibration_functions.read_run_status(cal_dir, key)
    if s and s.get("status") in ("finished", "stopped", "failed"):
        return {"status": s["status"], "progress": 100.0 if s["status"] == "finished" else s.get("progress", 0.0),
            "message": s.get("message", "")
        }
    return None

@router.post("/calibration_project")
async def calibration_project(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        name, time_zone = body.get('projectName'), body.get('timeZone')
        project_name, _ = functions.project_definer(name, user)
        input_dir = os.path.join(PROJECT_ROOT, project_name, "input")
        try: mdu_path = calibration_functions.find_mdu(input_dir)
        except FileNotFoundError:
            return JSONResponse({'status': 'error', 'message': f"MDU file not found in project '{project_name}'."})
        
        if not body.get('key') == '':
            calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
            os.makedirs(calibration_dir, exist_ok=True)
        mdu_content = calibration_functions.read_mdu(mdu_path)
        overrides = calibration_functions.load_overrides(os.path.join(PROJECT_ROOT, project_name, "calibration"))
        if overrides: mdu_content = calibration_functions.apply_overrides(mdu_content, overrides)
        info = calibration_functions.mdu_time_info(mdu_content)
        start_date = functions.utc_to_local(info["start"], time_zone)
        end_date = functions.utc_to_local(info["stop"], time_zone)
        content = {'start': start_date, 'end': end_date}
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e: functions.error("/calibration_project", e)

@router.post("/load_log_calibration")
async def load_log_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, folder = body.get('projectName'), body.get('folder')
        project_name, _ = functions.project_definer(project_name, user)
        log_path = os.path.join(PROJECT_ROOT, project_name, folder, 'logs', body.get('logFile'))
        if not os.path.exists(log_path):
            return JSONResponse({'status': 'error', 'message': f"LOG file not found in project '{project_name}'."})
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e: functions.error("/load_log_calibration", e)

@router.post("/get_mdu_keys")
async def get_mdu_keys(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        input_dir = os.path.join(PROJECT_ROOT, project_name, "input")
        try: mdu_path = calibration_functions.find_mdu(input_dir)
        except FileNotFoundError:
            return JSONResponse({'status': 'error', 'message': f"MDU file not found in project '{project_name}'."})
        with open(mdu_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = calibration_functions.get_all_mdu_keys(f.readlines())
        return JSONResponse({'content': content})
    except Exception as e: functions.error("/get_mdu_keys", e)

@router.post("/obs_calibration_upload")
async def obs_calibration_upload(file: UploadFile = File(...), projectName: str = Form(...),
    simStart: str = Form(...), simEnd: str = Form(...), user=Depends(functions.basic_auth)):
    try:
        project_name, _ = functions.project_definer(projectName, user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        os.makedirs(calibration_dir, exist_ok=True)
        path = os.path.join(calibration_dir, os.path.basename(file.filename))
        with open(path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                f.write(chunk)
        df = pd.read_csv(path, low_memory=False)
        content = calibration_functions.clip_data(df, 'Time', simStart, simEnd)
        if len(content) == 0:
            return JSONResponse({'status': 'error', 
                'message': f"No data in the uploaded CSV file falls within the simulation dates ({simStart} to {simEnd})."
            })
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e: functions.error("/obs_calibration_upload", e)

@router.post("/initial_check_calibration")
async def initial_check_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        project_dir, time_zone = os.path.join(PROJECT_ROOT, project_name), body.get('timeZone')
        measured, removed, qc = calibration_functions.observations(body.get('obsData'), time_zone)
        content = calibration_functions.initial_check(
            project_dir, project_name, measured, body.get('station'), time_zone
        )
        if not removed.empty: content["warnings"].insert(0, qc)
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e: functions.error("/initial_check_calibration", e)

@router.post("/save_overrides_calibration")
async def save_overrides_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        project_dir, time_zone = os.path.join(PROJECT_ROOT, project_name), body.get('timeZone')
        res = calibration_functions.save_overrides(project_dir, body.get('rows') or [], time_zone)
        return JSONResponse(res)
    except Exception as e: functions.error("/save_overrides_calibration", e)

@router.post("/check_sim_status_calibration")
async def check_sim_status_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    key = body.get('key'); process_key = f"{project_name}:{key}"
    info = processes.get(process_key)
    if not info:
        disk = _disk_status(project_name, key)
        if disk: return JSONResponse(disk)
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No simulation running'})
    status, progress = info.get("status"), info.get("progress", 0.0)
    if status == "stopped":
        return JSONResponse({"status": "stopped", "progress": progress, "message": info.get("message", "Simulation stopped.")})
    current, total = info.get("current", 0), info.get("total", 0)
    if status == "finished":
        return JSONResponse({"status": "finished", "progress": 100.0,
            "message": info.get("message", "Simulation completed.")
        })
    if status in ("failed", "error"):
        return JSONResponse({"status": status, "progress": progress,
            "message": info.get("message", "Simulation failed.")
        })
    running_ids = info.get("running") or []
    if key == 'baseline': complete = f'Baseline simulation: {progress}% completed.'
    else:
        complete = f'Simulation ({current}/{total} finished): {progress}% completed.'
        if running_ids: complete += f" Running: {', '.join(map(str, running_ids))}."
    return JSONResponse({"status": status, "progress": progress, "message": _progress_message(key, info)})

@router.post("/stop_sim_calibration")
async def stop_sim_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        key = body.get('key'); process_key = f"{project_name}:{key}"
        info = processes.get(process_key)
        if info and info.get("status") == "running":
            processes.pop(process_key, None)
            return JSONResponse({"status": "stopping", "message": "Stop requested - the running solvers are being stopped."})
        cal_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        return JSONResponse(calibration_functions.stop_run(cal_dir, key))
    except Exception as e: functions.error("/stop_sim_calibration", e)

@router.post("/scenario_status_calibration")
async def scenario_status_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        scenario_dir = os.path.join(PROJECT_ROOT, project_name, "calibration", "scenarios")
        content = calibration_functions.scenario_run_status(scenario_dir)
        if content["total"] == 0:
            return JSONResponse({"status": "error", "message": "No scenarios found. Please run 'Run Iterations' first."})
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e: functions.error("/scenario_status_calibration", e)

@router.post("/calibration_data_status")
async def calibration_data_status(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        content = calibration_functions.calibration_data_status(os.path.join(PROJECT_ROOT, project_name))
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e: functions.error("/calibration_data_status", e)

@router.post("/create_baseline_calibration")
async def create_baseline_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        running = _running_simulation(project_name)
        if running:   # create_baseline deletes calibration/baseline – never while a run uses it
            return JSONResponse({"status": "error", "message":
                f"The {running} simulation of '{project_name}' is still running. Please wait until it finishes."})
        case_dir = calibration_functions.create_baseline(os.path.join(PROJECT_ROOT, project_name))
        return JSONResponse({
            "status": "ok", "message": f"Baseline case created: .../{os.path.relpath(case_dir, PROJECT_ROOT)}"
        })
    except Exception as e: functions.error("/create_baseline_calibration", e)

@router.post("/start_sim_calibration", dependencies=[Depends(functions.require_sim_slot)])
async def start_sim_calibration(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        key, start_id, end_id = body.get('key'), body.get('iterationStart'), body.get('iterationEnd')
        redis, process_key = request.app.state.redis, f"{project_name}:{key}"
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        # Never start a second batch: neither baseline/calibration overlap, nor the same batch twice
        # (also when the first one runs in another uvicorn worker – checked via the lock file on disk).
        info = processes.get(process_key)
        if info and info.get("status") == "running":
            return JSONResponse({"status": "running", "progress": info.get("progress", 0.0),
                "message": f'Simulation {info.get("current", 0)} of {info.get("total", 0)}: {info.get("progress", 0.0)}% completed.'})
        running = _running_simulation(project_name)
        if running == key:                         # same batch in another worker / orphaned -> let the client attach
            disk = _disk_status(project_name, key)
            if disk and disk["status"] in ("running", "orphaned"): return JSONResponse(disk)
        if running:
            other = calibration_functions.active_run(calibration_dir) or {}
            since = f" (started {other['started']})" if other.get('started') else ""
            return JSONResponse({"status": "error", "message":
                f"The {running} simulation of '{project_name}' is still running{since}. Please wait until it finishes."})
        sample_dir = os.path.join(calibration_dir, body.get("samplingName") or "")
        if not os.path.exists(os.path.join(sample_dir, "scenarios")):
            return JSONResponse({"status": "error", "message": f"Sampling directory not found. Please run resampling first."})
        scenario_dir = os.path.join(sample_dir, "scenarios")
        list_sorted = sorted(
            int(x) for x in os.listdir(scenario_dir) if x.isdigit() and os.path.isdir(os.path.join(scenario_dir, x))
        )
        if not list_sorted:
            return JSONResponse({"status": "error", "message": "No simulation scenarios found."})
        all_ids = list(list_sorted)
        # Optional range From/To: only run scenarios start <= id <= end
        if start_id not in (None, '') or end_id not in (None, ''):
            start_id = int(start_id) if start_id not in (None, '') else list_sorted[0]
            end_id = int(end_id) if end_id not in (None, '') else list_sorted[-1]
            if start_id > end_id:
                return JSONResponse({"status": "error", "message": "'From' must not be larger than 'To'."})
            list_sorted = [c for c in list_sorted if start_id <= c <= end_id]
            if not list_sorted:
                return JSONResponse({"status": "error", "message":
                    f"No scenario between {start_id} and {end_id} (available: {all_ids[0]} - {all_ids[-1]})."})
        if body.get("skipCompleted"):              # resume: do not re-run scenarios that already finished
            done = set(calibration_functions.scenario_run_status(scenario_dir)["done"])
            list_sorted = [c for c in list_sorted if c not in done]
            if not list_sorted:
                return JSONResponse({"status": "error", "message": "All scenarios in this range are already completed."})
        # Optional: how many scenarios side by side and OpenMP threads per scenario (defaults: physical cores / 4, 4)
        parallel, threads = calibration_functions.default_parallel_config(
            len(list_sorted), body.get("parallelRuns"), body.get("threadsPerRun")
        )
        log_dir = os.path.join(calibration_dir, "logs"); os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, os.path.basename(body.get("logFile") or "log_calibration.txt"))
        lock = redis.lock(process_key, timeout=60, blocking_timeout=10)
        async with lock:
            info = processes.get(process_key)
            # Check if simulation already running
            if info and info["status"] == "running":
                complete = f'Simulation {info["current"]} of {info["total"]}: {info["progress"]}% completed.'
                return JSONResponse({"status": "running", "progress": info["progress"], "message": complete})
            # Lock on disk – visible to every worker; released by run_calibration when the batch ends
            lock_path, other = calibration_functions.acquire_run_lock(calibration_dir, key, list_sorted)
            if lock_path is None:
                return JSONResponse({"status": "error", "message":
                    f"A {other.get('key', '')} simulation of '{project_name}' is still running "
                    f"(started {other.get('started', '?')}). Please wait until it finishes."})
            # Remove old log only once we are sure no other batch is writing to it
            if os.path.exists(log_path) and list_sorted[0] == all_ids[0]: os.remove(log_path)
            processes[process_key] = {"progress": 0.0, "status": "running", "running": [],
                "message": "Preparing data for simulation...", "current": 0, "total": len(list_sorted),
            }
            background_tasks.add_task(
                calibration_functions.run_calibration, processes, process_key, scenario_dir, 
                list_sorted, log_path, parallel, threads, lock_path
            )
        return JSONResponse({"status": "ok", "message":
            f"Simulation {project_name} started: {len(list_sorted)} scenarios, {parallel} at a time, {threads} threads each."})
    except Exception as e: functions.error("/start_sim_calibration", e)

@router.get("/calibration_log_tail/{project_name}")
async def calibration_log_tail(project_name: str, offset: int = Query(0),
    log_file: str = Query(""), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    log_dir = os.path.join(PROJECT_ROOT, project_name, 'calibration', "logs")
    log_path = os.path.normpath(os.path.join(log_dir, os.path.basename(log_file)))
    if not os.path.exists(log_path): return {"lines": [], "offset": 0}
    size = os.path.getsize(log_path)
    skip_partial = offset < 0                  # offset = -N: only the last N bytes (attach to a long running log)
    if offset < 0: offset = max(0, size + offset)
    if offset > size: offset = 0
    with open(log_path, "rb") as f:
        f.seek(offset)
        data = f.read()
        new_offset = f.tell()
    if skip_partial and offset > 0 and b"\n" in data: data = data[data.index(b"\n") + 1:]   # drop the cut line
    text = data.decode("utf-8", errors="replace")
    return {"lines": text.splitlines(), "offset": new_offset}

@router.get("/calibration_log_full/{project_name}")
async def calibration_log_full(project_name: str, log_file: str = Query(""), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    log_dir = os.path.join(PROJECT_ROOT, project_name, "calibration", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.normpath(os.path.join(log_dir, os.path.basename(log_file)))
    if not os.path.exists(log_path): return {"content": ""}
    with open(log_path, "r", encoding=functions.encoding_detect(log_path), errors="replace") as f:
        content = f.read()
    return {"content": content, "offset": os.path.getsize(log_path)}

@router.post("/check_calibration")
async def check_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    key = f"{project_name}:{body.get('key')}"
    info = processes.get(key)
    if not info: 
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No calibration running.', "his": []})
    if info["status"] in ("finished", "stopped", "failed"):
        response = {"status": info["status"], "progress": info["progress"], "message": info["message"], "his": info["history"]}
        processes.pop(key, None)
        return JSONResponse(response)
    return JSONResponse({"status": info["status"], "progress": info["progress"], "message": info["message"], "his": info["history"]})

@router.post("/start_calibration_iteration")
async def start_calibration_iteration(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_path = os.path.join(PROJECT_ROOT, project_name, "input", 'calibration.json')
        if os.path.exists(calibration_path): functions.safe_remove(calibration_path)
        directory = os.path.join(PROJECT_ROOT, project_name)
        iterations, key_process = int(body.get("iterationNumber")), f"{project_name}:{body.get('key')}"
        if iterations <= 0: 
            return JSONResponse({'status': 'error', 'message': "Number of resamples must be a positive integer."})
        redis, params = request.app.state.redis, body.get('params')
        running = _running_simulation(project_name)
        if running:
            return JSONResponse({'status': 'error', 'message':
                f"The {running} simulation of '{project_name}' is still running. Please wait until it finishes."})
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            info = processes.get(key_process)
            if info and info.get("status") == "running":
                return JSONResponse({
                    "status": info["status"], "progress": info["progress"], "message": info["message"], "his": info["history"]
                })
            processes[key_process] = {
                "status": "running", "progress": 0.0, "history": '', "message": 'Preparing data for calibration ...'
            }
            background_tasks.add_task(
                calibration_functions.run_iteration, directory, processes, key_process, iterations, params
            )
        return JSONResponse({"status": "started", "progress": 0.0, "message": "Iteration started.", "his": ''})
    except Exception as e: functions.error("/start_calibration_iteration", e)

@router.post("/evaluate_baseline_calibration")
async def evaluate_baseline_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        time_col, time_zone, station = 'Time', body.get('timeZone'), body.get('station')
        depths = [x.strip() for x in str(body.get('depthSelection') or '').split(",") if x.strip()]
        measured, _, qc = calibration_functions.observations(body.get('obsData'), time_zone)
        obs_df = calibration_functions.split_temp_from_depth(measured, depths, time_col)
        if obs_df.empty: return JSONResponse({"status": "error", "message": "No observation profiles for the selected depths."})
        obs_df[time_col] = pd.to_datetime(obs_df[time_col], utc=True)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        runs, content = {"baseline": calibration_functions.find_his(os.path.join(calibration_dir, "baseline", "scenarios", "0")),
            "original": calibration_functions.hyd_his(project_name)
        }, {}
        for name, his in runs.items():
            if not his: continue
            sim = calibration_functions.read_and_interpolate_his(his, station, depths, time_col)
            if sim.empty: continue
            obs_in = obs_df[(obs_df[time_col] >= sim.index.min()) & (obs_df[time_col] <= sim.index.max())]
            rows = calibration_functions.score_depths(sim, obs_in, depths, time_col)
            rmse = calibration_functions.compute_weighted_rmse(depths, [r["rmse"] for r in rows])
            note = None if any(r["n"] for r in rows) else calibration_functions.match_diagnostics(sim, obs_in, depths, time_col)
            content[name] = {"his": os.path.basename(his), "rmse": None if not np.isfinite(rmse) else round(rmse, 3), "note": note,
                "rows": [{k: (None if isinstance(v, float) and not np.isfinite(v) else
                (round(v, 3) if isinstance(v, float) else v)) for k, v in r.items()} for r in rows]}
        if "baseline" not in content:
            return JSONResponse({
                "status": "error", "message": "Baseline output not found – run the baseline first.", "content": content
            })
        return JSONResponse({"status": "ok", "content": content, "qc": qc})
    except Exception as e: functions.error("/evaluate_baseline_calibration", e)

@router.post("/get_stations_calibration")
async def get_stations_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        scenario_dir = os.path.join(calibration_dir, 'scenarios')
        if not os.path.exists(scenario_dir):
            return JSONResponse({"status": "error", "message": f"Scenario directory not found. Please run iteration first."})
        list_sorted = sorted([int(x) for x in os.listdir(scenario_dir) if os.path.isdir(os.path.join(scenario_dir, x))])
        if not list_sorted:
            return JSONResponse({"status": "error", "message": "No simulation scenarios found. Please run simulation first."})
        his_path = next((h for h in (calibration_functions.find_his(os.path.join(scenario_dir, str(c)))
            for c in list_sorted) if h), None)
        if not his_path: 
            return JSONResponse({"status": "error", "message": "No simulation output found. Please run simulation first."})
        content = calibration_functions.get_station_from_his_file(his_path)
        if len(content) == 0: return JSONResponse({"status": "error", "message": "No station data found in the simulation."})
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e: functions.error("/get_stations_calibration", e)

@router.post("/summarize_calibration")
async def summarize_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        param_samples_path = os.path.join(calibration_dir, 'parameter_samples.csv')
        if not os.path.exists(param_samples_path):
            return JSONResponse({"status": "error", "message": f"Parameter samples file not found. Please run iteration first."})
        scenario_dir = os.path.join(calibration_dir, 'scenarios')
        if not os.path.exists(scenario_dir):
            return JSONResponse({"status": "error", "message": f"Scenario directory not found. Please run iteration first."})
        list_sorted = sorted([int(x) for x in os.listdir(scenario_dir) if os.path.isdir(os.path.join(scenario_dir, x))])
        if not list_sorted:
            return JSONResponse({"status": "error", "message": "No simulation scenarios found. Please run simulation first."})
        summary_dir = os.path.join(calibration_dir, 'summary')
        if os.path.exists(summary_dir): shutil.rmtree(summary_dir)
        os.makedirs(summary_dir, exist_ok=True)
        sim_start, sim_end = body.get('simStart'), body.get('simEnd')
        obs_start, obs_end = body.get('obsStart'), body.get('obsEnd')
        obs_station, depth_selection = body.get('station'), body.get('depthSelection')
        # Define period
        time_col, time_zone = 'Time', body.get('timeZone')
        sim_start = functions.local_to_utc(sim_start, time_zone)
        sim_end = functions.local_to_utc(sim_end, time_zone)
        obs_start = functions.local_to_utc(obs_start, time_zone)
        obs_end = functions.local_to_utc(obs_end, time_zone)
        t_start, t_end = max(sim_start, obs_start), min (sim_end, obs_end)
        depths_selected = [x.strip() for x in depth_selection.split(",")]
        summary_list, target_method, weight_method = [], body.get('targetValue'), body.get('weightValue')
        df_params = pd.read_csv(param_samples_path, index_col=0)
        # Process measured data
        measured_df, removed, qc = calibration_functions.observations(body.get('obsData'), time_zone)
        qc_path, qc_rows = os.path.join(calibration_dir, 'obs_qc_removed.csv'), []
        if not removed.empty:                      # removed values, for review (file + list for the browser tab)
            rem = removed.copy(); rem[time_col] = functions.utc_to_local(rem[time_col], time_zone)
            rem.to_csv(qc_path, index=False)
            qc_rows = rem.round({"temperature": 3}).replace({np.nan: None}).to_dict(orient="records")
        elif os.path.exists(qc_path): os.remove(qc_path)      # no stale list from an earlier summary
        # Detect measurement periods
        measured_df = measured_df[(measured_df[time_col] >= t_start) & (measured_df[time_col] <= t_end)]
        obs_df = calibration_functions.split_temp_from_depth(measured_df, depths_selected, time_col)
        if obs_df.empty:
            return JSONResponse({"status": "error", "message": "No observation profiles for the selected depths in the common period."})
        obs_path = os.path.join(calibration_dir, f'obs_{obs_station}.csv')
        # Convert to local timezone and save
        obs_df[time_col] = functions.utc_to_local(obs_df[time_col], time_zone)
        obs_df.to_csv(obs_path, index=False)
        metric_rows, metric_key = [], {"RMSE": "rmse", "R2": "r2", "MAE": "mae"}.get(target_method, "rmse")
        obs_df[time_col] = functions.local_to_utc(obs_df[time_col], time_zone)
        for case_id in list_sorted:
            if case_id not in df_params.index: continue
            his_file = calibration_functions.find_his(os.path.join(scenario_dir, str(case_id)))
            df_sim = calibration_functions.read_and_interpolate_his(his_file, obs_station, depths_selected, time_col)
            if df_sim.empty: continue
            out = df_sim.reset_index()
            out[time_col] = functions.utc_to_local(out[time_col], time_zone)
            out.to_csv(os.path.join(summary_dir, f"sim_{case_id}.csv"), index=False)
            scores = calibration_functions.score_depths(df_sim, obs_df, depths_selected, time_col)
            target = [r[metric_key] for r in scores]
            weighted = calibration_functions.compute_weighted_rmse(depths_selected, target, weight_method, target_method)
            row_params = df_params.loc[case_id].to_dict()
            row_params[target_method] = weighted
            summary_list.append(row_params)
            for r in scores: metric_rows.append({"case": case_id, **r})
        if metric_rows:
            pd.DataFrame(metric_rows).to_csv(os.path.join(calibration_dir, 'calibration_metrics.csv'), index=False)
        if not summary_list:
            return JSONResponse({"status": "error", "message": f"No scenario output contains station '{obs_station}'."})
        summary_df = pd.DataFrame(summary_list).dropna(subset=[target_method])
        summary_csv = os.path.join(calibration_dir, 'calibration_summary.csv')
        summary_df.to_csv(summary_csv, index=False)
        bias = pd.DataFrame(metric_rows).groupby("depth", sort=False)["bias"].mean()
        bias_txt = "\n   - ".join(f"{d} (m): {b:+.2f}" for d, b in bias.items() if np.isfinite(b))
        return JSONResponse({'status': 'ok', 'message': f'Calibration summary completed ({len(summary_df)} scenarios). '
            f'\nMean bias (sim - obs, °C):\n\n   - {bias_txt}\n\n{qc}',
            'qc': {'message': qc, 'file': 'obs_qc_removed.csv', 'removed': qc_rows}
        })
    except Exception as e: functions.error("/summarize_calibration", e)

@router.post("/correlation_calibration")
async def correlation_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        correlation_path = os.path.join(calibration_dir, 'calibration_summary.csv')
        if not os.path.exists(correlation_path):
            return JSONResponse({"status": "error", "message": "Calibration summary not found. Please run summarize first."})
        summary_df = pd.read_csv(correlation_path)
        columns = summary_df.columns.tolist()
        return JSONResponse({'content': summary_df.to_dict(orient='records'), 'key': columns[-1]})
    except Exception as e: functions.error("/correlation_calibration", e)

@router.post("/check_optimization_status_calibration")
async def check_optimization_status_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    process_key = f"{project_name}:{body.get('key')}"
    info = processes.get(process_key)
    if not info:
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No optimization running'})
    status, progress = info.get("status"), info.get("progress", 0.0)
    current, total = info.get("current", 0), info.get("total", 0)
    if status == "finished":
        processes.pop(process_key, None)
        return JSONResponse({"status": "finished", "progress": 100.0,
            "best_params": info.get("best_params"),
            "message": info.get("message", "Optimization completed.")
        })
    if status in ("failed", "error"):
        processes.pop(process_key, None)
        return JSONResponse({"status": status, "progress": progress,
            "best_params": info.get("best_params"), "message": info.get("message", "Simulation failed.")
        })
    complete = f'Optimization ({current}/{total}): {progress}% completed.'
    return JSONResponse({"status": status, "progress": progress, "message": complete})

@router.post("/start_optimization_calibration")
async def start_optimization_calibration(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, process_key = request.app.state.redis, f"{project_name}:{body.get('key')}"
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        correlation_path = os.path.join(calibration_dir, 'calibration_summary.csv')
        if not os.path.exists(correlation_path):
            return JSONResponse({"status": "error", "message": "Calibration summary not found. Please run summarize first."})
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            info = processes.get(process_key)
            # Check if simulation already running
            if info and info["status"] == "running":
                complete = f'Simulation ({info["current"]}/{info["total"]}): {info["progress"]}% completed.'
                return JSONResponse({"status": "running", "progress": info["progress"], "message": complete})
            processes[process_key] = {"progress": 0.0, "status": "running", "current": 0, "total": 0,
                "message": "Preparing data for optimization...",
            }
        summary_df = pd.read_csv(correlation_path)
        # Remove old log
        log_dir = os.path.join(calibration_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, body.get("logFile"))
        if os.path.exists(log_path): os.remove(log_path)
        background_tasks.add_task(
            calibration_functions.surrogate_model, processes, process_key,
            summary_df, body.get("model"), calibration_dir, log_path
        )
        return JSONResponse({"status": "ok", "message": f"Simulation {project_name} started"})
    except Exception as e: functions.error("/start_optimization_calibration", e)

@router.post("/start_optimization_optuna")
async def start_optimization_optuna(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, process_key = request.app.state.redis, f"{project_name}:{body.get('key')}"
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        direction, iterations = body.get('direction'), int(body.get('iterations'))
        params_path = os.path.join(calibration_dir, 'parameters.json')
        if not os.path.exists(params_path):
            return JSONResponse({"status": "error", "message": "Parameter file not found. Please 'Run Iterations' first."})
        model_dir = os.path.join(calibration_dir, "model")
        if not os.path.exists(model_dir):
            return JSONResponse({"status": "error", "message": "Model not found. Please 'Run Surrogate Model' first."})
        scaler_path = os.path.join(model_dir, "scaler_X.pkl")
        model_path = os.path.join(model_dir, "gpr_model.pkl")
        if not os.path.exists(scaler_path) or not os.path.exists(model_path):
            return JSONResponse({"status": "error", "message": "Scaler/Surrogate model not found. Please 'Run Surrogate Model' first."})
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            info = processes.get(process_key)
            # Check if optimization already running
            if info and info["status"] == "running":
                complete = f'Optimization ({info["current"]}/{info["total"]}): {info["progress"]}% completed.'
                return JSONResponse({"status": "running", "progress": info["progress"], "message": complete})
            processes[process_key] = {"progress": 0.0, "status": "running", "current": 0, "total": iterations,
                "message": "Preparing data for optimization...", "best_params": '',
            }
        # Remove old log
        log_dir = os.path.join(calibration_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, body.get("logFile"))
        optuna_path = os.path.join(calibration_dir, 'optuna.csv')
        importance_path = os.path.join(calibration_dir, 'optuna_importance.csv')
        if os.path.exists(log_path): os.remove(log_path)
        background_tasks.add_task(
            calibration_functions.run_optuna, processes, process_key, direction, iterations, 
            params_path, optuna_path, importance_path, scaler_path, model_path, log_path
        )
        return JSONResponse({"status": "ok", "message": f"Optimization {project_name} started"})
    except Exception as e: functions.error("/start_optimization_optuna", e)

@router.post("/plot_optuna")
async def plot_optuna(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        optuna_path = os.path.join(calibration_dir, 'optuna.csv')
        summary_path = os.path.join(calibration_dir, 'calibration_summary.csv')
        importance_path = os.path.join(calibration_dir, 'optuna_importance.csv')
        if not os.path.exists(optuna_path):
            return JSONResponse({"status": "error", "message": "File not found. Please 'Run Optimization' first."})
        optuna_df, summary_df = pd.read_csv(optuna_path), pd.read_csv(summary_path)
        columns = summary_df.columns.to_list()
        optuna_df = optuna_df.replace({np.nan: None})
        with open(importance_path, 'r') as f:
            importance = json.load(f)
        importance = [[key, value] for key, value in importance.items()]
        return JSONResponse({
            "status": "ok", "content": optuna_df.to_dict(orient='records'), 
            "importance": importance, "target": columns[-1]
        })
    except Exception as e: functions.error("/plot_optuna", e)

@router.post("/save_scenario_calibration")
async def save_scenario_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_old, _ = functions.project_definer(body.get('oldProject'), user)
        project_new, _ = functions.project_definer(body.get('newProject'), user)
        functions.require_owner(project_new, user)       # new scenario always goes into the user's own folder
        redis, key = request.app.state.redis, f"{project_old}:{project_new}"
        lock = redis.lock(key, timeout=1000, blocking_timeout=10)
        async with lock:
            old_dir = os.path.join(PROJECT_ROOT, project_old, 'input')
            new_dir = os.path.join(PROJECT_ROOT, project_new, 'input')
            if not os.path.exists(new_dir): os.makedirs(new_dir, exist_ok=True)
            params = { i['abbreviation']: i['value'] for i in body.get('params')}
            calibration_functions.copy_model_inputs(old_dir, new_dir)
            old_mdu = calibration_functions.find_mdu(old_dir)
            base_lines = calibration_functions.read_mdu(old_mdu)
            overrides = calibration_functions.load_overrides(os.path.join(PROJECT_ROOT, project_old, "calibration"))
            mdu_base = calibration_functions.apply_overrides(base_lines, overrides)
            ov_lines = list(mdu_base)          # Key_factor / Key[i] act on the overridden values (as in the scenarios)
            for name, val in params.items():
                mdu_base = calibration_functions.apply_param(mdu_base, ov_lines, name, float(val))
            for name, val in {"OutputDir": "DFM_OUTPUT", "WAQOutputDir": "DFM_DELWAQ"}.items():
                if calibration_functions.has_mdu_key(mdu_base, name):
                    mdu_base = calibration_functions.modify_mdu_key(mdu_base, name, val)
            with open(os.path.join(new_dir, os.path.basename(old_mdu)), 'w', encoding='utf-8') as f:
                f.writelines(mdu_base)
            return JSONResponse({"status": "ok", "message": f"Created scenario '{project_new}' successfully."})
    except Exception as e: functions.error("/save_scenario_calibration", e)

@router.post("/obs_comparison_upload")
async def obs_comparison_upload(file: UploadFile = File(...), simStart: str = Form(...), simEnd: str = Form(...)):
    try:
        df = pd.read_csv(file.file, low_memory=False)
        content = calibration_functions.clip_data(df, 'Time', simStart, simEnd)
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e: functions.error("/obs_comparison_upload", e)

@router.post("/get_stations_comparison")
async def get_stations_comparison(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        name = body.get('projectName')
        project_name, _ = functions.project_definer(name, user)
        redis, key = request.app.state.redis, f"{project_name}:station"
        his_path = calibration_functions.hyd_his(project_name) or \
            os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "output", 'HYD', "FlowFM_his.zarr"))
        if not os.path.exists(his_path):
            path = his_path[his_path.index(name):].replace("\\", "/")
            return JSONResponse({"status": "error", "message": f"No simulation found at: .../{path}\nConsider rerun the simulation."})
        lock = redis.lock(key, timeout=1000, blocking_timeout=10)
        async with lock:
            content = calibration_functions.get_station_from_his_file(his_path)
            if len(content) == 0: return JSONResponse({"status": "error", "message": "No station data found in the simulation."})
            return JSONResponse({"status": "ok", "content": content})
    except Exception as e: functions.error("/get_stations_comparison", e)

@router.post("/comparison_plot")
async def comparison_plot(request: Request, user=Depends(functions.basic_auth)):
    """Observations vs the calibrated scenario (projectName) and, optionally, the initial scenario (initialProject)."""
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        initial_name = (body.get('initialProject') or '').strip()
        initial_project = functions.project_definer(initial_name, user)[0] if initial_name else None
        time_col, time_zone = 'Time', body.get('timeZone')
        station, obs_data = body.get('station'), body.get('obsData')
        redis, process_key = request.app.state.redis, f"{project_name}:comparison"
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            # Define period (the period of the calibrated scenario, as opened in the form)
            start_sim, end_sim = body.get('simStart'), body.get('simEnd')
            start_obs, end_obs = body.get('obsStart'), body.get('obsEnd')
            depths_selected = [x.strip() for x in body.get('depthSelection').split(",") if x.strip()]
            measured_df, removed, qc = calibration_functions.observations(obs_data, time_zone)
            sim_start, sim_end = functions.local_to_utc(start_sim, time_zone), functions.local_to_utc(end_sim, time_zone)
            obs_start, obs_end = functions.local_to_utc(start_obs, time_zone), functions.local_to_utc(end_obs, time_zone)
            t_start, t_end = max(sim_start, obs_start), min(sim_end, obs_end)
            if t_start > t_end:
                return JSONResponse({"status": "error", "message": "Simulation and observation periods do not overlap."})
            measured_df = measured_df[(measured_df[time_col] >= t_start) & (measured_df[time_col] <= t_end)]
            obs_df = calibration_functions.split_temp_from_depth(measured_df, depths_selected, time_col)
            if len(obs_df) == 0:
                return JSONResponse({"status": "error", "message": f"No observation found between: {start_obs} and {end_obs}"})
            obs_df = obs_df.reset_index(drop=True)
            calibrated = calibration_functions.compare_run(calibration_functions.hyd_his(project_name), station, depths_selected,
                obs_df, t_start, t_end, time_col, time_zone)
            if calibrated is None:
                return JSONResponse({"status": "error", "message":
                    f"No simulation of station '{station}' found in '{project_name}' between {start_sim} and {end_sim}."})
            initial, note = None, None
            if initial_project:
                initial =  calibration_functions.compare_run(calibration_functions.hyd_his(initial_project), station, depths_selected,
                    obs_df, t_start, t_end, time_col, time_zone)
                if initial is None:
                    note = f"Initial scenario '{initial_name}': no output for station '{station}' in the comparison period."
                else: initial["project"] = initial_name
            ranges = [r["min_max"] for r in (calibrated, initial) if r and r["min_max"][0] is not None]
            min_max = [min(r[0] for r in ranges), max(r[1] for r in ranges)] if ranges else [0, 1]
            obs_df[time_col] = functions.utc_to_local(obs_df[time_col], time_zone)
            # Values removed by the QC in the comparison period: plotted (grey x), not used in the metrics
            removed = removed[(removed[time_col] >= t_start) & (removed[time_col] <= t_end)].copy()
            removed[time_col] = functions.utc_to_local(removed[time_col], time_zone)
            content = {
                'sim': calibrated["sim"], 'depth': depths_selected, 'obs': obs_df.replace({np.nan: None}).to_dict(orient="records"),
                'metrics': calibrated["metrics"], 'station': station, "rmse": calibrated["rmse"], "merge": calibrated["merge"],
                "min_max": min_max, "project": body.get('projectName'), "initial": initial, "note": note,
                "removed": removed.replace({np.nan: None}).to_dict(orient="records"), "qc": qc
            }
            return JSONResponse({"status": "ok", "content": content})
    except Exception as e: functions.error("/comparison_plot", e)

@router.post("/finish_calibration")
async def finish_calibration(request: Request, user=Depends(functions.basic_auth)):
    extend_task = None
    try:
        body = await request.json()
        old_project, new_project = body.get('oldName'), body.get('newName')
        project_old, _ = functions.project_definer(old_project, user)
        project_new, _ = functions.project_definer(new_project, user)
        checked = body.get('delete', False)
        old_dir = os.path.join(PROJECT_ROOT, project_old)
        new_dir = os.path.join(PROJECT_ROOT, project_new)
        redis = request.app.state.redis
        lock = redis.lock(f"{project_old}:copy_project", timeout=30)
        async with lock:
            # Optional: auto-extend lock if deletion may take long
            extend_task = asyncio.create_task(functions.auto_extend(lock))
            calibration_dir = os.path.join(new_dir, 'calibration')
            if os.path.exists(calibration_dir): functions.safe_remove(calibration_dir)
            folders = ['DFM_DELWAQ', 'input', 'output/HYD']
            for item in folders:
                temp_dir = os.path.join(new_dir, item)
                if os.path.exists(temp_dir): functions.safe_remove(temp_dir)
                shutil.copytree(os.path.join(old_dir, item), temp_dir)
            log_file = os.path.join(old_dir, 'logs', 'log_hyd.txt')
            if os.path.exists(log_file): 
                shutil.copy2(log_file, os.path.join(new_dir, 'logs', 'log_hyd.txt'))
        if checked == True: functions.safe_remove(old_dir)
        calibration_path = os.path.join(PROJECT_ROOT, project_new, "input", 'calibration.json')
        with open(calibration_path, 'w', encoding='utf-8') as f:
            json.dump({'calibration': True}, f)
        return JSONResponse({"status": "ok", 'message': f"Scenario '{new_project}' is overwritten successfully."})
    except Exception as e: functions.error("/finish_calibration", e)
    finally:
        if extend_task:
            extend_task.cancel()
            try: await extend_task
            except asyncio.CancelledError: pass
