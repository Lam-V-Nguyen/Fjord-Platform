import { waqMapId, origin, getLastTimeZone } from "./constant.js";
import { setupTabs } from "./tabManager.js";
import { getProjectList, jsonLoader, fillTable, deleteTable, addRowToTable, signalSender,
    nameChecker, iframeConnector, getDataFromTable, copyPaste, removeRowFromTable
} from "./commonFunctions.js";
import { projectRender } from "./projectManager.js";
import { toUTC } from "./projectSaver.js";

const defaultWAQ = `<option value="">-- New WAQ model --</option>`;
let projectSelected = [], subKey = '', folderName = '', useforsTo = [], 
    volPath = '', timeStep1 = 0, timeStep2 = 0, nSegments = 0, attrPath_ =' ',
    exchange_x = 0, exchange_z = 0, exchange_y = 0, ptrPath = '', areaPath = '',
    flowPath = '', lengthPath = '', srfPath = '', vdfPath = '', temPath = '',
    salPath = '', usefors = null, initial_area = null, scheme = null, maxiter = null,
    tolerance = null, useforsFrom = [], waqContent = [], from_initial = null,
    initial_value = null, to_usefors = null, from_usesfor = null, n_layers='';

const $ = (id) => document.getElementById(id);
const obj = {
    descriptionTab: $('desription-tab'), controlTab: $('control-tab'),
    projectName: $('project-name'), projectList: $('project-list'),
    waqSelector: $('waq-name'), waqLabel: $('label-waq'),
    projectCreator: $('create-btn'), projectCloner: $('duplicate-btn'),
    projectRemover: $('remove-btn'), hydFilename: $('hyd-filename'), 
    nLayers: $('n-layer'), startTime: $('start-time'), stopTime: $('stop-time'),
    sourcesContainer: $('wq-sources-container'), sourcesTable: $('wq-sources-table'),
    obsPointName: $('wq-obs-point'), obsPointPicker: $('wq-obs-picker'),
    obsPointRemove: $('wq-obs-remove'), obsPointTable: $('wq-obs-table'),
    loadsPointName: $('wq-loads-point'), loadsPointPicker: $('wq-loads-picker'),
    loadsPointRemove: $('wq-loads-remove'), loadsPointTable: $('wq-loads-table'),
    timeTable: $('wq-time-series-table'), timeTableAddRow: $('time-series-add-row'),
    inputFile: $('input-csv'), csvTable: $('time-series-csv'),
    removeTable: $('time-series-remove'), timePreviewContainer: $('wq-textarea-container'),
    timePreview: $('wq-data-view'), physicalSelector: $('wq-physical'),
    physicalName: $('wq-physical-name'), usesforFromPhysical: $('wq-physical-usefors-from'),
    usesforToPhysical: $('wq-physical-usefors-to'), usesforPhysical: $('wq-usefors-physical'),
    initialFromPhysical: $('wq-physical-initial-from'), initialToPhysical: $('wq-physical-initial'),
    initialAreaPhysical: $('wq-initial-physical'), schemePhysical: $('wq-scheme-physical'),
    maxInterPhysical: $('max-iterations-physical'), tolerancePhysical: $('tolerance-physical'),
    chemicalSelector: $('wq-chemical'), chemicalName: $('wq-chemical-name'),
    usesforFromChemical: $('wq-chemical-usefors-from'), usesforToChemical: $('wq-chemical-usefors-to'),
    usesforChemical: $('wq-usefors-chemical'), initialFromChemical: $('wq-chemical-initial-from'),
    initialToChemical: $('wq-chemical-initial'), initialAreaChemical: $('wq-initial-chemical'),
    schemeChemical: $('wq-scheme-chemical'), maxInterChemical: $('max-iterations-chemical'),
    toleranceChemical: $('tolerance-chemical'), microbialSelector: $('wq-microbial'),
    microbialName: $('wq-microbial-name'), usesforFromMirobial: $('wq-microbial-usefors-from'),
    usesforToMirobial: $('wq-microbial-usefors-to'), usesforMicrobial: $('wq-usefors-microbial'),
    initialFromMirobial: $('wq-microbial-initial-from'), initialToMirobial: $('wq-microbial-initial'),
    initialAreaMirobial: $('wq-initial-microbial'), schemeMicrobial: $('wq-scheme-microbial'),
    maxInterMirobial: $('max-iterations-microbial'), toleranceMirobial: $('tolerance-microbial'),
};

