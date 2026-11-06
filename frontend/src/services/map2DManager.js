import { signalSender, jsonLoader, decodeArray, updateMapByTime } from "./commonFunctions.js";
import { setStateVisualization, getStateVisualization, L, arrowShape, getLastTimeZone } from "./constant.js";
import { getColorFromValue, updateColorbar } from "./unstructuredGrid.js";
import { clearMap } from "./mapManager.js";


let layerAbove = null, layerMap = null, playHandlerAttached = false, 
    playHandlerRef = null, parsedFrame = null;

// Define CanvasLayer
L.CanvasLayer = L.Layer.extend({
    initialize: function (options) { L.setOptions(this, options);},
    onAdd: function (map) {
        this._map = map;
        this._canvas = L.DomUtil.create('canvas', 'leaflet-layer');
        const size = map.getSize();
        this._canvas.width = size.x;
        this._canvas.height = size.y;
        const pane = map.getPane(this.options.pane || 'overlayPane');
        pane.appendChild(this._canvas);
        this._ctx = this._canvas.getContext('2d');
        map.on('moveend zoomend resize', this._reset, this);
        this._reset();
    },
    onRemove: function (map) {
        const pane = map.getPane(this.options.pane || 'overlayPane');
        if (this._canvas) pane.removeChild(this._canvas);
        map.off('moveend zoomend resize', this._reset, this);
    },
    _reset: function () {
        const size = this._map.getSize();
        this._canvas.width = size.x;
        this._canvas.height = size.y;
        const topLeft = this._map.containerPointToLayerPoint([0, 0]);
        L.DomUtil.setPosition(this._canvas, topLeft);
        this._redraw();
    },
    _redraw: function () {
        if (!this._map) return;
        if (typeof this.options.drawLayer === 'function') {
            this.options.drawLayer.call(this);
        }
    }
});


// Create map layer
function layerCreator(colorbarContainer, map, meshes, values, key, vmin, vmax, legend, colorbarKey) {
    const colorbarTitle = colorbarContainer.querySelector("#colorbar-title");
    const colorbarColor = colorbarContainer.querySelector("#colorbar-gradient");
    const colorbarLabel = colorbarContainer.querySelector("#colorbar-labels");
    colorbarContainer.style.display = "block";
    // Filter features
    const filteredFeatures = meshes.features.filter(f => {
        const idx = f.properties.index;
        return values[idx] !== null && values[idx] !== undefined;
    });
    const filteredData = { ...meshes, features: filteredFeatures};
    // Reset variables
    setStateVisualization({lastFeatureColors: {}}); 
    setStateVisualization({featureMap: {}});
    const featureIds = [];
    filteredData.features.forEach(f => {
        const idx = f.properties.index, fmap = getStateVisualization().featureMap;
        fmap[idx] = f; featureIds.push(idx);
        setStateVisualization({featureMap: fmap});
    });
    // Create map layer
    if (layerMap) map.removeLayer(layerMap); layerMap = null;
    layerMap = L.vectorGrid.slicer(filteredData, {
        rendererFactory: L.canvas.tile, vectorTileLayerStyles: {
            sliced: function(properties) {
                const idx = properties.index, value = values[idx];
                // Ignore null values
                if (value === null || value === undefined) return { fill: false, weight: 0, opacity: 0 };
                const { r, g, b, a } = getColorFromValue(value, vmin, vmax, colorbarKey);
                getStateVisualization().lastFeatureColors[idx] = `${r},${g},${b},${a}`;
                setStateVisualization({lastFeatureColors: getStateVisualization().lastFeatureColors});
                return {
                    fill: true, fillColor: `rgb(${r},${g},${b})`, fillOpacity: a, weight: 0, opacity: 1
                };
            },
        }, interactive: true, maxZoom: 18, getFeatureId: f => f.properties.index
    });
    // Tooltip
    const hoverTooltip = L.tooltip({ direction: 'top', sticky: true });
    layerMap.on('mouseover', function(e) {
        if (getStateVisualization().isPlaying) return;
        const idx = e.layer.properties.index;
        // Show tooltip
        const html = `<div style="text-align: center;">
                <b>${legend.split('\n')[0]}:</b> ${values[idx] ?? 'N/A'}
            </div>`;
        hoverTooltip.setContent(html).setLatLng(e.latlng)
        map.openTooltip(hoverTooltip);
    }).on('mouseout', () => {
        map.closeTooltip(hoverTooltip);        
    });
    if (key.includes('multi')) { setStateVisualization({isMultiLayer: true});
    } else { setStateVisualization({isMultiLayer: false}); }
    const polygonCentroids = [];
    filteredData.features.forEach(f => {
        const value = values[f.properties.index];
        if (value !== null && value !== undefined){ 
            const center = turf.centroid(f).geometry.coordinates;
            polygonCentroids.push({ lat: center[1], lng: center[0], value: value });
        }
    });
    setStateVisualization({polygonCentroids: polygonCentroids});
    // Add click event to the layer
    layerMap.on('click', () => { setStateVisualization({isClickedInsideLayer: true}); });
    // Adjust Colorbar Control
    updateColorbar(vmin, vmax, legend, colorbarKey, colorbarColor, colorbarTitle, colorbarLabel);
    // Save layer
    setStateVisualization({mapLayer: featureIds});
    return layerMap;
}

