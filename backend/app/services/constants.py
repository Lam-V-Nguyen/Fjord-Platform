import re, math, os
from config import SOURCE_BACKEND
import numpy as np

SAMPLE_DIR = os.path.normpath(os.path.join(SOURCE_BACKEND, "samples"))
FLOW_SAMPLE_DIR = os.path.normpath(os.path.join(SOURCE_BACKEND, "flow_samples"))

# Dashboard
DASH_MOVE_DIR, DASH_VIEW_DIR = "movement",  "viewer"
VELOCITY_PAIRS = [
    ("mesh2d_ucxa", "mesh2d_ucya"), ("ucxa", "ucya"), 
    ("mesh2d_ucx", "mesh2d_ucy"), ("ucx", "ucy")
]
PERIODS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "all": None}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
# Map HYD variables on the Dashboard (name in map file -> lable, unit, color bar)
HYD_MAP_VARS = {
    "mesh2d_tem1": {"label": "Temperature", "unit": "°C", "palette": "thermal"},
    "mesh2d_sa1": {"label": "Salinity", "unit": "ppt", "palette": "haline"},
    "mesh2d_ucmag": {"label": "Current speed", "unit": "m/s", "palette": "speed"},
    "mesh2d_s1": {"label": "Water level", "unit": "m", "palette": "level"},
    "mesh2d_waterdepth": {"label": "Water depth", "unit": "m", "palette": "depth"},
    "mesh2d_Contaminant": {"label": "Contaminant", "unit": "mg/m³", "palette": "quality"},
}
# HYD station variables (his)
HYD_HIS_VARS = {
    "temperature": {"label": "Temperature", "unit": "°C"},
    "salinity": {"label": "Salinity", "unit": "ppt"},
    "velocity_magnitude": {"label": "Current speed", "unit": "m/s"},
    "waterlevel": {"label": "Water level", "unit": "m"},
    "Contaminant": {"label": "Contaminant", "unit": "mg/m³"},
}
SKIP_WAQ = {"station_name", "station_x", "station_y", "station_z", "nTimesDlwqBnd", "mesh2d_layer_dlwq"}
HIS_TO_MAP = {"temperature": "mesh2d_tem1", "salinity": "mesh2d_sa1", "velocity_magnitude": 
    "mesh2d_ucmag", "waterlevel": "mesh2d_s1", "Contaminant": "mesh2d_Contaminant"}
CACHE_TTL = 300  # seconds

# Calibration
OVERRIDES_FILE, COMPLETED_FLAG, FACTOR_SUFFIX, RUN_LOCK_FILE = "base_overrides.json", ".completed", "_factor", ".simulation_run.lock"
INTERVAL_KEYS = ("HisInterval", "MapInterval", "WaqInterval", "ClassMapInterval", "RstInterval")
INDEXED = re.compile(r"^(?P<key>[A-Za-z0-9_]+)\[(?P<i>\d+)\]$")
ERROR_RE = re.compile(r"\*\*\s*error|forrtl:|^\s*error\s*:", re.IGNORECASE)
STALE_RESULTS = (
    "scenarios", "summary", "model", "calibration_summary.csv",
    "failed_cases.json", "optuna.csv", "optuna_importance.csv",
)
OUTPUT_DIR, FEATURES_FILE = "output", "features.json"

# Data Download
DATASET_POINT = "reanalysis-era5-single-levels-timeseries"
DATASET_GRID = "reanalysis-era5-single-levels"
KARTVERKET_URL = "https://ws.geonorge.no/hoydedata/v1/punkt"
POINT_SOURCES = ("met-nordic", "open-meteo")
ACTIVE_STATES = ["accepted", "running"]
ERA5_LATENCY_DAYS, TOA_CDS_NAME = 6, "toa_incident_solar_radiation"
ERA5_GRID_DEG, G = 0.25, 9.80665    # m/s²
RD = 287.05          # J/(kg·K)
LAPSE_T = 0.0065     # K/m  (temperature)
TO_MM = {"tp", "cp", "lsp", "sf", "e", "pev", "ro", "sro", "ssro", "smlt", "es"}
TO_DEGC = {"t2m", "d2m", "skt", "sst", "stl1", "stl2", "stl3", "stl4"}
TO_HPA, TO_PERCENT = {"msl", "sp"}, {"tcc", "lcc", "mcc", "hcc"}
DERIVED = {"wind_speed", "wind_direction"}
ACCUMULATED_J = ("ssrd", "strd", "tisr", "fdir")
NVE_CHUNK_DAYS = {0: 31, 60: 366, 1440: 36600}
WIND_COMPONENTS = {"10m_u_component_of_wind": "u10", "10m_v_component_of_wind": "v10"}
POINT_ALIASES = {"10m_u_component_of_wind": "wind_speed_10m", "10m_v_component_of_wind": "wind_direction_10m"}
VARIABLES = {
    'total_precipitation': 'tp', # Precipitation
    '2m_temperature': 't2m', # Temperature
    '10m_u_component_of_wind': 'u10', '10m_v_component_of_wind': 'v10', # Wind
    "mean_sea_level_pressure": "msl",  # Pressure
    'surface_solar_radiation_downwards': 'ssrd', # Surface shortwave radiation (downward)
    'toa_incident_solar_radiation': 'tisr', # TOA incident solar radiation (shortwave)
    'total_cloud_cover': 'tcc', # Cloud cover
}
ELEV_SENSITIVE = {"t2m", "d2m", "skt", "sp"}
ALIASES = {"fg10": ("i10fg",), "i10fg": ("fg10",)}
NVE_THEMES = {"windSpeed10m1h": "Wind Speed_10m (m/s)", "windDirection10m1h": "Wind Direction_10m (°)"}

