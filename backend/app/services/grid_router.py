import traceback, os, json, tempfile
from fastapi import UploadFile, File, APIRouter, Request, Depends, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from services import functions, grid_functions
from config import PROJECT_ROOT
from shapely.geometry import Point, Polygon
import numpy as np, geopandas as gpd
import pandas as pd, xarray as xr, dask.array as da
from meshkernel import MeshKernel, GeometryList

router, processes = APIRouter(), {}

@router.post("/polygon_generator")
async def polygon_generator(request: Request):
    try:
        body = await request.json()
        points = body.get('points', None)
        vertices = [{"id": i, "geometry": Point((coord[1], coord[0]))} for i, coord in enumerate(points)]
        point = gpd.GeoDataFrame(vertices, geometry="geometry", crs="EPSG:4326")
        coords = [(lon, lat) for lat, lon in points]
        data = { "Name": ["Unknown"], "Region": ["Unknown"], "max": ["Unknown"],
            "min": ["Unknown"], "avg": ["Unknown"]}
        gdf = gpd.GeoDataFrame(data=data, geometry=[Polygon(coords)], crs="EPSG:4326")
        temp_gdf = gdf.to_crs(gdf.estimate_utm_crs())
        gdf['perimeter'] = round(temp_gdf['geometry'].iloc[0].length, 2)
        gdf['area'] = round(temp_gdf['geometry'].iloc[0].area, 2)
        result = {"polygon": json.loads(gdf.to_json()), "point": json.loads(point.to_json())}
        return JSONResponse({'status': 'ok', 'content': result})
    except Exception as e: functions.error('/polygon_generator', e)

@router.post("/init_lakes")
async def init_lakes(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        lakes_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "lakes"))
        os.makedirs(lakes_dir, exist_ok=True)
        project_cache = request.app.state.project_cache.setdefault(project_name,{})
        grid_functions.lake_generation(lakes_dir, project_cache)
        lake_json = os.path.normpath(os.path.join(lakes_dir, 'lakes.json'))
        with open(lake_json, 'r') as f:
            result = json.load(f)
        return JSONResponse({'content': result, 'status': 'ok'})
    except Exception as e: functions.error('/init_lakes', e)
    
@router.post("/load_lakes")
async def load_lakes(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        lake = body.get('lakeName')
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        lakes_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "lakes"))
        os.makedirs(lakes_dir, exist_ok=True)
        project_cache = request.app.state.project_cache.setdefault(project_name, {})
        grid_functions.lake_generation(lakes_dir, project_cache)
        lake_db, depth_db = project_cache['lake_db'], project_cache['depth_db']
        if lake != 'all':
            lake_data = lake_db[lake_db['name'] == lake].copy()
            if lake_data.empty: return JSONResponse({'status': 'error', 'message': 'Lake not found.'})
            lake_id = lake_data['id'].iloc[0]
            depth_data = depth_db.loc[lake_id].copy()
            lake_data['min'] = round(depth_data['depth'].min(), 2)
            lake_data['max'] = round(depth_data['depth'].max(), 2)
            lake_data['avg'] = round(depth_data['depth'].mean(), 2)
        else: lake_data, depth_data = lake_db.copy(), None
        lake_data["geometry"] = lake_data.geometry.apply(lambda geo: grid_functions.remove_holes(geo, None))
        temp = lake_data.copy().to_crs(lake_data.estimate_utm_crs())
        lake_data['perimeter'] = temp.geometry.apply(
            lambda g: round(g.exterior.length if isinstance(g, Polygon)
                else sum(p.exterior.length for p in g.geoms), 2))
        project_cache['lake'], project_cache['depth'] = lake_data, depth_data
        contents = {'lake': json.loads(lake_data.to_json()), 
            'depth': json.loads(depth_data.to_json()) if depth_data is not None else None}
        return JSONResponse({'content': contents})
    except Exception as e: functions.error('/load_lakes', e)

