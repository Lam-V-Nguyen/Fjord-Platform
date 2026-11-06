import { hydMapId, getLastTimeZone } from "./constant.js";
import { setupTabs } from "./tabManager.js";
import { jsonLoader, nameChecker, fillTable, updateTable, iframeConnector, closeWindow,
    getDataFromTable, csvUploader, fileUploader, deleteTable, copyPaste, moveWindow,
    addRowToTable, removeRowFromTable, pointUpdate, getProjectList, signalSender
} from "./commonFunctions.js";
import { timeStepCalculator, saveProject } from "./projectSaver.js";
import { projectRender } from "./projectManager.js";
import { plotTimeSeries } from "./chartManager.js";

const $ = (id) => document.getElementById(id);
const obj = {
    descriptionTab: $('desription-tab'), controlTab: $('control-tab'),
    projectList: $('project-list'), projectName: $('project-name'),
    projectCreator: $('create-btn'), projectCloner: $('duplicate-btn'),
    projectRemover: $('remove-btn'), projectSaver: $('save-btn'),
    plotContainer: $('plot-container'), plotHeader: $('plot-header'),
    closePlotBtn: $('close-plot-btn'), plotTitle: $('plot-title'),
    latitude: $('latitude'), getLocation: $('location'),
    nLayers: $('n-layer'), gridPathText: $('grid-text'), 
    gridPathFile: $('grid-file'), startDate: $('start-date'), 
    stopDate: $('stop-date'), userTimestepDate: $('user-date'), 
    userTimestepTime: $('user-time'), nodalTimestepDate: $('nodal-date'), 
    nodalTimestepTime: $('nodal-time'), obsPointName: $('obs-point'),
    obsPointLatitude: $('obs-latitude'), obsPointLongitude: $('obs-longitude'),
    obsPointPicker: $('obs-picker'), obsPointAddList: $('obs-add-list'),
    obsPointAddRow: $('obs-add-row'), obsPointRemove: $('obs-remove'),
    obsPointTable: $('obs-table'), obsPointUploadFile: $('obs-file'),
    obsPointUploadText: $('obs-text'), obsPointUpdate: $('obs-update'), 
    crossSectionName: $('cross-section'), crossSectionPicker: $('cross-section-picker'), 
    crossSectionRemove: $('cross-section-remove'), crossSectionTable: $('cross-section-table'),    
    boundaryName: $('boundary-name'), boundaryPicker: $('boundary-picker'), 
    boundaryRemove: $('boundary-remove'), boundaryTable: $('boundary-table'),
    boundarySelector: $('boundary-edit'), boundaryTypeSelector: $('boundary-type'),
    boundaryUploadFile: $('boundary-picker-file'), boundaryUploadText: $('boundary-picker-text'),   
    boundaryCSV: $('boundary-upload-csv'), boundaryAddRow: $('boundary-add-row'),    
    boundaryEditTable: $('boundary-edit-table'), boundaryEditUpdate: $('boundary-update'),    
    boundaryEditRemove: $('boundary-edit-remove'), boundarySelectorView: $('boundary-type-view'),
    boundaryViewContainer: $('textarea-container-hyd'), boundaryText: $('data-view-hyd'),    
    sourceName: $('source-name'), sourceOptionNew: $('source-sink-new'),
    sourceOptionExist: $('source-sink-exist'), sourceOptionPicker: $('source-picker'),   
    sourceLatitude: $('source-latitude'), sourceLongitude: $('source-longitude'),
    sourceTable: $('source-table'), sourceUploadFile: $('source-csv-file'),
    sourceUploadText: $('source-csv-text'), sourceAddBtn: $('add-source-btn'),
    sourcePlotBtn: $('plot-source-btn'), sourceSaveBtn: $('save-source-btn'),
    sourceSelectorRemove: $('option-source-remove'), sourceRemoveBtn: $('source-remove'),
    sourceRemoveTable: $('source-remove-table'), sourceDeleteTableBtn: $('delete-source-btn'),
    meteoAddBtn: $('add-meteo-btn'), meteoPlotBtn: $('plot-meteo-btn'),
    meteoDeleteBtn: $('delete-meteo-btn'), meteoSaveBtn: $('save-meteo-btn'),
    meteoTable: $('edit-meteo-table'), meteoUploadFile: $('meteo-picker-file'),
    meteoUploadText: $('meteo-picker-text'), weatherPanel: $('weather-upload-panel'),
    weatherSelector: $('option-weather'), weatherUpload: $('weather-update'),
    weatherAddRow: $('weather-add-row'), weatherRemove: $('weather-remove'),
    weatherCSVUploadFile: $('weather-update-file'), weatherCSVUploadText: $('weather-update-text'),
    weatherTable: $('weather-edit-table'), hisIntervalDate: $('his-output-interval-date'),
    hisIntervalTime: $('his-output-interval-time'), mapIntervalDate: $('map-output-interval-date'),
    mapIntervalTime: $('map-output-interval-time'), wqIntervalDate: $('water-quality-output-interval-date'),
    wqIntervalTime: $('water-quality-output-interval-time'), rstIntervalDate: $('restart-interval-date'),
    rstIntervalTime: $('restart-interval-time'), statisticDate: $('statistic-output-interval-date'),
    statisticTime: $('statistic-output-interval-time'), timingDate: $('timing-statistic-output-interval-date'),
    timingTime: $('timing-statistic-output-interval-time'), salinity: $('use-salinity'),
    temperature: $('option-temperature'), initWaterLevel: $('initial-water-level'),
    initTemperature: $('initial-temperature'), initSalinity: $('initial-salinity'),
    outputHis: $('write-his-file'), hisStart: $('his-output-start'), hisStop: $('his-output-end'),
    outputMap: $('write-map-file'), mapStart: $('map-output-start'), mapStop: $('map-output-end'),
    outputWQ: $('write-water-quality-file'), wqStart: $('water-quality-output-start'), wqStop: $('water-quality-output-end'),
    outputRestart: $('write-restart-file'), rstStart: $('restart-output-start'), rstStop: $('restart-output-end'),
}

