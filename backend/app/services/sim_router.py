import os, re, subprocess, threading, asyncio, traceback, json, shutil
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import JSONResponse
from config import PROJECT_ROOT, DELFT_PATH
from services import functions, wq_functions, calibration_functions
import xarray as xr
from datetime import datetime, timezone


router, processes = APIRouter(), {}

@router.post("/check_folder")
async def check_folder(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    folder, key = body.get('folder'), body.get('key')
    if key == "hyd": path = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, folder))
    elif key == "waq":
        waq_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "output", "WAQ"))
        if not os.path.exists(waq_dir): return JSONResponse({"status": 'error'})
        files = [f for f in os.listdir(waq_dir) if f.split('.')[0] == folder]
        if len(files) == 0: return JSONResponse({"status": 'error'})
        path = os.path.normpath(os.path.join(waq_dir, files[0]))
    else: path = None
    if path is None: return JSONResponse({"status": 'error'})
    status = 'ok' if os.path.exists(path) else 'error'
    return JSONResponse({"status": status})

# Check if simulation is running
@router.post("/check_sim_status_hyd")
async def check_sim_status_hyd(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    process_key = f"{project_name}:sim_hyd"
    info = processes.get(process_key)
    if not info: 
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No simulation running'})
    if info["status"] in ("finished", "failed", "error"): processes.pop(process_key, None)
    if info["status"] == "finished":
        return JSONResponse({"status": "finished", "progress": 100,
            "message": info.get("message", 'Simulation completed')})
    if info["status"] == "failed":
        return JSONResponse({"status": "failed", "progress": info["progress"],
            "message": info.get("message", 'Simulation failed')})
    if info["status"] == "reorganizing":
        return JSONResponse({"status": "reorganizing", "progress": 100, "message": 'Reorganizing outputs. Please wait...'})
    complete = f'HYD simulation completed: {info["progress"]}% [Time used: {info["time_used"]} → Time left: {info["time_left"]}]'
    return JSONResponse({"status": info["status"], "progress": info["progress"], "message": complete})

# Start a hydrodynamic simulation
@router.post("/start_sim_hyd")
async def start_sim_hyd(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    redis, process_key = request.app.state.redis, f"{project_name}:sim_hyd"
    lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
    async with lock:
        # Check if simulation already running
        if project_name in processes and processes[process_key]["status"] == "running":
            info = processes[process_key]
            complete = f'HYD simulation completed: {info["progress"]}% [Time used: {info["time_used"]} → Time left: {info["time_left"]}]'
            return JSONResponse({"status": "running", "progress": info["progress"], "message": complete})
        path = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "input"))
        mdu_path = os.path.normpath(os.path.join(path, "FlowFM.mdu"))
        bat_path = os.path.normpath(os.path.join(DELFT_PATH, "x64/dflowfm/scripts/run_dflowfm.bat"))
        # Check if file exists
        if not os.path.exists(mdu_path): 
            return JSONResponse({"status": "error", "progress": 0.0, "message": "MDU file not found."})
        if not os.path.exists(bat_path): 
            return JSONResponse({"status": "error", "progress": 0.0, "message": "Executable file not found."})
        # Remove old log
        log_dir = os.path.join(PROJECT_ROOT, project_name, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.normpath(os.path.join(log_dir, "log_hyd.txt"))
        if os.path.exists(log_path): os.remove(log_path)
        percent_re = re.compile(r'(?P<percent>\d{1,3}(?:\.\d+)?)\s*%')
        time_re = re.compile(r'(?P<tt>\d+d\s+\d{1,2}:\d{2}:\d{2})')
        # Modify output structure
        with open(mdu_path, 'r', encoding='utf-8', errors='ignore') as f:
            mdu_base = f.readlines()
        max_length = min(
            len(line.split('=', 1)[1].split('#', 1)[0])
            for line in mdu_base if '=' in line
        )
        mdu_base = calibration_functions.modify_mdu_key(mdu_base, 'OutputDir', 'DFM_OUTPUT', max_length)
        value = calibration_functions.get_values_from_mdu(mdu_base, 'WaqOutputDir')
        if value is None or value == '': value = 'DFM_DELWAQ'
        mdu_base =calibration_functions.modify_mdu_key(mdu_base, 'WaqOutputDir', value, max_length)
        with open(mdu_path, 'w', encoding='utf-8') as f:
            f.writelines(mdu_base)
        # Run the process
        command = ["cmd.exe", "/c", bat_path, "--autostartstop", mdu_path]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1, cwd=path)
        processes[process_key] = {"process": process, "progress": 0.0, "status": "running", 
            "message": 'Preparing data for simulation...', "time_used": "N/A", "time_left": "N/A"}
        # Stream logs
        def stream_logs():
            try:
                stdout = process.stdout
                if stdout is None: raise RuntimeError("stdout is not available; did you pass stdout=PIPE?")
                for line in stdout:
                    proc_info = processes.get(process_key)
                    if not proc_info: return
                    line = line.strip()
                    if not line: continue
                    functions.append_log(log_path, line)
                    # Catch error messages
                    if "forrtl:" in line.lower() or "error" in line.lower():
                        processes[process_key]["status"] = "error"
                        processes[process_key]["message"] = line
                        functions.append_log(log_path, line)
                        res = functions.kill_process(process)
                        functions.append_log(log_path, res["message"])
                        return
                    # Check for progress
                    match_pct = percent_re.search(line)
                    if match_pct: processes[process_key]["progress"] = float(match_pct.group("percent"))
                    # Extract run time
                    times = time_re.findall(line)
                    if len(times) >= 4:
                        processes[process_key]["time_used"] = times[2]
                        processes[process_key]["time_left"] = times[3]
                    elif len(times) == 3:
                        processes[process_key]["time_used"] = times[1]
                        processes[process_key]["time_left"] = times[2]
            except Exception as e:
                proc_info = processes.get(process_key)
                if proc_info:
                    processes[process_key]["status"] = "failed"
                    processes[process_key]["message"] = f"Internal error: {e}"
                functions.append_log(log_path, f"[INTERNAL ERROR] {e}")
            finally:
                process.wait()
                proc_info = processes.get(process_key)
                if not proc_info or proc_info["status"] == "error": return
                processes[process_key]["status"] = "reorganizing"
                processes[process_key]["message"] = "Reorganizing outputs. Please wait..."
                processes[process_key]["progress"] = 100.0
                try:
                    post_result = functions.postProcess(path)
                    if post_result["status"] != "ok":
                        processes[process_key]["status"] = "error"
                        processes[process_key]["message"] = post_result["message"]
                    else:
                        processes[process_key]["status"] = "finished"
                        processes[process_key]["message"] = "Simulation completed."
                    functions.append_log(log_path, processes[process_key]["message"])
                except Exception as e:
                    processes[process_key]["status"] = "failed"
                    processes[process_key]["message"] = f"Simulation failed: {e}."
                    functions.append_log(log_path, processes[process_key]["message"])
        threading.Thread(target=stream_logs, daemon=True).start()
    return JSONResponse({"status": "ok", "message": f"Simulation {project_name} started."})

