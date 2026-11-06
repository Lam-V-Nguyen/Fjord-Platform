import { jsonLoader, signalSender, splitLines, initOptions } from "./commonFunctions.js";
import { L, ZOOM, getStateVisualization, setStateVisualization, 
    resetStateVisualization, getMap, getLastTimeZone } from "./constant.js";
import { plotChart, plotProfileSingleLayer, plotProfileMultiLayer, 
    thermoclinePlotter } from "./chartManager.js";
import { clearMap } from "./mapManager.js";

const hoverTooltip = L.tooltip({
    permanent: false, direction: 'bottom',
    sticky: true, offset: [0, 10], className: 'custom-tooltip'
});


const $ = (id) => document.getElementById(id);
const obj = { 
    summaryContainer: $("summary-container"), summaryContent: $('summaryChart'),
    summaryTitle: $("summary-title"), timeSeriesContainer: $("time-series-container"),
    colorBarTitle: $("colorbar-title"), profileContainer: $("profile-window")
}

let objContent = null, currentProject = null, pathLine = null,
    mapObj = null, html = null, selectedMarkers = [], gridLayer = null,  
    pointContainer = [], selectedCell = null, marker = null;


export async function generalOptionsManager(projectName){
    const popupContent = document.getElementById('popup-content');
    const $$ = (id) => popupContent.querySelector(`#${id}`);
    objContent = {
        projectSummaryOption: $$('projectSummaryOption'), hydStation: $$('hyd-obs-checkbox'),
        sourceStation: $$('source-checkbox'), crossSection: $$('cross-section-checkbox'),
        waqObsStation: $$('waq-obs-checkbox'), waqLoadsStation: $$('waq-loads-checkbox'),
        pathQuery: $$('path-query-checkbox'), thermoclineOptions: $$('thermocline-row'),
        thermoclineWAQ: $$('waq-thermocline-selector'), resetConfig: $$('reset-config')
    }
    currentProject = projectName; mapObj = getMap();
    generalEvents(); pathEvents(mapObj); mapOptions(mapObj);
}

function pathEvents(mapObject) {
    objContent.pathQuery.checked = getStateVisualization().isPathQuery;
    objContent.pathQuery.addEventListener('change', () => { 
        if (objContent.pathQuery.checked) { 
            setStateVisualization({isThemocline: false}); 
            if (getStateVisualization().mapLayer === null){
                alert("No map layer available"); deActivePathQuery(mapObj);
            } else {
                mapObject.getContainer().style.cursor = "crosshair";
                mapObject.on("click", mapPath); mapObject.on("contextmenu", mapPath);
            }
        } else deActivePathQuery(mapObj);
        setStateVisualization({isPathQuery: objContent.pathQuery.checked});
    });
}