setupTabs(document); projectOptions(); hydManager();

async function projectOptions(){
    // Create new HYD project
    obj.projectCreator.addEventListener('click', async () => {
        const name = obj.projectName.value.trim(); let project = '';
        if (!name || name.trim() === '') { alert('Please define scenario name.'); return; }
        if (nameChecker(name)) { alert('Scenario name contains invalid characters.'); return; }
        if (name.includes('/')) { project = name.split('/').pop(); } else { project = name; }
        const data = await jsonLoader('setup_new_project', { projectName: project });
        obj.controlTab.style.display = "block"; obj.descriptionTab.style.display = "none";
        alert(data.message); const respond = await getProjectList();
        await projectRender(obj.projectName, obj.projectList, respond);
        await loadScenario(name); 
    });
    // Copy HYD project
    obj.projectCloner.addEventListener('click', async () => {
        const name = obj.projectName.value.trim();
        if (!name || name === '') { alert('Please select scenario first.'); return; }
        // Ask for a new name
        const newName = prompt('Please enter a name for the new scenario.\nCloning a scenario will take some time. Please be patient.');
        if (!newName || newName === '') { alert('Please define clone scenario name.'); return; }
        if (nameChecker(newName)) { alert('Name of clone scenario is invalid.'); return;}
        signalSender('showOverlay', `Cloning scenario '${name}' to '${newName}'. Please wait...`);
        const data = await jsonLoader('copy_project', {oldName: name, newName: newName});
        const respond = await getProjectList(); obj.projectName.value = newName;
        await projectRender(obj.projectName, obj.projectList, respond);
        signalSender('hideOverlay'); alert(data.message);
    });
    // Delete HYD project
    obj.projectRemover.addEventListener('click', async () => {
        const name = obj.projectName.value.trim();
        if (!name || name === '') { alert('Please define scenario.'); return; }
        // Ask for confirmation
        if (name === 'demo') {
            if (!confirm(
                `1. Deleting the default scenario 'demo' is NOT recommended.\n` + 
                `2. This might cause unknown problems while visualizing outputs.\n\n` +
                `Are you sure you want to delete scenario '${name}'?`
            )) { return; }
        }
        signalSender('showOverlay', `Deleting scenario '${name}'. Please wait...`);
        const data = await jsonLoader('delete_project', {projectName: name});
        window.location.reload(); // refresh page
        obj.projectName.value = ''; const respond = await getProjectList();
        await projectRender(obj.projectName, obj.projectList, respond);
        signalSender('hideOverlay'); alert(data.message);
    });
}

