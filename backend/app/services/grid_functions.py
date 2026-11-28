import os, pickle, warnings, optuna, json, netCDF4, shutil
from pyproj import Transformer
from scipy.spatial import KDTree
from config import SOURCE_BACKEND
import geopandas as gpd, numpy as np
import xarray as xr, dask.array as da
from shapely.geometry import Polygon, MultiPolygon
from meshkernel import MeshKernel, GeometryList, \
    OrthogonalizationParameters, ProjectToLandBoundaryOption, Mesh2d
from services import functions, constants
from pyproj import CRS
from functools import partial
import multiprocessing as mp, queue, time

warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.ERROR)

class GridError(ValueError):
    """Grid error"""

SMALL_FLOW_EDGE_THRESHOLD = 0.15
DFM_SMALL_LINK_THRESHOLD = 0.1      # D-Flow FM default (Removesmalllinkstrsh)
_FILL_INT, _FILL_DBL = -999, -999.0

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

def clean_ring(x, y, tol=1e-10):
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if x.size != y.size or x.size < 3 or not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise GridError("Invalid polygon: need at least 3 vertices with valid coordinates.")
    keep = np.ones(x.size, dtype=bool)
    keep[1:] = (np.abs(np.diff(x)) > tol) | (np.abs(np.diff(y)) > tol)
    x, y = x[keep], y[keep]
    while x.size > 1 and abs(x[-1] - x[0]) <= tol and abs(y[-1] - y[0]) <= tol:
        x, y = x[:-1], y[:-1]
    if x.size < 3: raise GridError("Polygon must have at least 3 distinct vertices.")
    poly = Polygon(np.column_stack([x, y]))
    if poly.area <= 0: raise GridError("Polygon has zero area (all vertices lie on a line).")
    if not poly.is_valid:
        raise GridError("Polygon is self-intersecting. Move or delete the crossing vertexes and try again.")
    return np.append(x, x[0]), np.append(y, y[0])

def _mesh_worker(x, y, scale, q):
    try:
        mk = MeshKernel(); poly = GeometryList(x, y)
        if scale is None: mk.mesh2d_make_triangular_mesh_from_polygon(poly)
        else: mk.mesh2d_make_triangular_mesh_from_polygon(poly, scale_factor=float(scale))
        q.put(("ok", mk.mesh2d_get()))
    except BaseException as e:
        q.put(("error", str(e)))

def _friendly(msg: str) -> str:
    for key, text in constants.FRIENDLY.items():
        if key.lower() in msg.lower(): return f"{text}\n\n(MeshKernel: {msg})"
    return msg

def build_triangular_mesh(x, y, scale=None, timeout=120) -> MeshKernel:
    ctx = mp.get_context("spawn"); q = ctx.Queue()
    p = ctx.Process(target=_mesh_worker, args=(x, y, scale, q), daemon=True)
    p.start(); deadline = time.monotonic() + timeout; result = None
    try:
        while result is None:
            try: result = q.get(timeout=0.5)
            except queue.Empty:
                if not p.is_alive():
                    raise GridError(f"Mesh generation crashed (exit code {p.exitcode}).\nCheck the polygon or try a bigger value of 'Level'.")
                if time.monotonic() > deadline:
                    raise GridError(f"Mesh generation did not finish within {timeout} s. "
                        "Use a larger scale factor or fewer vertexes.")
    finally:
        if p.is_alive(): p.terminate()
        p.join(5)
    status, payload = result
    if status == "error": raise GridError(_friendly(payload))
    mk = MeshKernel(); mk.mesh2d_set(payload)
    if mk.mesh2d_get().face_x.size == 0:
        raise GridError("MeshKernel produced an empty grid. Use a smaller scale factor or check the polygon.")
    return mk

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
    grid_uds['mesh2d_node_z'] = (("mesh2d_nNodes",), da.from_array(np.asarray(node_z, dtype=np.float64)))
    grid_uds.attrs.update({ "institution": 'Private', "references": 'vanlnNTNU@gmail.com',
        "Conventions": "CF-1.8 UGRID-1.0 Deltares-0.10"})
    return grid_uds

