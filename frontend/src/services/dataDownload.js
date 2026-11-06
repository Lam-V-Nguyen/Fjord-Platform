import { setupTabs } from "./tabManager.js";
import { initMap, pointPloter, selectStations } from "./visualizationMap.js";
import { getDataFromTable, signalSender, jsonLoader, fillTable,
    formatDate, moveWindow, closeWindow, deleteTable, getUser, 
    updateLog, addDataToTable, saveFileSmart
} from "./commonFunctions.js";
import { L, getLastTimeZone } from "./constant.js";

const hoverTooltip = L.tooltip({
    permanent: false, direction: 'bottom',
    sticky: true, offset: [0, 10], className: 'custom-tooltip'
});

window._uploadedGISFiles = {}; window._gisFileIdCounter = 0;

const $ = (id) => document.getElementById(id);
const obj = {
    plotDataContainer: $("plot-station-window"), plotDataHeader: $("plot-station-header"),
    plotDataCloseBtn: $("close-station-plot"), selectBox: $("select-object"),
    checkboxList: $("checkbox-list"), dropdown: $("select-object"), downloadInterval: $("interval-download"),
    stationSelectedTable: $("station-selected-table"), plotStart: $("start-plot"),
    plotEnd: $("end-plot"), plotInterval: $("interval-plot"), downloadBtn: $("download-btn"),
    downloadStart: $("start-download"), downloadEnd: $("end-download"),
    typeSelector: $("type-download"), plotContainer: $("plot-container"),
    stationTable: $("station-table"), waterFlowCheckbox: $("water-flow-checkbox"),
    waterLevelCheckbox: $("water-level-checkbox"), rainfallCheckbox: $("rainfall-checkbox"),
    dataGISBtn: $("upload-gis-btn"), dataGISFile: $("data-gis-file"), dataGISContainer: $("data-gis-container"),
    // Met options
    clientIdMet: $("met-client-id"), clientRememberMet: $("met-remember-btn"), metVariablesBtn: $("met-variable-btn"),
    metLocationBtn: $("met-location-btn"), stationCheckboxMet: $("met-station-checkbox"),
    metLabel: $("met-station-label"), metSelectedLabel: $("met-station-selected-label"),
    metTable: $("met-station-table"), metDeleteBtn: $("met-delete-btn"), metStart: $("met-start"),
    metEnd: $("met-end"), metDownloadBtn: $("met-download-btn"), metSaveBtn: $("met-save-btn"),
    metDownloadTable: $("met-table"), metLogContainer: $("met-log-container"), metLogText: $("met-log-text"),
    // NVE options
    clientIdNVE: $("nve-client-id"), clientRememberNVE: $("nve-remember-btn"),
    nveLocationBtn: $("nve-location-btn"), stationCheckboxNVE: $("nve-station-checkbox"),
    nveTable: $("nve-station-table"), nveDeleteBtn: $("nve-delete-btn"), nveLabel: $("nve-station-label"),
    nveSelectedLabel: $("nve-station-selected-label"), nveStart: $("nve-start"), nveEnd: $("nve-end"),
    nveInterval: $("nve-download-interval"), nveDownloadBtn: $("nve-download-btn"),
    nveSaveBtn: $("nve-save-btn"), nveVariablesBtn: $("nve-variable-btn"),
    nveDownloadTable: $("nve-table"), nveLogContainer: $("nve-log-container"), nveLogText: $("nve-log-text"),
    // Regnbyge options
    clientIdRegnbyge: $("regnbyge-client-id"), clientSecretRegnbyge: $("regnbyge-client-secret"),
    clientRememberRegnbyge: $("regnbyge-remember-btn"), clientUserNameRegnbyge: $("regnbyge-client-username"),
    clientPasswordRegnbyge: $("regnbyge-client-password"),
    // overFlowCheckbox: $("overflow-checkbox"), temperatureCheckbox: $("temperature-checkbox"),
    // evaporationCheckbox: $("evaporation-checkbox"), weirCheckbox: $("weir-checkbox"),
    stationSelectedLabel: $("station-selected-label"), resertStationBtn: $("reset-station-btn"),
    downloadListContainer: $("download-list-container"), downloadListArea: $("download-list"),
    // ERA5 options
    apikeyEra5: $("era5-api-key"), clientRememberEra5: $("era5-remember-btn"),
    era5LocationBtn: $("era5-location-btn"), era5Lat: $("era5-latitude"),
    era5Lon: $("era5-longitude"), era5WindSpeed: $("wind-speed-checkbox"),
    era5WWindU: $("wind-u-checkbox"), era5WWindV: $("wind-v-checkbox"),
    era5Start: $("era5-start"), era5End: $("era5-end"), era5Table: $("era5-table"),
    era5LogContainer: $("era5-log-container"), era5LogText: $("era5-log-text"),
    era5DownloadBtn: $("era5-download-btn"), era5CancelBtn: $("era5-cancel-btn"), 
    era5ClearQueueBtn: $("era5-clear-btn"), era5SaveBtn: $("era5-save-btn")
};
const ERA5_KEY = 'era5';

let plotChecked = true, waterFlowLayer = null, waterLevelLayer = null, 
    overFlowLayer = null, tempLayer = null, preLayer = null, weirLayer = null, 
    evaLayer = null, currentProject = null, era5Checked = false,
    nameID = null, secret = null, userName = null, password = null, key = 'met',
    metLayer = null, metSelected = false, selectedMetLayer = null, nveLayer = null,
    nveSelected = false, selectedNVELayer = null, era5ClearingQueue = false;

setupTabs(document); await getProject();
const mapObj = await initMap('leaflet-map-data'); mapOptions(mapObj);
savePassword(); await loadClient(key); updateManager();


async function getProject() {
    const userName = await getUser();
    currentProject = userName.split('/').pop();
}