# Flow
ISRIC_URL = "https://files.isric.org/soilgrids/former/2017-03-10/data/"
VIEW_DIR, FORMAT, LAYER_SEP, M_PER_DEG = "viewer", 1, "__layer", 111_320.0
LDD_NAMES = ["local_drain_direction", "wflow_ldd", "ldd"]
# PCRaster LDD: 1 SW 2 S 3 SE 4 W 5 pit 6 E 7 NW 8 N 9 NE
LDD = {1: (-1, -1), 2: (0, -1), 3: (1, -1), 4: (-1, 0), 6: (1, 0), 7: (-1, 1), 8: (0, 1), 9: (1, 1)}
LDD_UNIT = {k: (dx / math.hypot(dx, dy), dy / math.hypot(dx, dy)) for k, (dx, dy) in LDD.items()}
SLOPE_NAMES = ["land_slope", "Slope", "slope"]
LAND_N_NAMES = ["land_manning_n", "N"]
RIVER_NAMES = ["river_mask", "wflow_river"]
RIVER_W_NAMES = ["river_width", "wflow_riverwidth"]
RIVER_S_NAMES = ["river_slope", "RiverSlope"]
RIVER_N_NAMES = ["river_manning_n", "N_River"]
BASIN_NAMES = ["subcatchment", "wflow_subcatch", "basins"]
VAR_INFO = {
    "overland_flow": ("m³/s", "Overland discharge", False, "flow", "Overland flow"),
    "overland_depth": ("m", "Overland water depth", False, "depth", "Overland flow"),
    "overland_volume": ("m³", "Overland water volume", False, "depth", "Overland flow"),
    "soil_evapotranspiration": ("mm/Δt", "Actual evapotranspiration", True, "runoff", "Soil"),
    "soil_storage_total": ("mm", "Total water storage", False, "depth", "Soil"),
    "soil_infiltration_volume": ("mm/Δt", "Infiltration", True, "depth", "Soil"),
    "soil_transpiration_volume": ("mm/Δt", "Transpiration", True, "runoff", "Soil"),
    "soil_runoff": ("mm/Δt", "Total surface runoff", True, "runoff", "Soil"),
    "soil_net_runoff": ("mm/Δt", "Net surface runoff", True, "runoff", "Soil"),
    "soil_water_volume_fraction": ("-", "Water volume fraction", False, "depth", "Soil"),
    "soil_water_volume_percentage": ("%", "Water volume percentage", False, "depth", "Soil"),
    "soil_water_rootzone_volume_fraction": ("-", "Root-zone water fraction", False, "depth", "Soil"),
    "soil_water_rootzone_volume_percentage": ("%", "Root-zone water percentage", False, "depth", "Soil"),
    "soil_water_rootzone_depth": ("mm", "Root-zone water storage", False, "depth", "Soil"),
    "soil_water_unsatzone_depth": ("mm", "Unsaturated store", False, "depth", "Soil"),
    "soil_water_satzone_capillary_volume_flux": ("mm/Δt", "Capillary rise", True, "depth", "Groundwater"),
    "soil_water_satzone_recharge_volume_flux": ("mm/Δt", "Recharge to saturated zone", True, "depth", "Groundwater"),
    "soil_water_satzone_net_recharge_volume_flux": ("mm/Δt", "Net recharge", True, "depth", "Groundwater"),
    "soil_water_satzone_leakage_volume_flux": ("mm/Δt", "Leakage", True, "depth", "Groundwater"),
    "soil_water_satzone_depth": ("mm", "Water table depth", False, "hours", "Groundwater"),
    "river_discharge": ("m³/s", "River discharge", False, "flow", "River"),
    "river_depth": ("m", "River depth", False, "depth", "River"),
    "river_volume": ("m³", "River volume", False, "depth", "River"),
    "river_lateral_inflow": ("m³/s", "Lateral inflow to river", False, "flow", "River"),
    "lake_volume": ("m³", "Lake volume", False, "depth", "Lakes & reservoirs"),
    "lake_level": ("m", "Lake level", False, "depth", "Lakes & reservoirs"),
    "lake_inflow": ("m³/s", "Lake inflow", False, "flow", "Lakes & reservoirs"),
    "lake_outflow": ("m³/s", "Lake outflow", False, "flow", "Lakes & reservoirs"),
    "reservoir_volume": ("m³", "Reservoir volume", False, "depth", "Lakes & reservoirs"),
    "reservoir_inflow": ("m³/s", "Reservoir inflow", False, "flow", "Lakes & reservoirs"),
    "reservoir_outflow": ("m³/s", "Reservoir outflow", False, "flow", "Lakes & reservoirs"),
    "snow_water": ("mm", "Snow water equivalent", False, "depth", "Snow & glacier"),
    "snow_melt": ("mm/Δt", "Snow melt", True, "depth", "Snow & glacier"),
    "snow_runoff": ("mm/Δt", "Snowpack runoff", True, "depth", "Snow & glacier"),
    "glacier_melt": ("mm/Δt", "Glacier melt", True, "depth", "Snow & glacier"),
    "vegetation_stemflow": ("mm/Δt", "Stemflow", True, "depth", "Vegetation"),
    "vegetation_throughfall": ("mm/Δt", "Throughfall", True, "depth", "Vegetation"),
}
NODATA_DEM, NODATA_SOIL_THICKNESS, NODATA_INT, NODATA_FLWDR = -9999.0, -99999, 0, 247

