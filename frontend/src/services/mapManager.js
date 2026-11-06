import { CENTER, ZOOM, L, getPendingRequest, clearPendingRequest, origin } from "./constant.js";
import { signalSender, jsonLoader } from "./commonFunctions.js";

const layerConfig = {
    terrainLayer: {
        getLayer: () => terrainLayer, setLayer: (l) => terrainLayer = l,
        title: 'Raw Terrain (m)', colorKey: 'terrain', min: 0, max: 0,
        alert: 'Please upload terrain data first.'
    },
    streamLayer: {
        getLayer: () => streamLayer, setLayer: (l) => streamLayer = l,
        title: 'Streams', colorKey: 'terrain', min: 0, max: 1,
        alert: 'Please upload terrain data first.'
    },
    soilLayer: {
        getLayer: () => streamLayer, setLayer: (l) => streamLayer = l,
        title: 'Soil', colorKey: 'terrain', min: 0, max: 0,
        alert: 'Please upload soil data first.'
    },
    catchmentLayer_Vector: {
        layer: null, data: null,
        getLayer() { return this.layer; }, setLayer(l) { this.layer = l; },
        getData() { return this.data; }, setData(d) { this.data = d; },
    },
    landLayer_Vector: {
        layer: null, data: null,
        getLayer() { return this.layer; }, setLayer(l) { this.layer = l; },
        getData() { return this.data; }, setData(d) { this.data = d; },
    },
    riverLayer_Vector: {
        layer: null, data: null,
        getLayer() { return this.layer; }, setLayer(l) { this.layer = l; },
        getData() { return this.data; }, setData(d) { this.data = d; },
    },
    lakeLayer_Vector: {
        layer: null, data: null,
        getLayer() { return this.layer; }, setLayer(l) { this.layer = l; },
        getData() { return this.data; }, setData(d) { this.data = d; },
    }
}


export let currentMap;
let currentTileLayer = null, timeCounter = null, html = '', markersObs = [],
    markerCrossSection = [], currentPoints = [], markerBoundary = [],
    pathCrossSection = null, pathBoundary = null, currentPointsCross = [],
    currentPointsBoundary = [], waqObs = [], waqLoads = [], mapContainer = null,
    markerLayer = null, terrainLayer = null, streamLayer = null, gridLayer = null,
    isPourpointActive = false;
const configCrossSectionPoint = { color: 'blue', fillColor: 'yellow', radius: 4, fill: true, fillOpacity: 1 },
    configBoundaryPoint = { color: 'red', fillColor: 'green', radius: 4, fill: true, fillOpacity: 1 },
    configCrossSectionPath = { color: 'blue', weight: 2, dashArray: '5,5' },
    configBoundaryPath = { color: 'red', weight: 2 };

const hoverTooltip = L.tooltip({
    permanent: false, direction: 'bottom', sticky: true,
    offset: [0, 10], className: 'custom-tooltip'
});

function iconAdd(iconUrl, markers, map, pointList) {
    if (!pointList || pointList.length === 0) return;
    const customIcon = iconUrl ? L.icon({
        iconUrl: iconUrl, iconSize: [20, 20], popupAnchor: [1, -34],
    }) : null;
    // Add new markers
    pointList.forEach(row => {
        const [name, lat, lon] = row;
        if (!name || isNaN(lat) || isNaN(lon)) return;
        const markerOptions = customIcon ? { icon: customIcon } : {};
        const marker = L.marker(
            [parseFloat(lat), parseFloat(lon)], markerOptions
        ).addTo(map);
        marker.bindPopup(name); markers.push(marker); markerLayer.addLayer(marker);
    })
}

function lineAdd(pointContainer, map, lineType) {
    const latlngs = pointContainer
        .map(p => {
            const lat = parseFloat(p[1]), lon = parseFloat(p[2]);
            if (isNaN(lat) || isNaN(lon)) { return null; }
            return [lat, lon];
        })
        .filter(Boolean);
    if (latlngs.length < 2) return;
    if (lineType === 'crossSection') {
        pathCrossSection = L.polyline(latlngs, configCrossSectionPath).addTo(map);
        currentMap.fitBounds(pathCrossSection.getBounds());
    } else if (lineType === 'boundary') {
        pathBoundary = L.polyline(latlngs, configBoundaryPath).addTo(map);
        currentMap.fitBounds(pathBoundary.getBounds());
    }
}

