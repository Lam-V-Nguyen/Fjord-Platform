
variablesNames = {
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

units = {
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
