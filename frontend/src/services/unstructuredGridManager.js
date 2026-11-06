import { setupTabs } from "./tabManager.js";
import { initMap } from "./visualizationMap.js";
import { getUser, signalSender, jsonLoader, deleteTable,
    fillTable, addRowToTable, getProjectList
} from "./commonFunctions.js";
import { gridPlotter, polygonPlotter, toggleMoveMode, orthoPlotter,
    getColorFromValue, updateColorbar, plotUnstructuredGrid
} from "./unstructuredGrid.js";


const $ = (id) => document.getElementById(id);
const obj = {
    selectContainer: $("select-container"), municipalityName: $("municipality-name"), 
    municipalityList: $("municipality-list"), lakeSearcher: $("lake-search"), 
    sugesstionLake: $("lake-suggestions"), lakeLabel: $("lake-name-label"), 
    lakeSelector: $("lake-name"), lakeTable: $("lake-table"), 
    depthGridCheckbox: $("depth-grid-checkbox"), exportDepth: $("get-depth-btn"),
    tableContent: $("table-of-contents"), menuContent: $("menu-container"),
    polygonCheckbox: $("polygon-checkbox"), depthCheckbox: $("depth-checkbox"),
    vertexesBtn: $("vertexes-btn"), refinementCheckbox: $("refinement-checkbox"),
    refinementContainer: $("refinement-container"), refinementValue: $("refinement-value"),
    moveCheckbox: $("move-checkbox"), deleteCheckbox: $("delete-checkbox"),
    scaleSelector: $("scale-factor"), scaleFactor: $("custom-scale-factor"),
    orthoCheckbox: $("orthogonality-checkbox"), createGrid: $("generate-grid"),
    chartDiv: $("myChart-grid"), saveGrid: $("save-grid"), reCheckGrid: $("recheck-grid"), 
    interpolateDelete: $("interpolate-grid-delete"), interpolateHide: $("interpolate-grid-hide"),
    interpolateGrid: $("interpolate-grid"), interpolateMapBtn: $("interpolate-grid-pick-btn"),
    interpolateCSVBtn: $("interpolate-grid-csv-btn"), interpolateFile: $("interpolate-grid-csv"),
    iterationValue: $("iterations"), valueFrom: $("detail-level-from"), 
    valueTo: $("detail-level-to"), optimizeBtn: $("optimize-grid"), reCheckGridFile: $("recheck-grid-file"),
    progressbarGrid: $("progressbar-grid"), progressTextGrid: $("progress-text-grid"),
    baseMap: $("basemap-btn"), leafletMap: $("leaflet-map-lakes"), saveDepth: $("export-depth-btn"),
    leafletContainer: $("map-container"), plotContainer: $("plot-container"), 
    colorBarContainer: $("custom-colorbar-grid"), colorBarColor: $("colorbar-gradient"),
    colorBarTitle: $("colorbar-title"), colorBarLabel: $("colorbar-label"),
    optimizeCloseBtn: $("optimization-close"), 

};

const row = ['Name', 'Municipality', 'Area', 'Perimeter', 
    'Max Depth', 'Min Depth', 'Average Depth'];
let currentProject = null, mapContainer = null, timeOut = null, lakesData = {}, 
    dataLake = null, dataDepth = null,gridPoints = null, drawSelection = false, 
    drawChecked = false, entireNorway = false, interpolateCheked = null, 
    interpolateDeleteChecked = false, interpolatedPointLayer = null, 
    interpolatedLayer = null, interpolatedPoints = [], depthPointLayer = null,
    depthLayer = null, lakeLayer = null, gridLayer = null, orthoLayer = null,
    refineChecked = false, pointContainer = [], html = null, tempLine = null,
    moveChecked = false, deleteChecked = false, levelValue = null, lakeList = [],
    activeProject = null, isRunning = false, logInterval = null, pointLayer = null;

const hoverTooltip = L.tooltip({
    permanent: false, direction: 'bottom',
    sticky: true, offset: [0, 10], className: 'custom-tooltip'
});

setupTabs(document); await getProject();
const mapObj = await initMap('leaflet-map-grid'); 
mapOptions(mapObj); lakeOptions(); dataBaseOptions(); unGridManager(); 

async function getProject() { 
    const user = await getUser();
    currentProject = user.split('/').pop();
}