def ugrid_to_wgs84(ds: xr.Dataset, src_crs: str) -> xr.Dataset:
    """UGRID dataset whose coordinates are in `src_crs` (e.g. the UTM grid of the optimization) -> lon/lat (EPSG:4326),
    so every saved grid uses the same coordinate system as the grids made by /grid_creator."""
    if CRS.from_user_input(src_crs).equals(CRS.from_epsg(4326)): return ds
    from pyproj import Transformer
    tr = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
    ds = ds.copy()
    for xv, yv in (("mesh2d_node_x", "mesh2d_node_y"), ("mesh2d_edge_x", "mesh2d_edge_y"),
        ("mesh2d_face_x", "mesh2d_face_y"), ("mesh2d_face_x_bnd", "mesh2d_face_y_bnd")):
        if xv not in ds or yv not in ds: continue
        lon, lat = tr.transform(np.asarray(ds[xv].values, dtype=np.float64), np.asarray(ds[yv].values, dtype=np.float64))
        ds[xv] = (ds[xv].dims, da.from_array(np.asarray(lon, dtype=np.float64)), ds[xv].attrs)
        ds[yv] = (ds[yv].dims, da.from_array(np.asarray(lat, dtype=np.float64)), ds[yv].attrs)
    ds["crs"].attrs.update({"grid_mapping_name": "latitude_longitude", "crs_wkt": CRS.from_epsg(4326).to_wkt()})
    return ds

def remove_small_flow_edges(mk: MeshKernel, threshold: float = SMALL_FLOW_EDGE_THRESHOLD) -> int:
    """Xoá các 'small flow link' mà D-Flow FM sẽ báo lỗi (khoảng cách 2 tâm ngoại tiếp quá nhỏ so với kích
    thước ô; D-Flow FM mặc định Removesmalllinkstrsh = 0.1). Lưới trong mk PHẢI ở hệ mét (UTM).
    threshold > 0.1 để có biên an toàn. Trả về số ô đã bị xoá."""
    n_before = mk.mesh2d_get().face_x.size
    if mk.mesh2d_get_small_flow_edge_centers(threshold).x_coordinates.size == 0: return 0
    mk.mesh2d_delete_small_flow_edges_and_small_triangles(
        small_flow_edges_length_threshold=threshold, min_fractional_area_triangles=0.0)
    if mk.mesh2d_get().face_x.size == 0:
        raise GridError("Removing small flow links left an empty grid.")
    return n_before - mk.mesh2d_get().face_x.size

