import os, pickle, warnings, optuna, json
from config import SOURCE_BACKEND
import geopandas as gpd, numpy as np
import xarray as xr, dask.array as da
from shapely.geometry import Polygon, MultiPolygon
from meshkernel import MeshKernel, GeometryList, OrthogonalizationParameters, ProjectToLandBoundaryOption
from services import functions
from pyproj import CRS
from optuna.pruners import MedianPruner
from functools import partial
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.ERROR)

def lake_generation(folder, project_cache):
    lake_pkl_path = os.path.normpath(os.path.join(folder, 'lakes.pkl'))
    depth_pkl_path = os.path.normpath(os.path.join(folder, 'depth.pkl'))
    lake_json_path = os.path.normpath(os.path.join(folder, 'lakes.json'))
    lake_name_path = os.path.normpath(os.path.join(folder, 'lakes_name.json'))
    if not os.path.exists(lake_pkl_path): 
        print("Lake data is not available. Creating a new one...")
        initLakes(lake_path=lake_pkl_path)
    if not os.path.exists(depth_pkl_path):
        print("Depth data is not available. Creating a new one...")
        initLakes(depth_path=depth_pkl_path)
    if 'lake_db' not in project_cache:
        with open(lake_pkl_path, 'rb') as f: lake_db = pickle.load(f)
        project_cache['lake_db'] = lake_db
    else: lake_db = project_cache['lake_db']
    if 'depth_db' not in project_cache:
        with open(depth_pkl_path, 'rb') as f: depth_db = pickle.load(f)
        project_cache['depth_db'] = depth_db
    else: depth_db = project_cache['depth_db']
    if not os.path.exists(lake_json_path):
        encoding = functions.encoding_detect(lake_json_path)
        result = lake_db.groupby("region")["name"].apply(list).to_dict()
        # Save the processed lake data
        with open(lake_json_path, 'w', encoding=encoding) as f:
            json.dump(result, f)
    if not os.path.exists(lake_name_path):
        encoding = functions.encoding_detect(lake_name_path)
        data = np.unique(lake_db['name'].values).tolist()
        with open(lake_name_path, 'w', encoding=encoding) as f:
            json.dump(data, f)

def initLakes(lake_path=None, depth_path=None):
    # Load lake database
    lake_dir = os.path.join(SOURCE_BACKEND, 'lakes_database')
    if lake_path is not None:
        lake_db_path, lake_db = os.path.normpath(os.path.join(lake_dir, 'lakes.shp')), None
        if os.path.exists(lake_db_path):
            lake_db = gpd.read_file(lake_db_path)
            if lake_db.crs != 'EPSG:4326': lake_db = lake_db.to_crs(crs='EPSG:4326')
            lake_db = lake_db.dropna(subset=['name', 'region', 'geometry'])
            lake_db['name'] = lake_db['name'].fillna('Unnamed Lake')
            lake_db['region'] = lake_db['region'].where(
                lake_db['region'].notna(),
                'Unknown Municipality' + lake_db["id"].fillna(-1).astype(int).map(str)
            )
            lake_db['id'] = lake_db['id'].astype('int64')
        with open(lake_path, 'wb') as f: pickle.dump(lake_db, f)
    if depth_path is not None:
        depth_db_path = os.path.normpath(os.path.join(lake_dir, 'depth.shp'))
        depth_db = gpd.read_file(depth_db_path)
        depth_db['id'] = depth_db['id'].astype('int64')
        depth_db.set_index('id', inplace=True)
        if depth_db.crs != 'EPSG:4326': depth_db = depth_db.to_crs(crs='EPSG:4326')
        depth_db['depth'] = depth_db['depth'].astype(float)
        with open(depth_path, 'wb') as f: pickle.dump(depth_db, f)

def remove_holes(geom, cell_size: float | None = 0):
    geom = geom.buffer(0)
    cell_size_f: float = geom.area if cell_size is None else float(cell_size)
    if geom.geom_type == "Polygon":
        kept_interiors = [ring for ring in geom.interiors if Polygon(ring).area >= cell_size_f]
        return Polygon(geom.exterior, kept_interiors)
    elif geom.geom_type == "MultiPolygon":
        polygons = []
        for poly in geom.geoms:
            kept_interiors = [ring for ring in poly.interiors if Polygon(ring).area >= cell_size_f]
            polygons.append(Polygon(poly.exterior, kept_interiors))
        return MultiPolygon(polygons)
    else: return geom

def sort_face_ccw(nodes, x, y):
    xs, ys = x[nodes], y[nodes]
    cx, cy = xs.mean(), ys.mean()
    angles = np.arctan2(ys - cy, xs - cx)
    return nodes[np.argsort(angles)]

