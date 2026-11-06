import tomllib
from hydromt_wflow import WflowSbmModel
from services import functions
import numpy as np, pandas as pd, xarray as xr

OUTPUT_VARS = {
    # Overland flow variables
    "overland_flow": "land_surface_water__volume_flow_rate",    # Overland discharge (average over timestep)	m³ s⁻¹
    "overland_depth": "land_surface_water__depth",              # Overland depth (average over timestep)	m
    "overland_volume": "land_surface_water__volume",            # Overland volume (average over timestep)	m³
    # Soil variables
    "soil_evapotranspiration": "land_surface__evapotranspiration_volume_flux",                                  # Total actual evapotranspiration	mm
    "soil_storage_total": "land_water~storage~total__depth",                                                    # Total water storage (excluding floodplains, lakes and reservoirs)	mm
    "soil_infiltration_volume": "soil_water__infiltration_volume_flux",                                         # Actual infiltration into the unsaturated zone	mm Δt⁻¹
    "soil_transpiration_volume": "soil_water__transpiration_volume_flux",                                       # Transpiration from vegetation	mm Δt⁻¹
    "soil_runoff": "soil_surface_water__runoff_volume_flux",                                                    # Total surface runoff from infiltration and saturation excess	mm Δt⁻¹
    "soil_net_runoff": "soil_surface_water__net_runoff_volume_flux",                                            # Net surface runoff (after open water evaporation)	mm Δt⁻¹
    "soil_water_volume_fraction": "soil_layer_water__volume_fraction",                                          # Volumetric water content per soil layer (including residual water content and saturated zone)
    "soil_water_volume_percentage": "soil_layer_water__volume_percentage",                                      # Volumetric water content per soil layer (including residual water content and saturated zone)	%
    "soil_water_rootzone_volume_fraction": "soil_water_root-zone__volume_fraction",                             # Volumetric water content in root zone (including residual water content and saturated zone)
    "soil_water_rootzone_volume_percentage": "soil_water_root-zone__volume_percentage",                         # Volumetric water content in root zone (including residual water content and saturated zone)	%
    "soil_water_rootzone_depth": "soil_water_root-zone__depth",                                                 # Root water storage in unsaturated and saturated zone (excluding residual water content)	mm
    "soil_water_unsatzone_depth": "soil_water_unsat-zone__depth",                                               # Amount of water in the unsaturated store	mm
    "soil_water_satzone_capillary_volume_flux": "soil_water_sat-zone_top__capillary_volume_flux",               # Actual capillary rise	mm Δt⁻¹
    "soil_water_satzone_recharge_volume_flux": "soil_water_sat-zone_top__recharge_volume_flux",                 # Downward flux from unsaturated to saturated zone	mm Δt⁻¹
    "soil_water_satzone_net_recharge_volume_flux": "soil_water_sat-zone_top__net_recharge_volume_flux",         # Net recharge to saturated zone	mm Δt⁻¹
    "soil_water_satzone_leakage_volume_flux": "soil_water_sat-zone_bottom__leakage_volume_flux",                # Actual leakage from saturated store	mm Δt⁻¹
    "soil_water_satzone_depth": "soil_water_sat-zone_top__depth",                                               # Pseudo-water table depth (top of the saturated zone)	mm
    # Lake variables
    "lake_volume": "reservoir_water__volume",                                            # Lake volume (average over timestep), m³
    "lake_level": "reservoir_water_surface__elevation",                                  # Lake water level (average over timestep), m
    "lake_outflow": "reservoir_water~outgoing__volume_flow_rate",                        # Outflow of the lake (average over timestep)	m³ s⁻¹
    "lake_inflow": "reservoir_water~incoming__volume_flow_rate",                         # Inflow into the lake (average over timestep)	m³ s⁻¹
    "lake_evaporation": "reservoir_water__evaporation_volume_flux",                      # Average actual evaporation over the lake area	mm Δt⁻¹
    "lake_precipitation": "reservoir_water__precipitation_volume_flux",                  # Average precipitation over the lake area	mm Δt⁻¹
    "lake_potential_evaporation": "reservoir_water__potential_evaporation_volume_flux",  # Average potential evaporation over the lake area	mm Δt⁻¹
    # Reservoir variables
    "reservoir_volume": "reservoir_water__volume",                                              # Reservoir volume (average over the timestep)	m³
    "reservoir_outflow": "reservoir_water~outgoing__volume_flow_rate",                          # Outflow of the reservoir (average over the timestep)	m³ s⁻¹
    "reservoir_inflow": "reservoir_water~incoming__volume_flow_rate",                           # Inflow into the reservoir (average over the timestep)	m³ s⁻¹
    "reservoir_evaporation": "reservoir_water__evaporation_volume_flux",                        # Average actual evaporation over the reservoir area	mm Δt⁻¹
    "reservoir_precipitation": "reservoir_water__precipitation_volume_flux",                    # Average precipitation over the reservoir area	mm Δt⁻¹
    "reservoir_potential_evaporation": "reservoir_water__potential_evaporation_volume_flux",    # Average potential evaporation over the reservoir area	mm Δt⁻¹
    # River variables (Kinematic wave)
    "river_discharge": "river_water__volume_flow_rate",                         # River discharge (average over timestep)	m³ s⁻¹
    "river_depth": "river_water__depth",                                        # River depth (average over timestep)	m
    "river_volume": "river_water__volume",                                      # River volume (average over timestep)	m³
    "river_lateral_inflow": "river_water_inflow~lateral__volume_flow_rate",     # Lateral inflow into the river (average over timestep)	m³ s⁻¹
    # Snow, Glacier, Vegetation variables
    "snow_water": "snowpack__leq-depth",                                            # Liquid-water equivalent of snow pack (SWE)	mm
    "snow_melt": "snowpack_meltwater__volume_flux",                                 # Amount of snow melt	mm Δt⁻¹
    "snow_runoff": "snowpack_water__runoff_volume_flux",                            # Runoff from snowpack	mm Δt⁻¹
    "glacier_melt": "glacier_ice__melt_volume_flux",                                # Melt from the glacier	mm Δt⁻¹
    "vegetation_stemflow": "vegetation_canopy_water__stemflow_volume_flux",         # Stemflow	mm Δt⁻¹
    "vegetation_throughfall": "vegetation_canopy_water__throughfall_volume_flux",   # Throughfall	mm Δt⁻¹
}
STATIC_REQUIRED = {"local_drain_direction": "Flow Direction (LDD)"}
GRID_FILE, SCALAR_FILE, CSV_FILE = "output.nc", "output_scalar.nc", "output.csv"
MOVEMENT_OUTPUTS = ("overland_flow", "overland_depth", "river_discharge", "river_depth")
REQUIRES = {
    **{k: "river" for k in OUTPUT_VARS if k.startswith("river_")},
    **{k: "reservoir" for k in OUTPUT_VARS if k.startswith(("lake_", "reservoir_"))},
    **{k: "snow" for k in OUTPUT_VARS if k.startswith("snow_")},
    "glacier_melt": "glacier",
}
SPATIAL = (("y", "x"), ("lat", "lon"))
MASK_NAMES = ("mask", "subcatchment", "basins")


