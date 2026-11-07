# HydroAI Fjord Platform (FjordFlow AI)

**AI-integrated hydrodynamic and water-quality modelling of fjord and lake systems.**

HydroAI Fjord Platform is a web application for building, running, calibrating and analysing coupled
**hydrodynamic (HYD, Delft3D FM / D-Flow FM)**, **water-quality (WAQ, D-Water Quality)** and
**catchment-hydrology (Wflow)** models. It has a FastAPI backend and a vanilla-JavaScript frontend.

The application opens on a dark **operational dashboard (Overview)** built around the HYD/WAQ
simulation results. All modelling tools open on demand in a **Workspace** of resizable widgets:
- data download
- grid generation
- HYD/WAQ scenario set-up and runs
- Wflow flow estimation
- calibration
- detailed visualization

---

## Table of contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the platform](#running-the-platform)
7. [Using the dashboard](#using-the-dashboard)
8. [Map layers: Flow movement and Wflow results](#map-layers-flow-movement-and-wflow-results)
9. [Data model and file layout](#data-model-and-file-layout)
10. [Dashboard API reference](#dashboard-api-reference)
11. [Thresholds and alerts](#thresholds-and-alerts)
12. [Synthetic demo data](#synthetic-demo-data)
13. [Extending the platform](#extending-the-platform)
14. [Troubleshooting](#troubleshooting)
15. [Changelog](#changelog)
16. [Contributing](#contributing)

---

## Features

### Overview dashboard (HYD/WAQ results first)

- **KPI row**: surface temperature, stratification ΔT, water level, maximum current speed,
  Water Quality Index (WQI) and overall risk level. Every value is computed for the selected analysis
  window (`1D / 7D / 30D / All`) and end date.
- **Interactive map** (Leaflet), on the computational mesh:
  - HYD/WAQ fields with a field picker (variable, plus surface / bottom / depth-mean layer);
  - time slider and playback;
  - observation stations (HYD and WAQ stations merged when they are less than 150 m apart);
  - alert zones and context layers (lake boundary, catchment, river network, bathymetry points, project GIS files);
  - a legend with colour scales.
- **Vertical profile** at any station. If the WAQ history file has no layer dimension, the profile
  falls back to the HYD/WAQ map file at the nearest mesh cell.
- **Environmental monitoring**: a WQI gauge plus the worst substances (e.g. *E. coli*, dissolved oxygen).
- **Water-quality status / sources**: the worst value in the window compared with thresholds, and
  source/sink contributions.
- **Station time series**, **risk distribution** and **recent alerts**. Clicking an alert flies the
  map to the station and switches the displayed variable.
- **Empty state**: when a project has no results yet, the Overview shows the modelling pipeline
  (Input data → Grid → HYD run → WAQ run → Calibration). Each step opens the corresponding tool.

### Workspace (modelling tools)

Each tool runs in an isolated iframe widget (Gridstack) and is opened from the sidebar:

| Group | Tools |
|---|---|
| Data Download | Data Download |
| Hydrodynamics (HYD) | HYD Data Preparation, Grid Generation, Create HYD Scenario, Run HYD Scenario |
| Water Quality (WAQ) | Create WAQ Scenario, Run WAQ Scenario |
| Flow Estimation | Wflow Data Preparation, Run Flow Model, Flow Estimation |
| Visualization | Visualization (time series, thermocline, simulation summary, station plots, particle movement) |
| Model Calibration | Model Calibration |

### Dashboard map layers: particles and Wflow

- **Flow movement (HYD)**: animated particles driven by the D-Flow FM velocity field. Optional speed
  or depth background raster, cursor read-out, and a settings panel.
- **Wflow results**:
  - river discharge on the river network and gridded variables (e.g. runoff) as a raster, shown at
    the same time;
  - statistic modes (max / mean / sum / time of peak / hours above a threshold);
  - **surface-flow particles**;
  - a settings panel.
- The settings panels are **floating, draggable and resizable**. Their position and size are
  remembered per layer, and closing a panel also unticks and hides its layer.

---

## Architecture

```
┌──────────────────────────── Browser ────────────────────────────┐
│ index.html                                                       │
│  ├─ Sidebar (toolRegistry.js) ─ Overview · Map · Workspace       │
│  ├─ Overview  (dashboardManager.js, dashboardMap.js, Plotly)      │
│  │    └─ extra layers: dashboardMovement.js, dashboardWflow.js    │
│  │         └─ shared engines: dashboardParticles.js,              │
│  │                            dashboardLayerSettings.js           │
│  └─ Workspace (mainManager.js + Gridstack)                        │
│        └─ <iframe> per tool  ⇄  postMessage bus                    │
└───────────────▲───────────────────────────────────▲──────────────┘
                │ REST (Basic Auth, JSON / binary)    │
┌───────────────┴──────────── FastAPI (backend/app) ┴──────────────┐
│ main.py → routers                                                 │
│  dashboard_router  → dashboard_results / dashboard_functions /    │
│                      dashboard_extras                             │
│  hyd / waq / grid / data / flow / calibration / movement / sim …  │
│ Cache: Redis (optional) or in-memory · heavy work in threads      │
└───────────────▲──────────────────────────────────────────────────┘
                │ files
┌───────────────┴──────── backend/projects/<user>/<project> ───────┐
│ output/HYD/*.zarr · output/WAQ/*.zarr · output/config/*.json      │
│ output/movement/ (particle cache) · flows/<flow>/wflow_model/     │
└───────────────────────────────────────────────────────────────────┘
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

| Goal | Where |
|---|---|
| New pipeline step | `project_inventory()` in `dashboard_functions.py`: `{"key", "label", "done", "tool": "<tool id>"}` |
| New KPI | `summarize()`: `{key, label, value, unit, decimals, icon, status \| delta}`; card colour in `KPI_TONES` |
| New alert indicator | Add a key to `dashboard_thresholds.json` |
| New HYD map/station variable | `HYD_MAP_VARS` / `HYD_HIS_VARS` in `dashboard_results.py` (WAQ variables are detected automatically) |
| New context layer | `build_layers()` plus `LAYER_DEFS` in `dashboardMap.js` (`<project>/GIS/*.geojson` is automatic) |

### 3. Add an animated map layer with a settings panel

1. Write a class with `init()`, `setTime(iso)`, `legend()` (an object or an array of legend entries),
   `show()`, `hide()`, `destroy()` and, optionally, `renderSettings(el, ctx)`, `status()` and `setPlaying(on)`.
2. For particles, reuse `ParticleField` (`dashboardParticles.js`): call
   `setGrid({nx, ny, lon0, lat0, dlon, dlat}, cells, speedMax)`, then `setFrame({u, v})`.
3. Build the settings UI with `ui.*`, `bindSettings()`, `loadSettings()` and `saveSettings()` from
   `dashboardLayerSettings.js`. `ctx` provides `refreshLegend()`, `busy(on)`, `toast(msg)` and `openTool(id)`.
4. Declare the layer in `LAYER_DEFS` with `extra: true`, and register it in `dashboardManager.init()`
   with `this.map.registerExtra(key, onToggle, onSettings)`.

### Conventions

- **Backend**: routers only parse requests, authenticate (`basic_auth`) and call `*_functions.py`.
  Heavy work runs in `asyncio.to_thread`. Module outputs go to `<project>/output/<MODULE>/`.
- **Frontend**: each tool is an independent iframe that talks to the shell only through `postMessage`.
  New message types go in `updateComponent()` in `mainManager.js`.
- **Styling**: use the tokens in `theme.css` (`--panel`, `--accent`, `--sev-*`, …), never hard-coded
  colours.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ImportError: cannot import name 'constants' from 'services'` | The dashboard imports `services.constants` optionally. Update `dashboard_results.py`. |
| Profile returns *empty* | The station is not in the selected source, or WAQ his has no layers. The map file at the nearest cell is used when available. |
| Extra layer greyed out | Hover it to see the reason: no HYD map with `ucx/ucy`, a non-WGS84 grid, or no Wflow run output. |
| No particles visible | Open the ⚙ panel and read the status line: *waiting for frame*, *paused (motion = when playing)*, *no velocity field*, … For Wflow, run **Force rebuild** once so `velocity.npy` is created. |
| `ValueError: Unknown format code 'g' for object of type 'str'` (Wflow rebuild) | In `flow_connection.build_cache`, write the version tag as `f"{int(time.time())}-{res}"`. |
| `ValueError: n_land and S_land must not be None` | These are intentionally `None` when the Wflow output contains `overland_depth`. Check them only in the branch that uses them. A velocity failure should only skip particles, not abort `build_cache`. |
| `ValueError: The truth value of an array … is ambiguous` (movement) | Do not use `coords.get("time") or …` on xarray objects; test `is None` explicitly. |
| Settings panel cannot be resized, or the grip sits mid-edge | Force-reload (Ctrl+F5). The panel frame must not scroll (`overflow: hidden`); only `.ls-scroll` scrolls. |
| Changes not visible | Hard-reload the browser (Ctrl+F5); restart `python main.py` after backend changes. |

---

## Changelog

### Dashboard UI v2: layer settings and particles (2026-11)

**Added**
- Floating **settings panels** for *Flow movement (HYD)* and *Wflow results* (⚙ next to the layer):
  draggable header, resize grips (right edge, bottom edge, corner), position and size remembered per
  layer, double-click the header to reset, a live status line.
- Closing a panel (**×**) unticks and hides its layer.
- Movement: resolution and layer rebuild, depth-labelled layers from `layers_hyd.json`, speed/depth
  background raster, particle controls, cursor read-out.
- Wflow:
  - scenario selector, and resolution placed directly below the scenario;
  - simultaneous river and grid variables;
  - statistic modes via `/dashboard/wflow/stat`, threshold, colour scale, opacity;
  - **surface-flow particles** from `velocity.npy`;
  - a note and button linking to *Flow Estimation* for point time series.
- Shared modules `dashboardParticles.js` (particle engine) and `dashboardLayerSettings.js` (settings UI).
- Endpoints `/dashboard/movement/{mask,frame}`, `/dashboard/wflow/velocity/{i}` and `/dashboard/wflow/stat/{var}/{stat}`.
- `make_demo_results.py` writes `stats_*.npz` and a synthetic `velocity.npy`.

**Changed**
- The dashboard reuses existing movement/Wflow caches and rebuilds only on an explicit change. It uses
  the same locks as the Visualization and Flow Estimation tools.
- The particle engine now uses bilinear sampling, sub-stepping and wet-cell respawning, matching the
  Flow Estimation viewer, so particles travel along channels instead of stopping after a few pixels.
- HYD layer convention unified (`normalize_layer`): `-1` = depth mean, `k` counted from the surface.
- Wflow data URLs carry the cache version (no stale browser cache after a rebuild).
- Movement particle opacity removed; the background raster keeps its own opacity.

**Fixed**
- Visualization movement layer index was inverted (*Average* produced the surface, *surface* produced the bottom).
- Wflow rebuild with automatic resolution failed in `build_cache` (`res=None` / `"auto"` formatting).
- A Wflow velocity failure no longer aborts the whole viewer cache.
- Movement frames are read directly from `frames.npy`, with no dependency on the movement router's
  in-memory cache and no file locks on Windows.
- Particles now appear when a layer is enabled before the map has a size.

### Dashboard UI v1 (2026-11)

- New dark operational dashboard focused on HYD/WAQ results (KPIs, map, profile, monitoring, quality
  status, station series, risk distribution, alerts).
- Sidebar driven by `toolRegistry.js`; tools open as Workspace widgets; *HYD Data Preparation* and
  *Wflow Data Preparation* labelled separately.
- Project dialog centred on screen; WAQ station coordinates taken from `wq_obs`; profile fallback to map files.
- Movement and Wflow map layers, greyed out when no data exists.
- `/dashboard/*` API, threshold file, synthetic demo generator.

---

## Contributing

1. Branch from `dev` (`git checkout -b feature/<name>`).
2. Follow the module conventions in [Extending the platform](#extending-the-platform).
3. Test with a real project and with the synthetic demo project.
4. Open a pull request against `dev` with a short description and screenshots of UI changes.
