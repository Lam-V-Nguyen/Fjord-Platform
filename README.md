Fjord-Platform
A platform for AI and hydrodynamic integration.

Fjord-Platform hosts FjordFlow AI, a dashboard that visualizes hydrodynamic (HYD, D-Flow FM) and water quality (WAQ, D-Water Quality) simulation results for the active project. Auxiliary functions — data download, data preparation, grid generation and optimization, HYD/WAQ scenario creation and execution, flow estimation, calibration, and detailed visualization — are not displayed on the Overview. They open as widgets in the Workspace view (Gridstack, as in the legacy interface) when selected from the sidebar.

text
┌ Sidebar ───────────┐┌ Topbar: project · analysis window (1D/7D/30D/All) · date · Share · Filters ┐
│ Overview           ││ KPI: Surface temperature · ΔT stratification · Water level · Max velocity · WQI · Risk │
│ Map View           │├──────────────────────────────────────────────┬───────────────────────────┤
│ Workspace (n)      ││ HYD/WAQ field map on the computational grid  │ Vertical profile at station │
│ ── Modelling tools ││  · select variable + layer (surface/bottom/mean) │ Monitoring: WQI + worst │
│ Data Download      ││  · timeline + animation playback             │ pollutant (e.g. E. coli, DO)│
│ HYD ▸ … WAQ ▸ …    ││  · stations, warning zones, legend + color scale │                        │
│ Flow ▸ … Calib …   │├──────────────┬────────────┬──────────┬────────┴───────────────────────────┤
│ ── Map layers      ││ Water quality│ Station    │ Risk     │ Recent alerts                      │
│ ── System          ││ status /     │ time series│ distribution │ (click → fly to station, change variable) │
│                    ││ sources      │            │          │                                    │
└────────────────────┘└──────────────┴────────────┴──────────┴────────────────────────────────────┘
When a project has no HYD/WAQ results yet, the Overview displays the model pipeline steps (Input data → Grid → HYD run → WAQ run → Calibration). Each step is a button that opens the corresponding tool in the Workspace.

