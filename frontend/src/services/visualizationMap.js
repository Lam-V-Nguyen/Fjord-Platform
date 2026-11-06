import { CENTER, ZOOM, L, setMap, getLastTimeZone, highlightColor } from "./constant.js";
import { plotTimeSeries } from "./chartManager.js";
import { fillTable, getDataFromTable, signalSender, jsonLoader } from "./commonFunctions.js";

let tileLayer = null, timeCounter = null;

const $ = (id) => document.getElementById(id);
const obj = { baseMap: $("basemap-btn") };

export async function initMap(id) {
    const map = L.map(id, { center: CENTER, zoom: ZOOM, zoomControl: false, attributionControl: true });
    tileLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png").addTo(map); setMap(map);
    L.control.scale({imperial: false, metric: true, maxWidth: 200}).addTo(map);
    setTimeout(() => { map.invalidateSize(); }, 0);
    const baseMapPopup = document.querySelector('.basemap-popup');
    obj.baseMap.addEventListener('mouseenter', () => { 
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
            tileLayer.setUrl(url); 
            baseMapPopup.classList.remove('show'); 
        } 
    });
    return map;
}

export async function pointPloter(key, points, map, content) {
    const timeZone = getLastTimeZone();
    const tempLayer = L.geoJSON(points, {
        pointToLayer: (feature, latlng) => {
            const iconType = feature?.properties?.mode || content?.pointType || key;
            let iconUrl = `/src_frontend/images/station.png?v=${Date.now()}`;
            if (iconType === 'flow') { iconUrl = `/src_frontend/images/water_flow.png?v=${Date.now()}`; }
            else if (iconType === 'level') { iconUrl = `/src_frontend/images/water_level.png?v=${Date.now()}`; }
            else if (iconType === 'rain') { iconUrl = `/src_frontend/images/rain.png?v=${Date.now()}`; }
            else if (iconType === 'met') { iconUrl = `/src_frontend/images/met.png?v=${Date.now()}`; }
            else if (iconType === 'nve') { iconUrl = `/src_frontend/images/nve.png?v=${Date.now()}`; }
            const marker = L.marker(latlng, {
                icon: L.icon({
                    iconUrl: iconUrl, iconSize: [20, 20], iconAnchor: [10, 10]
                }),
            });
            return marker;
        },
        onEachFeature: (feature, layer) => {
            if (key === 'met') {
                const properties = feature.properties || {};
                const lat = feature.geometry.coordinates[1] || 'N/A';
                const lon = feature.geometry.coordinates[0] || 'N/A';
                const name = properties.name || 'No name', id = properties.id || 'N/A';
                const country = properties.country || 'N/A';
                const { getSelectedLayer, setSelectedLayer, getSelected, table, label } = content;
                const formatValue = (value) => {
                    if (value === null || value === undefined || value === '') { return 'N/A'; }
                    if (Array.isArray(value)) { return value.length > 0 ? value.join(', ') : 'N/A'; }
                    return value;
                };
                const hoverTooltip = `
                    <div style="font-size: 14px; border-radius: 10px; line-height: 1.4;">
                        <span style=" display: block; text-align: center; font-weight: bold; line-height: 1.2;">
                            ${formatValue(id)}
                        </span>
                        <hr style="border-top: 1px solid #5d5d61ff; margin: 5px 0;">
                        <span>• Country: ${formatValue(country)}</span><br>
                        <span>• County: ${formatValue(properties.county)}</span><br>
                        <span>• Municipality: ${formatValue(properties.municipality)}</span><br>
                        <span>• Elevation: ${formatValue(properties.masl)} m</span>
                    </div>
                `;
                const fullTooltip = `
                    <div class="met-tooltip-content" style="
                        font-size: 14px; border-radius: 10px; line-height: 1.4;">
                        <span style="display: block; text-align: center;
                            font-weight: bold; line-height: 1.2;">
                            ${formatValue(properties.id)}
                        </span>
                        <hr style="border-top: 1px solid #5d5d61ff; margin: 5px 0;">
                        ${Object.entries(properties)
                        .filter(([propertyKey]) => propertyKey !== 'id')
                        .map(([propertyKey, value]) => `
                            <div>• <b>${propertyKey}:</b> ${formatValue(value)}</div>
                        `).join('')}
                    </div>
                `;
                layer._hoverTooltip = hoverTooltip; layer._fullTooltip = fullTooltip;
                layer.bindTooltip(hoverTooltip, {
                    sticky: true, permanent: false, direction: 'bottom', opacity: 1, offset: [0, 10]
                });
                layer.on('click', () => {
                    const prevLayer = getSelectedLayer ? getSelectedLayer() : null;
                    if (prevLayer && prevLayer !== layer) {
                        prevLayer.closeTooltip(); prevLayer.unbindTooltip();
                        if (prevLayer._hoverTooltip) {
                            prevLayer.bindTooltip(prevLayer._hoverTooltip, {
                                sticky: true, permanent: false,
                                direction: 'bottom', opacity: 1, offset: [0, 10]
                            });
                        }
                    }
                    if (setSelectedLayer) setSelectedLayer(layer); layer.unbindTooltip();
                    layer.bindTooltip(fullTooltip, {
                        permanent: true, direction: 'bottom', opacity: 1, offset: [0, 10], className: 'met-tooltip'
                    }).openTooltip();
                    const isSelected = getSelected ? getSelected() : false;
                    if (isSelected && table && label) {
                        const stationId = String(id), rows = getDataFromTable(table, true).rows;
                        // Check whether the station ID already exists in the table.
                        const exists = Array.from(rows).some(row => {
                            const firstCell = row[0]; return firstCell && firstCell.trim() === stationId;
                        });
                        if (exists) { alert(`Station "${stationId}" has already been selected.`); return; }
                        const data = [stationId, name, country, lat, lon]; fillTable([data], table, false);
                        const n = table.querySelectorAll('tbody tr.selected').length;
                        const m = table.querySelectorAll('tbody tr').length;
                        label.innerHTML = `(Selected Station(s): ${n}/${m})`;
                    }
                });
            } else if (key === 'nve') {
                const properties = feature.properties || {};
                const lat = properties.latitude || 'N/A', lon = properties.longitude || 'N/A';
                const name = properties.stationName || 'No name', id = properties.stationId || 'N/A';
                const river = properties.riverName || 'N/A', council = properties.councilName || 'N/A';
                const { getSelectedLayer, setSelectedLayer, getSelected, table, label, key, params } = content;
                const formatValue = (value) => {
                    if (value === null || value === undefined || value === '') { return 'N/A'; }
                    if (Array.isArray(value)) { return value.length > 0 ? value.join(', ') : 'N/A'; }
                    return value;
                };
                const hoverTooltip = `
                    <div style="font-size: 14px; border-radius: 10px; line-height: 1.4;">
                        <span style=" display: block; text-align: center; font-weight: bold; line-height: 1.2;">
                            ${formatValue(id)}
                        </span>
                        <hr style="border-top: 1px solid #5d5d61ff; margin: 5px 0;">
                        <span>• Station Name: ${name}</span><br><span>• River Name: ${river}</span><br>
                        <span>• Council Name: ${council} m</span>
                    </div>
                `;
                const fullTooltip = `
                    <div class="met-tooltip-content" style="
                        font-size: 14px; border-radius: 10px; line-height: 1.4;">
                        <span style="display: block; text-align: center;
                            font-weight: bold; line-height: 1.2;">
                            ${formatValue(id)}
                        </span>
                        <hr style="border-top: 1px solid #5d5d61ff; margin: 5px 0;">
                        ${Object.entries(properties)
                        .filter(([propertyKey]) => propertyKey !== 'id')
                        .map(([propertyKey, value]) => `
                                <div>• <b>${propertyKey}:</b> ${formatValue(value)}</div>
                            `).join('')}
                    </div>
                `;
                layer._hoverTooltip = hoverTooltip; layer._fullTooltip = fullTooltip;
                layer.bindTooltip(hoverTooltip, {
                    sticky: true, permanent: false, direction: 'bottom', opacity: 1, offset: [0, 10]
                });
                layer.on('click', () => {
                    const prevLayer = getSelectedLayer ? getSelectedLayer() : null;
                    if (prevLayer && prevLayer !== layer) {
                        prevLayer.closeTooltip(); prevLayer.unbindTooltip();
                        if (prevLayer._hoverTooltip) {
                            prevLayer.bindTooltip(prevLayer._hoverTooltip, {
                                sticky: true, permanent: false, direction: 'bottom', opacity: 1, offset: [0, 10]
                            });
                        }
                    }
                    if (setSelectedLayer) setSelectedLayer(layer); layer.unbindTooltip();
                    layer.bindTooltip(fullTooltip, {
                        permanent: true, direction: 'bottom', opacity: 1, offset: [0, 10], className: 'met-tooltip'
                    }).openTooltip();
                    const isSelected = getSelected ? getSelected() : false;
                    if (isSelected && table && label) {
                        const stationId = String(id), rows = getDataFromTable(table, true).rows;
                        // Check whether the station ID already exists in the table.
                        const exists = Array.from(rows).some(row => {
                            const firstCell = row[0]; return firstCell && firstCell.trim() === stationId;
                        });
                        if (exists) { alert(`Station "${stationId}" has already been selected.`); return; }
                        const data = [stationId, name, river, council, lat, lon]; fillTable([data], table, false);
                        const n = table.querySelectorAll('tbody tr.selected').length;
                        const m = table.querySelectorAll('tbody tr').length;
                        label.innerHTML = `(Selected Station(s): ${n}/${m})`;
                    }
                    if (key === 'data-preparation') {
                        const {stationID, stationName, basinArea} = params;
                        stationID.value = id; stationName.value = name;
                        basinArea.value = properties?.drainageBasinArea || properties?.drainageBasinAreaNorway || '';
                    }
                });
            } else if (key === 'regnbyge' || ['flow', 'level', 'rain'].includes(key)) {
                const { 
                    startObj, endObj, intervalObj, params, containerObj, 
                    tableObj, selectorObj, labelObj, getPlotChecked
                } = content || {};
                layer.on('click', async () => {
                    const id = feature.properties.id, name = feature.properties.name;
                    if (getPlotChecked && getPlotChecked()) {
                        const mode = feature.properties.mode, interval = intervalObj.value;
                        const startTime = startObj.value, endTime = endObj.value;
                        if (startTime === '' || endTime === '') { alert('Please select a time range to plot.'); return; }
                        const titleY = intervalObj.selectedOptions[0].text;
                        signalSender('showOverlay',
                            `Getting '${intervalObj.selectedOptions[0].text}' for station '${name}'.\nThis takes a while. Please wait...`
                        );
                        const contents = {
                            id: [id], name: name, mode: mode, timeZone: timeZone,
                            startTime: startTime, endTime: endTime, interval: interval,
                            clientName: params?.nameID, clientSecret: params?.secret, 
                            clientUserName: params?.userName, clientPassword: params?.password
                        };
                        const response = await jsonLoader('plot_station', contents); signalSender('hideOverlay');
                        if (response.status === "error") { alert(response.message); return; }
                        const chartTitle = `Station: ${name}`, titleX = 'Time';
                        await plotTimeSeries(
                            containerObj, chartTitle, response.content, name, titleX, titleY
                        );
                    } else {
                        const type = feature.properties.type;
                        const data = [name, String(id), type];
                        const tableData = getDataFromTable(tableObj, true);
                        const exitCheck = tableData.rows.some(row => row.length === data.length &&
                            row.every((value, index) => value === data[index]));
                        if (!exitCheck) { fillTable([data], tableObj, false); }
                        selectStations(selectorObj.value, tableObj, labelObj);
                    }
                });
                layer.bindTooltip(() => {
                    const isPlot = getPlotChecked ? getPlotChecked() : false;
                    const note = isPlot
                        ? `<hr style="border-top: 1px solid #5d5d61ff; margin: 5px 0 5px 0;"><span style="display: block; font-weight: bold; text-align: center; line-height: 1.0;">Click to plot time-series data</span>`
                        : '';
                    return `<div style="font-size: 14px; border-radius: 10px;">
                        <span style="display: block; text-align: center; font-weight: bold; line-height: 1.0;">${feature.properties.name || 'No name'}</span>
                        <hr style="border-top: 1px solid #5d5d61ff; margin: 5px 0 5px 0;">
                        ${Object.entries(feature.properties).filter(([k]) => k !== 'name' && k !== 'mode')
                            .map(([k, value]) => `<span>• ${k}: ${value}</span><br>`).join('')}${note}
                    </div>`;
                }, { sticky: true, permanent: false, direction: 'bottom', opacity: 1, offset: [0, 10] });
            }
        }
    }).addTo(map);
    const bounds = tempLayer.getBounds();
    if (bounds.isValid()) {
        setTimeout(() => { map.invalidateSize(); map.fitBounds(bounds); }, 0);
    }
    return tempLayer;
}