export async function plot2DMapStatic(
    currentProject, map, timeControl, substanceContainer, 
    colorbarContainer, key, legend, colorbarKey) {
    signalSender('showOverlay', 'Preparing Static Map.\nPlease wait...');
    const content = { 
        query: key, projectName: currentProject, key: 'static', timeZone: getLastTimeZone() 
    };
    const data = await jsonLoader('process_data', content); signalSender('hideOverlay');
    if (data.status === 'error') { alert(data.message); return; }
    setStateVisualization({isPlaying: false});
    // Hide timeslider
    timeControl.style.display = 'none'; substanceContainer.style.display = 'none';
    // Get the min and max values of the data
    const vmin = data.content.min_max[0], vmax = data.content.min_max[1];
    const meshes = data.content.meshes, values = data.content.values;
    layerMap = layerCreator(
        colorbarContainer, map, meshes, values, key, vmin, vmax, legend, colorbarKey
    );
    map.addLayer(layerMap);
}

function buildFrameData(data) {
    const coordsArray = data.coordinates, result = [], values = data.values;
    for (let i = 0; i < coordsArray.length; i++) {
        const coords = coordsArray[i], val = values[i];
        let parts = [];
        if (typeof val === 'string') {
            const temp = val.replace(/[()]/g, '');
            parts = temp.split(',').map(s => parseFloat(s.trim()));
        } else if (Array.isArray(val)) { parts = val.map(Number); }
        if (!isNaN(parts[0]) && !isNaN(parts[1]) && !isNaN(parts[2])) {
            result.push({
                x: coords[0], y: coords[1], a: parts[0], b: parts[1], c: parts[2]
            });
        }
    }
    return result;
}

function vectorCreator(colorbarVectorContainer, scaleObj, 
    parsedData, vmin, vmax, title, colorbarKey, vectorScaler) {
    const colorbarTitle = colorbarVectorContainer.querySelector("#colorbar-title-vector");
    const colorbarColor = colorbarVectorContainer.querySelector("#colorbar-gradient-vector");
    const colorbarLabel = colorbarVectorContainer.querySelector("#colorbar-labels-vector");
    const scale = initScaler(scaleObj);
    const layer = new L.CanvasLayer({ data: parsedData,
        drawLayer: function () {
            const ctx = this._ctx, map = this._map;
            const canvas = ctx.canvas, data = this.options.data;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            for (let i = 0; i < data.length; i++) {
                const pt = data[i];
                const p = map.latLngToContainerPoint([pt.y, pt.x]);
                if (p.x < 0 || p.x > canvas.width || p.y < 0 || p.y > canvas.height) continue;
                const dx = pt.a * scale, dy = -pt.b * scale;
                const length = Math.sqrt(dx * dx + dy * dy);
                if (length < 0.1) continue;
                const angle = Math.atan2(dy, dx);
                ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(angle);
                ctx.scale(length, length);
                const color = getColorFromValue(pt.c, vmin, vmax, colorbarKey);
                ctx.strokeStyle = `rgb(${color.r}, ${color.g}, ${color.b})`;
                ctx.lineWidth = 1 / length;
                ctx.stroke(arrowShape); ctx.restore();
            }
        }
    });
    // Adjust Colorbar Control
    vectorScaler.innerHTML = `Scaler: ${scale}`;
    updateColorbar(vmin, vmax, title, colorbarKey, colorbarColor, colorbarTitle, colorbarLabel);
    return layer;
}

