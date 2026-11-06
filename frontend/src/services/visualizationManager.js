import { L, getState, setState, initState } from "./constant.js";
import { getUser, signalSender, jsonLoader, moveWindow, closeWindow, 
    getVisualizationFiles } from "./commonFunctions.js";
import { locationFinder, initializeMenu, projectChecker } from "./visualization.js";
import { initMap } from "./visualizationMap.js";

const $ = (id) => document.getElementById(id);
const obj = {
    baseMap: $("basemap-btn"), locationSearcher: $("search"), 
    locationList: $("suggestions"), popupMenu: $("popup-menu"), 
    closeSummaryBtn: $("close-summary-btn"), summaryContainer: $("summary-container"), 
    summaryHeader: $("summary-header"), closeTimeSeriesBtn: $("close-time-series-btn"), 
    timeSeriesContainer: $("time-series-container"), timeSeriesHeader: $("time-series-header"), 
    substanceContainer: $("substance-container"), substanceHeader: $("substance-header"), 
    closeSubstanceBtn: $("close-substance-btn"), profileContainer: $("profile-window"), 
    profileHeader: $("profile-header"), closeProfileBtn: $("close-profile-btn")
}

let currentProject = null, currentParams = null, userName = null, 
    waqModel = null, mapObj = null, waqName = null, hideTimeout = null, gisLayers = {};


await getProject(); mapObj = await initMap('leaflet-map');
updateManager(); await restoreGISLayers();

export async function getProject() { 
    userName = await getUser(); initState(userName.split('/').shift());
    const project = userName.split('/'); currentProject = project[1];
    const values = await getVisualizationFiles(project[0], currentProject);
    currentProject = values[0]; currentParams = values[1]; waqName = values[2]; waqModel = values[3];
    signalSender('showNote', `${project[0]}/${currentProject}`);
    let message = `Initializing project "${currentProject}"`;
    if (waqName != '') message = message + ` and WAQ model "${waqName}"`;
    message = message + `.\nPlease wait...`
    await projectChecker(currentProject, currentParams, waqName, waqModel, message);
}

function updateManager() { 
    // Search locations
    locationFinder(obj.locationSearcher, obj.locationList, mapObj);
    initializeMenu(currentProject, currentParams, waqName, waqModel); 
    // Show popup menu on click or leave
    if (obj.popupMenu) {
        obj.popupMenu.addEventListener('mouseenter', () => {
            obj.popupMenu.classList.add('show');
            if (hideTimeout) { clearTimeout(hideTimeout); hideTimeout = null; }
        });
        obj.popupMenu.addEventListener('mouseleave', () => {
            hideTimeout = setTimeout(() => {
                obj.popupMenu.classList.remove('show');
            }, 500);
        });
    };
    document.addEventListener('change', async (e) => {
        // Change WAQ model
        if (e.target.classList.contains('waq-model-selector')) {
            waqModel = e.target.value; waqName = e.target.closest('label').dataset.name;
            const message = `Opening WAQ Model '${waqName}'.\nPlease wait...`;
            // Update parameters when user change WAQ model
            currentParams[2] = `${waqName}_his.zarr`; currentParams[3] = `${waqName}_map.zarr`;
            await projectChecker(currentProject, currentParams, waqName, waqModel, message, false);
            initializeMenu(currentProject, currentParams, waqName, waqModel);
        }
    });
    // Moving window
    moveWindow(obj.summaryHeader, obj.summaryContainer);
    closeWindow(obj.closeSummaryBtn, obj.summaryContainer);
    moveWindow(obj.timeSeriesHeader, obj.timeSeriesContainer);
    closeWindow(obj.closeTimeSeriesBtn, obj.timeSeriesContainer);
    moveWindow(obj.profileHeader, obj.profileContainer);
    closeWindow(obj.closeProfileBtn, obj.profileContainer);
    moveWindow(obj.substanceHeader, obj.substanceContainer);
    closeWindow(obj.closeSubstanceBtn, obj.substanceContainer);
    document.addEventListener('click', (e) => {
        // Hide suggestions for location search
        if (e.target !== obj.locationSearcher) {
            obj.locationSearcher.value = '';
            obj.locationList.style.display = 'none';
        }
        // Close the popup menu if clicked outside
        if (obj.popupMenu && !obj.popupMenu.contains(e.target)) {
            obj.popupMenu.classList.remove('show');
        }
        // Toogle the menu if click on menu-link
        const handleMenuClick = (e, linkClass, submenuClass) => {
            const link = e.target.closest(linkClass);
            if (!link) return false;
            const submenu = link.nextElementSibling;
            if (!submenu || !submenu.classList.contains(submenuClass)) return true;
            // Close other submenus and remove active on menu-links
            document.querySelectorAll(`.${submenuClass}.open`).forEach(s => {
                if (s !== submenu) s.classList.remove('open');
            });
            // Remove active class from other menu-links
            document.querySelectorAll(`${linkClass}.active`).forEach(l => {
                if (l !== link) l.classList.remove('active');
            });
            // Toggle class open
            submenu.classList.toggle('open');
            link.classList.toggle('active');
            return true;
        }
        if (handleMenuClick(e, '.menu-link', 'submenu')) return;
        if (handleMenuClick(e, '.menu-link-1', 'submenu-1')) return;
    });
    obj.popupMenu.addEventListener('click', async (e) => {
        // Show/hide GIS layers
        if (e.target.type === 'checkbox' && e.target.className === 'layer-gis') {
            const id = e.target.id, value = e.target.checked;
            await GISLayerChange(currentProject, id, value);
        }
        // Delete GIS layer
        if (e.target.classList.contains('delete-btn')) {
            const id = e.target.id.replace('delete-', '');
            signalSender('showOverlay', 'Deleting GIS Layer.\nPlease wait...');
            const data = await jsonLoader('delete_gis', { projectName: currentProject, name: id });
            signalSender('hideOverlay'); 
            if (data.status === "error") { alert(data.message); return; }
            await GISLayerChange(currentProject, id, false);
            // Reload project
            const message = `Reloading WAQ Model '${waqName}'.\nPlease wait...`;
            await projectChecker(currentProject, currentParams, waqName, waqModel, message, true);
            initializeMenu(currentProject, currentParams, waqName, waqModel);
            const rowDiv = e.target.parentNode; if (rowDiv) {rowDiv.remove();}
        }
    });
}