function generalEvents(){
    objContent.projectSummaryOption.addEventListener('click', async () => { 
        const content = { 
            projectName: currentProject, key: 'summary', timeZone: getLastTimeZone()
        };
        const data = await jsonLoader('process_data', content);
        if (data.status === 'error') { alert(data.message); return; }
        const currentDisplay = window.getComputedStyle(obj.summaryContainer).display;
        if (currentDisplay === "none") {
            // Create a table to display the summary
            let html = `<table><thead><tr>
                <th style="text-align: center;">Parameter</th>
                <th style="text-align: center;">Value</th>
                </tr></thead><tbody>`;
            data.content.forEach(item => {
                html += `<tr><td>${item.parameter}</td> <td>${item.value}</td></tr>`;
            });
            html += `</tbody></table>`;
            obj.summaryContent.innerHTML = html;
            obj.summaryTitle.textContent = `Project Summary - ${currentProject}`;
            // Open the summary window
            obj.summaryContainer.style.display = "flex";
        }
    });
    // 1. Hydrodynamic Observations
    checkUpdater("hydLayer", objContent.hydStation, loadHYDStations);
    // 2. Sources/Sinks Observations
    checkUpdater("sourceLayer", objContent.sourceStation, loadSourceStations);
    // 3. Cross-Section Observations
    checkUpdater("crosssectionLayer", objContent.crossSection, loadCrossSection);
    // 4. Update Water Quality Observation Points
    checkUpdater("wqObsLayer", objContent.waqObsStation, loadWAQStations);
    // 5. Update water quality observation points
    checkUpdater("wqLoadsLayer", objContent.waqLoadsStation, loadWAQLoads);
    // Add event when user clicks on the popup
    document.addEventListener('click', async function(e) {
        if (e.target && e.target.classList.contains('in-situ')) {
            e.preventDefault();
            const [query, colorbarTitle] = e.target.dataset.info.split('|');
            const chartTitle = query.split('*')[1] + ' (' + colorbarTitle.split('(')[0].trim() + ')';
            plotChart(currentProject, obj.timeSeriesContainer, query, '_in-situ', chartTitle, 'Time', colorbarTitle);
        }
        if (e.target && e.target.classList.contains('function')) {
            e.preventDefault();
            const [key, colorbarTitle] = e.target.dataset.info.split('|');
            const chartTitle = colorbarTitle.split('(')[0].trim();
            plotChart(currentProject, obj.timeSeriesContainer, '', key, chartTitle, 'Time', colorbarTitle);
        }
        // Plot thermocline for hydrodynamic simulation
        if (e.target.id === 'hyd-thermocline-selector') {
            signalSender('showOverlay', 'Preparing grid for hydrodynamic thermocline plot...');
            const titleX = 'Temperature (°C)', titleY = 'Depth (m)';
            setStateVisualization({isThemocline: true}); 
            if (getStateVisualization().isPathQuery) deActivePathQuery(mapObj);
            const chartTitle = 'Thermocline for Hydrodynamic Simulation';
            const key = 'thermocline_hyd', query = 'temp_multi_dynamic';
            await thermoclineGridCreator(
                currentProject, mapObj, key, query, titleX, titleY, chartTitle
            ); signalSender('hideOverlay');
        }
        // Plot thermocline for water quality
        if (e.target.id === 'waq-thermocline') {
            if (objContent.thermoclineOptions) {
                objContent.thermoclineOptions.style.display = 'flex';
                await initOptions(objContent.thermoclineWAQ, 'thermocline_waq', currentProject); return;
            }
        }
    });
    objContent.thermoclineWAQ.addEventListener('change', async (e) => {
        const selected = e.target.value;
        if (selected === '') return;
        setStateVisualization({isThemocline: true}); 
        if (getStateVisualization().isPathQuery) { deActivePathQuery(mapObj); }
        const titleX = e.target.options[e.target.selectedIndex].text, titleY = 'Depth (m)';
        const chartTitle = 'Vertical Profile for Water Quality Simulation';
        const key = 'thermocline_waq', query = `mesh2d_${selected}`;
        signalSender('showOverlay', 'Preparing grid for water quality thermocline plot...');
        await thermoclineGridCreator(
            currentProject, mapObj, key, query, titleX, titleY, chartTitle
        );
        signalSender('hideOverlay'); objContent.thermoclineOptions.style.display = 'none';
    });
    objContent.resetConfig.addEventListener('click', async() => {
        await jsonLoader('close_movement', {projectName: currentProject});
        const data = await jsonLoader('reset_config', {projectName: currentProject});
        resetStateVisualization(); alert(data.message); location.reload();
    });
}

// Create grid for thermocline plot and add click event to each cell
async function thermoclineGridCreator(currentProject, mapObject, key, query, titleX, titleY, chartTitle) {
    const content = { 
        key: key, query: query, type: 'thermocline_grid', 
        projectName: currentProject, timeZone: getLastTimeZone()
    };
    const data = await jsonLoader('select_thermocline', content);
    if (data.status === "error") {
        signalSender('hideOverlay'); alert(data.message); return;
    }
    gridLayer = clearMap(gridLayer, mapObject);
    gridLayer = L.geoJSON(data.content, {
        style: {color: 'black', weight: 1},
        onEachFeature: function (feature, layer) {
            layer.on('click', function (e) {
                if (selectedCell) { selectedCell.setStyle({
                    color: 'black', weight: 1, fillColor: null, fillOpacity: 0
                }); }
                layer.setStyle({
                    color: 'red', weight: 2, fillColor: 'red', fillOpacity: 0.5
                }); selectedCell = layer;
                const index = feature.properties.index;
                // Make popup HTML
                const popupContent = `
                    <div style="width:200px">
                        <h4 style="margin:0 0 6px 0;">Change Index: #${index}</h4>
                        <label>New Name:</label>
                        <input id="nameInput" type="text" value="${index}" 
                            style="width:100%;margin-bottom:6px;padding:3px;" />
                        <button id="saveBtn" 
                            style="width:100%;padding:4px;background:#007bff;
                            color:white;border:none;border-radius:4px;cursor:pointer;">
                            Plot Chart
                        </button>
                    </div>
                `;
                layer.bindPopup(popupContent).openPopup(e.latlng);
                // Add event listener to save button
                setTimeout(() => {
                    const input = document.getElementById('nameInput');
                    const saveBtn = document.getElementById('saveBtn');
                    if (input && saveBtn) {
                        saveBtn.addEventListener('click', async() => {
                            const newName = input.value;
                            if (newName !== '') {
                                const content = { 
                                    key: key, query: query, type: 'thermocline_init', 
                                    idx: index, projectName: currentProject, timeZone: getLastTimeZone()
                                };
                                const initData = await jsonLoader('select_thermocline', content);
                                layer.closePopup(); setStateVisualization({isThemocline: false});
                                if (initData.status === "error") { alert(initData.message); return; }
                                thermoclinePlotter(
                                    currentProject, obj.profileContainer, key, 
                                    initData.content, newName, titleX, titleY, chartTitle
                                );
                            } else { alert('Enter a name.'); return; }
                        });
                    }
                }, 200);
            });
        }
    }).addTo(mapObject);
}