function savePassword() {
    document.querySelectorAll('.toggle-password').forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            const input = this.parentElement.querySelector('input');
            if (!input) { return; }
            if (input.type === 'password') {
                input.type = 'text'; this.textContent = '🙈';
            } else {
                input.type = 'password'; this.textContent = '👁️';
            }
        });
    });
    obj.clientRememberMet.addEventListener('click', async () => {
        if (key === '') { key = 'met'; }
        nameID = obj.clientIdMet.value;
        if (nameID === '') {
            alert('Please check registration information and try again.\nInformation is NOT saved.'); return;
        }
        const content = {
            projectName: currentProject, key: key,
            clientName: nameID, clientSecret: secret
        };
        const response = await jsonLoader('save_client', content);
        alert(response.message); if (response.status === "error") { return; }
    });
    obj.clientRememberNVE.addEventListener('click', async () => {
        if (key === '') { key = 'nve'; }
        nameID = obj.clientIdNVE.value;
        if (nameID === '') {
            alert('Please check registration information and try again.\nInformation is NOT saved.'); return;
        }
        const content = {
            projectName: currentProject, key: key,
            clientName: nameID, clientSecret: secret
        };
        const response = await jsonLoader('save_client', content);
        alert(response.message); if (response.status === "error") { return; }
    });
    obj.clientRememberRegnbyge.addEventListener('click', async () => {
        if (key === '') { key = 'regnbyge'; }
        nameID = obj.clientIdRegnbyge.value; secret = obj.clientSecretRegnbyge.value;
        userName = obj.clientUserNameRegnbyge.value; password = obj.clientPasswordRegnbyge.value;
        if (nameID === '' || secret === '' || userName === '' || password === '') {
            alert('Please check registration information and try again.\nInformation is NOT saved.'); return;
        }
        const content = {
            projectName: currentProject, key: key, clientName: nameID,
            clientSecret: secret, clientUsername: userName, clientPassword: password
        };
        const response = await jsonLoader('save_client', content);
        alert(response.message); if (response.status === "error") { return; }
    });
    obj.clientRememberEra5.addEventListener('click', async () => {
        if (key === '') { key = 'era5'; }; nameID = obj.apikeyEra5.value;
        if (nameID === '') {
            alert('Please check registration information and try again.\nInformation is NOT saved.'); return;
        }
        const content = { projectName: currentProject, key: key, clientName: nameID };
        const response = await jsonLoader('save_client', content);
        alert(response.message); if (response.status === "error") { return; }
    });
}

async function loadClient(key) {
    const content = { projectName: currentProject, key: key };
    const response = await jsonLoader('load_client', content);
    nameID = response.content.client_id; secret = response.content.client_secret;
    userName = response.content.client_username; password = response.content.client_password;
    if (key === 'met') {
        obj.clientIdMet.value = nameID;
    } else if (key === 'nve') {
        obj.clientIdNVE.value = nameID;
    } else if (key === 'regnbyge') {
        obj.clientIdRegnbyge.value = nameID; obj.clientSecretRegnbyge.value = secret;
        obj.clientUserNameRegnbyge.value = userName; obj.clientPasswordRegnbyge.value = password;
    } else if (key === 'era5') { obj.apikeyEra5.value = nameID; }
}