async function mapOptions(map) { 
    mapContainer = map.getContainer(); pointLayer = L.layerGroup().addTo(map);
    interpolatedLayer = L.layerGroup().addTo(map);
    map.on('mousemove', function (e) { 
        mapContainer.style.cursor = "grab";
        if (refineChecked) {
            if (pointContainer.length === 0) { html = "Select start point to refine<br>Id of the start point < Id of the end point"; }
            hoverTooltip.setLatLng(e.latlng).setContent(html);
            map.openTooltip(hoverTooltip);
        }
        if (deleteChecked) {
            if (pointContainer.length === 0) { 
                html = "Select start point to delete.<br>Id of the start point < Id of the end point"; 
            }
            hoverTooltip.setLatLng(e.latlng).setContent(html);
            map.openTooltip(hoverTooltip);
        }
        if (drawChecked) { 
            mapContainer.style.cursor = "crosshair";
            if (pointContainer.length === 0) { 
                html = `Draw a polygon with the left-mouse button`; 
            } else { html = `Finish drawing with the right-mouse button`; }
            hoverTooltip.setLatLng(e.latlng).setContent(html);
            map.openTooltip(hoverTooltip);
        } 
        if (moveChecked) { 
            mapContainer.style.cursor = "move";
            html = `Move a vertex using the left-mouse button`;
            hoverTooltip.setLatLng(e.latlng).setContent(html);
            map.openTooltip(hoverTooltip);
        }
        if (interpolateCheked) { 
            mapContainer.style.cursor = "crosshair";
            if (interpolatedPoints.length === 0) { 
                html = `Click on map to add a point and depth`;
            } else { html = `Finish selecting with the right-mouse button`; }
            hoverTooltip.setLatLng(e.latlng).setContent(html);
            map.openTooltip(hoverTooltip);
        }
    });
    map.on('click', async function (e) {
        if (drawChecked) { 
            mapContainer.style.cursor = "crosshair";
            // Add marker
            const marker = L.circleMarker(e.latlng, {
                radius: 5, color: 'red', fillColor: 'pink', fillOpacity: 0.9
            });
            marker.addTo(pointLayer);
            pointContainer.push([e.latlng.lat, e.latlng.lng]);
            // Plot polygon
            if (tempLine) { tempLine.setLatLngs(pointContainer);
            } else {
                tempLine = L.polyline(pointContainer, { 
                    color: 'red', weight: 2
                }).addTo(map);
            }
            if (hoverTooltip) map.closeTooltip(hoverTooltip); return; 
        }
        if (interpolateCheked) {
            mapContainer.style.cursor = "crosshair";
            if (!interpolatedLayer) interpolatedLayer = L.layerGroup().addTo(map);
            if (hoverTooltip) map.closeTooltip(hoverTooltip);
            const latlng = e.latlng;
            const marker = L.circleMarker(latlng, {
                radius: 3, color: 'white', weight: 1, fillColor: 'red', fillOpacity: 0.8
            });
            marker.addTo(interpolatedLayer);
            // Add form
            const container = document.createElement('div');
            container.innerHTML = `
                <div style="min-width: 200px;">
                    <div><b>Latitude:</b> ${latlng.lat.toFixed(10)}</div>
                    <div><b>Longitude:</b> ${latlng.lng.toFixed(10)}</div>
                    <label><b>Depth:</b></label>
                    <input type="number" id="depthInput" step="any" ="Enter depth"
                        style="width:100%; box-sizing:border-box;
                        margin-top:5px; margin-bottom:10px;"/>
                    <div style="display:flex; gap:8px; justify-content: -end;">
                        <button id="save-1">Save</button>
                        <button id="cancel-1">Cancel</button>
                    </div>
                </div>
            `;
            const popup = L.popup({
                closeOnClick: false, autoClose: false, maxWidth: 300
            }).setLatLng(latlng).setContent(container).openOn(map);
            const input = container.querySelector('#depthInput'); input.focus();
            // Save
            container.querySelector('#save-1').addEventListener('click', () => {
                const value = input.value.trim();
                if (value === '' || !Number.isFinite(Number(value))) {
                    alert('Please enter a valid depth value.'); input.focus(); return;
                }
                const depth = Number(value);
                const point = {
                    id: crypto.randomUUID(), lat: latlng.lat, lng: latlng.lng, 
                    Depth: depth, marker: marker
                };
                interpolatedPoints.push(point);
                marker._interpolatedIndex = interpolatedPoints.length - 1;
                marker.feature = {
                    type: 'Feature', properties: { Depth: depth },
                    geometry: { type: 'Point',
                        coordinates: [latlng.lng, latlng.lat]
                    }
                };
                marker.bindTooltip(`
                    <b>Lat:</b> ${latlng.lat.toFixed(10)}<br>
                    <b>Lon:</b> ${latlng.lng.toFixed(10)}<br>
                    <b>Depth:</b> ${depth.toFixed(2)}
                `, { sticky: true, direction: 'top' });
                marker.on('click', () => { 
                    if (interpolateDeleteChecked) { deleteInterpolatedPoint(marker); }
                    else { editDepth(marker, map); }
                });
                map.closePopup(popup);
            });
            // Cancel
            container.querySelector('#cancel-1').addEventListener('click', () => {
                marker.remove(); map.closePopup(popup);
            });
            return;
        }
    });
    map.on('contextmenu', async function (e) { 
        e.originalEvent.preventDefault();
        if (drawChecked) { 
            if (pointContainer.length < 3) { 
                alert("Polygon must have at least 3 points."); return; 
            }
            tempLine = clearMap(tempLine, map); 
            lakeLayer = clearMap(lakeLayer, map);
            // Plot polygon
            await drawPolygon(pointContainer, map); drawChecked = false;
            pointContainer = []; mapContainer.style.cursor = "auto";
            obj.menuContent.style.display = 'grid';
        }
        if (interpolateCheked) {
            interpolateCheked = false; mapContainer.style.cursor = "auto";
        }
        if (hoverTooltip) { map.closeTooltip(hoverTooltip); }
    });
}

function deleteInterpolatedPoint(marker) {
    const index = interpolatedPoints.findIndex(
        point => point.marker === marker
    );
    if (index !== -1) { interpolatedPoints.splice(index, 1); }
    marker.remove();
}

function editDepth(marker, map) {
    const latlng = marker.getLatLng();
    const currentDepth = marker.feature?.properties?.depth ?? '';
    const container = document.createElement('div');
    container.innerHTML = `
        <div style="min-width: 200px;">
            <div><b>Latitude:</b> ${latlng.lat.toFixed(10)}</div>
            <div><b>Longitude:</b> ${latlng.lng.toFixed(10)}</div>
            <label><b>Depth:</b></label>
            <input type="number" id="depthInput" step="any" ="Enter depth"
                style="width:100%; box-sizing:border-box;
                margin-top:5px; margin-bottom:10px;"/>
            <div style="display:flex; gap:8px; justify-content: -end;">
                <button id="save-2">Save</button>
                <button id="cancel-2">Cancel</button>
            </div>
        </div>
    `;
    const popup = L.popup({
        closeOnClick: false, autoClose: false, maxWidth: 300
    }).setLatLng(latlng).setContent(container).openOn(map);
    const input = container.querySelector('#depthInput');
    input.focus(); input.select(); input.value = currentDepth;
    // Save
    container.querySelector('#save-2').addEventListener('click', () => {
        const value = input.value.trim();
        if (value === '' || !Number.isFinite(Number(value))) {
            alert('Please enter a valid depth value.');
            input.focus(); return;
        }
        const newDepth = Number(value);
        if (!marker.feature) {
            marker.feature = {
                type: 'Feature', properties: {},
                geometry: { type: 'Point',
                    coordinates: [latlng.lng, latlng.lat]
                }
            };
        }
        marker.feature.properties.Depth = newDepth;
        const index = marker._interpolatedIndex;
        if (index !== undefined && interpolatedPoints[index]) { interpolatedPoints[index].depth = newDepth; }
        marker.bindTooltip(`
            <b>Lat:</b> ${latlng.lat.toFixed(10)}<br>
            <b>Lon:</b> ${latlng.lng.toFixed(10)}<br>
            <b>Depth:</b> ${newDepth}
        `, { sticky: true, direction: 'top' });
        map.closePopup(popup);
    });
    // Cancel
    container.querySelector('#cancel-2').addEventListener('click', () => {
        map.closePopup(popup);
    });
}

