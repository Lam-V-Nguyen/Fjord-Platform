import traceback, asyncio, os, shutil, json, msgpack
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from config import PROJECT_ROOT
from services import functions, grid_functions
import numpy as np, xarray as xr


router = APIRouter()


@router.post("/auth_check")
async def auth_check(user=Depends(functions.basic_auth)):
    output = 'ok' if user == 'admin' else 'error'
    return {"user": user, "output": output}

@router.post("/get_timezone")
async def get_timezone(request: Request):
    body = await request.json()
    query, count = body.get('query'), int(body.get('n'))
    return functions.time_zone_get(query, count)

# Remove folder configuration
@router.post("/reset_config")
async def reset_config(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis = request.app.state.redis
        lock = redis.lock(f"{project_name}:reset_config", timeout=20)        
        async with lock:
            # Reset project data in Redis
            folder = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
            if not os.path.exists(folder): return JSONResponse({"message": "Project folder doesn't exist."})
            config_dir = os.path.normpath(os.path.join(folder, "output", "config"))
            if not os.path.exists(config_dir): return JSONResponse({"message": "Configuration folder doesn't exist."})
            shutil.rmtree(config_dir, onerror=functions.remove_readonly)
            movement_dir = os.path.normpath(os.path.join(folder, "output", "movement"))
            if os.path.exists(movement_dir): shutil.rmtree(movement_dir, onerror=functions.remove_readonly)
            # Delete config in Redis
            await redis.hdel(project_name, "config", "layer_reverse_hyd", "layer_reverse_waq")
            return JSONResponse({"status": "ok", "message": "Configuration reset successfully!"})
    except Exception as e: functions.error('/reset_config', e)
    
# Create a new project with necessary folders
@router.post("/setup_new_project")
async def setup_new_project(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        project_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
        os.makedirs(project_dir, exist_ok=True)
        input_dir = os.path.normpath(os.path.join(project_dir, "input"))
        if not os.path.exists(input_dir): os.makedirs(input_dir, exist_ok=True)
        status, message = project_name, f"Scenario '{body.get('projectName')}' loaded!"
    except Exception as e: functions.error('/setup_new_project', e)
    return JSONResponse({"status": status, "message": message})

# Copy a project
@router.post("/copy_project")
async def copy_project(request: Request, user=Depends(functions.basic_auth)):
    extend_task = None
    try:
        body = await request.json()
        old_name, _ = functions.project_definer(body.get('oldName'), user)
        new_name, _ = functions.project_definer(body.get('newName'), user)
        project_folder = os.path.normpath(os.path.join(PROJECT_ROOT, old_name))
        redis = request.app.state.redis
        lock = redis.lock(f"{old_name}:copy_project", timeout=600)
        async with lock:
            # Optional: auto-extend lock if deletion may take long
            extend_task = asyncio.create_task(functions.auto_extend(lock))
            if not os.path.exists(project_folder): 
                return JSONResponse({"status": 'error', "message": f"Project '{old_name}' does not exist."})
            shutil.copytree(project_folder, os.path.normpath(os.path.join(PROJECT_ROOT, new_name)))
            return JSONResponse({"message": f"HYD scenario '{new_name}' was cloned successfully!"})
    except Exception as e: functions.error('/copy_project', e)
    finally:
        if extend_task:
            extend_task.cancel()
            try: await extend_task
            except asyncio.CancelledError: pass

# Delete a project
@router.post("/delete_project")
async def delete_project(request: Request, user=Depends(functions.basic_auth)):
    extend_task = None
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis = request.app.state.redis
        name = project_name if '/' not in project_name else project_name.split('/')[-1]
        lock = redis.lock(f"{project_name}:delete_project", timeout=600)
        project_folder, extend_task = os.path.normpath(os.path.join(PROJECT_ROOT, project_name)), None
        async with lock:
            extend_task = asyncio.create_task(functions.auto_extend(lock))
            if not os.path.exists(project_folder): 
                return JSONResponse({"status": 'error', "message": f"Project '{project_name}' does not exist."})
            shutil.rmtree(project_folder, onerror=functions.remove_readonly)
            if hasattr(request.app.state, "project_cache"): request.app.state.project_cache.pop(project_name, None)
            return JSONResponse({"status": "ok", "message": f"Project '{name}' was deleted successfully."})
    except Exception as e: functions.error('/delete_project', e)
    finally:
        if extend_task:
            extend_task.cancel()
            try: await extend_task
            except asyncio.CancelledError: pass

@router.post("/select_project")
async def select_project(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        key, folder_check = body.get('key'), body.get('folder_check')
        key_checker, name = body.get('keyChecked'), body.get('filename')
        if name == 'None' or name == 'undefined':
            return {"status": "error", "message": f"Cannot find scenario '{name}'.\nConsider running the scenario again."}
        project_name, _ = functions.project_definer(name, user)
        project_dir, data = os.path.normpath(os.path.join(PROJECT_ROOT, project_name)), []
        if key_checker == 'flows':
            project_dir = os.path.normpath(os.path.join(project_dir, 'flows'))
            os.makedirs(project_dir, exist_ok=True)
        if key == 'getProjects':
            project = [p.name for p in os.scandir(project_dir) if p.is_dir()]
            project = [p for p in project if os.path.exists(os.path.normpath(os.path.join(project_dir, p, folder_check)))]
            data = sorted(project)
        elif key == 'getWAQs': # List the scenarios for water quality
            scenario_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, 'output', 'scenarios'))
            if not os.path.exists(scenario_dir):
                return JSONResponse({"status": 'error', "message": "Couldn't find any scenario for water quality.\nTry to create a new scenario first."})
            project = [p.name for p in os.scandir(scenario_dir)]
            project = [p.replace('.json', '') for p in project if os.path.exists(os.path.normpath(os.path.join(scenario_dir, p)))]
            data = sorted(project)
        elif key == 'getLakes':
            project_dir = os.path.normpath(os.path.join(project_dir, 'lakes'))
            lake_path = os.path.normpath(os.path.join(project_dir, 'lakes.json'))
            if not os.path.exists(lake_path):
                lakes_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "lakes"))
                os.makedirs(lakes_dir, exist_ok=True)
                project_cache = request.app.state.project_cache.setdefault(project_name, {})
                grid_functions.lake_generation(lakes_dir, project_cache)
            with open(lake_path, 'r') as f: lakes = json.load(f)
            data = sorted(lakes.keys())
        return JSONResponse({"content": data})
    except Exception as e: functions.error('/select_project', e)