setupTabs(document); projectOptions(); waqManager();

async function projectOptions(){
    projectSelected = await getProjectList();
    await projectRender(obj.projectName, obj.projectList, projectSelected);
    obj.projectName.style.pointerEvents = "auto";
    obj.projectName.addEventListener('click', async () => {
        if (projectSelected.length === 0) { return; }
        obj.projectList.innerHTML = '';
        projectSelected.forEach(p => {
            const li = document.createElement("li");
            li.textContent = p;
            li.addEventListener('mousedown', () => {
                obj.projectName.value = p; 
                obj.projectList.style.display = "none";
            });
            obj.projectList.appendChild(li);
        });
        obj.projectList.style.display = "block";
    });
    obj.projectName.addEventListener('input', (e) => { 
        const value = e.target.value.trim();
        if (value === '') {
            obj.controlTab.style.display = "none"; 
            obj.descriptionTab.style.display = "block";
            obj.waqSelector.innerHTML = ''; 
            obj.waqSelector.style.display = "none"; 
            obj.waqLabel.style.display = "none";
            obj.projectCloner.style.display = "none"; 
            obj.projectRemover.style.display = "none";
        }
        projectRender(obj.projectName, obj.projectList, projectSelected);
    });
    obj.projectName.addEventListener('blur', () => { 
        setTimeout(() => { obj.projectList.style.display = "none"; }, 5);
    });
    obj.projectList.addEventListener('mousedown', async () => {
        const name = obj.projectName.value.trim();
        const data = await jsonLoader('select_waq', { projectName: name });
        if (!name || name === '' || data.status === "error") { 
            obj.waqSelector.style.display = "none"; obj.waqSelector.value = '';
            obj.waqLabel.style.display = "none"; obj.projectCloner.style.display = "none"; 
            obj.projectRemover.style.display = "none"; return; 
        }
        waqContent = data.content; obj.descriptionTab.style.display = "block";
        obj.controlTab.style.display = "none";
        const waqTemp = waqContent.map(name => `<option value="${name}">${name}</option>`).join('');
        obj.waqSelector.innerHTML = defaultWAQ + waqTemp; obj.waqSelector.value = '';
        obj.waqSelector.style.display = "flex"; obj.waqLabel.style.display = "flex";
    });
    obj.waqSelector.addEventListener('change', async (e) => { 
        const value = e.target.value;
        if (value === '') {
            obj.projectCloner.style.display = "none"; 
            obj.projectRemover.style.display = "none";
        } else {
            obj.projectCloner.style.display = "flex"; 
            obj.projectRemover.style.display = "flex";
        }
        obj.controlTab.style.display = "none"; 
        obj.descriptionTab.style.display = "block";
    });    
    // Create new WAQ scenario
    obj.projectCreator.addEventListener('click', async () => {
        const name = obj.projectName.value.trim();
        if (!name || name.trim() === '') { alert('Please select a HYD Scenario from the list.'); return; }
        // Find .hyd file
        const data = await jsonLoader('select_hyd', { projectName: name, timeZone: getLastTimeZone() });
        if (data.status === "error") { alert(data.message); return; }
        // Show tabs
        obj.controlTab.style.display = "block"; obj.descriptionTab.style.display = "none";
        obj.sourcesContainer.style.display = data.content.sink_sources.length > 0 ? "block":"none";
        fillTable(data.content.sink_sources, obj.sourcesTable, true);
        // Assign values
        obj.hydFilename.value = data.content.filename; volPath = data.content.vol_path;
        timeStep1 = data.content.time_step1; timeStep2 = data.content.time_step2;
        nSegments = data.content.n_segments; attrPath_ = data.content.attr_path;
        exchange_x = data.content.exchange_x; exchange_z = data.content.exchange_z;
        if (data.content.exchange_y) { exchange_y = data.content.exchange_y; }
        ptrPath = data.content.ptr_path; areaPath = data.content.area_path;
        flowPath = data.content.flow_path; lengthPath = data.content.length_path;
        if (data.content.n_layers) { obj.nLayers.value = data.content.n_layers; }
        srfPath = data.content.srf_path; vdfPath = data.content.vdf_path;
        temPath = data.content.tem_path; salPath = data.content.sal_path;
        obj.startTime.value = data.content.start_time; 
        obj.stopTime.value = data.content.stop_time;
        // Plot grid on map
        const content = { 
            projectName: name, gridName: data.content.grid, 
            key: 'waq', opacity: 0.2
        }
        iframeConnector(null, null, 'gridPlot', content);
        // Set default values
        deleteTable(obj.obsPointTable); deleteTable(obj.loadsPointTable);
        addRowToTable(obj.obsPointTable, ['Name', 'Latitude', 'Longitude']);
        addRowToTable(obj.loadsPointTable, ['Name', 'Latitude', 'Longitude']);
        deleteTable(obj.timeTable); 
        addRowToTable(obj.timeTable, ['Time', 'Location', 'Substance', 'Value']);
        // Physical tab
        obj.physicalSelector.value = ''; obj.physicalName.value = ''; 
        obj.usesforFromPhysical.innerHTML = ''; obj.usesforToPhysical.innerHTML = '';
        obj.usesforPhysical.value = ''; obj.initialFromPhysical.innerHTML = '';
        obj.initialAreaPhysical.value = ''; obj.schemePhysical.value = '15';
        obj.maxInterPhysical.value = '500'; obj.tolerancePhysical.value = '1E-07';
        // Chemical tab
        obj.chemicalSelector.value = ''; obj.chemicalName.value = '';
        obj.usesforFromChemical.innerHTML = ''; obj.usesforToChemical.innerHTML = '';
        obj.usesforChemical.value = ''; obj.initialFromChemical.innerHTML = '';
        obj.initialAreaChemical.value = ''; obj.schemeChemical.value = '15';
        obj.maxInterChemical.value = '500'; obj.toleranceChemical.value = '1E-07';
        // Microbial tab
        obj.microbialSelector.value = ''; obj.microbialName.value = '';
        obj.usesforFromMirobial.innerHTML = ''; obj.usesforToMirobial.innerHTML = '';
        obj.usesforMicrobial.value = ''; obj.initialFromMirobial.innerHTML = '';
        obj.initialAreaMirobial.value = ''; obj.schemeMicrobial.value = '15';
        obj.maxInterMirobial.value = '500'; obj.toleranceMirobial.value = '1E-07';
        obj.timePreview.value = ''; obj.timePreviewContainer.style.display = 'none';
        const waqValue = obj.waqSelector.value;
        if (waqValue !== '') { 
            const content = { projectName: name, waqName: waqValue, timeZone: getLastTimeZone() };
            const data = await jsonLoader('load_waq', content);
            if (data.status === "error") { alert(data.message); return; }
            if (data.content.obs.length > 0) { fillTable(data.content.obs, obj.obsPointTable, true); }
            fillTable(data.content.loads, obj.loadsPointTable, true);
            deleteTable(obj.timeTable); fillTable(data.content.time_data, obj.timeTable, true);
            if (data.content.mode === 'physical') {
                obj.physicalSelector.value = data.content.key;
                obj.physicalName.value = data.content.name;
                obj.chemicalSelector.value = ''; obj.chemicalName.value = '';
                obj.microbialSelector.value = ''; obj.microbialName.value = '';
                from_usesfor = obj.usesforFromPhysical; to_usefors = obj.usesforToPhysical;
                from_initial = obj.initialFromPhysical; usefors = obj.usesforPhysical;
                initial_area = obj.initialAreaPhysical; scheme = obj.schemePhysical;
                maxiter = obj.maxInterPhysical; tolerance = obj.tolerancePhysical;
            } else if (data.content.mode === 'chemical') {
                obj.chemicalSelector.value = data.content.key;
                obj.chemicalName.value = data.content.name;
                obj.physicalSelector.value = ''; obj.physicalName.value = '';
                obj.microbialSelector.value = ''; obj.microbialName.value = '';
                from_usesfor = obj.usesforFromChemical; to_usefors = obj.usesforToChemical;
                from_initial = obj.initialFromChemical; usefors = obj.usesforChemical;
                initial_area = obj.initialAreaChemical; scheme = obj.schemeChemical;
                maxiter = obj.maxInterChemical; tolerance = obj.toleranceChemical;
            } else if (data.content.mode === 'microbial') {
                obj.microbialSelector.value = data.content.key;
                obj.microbialName.value = data.content.name;
                obj.physicalSelector.value = ''; obj.physicalName.value = '';
                obj.chemicalSelector.value = ''; obj.chemicalName.value = '';
                from_usesfor = obj.usesforFromMirobial; to_usefors = obj.usesforToMirobial;
                from_initial = obj.initialFromMirobial; usefors = obj.usesforMicrobial;
                initial_area = obj.initialAreaMirobial; scheme = obj.schemeMicrobial;
                maxiter = obj.maxInterMirobial; tolerance = obj.toleranceMirobial;
            }
            usefors.value = data.content.usefors; initial_area.value = data.content.initial;
            scheme.value = data.content.scheme; maxiter.value = data.content.maxiter;
            tolerance.value = data.content.tolerance;
            data.content.useforsFrom.forEach(item => {
                [from_usesfor, from_initial].forEach(select => {
                    const option = document.createElement('option');
                    option.value = item; option.text = item;
                    select.add(option);
                })
            });
            data.content.useforsTo.forEach(item => {
                const option = document.createElement('option');
                option.value = item; option.text = item;
                to_usefors.add(option);
            });
            obj.timePreviewContainer.style.display = 'flex';
            obj.timePreview.value = data.content.times;
        }
    });
    // Clone scenario
    obj.projectCloner.addEventListener('click', async () => { 
        const name = obj.waqSelector.value;
        if (!name || name === '') { alert('Please select a WAQ scenario first.'); return; }
        const newName = prompt('Please enter a name for the new WAQ scenario.');
        if (!newName || newName === '') { alert('Please define the clone scenario name.'); return; }
        if (nameChecker(newName)) { alert('Scenario name contains invalid characters.'); return; }
        signalSender('showOverlay', `Cloning WAQ scenario '${name}' to '${newName}'. Please be patient...`);
        const data = await jsonLoader('clone_waq', {
            projectName: obj.projectName.value, oldName: name, newName: newName
        });
        if (data.status === "error") { signalSender('hideOverlay'); return; }
        waqContent.push(newName); obj.waqSelector.innerHTML = '';
        const waqTemp = waqContent.map(name => `<option value="${name}">${name}</option>`).join('');
        obj.waqSelector.innerHTML = defaultWAQ + waqTemp; obj.waqSelector.value = newName;
        signalSender('hideOverlay'); alert(data.message);
    });
    // Delete scenario
    obj.projectRemover.addEventListener('click', async () => {
        const name = obj.projectName.value.trim(), waqName = obj.waqSelector.value;
        if (!confirm(`Are you sure you want to delete scenario '${waqName}'?`)) { return; }
        signalSender('showOverlay', `Deleting WAQ scenario '${waqName}'. Please be patient...`);
        const data = await jsonLoader('delete_waq', { projectName: name, name: waqName });
        obj.waqSelector.innerHTML = '';
        waqContent = waqContent.filter(item => item !== waqName);
        const waqTemp = waqContent.map(name => `<option value="${name}">${name}</option>`).join('');
        obj.waqSelector.innerHTML = defaultWAQ + waqTemp;
        obj.waqSelector.value = ''; signalSender('hideOverlay'); alert(data.message); 
    });
}

