import uvicorn, os, sys
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
    

# Import internally backend modules
from config import SOURCE_BACKEND, SOURCE_FRONTEND, lifespan
from services import hyd_router, process_router, \
    project_router, route_page, data_router, flow_router, \
    grid_router, sim_router, calibration_router, waq_router, \
    movement_router, dashboard_router

app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Mount common static files for Mobirise (frontend)
app.mount("/src_frontend", StaticFiles(directory=SOURCE_FRONTEND), name="src_frontend")
app.mount("/src_backend", StaticFiles(directory=SOURCE_BACKEND), name="src_backend")

# Mount routes
all_routers = [
    route_page.router, project_router.router, data_router.router, 
    grid_router.router, hyd_router.router, process_router.router,
    sim_router.router, waq_router.router, flow_router.router,
    calibration_router.router, movement_router.router,
    dashboard_router.router
]
for router in all_routers:
    app.include_router(router)


if __name__ == "__main__":
    # Remove reload=True for production
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, 
        reload_dirs=[os.path.dirname(os.path.abspath(__file__))]
    )