@router.post("/search_lake")
async def search_lake(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    lake_dir = os.path.join(PROJECT_ROOT, project_name, "lakes")
    os.makedirs(lake_dir, exist_ok=True)
    lake_name_path = os.path.normpath(os.path.join(lake_dir, 'lakes_name.json'))
    project_cache = request.app.state.project_cache.setdefault(project_name,{})
    grid_functions.lake_generation(lake_dir, project_cache)
    with open(lake_name_path, 'r') as f:
        data = json.load(f)
    name = body.get('name')
    result = data if name == '' else [x for x in data if name.lower() in x.lower()]
    return JSONResponse({'content': result})

@router.post("/export_depth")
async def export_depth(request: Request):
    try:
        body = await request.json()
        data = body.get('depthData')
        depth = gpd.GeoDataFrame.from_features(
            data['features'] if data['type'] == 'FeatureCollection' else [data], crs="EPSG:4326"
        )
        depth['geometry'] = depth['geometry'].centroid
        depth['Latitude'], depth['Longitude'], depth['Depth'] = depth.geometry.y, depth.geometry.x, depth['value']
        depth = depth[['Latitude', 'Longitude', 'Depth', 'geometry']]
        return JSONResponse({'status': 'ok', 'content': json.loads(depth.to_json())})
    except Exception as e: functions.error('/export_depth', e)

@router.post("/vertex_generator")
async def vertex_generator(request: Request):
    try:
        body = await request.json()
        polygon = gpd.GeoDataFrame.from_features(body.get('polygon')["features"], crs="EPSG:4326")
        coords, polygon = [], polygon["geometry"].iloc[0]
        if polygon.geom_type == "Polygon": coords = list(polygon.exterior.coords)
        elif polygon.geom_type == "MultiPolygon":
            for poly in polygon.geoms:
                coords.extend(list(poly.exterior.coords))
        vertices = [{"id": i, "coord": Point((coord[0], coord[1]))} for i, coord in enumerate(coords)]
        point = gpd.GeoDataFrame(vertices, geometry="coord", crs="EPSG:4326")
        return JSONResponse({'status': 'ok', 'content': json.loads(point.to_json())})
    except Exception as e: functions.error('/vertex_generator', e)

@router.post("/vertex_refiner")
async def vertex_refiner(request: Request):
    try:
        body = await request.json()
        start_point, end_point = int(body.get('startPoint')), int(body.get('endPoint'))
        polygon, distance = body.get('polygon', None), body.get('distance')
        if not polygon or len(polygon) < 3:
            return JSONResponse({"status": "error", "message": "Invalid polygon"})
        poly = Polygon([(p[1], p[0]) for p in polygon])
        polygon_wgs84 = gpd.GeoDataFrame(geometry=[poly], crs="EPSG:4326")        
        crs = polygon_wgs84.estimate_utm_crs()
        polygon_xy = polygon_wgs84.to_crs(crs)        
        boundary = polygon_xy["geometry"].iloc[0].exterior        
        boundary_coords = list(boundary.coords)
        if start_point == end_point:
            return JSONResponse({"status": "error", "message": "Start and end point must be different"})
        start_dis = boundary.project(Point(boundary_coords[start_point]))
        end_dis = boundary.project(Point(boundary_coords[end_point]))
        total_length = boundary.length
        if end_dis < start_dis:
            distances = np.concatenate([
                np.arange(start_dis, total_length, distance),
                np.arange(0, end_dis, distance)
            ])
        else: distances = np.arange(start_dis, end_dis, distance)
        if len(distances) == 0:
            return JSONResponse({"status": "error", "message": "Distance too large for selected segment."})
        # Interpolate points
        points = [boundary.interpolate(d) for d in distances]
        new_point_xy = [Point((p.x, p.y)) for p in points]
        temp = gpd.GeoDataFrame(geometry=new_point_xy, crs=crs).to_crs("EPSG:4326")
        new_point_wgs84 = [[p.y, p.x] for p in temp['geometry'].values]
        # Insert the new point at the specified index
        if start_point < end_point: polygon_new = (polygon[:start_point] + new_point_wgs84 + polygon[end_point:])
        else: polygon_new = (new_point_wgs84 + polygon[end_point:start_point])
        if polygon_new[0] != polygon_new[-1]: polygon_new.append(polygon_new[0])
        vertices = [{"id": i, "geometry": Point((coord[1], coord[0]))} for i, coord in enumerate(polygon_new)]
        point = gpd.GeoDataFrame(vertices, geometry="geometry", crs="EPSG:4326")
        poly_new = Polygon([(p.x, p.y) for p in point['geometry'].values])
        lake_db = gpd.GeoDataFrame(geometry=[poly_new], crs="EPSG:4326")
        return JSONResponse({'content': {
            "polygon": json.loads(lake_db.to_json()), "point": json.loads(point.to_json())
        }})
    except Exception as e: functions.error('/vertex_refiner', e)

@router.post("/vertex_mover")
async def vertex_mover(request: Request):
    try:
        body = await request.json()
        points = np.array(body.get('pointCollection'))
        vertices = [{"id": i, "geometry": Point((coord[1], coord[0]))} for i, coord in enumerate(points)]
        point = gpd.GeoDataFrame(vertices, geometry="geometry", crs="EPSG:4326")
        poly = Polygon([(p.x, p.y) for p in point['geometry'].values])
        gdf = gpd.GeoDataFrame(geometry=[poly], crs="EPSG:4326")
        contents = {"polygon": json.loads(gdf.to_json()), "point": json.loads(point.to_json())}
        return JSONResponse({'status': 'ok', 'content': contents})
    except Exception as e: functions.error('/vertex_mover', e)

@router.post("/vertex_remover")
async def vertex_remover(request: Request):
    try:
        body = await request.json()
        start_point, end_point = int(body.get('startPoint')), int(body.get('endPoint'))
        polygon = body.get('polygon', None)
        if not polygon or len(polygon) < 3:
            return JSONResponse({"status": "error", "message": "Invalid polygon."})
        n = len(polygon)
        if not (0 <= start_point < n and 0 <= end_point < n):
            return JSONResponse({"status": "error", "message": "Invalid start or end point index."})
        if start_point <= end_point:
            polygon_new = polygon[:start_point + 1] + polygon[end_point:]
        else: polygon_new = polygon[:end_point + 1] + polygon[start_point:]
        if len(polygon_new) < 3:
            return JSONResponse({"status": "error", "message": "Polygon must have at least 3 vertices."})
        vertices = [{"id": i, "geometry": Point((coord[1], coord[0]))} for i, coord in enumerate(polygon_new)]
        point = gpd.GeoDataFrame(vertices, geometry="geometry", crs="EPSG:4326")
        poly_new = Polygon([(p.x, p.y) for p in point['geometry'].values])
        gdf = gpd.GeoDataFrame(geometry=[poly_new], crs="EPSG:4326")
        contents = {"polygon": json.loads(gdf.to_json()), "point": json.loads(point.to_json())}
        return JSONResponse({'status': 'ok', 'content': contents})
    except Exception as e: functions.error('/vertex_remover', e)

@router.post("/grid_creator")
async def grid_creator(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        project_cache = request.app.state.project_cache.setdefault(project_name, {})
        points, level = np.array(body.get('pointCollection')), body.get('levelValue')
        gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy(points[:, 1], points[:, 0]), crs="EPSG:4326")
        x, y = np.array(gdf.geometry.x), np.array(gdf.geometry.y)
        if x[0] != x[-1] or y[0] != y[-1]:
            x, y = np.append(x, x[0]), np.append(y, y[0])
        mk, polygon = MeshKernel(), GeometryList(x, y)
        if level == '': mk.mesh2d_make_triangular_mesh_from_polygon(polygon)
        else: mk.mesh2d_make_triangular_mesh_from_polygon(polygon, scale_factor=float(level))
        grid_uds = grid_functions.netCDF_creator(mk)
        project_cache['grid_uds'], project_cache['mk'] = grid_uds, mk
        project_cache["mk_crs"] = "EPSG:4326"
        grid = functions.unstructuredGridCreator(grid_uds)
        points = functions.nodes_from_grid(grid)
        points['Lat'], points['Lon'] = points.geometry.y, points.geometry.x
        content = {'point': json.loads(points.to_json()), 'polygon': json.loads(grid.to_json())}
        return JSONResponse({'content': content})
    except Exception as e: functions.error('/grid_creator', e)

@router.post("/grid_ortho")
async def grid_ortho(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        project_cache = request.app.state.project_cache.setdefault(project_name)
        if not project_cache:
            return JSONResponse({"status": "error", "message": "Project is not available in memory."}) 
        mk = project_cache.get('mk', None)
        if mk is None: 
            return JSONResponse({"status": "error", "message": "Unstructured grid is not available in memory."})
        mk_crs = project_cache.get('mk_crs', "EPSG:4326")
        mesh = mk.mesh2d_get()
        gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy(mesh.edge_x, mesh.edge_y), crs=mk_crs)
        gdf['orth'] = np.round(mk.mesh2d_get_orthogonality().values, 4)
        gdf = gdf[gdf.orth != -999]
        if mk_crs != "EPSG:4326": gdf = gdf.to_crs("EPSG:4326")
        values = gdf['orth'].to_numpy(dtype=float)
        if len(values) == 0:
            return JSONResponse({"status": "error", "message": "No valid orthogonality values found."})
        min, max = np.min(values), np.max(values)
        return JSONResponse({'status': 'ok', 'content': {"min": min, "max": max, "data": json.loads(gdf.to_json())}})
    except Exception as e: functions.error('/grid_ortho', e)

@router.post("/check_grid_optimization")
async def check_grid_optimization(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    process_key = f"{project_name}:grid_optimization"
    info = processes.get(process_key)
    if not info: 
        return JSONResponse({"status": "not_started", "progress": 0, "message": 'No optimization running.', "his": []})
    if info["status"] in ("finished", "stopped"):
        response = {"status": "finished", "progress": 100, "message": info["message"], "his": info["history"], "grid": info["grid"]}
        processes.pop(process_key, None)
        return JSONResponse(response)
    if info["status"] == "failed":
        response = {"status": "failed", "progress": info["progress"], "message": info["message"], "his": []}
        processes.pop(process_key, None)
        return JSONResponse(response)
    return JSONResponse({"status": info["status"], "progress": info["progress"], "message": info["message"], "his": info["history"]})

# Start a optimization
@router.post("/start_grid_optimization")
async def start_grid_optimization(request: Request, background_tasks: BackgroundTasks, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        redis, process_key = request.app.state.redis, f"{project_name}:grid_optimization"
        cache_root = request.app.state.project_cache
        if not isinstance(cache_root, dict):
            cache_root = {}
            request.app.state.project_cache = cache_root
        project_cache = cache_root.get(project_name)
        if not isinstance(project_cache, dict):
            project_cache = {}
            cache_root[project_name] = project_cache
        lock = redis.lock(process_key, timeout=1000, blocking_timeout=10)
        async with lock:
            info = processes.get(process_key)
            # Check if optimization already running
            if info and info["status"] == "running":
                return JSONResponse({
                    "status": info["status"], "progress": info["progress"], 
                    "message": info["message"], "his": info["history"]
                })
            processes[process_key] = {
                "status": "running", "progress": 0.0, "history": [], "grid": None,
                "message": 'Preparing data for optimization ...', "stop": False
            }
            info = processes[process_key]
            iterations, points = int(body.get('iterations')), np.array(body.get('pointCollection'))
            level_from, level_to = float(body.get('levelFrom')), float(body.get('levelTo'))
            gdf = gpd.GeoDataFrame(geometry=gpd.points_from_xy(points[:, 1], points[:, 0]), crs="EPSG:4326")
            utm_crs = gdf.estimate_utm_crs()
            utm_crs_str = utm_crs.to_string() if utm_crs is not None else "EPSG:4326"
            gdf_proj = gdf.to_crs(utm_crs)
            x = np.array(gdf_proj.geometry.x.to_numpy(), dtype=np.float64, copy=True)
            y = np.array(gdf_proj.geometry.y.to_numpy(), dtype=np.float64, copy=True)
            polygon = GeometryList(x, y)
            params = {
                "level": [level_from, level_to], 'mode': ['auto', 'custom'],
                "outer_iterations": [1, 10], "boundary_iterations": [1, 50],
                "inner_iterations": [1, 50], "smoothing_factor": [0, 1]
            }
            background_tasks.add_task(
                grid_functions.run_grid_optimization, processes, process_key, iterations, 
                polygon, params, project_cache, utm_crs_str
            )
        return JSONResponse({"status": "ok", "message": f"Grid optimization started."})
    except Exception as e: functions.error('/start_grid_optimization', e)

@router.post("/stop_grid_optimization")
async def stop_grid_optimization(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        process_key = f"{project_name}:grid_optimization"
        info = processes.get(process_key)
        if not info:
            return JSONResponse({"status": "error", "message": "No optimization running."})
        if info["status"] == "running":
            info["stop"], message = True, "Optimization stopped by user. The grid will be created with the current best parameters."
            return JSONResponse({"status": "error", "message": message})
        return JSONResponse({"status": "ok"})
    except Exception as e: functions.error('/stop_grid_optimization', e)

@router.post("/grid_checker")
async def grid_checker(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project_name, _ = functions.project_definer(body.get('projectName'), user)
    grid_dir = os.path.join(PROJECT_ROOT, project_name, "grids")
    if not os.path.exists(grid_dir): os.makedirs(grid_dir)
    grid_path = os.path.normpath(os.path.join(grid_dir, body.get('gridName')))
    if not os.path.exists(grid_path): return JSONResponse({'status': 'ok'})
    else: return JSONResponse({'status': 'error'})

@router.post("/grid_interpolation_upload")
async def grid_interpolation_upload(file: UploadFile = File(...)):
    try:
        df = pd.read_csv(file.file, low_memory=False)
        required_columns = ['Lat', 'Lon', 'Depth']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            return JSONResponse({'status': 'error',
                'message': f"Missing required columns: {', '.join(missing_columns)}"}, status_code=400)
        content = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(x=df['Lon'], y=df['Lat']), crs="EPSG:4326")
        return JSONResponse({'status': 'ok', 'content': json.loads(content.to_json())})
    except Exception as e: functions.error('/grid_interpolation_upload', e)
    finally: await file.close()

@router.post("/grid_interpolation")
async def grid_interpolation(request: Request):
    try:
        body = await request.json()
        data_point, data_grid = np.array(body.get('pointCollection')), body.get('grid')
        grid = gpd.GeoDataFrame.from_features(
            data_grid['features'] if data_grid['type'] == 'FeatureCollection' else [data_grid], crs="EPSG:4326"
        )
        points = pd.DataFrame(data_point, columns=['lat', 'lon', 'depth'])
        # Interpolation
        x_array = points['lon'].to_numpy(dtype=float)
        y_array = points['lat'].to_numpy(dtype=float)
        z_array = points['depth'].to_numpy(dtype=float)
        nodes = functions.nodes_from_grid(grid)
        nodes['Depth'] = functions.interpolation_Z(nodes, x_array, y_array, z_array, geo_type='point')
        return JSONResponse({'status': 'ok', 'content': json.loads(nodes.to_json())})
    except Exception as e: functions.error('/grid_interpolation', e)

@router.post("/grid_saver")
async def grid_saver(request: Request, user=Depends(functions.basic_auth)):
    temp_path = None
    try:
        body = await request.json()
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        point_data = body.get('gridPoints')
        points = gpd.GeoDataFrame.from_features(
            point_data['features'] if point_data['type'] == 'FeatureCollection' else [point_data], crs="EPSG:4326"
        )
        points['lat'], points['lon'] = points.geometry.y, points.geometry.x
        project_cache = request.app.state.project_cache.setdefault(project_name)
        if not project_cache: 
            return JSONResponse({"status": "error", "message": "Project is not available in memory."}) 
        grid_uds = project_cache.get('grid_uds')
        if grid_uds is None:
            return JSONResponse({"status": "error", "message": "Grid is not available."})
        grid = functions.unstructuredGridCreator(grid_uds)
        nodes = functions.nodes_from_grid(grid)
        x_array = points['lon'].to_numpy(dtype=float)
        y_array = points['lat'].to_numpy(dtype=float)
        z_array = points['depth'].to_numpy(dtype=float)
        depth = functions.interpolation_Z(nodes, x_array, y_array, z_array, geo_type='point')
        grid_uds['mesh2d_node_z'] = (("mesh2d_nNodes",), da.from_array(depth.astype(np.float32)))
        with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as temp_file:
            temp_path = temp_file.name
        grid_uds.to_netcdf(temp_path, engine='netcdf4')
        def file_iterator():
            try:
                with open(temp_path, 'rb') as f:
                    while chunk := f.read(1024 * 1024):
                        yield chunk
            finally:
                if os.path.exists(temp_path): os.remove(temp_path)
        return StreamingResponse(file_iterator(), media_type="application/x-netcdf")
    except Exception as e:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        functions.error('/grid_saver', e)

@router.post("/grid_rechecker")
async def grid_rechecker(file: UploadFile = File(...)):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as temp_file:
            temp_path = temp_file.name
            while chunk := await file.read(1024 * 1024):
                temp_file.write(chunk)
        with xr.open_dataset(temp_path, engine='netcdf4') as grid_uds:
            grid = functions.unstructuredGridCreator(grid_uds)
            nodes = functions.nodes_from_grid(grid)
            nodes['Depth'] = 0
            if 'mesh2d_node_z' in grid_uds: nodes['Depth'] = grid_uds['mesh2d_node_z'].values
            if 'NetNode_z' in grid_uds: nodes['Depth'] = grid_uds['NetNode_z'].values
        content = {'grid': json.loads(grid.to_json()), 'nodes': json.loads(nodes.to_json())}
        return JSONResponse({'status': 'ok', 'content': content})
    except Exception as e: functions.error('/grid_rechecker', e)
    finally: await file.close()