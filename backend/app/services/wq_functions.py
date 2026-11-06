import os, json
from config import SOURCE_BACKEND
from services import functions
import geopandas as gpd, xarray as xr, numpy as np
from shapely.geometry import Point
from datetime import datetime, timezone, timedelta
from typing import Any


def hydReader(hyd_path: str, time_zone: str) -> dict:
    # Read the hyd file
    data: dict[str, object] = {'filename': 'FlowFM.hyd'}
    check, sinks = False, []
    with open(hyd_path, 'r', encoding=functions.encoding_detect(hyd_path)) as f:
        lines = f.readlines()
    for line in lines:
        if "number-hydrodynamic-layers" in line: data['n_layers'] = line.split()[1]
        if "hydrodynamic-start-time" in line:
            temp = line.split()[1].replace("'", "")
            dt = datetime.strptime(temp, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
            data['start_time'] = functions.utc_to_local(dt, time_zone)
        if "hydrodynamic-stop-time" in line:
            temp = line.split()[1].replace("'", "")
            dt = datetime.strptime(temp, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
            data['stop_time'] = functions.utc_to_local(dt, time_zone)
        if "hydrodynamic-timestep" in line:
            data['time_step1'] = int(line.split()[1].replace("'", ""))
        if "conversion-timestep" in line:
            data['time_step2'] = int(line.split()[1].replace("'", ""))
        if "number-water-quality-segments-per-layer" in line:
            data['n_segments'] = int(line.split()[1])
        if "number-water-quality-layers" in line:
            data['n_layer'] = int(line.split()[1])
        if "attributes-file" in line:
            data['attr_path'] = line.split()[1].replace("'", "")
        if "volumes-file" in line:
            data['vol_path'] = line.split()[1].replace("'", "")
        if "number-horizontal-exchanges" in line:
            data['exchange_x'] = int(line.split()[1])
        if "number-vertical-exchanges" in line:
            data['exchange_z'] = int(line.split()[1])
        if "waqgeom-file" in line or "grid-coordinates-file" in line:
            data['grid'] = line.split()[1].replace("'", "")
        if "pointers-file" in line:
            data['ptr_path'] = line.split()[1].replace("'", "")
        if "areas-file" in line:
            data['area_path'] = line.split()[1].replace("'", "")
        if "flows-file" in line:
            data['flow_path'] = line.split()[1].replace("'", "")
        if "lengths-file" in line:
            data['length_path'] = line.split()[1].replace("'", "")
        if "sink-sources" in line: check = True
        if check and "sink-sources" not in line:
            temp = line.split()
            sinks.append([temp[-1], temp[-2], temp[-3]])
        if "end-sink-sources" in line: check = False
        if "horizontal-surfaces-file" in line:
            data['srf_path'] = line.split()[1].replace("'", "")
        if "vert-diffusion-file" in line:
            data['vdf_path'] = line.split()[1].replace("'", "")
        if "temperature-file" in line:
            data['tem_path'] = line.split()[1].replace("'", "")
        if "salinity-file" in line:
            data['sal_path'] = line.split()[1].replace("'", "")
    data['sink_sources'] = sinks
    if 'n_layer' in data and isinstance(data['n_layer'], int):
        n_seg = data.get('n_segments')
        if isinstance(n_seg, int): data['n_segments'] = n_seg * data['n_layer']
    else: data['n_layer'] = 0
    return data
    
def segmentFinder(lat:float, lon:float, grid:gpd.GeoDataFrame) -> int:
    if grid.empty: return 0
    # Convert the grid to WGS84 if not already
    if grid.crs != '4326': grid = grid.to_crs(epsg=4326)
    # Find the segment that the point is in
    pt = Point(lon, lat)
    mash = grid.contains(pt)
    polygon = grid.loc[mash]
    if polygon.empty: return 0
    idx = int(polygon.index[0])
    return idx + 1

def ddhhmmss_to_seconds(value) -> int:
    s = str(value).strip().strip("'").zfill(8)
    dd, hh, mm, ss = int(s[:-6]), int(s[-6:-4]), int(s[-4:-2]), int(s[-2:])
    return ((dd * 24 + hh) * 60 + mm) * 60 + ss

def seconds_to_ddhhmmss(seconds: int) -> str:
    dd, r = divmod(int(seconds), 86400)
    hh, r = divmod(r, 3600)
    mm, ss = divmod(r, 60)
    return f"{dd:03d}{hh:02d}{mm:02d}{ss:02d}"

def _recordTimes(path: str, n_values: int) -> tuple:
    rec = np.dtype([('t', '<i4'), ('v', '<f4', (n_values,))])
    size = os.path.getsize(path)
    if size % rec.itemsize != 0:
        raise ValueError(f"{os.path.basename(path)}: size {size} is not a multiple of record size {rec.itemsize}")
    data = np.memmap(path, dtype=rec, mode='r')
    t_first, t_last, n_rec = int(data['t'][0]), int(data['t'][-1]), len(data)
    del data
    return t_first, t_last, n_rec

def hydDataWindow(vol_path: str, flow_path: str, n_segments: int, noq: int, ref_time: datetime) -> tuple:
    v_first, v_last, _ = _recordTimes(vol_path, n_segments)
    f_first, f_last, _ = _recordTimes(flow_path, noq)
    first = ref_time + timedelta(seconds=max(v_first, f_first))
    last = ref_time + timedelta(seconds=min(v_last, f_last))
    return first, last

def hydTimeInfo(hyd_path: str) -> dict:
    keys, info = {'conversion-ref-time': 'ref_time', 'conversion-start-time': 'start_time',
        'conversion-stop-time': 'stop_time', 'conversion-timestep': 'conv_step'}, {}
    with open(hyd_path, 'r', encoding=functions.encoding_detect(hyd_path)) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2 or parts[0] not in keys: continue
            value = parts[1].strip("'")
            if parts[0] == 'conversion-timestep':
                info['conv_step'] = ddhhmmss_to_seconds(value)
            else:
                info[keys[parts[0]]] = datetime.strptime(value, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    missing = [k for k in keys.values() if k not in info]
    if missing: raise ValueError(f"Missing in .hyd file: {missing}")
    return info


def simWindowChecker(parameters: dict, noq: int) -> tuple:
    info = hydTimeInfo(parameters['hyd_path'])
    conv = info['conv_step']
    first, last = hydDataWindow(
        parameters['vol_path'], parameters['flow_path'],
        int(parameters['n_segments']), noq, info['ref_time']
    )
    dt_calc = ddhhmmss_to_seconds(parameters['t_calc']) if parameters.get('t_calc') else conv
    dt_out = ddhhmmss_to_seconds(parameters['t_output'])
    if dt_calc <= 0 or dt_out <= 0:
        raise ValueError("Timesteps must be positive.")
    if dt_calc > conv or conv % dt_calc != 0:
        raise ValueError(f"Integration timestep ({dt_calc}s) must be <= and divide the "
                         f"hydrodynamic coupling interval ({conv}s).")
    if dt_out % dt_calc != 0:
        raise ValueError(f"Output timestep ({dt_out}s) must be a multiple of the integration timestep ({dt_calc}s).")
    t_start, t_stop, msgs = parameters['t_start'], parameters['t_stop'], []
    if t_start < first or t_start >= last:
        raise ValueError(f"WAQ start {t_start:%Y-%m-%d %H:%M} UTC is outside the hydrodynamic data "
                         f"({first:%Y-%m-%d %H:%M} – {last:%Y-%m-%d %H:%M} UTC).")
    safe_stop = last - timedelta(seconds=conv)
    target = min(t_stop, safe_stop)
    n_steps = int((target - t_start).total_seconds() // dt_calc)
    if n_steps < 1:
        raise ValueError("WAQ simulation window is too short after aligning to the hydrodynamic data.")
    new_stop = t_start + timedelta(seconds=n_steps * dt_calc)
    if new_stop != t_stop:
        msgs.append(f"Stop time adjusted from {t_stop:%Y-%m-%d %H:%M} to {new_stop:%Y-%m-%d %H:%M} UTC "
            f"(last hydrodynamic record: {last:%Y-%m-%d %H:%M} UTC).")
    return t_start, new_stop, dt_calc, dt_out, ' '.join(msgs)

def poiInspector(ptr_path: str, n_vertical: int) -> dict:
    raw = np.fromfile(ptr_path, dtype=np.int32)
    if raw.size % 4 != 0:
        raise ValueError(f"Unexpected .poi size: {raw.size} ints (not divisible by 4)")
    poi = raw.reshape(-1, 4)
    noq = poi.shape[0]
    min_ptr = int(poi[:, :2].min())
    return {
        'noq': noq, 'exchange_x': noq - int(n_vertical),
        'n_boundaries': -min_ptr if min_ptr < 0 else 0,
    }

def wqPreparation(parameters:dict, key:str, output_folder:str, includes_folder:str) -> tuple[str | None, str]:
    try:
        sample_path = os.path.normpath(os.path.join(SOURCE_BACKEND, 'templates', 'waq'))
        params_INP, params_INC = {}, {}
        model_type: dict[str, Any] = {"model_type": key}
        grid_path = os.path.normpath(os.path.join(os.path.dirname(parameters['hyd_path']), 'FlowFM_waqgeom.nc'))
        data = xr.open_dataset(grid_path, chunks={'time': 1}, decode_times=False)
        grid = functions.unstructuredGridCreator(data)
        data.close()
        poi_info = poiInspector(parameters['ptr_path'], parameters['exchange_z'])
        params_INC['t0'], params_INC['t0_scu'] = '1970.01.01 00:00:00', 1
        params_INC['B2_numsettings'] = f'{parameters["scheme"]}.70 ; integration option\n; detailed balance options'
        # Prepare for the config file B2_simtimers
        t_start, t_stop, dt_calc, dt_out, time_msg = simWindowChecker(parameters, poi_info['noq'])
        start_utc = t_start.strftime('%Y/%m/%d-%H:%M:%S')
        stop_utc = t_stop.strftime('%Y/%m/%d-%H:%M:%S')
        calc_str, out_str = seconds_to_ddhhmmss(dt_calc), seconds_to_ddhhmmss(dt_out)
        params_INC['B2_simtimers'] = [f"{start_utc} ; start time", f"{stop_utc} ; stop time", "0 ; timestep constant", f"{calc_str} ; timestep"]
        # Prepare for the config file B2_outlocs
        obs_point = list(parameters['obs_data'])
        if len(obs_point) > 0:
            content, points = f'{len(obs_point)} ; nr of monitor locations', []
            for point in obs_point:
                segment = segmentFinder(point[1], point[2], grid)
                if segment == 0: continue
                points.append(f'{point[0]} 1\n{segment}')
            joined = '\n'.join(points)
            params_INC['B2_outlocs'] = f"{content}\n{joined}"
            model_type['wq_obs'] = obs_point
        else: params_INC['B2_outlocs'] = '0 ; nr of monitor locations'
        # Prepare for the config file B2_outputtimers
        params_INC['B2_outputtimers'] = [';  output control (see DELWAQ-manual)', ';  yyyy/mm/dd-hh:mm:ss  yyyy/mm/dd-hh:mm:ss  dddhhmmss',
            f'{start_utc} {stop_utc} {out_str} ;  start, stop and step for balance output',
            f'{start_utc} {stop_utc} {out_str} ;  start, stop and step for map output',
            f'{start_utc} {stop_utc} {out_str} ;  start, stop and step for his output'
        ]
        # Prepare for the config file B3_ugrid
        ugrid_path = os.path.normpath(os.path.join(os.path.dirname(parameters['hyd_path']), 'FlowFM_waqgeom.nc'))
        temp_ugrid = ugrid_path.replace(os.sep, "/")
        params_INC['B3_ugrid'] = f"UGRID '{temp_ugrid}'"
        # Prepare for the config file B3_nrofseg
        params_INC['B3_nrofseg'] = f"{parameters['n_segments']} ; number of segments"
        # Prepare for the config file B3_attributes
        temp_attr = parameters['attr_path'].replace(os.sep, "/")
        params_INC['B3_attributes'] = f"INCLUDE '{temp_attr}' ; attributes file"
        # Prepare for the config file B3_volumes
        temp_vol = parameters['vol_path'].replace(os.sep, "/")
        params_INC['B3_volumes'] = f"-2 ; volumes will be interpolated from a binary file\n'{temp_vol}' ; volumes file from hyd file"
        # Prepare for the config file B4_nrofexch
        params_INC['B4_nrofexch'] = f"{poi_info['exchange_x']} 0 {parameters['exchange_z']} ; number of exchanges in three directions"
        # Prepare for the config file B4_pointers
        temp_ptr = parameters['ptr_path'].replace(os.sep, "/")
        params_INC['B4_pointers'] = f"0 ; pointers from binary file.\n'{temp_ptr}' ; pointers file"
        # Prepare for the config file B4_cdispersion
        params_INC['B4_cdispersion'] = "1 0.0 1E-07 ; constant dispersion"
        # Prepare for the config file B4_area
        temp_area = parameters['area_path'].replace(os.sep, "/")
        params_INC['B4_area'] = f"-2 ; areas will be interpolated from a binary file\n'{temp_area}' ; areas file"
        # Prepare for the config file B4_flows
        temp_flow = parameters['flow_path'].replace(os.sep, "/")
        params_INC['B4_flows'] = f"-2 ; flows from binary file\n'{temp_flow}' ; flows file"
        # Prepare for the config file B4_length
        temp_length = parameters['length_path'].replace(os.sep, "/")
        params_INC['B4_length'] = f"0 ; Lengths from binary file\n'{temp_length}' ; lengths file"
        # Prepare for the config file B5
        params_INC['B5_boundlist'] = [";'NodeID' 'Comment field' 'Boundary name used for data grouping'"]
        n_bnd = poi_info['n_boundaries']
        names = [p[0] for p in parameters['sources']] if parameters['sources'] else []
        for i in range(n_bnd):
            group = names[i % len(names)] if names else f"Bnd_{i+1}"
            params_INC['B5_boundlist'].append(f"'{i+1}' '' '{group}'")
        params_INC['B5_boundaliases'], params_INC['B5_bounddata'] = '', ''
        # Prepare for the config file B6_loads
        n_loads = parameters["loads_data"]
        if len(list(parameters["loads_data"])) > 0: model_type['wq_loads'] = list(parameters["loads_data"])
        params_INC['B6_loads'] = [f'{len(n_loads)} ; Number of loads']
        params_INC['B6_loads'].append(';SegmentID  Load-name  Comment  Load-type')
        for load in n_loads:
            segment = segmentFinder(load[1], load[2], grid)
            if segment == 0: continue
            params_INC['B6_loads'].append(f"{segment} '{load[0]}' '' ''")
        # Prepare for the config file B6_loads_aliases
        params_INC['B6_loads_aliases'] = []
        for load in n_loads:
            params_INC['B6_loads_aliases'].append(f"USEDATA_ITEM '{load[0]}' FORITEM\n'{load[0]}'")
        # Prepare for the config file B6_loads_data
        tbl_path = os.path.normpath(os.path.join(output_folder, 'includes_deltashell', 'load_data_tables', f'{parameters["folder_name"]}.tbl'))
        temp_btl = tbl_path.replace(os.sep, "/")
        params_INC['B6_loads_data'] = f"INCLUDE '{temp_btl}'"
        # Prepare for the config file B7_functions, B7_dispersion
        params_INC['B7_functions'], params_INC['B7_dispersion'] = '', ''
        # Prepare for the config file B7_parameters
        temp_srf = parameters['srf_path'].replace(os.sep, "/")
        params_INC['B7_parameters'] = f"PARAMETERS\n'Surf'\nALL\nBINARY_FILE '{temp_srf}' ; from horizontal-surfaces-file key in hyd file"
        # Prepare for the config file B7_vdiffusion
        temp_vdf = parameters['vdf_path'].replace(os.sep, "/")
        params_INC['B7_vdiffusion'] = ["CONSTANTS 'ACTIVE_VertDisp' DATA 1.0","SEG_FUNCTIONS","'VertDisper'", "ALL", f"BINARY_FILE '{temp_vdf}'"]
        # Prepare for the config file B7_numerical_options
        params_INC['B7_numerical_options'] = ["CONSTANTS 'CLOSE_ERR' DATA 1 ; If defined, allow delwaq to correct water volumes to keep concentrations continuous",
            "CONSTANTS 'NOTHREADS' DATA 2 ; Number of threads used by delwaq",
            "CONSTANTS 'DRY_THRESH' DATA 0.001 ; Dry cell threshold",
            f"CONSTANTS 'maxiter' DATA {int(parameters['maxiter'])} ; Maximum number of iterations",
            f"CONSTANTS 'tolerance' DATA {parameters['tolerance']} ; Convergence tolerance",
            "CONSTANTS 'iteration report' DATA 0 ; Write iteration report (when 1) or not (when 0)"
        ]
        # Prepare for the config file B8_initials
        params_INC['B8_initials'], init_dict = ["MASS/M2", "INITIALS"], {}
        for item in parameters['initial_set']:
            if item == '': continue
            temp = item.split(' ')
            init_dict[temp[0]] = temp[1]
        for item in parameters['initial_list']:
            params_INC['B8_initials'].append(f"'{item}'")
        params_INC['B8_initials'].append("DEFAULTS")
        for i in parameters['initial_list']:
            if i in init_dict.keys() and len(init_dict[i]) > 0: params_INC['B8_initials'].append(init_dict[i])
            else: params_INC['B8_initials'].append("0")

        # *************************** Simple oxygen model ***************************
        if key == 'simple-oxygen':
            # Get data for B1_sublist variable
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_simple_oxygen.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], ['Nitrif_NH4', 'BODCOD', 'RearOXY', 'SedOXYDem', 'PosOXY',
                     'DynDepth', 'SaturOXY', 'TotDepth', 'Veloc']
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['RcNit', 'RcBOD ', 'COXBOD', 'OOXBOD', 'CFLBOD',
                     'O2FuncBOD', 'BOD5', 'BODu', 'SWRear', 'KLRear', 'fSOD', 'RcSOD', 'VWind']
            values = ['0.1', '0.3', '1', '5', '0.3', '0', '0', '0', '1', '1', '0', '0.1', '3']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            temp_tem = parameters['tem_path'].replace(os.sep, "/")
            params_INC['B7_segfunctions'] = ["SEG_FUNCTIONS", "'Temp'", "ALL", f"BINARY_FILE '{temp_tem}'"]
            # Prepare for the config file B9
            pr = ['DO']
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        
        # *************************** Trace metals model ***************************
        elif key == 'trace-metals':
            # Get data for B1_sublist variable
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_trace_metals.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], ['HydDuflow', 'Metal']
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['aAs', 'aCu ', 'aNi', 'aPb', 'aZn', 'bAs', 'bCu', 'bNi', 'bPb', 'bZn', 
                'alfa', 'CUSulf', 'DZ1', 'DZ2', 'Ez0', 'fbx', 'fc', 'fwx', 'KAsDOC', 'KAsSS', 'KAsSSW', 'KCuDOC', 'KCuSS', 'KCuSSW',
                'KdAOC', 'KHYDW', 'KHYDB', 'KDMINW', 'KDMINB', 'KNiDOC', 'KNiSS', 'KNiSSW', 'KPbDOC', 'KPbSS', 'KPbSSW', 'KZnDOC',
                'KZnSS', 'KZnSSW', 'NIsulf', 'Pbsulf', 'POR', 'RHOANO', 'RHOORG', 'Vsp', 'Vss', 'Vsn', 'Znsulf', 'Cl', 'pHb', 'PAOC',
                'Fres', 'ASatm', 'CUatm', 'NIatm', 'Pbatm', 'Znatm']
            values = ['0', '1.25', '0', '1.176', '1.358', '0', '-5.39E-05', '0', '-6.59E-05', '-8.06E-05', '0.5', '0.003', '0.01',
                '0.09', '5E-05', '10', '0.52', '30', '1E-05', '0.003', '0.1', '0.5', '0.085', '0.05', '0.5', '0.001', '5E-05',
                '0.04', '0.015', '0.1', '0.01', '0.008', '1.02', '0.231', '0.64', '0.1', '0.02', '0.22', '0.025', '0.003', '0.8',
                '2600000', '1000000', '0.4', '1.5', '0.7', '0.01', '200', '6', '1', '20', '0','0', '0', '0', '0', '0', '0', '0', '0']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            params_INC['B7_segfunctions'] = ''
            pr = ['Z', 'Q', 'As', 'dt', 'dx', 'V', 'Wf', 'Wd', 'SSB', 'FCSSW', 'FCSSB', 'FOMW', 'FSSW', 'FOMB', 'FSSB', 'RHOSSW', 'RHOSSB', 'VR', 'VS', 'FSPOC', 'EZ1',
                'EZ2', 'KASSSB', 'KCUSSB', 'KNISSB', 'KPBSSB', 'KZNSSB', 'ASWDIS', 'ASWDOC', 'ASWSS', 'ASSDIS', 'ASSDOC', 'ASSSS', 'ASREDDOC', 'ASREDSS', 'ASREDDIS',
                'ASSUBDIS', 'ASSUBDOC', 'ASSUBSS', 'CUWDIS', 'CUWDOC', 'CUWSS', 'CUSDIS', 'CUSDOC', 'CUSSS', 'CUREDDOC', 'CUREDSS', 'CUREDDIS', 'CUSUBDIS', 'CUSUBDOC',
                'CUSUBSS', 'NIWDIS', 'NIWDOC', 'NIWSS', 'NISDIS', 'NISDOC', 'NISSS', 'NIREDDOC', 'NIREDSS', 'NIREDDIS', 'NISUBDIS', 'NISUBDOC', 'NISUBSS', 'PBWDIS',
                'PBWDOC', 'PBWSS', 'PBSDIS', 'PBSDOC', 'PBSSS', 'PBREDDOC', 'PBREDSS', 'PBREDDIS', 'PBSUBDIS', 'PBSUBDOC', 'PBSUBSS', 'ZNWDIS', 'ZNWDOC', 'ZNWSS',
                'ZNSDIS', 'ZNSDOC', 'ZNSSS', 'ZNREDDOC', 'ZNREDSS', 'ZNREDDIS', 'ZNSUBDIS', 'ZNSUBDOC', 'ZNSUBSS', 'MASATM', 'MASSED', 'MASRES', 'MASDIF1', 'MASDIF2',
                'MASDIF3', 'MASPSAD1', 'MASPSAD2', 'MASPSAD3', 'MCUATM', 'MCUSED', 'MCURES', 'MCUDIF1', 'MCUDIF2', 'MCUDIF3', 'MCUPSAD1', 'MCUPSAD2', 'MCUPSAD3',
                'MNIATM', 'MNISED', 'MNIRES', 'MNIDIF1', 'MNIDIF2', 'MNIDIF3', 'MNIPSAD1', 'MNIPSAD2', 'MNIPSAD3', 'MPBATM', 'MPBSED', 'MPBRES', 'MPBDIF1', 'MPBDIF2',
                'MPBDIF3', 'MPBPSAD1', 'MPBPSAD2', 'MPBPSAD3', 'MZNATM', 'MZNSED', 'MZNRES', 'MZNDIF1', 'MZNDIF2', 'MZNDIF3', 'MZNPSAD1', 'MZNPSAD2', 'MZNPSAD3',
                'ASDISW', 'ASDISS', 'ASDISRED', 'ASSSW', 'ASSSRED', 'CUDISW', 'CUDISS', 'CUDISRED', 'CUSSW', 'CUSSRED', 'NIDISW', 'NIDISS', 'NIDISRED', 'NISSW',
                'NISSRED', 'PBDISW', 'PBDISS', 'PBDISRED', 'PBSSW', 'PBSSRED', 'ZNDISW', 'ZNDISS', 'ZNDISRED', 'ZNSSW', 'ZNSSRED']
            # Prepare for the config file B9
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        elif key == 'oxygen-bod-water':
            # Get data for B1_sublist variable
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_bod_water.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], ['RearOXY', 'BODCOD', 'DynDepth', 'SaturOXY', 'TotDepth', 'Veloc']
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['VWind', 'SWRear', 'KLRear', 'RcBOD', 'Phyt']
            values = ['3', '1', '1', '0.3', '0']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            content1, params_INC['B7_segfunctions'] = ['Salinity', 'Temp'], []
            content2 = [parameters['sal_path'].replace(os.sep, "/"), parameters['tem_path'].replace(os.sep, "/")]
            for i in range(len(content1)):
                params_INC['B7_segfunctions'].append(f"SEG_FUNCTIONS\n'{content1[i]}'\nALL\nBINARY_FILE '{content2[i]}'")
            # Prepare for the config file B9
            pr = ['Depth', 'Temp', 'Velocity', 'VWind', 'SWRear', 'KLRear', 'TCRear', 'SaturOXY', 'Salinity', 'RCREAR',
                'SatPercOXY', 'SwOXYDem', 'RcBOD', 'TcBOD', 'BOD5', 'BODu', 'fSOD', 'RcSOD', 'TcSOD', 'TotalDepth', 'COXSOD',
                'OOXSOD', 'VSedBOD5', 'Tau', 'TaucSBOD', 'MinDepth', 'PSedBOD5', 'fSedBOD5', 'fSedSOD', 'Cl', 'SWSatOXY', 'CHEZY']
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        elif key == 'cadmium':
            # Get data for B1_sublist variable
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_cadmium.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], ['Compos', 'Sed_IM1', 'S12TraIM1', 'S1_Comp', 'Sed_Cd', 'S12TraCd', 'CalTau', 'DynDepth',
                                                         'Res_DM', 'PartWK_Cd', 'PartS1_Cd', 'Veloc', 'Chezy', 'TotDepth']
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['TaucSIM1', 'ZResDM', 'TaucRS1DM']
            values = ['0.1', '0', '0.2']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            params_INC['B7_segfunctions'] = ''
            # Prepare for the config file B9
            pr = []
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        elif key == 'eutrophication':
            # Get data for B1_sublist variable
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_eu_1a.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], ['HydDuflow', 'Eutr1a']
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['UMAX', 'ALFA', 'IOPT', 'PIOPT', 'ACA', 'KLOSS', 'KMIN', 'KNIT', 'KDEN',
                'KP', 'KN', 'EPS0', 'EPSALG', 'THGA', 'THMIN', 'THNIT', 'THDEN', 'VSO', 'ANC', 'APC', 'ISOM', 'L', 'T', 'NFLUX', 'PFLUX']
            values = ['1', '0.02', '40', '1', '30', '0.1', '0.1', '0.2', '0.05', '0.005', '0.01', '2', '0.016', '1.04', '1.04', '1.06', '1.06',
                      '1', '0.176', '0.024', '100', '12', '20', '0', '0']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            params_INC['B7_segfunctions'] = ''
            # Prepare for the config file B9
            pr = ['Z', 'Q', 'As', 'dt', 'dx', 'V', 'Wf', 'Wd', 'FT', 'FN', 'IOMAX', 'CHLA', 'ETOT', 'H1', 'fl2', 'f', 'ALFA1', 'GA',
                  'KMINT', 'KNITT', 'KDENT', 'KJN', 'NTOT', 'PTOT']
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        elif key == 'conservative-tracers':
            # Get data for B1_sublist variable
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_conservative.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], ['Age1', 'Age2', 'Age3']
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['RcDecTR1', 'RcDecTR3']
            values = ['0.01', '0.01']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            params_INC['B7_segfunctions'] = ''
            # Prepare for the config file B9
            pr = []
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        elif key == 'suspend-sediment':
            # Get data for B1_sublist variable
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_suspend_sediment.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], ['Compos', 'Sed_IM1', 'S12TraIM1', 'S1_Comp', 'Sed_IM2',
                    'S12TraIM2', 'Sed_IM3', 'S12TraIM3', 'CalTau', 'DynDepth', 'Res_DM', 'Veloc', 'Chezy', 'TotDepth']
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['VSedIM1', 'TaucSIM1', 'VSedIM2', 'TaucSIM2', 'VSedIM3', 'TaucSIM3', 'TaucRS1DM']
            values = ['0.1', '0.1', '0.1', '0.1', '0.1', '0.1', '0.2']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            params_INC['B7_segfunctions'] = ''
            # Prepare for the config file B9
            pr = []
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        elif key == 'trwp-metals':
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_trwp_metals.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], [
                'Emersion', 'ResTim', 'HDisperVel', 'HDisperAdd', 'Evap_Conti',
                'Age1', 'Decay1', 'Temperatur', 'MakOOC', 'Compos', 'Res_Buffer', 'S12TraIM1', 'Sed_IM1', 'AtmDep_IM1',
                'Dfwast_IM1', 'Secchi', 'Dredge', 'IM_Floceq', 'TraSe2_IM1', 'MakOOCS1', 'S1_Comp', 'S2_Comp', 'Sed_Cd',
                'S12TraCd', 'AtmDep_Cd', 'Dfwast_Cd', 'Sed_Cr', 'S12TraCr', 'AtmDep_Cr', 'Dfwast_Cr', 'Sed_Cu', 'S12TraCu',
                'AtmDep_Cu', 'Dfwast_Cu', 'Sed_Ni', 'S12TraNi', 'AtmDep_Ni', 'Dfwast_Ni', 'Sed_Pb', 'S12TraPb', 'AtmDep_Pb',
                'Dfwast_Pb', 'Sed_Zn', 'S12TraZn', 'AtmDep_Zn', 'Dfwast_Zn', 'DynDepth', 'TotDepth', 'Meteo', 'CalTau',
                'S12TraIM2', 'S12TraIM3', 'Res_DM', 'Extinc_VLG', 'PartWK_Cd', 'PartS1_Cd', 'PartWK_Cr', 'PartS1_Cr',
                'PartWK_Cu', 'PartS1_Cu', 'PartWK_Ni', 'PartS1_Ni', 'PartWK_Pb', 'PartS1_Pb', 'PartWK_Zn', 'PartS1_Zn',
                'Veloc', 'Chezy', 'PROPSING', 'PROPTAGG', 'HTRAGG', 'SEDTYRE', 'SEDTAGG', 'SUMTRWP'
                ]
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['ZThreshold', 'Dfact_a', 'Dfact_b', 'Dfact_c', 'Dback', 
                'Dmin', 'Dmax', 'AddDispH', 'fEvapConti', 'sw1AtmDCon', 'sw2AtmDCon', 'RcDecTR1', 'dDecdTR1', 'NatTemp',
                'CP', 'SwitchTemp', 'FactRcHeat', 'ZHeatExch', 'SwTempDF', 'IM2', 'IM3', 'FCSEDIM1', 'FCSEDIM2', 'FCSEDIM3',
                'DMCFOOC', 'DOC', 'Phyt', 'IM2S1', 'IM3S1', 'IM2S2', 'IM3S2', 'TauShields', 'GRAIN50', 'GRAV', 'KinViscos',
                'RHOSAND', 'RhoWater', 'PORS2', 'ThickS2', 'MinDepth', 'MaxResPup', 'TaucRS1IM3', 'DMS2', 'FrTIMS2', 'FrIM1S2',
                'FrIM1S3', 'fBurS1DM', 'fBurS2DM', 'fDigS1DM', 'fDigS2DM', 'SWDigS1', 'ZSedIM1', 'VSedIM1', 'TaucSIM1', 
                'FrIM1SedS2', 'fAtmDepIM1', 'sw1AtmDIM1', 'sw2AtmDIM1', 'fDfwastIM1', 'sw1DfwaIM1', 'sw2DfwaIM1', 'PAConstant',
                'basin_no', 'no_basin', '*IM1', '*IM2', '*IM3', '*IM1S1', '*IM2S1', '*IM3S1', '*IM1S2', '*IM2S2', '*IM3S2',
                'start_dr01', 'start_dr02', 'freq__dr04', 'freq__dr05', 'freq__dr06', 'freq__dr07', 'freq__dr08', 'freq__dr09',
                'crit__dr01', 'crit__dr02', 'crit__dr03', 'crit__dr04', 'crit__dr05', 'crit__dr06', 'crit__dr07', 'crit__dr08',
                'crit__dr09', 'sws1s2dr01', 'sws1s2dr02', 'sws1s2dr03', 'sws1s2dr04', 'sws1s2dr05', 'sws1s2dr06', 'sws1s2dr07',
                'sws1s2dr08', 'sws1s2dr09', 'seg___dr01', 'seg___dr02', 'seg___dr03', 'seg___dr04', 'seg___dr05', 'seg___dr06',
                'seg___dr07', 'seg___dr08', 'seg___dr09', 'speed_dr01', 'speed_dr02', 'speed_dr03', 'speed_dr04', 'speed_dr05',
                'speed_dr06', 'speed_dr07', 'speed_dr08', 'speed_dr09', 'relab_dr01', 'relab_dr02', 'relab_dr03', 'relab_dr04',
                'relab_dr05', 'relab_dr06', 'relab_dr07', 'relab_dr08', 'relab_dr09', 'SwFloceq', 'RcFloc', 'RcBreakup', 'VxRes0',
                'VxSedDum', 'VxBur0', 'VxTur0', 'VxDif0', 'VxSep0', 'FCSEDIM1S1', 'FCSEDIM2S1', 'FCSEDIM3S1', 'DMCFOOCS', 'OOCS1',
                'DetCS1', 'AAPS1', 'DetNS1', 'DetPS1', 'DetSiS1', 'OONS1', 'PHYTS1', 'fSedIM2', 'fSedIM3', 'fSedPOCnoa', 'fSedPHYT',
                'fAtmDepCd', 'sw1AtmDCd', 'sw2AtmDCd', 'fDfwastCd', 'sw1DfwaCd', 'sw2DfwaCd', 'QCrIM2', 'QCrIM3', 'QCrPOC', 'QCrPHYT',
                'QCrDMS2', 'QCrDMS3', 'fAtmDepCr', 'sw1AtmDCr', 'sw2AtmDCr', 'fDfwastCr', 'sw1DfwaCr', 'sw2DfwaCr', 'QCuIM2', 'QCuIM3',
                'QCuPOC', 'QCuPHYT', 'fAtmDepCu', 'sw1AtmDCu', 'sw2AtmDCu', 'fDfwastCu', 'sw1DfwaCu', 'sw2DfwaCu', 'fAtmDepNi', 'sw1AtmDNi',
                'sw2AtmDNi', 'fDfwastNi', 'sw1DfwaNi', 'sw2DfwaNi', 'QPbIM2', 'QPbIM3', 'QPbPOC', 'QPbPHYT', 'QPbDMS2', 'QPbDMS3',
                'fAtmDepPb', 'sw1AtmDPb', 'sw2AtmDPb', 'fDfwastPb', 'sw1DfwaPb', 'sw2DfwaPb', 'fAtmDepZn', 'sw1AtmDZn', 'sw2AtmDZn', 'fDfwastZn',
                'sw1DfwaZn', 'sw2DfwaZn', 'Rad_1', 'VWind_1', 'WinDir_1', 'RelHum_1', 'AirTemp_1', 'AirPres_1', 'Cloud_1', 'Rad_2', 'VWind_2',
                'WinDir_2', 'RelHum_2', 'AirTemp_2', 'AirPres_2', 'Cloud_2', 'Rad_3', 'VWind_3', 'WinDir_3', 'RelHum_3', 'AirTemp_3', 'AirPres_3',
                'Cloud_3', 'Rad_4', 'VWind_4', 'WinDir_4', 'RelHum_4', 'AirTemp_4', 'AirPres_4', 'Cloud_4', 'Rad_5', 'VWind_5', 'WinDir_5',
                'RelHum_5', 'AirTemp_5', 'AirPres_5', 'Cloud_5', 'Rad_6', 'VWind_6', 'WinDir_6', 'RelHum_6', 'AirTemp_6', 'AirPres_6',
                'Cloud_6', 'Rad_7', 'VWind_7', 'WinDir_7', 'RelHum_7', 'AirTemp_7', 'AirPres_7', 'Cloud_7', 'Rad_8', 'VWind_8', 'WinDir_8',
                'RelHum_8', 'AirTemp_8', 'AirPres_8', 'Cloud_8', 'Rad_9', 'VWind_9', 'WinDir_9', 'RelHum_9', 'AirTemp_9', 'AirPres_9',
                'Cloud_9', 'Rad_10', 'VWind_10', 'WinDir_10', 'RelHum_10', 'AirTemp_10', 'AirPres_10', 'Cloud_10', 'XMeteo1', 'YMeteo1',
                'XMeteo2', 'YMeteo2', 'XMeteo3', 'YMeteo3', 'XMeteo4', 'YMeteo4', 'XMeteo5', 'YMeteo5', 'XMeteo6', 'YMeteo6', 'XMeteo7',
                'YMeteo7', 'XMeteo8', 'YMeteo8', 'XMeteo9', 'YMeteo9', 'XMeteo10', 'YMeteo10', 'XYScaleFac', 'NoMeteoSta', 'MeteoClcSW',
                'XSeg', 'YSeg', 'XDOCCd', 'KdCdIM1', 'KdCdIM2', 'DOCS1', 'KdCdIM1S1', 'KdCdIM2S1', 'XDOCCr', 'KdCrIM1', 'KdCrIM2',
                'KdCrIM1S1', 'KdCrIM2S1', 'XDOCCu', 'KdCuIM1', 'KdCuIM2', 'XDOCPb', 'KdPbIM1', 'KdPbIM2', 'KdPbIM1S1', 'KdPbIM2S1',
                'XDOCZn', 'KdZnIM1', 'KdZnIM2', 'DiamTyre1', 'Dens_Tyre1', 'ShapeFac1', 'DiamTyre2', 'Dens_Tyre2', 'ShapeFac2',
                'DiamTyre3', 'Dens_Tyre3', 'ShapeFac3', 'DiamTyre4', 'Dens_Tyre4', 'ShapeFac4', 'Diam_Susp1', 'Dens_Susp1', 'Diam_Susp2',
                'Dens_Susp2', 'Diam_Susp3', 'Dens_Susp3', 'ShapeFacSu', 'BioFilmThk', 'BioFilmDen', 'Efficiency', 'SafeFactor', 'VSedIM2', 'VSedIM3']
            values = ['0.01', '10', '0.8', '1.2', '10', '0', '1000', '10', '-999', '1', '0', '0.01', '0.01', '15', '4183', '1', '1', '0',
                '0', '0', '0', '-999', '0', '0', '2.5', '0', '0', '0', '0', '0', '0', '0.2', '0.0003', '9.8', '0.000001', '2600000',
                '-999', '0', '0.5', '0.1', '1.0E+20', '0.2', '0.000001', '0', '0', '0', '0', '0', '0', '0', '0', '0',
                '0.1', '0.1', '0','-999', '1', '0', '-999', '1', '0', '1.7', '0', '-999', '1', '1', '1', '1', '1', '1', '1', '1', '1',
                '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '1', '1', '1', '1', '1', '1',
                '1', '1', '1', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0',
                '0', '0', '0', '0', '0', '0', '0', '0', '2.31', '2.31', '0', '0', '0', '0', '0', '0', '-999', '0', '0', '1.7',
                '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '-999', '1', '0', '-999', '1', '0', '0', '0', '0',
                '0', '0', '0', '-999', '1', '0', '-999', '1', '0', '0', '0', '0', '0', '-999', '1', '0', '-999', '1', '0', '-999',
                '1', '0', '-999', '1', '0', '0', '0', '0', '0', '0', '0', '-999', '1', '0', '-999', '1', '0', '-999', '1', '0',
                '-999', '1', '0', '-999', '-999', '-999', '-999', '-999', '-999', '-999', '0', '0', '0', '0', '0', '0', '0.5', 
                '0', '0', '0', '0', '0', '0', '0.5', '0', '0', '0', '0', '0', '0', '0.5', '0', '0', '0', '0', '0', '0', '0.5',
                '0', '0', '-999', '0', '0', '0', '0', '0', '0', '-999', '0', '0', '0', '0.5', '0', '0', '-999', '0', '0', '0',
                '0.5', '0', '0', '0', '0', '0', '0', '0.5', '0', '0', '0', '0', '0', '0', '0.5', '0', '0', '0', '0', '0', '0',
                '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', '1', '1', '1', '-999', '-999', '0.1',
                '130', '130', '0', '130', '130', '0.18', '290', '290', '290', '290', '0.18', '50', '50', '0.18', '640', '640',
                '640', '640', '0.18', '110', '110', '30', '1800', '0.8', '75', '1800', '0.8', '125', '1800', '0.8', '200', '1800',
                '0.8', '2', '2650', '16', '2650', '63', '2650', '0.7', '0', '1100', '0.001', '0.05', '0', '0']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            params_INC['B7_segfunctions'] = ''
            # Prepare for the config file B9
            pr = ['Depth', 'ZThreshold', 'SwEmersion', 'ResTim', 'Dfact_a', 'Dfact_b', 'Dfact_c', 'Dback',
                'Dmin', 'Dmax', 'TotalDepth', 'Ddir1', 'AddDispH', 'HorzDispA', 'fEvapConti', 'sw1AtmDCon',
                'sw2AtmDCon', 'RcDecTR1', 'AgeTR1', 'dDecdTR1', 'NatTemp', 'VWind', 'CP', 'SwitchTemp', 'FactRcHeat',
                'ZHeatExch', 'SwTempDF', 'LocSedDept', 'IM2', 'IM3', 'FCSEDIM1', 'FCSEDIM2', 'FCSEDIM3', 'DMCFOOC',
                'POCnoa', 'DOC', 'Phyt', 'TPM', 'IM2S1', 'IM3S1', 'IM2S2', 'IM3S2', 'Tau', 'TauShields', 'GRAIN50',
                'GRAV', 'KinViscos', 'RHOSAND', 'RhoWater', 'PORS2', 'ThickS2', 'MinDepth', 'MaxResPup', 'FactResPup',
                'SWResusp', 'ZResIM1', 'VResIM1', 'TaucRS1IM1', 'ZResIM2', 'VResIM2', 'TaucRS1IM2', 'ZResIM3', 'VResIM3',
                'TaucRS1IM3', 'DMS1', 'DMS2', 'fRIM1S2Pup', 'fRIM2S2Pup', 'fRIM3S2Pup', 'fRTIMS2Pup', 'Pshields', 'FrTIMS2',
                'FrPOMS2', 'fResS1IM1', 'fResS1IM2', 'fResS1IM3', 'fResS1DM', 'fResS2DM', 'dResS1IM2', 'FrIM1S1', 'FrIM1S2',
                'FrIM1S3', 'fBurS1DM', 'fBurS2DM', 'fDigS1DM', 'fDigS2DM', 'SWDigS1', 'ZSedIM1', 'VSedIM1', 'TaucSIM1', 'FrIM1SedS2',
                'fSedIM1', 'fAtmDepIM1', 'sw1AtmDIM1', 'sw2AtmDIM1', 'fDfwastIM1', 'sw1DfwaIM1', 'sw2DfwaIM1', 'ExtVl', 'PAConstant',
                'SecchiDept', 'basin_no', 'no_basin', 'ActThS1', 'ActThS2', 'start_dr01', 'start_dr02', 'start_dr03', 'start_dr04',
                'start_dr05', 'start_dr06', 'start_dr07', 'start_dr08', 'start_dr09', 'freq__dr01', 'freq__dr02', 'freq__dr03',
                'freq__dr04', 'freq__dr05', 'freq__dr06', 'freq__dr07', 'freq__dr08', 'freq__dr09', 'crit__dr01', 'crit__dr02',
                'crit__dr03', 'crit__dr04', 'crit__dr05', 'crit__dr06', 'crit__dr07', 'crit__dr08', 'crit__dr09', 'sws1s2dr01',
                'sws1s2dr02', 'sws1s2dr03', 'sws1s2dr04', 'sws1s2dr05', 'sws1s2dr06', 'sws1s2dr07', 'sws1s2dr08', 'sws1s2dr09',
                'seg___dr01', 'seg___dr02', 'seg___dr03', 'seg___dr04', 'seg___dr05', 'seg___dr06', 'seg___dr07', 'seg___dr08',
                'seg___dr09', 'speed_dr01', 'speed_dr02', 'speed_dr03', 'speed_dr04', 'speed_dr05', 'speed_dr06', 'speed_dr07',
                'speed_dr08', 'speed_dr09', 'relab_dr01', 'relab_dr02', 'relab_dr03', 'relab_dr04', 'relab_dr05', 'relab_dr06',
                'relab_dr07', 'relab_dr08', 'relab_dr09', 'SwFloceq', 'RcFloc', 'RcBreakup', 'SPMratioem', 'VxRes0', 'VxSedDum',
                'VxBur0', 'VxTur0', 'VxDif0', 'VxSep0', 'FCSEDIM1S1', 'FCSEDIM2S1', 'FCSEDIM3S1', 'DMCFOOCS', 'POCS1', 'dDumS1Mak',
                'OOCS1', 'DetCS1', 'AAPS1', 'DetNS1', 'DetPS1', 'DetSiS1', 'OONS1', 'PHYTS1', 'fSedIM2', 'fSedIM3', 'fSedPOCnoa',
                'fSedPHYT', 'fAtmDepCd', 'sw1AtmDCd', 'sw2AtmDCd', 'dAtmDepCd', 'fDfwastCd', 'sw1DfwaCd', 'sw2DfwaCd', 'QCrIM1',
                'QCrIM2', 'QCrIM3', 'QCrPOC', 'QCrPHYT', 'QCrDMS1', 'QCrDMS2', 'QCrDMS3', 'fAtmDepCr', 'sw1AtmDCr', 'sw2AtmDCr',
                'fDfwastCr', 'sw1DfwaCr', 'sw2DfwaCr', 'QCuIM1', 'QCuIM2', 'QCuIM3', 'QCuPOC', 'QCuPHYT', 'fAtmDepCu', 'sw1AtmDCu',
                'sw2AtmDCu', 'fDfwastCu', 'sw1DfwaCu', 'sw2DfwaCu', 'fAtmDepNi', 'sw1AtmDNi', 'sw2AtmDNi', 'fDfwastNi', 'sw1DfwaNi',
                'sw2DfwaNi', 'QPbIM1', 'QPbIM2', 'QPbIM3', 'QPbPOC', 'QPbPHYT', 'QPbDMS1', 'QPbDMS2', 'QPbDMS3', 'fAtmDepPb',
                'sw1AtmDPb', 'sw2AtmDPb', 'fDfwastPb', 'sw1DfwaPb', 'sw2DfwaPb', 'fAtmDepZn', 'sw1AtmDZn', 'sw2AtmDZn', 'fDfwastZn',
                'sw1DfwaZn', 'sw2DfwaZn', 'LocalDepth', 'Rad_1', 'VWind_1', 'WinDir_1', 'RelHum_1', 'AirTemp_1', 'AirPres_1','Cloud_1',
                'Rad_2', 'VWind_2', 'WinDir_2', 'RelHum_2', 'AirTemp_2', 'AirPres_2', 'Cloud_2', 'Rad_3', 'VWind_3', 'WinDir_3',
                'RelHum_3', 'AirTemp_3', 'AirPres_3', 'Cloud_3', 'Rad_4', 'VWind_4', 'WinDir_4', 'RelHum_4', 'AirTemp_4', 'AirPres_4',
                'Cloud_4', 'Rad_5', 'VWind_5', 'WinDir_5', 'RelHum_5', 'AirTemp_5', 'AirPres_5', 'Cloud_5', 'Rad_6', 'VWind_6', 'WinDir_6',
                'RelHum_6', 'AirTemp_6', 'AirPres_6', 'Cloud_6', 'Rad_7', 'VWind_7', 'WinDir_7', 'RelHum_7', 'AirTemp_7', 'AirPres_7',
                'Cloud_7', 'Rad_8', 'VWind_8', 'WinDir_8', 'RelHum_8', 'AirTemp_8', 'AirPres_8', 'Cloud_8', 'Rad_9', 'VWind_9', 'WinDir_9',
                'RelHum_9', 'AirTemp_9', 'AirPres_9', 'Cloud_9', 'Rad_10', 'VWind_10', 'WinDir_10', 'RelHum_10', 'AirTemp_10', 'AirPres_10',
                'Cloud_10', 'XMeteo1', 'YMeteo1', 'XMeteo2', 'YMeteo2', 'XMeteo3', 'YMeteo3', 'XMeteo4', 'YMeteo4', 'XMeteo5', 'YMeteo5',
                'XMeteo6', 'YMeteo6', 'XMeteo7', 'YMeteo7', 'XMeteo8', 'YMeteo8', 'XMeteo9', 'YMeteo9', 'XMeteo10', 'YMeteo10', 'XYScaleFac',
                'NoMeteoSta', 'MeteoClcSW', 'XSeg', 'YSeg', 'RadSW', 'WinDir', 'RelHumAir', 'TempAir', 'PAtm', 'Cloud', 'XDOCCd', 'KdCdIM1',
                'KdCdIM2', 'DOCS1', 'KdCdIM1S1', 'KdCdIM2S1', 'XDOCCr', 'KdCrIM1', 'KdCrIM2', 'KdCrIM1S1', 'KdCrIM2S1', 'XDOCCu', 'KdCuIM1',
                'KdCuIM2', 'XDOCPb', 'KdPbIM1', 'KdPbIM2', 'KdPbIM1S1', 'KdPbIM2S1', 'XDOCZn', 'KdZnIM1', 'KdZnIM2']
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        elif key == 'coliform':
            # Get data for B1_sublist variable
            b1_path = os.path.normpath(os.path.join(sample_path, 'B1_coliform.inc'))
            with open(b1_path, 'r', encoding=functions.encoding_detect(b1_path)) as f:
                params_INC['B1_sublist'] = f.read()
            # Prepare for the config file B7_processes
            params_INC['B7_processes'], processes = [], ['EColiMrt', 'Salinchlor', 'CalcRadDay', 'DynDepth']
            for item in processes:
                params_INC['B7_processes'].append(f"CONSTANTS 'ACTIVE_{item}' DATA 0")
            # Prepare for the config file B7_constants
            params_INC['B7_constants'], constants = [], ['RcMrtEColi', 'DayL', 'ExtVl', 'DayRadSurf']
            values = ['0.8', '0.58', '1', '-999']
            for i in range(len(constants)):
                params_INC['B7_constants'].append(f"CONSTANTS '{constants[i]}' DATA {values[i]}")
            # Prepare for the config file B7_segfunctions
            temp_tem = parameters['tem_path'].replace(os.sep, "/")
            params_INC['B7_segfunctions'] = [f"SEG_FUNCTIONS\n'Temp'\nALL\nBINARY_FILE '{temp_tem}'"]
            # Prepare for the config file B9
            pr = []
            temp = ["2 ; perform default output and extra parameters listed below", f"{len(pr)} ; number of parameters listed"]
            params_INC['B9_Hisvar'], params_INC['B9_Mapvar'] = temp.copy(), temp.copy()
            for item in pr:
                params_INC['B9_Hisvar'].append(f"'{item}' 'volume'")
                params_INC['B9_Mapvar'].append(f"'{item}'")
        # Write .inc files
        # ######################## Block 1 ########################
        # Write file B1_t0.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B1_t0.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(f"'T0: {params_INC['t0']}  (scu= {params_INC['t0_scu']:7d}s)'")
        # Write file B1_sublist.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B1_sublist.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B1_sublist'])
        
        # ######################## Block 2 ########################
        # Write file B2_numsettings.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B2_numsettings.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B2_numsettings'])
        # Write file B2_simtimers.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B2_simtimers.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B2_simtimers']))
        # Write file B2_outlocs.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B2_outlocs.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B2_outlocs'])
        # Write file B2_outputtimers.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B2_outputtimers.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B2_outputtimers']))
        
        # ######################## Block 3 ########################
        # Write file B3_ugrid.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B3_ugrid.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B3_ugrid'])
        # Write file B3_nrofseg.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B3_nrofseg.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B3_nrofseg'])
        # Write file B3_attributes.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B3_attributes.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B3_attributes'])
        # Write file B3_volumes.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B3_volumes.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B3_volumes'])

        # ######################## Block 4 ########################
        # Write file B4_nrofexch.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B4_nrofexch.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B4_nrofexch'])
        # Write file B4_pointers.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B4_pointers.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B4_pointers'])
        # Write file B4_cdispersion.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B4_cdispersion.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B4_cdispersion'])
        # Write file B4_area.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B4_area.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B4_area'])
        # Write file B4_flows.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B4_flows.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B4_flows'])
        # Write file B4_length.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B4_length.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B4_length'])

        # ######################## Block 5 ########################
        # Write file B5_boundlist.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B5_boundlist.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B5_boundlist']))
        # Write file B5_boundaliases.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B5_boundaliases.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B5_boundaliases'])
        # Write file B5_bounddata.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B5_bounddata.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B5_bounddata'])
        
        # ######################## Block 6 ########################
        # Write file B6_loads.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B6_loads.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B6_loads']))
        # Write file B6_loads_aliases.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B6_loads_aliases.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B6_loads_aliases']))
        # Write file B6_loads_data.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B6_loads_data.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B6_loads_data'])

        # ######################## Block 7 ########################
        # Write file B7_processes.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B7_processes.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B7_processes']))
        # Write file B7_constants.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B7_constants.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B7_constants']))
        # Write file B7_functions.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B7_functions.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B7_functions'])
        # Write file B7_parameters.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B7_parameters.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B7_parameters'])
        # Write file B7_dispersion.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B7_dispersion.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write(params_INC['B7_dispersion'])
        # Write file B7_vdiffusion.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B7_vdiffusion.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B7_vdiffusion']))
        # Write file B7_segfunctions.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B7_segfunctions.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B7_segfunctions']))
        # Write file B7_numerical_options.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B7_numerical_options.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B7_numerical_options']))

        # ######################## Block 8 ########################
        # Write file B8_initials.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B8_initials.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B8_initials']))

        # ######################## Block 9 ########################
        # Write file B9_Hisvar.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B9_Hisvar.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B9_Hisvar']))
        # Write file B9_Mapvar.inc
        temp_path = os.path.normpath(os.path.join(includes_folder, 'B9_Mapvar.inc'))
        with open(temp_path, 'w', encoding=functions.encoding_detect(temp_path)) as f:
            f.write('\n'.join(params_INC['B9_Mapvar']))

        # Write file .inp
        time_step = int(86400/params_INC['t0_scu'])
        params_INP['time_step'] = time_step
        # Open the file and read its contents
        inp_temp = os.path.normpath(os.path.join(sample_path, 'INPFile.inp'))
        with open(inp_temp, 'r', encoding=functions.encoding_detect(inp_temp)) as file:
            content_inp = file.read()
        # Write file to store model type
        model_path = os.path.normpath(os.path.join(output_folder, f'{parameters["folder_name"]}.json'))
        with open(model_path, 'w', encoding=functions.encoding_detect(model_path)) as f:
            json.dump(model_type, f, indent=4)
        # Replace placeholders with actual values
        for key, value in params_INP.items():
            content_inp = content_inp.replace(f'{{{key}}}', str(value))
        inp_path = os.path.normpath(os.path.join(output_folder, f'{parameters["folder_name"]}.inp'))
        with open(inp_path, 'w', encoding=functions.encoding_detect(inp_path)) as f:
            f.write(content_inp)
        return inp_path, time_msg
    except Exception as e: return None, str(e)