export async function renderPreview(request = null) {
    currentPoints.length = 0; if (!request) return;
    const type = request.type, requestId = request.requestId;
    if (requestId === 'updateObsPoint') {
        markerLayer.clearLayers();
        const iconUrl = `/src_frontend/images/station.png?v=${Date.now()}`;
        iconAdd(iconUrl, markersObs, currentMap, request.content.rows);
    } else if (requestId === 'pickPath') {
        const pointList = request.content.rows, lineType = request.content.lineType;
        if (!pointList || pointList.length === 0) return;
        lineAdd(pointList, currentMap, lineType);
    } else if (requestId === 'clearCrossSection') {
        if (pathCrossSection) {
            pathCrossSection.remove(); pathCrossSection = null;
        }
        if (markerCrossSection.length > 0) {
            markerCrossSection.forEach(marker => marker.remove());
            markerCrossSection.length = 0;
        }
        currentPointsCross.length = 0; currentPoints.length = 0;
    } else if (requestId === 'clearBoundary') {
        if (pathBoundary) {
            pathBoundary.remove(); pathBoundary = null;
        }
        if (markerBoundary.length > 0) {
            markerBoundary.forEach(marker => marker.remove());
            markerBoundary.length = 0;
        }
        currentPointsBoundary.length = 0; currentPoints.length = 0;
    } else if (requestId === 'waqPoint' || requestId === 'loadsPoint') {
        let iconUrl = null;
        if (requestId === 'waqPoint') {
            iconUrl = `/src_frontend/images/waq_obs.png?v=${Date.now()}`;
            waqObs.forEach(marker => marker.remove()); waqObs.length = 0;
            iconAdd(iconUrl, waqObs, currentMap, request.content.rows);
        } else {
            iconUrl = `/src_frontend/images/waq_loads.png?v=${Date.now()}`;
            waqLoads.forEach(marker => marker.remove()); waqLoads.length = 0;
            iconAdd(iconUrl, waqLoads, currentMap, request.content.rows);
        }
    } else if (requestId === 'gridPlot') {
        // Plot unstructure grid on map
        const currentProject = request.content.projectName;
        const gridName = request.content.gridName, key = request.content.key;
        signalSender('showOverlay', `Preparing Unstructure Grid. Please wait...`);
        const content = { projectName: currentProject, gridName: gridName, key: key };
        const data = await jsonLoader('unstructure_grid_plot', content);
        signalSender('hideOverlay'); gridLayer = clearMap(gridLayer, currentMap);
        if (data.status === "error") { alert(data.message); return; }
        gridLayer = L.geoJSON(data.content, {
            style: {
                color: 'black', weight: 1, fillColor: 'transparent',
                fillOpacity: 0.2, opacity: request.content.opacity
            },
        }).addTo(currentMap);
    } else if (requestId === 'clearGridMap') {
        if (gridLayer) { gridLayer = clearMap(gridLayer, currentMap); }
    }
}