// Load hydrodynamic observation points
async function loadHYDStations() {
    signalSender('showOverlay', 'Reading Hydrodynamic Observation Points from Database.\nPlease wait...');
    const content = { projectName: currentProject, key: 'hyd_station', timeZone: getLastTimeZone() };
    const data = await jsonLoader('process_data', content); signalSender('hideOverlay');    
    if (data.status === "error") { alert(data.message); return; }
    if (getStateVisualization().hydLayer) { mapObj.removeLayer(getStateVisualization().hydLayer); }
    // Add station layer to the map
    const indx = data.message;
    const layer = L.geoJSON(data.content, {
        // Custom marker icon
        pointToLayer: function (feature, latlng) {
            const customIcon = L.icon({
                iconUrl: `/src_frontend/images/station.png?v=${Date.now()}`,
                iconSize: [20, 20], popupAnchor: [1, -34],
            });
            const marker = L.marker(latlng, {icon: customIcon});
            const stationId = feature.properties.name || 'Unknown';
            // Add tooltip
            const info = indx.length > 0 ? '<br>Select object to see values at each layer' : '';
            const value = `<div style="text-align: center; weight: bold; font-size: 16px;"> <b>${stationId}</b>${info}</div>`;
            marker.bindTooltip(value, { permanent: false, direction: 'top', offset: [0, 0] });
            // Get name of the station
            const name = indx.find(item => item[stationId]);
            let popupContent = `<div style="max-height: 200px; overflow-y: auto;">
                <h3 style="text-align: center;">${stationId}</h3>
                <hr style="margin: 5px 0 5px 0;"><ul style="left: 0; cursor: pointer; padding-left: 0;">`;
            if (name && Array.isArray(name[stationId])) {
                name[stationId].forEach(item => {
                    const [key, value] = Object.entries(item)[0];
                    popupContent += `<li style="margin-bottom:5px; line-height:1.3; font-size: 16px;">
                        <a class="in-situ" data-info="${key}*${stationId}*station_name|${value}">• ${value}</a></li>`;
                })
            } else popupContent += `<li><em>No data available</em></li>`;
            popupContent += `</ul></div>`;
            marker.bindPopup(popupContent, {offset: [0, 40]});
            return marker;
        }
    });
    mapObj.addLayer(layer); mapObj.setView(layer.getBounds().getCenter(), ZOOM);
    return layer;
}

// Load sources/sinks observation points
async function loadSourceStations() {
    signalSender('showOverlay', 'Reading Sources/Sinks from Database.\nPlease wait...');
    const content = { projectName: currentProject, key: 'sources', timeZone: getLastTimeZone() };
    const data = await jsonLoader('process_data', content); signalSender('hideOverlay');    
    if (data.status === "error") { alert(data.message); return; }
    if (getStateVisualization().sourceLayer) { mapObj.removeLayer(getStateVisualization().sourceLayer); }
    const layer = L.geoJSON(data.content, {
        pointToLayer: function (feature, latlng) {
            const customIcon = L.icon({
                iconUrl: `/src_frontend/images/source.png?v=${Date.now()}`,
                iconSize: [20, 20], popupAnchor: [1, -34],
            });
            const marker = L.marker(latlng, {icon: customIcon});
            const sourceId = feature.properties.name || 'Unknown';
            const value = `<div style="text-align: center;"><b>${sourceId}</b></div>`;
            marker.bindTooltip(value, {
                permanent: false, direction: 'top', offset: [0, 0]
            });
            return marker;
        }
    });
    mapObj.addLayer(layer); mapObj.setView(layer.getBounds().getCenter(), ZOOM);
    return layer;
}