def grid_creator_sync(points, level):
    scale = None if level in ('', None) else float(level)
    if scale is not None and not (np.isfinite(scale) and scale > 0):
        raise GridError("Scale factor must be a positive number.")
    lon, lat = clean_ring(points[:, 1], points[:, 0])
    # Tạo lưới trong hệ mét (UTM), giống chế độ optimization. Tạo trực tiếp trên lon/lat thì MeshKernel coi độ
    # là mét -> ở vĩ độ 62° tam giác bị kéo giãn ~2 lần theo phương x, nhiều góc tù -> small flow links.
    utm_crs = gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs="EPSG:4326").estimate_utm_crs()
    utm_crs_str = utm_crs.to_string()
    from pyproj import Transformer
    x, y = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True).transform(lon, lat)
    x, y = clean_ring(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
    mk = build_triangular_mesh(x, y, scale)
    remove_small_flow_edges(mk)
    grid_uds = ugrid_to_wgs84(netCDF_creator(mk), utm_crs_str)
    grid = functions.unstructuredGridCreator(grid_uds)
    nodes = functions.nodes_from_grid(grid)
    nodes['Lat'], nodes['Lon'] = nodes.geometry.y, nodes.geometry.x
    content = {'point': json.loads(nodes.to_json()), 'polygon': json.loads(grid.to_json())}
    return mk, grid_uds, content, utm_crs_str

def meshkernel_to_Ugrid(mk: MeshKernel, crs: str):
    mesh = mk.mesh2d_get()
    # float64: float32 chỉ có ~7 chữ số -> sai ~0.5 m với toạ độ UTM (northing ~6.9e6 m)
    node_x = np.asarray(mesh.node_x, dtype=np.float64)
    node_y = np.asarray(mesh.node_y, dtype=np.float64)
    edge_x = np.asarray(mesh.edge_x, dtype=np.float64)
    edge_y = np.asarray(mesh.edge_y, dtype=np.float64)
    edge_nodes = mesh.edge_nodes.reshape((-1, 2)).astype(np.int32)  # 0-based
    face_nodes_flat = mesh.face_nodes.astype(np.int32)
    nodes_per_face = mesh.nodes_per_face.astype(np.int32)
    face_x = np.asarray(mesh.face_x, dtype=np.float64)
    face_y = np.asarray(mesh.face_y, dtype=np.float64)
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
    x_bnd = np.full_like(face_nodes, np.nan, dtype=np.float64)
    y_bnd = np.full_like(face_nodes, np.nan, dtype=np.float64)
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
            "node_dimension": "mesh2d_nNodes",
            "max_face_nodes_dimension": "mesh2d_nMax_face_nodes",
            "edge_node_connectivity": "mesh2d_edge_nodes",
            "edge_dimension": "mesh2d_nEdges",
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

def _start_kernel(mode, level, polygon, base_meshes=None) -> MeshKernel:
    """Triangular mesh to orthogonalize. 'auto' always gives the same mesh -> reuse the one built once
    (base_meshes['auto']) instead of triangulating the polygon again in every trial."""
    mk = MeshKernel()
    base = (base_meshes or {}).get('auto') if mode == 'auto' else None
    if base is not None: mk.mesh2d_set(base)
    elif mode == 'auto': mk.mesh2d_make_triangular_mesh_from_polygon(polygon)
    else: mk.mesh2d_make_triangular_mesh_from_polygon(polygon, scale_factor=float(level))
    return mk

def objective_function(trial: optuna.trial.Trial, polygon, space, stop_checker=None, base_meshes=None):
    if stop_checker and stop_checker():
        trial.study.stop()
        raise optuna.TrialPruned("Stopped by user before execution.")
    try:
        type_choice = trial.suggest_categorical("mode", space['mode'])
        # 'level' (scale factor) is only used by custom mode -> only sampled there (log scale: range spans decades)
        level = (trial.suggest_float("level", space['level'][0], space['level'][1], log=True)
            if type_choice == 'custom' else None
        )
        outer_iterations = trial.suggest_int(
            "outer_iterations", space['outer_iterations'][0], space['outer_iterations'][1]
        )
        boundary_iterations = trial.suggest_int(
            "boundary_iterations", space['boundary_iterations'][0], space['boundary_iterations'][1]
        )
        inner_iterations = trial.suggest_int(
            "inner_iterations", space['inner_iterations'][0], space['inner_iterations'][1]
        )
        smoothing_factor = trial.suggest_float(
            "smoothing_factor", space['smoothing_factor'][0], space['smoothing_factor'][1]
        )
        mk = _start_kernel(type_choice, level, polygon, base_meshes)
        ortho_params = OrthogonalizationParameters(
            outer_iterations=outer_iterations, boundary_iterations=boundary_iterations,
            inner_iterations=inner_iterations, orthogonalization_to_smoothing_factor=smoothing_factor
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
        min_value = float(np.min(orth_valid)); mean_value = float(np.mean(orth_valid))
        max_value = float(np.max(orth_valid))
        trial.set_user_attr("min", min_value); trial.set_user_attr("mean", mean_value)
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
        best_trial = study.best_trial; best_ortho = best_trial.value
        best_type = best_trial.params.get("mode", "")
        best_level = best_trial.params.get("level")          # None when the best trial used auto mode
    except ValueError:
        best_ortho, best_type, best_level = current_ortho, "", None
    level_txt = f"{best_level:.2f}" if best_level is not None else "-"
    message = f"Completed: {progress:.1f}% ({iteration}/{n}) - Current orthogonality: {current_ortho:.4f}"
    message += f" [Best orthogonality: {best_ortho:.4f} (mode: '{best_type:^6}' - level: {level_txt})]"
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
        base_meshes = {}
        if 'auto' in space['mode']:
            # Auto mode: the triangular mesh does not depend on any parameter -> build it ONCE (in a separate
            # process, so a MeshKernel crash cannot take the server down); trials only tune the orthogonalization
            info["message"] = "Generating the base grid (auto mode) ..."
            try:
                base_meshes['auto'] = build_triangular_mesh(
                    np.asarray(polygon.x_coordinates, dtype=np.float64), np.asarray(polygon.y_coordinates, dtype=np.float64)
                ).mesh2d_get()
            except GridError as e:
                if space['mode'] == ['auto']:                 # auto only: nothing left to optimize
                    info["status"], info["message"], info["grid"] = "failed", f"Auto grid generation failed: {e}", None
                    return
                space = {**space, 'mode': [m for m in space['mode'] if m != 'auto']}   # continue with custom only
        optuna_func = partial(
            objective_function, polygon=polygon, space=space, stop_checker=stop_checker, base_meshes=base_meshes
        )
        study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=42), direction="minimize")
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
            mk = mk_from_params(best_params, polygon, base_meshes)
            remove_small_flow_edges(mk)          # mk đang ở UTM (mét)
        except Exception as grid_err:
            info["status"] = "stopped" if info.get("stop") else "failed"
            info["message"] = f"Optimization found best params but grid generation failed: {grid_err}"
            info["grid"], info["best_params"] = None, best_params
            return
        project_cache["mk"], project_cache["mk_crs"] = mk, utm_crs
        # The optimization works in metres (UTM); the grid that is shown / saved / interpolated is in lon/lat
        # (netCDF_creator labels it EPSG:4326, so it must really be lon/lat – before, it held UTM metres)
        grid_uds = ugrid_to_wgs84(netCDF_creator(mk), utm_crs)
        best_grid = functions.unstructuredGridCreator(grid_uds)
        info["grid"] = json.loads(best_grid.to_json())
        # 'level' exists only when the best trial used custom mode (auto has no level -> was KeyError: 'level')
        display_params = {k: (f"{v:.3f}" if isinstance(v, float) else v) for k, v in best_params.items()}
        notice += f" - Best parameters: {display_params})"
        project_cache['grid_uds'] = grid_uds; info["best_params"] = study.best_params
        info["status"] = "finished"; info["message"] = f"Completed: {notice}"
    except Exception as e:
        if info:
            info["status"], info["message"] = "failed", f"Internal Error: {e}"

