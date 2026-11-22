"""
security.py – Application-wide middleware & error handling (installed in main.py).

- Security headers (no framing by other sites, no MIME sniffing, HSTS in production…)
- CSRF protection: state-changing requests (POST/PUT/PATCH/DELETE) must come from this site (Origin/Referer)
- Upload size limit (Content-Length > MAX_UPLOAD_MB -> 413)
- /src_backend/* for signed-in users only
- AppError / uncaught errors -> JSON {"status": "error", "message", "error_id"} (no tracebacks in production)
- /health for monitoring tools
"""
import logging, shutil, time
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from config import ALLOWED_ORIGINS, MAX_UPLOAD_MB, PROJECT_ROOT, SHOW_ERROR_DETAILS, IS_PROD
from services.functions import AppError, log_error

log = logging.getLogger("fjord.security")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin") or ""
    if not origin:
        ref = request.headers.get("referer") or ""
        if not ref: return True              # command-line tools / scripts (still 401 without a session cookie)
        u = urlparse(ref); origin = f"{u.scheme}://{u.netloc}"
    origin = origin.rstrip("/")
    # Same host as the request (behind a proxy: X-Forwarded-Host) or listed in ALLOWED_ORIGINS
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return origin in ALLOWED_ORIGINS or urlparse(origin).netloc == host

class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method in UNSAFE:
            if not _origin_ok(request):
                log.warning("Blocked cross-site %s %s (origin=%s)", request.method, path, request.headers.get("origin"))
                return JSONResponse({"status": "error", "message": "Cross-site request blocked"}, status_code=403)
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > MAX_UPLOAD_MB * 1024 * 1024:
                return JSONResponse({"status": "error", "message": f"Upload too large (max {MAX_UPLOAD_MB} MB)"}, status_code=413)
        if path.startswith("/src_backend/"):
            from services.functions import current_user
            if current_user(request) is None:
                return JSONResponse({"status": "error", "message": "Not authenticated"}, status_code=401)
        t0 = time.perf_counter()
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")              # same-origin widget iframes keep working
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if IS_PROD: h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith(("/auth/", "/login", "/admin")): h["Cache-Control"] = "no-store"
        dt = (time.perf_counter() - t0) * 1000
        if dt > 5000: log.info("Slow request %s %s: %.0f ms", request.method, path, dt)
        return response

def install(app: FastAPI):
    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse({"status": "error", "message": exc.message, "error_id": exc.error_id}, status_code=exc.code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        # Same shape as other errors ({"status": "error", "message"}) + "detail" for older code
        return JSONResponse({"status": "error", "message": exc.detail, "detail": exc.detail},
                            status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        error_id = log_error(f"{request.method} {request.url.path}", exc)
        msg = f"Error: {exc}" if SHOW_ERROR_DETAILS else f"Internal error (id {error_id}). Please contact the administrator."
        return JSONResponse({"status": "error", "message": msg, "error_id": error_id}, status_code=500)

    @app.get("/health", include_in_schema=False)
    async def health(request: Request):
        """For Uptime Kuma / UptimeRobot…: 200 when healthy, 503 when Redis is down or the disk is almost full."""
        checks, ok = {}, True
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            try: await redis.ping(); checks["redis"] = "ok"
            except Exception as e: checks["redis"] = f"error: {e}"; ok = False
        else: checks["redis"] = "not configured"
        du = shutil.disk_usage(PROJECT_ROOT)
        used = du.used / du.total
        checks["disk_used_pct"] = round(100 * used, 1)
        checks["disk_free_gb"] = round(du.free / 1e9, 1)
        if used > 0.95: ok = False
        try:
            from services import auth_store
            with auth_store.db() as con: con.execute("SELECT 1")
            checks["auth_db"] = "ok"
        except Exception as e: checks["auth_db"] = f"error: {e}"; ok = False
        return JSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)