async function waqManager(){
    // Check whether map widget exists
    const layout = localStorage.getItem('grid-layout');
    const hasMap = layout ? JSON.parse(layout).some(item => item.id === waqMapId) : false;
    const content = { id: waqMapId, title: 'Water Quality Scenario Map' };
    if (!hasMap) signalSender('addMapWidget', content);
    // Update location
    iframeConnector(obj.obsPointPicker, [obj.obsPointName, obj.obsPointTable],
        'waqPoint', () => getDataFromTable(obj.obsPointTable, true), ''
    );
    iframeConnector(obj.loadsPointPicker, [obj.loadsPointName, obj.loadsPointTable],
        'loadsPoint', () => getDataFromTable(obj.loadsPointTable, true), ''
    );
    // Copy and paste to tables
    copyPaste(obj.obsPointTable, 3); copyPaste(obj.loadsPointTable, 3);
    copyPaste(obj.timeTable, 4); copyPaste(obj.sourcesTable, 3);
    // Remove point from table
    obj.obsPointRemove.addEventListener('click', () => {
        const name = obj.obsPointName.value.trim();
        if (name === '') { alert('Please enter name of observation point from list to remove.'); return; }
        removeRowFromTable(obj.obsPointTable, name); obj.obsPointName.value = '';
        const contents = { type: 'waqPoint', content: getDataFromTable(obj.obsPointTable, true) }
        window.parent.postMessage(contents, origin);
    });
    obj.loadsPointRemove.addEventListener('click', () => {
        const name = obj.loadsPointName.value.trim();
        if (name === '') { alert('Please enter name of loads point from list to remove.'); return; }
        removeRowFromTable(obj.loadsPointTable, name); obj.loadsPointName.value = '';
        const contents = { type: 'loadsPoint', content: getDataFromTable(obj.loadsPointTable, true) }
        window.parent.postMessage(contents, origin);
    });
    // Update when user change Combobox
    substanceChanger(obj.waqSelector, obj.chemicalSelector, obj.chemicalName, 'wq-chemical');
    substanceChanger(obj.waqSelector, obj.physicalSelector, obj.physicalName, 'wq-physical');
    substanceChanger(obj.waqSelector, obj.microbialSelector, obj.microbialName, 'wq-microbial');
    // Add new row to table
    obj.timeTableAddRow.addEventListener('click', () => {
        addRowToTable(obj.timeTable, ['YYYY-MM-DD HH:MM:SS', 'PointName', 'Substance', 'Value']);
    });
    // Delete table
    obj.removeTable.addEventListener('click', () => { 
        deleteTable(obj.timeTable); obj.timePreview.value = ''; 
        obj.timePreviewContainer.style.display = 'none';
        addRowToTable(obj.timeTable, ['YYYY-MM-DD HH:MM:SS', 'PointName', 'Substance', 'Value']);
    });
    // Upload CSV
    obj.csvTable.addEventListener('click', () => { 
        obj.inputFile.click();
        obj.inputFile.addEventListener('change', () => {
            if (obj.inputFile.files.length > 0) {
                const file = obj.inputFile.files[0];
                const reader = new FileReader();
                reader.onload = (e) => {
                    const text = e.target.result;
                    // Get the first row
                    const firstLine = text.split(/\r?\n/)[0];
                    const columns = firstLine.split(/\t|,/);
                    if (columns.length !== 4) { 
                    alert(`The current table has ${columns.length} columns.\nNumber of columns must be 4.`); 
                        obj.inputFile.value = ''; return; 
                    }
                    const rows = text.split(/\r?\n/).slice(1).filter(row => row.trim() !== ''); // Split into rows 
                    const data_arr = rows.map(row => row.split(/\t|,/).slice(0, 4)); // Split into columns
                    fillTable(data_arr, obj.timeTable, true);
                    obj.inputFile.value = "";
                };
                reader.readAsText(file, 'UTF-8');
            }
        }, {once: true});
    });
    // Check function to process time-series
    document.querySelectorAll('.wq-process-time-series').forEach(btn => {
        btn.addEventListener('click', async () => {
            const loadsData = getDataFromTable(obj.loadsPointTable, true);
            if (loadsData.rows.length === 0) {
                alert("No loads data found in the table.\nPlease check the load table in tab 'Point Settings'."); 
                obj.timePreviewContainer.style.display = 'none'; return; 
            }
            const timeData = getDataFromTable(obj.timeTable, true);
            if (timeData.rows.length === 0) {
                alert("No time-series data found in the table.\nPlease check the table 'Time-Series Preparation'."); 
                obj.timePreviewContainer.style.display = 'none'; return; 
            }
            if (btn.id === 'wq-chemical') {
                subKey = obj.chemicalSelector.value; folderName = obj.chemicalName.value.trim();
                initial_area = obj.initialAreaChemical; usefors = obj.usesforChemical;
                to_usefors = obj.usesforToChemical; initial_value = obj.initialToChemical;
            } else if (btn.id === 'wq-physical') {
                subKey = obj.physicalSelector.value; folderName = obj.physicalName.value.trim();
                initial_area = obj.initialAreaPhysical; usefors = obj.usesforPhysical;
                to_usefors = obj.usesforToPhysical; initial_value = obj.initialToPhysical;
            } else if (btn.id === 'wq-microbial') {
                subKey = obj.microbialSelector.value; folderName = obj.microbialName.value.trim();
                initial_area = obj.initialAreaMirobial; usefors = obj.usesforMicrobial;
                to_usefors = obj.usesforToMirobial; initial_value = obj.initialToMirobial;
            }
            obj.timePreview.value = ''; initial_area.value = ''; usefors.value = ''; initial_value.value = '0';
            if (subKey === '') { alert('Please specify type of simulation.'); return; }
            if (folderName === '') { alert('Please specify name of substance.'); return; }
            const content = { folderName: folderName, loadsData: loadsData.rows, 
                timeData: timeData.rows, timeZone: getLastTimeZone()
            };
            const data = await jsonLoader('wq_time_to_waq', content);
            if (data.status === "error") {
                obj.timePreviewContainer.style.display = 'none'; 
                obj.timePreview.value = ''; alert(data.message); return;
            };
            obj.timePreview.value = data.content; useforsTo = data.tos;
            obj.timePreviewContainer.style.display = 'flex'; to_usefors.innerHTML = '';
            data.tos.forEach(item => {
                const option = document.createElement('option');
                option.value = item; option.text = item;
                to_usefors.add(option);
            });
        });
    });
    // Update USEFORS data
    document.querySelectorAll('.wq-usefors').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.info === 'physical') {
                from_usesfor = obj.usesforFromPhysical; 
                to_usefors = obj.usesforToPhysical;
                usefors = obj.usesforPhysical;
            } else if (btn.dataset.info === 'chemical') {
                from_usesfor = obj.usesforFromChemical; 
                to_usefors = obj.usesforToChemical;
                usefors = obj.usesforChemical;
            } else if (btn.dataset.info === 'microbial') {
                from_usesfor = obj.usesforFromMirobial; 
                to_usefors = obj.usesforToMirobial;
                usefors = obj.usesforMicrobial;
            }
            const txt = `USEFOR '${from_usesfor.value}' '${to_usefors.value}'`;
            let content = usefors.value;
            content = content === '' ? txt : content + '\n' + txt;
            // Split and remove duplicates
            usefors.value = [...new Set(content.split('\n'))].join('\n');
        });
    });
    // Update initial data
    document.querySelectorAll('.wq-initial').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.info === 'physical') {
                from_initial = obj.initialFromPhysical; 
                initial_value = obj.initialToPhysical;
                initial_area = obj.initialAreaPhysical;
            } else if (btn.dataset.info === 'chemical') {
                from_initial = obj.initialFromChemical; 
                initial_value = obj.initialToChemical;
                initial_area = obj.initialAreaChemical;
            } else if (btn.dataset.info === 'microbial') {
                from_initial = obj.initialFromMirobial; 
                initial_value = obj.initialToMirobial;
                initial_area = obj.initialAreaMirobial;
            }
            const txt = `${from_initial.value} ${initial_value.value}`;
            let content = initial_area.value;
            content = content === '' ? txt : content + '\n' + txt;
            content = [...new Set(content.split('\n'))].join('\n');
            initial_area.value = content;
        });
    });
    // Save water quality simulation
    document.querySelectorAll('.wq-simulation').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = obj.projectName.value.trim();
            if (!name || name === '') { alert('Please define project.'); return; }
            const hydPath = obj.hydFilename.value.trim();
            if (!hydPath || hydPath === '') { alert('Please define hydrological (*.hyd) file.'); return; }
            const start = obj.startTime.value, stop = obj.stopTime.value;
            if (!start || start === '' || !stop || stop === '') { alert("The fields 'Start time' and 'Stop time' are required"); return; }
            const data = await jsonLoader('select_hyd', { projectName: name, timeZone: getLastTimeZone() });
            if (data.status === "error") { alert(data.message); return; }
            timeStep1 = data.content.time_step1; timeStep2 = data.content.time_step2;
            attrPath_ = data.content.attr_path; volPath = data.content.vol_path;
            nSegments = data.content.n_segments; ptrPath = data.content.ptr_path;
            exchange_x = data.content.exchange_x; exchange_z = data.content.exchange_z;
            if (data.content.exchange_y) { exchange_y = data.content.exchange_y; }
            flowPath = data.content.flow_path; lengthPath = data.content.length_path;
            areaPath = data.content.area_path; n_layers = obj.nLayers.value.trim();
            if (!n_layers || n_layers === '') { alert("The field 'Nr. sigma layers' is required"); return; }
            srfPath = data.content.srf_path; vdfPath = data.content.vdf_path;
            temPath = data.content.tem_path; salPath = data.content.sal_path;
            const sourceTable = getDataFromTable(obj.sourcesTable, true);         
            const obsTable = getDataFromTable(obj.obsPointTable, true);
            const loadTable = getDataFromTable(obj.loadsPointTable, true);
            if (loadTable.rows.length === 0) { alert('No loads data found. Please add at least one load.'); return; }
            const timeData = obj.timePreview.value.trim(), timeZone = getLastTimeZone();
            if (!timeData || timeData === '') { alert("Post-processing field is required"); return; }
            if (btn.dataset.info === 'chemical') {
                subKey = obj.chemicalSelector.value; folderName = obj.chemicalName.value.trim();
                useforsFrom = obj.usesforFromChemical; useforsTo = obj.usesforToChemical;
                usefors = obj.usesforChemical; initial_area = obj.initialAreaChemical;
                maxiter = obj.maxInterChemical; tolerance = obj.toleranceChemical; 
                scheme = obj.schemeChemical; 
            } else if (btn.dataset.info === 'physical') {
                subKey = obj.physicalSelector.value; folderName = obj.physicalName.value.trim();
                useforsFrom = obj.usesforFromPhysical; useforsTo = obj.usesforToPhysical;
                usefors = obj.usesforPhysical; initial_area = obj.initialAreaPhysical;
                maxiter = obj.maxInterPhysical; tolerance = obj.tolerancePhysical; 
                scheme = obj.schemePhysical;
            } else if (btn.dataset.info === 'microbial') {
                subKey = obj.microbialSelector.value; folderName = obj.microbialName.value.trim();
                useforsFrom = obj.usesforFromMirobial; useforsTo = obj.usesforToMirobial;
                usefors = obj.usesforMicrobial; initial_area = obj.initialAreaMirobial;
                maxiter = obj.maxInterMirobial; tolerance = obj.toleranceMirobial; 
                scheme = obj.schemeMicrobial;
            }
            if (!folderName || folderName === '') { alert("Name of substance is required"); return; }
            const userforValue = usefors.value.trim();
            if (userforValue === '') { alert("The field 'Assigned Substance' must has at least one value"); return; }
            const valueFrom = Array.from(useforsFrom.options).map(option => option.value);
            const valueTo = Array.from(useforsTo.options).map(option => option.value);
            const initialArea = initial_area.value.trim();
            if (maxiter.value === '' || parseInt(maxiter.value) <= 0) { alert('Please define maximum number of iterations.'); return; }
            if (tolerance.value === '' || parseFloat(tolerance.value) <= 0) { alert('Please define tolerance.'); return; }
            const params = { mode: btn.dataset.info, projectName: name, key: subKey, folderName: folderName,
                hydName: hydPath, nLayers: n_layers, timeStep1: timeStep1, timeStep2: timeStep2, nSegments: nSegments,
                startTime: toUTC(start, timeZone), stopTime: toUTC(stop, timeZone), exchangeY: exchange_y, exchangeX: exchange_x,
                exchangeZ: exchange_z, attrPath: attrPath_, volPath: volPath, ptrPath: ptrPath, areaPath: areaPath, 
                flowPath: flowPath, lengthPath: lengthPath, srfPath: srfPath, vdfPath: vdfPath, temPath: temPath,
                salPath: salPath, useforsFrom: valueFrom, useforsTo: valueTo, usefors: userforValue,
                sources: sourceTable.rows, obsPoints: obsTable.rows, loadsData: loadTable.rows, timeTable: timeData, 
                initial: initialArea, maxiter: maxiter.value, tolerance: tolerance.value, scheme: scheme.value
            }
            signalSender('showOverlay', "Saving water quality configurations.\nPlease wait...")
            const waq_config = await jsonLoader('waq_config_writer', params);
            signalSender('hideOverlay'); await new Promise(resolve => setTimeout(resolve, 100));
            if (waq_config.status === 'error') { alert(waq_config.message); return; }
            alert(waq_config.message);
        });
    });
}

