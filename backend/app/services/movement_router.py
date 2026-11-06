import os, traceback, json, math, gzip, asyncio, gc
from fastapi import APIRouter, Request, Depends, HTTPException, Response, Query
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from services import functions, movement_functions
from config import PROJECT_ROOT
import numpy as np

router = APIRouter()
MOVEMENT_CACHE, CACHE, _CACHE_LOCKS = {}, {"Cache-Control": "public, max-age=86400"}, {}


def _cache_lock_for(project_name):
    return _CACHE_LOCKS.setdefault(project_name, asyncio.Lock())

def _close_movement_cache(project_name):
    cached = MOVEMENT_CACHE.pop(project_name, None)
    if cached is None: return
    frames = cached.get("FRAMES")
    mmap = getattr(frames, "_mmap", None)
    if mmap is not None: mmap.close()
    del frames, cached
    gc.collect()

def _parse_res(value):
    if value in (None, "", "auto", "Auto", "AUTO"): return None
    res = float(value)
    if not math.isfinite(res) or res <= 0:
        raise ValueError("Resolution must be a positive number (m) or 'auto'.")
    return res

def _gzip_response(request: Request, data: bytes, headers: dict):
    if "gzip" in request.headers.get("accept-encoding", "") and len(data) > 2048:
        data = gzip.compress(data, compresslevel=1, mtime=0)
        headers = {**headers, "Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
    return Response(data, media_type="application/octet-stream", headers=headers)

def _movement_paths(project_name, body):
    out_dir = os.path.join(PROJECT_ROOT, project_name, "output")
    move_dir = os.path.join(out_dir, "movement")
    os.makedirs(move_dir, exist_ok=True)
    return {
        "out_dir": out_dir, "move_dir": move_dir,
        "meta_path": os.path.join(move_dir, "meta.json"),
        "polygon_path": os.path.join(move_dir, "domain.geojson"),
        "frame_path": os.path.join(move_dir, "frames.npy"),
        "mask_path": os.path.join(move_dir, "mask.npy"),
        "resolution": _parse_res(body.get("res")),
        "layer": body.get("layer") or "mean",
        "force": bool(body.get("rebuild")),
    }

def _load_movement_cache(project_name, paths):
    frames = None
    try:
        frames = np.load(paths["frame_path"], mmap_mode="r")
        mask = np.load(paths["mask_path"])
        with open(paths["meta_path"], "r", encoding="utf-8") as f:
            meta = json.load(f)
        if frames.ndim != 3:
            raise ValueError(f"frames.npy must have shape (T, 3, Nv); got {frames.shape}")
        T, C, NV = frames.shape
        if C != 3:
            raise ValueError(f"Expected 3 channels (u, v, depth); got {C}")
        if mask.ndim != 2:
            raise ValueError(f"mask.npy must be 2D; got shape {mask.shape}")
        NY, NX = mask.shape
        flat = np.flatnonzero(mask.ravel())
        if flat.size != NV:
            raise ValueError("mask.npy and frames.npy mismatch. Rebuild data.")
        if T != len(meta.get("times", [])):
            raise ValueError("Frame count and meta.json times do not match. Rebuild data.")
        idx_map = np.full(NY * NX, -1, dtype=np.int64)
        idx_map[flat] = np.arange(NV)
        cache = {
            "FRAMES": frames, "MASK": mask, "IDX": idx_map, "T": T, "C": C,
            "NV": NV, "NY": NY, "NX": NX, "NODATA": meta.get("nodata"), "META": meta,
        }
        MOVEMENT_CACHE[project_name] = cache
        frames = None
        return cache
    finally:
        if frames is not None:
            mmap = getattr(frames, "_mmap", None)
            if mmap is not None: mmap.close()

@router.post("/close_movement")
async def close_movement(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        _close_movement_cache(project_name)
        return JSONResponse({'status': 'ok'})
    except Exception as e:
        print('/close_movement:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.post("/init_movement")
async def init_movement(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    try:
        project_name, _ = functions.project_definer(body.get('projectName'), user)
        paths = _movement_paths(project_name, body)
        meta_path = paths["meta_path"]
        polygon_path = paths["polygon_path"]
        frame_path = paths["frame_path"]
        mask_path = paths["mask_path"]
        resolution = paths["resolution"]
        layer = paths["layer"]
        force = paths["force"]
        async with _cache_lock_for(project_name):
            reason = movement_functions.needs_rebuild(meta_path, frame_path, mask_path, resolution, layer)
            if force: reason = "Rebuilding data"
            if reason:
                # map_path = os.path.join(out_dir, 'HYD', 'FlowFM_map.zarr')
                map_path = os.path.join('test', 'test_map.nc')
                if not os.path.isfile(map_path):
                    return JSONResponse({"status": "error", "message": f"Cannot find model output (.nc/.zarr) in {paths['out_dir']}."})
                _close_movement_cache(project_name)
                result = await run_in_threadpool(
                    movement_functions.init_data, meta_path, polygon_path, map_path, frame_path, mask_path, resolution, layer
                )
                if result is not None: return JSONResponse({"status": "error", "message": result})
            cache = MOVEMENT_CACHE.get(project_name)
            if cache is None: cache = _load_movement_cache(project_name, paths)
            meta = cache["META"]
            content = {
                "data": meta, "frame_count": cache["T"], 
                "rebuilt": bool(reason), "reason": reason, "layer": layer
            }
            return JSONResponse({'content': content})
    except Exception as e:
        print('/init_movement:\n==============')
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': f"Error: {e}"})

@router.get("/mask_movement")
async def mask_movement(request: Request, project_name: str = Query(alias="projectName"), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    async with _cache_lock_for(project_name):
        project_data = MOVEMENT_CACHE.get(project_name)
        if project_data is None:
            return JSONResponse(status_code=404, content={"status": "error","message": f"Movement data not initialized for project: {project_name}"})
        data = np.ascontiguousarray(project_data["MASK"]).tobytes()
        return _gzip_response(request, data, {"Cache-Control": "no-cache"})

@router.get("/frame/{i}")
async def frame(i: int, request: Request, project_name: str = Query(alias="projectName"), user=Depends(functions.basic_auth)):
    project_name, _ = functions.project_definer(project_name, user)
    async with _cache_lock_for(project_name):
        project_data = MOVEMENT_CACHE.get(project_name)
        if project_data is None:
            return JSONResponse(status_code=404, content={"status": "error","message": f"Movement data not initialized for project: {project_name}"})
        total = project_data["T"]
        if not 0 <= i < total: raise HTTPException(404, f"Frame {i} outside of 0..{total - 1}")
        data = np.ascontiguousarray(project_data["FRAMES"][i]).astype("<i2", copy=False).tobytes()
        headers, times = dict(CACHE), project_data["META"].get("times", [])
        if i < len(times): headers["X-Frame-Time"] = str(times[i])
        return _gzip_response(request, data, headers)

@router.get("/point_movement")
async def point_movement(project_name: str = Query(alias="projectName"), 
    lat: float = Query(...), lon: float = Query(...), user=Depends(functions.basic_auth)):
    try:
        project_name, _ = functions.project_definer(project_name, user)
        async with _cache_lock_for(project_name):
            pdata = MOVEMENT_CACHE.get(project_name)
            if pdata is None: return JSONResponse(status_code=404, content={"status": "error", "message": f"Movement data not initialized for project: {project_name}"})
            meta, nodata = pdata["META"], pdata["NODATA"]
            nx, ny = pdata["NX"], pdata["NY"]
            if not math.isfinite(lat) or not math.isfinite(lon):
                return JSONResponse(status_code=200, content={ "status": "error", "message": "Coordinates are invalid."})
            ix = int(math.floor((lon - meta["lon0"]) / meta["dlon"] + 0.5))
            iy = int(math.floor((lat - meta["lat0"]) / meta["dlat"] + 0.5))
            if not (0 <= ix < nx and 0 <= iy < ny):
                return JSONResponse(content={"status": "error", "message": "Point is outside of the area. Select another point inside the boundary."})
            j = int(pdata["IDX"][iy * nx + ix])
            if j < 0: return JSONResponse(content={"status": "error", "message": "Point is outside of the area. Select another point inside the boundary."})
            cell = np.asarray(pdata["FRAMES"][:, :, j], dtype=np.float32)
            dry = cell[:, 0] == nodata
            scale = meta["scale"]
            u, v = cell[:, 0] * scale["u"], cell[:, 1] * scale["v"]
            speed, depth = np.hypot(u, v), cell[:, 2] * scale["depth"]
            direction = (np.degrees(np.arctan2(u, v)) + 360) % 360
            def to_list(a, nd):
                return [None if d else round(float(x), nd) for x, d in zip(a, dry)]
            return {
                "ix": ix, "iy": iy, "lat": meta["lat0"] + iy * meta["dlat"], "lon": meta["lon0"] + ix * meta["dlon"],
                "speed": to_list(speed, 3), "depth": to_list(depth, 2), "direction": to_list(direction, 0)
            }
    except Exception as exc:
        print('/point_movement:\n==============')
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})