function sourceChange(target, table, lat, lon, sourceName, sourceText){
    const check = target.checked;
    if (!check) return;
    lat.value = ''; lon.value = '';
    // Clear table and name
    const tbody = table.querySelector("tbody");
    tbody.innerHTML = ""; sourceName.value = ''; sourceText.value = '';
}

function assignOutput(target, start, end, startDate, stopDate){
    target.addEventListener('change', () => { 
        if (!target.checked) { start.value = ''; end.value = ''; return; }
        start.value = startDate.value !== '' ? startDate.value : '';
        end.value = stopDate.value !== '' ? stopDate.value : '';
    });
}

async function hydManager(){
    const respond = await getProjectList();
    obj.projectName.style.pointerEvents = "auto";
    await projectRender(obj.projectName, obj.projectList, respond);
    // Check whether map widget exists
    const layout = localStorage.getItem('grid-layout');
    const hasMap = layout ? JSON.parse(layout).some(item => item.id === hydMapId):false;
    const content = { id: hydMapId, title: 'Hydrodynamic Scenario Map' };
    if (!hasMap) signalSender('addMapWidget', content);
    // Show/Hide tabs
    obj.projectName.addEventListener('input', (e) => { 
        const value = e.target.value.trim();
        if (value === '') { 
            obj.controlTab.style.display = "none"; obj.descriptionTab.style.display = "block"; 
        }
    });
    // Moving window
    moveWindow(obj.plotHeader, obj.plotContainer);
    // Close plot
    closeWindow(obj.closePlotBtn, obj.plotContainer);
    // Update location
    iframeConnector(obj.getLocation, obj.latitude, 'pickLocation');
    iframeConnector(obj.obsPointPicker, 
        [obj.obsPointName, obj.obsPointLatitude, obj.obsPointLongitude], 'pickPoint', 
        () => getDataFromTable(obj.obsPointTable, true), ''
    );
    iframeConnector(obj.crossSectionPicker, 
        [obj.crossSectionName, obj.crossSectionTable], 'pickPath',
        () => getDataFromTable(obj.crossSectionTable, true), 'crossSection'
    );
    iframeConnector(obj.boundaryPicker,
        [obj.boundaryName, obj.boundaryTable, obj.boundarySelector], 'pickPath', 
        () => getDataFromTable(obj.boundaryTable, true), 'boundary'
    );
    iframeConnector(obj.sourceOptionPicker, 
        [obj.sourceName, obj.sourceLatitude, obj.sourceLongitude], 'pickSource'
    );
    // Upload file to server
    obj.gridPathText.addEventListener('click', () => { obj.gridPathFile.click(); });
    obj.gridPathFile.addEventListener('change', async (event) => {
        await fileUploader(
            obj.gridPathFile, obj.gridPathText, obj.projectName.value,
            obj.gridPathFile.files[0].name, 'Uploading the Unstructured Grid...', 'grid'
        ); event.target.value = '';
        // Plot grid on map
        const content = { 
            projectName: obj.projectName.value, key: 'hyd', gridName: obj.gridPathText.value, opacity: 1
        }
        iframeConnector(null, null, 'gridPlot', content);
    });
    // Event when user uploads CSV file
    obj.obsPointUploadText.addEventListener('click', () => { obj.obsPointUploadFile.click(); });
    obj.obsPointUploadFile.addEventListener('change', async (event) => { 
        await csvUploader(event, obj.obsPointUploadText, obj.obsPointTable, 3); event.target.value = '';
    });
    obj.sourceUploadText.addEventListener('click', () => { obj.sourceUploadFile.click(); });
    obj.sourceUploadFile.addEventListener('change', async (event) => { 
        if (getDataFromTable(obj.sourceTable).rows.length >0) deleteTable(obj.sourceTable);
        await csvUploader(
            event, obj.sourceUploadText, obj.sourceTable, 4, obj.startDate.value, obj.stopDate.value, 
            true, obj.sourceName, obj.sourceLatitude, obj.sourceLongitude
        ); event.target.value = ''; 
    });
    obj.meteoUploadText.addEventListener('click', () => { obj.meteoUploadFile.click(); });
    obj.meteoUploadFile.addEventListener('change', async (event) => {
        if (getDataFromTable(obj.meteoTable).rows.length >0) deleteTable(obj.meteoTable);
        await csvUploader(
            event, obj.meteoUploadText, obj.meteoTable, 4, obj.startDate.value, obj.stopDate.value
        ); event.target.value = '';
    });
    obj.weatherCSVUploadText.addEventListener('click', () => { obj.weatherCSVUploadFile.click(); });
    obj.weatherCSVUploadFile.addEventListener('change', async (event) => {
        if (getDataFromTable(obj.weatherTable).rows.length >0) deleteTable(obj.weatherTable);
        await csvUploader(
            event, obj.weatherCSVUploadText, obj.weatherTable, 3, obj.startDate.value, obj.stopDate.value
        ); event.target.value = '';
    });
    // Copy and paste to tables
    copyPaste(obj.boundaryEditTable, 2); copyPaste(obj.sourceTable, 4);
    copyPaste(obj.meteoTable, 4); copyPaste(obj.weatherTable, 3); 
    copyPaste(obj.obsPointTable, 3);
    // Add point to table
    obj.obsPointAddList.addEventListener('click', () => {
        const name = obj.obsPointName.value.trim();
        const lat = obj.obsPointLatitude.value.trim();
        const lon = obj.obsPointLongitude.value.trim();
        if (name === '' || lat === '' || lon === '' || 
            isNaN(lat) || isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180
        ) { alert('Please check name, latitude, and longitude of the observation point.'); return;}
        // Add to table
        fillTable([[name, lat, lon]], obj.obsPointTable, false);
        // Clear input
        obj.obsPointName.value = ''; obj.obsPointLatitude.value = ''; obj.obsPointLongitude.value = '';
        obj.obsPointUpdate.dispatchEvent(new Event('click'));
    });
    // Add a new row to the table
    obj.obsPointAddRow.addEventListener('click', () => 
        addRowToTable(obj.obsPointTable, ['Name', 'Latitude', 'Longitude'])
    );
    obj.boundaryAddRow.addEventListener('click', () => 
        addRowToTable(obj.boundaryEditTable, ['YYYY-MM-DD HH:MM:SS', 'Value'])
    );
    obj.sourceAddBtn.addEventListener('click', () => 
        addRowToTable(obj.sourceTable, ['YYYY-MM-DD HH:MM:SS', 'Discharge', 'Salinity', 'Temperature'])
    );
    obj.meteoAddBtn.addEventListener('click', () => 
        addRowToTable(obj.meteoTable, ['YYYY-MM-DD HH:MM:SS', 'Humidity', 'Air temperature', 'Cloud coverage'])
    );
    obj.weatherAddRow.addEventListener('click', () => 
        addRowToTable(obj.weatherTable, ['YYYY-MM-DD HH:MM:SS', 'Magnitude', 'Direction'])
    );
    // Remove point from table
    obj.obsPointRemove.addEventListener('click', () => {
        const name = obj.obsPointName.value.trim();
        removeRowFromTable(obj.obsPointTable, name); obj.obsPointName.value = '';
    });
    // Event when user change radio button for observation points
    pointUpdate(document.getElementById('observation-point-new'), 
        obj.obsPointTable, false, ["Name", "Latitude", "Longitude"]
    );
    pointUpdate(document.getElementById('observation-point-exist'), obj.obsPointTable, 
        true, [obj.obsPointName, obj.obsPointLatitude, obj.obsPointLongitude]
    );
    // Update observation point on map
    obj.obsPointUpdate.addEventListener('click', () => {
        const content = getDataFromTable(obj.obsPointTable, true);
        if (content.rows.length === 0) {alert('No observation points found.'); return;}
        signalSender('updateObsPoint', {content: content});
    });
    // Event when user delete table
    obj.crossSectionRemove.addEventListener('click', () => 
        deleteTable(obj.crossSectionTable, obj.crossSectionName, 'clearCrossSection')
    );
    obj.boundaryEditRemove.addEventListener('click', () => { 
        deleteTable(obj.boundaryEditTable); obj.boundaryAddRow.click(); 
    });
    obj.sourceDeleteTableBtn.addEventListener('click', () => { 
        deleteTable(obj.sourceTable); obj.sourceAddBtn.click(); 
    });
    obj.meteoDeleteBtn.addEventListener('click', () => { 
        deleteTable(obj.meteoTable); obj.meteoAddBtn.click(); 
    });
    obj.weatherRemove.addEventListener('click', () => { 
        deleteTable(obj.weatherTable); obj.weatherAddRow.click(); 
    });
    // Update boundary option
    obj.boundaryEditUpdate.addEventListener('click', async () => {
        const nameProject = obj.projectName.value.trim(), nameBoundary = obj.boundaryName.value.trim();
        const subBoundary = obj.boundarySelector.value, boundaryType = obj.boundaryTypeSelector.value;
        if (nameProject === '' || nameBoundary === '' || subBoundary === '' || boundaryType === '') 
        { alert('Please check: \n     1. Name of project/boundary/sub-boundary option is required.' + 
            '\n     2. Boundary type is required.' + '\n     3. Reference date is required.'); return;
        }
        const boundaryData = getDataFromTable(obj.boundaryTable, true);
        if (boundaryData.rows.length === 0) { 
            alert('No data in the table. Please check boundary condition.'); return; 
        }
        const subBoundaryData = getDataFromTable(obj.boundaryEditTable, true);
        if (subBoundaryData.rows.length === 0) { 
            alert('No data in the table. Please check sub-boundary condition.'); return; 
        }
        // Create boundary
        const content = {
            projectName: nameProject, boundaryName: nameBoundary, timeZone: getLastTimeZone(),
            boundaryData: boundaryData.rows, subBoundaryName: subBoundary, 
            boundaryType: boundaryType, subBoundaryData: subBoundaryData.rows
        }
        const data = await jsonLoader('update_boundary', content);
        alert(data.message); obj.boundarySelectorView.value = '';
        obj.boundaryViewContainer.style.display = 'none'; obj.boundaryText.value = '';
    });
    // Update parameters of boundary from file
    const handleBoundaryChange = async () => {
        const content = {
            projectName: obj.projectName.value.trim(), timeZone: getLastTimeZone(),
            boundaryName: obj.boundarySelector.value, boundaryType: obj.boundaryTypeSelector.value
        };
        const data = await jsonLoader('get_boundary_params', content);
        if (data.status === 'new') { obj.boundaryEditRemove.click(); return; }
        if (data.status === 'error') { alert(data.message); return; }
        obj.boundaryEditRemove.click(); fillTable(data.content, obj.boundaryEditTable);
    };
    obj.boundarySelector.addEventListener('change', handleBoundaryChange);
    obj.boundaryTypeSelector.addEventListener('change', handleBoundaryChange);
    // Upload boundary condition from CSV
    obj.boundaryCSV.addEventListener('click', () => { obj.boundaryUploadFile.click(); });
    obj.boundaryUploadFile.addEventListener('change', async (event) => { 
        deleteTable(obj.boundaryEditTable);
        await csvUploader(event, obj.boundaryUploadText, obj.boundaryEditTable, 2);
        obj.boundaryUploadFile.value = '';
    });
    // View boundary condition
    obj.boundarySelectorView.addEventListener('change', async () => {
        if (obj.boundarySelectorView.value === '') { 
            obj.boundaryViewContainer.style.display = 'none'; return; 
        }
        if (obj.projectName.value === '') {
            alert('Name of project is required.'); 
            obj.boundaryViewContainer.style.display = 'none'; return;
        }
        const value = obj.boundarySelectorView.value;
        obj.boundaryText.value = '';
        // Create boundary
        const data = await jsonLoader('view_boundary', {
            projectName: obj.projectName.value, boundaryType: value
        });
        if (data.status === "error") {
            obj.boundarySelectorView.value = ''; alert(data.message);
            obj.boundaryViewContainer.style.display = 'none'; return;
        };
        obj.boundaryText.value = data.content; obj.boundaryViewContainer.style.display = 'flex';
    });
    // Delete boundary
    obj.boundaryRemove.addEventListener('click', async () => {
        const content = getDataFromTable(obj.boundaryTable, true).rows;
        // Delete the last part and get unique name
        const nameBoundary = [...new Set(content.map(p => p[0].replace(/_\d+$/, '')))];
        const data = await jsonLoader('delete_boundary', {
            projectName: obj.projectName.value, boundaryName: nameBoundary
        });
        if (data.status === 'error') { alert(data.message); return; }
        alert(data.message); deleteTable(obj.boundaryTable, undefined, 'clearBoundary');
        const tbody = obj.boundaryEditTable.querySelector("tbody"); tbody.innerHTML = "";
        obj.boundarySelectorView.value = ''; obj.boundarySelector.value = ''; 
        obj.boundarySelector.innerHTML = ''; obj.boundaryViewContainer.style.display = 'none'; 
        obj.boundaryText.value = ''; obj.boundaryName.value = '';
    });
    // Plot chart
    obj.sourcePlotBtn.addEventListener('click', () => { 
        const data = getDataFromTable(obj.sourceTable, true);
        const title = 'Hydrological Time-Series Graph';
        const titleChart = obj.sourceName.value.slice(0, -4);
        plotTimeSeries(obj.plotContainer, title, data, titleChart);
    });
    obj.meteoPlotBtn.addEventListener('click', () => {
        const data = getDataFromTable(obj.meteoTable, true);
        const title = 'Meteorological Time-Series Graph';
        const titleChart = obj.meteoUploadText.value.slice(0, -4);
        plotTimeSeries(obj.plotContainer, title, data, titleChart);
    });
    // Working on hydrological option
    const hydrologicalOption = (e) => {
        sourceChange(
            e.target, obj.sourceTable, obj.sourceLatitude, 
            obj.sourceLongitude, obj.sourceName, obj.sourceUploadText
        ); 
        deleteTable(obj.sourceTable); obj.sourceAddBtn.click();
        obj.sourceOptionPicker.style.display =
        e.target === obj.sourceOptionNew ? 'block' : 'none';
    };
    obj.sourceOptionNew.addEventListener('change', (e) => { hydrologicalOption(e); });
    obj.sourceOptionExist.addEventListener('change', (e) => { hydrologicalOption(e); });
    // Remove source from project
    obj.sourceRemoveBtn.addEventListener('click', async () => {
        const nameProject = obj.projectName.value.trim();
        if (nameProject === ''){ alert('Please check HYD scenario.'); return; }
        const name = obj.sourceSelectorRemove.value;
        removeRowFromTable(obj.sourceRemoveTable, name); deleteTable(obj.sourceTable);
        const content = getDataFromTable(obj.sourceRemoveTable, true).rows;
        updateTable(obj.sourceRemoveTable, obj.sourceSelectorRemove, nameProject, content);
    });
    // Change output options
    assignOutput(obj.outputHis, obj.hisStart, obj.hisStop, obj.startDate, obj.stopDate);
    assignOutput(obj.outputMap, obj.mapStart, obj.mapStop, obj.startDate, obj.stopDate);
    assignOutput(obj.outputWQ, obj.wqStart, obj.wqStop, obj.startDate, obj.stopDate);
    assignOutput(obj.outputRestart, obj.rstStart, obj.rstStop, obj.startDate, obj.stopDate);
    // Save source to project
    obj.sourceSaveBtn.addEventListener('click', async () => {
        const nameProject = obj.projectName.value.trim();
        if (nameProject === ''){ alert('Please check HYD scenario.'); return; }
        const table = getDataFromTable(obj.sourceTable, true), name = obj.sourceName.value;
        const lat = obj.sourceLatitude.value, lon = obj.sourceLongitude.value;
        if (table.rows.length === 0) { alert('No data to save. Please check the table.'); return; }
        if (lat === '' || lon === '' || name === ''){ alert('Please check Name/Latitude/Longitude.'); return; }
        const content = {
            projectName: nameProject, nameSource: name, lat: lat, 
            lon: lon, data: table.rows, BC: 1, timeZone: getLastTimeZone()
        };
        const data = await jsonLoader('save_source', content);
        updateTable(obj.sourceRemoveTable, obj.sourceSelectorRemove, nameProject);
        alert(data.message);
    });
    // Save meteo data to project
    obj.meteoSaveBtn.addEventListener('click', async () => {
        const nameProject = obj.projectName.value.trim();
        if (nameProject === ''){ alert('Please check HYD scenario.'); return; }        
        const table = getDataFromTable(obj.meteoTable, true);
        if (table.rows.length === 0) { alert('No data to save. Please check the table.'); return; }
        const content = {projectName: nameProject, data: table.rows, timeZone: getLastTimeZone()};
        const data = await jsonLoader('save_meteo', content);
        alert(data.message);
    });
    // Weather data
    obj.weatherSelector.addEventListener('change', () => {
        if (obj.weatherSelector.value === '') {
            obj.weatherPanel.style.display = 'none'; obj.weatherTable.style.display = 'none';
            obj.weatherRemove.style.display = 'none'; obj.weatherUpload.style.display = 'none'; return;
        }
        obj.weatherPanel.style.display = 'block'; obj.weatherTable.style.display = 'block'; 
        obj.weatherRemove.style.display = 'block'; obj.weatherUpload.style.display = 'block'; 
        deleteTable(obj.weatherTable);
        // Add row after above function finished
        requestAnimationFrame(() => { obj.weatherAddRow.click(); });
    });
    obj.weatherUpload.addEventListener('click', async () => {
        const nameProject = obj.projectName.value.trim();
        if (nameProject === '') { alert('Please check HYD scenario.'); return; }        
        const table = getDataFromTable(obj.weatherTable, true);
        if (table.rows.length === 0) { alert('No data to save. Please check the table.'); return; }
        const content = { projectName: nameProject, data: table.rows, timeZone: getLastTimeZone() };
        const data = await jsonLoader('save_weather', content); alert(data.message);
    })
    // Save project
    obj.projectSaver.addEventListener('click', async () => { 
        const name = obj.projectName.value.trim();
        if (name === '') { alert('Please check HYD scenario.'); return; }
        if (name === 'demo') {
            if (!confirm(
                `1. Adjusting/Overwriting the default scenario 'demo' is NOT recommended.\n` + 
                `2. This might cause unknown problems while visualizing outputs.\n` +
                `3. It is hightly recommended to make a clone of this scenario before doing any adjusment.\n\n` +
                `Are you sure you want to move on?`
            )) { return; }
        }
        const userTimeSec = timeStepCalculator(obj.userTimestepDate.value, obj.userTimestepTime.value);
        const nodalTimeSec = timeStepCalculator(obj.nodalTimestepDate.value, obj.nodalTimestepTime.value);
        const hisInterval = timeStepCalculator(obj.hisIntervalDate.value, obj.hisIntervalTime.value);
        const mapInterval = timeStepCalculator(obj.mapIntervalDate.value, obj.mapIntervalTime.value);
        const wqInterval = timeStepCalculator(obj.wqIntervalDate.value, obj.wqIntervalTime.value);
        const rtsInterval = timeStepCalculator(obj.rstIntervalDate.value, obj.rstIntervalTime.value);
        const sttInterval = timeStepCalculator(obj.statisticDate.value, obj.statisticTime.value);
        const timingInterval = timeStepCalculator(obj.timingDate.value, obj.timingTime.value);
        const elements = { name: name, latitude: obj.latitude, nLayers: obj.nLayers, 
            gridPathText: obj.gridPathText, startDate: obj.startDate, stopDate: obj.stopDate,
            userTimeSec: userTimeSec, nodalTimeSec: nodalTimeSec, obsPointTable: obj.obsPointTable, 
            crossSectionName: obj.crossSectionName, crossSectionTable: obj.crossSectionTable, 
            salinity: obj.salinity, temperature: obj.temperature, initWaterLevel: obj.initWaterLevel, 
            initSalinity: obj.initSalinity, initTemperature: obj.initTemperature, outputHis: obj.outputHis, 
            hisInterval: hisInterval, hisStart: obj.hisStart, hisStop: obj.hisStop, outputMap: obj.outputMap, 
            mapInterval: mapInterval, mapStart: obj.mapStart, mapStop: obj.mapStop, outputWQ: obj.outputWQ, 
            wqInterval: wqInterval, wqStart: obj.wqStart, wqStop: obj.wqStop, outputRestart: obj.outputRestart, 
            rtsInterval: rtsInterval, rtsStart: obj.rstStart, rtsStop: obj.rstStop, sttInterval: sttInterval, 
            timingInterval: timingInterval 
        };
        await saveProject(elements, getLastTimeZone()); 
    });
}

