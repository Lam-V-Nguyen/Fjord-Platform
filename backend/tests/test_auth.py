"""
Tests for sign-in, roles and project isolation.   Run:  cd backend && python -m pytest -q tests
Uses temporary folders for projects / database, so real data is never touched.
"""
import os, sys, importlib
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "app"), os.path.join(HERE, "..")]

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fjord")
    (tmp / "frontend" / "src" / "htmls").mkdir(parents=True)
    for n in ("login.html", "admin.html"): (tmp / "frontend" / "src" / "htmls" / n).write_text("<html></html>")
    os.environ.update(PROJECT_DES=str(tmp), ENV="development", REQUIRE_REDIS="false")
    from fastapi import APIRouter, Depends, FastAPI, Request
    from fastapi.testclient import TestClient
    import backend.app.config; importlib.reload(backend.app.config)
    from backend.app.services import auth_store, auth_router, security, functions
    for m in (auth_store, functions, auth_router, security): importlib.reload(m)
    auth_store.init_db()
    app = FastAPI(); security.install(app); app.include_router(auth_router.router)
    r = APIRouter()

    @r.post("/project")
    async def project(request: Request, user=Depends(functions.basic_auth)):
        body = await request.json()
        try: return {"project": functions.project_definer(body.get("projectName"), user)[0]}
        except Exception as e: functions.error("/project", e)

    @r.post("/dashboard/read")
    async def read(request: Request, user=Depends(functions.basic_auth)):
        body = await request.json() if (await request.body()) else {}
        try:
            if body.get("projectName"): return {"project": functions.project_definer(body["projectName"], user)[0]}
            return {"ok": True}
        except Exception as e: functions.error("/dashboard/read", e)
    app.include_router(r, dependencies=[Depends(functions.basic_auth)])
    auth_store.create_user(
        "boss", email="boss@x.no", password="Correct-horse-9", 
        role="admin", status="active", allow_reserved=True
    )
    import backend.app.config as cfg
    os.makedirs(os.path.join(cfg.PROJECT_ROOT, "boss", "lake1"), exist_ok=True)
    os.makedirs(os.path.join(cfg.PROJECT_ROOT, "demo"), exist_ok=True)          # sample project
    return TestClient(app, base_url="http://localhost:8000"), auth_store

def login(c, u, p): return c.post("/auth/login", json={"username": u, "password": p})

def test_requires_login(client):
    c, _ = client
    assert c.post("/project", json={"projectName": "a"}).status_code == 401

def test_login_and_project_isolation(client):
    c, _ = client
    assert login(c, "boss", "wrong-password").status_code == 401
    assert login(c, "boss", "Correct-horse-9").status_code == 200
    assert c.post("/project", json={"projectName": "lake1"}).json()["project"] == "boss/lake1"
    # "../other/x" cannot leave the user's folder
    assert c.post("/project", json={"projectName": "../other/x"}).json()["project"] == "boss/x"
    assert c.post("/project", json={"projectName": ".."}).status_code == 400

