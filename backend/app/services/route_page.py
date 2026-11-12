import os, traceback, msgpack, json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.config import SOURCE_FRONTEND, PROJECT_ROOT
from services import functions

router = APIRouter()
htmls = Jinja2Templates(directory=os.path.normpath(os.path.join(SOURCE_FRONTEND, "htmls")))

# ==================== Routes ====================
# Home page
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    folder = Jinja2Templates(directory=os.path.dirname(SOURCE_FRONTEND))
    return folder.TemplateResponse(request=request, name="dashboard.html")

# Load widget menu
@router.get("/getWidgetMenu")
def load_widgetMenu(request: Request):
    try: return htmls.TemplateResponse(request=request, name="mainMenu.html")
    except Exception as e: functions.error('/getWidgetMenu', e)

# Load popup menu
@router.get("/load_popupMenu", response_class=HTMLResponse)
async def load_popupMenu(request: Request, data: str, project_name: str = '', user=Depends(functions.basic_auth)):
    # Show project menu, read config from Redis
    if not project_name:
        return HTMLResponse(f"<p>Project '{project_name}' not found</p>", status_code=404)    
    # Acquire Redis lock to prevent race condition
    redis = request.app.state.redis
    project_name, _ = functions.project_definer(project_name, user)
    htmlFile, waq_name = data.split("|")
    if htmlFile == "gisLayer.html":
        gis_folder = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "GIS"))
        if not os.path.exists(gis_folder): os.makedirs(gis_folder, exist_ok=True)
        gis_layers = [f.replace('.geojson', '') for f in os.listdir(gis_folder) if f.endswith(".geojson")]
        html = [f'<div class="menu" style="max-height: 300px; overflow-y: auto;">']
        if len(gis_layers) > 0:
            for layer in gis_layers:
                html.append(f'''
                    <div style="display:flex; align-items:center; justify-content:space-between;">
                        <label class="submenu-label" style="margin:0 10px 0 0;">
                            <input type="checkbox" class="layer-gis" id="{layer.replace(" ", "_")}" value="{layer}">
                            <span">{layer}</span>
                        </label>
                        <button class="delete-btn" id="delete-{layer.replace(" ", "_")}" 
                            style="margin:5px 5px 0 10px; border-radius:5px; cursor:pointer; color:red;
                            font-weight: bold; padding: 3px 6px; background-color: #9acde9;">Delete
                        </button>
                    </div>
                ''')
        else: html.append('<div><p>No GIS layers found</p></div>')
        html.append('</div>')
        return ''.join(html)
    elif htmlFile == 'waqMenu.html':
        waq_dir = os.path.normpath(os.path.join(PROJECT_ROOT, project_name, "output", "WAQ"))
        html = [f'<div class="menu">']
        if os.path.exists(waq_dir):
            waq_models = [f for f in os.listdir(waq_dir) if f.endswith(".json")]
            if len(waq_models) > 0:
                for model in waq_models:
                    path = os.path.normpath(os.path.join(waq_dir, model))
                    with open(path, "r", encoding=functions.encoding_detect(path)) as f:
                        config = json.load(f)
                    value, model_name = config.get("model_type", ""), model.split(".")[0]
                    checked = "checked" if value == waq_name else ""
                    html.append(f'''
                        <label class="menu-link" data-name="{model_name}">
                            <input type="radio" class="waq-model-selector" name="waq-model" value="{value}" {checked}>
                            <span>{model_name}</span>
                        </label>
                    ''')
            else: html.append('<div><p>No WAQ models found</p></div>')
        else: html.append('<div><p>No WAQ models found</p></div>')    
        html.append('</div>')
        return ''.join(html)
    path = os.path.normpath(os.path.join(SOURCE_FRONTEND, "htmls", htmlFile))
    if not os.path.exists(path):
        return HTMLResponse(f"<p>Popup menu template not found</p>", status_code=404)
    # Get config from Redis, if not found scan files to get variables
    config_raw = await redis.hget(project_name, "config")
    # If config already exists → no race → render
    if config_raw:
        config = msgpack.unpackb(config_raw, raw=False)
        return htmls.TemplateResponse(request=request, name=htmlFile, context={'configuration': config})
    lock = redis.lock(f"{project_name}:init_config", timeout=10)  # 10s lock
    async with lock:
        # Create config the first time
        project_cache = request.app.state.project_cache.setdefault(project_name)
        files = [project_cache.get("hyd_his"), project_cache.get("hyd_map"),
            project_cache.get("waq_his"), project_cache.get("waq_map")]
        waq_model_raw = await redis.hget(project_name, "waq_model")
        waq_model = waq_model_raw.decode() if waq_model_raw else "unknown"
        config_obj = functions.getVariablesNames(files, waq_model)
        # Restructure configuration
        config = {**config_obj.get("hyd", {}), **config_obj.get("waq", {})}
        for k, v in config_obj.items():
            if k not in ("hyd", "waq", "meta"): config[k] = v
        # Save updated project data back to Redis
            await redis.hset(project_name, "config", msgpack.packb(config, use_bin_type=True))
    return htmls.TemplateResponse(request=request, name=htmlFile, context={'configuration': config})