# Movement
U_NAMES_AVG, V_NAMES_AVG = ["mesh2d_ucxa", "ucxa"], ["mesh2d_ucya", "ucya"]
U_NAMES, V_NAMES = ["mesh2d_ucx", "ucx"], ["mesh2d_ucy", "ucy"]
DEPTH_NAMES = ["mesh2d_waterdepth", "waterdepth"]
S1_NAMES, BED_NAMES = ["mesh2d_s1", "s1"], ["mesh2d_flowelem_bl", "flowelem_bl"]
UV_SCALE = 0.001     # 1 unit int16 = 1 mm/s
DEPTH_SCALE = 0.01   # 1 unit = 1 cm

# Weather
SOURCES = {
    "era5": {"label": "ERA5 reanalysis (CDS)", "needs_key": True, "min_date": "1940-01-01", "latency_days": ERA5_LATENCY_DAYS},
    "era5-timeseries": {"label": "ERA5 hourly time series (CDS)", "needs_key": True, "min_date": "1940-01-01", "latency_days": ERA5_LATENCY_DAYS},
    "met-nordic": {"label": "MET Nordic analysis (MET Norway)", "needs_key": False, "min_date": "2012-09-01", "latency_days": 0},
    "open-meteo": {"label": "Open-Meteo historical API", "needs_key": False, "min_date": "1940-01-01", "latency_days": 2},
    "nve-gts": {"label": "NVE GridTimeSeries (seNorge, xgeo.no)", "needs_key": False, "min_date": None, "latency_days": 0},
}
FORCING = {
    "precip": ("mm",    lambda s: np.clip(s["tp"] * 1000.0, 0.0, None)),
    "temp": ("degC",  lambda s: s["t2m"] - 273.15),
    "kin": ("W/m^2", lambda s: np.clip(s["ssrd"] / 3600.0, 0.0, None)),
    "kout": ("W/m^2", lambda s: np.clip(s["tisr"] / 3600.0, 0.0, None)),
    "wind": ("m/s",   lambda s: np.hypot(s["u10"], s["v10"])),
    "press_msl": ("hPa",   lambda s: s["msl"] / 100.0),
}
CHUNK_T, CHUNK_XY, LAPSE_TD = 24, 128,  0.0018    # K/m  (dew point temperature)
CACHE_MAX_AGE_DAYS, DOWNLOAD_RETRIES, RETRY_WAIT_S = 2, 3, (30, 120, 300)
ALIASES = {"degc": "degC", "°c": "degC", "c": "degC", "celsius": "degC", "k": "K",
    "w/m^2": "W/m^2", "w/m2": "W/m^2", "w m-2": "W/m^2", "j/m^2": "J/m^2", "j/m2": "J/m^2",
    "j m-2": "J/m^2", "kg/m2": "kg/m^2", "kg m**2": "kg/m^2", "pa": "Pa", "hpa": "hPa",
    "kg/m-2": "kg/m^-2", "kg m**-2": "kg/m^-2",
    "kpa": "kPa", "kg/m3": "kg/m^3", "kg m-3": "kg/m3", "g/cm3": "g/cm^3", "ph": "-", "1": "-"
}
SIGMA, MET_HOUR_RETRIES, MET_MAX_GAP_H, MET_MAX_MISSING_SHARE = 5.670374419e-8, 3, 6, 0.2
METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
MET_BASE = "https://thredds.met.no/thredds/dodsC"
MET_URLS = (
    MET_BASE + "/metpparchivev3/{t:%Y/%m/%d}/met_analysis_1_0km_nordic_{t:%Y%m%dT%H}Z.nc",
    MET_BASE + "/metpparchive/{t:%Y/%m/%d}/met_analysis_1_0km_nordic_{t:%Y%m%dT%H}Z.nc",
    MET_BASE + "/metpplatest/met_analysis_1_0km_nordic_{t:%Y%m%dT%H}Z.nc",
)
MET_VARS = {"precipitation_amount": "precip_mm", "air_temperature_2m": "t_k", "relative_humidity_2m": "rh",
    "air_pressure_at_sea_level": "msl_pa", "wind_speed_10m": "ws", "wind_direction_10m": "wd",
    "cloud_area_fraction": "cloud", "integral_of_surface_downwelling_shortwave_flux_in_air_wrt_time": "sw_j",
    "integral_of_surface_downwelling_longwave_flux_in_air_wrt_time": "lw_j"
}
OM_HOURLY = ("precipitation,temperature_2m,relative_humidity_2m,dew_point_2m,shortwave_radiation,wind_speed_10m,"
    "wind_direction_10m,pressure_msl,surface_pressure,cloud_cover"
)

