"""
Dashboard router
================
API cho trang Overview – nội dung chính là KẾT QUẢ MÔ PHỎNG HYD / WAQ của dự án.

    POST /dashboard/projects    -> danh sách dự án của người dùng
    POST /dashboard/catalog     -> thông tin kịch bản HYD/WAQ, biến bản đồ & trạm, trạm, nguồn, mốc thời gian
    POST /dashboard/summary     -> KPI, gauges, trạng thái chất lượng nước, nguồn/xả, checks, phân bố rủi ro
    POST /dashboard/alerts      -> cảnh báo (sắp xếp theo mức độ, thời gian)
    POST /dashboard/mesh        -> lưới tính toán (GeoJSON WGS84, thứ tự = chỉ số ô)
    POST /dashboard/field       -> giá trị một biến bản đồ tại một thời điểm & lớp (surface|bottom|average|<i>)
    POST /dashboard/station     -> chuỗi thời gian tại trạm (biến his HYD/WAQ)
    POST /dashboard/profile     -> profile thẳng đứng tại trạm, một thời điểm
    POST /dashboard/layers      -> lớp ngữ cảnh: ranh giới hồ, lưu vực, sông, độ sâu mẫu, GIS dự án
    POST /dashboard/thresholds  -> ngưỡng (reload=true để nạp lại file JSON và xoá cache)

Body chung: {"projectName": "...", "end": "YYYY-MM-DD[THH:MM]", "period": "1d|7d|30d|90d|all"}
Cache: Redis (nếu có) hoặc bộ nhớ, khoá gồm "phiên bản" file kết quả -> tự làm mới khi chạy lại mô phỏng.
"""
import asyncio, gzip, json, os, time, traceback, msgpack
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from services import dashboard_functions as dfn
from services import dashboard_results as dres
from services import dashboard_extras as dext
from services import functions

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

CACHE_TTL = 300  # seconds
_memory_cache: dict[str, tuple[float, object]] = {}


# helpers
async def _body(request: Request) -> dict:
    try: return await request.json()
    except Exception: return {}

def _project(body: dict, user: str) -> str:
    name = (body.get("projectName") or "").strip().strip("/")
    if "/" in name: name = name.split("/")[-1]
    project_name, _ = functions.project_definer(name, user)
    return project_name

async def _cached(request: Request, key: str, builder, *args, use_redis=True):
    redis = getattr(request.app.state, "redis", None) if use_redis else None
    full_key = f"dashboard:{key}"
    if redis is not None:
        try:
            raw = await redis.get(full_key)
            if raw: return msgpack.unpackb(raw, raw=False)
        except Exception: redis = None
    hit = _memory_cache.get(full_key)
    if redis is None and hit and time.time() - hit[0] < CACHE_TTL: return hit[1]
    result = await asyncio.to_thread(builder, *args)
    if redis is not None:
        try: await redis.set(full_key, msgpack.packb(result, use_bin_type=True), ex=CACHE_TTL)
        except Exception: traceback.print_exc()
    else:
        _memory_cache[full_key] = (time.time(), result)
        if len(_memory_cache) > 256:
            for k in sorted(_memory_cache, key=lambda k: _memory_cache[k][0])[:64]:
                _memory_cache.pop(k, None)
    return result

def _version(project: str) -> str:
    return dres.ProjectResults(project).version()