async function lakeOptions() {
    // Search lake
    const handleLakeSearchEvent = async (e) => { 
        const value = e.target.value.trim(); 
        if (e.type === 'input') clearTimeout(timeOut); 
        if (e.type === 'click') { 
            obj.lakeSelector.style.display = 'none'; 
            obj.lakeLabel.style.display = 'none'; 
        } 
        if (value === "") {
            obj.menuContent.style.display = "none"; resetMap(mapObj);
        }
        await addItems(currentProject, value); 
        obj.municipalityName.value = ''; 
        obj.municipalityList.style.display = "none"; 
    }; 
    ['click', 'input'].forEach(evt => { 
        obj.lakeSearcher.addEventListener(evt, handleLakeSearchEvent); 
    }); 
    obj.lakeSelector.addEventListener('change', async (e) => { 
        const lakeName = e.target.value.trim(); if (!lakeName) return;
        if (lakeName === '') { 
            obj.menuContent.style.display = "none";
        } else { obj.menuContent.style.display = "grid"; }
        resetMap(mapObj); signalSender('showOverlay', `Loading data for lake: ${lakeName}`);
        const response = await jsonLoader('load_lakes', { 
            projectName: currentProject, lakeName: lakeName 
        }); signalSender('hideOverlay');
        if (response.status === "error") { alert(response.message); return; } 
        dataLake = response.content.lake; dataDepth = response.content.depth;
        const data = dataLake.features[0].properties;
        const contents = [[
            data.name, data.region, data.area, 
            data.perimeter, data.min, data.max, data.avg
        ]]; 
        obj.tableContent.style.display = "block"; 
        fillTable(contents, obj.lakeTable, true);
        obj.polygonCheckbox.checked = true;
        obj.depthCheckbox.checked = true;
        obj.orthoCheckbox.checked = false; 
        // Plot depth grid on map
        depthLayer = clearMap(depthLayer, mapObj);
        depthLayer = gridPlotter(
            'Depth (m)', dataLake, dataDepth, mapObj, obj.colorBarContainer
        );
        lakeLayer = clearMap(lakeLayer, mapObj);
        lakeLayer = polygonPlotter(dataLake, mapObj, entireNorway, true);
    });
    // Get lake list
    lakeList = await getProjectList(currentProject, '', 'getLakes');
}

async function dataBaseOptions() {
    // Update municipality name 
    obj.municipalityName.addEventListener('change', async (e) => { 
        const selectedLake = e.target.value.trim(); entireNorway = false; 
        if (!selectedLake || selectedLake === "") { 
            e.target.dispatchEvent(new Event('click')); return; 
        }
        if (selectedLake === "All Municipalities" ) { 
            signalSender('showOverlay', 'Loading Lakes for entire Norway.\nPlease wait...');
            entireNorway = true; obj.tableContent.style.display = "none"; 
            obj.menuContent.style.display = "none"; 
            obj.lakeSelector.style.display = "none"; 
            obj.colorBarContainer.style.display = "none";
            const response = await jsonLoader('load_lakes', { 
                projectName: currentProject, lakeName: 'all'
            }); signalSender('hideOverlay'); resetMap(mapObj);
            if (response.status === "error") { alert(response.message); return; } 
            dataLake = response.content.lake; dataDepth = response.content.depth;
            lakeLayer = polygonPlotter(dataLake, mapObj, entireNorway, true);
            return;
        }
        if (Object.keys(lakesData).length === 0) { await loadLakes(currentProject); }
        obj.lakeSelector.innerHTML = lakesData[selectedLake]
            .map(name => `<option value="${name}">${name}</option>`).join(''); 
        obj.lakeSelector.value = lakesData[selectedLake][0]; 
        obj.lakeSelector.dispatchEvent(new Event('change'));
    });
    obj.municipalityName.addEventListener('click', async (e) => { 
        obj.sugesstionLake.style.display = "none"; obj.lakeSearcher.value = "";
        if (e.target.value.trim() === "") { 
            obj.lakeLabel.style.display = "none"; 
            obj.lakeSelector.style.display = "none"; 
            obj.menuContent.style.display = "none"; 
            const tbody = obj.lakeTable.querySelector("tbody"); tbody.innerHTML = '';
            deleteTable(obj.lakeTable); addRowToTable(obj.lakeTable, row);
        } 
        if (Object.keys(lakesData).length === 0) { await loadLakes(currentProject); } 
        obj.municipalityList.innerHTML = ''; 
        // Add "All Municipalities" option 
        const allLi = document.createElement("li"); 
        allLi.textContent = "All Municipalities"; allLi.style.fontWeight = "bold"; 
        allLi.dataset.value = "All Municipalities"; allLi.style.fontSize = "18px"; 
        allLi.addEventListener('mousedown', () => { 
            obj.municipalityName.value = allLi.dataset.value; 
            obj.municipalityList.style.display = "none"; 
            obj.lakeLabel.style.display = "none"; 
            obj.lakeSelector.style.display = "none"; 
            obj.municipalityName.dispatchEvent(new Event('change')); 
        }); 
        obj.municipalityList.appendChild(allLi); 
        const allHr = document.createElement("hr"); 
        allHr.style.margin = "5px 10px 5px 10px"; 
        allHr.style.borderTop = "1px solid #0414f5"; 
        obj.municipalityList.appendChild(allHr); 
        Object.keys(lakesData).forEach(p => { 
            const li = document.createElement("li"); 
            li.textContent = p; 
            li.addEventListener('mousedown', () => { 
                obj.municipalityName.value = p; 
                obj.municipalityList.style.display = "none"; 
                obj.lakeLabel.style.display = "block"; 
                obj.lakeSelector.style.display = "block"; 
                obj.municipalityName.dispatchEvent(new Event('change')); 
            }); 
            obj.municipalityList.appendChild(li); 
        });
        obj.municipalityList.style.display = "block";
    });
    obj.municipalityName.addEventListener('input', async (e) => { 
        const value = e.target.value.trim();
        if (value !== "") { 
            if (lakeList.length === 0) { 
                lakeList = await getProjectList(currentProject, '', 'getLakes'); 
            }
            const lakeFilltered = lakeList.filter(p => p.toLowerCase().startsWith(value.toLowerCase()));
            obj.municipalityList.innerHTML = "";
            lakeFilltered.forEach(p => {
                const li = document.createElement("li");
                li.textContent = p;
                li.addEventListener('mousedown', () => { 
                    e.target.value = p; obj.municipalityList.style.display = "none";
                });
                obj.municipalityList.appendChild(li);
            });
            obj.municipalityList.style.display = lakeFilltered.length > 0 ? "block": "none";
        } else { 
            obj.lakeSelector.value = ""; e.target.value = ""; 
            obj.lakeLabel.style.display = "none"; 
            obj.lakeSelector.style.display = "none"; 
            e.target.dispatchEvent(new Event('click'));
        }
    });
    obj.municipalityName.addEventListener('blur', (e) => { 
        setTimeout(() => { obj.municipalityList.style.display = "none"; }, 0); 
        if (e.target.value === "") { 
            obj.lakeLabel.style.display = "none"; obj.lakeSelector.style.display = "none"; 
        } 
    });
}

