"""
Platform configuration – read from environment variables / the .env file (see deploy/.env.example).

ENV=development : local machine (http://localhost:8000, cookies do not require HTTPS)
ENV=production  : real server behind an HTTPS reverse proxy (Caddy / Nginx) – SECRET_KEY, PUBLIC_URL and Redis are required
All default paths derive from PROJECT_DES (repository root); each one can be overridden.
"""
import os, socket, secrets, logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

def _env(name: str, default=None, required: bool = False):
    v = os.getenv(name)
    v = v.strip() if isinstance(v, str) else v
    if not v:
        if required: raise RuntimeError(f"Environment variable {name} is not set (see deploy/.env.example).")
        return default
    return v

def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")

def _path(name: str, default: str) -> str:
    return os.path.normpath(_env(name, default))

# ============== Environment ================
env_mode = (_env("ENV", "development") or "development").lower()
if env_mode not in ("development", "production"):
    raise RuntimeError("ENV must be 'development' or 'production'")
IS_PROD = env_mode == "production"

# ============== Folders ================
PROJECT_DES = _env("PROJECT_DES", required=True)
PROJECT_ROOT = _path("PROJECT_ROOT", os.path.join(PROJECT_DES, "backend/projects"))
SOURCE_BACKEND = _path("SOURCE_BACKEND", os.path.join(PROJECT_DES, "backend/src"))
SOURCE_FRONTEND = _path("SOURCE_FRONTEND", os.path.join(PROJECT_DES, "frontend/src"))
DELFT_PATH = _path("DELFT_PATH", os.path.join(PROJECT_DES, "backend/softs"))
WFLOW_PATH = _path("WFLOW_PATH", os.path.join(PROJECT_DES, "backend/softs/wflow 1.0.2"))
WHITEBOX_DIR = _path("WHITEBOX_DIR", os.path.join(PROJECT_DES, "backend/softs/whitebox"))
DATA_DIR = _path("DATA_DIR", os.path.join(PROJECT_DES, "backend/data"))          # user database, logs
LOG_DIR = _path("LOG_DIR", os.path.join(DATA_DIR, "logs"))
AUTH_DB = _path("AUTH_DB", os.path.join(DATA_DIR, "auth.sqlite3"))
# Only used to import the legacy accounts ONCE (python backend/tools/manage_users.py import-json)
ALLOWED_USERS = _path("ALLOWED_USERS", os.path.join(PROJECT_DES, "backend/allowed_users.json"))
for _d in (PROJECT_ROOT, DATA_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

# ============== Web / security ================
PUBLIC_URL = (_env("PUBLIC_URL", "http://localhost:8000", required=IS_PROD) or "").rstrip("/")
# Secret key (encrypts credentials.json, ...): required in production; in development it is generated once
# and stored in DATA_DIR/.secret_key so it survives restarts
def _dev_secret() -> str:
    p = os.path.join(DATA_DIR, ".secret_key")
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8") as f: f.write(secrets.token_urlsafe(48))
    with open(p, encoding="utf-8") as f: return f.read().strip()
SECRET_KEY = _env("SECRET_KEY", required=IS_PROD) or _dev_secret()
COOKIE_NAME = _env("COOKIE_NAME", "fjord_session")
COOKIE_SECURE = _bool("COOKIE_SECURE", IS_PROD)            # True: cookie is only sent over HTTPS
SESSION_HOURS = int(_env("SESSION_HOURS", "12"))           # expires after N idle hours
SESSION_MAX_DAYS = int(_env("SESSION_MAX_DAYS", "14"))     # at most N days, even when used continuously
INVITE_HOURS = int(_env("INVITE_HOURS", "72"))
RESET_HOURS = int(_env("RESET_HOURS", "2"))
LOGIN_MAX_FAILS = int(_env("LOGIN_MAX_FAILS", "5"))        # N failures within 15 min -> locked for 15 min
MAX_UPLOAD_MB = int(_env("MAX_UPLOAD_MB", "200"))
_origins = [o.strip().rstrip("/") for o in (_env("ALLOWED_ORIGINS", "") or "").split(",") if o.strip()]
ALLOWED_ORIGINS = _origins or ([PUBLIC_URL] if IS_PROD else [PUBLIC_URL, "http://localhost:8000", "http://127.0.0.1:8000"])
_hosts = [h.strip() for h in (_env("ALLOWED_HOSTS", "") or "").split(",") if h.strip()]
ALLOWED_HOSTS = _hosts or ([urlparse(PUBLIC_URL).hostname, "localhost", "127.0.0.1"] if IS_PROD else ["*"])
# Send error details to the browser? (production: generic message + error id only; details go to the log)
SHOW_ERROR_DETAILS = _bool("SHOW_ERROR_DETAILS", not IS_PROD)
SELF_REGISTRATION = _bool("SELF_REGISTRATION", True)
# Sample projects visible to EVERY signed-in user, read only (administrators may edit them).
# Folders directly under PROJECT_ROOT (e.g. PROJECT_ROOT/demo), referenced as "public:<name>".
PUBLIC_PROJECTS = [p.strip() for p in (_env("PUBLIC_PROJECTS", "demo") or "").split(",") if p.strip()]       # "Request access" button (account waits for admin approval)

# Max simulation processes (D-Flow FM, DELWAQ, Wflow) running at once on the server – above it, new runs are refused
MAX_SOLVER_PROCESSES = int(_env("MAX_SOLVER_PROCESSES", str(max(1, (os.cpu_count() or 4) // 2))))

# ============== E-mail (SMTP) – not configured: invite / reset links are shown to the admin to send manually ================
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
SMTP_FROM = _env("SMTP_FROM", SMTP_USER or "no-reply@localhost")
SMTP_TLS = _bool("SMTP_TLS", True)                          # STARTTLS (port 587)
SMTP_SSL = _bool("SMTP_SSL", False)                         # implicit SSL (port 465)
ADMIN_EMAILS = [e.strip() for e in (_env("ADMIN_EMAILS", "") or "").split(",") if e.strip()]

# ============== Redis ================
REDIS_URL = _env("REDIS_URL", "redis://localhost:6379/0")
REQUIRE_REDIS = _bool("REQUIRE_REDIS", IS_PROD)

# ============== Logging (rotating file + console) ================
def setup_logging():
    from logging.handlers import RotatingFileHandler
    root = logging.getLogger()
    if getattr(root, "_fjord_configured", False): return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root.setLevel((_env("LOG_LEVEL", "INFO") or "INFO").upper())
    fh = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"), maxBytes=10_000_000, backupCount=10, encoding="utf-8")
    fh.setFormatter(fmt); root.addHandler(fh)
    sh = logging.StreamHandler(); sh.setFormatter(fmt); root.addHandler(sh)
    root._fjord_configured = True

setup_logging()
log = logging.getLogger("fjord")

# ============== Redis Client ================
def check_redis_running(url: str = REDIS_URL, timeout=1):
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "localhost", u.port or 6379), timeout=timeout): return True
    except OSError: return False

# ============== Lifespan ================
@asynccontextmanager
async def lifespan(app):
    from redis.asyncio import Redis
    from services import auth_store, dataset_manager
    auth_store.init_db()
    app.state.dataset_manager = dataset_manager.DatasetManager()
    app.state.env, app.state.project_cache = env_mode, {}
    app.state.redis = None
    if check_redis_running():
        try: app.state.redis = Redis.from_url(REDIS_URL, decode_responses=False)
        except Exception as e: log.error("Failed to initialise Redis: %s", e)
    if app.state.redis is None:
        if REQUIRE_REDIS: raise RuntimeError(f"Redis is required (REQUIRE_REDIS) but not reachable at {REDIS_URL}")
        log.warning("Redis server is not running – using in-memory caches only.")
    log.info("Fjord Platform started (ENV=%s, PUBLIC_URL=%s)", env_mode, PUBLIC_URL)
    yield
    try:
        app.state.dataset_manager.close()
        if app.state.redis: await app.state.redis.close()
    except Exception as e: log.error("Shutdown error: %s", e)
