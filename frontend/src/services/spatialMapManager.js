import { signalSender, jsonLoader, initOptions } from "./commonFunctions.js";
import { L, getStateVisualization, setStateVisualization, getMap, getLastTimeZone
} from "./constant.js";
import { plot2DMapStatic, plot2DMapDynamic, plot2DVectorMap } from "./map2DManager.js";


const $ = (id) => document.getElementById(id);
const obj = { 
    timeControl: $("time-controls"), substanceContainer: $("substance-container"),
    colorbarContainer: $("custom-colorbar"), colorbarVectorContainer: $("custom-colorbar-vector"),
    vectorScaler: $("custom-colorbar-scaler"), timeSeriesContainer: $("time-series-container")
}

let objContent = {}, newKey = '', newQuery = '', titleColorbar = '', colorbarKey = '', mapObj = null;

export async function spatialMapManager(projectName) {
    const popupContent = document.getElementById('popup-content');
    const $$ = (id) => popupContent.querySelector(`#${id}`);
    objContent = {
        layerSelector: $$('layer-selector'), vectorSelector: $$('vector-selector'), 
        sigmaSelector: $$('sigma-selector'), vectorPlotBtn: $$('plotVectorBtn'),
        scale: $$('scaler-value'), 
    }
    await initOptions(objContent.layerSelector, 'layer_hyd', projectName); 
    await initOptions(objContent.vectorSelector, 'vector', projectName);
    await initOptions(objContent.sigmaSelector, 'sigma_waq', projectName); 
    await checkVectorComponents(); mapObj = getMap();
    setStateVisualization({layerSelected: objContent.layerSelector.value});
    setStateVisualization({vectorSelected: objContent.vectorSelector.value});
    setStateVisualization({sigmaSelected: objContent.sigmaSelector.value});
    // Add event listener for objects
    reAssign(objContent.layerSelector, 'layerSelected');
    reAssign(objContent.vectorSelector, 'vectorSelected');
    reAssign(objContent.sigmaSelector, 'sigmaSelected');
    // Set function for 2D dynamic map plot
    document.querySelectorAll('.map2D_dynamic').forEach(plot => {
        plot.addEventListener('click', () => {
            if (obj.substanceContainer.style.display !== 'none') {
                obj.substanceContainer.style.display = 'none';
            }
            const [key, legend, colorbarKey] = plot.dataset.info.split('|');
            if (!key.includes('single')) {
                titleColorbar = objContent.layerSelector.value==='-1' 
                    ? `${legend}\nLayer: ${objContent.layerSelector.selectedOptions[0].text}`
                    : `${legend}\n${objContent.layerSelector.selectedOptions[0].text}`;
            } else titleColorbar = legend;
            const query = `|${objContent.layerSelector.value}`;
            plot2DMapDynamic(
                projectName, mapObj, obj.timeControl, obj.colorbarContainer, obj.colorbarVectorContainer,
                objContent.scale, false, query, key, titleColorbar, colorbarKey, obj.vectorScaler
            );
        });
    });
    // Set function for water quality
    document.querySelectorAll('.waq-function').forEach(item => {
        item.addEventListener('click', async() => {
            obj.substanceContainer.style.display = 'none';
            if (obj.timeSeriesContainer.style.display !== 'none') {
                obj.timeSeriesContainer.style.display = 'none';
            }
            const [query, type] = item.dataset.info.split('|');
            signalSender('showOverlay', 'Getting Substance Data for Map.\nPlease wait...');
            const content = { 
                query: query, key: 'substance_check', 
                projectName: projectName, timeZone: getLastTimeZone()
            };
            const data = await jsonLoader('process_data', content); signalSender('hideOverlay');
            if (data.status === "error") { 
                document.querySelector('.hide-maps').click();
                alert(data.message); return; 
            }
            const substanceTitle = obj.substanceContainer.querySelector('#substance-title');
            substanceTitle.textContent = 'Substance - Spatial Map';
            const substancesContent = obj.substanceContainer.querySelector('#substance-content');
            obj.substanceContainer.style.display = 'flex'; substancesContent.innerHTML = ''; 
            // Add content
            substancesContent.innerHTML = data.content.map((substance, i) => {
                return `<label for="map-${substance}"><input type="radio" name="waq-substance-map" id="map-${substance}"
                    value="${data.content[i]}|${type}" ${i === 0 ? 'checked' : ''}>${data.message[i]}</label>`;
            }).join('');
            const name = data.content[0]; titleColorbar = data.message[0];
            if (type === 'single') {
                newKey = `${name}_waq_single_dynamic`; newQuery = `mesh2d_2d_${name}|${objContent.sigmaSelector.value}`;
            } else {
                newKey = `${name}_waq_multi_dynamic`; newQuery = `mesh2d_${name}|${objContent.sigmaSelector.value}`;
                titleColorbar = objContent.sigmaSelector.value==='-1' 
                    ? `${titleColorbar}\nSigma layer: ${objContent.sigmaSelector.selectedOptions[0].text}`
                    : `${titleColorbar}\n${objContent.sigmaSelector.selectedOptions[0].text}`;
            }
            setStateVisualization({sigma: objContent.sigmaSelector});
            plot2DMapDynamic(
                projectName, mapObj, obj.timeControl, obj.colorbarContainer, obj.colorbarVectorContainer,
                objContent.scale, true, newQuery, newKey, titleColorbar, '', obj.vectorScaler
            );
        });
    });
    // Listen to substance selection
    obj.substanceContainer.addEventListener('change', (e) => {
        if (e.target && e.target.name === "waq-substance-map") {
            if (obj.timeSeriesContainer.style.display !== 'none') {
                obj.timeSeriesContainer.style.display = 'none';
            }
            const [value, type] = e.target.value.split('|');
            const label = e.target.closest('label');
            titleColorbar = label ? label.textContent.trim() : value;
            const sigma = getStateVisualization().sigma;
            if (type === 'single') {
                newKey = `${value}_waq_single_dynamic`; newQuery = `mesh2d_2d_${value}|${sigma.value}`;
            } else {
                newKey = `${value}_waq_multi_dynamic`; newQuery = `mesh2d_${value}|${sigma.value}`;
                titleColorbar = sigma.value==='-1'
                    ? `${titleColorbar}\nLayer: ${sigma.selectedOptions[0].text}`
                    : `${titleColorbar}\n${sigma.selectedOptions[0].text}`;
            }
            plot2DMapDynamic(
                projectName, mapObj, obj.timeControl, obj.colorbarContainer, obj.colorbarVectorContainer,
                objContent.scale, true, newQuery, newKey, titleColorbar, '', obj.vectorScaler
            );
        }
    });
    // Plot vector map
    objContent.vectorPlotBtn.addEventListener('click', () => {
        if (objContent.vectorSelector.value === '') { 
            alert('Please select a vector object.'); 
            document.querySelector('.hide-maps').click(); return;
        }
        const vectorName = objContent.vectorSelector.value;
        const layerName = objContent.layerSelector.value;
        titleColorbar = '', colorbarKey = '';
        if (vectorName === '0') {titleColorbar = 'Velocity (m/s)'; colorbarKey = 'vector';}
        const colorbarTitle = objContent.layerSelector.value==='-1' 
            ? `${titleColorbar}\nLayer: ${objContent.layerSelector.selectedOptions[0].text}` 
            : `${titleColorbar}\n${objContent.layerSelector.selectedOptions[0].text}`;
        plot2DVectorMap(
            projectName, mapObj, obj.timeControl, obj.colorbarContainer, obj.colorbarVectorContainer,
            objContent.scale, 'load', layerName, colorbarTitle, colorbarKey, obj.vectorScaler
        );
    });
    // Select static map
    document.querySelectorAll('.map2D_static').forEach(plot => {
        plot.addEventListener('click', () => {
            const [key, title, colorbarKey] = plot.dataset.info.split('|');
            plot2DMapStatic(
                projectName, mapObj, obj.timeControl, obj.substanceContainer, 
                obj.colorbarContainer, key, title, colorbarKey
            );
        });
    });
    // Velocity
    document.querySelector('#velocity').addEventListener('click', async () => {
        const flowPanel = document.getElementById('velocity-container');
        if (!flowPanel) return; flowPanel.style.display = 'block';
        const { initMovement } = await import("./moveManager.js");
        const mapObj = getMap(), $$ = (id) => document.getElementById(id);
        const objMovement = {
            modelName: $$("modelName"), timeObj: $$("time"), monthObj: $$("months"),
            playObj: $$("play"), playIcon: $$("playIcon"), backObj: $$("back"),
            fwdObj: $$("fwd"), rateObj: $$("rate"), legendObj: $$("legend"), closeObj: $$("close"),
            rampObj: $$("ramp"), legendMinObj: $$("legMin"), legendMidObj: $$("legMid"),
            legendMaxObj: $$("legMax"), collapseObj: $$("collapse"), panelObj: $$("panel"),
            probeHintObj: $$("probeHint"), readoutObj: $$("readout"), traceObj: $$("trace"),
            rSpeedObj: $$("rSpeed"), rDirObj: $$("rDir"), rDepthObj: $$("rDepth"),
            clockObj: $$("clock"), dateObj: $$("date"), loadingObj: $$("loading"),
            resObj: $$("resInput"), motionObj: $$("motion"), curveObj: $$("curve"), 
            downloadObj: $$("download-data-btn")
        }
        const layerObj = document.getElementById('layerInput'); if (!layerObj) return;
        await initOptions(layerObj, 'layer_hyd', projectName);
        layerObj.onchange = async (e) => {
            await initMovement(projectName, e.target.value, mapObj, objMovement, getLastTimeZone()); 
        }
        await initMovement(projectName, layerObj.value, mapObj, objMovement, getLastTimeZone()); 
    });
    // Hide maps
    document.querySelector('.hide-maps').addEventListener('click', () => {
        // Clear map
        mapObj.eachLayer((layer) => { 
            if (!(layer instanceof L.TileLayer)) mapObj.removeLayer(layer); layer = null; 
        });
        obj.timeControl.style.display = 'none'; 
        obj.colorbarContainer.style.display = 'none';
        obj.colorbarVectorContainer.style.display = 'none';
        if (obj.substanceContainer.style.display !== 'none') {
            obj.substanceContainer.style.display = 'none';
        }
        Object.keys(getStateVisualization().gisLayers).forEach(layerName => {
            setStateVisualization({gisLayers: {
                ...getStateVisualization().gisLayers, [layerName]: false
            }});
        });
        setStateVisualization({
            hydLayer: null, sourceLayer: null, wqLoadsLayer: null, wqObsLayer: null, 
            isMultiLayer: false, isClickedInsideLayer: false, isThemocline: false, 
            crosssectionLayer: null, isPathQuery: false
        });
        mapObj.getContainer().style.cursor = '';
    });
}

async function checkVectorComponents() {
    // Initiate objects for vector object
    if (getStateVisualization().layerSelected !== '') { 
        objContent.layerSelector.value = getStateVisualization().layerSelected; 
    }
    if (getStateVisualization().vectorSelected !== '') { 
        objContent.vectorSelector.value = getStateVisualization().vectorSelected; 
    }
    if (getStateVisualization().sigmaSelected !== '') { 
        objContent.sigmaSelector.value = getStateVisualization().sigmaSelected; 
    }
}

function reAssign(target, key){
    target.addEventListener('change', () => { 
        setStateVisualization({[key]: target.value});
        document.querySelector('.hide-maps').click();
    });
}