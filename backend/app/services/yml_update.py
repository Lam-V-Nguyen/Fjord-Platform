import os, math, glob, re, rasterio, pyogrio
import xarray as xr, numpy as np
from pyproj import CRS
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

TARGET_UNITS = {
    "precip": "mm", "temp": "degC", "press_msl": "hPa", "kin": "W/m^2", "kout": "W/m^2",
    "bd": "g/cm^3", "oc": "%", "ph": "-", "clyppt": "%", "sltppt": "%", "sndppt": "%", "soilthickness": "cm",
}
CONVERSIONS = {
    ("m", "mm"): (1000, 0), ("kg/m-2", "mm"): (1, 0), ("K", "degC"): (1, -273.15),
    ("Pa", "hPa"): (0.01, 0), ("kPa", "hPa"): (10, 0),
    ("kg/m3", "g/cm3"): (0.001, 0), ("cg/cm3", "g/cm3"): (0.01, 0),
    ("g/kg", "%"): (0.1, 0), ("dg/kg", "%"): (0.01, 0),
    ("pHx10", "-"): (0.1, 0), ("mm", "cm"): (0.1, 0), ("m", "cm"): (100, 0),
}
_ALIASES = {"degc": "degC", "°c": "degC", "c": "degC", "celsius": "degC", "k": "K",
    "w/m^2": "W/m^2", "w/m2": "W/m^2", "w m-2": "W/m^2", "j/m^2": "J/m^2", "j/m2": "J/m^2",
    "j m-2": "J/m^2", "kg/m2": "kg/m^2", "kg m**2": "kg/m^2", "pa": "Pa", "hpa": "hPa",
    "kg/m-2": "kg/m^-2", "kg m**-2": "kg/m^-2",
    "kpa": "kPa", "kg/m3": "kg/m^3", "kg m-3": "kg/m3", "g/cm3": "g/cm^3", "ph": "-", "1": "-"
}

def _norm_unit(u):
    return _ALIASES.get(str(u).strip().lower(), str(u).strip()) if u is not None else None

def _target(var):
    if var in TARGET_UNITS: return TARGET_UNITS[var]
    return TARGET_UNITS.get(var.split("_")[0])
 
def _conversion(var, unit, dt=None):
    target, unit = _target(var), _norm_unit(unit)
    if target is None or unit is None: return None
    if unit == target: return (1, 0)
    if unit == "J m-2" and target == "W m-2" and dt: return (1 / dt, 0)
    return CONVERSIONS.get((unit, target))

def _crs_value(crs):
    if crs is None: return None
    crs = CRS.from_user_input(crs)
    epsg = crs.to_epsg()
    return int(epsg) if epsg else crs.to_wkt()

def _py(v):
    if v is None: return None
    v = v.item() if hasattr(v, "item") else v
    if isinstance(v, float) and math.isnan(v): return float("nan")
    return int(v) if isinstance(v, float) and v.is_integer() else v

def _nodata_value(nodatas):
    vals = {k: _py(v) for k, v in nodatas.items() if v is not None}
    if not vals: return None
    uniq = {"nan" if isinstance(v, float) and math.isnan(v) else v for v in vals.values()}
    return next(iter(vals.values())) if len(uniq) == 1 and len(vals) == len(nodatas) else vals

def _resolve(root, uri):
    if "{variable}" not in uri:
        p = os.path.join(root, uri)
        return {None: p} if os.path.exists(p) else {}
    pattern = os.path.join(root, uri.replace("{variable}", "*"))
    regex = re.compile(re.escape(os.path.normpath(os.path.join(root, uri))).replace(r"\{variable\}", "(.+)") + "$")
    out = {}
    for p in sorted(glob.glob(pattern)):
        m = regex.match(os.path.normpath(p))
        if m: out[m.group(1)] = p
    return out

def _remove(container, key):
    if key not in container: return
    token = _trailing(container, key)
    keys = list(container)
    container.pop(key)
    if token is not None and keys.index(key) == len(keys) - 1 and len(container):
        _attach(container, list(container)[-1], token)

def _trailing(container, key):
    v = container[key]
    if isinstance(v, CommentedMap) and len(v): return _trailing(v, list(v)[-1])
    return container.ca.items.pop(key, None)
 
def _attach(container, key, token):
    if token is None: return
    v = container[key]
    if isinstance(v, CommentedMap) and len(v): _attach(v, list(v)[-1], token)
    else: container.ca.items[key] = token
 
def _put(container, key, value, pos=None):
    value = CommentedMap(value) if isinstance(value, dict) and not isinstance(value, CommentedMap) else value
    if key in container:
        token = _trailing(container, key)
        container[key] = value
        _attach(container, key, token)
    else: container.insert(len(container) if pos is None else pos, key, value)

def _set_metadata(entry, **kv):
    if "metadata" not in entry: _put(entry, "metadata", CommentedMap())
    md = entry["metadata"]
    for k, v in kv.items():
        if v is None: _remove(md, k)
        else:
            anchors = [i for i, key in enumerate(md) if key in ("category", "crs")]
            _put(md, k, v, pos=anchors[-1] + 1 if anchors else 0)

def _inspect_rasterio(files, missing:list):
    crs_set, nodatas, units = set(), {}, {}
    for var, p in files.items():
        with rasterio.open(p) as src:
            crs_set.add(_crs_value(src.crs))
            nodatas[var or "_"] = src.nodata
            units[var or "_"] = src.tags().get("units")
    if len(crs_set) > 1:
        crs = None
        missing.append(f"- The files have different CRS: {crs_set}")
    else: crs = next(iter(crs_set), None)
    nodata = _nodata_value(nodatas)
    if isinstance(nodata, dict) and "_" in nodata: nodata = nodata["_"]
    return crs, nodata, units, None