def model_init(mod_path:str, data_lib:list):
    return WflowSbmModel(root=mod_path, config_filename='wflow_sbm.toml', data_libs=data_lib, mode='w')

def model_time(start:str, end:str, step, time_zone:str):
    start_utc = functions.local_to_utc(start, time_zone).replace(tzinfo=None)
    end_utc = functions.local_to_utc(end, time_zone).replace(tzinfo=None)
    return {"starttime": start_utc, "endtime": end_utc, "timestepsecs": step}

def model_setting(step=3600):
    # Reference: https://deltares.github.io/Wflow.jl/dev/model_docs/model_settings.html
    config, step = {}, int(step)
    config['type'] = 'sbm' # model type: [sbm, sbm_gwf]
    config['cold_start__flag'] = True  # Initialize model with cold (cold_start__flag = true) or warm state
    config['reservoir__flag'] = False #Include reservoir modelling
    config['water_mass_balance__flag'] = False # Include water mass balance error computations
    config['snow_gravitational_transport__flag'] = True # Include gravitational lateral snow transport
    config['glacier__flag'] = False # Include glacier modelling
    config['soil_infiltration_reduction__flag'] = True # Enable reduction factor applied to the soil infiltration capacity
    config['snow__flag'] = True # Include snow modelling
    # Saturated hydraulic conductivity depth profile for SBM soil model
    # optional, one of ("exponential", "exponential_constant", "layered", "layered_exponential"), default is "exponential"
    config['saturated_hydraulic_conductivity_profile'] = 'exponential'
    config['land_routing'] = 'kinematic_wave' # Routing approach for overland flow: ["kinematic_wave", "local_inertial"]
    config['river_routing'] = 'kinematic_wave' # Routing approach for river flow: ["kinematic_wave", "local_inertial"]
    config['river_kinematic_wave__time_step'] = min(900, step)
    config['land_kinematic_wave__time_step'] = min(3600, step)
    config['kinematic_wave__adaptive_time_step_flag'] = False # Enable kinematic wave adaptive (internal) time stepping
    return config