function updateManager() {
    const now = new Date(); now.setHours(0, 0, 0, 0);
    const start = new Date(now); start.setDate(start.getDate() - 20);
    const end = new Date(now); end.setDate(end.getDate() - 10);
    moveWindow(obj.plotDataHeader, obj.plotDataContainer);
    closeWindow(obj.plotDataCloseBtn, obj.plotDataContainer);
    hightlightRows(obj.stationSelectedTable, obj.stationSelectedLabel);
    hightlightRows(obj.metTable, obj.metSelectedLabel);
    hightlightRows(obj.nveTable, obj.nveSelectedLabel);
    obj.plotStart.value = formatDate(start); obj.plotEnd.value = formatDate(end);
    obj.downloadStart.value = formatDate(start); obj.downloadEnd.value = formatDate(end);
    obj.era5Start.value = formatDate(start); obj.era5End.value = formatDate(end);
    obj.metStart.value = formatDate(start).split(' ')[0]; obj.metEnd.value = formatDate(end).split(' ')[0];
    obj.nveStart.value = formatDate(start).split(' ')[0]; obj.nveEnd.value = formatDate(end).split(' ')[0];
    obj.selectBox.addEventListener("click", () => { obj.checkboxList.style.display === 'block'; });
    document.addEventListener('click', (event) => {
        if (!obj.dropdown.contains(event.target)) obj.checkboxList.style.display = 'none';
    });
    // Toggle sub tabs
    document.querySelectorAll('[data-tab]').forEach(tab => {
        tab.addEventListener('click', async () => {
            const tabName = tab.getAttribute('data-tab');
            if (tabName === 'regnbyge-tab') {
                key = 'regnbyge'; await loadClient(key);
            } else if (tabName === 'eklima-tab') {
                key = 'met'; await loadClient(key);
            } else if (tabName === 'nve-tab') {
                key = 'nve'; await loadClient(key);
            } else if (tabName === 'regnbyge-tab-2') {
                plotChecked = true; deleteTable(obj.stationSelectedTable);
            } else if (tabName === 'regnbyge-tab-3') {
                plotChecked = false;
            } else if (tabName === 'era5-tab') {
                key = 'era5'; await loadClient(key);
                // Clear map
                plotChecked = true; deleteTable(obj.stationSelectedTable);
                obj.waterFlowCheckbox.checked = false;
                obj.waterFlowCheckbox.dispatchEvent(new Event('change'));
                obj.waterLevelCheckbox.checked = false;
                obj.waterLevelCheckbox.dispatchEvent(new Event('change'));
                obj.rainfallCheckbox.checked = false;
                obj.rainfallCheckbox.dispatchEvent(new Event('change'));
            }
            setTimeout(() => { mapObj.invalidateSize(); }, 10);
            obj.stationSelectedLabel.style.display = 'none';
            updateLayerTooltips(waterFlowLayer); updateLayerTooltips(waterLevelLayer);
            updateLayerTooltips(overFlowLayer); updateLayerTooltips(tempLayer);
            updateLayerTooltips(preLayer); updateLayerTooltips(evaLayer);
            updateLayerTooltips(weirLayer);
        });
    });
    // Work on MET option
    obj.metLocationBtn.addEventListener('click', async () => {
        const key = 'met', apiKey = obj.clientIdMet.value;
        if (!apiKey || apiKey === '') { alert('Please enter your MET API key.'); return; }
        signalSender('showOverlay', 'Getting stations from MET. Please wait...');
        const content = { projectName: currentProject, key: key, clientName: apiKey };
        const data = await jsonLoader('stations_met', content); signalSender('hideOverlay');
        if (data.status === 'error') { alert(data.message); return; }
        obj.waterFlowCheckbox.checked = false; obj.waterLevelCheckbox.dispatchEvent(new Event('change'));
        obj.waterLevelCheckbox.checked = false; obj.waterLevelCheckbox.dispatchEvent(new Event('change'));
        obj.rainfallCheckbox.checked = false; obj.rainfallCheckbox.dispatchEvent(new Event('change'));
        nveLayer = clearMap(nveLayer, mapObj); deleteTable(obj.nveTable);
        metLayer = clearMap(metLayer, mapObj); deleteTable(obj.metTable);
        const contents = {
            getSelectedLayer: () => selectedMetLayer, 
            setSelectedLayer: (layer) => { selectedMetLayer = layer; },
            getSelected: () => metSelected, table: obj.metTable, label: obj.metSelectedLabel
        }
        metLayer = await pointPloter(key, data.content.point, mapObj, contents);
        obj.metLabel.textContent = `Number of Stations: ${data.content.length}`;
    });
    obj.stationCheckboxMet.addEventListener('change', async (e) => {
        metSelected = e.target.checked;
        if (metSelected) {
            if (!metLayer) {
                alert('Please click "Get Stations" first to load MET stations first.');
                e.target.checked = false; metSelected = false; return;
            }
        }
    });
    obj.metDeleteBtn.addEventListener('click', async () => {
        const selectedRows = obj.metTable.querySelectorAll('tbody tr.selected');
        if (selectedRows.length === 0) { alert('No station selected. Please select a station from the table first.'); return; }
        selectedRows.forEach(row => row.remove());
        const n = obj.metTable.querySelectorAll('tbody tr.selected').length;
        obj.metSelectedLabel.textContent = `Selected Stations: ${n}`;
    });
    obj.metVariablesBtn.addEventListener('click', async () => {
        const metGrid = document.getElementById("met-variables-grid");
        if (!metGrid) { alert('Cannot find Variable container. Please recheck frontend components.'); return; }
        const apiKey = obj.clientIdMet.value; metGrid.innerHTML = '';
        if (!apiKey || apiKey === '') { alert('Please enter your MET API key.'); return; }
        const tableData = getDataFromTable(obj.metTable, true);
        if (tableData.rows.length === 0) {
            alert('No station selected. Please select a station from the map first.'); return;
        }
        const ids = tableData.rows.map(row => row[0]);
        signalSender('showOverlay', 'Getting Variables from MET Database. Please wait...');
        const response = await jsonLoader('met_parameters', { apiKey: apiKey, ids: ids }); 
        signalSender('hideOverlay');
        if (response.status === 'error') { alert(response.message); return; }
        if (response.content.length === 0) {
            metGrid.innerHTML = `<strong style="display: flex; text-align: center; color: red; font-size: 20px; padding: 20px;">No Variables found</strong>`; return;
        }
        response.content.forEach(variable => {
            const label = document.createElement('label');
            label.className = 'checkbox-right';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox'; checkbox.value = variable[0];
            label.appendChild(checkbox);
            label.appendChild(document.createTextNode(variable[0]));
            metGrid.appendChild(label);
        });
    });
    obj.metDownloadBtn.addEventListener('click', async () => {
        const apiKey = obj.clientIdMet.value, key = 'met', logFile = 'MET_log.txt';
        if (!apiKey || apiKey === '') { alert('Please enter your MET API key.'); return; }
        const tableData = getDataFromTable(obj.metTable, true);
        if (tableData.rows.length === 0) {
            alert('No station selected. Please select a station from the map first.'); return;
        }
        const ids = tableData.rows.map(row => [row[0], row[3], row[4]]);
        const startTime = obj.metStart.value, endTime = obj.metEnd.value;
        if (startTime === '' || endTime === '') { alert('Please select start and end time to download.'); return; }
        // Get variables selected
        const selectedValues = [...document.querySelectorAll(
            '#met-variables-grid input[type="checkbox"]:checked'
        )].map(checkbox => ({
            value: checkbox.value, des: checkbox.parentElement.textContent.trim()
        }));
        if (selectedValues.length === 0) {
            alert('Please select at least one variable to download.'); return;
        }
        try {
            const statusRes = await jsonLoader('check_download_status', { projectName: currentProject, key: key });
            if (statusRes.status === "running") { alert("Data download is running."); return; }
            obj.metLogText.value = ''; obj.metLogContainer.style.display = 'flex';
            obj.metSaveBtn.style.display = 'none'; obj.metDownloadTable.style.display = 'none';
            const contents = {
                projectName: currentProject, api_key: apiKey, ids: ids, key: key, logFile: logFile,
                startTime: startTime, endTime: endTime, timeZone: getLastTimeZone(),
                variables: selectedValues.map(v => v.value), columns: selectedValues.map(v => v.des)
            };
            const data = await jsonLoader('download_met', contents);
            if (data.status === 'error') { alert(data.message); return; }
            updateLog(currentProject, obj.metLogText, 2, key, async () => {
                alert('Downloading data completed.');
                const content_csv = { projectName: currentProject, fileName: `${key}.csv` };
                signalSender('showOverlay', 'Uploading weather data to table. Please wait...');
                const csv = await jsonLoader('upload_weather_csv', content_csv);
                signalSender('hideOverlay');
                if (csv.status === 'error') { alert(csv.message); return; }
                addDataToTable(obj.metDownloadTable, csv.columns, csv.content);
                obj.metSaveBtn.style.display = 'block'; obj.metLogContainer.style.display = 'none';
                obj.metDownloadTable.style.display = 'table';
            }, logFile);
        } catch (error) {
            alert(error.message || error); obj.metSaveBtn.style.display = 'none'; return;
        }
    });
    obj.metSaveBtn.addEventListener('click', async () => {
        const tableData = getDataFromTable(obj.metTable, true);
        if (tableData.rows.length === 0) {
            alert('No station selected. Please select a station from the map first.'); return;
        }
        const ids = tableData.rows.map(row => [row[0], row[3], row[4]]);
        const data = getDataFromTable(obj.metDownloadTable, true);
        if (data.rows.length === 0) { alert('No data to save.'); return; }
        try {
            const response = await fetch('/save_weather_csv', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: ids, data: data })
            });
            if (response.ok) {
                const blob = await response.blob();
                await saveFileSmart(blob, `met_data.zip`, true);
                alert('Save ZIP file completed.')
            }
        } catch (error) {
            alert(`Failed to save MET data:\n${error.message}`);
        }
    });
    // Work on NVE option
    obj.nveLocationBtn.addEventListener('click', async () => {
        const key = 'nve', apiKey = obj.clientIdNVE.value;
        if (apiKey === '') { alert('Please enter your NVE API key.'); return; }
        signalSender('showOverlay', 'Getting stations from NVE. Please wait...');
        const content = { projectName: currentProject, key: key, clientName: apiKey };
        const data = await jsonLoader('stations_nve', content); signalSender('hideOverlay');
        if (data.status === 'error') { alert(data.message); return; }
        obj.waterFlowCheckbox.checked = false; obj.waterLevelCheckbox.dispatchEvent(new Event('change'));
        obj.waterLevelCheckbox.checked = false; obj.waterLevelCheckbox.dispatchEvent(new Event('change'));
        obj.rainfallCheckbox.checked = false; obj.rainfallCheckbox.dispatchEvent(new Event('change'));
        metLayer = clearMap(metLayer, mapObj); deleteTable(obj.metTable);
        nveLayer = clearMap(nveLayer, mapObj); deleteTable(obj.nveTable);
        const contents = {
            getSelectedLayer: () => selectedNVELayer, 
            setSelectedLayer: (layer) => { selectedNVELayer = layer; },
            getSelected: () => nveSelected, table: obj.nveTable, 
            label: obj.nveSelectedLabel, key: ''
        }
        nveLayer = await pointPloter(key, data.content.point, mapObj, contents);
        obj.nveLabel.textContent = `Number of Stations: ${data.content.length}`;
    });
    obj.stationCheckboxNVE.addEventListener('change', async (e) => {
        nveSelected = e.target.checked;
        if (nveSelected) {
            if (!nveLayer) {
                alert('Please click "Get Stations" first to load MET stations first.');
                e.target.checked = false; nveSelected = false; return;
            }
        }
    });
    obj.nveDeleteBtn.addEventListener('click', async () => {
        const selectedRows = obj.nveTable.querySelectorAll('tbody tr.selected');
        if (selectedRows.length === 0) { alert('No station selected. Please select a station from the table first.'); return; }
        selectedRows.forEach(row => row.remove());
        const n = obj.nveTable.querySelectorAll('tbody tr.selected').length;
        obj.nveSelectedLabel.textContent = `Selected Stations: ${n}`;
    });
    obj.nveVariablesBtn.addEventListener('click', async () => {
        const nveGrid = document.getElementById("nve-variables-grid");
        if (!nveGrid) { alert('Cannot find Variable container. Please recheck frontend components.'); return; }
        const apiKey = obj.clientIdNVE.value; nveGrid.innerHTML = '';
        if (!apiKey || apiKey === '') { alert('Please enter your NVE API key.'); return; }
        const tableData = getDataFromTable(obj.nveTable, true);
        if (tableData.rows.length === 0) {
            alert('No station selected. Please select a station from the map first.'); return;
        }
        const ids = tableData.rows.map(row => row[0]);
        signalSender('showOverlay', 'Getting Variables from NVE Database. Please wait...');
        const response = await jsonLoader('nve_parameters', { apiKey: apiKey, ids: ids }); 
        signalSender('hideOverlay');
        if (response.status === 'error') { alert(response.message); return; }
        if (response.content.length === 0) {
            nveGrid.innerHTML = `<strong 
                style="display: flex; text-align: center; color: red; font-size: 20px; padding: 20px;">
                No Variables found</strong>
            `
        }
        response.content.forEach(item => {
            const label = document.createElement('label');
            label.className = 'checkbox-right';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox'; checkbox.value = item[0];
            label.appendChild(checkbox);
            const content = `${item[2]} (${item[3]})`;
            label.appendChild(document.createTextNode(content));
            nveGrid.appendChild(label);
        });
    });
    obj.nveDownloadBtn.addEventListener('click', async () => {
        const apiKey = obj.clientIdNVE.value, key = 'nve', logFile = 'NVE_log.txt';
        if (!apiKey || apiKey === '') { alert('Please enter your NVE API key.'); return; }
        const tableData = getDataFromTable(obj.nveTable, true);
        if (tableData.rows.length === 0) {
            alert('No station selected. Please select a station from the map first.'); return;
        }
        const ids = tableData.rows.map(row => [row[0], row[4], row[5]]);
        const startTime = obj.nveStart.value, endTime = obj.nveEnd.value;
        if (startTime === '' || endTime === '') {
            alert('Please select start and end time to download.'); return;
        }
        // Get variables selected
        const selectedValues = [...document.querySelectorAll(
            '#nve-variables-grid input[type="checkbox"]:checked'
        )].map(checkbox => ({
            value: checkbox.value, des: checkbox.parentElement.textContent.trim()
        }));
        if (selectedValues.length === 0) {
            alert('Please select at least one variable to download.'); return;
        }
        try {
            const statusRes = await jsonLoader('check_download_status', { projectName: currentProject, key: key });
            if (statusRes.status === "running") { alert("Data download is running."); return; }
            obj.nveLogText.value = ''; obj.nveLogContainer.style.display = 'flex';
            obj.nveSaveBtn.style.display = 'none'; obj.nveDownloadTable.style.display = 'none';
            const contents = {
                projectName: currentProject, api_key: apiKey, ids: ids, interval: obj.nveInterval.value,
                startTime: startTime, endTime: endTime, timeZone: getLastTimeZone(), key: key, logFile: logFile,
                variables: selectedValues.map(v => v.value), columns: selectedValues.map(v => v.des)
            };
            const data = await jsonLoader('download_nve', contents);
            if (data.status === 'error') { alert(data.message); return; }
            updateLog(currentProject, obj.nveLogText, 2, key, async () => {
                alert('Downloading data completed.');
                const content_csv = { projectName: currentProject, fileName: `${key}.csv` };
                signalSender('showOverlay', 'Uploading weather data to table. Please wait...');
                const csv = await jsonLoader('upload_weather_csv', content_csv);
                signalSender('hideOverlay');
                if (csv.status === 'error') { alert(csv.message); return; }
                addDataToTable(obj.nveDownloadTable, csv.columns, csv.content);
                obj.nveSaveBtn.style.display = 'block';
                obj.nveLogContainer.style.display = 'none';
                obj.nveDownloadTable.style.display = 'table';
            }, logFile);
        } catch (error) {
            alert(error.message || error);
            obj.nveSaveBtn.style.display = 'none'; return;
        }
    });
    obj.nveSaveBtn.addEventListener('click', async () => {
        const tableData = getDataFromTable(obj.nveTable, true);
        if (tableData.rows.length === 0) { 
            alert('No station selected. Please select a station from the map first.'); return; 
        }
        const ids = tableData.rows.map(row => [row[0], row[4], row[5]]);
        const data = getDataFromTable(obj.nveDownloadTable, true);
        if (data.rows.length === 0) { alert('No data to save.'); return; }
        try {
            const response = await fetch('/save_weather_csv', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ids: ids, data: data})
            });
            if (response.ok) { 
                const blob = await response.blob();
                await saveFileSmart(blob, `nve_data.zip`, true);
                alert('Save ZIP file completed.')
            }
        } catch (error) {
            alert(`Failed to save NVE data:\n${error.message}`);
        }
    });
    // Work on Regnbyge option
    obj.waterFlowCheckbox.addEventListener('change', async (e) => {
        const filter = ['flow']; 
        metLayer = clearMap(metLayer, mapObj); nveLayer = clearMap(nveLayer, mapObj);
        if (e.target.checked === true) {
            waterFlowLayer = await loadStations(
                currentProject, 'regnbyge', e.target, obj.stationTable, 'water flow',
                'flow', waterFlowLayer, filter, mapObj
            );
        } else {
            waterFlowLayer = clearMap(waterFlowLayer, mapObj);
            removeStationsByType(obj.stationTable, filter);
        }
    });
    obj.waterLevelCheckbox.addEventListener('change', async (e) => {
        const filter = ['overflow'];
        metLayer = clearMap(metLayer, mapObj); nveLayer = clearMap(nveLayer, mapObj);
        if (e.target.checked === true) {
            waterLevelLayer = await loadStations(
                currentProject, 'regnbyge', e.target, obj.stationTable, 'water level',
                'level', waterLevelLayer, filter, mapObj
            );
        } else {
            waterLevelLayer = clearMap(waterLevelLayer, mapObj);
            removeStationsByType(obj.stationTable, filter);
        }
    });
    obj.rainfallCheckbox.addEventListener('change', async (e) => {
        const filter = ['permanent', 'permanentTemp']; 
        metLayer = clearMap(metLayer, mapObj); nveLayer = clearMap(nveLayer, mapObj);
        if (e.target.checked === true) {
            preLayer = await loadStations(
                currentProject, 'regnbyge', e.target, obj.stationTable, 'rainfall',
                'rain', preLayer, filter, mapObj
            );
        } else {
            preLayer = clearMap(preLayer, mapObj);
            removeStationsByType(obj.stationTable, filter);
        }
    });
    obj.typeSelector.addEventListener('change', () => {
        selectStations(obj.typeSelector.value, obj.stationSelectedTable, obj.stationSelectedLabel);
    });
    obj.downloadBtn.addEventListener('click', async () => {
        if (nameID === '' || secret === '' || userName === '' || password === '') {
            alert('Please check registration information and try again.'); return;
        }
        const tableData = getDataFromTable(obj.stationSelectedTable, true);
        const n = obj.stationSelectedTable.querySelectorAll('tr.selected').length;
        if (tableData.rows.length === 0 || n === 0) {
            alert('No station selected. Please select a station from the map first.'); return;
        }
        const startTime = obj.downloadStart.value, endTime = obj.downloadEnd.value,
            downloadType = obj.typeSelector.value, interval = obj.downloadInterval.value;
        if (startTime === '' || endTime === '') {
            alert('Please select start and end time to download.'); return;
        }
        try {
            const dirHandle = await window.showDirectoryPicker();
            obj.downloadListContainer.style.display = 'flex'; obj.downloadListArea.value = '';
            for (const file of tableData.rows) {
                const name = `${file[0]}_${startTime.replace(' ', '_')}-${endTime.replace(' ', '_')}`;
                obj.downloadListArea.value += `Downloading: ${name} ...\n`;
                const contents = {
                    mode: downloadType, downloadInterval: interval, timeZone: getLastTimeZone(),
                    startTime: startTime, endTime: endTime, id: [Number(file[1].trim())],
                    clientName: nameID, clientSecret: secret,
                    clientUserName: userName, clientPassword: password
                };
                const response = await jsonLoader('download_station', contents);
                if (response.status === 'error') {
                    alert(response.message);
                    obj.downloadListArea.value += `Error downloading: [${response.message}] \n`;
                    obj.downloadListArea.value += `Downloading [${name}] is skipped.\n`;
                    continue;
                }
                let nameSaved = name.replace('Å', 'Aa').replace('å', 'aa').replace('Æ', 'Ae').replace('æ', 'ae');
                nameSaved = nameSaved.replace('Ø', 'oo').replace(/[^a-zA-Z0-9_\-]/g, '_');
                nameSaved = `${nameSaved}.csv`;
                if (dirHandle !== null) {
                    const fileHandle = await dirHandle.getFileHandle(nameSaved, { create: true });
                    const writable = await fileHandle.createWritable();
                    await writable.write("\uFEFF" + response.content);
                    await writable.close();
                } else {
                    const blob = new Blob([response.content], { type: 'text/csv;charset=utf-8;' });
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    link.setAttribute('href', url);
                    link.setAttribute('download', nameSaved);
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }
                obj.downloadListArea.value += `Saved file: ${nameSaved}.\n`;
            }
            obj.downloadListArea.value += '\nDownload complete.'; alert('Download complete.');
        } catch (error) {
            alert(error.message || error); obj.downloadListContainer.style.display = 'none';
            obj.downloadListArea.value = ''; return;
        }
    });
    obj.resertStationBtn.addEventListener('click', async () => {
        if (obj.waterFlowCheckbox.checked === false &&
            obj.waterLevelCheckbox.checked === false &&
            obj.rainfallCheckbox.checked === false) {
            alert('No station type selected. Please select at least one type first.'); return;
        }
        const ok = confirm('Do you want to delete the selected stations?');
        if (!ok) return;
        signalSender('showOverlay', 'Deleting Station(s). Please wait...');
        const contents = {
            projectName: currentProject, flow: obj.waterFlowCheckbox.checked, key: key, keyType: 'regnbyge',
            level: obj.waterLevelCheckbox.checked, rain: obj.rainfallCheckbox.checked
        };
        const response = await jsonLoader('reset_station', contents);
        obj.waterFlowCheckbox.checked = false; waterFlowLayer = clearMap(waterFlowLayer, mapObj);
        obj.waterLevelCheckbox.checked = false; waterLevelLayer = clearMap(waterLevelLayer, mapObj);
        obj.rainfallCheckbox.checked = false; preLayer = clearMap(preLayer, mapObj);
        alert(response.message); signalSender('hideOverlay');
    });
    // Regnbyge upload GIS data
    obj.dataGISBtn.addEventListener('click', () => { obj.dataGISFile.click(); });
    obj.dataGISFile.addEventListener('change', async (event) => {
        const file = event.target.files[0]; if (!file) return;
        const filename = file?.name || "";
        signalSender('showOverlay', `Uploading GIS data '${filename}'. Please wait...`);
        const formData = new FormData(); formData.append('file', file);
        const response = await fetch('/data_upload_gis', { method: 'POST', body: formData });
        const data = await response.json(); signalSender('hideOverlay');
        if (data.status === "error") { return; }
        event.target.value = '';
        await addGISFileToList(obj.dataGISContainer, filename, data.content, mapObj);
    });
    // Work on ERA5
    obj.era5LocationBtn.addEventListener('click', () => { era5Checked = true; });
    obj.era5WindSpeed.addEventListener('change', (e) => {
        if (e.target.checked === true) {
            obj.era5WWindU.checked = true; obj.era5WWindV.checked = true;
        }
    });
    obj.era5WWindU.addEventListener('change', (e) => {
        if (e.target.checked === false) { obj.era5WindSpeed.checked = false; }
    });
    obj.era5WWindV.addEventListener('change', (e) => {
        if (e.target.checked === false) { obj.era5WindSpeed.checked = false; }
    });
    obj.era5DownloadBtn.addEventListener('click', async () => {
        const apiKey = obj.apikeyEra5.value, logFile = 'ERA5_log.txt';
        if (!apiKey || apiKey === '') { alert('Please enter your ERA5 API key.'); return; }
        const lat = parseFloat(obj.era5Lat.value), lon = parseFloat(obj.era5Lon.value);
        if (Number.isNaN(lat) || Number.isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 360) {
            alert('Please select a valid location.'); return;
        }
        const startTime = obj.era5Start.value, endTime = obj.era5End.value;
        if (startTime === '' || endTime === '') {
            alert('Please select start and end time to download.'); return;
        }
        if (new Date(startTime) >= new Date(endTime)) { alert('Start time must be before end time.'); return; }
        const selectedValues = [...document.querySelectorAll('#era5-variables-grid input[type="checkbox"]:checked'
        )].map(checkbox => ({
            value: checkbox.value, label: checkbox.getAttribute('data-label'),
            des: checkbox.parentElement.textContent.trim()
        }));
        if (selectedValues.length === 0) {
            alert('Please select at least one variable to download.'); return;
        }
        try {
            const statusRes = await jsonLoader('check_download_status', { projectName: currentProject, key: ERA5_KEY });
            if (statusRes.status === "running") { 
                alert(statusRes.cancelling ? 'Previous download is being cancelled. Please wait a moment.'
                    : 'Weather download is running.');
            }
            obj.era5LogText.value = ''; obj.era5LogContainer.style.display = 'flex';
            obj.era5SaveBtn.style.display = 'none'; obj.era5Table.style.display = 'none'; 
            obj.era5CancelBtn.style.display = 'block';
            const contents = {
                projectName: currentProject, lat: lat, lon: lon, api_key: apiKey,
                startTime: startTime, endTime: endTime, key: ERA5_KEY, logFile: logFile,
                timeZone: getLastTimeZone(), variables: selectedValues.map(v => v.value)
            };
            const data = await jsonLoader('download_era5', contents);
            if (data.status === 'error') { alert(data.message); return; }
            setEra5Running(true);
            updateLog(currentProject, obj.era5LogText, 2, ERA5_KEY, async () => {
                const res = await jsonLoader('check_download_status', { projectName: currentProject, key: ERA5_KEY });
                setEra5Running(false);
                if (res.cancelled) {
                    if (!era5ClearingQueue) alert('Download cancelled. No data was saved.'); return;
                }
                if (res.status !== 'finished') { alert(`Weather download failed: ${res.message}`); return; }
                signalSender('showOverlay', 'Uploading weather data to table. Please wait...');
                let csv;
                try { csv = await jsonLoader('upload_era5_csv', { projectName: currentProject, fileName: `${ERA5_KEY}.csv` }); }
                finally { signalSender('hideOverlay'); }
                if (csv.status === 'error') { alert(csv.message); return; }
                addDataToTable(obj.era5Table, csv.columns, csv.content);
                obj.era5SaveBtn.style.display = 'block'; obj.era5LogContainer.style.display = 'none';
                obj.era5Table.style.display = 'table'; obj.era5CancelBtn.style.display = 'none';
            }, logFile);
        } catch (error) {
            signalSender('hideOverlay'); setEra5Running(false);
            alert(error.message || error); obj.era5SaveBtn.style.display = 'none'; return;
        }
    });
    obj.era5CancelBtn.addEventListener('click', async () => {
        if (!confirm('Cancel the ERA5 download?')) return;
        obj.era5CancelBtn.disabled = true; obj.era5CancelBtn.textContent = 'Cancelling...';
        try {
            const res = await jsonLoader('cancel_download_era5', { projectName: currentProject, key: ERA5_KEY });
            if (res.status === 'idle') setEra5Running(false); 
            obj.era5CancelBtn.style.display = 'none'; alert('ERA5 download cancelled.');
        } catch (error) {
            obj.era5CancelBtn.disabled = false; obj.era5CancelBtn.textContent = 'Cancel'; alert(error.message || error);
        }
    });
    obj.era5ClearQueueBtn.addEventListener('click', async () => {
        const apiKey = obj.apikeyEra5.value.trim();
        if (!apiKey) { alert('Please enter your ERA5 API key.'); return; }
        const status = await jsonLoader('check_download_status', { projectName: currentProject, key: ERA5_KEY });
        const running = status.status === 'running';
        const question = running
            ? 'A download is running.\nIt will be cancelled and all queued/running ERA5 requests of this CDS account will be removed.\n\n\nContinue?'
            : 'Remove all queued/running ERA5 requests of this CDS account?';
        if (!confirm(question)) return;
        era5ClearingQueue = running; obj.era5ClearQueueBtn.disabled = true;
        obj.era5ClearQueueBtn.textContent = running ? 'Cancelling & clearing...' : 'Clearing...';
        if (running && obj.era5CancelBtn) { obj.era5CancelBtn.disabled = true; obj.era5CancelBtn.textContent = 'Cancelling...'; }
        signalSender('showOverlay', running ? 'Cancelling download and clearing CDS queue...' : 'Clearing CDS queue...');
        try {
            const res = await jsonLoader('cds_clear_queue', { projectName: currentProject, key: ERA5_KEY, api_key: apiKey });
            if ((res.cancelled || []).includes(ERA5_KEY)) setEra5Running(false); alert(res.message);
        } catch (error) { alert(error.message || error);
        } finally {
            signalSender('hideOverlay'); obj.era5ClearQueueBtn.disabled = false;
            obj.era5ClearQueueBtn.textContent = 'Clear CDS queue';
            setTimeout(() => { era5ClearingQueue = false; }, 5000);
        }
    });
    obj.era5SaveBtn.addEventListener('click', async () => {
        const data = getDataFromTable(obj.era5Table, true);
        if (data.rows.length === 0) { alert('No data to save.'); return; }
        const response = await jsonLoader('save_era5', { data: data });
        if (response.status === 'ok') {
            await saveFileSmart(response.content, `era5_${Date.now()}.csv`);
        }
        alert(response.message);
    });
}

