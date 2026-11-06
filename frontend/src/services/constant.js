export const origin = '*';
export const CENTER = [62.476969, 6.471598];
export const ZOOM = 13, L = window.L, n_decimals = 2;

export const arrowShape = new Path2D();
arrowShape.moveTo(0, 0);          // Origin
arrowShape.lineTo(1, 0);          // Main length
arrowShape.moveTo(1, 0);
arrowShape.lineTo(0.8, 0.1);      // Left branch
arrowShape.moveTo(1, 0);
arrowShape.lineTo(0.8, -0.1);     // Right branch

export const gridId = 'grid-generation-map',
    hydMapId = 'new-hyd-map', waqMapId = 'new-waq-map';

let state = {}, currentProjectId = null, pendingRequest = null, mapInstance = null;

const getKey = (projectId) => `app_state_${projectId}`;
const loadState = (projectId) => {
    const saved = localStorage.getItem(getKey(projectId));
    return saved ? JSON.parse(saved) : {};
};
export const initState = (projectId) => {
    currentProjectId = projectId; state = loadState(projectId);
};
export const getState = () => state;
export const setState = (newState) => {
    state = { ...state, ...newState };
    if (currentProjectId) saveState(currentProjectId, state);
};
const saveState = (projectId, state) => {
    localStorage.setItem(getKey(projectId), JSON.stringify(state));
};

const LAST_PROJECT_KEY = 'demo', LAST_TIMEZONE = "";
export const getLastProject = () => {
    return localStorage.getItem(LAST_PROJECT_KEY) || 'demo';
}
export const setLastProject = (projectId) => {
    localStorage.setItem(LAST_PROJECT_KEY, projectId);
}

export const getLastTimeZone = () => {
    return localStorage.getItem(LAST_TIMEZONE) || '';
}
export const setLastTimeZone = (newTimeZone) => {
    localStorage.setItem(LAST_TIMEZONE, newTimeZone);
}

export function getMap() { return mapInstance; }
export function setMap(map) { mapInstance = map; }
export function clearPendingRequest() { pendingRequest = null; }
export function getPendingRequest() { return pendingRequest; }
export function setPendingRequest(req) { pendingRequest = req; }

const defaultVisualization = { 
    hydLayer: null, sourceLayer: null, crosssectionLayer: null, 
    wqObsLayer: null, wqLoadsLayer: null, isPathQuery: false, 
    isThemocline: false, mapLayer: null, isMultiLayer: false, gisLayers: {},
    polygonCentroids: [], showedQuery: '', isClickedInsideLayer: false,
    vectorSelected: '', layerSelected: '', sigmaSelected: '', isPlaying: null, 
    lastFeatureColors: {}, featureMap: {}, isHYD: false, sigma: null
}
let stateVisualization = structuredClone(defaultVisualization);
export const getStateVisualization = () => stateVisualization;
export const setStateVisualization = (newState) => { stateVisualization = { ...stateVisualization, ...newState }; };
// Reset state
export const resetStateVisualization = () => { stateVisualization = structuredClone(defaultVisualization); };

export function valueFormatter(value, options={}) {
    const { scientificThreshold = 0.01, decimalPlaces = 2, scientificDigits = 2 } = options;
    if (!Number.isFinite(value)) return "—";
    if (Math.abs(value) < scientificThreshold && value !== 0) {
        const [mantissa, exponent] = value
            .toExponential(scientificDigits).split("e");
        return `${mantissa}×10${toSuperscript(Number(exponent))}`;
    }
    return value.toFixed(decimalPlaces);
}

export function highlightColor(id){
    const hue = (id * 57) % 360;
    return `hsl(${hue},70%,60%)`;
}

export const superscriptMap = {
    '-': '⁻', '0': '⁰', '1': '¹', '2': '²', '3': '³',
    '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹'
};

function toSuperscript(num) {
    return String(num).split('').map(ch => superscriptMap[ch] || ch).join('');
}

export function getColors(nColors){
    if (nColors === 5) return ['#0416FF', '#03FFF8', '#02FF07', '#EDFF01', '#FF1E00'];
    else if (nColors === 10) return ['#0416FF', '#0094FF', '#03DAFF', '#00A305',
        '#71E507', '#DBF400', '#FFD602', '#FF9B0F', '#FF6301', '#FF1E00'];
    else if (nColors === 15) return ['#0416FF', '#035AFF', '#039EFF', '#03E3FF', 
        '#03FFD6', '#02FF91', '#02FF4C', '#02FF07', '#41FF02', '#86FF02',
        '#CBFF01', '#FFED01', '#FFA801', '#FF6301', '#FF1E00']
    return ['#0416FF', '#0348FF', '#037AFF', '#03ADFF', '#03DFFF', '#03FFEC',
        '#03FFB9', '#02FF86', '#02FF54', '#02FF21', '#16FF02', '#49FF02', '#7BFF02',
        '#AEFF01', '#E1FF01', '#FFEA01', '#FFB701', '#FF8401', '#FF5101', '#FF1E00']
}

export const calibrationParams = [
    { name: "Water Clarity", value: "Secchidepth", checked: false, lower: 1.0, upper: 20.0 },
    { name: "Vertical Viscosity", value: "Vicoww", checked: true, lower: 0.0, upper: 5e-6 },
    { name: "Vertical Diffusivity", value: "Dicoww", checked: true, lower: 0.0, upper: 5e-6 },
    { name: "Horizontal Viscosity", value: "Vicouv", checked: true, lower: 0.0, upper: 0.5 },
    { name: "Horizontal Diffusivity", value: "Dicouv", checked: true, lower: 1e-4, upper: 1e-2 },
];
export function getCalibrationParam(value) {
    return calibrationParams.find(p => p.value === value);
}

