import os, json, re, traceback, asyncio, shutil
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from services import functions, wq_functions
from config import PROJECT_ROOT
import numpy as np, pandas as pd

router = APIRouter()


@router.post("/select_hyd")
async def select_hyd(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    folder, time_zone = [PROJECT_ROOT, project_name, "DFM_DELWAQ", 'FlowFM.hyd'], body.get('timeZone')
    path = os.path.normpath(os.path.join(*folder))
    if os.path.exists(path):
        return JSONResponse({"status": 'ok', "content": wq_functions.hydReader(path, time_zone)})
    message = f"Error: Cannot find .hyd file in project '{project_name}'.\nPlease run a hydrodynamic simulation first."
    return JSONResponse({"status": 'error', "message": message})

@router.post("/load_waq")
async def load_waq(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        folder = [PROJECT_ROOT, project_name, "output", 'scenarios', f"{body.get('waqName')}.json"]
        path, data, time_zone = os.path.normpath(os.path.join(*folder)), {}, body.get('timeZone')
        if not os.path.exists(path): return JSONResponse({"status": 'error', "message": 'Configuration file not found.'})
        with open(path, 'r', encoding=functions.encoding_detect(path)) as f:
            files = json.load(f)
        parts = re.split('DATA_ITEM', files['timeTable'])
        parts, time_data = [p.strip() for p in parts if p.strip()], []
        for part in parts:
            temp = part.split('\n')
            location, substances, times = temp[0].strip(), temp[4].strip().split(' '), temp[5:]
            if len(times) > 0:
                for idx, substance in enumerate(substances):
                    for item in times:
                        temp_item = item.strip().split(' ')
                        temp_time = pd.to_datetime(temp_item[0], utc=True, format='%Y/%m/%d-%H:%M:%S')
                        temp_time = functions.utc_to_local(temp_time, time_zone)
                        time_data.append([temp_time, location, substance.replace("'", ""), temp_item[idx + 1]])
        result = [item for item in time_data if item[3] != '-999.0']
        data['key'], data['name'], data['mode'] = files['key'], files['folderName'], files['mode']
        data['obs'], data['loads'], data['time_data'] = files['obsPoints'], files['loadsData'], result
        data['times'], data['usefors'] = files['timeTable'], files['usefors']
        data['initial'], data['scheme'] = files['initial'], files['scheme']
        data['maxiter'], data['tolerance'] = files['maxiter'], files['tolerance']
        data['useforsFrom'], data['useforsTo'] = files['useforsFrom'], files['useforsTo']
        return JSONResponse({"status": 'ok', "content": data})
    except Exception as e: functions.error('/load_waq', e)

@router.post("/clone_waq")
async def clone_waq(request: Request, user=Depends(functions.basic_auth)):
    extend_task = None
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        old_name, new_name = body.get('oldName'), body.get('newName')
        project_folder = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, 'output', 'scenarios'))
        redis = request.app.state.redis
        lock = redis.lock(f"{project_name}:clone_waq", timeout=100, blocking_timeout=10)
        async with lock:
            extend_task = asyncio.create_task(functions.auto_extend(lock))
            old_path = os.path.normpath(os.path.join(project_folder, f"{old_name}.json"))
            new_path = os.path.normpath(os.path.join(project_folder, f"{new_name}.json"))
            if not os.path.exists(old_path): 
                return JSONResponse({"status": 'error', "message": f"Path '{old_path}' does not exist."})
            data = json.load(open(old_path, 'r', encoding=functions.encoding_detect(old_path)))
            data['folderName'] = new_name.replace('.json', '')
            data['timeTable'] = data['timeTable'].replace(old_name, new_name)
            json.dump(data, open(new_path, 'w', encoding=functions.encoding_detect(new_path)))
            return JSONResponse({"message": f"WAQ scenario '{new_name}' was cloned successfully!"})
    except Exception as e: functions.error('/clone_waq', e)
    finally:
        if extend_task:
            extend_task.cancel()
            try: await extend_task
            except asyncio.CancelledError: pass