async function restoreGISLayers() {
    const savedLayers = getState().gisLayers || {};
    const layersToRestore = Object.entries(savedLayers).filter(([_, checked]) => checked);
    if (layersToRestore.length > 0) {
        for (const [id, _] of layersToRestore) {
            await GISLayerChange(currentProject, id, true);
        }
    }
}

async function GISLayerChange(currentProject, id, checked){
    setState({gisLayers: {...getState().gisLayers, [id]: checked}});
    if (!checked) {
        if (gisLayers[id]) { mapObj.removeLayer(gisLayers[id]); }
        return;
    }
    if (gisLayers[id]) { mapObj.addLayer(gisLayers[id]); return; }
    // Load gis layer
    signalSender('showOverlay', 'Loading GIS Layer.\nPlease wait...');
    const response = await jsonLoader('get_gis_layer', { projectName: currentProject, layer: id });
    if (response.status === "error") { signalSender('hideOverlay'); alert(response.message); return; }
    const hue1 = Math.floor(Math.random() * 360), hue2 = Math.floor(Math.random() * 360);
    const fillColor = `hsl(${hue1}, 70%, 50%)`, color = `hsl(${hue2}, 70%, 50%)`;
    const layer = L.geoJSON(response.content, { renderer: L.canvas(),
        pointToLayer: function (feature, latlng) {
            return L.circleMarker(latlng, {
                radius: 3, fillColor: fillColor, color: color,
                weight: 1, opacity: 1, fillOpacity: 0.8
            });
        },
        style: feature => {
            switch (feature.geometry.type) {
                case 'LineString': 
                case 'MultiLineString':
                    return { color: color, weight: 2 };
                case 'Polygon':
                case 'MultiPolygon':
                    return { color: color, fillColor: fillColor, fillOpacity: 0.5, weight: 1 };
                default: return {};
            }
        },
        onEachFeature: (feature, l) => {
            l.on('click', () => {
                if (!feature.properties) return;
                const content = Object.entries(feature.properties)
                    .map(([k, v]) => `<b>${k}</b>: ${v}`).join('<br>')
                l.bindPopup(`<div style="max-height: 200px; overflow-y: auto;
                    overflow-x: hidden;">${content}</div>`).openPopup();
            });
        }
    });
    gisLayers[id] = layer; mapObj.addLayer(layer); 
    if (layer.getLayers().length < 2000) { mapObj.fitBounds(layer.getBounds()); }
    signalSender('hideOverlay');
}