# routes
@router.post("/projects")
async def dashboard_projects(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        user_dir, _ = functions.project_definer("", user)
        projects = await asyncio.to_thread(dfn.list_projects, user_dir) if user != "admin" else ["demo"]
        current = _project(body, user) if body.get("projectName") else None
        return {"status": "ok", "user": user, "projects": projects, "current": current}
    except Exception as e: functions.error("/dashboard/projects", e)

@router.post("/catalog")
async def dashboard_catalog(request: Request, user=Depends(functions.basic_auth)):
    try:
        project = _project(await _body(request), user)
        data = await _cached(request, f"catalog:{project}:{_version(project)}", dres.catalog, project)
        return {"status": "ok", **data}
    except Exception as e: functions.error("/dashboard/catalog", e)

@router.post("/summary")
async def dashboard_summary(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        end, period = body.get("end"), body.get("period", "7d")
        key = f"summary:{project}:{_version(project)}:{end}:{period}"
        data = await _cached(request, key, dfn.summarize, project, end, period)
        return {"status": "ok", **data}
    except Exception as e: functions.error("/dashboard/summary", e)
    

@router.post("/alerts")
async def dashboard_alerts(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        end, period = body.get("end"), body.get("period", "7d")
        key = f"summary:{project}:{_version(project)}:{end}:{period}"
        summary = await _cached(request, key, dfn.summarize, project, end, period)
        alerts = dfn.alerts_from_checks(summary["checks"], limit=200)
        if body.get("severities"):
            alerts = [a for a in alerts if a["severity"] in body["severities"]]
        limit = int(body.get("limit", 20))
        return {"status": "ok", "alerts": alerts[:limit], "total": len(alerts)}
    except Exception as e: functions.error("/dashboard/alerts", e)

@router.post("/mesh")
async def dashboard_mesh(request: Request, user=Depends(functions.basic_auth)):
    try:
        project = _project(await _body(request), user)
        # Lưới có thể lớn: chỉ cache trong ProjectResults (bộ nhớ), không đẩy vào Redis
        mesh = await asyncio.to_thread(lambda: dres.ProjectResults(project).mesh())
        if mesh is None:
            return JSONResponse({"status": "error", "message": "No map file with a mesh in this project."}, status_code=404)
        return {"status": "ok", "mesh": {k: v for k, v in mesh.items() if k != "centroids"}}
    except Exception as e: functions.error("/dashboard/mesh", e)

@router.post("/field")
async def dashboard_field(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        source, name = body.get("source", "hyd"), body.get("name")
        layer, when = body.get("layer", "surface"), body.get("time")
        pr = dres.ProjectResults(project)
        data = await asyncio.to_thread(pr.field, source, name, layer, when)
        return {"status": "ok", **data}
    except ValueError as e: functions.error("/dashboard/field", e, 404)
    except Exception as e: functions.error("/dashboard/field", e)

@router.post("/station")
async def dashboard_station(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        pr = dres.ProjectResults(project)
        times = pr.times(body.get("source", "hyd"))
        start, end, _ = dfn.resolve_window(times, body.get("end"), body.get("period", "7d"))
        data = await asyncio.to_thread(pr.station_series, body.get("source", "hyd"), 
            body.get("name"), body.get("layer", "surface"), start, end, body.get("stations"))
        return {"status": "ok", **data}
    except Exception as e: functions.error("/dashboard/station", e)

@router.post("/profile")
async def dashboard_profile(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        pr = dres.ProjectResults(project)
        data = await asyncio.to_thread(pr.profile, body.get("source", "hyd"), 
            body.get("name", "temperature"), body.get("station"), body.get("time"))
        if data.get("empty"):
            # Không có dữ liệu profile không phải là lỗi -> 200 + status "empty" (tránh 404 trong console)
            return {"status": "empty", "message": data["reason"], "station": data.get("station")}
        return {"status": "ok", **data}
    except Exception as e: functions.error("/dashboard/profile", e)

@router.post("/layers")
async def dashboard_layers(request: Request, user=Depends(functions.basic_auth)):
    try:
        project = _project(await _body(request), user)
        inventory = await asyncio.to_thread(dfn.project_inventory, project)
        key = f"layers:{project}:{','.join(inventory.get('gis_layers', []))}"
        data = await _cached(request, key, dfn.build_layers, project, inventory)
        return {"status": "ok", "layers": data}
    except Exception as e: functions.error("/dashboard/layers", e)

@router.post("/thresholds")
async def dashboard_thresholds(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        if body.get("reload"):
            _memory_cache.clear()
            redis = getattr(request.app.state, "redis", None)
            if redis is not None:
                try:
                    async for k in redis.scan_iter("dashboard:summary:*"):
                        await redis.delete(k)
                except Exception: traceback.print_exc()
            data = dfn.reload_thresholds()
        else: data = dfn.load_thresholds()
        return {"status": "ok", **json.loads(json.dumps(data))}
    except Exception as e: functions.error("/dashboard/thresholds", e)

# lớp bổ sung: Movement & Wflow
@router.post("/extras")
async def dashboard_extras_status(request: Request, user=Depends(functions.basic_auth)):
    """Trạng thái dữ liệu cho 2 lớp bổ sung (không cache: chỉ kiểm tra file)."""
    try:
        project = _project(await _body(request), user)
        data = await asyncio.to_thread(dext.extras_status, project)
        return {"status": "ok", **data}
    except Exception as e: functions.error("/dashboard/extras", e)

@router.post("/movement/init")
async def dashboard_movement_init(request: Request, user=Depends(functions.basic_auth)):
    """Dựng/nạp cache movement từ HYD map của dự án. Khung hình đọc qua GET /mask_movement, /frame/{i}."""
    try:
        body = await _body(request)
        project = _project(body, user)
        # Khoá riêng của dashboard (cache output/movement_dashboard)
        async with dext.movement_lock(project):
            data = await asyncio.to_thread(dext.movement_init, 
                project, body.get("res"), body.get("layer"), bool(body.get("rebuild")))
        if data.get("error"): return {"status": "unavailable", "message": data["error"]}
        return {"status": "ok", **data}
    except Exception as e: functions.error("/dashboard/movement/init", e)

@router.post("/wflow/init")
async def dashboard_wflow_init(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        flow = body.get("flowName")
        if not flow:
            status = await asyncio.to_thread(dext.wflow_status, project)
            flow = status.get("default")
            if not flow: return {"status": "unavailable", "message": status.get("reason")}
        data = await asyncio.to_thread(dext.wflow_init, project, flow, body.get("res"), bool(body.get("rebuild")))
        if data.get("error"): return {"status": "unavailable", "message": data["error"]}
        return {"status": "ok", **data}
    except Exception as e: functions.error("/dashboard/wflow/init", e)

def _wflow_ctx(project_name: str, flow: str, user):
    project = _project({"projectName": project_name}, user)
    return dext.wflow_context(project, flow)

@router.get("/wflow/mask")
async def dashboard_wflow_mask(request: Request, project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), user=Depends(functions.basic_auth)):
    try:
        model, meta, fc = await asyncio.to_thread(_wflow_ctx, project_name, flow, user)
        import numpy as np
        m = np.load(os.path.join(fc.model_paths(model, dext.DASH_VIEW_DIR)["cache"], "mask.npy"))
        return fc.get_bin(request, np.ascontiguousarray(m, dtype=np.uint8).tobytes())
    except Exception as e: functions.error("/dashboard/wflow/mask", e, 404)

@router.get("/wflow/rivers")
async def dashboard_wflow_rivers(project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), user=Depends(functions.basic_auth)):
    try:
        model, meta, fc = await asyncio.to_thread(_wflow_ctx, project_name, flow, user)
        path = os.path.join(fc.model_paths(model, dext.DASH_VIEW_DIR)["cache"], "rivers.geojson")
        if not os.path.exists(path):
            return JSONResponse({"type": "FeatureCollection", "features": []})
        return FileResponse(path, media_type="application/geo+json")
    except Exception as e: functions.error("/dashboard/wflow/rivers", e, 404)

@router.get("/wflow/geometry/{kind}")
async def dashboard_wflow_geometry(kind: str, project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), user=Depends(functions.basic_auth)):
    """kind = basins | water (GeoJSON WGS84) – dùng để cắt/xoá hạt giống Flow Estimation."""
    if kind not in ("basins", "water"):
        return JSONResponse({"status": "error", "message": "kind must be basins or water"}, status_code=400)
    try:
        project = _project({"projectName": project_name}, user)
        return JSONResponse(await asyncio.to_thread(dext.wflow_geometry, project, flow, kind))
    except Exception as e: functions.error("/dashboard/wflow/geometry", e, 404)

@router.get("/wflow/frame/{var}/{i}")
async def dashboard_wflow_frame(var: str, i: int, request: Request, project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), user=Depends(functions.basic_auth)):
    try:
        model, meta, fc = await asyncio.to_thread(_wflow_ctx, project_name, flow, user)
        if var not in meta.get("variables", {}):
            return JSONResponse({"status": "error", "message": f"Unknown variable {var}"}, status_code=404)
        if not 0 <= i < len(meta.get("times", [])):
            return JSONResponse({"status": "error", "message": f"Frame {i} out of range"}, status_code=404)
        data = await asyncio.to_thread(fc.frame_bytes, model, meta, var, i, dext.DASH_VIEW_DIR)
        return fc.get_bin(request, data)
    except Exception as e: functions.error("/dashboard/wflow/frame", e, 404)

def _bin(request: Request, data: bytes, headers: Optional[dict] = None):
    """Nhị phân, nén gzip khi trình duyệt hỗ trợ; URL có ?v=<version> nên được cache lâu."""
    headers = {"Cache-Control": "private, max-age=86400", **(headers or {})}
    if "gzip" in request.headers.get("accept-encoding", "") and len(data) > 2048:
        data = gzip.compress(data, compresslevel=1, mtime=0)
        headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(data, media_type="application/octet-stream", headers=headers)

@router.get("/movement/mask")
async def dashboard_movement_mask(request: Request, 
    project_name: str = Query(alias="projectName"), user=Depends(functions.basic_auth)):
    """mask.npy (ny, nx) của cache movement, uint8, hàng 0 = phía nam."""
    try:
        project = _project({"projectName": project_name}, user)
        return _bin(request, await asyncio.to_thread(dext.movement_mask_bytes, project))
    except Exception as e: functions.error("/dashboard/movement/mask", e, 404)

@router.get("/movement/frame/{i}")
async def dashboard_movement_frame(i: int, request: Request, 
    project_name: str = Query(alias="projectName"), user=Depends(functions.basic_auth)):
    """Khung i: int16 u | v | depth trên các ô hợp lệ (đọc thẳng frames.npy, không cần /init_movement)."""
    try:
        project = _project({"projectName": project_name}, user)
        return _bin(request, await asyncio.to_thread(dext.movement_frame_bytes, project, i))
    except Exception as e: functions.error("/dashboard/movement/frame", e, 404)

@router.get("/wflow/velocity/{i}")
async def dashboard_wflow_velocity(i: int, request: Request, project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), user=Depends(functions.basic_auth)):
    """Trường vận tốc Wflow tại bước i: float16 (2, Nv) = u (đông), v (bắc) m/s trên các ô của mask; NaN = khô.
    Đọc cache riêng của dashboard (viewer), không phải cache của Flow Estimation."""
    try:
        model, meta, fc = await asyncio.to_thread(_wflow_ctx, project_name, flow, user)
        if not meta.get("velocity"):
            return JSONResponse({"status": "error", "message": "No velocity field in this Wflow output "
                "(needs overland_flow / river_discharge on the grid)."}, status_code=404)
        if not 0 <= i < len(meta.get("times", [])):
            return JSONResponse({"status": "error", "message": f"Frame {i} out of range"}, status_code=404)
        data = await asyncio.to_thread(fc.velocity_bytes, model, meta, i, dext.DASH_VIEW_DIR)
        return fc.get_bin(request, data)
    except Exception as e: functions.error("/dashboard/wflow/velocity", e, 404)

@router.get("/wflow/stat/{var}/{stat}")
async def dashboard_wflow_stat(var: str, stat: str, request: Request, project_name: str = Query(alias="projectName"),
    flow: str = Query(alias="flowName"), threshold: float = Query(None), user=Depends(functions.basic_auth)):
    """Thống kê cả kỳ chạy theo ô (float32) – header X-Stat-Min / X-Stat-Max cho thang màu."""
    try:
        project = _project({"projectName": project_name}, user)
        data, lo, hi = await asyncio.to_thread(dext.wflow_stat, project, flow, var, stat, threshold)
        model, meta, fc = await asyncio.to_thread(dext.wflow_context, project, flow)
        return fc.get_bin(request, data, {"X-Stat-Min": f"{lo:g}", "X-Stat-Max": f"{hi:g}",
            "Access-Control-Expose-Headers": "X-Stat-Min, X-Stat-Max"})
    except (ValueError, KeyError, FileNotFoundError) as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    except Exception as e: functions.error("/dashboard/wflow/stat", e)