# Delete a file
@router.post("/delete_waq")
async def delete_waq(request: Request, user=Depends(functions.basic_auth)):
    extend_task = None
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, file = request.app.state.redis, body.get('name')
        scenario_folder = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, 'output', 'scenarios'))
        waq_folder = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, 'output', 'WAQ'))
        lock = redis.lock(f"{project_name}:delete_waq", timeout=300)
        async with lock:
            extend_task = asyncio.create_task(functions.auto_extend(lock))
            file_name = os.path.normpath(os.path.join(scenario_folder, f"{file}.json"))
            if not os.path.exists(file_name): 
                return JSONResponse({"status": 'error', "message": f"Path '{file_name}' does not exist."})
            if os.path.exists(waq_folder):
                waq_files = [f for f in os.listdir(waq_folder) if file in f]
                if len(waq_files) > 0:
                    for f in waq_files:
                        temp_path = os.path.normpath(os.path.join(waq_folder, f))
                        functions.safe_remove(temp_path) if f.endswith('.json') else shutil.rmtree(temp_path)
            functions.safe_remove(file_name)
            return JSONResponse({"message": f"Scenario '{file}' was deleted successfully!"})
    except Exception as e: functions.error('/delete_waq', e)
    finally:
        if extend_task:
            extend_task.cancel()
            try: await extend_task
            except asyncio.CancelledError: pass