# Set up the database depending on the project
@router.post("/setup_database")
async def setup_database(request: Request, user=Depends(functions.basic_auth)):
    extend_task = None
    try:
        body = await request.json()
        name = body.get('projectName')
        if name == 'None' or name == 'undefined':
            return {"status": "error", "message": f"Cannot find scenario '{name}'.\nConsider running the scenario again."}
        project_name, _ = functions.project_definer(name, user)
        redis, params, model_name = request.app.state.redis, body.get('params'), body.get('waqName')
        model_type, gisChecked = body.get('waqModel'), body.get('gisChanged')
        lock = redis.lock(f"{project_name}:setup_database", timeout=600)
        async with lock:
            extend_task = asyncio.create_task(functions.auto_extend(lock, interval=10))
            project_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name))
            # demo_dir = os.path.normpath(os.path.join(PROJECT_ROOT, 'demo'))
            # check_demo = os.listdir(project_dir)
            # if user != 'admin' and 'demo' not in check_demo:
            #     print(f"Copying project 'demo' folder to '{project_dir}'")
            #     os.makedirs(project_dir, exist_ok=True)
            #     shutil.copytree(demo_dir, project_dir, dirs_exist_ok=True)
            gis_dir = os.path.normpath(os.path.join(project_dir, "GIS"))
            if not os.path.exists(gis_dir): os.makedirs(gis_dir, exist_ok=True)
            output_dir = os.path.normpath(os.path.join(project_dir, "output"))
            config_dir = os.path.normpath(os.path.join(output_dir, "config"))
            if not os.path.exists(config_dir): os.makedirs(config_dir, exist_ok=True)
            hyd_dir = os.path.normpath(os.path.join(output_dir, 'HYD'))
            cache_root = request.app.state.project_cache
            if not isinstance(cache_root, dict):
                cache_root = {}
                request.app.state.project_cache = cache_root
            project_cache = cache_root.get(project_name)
            if not isinstance(project_cache, dict):
                project_cache = {}
                cache_root[project_name] = project_cache
            dm, waq_his, waq_map = request.app.state.dataset_manager, None, None
            # Assign datasets
            if params[0] == '' or params[1] == '':
                return {"status": "error", "message": "Cannot find simulation files.\nConsider running the scenario again."}
            if 'hyd_his' not in project_cache:
                project_cache['hyd_his'] = dm.get(os.path.normpath(os.path.join(hyd_dir, params[0])))
            if 'hyd_map' not in project_cache:
                project_cache['hyd_map'] = dm.get(os.path.normpath(os.path.join(hyd_dir, params[1])))
            hyd_his, hyd_map = project_cache['hyd_his'], project_cache['hyd_map']
            if hyd_map is None: return JSONResponse({"status": "error", "message": "HYD map data not available."})
            waq_dir = os.path.normpath(os.path.join(output_dir, 'WAQ'))
            if os.path.exists(waq_dir):
                if params[2] != '':
                    waq_his = dm.get(os.path.normpath(os.path.join(waq_dir, params[2])))
                    project_cache['waq_his'] = waq_his
                if params[3] != '':
                    waq_map = dm.get(os.path.normpath(os.path.join(waq_dir, params[3])))
                    project_cache['waq_map'] = waq_map
            if hyd_map is None:
                return {"status": "error", "message": "Cannot find hydrodynamic data (map file).\nConsider running the scenario again."}
            if hyd_map is not None and 'grid' not in project_cache:
                print('Creating grid for hydrodynamic simulation...')
                project_cache['grid'] = functions.unstructuredGridCreator(hyd_map)
            # Load or init config
            config_path, obs, waq_model = os.path.normpath(os.path.join(config_dir, 'config.json')), {}, ''
            config = {
                "hyd": {}, "waq": {}, "meta": {"hyd_scanned": False, "waq_scanned": False},
                "model_type": '', "model_name": '', 'gis_layers': []
            }
            if os.path.exists(config_path) and os.path.getsize(config_path) > 0:
                print('Config already exists. Loading...')
                config = json.loads(open(config_path, "r", encoding=functions.encoding_detect(config_path)).read())
                waq_model = config.get('model_type')
            print(f"Current WAQ model: {waq_model}, New WAQ model: {model_type}")
            # Lazy scan HYD variables only once
            if (hyd_map or hyd_his) and not config['meta']['hyd_scanned']:
                print('Scanning HYD variables...')
                hyd_vars = functions.getVariablesNames([hyd_his, hyd_map])
                config["hyd"], config["meta"]["hyd_scanned"] = hyd_vars, True
            model_path = os.path.normpath(os.path.join(waq_dir, f'{model_name}.json'))
            if model_type != '': 
                waq_vars = functions.getVariablesNames([waq_his, waq_map], model_type, model_name)
                config["waq"], config["meta"]["waq_scanned"] = waq_vars, True
                config['model_type'], config['model_name'] = model_type, params[2].replace('_his.zarr', '')
                # Get WAQ model
                if os.path.exists(model_path):
                    print('Loading WAQ model...')
                    with open(model_path, "r", encoding=functions.encoding_detect(model_path)) as f:
                        temp_data = json.load(f)
                    waq_model = temp_data['model_type']
                    config['wq_obs'] = True if 'wq_obs' in temp_data else False
                    config['wq_loads'] = True if 'wq_loads' in temp_data else False
            if model_type != waq_model:
                print('Model changed. Updating config...')
                # Get WAQ model
                if os.path.exists(model_path):
                    print('Loading WAQ model...')
                    with open(model_path, "r", encoding=functions.encoding_detect(model_path)) as f:
                        temp_data = json.load(f)
                    waq_model = temp_data['model_type']
                    config['wq_obs'] = True if 'wq_obs' in temp_data else False
                    config['wq_loads'] = True if 'wq_loads' in temp_data else False
                if (waq_his or waq_map) and waq_model == '': 
                    return JSONResponse({"status": 'error', "message": "Some WAQ-related parameters are missing.\nConsider running the model again."})  
                # Lazy scan WAQ
                if (waq_map or waq_his):
                    print('Scanning WAQ variables...')
                    waq_vars = functions.getVariablesNames([waq_his, waq_map], waq_model, model_name)
                    config["waq"], config["meta"]["waq_scanned"] = waq_vars, True
                    config['model_type'], config['model_name'] = waq_model, model_name
                # Delete waq option if no waq files
                if waq_his is None and waq_map is None:
                    print('No waq files. Deleting waq option...')
                    config['waq'], config['meta']['waq_scanned'] = {}, False
                    for k in ("wq_obs", "wq_loads"):
                        config.pop(k, None)
            # Save config
            open(config_path, "w", encoding=functions.encoding_detect(config_path)).write(json.dumps(config))
            # Get number of HYD layers
            layer_path = os.path.normpath(os.path.join(config_dir, 'layers_hyd.json'))
            hyd_map_ds = project_cache['hyd_map']
            if not isinstance(hyd_map_ds, xr.Dataset): return JSONResponse({"status": "error", "message": "HYD map data not available."})
            if not os.path.exists(layer_path):
                layer_reverse_hyd = functions.layerCounter(hyd_map_ds, 'hyd')
                json.dump(layer_reverse_hyd, open(layer_path, "w", encoding=functions.encoding_detect(layer_path)))                    
            else: layer_reverse_hyd = json.load(open(layer_path, "r", encoding=functions.encoding_detect(layer_path)))
            # Get number of WAQ layers
            if waq_map is not None:
                print('Creating layers for water quality simulation...')
                layer_path = os.path.normpath(os.path.join(config_dir, 'layers_waq.json'))
                if not os.path.exists(layer_path):
                    layer_reverse_waq = functions.layerCounter(waq_map, 'waq')
                    json.dump(layer_reverse_waq, open(layer_path, "w", encoding=functions.encoding_detect(layer_path)))                    
                else: layer_reverse_waq = json.load(open(layer_path, "r", encoding=functions.encoding_detect(layer_path)))
            else: layer_reverse_waq = {}
            # Convert sigma layer to depth layer
            depth_values = [float(v.split(' ')[1]) for k, v in layer_reverse_hyd.items() if int(k) >= 0]
            max_depth, layer_reverse_waq_depth = max(np.array(depth_values, dtype=float), key=abs), {}
            for k, v in layer_reverse_waq.items():
                if int(k) >= 0: 
                    note, val = '', round(max_depth*float(v.split(':')[1].strip().split(' ')[0].strip())/100, 2)
                    if int(k) == 0: note = ' (surface)'
                    elif int(k) == len(layer_reverse_hyd)-2: note = ' (bottom)'
                    layer_reverse_waq_depth[k] = f'Depth: {val} m{note}'
                else: layer_reverse_waq_depth[k] = v
            # Get observations in WAQ
            if os.path.exists(model_path):
                temp_data = json.load(open(model_path, "r", encoding=functions.encoding_detect(model_path)))
                if 'wq_obs' in temp_data: obs['wq_obs'] = temp_data['wq_obs']
                if 'wq_loads' in temp_data: obs['wq_loads'] = temp_data['wq_loads']
            # Save config if GIS changed
            if gisChecked:
                gis_file = [f.replace('.geojson', '') for f in os.listdir(gis_dir) if f.endswith(".geojson")]
                if len(gis_file) > 0: config['gis_layers'] = gis_file
                else: config['gis_layers'] = []
                open(config_path, "w", encoding=functions.encoding_detect(config_path)).write(json.dumps(config))
            # Restructure configuration
            result = {**config.get("hyd", {}), **config.get("waq", {})}
            for k, v in config.items():
                if k not in ("hyd", "waq", "meta"): result[k] = v
            # Serialize grid & layer_reverse to JSON-safe formats
            redis_mapping = {
                "hyd_his_path": params[0], "hyd_map_path": params[1], "waq_his_path": params[2], "waq_map_path": params[3],
                "layer_reverse_hyd": msgpack.packb(layer_reverse_hyd, use_bin_type=True),
                "layer_reverse_waq": msgpack.packb(layer_reverse_waq_depth, use_bin_type=True),
                "config": msgpack.packb(result, use_bin_type=True), "waq_model": waq_model,
                "waq_obs": msgpack.packb(obs, use_bin_type=True), 
                'gis_layers': msgpack.packb(config['gis_layers'], use_bin_type=True)
            }
            # Save to Redis
            await redis.delete(project_name)
            await redis.hset(project_name, mapping=redis_mapping)
            print('Configuration loaded successfully.')
            return JSONResponse({"user": project_name, "content": result})
    except Exception as e: functions.error('/setup_database', e)
    finally:
        if extend_task:
            extend_task.cancel()
            try: await extend_task
            except asyncio.CancelledError: pass

@router.post("/get_config_files")
async def get_config_files(request: Request):
    body = await request.json()
    user_name, project = body.get('userName').strip(), body.get('project').strip()
    original_project, warning = project, None
    try: result = functions.project_reader(user_name, project)
    except:
        project = 'demo'
        traceback.print_exc()
        # Store warning for frontend
        warning = (
            f"Project '{original_project}' does not contain enough HYD configuration files." + 
            f"\nPlease rerun the simulation and try again.\nUsing default project 'demo'."
        )
        try: result = functions.project_reader(user_name, project)
        except Exception: functions.error('/get_config_files', f"Could not load project '{original_project}' or fallback project 'demo'.")
    if warning: result["status"], result["message"] = "warning", warning
    else: result["status"] = "ok"
    return JSONResponse(result)