"""
Router "Wflow 3D" cho Dashboard (tách riêng, không sửa dashboard_router.py)
=========================================================================
    GET /dashboard/wflow3d/terrain?projectName=&flowName=      -> float32 (ny, nx) độ cao trên lưới viewer
                                                                  (header X-Z-Min, X-Z-Max, X-Z-Source)
    GET /dashboard/wflow3d/scene?projectName=&flowName=        -> JSON: cửa ra + hydrograph, mưa lưu vực
    GET /dashboard/wflow3d/series/{river_id}?projectName=&flowName=[&var=] -> JSON chuỗi thời gian tại một ô sông

Cần gọi POST /dashboard/wflow/init trước (dựng cache viewer) – frontend đã làm việc này.
Các endpoint còn lại (mask, frame, velocity, rivers, geometry) dùng lại của dashboard_router.
"""
import asyncio, gzip
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from services import functions
from services import wflow3D_functions as w3d

router = APIRouter(prefix="/dashboard/wflow3d", tags=["dashboard-wflow3d"])


def _project(name: str, user: str) -> str:
    name = (name or "").strip().strip("/")
    if "/" in name: name = name.split("/")[-1]
    project_name, _ = functions.project_definer(name, user)
    return project_name

def _bin(request: Request, data: bytes, headers: Optional[dict] = None) -> Response:
    headers = {"Cache-Control": "private, max-age=86400", **(headers or {})}
    if "gzip" in request.headers.get("accept-encoding", "") and len(data) > 2048:
        data = gzip.compress(data, compresslevel=1, mtime=0)
        headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(data, media_type="application/octet-stream", headers=headers)

def _err(where: str, e: Exception, code: int = 404):
    print(f"{where}: {type(e).__name__}: {e}")
    return JSONResponse({"status": "error", "message": str(e)}, status_code=code)

@router.get("/terrain")
async def wflow3d_terrain(request: Request, project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), user=Depends(functions.basic_auth)):
    try:
        project = _project(project_name, user)
        data, info = await asyncio.to_thread(w3d.terrain, project, flow)
        return _bin(request, data, {"X-Z-Min": f"{info['zmin']:g}", "X-Z-Max": f"{info['zmax']:g}",
            "X-Z-Source": str(info.get("source")), "X-Z-Filled": f"{info.get('filled_fraction', 0):g}",
            "Access-Control-Expose-Headers": "X-Z-Min, X-Z-Max, X-Z-Source, X-Z-Filled"})
    except Exception as e: return _err("/dashboard/wflow3d/terrain", e)

@router.get("/scene")
async def wflow3d_scene(project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), user=Depends(functions.basic_auth)):
    try:
        project = _project(project_name, user)
        return {"status": "ok", **(await asyncio.to_thread(w3d.scene, project, flow))}
    except Exception as e: return _err("/dashboard/wflow3d/scene", e)

@router.get("/series/{river_id}")
async def wflow3d_series(river_id: int, project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), var: Optional[str] = Query(None), user=Depends(functions.basic_auth)):
    try:
        project = _project(project_name, user)
        return {"status": "ok", **(await asyncio.to_thread(w3d.river_series, project, flow, river_id, var))}
    except ValueError as e: return _err("/dashboard/wflow3d/series", e, 400)
    except Exception as e: return _err("/dashboard/wflow3d/series", e)
