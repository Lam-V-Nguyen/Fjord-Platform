# Fjord-Platform
Platform for AI and Hydrodynamic Integration

# Giao diện Dashboard – FjordFlow AI

Trang **Overview** hiển thị **kết quả mô phỏng thủy động lực (HYD, D-Flow FM) và chất lượng nước (WAQ, D-Water Quality)** của dự án đang mở. Các chức năng khác như tải dữ liệu, chuẩn bị dữ liệu, tạo/tối ưu lưới, tạo/chạy kịch bản HYD/WAQ, Flow Estimation, hiệu chỉnh và trực quan hoá chi tiết **không chiếm chỗ trên Overview**. Chúng mở ra khi bấm mục tương ứng ở sidebar, dưới dạng widget trong view **Workspace** (Gridstack, như giao diện cũ).

```
┌ Sidebar ───────────┐┌ Topbar: dự án · cửa sổ phân tích (1D/7D/30D/All) · ngày · Share · Filters ┐
│ Overview           ││ KPI: Nhiệt độ mặt · ΔT phân tầng · Mực nước · Vận tốc max · WQI · Rủi ro │
│ Map View           │├──────────────────────────────────────────────┬───────────────────────────┤
│ Workspace (n)      ││ Bản đồ trường HYD/WAQ trên lưới tính toán     │ Profile thẳng đứng tại trạm│
│ ── Modelling tools ││  · chọn biến + lớp (mặt/đáy/trung bình)       │ Giám sát: WQI + chất xấu   │
│ Data Download      ││  · thanh thời gian + phát animation           │ nhất (vd. E. coli, DO)     │
│ HYD ▸ … WAQ ▸ …    ││  · trạm, vùng cảnh báo, chú giải + thang màu  │                           │
│ Flow ▸ … Calib …   │├──────────────┬────────────┬──────────┬────────┴───────────────────────────┤
│ ── Map layers      ││ Trạng thái CL│ Chuỗi thời │ Phân bố  │ Cảnh báo gần đây                   │
│ ── System          ││ nước / Nguồn │ gian trạm  │ rủi ro   │ (bấm -> bay tới trạm, đổi biến)    │
└────────────────────┘└──────────────┴────────────┴──────────┴────────────────────────────────────┘
```

---

## Requirements

| Component | Version / notes |
|---|---|
| Python | 3.11 (tested on Windows 10/11 and Linux) |
| Python packages | `backend/requirements.txt` (FastAPI, uvicorn, xarray/zarr via `hydromt-wflow`, xugrid, numba-celltree, meshkernel, pyproj, shapely, msgpack, …). `numpy==1.26.4` is required. |
| Delft3D FM / D-Water Quality | Binaries in `backend/softs/` (used by the HYD/WAQ run tools) |
| Wflow | `backend/softs/wflow 1.0.2/` (used by *Run Flow Model*) |
| WhiteboxTools | `backend/softs/whitebox/` (catchment delineation) |
| Redis | Optional. Used as a cache when a server runs on `localhost:6379`; otherwise an in-memory cache is used. |
| Browser | A recent Chrome, Edge or Firefox |

The dashboard itself needs **no extra packages** beyond `requirements.txt`.

---

## Installation

```bash
# 1. Clone the repository (development branch)
git clone -b dev https://github.com/Lam-V-Nguyen/HydroAI-Fjord-Platform.git
cd HydroAI-Fjord-Platform

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 3. Install dependencies
pip install -r backend/requirements.txt
```