function unGridManager() {
    // Change data source 
    document.querySelectorAll('input[type="radio"]').forEach(opt => { 
        opt.addEventListener('change', () => { 
            interpolatedLayer = clearMap(interpolatedLayer, mapObj);
            interpolatedPointLayer = clearMap(interpolatedPointLayer, mapObj);
            obj.menuContent.style.display = 'none';
            deleteTable(obj.lakeTable); addRowToTable(obj.lakeTable, row); resetMap(mapObj);
            moveChecked = false; obj.moveCheckbox.checked = false;
            refineChecked = false; obj.refinementCheckbox.checked = false;
            obj.refinementCheckbox.dispatchEvent(new Event('change'));
            obj.depthCheckbox.checked = false; obj.depthCheckbox.dispatchEvent(new Event('change'));
            obj.orthoCheckbox.checked = false; obj.orthoCheckbox.dispatchEvent(new Event('change'));
            if (opt.id === 'new-database') { 
                obj.selectContainer.style.display = 'flex';
                obj.tableContent.style.display = 'block';
                obj.municipalityName.value = ''; obj.lakeSearcher.value = ''; 
                obj.lakeSelector.style.display = 'none'; 
                obj.lakeSelector.value = ''; obj.lakeLabel.style.display = 'none';
                drawSelection = false; drawChecked = false;
            } else if (opt.id === 'new-map') { 
                obj.selectContainer.style.display = 'none'; 
                drawSelection = true; drawChecked = true;
                obj.colorBarContainer.style.display = 'none';
                obj.tableContent.style.display = 'block';
            }
        });
    });
    // Hide suggestions
    document.addEventListener('click', (e) => { 
        const input = obj.lakeSearcher, suggestion = obj.sugesstionLake; 
        if (!input.contains(e.target) && !suggestion.contains(e.target)) { 
            suggestion.style.display = "none"; 
        }
    });
    // Show/Hide objects
    obj.polygonCheckbox.addEventListener('change', (e) => {
        if (e.target.checked) { 
            if (!lakeLayer) {
                lakeLayer = polygonPlotter(dataLake, mapObj, entireNorway, true);
            }
        } else { 
            lakeLayer = clearMap(lakeLayer, mapObj); pointLayer = clearMap(pointLayer, mapObj);
        }
    });
    obj.depthCheckbox.addEventListener('change', async (e) => {
        if (e.target.checked) { 
            if (drawSelection) { e.target.checked = false; return; } 
            if (!depthLayer) {
                signalSender('showOverlay', 'Plotting depth grid.\nPlease wait...');
                depthLayer = gridPlotter(
                    'Depth (m)', dataLake, dataDepth, mapObj, obj.colorBarContainer
                ); signalSender('hideOverlay');
            }
        } else { 
            depthLayer = clearMap(depthLayer, mapObj);
            obj.colorBarContainer.style.display = 'none';
        }
    });
    obj.exportDepth.addEventListener('click', async () => {
        if (depthLayer === null) { alert("No depth layer found on the map.\nActive this layer and try again."); return; }
        signalSender('showOverlay', 'Exporting depth data.\nPlease wait...');
        const contents = { depthData: depthLayer.toGeoJSON() };
        const response = await jsonLoader('export_depth', contents); signalSender('hideOverlay');
        if (response.status === "error") { 
            alert(response.message); return; 
        }
        depthPointLayer = clearMap(depthPointLayer, mapObj);
        depthPointLayer = addGridPoints(response.content, mapObj, false);
        obj.depthGridCheckbox.checked = true; 
        obj.depthGridCheckbox.dispatchEvent(new Event('change'));
    });
    obj.depthGridCheckbox.addEventListener('change', async (e) => {
        if (e.target.checked) { 
            if (depthPointLayer !== null) {
                mapObj.addLayer(depthPointLayer);
            } else { e.target.checked = false; }
        } else { mapObj.removeLayer(depthPointLayer); }
    });
    obj.saveDepth.addEventListener('click', async () => {
        if (depthPointLayer === null) { alert("No point layer found on the map."); return; }
        const header = ['Lon', 'Lat', 'Depth(m)']; layerToCSV(depthPointLayer, header, 'Grid CSV');
    });
    obj.vertexesBtn.addEventListener('click', async () => {
        obj.colorBarContainer.style.display = 'none'; gridPoints = clearMap(gridPoints, mapObj);
        if (lakeLayer === null) { alert('Please add/draw a polygon on the map first.'); return;}
        signalSender('showOverlay', 'Generating Vertexes.\nPlease wait...');
        const lakeContent = { projectName: currentProject, polygon: lakeLayer.toGeoJSON() };
        const response = await jsonLoader('vertex_generator', lakeContent); 
        signalSender('hideOverlay'); resetMap(mapObj); 
        if (response.status === "error") { alert(response.message); return; }
        pointLayer = addPointLayer(response.content, mapObj, false);
        if (!obj.polygonCheckbox.checked) obj.polygonCheckbox.checked = true;
        obj.polygonCheckbox.dispatchEvent(new Event('change'));
        obj.orthoCheckbox.checked = false; obj.orthoCheckbox.dispatchEvent(new Event('change'));
        refineChecked = false; obj.refinementCheckbox.checked = false;
        obj.refinementCheckbox.dispatchEvent(new Event('change'));
        obj.depthCheckbox.checked = false; obj.depthCheckbox.dispatchEvent(new Event('change'));
    });
    obj.refinementCheckbox.addEventListener('change', async (e) => {
        if (e.target.checked) { 
            if (!pointLayer) { 
                alert("Select the button 'Get/Reset Vertexes' to create vertexes first.");
                e.target.checked = false; return;
            }
            refineChecked = true; obj.depthCheckbox.checked = false;
            obj.moveCheckbox.checked = false; moveChecked = false;
            obj.depthCheckbox.checked = false;
            obj.depthCheckbox.dispatchEvent(new Event('change'));
            deleteChecked = false; obj.deleteCheckbox.checked = false;
            obj.deleteCheckbox.dispatchEvent(new Event('change'));
            obj.refinementContainer.style.display = 'flex';
            pointContainer = []; gridLayer = clearMap(gridLayer, mapObj);
            orthoLayer = clearMap(orthoLayer, mapObj);
            obj.orthoCheckbox.checked = false; 
            obj.orthoCheckbox.dispatchEvent(new Event('change'));
        } else { 
            obj.refinementContainer.style.display = 'none'; refineChecked = false;
        }
    });
    obj.moveCheckbox.addEventListener('change', async (e) => { 
        const value = e.target.checked, pointCollection = [];
        if (value) {
            if (pointLayer === null) {
                alert("Select the button 'Get/Reset Vertexes' to create vertexes first.");
                e.target.checked = false; return;
            }
            toggleMoveMode(pointLayer, true);
            pointLayer.eachLayer(layer => {
                const latlng = layer.getLatLng();
                pointCollection.push([latlng.lat, latlng.lng]);
            });
            if (pointCollection.length === 0) { alert("No vertexes found. Please draw/add polygon on the map first."); return; }
            pointCollection.push(pointCollection[0]);
            moveChecked = true; refineChecked = false; deleteChecked = false;
            obj.deleteCheckbox.checked = false; obj.refinementCheckbox.checked = false;
            signalSender('showOverlay', 'Regenerating vertexes.\nPlease wait...');
            const contents = { projectName: currentProject, pointCollection: pointCollection };
            const response = await jsonLoader('vertex_mover', contents); signalSender('hideOverlay');
            if (response.status === "error") { alert(response.message); return; }
            if (!obj.polygonCheckbox.checked) { obj.polygonCheckbox.checked = true; }
            lakeLayer = clearMap(lakeLayer, mapObj); 
            lakeLayer = polygonPlotter(response.content.polygon, mapObj);
            pointLayer = clearMap(pointLayer, mapObj); 
            pointLayer = addPointLayer(response.content.point, mapObj, true);
            obj.refinementContainer.style.display = 'none';
        } else { moveChecked = false; toggleMoveMode(pointLayer, false); }
    });
    obj.deleteCheckbox.addEventListener('change', async (e) => {
        if (!e.target.checked) { deleteChecked = false; return; }
        if (pointLayer === null) { 
            alert("Select the button 'Get/Reset Vertexes' to create vertexes first.");
            e.target.checked = false; return;
        }
        obj.moveCheckbox.checked = false; moveChecked = false;
        pointContainer = []; gridLayer = clearMap(gridLayer, mapObj); deleteChecked = true; 
        obj.orthoCheckbox.checked = false; obj.orthoCheckbox.dispatchEvent(new Event('change'));
        refineChecked = false; obj.refinementCheckbox.checked = false;
        obj.refinementCheckbox.dispatchEvent(new Event('change'));
    });
    obj.scaleSelector.addEventListener('change', (e) => {
        const value = e.target.value;
        if (value === "auto") { obj.scaleFactor.style.display = "none"; }
        else { obj.scaleFactor.style.display = "flex"; }
    });
    obj.scaleFactor.addEventListener('input', (e) => { 
        const value = e.target.value;
        if (value === "" || !Number.isFinite(Number(value)) || Number(value) <= 0) { 
            e.target.value = '1.0'; return; 
        }
    });
    // Create grid
    obj.createGrid.addEventListener('click', async () => {
        if (pointLayer === null) { alert("Please generate vertexes first."); return; }
        const levelSelector = obj.scaleSelector.value, pointCollection = [];
        if (levelSelector === "auto") { levelValue = ''; } 
        else { levelValue = Number(obj.scaleFactor.value); }
        pointLayer.eachLayer(layer => {
            const latlng = layer.getLatLng();
            pointCollection.push([latlng.lat, latlng.lng]);
        });
        if (pointCollection.length === 0) { alert("No vertexes found."); return; }
        pointCollection.push(pointCollection[0]);
        signalSender('showOverlay', 'Generating an Unstructured Grid.\nPlease wait...');
        const contents = { 
            projectName: currentProject, pointCollection: pointCollection, levelValue: levelValue 
        }
        const response = await jsonLoader('grid_creator', contents); signalSender('hideOverlay');
        if (response.status === "error") { alert(response.message); return; }
        gridLayer = clearMap(gridLayer, mapObj); orthoLayer = clearMap(orthoLayer, mapObj);
        gridLayer = await plotUnstructuredGrid(response.content.polygon, mapObj);
        interpolatedPointLayer = clearMap(interpolatedPointLayer, mapObj);
        gridPoints = clearMap(gridPoints, mapObj); 
        gridPoints = await plotUnstructuredGrid(response.content.point, mapObj, 'black');
        moveChecked = false; obj.moveCheckbox.checked = false;
        refineChecked = false; obj.refinementCheckbox.checked = false;
        obj.refinementCheckbox.dispatchEvent(new Event('change'));
        obj.depthCheckbox.checked = false; obj.depthCheckbox.dispatchEvent(new Event('change'));
        obj.orthoCheckbox.checked = false; obj.orthoCheckbox.dispatchEvent(new Event('change'));
    });
    // Generate orthogonality
    obj.orthoCheckbox.addEventListener('change', async (e) => {
        if (e.target.checked) { 
            if (gridLayer === null) { 
                alert("Please generate grid first."); 
                obj.orthoCheckbox.checked = false; return; 
            }
            signalSender('showOverlay', 'Generating Orthogonality Grid.\nPlease wait...');
            const contents = { projectName: currentProject };
            const response = await jsonLoader('grid_ortho', contents); signalSender('hideOverlay');
            if (response.status === "error") { alert(response.message); return; }
            depthLayer = clearMap(depthLayer, mapObj); orthoLayer = clearMap(orthoLayer, mapObj); 
            obj.depthCheckbox.checked = false;
            const vmin = response.content.min, vmax = response.content.max, colorKey = 'ortho';
            orthoLayer = L.geoJSON(response.content.data, {
                pointToLayer: (feature, latlng) => {
                    const value = Number(feature.properties.orth);
                    const { r, g, b, a } = getColorFromValue(value, vmin, vmax, colorKey);
                    const col = `rgb(${r},${g},${b})`;
                    return L.circleMarker(latlng, {
                        color: col, fillColor: col, radius: 2, fillOpacity: a
                    });
                },
                onEachFeature: (feature, layer) => {
                    layer.bindTooltip(`Orthogonality: ${feature.properties.orth}`, {
                        sticky: true, permanent: false, direction: 'center', opacity: 1
                    });
                }
            }).addTo(mapObj);
            updateColorbar(vmin, vmax, 'Orthogonality', colorKey, obj.colorBarColor, 
                obj.colorBarTitle, obj.colorBarLabel);
            obj.colorBarContainer.style.display = 'block';
        } else { 
            orthoLayer = clearMap(orthoLayer, mapObj);
            if (!obj.depthCheckbox.checked) { obj.colorBarContainer.style.display = 'none'; } 
        }
    });
    obj.optimizeBtn.addEventListener('click', async () => {
        obj.progressbarGrid.value = 0; obj.progressTextGrid.innerText = ''; 
        gridLayer = clearMap(gridLayer, mapObj);
        if (isRunning) { alert("Grid optimization is running."); return; }
        if (pointLayer === null) { alert("Please generate grid first."); return; }
        const iterations = Number(obj.iterationValue.value);
        if (isNaN(iterations)) { alert("Please enter a valid number of iterations."); return; }
        const levelFrom = Number(obj.valueFrom.value), levelTo = Number(obj.valueTo.value);
        if (isNaN(levelFrom) || isNaN(levelTo) || levelFrom <= 0 || levelTo <= 0 || levelFrom >= levelTo) {
            alert("Please enter a valid value range."); return; 
        }
        const pointCollection = [];
        pointLayer.eachLayer(layer => {
            const latlng = layer.getLatLng();
            pointCollection.push([latlng.lat, latlng.lng]);
        });
        if (pointCollection.length === 0) { alert("No vertexes found."); return; }
        pointCollection.push(pointCollection[0]);
        // Reset UI
        activeProject = currentProject; isRunning = true;
        obj.leafletContainer.style.display = 'none'; 
        obj.plotContainer.style.display = 'flex'; 
        obj.menuContent.style.display = 'none';
        obj.optimizeCloseBtn.innerText = 'Stop';
        const contents = { projectName: currentProject, pointCollection: pointCollection,
            iterations: iterations, levelFrom: levelFrom, levelTo: levelTo
        };
        try {
            const start = await jsonLoader('start_grid_optimization', contents);
            if (start.status === "error") { isRunning = false; alert(start.message); return; }
            updateLog(currentProject, obj.chartDiv, obj.progressbarGrid, obj.progressTextGrid, 1, mapObj);
        } catch (error) {
            obj.progressTextGrid.innerText = 'Failed to start optimization.';
            alert(`Grid optimization failed: ${error.message}`); isRunning = false;
        }
    });
    obj.optimizeCloseBtn.addEventListener('click', async (e) => {
        const value = e.target.innerText;
        if (value === 'Stop') {
            const response = await jsonLoader('stop_grid_optimization', {projectName: currentProject});
            if (response.status === "error") { alert(response.message); }
            isRunning = false; e.target.innerText = 'Close and Plot Grid';
        } else if (value === 'Close and Plot Grid') {
            obj.leafletContainer.style.display = 'flex'; obj.plotContainer.style.display = 'none'; 
            obj.menuContent.style.display = 'grid';
        }
    });
    obj.interpolateCSVBtn.addEventListener('click', async () => {
        obj.interpolateFile.click(); interpolateCheked = false;
    });
    obj.interpolateFile.addEventListener('change', async (e) => {
        const file = e.target.files[0]; if (!file) return;
        const formData = new FormData(); formData.append('file', file); 
        signalSender('showOverlay', 'Reading depth data. Please wait...');
        const response = await fetch('/grid_interpolation_upload', { method: 'POST', body: formData });
        const data = await response.json(); signalSender('hideOverlay'); e.target.value = '';
        if (data.status === 'error') { 
            alert(`Uploading depth data failed: ${data.message}`); return; 
        }
        interpolatedLayer = clearMap(interpolatedLayer, mapObj); drawChecked = false;
        interpolatedLayer = addGridPoints(data.content, mapObj); obj.interpolateHide.checked = true;
    });
    obj.interpolateMapBtn.addEventListener('click', async () => { 
        drawChecked = false; interpolateCheked = true; pointContainer = [];
        interpolatedLayer = clearMap(interpolatedLayer, mapObj);
    });
    obj.interpolateDelete.addEventListener('change', (e) => {
        if (e.target.checked && interpolatedLayer === null) { 
            alert("Please add points to interpolate."); e.target.checked = false;
        }
        interpolateDeleteChecked = e.target.checked;
    });
    obj.interpolateGrid.addEventListener('click', async () => {
        if (gridLayer === null) { alert("Please add unstructured grid first."); return; }
        if (interpolatedLayer === null) { alert("Please add points to interpolate."); return; }
        interpolatedPoints = [];
        interpolatedLayer.eachLayer(layer => {
            const latlng = layer.getLatLng();
            const depth = layer.feature?.properties?.Depth;
            if (depth === undefined || depth === null || !Number.isFinite(Number(depth))) {
                alert("Invalid depth for marker:", layer); return;
            }
            interpolatedPoints.push([latlng.lat, latlng.lng, Number(depth)]);
        });
        if (interpolatedPoints.length === 0) { alert("No depth points found."); return; }
        if (interpolatedPoints.length < 2) { alert("Please add at least 2 points to interpolate."); return; }
        signalSender('showOverlay', 'Interpolating an Unstructured Grid.\nPlease wait...');
        const contents = { 
            projectName: currentProject, grid: gridLayer.toGeoJSON(), 
            pointCollection: interpolatedPoints
        }
        const response = await jsonLoader('grid_interpolation', contents); signalSender('hideOverlay');
        if (response.status === "error") { alert(response.message); return; }
        interpolatedPointLayer = clearMap(interpolatedPointLayer, mapObj); 
        interpolatedPointLayer = await plotUnstructuredGrid(response.content, mapObj);
        obj.interpolateHide.checked = false; obj.interpolateHide.dispatchEvent(new Event('change'));
    });
    obj.interpolateHide.addEventListener('change', (e) => {
        if (e.target.checked) { mapObj.addLayer(interpolatedLayer); }
        else { mapObj.removeLayer(interpolatedLayer); }
    });
    obj.saveGrid.addEventListener('click', async() => {
        if (interpolatedPointLayer === null) { alert("No interpolated grid found. Please interpolate grid first."); return; }
        signalSender('showOverlay', 'Saving interpolated grid data to a NetCDF file.\nPlease wait...');
        // Check browser support
        if (!window.showSaveFilePicker) {
            alert(
                "Your browser does not support choosing a save location.\n" +
                "Please use a recent version of Chrome or Edge."
            );
            return;
        }
        try {
            const fileHandle = await window.showSaveFilePicker({
                suggestedName: "Grid.nc",
                types: [
                    { description: 'NetCDF files', accept: {'application/x-netcdf': ['.nc']}}
                ]
            });
            const contents = { 
                projectName: currentProject, gridPoints: interpolatedPointLayer.toGeoJSON() 
            };
            const response = await fetch('/grid_saver', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(contents)
            });
            if (!response.ok) {
                throw new Error(`Server returned HTTP ${response.status}`);
            }
            const blob = await response.blob();
            const writable = await fileHandle.createWritable();
            await writable.write(blob); await writable.close();
            alert(`Grid saved successfully.`);
        } catch (error) {
            // User cancelled the Save dialog
            if (error.name === 'AbortError') { return; }
            alert(`Error saving grid: ${error.message}`);
        }
        signalSender('hideOverlay');
    });
    obj.reCheckGrid.addEventListener('click', () => obj.reCheckGridFile.click());
    obj.reCheckGridFile.addEventListener('change', async (e) => {
        const file = e.target.files[0]; if (!file) return;
        const formData = new FormData(); formData.append('file', file);
        signalSender('showOverlay', 'Reading NetCDF file. Please wait...');
        const response = await fetch('/grid_rechecker', { method: 'POST', body: formData });
        const data = await response.json(); signalSender('hideOverlay'); e.target.value = '';
        if (data.status === 'error') { 
            alert(`Reading NetCDF failed: ${data.message}`); return; 
        }
        gridLayer = clearMap(gridLayer, mapObj); pointLayer = clearMap(pointLayer, mapObj);
        interpolatedPointLayer = clearMap(interpolatedPointLayer, mapObj);
        gridLayer = await plotUnstructuredGrid(data.content.grid, mapObj);
        interpolatedPointLayer = await plotUnstructuredGrid(data.content.nodes, mapObj);
    });
}