def model_basemap(model, region:dict, res:int):
    # Reference: https://deltares.github.io/hydromt_wflow/stable/api/_generated/hydromt_wflow.WflowSbmModel.setup_basemaps.html
    model.setup_basemaps(
        region=region, hydrography_fn='my_hydro', res=res, upscale_method='ihu' # 'ihu', 'eam', 'dmm'
    )
    # Unit cell length of input rasters in lat/lon degree (cell_length_in_meter__flag = false) or in meter
    model.setup_config({"model.cell_length_in_meter__flag": not model.crs.is_geographic})
    return model

def model_river(model, uparea:float):
    # Reference: https://deltares.github.io/hydromt_wflow/stable/api/_generated/hydromt_wflow.WflowSbmModel.setup_rivers.html
    output_names = {
        'river__length': 'river_length', 'river__width': 'river_width', 'river__slope': 'river_slope',
        'river_bank_water__depth': 'river_bank_depth', # Bankfull depth of river, default is 1.0 m
        'river_water_flow__manning_n_parameter': 'river_manning_n', # Manning's roughness, default is 0.036
        'river_bank_water__elevation': 'river_bank_elevation', 'river_location__mask': 'river_mask'
    }
    model.setup_rivers(
        hydrography_fn='my_hydro', river_geom_fn='river_network', 
        river_upa=uparea, # Minimum upstream area threshold for the river map [km2]
        rivdph_method='powlaw', # 'gvf', 'manning', 'powlaw'
        slope_len=2, #  Length over which the river slope is calculated [km]
        min_rivlen_ratio=0, min_rivdph=1.0, # Minimum river depth [m]
        min_rivwth=30, # Minimum river width [m]
        smooth_len=5000, # Length [m] over which to smooth the output river width and depth
        connectivity=8, river_routing='kinematic_wave', # 'kinematic_wave', 'local_inertial'
        elevtn_map='land_elevation', # Name of the elevation map in the current WflowBaseModel.staticmaps
        output_names=output_names
    )
    model.setup_river_roughness(
        rivman_mapping_fn='river_manning_mapping', # Name of the river manning n map in the current WflowBaseModel.river_maps
        strord_name='meta_streamorder', # Name of the stream order map in the current WflowBaseModel.staticmaps
        output_name='river_manning_n' # Mapping of output variable names.
    )
    return model

def model_soil(model, soil_layers:list):
    # Reference: https://deltares.github.io/hydromt_wflow/stable/api/_generated/hydromt_wflow.WflowSbmModel.setup_soilmaps.html
    soil_names = {
        'soil__thickness': 'soil_thickness', 'soil_layer_water__brooks_corey_exponent': 'soil_brooks_corey_c',
        'soil_surface_water__vertical_saturated_hydraulic_conductivity': 'soil_ksat_vertical', 
        'soil_water__residual_volume_fraction': 'soil_theta_r', 'soil_water__saturated_volume_fraction': 'soil_theta_s', 
        'soil_water__vertical_saturated_hydraulic_conductivity_scale_parameter': 'soil_f'
    }
    model.setup_soilmaps(
        soil_fn='soilgrids', ptf_ksatver='brakensiek', # 'brakensiek', 'cosby'
        wflow_thicknesslayers=soil_layers, # Thickness of soil layers [mm] for wflow_sbm soil model
        output_names=soil_names
    )
    return model

