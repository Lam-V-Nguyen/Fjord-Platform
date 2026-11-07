import os, traceback, datetime, shutil, json
from fastapi import APIRouter, Request, Depends, Query
from fastapi import UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from config import PROJECT_ROOT
from services import calibration_functions, functions
from datetime import datetime, timedelta
import pandas as pd, numpy as np
from sklearn.metrics import mean_squared_error, r2_score

router, processes = APIRouter(), {}

def _hyd_his(project_name: str):
    """his của lần chạy HYD chính (output/HYD/*_his.zarr|nc) – không giả định tên FlowFM."""
    import glob
    d = os.path.join(PROJECT_ROOT, project_name, "output", "HYD")
    hits = sorted(glob.glob(os.path.join(d, "*_his.zarr"))) + sorted(glob.glob(os.path.join(d, "*_his.nc")))
    return hits[0] if hits else None

def _measured_utc(obs_data, time_zone: str, time_col: str = "Time") -> pd.DataFrame:
    df = pd.DataFrame(data=obs_data, columns=[time_col, "temperature", "depth"])
    df[time_col] = functions.local_to_utc(df[time_col], time_zone)
    return df.dropna(subset=[time_col])

    
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
        # Thời gian mô phỏng sau khi áp overrides của bước kiểm tra ban đầu (vd. TStart mới)
        overrides = calibration_functions.load_overrides(os.path.join(PROJECT_ROOT, project_name, "calibration"))
        if overrides: mdu_content = calibration_functions.apply_overrides(mdu_content, overrides)
        info = calibration_functions.mdu_time_info(mdu_content)
        start_date = functions.utc_to_local(info["start"], time_zone)
        end_date = functions.utc_to_local(info["stop"], time_zone)
        content = {'start': start_date, 'end': end_date}
        return JSONResponse({'content': content})
    except Exception as e:
        print('/calibration_project:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/obs_calibration_upload")
async def obs_calibration_upload(file: UploadFile = File(...), projectName: str = Form(...),
    simStart: str = Form(...), simEnd: str = Form(...), user=Depends(functions.basic_auth)):
    try:
        project_name, _ = functions.project_definer(projectName, user)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        path = os.path.join(calibration_dir, file.filename)
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
    except Exception as e:
        print('/get_stations_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

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
        measured_df = pd.DataFrame(data=body.get('obsData'), columns=[time_col, 'temperature', 'depth'])
        measured_df[time_col] = functions.local_to_utc(measured_df[time_col], time_zone)
        # Detect measurement periods
        measured_df = measured_df[(measured_df[time_col] >= t_start) & (measured_df[time_col] <= t_end)]
        obs_df = calibration_functions.split_temp_from_depth(measured_df, depths_selected, time_col)
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
            row_params[target_method] = weighted          # cột mục tiêu luôn là cột CUỐI (surrogate dùng)
            summary_list.append(row_params)
            for r in scores: metric_rows.append({"case": case_id, **r})
        if metric_rows:   # bias/RMSE theo độ sâu: phân biệt sai số hệ thống (điều kiện ban đầu) với sai số động lực
            pd.DataFrame(metric_rows).to_csv(os.path.join(calibration_dir, 'calibration_metrics.csv'), index=False)
        if not summary_list:
            return JSONResponse({"status": "error", "message": f"No scenario output contains station '{obs_station}'."})
        summary_df = pd.DataFrame(summary_list).dropna(subset=[target_method])
        summary_csv = os.path.join(calibration_dir, 'calibration_summary.csv')
        summary_df.to_csv(summary_csv, index=False)
        bias = pd.DataFrame(metric_rows).groupby("depth", sort=False)["bias"].mean()
        bias_txt = ", ".join(f"{d} m: {b:+.2f}" for d, b in bias.items() if np.isfinite(b))
        return JSONResponse({'status': 'ok', 'message': f'Calibration summary completed ({len(summary_df)} scenarios). '
                                                       f'Mean bias (sim − obs, °C) — {bias_txt}'})
    except Exception as e:
        print('/summarize_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

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
    except Exception as e:
        print('/correlation_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

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
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get("projectName"), user)
        dir = os.path.join(PROJECT_ROOT, project_name)
        redis, key = request.app.state.redis, f"{project_name}:{body.get('key')}"
        lock = redis.lock(key, timeout=1000, blocking_timeout=10)
        async with lock:
            # Check if iteration already running
            if key in processes and processes[key]["status"] == "running":
                info = processes[key]
                return JSONResponse({
                    "status": info["status"], "progress": info["progress"], 
                    "message": info["message"], "his": info["history"]
                })
            processes[key] = {
                "status": "running", "progress": 0.0, "history": '',
                "message": 'Iteration data for calibration ...'
            }
            background_tasks.add_task(
                calibration_functions.run_iteration, dir, processes, body, key
            )
        return JSONResponse({"status": "started", "progress": 0.0, "message": f"Iteration started.", "his": []})
    except Exception as e:
        print('/start_calibration_iteration:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'failed', 'message': f"Error: {e}"})

@router.post("/check_sim_status_calibration")
async def check_sim_status_calibration(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    process_key = f"{project_name}:{body.get('key')}"
    info = processes.get(process_key)
    if not info: 
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No simulation running'})
    status, progress = info.get("status"), info.get("progress", 0.0)
    current, total = info.get("current", 0), info.get("total", 0)
    if status == "finished":
        return JSONResponse({"status": "finished", "progress": 100.0,
            "message": info.get("message", "Simulation completed.")
        })
    if status in ("failed", "error"):
        return JSONResponse({"status": status, "progress": progress,
            "message": info.get("message", "Simulation failed.")
        })
    complete = f'Simulation ({current}/{total}): {progress}% completed.'
    return JSONResponse({"status": status, "progress": progress, "message": complete})

@router.get("/calibration_log_full/{project_name}")
async def calibration_log_full(project_name: str, log_file: str = Query(""), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    log_dir = os.path.join(PROJECT_ROOT, project_name, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.normpath(os.path.join(log_dir, log_file))
    if not os.path.exists(log_path): return {"content": ""}
    with open(log_path, "r", encoding=functions.encoding_detect(log_path), errors="replace") as f:
        content = f.read()
    return {"content": content, "offset": os.path.getsize(log_path)}

@router.get("/calibration_log_tail/{project_name}")
async def calibration_log_tail(project_name: str, offset: int = Query(0), 
    log_file: str = Query(""), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    log_dir = os.path.join(PROJECT_ROOT, project_name, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path, lines = os.path.normpath(os.path.join(log_dir, log_file)), []
    if not os.path.exists(log_path): return {"lines": lines, "offset": 0}
    with open(log_path, "r", encoding=functions.encoding_detect(log_path), errors="replace") as f:
        f.seek(offset)
        for line in f:
            lines.append(line.rstrip())
    return {"lines": lines, "offset": os.path.getsize(log_path)}

@router.post("/start_sim_calibration")
async def start_sim_calibration(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, process_key = request.app.state.redis, f"{project_name}:{body.get('key')}"
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        sample_dir = os.path.join(calibration_dir, body.get("samplingName") or "")   # '' = các kịch bản hiệu chỉnh
        if not os.path.exists(os.path.join(sample_dir, "scenarios")):
            return JSONResponse({"status": "error", "message": f"Sampling directory not found. Please run resampling first."})
        scenario_dir = os.path.join(sample_dir, "scenarios")
        list_sorted = sorted([int(x) for x in os.listdir(scenario_dir) if os.path.isdir(os.path.join(scenario_dir, x))])
        if not list_sorted:
            return JSONResponse({"status": "error", "message": "No simulation scenarios found."})
        # Remove old log
        log_dir = os.path.join(calibration_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, body.get("logFile"))
        if os.path.exists(log_path): os.remove(log_path)
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            info = processes.get(process_key)
            # Check if simulation already running
            if info and info["status"] == "running":
                complete = f'Simulation ({info["current"]}/{info["total"]}): {info["progress"]}% completed.'
                return JSONResponse({"status": "running", "progress": info["progress"], "message": complete})
            processes[process_key] = {"progress": 0.0, "status": "running",
                "message": "Preparing data for simulation...", "current": 0, "total": len(list_sorted),
            }
            background_tasks.add_task(
                calibration_functions.run_calibration, processes, process_key, scenario_dir, list_sorted, log_path
            )
        return JSONResponse({"status": "ok", "message": f"Simulation {project_name} started"})
    except Exception as e:
        print('/start_sim_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

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
    except Exception as e:
        print('/start_optimization_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

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
    except Exception as e:
        print('/start_optimization_optuna:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

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
    except Exception as e:
        print('/plot_optuna:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

@router.post("/save_scenario_calibration")
async def save_scenario_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_old, _ = functions.project_definer(body.get('oldProject'), user)
        project_new, _ = functions.project_definer(body.get('newProject'), user)
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
            # Overrides của bước kiểm tra ban đầu + tham số tối ưu (hỗ trợ Key_factor / Key[i] cho khoá nhiều giá trị)
            overrides = calibration_functions.load_overrides(os.path.join(PROJECT_ROOT, project_old, "calibration"))
            mdu_base = calibration_functions.apply_overrides(base_lines, overrides)
            for name, val in params.items():
                mdu_base = calibration_functions.apply_param(mdu_base, base_lines, name, float(val))
            for name, val in {"OutputDir": "DFM_OUTPUT", "WAQOutputDir": "DFM_DELWAQ"}.items():
                if calibration_functions.has_mdu_key(mdu_base, name):
                    mdu_base = calibration_functions.modify_mdu_key(mdu_base, name, val)
            with open(os.path.join(new_dir, os.path.basename(old_mdu)), 'w', encoding='utf-8') as f:
                f.writelines(mdu_base)
            return JSONResponse({"status": "ok", "message": f"Created scenario '{project_new}' successfully."})
    except Exception as e:
        print('/save_scenario_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

@router.post("/obs_comparison_upload")
async def obs_comparison_upload(file: UploadFile = File(...), simStart: str = Form(...), simEnd: str = Form(...)):
    try:
        df = pd.read_csv(file.file, low_memory=False)
        content = calibration_functions.clip_data(df, 'Time', simStart, simEnd)
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e:
        print('/obs_comparison_upload:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/get_stations_comparison")
async def get_stations_comparison(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        name = body.get('projectName')
        project_name, _ = functions.project_definer(name, user)
        redis, key = request.app.state.redis, f"{project_name}:station"
        his_path = _hyd_his(project_name) or os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "output", 'HYD', "FlowFM_his.zarr"))
        if not os.path.exists(his_path):
            path = his_path[his_path.index(name):].replace("\\", "/")
            return JSONResponse({"status": "error", "message": f"No simulation found at: .../{path}\nConsider rerun the simulation."})
        lock = redis.lock(key, timeout=1000, blocking_timeout=10)
        async with lock:
            content = calibration_functions.get_station_from_his_file(his_path)
            if len(content) == 0: return JSONResponse({"status": "error", "message": "No station data found in the simulation."})
            return JSONResponse({"status": "ok", "content": content})
    except Exception as e:
        print('/get_stations_comparison:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

@router.post("/comparison_plot")
async def comparison_plot(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        time_col, time_zone = 'Time', body.get('timeZone')
        station, obs_data = body.get('station'), body.get('obsData')
        redis, process_key = request.app.state.redis, f"{project_name}:comparison"
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            # Define period
            start_sim, end_sim = body.get('simStart'), body.get('simEnd')
            start_obs, end_obs = body.get('obsStart'), body.get('obsEnd')
            depths_selected = [x.strip() for x in body.get('depthSelection').split(",")]
            measured_df = pd.DataFrame(data=obs_data, columns=[time_col, 'temperature', 'depth'])
            measured_df[time_col] = functions.local_to_utc(measured_df[time_col], time_zone)
            sim_start = functions.local_to_utc(start_sim, time_zone)
            sim_end = functions.local_to_utc(end_sim, time_zone)
            obs_start = functions.local_to_utc(start_obs, time_zone)
            obs_end = functions.local_to_utc(end_obs, time_zone)
            t_start, t_end = max(sim_start, obs_start), min (sim_end, obs_end)
            if t_start > t_end:
                return JSONResponse({
                    "status": "error", "message": "Simulation and observation periods do not overlap."
                })
            # Detect measurement periods
            measured_df = measured_df[(measured_df[time_col] >= t_start) & (measured_df[time_col] <= t_end)]
            obs_df = calibration_functions.split_temp_from_depth(measured_df, depths_selected, time_col)
            if len(obs_df) == 0: return JSONResponse({"status": "error", "message": f"No simulation found between: {start_obs} and {end_obs}"})
            his_file = _hyd_his(project_name)
            sim_df = calibration_functions.read_and_interpolate_his(his_file, station, depths_selected, time_col)
            if len(sim_df) == 0: return JSONResponse({"status": "error", "message": f"No simulation found between: {start_sim} and {end_sim}"})
            sim_df = sim_df[(sim_df.index >= t_start) & (sim_df.index <= t_end)]
            # Prepare observation timestamps
            sim_df, obs_df = sim_df.reset_index(), obs_df.reset_index(drop=True)
            metrics, merge, rmses, global_min, global_max = {}, {}, [], np.inf, -np.inf
            for depth in depths_selected:
                obs_col, sim_col = f"obs_{depth}", f"sim_{depth}"
                obs_series = (obs_df[[time_col, obs_col]]
                    .dropna().drop_duplicates(time_col).set_index(time_col).sort_index()
                )
                sim_series = (sim_df[[time_col, sim_col]]
                    .dropna().drop_duplicates(time_col).set_index(time_col).sort_index()
                )
                combined_index = sim_series.index.union(obs_series.index)
                sim_interp = sim_series.reindex(combined_index).sort_index()[sim_col].interpolate(method="time")
                aligned_df = sim_interp.loc[obs_series.index]
                merge_df = pd.DataFrame({
                    time_col: obs_series.index, obs_col: obs_series[obs_col].values, sim_col: aligned_df.values
                })
                merge_df[time_col] = functions.utc_to_local(merge_df[time_col], time_zone)
                merge[str(depth)] = merge_df.replace({np.nan: None}).to_dict(orient="records")
                valid = merge_df[[obs_col, sim_col]].dropna()
                if valid.empty:
                    metrics[str(depth)] = [None, None, None]; continue
                rmse = np.sqrt(mean_squared_error(valid[obs_col], valid[sim_col]))
                r2 = r2_score(valid[obs_col], valid[sim_col]) if len(valid) > 1 else np.nan
                bias = float((valid[sim_col] - valid[obs_col]).mean())
                metrics[str(depth)] = [round(rmse, 3), round(r2, 3) if np.isfinite(r2) else None, round(bias, 3)]
                local_min = min(valid[obs_col].min(), valid[sim_col].min())
                local_max = max(valid[obs_col].max(), valid[sim_col].max())
                global_min = min(global_min, local_min)
                global_max = max(global_max, local_max)
                rmses.append(rmse)
            obs_df[time_col] = functions.utc_to_local(obs_df[time_col], time_zone)
            sim_df[time_col] = functions.utc_to_local(sim_df[time_col], time_zone)
            total_rmse = calibration_functions.compute_weighted_rmse(depths_selected, rmses)
            content = {
                'sim': sim_df.to_dict(orient="records"), 'depth': depths_selected,
                'obs': obs_df.to_dict(orient="records"), 'metrics': metrics, 'station': station,
                "rmse": total_rmse, "merge": merge, "min_max": [global_min, global_max]
            }
            return JSONResponse({"status": "ok", "content": content})
    except Exception as e:
        print('/comparison_plot:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

# ======================================================================
# Kiểm tra điều kiện ban đầu – chạy TRƯỚC khi tạo các kịch bản hiệu chỉnh
# ======================================================================
@router.post("/initial_check_calibration")
async def initial_check_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        time_zone = body.get('timeZone')
        measured = _measured_utc(body.get('obsData') or [], time_zone)
        content = calibration_functions.initial_check(os.path.join(PROJECT_ROOT, project_name), project_name,
                                                      measured, body.get('station'), time_zone)
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e:
        print('/initial_check_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

@router.post("/save_overrides_calibration")
async def save_overrides_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        res = calibration_functions.save_overrides(os.path.join(PROJECT_ROOT, project_name),
                                                   body.get('rows') or [], body.get('timeZone'))
        return JSONResponse(res)
    except Exception as e:
        print('/save_overrides_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

@router.post("/create_baseline_calibration")
async def create_baseline_calibration(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        case_dir = calibration_functions.create_baseline(os.path.join(PROJECT_ROOT, project_name))
        return JSONResponse({"status": "ok", "message": f"Baseline case created: .../{os.path.relpath(case_dir, PROJECT_ROOT)}"})
    except Exception as e:
        print('/create_baseline_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})

@router.post("/evaluate_baseline_calibration")
async def evaluate_baseline_calibration(request: Request, user=Depends(functions.basic_auth)):
    """Sai số theo độ sâu của lần chạy baseline (MDU + overrides) và của lần chạy HYD gốc (nếu có)."""
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        time_col, time_zone, station = 'Time', body.get('timeZone'), body.get('station')
        depths = [x.strip() for x in str(body.get('depthSelection') or '').split(",") if x.strip()]
        measured = _measured_utc(body.get('obsData') or [], time_zone)
        obs_df = calibration_functions.split_temp_from_depth(measured, depths, time_col)
        if obs_df.empty: return JSONResponse({"status": "error", "message": "No observation profiles for the selected depths."})
        obs_df[time_col] = pd.to_datetime(obs_df[time_col], utc=True)
        calibration_dir = os.path.join(PROJECT_ROOT, project_name, "calibration")
        runs = {"baseline": calibration_functions.find_his(os.path.join(calibration_dir, "baseline", "scenarios", "0")),
                "original": _hyd_his(project_name)}
        content = {}
        for name, his in runs.items():
            if not his: continue
            sim = calibration_functions.read_and_interpolate_his(his, station, depths, time_col)
            if sim.empty: continue
            obs_in = obs_df[(obs_df[time_col] >= sim.index.min()) & (obs_df[time_col] <= sim.index.max())]
            rows = calibration_functions.score_depths(sim, obs_in, depths, time_col)
            rmse = calibration_functions.compute_weighted_rmse(depths, [r["rmse"] for r in rows])
            content[name] = {"his": os.path.basename(his), "rmse": None if not np.isfinite(rmse) else round(rmse, 3),
                             "rows": [{k: (None if isinstance(v, float) and not np.isfinite(v) else
                                           (round(v, 3) if isinstance(v, float) else v)) for k, v in r.items()} for r in rows]}
        if "baseline" not in content:
            return JSONResponse({"status": "error", "message": "Baseline output not found – run the baseline first.",
                                 "content": content})
        return JSONResponse({"status": "ok", "content": content})
    except Exception as e:
        print('/evaluate_baseline_calibration:\n==============')
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": f"Error: {str(e)}"})