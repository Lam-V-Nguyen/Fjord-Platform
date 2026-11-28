import uvicorn, os, sys
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

# backend/ (for "app.main") and backend/app/ (for "import config", "from services import …") – both are needed when
# running "uvicorn app.main:app" from the backend folder (production service) and "python app/main.py" (development)
app_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(app_dir)
for _p in (backend_dir, app_dir):
    if _p not in sys.path: sys.path.insert(0, _p)

# Import internally backend modules
from config import SOURCE_BACKEND, SOURCE_FRONTEND, lifespan, IS_PROD, ALLOWED_HOSTS
from services import hyd_router, process_router, \
    project_router, route_page, data_router, flow_router, \
    grid_router, sim_router, calibration_router, waq_router, \
    movement_router, dashboard_router, wflow3D_router, auth_router, security, functions

app = FastAPI(lifespan=lifespan,
    # Auto-generated API docs (/docs) only in development
    docs_url=None if IS_PROD else "/docs", redoc_url=None, openapi_url=None if IS_PROD else "/openapi.json")
# Production: Caddy compresses (encode zstd gzip) outside Python; GZipMiddleware would compress large GeoJSON on the
# event loop and block other requests. Development (no reverse proxy): compress here.
if not IS_PROD: app.add_middleware(GZipMiddleware, minimum_size=1000)
security.install(app)                                       # security headers, CSRF check, upload limit, error handling, /health
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

# Static files (frontend is public: the login page needs CSS/JS; /src_backend requires a session – see security.py)
app.mount("/src_frontend", StaticFiles(directory=SOURCE_FRONTEND), name="src_frontend")
app.mount("/src_backend", StaticFiles(directory=SOURCE_BACKEND), name="src_backend")

# Public routes: home page (redirects to /login), sign-in / admin pages (check permissions themselves)
app.include_router(route_page.router)
app.include_router(auth_router.router)

# EVERY other router requires a signed-in user at router level -> no route can miss authentication.
# (Routes that already declare `user=Depends(functions.basic_auth)` are not checked twice: FastAPI caches dependencies per request.)
protected_routers = [
    project_router.router, data_router.router,
    grid_router.router, hyd_router.router, process_router.router,
    sim_router.router, waq_router.router, flow_router.router,
    calibration_router.router, movement_router.router,
    dashboard_router.router, wflow3D_router.router
]
for router in protected_routers:
    app.include_router(router, dependencies=[Depends(functions.basic_auth)])


if __name__ == "__main__":
    # Development: python app/main.py (auto-reload on code changes).
    # Production: do NOT use this – run as a service (deploy/windows or deploy/linux):
    #   uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=not IS_PROD,
        reload_dirs=[os.path.dirname(os.path.abspath(__file__))], proxy_headers=True, forwarded_allow_ips="127.0.0.1"
    )