1. Data Sources
Source	File	Used for
HYD his	output/HYD/<name>_his.zarr (time, stations, laydim)	Temperature, stratification, water level, and velocity KPIs · profiles · time series · source/sink (source_sink_*)
HYD map	output/HYD/<name>_map.zarr (mesh2d_*, time, nFaces, nLayers)	Computational grid + fields mesh2d_tem1, mesh2d_sa1, mesh2d_ucmag, mesh2d_s1, mesh2d_waterdepth
WAQ his	output/WAQ/<waq>_his.zarr (nTimesDlwq, nStations, nLayers)	Threshold comparison → checks, WQI, alerts · time series · profiles
WAQ map	output/WAQ/<waq>_map.zarr (nTimesDlwq, nLayers, nFaces)	Pollutant fields on the map (mesh2d_<substance>, mean mesh2d_2d_<substance>)
Active WAQ scenario	output/config/config.json (model_name) or the first .zarr file in output/WAQ	
Context	backend/src/flow_samples/*.geojson, samples/depth.csv, <project>/GIS/*.geojson	Lake boundaries, catchment, rivers, depth, project GIS layers
Data dimensions are auto-detected. Layer ordering follows Delft3D conventions:

HYD sigma layers are numbered from bottom to surface, so the surface layer is the last index.

WAQ layers are numbered from surface to bottom, so the surface layer is the first index.

Coordinates are converted to WGS84 using projected_coordinate_system.EPSG_code. Datasets and grids are cached based on file modification time, so the dashboard updates automatically when a simulation is re-run.

Trying the platform without Delft3D: run python backend/tools/make_demo_results.py <PROJECT_ROOT>/<user>/<project>. This generates a synthetic result set with the same structure as real results: a triangular grid over Brusdalsvatnet, 30 days, 8 layers, E. coli and DO. The dashboard labels this result set as "synthetic".

2. API /dashboard/*
All endpoints are POST and use Basic Auth. Common body: {"projectName", "end", "period": "1d|7d|30d|90d|all"}. The analysis window is always constrained to the simulation time range.

Endpoint	Returns
catalog	HYD/WAQ scenario metadata, map and station variables, stations (WGS84), sources/sinks, timestamps, version
summary	kpis, gauges, quality (worst value vs. threshold), contributions (sources/sinks), checks, risk_distribution, inventory.pipeline, simulation
alerts	Sorted alerts (limit, severities)
mesh	Grid GeoJSON (feature order = cell index)
field	{source, name, layer, time} → per-cell value array, min/max (P2–P98 over the full time range)
station	{source, name, layer, stations?} → time series at stations
profile	{source, name, station, time} → values by depth
layers	Context layers
thresholds	Thresholds; {"reload": true} reloads the file and clears the cache
Thresholds are defined in backend/src/dashboard_thresholds.json. Keys are WAQ substance names without the mesh2d_ prefix. The physics section covers surface temperature and velocity. These are illustrative thresholds and must be adjusted to the applicable regulations and the model's units.

3. Source Code Structure
text
backend/
  app/main.py                          router registration
  app/services/
    dashboard_results.py               READ HYD/WAQ results (ProjectResults, catalog, mesh, field, station, profile)
    dashboard_functions.py             AGGREGATE: thresholds, pipeline inventory, KPIs, checks, WQI, risk, alerts, context layers
    dashboard_router.py                /dashboard/* API + cache (Redis or in-memory, keyed by file version)
    <tool>_router.py / <tool>_functions.py   existing tools (hyd, waq, flow, grid, calibration, data…)
  src/dashboard_thresholds.json        alert thresholds
  tools/make_demo_results.py           generates synthetic results for development
frontend/
  index.html                           shell: sidebar · topbar · Overview/Map views · Workspace view
  src/services/
    mainManager.js                     view navigation, sidebar, widget opening, postMessage bus with iframes
    toolRegistry.js                    TOOL CATALOG → generates sidebar + widget dimensions
    dashboardManager.js                Overview state & rendering
    dashboardMap.js                    map: grid + fields, stations, alerts, context layers, legend
    dashboardCharts.js                 Plotly: gauge, donut, station time series, profile
    icons.js                           SVG icons
    <tool>.js / <tool>Manager.js       per-tool logic (unchanged)
  src/htmls/<tool>.html                per-tool UI (runs in an iframe)
  src/styles/theme.css · layout.css · dashboard.css
4. Extending the Platform
Each feature is a module with three parts, wired into the platform through two registration points. No changes to mainManager.js or dashboardManager.js are required.

4.1. Adding a New Tool (opened in the Workspace)
Example: a "Scenario Comparison" tool with id compare.

Backend: create backend/app/services/compare_functions.py (pure computation, no FastAPI dependency) and compare_router.py:

python
from fastapi import APIRouter, Request, Depends
from services import functions, compare_functions as cf
router = APIRouter(prefix="/compare", tags=["compare"])

@router.post("/run")
async def run(request: Request, user=Depends(functions.basic_auth)):
    body = await request.json()
    project, _ = functions.project_definer(body.get("projectName"), user)
    return {"status": "ok", "content": cf.compare(project, body)}
Then register it in backend/app/main.py: add compare_router to the import block and compare_router.router to all_routers.

Frontend: create frontend/src/htmls/compare.html (the page running inside the iframe), frontend/src/services/compare.js, and, if needed, frontend/src/styles/compare.css. In compare.js, reuse the existing helpers:

jsonLoader('compare/run', {...}) to call the API;

getUser() to obtain the current project (via GET_USER);

signalSender('showOverlay', 'Running…') / signalSender('hideOverlay') to toggle the loading overlay;

window.parent.postMessage({type: 'addMapWidget', content: {id: 'compare-map', title: 'Map'}}, '*') to open an accompanying map;

window.parent.postMessage({type: 'refreshDashboard'}, '*') after new results are produced, so the Overview refreshes automatically.

Register in the sidebar (toolRegistry.js):

js
{ id: 'compare', title: 'Scenario Comparison', url: '/src_frontend/htmls/compare.html', w: 12, h: 10, icon: 'chart' }
Add it to an existing group (e.g., waq) or create a new group { id, title, icon, tools: [...] }. The sidebar, "open" state, quick-access buttons, and duplicate-open prevention are all handled automatically.

4.2. Surfacing Tool Results on the Overview
New pipeline step: add an element to pipeline in project_inventory() with {"key", "label", "done", "tool": "<tool id>"}. The step appears at the bottom of the sidebar and on the empty-state screen; clicking it opens the tool.

New KPI: add an element to the kpis list in summarize() with the keys key, label, value, unit, decimals, icon, status | delta. The frontend renders it automatically; card colors are defined in KPI_TONES.

New alert indicator: simply add a key to dashboard_thresholds.json. Any WAQ substance with a matching name is automatically evaluated, rendered as a status bar with a threshold line, and included in alerts.

New HYD map variable: add it to HYD_MAP_VARS / HYD_HIS_VARS in dashboard_results.py. WAQ variables are detected automatically.

New context map layer: return an additional key in build_layers() and declare it in LAYER_DEFS (dashboardMap.js). Files under <project>/GIS/*.geojson are displayed automatically.

Dedicated overview panel/page for a module (e.g., "Flow Overview"): create a /<module>/summary endpoint returning JSON of the same {kpis, ...} shape, add a new view in index.html, and a data-view entry in the sidebar. You can reuse renderKpis, renderStationSeries, renderProfile, and DashboardMap.

4.3. Conventions to Follow
Backend: <module>_router.py only receives requests, checks permissions (basic_auth), and calls <module>_functions.py. Heavy computation runs via asyncio.to_thread. Errors are returned as {"status": "error", "message"}.

Module outputs: write results to <project>/output/<MODULE>/ with clear naming so the Overview and other modules can read them.

Frontend: each tool is an independent iframe that communicates with the shell only via postMessage. Do not modify the main page's DOM directly. Add new message types to updateComponent() in mainManager.js.

UI: use variables from theme.css (--panel, --accent, --sev-*, …) instead of hard-coded colors so all modules stay consistent with the theme.










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

Khi dự án **chưa có kết quả HYD/WAQ**, Overview hiển thị các bước của pipeline mô hình (Input data → Grid → HYD run → WAQ run → Calibration). Mỗi bước là một nút mở đúng công cụ trong Workspace.

## 1. Dữ liệu được đọc

| Nguồn | File | Dùng cho |
|---|---|---|
| HYD his | `output/HYD/<name>_his.zarr` (`time, stations, laydim`) | KPI nhiệt độ, phân tầng, mực nước, vận tốc · profile · chuỗi thời gian · nguồn/xả (`source_sink_*`) |
| HYD map | `output/HYD/<name>_map.zarr` (`mesh2d_*`, `time, nFaces, nLayers`) | Lưới tính toán + trường `mesh2d_tem1`, `mesh2d_sa1`, `mesh2d_ucmag`, `mesh2d_s1`, `mesh2d_waterdepth` |
| WAQ his | `output/WAQ/<waq>_his.zarr` (`nTimesDlwq, nStations, nLayers`) | So ngưỡng → checks, WQI, cảnh báo · chuỗi thời gian · profile |
| WAQ map | `output/WAQ/<waq>_map.zarr` (`nTimesDlwq, nLayers, nFaces`) | Trường chất trên bản đồ (`mesh2d_<chất>`, trung bình `mesh2d_2d_<chất>`) |
| Kịch bản WAQ đang chọn | `output/config/config.json` (`model_name`) hoặc file `.zarr` đầu tiên trong `output/WAQ` | |
| Ngữ cảnh | `backend/src/flow_samples/*.geojson`, `samples/depth.csv`, `<project>/GIS/*.geojson` | Ranh giới hồ, lưu vực, sông, độ sâu, lớp GIS của dự án |

Chiều dữ liệu được tự nhận dạng. Thứ tự lớp theo quy ước Delft3D:
- HYD sigma đánh số từ đáy lên mặt, nên lớp mặt là lớp cuối.
- WAQ đánh số từ mặt xuống đáy, nên lớp mặt là lớp đầu.

Toạ độ được chuyển sang WGS84 theo `projected_coordinate_system.EPSG_code`. Dataset và lưới được cache theo thời điểm sửa file, nên khi chạy lại mô phỏng dashboard tự cập nhật.

**Thử khi chưa có Delft3D:** chạy `python backend/tools/make_demo_results.py <PROJECT_ROOT>/<user>/<project>`. Lệnh này tạo bộ kết quả **tổng hợp**, cùng cấu trúc với kết quả thật: lưới tam giác trong Brusdalsvatnet, 30 ngày, 8 lớp, E. coli và DO. Dashboard sẽ gắn nhãn "synthetic" cho bộ kết quả này.

## 2. API `/dashboard/*`

Tất cả là `POST` và dùng Basic Auth. Body chung: `{"projectName", "end", "period": "1d|7d|30d|90d|all"}`. Cửa sổ phân tích luôn nằm trong thời gian mô phỏng.

| Endpoint | Trả về |
|---|---|
| `catalog` | thông tin kịch bản HYD/WAQ, biến bản đồ & biến trạm, trạm (WGS84), nguồn/xả, mốc thời gian, `version` |
| `summary` | `kpis`, `gauges`, `quality` (giá trị xấu nhất so ngưỡng), `contributions` (nguồn/xả), `checks`, `risk_distribution`, `inventory.pipeline`, `simulation` |
| `alerts` | cảnh báo đã sắp xếp (`limit`, `severities`) |
| `mesh` | GeoJSON lưới (thứ tự feature = chỉ số ô) |
| `field` | `{source, name, layer, time}` → mảng giá trị theo ô, `min/max` (P2–P98 trên toàn thời gian) |
| `station` | `{source, name, layer, stations?}` → chuỗi thời gian tại trạm |
| `profile` | `{source, name, station, time}` → giá trị theo độ sâu |
| `layers` | lớp ngữ cảnh |
| `thresholds` | ngưỡng; `{"reload": true}` nạp lại file và xoá cache |

Ngưỡng nằm trong `backend/src/dashboard_thresholds.json`. Khoá là tên chất WAQ không có tiền tố `mesh2d_`. Phần `physics` gồm nhiệt độ mặt và vận tốc. Đây là **ngưỡng minh hoạ**, cần chỉnh theo quy chuẩn áp dụng và đúng đơn vị của mô hình.

## 3. Cấu trúc mã nguồn

```
backend/
  app/main.py                          đăng ký router
  app/services/
    dashboard_results.py               ĐỌC kết quả HYD/WAQ (ProjectResults, catalog, mesh, field, station, profile)
    dashboard_functions.py             TỔNG HỢP: ngưỡng, kiểm kê pipeline, KPI, checks, WQI, rủi ro, cảnh báo, lớp ngữ cảnh
    dashboard_router.py                API /dashboard/* + cache (Redis hoặc bộ nhớ, khoá theo phiên bản file)
    <tool>_router.py / <tool>_functions.py   các công cụ hiện có (hyd, waq, flow, grid, calibration, data…)
  src/dashboard_thresholds.json        ngưỡng cảnh báo
  tools/make_demo_results.py           sinh kết quả tổng hợp để phát triển
frontend/
  index.html                           khung: sidebar · topbar · view Overview/Map · view Workspace
  src/services/
    mainManager.js                     điều hướng view, sidebar, mở widget, bus postMessage với iframe
    toolRegistry.js                    DANH MỤC CÔNG CỤ -> sinh sidebar + kích thước widget
    dashboardManager.js                trạng thái & render Overview
    dashboardMap.js                    bản đồ: lưới + trường, trạm, cảnh báo, lớp ngữ cảnh, chú giải
    dashboardCharts.js                 Plotly: gauge, donut, chuỗi thời gian trạm, profile
    icons.js                           icon SVG
    <tool>.js / <tool>Manager.js       logic của từng công cụ (giữ nguyên)
  src/htmls/<tool>.html                giao diện từng công cụ (chạy trong iframe)
  src/styles/theme.css · layout.css · dashboard.css
```

## 4. Phát triển thêm công cụ / chức năng

Mỗi chức năng là một **module** gồm 3 phần, ghép vào nền tảng qua **2 điểm đăng ký**. Không cần sửa `mainManager.js` hay `dashboardManager.js`.

### 4.1. Công cụ mới (mở trong Workspace)

Ví dụ: công cụ "Scenario Comparison", id `compare`.

1. **Backend**: tạo `backend/app/services/compare_functions.py` (tính toán thuần, không phụ thuộc FastAPI) và `compare_router.py`:
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
   Sau đó đăng ký trong `backend/app/main.py`: thêm `compare_router` vào phần `import` và `compare_router.router` vào `all_routers`.
2. **Frontend**: tạo `frontend/src/htmls/compare.html` (trang chạy trong iframe), `frontend/src/services/compare.js` và nếu cần `frontend/src/styles/compare.css`. Trong `compare.js`, dùng lại các hàm sẵn có:
   - `jsonLoader('compare/run', {...})` để gọi API;
   - `getUser()` để biết dự án hiện tại (qua `GET_USER`);
   - `signalSender('showOverlay', 'Running…')` / `signalSender('hideOverlay')` để bật/tắt màn hình chờ;
   - `window.parent.postMessage({type: 'addMapWidget', content: {id: 'compare-map', title: 'Map'}}, '*')` để mở bản đồ đi kèm;
   - `window.parent.postMessage({type: 'refreshDashboard'}, '*')` sau khi tạo kết quả mới, để Overview tự làm mới.
3. **Đăng ký vào sidebar** (`toolRegistry.js`):
   ```js
   { id: 'compare', title: 'Scenario Comparison', url: '/src_frontend/htmls/compare.html', w: 12, h: 10, icon: 'chart' }
   ```
   Thêm vào nhóm sẵn có (vd. `waq`), hoặc tạo nhóm mới `{ id, title, icon, tools: [...] }`. Sidebar, trạng thái "đang mở", nút nhanh và việc chống mở trùng đều được xử lý tự động.

### 4.2. Đưa kết quả của công cụ lên Overview

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