function setEra5Running(running) {
    obj.era5DownloadBtn.disabled = running;
    if (obj.era5CancelBtn) {
        obj.era5CancelBtn.style.display = running ? 'inline-block' : 'none';
        obj.era5CancelBtn.disabled = false; obj.era5CancelBtn.textContent = 'Cancel';
    }
}

async function addGISFileToList(container, filename, geojson, map) {
    const id = `gis_${window._gisFileIdCounter++}`;
    window._uploadedGISFiles[id] = {
        name: filename, geojson: geojson,
        layer: null, checked: false,
    };
    const emptyMsg = container.querySelector('.gis-empty');
    if (emptyMsg) emptyMsg.remove();
    const item = document.createElement('div');
    item.className = 'gis-file-item';
    item.dataset.gisId = id;
    item.innerHTML = `
        <input type="checkbox" id="chk_${id}" data-gis-id="${id}">
        <label class="gis-file-name" for="chk_${id}" title="${filename}">
            ${filename}
        </label>
        <span class="gis-file-remove" data-gis-id="${id}" title="Remove">×</span>
    `;
    container.appendChild(item); container.style.display = 'block';
    item.querySelector('input[type="checkbox"]').addEventListener('change', (e) => {
        toggleGISLayer(id, e.target.checked, map);
    });
    item.querySelector('.gis-file-remove').addEventListener('click', () => {
        removeGISFile(container, id, map);
    });
    return id;
}