function initDynamicMap(projectName, map, timeControl, colorbarContainer,
    colorbarVectorContainer, scaleObj, query, key_below, key_above, data_below, data_above, 
    colorbarTitleBelow, colorbarTitleAbove, colorbarKeyBelow, colorbarKeyAbove, vectorScaler) {
    // Clear map
    map.eachLayer((layer) => { if (!(layer instanceof L.TileLayer)) map.removeLayer(layer); });
    timeControl.style.display = "flex"; // Show time slider
    const slider = timeControl.querySelector("#time-slider");
    const timeSpeed = timeControl.querySelector("#time-slider-speed");
    const playBtn = timeControl.querySelector("#play-btn");
    // Hide colorbar control
    colorbarContainer.style.display = "none"; colorbarVectorContainer.style.display = "none";
    // Destroy slider if it exists
    if (slider.noUiSlider) slider.noUiSlider.destroy();
    // Stop animation if running
    if (getStateVisualization().isPlaying) {
        clearInterval(getStateVisualization().isPlaying); 
        setStateVisualization({isPlaying: null});
        playBtn.textContent = "▶ Play";
    }
    let timestamp = null, currentIndex, vminBelow, vmaxBelow, 
        vminAbove, vmaxAbove, lastRequestId = 0, debounceTimer = null;
    // Process below layer
    if (data_below !== null) {
        // Get min and max values
        vminBelow = data_below.min_max[0]; vmaxBelow = data_below.min_max[1];
        timestamp = data_below.timestamps; currentIndex = timestamp.length - 1;
        const meshes = data_below.meshes, values = data_below.values;
        layerMap = clearMap(layerMap, map);
        layerMap = layerCreator(colorbarContainer, map, meshes, values, key_below, vminBelow,
            vmaxBelow, colorbarTitleBelow, colorbarKeyBelow);
        map.addLayer(layerMap);
        colorbarContainer.style.display = "block";
    }
    // Process above layer
    if (data_above !== null) {
        // Get min and max values
        vminAbove = data_above.min_max[0], vmaxAbove = data_above.min_max[1];
        timestamp = data_above.timestamps; currentIndex = timestamp.length - 1;
        layerAbove = clearMap(layerAbove, map); parsedFrame = buildFrameData(data_above);
        layerAbove = vectorCreator(colorbarVectorContainer, scaleObj, parsedFrame, 
            vminAbove, vmaxAbove, colorbarTitleAbove, colorbarKeyAbove, vectorScaler);
        map.addLayer(layerAbove);
        colorbarVectorContainer.style.display = "block";
    }
    // Create Slider
    const maxIndex = timestamp.length - 1;
    noUiSlider.create(slider, {
        start: currentIndex, step: 1,
        range: { min: 0, max: maxIndex },
        tooltips: [{
            to: value => timestamp[Math.round(value)],
            from: value => timestamp.indexOf(value)
        }]
    });
    // Slider update event (with debounce to avoid multiple requests)
    const handleSliderUpdate = async (values, handle, unencoded) => {
        const rawIndex = unencoded[handle];
        const newIndex = Math.round(rawIndex);
        const safeIndex = Math.max(0, Math.min(newIndex, maxIndex));
        currentIndex = safeIndex;
        // Token to avoid race conditions
        const requestId = ++lastRequestId;
        if (data_below && layerMap) {
            const content = { 
                query: `${query}|${currentIndex}`, key: key_below, 
                projectName: projectName, timeZone: getLastTimeZone()
            };
            const frame_below = await jsonLoader('load_general_dynamic', content);
            if (requestId !== lastRequestId) return;
            if (frame_below.status === 'error') return alert(frame_below.message);
            let parsedFrame = decodeArray(frame_below.content.values, 3);
            if (key_below === 'wd_single_dynamic') parsedFrame = parsedFrame.map(v => -v);
            updateMapByTime(
                setStateVisualization, getStateVisualization,
                layerMap, parsedFrame, vminBelow, vmaxBelow, colorbarKeyBelow
            );
        }
        if (data_above && layerAbove) {
            const content = { 
                query: currentIndex, key: key_above, 
                projectName: projectName, timeZone: getLastTimeZone()
            };
            const frame_above = await jsonLoader('load_vector_dynamic', content);
            if (frame_above.status === 'error') return alert(frame_above.message);
            parsedFrame = buildFrameData(frame_above.content);
            layerAbove.options.data = parsedFrame; layerAbove._redraw();
        }
    };
    // Debounce wrapper
    slider.noUiSlider.on('update', async (values, handle, unencoded) => {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => { handleSliderUpdate(values, handle, unencoded); }, 80);
    });
    // Play/Pause button
    if (playHandlerAttached && playHandlerRef) {
        // Remove previous handler
        playBtn.removeEventListener("click", playHandlerRef);
        playHandlerAttached = false;
    }
    playHandlerRef = () => {
        if (getStateVisualization().isPlaying) {
            clearInterval(getStateVisualization().isPlaying);
            setStateVisualization({isPlaying: null});
            playBtn.textContent = "▶ Play"; return;
        }
        // Get current index
        let idx = Math.round(slider.noUiSlider.get());
        const len = maxIndex + 1;
        const speed = 1000/parseFloat(timeSpeed.value || 1);
        const interval = setInterval(() => {
            idx = (idx + 1) % len;
            slider.noUiSlider.set(idx);
        }, speed);
        setStateVisualization({isPlaying: interval});
        playBtn.textContent = "⏸ Pause";
    };
    playBtn.addEventListener("click", playHandlerRef);
    playHandlerAttached = true;
}