def mk_from_params(params, polygon, base_meshes=None):
    mk = _start_kernel(params['mode'], params.get('level'), polygon, base_meshes)
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

def write_dflowfm_net(path, node_x, node_y, node_z, face_nodes, spherical=True, epsg=4326):
    import netCDF4
    node_x = np.asarray(node_x, dtype=np.float64)
    node_y = np.asarray(node_y, dtype=np.float64)
    node_z = np.asarray(node_z, dtype=np.float64)
    fn = np.asarray(face_nodes, dtype=np.float64)
    fn = np.where(np.isfinite(fn), fn, -1).astype(np.int64)
    nmax, nnode, faces = fn.shape[1], node_x.size, []
    for row in fn:
        ids = row[row >= 0]
        _, first = np.unique(ids, return_index=True); ids = ids[np.sort(first)]
        if ids.size < 3: continue
        xs, ys = node_x[ids], node_y[ids]
        if np.sum(xs * np.roll(ys, -1) - np.roll(xs, -1) * ys) < 0: ids = ids[::-1]
        faces.append(ids)
    if not faces: raise GridError("Grid has no valid cells to save.")
    nface = len(faces)
    edge_index, edge_nodes, edge_faces = {}, [], []
    for f, ids in enumerate(faces):
        for a, b in zip(ids, np.roll(ids, -1)):
            key = (int(min(a, b)), int(max(a, b)))
            e = edge_index.get(key)
            if e is None:
                edge_index[key] = len(edge_nodes); edge_nodes.append(key); edge_faces.append([f, -1])
            else: edge_faces[e][1] = f
    edge_nodes = np.asarray(edge_nodes, dtype=np.int32)
    edge_faces = np.asarray(edge_faces, dtype=np.int32)
    fn_out = np.full((nface, nmax), _FILL_INT, dtype=np.int32)
    xb = np.full((nface, nmax), _FILL_DBL); yb = np.full((nface, nmax), _FILL_DBL)
    fx, fy = np.empty(nface), np.empty(nface)
    for f, ids in enumerate(faces):
        k = ids.size
        fn_out[f, :k] = ids + 1
        xb[f, :k], yb[f, :k] = node_x[ids], node_y[ids]
        fx[f], fy[f] = node_x[ids].mean(), node_y[ids].mean()
    ex, ey = node_x[edge_nodes].mean(axis=1), node_y[edge_nodes].mean(axis=1)
    if spherical:
        xunit, yunit, xstd, ystd, crs_name = "degrees_east", "degrees_north", "longitude", "latitude", "wgs84"
    else:
        xunit = yunit = "m"; xstd, ystd = "projection_x_coordinate", "projection_y_coordinate"
        crs_name = "projected_coordinate_system"
    with netCDF4.Dataset(path, "w", format="NETCDF3_CLASSIC") as d:
        d.setncatts({"institution": "Private", "references": "vanlnNTNU@gmail.com",
            "source": "Fjord-Platform", "Conventions": "CF-1.8 UGRID-1.0 Deltares-0.10"
        })
        for name, size in (("Two", 2), ("mesh2d_nEdges", len(edge_nodes)), ("mesh2d_nNodes", nnode),
            ("mesh2d_nFaces", nface), ("mesh2d_nMax_face_nodes", nmax)):
            d.createDimension(name, size)
        crs = d.createVariable(crs_name, "i4")
        crs_attrs = {"name": "WGS 84" if spherical else f"EPSG:{epsg}", "epsg": np.int32(epsg),
            "EPSG_code": f"EPSG:{epsg}", "grid_mapping_name": "latitude_longitude" if spherical else "transverse_mercator"
        }
        if spherical:
            crs_attrs.update({"longitude_of_prime_meridian": 0.0, "semi_major_axis": 6378137.0,
                "semi_minor_axis": 6356752.314245, "inverse_flattening": 298.257223563
            })
        crs.setncatts(crs_attrs); crs.assignValue(epsg)
        m = d.createVariable("mesh2d", "i4")
        m.setncatts({
            "cf_role": "mesh_topology", "long_name": "Topology data of 2D mesh", "topology_dimension": np.int32(2),
            "node_coordinates": "mesh2d_node_x mesh2d_node_y", "node_dimension": "mesh2d_nNodes",
            "max_face_nodes_dimension": "mesh2d_nMax_face_nodes",
            "edge_node_connectivity": "mesh2d_edge_nodes", "edge_dimension": "mesh2d_nEdges",
            "edge_coordinates": "mesh2d_edge_x mesh2d_edge_y",
            "face_node_connectivity": "mesh2d_face_nodes", "face_dimension": "mesh2d_nFaces",
            "edge_face_connectivity": "mesh2d_edge_faces", "face_coordinates": "mesh2d_face_x mesh2d_face_y"})
        def var(name, dtype, dims, data, fill=None, **attrs):
            v = d.createVariable(name, dtype, dims, fill_value=fill)
            v.setncatts(attrs); v[:] = data
            return v
        var("mesh2d_node_x", "f8", ("mesh2d_nNodes",), node_x, units=xunit, standard_name=xstd, long_name="x-coordinate of mesh nodes")
        var("mesh2d_node_y", "f8", ("mesh2d_nNodes",), node_y, units=yunit, standard_name=ystd, long_name="y-coordinate of mesh nodes")
        var("mesh2d_node_z", "f8", ("mesh2d_nNodes",), np.where(np.isfinite(node_z), node_z, _FILL_DBL), _FILL_DBL,
            mesh="mesh2d", location="node", coordinates="mesh2d_node_x mesh2d_node_y", standard_name="altitude",
            long_name="z-coordinate of mesh nodes", units="m", grid_mapping=crs_name)
        var("mesh2d_edge_x", "f8", ("mesh2d_nEdges",), ex, units=xunit, standard_name=xstd, long_name="characteristic x-coordinate of the mesh edge (e.g. midpoint)")
        var("mesh2d_edge_y", "f8", ("mesh2d_nEdges",), ey, units=yunit, standard_name=ystd, long_name="characteristic y-coordinate of the mesh edge (e.g. midpoint)")
        var("mesh2d_edge_nodes", "i4", ("mesh2d_nEdges", "Two"), edge_nodes + 1,
            cf_role="edge_node_connectivity", long_name="Start and end nodes of mesh edges", start_index=np.int32(1))
        var("mesh2d_face_nodes", "i4", ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"), fn_out, _FILL_INT,
            cf_role="face_node_connectivity", long_name="Vertex nodes of mesh faces (counterclockwise)", start_index=np.int32(1))
        var("mesh2d_edge_faces", "i4", ("mesh2d_nEdges", "Two"), edge_faces + 1, _FILL_INT,   # cạnh biên: 0
            cf_role="edge_face_connectivity", long_name="Neighboring faces of mesh edges", start_index=np.int32(1))
        var("mesh2d_face_x", "f8", ("mesh2d_nFaces",), fx, units=xunit, standard_name=xstd, long_name="Characteristic x-coordinate of mesh face", bounds="mesh2d_face_x_bnd")
        var("mesh2d_face_y", "f8", ("mesh2d_nFaces",), fy, units=yunit, standard_name=ystd, long_name="Characteristic y-coordinate of mesh face", bounds="mesh2d_face_y_bnd")
        var("mesh2d_face_x_bnd", "f8", ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"), xb, _FILL_DBL, units=xunit, standard_name=xstd, long_name="x-coordinate bounds of mesh faces (i.e. corner coordinates)")
        var("mesh2d_face_y_bnd", "f8", ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"), yb, _FILL_DBL, units=yunit, standard_name=ystd, long_name="y-coordinate bounds of mesh faces (i.e. corner coordinates)")

def save_dflowfm_net(grid_uds: xr.Dataset, path: str, node_z=None):
    fn = np.asarray(grid_uds['mesh2d_face_nodes'].values, dtype=np.float64)
    start = grid_uds['mesh2d_face_nodes'].attrs.get('start_index', 1)
    if node_z is None: node_z = grid_uds['mesh2d_node_z'].values
    write_dflowfm_net(path, grid_uds['mesh2d_node_x'].values, 
        grid_uds['mesh2d_node_y'].values, node_z, fn - start, spherical=True, epsg=4326
    )

# ---------------------------------------------------------------------------
# Grid quality check / repair of small flow links
# ---------------------------------------------------------------------------
def to_metric_kernel(mk: MeshKernel, crs: str):
    """MeshKernel in metres. A grid in lon/lat is copied to its UTM zone (small-flow-link and angle checks need metres)."""
    c = CRS.from_user_input(crs)
    if not c.is_geographic: return mk, c.to_string()
    from pyproj import Transformer
    mesh = mk.mesh2d_get()
    utm = gpd.GeoSeries(gpd.points_from_xy(mesh.node_x, mesh.node_y), crs="EPSG:4326").estimate_utm_crs()
    x, y = Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform(mesh.node_x, mesh.node_y)
    k = MeshKernel()
    k.mesh2d_set(Mesh2d(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64), mesh.edge_nodes.astype(np.int32)))
    return k, utm.to_string()

def _face_matrix(mesh):
    npf = np.asarray(mesh.nodes_per_face, dtype=int)
    fn = np.full((npf.size, max(int(npf.max()), 3) if npf.size else 3), -1, dtype=np.int64)
    off = np.r_[0, np.cumsum(npf)[:-1]]
    for i, (o, n) in enumerate(zip(off, npf)): fn[i, :n] = mesh.face_nodes[o:o + n]
    return fn

def _triangle_angles(mesh):
    """(min angle, max angle) in degrees of every triangle."""
    fn = _face_matrix(mesh)
    tri = fn[(fn[:, :3] >= 0).all(axis=1) & ((fn.shape[1] == 3) | (fn[:, 3:] < 0).all(axis=1)), :3]
    if tri.size == 0: return np.array([]), np.array([])
    P = np.stack([mesh.node_x[tri], mesh.node_y[tri]], axis=-1)
    ang = []
    for j in range(3):
        u, v = P[:, (j + 1) % 3] - P[:, j], P[:, (j + 2) % 3] - P[:, j]
        cos = (u * v).sum(-1) / (np.linalg.norm(u, axis=-1) * np.linalg.norm(v, axis=-1))
        ang.append(np.degrees(np.arccos(np.clip(cos, -1, 1))))
    ang = np.stack(ang, axis=1)
    return ang.min(axis=1), ang.max(axis=1)

def grid_quality_report(mk: MeshKernel, crs: str) -> dict:
    """What D-Flow FM will complain about, plus the angle/orthogonality statistics of the grid."""
    from pyproj import Transformer
    k, crs_m = to_metric_kernel(mk, crs)
    mesh = k.mesh2d_get()
    centers = k.mesh2d_get_small_flow_edge_centers(DFM_SMALL_LINK_THRESHOLD)
    cx, cy = np.asarray(centers.x_coordinates), np.asarray(centers.y_coordinates)
    points = []
    if cx.size:
        lon, lat = Transformer.from_crs(crs_m, "EPSG:4326", always_xy=True).transform(cx, cy)
        points = [[float(a), float(b)] for a, b in zip(lat, lon)]
    amin, amax = _triangle_angles(mesh)
    orth = np.asarray(k.mesh2d_get_orthogonality().values); orth = orth[orth != -999]
    return {
        "n_nodes": int(mesh.node_x.size), "n_edges": int(mesh.edge_x.size), "n_faces": int(mesh.face_x.size),
        "small_links": int(cx.size), "small_link_points": points,           # [lat, lon] for Leaflet
        "obtuse_cells": int((amax > 90).sum()),
        "max_angle": round(float(amax.max()), 1) if amax.size else None,
        "min_angle": round(float(amin.min()), 1) if amin.size else None,
        "max_orthogonality": round(float(orth.max()), 4) if orth.size else None,
        "mean_orthogonality": round(float(orth.mean()), 4) if orth.size else None,
    }

def grid_content(grid_uds: xr.Dataset) -> dict:
    """GeoJSON of cells and nodes for the map (same as grid_creator_sync returns)."""
    grid = functions.unstructuredGridCreator(grid_uds)
    nodes = functions.nodes_from_grid(grid)
    nodes['Lat'], nodes['Lon'] = nodes.geometry.y, nodes.geometry.x
    return {'point': json.loads(nodes.to_json()), 'polygon': json.loads(grid.to_json())}

def fix_cached_grid_small_links(project_cache: dict) -> dict:
    """Remove the small flow links of the grid in memory (grid page). Updates mk, mk_crs and grid_uds."""
    mk = project_cache.get('mk')
    if mk is None: raise GridError("No grid in memory. Generate or optimize a grid first.")
    k, crs_m = to_metric_kernel(mk, project_cache.get('mk_crs', "EPSG:4326"))
    n_links = k.mesh2d_get_small_flow_edge_centers(DFM_SMALL_LINK_THRESHOLD).x_coordinates.size
    removed = remove_small_flow_edges(k)
    left = k.mesh2d_get_small_flow_edge_centers(DFM_SMALL_LINK_THRESHOLD).x_coordinates.size
    grid_uds = ugrid_to_wgs84(netCDF_creator(k), crs_m)
    project_cache['mk'], project_cache['mk_crs'], project_cache['grid_uds'] = k, crs_m, grid_uds
    return {"small_links": int(n_links), "removed_cells": int(removed), "left": int(left), "content": grid_content(grid_uds)}

def fix_net_file_small_flow_links(path: str, threshold: float = SMALL_FLOW_EDGE_THRESHOLD) -> dict:
    """Remove small flow links from a saved *_net.nc (scenario input). The original file is kept as a backup;
    the depth of every node is taken from the nearest node of the original grid."""
    with netCDF4.Dataset(path) as d:
        d.set_auto_mask(False)
        if 'mesh2d_node_x' not in d.variables or 'mesh2d_edge_nodes' not in d.variables:
            raise GridError("The grid file is not a UGRID (mesh2d) file. Save the grid again from the Grid tab.")
        x, y = d['mesh2d_node_x'][:].astype(np.float64), d['mesh2d_node_y'][:].astype(np.float64)
        z = d['mesh2d_node_z'][:].astype(np.float64) if 'mesh2d_node_z' in d.variables else np.zeros_like(x)
        ev = d['mesh2d_edge_nodes']; en = ev[:].astype(np.int64) - int(getattr(ev, 'start_index', 1))
        spherical = 'degree' in str(getattr(d['mesh2d_node_x'], 'units', '')).lower()
        epsg = 4326
        for v in d.variables.values():
            if hasattr(v, 'epsg'): epsg = int(np.asarray(v.epsg).ravel()[0]); break
    if spherical:
        utm = gpd.GeoSeries(gpd.points_from_xy(x, y), crs="EPSG:4326").estimate_utm_crs()
        xm, ym = Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform(x, y)
        xm, ym = np.asarray(xm, dtype=np.float64), np.asarray(ym, dtype=np.float64)
    else: xm, ym = x, y
    k = MeshKernel(); k.mesh2d_set(Mesh2d(xm, ym, en.ravel().astype(np.int32)))
    n_links = k.mesh2d_get_small_flow_edge_centers(DFM_SMALL_LINK_THRESHOLD).x_coordinates.size
    n_before = k.mesh2d_get().face_x.size
    removed = remove_small_flow_edges(k, threshold)
    if removed == 0: return {"changed": False, "small_links": int(n_links), "removed_cells": 0, "backup": None,
        "message": "No small flow links found in the grid."}
    mesh = k.mesh2d_get()
    z_new = z[KDTree(np.c_[xm, ym]).query(np.c_[mesh.node_x, mesh.node_y])[1]]
    nx, ny = mesh.node_x, mesh.node_y
    if spherical: nx, ny = Transformer.from_crs(utm, "EPSG:4326", always_xy=True).transform(nx, ny)
    # Write to a temporary file first, then replace the old grid in one step (no half-written grid on errors)
    root, ext = os.path.splitext(path)
    tmp = f"{root}_tmp{ext}"
    try:
        write_dflowfm_net(tmp, nx, ny, z_new, _face_matrix(mesh), spherical=spherical, epsg=epsg)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    deleted = remove_old_grid_files(os.path.dirname(path), keep_file=os.path.basename(path))
    left = k.mesh2d_get_small_flow_edge_centers(DFM_SMALL_LINK_THRESHOLD).x_coordinates.size
    return {"changed": True, "small_links": int(n_links), "removed_cells": int(n_before - mesh.face_x.size),
        "left": int(left), "deleted": deleted,
        "message": f"Removed {n_links} small flow link(s) ({n_before - mesh.face_x.size} of {n_before} cells merged). "
        f"The grid file '{os.path.basename(path)}' was replaced."
    }

def remove_old_grid_files(folder: str, keep_file: str, old_file: str | None = None) -> list:
    """Delete the previous grid of a scenario and all backup copies. Only grid files are touched:
    the old NetFile, other *_net.nc files and *_backup_*.nc / *_tmp.nc copies. Other .nc inputs
    (meteo, boundary data, ...) are never deleted. Returns the names of the deleted files."""
    import glob, re
    keep = os.path.normcase(os.path.abspath(os.path.join(folder, keep_file)))
    candidates = set(glob.glob(os.path.join(folder, "*_net.nc")))
    candidates |= {p for p in glob.glob(os.path.join(folder, "*.nc"))
        if re.search(r"_backup_\d{8}_\d{6}\.nc$|_tmp\.nc$", os.path.basename(p), re.IGNORECASE)
    }
    if old_file: candidates.add(os.path.join(folder, old_file))
    deleted = []
    for p in candidates:
        if os.path.normcase(os.path.abspath(p)) == keep or not os.path.isfile(p): continue
        try: os.remove(p); deleted.append(os.path.basename(p))
        except OSError as e: print(f"Could not delete old grid file {p}: {e}")
    return sorted(deleted)

def update_scenario_grid(input_dir: str, mdu_path: str, new_grid_path: str, new_name: str | None = None) -> dict:
    """Put a new grid into a scenario: copy it to the input folder, point NetFile in the MDU to it,
    then delete the old grid file and its backups."""
    import shutil, re
    new_name = new_name or os.path.basename(new_grid_path)
    dst = os.path.join(input_dir, new_name)
    with open(mdu_path, 'r', encoding='utf-8', errors='ignore') as f: lines = f.readlines()
    old_name = None
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*NetFile\s*=\s*)([^#\r\n]*?)(\s*(#.*)?)(\r?\n?)$', line, re.IGNORECASE)
        if m:
            old_name = m.group(2).strip() or None
            lines[i] = f"{m.group(1)}{new_name}{m.group(3)}{m.group(5) or os.linesep}"
            break
    else: raise GridError("NetFile not found in the MDU file.")
    if os.path.normcase(os.path.abspath(new_grid_path)) != os.path.normcase(os.path.abspath(dst)):
        tmp = dst + ".copying"
        shutil.copy2(new_grid_path, tmp); os.replace(tmp, dst)
    with open(mdu_path, 'w', encoding='utf-8') as f: f.writelines(lines)
    deleted = remove_old_grid_files(input_dir, keep_file=new_name, old_file=old_name)
    return {"grid": new_name, "old_grid": old_name, "deleted": deleted}

def inspect_net_file(path: str) -> dict:
    """Check an uploaded grid file: format (UGRID / old D-Flow FM 'NetNode' format) and number of small flow links."""
    import netCDF4
    from pyproj import Transformer
    try: d = netCDF4.Dataset(path)
    except OSError: raise GridError("The file is not a NetCDF file.")
    with d:
        d.set_auto_mask(False)
        if 'mesh2d_node_x' in d.variables and 'mesh2d_edge_nodes' in d.variables:
            if 'UGRID' not in str(getattr(d, 'Conventions', '')).upper():
                raise GridError("The grid file has no 'Conventions = UGRID-1.0' attribute, so D-Flow FM cannot read it. "
                    "Save the grid again from the Grid tab.")
            x, y = d['mesh2d_node_x'][:].astype(np.float64), d['mesh2d_node_y'][:].astype(np.float64)
            ev = d['mesh2d_edge_nodes']; en = ev[:].astype(np.int64) - int(getattr(ev, 'start_index', 1))
            spherical = 'degree' in str(getattr(d['mesh2d_node_x'], 'units', '')).lower()
            fmt = "UGRID"
        elif 'NetNode_x' in d.variables:
            return {"format": "old", "small_links": None}      # old D-Flow FM format: readable, not checked here
        else: raise GridError("The file does not contain a 2D grid (no mesh2d or NetNode variables).")
    if spherical:
        utm = gpd.GeoSeries(gpd.points_from_xy(x, y), crs="EPSG:4326").estimate_utm_crs()
        x, y = (np.asarray(v, dtype=np.float64) for v in Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform(x, y))
    k = MeshKernel(); k.mesh2d_set(Mesh2d(x, y, en.ravel().astype(np.int32)))
    return {"format": fmt, "small_links": int(k.mesh2d_get_small_flow_edge_centers(DFM_SMALL_LINK_THRESHOLD).x_coordinates.size)}