// Load cross-section observation path
async function loadCrossSection() {
    signalSender('showOverlay', 'Reading Cross-Sections from Database.\nPlease wait...');
    const content = { projectName: currentProject, key: 'crosssections', timeZone: getLastTimeZone() };
    const data = await jsonLoader('process_data', content); signalSender('hideOverlay');
    if (data.status === "error") { alert(data.message); return; }
    if (getStateVisualization().crosssectionLayer) { mapObj.removeLayer(getStateVisualization().crosssectionLayer); }
    const indx = data.message;
    const layer = L.geoJSON(data.content, { 
        color: 'blue', weight: 3,
        onEachFeature: function (feature, layer) {
            const name = feature.properties.name || 'Unknown';
            // Add tooltip
            const info = indx.length > 0 ? '<br>Select object to see more information' : '';
            const value = `<div style="text-align: center; weight: bold; font-size: 16px;"> <b>${name}</b>${info}</div>`;
            layer.bindTooltip(value, { permanent: false, direction: 'top', offset: [0, 0] });
            let popupContent = `<div style="max-height: 200px; overflow: auto;">
                <h3 style="text-align: center;">${name}</h3>
                <hr style="margin: 5px 0 5px 0;"><ul style="left: 0; cursor: pointer; padding-left: 0;">`;
            if (Array.isArray(indx) && indx.length > 0) {
                indx.forEach(item => {
                    const [key, value] = Object.entries(item)[0];
                    popupContent += `<li style="margin-bottom:5px; line-height:1.3; font-size: 16px;">
                        <a class="function" data-info="${key}_crs|${value}">• ${value}</a></li>`;
                })
            } else popupContent += `<li><em>No data available</em></li>`;
            popupContent += `</ul></div>`;
            layer.bindPopup(popupContent, {offset: [0, 40]});      
        }
    });
    mapObj.addLayer(layer); mapObj.setView(layer.getBounds().getCenter(), ZOOM);
    return layer;
}

async function loadWAQStations() {
    signalSender('showOverlay', 'Loading Water Quality Observation Points from Database.\nPlease wait...');
    const content = { projectName: currentProject, key: 'wq_obs', timeZone: getLastTimeZone() };
    const data = await jsonLoader('process_data', content); signalSender('hideOverlay');    
    if (data.status === "error") { alert(data.message); return; }
    if (getStateVisualization().wqObsLayer) { mapObj.removeLayer(getStateVisualization().wqObsLayer); }
    const layer = L.geoJSON(data.content, {
        // Custom marker icon
        pointToLayer: function (feature, latlng) {
            const customIcon = L.icon({
                iconUrl: `/src_frontend/images/waq_obs.png?v=${Date.now()}`,
                iconSize: [27, 27], popupAnchor: [1, -34],
            });
            const marker = L.marker(latlng, {icon: customIcon});
            const stationId = feature.properties.name || 'Unknown';
            // Add tooltip
            const value = `<div style="text-align: center; weight: bold;">
                    <b>${stationId}</b>
                </div>`;
            marker.bindTooltip(value, {
                permanent: false, direction: 'top', offset: [0, 0]
            });
            return marker;
        }
    });
    mapObj.addLayer(layer); mapObj.setView(layer.getBounds().getCenter(), ZOOM);
    return layer;
}

async function loadWAQLoads() {
    signalSender('showOverlay', 'Loading Loads of Water Quality Observation Points from Database.\nPlease wait...');
    const content = { projectName: currentProject, key: 'wq_loads', timeZone: getLastTimeZone() };
    const data = await jsonLoader('process_data', content); signalSender('hideOverlay');    
    if (data.status === "error") { alert(data.message); return; }
    if (getStateVisualization().wqLoadsLayer) { mapObj.removeLayer(getStateVisualization().wqLoadsLayer); }
    const layer = L.geoJSON(data.content, {
        // Custom marker icon
        pointToLayer: function (feature, latlng) {
            const customIcon = L.icon({
                iconUrl: `/src_frontend/images/waq_loads.png?v=${Date.now()}`,
                iconSize: [20, 20], popupAnchor: [1, -34],
            });
            const marker = L.marker(latlng, {icon: customIcon});
            const stationId = feature.properties.name || 'Unknown';
            // Add tooltip
            const value = `<div style="text-align: center; weight: bold;">
                    <b>${stationId}</b>
                </div>`;
            marker.bindTooltip(value, {
                permanent: false, direction: 'top', offset: [0, 0]
            });
            return marker;
        }
    });
    mapObj.addLayer(layer); mapObj.setView(layer.getBounds().getCenter(), ZOOM);
    return layer;
}