# Weather Live
WEATHER_USER_AGENT = "HydroAI-Water-Platform/1.0 (+https://github.com/Lam-V-Nguyen/HydroAI-Fjord-Platform)"
WEATHER_STATION_MAX_KM = 30                   # trạm Frost xa hơn -> coi như không có trạm gần
WEATHER_FALLBACK_ORDER = ("metno", "openmeteo")   # nguồn thay thế khi không có số đo trạm
WEATHER_PAST_HOURS, WEATHER_FORECAST_HOURS, WEATHER_TIMEOUT = 48, 48, 20
WEATHER_VARIABLES = [
    {"key": "temperature", "label": "Air temperature", "short": "Temperature", "unit": "°C", "icon": "temperature", "decimals": 1},
    {"key": "precipitation", "label": "Precipitation", "short": "Precipitation", "unit": "mm/h", "icon": "droplet", "decimals": 1},
    {"key": "pressure", "label": "Air pressure (sea level)", "short": "Pressure (MSL)", "unit": "hPa", "icon": "activity", "decimals": 0},
    {"key": "humidity", "label": "Relative humidity", "short": "Humidity", "unit": "%", "icon": "waves", "decimals": 0},
]
WEATHER_SOURCES = {
    "frost": {"label": "MET Norway Frost", "kind": "Observations", "resolution": "hourly (~1 h delay)",
        "ttl": 900, "license": "CC BY 4.0 · MET Norway", "url": "https://frost.met.no"},
    "metno": {"label": "MET Norway Locationforecast 2.0", "kind": "Forecast", "resolution": "hourly, updated ~hourly",
        "ttl": 1800, "license": "CC BY 4.0 · MET Norway", "url": "https://api.met.no"},
    "openmeteo": {"label": "Open-Meteo", "kind": "Model analysis + forecast", "resolution": "hourly, updated ~hourly",
        "ttl": 900, "license": "CC BY 4.0 · Open-Meteo", "url": "https://open-meteo.com"},
}
FROST_ELEMENTS = {
    "temperature": "air_temperature", "precipitation": "sum(precipitation_amount PT1H)",
    "pressure": "air_pressure_at_sea_level", "humidity": "relative_humidity",
}

# Wflow
MASK_NAMES = ("mask", "subcatchment", "basins")
SPATIAL = (("y", "x"), ("lat", "lon"))
MOVEMENT_OUTPUTS = ("overland_flow", "overland_depth", "river_discharge", "river_depth")
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
REQUIRES = {
    **{k: "river" for k in OUTPUT_VARS if k.startswith("river_")},
    **{k: "reservoir" for k in OUTPUT_VARS if k.startswith(("lake_", "reservoir_"))},
    **{k: "snow" for k in OUTPUT_VARS if k.startswith("snow_")},
    "glacier_melt": "glacier",
}
GRID_FILE, SCALAR_FILE, CSV_FILE = "output.nc", "output_scalar.nc", "output.csv"
SOILS = [
    ['Clay', 'Sand', 'Silt', 'Bulk density', 'Soil organic carbon', 'Soil pH'],
    ['0', '5', '15', '30', '60', '100', '200']
]
SOIL_REVERSE = {
    'clyppt': 'Clay', 'sndppt': 'Sand', 'sltppt': 'Silt', 
    'bd': 'Bulk density', 'oc': 'Soil organic carbon', 'ph': 'Soil pH'
}
SOIL_TYPES = {
    'bulk density': ['BLDFIE_M', 'bd', 1000, np.float32, -9999.0, "g/cm3"],
    'clay': ['CLYPPT_M', 'clyppt', 1, np.float32, -9999.0, "%"], 
    'sand': ['SNDPPT_M', 'sndppt', 1, np.float32, -9999.0, "%"],
    'silt': ['SLTPPT_M', 'sltppt', 1, np.float32, -9999.0, "%"],  
    'organic carbon': ['ORCDRC_M', 'oc', 10, np.float32, -9999.0, "%"], 
    'pH': ['PHIHOX_M', 'ph', 10, np.float32, -9999.0, "-"]
}
SOIL_DEPTH = {
    '0cm': 'sl1', '5cm': 'sl2', '15cm': 'sl3', '30cm': 'sl4', 
    '60cm': 'sl5', '100cm': 'sl6', '200cm': 'sl7'
}
SOIL_DEPTH_REVERSE = {v: k for k, v in SOIL_DEPTH.items()}

