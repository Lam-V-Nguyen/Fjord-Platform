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