def test_csrf_blocked(client):
    c, _ = client
    r = c.post("/project", json={"projectName": "a"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403

def test_invite_flow_and_viewer(client):
    c, _ = client
    login(c, "boss", "Correct-horse-9")
    inv = c.post("/auth/admin/invite", json={"username": "anna", "email": "anna@x.no", "role": "viewer"}).json()
    token = inv["link"].split("invite=")[1]
    from fastapi.testclient import TestClient
    v = TestClient(c.app, base_url="http://localhost:8000")
    assert v.post("/auth/set_password", json={"kind": "invite", "token": token, "password": "short"}).status_code == 400
    assert v.post("/auth/set_password", json={"kind": "invite", "token": token, "password": "Blue-river-fjord-1"}).status_code == 200
    assert v.post("/auth/set_password", json={"kind": "invite", "token": token, "password": "Blue-river-fjord-2"}).status_code == 400
    assert v.post("/dashboard/read").status_code == 200        # viewer: may read
    assert v.post("/project", json={"projectName": "a"}).status_code == 403   # viewer: may not write
    assert v.get("/auth/admin/users").status_code == 403

def test_rate_limit_and_last_admin(client):
    c, _ = client
    x = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(c.app, base_url="http://localhost:8000")
    for _ in range(6): login(x, "anna", "nope-nope-nope")
    assert login(x, "anna", "Blue-river-fjord-1").status_code == 429
    login(c, "boss", "Correct-horse-9")
    r = c.post("/auth/admin/update", json={"username": "boss", "role": "user"})
    assert r.status_code == 400 and "last" in r.json()["message"]

def test_passwords_are_hashed(client):
    _, store = client
    with store.db() as con:
        h = con.execute("SELECT password_hash FROM users WHERE username='boss'").fetchone()[0]
    assert h.startswith("$argon2id$") and "Correct-horse-9" not in h


def test_project_sharing(client):
    c, store = client
    from fastapi.testclient import TestClient
    login(c, "boss", "Correct-horse-9")
    store.create_user("carl", email="carl@x.no", password="Green-lake-tide-7", role="user", status="active")
    carl = TestClient(c.app, base_url="http://localhost:8000"); login(carl, "carl", "Green-lake-tide-7")
    # not shared yet
    assert carl.post("/dashboard/read", json={"projectName": "boss:lake1"}).status_code == 403
    # view share: read OK, write refused
    r = c.post("/auth/shares/add", json={"project": "lake1", "username": "carl", "access": "view"})
    assert r.status_code == 200 and r.json()["shares"][0]["grantee"] == "carl"
    assert carl.post("/dashboard/read", json={"projectName": "boss:lake1"}).json()["project"] == "boss/lake1"
    assert carl.post("/project", json={"projectName": "boss:lake1"}).status_code == 403
    assert [s["ref"] for s in carl.get("/auth/shares/with_me").json()["shares"]] == ["boss:lake1"]
    # carl cannot manage the owner's sharing
    assert carl.post("/auth/shares/add", json={"project": "boss:lake1", "username": "anna", "access": "edit"}).status_code == 403
    # edit share: write allowed
    c.post("/auth/shares/add", json={"project": "lake1", "username": "carl", "access": "edit"})
    assert carl.post("/project", json={"projectName": "boss:lake1"}).json()["project"] == "boss/lake1"
    # unknown project / traversal through the owner part
    assert c.post("/auth/shares/add", json={"project": "nope", "username": "carl"}).status_code == 404
    assert carl.post("/dashboard/read", json={"projectName": "..:x"}).status_code == 400
    # revoke
    c.post("/auth/shares/remove", json={"project": "lake1", "username": "carl"})
    assert carl.post("/dashboard/read", json={"projectName": "boss:lake1"}).status_code == 403


def test_sample_project_read_only(client):
    c, store = client
    from fastapi.testclient import TestClient
    u = TestClient(c.app, base_url="http://localhost:8000"); login(u, "carl", "Green-lake-tide-7")
    assert u.post("/dashboard/read", json={"projectName": "public:demo"}).json()["project"] == "demo"
    r = u.post("/project", json={"projectName": "public:demo"})
    assert r.status_code == 403 and "read-only" in r.json()["message"]
    assert u.get("/auth/shares?project=public:demo").json()["my_access"] == "view"
    assert u.post("/dashboard/read", json={"projectName": "public:other"}).status_code == 404
    login(c, "boss", "Correct-horse-9")                                   # administrator may edit the sample
    assert c.post("/project", json={"projectName": "public:demo"}).json()["project"] == "demo"
    assert c.post("/auth/shares/add", json={"project": "public:demo", "username": "carl"}).status_code == 400


def test_per_user_waq_choice_on_sample_and_shared_projects(client):
    """Choosing a WAQ scenario on someone else's project (sample / shared) is stored per user."""
    import json, types
    _, store = client
    from backend.app.services import functions
    import backend.app.config as cfg
    def as_user(name):
        req = types.SimpleNamespace(
            state=types.SimpleNamespace(user=store.get_user(name)), method="POST", url=types.SimpleNamespace(path="/setup_database")
        )
        functions._CURRENT_REQUEST.set(req)
    base = os.path.join(cfg.PROJECT_ROOT, "demo", "output", "config", "config.json")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    json.dump({"model_name": "A"}, open(base, "w"))
    as_user("carl")                                               # ordinary user on the sample project
    mine = functions.user_config_path("demo")
    assert mine != base and json.load(open(mine))["model_name"] == "A"     # starts from the sample's choice
    json.dump({"model_name": "B"}, open(mine, "w"))                         # carl switches to scenario B
    assert functions.cache_key("demo") == "demo#carl"
    assert json.load(open(base))["model_name"] == "A"                       # nobody else is affected
    as_user("boss")                                               # administrators edit the sample itself
    assert functions.user_config_path("demo") == base and functions.cache_key("demo") == "demo"
    assert functions.user_config_path("boss/lake1").startswith(cfg.PROJECT_ROOT)    # owner: own project config
    as_user("carl")
    assert "user_state" in functions.user_config_path("boss/lake1")        # shared project: private copy
    functions._CURRENT_REQUEST.set(None)