async function loadScenario(scenarioName){
    // Get average latitude
    const content = { projectName: scenarioName, timeZone: getLastTimeZone() };
    const data = await jsonLoader('get_scenario', content);
    if (data.status === 'error') { alert(data.message); return; }
    obj.latitude.value = data.content.avgLat;
    obj.nLayers.value = data.content.nLayers;
    const gridName = data.content.gridPath;
    if (gridName != '') {
        const contentGrid = { 
            projectName: scenarioName, key: 'hyd', gridName: gridName, opacity: 1
        }
        iframeConnector(null, null, 'gridPlot', contentGrid);
    } else { iframeConnector(null, null, 'clearGridMap', {}); }
    obj.gridPathText.value = gridName;
    obj.startDate.value = data.content.startDate;
    obj.stopDate.value = data.content.stopDate;
    obj.userTimestepDate.value = data.content.userTimestepDate;
    obj.userTimestepTime.value = data.content.userTimestepTime;
    obj.nodalTimestepDate.value = data.content.nodalTimestepDate;
    obj.nodalTimestepTime.value = data.content.nodalTimestepTime;
    if (data.content.obsPointTable !== undefined && data.content.obsPointTable.length > 0) {
        fillTable(data.content.obsPointTable, obj.obsPointTable);
    } else { deleteTable(obj.obsPointTable); obj.obsPointAddRow.click(); }
    if (data.content.crossSectionTable !== undefined && data.content.crossSectionTable.length > 0) {
        fillTable(data.content.crossSectionTable, obj.crossSectionTable); 
    } else { 
        deleteTable(obj.crossSectionTable); 
        addRowToTable(obj.crossSectionTable, ['Cross-Section Name', 'Latitude', 'Longitude']); 
    }
    let defaultOption = `<option value="" selected>--- No selected ---</option>`;
    if (data.content.boundaryTable !== undefined && data.content.boundaryTable.length > 0) {
        fillTable(data.content.boundaryTable, obj.boundaryTable);
        // Update boundary option
        const options = data.content.boundaryTable.map(row => `<option value="${row[0]}">${row[0]}</option>`).join(' ');
        defaultOption = defaultOption + options;
    } else { 
        deleteTable(obj.boundaryTable);
        addRowToTable(obj.boundaryTable, ['Boundary Name', 'Latitude', 'Longitude']);
    }
    obj.boundarySelector.innerHTML = defaultOption;
    obj.initWaterLevel.value = data.content.initWaterLevel;
    obj.initSalinity.value = data.content.initSalinity;
    obj.initTemperature.value = data.content.initTemperature;
    // Get source data if exist
    updateTable(obj.sourceRemoveTable, obj.sourceSelectorRemove, scenarioName);
    if (data.content.meteoPath.length > 0) { 
        obj.meteoUploadText.value = data.content.meteoName;
        const meteoPath = data.content.meteoPath;
        if (meteoPath.length === 0) return;
        const header = meteoPath[0], content = meteoPath.slice(1);
        const thead = obj.meteoTable.querySelector('thead');
        thead.innerHTML = ''; const tr = document.createElement('tr');
        header.forEach(item => {
            const th = document.createElement('th');
            th.textContent = item; tr.appendChild(th);
        }); thead.appendChild(tr); fillTable(content, obj.meteoTable);
    } else { deleteTable(obj.meteoTable); obj.meteoAddBtn.click(); }
    if (data.content.weatherPath.length > 0) {
        obj.weatherSelector.value = data.content.weatherType;
        obj.weatherCSVUploadText.value = data.content.weatherName;
        obj.weatherPanel.style.display = 'block'; obj.weatherTable.style.display = 'block';
        obj.weatherUpload.style.display = 'block'; obj.weatherRemove.style.display = 'block';
        fillTable(data.content.weatherPath, obj.weatherTable);
    } else { 
        obj.weatherPanel.style.display = 'none'; obj.weatherTable.style.display = 'none';
        obj.weatherUpload.style.display = 'none'; obj.weatherRemove.style.display = 'none';
        obj.weatherSelector.value = ''; deleteTable(obj.weatherTable); obj.weatherAddRow.click(); 
    }
    obj.hisIntervalDate.value = data.content.hisIntervalDate;
    obj.hisIntervalTime.value = data.content.hisIntervalTime;
    obj.hisStart.value = data.content.hisStart; obj.hisStop.value = data.content.hisStop;
    if (obj.hisStart.value !== '' || obj.hisStop.value !== '') { obj.outputHis.checked = true; }
    obj.mapIntervalDate.value = data.content.mapIntervalDate;
    obj.mapIntervalTime.value = data.content.mapIntervalTime;
    obj.mapStart.value = data.content.mapStart; obj.mapStop.value = data.content.mapStop;
    if (obj.mapStart.value !== '' || obj.mapStop.value !== '') { obj.outputMap.checked = true; }
    obj.wqIntervalDate.value = data.content.wqIntervalDate;
    obj.wqIntervalTime.value = data.content.wqIntervalTime;
    obj.wqStart.value = data.content.wqStart; obj.wqStop.value = data.content.wqStop;
    if (obj.wqStart.value !== '' || obj.wqStop.value !== '') { obj.outputWQ.checked = true; }
    obj.statisticDate.value = data.content.statisticDate; obj.statisticTime.value = data.content.statisticTime;
    obj.timingDate.value = data.content.timingDate; obj.timingTime.value = data.content.timingTime;
}
