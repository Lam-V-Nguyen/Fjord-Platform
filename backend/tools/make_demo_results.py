"""
make_demo_results.py
====================
Sinh bộ kết quả HYD/WAQ *tổng hợp* (không phải kết quả mô phỏng thật) với cấu trúc
giống D-Flow FM / D-Water Quality đã chuyển sang Zarr, để phát triển và thử Dashboard
khi chưa chạy được Delft3D.

    python backend/tools/make_demo_results.py <PROJECT_DIR> [--days 30] [--step-hours 3] [--layers 8]

Tạo ra:
    <PROJECT_DIR>/output/HYD/FlowFM_his.zarr     (time, stations, laydim) + source_sink
    <PROJECT_DIR>/output/HYD/FlowFM_map.zarr     mesh2d_* (time, mesh2d_nFaces, mesh2d_nLayers)
    <PROJECT_DIR>/output/WAQ/demo_waq_his.zarr   (nTimesDlwq, nStations, nLayers)
    <PROJECT_DIR>/output/WAQ/demo_waq_map.zarr   mesh2d_* (nTimesDlwq, mesh2d_nLayers, mesh2d_nFaces)
    <PROJECT_DIR>/output/WAQ/demo_waq.json       {"model_type": "coliform", ...}

Lưới: tam giác Delaunay từ điểm độ sâu mẫu (backend/src/samples/depth.csv) nằm trong
ranh giới Brusdalsvatnet, toạ độ lưu ở EPSG:25833 (như dự án thật).
"""
import argparse, json, os, sys

import numpy as np
import pandas as pd
import xarray as xr
from pyproj import Transformer
from scipy.spatial import Delaunay
from shapely.geometry import Point, shape
from shapely.prepared import prep

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "src"))


def build_mesh(n_points=2600, seed=3):
    lake = json.load(open(os.path.join(SRC, "flow_samples", "BrusdalLake.geojson"), encoding="utf-8-sig"))
    poly = shape(lake["features"][0]["geometry"])
    poly2 = prep(poly)
    depth = pd.read_csv(os.path.join(SRC, "samples", "depth.csv")).dropna()
    depth = depth.sample(min(n_points, len(depth)), random_state=seed)
    lon, lat, z = depth["Lon"].values, depth["Lat"].values, np.abs(depth["Depth"].values)
    tf = Transformer.from_crs("EPSG:4326", "EPSG:25833", always_xy=True)
    x, y = tf.transform(lon, lat)
    tri = Delaunay(np.column_stack([x, y]))
    keep = []
    for t in tri.simplices:
        cx, cy = lon[t].mean(), lat[t].mean()
        edge = max(np.hypot(*(np.array([x[t[i]], y[t[i]]]) - np.array([x[t[(i + 1) % 3]], y[t[(i + 1) % 3]]]))) for i in range(3))
        if edge < 260 and poly2.contains(Point(cx, cy)):
            keep.append(t)
    faces = np.array(keep)
    face_x, face_y = x[faces].mean(axis=1), y[faces].mean(axis=1)
    face_depth = np.maximum(1.0, z[faces].mean(axis=1))
    return dict(node_x=x, node_y=y, faces=faces, face_x=face_x, face_y=face_y, face_depth=face_depth,
                lon=lon, lat=lat)


