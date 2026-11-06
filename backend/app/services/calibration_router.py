import os, traceback, shutil, json
from fastapi import APIRouter, Request, Depends
from fastapi import UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from config import PROJECT_ROOT
from services import calibration_functions, functions
import pandas as pd, numpy as np
from datetime import datetime

router, processes = APIRouter(), {}

def _is_running(key: str) -> bool:
    info = processes.get(key)
    return bool(info) and info.get('status') == 'running'

@router.post("/calibration_project")
async def calibration_project(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, time_zone = body.get('projectName'), body.get('timeZone')
        project_name, _ = functions.project_definer(project_name, user)
        input_dir, folder = os.path.join(PROJECT_ROOT, project_name, "input"), body.get('folder') or ''
        mdu_path = calibration_functions.find_mdu_file(input_dir)
        if mdu_path is None:
            return JSONResponse({'status': 'error', 'message': f"MDU file not found in project '{project_name}'."})
        if folder:
            calibration_dir = os.path.join(PROJECT_ROOT, project_name, folder)
            os.makedirs(calibration_dir, exist_ok=True)
        with open(mdu_path, 'r', encoding='utf-8', errors='ignore') as mdu_file:
            mdu_content = mdu_file.readlines()
        # Extract start and end dates from MDU file
        start_dt, end_dt = calibration_functions.get_simulation_period(mdu_content)
        start_date = functions.utc_to_local(start_dt, time_zone)
        end_date = functions.utc_to_local(end_dt, time_zone)
        return JSONResponse({'status': 'ok', 'content': {'start': start_date, 'end': end_date}})
    except Exception as e:
        print('/calibration_project:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

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
    except Exception as e:
        print('/load_log_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/calibration_log_tail")
async def calibration_log_tail(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        result = calibration_functions.read_log_tail(
            calibration_functions.get_log_path(project_name, body.get('logFile')), int(body.get('offset') or 0)
        )
        return JSONResponse({"status": "ok", **result})
    except Exception as e:
        print('/calibration_log_tail:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/calibration_log_full")
async def calibration_log_full(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        log_path = calibration_functions.get_log_path(project_name, body.get('logFile'))
        if not os.path.exists(log_path): return JSONResponse({"status": "ok", "content": "", "offset": 0})
        with open(log_path, "rb") as f: data = f.read()
        return JSONResponse({"status": "ok", "content": data.decode("utf-8", errors="replace"), "offset": len(data)})
    except Exception as e:
        print('/calibration_log_full:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/start_calibration_iteration")
async def start_calibration_iteration(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        directory = os.path.join(PROJECT_ROOT, project_name)
        iterations, key_process = int(body.get('iterationNumber')), f"{project_name}:{body.get('key')}"
        if iterations <= 0: 
            return JSONResponse({'status': 'error', 'message': "Number of resamples must be a positive integer."})
        redis, params = request.app.state.redis, body.get('params')
        if _is_running(f"{project_name}:calibration"):
            return JSONResponse({'status': 'error', 'message': "A calibration simulation is running. Please wait until it finishes."})
        lock = redis.lock(key_process, timeout=1000, blocking_timeout=10)
        async with lock:
            if _is_running(key_process):
                info = processes[key_process]
                return JSONResponse({
                    "status": info["status"], "progress": info["progress"], "message": info["message"], "his": info["history"]
                })
            processes[key_process] = {
                "status": "running", "progress": 0.0, "history": '', "message": 'Preparing data for calibration ...'
            }
            background_tasks.add_task(calibration_functions.run_iteration, directory, processes, key_process, iterations, params)
        return JSONResponse({"status": "started", "progress": 0.0, "message": "Iteration started.", "his": ''})
    except Exception as e:
        print('/start_calibration_iteration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/check_calibration")
async def check_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    key_process = f"{project_name}:{body.get('key')}"
    info = processes.get(key_process)
    if not info:
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No calibration running.', "his": ''})
    response = {"status": info["status"], "progress": info["progress"], "message": info["message"], "his": info["history"]}
    if info["status"] in ("finished", "stopped", "failed"): processes.pop(key_process, None)
    return JSONResponse(response)

@router.post("/check_optimization_status_calibration")
async def check_optimization_status_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    key_process = f"{project_name}:{body.get('key')}"
    info = processes.get(key_process)
    if not info:
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No optimization running.'})
    status, progress = info.get("status"), info.get("progress", 0.0)
    if status == "finished":
        processes.pop(key_process, None)
        return JSONResponse({"status": "finished", "progress": 100.0, "best_params": info.get("best_params"),
            "message": info.get("message", "Optimization completed.")})
    if status in ("failed", "error"):
        processes.pop(key_process, None)
        return JSONResponse({"status": "failed", "progress": progress, "best_params": info.get("best_params"),
            "message": info.get("message", "Optimization failed.")})
    current, total = info.get("current", 0), info.get("total", 0)
    message = f'Optimization ({current}/{total}): {progress}% completed.' if total else info.get("message", "")
    return JSONResponse({"status": status, "progress": progress, "message": message})

@router.post("/check_sim_status_calibration")
async def check_sim_status_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    key_process = f"{project_name}:{body.get('key')}"
    info = processes.get(key_process)
    if not info:
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No simulation running.'})
    status, progress = info.get("status"), info.get("progress", 0.0)
    if status == "finished":
        processes.pop(key_process, None)
        return JSONResponse({"status": "finished", "progress": 100.0, "message": info.get("message", "\nSimulation completed.")})
    if status in ("failed", "error"):
        processes.pop(key_process, None)
        return JSONResponse({"status": "failed", "progress": progress, "message": info.get("message", "Simulation failed.")})
    current, total = info.get("current", 0), info.get("total", 0)
    return JSONResponse({"status": status, "progress": progress, "message": f'Simulation ({current}/{total}): {progress}% completed.'})

@router.post("/start_sim_calibration")
async def start_sim_calibration(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, key_process = request.app.state.redis, f"{project_name}:{body.get('key')}"
        if _is_running(f"{project_name}:iteration"):
            return JSONResponse({'status': 'error', 'message': "Resampling is running. Please wait until it finishes."})
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        # A run started by another worker / before an auto-reload is not in `processes`
        owner = calibration_functions.read_run_lock(calibration_dir)
        if owner and not _is_running(key_process):
            return JSONResponse({'status': 'error', 'message':
                f"A simulation run is already active for this project (pid {owner.get('pid')}, "
                f"started {owner.get('started')}). Wait until it finishes or stop that process first."})
        scenario_dir = os.path.join(calibration_dir, "scenarios")
        start, end = int(body.get('start')), int(body.get('end'))
        if not os.path.exists(scenario_dir):
            return JSONResponse({'status': 'error', 'message': "Scenario directory not found. Please run iteration first."})
        list_sorted = sorted(
            int(x) for x in os.listdir(scenario_dir) if x.isdigit() 
            and os.path.isdir(os.path.join(scenario_dir, x)) and start <= int(x) <= end
        )
        if not list_sorted: 
            return JSONResponse({'status': 'error', 'message': "No simulation scenarios found."})
        log_path = calibration_functions.get_log_path(project_name, body.get("logFile"))
        lock = redis.lock(key_process, timeout=60, blocking_timeout=10)
        async with lock:
            info = processes.get(key_process)
            if info and info["status"] == "running":
                complete = f'Simulation ({info["current"]}/{info["total"]}): {info["progress"]}% completed.'
                return JSONResponse({"status": "running", "progress": info["progress"], "message": complete})
            # Remove old log and the list of failed cases
            if os.path.exists(log_path): os.remove(log_path)
            processes[key_process] = {"progress": 0.0, "status": "running",
                "message": "Preparing data for simulation...", "current": 0, "total": len(list_sorted),
            }
            background_tasks.add_task(calibration_functions.run_calibration, processes, key_process, scenario_dir, list_sorted, log_path)
        return JSONResponse({"status": "ok", "message": f"Simulation {project_name} started"})
    except Exception as e:
        print('/start_sim_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/obs_calibration_upload")
async def obs_calibration_upload(file: UploadFile = File(...), projectName: str = Form(...),
    simStart: str = Form(...), simEnd: str = Form(...), user=Depends(functions.basic_auth)):
    try:
        project_name, _ = functions.project_definer(projectName, user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        os.makedirs(calibration_dir, exist_ok=True)
        path = calibration_functions.safe_path(calibration_dir, file.filename)
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
    except Exception as e:
        print('/obs_calibration_upload:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/get_stations_calibration")
async def get_stations_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        scenario_dir = os.path.join(calibration_dir, "scenarios")
        if not os.path.exists(scenario_dir):
            return JSONResponse({'status': 'error', 'message': "Scenario directory not found. Please run iteration first."})
        list_sorted = sorted([int(x) for x in os.listdir(scenario_dir) if x.isdigit() and os.path.isdir(os.path.join(scenario_dir, x))])
        if not list_sorted:
            return JSONResponse({'status': 'error', 'message': "No simulation scenarios found. Please run iteration first."})
        failed = calibration_functions.read_failed_cases(calibration_dir)
        his_path = next((
            p for p in (calibration_functions.find_his_file(os.path.join(scenario_dir, str(c))) for c in list_sorted if c not in failed)
            if p is not None
        ), None)
        if his_path is None:
            return JSONResponse({'status': 'error', 'message': "No simulation output found. Please run simulation first."})
        content = calibration_functions.get_station_from_his_file(his_path)
        if len(content) == 0: 
            return JSONResponse({'status': 'error', 'message': "No station data found in the simulation."})
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e:
        print('/get_stations_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/summarize_calibration")
async def summarize_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        param_samples_path = os.path.join(calibration_dir, 'parameter_samples.csv')
        if not os.path.exists(param_samples_path):
            return JSONResponse({'status': 'error', 'message': "Parameter samples file not found. Please run iteration first."})
        scenario_dir = os.path.join(calibration_dir, "scenarios")
        list_sorted = sorted([int(x) for x in os.listdir(scenario_dir) if x.isdigit() and os.path.isdir(os.path.join(scenario_dir, x))])
        if not list_sorted:
            return JSONResponse({'status': 'error', 'message': "No simulation scenarios found. Please run iteration first."})
        if _is_running(f"{project_name}:calibration"):
            return JSONResponse({'status': 'error', 'message': "Simulation is still running. Please wait until it finishes."})
        target_method, weight_method = body.get('targetValue'), body.get('weightValue')
        if target_method not in calibration_functions.TARGET_METHODS: 
            return JSONResponse({'status': 'error', 'message': f"Unknown target: '{target_method}'."})
        if weight_method not in calibration_functions.WEIGHT_METHODS: 
            return JSONResponse({'status': 'error', 'message': f"Unknown weight method: '{weight_method}'."})
        obs_station, time_zone = body.get('station'), body.get('timeZone')
        depths_selected = calibration_functions.parse_depths(body.get('depthSelection'))
        # Define overlapping period (UTC)
        sim_start = functions.local_to_utc(body.get('simStart'), time_zone)
        sim_end = functions.local_to_utc(body.get('simEnd'), time_zone)
        obs_start = functions.local_to_utc(body.get('obsStart'), time_zone)
        obs_end = functions.local_to_utc(body.get('obsEnd'), time_zone)
        t_start, t_end = max(sim_start, obs_start), min(sim_end, obs_end)
        if t_start > t_end: 
            return JSONResponse({'status': 'error', 'message': "Simulation and observation periods do not overlap."})
        # Observations
        measured_df = pd.DataFrame(data=body.get('obsData'), columns=["Time", 'temperature', 'depth'])
        measured_df["Time"] = functions.local_to_utc(measured_df["Time"], time_zone)
        measured_df = measured_df[(measured_df["Time"] >= t_start) & (measured_df["Time"] <= t_end)]
        obs_df = calibration_functions.split_temp_from_depth(measured_df, depths_selected, "Time")
        if obs_df.empty: 
            return JSONResponse({'status': 'error', 'message': "No observation profiles found within the overlapping period."})
        obs_save = obs_df.copy()
        obs_save["Time"] = functions.utc_to_local(obs_save["Time"], time_zone)
        obs_save.to_csv(os.path.join(calibration_dir, f'obs_{calibration_functions.safe_token(obs_station)}.csv'), index=False)
        # Clean observations ONCE (they are the same for every scenario)
        obs_clean = {}
        for d in depths_selected:
            series = calibration_functions.obs_series(obs_df, d)
            if not series.empty: 
                col = f"obs_{d}"
                series = calibration_functions.remove_rolling_outliers(series.to_frame(), col)[col]
            obs_clean[d] = series
        if all(len(s) == 0 for s in obs_clean.values()):
            return JSONResponse({'status': 'error', 'message': "No observation values at the selected depths."})
        # Prepare summary folder
        summary_dir = os.path.join(calibration_dir, 'summary')
        if os.path.exists(summary_dir): shutil.rmtree(summary_dir)
        os.makedirs(summary_dir, exist_ok=True)
        df_params = pd.read_csv(param_samples_path, index_col=0)
        failed_cases = calibration_functions.read_failed_cases(calibration_dir)
        summary_list, skipped = [], []
        for case_id in list_sorted:
            if case_id in failed_cases or case_id not in df_params.index:
                skipped.append(case_id); continue
            his_file = calibration_functions.find_his_file(os.path.join(scenario_dir, str(case_id)))
            df_sim = calibration_functions.read_and_interpolate_his(his_file, obs_station, depths_selected, "Time") if his_file else pd.DataFrame()
            if df_sim.empty:
                skipped.append(case_id); continue
            df_save = df_sim.reset_index()
            df_save["Time"] = functions.utc_to_local(df_save["Time"], time_zone)
            df_save.to_csv(os.path.join(summary_dir, f"sim_{case_id}.csv"), index=False)
            scores = []
            for d in depths_selected:
                aligned = calibration_functions.align_sim_to_obs(df_sim[f"sim_{d}"], obs_clean[d])
                scores.append(calibration_functions.target_score(target_method, aligned['obs'], aligned['sim']) if len(aligned) >= 2 else np.nan)
            weighted = calibration_functions.compute_weighted_score(depths_selected, scores, weight_method, target_method)
            if not np.isfinite(weighted):
                skipped.append(case_id); continue
            row_params = df_params.loc[case_id].to_dict()
            row_params[target_method] = weighted
            summary_list.append(row_params)
        if not summary_list:
            return JSONResponse({'status': 'error', 'message': "No valid scenario could be compared with the observations."})
        summary_df = pd.DataFrame(summary_list)
        summary_df.to_csv(os.path.join(calibration_dir, 'calibration_summary.csv'), index=False)
        message = f"Calibration summary completed: {len(summary_list)} scenario(s) used."
        if skipped:
            shown = ", ".join(str(c) for c in skipped[:20]) + (" ..." if len(skipped) > 20 else "")
            message += f"\n{len(skipped)} scenario(s) skipped (failed or no valid output): {shown}"
        return JSONResponse({'status': 'ok', 'message': message})
    except Exception as e:
        print('/summarize_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/correlation_calibration")
async def correlation_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        correlation_path = os.path.join(calibration_dir, 'calibration_summary.csv')
        if not os.path.exists(correlation_path):
            return JSONResponse({'status': 'error', 'message': "Calibration summary not found. Please run summarize first."})
        summary_df = pd.read_csv(correlation_path)
        return JSONResponse({'status': 'ok', 'content': calibration_functions.get_records(summary_df), 'key': summary_df.columns[-1]})
    except Exception as e:
        print('/correlation_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/start_optimization_calibration")
async def start_optimization_calibration(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, process_key = request.app.state.redis, f"{project_name}:{body.get('key')}"
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        model = body.get("model")
        if model not in calibration_functions.SURROGATE_MODELS:
            return JSONResponse({'status': 'error', 'message': f"Unsupported surrogate model: '{model}'."})
        correlation_path = os.path.join(calibration_dir, 'calibration_summary.csv')
        if not os.path.exists(correlation_path):
            return JSONResponse({'status': 'error', 'message': "Calibration summary not found. Please run summarize first."})
        if _is_running(f"{project_name}:optuna"):
            return JSONResponse({'status': 'error', 'message': "Optimization is running. Please wait until it finishes."})
        summary_df = pd.read_csv(correlation_path)
        if summary_df.empty: 
            return JSONResponse({'status': 'error', 'message': "Calibration summary is empty."})
        log_path = calibration_functions.get_log_path(project_name, body.get("logFile"))
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            info = processes.get(process_key)
            if info and info["status"] == "running":
                return JSONResponse({"status": "running", "progress": info["progress"], "message": info["message"]})
            if os.path.exists(log_path): os.remove(log_path)
            processes[process_key] = {"progress": 0.0, "status": "running", "current": 0, "total": 0,
                "message": "Building surrogate model...", "history": ''}
            background_tasks.add_task(calibration_functions.surrogate_model, processes, process_key,
                summary_df, model, calibration_dir, log_path)
        return JSONResponse({"status": "ok", "message": f"Surrogate model for {project_name} started"})
    except Exception as e:
        print('/start_optimization_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/start_optimization_optuna")
async def start_optimization_optuna(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, process_key = request.app.state.redis, f"{project_name}:{body.get('key')}"
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        iterations = int(body.get('iterations') or 0)
        if iterations <= 0: 
            return JSONResponse({'status': 'error', 'message': "Number of iterations must be a positive integer."})
        params_path = os.path.join(calibration_dir, 'parameters.json')
        if not os.path.exists(params_path):
            return JSONResponse({'status': 'error', 'message': "Parameter file not found. Please 'Run Iterations' first."})
        summary_path = os.path.join(calibration_dir, 'calibration_summary.csv')
        if not os.path.exists(summary_path):
            return JSONResponse({'status': 'error', 'message': "Calibration summary not found. Please run summarize first."})
        model_dir = os.path.join(calibration_dir, "model")
        scaler_path = os.path.join(model_dir, "scaler_X.pkl")
        model_path = os.path.join(model_dir, "gpr_model.pkl")
        if not os.path.exists(scaler_path) or not os.path.exists(model_path):
            return JSONResponse({'status': 'error', 'message': "Scaler/Surrogate model not found. Please 'Run Surrogate Model' first."})
        if _is_running(f"{project_name}:surrogate_model"):
            return JSONResponse({'status': 'error', 'message': "Surrogate model is being built. Please wait until it finishes."})
        # The direction follows the calibration target (RMSE -> minimize, R2 -> maximize)
        target = pd.read_csv(summary_path, nrows=0).columns[-1]
        direction = 'maximize' if target in calibration_functions.MAXIMIZE_TARGETS else 'minimize'
        requested = body.get('direction')
        log_path = calibration_functions.get_log_path(project_name, body.get("logFile"))
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            info = processes.get(process_key)
            if info and info["status"] == "running":
                complete = f'Optimization ({info["current"]}/{info["total"]}): {info["progress"]}% completed.'
                return JSONResponse({"status": "running", "progress": info["progress"], "message": complete})
            if os.path.exists(log_path): os.remove(log_path)
            if requested and requested != direction:
                functions.append_log(log_path,
                    f"[INFO] Direction changed from '{requested}' to '{direction}' to match target '{target}'."
                )
            processes[process_key] = {"progress": 0.0, "status": "running", "current": 0, "total": iterations,
                "message": "Preparing data for optimization...", "best_params": '', "history": ''}
            background_tasks.add_task(
                calibration_functions.run_optuna, processes, process_key, direction, iterations, params_path,
                os.path.join(calibration_dir, 'optuna.csv'), os.path.join(calibration_dir, 'optuna_importance.csv'),
                scaler_path, model_path, log_path
            )
        return JSONResponse({"status": "ok", "message": f"Optimization {project_name} started", "direction": direction})
    except Exception as e:
        print('/start_optimization_optuna:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/plot_optuna")
async def plot_optuna(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        optuna_path = os.path.join(calibration_dir, 'optuna.csv')
        summary_path = os.path.join(calibration_dir, 'calibration_summary.csv')
        importance_path = os.path.join(calibration_dir, 'optuna_importance.csv')
        for path in (optuna_path, summary_path, importance_path):
            if not os.path.exists(path):
                return JSONResponse({'status': 'error', 'message': "Optimization results not found. Please 'Run Optimization' first."})
        optuna_df = pd.read_csv(optuna_path)
        target = pd.read_csv(summary_path, nrows=0).columns[-1]
        with open(importance_path, 'r', encoding='utf-8') as f:
            importance = [[k, v] for k, v in json.load(f).items()]
        return JSONResponse({
            "status": "ok", "content": calibration_functions.get_records(optuna_df), "importance": importance, "target": target,
            "direction": 'maximize' if target in calibration_functions.MAXIMIZE_TARGETS else 'minimize'
        })
    except Exception as e:
        print('/plot_optuna:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/save_scenario_calibration")
async def save_scenario_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_old, _ = functions.project_definer(body.get('oldProject'), user)
        project_new, _ = functions.project_definer(body.get('newProject'), user)
        params = {}
        for item in body.get('params') or []:
            try: params[item['abbreviation']] = float(item['value'])
            except (KeyError, TypeError, ValueError):
                return JSONResponse({'status': 'error', 'message': f"Invalid value for parameter '{item.get('abbreviation')}'."})
        if not params: 
            return JSONResponse({'status': 'error', 'message': "No optimal parameters found."})
        old_dir, new_dir = os.path.join(PROJECT_ROOT, project_old, 'input'),  os.path.join(PROJECT_ROOT, project_new, 'input')
        if not os.path.isdir(old_dir): 
            return JSONResponse({'status': 'error', 'message': f"Input folder of '{project_old}' not found."})
        if os.path.isdir(new_dir) and os.listdir(new_dir):
            return JSONResponse({'status': 'error', 'message': f"Scenario '{project_new}' already exists. Please choose another name."})
        redis, key = request.app.state.redis, f"{project_old}:{project_new}"
        lock = redis.lock(key, timeout=1000, blocking_timeout=10)
        async with lock:
            calibration_functions.copy_input_tree(old_dir, new_dir, exclude_mdu=False)
            new_path = calibration_functions.find_mdu_file(new_dir)
            if new_path is None: 
                return JSONResponse({'status': 'error', 'message': "MDU file not found in the new scenario."})
            with open(new_path, 'r', encoding='utf-8', errors='ignore') as f:
                mdu_base = f.readlines()
            for name, val in params.items():
                mdu_base = calibration_functions.modify_mdu_key(mdu_base, name, f"{val:.5e}")
            for name, val in {"OutputDir": "DFM_OUTPUT", "WAQOutputDir": "DFM_DELWAQ"}.items():
                mdu_base = calibration_functions.modify_mdu_key(mdu_base, name, val, required=False)
            with open(new_path, 'w', encoding='utf-8') as f:
                f.writelines(mdu_base)
            with open(os.path.join(PROJECT_ROOT, project_new, 'calibration_source.json'), 'w', encoding='utf-8') as f:
                json.dump({"base_project": body.get('oldProject'), "params": params,
                    "created": datetime.now().isoformat(timespec='seconds')}, f, indent=4)
        return JSONResponse({"status": "ok", "message": f"Created scenario '{project_new}' successfully."})
    except Exception as e:
        print('/save_scenario_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/get_stations_comparison")
async def get_stations_comparison(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        name = body.get('projectName')
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, key = request.app.state.redis, f"{project_name}:station"
        his_path = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "output", 'HYD', "FlowFM_his.zarr"))
        if not os.path.exists(his_path):
            path = his_path[his_path.index(name):].replace("\\", "/") if name in his_path else his_path
            return JSONResponse({'status': 'error', 'message': f"No simulation found at: .../{path}\nConsider rerun the simulation."})
        lock = redis.lock(key, timeout=1000, blocking_timeout=10)
        async with lock:
            content = calibration_functions.get_station_from_his_file(his_path)
        if len(content) == 0: 
            return JSONResponse({'status': 'error', 'message': "No station data found in the simulation."})
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e:
        print('/get_stations_comparison:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/obs_comparison_upload")
async def obs_comparison_upload(file: UploadFile = File(...), simStart: str = Form(...), simEnd: str = Form(...)):
    try:
        df = pd.read_csv(file.file, low_memory=False)
        content = calibration_functions.clip_data(df, 'Time', simStart, simEnd)
        if len(content) == 0:
            return JSONResponse({'status': 'error', 'message': f"No data falls within the simulation dates ({simStart} to {simEnd})."})
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e:
        print('/obs_comparison_upload:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/comparison_plot")
async def comparison_plot(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        time_zone = body.get('timeZone')
        station, obs_data = body.get('station'), body.get('obsData')
        depths_selected = calibration_functions.parse_depths(body.get('depthSelection'))
        redis, process_key = request.app.state.redis, f"{project_name}:comparison"
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            start_obs, end_obs = body.get('obsStart'), body.get('obsEnd')
            sim_start = functions.local_to_utc(body.get('simStart'), time_zone)
            sim_end = functions.local_to_utc(body.get('simEnd'), time_zone)
            obs_start = functions.local_to_utc(start_obs, time_zone)
            obs_end = functions.local_to_utc(end_obs, time_zone)
            t_start, t_end = max(sim_start, obs_start), min(sim_end, obs_end)
            if t_start > t_end:
                return JSONResponse({'status': 'error', 'message': "Simulation and observation periods do not overlap."})
            measured_df = pd.DataFrame(data=obs_data, columns=['Time', 'temperature', 'depth'])
            measured_df['Time'] = functions.local_to_utc(measured_df['Time'], time_zone)
            measured_df = measured_df[(measured_df['Time'] >= t_start) & (measured_df['Time'] <= t_end)]
            obs_df = calibration_functions.split_temp_from_depth(measured_df, depths_selected, 'Time')
            if len(obs_df) == 0:
                return JSONResponse({'status': 'error', 'message': f"No observation profiles found between {start_obs} and {end_obs}."})
            # Calibrated simulation (selected project)
            calibrated = calibration_functions.compare_simulation(
                calibration_functions.his_zarr(project_name), station, depths_selected, obs_df, t_start, t_end, time_zone
            )
            if calibrated is None:
                return JSONResponse({'status': 'error', 'message': f"No simulation data found for station '{station}' within the overlapping period."})
            if calibrated['min_max'] is None:
                return JSONResponse({'status': 'error', 'message': "No overlapping simulation and observation values at the selected depths."})
            # Uncalibrated simulation (original project), optional
            base, warning = None, None
            base_label, base_name = calibration_functions.resolve_base_project(body, project_name, user)
            if base_name:
                base = calibration_functions.compare_simulation(calibration_functions.his_zarr(base_name), station, depths_selected,
                    obs_df, t_start, t_end, time_zone)
                if base is None:
                    warning = f"No simulation output of the original scenario '{base_label}' for station '{station}'."
                else: base['project'] = base_label
            else: warning = "Original (uncalibrated) scenario not found; only the calibrated simulation is shown."
            ranges = [calibrated['min_max']] + ([base['min_max']] if base and base['min_max'] else [])
            obs_out = obs_df.copy()
            obs_out['Time'] = functions.utc_to_local(obs_out['Time'], time_zone)
            content = {
                'sim': calibrated['sim'], 'obs': calibration_functions.get_records(obs_out),
                'depth': depths_selected, 'metrics': calibrated['metrics'], 'station': station, 
                'rmse': calibrated['rmse'], 'merge': calibrated['merge'],
                'min_max': [min(r[0] for r in ranges), max(r[1] for r in ranges)],
                'project': body.get('projectName'), 'base': base, 'warning': warning
            }
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e:
        print('/comparison_plot:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})