export async function plot2DMapDynamic(projectName, map, timeControl, colorbarContainer, 
    colorbarVectorContainer, scaleObj, waterQuality, query, key, colorbarTitle, colorbarKey, vectorScaler) {
    signalSender('showOverlay', `Preparing Dynamic map.\nPlease wait...`);
    let data_below = null, data_above = null, colorbarTitleAbove = null, 
        colorbarKeyAbove = null, key_below = key, key_above = null;
    setStateVisualization({showedQuery: key}); 
    setStateVisualization({isHYD: waterQuality});  // Set HYD flag
    // Process below layer
    const content = { 
        query: `${query}|load`, key: key, projectName: projectName, timeZone: getLastTimeZone() 
    };
    const dataBelow = await jsonLoader('load_general_dynamic', content);
    signalSender('hideOverlay');
    if (dataBelow.status === 'error') { alert(dataBelow.message); return; }
    data_below = dataBelow.content; data_below.values = decodeArray(data_below.values, 3);
    // If data is water depth, reverse values in below layer    
    if (key === 'wd_single_dynamic') {
        data_below.values = data_below.values.map(v => -v);
        data_below.min_max = [-data_below.min_max[1], -data_below.min_max[0]];
    }
    if (waterQuality) {getStateVisualization().vectorSelected = ''; }
    if (getStateVisualization().vectorSelected !== '' && key.includes('multi')) {
        const vector = document.getElementById("vector-selector");
        const layer = document.getElementById("layer-selector");
        key_above = layer.value;         
        if (vector.selectedOptions[0].text === 'Velocity') {
            // Process above data for velocity
            const title = key_above==='-1' 
                ? `Layer: ${layer.selectedOptions[0].text}` 
                : `${layer.selectedOptions[0].text}`;
            colorbarTitleAbove = `${vector.selectedOptions[0].text} (m/s)\n${title}`; 
            colorbarKeyAbove = 'vector';
        }
        const content = { 
            query: 'load', key: key_above, projectName: projectName, timeZone: getLastTimeZone()
        };
        const dataAbove = await jsonLoader('load_vector_dynamic', content);
        data_above = dataAbove.content; 
    }
    initDynamicMap(
        projectName, map, timeControl, colorbarContainer, colorbarVectorContainer,
        scaleObj, query, key_below, key_above, data_below, data_above, colorbarTitle, 
        colorbarTitleAbove, colorbarKey, colorbarKeyAbove, vectorScaler
    );
}

export async function plot2DVectorMap(projectName, map, timeControl, colorbarContainer, 
    colorbarVectorContainer, scaleObj, query, key, colorbarTitle, colorbarKey, vectorScaler) {
    signalSender('showOverlay', 'Preparing Dynamic Vector Map.\nPlease wait...');
    const content = {query: query, key: key, projectName: projectName, timeZone: getLastTimeZone()};
    const data = await jsonLoader('load_vector_dynamic', content); signalSender('hideOverlay'); 
    if (data.status === 'error') { alert(data.message); return; }
    layerMap = clearMap(layerMap, map); layerAbove = clearMap(layerAbove, map);
    initDynamicMap(
        projectName, map, timeControl, colorbarContainer, colorbarVectorContainer, scaleObj,
        query, null, key, null, data.content, null, colorbarTitle, null, colorbarKey, vectorScaler
    );
}

function initScaler(scaleObj) {
    // Initialize vector scale
    if (scaleObj === null) {
        setStateVisualization({scalerValue: 1000});
        return getStateVisualization().scalerValue;
    };
    if (parseFloat(scaleObj.value) <= 0) {
        alert('Wrong scaler value. Please check the scaler object.'); return;
    }
    getStateVisualization().scalerValue = scaleObj.value;
    // Store scaler value
    setStateVisualization({scalerValue: scaleObj.value}); 
    return parseFloat(getStateVisualization().scalerValue);
}