def thermal(depth, t_frac, layer_frac):
    """Nhiệt độ: nóng dần theo thời gian ở lớp mặt, thermocline ~ 8–12 m."""
    surface = 9.0 + 8.0 * t_frac
    bottom = 4.6 + 0.4 * t_frac
    zc = 9.0 + 3.0 * t_frac
    zl = depth * (1 - layer_frac)  # độ sâu của tâm lớp (layer_frac=1 -> mặt)
    return bottom + (surface - bottom) / (1 + np.exp((zl - zc) / 1.6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_dir")
    ap.add_argument("--start", default="2024-06-01")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--step-hours", type=int, default=3)
    ap.add_argument("--layers", type=int, default=8)
    a = ap.parse_args()

    rng = np.random.default_rng(42)
    mesh = build_mesh()
    nF, nL = len(mesh["faces"]), a.layers
    times = pd.date_range(a.start, periods=int(a.days * 24 / a.step_hours) + 1, freq=f"{a.step_hours}h")
    nT = len(times)
    tfrac = np.linspace(0, 1, nT)
    lfrac = (np.arange(nL) + 0.5) / nL                      # 0 = đáy ... 1 = mặt (sigma, như D-Flow FM)
    print(f"mesh faces={nF}, times={nT}, layers={nL}")

    hyd_dir = os.path.join(a.project_dir, "output", "HYD")
    waq_dir = os.path.join(a.project_dir, "output", "WAQ")
    os.makedirs(hyd_dir, exist_ok=True)
    os.makedirs(waq_dir, exist_ok=True)
    crs_attrs = {"EPSG_code": "EPSG:25833", "name": "ETRS89 / UTM zone 33N"}

    # ------------------------------------------------------------------ HYD map
    D = mesh["face_depth"]
    tem = np.empty((nT, nF, nL), np.float32)
    for k in range(nL):
        tem[:, :, k] = thermal(D[None, :], tfrac[:, None], lfrac[k]) + rng.normal(0, 0.05, (nT, nF))
    # gió: mặt hồ phía tây ấm hơn chút
    west = (mesh["face_x"] - mesh["face_x"].min()) / np.ptp(mesh["face_x"])
    tem[:, :, -1] += (0.6 * (1 - west))[None, :]
    sal = np.full((nT, nF, nL), 0.02, np.float32)
    diurnal = np.sin(2 * np.pi * (times.hour.values / 24.0))
    ucm = np.abs(0.03 + 0.05 * np.sin(west * 9)[None, :, None] * (0.6 + 0.4 * diurnal[:, None, None])
                 * lfrac[None, None, :]).astype(np.float32)
    s1 = (25.40 + 0.06 * np.sin(np.linspace(0, 6, nT))[:, None] + np.zeros((1, nF))).astype(np.float32)
    nmax = 3
    face_nodes = (mesh["faces"] + 1).astype(np.float64)
    hyd_map = xr.Dataset(
        {
            "mesh2d_node_x": (("mesh2d_nNodes",), mesh["node_x"]),
            "mesh2d_node_y": (("mesh2d_nNodes",), mesh["node_y"]),
            "mesh2d_face_nodes": (("mesh2d_nFaces", "mesh2d_nMax_face_nodes"), face_nodes),
            "mesh2d_face_x": (("mesh2d_nFaces",), mesh["face_x"]),
            "mesh2d_face_y": (("mesh2d_nFaces",), mesh["face_y"]),
            "mesh2d_s1": (("time", "mesh2d_nFaces"), s1),
            "mesh2d_waterdepth": (("time", "mesh2d_nFaces"), (D[None, :] + (s1 - 25.40)).astype(np.float32)),
            "mesh2d_tem1": (("time", "mesh2d_nFaces", "mesh2d_nLayers"), tem),
            "mesh2d_sa1": (("time", "mesh2d_nFaces", "mesh2d_nLayers"), sal),
            "mesh2d_ucmag": (("time", "mesh2d_nFaces", "mesh2d_nLayers"), ucm),
            "projected_coordinate_system": ((), 0, crs_attrs),
        },
        coords={"time": times},
    )
    assert nmax == face_nodes.shape[1]
    hyd_map.to_zarr(os.path.join(hyd_dir, "FlowFM_map.zarr"), mode="w")

    # ------------------------------------------------------------------ stations
    obs = pd.read_csv(os.path.join(SRC, "samples", "obsPoints.csv"))
    extra = pd.DataFrame({"Name": ["East basin", "Mid lake"], "Lat": [62.4800, 62.4770], "Long": [6.5250, 6.4900]})
    st = pd.concat([obs, extra], ignore_index=True)
    tf = Transformer.from_crs("EPSG:4326", "EPSG:25833", always_xy=True)
    sx, sy = tf.transform(st["Long"].values, st["Lat"].values)
    # trạm -> ô lưới gần nhất
    idx = [int(np.argmin(np.hypot(mesh["face_x"] - px, mesh["face_y"] - py))) for px, py in zip(sx, sy)]
    nS = len(st)
    names = np.array([n.encode() for n in st["Name"]], dtype="S40")
    zc = np.empty((nT, nS, nL), np.float32)
    for j, f in enumerate(idx):
        zc[:, j, :] = s1[:, f][:, None] - (D[f] * (1 - lfrac))[None, :]   # cùng mốc cao độ với waterlevel

    # nguồn/xả từ samples/sources
    src_names, src_q = [], []
    for fn in sorted(os.listdir(os.path.join(SRC, "samples", "sources"))):
        df = pd.read_csv(os.path.join(SRC, "samples", "sources", fn), skiprows=1)
        tcol = df.columns[0]
        df[tcol] = pd.to_datetime(df[tcol])
        q = df.drop_duplicates(tcol).set_index(tcol).sort_index().iloc[:, 0].reindex(times, method="nearest")
        src_names.append(os.path.splitext(fn)[0].encode())
        src_q.append(q.values)
    src_q = np.array(src_q, np.float32).T

    hyd_his = xr.Dataset(
        {
            "station_name": (("stations",), names),
            "station_x_coordinate": (("stations",), sx),
            "station_y_coordinate": (("stations",), sy),
            "temperature": (("time", "stations", "laydim"), tem[:, idx, :]),
            "salinity": (("time", "stations", "laydim"), sal[:, idx, :]),
            "velocity_magnitude": (("time", "stations", "laydim"), ucm[:, idx, :]),
            "waterlevel": (("time", "stations"), s1[:, idx]),
            "zcoordinate_c": (("time", "stations", "laydim"), zc),
            "source_sink_name": (("source_sink",), np.array(src_names, dtype="S40")),
            "source_sink_discharge_average": (("time", "source_sink"), src_q),
            "projected_coordinate_system": ((), 0, crs_attrs),
        },
        coords={"time": times},
    )
    hyd_his.to_zarr(os.path.join(hyd_dir, "FlowFM_his.zarr"), mode="w")

    # ------------------------------------------------------------------ WAQ (coliform + oxygen)
    # WAQ: lớp 0 = mặt (DELWAQ đánh số từ trên xuống)
    lf_top = lfrac[::-1]
    s1x, s1y = tf.transform(6.395315, 62.465004)   # nguồn S1
    dist = np.hypot(mesh["face_x"] - s1x, mesh["face_y"] - s1y)
    pulse = np.exp(-((tfrac - 0.55) / 0.08) ** 2)       # đợt mưa giữa kỳ
    ecoli = np.empty((nT, nL, nF), np.float32)
    oxy = np.empty((nT, nL, nF), np.float32)
    for k in range(nL):
        ecoli[:, k, :] = (5 + 900 * pulse[:, None] * np.exp(-dist / 900.0)[None, :] * lf_top[k] ** 2)
        oxy[:, k, :] = (10.8 - 0.6 * tfrac[:, None] - 4.5 * (1 - lf_top[k]) * tfrac[:, None]
                        * (D[None, :] / D.max()) + rng.normal(0, 0.05, (nT, nF)))
    waq_map = xr.Dataset(
        {
            "mesh2d_node_x": (("mesh2d_nNodes",), mesh["node_x"]),
            "mesh2d_node_y": (("mesh2d_nNodes",), mesh["node_y"]),
            "mesh2d_face_nodes": (("mesh2d_nFaces", "mesh2d_nMax_face_nodes"), face_nodes),
            "mesh2d_face_x": (("mesh2d_nFaces",), mesh["face_x"]),
            "mesh2d_face_y": (("mesh2d_nFaces",), mesh["face_y"]),
            "mesh2d_EColi": (("nTimesDlwq", "mesh2d_nLayers", "mesh2d_nFaces"), ecoli),
            "mesh2d_OXY": (("nTimesDlwq", "mesh2d_nLayers", "mesh2d_nFaces"), oxy),
            "mesh2d_2d_EColi": (("nTimesDlwq", "mesh2d_nFaces"), ecoli.mean(axis=1)),
            "mesh2d_2d_OXY": (("nTimesDlwq", "mesh2d_nFaces"), oxy.mean(axis=1)),
            "mesh2d_layer_dlwq": (("mesh2d_nLayers",), lf_top),
            "projected_coordinate_system": ((), 0, crs_attrs),
        },
        coords={"nTimesDlwq": times},
    )
    waq_map.to_zarr(os.path.join(waq_dir, "demo_waq_map.zarr"), mode="w")
    waq_his = xr.Dataset(
        {
            "station_name": (("nStations",), names),
            "station_x": (("nStations",), sx),
            "station_y": (("nStations",), sy),
            "EColi": (("nTimesDlwq", "nStations", "nLayers"), np.transpose(ecoli[:, :, idx], (0, 2, 1))),
            "OXY": (("nTimesDlwq", "nStations", "nLayers"), np.transpose(oxy[:, :, idx], (0, 2, 1))),
        },
        coords={"nTimesDlwq": times},
    )
    waq_his.to_zarr(os.path.join(waq_dir, "demo_waq_his.zarr"), mode="w")
    json.dump({"model_type": "coliform", "model_name": "demo_waq", "synthetic": True},
              open(os.path.join(waq_dir, "demo_waq.json"), "w"), indent=2)
    print("done ->", a.project_dir)


if __name__ == "__main__":
    sys.exit(main())