@router.get("/sim_log_tail/{project_name}")
async def sim_log_tail(project_name: str, offset: int = Query(0), 
    log_file: str = Query(""), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    log_dir = os.path.join(PROJECT_ROOT, project_name, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path, lines = os.path.normpath(os.path.join(log_dir, log_file)), []
    if not os.path.exists(log_path): return {"lines": lines, "offset": offset}
    with open(log_path, "r", encoding=functions.encoding_detect(log_path), errors="replace") as f:
        f.seek(offset)
        for line in f:
            lines.append(line.rstrip())
    return {"lines": lines, "offset": os.path.getsize(log_path)}

# Check if simulation is running
@router.post("/check_sim_status_waq")
async def check_sim_status_waq(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    process_key = f"{project_name}:{body.get('waqName')}"
    info = processes.get(process_key)
    if not info:
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'Simulation not started yet.'})
    if info["status"] in ("finished", "failed", "error"): processes.pop(process_key, None)
    if info["status"] == "finished":
        return JSONResponse({"status": "finished", "progress": 100,
            "message": info.get("message", 'Simulation completed.')})
    if info["status"] == "failed":
        return JSONResponse({"status": "failed", "progress": info["progress"],
            "message": info.get("message", 'Simulation failed.')})
    if info["status"] == "reorganizing":
        return JSONResponse({"status": "reorganizing", "progress": 100, "message": 'Reorganizing outputs. Please wait...'})
    if info["status"] == "preparing":
        return JSONResponse({"status": "preparing", "progress": 0, "message": info["message"]})
    return JSONResponse({"status": info["status"], "progress": info["progress"],
        "message": f"WAQ simulation completed: {info['progress']}%"})

# Start a waq simulation
@router.post("/start_sim_waq")
async def start_sim_waq(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    waq_name, log_file = body.get('waqName'), body.get('logFile')
    redis, process_key = request.app.state.redis, f"{project_name}:{waq_name}"
    lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
    async with lock:
        info = processes.get(process_key)
        if info and info["status"] in ("preparing", "running", "reorganizing"):
            return JSONResponse({"status": "running", "message": "WAQ simulation is already running."})
        processes[process_key] = {"status": "preparing", "progress": 0, "message": "Preparing data for the WAQ simulation...", "process": None}
        asyncio.create_task(run_waq_simulation(project_name, waq_name, log_file))
    return JSONResponse({"status": "ok", "message": "Simulation started."})

async def run_waq_simulation(project_name, waq_name, log_file):
    process_key = f"{project_name}:{waq_name}"
    processes[process_key] = {"status": "not_started", "progress": 0, "message": "", "process": None}
    log_dir = os.path.join(PROJECT_ROOT, project_name, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.normpath(os.path.join(log_dir, log_file))
    if os.path.exists(log_path): os.remove(log_path)
    log_file = open(log_path, "a", encoding=functions.encoding_detect(log_path), errors="replace")
    log_file.write(f"Project: {project_name}\n")
    log_file.write(f"Simulation started at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write("===============================================\n\n")
    # Check if configuration exists
    config_path = os.path.join(PROJECT_ROOT, project_name, "output", "scenarios")
    config_file = os.path.normpath(os.path.join(config_path, f"{waq_name}.json"))
    if not os.path.exists(config_file):
        log_file.write("Configuration file not found.\n")
        processes[process_key]["status"] = "error"
        processes[process_key]["message"] = "Configuration file not found."
        log_file.flush(); log_file.close()
        return
    log_file.write("Configuration file found. Reading configuration ...\n")
    with open(config_file, "r", encoding=functions.encoding_detect(config_file), errors="replace") as f:
        body = json.load(f)
    # Start simulation
    try:
        key, file_name, time_data, usefors = body['key'], body['folderName'], body['timeTable'], body['usefors']
        t_start = datetime.fromtimestamp(int(body['startTime']/1000.0), tz=timezone.utc)
        t_stop = datetime.fromtimestamp(int(body['stopTime']/1000.0), tz=timezone.utc)
        hyd_folder = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "DFM_DELWAQ"))
        wq_folder = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "WAQ_run"))
        path_keys = {
            "hyd_path": "hydName", "sal_path": "salPath", "attr_path": "attrPath",
            "vol_path": "volPath", "ptr_path": "ptrPath", "area_path": "areaPath",
            "flow_path": "flowPath", "length_path": "lengthPath", "srf_path": "srfPath",
            "vdf_path": "vdfPath", "tem_path": "temPath",
        }
        paths = {
            name: os.path.normpath(os.path.join(hyd_folder, body[key]))
            for name, key in path_keys.items()
        }
        hyd_path, sal_path, attr_path = paths["hyd_path"], paths["sal_path"], paths["attr_path"]
        vol_path, ptr_path, area_path = paths["vol_path"], paths["ptr_path"], paths["area_path"]
        flow_path, length_path, srf_path = paths["flow_path"], paths["length_path"], paths["srf_path"]
        vdf_path, tem_path = paths["vdf_path"], paths["tem_path"]
        os.makedirs(wq_folder, exist_ok=True)
        # Clear data if exists
        output_folder = os.path.normpath(os.path.join(wq_folder, file_name))
        if os.path.exists(output_folder): shutil.rmtree(output_folder, onerror=functions.remove_readonly)
        os.makedirs(output_folder, exist_ok=True)
        parameters = {'hyd_path': hyd_path, "t_start": t_start, "t_stop": t_stop, 'sal_path': sal_path,
            "maxiter": body['maxiter'], "tolerance": body['tolerance'], "scheme": body['scheme'], 'srf_path': srf_path, 
            "t_calc": body.get('calcTimeStep'), "t_output": body['timeStep2'], "obs_data": body['obsPoints'],
            'n_segments': body['nSegments'], 'attr_path': attr_path, 'vol_path': vol_path, 'exchange_x': body['exchangeX'],
            'exchange_y': body['exchangeY'], 'exchange_z': body['exchangeZ'], 'folder_name': file_name,
            'ptr_path': ptr_path, 'area_path': area_path, 'flow_path': flow_path, 'length_path': length_path,
            'n_layers': body['nLayers'], 'sources': body['sources'], 'loads_data': body['loadsData'],
            'vdf_path': vdf_path, 'tem_path': tem_path, 'initial_list': body['useforsFrom'], 'initial_set': body['initial'].split('\n')
        }
        includes_folder = os.path.normpath(os.path.join(output_folder, "includes_deltashell"))
        os.makedirs(includes_folder, exist_ok=True)
        table_folder = os.path.normpath(os.path.join(includes_folder, "load_data_tables"))
        os.makedirs(table_folder, exist_ok=True)
        # Write *.tbl file
        tbl_path = os.path.normpath(os.path.join(table_folder, f"{file_name}.tbl"))
        with open(tbl_path, 'w', encoding=functions.encoding_detect(tbl_path), newline='\n') as f:
            f.write(time_data)
        # Write *.usefors file
        usefor_path = os.path.normpath(os.path.join(table_folder, f"{file_name}.usefors"))
        with open(usefor_path, 'w', encoding=functions.encoding_detect(usefor_path), newline='\n') as f:
            f.write(usefors)
        # Prepare external inputs
        waq_model = wq_functions.wqPreparation(parameters, key, output_folder, includes_folder)
        inp_file, message = waq_model[0], waq_model[1]
        if inp_file is None:
            log_file.write(f"Error: {message}.\n")
            processes[process_key]['status'], processes[process_key]['message'] = "error", message
            log_file.flush(); log_file.close()
            return
        if message: log_file.write(f"{message}\n\n")
        # Check if all paths are valid to run the simulation
        bat_path = os.path.normpath(os.path.join(DELFT_PATH, "x64/dwaq/scripts/run_delwaq.bat"))
        bloom_path = os.path.normpath(os.path.join(DELFT_PATH, 'x64/dwaq/default/bloom.spe'))
        proc_path = os.path.normpath(os.path.join(DELFT_PATH, 'x64/dwaq/default/proc_def.def'))
        paths_to_check = [bat_path, proc_path, bloom_path]
        for path in paths_to_check:
            if not os.access(path, os.R_OK):
                log_file.write(f"No read permission: {path}\n")
                processes[process_key]["status"] = "error"
                processes[process_key]["message"] = "No read permission"
                log_file.flush(); log_file.close()
                return
        # Run Simulation and get output
        inp_name = os.path.basename(inp_file)
        progress_regex = re.compile(r"(\d+(?:\.\d+)?)% Completed")
        command = [bat_path, inp_name, "-p", proc_path.replace(".def", ""), "-eco", bloom_path]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, cwd=output_folder)
        processes[process_key] = {"process": process, "progress": 0.0, 
            "status": "running", "message": 'Checking inputs for WAQ simulation...'}
        log_file.write("Checking inputs for WAQ simulation...\n\n")
        log_file.write("=== Starting simulation ===\n\n")
        # Stream logs
        def stream_logs():
            try:
                stdout = process.stdout
                if stdout is None: raise RuntimeError("stdout is not available; did you pass stdout=PIPE?")
                for line in stdout:
                    if not line: continue
                    log_file.write(line)
                    log_file.flush()
                    clean_line = line.strip()
                    if not clean_line: continue
                    if "ERROR in GMRES" in line:
                        log_file.write("\nGMRES solver failed.\nConsider increasing the maximum number of iterations.\n")
                        log_file.flush()
                        processes[process_key]["status"] = "error" 
                        processes[process_key]["message"] = "GMRES solver failed. Consider increasing the maximum number of iterations."
                        res = functions.kill_process(process)
                        log_file.write(f'{res["message"]}\n')
                        log_file.write("\n\nGMRES solver failed.\nConsider increasing the maximum number of iterations.\n")
                        log_file.flush()
                        break
                    # Check for progress
                    match_pct = progress_regex.search(line)
                    if match_pct: processes[process_key]["progress"] = float(match_pct.group(1))
            except Exception as e:
                proc_info = processes.get(process_key)
                if proc_info:
                    processes[process_key]["status"] = "failed"
                    processes[process_key]["message"] = f"Internal error: {e}"
                log_file.write(f"Internal error: {e}\n")
            finally:
                process.wait()
                proc_info = processes.get(process_key)
                if not proc_info or proc_info["status"] == "error": return
                try:
                    processes[process_key]["progress"] = 100.0
                    processes[process_key]["status"] = "reorganizing"
                    processes[process_key]["message"] = "Reorganizing outputs. Please wait..."
                    output_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "output"))
                    if not os.path.exists(output_dir): os.makedirs(output_dir)
                    output_WAQ_dir = os.path.normpath(os.path.join(output_dir, 'WAQ'))
                    if not os.path.exists(output_WAQ_dir): os.makedirs(output_WAQ_dir)
                    for suffix in ["_his.nc", "_map.nc", ".json"]:
                        new_name = f"{file_name}{suffix}"
                        src = os.path.normpath(os.path.join(output_folder, new_name))
                        if os.path.exists(src):
                            # # Using .nc format
                            # dst = os.path.normpath(os.path.join(output_WAQ_dir, new_name))
                            # shutil.copy2(src, dst)
                            
                            # Using .zarr format
                            zarr_path = os.path.normpath(os.path.join(output_WAQ_dir, new_name.replace('.nc', '.zarr')))
                            if suffix != ".json":
                                functions.safe_remove(zarr_path + "_tmp")
                                functions.safe_remove(zarr_path)
                                with xr.open_dataset(src, chunks='auto') as ds:
                                    ds.to_zarr(zarr_path, mode='w', consolidated=True, compute=True)
                            else: shutil.copy2(src, zarr_path)
                            functions.safe_remove(src)
                    processes[process_key]["status"] = "finished"
                    processes[process_key]["message"] = f""
                    log_file.write(f"\n=== Simulation {project_name} completed ===\n")
                    log_file.flush(); log_file.close()
                    if os.path.exists(wq_folder): functions.safe_remove(wq_folder)
                except Exception as e:
                    processes[process_key]["status"], processes[process_key]["message"] = "failed", f"Simulation failed: {e}."
                    log_file.write(f"Simulation failed: {e}.\n{traceback.format_exc()}\n")
                    log_file.flush(); log_file.close()
        threading.Thread(target=stream_logs, daemon=True).start()
    except Exception as e:
        processes[process_key]["status"], processes[process_key]["message"] = "error", str(e)
        print('/run_waq_simulation:\n==============')
        traceback.print_exc()
        log_file.write(f"Error running simulation: {str(e)}.\n")
        log_file.flush(); log_file.close()
        return

@router.get("/sim_log_full/{project_name}")
async def sim_log_full(project_name: str, log_file: str = Query(""), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    log_dir = os.path.join(PROJECT_ROOT, project_name, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.normpath(os.path.join(log_dir, log_file))
    if not os.path.exists(log_path): return {"content": ""}
    with open(log_path, "r", encoding=functions.encoding_detect(log_path), errors="replace") as f:
        content = f.read()
    return {"content": content, "offset": os.path.getsize(log_path)}