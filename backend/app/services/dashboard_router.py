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
import asyncio, json, time, traceback, msgpack
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from typing import cast
from services import dashboard_functions as dfn
from services import dashboard_results as dres
from services import functions

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

CACHE_TTL = 300  # giây
_memory_cache: dict[str, tuple[float, object]] = {}


# ------------------------------------------------------------------ helpers
async def _body(request: Request) -> dict:
    try: return await request.json()
    except Exception: return {}

def _project(body: dict, user: str) -> str:
    name = (body.get("projectName") or "").strip().strip("/")
    if "/" in name:              # frontend có thể gửi "user/project"
        name = name.split("/")[-1]
    project_name, _ = functions.project_definer(name, user)
    return project_name

async def _cached(request: Request, key: str, builder, *args, use_redis=True) -> dict:
    redis = getattr(request.app.state, "redis", None) if use_redis else None
    full_key, now = f"dashboard:{key}", time.time()
    if redis is not None:
        try:
            raw = await redis.get(full_key)
            if raw: return msgpack.unpackb(raw, raw=False)
        except Exception: redis = None
    hit = _memory_cache.get(full_key)
    if hit and now - hit[0] < CACHE_TTL: cast(dict, hit[1])
    result = await asyncio.to_thread(builder, *args)   # pandas/xarray chạy trong thread
    if redis is not None:
        try: await redis.set(full_key, msgpack.packb(result, use_bin_type=True), ex=CACHE_TTL)
        except Exception: traceback.print_exc()
    else:
        _memory_cache[full_key] = (time.time(), result)
        if len(_memory_cache) > 256:                    # giới hạn bộ nhớ
            for k in sorted(_memory_cache, key=lambda k: _memory_cache[k][0])[:64]:
                _memory_cache.pop(k, None)
    return result

def _version(project: str) -> str:
    return dres.ProjectResults(project).version()

def _error(where: str, e: Exception, code: int = 500):
    print(f"{where}:\n==============")
    traceback.print_exc()
    return JSONResponse({"status": "error", "message": f"Error: {e}"}, status_code=code)

# ------------------------------------------------------------------ routes
@router.post("/projects")
async def dashboard_projects(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        user_dir, _ = functions.project_definer("", user)
        projects = await asyncio.to_thread(dfn.list_projects, user_dir) if user != "admin" else ["demo"]
        current = _project(body, user) if body.get("projectName") else None
        return {"status": "ok", "user": user, "projects": projects, "current": current}
    except Exception as e: return _error("/dashboard/projects", e)

@router.post("/catalog")
async def dashboard_catalog(request: Request, user=Depends(functions.basic_auth)):
    try:
        project = _project(await _body(request), user)
        data = await _cached(request, f"catalog:{project}:{_version(project)}", dres.catalog, project)
        return {"status": "ok", **data}
    except Exception as e: return _error("/dashboard/catalog", e)

@router.post("/summary")
async def dashboard_summary(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        end, period = body.get("end"), body.get("period", "7d")
        key = f"summary:{project}:{_version(project)}:{end}:{period}"
        data = await _cached(request, key, dfn.summarize, project, end, period)
        return {"status": "ok", **data}
    except Exception as e: return _error("/dashboard/summary", e)

@router.post("/alerts")
async def dashboard_alerts(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        end, period = body.get("end"), body.get("period", "7d")
        key = f"summary:{project}:{_version(project)}:{end}:{period}"
        summary = await _cached(request, key, dfn.summarize, project, end, period)
        alerts = dfn.alerts_from_checks(summary["checks"], limit=200)
        if body.get("severities"): alerts = [a for a in alerts if a["severity"] in body["severities"]]
        limit = int(body.get("limit", 20))
        return {"status": "ok", "alerts": alerts[:limit], "total": len(alerts)}
    except Exception as e: return _error("/dashboard/alerts", e)

@router.post("/mesh")
async def dashboard_mesh(request: Request, user=Depends(functions.basic_auth)):
    try:
        project = _project(await _body(request), user)
        # Lưới có thể lớn: chỉ cache trong ProjectResults (bộ nhớ), không đẩy vào Redis
        mesh = await asyncio.to_thread(lambda: dres.ProjectResults(project).mesh())
        if mesh is None:
            return JSONResponse({"status": "error", "message": "No map file with a mesh in this project."}, status_code=404)
        return {"status": "ok", "mesh": {k: v for k, v in mesh.items() if k != "centroids"}}
    except Exception as e: return _error("/dashboard/mesh", e)

@router.post("/field")
async def dashboard_field(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        source, name = body.get("source", "hyd"), body.get("name")
        if not isinstance(name, str) or not name:
            return JSONResponse({"status": "error", "message": "Missing 'name' in request body."}, status_code=400)
        layer, when = body.get("layer", "surface"), body.get("time")
        pr = dres.ProjectResults(project)
        data = await asyncio.to_thread(pr.field, source, name, layer, when)
        return {"status": "ok", **data}
    except ValueError as e: return _error("/dashboard/field", e, 404)
    except Exception as e: return _error("/dashboard/field", e)

@router.post("/station")
async def dashboard_station(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project = _project(body, user)
        pr = dres.ProjectResults(project)
        times, name = pr.times(body.get("source", "hyd")), body.get("name")
        start, end, _ = dfn.resolve_window(times, body.get("end"), body.get("period", "7d"))
        if not isinstance(name, str) or not name:
            return JSONResponse({"status": "error", "message": "Missing 'name' in request body."}, status_code=400)
        data = await asyncio.to_thread(pr.station_series, body.get("source", "hyd"), name,
            body.get("layer", "surface"), start, end, body.get("stations"))
        return {"status": "ok", **data}
    except Exception as e: return _error("/dashboard/station", e)

@router.post("/profile")
async def dashboard_profile(request: Request, user=Depends(functions.basic_auth)):
    try:
        body = await _body(request)
        project, station = _project(body, user), body.get("station")
        if not isinstance(station, str) or not station:
            return JSONResponse(
                {"status": "error", "message": "Missing 'station' in request body."}, status_code=400,
            )
        pr = dres.ProjectResults(project)
        data = await asyncio.to_thread(pr.profile, body.get("source", "hyd"), 
            body.get("name", "temperature"), station, body.get("time"))
        if data is None:
            return JSONResponse({"status": "error", "message": "No profile data available."}, status_code=404)
        if data.get("empty"):
            # Không có dữ liệu profile không phải là lỗi -> 200 + status "empty" (tránh 404 trong console)
            return {"status": "empty", "message": data["reason"], "station": data.get("station")}
        return {"status": "ok", **data}
    except Exception as e: return _error("/dashboard/profile", e)

@router.post("/layers")
async def dashboard_layers(request: Request, user=Depends(functions.basic_auth)):
    try:
        project = _project(await _body(request), user)
        inventory = await asyncio.to_thread(dfn.project_inventory, project)
        key = f"layers:{project}:{','.join(inventory.get('gis_layers', []))}"
        data = await _cached(request, key, dfn.build_layers, project, inventory)
        return {"status": "ok", "layers": data}
    except Exception as e: return _error("/dashboard/layers", e)

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
    except Exception as e: return _error("/dashboard/thresholds", e)