def model_landuse(model, luc_fn, lulc_mapping_fn, lai_fn):
    # Setup land use maps: https://deltares.github.io/hydromt_wflow/stable/api/_generated/hydromt_wflow.WflowSbmModel.setup_lulcmaps.html
    model.setup_laimaps_from_lulc_mapping(lulc_fn=luc_fn, lai_mapping_fn=lai_fn)
    lulc_variables = [
        'landuse', 'vegetation_kext', 'land_manning_n', 'soil_compacted_fraction', 
        'vegetation_root_depth', 'vegetation_leaf_storage', 'vegetation_wood_storage', 
        'land_water_fraction', 'vegetation_crop_factor', 'vegetation_feddes_alpha_h1', 
        'vegetation_feddes_h1', 'vegetation_feddes_h2', 'vegetation_feddes_h3_high', 
        'vegetation_feddes_h3_low', 'vegetation_feddes_h4'
    ]
    model.setup_lulcmaps(lulc_fn=luc_fn, lulc_mapping_fn=lulc_mapping_fn, lulc_vars=lulc_variables)
    return model

def model_forcing(model):
    model.setup_precip_forcing(precip_fn='weather_forcing')
    model.setup_temp_pet_forcing(
        temp_pet_fn='weather_forcing', pet_method='debruin', # 'debruin', 'makkink', 'penman-monteith_rh_simple', 'penman-monteith_tdew'
        press_correction=False, temp_correction=True, wind_correction=True,
        wind_altitude=10, reproj_method='nearest', fillna_method='nearest', dem_forcing_fn='dtm', skip_pet=False
    )
    return model

def model_constant_pars(model, params_input:dict):
    model.setup_constant_pars(
        subsurface_water__horizontal_to_vertical_saturated_hydraulic_conductivity_ratio = params_input['k_sat_ratio'],
        snowpack__degree_day_coefficient = params_input['dd_snow'],
        soil_surface_water__infiltration_reduction_parameter = params_input['inf_red'],
        vegetation_canopy_water__mean_evaporation_to_mean_precipitation_ratio = params_input['canopy_evap_ratio'],
        compacted_soil_surface_water__infiltration_capacity = params_input['inf_cap'],
        soil_water_saturated_zone_bottom__max_leakage_volume_flux = params_input['leak_max'],
        soil_wet_root__sigmoid_function_shape_parameter = params_input['soil_sigmoid'],
        atmosphere_air__snowfall_temperature_threshold = params_input['snowfall_t0'],
        atmosphere_air__snowfall_temperature_interval = params_input['snowfall_dt'],
        snowpack__melting_temperature_threshold = params_input['melt_t0_snow'],
        snowpack__liquid_water_holding_capacity = params_input['snow_liq_cap'],
        glacier_ice__degree_day_coefficient = params_input['dd_glacier'],
        glacier_firn_accumulation__snowpack_dry_snow_leq_depth_fraction = params_input['firn_dry_frac'],
        glacier_ice__melting_temperature_threshold = params_input['melt_t0_glacier']
    )
    return model

def _get_mask(staticmaps):
    if staticmaps is None: return None
    for name in MASK_NAMES:
        if name in staticmaps:
            m = staticmaps[name]
            return (m > 0) if m.dtype != bool else m
    return None

def _clean_attrs(obj, var_name, removed):
    for key, val in list(obj.attrs.items()):
        if isinstance(val, dict):
            if var_name in val and np.isscalar(val[var_name]):
                obj.attrs[key] = val[var_name]
            else:
                del obj.attrs[key]
                removed.append(f"{var_name}.{key}")
        elif val is None:
            del obj.attrs[key]
            removed.append(f"{var_name}.{key}")
        elif isinstance(val, (bool, np.bool_)):
            obj.attrs[key] = int(val)