# YAML
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

# Others
VARIABLES_NAME = {
    # For In-situ options
    'temperature':'Temperature (°C)', 'salinity':'Salinity (ppt)', 'contaminant':'Contaminant (mg/m³)', # For hydrodynamic stations
    'velocity_magnitude':'Velocity (m/s)', 'discharge_magnitude':'Discharge (m³/s)', # For hydrodynamic stations
    'x_velocity':'X-Velocity (m/s)', 'y_velocity':'Y-Velocity (m/s)','z_velocity':'Z-Velocity (m/s)', # For hydrodynamic stations
    'pre_discharge_source':'source_sink_prescribed_discharge', 'pre_discharge_increment_source':'source_sink_prescribed_salinity_increment', # For sources
    'pre_temperature_increment_source':'source_sink_prescribed_temperature_increment', 'avg_discharge_source':'source_sink_discharge_average', # For sources
    'discharge_source':'source_sink_current_discharge', 'cumulative_volume_source':'source_sink_cumulative_volume',  # For sources
    'cross_section_velocity':'Velocity (m/s)', 'cross_section_area':'Area (m²)', 'cross_section_discharge':'Discharge (m³/s)', # For cross-sections
    'cross_section_cumulative_discharge': 'Cumulative Discharge (m³)', 'cross_section_temperature':'Temperature (°C)', # For cross-sections
    'cross_section_cumulative_temperature': 'Cumulative Temperature (°C)', 'cross_section_salt':'Salinity (ppt)', # For cross-sections
    'cross_section_cumulative_salt': 'Cumulative Salinity (ppt)', 'cross_section_Contaminant':'Contaminant (mg/m³)', # For cross-sections
    'cross_section_cumulative_Contaminant': 'Cumulative Contaminant (mg/m³)', # For cross-sections
    # For Hydrodynamics options
    'wl':'waterlevel', 'wd':'waterdepth',
    # For Water Balance options
    'wb_tv':'water_balance_total_volume', 'wb_s':'water_balance_storage', 'wb_bi':'water_balance_boundaries_in',
    'wb_bo':'water_balance_boundaries_out', 'wb_bt':'water_balance_boundaries_total',
    'wb_pt':'water_balance_precipitation_total', 'wb_e':'water_balance_evaporation', 'wb_ss':'water_balance_source_sink',
    'wb_gi':'water_balance_groundwater_in', 'wb_go':'water_balance_groundwater_out', 'wb_gt':'water_balance_groundwater_total',
    'wb_pg':'water_balance_precipitation_on_ground','wb_ve':'water_balance_volume_error',
    # For Meteorological options
    'thf':'Qtot', 'at':'Tair', 'rh':'rhum', 'pr':'rain', 'si':'Qsun', 'shf':'Qcon', 'ws':'wind', 'fcshf':'Qfrcon',
    'lwbr':'Qlong', 'cloudiness':'clou', 'ehf':'Qeva', 'fcehf':'Qfreva',
    # For Single Layer Dynamic map: Hydrodynamics
    'wl_single_dynamic':'mesh2d_s1', 'wd_single_dynamic':'mesh2d_waterdepth',
    # For Multi Layer Dynamic map: Physical options
    'temp_multi_dynamic':'mesh2d_tem1', 'sal_multi_dynamic':'mesh2d_sa1', 'cont_multi_dynamic':'mesh2d_Contaminant',
}
UNITS = {
    # 1. Physical
    # 1.1. Conservative and Decaying Tracers
    'cTR1': 'Conservative Tracer 1 (g/m³)', 'dTR1': 'Decaying Tracer 1 (g/m³)',
    'cTR2': 'Conservative Tracer 2 (g/m³)', 'dTR2': 'Decaying Tracer 2 (g/m³)',
    'cTR3': 'Conservative Tracer 3 (g/m³)', 'dTR3': 'Decaying Tracer 3 (g/m³)',
    'RcDecTR1': 'Decay rate tracer1 for AGE calculations (1/day)',
    'RcDecTR2': 'Decay rate tracer2 for AGE calculations (1/day)',
    'RcDecTR3': 'Decay rate tracer3 for AGE calculations (1/day)',
    # 1.2. Suspended Sediment
    'IM1': 'Inorganic Matter (IM1) (gDM/m³)', 'IM1S1': 'IM1 in layer S1 (g/m²)',
    'IM2': 'Inorganic Matter (IM2) (gDM/m³)', 'IM2S1': 'IM2 in layer S1 (g/m²)',
    'IM3': 'Inorganic Matter (IM3) (gDM/m³)', 'IM3S1': 'IM3 in layer S1 (g/m²)',
    'VSedIM1': 'Sedimentation velocity IM1 (m/day)', 'TaucSIM1': 'Critical shear stress for sedimentation IM1 (N/m²)',
    'VSedIM2': 'Sedimentation velocity IM2 (m/day)', 'TaucSIM2': 'Critical shear stress for sedimentation IM2 (N/m²)',
    'VSedIM3': 'Sedimentation velocity IM3 (m/day)', 'TaucSIM3': 'Critical shear stress for sedimentation IM3 (N/m²)',
    'TaucRS1DM': 'Critical shear stress for resuspension DM layer S1 (N/m²)',
    # 2. Chemical 
    # 2.1. Simple Oxygen
    'NH4': 'Ammonium (g/m³)', 'CBOD5': 'Carbonaceous BOD (g/m³)', 'DO': 'Dissolved Oxygen concentration (g/m³)',
    'OXY': 'Dissolved Oxygen (g/m³)', 'SOD': 'Sediment Oxygen Demand (g/m²)',
    'RcNit': 'First-order Nitrification Rate (1/day)', 'RcBOD': 'Decay rate BOD (first pool) at 20°C (1/day)',
    'COXBOD': 'Critical oxygen concentration for BOD decay (g/m³)', 'OOXBOD': 'Optimum Oxygen concentration for BOD decay (g/m³)',
    'CFLBOD': 'Oxygen function level for Oxygen below COXBOD', 'O2FuncBOD': 'Oxygen function for CBOD decay',
    'BODu': 'Calculated Carbonaceous BOD at ultimate (g/m³)', 'SWRear': 'Switch for Oxygen reaeration formulation',
    'KLRear': 'Reaeration transfer coefficient (m/day)', 'fSOD': 'Zeroth-order sediment Oxygen demand flux (g/m²/day)',
    'RcSOD': 'Decay rate SOD at 20°C (1/day)', 'Temp': 'Ambient water Temperature (°C)', 'VWind': 'Wind speed (m/s)',
    # 2.2. Oxygen with Biochemical Oxygen Demand (BOD) (water phase only)
    'Salinity': 'Salinity (g/kg)', 'RcBOD': 'Decay rate BOD (first pool) at 20°C (1/day)',
    'Phyt': 'Total carbon in phytoplankton (g/m³)', 'SaturOXY': 'Saturation Concentration (g/m³)',
    'SatPercOXY': 'Actual Saturation Percentage O2 (%)',
    # 2.3. Cadmium
    'Cd': 'Cadmium (g/m³)', 'CdS1': 'Cadmium in S1 (g/m²)', 'ZResDM': 'Zeroth-order resuspension flux DM (g/m²/day)',
    # 2.4. Eutrophication (Eutrof 1a model)
    'AAP': 'Adsorbed Ortho Phosphate (g/m³)', 'DetC': 'Detritus Carbon (g/m³)', 'DetN': 'Detritus Nitrogen (g/m³)',
    'DetP': 'Detritus Phosphorus (g/m³)', 'GREEN':'Algae (non-Diatoms) (g/m³)', 'NO3':'Nitrate (g/m³)',
    'PO4': 'Ortho-Phosphate (g/m³)', 'Cl':'Chloride (g/m³)', 'Opal':'Inorganic Silica (g/m³)',
    'SWAdsP': 'Switch for Adsorbed Phosphate', 'KdPO4AAP': 'distrib. coeff. (-) or ads. eq. const. (m³/g)',
    'RcDetC': 'First-order mineralisation rate DetC (1/day)', 'TcDetC': 'Temperature coefficient for mineralisation DetC',
    'CTMin': 'Critical temperature for mineralisation (°C)', 'NCRatGreen': 'N:C ratio Greens (gN/gC)',
    'PCRatGreen': 'P:C ratio Greens (gP/gC)', 'FrAutGreen': 'Fraction autolysis Greens',
    'FrDetGreen': 'Fraction to detritus by mortality Greens', 'VSedDetC': 'Sedimentation velocity DetC (m/day)',
    'TauCSDetC': 'Critical shear stress for sedimentation DetC (N/m²)', 'RcDetN': 'First-order mineralisation rate DetN (1/day)',
    'TcDetN': 'Temperature coefficient for mineralisation DetN', 'RcDetP': 'First-order mineralisation rate DetP (1/day)',
    'MRespGreen': 'Maintenance respiration Greens st.temp', 'GRespGreen': 'Growth respiration factor Greens',
    'Mort0Green': 'Mortality rate constant Greens', 'SalM1Green': 'Lower salinity limit for mortality Greens',
    'SalM2Green': 'Upper salinity limit for mortality Greens (g/kg)', 'TcNit': 'Temperature coefficient for nitrification',
    'CTNit': 'Critical Temperature for nitrification (°C)', 'COXNIT': 'Critical Oxygen concentration for nitrification (g/m³)',
    'OOXNIT': 'Optimum Oxygen concentration for nitrification (g/m³)', 'TcDenWat': 'Temperature coefficient for denitrification',
    'COXDEN': 'Critical Oxygen concentration for denitrification (g/m³)', 'RcDenWat': 'First-order denitrification rate in water column (1/day)',
    'OOXDEN': 'Optimum Oxygen concentration for denitrification (g/m³)', 'TCRear': 'Temperature coefficient for rearation',
    'O2FuncBOD': 'Oxygen function for CBOD decay', 'fResS1DM': 'Total resuspension flux DM from layer S1 (g/m²/day)',
    'ExtVlIM1': 'VL specific extinction coefficient M1 (m²/gDM)', 'ExtVlBak': 'Background extinction visible light (1/m)',
    'DayL': 'Daylength (0-1) (day)', 'OptDLGreen': 'Daylength for growth saturation Greens (day)',
    'PrfNH4gree': 'Ammonium preferency over nitrate Greens', 'KMDINgreen': 'Half-saturation value N Greens (gN/m³)',
    'KMPgreen': 'Half-saturation value P Greens (gP/m³)', 'RadSatGree': 'Total radiation growth saturation greens (W/m²)',
    'TcGroGreen': 'Temperature coefficient for processes Greens', 'ExtVlDetC': 'VL specific extinction coefficient DetC (m²/gC)',
    'ExtVlGreen': 'VL specific extinction coefficient Greens (m²/gC)', 'RadSurf': 'Irradiation at the water surface (W/m²)',
    'Chezy': 'Chezy coefficient (m^0.5/s)', 'AlgN': 'Total Nitrogen in algae (gN/gC)', 'AlgP': 'Total Phosphorus in algae (gP/gC)',
    'SS': 'Suspended Solids (g/m³)', 'TotN': 'Total Nitrogen (including algae) (g/m³)', 'TotP': 'Total Phosphorus (including algae) (g/m³)',
    'KjelN': 'Kjeldahl Nitrogen (g/m³)', 'LimDLGreen': 'Daylength limitation function for Greens (0-1)',
    'LimNutGree': 'Nutrient limitation function for Greens (0-1)', 'LimRadGree': 'Radiation limitation function for Greens (0-1)',
    'Chlfa': 'Chlorophyll-a concentration (g/m³)', 'ExtVlPhyt': 'VL extinction by Phytoplankton (m²/gC)',
    # 2.5. Trace Metals
    'ASWTOT': 'Total Arsenic (g/m³)', 'CUWTOT': 'Total Copper (g/m³)', 'NIWTOT': 'Total Nickel (g/m³)', 'PBWTOT': 'Total Lead (g/m³)',
    'POCW': 'POC in water (g/m³)', 'AOCW': 'Algen Koolstof (g/m³)', 'DOCW': 'Opgelost organisch C waterkolom (g/m³)',
    'SSW': 'Zwevende stof water (g/m³)', 'ZNWTOT': 'Total Zinc (g/m³)', 'ASREDT': 'Arseen total gereduceerde laag (g/m³)',
    'ASSTOT': 'Arseen total aerobe laag (g/m³)', 'ASSUBT': 'Arseen total in onderlaag (g/m³)', 'CUREDT': 'Koper total gereduceerde laag (g/m³)',
    'CUSTOT': 'Koper total aerobe laag (g/m³)', 'CUSUBT': 'Koper total in onderlaag (g/m³)', 'NIREDT': 'Nikkel total gereduceerde laag (g/m³)',
    'NISTOT': 'Nikkel total aerobe laag (g/m³)', 'NISUBT': 'Nikkel total in onderlaag (g/m³)', 'PBREDT': 'Lood total gereduceerde laag (g/m³)',
    'PBSTOT': 'Lood total aerobe laag (g/m³)', 'PBSUBT': 'Lood total in onderlaag (g/m³)', 'DOCB': 'Opgelost orgamisch C toplaag (g/m³)',
    'DOCSUB': 'Opgelost orgamisch C sediment onderlaag (g/m³)', 'POCB': 'POC conc. in sediment (g/m³)', 'POCSUB': 'opgelost POC in onderlaag (g/m³)',
    'S': 'Total S', 'ZNREDT': 'Zink total gereduceerde laag (g/m³)', 'ZNSTOT': 'Zink total aerobe laag (g/m³)', 'ZNSUBT': 'Zink total in onderlaag (g/m³)',
    'aAs': 'Correctiefactor pH', 'aCu': 'Correctiefactor pH', 'aNi': 'Correctiefactor pH', 'aPb': 'Correctiefactor pH', 'aZn': 'Correctiefactor pH',
    'bAs': 'Correctiefactor CL', 'bCu': 'Correctiefactor CL', 'bNi': 'Correctiefactor pH', 'bPb': 'Correctiefactor Cl', 'bZn': 'Correctiefactor Cl',
    'alfa': 'Conversion factor algae in POC (gPOC/gAOC)', 'CUSulf': 'Constant conc. Cu-Sulfide precipitate (g/m³)', 'DZ1': 'Thickness aerobic top layer (m)',
    'DZ2': 'Thickness reduced sublayer (m)', 'Ez0': 'Effective diffusion coeff sediment/water (m²/day)', 'fbx': 'Percentage sediment < 16um',
    'fc': 'Carbon;organic matter ratio algae', 'fwx': 'Percentage suspended solids < 16 um', 'KAsDOC': 'Partitioncoeff. As on DOC (m³/gDOC)',
    'KAsSS': 'Partitioncoeff. As on SS (equivalents) (l/eq*10-6)', 'KAsSSW': 'Partitioncoeff. As on SS (m³/gSSW)', 'KCuDOC': 'Partitioncoeff. Cu on DOC (m³/gDOC)',
    'KCuSS': 'Partitioncoeff. Cu on SS (equivalents) (l/eq*10-6)', 'KCuSSW': 'Partitioncoeff. Cu on SS (m³/gSSW)', 'KdAOC': 'Phytoplankton decay rate (1/day)',
    'KHYDW': 'Snelheidsconstante hydrolyse POC water (1/day)', 'KHYDB': 'Snelheidsconstante hydrolyse POC sediment (1/day)',
    'KDMINW': 'Snelheidsconstante mineralisatie water (1/day)', 'KDMINB': 'Snelheidsconstante mineralisatie sediment (1/day)',
    'KNiDOC': 'Partitioncoeff. Ni on DOC (m³/gDOC)', 'KNiSS': 'Partitioncoeff. Ni on SS (equivalents) (l/eq*10-6)',
    'KPbDOC': 'Partitioncoeff. Pb on DOC (m³/gDOC)', 'KPbSS': 'Partitioncoeff. Pb on SS (equivalents) (l/eq*10-6)',
    'KPbSSW': 'Partitioncoeff. Pb on SS (m³/gSSW)', 'KZnDOC': 'Partitioncoeff. Zn on DOC (m³/gDOC)', 'KZnSS': 'Partitioncoeff. Zn on SS (equivalents) (l/eq*10-6)',
    'KZnSSW': 'Partitioncoeff. Zn on SS (m³/gSSW)', 'NIsulf': 'Constant conc. Ni-Sulfide precipitate (g/m³)', 'Pbsulf': 'Constant conc. Pb-Sulfide precipitate (g/m³)',
    'POR': 'Porosity', 'RHOANO': 'Density of organic matter (g/m³)', 'RHOORG': 'Density of inorganic matter (g/m³)', 'Vsp': 'Sedimentatiesnelheid algen (m/day)',
    'Vss': 'Sedimentatiesnelheid SS (m/day)', 'Vsn': 'Sedimentatiesnelheid organische stof (m/day)', 'Znsulf': 'Constant conc. Zn-Sulfide precipitate (g/m³)',
    'pHb': 'Zuurgraad waterbodem (pH)', 'PAOC': 'AOC productiesnelheid (g/m³/day)', 'Fres': 'Resuspensie flux (g/m²/day)',
    'ASatm': 'Gedistribueerde bron As (g/m²/day)', 'CUatm': 'Gedistribueerde bron Cu (g/m²/day)', 'NIatm': 'Gedistribueerde bron Ni (g/m²/day)',
    'PBatm': 'Gedistribueerde bron Pb (g/m²/day)', 'ZNatm': 'Gedistribueerde bron Zn (g/m²/day)',
    # 3. Microbial
    # 3.1. Coliform Bacteria
    'Salinity': 'Salinity (g/kg)', 'EColi': 'E.Coli bacteria (MPN/m³)', 'RcMrtEColi': 'First-order mortality rate E.Coli (1/day)',
    'ExtVl': 'Total extinction coefficient visible light (1/m)', 'DayRadSurf': 'Irradiation at the water surface (W/m²)',
    # 4. Water Quality - General
    'volume': 'Volume (m³)'
}
VAR_REVERT = {
    'tp': 'Total Precipitation (mm)', 't2m': 'Temperature (°C)',
    'u10': 'Wind (u Component) (m/s)', 'v10': 'Wind (v Component) (m/s)',
    'msl': 'Mean Sea Level Pressure (hPa)', 'tcc': 'Total Cloud Cover (%)',
    'ssrd': 'Shortwave Radiation (W/m²)', 'tisr': 'TOA Incident Solar Radiation (W/m²)',
    'Time': 'Time', 'wind_speed': 'Wind Speed (m/s)', 'wind_direction': 'Wind Direction (°)',
    'rh': 'Relative Humidity (%)'
}
FRIENDLY = {
    "Maximum area must be greater than zero":
        "Cannot triangulate the polygon: it has duplicate/overlapping vertices or zero-length edges, "
        "or the scale factor is too small. Reset the vertexes or increase the scale factor and try again.",
}
# Lớp nền (lake, catchment, rivers, bathymetry): thiếu file -> None / [] -> dashboard ẩn lựa chọn tương ứng.
# Cache theo (đường dẫn, mtime) để file được thêm / sửa / xoá sau khi server chạy vẫn được nhận ra.
CONTEXT_FILES = {
    "lake": os.path.join(FLOW_SAMPLE_DIR, "BrusdalLake.geojson"),
    "catchment": os.path.join(FLOW_SAMPLE_DIR, "catchment.geojson"),
    "rivers": os.path.join(FLOW_SAMPLE_DIR, "river.geojson"),
    "bathymetry": os.path.join(SAMPLE_DIR, "depth.csv"),
}