Then place the external executables (Delft3D FM, Wflow, WhiteboxTools) under `backend/softs/`, as
listed in [Requirements](#requirements).

---

## Configuration

### `.env`

Create `backend/app/.env`:

```ini
ENV=development
PROJECT_DES=C:/path/to/HydroAI-Fjord-Platform
```

`PROJECT_DES` is the repository root (the folder that contains `backend/` and `frontend/`). All other
paths are derived from it in `backend/app/config.py`:

| Setting | Path |
|---|---|
| `PROJECT_ROOT` | `backend/projects` (projects live in `backend/projects/<user>/<project>`) |
| `SOURCE_BACKEND` | `backend/src` (templates, samples, thresholds) |
| `SOURCE_FRONTEND` | `frontend/src` |
| `ALLOWED_USERS` | `backend/allowed_users.json` |
| `DELFT_PATH`, `WFLOW_PATH`, `WHITEBOX_DIR` | `backend/softs/...` |

### Users

`backend/allowed_users.json` maps user names to passwords (HTTP Basic Auth):

```json
{
  "admin": "change-me",
  "alice": "another-password"
}
```

The `admin` user works on the shared `demo` project. Other users work on `backend/projects/<user>/<project>`.

---

## Running the platform

```bash
cd backend/app
python main.py
```

Open <http://localhost:8080> and sign in with a user from `allowed_users.json`.

> After updating frontend files, force-reload the page (**Ctrl+F5**) so the browser loads the new
> JavaScript/CSS instead of cached copies.

---

## Using the dashboard

### Top bar

| Control | Purpose |
|---|---|
| Project chip | Open or switch project (centred dialog) |
| `1D / 7D / 30D / All` | Analysis window used for KPIs, checks and alerts |
| Date | End of the analysis window (clamped to the simulation period) |
| Refresh / Share / Filters | Reload results, share a view, filter alerts by severity |

### Sidebar

- **Overview / Map View / Workspace**: main views. The badge on Workspace shows the number of open widgets.
- **Modelling tools**: generated from `frontend/src/services/toolRegistry.js`.
- **Map layers**: toggle the map layers. Extra layers show a **⚙** button when they are switched on.
  A layer is greyed out, with a tooltip giving the reason, when its data does not exist yet.
- **Data source / Model pipeline / Project / Last updated**: status block at the bottom.

### Map

- Choose a variable and a layer in the field picker. The legend scale can use either *all times*
  or *this frame*.
- Use the time bar to scrub or play. Extra layers (movement, Wflow) follow the same time bar.
- Click a station to see its time series and vertical profile.

---

## Map layers: Flow movement and Wflow results

Both layers read **caches that are shared with the original tools**. The dashboard never creates a
second copy of the data.

| Layer | Cache | Shared with |
|---|---|---|
| Flow movement (HYD) | `<project>/output/movement/{meta.json, frames.npy, mask.npy}` | *Visualization* tool |
| Wflow results | `<project>/flows/<flow>/wflow_model/viewer/` | *Flow Estimation* tool |

- **Display settings** (particle count, speed, trail, colours, opacity, colour scale…) are client-side
  only, stored in `localStorage`. They never affect the other tools.
- **Data settings** (resolution, HYD layer) rebuild the shared cache, so the change is visible in the
  other tool as well. The dashboard **reuses the existing cache** and only rebuilds when a value
  actually changes or **Force rebuild** is pressed. Rebuilds are serialised with the same locks the
  original tools use.

### Flow movement (HYD) settings

| Section | Controls |
|---|---|
| Data (shared with Visualization) | **Resolution (m)**: empty = auto. **Layer**: *Depth-mean*, *Surface*, and every sigma layer labelled by depth (read from `output/config/layers_hyd.json`, e.g. `Depth: -4.69 m`). **Apply** / **Force rebuild** buttons. |
| Particles | Count (1 000–20 000), speed ×0.2–4, trail length, line width, speed curve (linear / √ / log), colour (by speed / white / cyan / yellow), motion (*always* / *only when playing*) |
| Background raster | None / speed / depth, raster opacity |
| Read-out | Speed, direction and depth under the cursor, plus a live status line (particles moving, current frame) |

**Layer convention.** Layer keys follow `layers_hyd.json`:
- `-1` / `Average` / `mean` is the depth mean;
- `surface` is the top layer;
- `k ≥ 0` counts **from the surface downward** (0 = surface, n-1 = bottom).

`movement_functions.normalize_layer()` and `reduce_layers()` apply this convention for the dashboard
and the Visualization tool alike. D-Flow FM stores sigma layers bottom → top.

### Wflow results settings

| Section | Controls |
|---|---|
| Scenario | Wflow run (`flows/<flow>`); the newest ready run is selected by default |
| Resolution (shared with Flow Estimation) | Viewer grid resolution (m, empty = auto), **Apply** / **Force rebuild** |
| Variables | **River lines** variable and **Map layer** (grid) variable. Both can be shown together; either can be *none*. |
| Display | Mode: *Animate* (follows the time bar), *Max*, *Mean*, *Sum*, *Time of peak*, *Hours above* (with threshold). Colour scale: auto / linear / log. Opacity. |
| Flow particles | On/off, opacity, count, speed, trail, width, speed curve, colour. Velocity comes from `velocity.npy`, derived from overland flow / river discharge. If the run has no velocity field, the section says so. |
| Time series at a point | Note and **Open Flow Estimation** button. Use that tool to download the time series of any Wflow variable over the whole run at any point. |

**Notes:**
- Wflow and HYD/WAQ are independent simulations. Particles still follow the dashboard time bar. When
  the time bar lies outside the Wflow period, the nearest Wflow step is shown, and the legend says
  *(nearest Wflow step)*.
- Statistic modes summarise the whole run and ignore the time bar. Particles keep following it.

### Particle engine

`dashboardParticles.js` (`ParticleField`) is shared by both layers and behaves like the Flow
Estimation viewer:
- velocity is **interpolated bilinearly** from the four neighbouring cells, ignoring dry cells;
- each frame is integrated in **sub-steps** of at most half a cell, re-sampling the velocity at every
  sub-step, so particles follow channels;
- particles are respawned only in **wet cells**, and live 40–130 frames;
- the step length is about 1.5 px per frame × speed factor × curve(|v| / v<sub>max</sub>); the log curve uses `log1p(100·x)/log1p(100)`;
- the canvas is resized automatically when the map container changes size.

---

## Data model and file layout

```
backend/projects/<user>/<project>/
├─ output/
│  ├─ HYD/<name>_his.zarr        time, stations, laydim     (sigma layers bottom → top)
│  ├─ HYD/<name>_map.zarr        mesh2d_*, time, nFaces, nLayers
│  ├─ WAQ/<waq>_his.zarr         nTimesDlwq, nStations      (one surface segment per point)
│  ├─ WAQ/<waq>_map.zarr         nTimesDlwq, nLayers, nFaces (layers top → bottom)
│  ├─ config/config.json         active WAQ scenario (model_name), …
│  ├─ config/layers_hyd.json     {"-1": "Average", "0": "Depth: -0.5 m (surface)", …}
│  └─ movement/                  meta.json · frames.npy (T,3,Nv int16: u,v,depth) · mask.npy
├─ flows/<flow>/wflow_model/
│  ├─ run_default/output.nc      Wflow output
│  └─ viewer/                    meta.json · mask.npy · rivers.geojson · {kind}_{var}.npy ·
│                                stats_{var}.npz · velocity.npy (T,2,Nv float16: u,v)
└─ GIS/*.geojson                 extra context layers (shown automatically)
```

- Coordinates are converted to WGS84 using `projected_coordinate_system.EPSG_code`.
- WAQ station coordinates come from `wq_obs` in the WAQ scenario JSON.
- Datasets and meshes are cached by file modification time, so re-running a simulation refreshes
  the dashboard automatically.

---

## Dashboard API reference

All endpoints use **HTTP Basic Auth**. JSON endpoints are `POST` with the body
`{"projectName", "end", "period": "1d|7d|30d|90d|all", …}`. Errors are returned as
`{"status": "error", "message": "…"}`.

### Results

| Method & path | Description |
|---|---|
| `POST /dashboard/projects` | Projects of the current user |
| `POST /dashboard/catalog` | HYD/WAQ scenario info, map and station variables, stations (WGS84), sources/sinks, times, `profile_variables`, `version` |
| `POST /dashboard/summary` | `kpis`, `gauges`, `quality`, `contributions`, `checks`, `risk_distribution`, `inventory.pipeline`, `simulation` |
| `POST /dashboard/alerts` | Sorted alerts (`limit`, `severities`) |
| `POST /dashboard/mesh` | Mesh as GeoJSON (feature order = cell index) |
| `POST /dashboard/field` | `{source, name, layer, time}` → values per cell plus `min/max` (P2–P98 over all times) |
| `POST /dashboard/station` | `{source, name, layer, stations?}` → station time series |
| `POST /dashboard/profile` | `{source, name, station, time}` → vertical profile (`status: "empty"` with a reason when no data is available) |
| `POST /dashboard/layers` | Context layers (lake, catchment, rivers, bathymetry, project GIS) |
| `POST /dashboard/thresholds` | Thresholds; `{"reload": true}` reloads the file and clears the cache |

### Extra layers

| Method & path | Description |
|---|---|
| `POST /dashboard/extras` | Availability of the movement and Wflow layers (with reasons) |
| `POST /dashboard/movement/init` | `{projectName, res?, layer?, rebuild?}`. Loads the movement cache and rebuilds it only when needed. Returns `meta`, `frame_count`, `n_layers`, `layers` (depth labels), `rebuilt`, `reason`. |
| `GET /dashboard/movement/mask` | `mask.npy` as `uint8` (rows south → north) |
| `GET /dashboard/movement/frame/{i}` | Frame *i*: `int16` u, v, depth over the valid cells (read directly from `frames.npy`) |
| `POST /dashboard/wflow/init` | `{projectName, flowName?, res?, rebuild?}`. Returns variables, grid, times, scenarios, `velocity` info, `version`. |
| `GET /dashboard/wflow/mask` | Viewer grid mask |
| `GET /dashboard/wflow/rivers` | River segments (GeoJSON) |
| `GET /dashboard/wflow/frame/{var}/{i}` | Variable values at step *i* (`float16`/`float32`) |
| `GET /dashboard/wflow/stat/{var}/{stat}` | `max \| mean \| sum \| tmax \| above` (`threshold=`). Returns `float32`, with range in the `X-Stat-Min` / `X-Stat-Max` headers. |
| `GET /dashboard/wflow/velocity/{i}` | Surface-flow velocity at step *i*: `float16` (2, Nv), u east / v north in m/s, NaN = dry |

Binary responses are gzip-compressed when the client accepts it. Their URLs carry `v=<cache version>`,
so the browser never reuses data from a previous build.

---

## Thresholds and alerts

Thresholds are defined in `backend/src/dashboard_thresholds.json`:

```json
{
  "substances": {
    "EColi": { "unit": "MPN/m³", "medium": 100, "high": 500 },
    "OXY":   { "unit": "mg/L", "direction": "below", "medium": 6, "high": 4 }
  },
  "physics": {
    "surface_temperature": { "unit": "°C", "medium": 16, "high": 18 },
    "current_speed":       { "unit": "m/s", "medium": 0.5, "high": 1.0 }
  }
}
```

- Keys under `substances` are WAQ substance names without the `mesh2d_` prefix. Any substance that
  matches a key is evaluated automatically and gets a status bar, threshold markers and alerts.
- `"direction": "below"` is for indicators where low values are bad (e.g. dissolved oxygen).
- **The shipped values are illustrative.** Adjust them to the applicable regulations, using the units
  of your model.

---

## Synthetic demo data

To develop or test without Delft3D/Wflow runs, generate a synthetic result set **in a separate
project**:

```bash
python backend/tools/make_demo_results.py <PROJECT_ROOT>/<user>/<project-test> [options]
```

| Option | Default | Description |
|---|---|---|
| `--start` | `2024-06-01` | Simulation start |
| `--days` | `30` | Length in days |
| `--step-hours` | `3` | Output interval |
| `--layers` | `8` | Number of sigma layers |
| `--crs` | `wgs84` | `wgs84` or `utm` (the movement layer needs WGS84) |
| `--no-wflow` | – | Skip the synthetic Wflow run (`flows/demo_flow`) |
| `--force` | – | Overwrite existing results |

The script writes:
- HYD/WAQ his and map files with a UGRID mesh;
- WAQ stations with `wq_obs`;
- a Wflow viewer cache including `stats_*.npz` and a synthetic `velocity.npy`.

The dashboard labels these results as *synthetic*.

---

## Extending the platform

A feature is a **module** of three parts (backend functions, router, frontend page), connected to
the platform through **two registration points**. `mainManager.js` and `dashboardManager.js` do not
need to change.

### 1. Add a Workspace tool

**Backend**: `backend/app/services/compare_functions.py` (pure computation) and `compare_router.py`:

```python
from fastapi import APIRouter, Request, Depends
from services import functions, compare_functions as cf

router = APIRouter(prefix="/compare", tags=["compare"])

@router.post("/run")
async def run(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project, _ = functions.project_definer(body.get("projectName"), user)
    return {"status": "ok", "content": cf.compare(project, body)}
```

Register it in `backend/app/main.py`: import `compare_router` and add `compare_router.router` to `all_routers`.

**Frontend**: add `frontend/src/htmls/compare.html`, `frontend/src/services/compare.js` and, if needed,
`frontend/src/styles/compare.css`. Inside the iframe, reuse these helpers:

- `jsonLoader('compare/run', {...})`: API call;
- `getUser()`: current user/project (via the `GET_USER` message);
- `signalSender('showOverlay', 'Running…')` / `signalSender('hideOverlay')`: busy overlay;
- `window.parent.postMessage({type: 'addMapWidget', content: {id: 'compare-map', title: 'Map'}}, '*')`: open a map widget;
- `window.parent.postMessage({type: 'refreshDashboard'}, '*')`: refresh the Overview after new results are written.

**Register** the tool in `frontend/src/services/toolRegistry.js`:

```js
{ id: 'compare', title: 'Scenario Comparison', url: '/src_frontend/htmls/compare.html', w: 12, h: 10, icon: 'chart' }
```

Add it to an existing group, or create a group `{ id, title, icon, tools: [...] }`. The sidebar entry,
the "open" state, quick buttons and duplicate-open protection are handled automatically. To open a
tool programmatically from the main page, dispatch a click on any element that carries `data-tool="<id>"`.

### 2. Surface results on the Overview

- **Bước pipeline mới**: thêm phần tử vào `pipeline` trong `project_inventory()` với `{"key", "label", "done", "tool": "<id công cụ>"}`. Bước này hiện ở chân sidebar và trong màn hình trống, bấm vào sẽ mở công cụ.
- **KPI mới**: thêm phần tử vào danh sách `kpis` trong `summarize()` với các khoá `key, label, value, unit, decimals, icon, status | delta`. Frontend tự render; màu thẻ đặt ở `KPI_TONES`.
- **Chỉ tiêu cảnh báo mới**: chỉ cần thêm khoá vào `dashboard_thresholds.json`. Chất WAQ nào trùng tên sẽ tự được đánh giá, vẽ thanh trạng thái, vạch ngưỡng và đưa vào cảnh báo.
- **Biến bản đồ HYD mới**: thêm vào `HYD_MAP_VARS` / `HYD_HIS_VARS` trong `dashboard_results.py`. Biến WAQ thì tự nhận dạng.
- **Lớp bản đồ ngữ cảnh mới**: trả thêm khoá trong `build_layers()` và khai báo trong `LAYER_DEFS` (`dashboardMap.js`). Riêng file `<project>/GIS/*.geojson` thì tự hiển thị.
- **Panel/trang tổng quan riêng cho một module** (vd. "Flow Overview"): tạo endpoint `/<module>/summary` trả JSON cùng dạng `{kpis, ...}`, thêm một view mới trong `index.html` và một mục `data-view` ở sidebar. Có thể dùng lại `renderKpis`, `renderStationSeries`, `renderProfile`, `DashboardMap`.

### 4.3. Quy ước nên giữ

- Backend: `<module>_router.py` chỉ nhận request, kiểm quyền (`basic_auth`), gọi `<module>_functions.py`. Tính toán nặng chạy bằng `asyncio.to_thread`. Lỗi trả về dạng `{"status": "error", "message"}`.
- Kết quả của module ghi vào `<project>/output/<MODULE>/`, đặt tên rõ ràng, để Overview và các module khác đọc được.
- Frontend: mỗi công cụ là một iframe độc lập, chỉ giao tiếp với khung qua `postMessage`. Không sửa trực tiếp DOM của trang chính. Thêm loại message mới vào `updateComponent()` trong `mainManager.js`.
- Giao diện: dùng biến trong `theme.css` (`--panel`, `--accent`, `--sev-*`…) thay vì mã màu cứng, để mọi module đồng bộ theme.

