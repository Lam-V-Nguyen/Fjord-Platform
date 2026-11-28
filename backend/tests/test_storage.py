"""
Storage quota per user (services/storage.py).   Run:  cd backend && python -m pytest -q tests
"""
import os, sys, importlib, time
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "app"), os.path.join(HERE, "..")]
from services import constants  # noqa: E402

KB = 1024

@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fjord_quota")
    (tmp / "frontend" / "src" / "htmls").mkdir(parents=True)
    for n in ("login.html", "admin.html"): (tmp / "frontend" / "src" / "htmls" / n).write_text("<html></html>")
    os.environ.update(PROJECT_DES=str(tmp), ENV="development", REQUIRE_REDIS="false",
                      USER_QUOTA_GB=str(100 * KB / 1024 ** 3), QUOTA_WARN_PERCENT="50")      # quota = 100 KB
    from fastapi import APIRouter, Depends, FastAPI, Request
    from fastapi.testclient import TestClient
    import config; importlib.reload(config)
    from services import auth_store, auth_router, security, functions, storage
    for m in (auth_store, storage, functions, auth_router, security): importlib.reload(m)
    auth_store.init_db()
    app = FastAPI(); security.install(app); app.include_router(auth_router.router)
    r = APIRouter()

    def route(path):
        async def handler(request: Request, user=Depends(functions.basic_auth)):
            body = await request.json()
            try: return {"status": "ok", "project": functions.project_definer(body.get("projectName"), user)[0]}
            except Exception as e: functions.error(path, e)
        r.add_api_route(path, handler, methods=["POST"])
    for p in ("/start_sim_hyd", "/data_upload", "/save_overrides_calibration", "/delete_project", "/download_station"):
        route(p)
    app.include_router(r, dependencies=[Depends(functions.basic_auth)])
    auth_store.create_user("anna", email="anna@x.no", password="Correct-horse-9", role="user", status="active")
    auth_store.create_user("boss", email="boss@x.no", password="Correct-horse-9", role="admin", status="active",
                           allow_reserved=True)
    for u in ("anna", "boss"): os.makedirs(os.path.join(config.PROJECT_ROOT, u, "lake"), exist_ok=True)
    c = TestClient(app, base_url=constants.LOCAL_HOST)
    return c, config, storage, auth_store

def put(config, user, kb):
    with open(os.path.join(config.PROJECT_ROOT, user, "lake", "big.nc"), "wb") as f: f.write(b"0" * kb * KB)

def login(c, u): assert c.post("/auth/login", json={"username": u, "password": "Correct-horse-9"}).status_code == 200

def test_levels_and_block(env):
    c, config, storage, _ = env
    login(c, "anna")
    put(config, "anna", 10); storage.invalidate()
    st = c.get("/auth/storage").json()["storage"]
    assert st["level"] == "ok" and st["quota"] == 100 * KB
    put(config, "anna", 60); storage.invalidate("anna"); storage._cache.pop("anna", None)
    st = c.get("/auth/storage").json()["storage"]
    assert st["level"] == "warn" and "storage quota" in st["message"]
    put(config, "anna", 120); storage._cache.pop("anna", None)
    r = c.post("/start_sim_hyd", json={"projectName": "lake"})
    assert r.status_code == 507 and "Storage quota reached" in r.json()["detail"]
    assert c.post("/data_upload", json={"projectName": "lake"}).status_code == 507
    # small settings, read-only downloads and deleting stay allowed
    assert c.post("/save_overrides_calibration", json={"projectName": "lake"}).status_code == 200
    assert c.post("/download_station", json={"projectName": "lake"}).status_code == 200
    assert c.post("/delete_project", json={"projectName": "lake"}).status_code == 200
    # after deleting: the size is measured again
    put(config, "anna", 5)
    for _ in range(50):
        storage.usage("anna"); time.sleep(0.05)
        if storage.usage("anna") < 50 * KB: break
    assert c.post("/start_sim_hyd", json={"projectName": "lake"}).status_code == 200

def test_admin_unlimited_and_override(env):
    c, config, storage, _ = env
    put(config, "boss", 200); storage._cache.pop("boss", None)
    login(c, "boss")
    assert c.post("/start_sim_hyd", json={"projectName": "lake"}).status_code == 200        # admins: no limit
    users = {u["username"]: u for u in c.get("/auth/admin/users").json()["users"]}
    assert users["boss"]["storage"]["quota"] is None and users["anna"]["storage"]["quota"] == 100 * KB
    # per-user override: anna gets 1 GB, then back to the default
    assert c.post("/auth/admin/quota", json={"username": "anna", "quota_gb": 1}).json()["storage"]["quota"] == 1024 ** 3
    assert storage.quota_gb("anna") == 1.0
    c.post("/auth/admin/quota", json={"username": "anna", "quota_gb": ""})
    assert storage.quota_gb("anna") == 100 * KB / 1024 ** 3
    # override for an admin applies too
    c.post("/auth/admin/quota", json={"username": "boss", "quota_gb": 100 * KB / 1024 ** 3})
    assert c.post("/start_sim_hyd", json={"projectName": "lake"}).status_code == 507