function createGISLayer(geojson, name) {
    if (!window.L) return null;
    const hue1 = Math.floor(Math.random() * 360), hue2 = Math.floor(Math.random() * 360);
    const fillColor = `hsl(${hue1}, 70%, 50%)`, color = `hsl(${hue2}, 70%, 50%)`;
    return L.geoJSON(geojson, {
        style: {
            color: color, weight: 2, fillColor: fillColor,
            opacity: 1, fillOpacity: 0.2,
        },
        onEachFeature: (feature, layer) => {
            const props = feature.properties || {};
            const popupContent = Object.entries(props)
                .slice(0, 10)
                .map(([k, v]) => `<b>${k}</b>: ${v}`)
                .join('<br>');
            if (popupContent) {
                layer.bindPopup(`<b>${name}</b><hr>${popupContent}`);
            }
        },
    });
}

function removeGISFile(container, id, map) {
    const fileInfo = window._uploadedGISFiles[id];
    if (!fileInfo) return;
    if (fileInfo.layer && map) {
        map.removeLayer(fileInfo.layer);
    }
    const item = container.querySelector(`.gis-file-item[data-gis-id="${id}"]`);
    if (item) item.remove();
    delete window._uploadedGISFiles[id];
    if (Object.keys(window._uploadedGISFiles).length === 0) {
        container.style.display = 'none';
    }
}