function layerToCSV(layer, header, title) {
    if (!layer) return;
    const geojson = layer.toGeoJSON();
    const features = geojson.type === 'FeatureCollection'
        ? geojson.features : [geojson];
    const rows = features.map(feature => {
        const center = turf.center(feature);
        const [lon, lat] = center.geometry.coordinates;
        const value = feature.properties?.Depth ?? '';
        const depth = value === '' || value === null || value === undefined
            ? '' : Number(value).toFixed(3);
        return [Number(lon).toFixed(7), Number(lat).toFixed(7), depth].join(',');
    });
    const csv = [header.join(','),...rows].join('\n');
    const html = `
        <!DOCTYPE html><html><head>
            <meta charset="UTF-8"><title>${title}</title>
            <style>
                body {font-family: monospace; white-space: pre; padding: 20px;}
            </style>
        </head>
        <body>${csv.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</body>
        </html>
    `;
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob); window.open(url, '_blank');
}

async function addItems(currentProject, value) {
    signalSender('showOverlay', 'Loading all Lakes for entire Norway.\nPlease wait...');
    const response = await jsonLoader('search_lake', { 
        projectName: currentProject, name: value 
    });
    if (response.status === "error") { alert(response.message); return; }
    if (response.content.length === 0) { obj.sugesstionLake.style.display = 'none'; return; } 
    obj.sugesstionLake.innerHTML = ''; 
    response.content.forEach(lake => { 
        var div = document.createElement('div'); 
        div.textContent = lake; 
        div.addEventListener('click', () => { 
            obj.lakeSearcher.value = lake; 
            obj.municipalityList.value = ''; 
            obj.lakeSelector.innerHTML = `<option value="${lake}">${lake}</option>`; 
            obj.sugesstionLake.style.display = 'none'; 
            obj.lakeSelector.dispatchEvent(new Event('change')); 
        }); 
        obj.sugesstionLake.appendChild(div); 
    }); 
    obj.sugesstionLake.style.display = 'block'; signalSender('hideOverlay');
}