def _spatial_dims(da):
    for dims in SPATIAL:
        if all(d in da.dims for d in dims): return dims
    return None

def sanitize_dataset(ds, comp, mask=None, logger=None, apply_mask=True):
    removed, masked, skipped = [], [], []
    _clean_attrs(ds, "<dataset>", removed)
    for name in list(ds.variables):
        _clean_attrs(ds[name], name, removed)
    if apply_mask and mask is not None:
        mdims = _spatial_dims(mask)
        for name, da in ds.data_vars.items():
            dims = _spatial_dims(da)
            if dims is None or not np.issubdtype(da.dtype, np.floating): continue
            if dims != mdims:
                skipped.append(f"{name}: dims {dims} ≠ mask {mdims}")
                continue
            same = all(da.sizes[d] == mask.sizes[d] and np.allclose(da[d].values, mask[d].values) for d in dims)
            if not same:
                sizes = {d: (da.sizes[d], mask.sizes[d]) for d in dims}
                skipped.append(f"{name}: grid {sizes} (data, mask) differs")
                continue
            ds[name] = da.where(mask)
            masked.append(name)
    if logger:
        if removed: logger.debug(f"{comp}: removed/converted {len(removed)} invalid attribute(s): {removed[:6]}.")
        if masked: logger.info(f"{comp}: masked {len(masked)} variable(s) outside the basin.")
        for s in skipped:
            logger.warning(f"{comp}: not masked – {s}.")
    return ds

def sanitize_model(model, logger, mask_forcing=True, mask_states=True):
    comps = {}
    for comp in ("staticmaps", "forcing", "states"):
        ds = getattr(getattr(model, comp, None), "data", None)
        if isinstance(ds, xr.Dataset): comps[comp] = ds
    mask = _get_mask(comps.get("staticmaps"))
    if mask is None and logger:
        logger.warning("No basin mask in static maps – masking skipped.")
    for comp, ds in comps.items():
        apply = (comp == "forcing" and mask_forcing) or (comp == "states" and mask_states)
        sanitize_dataset(ds, comp, mask, logger, apply_mask=apply)

def _current_output_cfg(model):
    cfg = getattr(model, "config", None)
    for getter in ("get_value", "get"):
        fn = getattr(cfg, getter, None)
        if callable(fn):
            try:
                v = fn("output")
                if isinstance(v, dict): return v
            except Exception: pass
    data = getattr(cfg, "data", None)
    return data.get("output", {}) if isinstance(data, dict) else {}

def _model_features(model, model_cfg):
    grid = None
    for comp in ("staticmaps", "grid"):
        grid = getattr(getattr(model, comp, None), "data", None)
        if grid is not None: break
    names = set(grid.data_vars) if grid is not None else set()
    def count(var):
        if grid is None or var not in grid: return 0
        return int(np.count_nonzero(np.nan_to_num(np.asarray(grid[var].values, dtype=float)) > 0))
    river_cells = count("river_mask")
    reservoir_maps = [n for n in names if n.startswith("reservoir")]
    return {
        "river": river_cells > 0,
        "reservoir": bool(model_cfg.get("reservoir__flag")) and bool(reservoir_maps),
        "snow": bool(model_cfg.get("snow__flag", False)),
        "glacier": bool(model_cfg.get("glacier__flag", False)), "_river_cells": river_cells,
    }

def resolve_outputs(params_output, model, model_cfg, logger=None):
    chosen = {k for k, v in (params_output or {}).items() if v}
    chosen |= set(MOVEMENT_OUTPUTS)
    unknown = sorted(chosen - set(OUTPUT_VARS))
    if unknown: raise ValueError(f"Unsupported output variable: {unknown}")
    feat = _model_features(model, model_cfg)
    reasons = {
        "river": f"no river cells in the model (river_mask has {feat['_river_cells']} cells; lower 'uparea' or check the region)",
        "reservoir": "the model has no lakes/reservoirs (reservoir__flag = false or no reservoir maps)",
        "snow": "snow modelling is disabled (snow__flag = false)",
        "glacier": "glacier modelling is disabled (glacier__flag = false)",
    }
    variables, dropped, used_names = {}, {}, {}
    for key in OUTPUT_VARS:
        if key not in chosen: continue
        need = REQUIRES.get(key)
        if need and not feat[need]:
            dropped[key] = reasons[need]
            continue
        std = OUTPUT_VARS[key]
        if std in used_names:
            dropped[key] = f"same Wflow variable as '{used_names[std]}' (lakes are reservoirs in Wflow.jl 1.x)"
            continue
        variables[std], used_names[std] = key, key
    if len(dropped) > 0:
        for k, r in dropped.items(): logger.warning(f"Output '{k}' skipped: {r}")
    return variables, dropped