def _inspect_netcdf(files, _):
    path = next(iter(files.values()))
    with xr.open_dataset(path, decode_coords="all") as ds:
        crs = ds.rio.crs
        if crs is None:
            for crs_var in ("spatial_ref", "crs"):
                if crs_var not in ds.variables: continue
                attrs = ds[crs_var].attrs
                crs_value = (attrs.get("spatial_ref") or attrs.get("crs_wkt") or attrs.get("epsg_code"))
                if crs_value: crs = crs_value; break
        if crs is None:
            for var in ds.data_vars:
                grid_mapping = ds[var].attrs.get("grid_mapping")
                if grid_mapping and grid_mapping in ds.variables:
                    attrs = ds[grid_mapping].attrs
                    crs_value = (attrs.get("spatial_ref") or attrs.get("crs_wkt") or attrs.get("epsg_code"))
                    if crs_value: crs = crs_value; break
        data_vars, nodatas = [v for v in ds.data_vars if ds[v].ndim >= 2], {}
        for v in data_vars:
            nodata = ds[v].encoding.get("_FillValue")
            if nodata is None: nodata = ds[v].attrs.get("_FillValue")
            nodatas[v] = nodata
        units = {v: ds[v].attrs.get("units") for v in data_vars}
        dt = None
        if "time" in ds.coords and ds.sizes.get("time", 0) > 1:
            t = ds["time"].values
            dt = float((t[1] - t[0]) / np.timedelta64(1, "s"))
    return _crs_value(crs), _nodata_value(nodatas), units, dt

def _inspect_vector(files, _):
    info = pyogrio.read_info(next(iter(files.values())))
    return _crs_value(info["crs"]), None, {}, None

def _update_units(entry, units, dt, missing:list):
    adapter = entry.get("data_adapter") or CommentedMap()
    mult = dict(adapter.get("unit_mult") or {})
    add = dict(adapter.get("unit_add") or {})
    assumed = set()
    for var, unit in units.items():
        if _target(var) is None: continue
        conv = _conversion(var, unit, dt)
        if conv is None:
            if unit is not None: missing.append(f"- {var}: cannot convert '{unit}' -> '{_target(var)}'")
            elif var not in mult and var not in add: assumed.add(var.split("_")[0] if var not in TARGET_UNITS else var)
            continue
        m, a = conv
        mult.pop(var, None); add.pop(var, None)
        if m != 1: mult[var] = round(m, 10)
        if a != 0: add[var] = a
    for v in sorted(assumed):
        missing.append(f"- {v}: no unit information, assuming {_target(v)}")
    for key, vals in (("unit_mult", mult), ("unit_add", add)):
        if vals: _put(adapter, key, dict(sorted(vals.items())))
        else: _remove(adapter, key)
    if adapter:
        pos = list(entry).index("metadata") if "metadata" in entry and "data_adapter" not in entry else None
        _put(entry, "data_adapter", adapter, pos)
    else: _remove(entry, "data_adapter")
 
def YML_updater(input_path:str, wflow_dir:str, out_path:str):
    input_path, out_path = os.path.normpath(input_path), os.path.normpath(out_path)
    wflow_dir, missing = os.path.normpath(wflow_dir), []
    INSPECTORS = {
        "rasterio": _inspect_rasterio, "raster_xarray": _inspect_netcdf,
        "pyogrio": _inspect_vector, "vector": _inspect_vector
    }
    yml = YAML()
    yml.preserve_quotes = True
    yml.width = 4096
    with open(input_path, encoding="utf-8") as f:
        catalog = yml.load(f)
    root = os.path.dirname(out_path)
    catalog.setdefault("meta", CommentedMap())["root"] = root.replace("\\", "/")
    for name, entry in catalog.items():
        if name == "meta" or not isinstance(entry, dict): continue
        driver = entry.get("driver")
        if isinstance(driver, str):
            driver = CommentedMap(name=driver)
            _put(entry, "driver", driver)
        if isinstance(driver, dict): drv_name = driver.get("name")
        else: drv_name = None
        if drv_name is None:
            missing.append(f"- [{name}] Invalid or missing driver.")
            continue
        uri = entry.get("uri")
        if uri is None:
            missing.append(f"- [{name}] doesn't have 'uri'"); continue
        files = _resolve(root, uri)
        if not files:
            missing.append(f"- [{name}] cannot find data ({drv_name}): {os.path.join(root, uri)}"); continue
        if entry.get("data_type") == "DataFrame":
            if driver is None: raise ValueError("driver is not initialized")
            opts = driver.get("options") or CommentedMap()
            opts.setdefault("index_col", 0)
            _put(driver, "options", opts)
            continue
        inspector = INSPECTORS.get(drv_name)
        if inspector is None:
            missing.append(f"- [{name}] driver '{drv_name}' is not supported yet. Keep the old one."); continue
        crs, nodata, units, dt = inspector(files, missing)
        if crs is None: missing.append(f"- [{name}] No CRS found in the data. Please define it manually.")
        _set_metadata(entry, crs=crs if crs is not None else entry.get("metadata", {}).get("crs"))
        if entry.get("data_type") == "RasterDataset":
            _set_metadata(entry, nodata=nodata)
            _update_units(entry, units, dt, missing)
    if len(missing) != 0: return '\n'.join(missing)
    with open(out_path, "w", encoding="utf-8") as f:
        yml.dump(catalog, f)
    return None