function toggleGISLayer(id, checked, map) {
    const fileInfo = window._uploadedGISFiles[id];
    if (!fileInfo) return; fileInfo.checked = checked;
    if (checked) {
        if (!fileInfo.layer) {
            fileInfo.layer = createGISLayer(fileInfo.geojson, fileInfo.name);
        }
        if (fileInfo.layer && map) { fileInfo.layer.addTo(map); }
    } else {
        if (fileInfo.layer && map) { map.removeLayer(fileInfo.layer); }
    }
}

function mapOptions(map) {
    map.on('mousemove', function (e) {
        if (!plotChecked && (waterFlowLayer || waterLevelLayer || overFlowLayer || tempLayer || preLayer || weirLayer || evaLayer)) {
            const html = `- Left-click to select station and add to the download list.<br>- Right-click to remove the last station.`;
            hoverTooltip.setLatLng(e.latlng).setContent(html);
            map.openTooltip(hoverTooltip);
        } else if (era5Checked) {
            map.getContainer().style.cursor = "crosshair";
            const html = `Select average location.`;
            hoverTooltip.setLatLng(e.latlng).setContent(html);
            map.openTooltip(hoverTooltip);
        } else { if (hoverTooltip) map.closeTooltip(hoverTooltip); }
    });
    map.on('click', async function (e) {
        if (era5Checked) {
            const lat = e.latlng.lat.toFixed(1), lon = e.latlng.lng.toFixed(1);
            obj.era5Lat.value = lat; obj.era5Lon.value = lon;
            map.getContainer().style.cursor = ""; era5Checked = false;
        }
        if (selectedMetLayer) {
            selectedMetLayer.closeTooltip(); selectedMetLayer.unbindTooltip();
            const layer = selectedMetLayer;
            if (layer._hoverTooltip) {
                layer.bindTooltip(layer._hoverTooltip, {
                    sticky: true, permanent: false,
                    direction: 'bottom', opacity: 1, offset: [0, 10]
                });
            }
            selectedMetLayer = null;
        }
        if (selectedNVELayer) {
            selectedNVELayer.closeTooltip(); selectedNVELayer.unbindTooltip();
            const layer = selectedNVELayer;
            if (layer._hoverTooltip) {
                layer.bindTooltip(layer._hoverTooltip, {
                    sticky: true, permanent: false,
                    direction: 'bottom', opacity: 1, offset: [0, 10]
                });
            }
            selectedNVELayer = null;
        }
    });
    map.on('contextmenu', async function (e) {
        e.originalEvent.preventDefault();
        if (!plotChecked) {
            const tableData = getDataFromTable(obj.stationSelectedTable, true);
            if (!tableData || !tableData.rows || tableData.rows.length === 0) return;
            // Remove the last station
            const newRows = tableData.rows.slice(0, -1);
            fillTable(newRows, obj.stationSelectedTable, true);
        }
    });
}