export function deActivePathQuery(mapObject) {
    if (objContent.pathQuery !== null) { objContent.pathQuery.checked = false; }
    setStateVisualization({isPathQuery: false});
    if (pathLine) { mapObject.removeLayer(pathLine); pathLine = null;}
    selectedMarkers.forEach(m => mapObject.removeLayer(m));
    selectedMarkers = []; pointContainer = [];
    mapObject.getContainer().style.cursor = ""; 
    setStateVisualization({mapLayer: null});
}

async function mapPath(e) {
    if (!getStateVisualization().isPathQuery) return;
    // Right-click
    if (e.type === "contextmenu") {
        e.originalEvent.preventDefault(); // Suppress context menu
        if (pointContainer.length < 2) { alert("Please select at least two points"); return; }
        if (!getStateVisualization().isMultiLayer){
            const titleY = obj.colorBarTitle.textContent;
            const title = 'Profile - Single Layer';
            plotProfileSingleLayer(
                obj.timeSeriesContainer, pointContainer, 
                getStateVisualization().polygonCentroids, title, 'Distance (m)', titleY
            );
        } else {
            const orderedPoints = splitLines(pointContainer, getStateVisualization().polygonCentroids, 20)
                .map(([dist, , lat, lng]) => [dist, lat, lng]);
            if (orderedPoints.length === 0) { alert("No intersected mesh found"); return; }
            signalSender('showOverlay', 'Acquiring selected meshes from Database.\nPlease wait...');
            const key = !getStateVisualization().isHYD ? 'hyd' : 'waq';
            const unit = obj.colorBarTitle.textContent.split('(')[1].trim().split(')')[0].replace(')', '');
            const title = `Profile - ${obj.colorBarTitle.textContent.split('(')[0].trim()}`;
            const query = getStateVisualization().showedQuery;
            const queryContents = {key: key, query: query, idx: 'load', 
                points: orderedPoints, projectName: currentProject};
            const data = await jsonLoader('select_meshes', queryContents);
            if (data.status === "error") { alert(data.message); return; }
            plotProfileMultiLayer(currentProject, obj.profileContainer, key, query, data.content, title, unit);
            signalSender('hideOverlay');
        }
        pointContainer = [];
    }
    // Left-click
    if (e.type === "click" && e.originalEvent.button === 0) {
        // Check if clicked inside layer
        if (getStateVisualization().isClickedInsideLayer) {
            if (getStateVisualization().isPathQuery) {
                // Add marker
                marker = L.circleMarker(e.latlng, {
                    radius: 5, color: 'blue', fillColor: 'cyan', fillOpacity: 0.9
                }).addTo(mapObj);
                selectedMarkers.push(marker);
                // Add point
                pointContainer.push({ lat: e.latlng.lat, lng: e.latlng.lng });
                // Plot line
                const latlngs = pointContainer.map(p => [p.lat, p.lng]);
                if (pathLine && mapObj.hasLayer(pathLine)) { pathLine.setLatLngs(latlngs);
                } else { pathLine = L.polyline(latlngs, { 
                    color: 'orange', weight: 2, dashArray: '5,5' }).addTo(mapObj); 
                }
            }
        }
        setStateVisualization({isClickedInsideLayer: false}); // Reset clicked inside layer
    }
}

function checkUpdater(setLayer, objCheckbox, checkFunction){
    objCheckbox.checked = getStateVisualization()[setLayer] !== null;
    objCheckbox.addEventListener('change', async (e) => {
        if (e.target.checked) { 
            const layer = await checkFunction();
            setStateVisualization({[setLayer]: layer});
        } else { 
            const currentLayer = getStateVisualization()[setLayer];
            if (currentLayer) { mapObj.removeLayer(currentLayer); }
            setStateVisualization({[setLayer]: null}); }
    });
}

function mapOptions(mapObject) {
    mapObject.on('mousemove', function (e) { 
        if (getStateVisualization().isPathQuery) {
            html = `- Click the left mouse button to draw a profile.<br>- Right-click to finish.`;
        } else if (getStateVisualization().isThemocline) {
            html = `- Click the left mouse button to select a point.<br>- Then change the name (optional).`;
        } else { 
            mapObject.closeTooltip(hoverTooltip); 
            mapObject.getContainer().style.cursor = ""; return; 
        }
        hoverTooltip.setLatLng(e.latlng).setContent(html);
        mapObject.openTooltip(hoverTooltip);
    });
}