@router.post("/select_waq")
async def select_waq(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        folder = [PROJECT_ROOT, project_name, "output", 'scenarios']
        path = os.path.normpath(os.path.join(*folder))
        files = [f.replace('.json', '') for f in os.listdir(path) if f.endswith('.json')]
        if len(files) == 0: return JSONResponse({"status": 'error'})
        return JSONResponse({"status": 'ok', "content": files})
    except Exception as e: functions.error('/select_waq', e)

@router.post("/wq_time_from_waq")
async def wq_time_from_waq(request: Request):
    try:
        body = await request.json()
        key = body.get('key')
        if key == 'Simple_Oxygen': from_ = ['NH4', 'CBOD5', 'OXY', 'SOD']
        elif key == 'Oxygen_BOD': from_ = ['OXY', 'CBOD5']
        elif key == 'Cadmium': from_ = ['IM1', 'Cd', 'IM1S1', 'CdS1']
        elif key == 'Eutrophication': from_ = ['A', 'DP', 'NORG', 'NH4', 'NO3']
        elif key == 'Trace_Metals': from_ = ['ASWTOT', 'CUWTOT', 'NIWTOT', 
            'PBWTOT', 'POCW', 'AOCW', 'DOCW', 'SSW', 'ZNWTOT', 'ASREDT', 'ASSTOT', 
            'ASSUBT', 'CUREDT', 'CUSTOT', 'CUSUBT', 'NIREDT', 'NISTOT', 'NISUBT',
            'PBREDT', 'PBSTOT', 'PBSUBT', 'DOCB', 'DOCSUB', 'POCB', 'POCSUB', 
            'S', 'ZNREDT', 'ZNSTOT', 'ZNSUBT']
        elif key == 'Conservative_Tracers': from_ = ['cTR1', 'cTR2', 'cTR3', 'dTR1', 'dTR2', 'dTR3']
        elif key == 'Suspend_Sediment': from_ = ['IM1', 'IM2', 'IM3', 'IM1S1', 'IM2S1', 'IM3S1']
        elif key == 'Metals_Tire_Road': from_ = ['Continuity', 'cTR1', 'dTR1', 'ModTemp', 'IM1', 
            'Cd', 'Cr', 'Cu', 'Ni', 'Pb', 'Zn', 'Tyre1', 'Tyre2', 'Tyre3', 'Tyre4',
            'TyreAgg11', 'TyreAgg12', 'TyreAgg13', 'TyreAgg21', 'TyreAgg22', 'TyreAgg23',
            'TyreAgg31', 'TyreAgg32', 'TyreAgg33', 'TyreAgg41', 'TyreAgg42', 'TyreAgg43', 'IM1S1',
            'IM1S2', 'CdS1', 'CdS2', 'CrS1', 'CrS2', 'CuS1', 'CuS2', 'NiS1', 'NiS2', 'PbS1', 
            'PbS2', 'ZnS1', 'ZnS2', 'TAggSed11', 'TAggSed12', 'TAggSed13', 'TAggSed21', 'TAggSed22',
            'TAggSed23', 'TAggSed31', 'TAggSed32', 'TAggSed33', 'TAggSed41', 'TAggSed42', 'TAggSed43']
        elif key == 'Coliform': from_ = ['Salinity', 'EColi']
        else: return JSONResponse({"status": 'error', "message": f"Unknown key: {key!r}"}, status_code=400)
        return JSONResponse({"status": 'ok', "froms": from_})
    except Exception as e: functions.error('/wq_time_from_waq', e)

@router.post("/wq_time_to_waq")
async def wq_time(request: Request):
    try:
        body = await request.json()
        load_data, time_data = body.get('loadsData'), body.get('timeData')
        folder, time_zone = body.get('folderName'), body.get('timeZone')
        # Check whether the location in time-series is in the load data
        loads, times = [x[0] for x in load_data], [x[1] for x in time_data]
        if not any(x in times for x in loads):
            return JSONResponse({"status": 'error', 
                "message": 'Error: No Location found in the table.\nThe field "Location" has to be defined in the table "List of Loads".'})        
        # Read file and prepare data
        time_data, idx = np.array(time_data), []
        idx = [functions.local_to_utc(x, time_zone) for x in time_data[:, 0]]
        df = pd.DataFrame(time_data[:, 1:], index=idx, columns=['source', 'substance', 'value'])
        # Sort data
        df = df.sort_index(ascending=True)
        # Structure data
        groups, result = df.groupby(['source']), []
        if len(groups) == 0: return JSONResponse({"status": 'error', "message": 'The inputed time-series data is not found in the table.'})
        gr_substance: list = []
        for name, group in groups:
            if (len(group) == 0 or name[0] not in loads): continue
            gr_substance = [x[0][0] for x in group.groupby(['substance'])]
            subs = ' '.join(f"'{x}'" for x in gr_substance)
            temp = ["DATA_ITEM", name[0], "CONCENTRATIONS",
                f"INCLUDE 'includes_deltashell\\load_data_tables\\{folder}.usefors'",
                "TIME LINEAR DATA", subs]
            # Assign data
            temp_df = pd.DataFrame()
            for item in gr_substance:
                subset = group[group['substance'] == item].copy()
                subset.index = pd.to_datetime(subset.index)
                temp_df[item] = pd.to_numeric(subset.value, errors="coerce")
            temp_df = temp_df.sort_index(ascending=True).fillna(-999)
            if isinstance(temp_df.index, pd.DatetimeIndex):
                if temp_df.index.tz is None:
                    temp_df.index = temp_df.index.tz_localize('UTC')
                else: temp_df.index = temp_df.index.tz_convert('UTC')
            idx = pd.DatetimeIndex(temp_df.index)
            if idx.tz is None: idx = idx.tz_localize('UTC')
            else: idx = idx.tz_convert('UTC')
            temp_df.index = idx.strftime('%Y/%m/%d-%H:%M:%S')
            temp_df.reset_index(inplace=True)
            lst = temp_df.astype(str).values.tolist() # Convert to string
            lst = [' '.join(x) for x in lst]
            temp += lst
            result.append('\n'.join(temp))
        return JSONResponse({"status": 'ok',"content": '\n\n\n'.join(result), "tos": gr_substance})
    except Exception as e: functions.error('/wq_time_to_waq', e)

@router.post("/waq_config_writer")
async def waq_config_writer(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, file_name = request.app.state.redis, body.get('folderName')
        lock = redis.lock(f"{project_name}:waq_config", timeout=10)
        async with lock:
            config_path = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "output", "scenarios"))
            if not os.path.exists(config_path): os.makedirs(config_path)
            config_file = os.path.normpath(os.path.join(config_path, f"{file_name}.json"))
            if os.path.exists(config_file): os.remove(config_file)
            with open(config_file, 'w', encoding=functions.encoding_detect(config_file)) as f:
                json.dump(body, f, indent=4)
            return JSONResponse({"status": 'ok', "message": f"Configurations of model '{file_name}' saved successfully."})
    except Exception as e: functions.error('/waq_config_writer', e)