def build_output_config(variables, existing_output=None, compression=3):
    # Source: https://deltares.github.io/Wflow.jl/previews/PR586/model_docs/parameters_routing.html
    out = {"netcdf_grid": {"path": GRID_FILE, "compressionlevel": compression, "variables": variables}}
    existing_output = existing_output or {}
    if existing_output.get("netcdf_scalar"): out["netcdf_scalar"] = {"path": SCALAR_FILE}
    if existing_output.get("csv"): out["csv"] = {"path": CSV_FILE}
    return out

def _time_steps(time_cfg):
    start, end = pd.Timestamp(time_cfg["starttime"]), pd.Timestamp(time_cfg["endtime"])
    return int((end - start).total_seconds() // int(time_cfg["timestepsecs"])) + 1

def model_writer(model, configs:dict, params_output:dict, logger, max_output_gb=10):
    variables, dropped = resolve_outputs(params_output, model, configs.get("model", {}), logger)
    steps = _time_steps(configs["time"]) if configs.get("time") else None
    est = estimate_output_gb(model, variables, steps) if steps else None
    if est is not None:
        logger.info(f"Expected Wflow run output (run_default/{GRID_FILE}): {len(variables)} variable(s) x {steps} steps; "
            f"~{est['active']:.1f} GB of data in {est['active_cells']:,} active cells "
            f"(full grid {est['full']:.1f} GB before compression)")
        if est["active"] > max_output_gb:
            logger.warning(f"The Wflow run output will be large (~{est['active']:.1f} GB). Select fewer outputs "
                f"(e.g. drop overland_volume) or use a coarser model resolution.")
    configs["output"] = build_output_config(variables, _current_output_cfg(model))
    model.setup_config({"output": configs["output"]})
    model.write(
        grid_filename='static_grid.nc', geoms_folder='staticgeoms',
        forcing_filename='weather_forcing.nc', states_filename='model_state.nc'
    )
    return dropped

def model_validate_config(toml_path, logger):
    with open(toml_path, 'rb') as f:
        model_cfg = tomllib.load(f).get('model', {})
    missing = [k for k in ('type', 'land_routing', 'river_routing') if k not in model_cfg]
    if missing:
        logger.info(f"[model] = {model_cfg}")
        raise ValueError(f"wflow_sbm.toml: [model] doesn't have {missing}")

def estimate_output_gb(model, variables, steps, n_soil_layers=None):
    grid = getattr(getattr(model, "staticmaps", None), "data", None)
    if grid is None: return None
    ny = grid.sizes.get("y", grid.sizes.get("lat", 0))
    nx = grid.sizes.get("x", grid.sizes.get("lon", 0))
    mask = _get_mask(grid)
    n_active = int(mask.values.sum()) if mask is not None else ny * nx
    river = grid["river_mask"] if "river_mask" in grid else None
    n_river = int(np.count_nonzero(np.nan_to_num(np.asarray(river.values, dtype=float)) > 0)) if river is not None else n_active
    layered = {"soil_water_volume_fraction", "soil_water_volume_percentage"}
    n_layers = n_soil_layers or grid.sizes.get("layer", 4)
    full = active = 0.0
    for name in variables.values():
        k = n_layers if name in layered else 1
        full += k * ny * nx
        active += k * (n_river if name.startswith("river_") else n_active)
    b = steps * 4 / 1e9
    return {"full": full * b, "active": active * b, "cells": ny * nx, "active_cells": n_active, "river_cells": n_river}