async function loadLakes(currentProject){
    signalSender('showOverlay', "Initializing Database for entire Norway's Lakes.\nPlease wait...");
    const response = await jsonLoader('init_lakes', {projectName: currentProject});
    if (response.status === "error") { alert(response.message); return; }
    signalSender('hideOverlay'); lakesData = response.content;
    dataLake = lakesData.lake; dataDepth = lakesData.depth;
}

async function drawPolygon(pointList, map) {
    signalSender('showOverlay', 'Drawing Polygon on the map.\nPlease wait...');
    const content = { projectName: currentProject, points: pointList };
    const response = await jsonLoader('polygon_generator', content); signalSender('hideOverlay');
    if (response.status === "error") { alert(response.message); return; }
    const polygon = response.content.polygon, point = response.content.point;
    const data = polygon.features[0].properties;
    const contents = [[
        data.name, data.region, data.area, 
        data.perimeter, data.min, data.max, data.avg
    ]];
    fillTable(contents, obj.lakeTable, true); obj.depthCheckbox.checked = false;
    obj.polygonCheckbox.checked = true; dataLake = polygon; resetMap(map);
    lakeLayer = polygonPlotter(polygon, map); 
    pointLayer = addPointLayer(point, map, true);
}

export function addGridPoints(points, map, editable=true) {
    const tempLayer = L.geoJSON(points, {
        pointToLayer: (_, latlng) => {
           return L.circleMarker(latlng, {
                radius: 3, color: 'white', weight: 1, fillColor: 'red', fillOpacity: 0.8
            });
        },
        onEachFeature: (feature, layer) => {
            if (!feature.properties) return;
            const tooltip = () => {
                const content = Object.entries(feature.properties)
                    .map(([name, value]) => `
                        <div><b>${name}:</b> ${value}</div>
                    `).join('');
                if (!editable) return content;
                return `${content}<hr><b>Click to adjust depth</b>`;
            };
            layer.bindTooltip(tooltip, {sticky: true, direction: 'top'});
            if (editable) {
                layer.on('click', () => { 
                    const originalDepth = feature.properties.Depth;
                    const container = document.createElement('div');
                    container.innerHTML = `
                        <div style="min-width: 200px;">
                            <label><b>Depth Adjustment:</b></label>
                            <input type="number" id="depthInput" step="any"
                                style="
                                    width:100%; box-sizing:border-box;
                                    margin-top:5px; margin-bottom:10px;
                                "
                            />
                            <div style="display:flex; gap:8px; justify-content:flex-end;">
                                <button id="save-3" style="cursor:pointer;">Save</button>
                                <button id="cancel-3" style="cursor:pointer;">Cancel</button>
                            </div>
                        </div>
                    `;
                    const input = container.querySelector('#depthInput');
                    input.value = originalDepth ?? '';
                    const popup = L.popup({
                        closeOnClick: false, autoClose: false, maxWidth: 300
                    }).setLatLng(layer.getLatLng()).setContent(container).openOn(map);
                    // Save Depth
                    container.querySelector('#save-3').addEventListener('click', () => {
                        const newDepth = input.value.trim();
                        if (newDepth === '' || !Number.isFinite(Number(newDepth))) {
                            alert('Please enter a valid depth value.'); return;
                        }
                        feature.properties.Depth = Number(newDepth); // Update Depth to GeoJSON feature
                        layer.setTooltipContent(createTooltip()); // Update Tooltip
                        layer.closePopup(popup);
                    });
                    // Cancel Depth
                    container.querySelector('#cancel-3').addEventListener('click', () => {
                        layer.closePopup(popup);
                    });
                });
            }
        }
    }).addTo(map);
    return tempLayer;
}