function hightlightRows(table, label = null) {
    const tbody = table.querySelector('tbody');
    const trList = Array.from(tbody.querySelectorAll('tr'));
    let lastSelectedIndex = null;
    tbody.addEventListener('click', (event) => {
        const tr = event.target.closest('tr');
        if (!tr) return;
        const index = trList.indexOf(tr);
        if (event.shiftKey && lastSelectedIndex !== null) { // Shift click
            const [start, end] = [lastSelectedIndex, index].sort((a, b) => a - b);
            for (let i = start; i <= end; i++) {
                trList[i].classList.add('selected');
            }
        } else if (event.ctrlKey || event.metaKey) { // Ctrl/Cmd click
            tr.classList.toggle('selected');
        } else { // Single click
            if (tr.classList.contains('selected')) { tr.classList.remove('selected'); }
            else { tr.classList.add('selected'); }
        }
        lastSelectedIndex = index;
        if (label) {
            const n = table.querySelectorAll('tr.selected').length;
            const total = table.querySelectorAll('tbody tr').length;
            label.innerHTML = `(Selected Station(s): ${n}/${total})`;
        }
    });
}

function updateLayerTooltips(layerGroup) {
    if (!layerGroup) return;
    layerGroup.eachLayer(layer => {
        if (!layer.feature) return;
        const feature = layer.feature; let note = '';
        if (plotChecked) {
            note = `<hr style="border-top: 1px solid #0414f5; margin: 5px 0;">
                <span style="display:block;font-weight:bold;text-align:center;">
                Click to plot raw data</span>`;
        }
        const content = `
            <div style="font-size:14px;border-radius:10px;">
                <span style="display:block;text-align:center;font-weight:bold;">
                    ${feature.properties.name || 'No name'}
                </span>
                <hr style="border-top:1px solid #0414f5;margin:5px 0;">
                ${Object.entries(feature.properties).filter(([key]) => key !== 'name' && key !== 'mode')
                .map(([key, value]) => `• ${key}: ${value}<br>`).join('')}
                ${note}
            </div>`;
        layer.setTooltipContent(content);
    });
}