def meshkernel_to_Ugrid(mk: MeshKernel, crs: str):
    mesh = mk.mesh2d_get()
    node_x = np.asarray(mesh.node_x, dtype=np.float32)
    node_y = np.asarray(mesh.node_y, dtype=np.float32)
    edge_x = np.asarray(mesh.edge_x, dtype=np.float32)
    edge_y = np.asarray(mesh.edge_y, dtype=np.float32)
    edge_nodes = mesh.edge_nodes.reshape((-1, 2)).astype(np.int32)  # 0-based
    face_nodes_flat = mesh.face_nodes.astype(np.int32)
    nodes_per_face = mesh.nodes_per_face.astype(np.int32)
    face_x = np.asarray(mesh.face_x, dtype=np.float32)
    face_y = np.asarray(mesh.face_y, dtype=np.float32)
    nEdges, nFaces = edge_x.size, face_x.size
    max_n = int(nodes_per_face.max()) if nFaces > 0 else 0
    face_nodes = np.full((nFaces, max_n), np.nan, dtype=np.float32)
    if nFaces > 0:
        offset = np.zeros(nFaces, dtype=int)
        offset[1:] = np.cumsum(nodes_per_face[:-1])
        for i, n in enumerate(nodes_per_face):
            if n < 3: continue
            nodes = face_nodes_flat[offset[i]:offset[i] + n]
            # remove duplicates, keep order
            _, idx = np.unique(nodes, return_index=True)
            nodes = nodes[np.sort(idx)]
            if nodes.size < 3: continue
            nodes = sort_face_ccw(nodes, node_x, node_y)
            face_nodes[i, :nodes.size] = nodes + 1
    edge_faces = np.full((nEdges, 2), -1, dtype=np.int32)
    if nEdges > 0 and nFaces > 0:
        edge_dict = {tuple(sorted(edge_nodes[i])): i for i in range(nEdges)}
        offset = np.zeros(nFaces, dtype=int)
        offset[1:] = np.cumsum(nodes_per_face[:-1])
        for fidx in range(nFaces):
            n = nodes_per_face[fidx]
            nodes = face_nodes_flat[offset[fidx]:offset[fidx] + n]
            for i in range(n):
                n1, n2 = nodes[i], nodes[(i + 1) % n]
                key = tuple(sorted((n1, n2)))
                eidx = edge_dict.get(key)
                if eidx is None: continue
                if edge_faces[eidx, 0] == -1: edge_faces[eidx, 0] = fidx
                elif edge_faces[eidx, 1] == -1: edge_faces[eidx, 1] = fidx
    ds = xr.Dataset()
    crs_obj = CRS.from_user_input(crs)
    if crs_obj.is_geographic: grid_mapping_name = 'latitude_longitude'
    else: grid_mapping_name = 'transverse_mercator'
    ds["crs"] = xr.DataArray(0,
        attrs={ "grid_mapping_name": grid_mapping_name, "crs_wkt": crs_obj.to_wkt() }
    )
    ds["mesh2d_edge_x"] = (("mesh2d_nEdges",), da.from_array(edge_x))
    ds["mesh2d_edge_y"] = (("mesh2d_nEdges",), da.from_array(edge_y))
    ds["mesh2d_edge_nodes"] = (("mesh2d_nEdges", "Two"), da.from_array(edge_nodes))
    ds["mesh2d_face_nodes"] = ( ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"), da.from_array(face_nodes))
    ds["mesh2d_edge_faces"] = (("mesh2d_nEdges", "Two"), da.from_array(edge_faces))    
    ds["mesh2d_face_x"] = (("mesh2d_nFaces",), da.from_array(face_x))
    ds["mesh2d_face_y"] = (("mesh2d_nFaces",), da.from_array(face_y))
    ds["mesh2d_node_x"] = (("mesh2d_nNodes",), da.from_array(node_x))
    ds["mesh2d_node_y"] = (("mesh2d_nNodes",), da.from_array(node_y))
    ds["mesh2d_node_x"].attrs["grid_mapping"] = "crs"
    ds["mesh2d_node_y"].attrs["grid_mapping"] = "crs"
    x_bnd = np.full_like(face_nodes, np.nan, dtype=np.float32)
    y_bnd = np.full_like(face_nodes, np.nan, dtype=np.float32)
    for i in range(nFaces):
        fn = face_nodes[i]
        valid = ~np.isnan(fn)
        if valid.sum() < 3: continue
        idx = fn[valid].astype(int) - 1
        x_bnd[i, valid] = node_x[idx]
        y_bnd[i, valid] = node_y[idx]
    ds["mesh2d_face_x_bnd"] = (("mesh2d_nFaces", "mesh2d_nMax_face_nodes"), da.from_array(x_bnd))
    ds["mesh2d_face_y_bnd"] = (("mesh2d_nFaces", "mesh2d_nMax_face_nodes"), da.from_array(y_bnd))
    # ---- UGRID topology variable ----
    ds["mesh2d"] = xr.DataArray(0,
        attrs={
            "cf_role": "mesh_topology", "topology_dimension": 2,
            "long_name": "Topology data of 2D mesh",
            "node_coordinates": "mesh2d_node_x mesh2d_node_y",
            "node dimensions": "mesh2d_nNodes", 
            "max_face_nodes_dimension": "mesh2d_nMax_face_nodes",
            "edge_node_connectivity": "mesh2d_edge_nodes",
            "edge_dimensions": "mesh2d_nEdges",
            "face_node_connectivity": "mesh2d_face_nodes",
            "edge_face_connectivity": "mesh2d_edge_faces",
            "face_dimension": "mesh2d_nFaces",
            "face_coordinates": "mesh2d_face_x mesh2d_face_y",
            "edge_coordinates": "mesh2d_edge_x mesh2d_edge_y",
        },
    )
    ds["mesh2d"].attrs["grid_mapping"] = "crs"
    ds = ds.set_coords(["mesh2d_node_x", "mesh2d_node_y"])
    return ds

def netCDF_creator(mk: MeshKernel, depth: gpd.GeoDataFrame | None = None):
    mesh = mk.mesh2d_get()
    node_x, node_y = mesh.node_x, mesh.node_y
    if depth is not None:
        if depth.crs is None: depth = depth.set_crs("EPSG:4326")
        crs_str = CRS.from_user_input(depth.crs).to_string()
        temp_grid = gpd.GeoDataFrame(geometry=gpd.points_from_xy(node_x, node_y), crs=depth.crs)
        node_z = functions.interpolation_Z(
            temp_grid, np.asarray(depth["geometry"].x), np.asarray(depth["geometry"].y),
            np.asarray(depth["depth"].values, dtype=float), n_neighbors=2, geo_type='point',
        )
    else:
        crs_str = "EPSG:4326"
        node_z = np.zeros(len(node_x))
    # Convert to Ugrid
    grid_uds = meshkernel_to_Ugrid(mk, crs_str)
    grid_uds['mesh2d_node_z'] = (("mesh2d_nNodes",), da.from_array(node_z.astype(np.float32)))    
    grid_uds.attrs.update({ "institution": 'Private', "references": 'vanlnNTNU@gmail.com'})
    return grid_uds

def objective_function(trial: optuna.trial.Trial, polygon, space, stop_checker=None):
    if stop_checker and stop_checker():
        trial.study.stop()
        raise optuna.TrialPruned("Stopped by user before execution.")
    try:
        type_choice = trial.suggest_categorical("mode", space['mode'])
        level = trial.suggest_float("level", space['level'][0], space['level'][1])
        outer_iterations = trial.suggest_int(
            "outer_iterations", space['outer_iterations'][0], 
            space['outer_iterations'][1]
        )
        boundary_iterations = trial.suggest_int(
            "boundary_iterations", space['boundary_iterations'][0], 
            space['boundary_iterations'][1]
        )
        inner_iterations = trial.suggest_int(
            "inner_iterations", space['inner_iterations'][0], 
            space['inner_iterations'][1]
        )
        smoothing_factor = trial.suggest_float(
            "smoothing_factor", space['smoothing_factor'][0], 
            space['smoothing_factor'][1]
        )
        # Remove outliers
        if type_choice == 'custom' and level <= 0:
            trial.set_user_attr("min", None); trial.set_user_attr("mean", None)
            return 1e6
        mk = MeshKernel()
        if type_choice == 'auto': mk.mesh2d_make_triangular_mesh_from_polygon(polygon)
        else: mk.mesh2d_make_triangular_mesh_from_polygon(polygon, scale_factor=float(level))        
        ortho_params = OrthogonalizationParameters(
            outer_iterations=outer_iterations, boundary_iterations=boundary_iterations,
            inner_iterations=inner_iterations,
            orthogonalization_to_smoothing_factor=smoothing_factor
        )        
        mk.mesh2d_compute_orthogonalization(
            project_to_land_boundary_option=ProjectToLandBoundaryOption.DO_NOT_PROJECT_TO_LANDBOUNDARY,
            orthogonalization_parameters=ortho_params, land_boundaries=polygon
        )
        orth = mk.mesh2d_get_orthogonality().values
        orth_valid = orth[orth != -999]
        if len(orth_valid) == 0: 
            trial.set_user_attr("min", None); trial.set_user_attr("mean", None)
            return 1e6
        min_value = float(np.min(orth_valid))
        mean_value = float(np.mean(orth_valid))
        max_value = float(np.max(orth_valid))
        trial.set_user_attr("min", min_value)
        trial.set_user_attr("mean", mean_value)
        if stop_checker and stop_checker(): trial.study.stop()
        if max_value <= 0.01: trial.study.stop()
        return max_value
    except optuna.TrialPruned: raise
    except BaseException: 
        trial.set_user_attr("min", None); trial.set_user_attr("mean", None)
        return 1e6

def optuna_callback(study: optuna.study.Study, trial: optuna.trial.FrozenTrial, info: dict, n: int):
    iteration = trial.number + 1
    progress = round(iteration / n * 100, 2)
    current_ortho = trial.value if trial.value is not None else float('inf')
    min_value = trial.user_attrs.get("min")
    mean_value = trial.user_attrs.get("mean")
    try:
        best_trial = study.best_trial
        best_ortho = best_trial.value
        best_type = best_trial.params.get("mode", "")
        best_level = best_trial.params.get("level", 0.0)
    except ValueError:
        best_ortho, best_type, best_level = current_ortho, "", 0.0
    message = f"Completed: {progress:.1f}% ({trial.number}/{n}) - Current orthogonality: {current_ortho:.4f}"
    message += f" [Best orthogonality: {best_ortho:.4f} (mode: '{best_type:^6}' - level: {best_level:.2f})]"
    info["progress"], info["message"] = progress, message
    info["history"].append({"iteration": iteration, "min": min_value, "mean": mean_value, "max": current_ortho})
    if info.get("stop"): study.stop()

def run_grid_optimization(processes: dict, key: str, iterations: int, 
    polygon:GeometryList, space: dict, project_cache: dict, utm_crs: str) -> None:
    info = processes[key]
    try:
        def stop_checker():
            current = processes.get(key)
            return current.get("stop", False) if current else True
        optuna_func = partial(
            objective_function, polygon=polygon, space=space, stop_checker=stop_checker
        )
        study = optuna.create_study(
            sampler=optuna.samplers.TPESampler(seed=42), direction="minimize", 
            pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=20),
        )
        callback = partial(optuna_callback, info=info, n=iterations)
        study.optimize(optuna_func, n_trials=iterations, callbacks=[callback])
        completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not completed:
            info["status"] = "stopped" if info.get("stop") else "failed"
            info["message"] = "Stopped before any valid trial completed. No grid generated."
            info["grid"] = None
            return
        best_params = study.best_params
        notice = info.get("message", "Optimization completed")
        if "[" in notice: notice = notice.split("[", 1)[1].split("(", 1)[0]
        info["status"], info["message"] = 'finalizing', "Generating final grid..."
        try:
            mk = mk_from_params(best_params, polygon)
        except Exception as grid_err:
            info["status"] = "stopped" if info.get("stop") else "failed"
            info["message"] = f"Optimization found best params but grid generation failed: {grid_err}"
            info["grid"], info["best_params"] = None, best_params
            return
        project_cache["mk"], project_cache["mk_crs"] = mk, utm_crs
        grid_uds = netCDF_creator(mk)
        best_grid = functions.unstructuredGridCreator(grid_uds)
        best_grid = best_grid.set_crs(utm_crs, allow_override=True).to_crs("EPSG:4326")
        info["grid"] = json.loads(best_grid.to_json())
        display_params = dict(best_params)
        display_params["level"] = f'{best_params["level"]:.3f}'
        display_params["mode"] = f'{best_params["mode"]:^6}'
        display_params["outer_iterations"] = f'{best_params["outer_iterations"]:.3f}'
        display_params["boundary_iterations"] = f'{best_params["boundary_iterations"]:.3f}'
        display_params["inner_iterations"] = f'{best_params["inner_iterations"]:.3f}'
        display_params["smoothing_factor"] = f'{best_params["smoothing_factor"]:.3f}'
        notice += f" - Best parameters: {display_params})"
        project_cache['grid_uds'] = grid_uds
        info["best_params"] = study.best_params
        info["status"] = "finished"
        info["message"] = f"Completed: {notice}"
    except Exception as e:
        if info:
            info["status"], info["message"] = "failed", f"Internal Error: {e}"

def mk_from_params(params, polygon):
    mk = MeshKernel()
    if params['mode'] == 'auto': mk.mesh2d_make_triangular_mesh_from_polygon(polygon)
    else: mk.mesh2d_make_triangular_mesh_from_polygon(polygon, scale_factor=float(params['level']))
    ortho_params = OrthogonalizationParameters(
        outer_iterations=params['outer_iterations'],
        boundary_iterations=params['boundary_iterations'],
        inner_iterations=params['inner_iterations'],
        orthogonalization_to_smoothing_factor=params['smoothing_factor']
    )
    mk.mesh2d_compute_orthogonalization(
        project_to_land_boundary_option=ProjectToLandBoundaryOption.DO_NOT_PROJECT_TO_LANDBOUNDARY,
        orthogonalization_parameters=ortho_params, land_boundaries=polygon
    )
    return mk