function substanceChanger(waqModel, target, name, type){
    target.addEventListener('change', async () => {
        if (type === 'wq-chemical') {
            from_usesfor = obj.usesforFromChemical; to_usefors = obj.usesforToChemical;
            usefors = obj.usesforChemical; from_initial = obj.initialFromChemical;
            initial_area = obj.initialAreaChemical; scheme = obj.schemeChemical;
            maxiter = obj.maxInterChemical; tolerance = obj.toleranceChemical;
            initial_value = obj.initialToChemical;
        } else if (type === 'wq-physical') {
            from_usesfor = obj.usesforFromPhysical; to_usefors = obj.usesforToPhysical;
            usefors = obj.usesforPhysical; from_initial = obj.initialFromPhysical;
            initial_area = obj.initialAreaPhysical; scheme = obj.schemePhysical;
            maxiter = obj.maxInterPhysical; tolerance = obj.tolerancePhysical;
            initial_value = obj.initialToPhysical;
        } else if (type === 'wq-microbial') {
            from_usesfor = obj.usesforFromMirobial; to_usefors = obj.usesforToMirobial;
            usefors = obj.usesforMicrobial; from_initial = obj.initialFromMirobial;
            initial_area = obj.initialAreaMirobial; scheme = obj.schemeMicrobial;
            maxiter = obj.maxInterMirobial; tolerance = obj.toleranceMirobial;
            initial_value = obj.initialToMirobial;
        }
        from_usesfor.innerHTML = ''; from_initial.innerHTML = ''; 
        initial_area.value = ''; initial_value.value = '0';
        scheme.value = '15'; maxiter.value = '500'; tolerance.value = '1E-07';
        const key = target.value;
        if (key === 'simple-oxygen') { subKey = 'Simple_Oxygen'; }
        else if (key === 'oxygen-bod-water') { subKey = 'Oxygen_BOD'; }
        else if (key === 'cadmium') { subKey = 'Cadmium'; }
        else if (key === 'eutrophication') { subKey = 'Eutrophication'; }
        else if (key === 'trace-metals') { subKey = 'Trace_Metals'; }
        else if (key === 'conservative-tracers') { subKey = 'Conservative_Tracers'; }
        else if (key === 'suspend-sediment') { subKey = 'Suspend_Sediment'; }
        else if (key === 'trwp-metals') { subKey = 'Metals_Tire_Road'; }
        else if (key === 'coliform') { subKey = 'Coliform'; }
        else { 
            obj.timePreviewContainer.style.display = 'none'; 
            obj.timePreview.value = ''; name.value = ''; 
            to_usefors.innerHTML = ''; usefors.value = ''; return;
        }
        if (waqModel.value !== '') { subKey = waqModel.value; }
        const data = await jsonLoader('wq_time_from_waq', { key: subKey });
        if (data.status === "error") {
            obj.timePreviewContainer.style.display = 'none'; 
            obj.timePreview.value = ''; alert(data.message); return;
        };
        name.value = subKey; useforsFrom = data.froms;
        [from_usesfor, from_initial].forEach(select => { 
            data.froms.forEach(item => {
                const option = document.createElement('option');
                option.value = item; option.text = item;
                select.add(option);
            }); 
        });
        obj.removeTable.dispatchEvent(new Event('click'));
    });
}