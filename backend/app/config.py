import os, socket
from contextlib import asynccontextmanager
from services import dataset_manager
from dotenv import load_dotenv
from redis.asyncio import Redis

# ============== Root directory ================
load_dotenv()
env_mode = os.getenv("ENV", "development")
if env_mode == "development":
    PROJECT_DES = os.getenv("PROJECT_DES")
    if PROJECT_DES is None: raise RuntimeError("PROJECT_DES environment variable is not set.")
    ALLOWED_USERS = os.path.normpath(os.path.join(PROJECT_DES, "backend/allowed_users.json"))
    PROJECT_ROOT = os.path.normpath(os.path.join(PROJECT_DES, "backend/projects"))
    SOURCE_BACKEND = os.path.normpath(os.path.join(PROJECT_DES, "backend/src"))
    SOURCE_FRONTEND = os.path.normpath(os.path.join(PROJECT_DES, "frontend/src"))
    DELFT_PATH = os.path.normpath(os.path.join(PROJECT_DES, "backend/softs"))
    WFLOW_PATH = os.path.normpath(os.path.join(PROJECT_DES, "backend/softs/wflow 1.0.2"))
    WHITEBOX_DIR = os.path.normpath(os.path.join(PROJECT_DES, "backend/softs/whitebox"))
    REDIS_URL = "redis://localhost:6379/0"
# else:
#     PROJECT_DES = os.getenv("/app")
#     ALLOWED_USERS_PATH = os.path.normpath(os.path.join(PROJECT_DES, "static/allowed_users.json"))
#     PROJECT_STATIC_ROOT = "/app/Delft_Projects"
#     STATIC_DIR_BACKEND = "/app/static"
#     STATIC_DIR_FRONTEND = "/app/frontend/static"
#     DELFT_PATH = os.path.normpath(os.path.join(PROJECT_DES, "x64"))
#     REDIS_URL = "redis://redis:6379/0"


# ============== Redis Client ================
def check_redis_running(host="localhost", port=6379, timeout=1):
    # Check if redis-server is running.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except Exception: return False

# ============== Lifespan ================
@asynccontextmanager
async def lifespan(app):
    # Dataset
    app.state.dataset_manager = dataset_manager.DatasetManager()
    app.state.env, app.state.project_cache = env_mode, {}
    # Check whether Redis is running
    if check_redis_running():
        try:
            app.state.redis = Redis.from_url(REDIS_URL, decode_responses=False)
        except Exception as e:
            print(f"Failed to initialize Redis: {e}")
            app.state.redis = None
    else:
        print("Redis server is not running. Try to install Redis.")
        app.state.redis = None
    yield
    try:
        app.state.dataset_manager.close()
        if app.state.redis:
            await app.state.redis.close()
    except Exception as e:
        print(f"Dataset manager close error: {e}")