async function loadStations(projectName, keyType, target, table, label, key, layer, filter, map) {
    const data = getDataFromTable(table, true);
    const filtered = data.rows.filter(row => !filter.includes(row[1])); layer = clearMap(layer, mapObj);
    if (target.checked) {
        signalSender('showOverlay', `Getting ${label} stations from Regnbyge.no.\nThis takes a while (especially the first time).\nPlease wait ...`);
        const content = {
            projectName: projectName, key: key, clientName: nameID, keyType: keyType,
            clientSecret: secret, clientUserName: userName, clientPassword: password
        };
        const response = await jsonLoader('init_station', content); signalSender('hideOverlay');
        if (response.status === "error") { alert(response.message); target.checked = false; return; }
        const stationNames = response.content.name, stationLocations = response.content.point;
        const contents = {
            startObj: obj.plotStart, endObj: obj.plotEnd, intervalObj: obj.plotInterval,
            params: {nameID, secret, userName, password}, containerObj: obj.plotDataContainer,
            tableObj: obj.stationSelectedTable, selectorObj: obj.typeSelector, 
            labelObj: obj.stationSelectedLabel, getPlotChecked: () => plotChecked
        }
        layer = await pointPloter('regnbyge', stationLocations, map, contents);
        stationNames.forEach(item => filtered.push(item));
    }
    deleteTable(table); fillTable(filtered, table, true);
    if (filtered.length > 0) {
        obj.plotContainer.style.display = 'flex';
    } else { obj.plotContainer.style.display = 'none'; }
    return layer;
}

function removeStationsByType(table, filter) {
    const data = getDataFromTable(table, true);
    const remaining = data.rows.filter(row => !filter.includes(row[1]));
    deleteTable(table);
    if (remaining.length > 0) { fillTable(remaining, table, true); }
}

function clearMap(layer, map) {
    if (layer) { map.removeLayer(layer); }
    return null;
}