export function selectStations(dataType, table, label) {
    const checkList = [dataType];
    if (dataType === 'permanent') { checkList.push('permanentTemp'); }
    const checkSet = new Set(checkList);
    const rows = table.querySelectorAll('tbody tr');
    let selectedCount = 0;
    rows.forEach(row => {
        const cells = row.querySelectorAll('td');
        if (cells.length < 3) return;
        const input = cells[2].querySelector('input');
        if (!input) return;
        const value = input.value.trim();
        if (checkSet.has(value)) {
            selectedCount++; row.classList.add('selected');
        } else { row.classList.remove('selected'); }
    });
    if (label.style.display === 'none') { label.style.display = 'flex'; }
    label.innerHTML = `Station(s) selected: ${selectedCount}`;
}

export async function mapPlotter(data, map, key) {
    let selectedLayer = null, layer; const isRiver = key === 'River';
    layer = L.geoJSON(data, {
        style: feature => {
            const id = feature.properties.id;
            if (isRiver) {
                return { color: 'black', weight: 3, opacity: 0.6, fill: false };
            }
            return {
                color: 'black', weight: 1, opacity: 1, fillOpacity: 0.8,
                fillColor: highlightColor(id ?? 0)
            };
        },
        onEachFeature: (feature, featureLayer) => {
            const description = feature.properties.description ?? key;
            let content = '';
            if (key === 'Land Cover') {
                content = `<div style="font-size: 13px;">
                    <div style="font-weight: bold; text-align: center;">Type: ${description}</div>
                    <hr style="margin: 5px 0 5px 0;">
                    <div style="display: block; text-align: center;">Click to see details</div>
                `;
                const popupContent = buildTooltip(feature.properties, key);
                featureLayer.bindPopup(
                    `<div class="popup-content">${popupContent}</div>`,
                    { maxWidth: 400, maxHeight: 200 }
                );
            } else { content = buildTooltip(feature.properties, key); }
            if (isRiver) {
                content += `<hr style="margin: 5px 0 5px 0;">
                    <div style="display: block; text-align: center; font-size: 14px;">Click to edit river's properties</div>
                `;
            }
            // Show tooltip when hovering
            featureLayer.bindTooltip(content, { sticky: true, direction: 'bottom' });
            featureLayer.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                if (selectedLayer && selectedLayer !== featureLayer) { layer.resetStyle(selectedLayer); }
                featureLayer.setStyle({ color: 'yellow', weight: isRiver ? 5 : 3 });
                selectedLayer = featureLayer;
            });
        }
    }).addTo(map);
    const bounds = layer.getBounds();
    if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [20, 20], maxZoom: 15, animate: true });
    }
    return layer;
}

export function buildTooltip(props, key) {
    let html = `<div style="font-size: 15px;">
        <div style="font-weight: bold; text-align: center;">Type: ${props.description || key}</div>
        <hr style="margin: 5px 0 5px 0;">
    `, unit = '';
    if (key === 'River') { unit = ' (m)'; }
    Object.entries(props).forEach(([k, v]) => {
        if (k === "description" || k === "geometry" || k === "id") return;
        html += `<strong>• ${k}:</strong> ${v}${unit}<br>`;
    }); html += `</div>`;
    return html;
}