function addPointLayer(points, map, checkMove=false) {
    const pointType = checkMove ? 'point-marker-move' : 'point-marker-default';
    const tempLayer = L.geoJSON(points, {
        pointToLayer: (_, latlng) => {
            const marker = L.marker(latlng, {
                draggable: checkMove,
                icon: L.divIcon({
                    className: "", html: `<div class="${pointType}"></div>`,
                    iconSize: [10, 10], iconAnchor: [5, 5]
                }),
            });
            return marker;
        },
        onEachFeature: (feature, layer) => {
            layer.on('click', async () => { 
                mapContainer.style.cursor = "auto";
                if (refineChecked) {
                    if (!pointContainer.includes(feature.properties.id)) { 
                        pointContainer.push(feature.properties.id); 
                    }
                    if (pointContainer.length === 1) { html = "Select end point to refine."; }
                    if (pointContainer.length === 2) { 
                        await polygonRefinement(pointContainer, map); pointContainer = [];
                        obj.refinementCheckbox.dispatchEvent(new Event('change'));
                        if (hoverTooltip) map.closeTooltip(hoverTooltip); 
                        return;
                    }
                } else if (deleteChecked) {
                    if (!pointContainer.includes(feature.properties.id)) { 
                        pointContainer.push(feature.properties.id); 
                    }
                    if (pointContainer.length === 1) { html = "Select end point to delete."; }
                    if (pointContainer.length === 2) { 
                        await pointRemoval(pointContainer); pointContainer = []; 
                        obj.deleteCheckbox.dispatchEvent(new Event('change'));
                        if (hoverTooltip) map.closeTooltip(hoverTooltip); 
                        return;
                    }
                }
            });
            layer.on('dragend', async function (e) {
                const pos = e.target.getLatLng(), pointCollection = [];
                // Update point
                layer.feature.geometry.coordinates = [pos.lng, pos.lat];
                pointLayer.eachLayer(layer => {
                    const latlng = layer.getLatLng();
                    pointCollection.push([latlng.lat, latlng.lng]);
                });
                signalSender('showOverlay', 'Regenerating vertexes.\nPlease wait...');
                const contents = { projectName: currentProject, pointCollection: pointCollection };
                const response = await jsonLoader('vertex_mover', contents); signalSender('hideOverlay');
                if (response.status === "error") { alert(response.message); return; }
                if (!obj.polygonCheckbox.checked) obj.polygonCheckbox.checked = true; resetMap(map);
                lakeLayer = polygonPlotter(response.content.polygon, map);
                pointLayer = addPointLayer(response.content.point, map, checkMove);
            });
            layer.bindTooltip(`Id: ${feature.properties.id}`, {
                sticky: true, permanent: false, direction: 'center', opacity: 1
            });
        }
    }).addTo(map);
    return tempLayer;
}