export function initMap(mapId = 'map') {
    currentMap = L.map(`leaflet-${mapId}`, {
        center: CENTER, zoom: ZOOM, zoomControl: false, attributionControl: true, preferCanvas: true
    });
    currentTileLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(currentMap);
    // Add scale bar 
    L.control.scale({ imperial: false, metric: true, maxWidth: 200 }).addTo(currentMap);
    setTimeout(() => currentMap.invalidateSize(), 100);
    markerLayer = L.layerGroup().addTo(currentMap);
    // Prepare base map
    const container = document.querySelector(`#leaflet-${mapId}`);
    const baseMapBtn = container.querySelector('.leaflet-basemap-btn');
    const baseMapPopup = container.querySelector('.basemap-popup');
    baseMapBtn.addEventListener('mouseenter', () => {
        baseMapPopup.classList.add('show'); clearTimeout(timeCounter);
        // Hide the popup after 4 seconds 
        timeCounter = setTimeout(() => {
            baseMapPopup.classList.remove('show');
        }, 4000);
    });
    // Change base map 
    baseMapPopup.addEventListener('click', (e) => {
        if (e.target.classList.contains('basemap-option')) {
            const url = e.target.dataset.url;
            currentTileLayer.setUrl(url);
            baseMapPopup.classList.remove('show');
        }
    });
    mapContainer = currentMap.getContainer();
    currentMap.on('mousemove', async (e) => {
        const req = getPendingRequest();
        if (!req) return;
        if (req.requestId === 'waqUpdate' || req.requestId === 'loadsUpdate') return;
        mapContainer.style.cursor = 'crosshair';
        if (req.requestId === 'pickLocation') {
            html = 'Pick average latitude';
        } else if (req.requestId === 'pickPoint') {
            html = 'Select an HYD point';
        } else if (req.requestId === 'pickPath') {
            html = `
            - Click the left mouse button to select points.<br>
            - Right-click to finish the selection.<br>
            - Number of points must be at least 2.<br>
            `;
        } else if (req.requestId === 'pickSource') {
            html = 'Select a HYD source';
        } else if (req.requestId === 'waqPoint') {
            html = '- Select a WAQ observation point.<br>- Point must be inside the area covered by the grid.';
        } else if (req.requestId === 'loadsPoint') {
            html = '- Select a WAQ load point.<br>- Point must be inside the area covered by the grid.';
        } else if (req.requestId === 'drawChecked') {
            html = 'Draw a polygon using the left mouse button.';
        } else if (req.requestId === 'updateObsPoint') {
            mapContainer.style.cursor = 'grab'; return;
        }
        hoverTooltip.setLatLng(e.latlng).setContent(html);
        currentMap.openTooltip(hoverTooltip);
    });
    currentMap.on('click', (e) => {
        let result = null;
        const req = getPendingRequest(); if (!req) return;
        if (req.requestId === 'pickLocation') {
            result = Number(e.latlng.lat).toFixed(2);
        } else if (req.requestId === 'pickPoint' || req.requestId === 'pickSource'
            || req.requestId === 'waqPoint' || req.requestId === 'loadsPoint') {
            result = e.latlng;
        } else if (req.requestId === 'pickPath') {
            const isCross = req.lineType === 'crossSection';
            const points = isCross ? currentPointsCross : currentPointsBoundary;
            const markerList = isCross ? markerCrossSection : markerBoundary;
            const configPoint = isCross ? configCrossSectionPoint : configBoundaryPoint;
            const configPath = isCross ? configCrossSectionPath : configBoundaryPath;
            let line = isCross ? pathCrossSection : pathBoundary;
            // Add point
            currentPoints.push({ lat: e.latlng.lat, lng: e.latlng.lng });
            points.push({ lat: e.latlng.lat, lng: e.latlng.lng });
            // Add marker
            const marker = L.circleMarker(e.latlng, configPoint).addTo(currentMap);
            markerList.push(marker);
            if (points.length < 2) return;
            const latlngs = points.map(p => [p.lat, p.lng]);
            // Draw/update line
            if (line) {
                line.setLatLngs(latlngs); line.setStyle(configPath);
            } else {
                line = L.polyline(latlngs, configPath).addTo(currentMap);
                if (isCross) { pathCrossSection = line; } else { pathBoundary = line; }
            }
        } else if (req.content.key === 'pourpoint' && isPourpointActive) {
            result = e.latlng;
            markerLayer = clearMap(markerLayer, currentMap);
            markerLayer = L.circleMarker(e.latlng, {
                radius: 4, fillColor: 'blue', color: 'red', weight: 2, opacity: 1, fillOpacity: 1
            }).addTo(currentMap);
            signalSender('updateUIState', {
                requestId: req.requestId, result: result
            });
            currentMap.closeTooltip(hoverTooltip);
            isPourpointActive = false; clearPendingRequest();









            // if (req.content.key === 'refineChecked' && req.content.checked) {
            //     // if (!currentPoints.includes(feature.properties.id)) { pointContainer.push(feature.properties.id); }
            //     // if (currentPoints.length === 1) { html = "Select end point to refine."; }
            //     // if (currentPoints.length === 2) { 
            //     //     await polygonRefinement(currentPoints); currentPoints = []; 
            //     //     refinementCheckbox().dispatchEvent(new Event('change'));
            //     //     if (hoverTooltip) lakeMap.closeTooltip(hoverTooltip); return; 
            //     // }
            // }
            // console.log(req);


        }



        if (req.requestId !== 'pickPath') {
            req.source.postMessage({ requestId: req.requestId, result: result }, origin);
            clearPendingRequest(); mapContainer.style.cursor = 'grab'; currentMap.closeTooltip(hoverTooltip);
        }
    });
    currentMap.on('contextmenu', async (e) => {
        e.originalEvent.preventDefault();
        const req = getPendingRequest(); if (!req) return;
        // Right-click
        if (req.requestId === 'pickPath') {
            if (currentPoints.length < 2) {
                alert("Not enough points selected.\nPlease select at least 02 points."); return;
            }
            req.source.postMessage({ requestId: req.requestId, result: currentPoints }, origin);
            // } else if (req.requestId === 'drawChecked') {
            //     if (currentPoints.length < 3) {
            //         alert("Polygon must have at least 3 points."); return;
            //     }
            //     tempLine = clearMap(tempLine, currentMap);
            //     polygonLayer = clearMap(polygonLayer, currentMap);
            //     // Plot polygon
            //     await pointsToPolygon(
            //         req.content.currentProject, currentPoints, polygonLayer, 
            //         pointLayer, currentMap, req.content.action
            //     ); 
            //     // drawChecked = false;




        }
        clearPendingRequest(); currentPoints.length = 0;
        mapContainer.style.cursor = 'grab'; currentMap.closeTooltip(hoverTooltip);
    });
}

export function clearMap(layer, map) {
    if (layer) { map.removeLayer(layer); }
    return null;
}

// function resetMap(map) {
//     // Hide all layers
//     Object.keys(layerConfig).forEach(key => {
//         let subLayer = layerConfig[key].getLayer();
//         if (subLayer) map.removeLayer(subLayer);
//     });
// }