async function polygonRefinement(pointIds, map) {
    const refineValue = Number(obj.refinementValue.value); 
    gridLayer = clearMap(gridLayer, map);
    if (!Number.isFinite(refineValue) || refineValue <= 0) { 
        alert("Please enter a valid non-negative value."); return; 
    }
    if (pointLayer === null) { 
        alert("No polygon has been found. Select the button 'Get/Reset Vertexes' to draw the original polygon first."); return; 
    }
    const pointCollection = [];
    pointLayer.eachLayer(layer => {
        const latlng = layer.getLatLng();
        pointCollection.push([latlng.lat, latlng.lng]);
    });
    if (pointCollection.length < 2) { alert("No point has been found. Select the button 'Get/Reset Vertexes' to create vertexes first."); return; }
    signalSender('showOverlay', 'Refining Vertexes.\nPlease wait...');
    const contents = {
        projectName: currentProject, distance: refineValue, polygon: pointCollection,
        startPoint: pointIds[0], endPoint: pointIds[pointIds.length - 1]
    }
    const response = await jsonLoader('vertex_refiner', contents); signalSender('hideOverlay');
    if (response.status === "error") { alert(response.message);  return; }
    const polygon = response.content.polygon, point = response.content.point; dataLake = polygon;
    if (!obj.polygonCheckbox.checked) { obj.polygonCheckbox.checked = true; }
    resetMap(map);
    lakeLayer = polygonPlotter(polygon, map); pointLayer = addPointLayer(point, map, false);
    obj.orthoCheckbox.checked = false; obj.orthoCheckbox.dispatchEvent(new Event('change'));
}

async function pointRemoval(pointIds, map) {
    if (pointLayer === null) { 
        alert("No polygon has been found. Select the button 'Get/Reset Vertexes' to draw the original polygon first."); return; 
    }
    const pointCollection = []; deleteChecked = true;
    pointLayer.eachLayer(layer => {
        const latlng = layer.getLatLng();
        pointCollection.push([latlng.lat, latlng.lng]);
    });
    if (pointCollection.length < 2) { 
        alert("No point has been found. Select the button 'Get/Reset Vertexes' to draw the original polygon first."); return; 
    }
    signalSender('showOverlay', "Deleting Vertexes.\nPlease wait...");
    const contents = {
        projectName: currentProject, polygon: pointCollection,
        startPoint: pointIds[0], endPoint: pointIds[pointIds.length - 1]
    }
    const response = await jsonLoader('vertex_remover', contents); signalSender('hideOverlay');
    if (response.status === "error") { alert(response.message); return; }
    const polygon = response.content.polygon, point = response.content.point;
    resetMap(map);
    lakeLayer = polygonPlotter(polygon, map, false, false); 
    pointLayer = addPointLayer(point, map, false);
    obj.orthoCheckbox.checked = false; 
    obj.orthoCheckbox.dispatchEvent(new Event('change'));
}

async function resetMap(map){
    pointContainer = []; tempLine = clearMap(tempLine, map);
    lakeLayer = clearMap(lakeLayer, map); depthLayer = clearMap(depthLayer, map);
    gridLayer = clearMap(gridLayer, map); orthoLayer = clearMap(orthoLayer, map);
    obj.colorBarContainer.style.display = 'none';
    if (pointLayer) { pointLayer.clearLayers(); }
    if (hoverTooltip) map.closeTooltip(hoverTooltip);
}

function clearMap(layer, map) {
    if (layer) { map.removeLayer(layer); }
    return null;
}

function updateLog(project, chartDiv, progress_bar, progress_text, seconds, map){
    activeProject = project; isRunning = true;
    if (logInterval !== null) { clearInterval(logInterval); logInterval = null; }
    const pollStatus = async () => {
        if (activeProject !== project) { clearInterval(logInterval); logInterval = null; return; }
        try {
            const statusRes = await jsonLoader('check_grid_optimization', {projectName: project});
            progress_text.innerText = statusRes.message; progress_bar.value = statusRes.progress;
            if (statusRes.status === "finished" || statusRes.status === "stopped") {
                clearInterval(logInterval); logInterval = null; isRunning = false;
                if (statusRes.grid) { 
                    gridLayer = clearMap(gridLayer, map);
                    gridLayer = await plotUnstructuredGrid(statusRes.grid, map);
                    obj.orthoCheckbox.checked = true; obj.orthoCheckbox.dispatchEvent(new Event('change'));
                } else { alert('No grid has been found. Consider running the optimization again.'); }
                obj.optimizeCloseBtn.innerText = "Close and Plot Grid"; return;
            }
            if (statusRes.status === "failed") {
                clearInterval(logInterval); logInterval = null; isRunning = false;
                alert(statusRes.message); return;
            }
            // Plotting Orthogonality
            await orthoPlotter(statusRes.his, chartDiv, 'Step', 'Orthogonality', 'Orthogonality History');
        } catch (error) { 
            alert("Polling error: " + (error.message || error)); isRunning = false;
            clearInterval(logInterval); logInterval = null; 
        }
    }
    logInterval = setInterval(pollStatus, seconds * 1000); pollStatus(); 
}
