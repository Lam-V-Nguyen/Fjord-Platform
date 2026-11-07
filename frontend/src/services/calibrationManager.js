import { setupTabs } from "./tabManager.js";
import { projectRender } from "./projectManager.js";
import { getProjectList, signalSender, jsonLoader, fillTable, 
    getDataFromTable, numberFormatter, nameChecker, stringToUTC
} from "./commonFunctions.js";
import { calibrationParams, getCalibrationParam, getLastTimeZone } from "./constant.js";


const $ = (id) => document.getElementById(id);
const obj = {
    projectList: $('project-list'), projectName: $('project-name'), projectBtn: $('project-btn'),
    simulationStartDate: $('sim-start-date'), simulationEndDate: $('sim-end-date'),
    iterationNumber: $('iteration-number'), calibrationLog: $('calibration-text'), 
    progressBar: $("progressbar"), progressText: $("progress-text"), 
    progressContainer: $("progressbar-container"), iterationBtn: $('iteration-btn'), 
    runSimulationBtn: $('run-simulation-btn'), observationBtn: $('observation-btn'), 
    observationFile: $('observation-file'), observationName: $('observation-name'),
    observationStartDate: $('observation-start-date'), observationEndDate: $('observation-end-date'),
    observationTable: $('observation-table'), extractStationBtn: $('extract-station-btn'), 
    stationSelector: $('station-select'), depthSelection: $('depth-selection'),
    criteriaTarget: $('criteria-select'), weightSelector: $('weight-select'),
    summaryBtn: $('summary-btn'), correlationBtn: $('correlation-btn'),
    sensitivityObject: $('sensitivity-analysis'), sensitivityContainer: $('sensitivity-container'), 
    surrogateSelector: $('surrogate-select'), surrogateBtn: $('surrogate-btn'),
    optimizationLogContainer: $('optimization-log'), optimizationChartContainer: $('optimization-chart'),
    optimizationLog: $('model-text'), optimizationDirection: $('direction-select'),
    optimizationIterations: $('iteration-value'), optimizationBtn: $('optimization-btn'),
    optimizationProgressBarContainer: $('progressbar-optimization-container'), 
    optimizationProgressBar: $('progressbar-optimization'), optimizationProgressText: $('progress-optimization-text'),
    optimizationResults: $('optimization-results'), optimizationPlotBtn: $('plot-optimization-btn'),
    optimizationSaveBtn: $('save-optimization-btn'), optimizationContainer: $('optimization-container'), 
    comparisonSimProject: $('project-name-comparison'), comparisonSimBtn: $('project-comparison-btn'), 
    comparisonSimList: $('project-list-comparison'), simStartDate: $('sim-start'), simEndDate: $('sim-end'), 
    comparisonObsBtn: $('obs-comparison-btn'), depthComparison: $('depth-comparison'), 
    stationComparison: $('comparison-select'), comparisonObsFile: $('obs-comparison-file'), 
    comparisonObsName: $('obs-comparison-name'), obsStartDate: $('obs-start-date'), 
    obsEndDate: $('obs-end-date'), obsTable: $('obs-table'), comparisonPlot: $('comparison-plot-btn'), 
    comparisonContainer: $('comparison-container'), plotContainer: $('plot-container'),
    initObsBtn: $('init-obs-btn'), initStation: $('init-station-select'), initCheckBtn: $('init-check-btn'),
    initResult: $('init-result'), overridesTable: $('overrides-table'), addOverrideBtn: $('add-override-btn'),
    saveOverrideBtn: $('save-override-btn'), baselineBtn: $('baseline-btn'), baselineEvalBtn: $('baseline-eval-btn'),
    customParamKey: $('custom-param-key'), customParamBtn: $('custom-param-btn')
}

let activeProject = null, isRunning = false, logInterval = null, lastOffsetHYD = 0;

setupTabs(document); await getProject();
await initParameterContainer(); calibrationManager(); initialCheckManager();


async function getProject() { 
    const respond = await getProjectList('', 'input');
    await projectRender(obj.projectName, obj.projectList, respond);
    await projectRender(obj.comparisonSimProject, obj.comparisonSimList, respond);
}

function parameterRowHtml(param) {
    const scale = param.scale || 'linear';
    return `
        <div class="parameter-row" data-param="${param.value}" title="MDU: ${param.value}">
            <div><input class="param-check" type="checkbox" ${param.checked ? 'checked' : ''}></div>
            <div>${param.name}</div>
            <div><input class="param-lower" type="text" value="${param.lower}" placeholder="Lower"></div>
            <div><input class="param-upper" type="text" value="${param.upper}" placeholder="Upper"></div>
            <div><select class="param-scale">
                <option value="linear" ${scale === 'linear' ? 'selected' : ''}>linear</option>
                <option value="log" ${scale === 'log' ? 'selected' : ''}>log</option>
            </select></div>
        </div>`;
}

async function initParameterContainer() {
    const container = document.querySelector('.parameter-container');
    container.innerHTML = `
        <div class="parameter-header">
            <div>Use</div><div>Parameter</div>
            <div>Lower</div><div>Upper</div><div>Scale</div>
        </div>
    ${calibrationParams.map(parameterRowHtml).join('')}
    `;
    container.addEventListener('change', e => {
        const row = e.target.closest('.parameter-row');
        if (!row) return;
        const param = getCalibrationParam(row.dataset.param);
        if (!param) return;
        if (e.target.classList.contains('param-check')) param.checked = e.target.checked;
        if (e.target.classList.contains('param-lower')) param.lower = parseFloat(e.target.value);
        if (e.target.classList.contains('param-upper')) param.upper = parseFloat(e.target.value);
        if (e.target.classList.contains('param-scale')) param.scale = e.target.value;
    });
    // Tham số tuỳ chọn: khoá MDU bất kỳ, 'Key_factor' hoặc 'Key[i]'
    obj.customParamBtn?.addEventListener('click', () => {
        const key = obj.customParamKey.value.trim();
        if (!/^[A-Za-z0-9_]+(\[\d+\])?$/.test(key)) { alert("Use an MDU key, 'Key_factor' or 'Key[i]'."); return; }
        if (getCalibrationParam(key)) { alert(`'${key}' is already in the list.`); return; }
        const isFactor = key.endsWith('_factor');
        const param = { name: key, value: key, checked: true, lower: isFactor ? 0.5 : 0, upper: isFactor ? 1.5 : 1, scale: 'linear' };
        calibrationParams.push(param);
        container.insertAdjacentHTML('beforeend', parameterRowHtml(param));
        obj.customParamKey.value = '';
    });
}

async function openProject(projectObj, startObj, endObj, key='') {
    const project = projectObj.value.trim();
    if (project === '') { alert('Please select a HYD scenario first.'); return; }
    signalSender('showOverlay', 'Reading information from project "' + project + '".\nPlease wait...');
    const content = { projectName: project, key: key, timeZone: getLastTimeZone() };
    const data = await jsonLoader('calibration_project', content); signalSender('hideOverlay');
    if (data.status === 'error') { alert(data.message); return; }
    startObj.value = data.content['start']; endObj.value = data.content['end'];
}

function calibrationManager() {
    obj.projectBtn.addEventListener('click', async () => {
        openProject(obj.projectName, obj.simulationStartDate, obj.simulationEndDate, 'calibration');
    });
    obj.iterationBtn.addEventListener('click', async () => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        const iterationNumber = parseInt(obj.iterationNumber.value);
        if (isNaN(iterationNumber) || iterationNumber <= 0) { alert('Please specify the number of resampling.'); return; }
        // Get checked parameters and their bounds
        const checkedParams = calibrationParams.filter(p => p.checked);
        if (checkedParams.length === 0) { alert('Please specify at least one parameter to be calibrated.'); return; }
        for (const p of checkedParams) {
            if (!Number.isFinite(p.lower) || !Number.isFinite(p.upper) || p.lower >= p.upper) {
                alert(`'${p.name}': lower must be smaller than upper.`); return;
            }
            if (p.scale === 'log' && p.lower <= 0) { alert(`'${p.name}': log scale needs lower > 0.`); return; }
        }
        const params_in = checkedParams.map(p => [p.value, p.lower, p.upper, p.scale || 'linear']);
        if (isRunning) { alert("Iteration is currently running."); return; }
        // Reset UI
        activeProject = currentProject; isRunning = true;
        obj.progressText.innerText = 'Iteration is in progress. Please wait...'; 
        obj.progressContainer.style.display = 'flex'; obj.progressBar.value = 0; 
        obj.calibrationLog.value = ''; const key = 'iteration';
        const content = { 
            projectName: currentProject, key: key, 
            iterationNumber: iterationNumber, params: params_in
        };
        try {
            const start = await jsonLoader('start_calibration_iteration', content);
            if (start.status === "running") {
                obj.progressBar.value = start.progress || 0;
                obj.progressText.innerText = start.message || 'Iteration is running.';
            }
            if (start.status === "failed") { 
                isRunning = false; obj.progressBar.value = 0;
                obj.progressText.innerText = start.message || 'Failed to start iteration.';
                alert(start.message || 'Failed to start iteration.'); return; 
            }
            updateLogResampling(currentProject, key, obj.progressBar, obj.progressText, 1);
        } catch (error) { 
            obj.progressText.innerText = 'Failed to start iteration.';
            alert(`Iteration is failed: ${error.message}`); isRunning = false;
        }
    });
    obj.runSimulationBtn.addEventListener('click', async () => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        // obj.resamplingName không tồn tại trong HTML -> lỗi TypeError; '' = các kịch bản hiệu chỉnh
        const samplingName = '', key = 'calibration', logFile = 'log_calibration.txt';
        if (isRunning) { alert("Detected an HYD simulation is running. Please wait until it finishes."); return; }
        obj.calibrationLog.value = ''; obj.progressBar.value = 0;
        obj.progressContainer.style.display = 'flex';
        const checkContent = { projectName: currentProject, key: key };
        const statusRes = await jsonLoader('check_sim_status_calibration', checkContent);
        if (statusRes.status === "running") {
            const res = await fetch(`/calibration_log_full/${currentProject}?log_file=${logFile}`);
            if (res.ok) {
                const data = await res.json();
                obj.calibrationLog.value = data.content || ''; lastOffsetHYD = data.offset;
            }; isRunning = true;
            updateLogSimulation(
                currentProject, key, obj.calibrationLog, obj.progressBar, obj.progressText, 5, logFile
            );
            obj.progressText.innerText = statusRes.message; obj.progressBar.value = statusRes.progress;
            alert("HYD simulation is running."); return; 
        } else { isRunning = false; }
        const content = { projectName: currentProject, key: key, samplingName: samplingName, logFile: logFile };
        const start = await jsonLoader('start_sim_calibration', content);
        if (start.status === "error") { alert(start.message); return; }
        obj.progressText.innerText = 'Preparing data for the HYD simulation...';
        updateLogSimulation(currentProject, key, obj.calibrationLog, obj.progressBar, obj.progressText, 5, logFile);
    });
    obj.observationBtn.addEventListener('click', () => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        obj.observationFile.click();
    });
    obj.observationFile.addEventListener('change', async (e) => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        const simStart = obj.simulationStartDate.value; const simEnd = obj.simulationEndDate.value;
        if (simStart === '' || simEnd === '') { alert('Please add the start/end of the simulation.'); return; }
        const file = e.target.files[0]; if (!file) return;
        const formData = new FormData(); formData.append('file', file); 
        formData.append('projectName', currentProject);
        formData.append('simStart', simStart); formData.append('simEnd', simEnd);
        signalSender('showOverlay', 'Reading observation data. Please wait...');
        const response = await fetch('/obs_calibration_upload', { method: 'POST', body: formData });
        const data = await response.json(); signalSender('hideOverlay'); e.target.value = '';
        if (data.status === 'error') { alert(data.message); return; }
        fillTable(data.content.data, obj.observationTable);
        obj.observationName.value = file.name;
        obj.observationStartDate.value = data.content['start'];
        obj.observationEndDate.value = data.content['end']; 
    });
    obj.extractStationBtn.addEventListener('click', async () => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        signalSender('showOverlay', 'Getting station data from simulation "' + currentProject + '".\nPlease wait...');
        const data = await jsonLoader('get_stations_calibration', { projectName: currentProject });
        signalSender('hideOverlay');
        if (data.status === "error") { alert(data.message); return; }
        // Add content to the selector
        const defaultValue = `<option value="">-- Select a station --</option>`;
        const options = data.content.map(s => `<option value="${s}">${s}</option>`).join(''); 
        obj.stationSelector.innerHTML = defaultValue + options;
    });
    obj.summaryBtn.addEventListener('click', async () => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        const simStart = obj.simulationStartDate.value; const simEnd = obj.simulationEndDate.value;
        if (simStart === '' || simEnd === '') { alert('Please add the start/end of HYD scenario.'); return; }
        const obsStart = obj.observationStartDate.value; const obsEnd = obj.observationEndDate.value;
        if (obsStart === '' || obsEnd === '') { alert('Please add the start/end of observation.'); return; }
        const station = obj.stationSelector.value.trim();
        if (station === '') { alert('Please select a station first.'); return; }
        const depthSelection = obj.depthSelection.value.trim();
        if (depthSelection === '') { alert('Please specify the depth selection in the tab "Settings".'); return; }
        const targetValue = obj.criteriaTarget.value;
        const weightValue = obj.weightSelector.value;
        const observationData = getDataFromTable(obj.observationTable, true).rows;
        if (observationData.length === 0) { alert('Please upload observation data first.'); return; }
        signalSender('showOverlay', 'Summarizing information from different iterations for station "' + station + '".\nPlease wait...');
        const content = { 
            projectName: currentProject, simStart: simStart, simEnd: simEnd, timeZone: getLastTimeZone(),
            obsStart: obsStart, obsEnd: obsEnd, station: station, depthSelection: depthSelection,
            obsData: observationData, targetValue: targetValue, weightValue: weightValue, timeZone: getLastTimeZone()
        };
        const data = await jsonLoader('summarize_calibration', content);
        signalSender('hideOverlay'); alert(data.message); 
    });
    obj.correlationBtn.addEventListener('click', async () => {    
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        const content = { projectName: currentProject};
        const respond = await jsonLoader('correlation_calibration', content);
        if (respond.status === "error") { 
            obj.sensitivityObject.style.display = 'none';
            alert(respond.message); return; 
        }
        obj.sensitivityObject.style.display = 'block';
        await sensitivityRender(obj.sensitivityContainer, respond.content, respond.key);
    });
    obj.surrogateBtn.addEventListener('click', async () => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        const model = obj.surrogateSelector.value.trim();
        const key = 'surrogate_model', logFile = 'log_surrogate.txt';
        if (isRunning) { alert("Optimization is currently running."); return; }
        obj.optimizationLogContainer.style.display = 'flex';
        obj.optimizationChartContainer.style.display = 'none';
        obj.optimizationLog.value = ''; 
        const content = { projectName: currentProject, model: model, key: key, logFile: logFile };
        try {
            const start = await jsonLoader('start_optimization_calibration', content);
            if (start.status === "error") { 
                alert(start.message); isRunning = false; return; 
            }
            updateLogOptimization(
                currentProject, key, obj.optimizationLog, null, null, 1, logFile,
                async () => {alert('Building surrogate model completed.');}
            );
        } catch (error) { 
            alert(`Starting optimization failed: ${error.message}`); isRunning = false;
        }
    });
    obj.optimizationBtn.addEventListener('click', async () => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        const iterationNumber = parseInt(obj.optimizationIterations.value);
        if (isNaN(iterationNumber) || iterationNumber <= 0) { alert('Please specify the number of iterations.'); return; }
        const direction = obj.optimizationDirection.value;
        if (isRunning) { alert("Optimization is currently running."); return; }
        // Reset UI
        activeProject = currentProject; isRunning = true;
        obj.optimizationProgressText.innerText = 'Iteration is in progress. Please wait...'; 
        obj.optimizationProgressBarContainer.style.display = 'flex'; 
        obj.optimizationProgressBar.value = 0; 
        obj.optimizationLogContainer.style.display = 'flex';
        obj.optimizationChartContainer.style.display = 'none';
        obj.optimizationLog.value = ''; 
        obj.optimizationResults.style.display = 'none';
        const key = 'optuna', logFile = 'log_optuna.txt';
        const content = { 
            projectName: currentProject, iterations: iterationNumber, 
            direction: direction, key: key, logFile: logFile
        };
        try {
            const start = await jsonLoader('start_optimization_optuna', content);
            if (start.status === "error") { 
                alert(start.message); isRunning = false; return; 
            }
            updateLogOptimization(
                currentProject, key, obj.optimizationLog, obj.optimizationProgressBar, 
                obj.optimizationProgressText, 1, logFile
            );
        } catch (error) { 
            obj.optimizationProgressText.innerText = 'Failed to start optimization.';
            obj.optimizationResults.style.display = 'none';
            alert(`Optimization is failed: ${error.message}`); isRunning = false;
        }
    });
    obj.optimizationPlotBtn.addEventListener('click', async () => {
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        obj.optimizationLogContainer.style.display = 'none';
        obj.optimizationChartContainer.style.display = 'block';
        signalSender('showOverlay', `Getting data for plotting. Please wait...`);
        const response = await jsonLoader('plot_optuna', { projectName: currentProject });
        signalSender('hideOverlay');
        if (response.status === "error") { 
            obj.optimizationLogContainer.style.display = 'none';
            obj.optimizationChartContainer.style.display = 'none';
            alert(response.message); return; 
        }
        plotOptuna(
            obj.optimizationContainer, response.content, response.importance, response.target
        );
    });
    obj.optimizationSaveBtn.addEventListener('click', async () => {
        const container = document.querySelector('.parameter-optimal');
        if (container === null) { return; }
        const rows = container.querySelectorAll('.parameter-row-optimal');
        const params = [...rows].map(row => ({
            value: row.dataset.param, name: row.children[0].textContent,
            abbreviation: row.children[1].textContent,
            value: row.children[2].querySelector('input').value
        }));
        if (params.length === 0) { alert('No optimal parameters found.'); return;}
        // Ask for a new name
        const newName = prompt(
            'Please specify a name for the new scenario.' +
            '\nOptimal parameters will be asigned for this scenario.' +
            '\nIt is recommened to run this scenario.'
        );
        // User clicked Cancel
        if (newName === null) { return; }
        if (nameChecker(newName)) { alert('Name of clone scenario is invalid.'); return; }
        const currentProject = obj.projectName.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        signalSender('showOverlay', `Creating scenario '${newName}'. Please wait...`);
        const content = { oldProject: currentProject, newProject: newName, params: params }
        const data = await jsonLoader('save_scenario_calibration', content);
        signalSender('hideOverlay'); alert(data.message);
        if (data.status === "error") { return; }
    });
    obj.comparisonSimBtn.addEventListener('click', async () => {
        const currentProject = obj.comparisonSimProject.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        openProject(obj.comparisonSimProject, obj.simStartDate, obj.simEndDate);
        signalSender('showOverlay', 'Getting station data from simulation "' + currentProject + '".\nPlease wait...');
        const data = await jsonLoader('get_stations_comparison', { projectName: currentProject });
        if (data.status === "error") { signalSender('hideOverlay'); alert(data.message); return; }
        await new Promise(resolve => requestAnimationFrame(resolve));
        // Add content to the selector
        const defaultValue = `<option value="">-- Select a station --</option>`;
        const options = data.content.map(s => `<option value="${s}">${s}</option>`).join(''); 
        obj.stationComparison.innerHTML = defaultValue + options; signalSender('hideOverlay');
    });
    obj.comparisonObsBtn.addEventListener('click', async () => obj.comparisonObsFile.click());
    obj.comparisonObsFile.addEventListener('change', async (e) => {
        const simStart = obj.simStartDate.value; const simEnd = obj.simEndDate.value;
        if (simStart === '' || simEnd === '') { alert('Please add the start/end of the simulation.'); return; }
        const file = e.target.files[0]; if (!file) return;
        const formData = new FormData(); formData.append('file', file); 
        formData.append('simStart', simStart); formData.append('simEnd', simEnd);
        signalSender('showOverlay', 'Reading observation data. Please wait...');
        const response = await fetch('/obs_comparison_upload', { method: 'POST', body: formData });
        const data = await response.json(); signalSender('hideOverlay'); e.target.value = '';
        if (data.status === 'error') { 
            alert(`Uploading observation data failed: ${data.message}`); return; 
        }
        fillTable(data.content.data, obj.obsTable); obj.comparisonObsName.value = file.name;
        obj.obsStartDate.value = data.content['start']; obj.obsEndDate.value = data.content['end'];
    });
    obj.comparisonPlot.addEventListener('click', async () => {
        const currentProject = obj.comparisonSimProject.value.trim();
        if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
        const simStart = obj.simStartDate.value; const simEnd = obj.simEndDate.value;
        if (simStart === '' || simEnd === '') { alert('Please add the start/end of HYD simulation.'); return; }
        const obsStart = obj.obsStartDate.value; const obsEnd = obj.obsEndDate.value;
        if (obsStart === '' || obsEnd === '') { alert('Please add the start/end of the observation.'); return; }
        const depthSelection = obj.depthComparison.value.trim();
        if (depthSelection === '') { alert('Please specify the depth to compare.'); return; }
        const stationName = obj.stationComparison.value;
        if (stationName === '') { alert('Please specify the station in the simulation.'); return; }
        const obsData = getDataFromTable(obj.obsTable, true).rows;
        if (obsData.length === 0) { alert('Please upload observation data first.'); return; }
        signalSender('showOverlay', 'Preparing data for comparison. Please wait...');
        const content = { 
            projectName: currentProject, timeZone: getLastTimeZone(), simStart: simStart, 
            simEnd: simEnd, obsStart: obsStart, obsEnd: obsEnd, 
            depthSelection: depthSelection, obsData: obsData, station: stationName
        };
        const data = await jsonLoader('comparison_plot', content); signalSender('hideOverlay'); 
        if (data.status === "error") { alert(data.message); return; }
        plotComparison(obj.comparisonContainer, data.content);
        obj.plotContainer.style.display = 'block';
    });
}

function plotComparison(container, data) {
    const dfSim = data.sim, dfObs = data.obs, merge = data.merge, minMax = data.min_max;
    const depths = data.depth, metrics = data.metrics, totalRMSE = data.rmse;
    if (!container || !Array.isArray(dfSim) || !Array.isArray(dfObs) ||
        !Array.isArray(depths) || depths.length === 0) {
        if (container) { container.innerHTML = '<p>No simulation or observation data available.</p>'; }
        return;
    }
    if (depths.length === 0) { container.innerHTML = '<p>No depth data available.</p>'; return; }
    const margin = { l: 65, r: 15, t: 65, b: 50 };
    container.innerHTML = `<div class="comparison-grid"></div>`;
    const chartGrid = container.querySelector('.comparison-grid');
    // Temperature plots
    depths.forEach((d, idx) => {
        const chart = document.createElement('div'); 
        chart.id = `comparison-${idx}`; chart.className = 'comparison-chart'; 
        chartGrid.appendChild(chart); 
        const obsCol = `obs_${d}`, simCol = `sim_${d}`, traces = [];
        const metric = metrics[String(d)];
        const rmse = metric?.[0], r2 = metric?.[1], bias = metric?.[2];
        // Observation
        if (Array.isArray(dfObs) && dfObs.length > 0) { 
            const x = [], y = []; 
            dfObs.forEach(row => { 
                const time = stringToUTC(row.Time), rawValue = row[obsCol];
                // Skip missing observation values
                if (rawValue === null || rawValue === undefined || rawValue === '' ||
                    (typeof rawValue === 'string' && rawValue.trim() === '')
                ) { return; }
                const value = Number(rawValue);
                if (time && Number.isFinite(value)) { x.push(time); y.push(value); } 
            }); 
            if (x.length > 0) { 
                traces.push({ 
                    x, y, type: 'scatter', mode: 'lines+markers', name: `Observation`,
                    line: { color: '#d62728', width: 1.4 }, marker: { size: 3 }, 
                    opacity: 0.85, hovertemplate: 'Observation: %{y:.3f} °C<extra></extra>',
                });
            }
        }
        // Simulation
        if (Array.isArray(dfSim) && dfSim.length > 0) { 
            const x = [], y = []; 
            dfSim.forEach(row => { 
                const time = stringToUTC(row.Time), value = Number(row[simCol]); 
                if (time && Number.isFinite(value)) { x.push(time); y.push(value); } 
            }); 
            if (x.length > 0) { 
                traces.push({ 
                    x, y, type: 'scatter', mode: 'lines+markers', 
                    opacity: 0.85, name: `Simulation`, marker: { size: 3 },
                    line: { color: '#1f77b4', width: 1.4 }, 
                    hovertemplate: 'Simulation: %{y:.3f} °C<extra></extra>',
                });
            }
        }
        // Layout
        const layout = {
            title: {
                text: `<b>Temperature Comparation - Station: ${data.station}<br>Water Depth: ${d}m </b>`,
                font: { size: 15, weight: "bold" }
            },
            xaxis: {
                title: {text: `<b>Time<b>`, standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                type: 'date', showgrid: true, gridcolor: 'rgba(0,0,0,0.15)',
                linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            },
            yaxis: {
                title: {text: `<b>Temperature (°C)<b>`, standoff: 10, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showgrid: true, gridcolor: 'rgba(0,0,0,0.15)',
                linecolor: 'black', ticks: 'outside', ticklen: 6, 
                tickwidth: 1, tickcolor: 'black',
            },
            legend: {
                x: 0.03, y: 0.95, xanchor: 'left', yanchor: 'top', bgcolor: 'rgba(255,255,255,0.65)',
                bordercolor: 'rgba(128,128,128,0.4)', borderwidth: 1, font: { size: 12 }
            },
            annotations: [{
                x: 0.45, y: 1.03, xref: 'paper', yref: 'paper', xanchor: 'center', yanchor: 'top',
                text: `(RMSE = ${rmse == null ? '–' : Number(rmse).toFixed(3)}°C · Bias = ${bias == null ? '–' : (bias > 0 ? '+' : '') + Number(bias).toFixed(2)}°C · R² = ${r2 == null ? '–' : Number(r2).toFixed(3)})`,
                showarrow: false, align: 'center', font: { size: 12 }, 
            }],
            margin: margin, autosize: true, hovermode: 'x unified'
        };
        const config = { responsive: true, displaylogo: false };
        Plotly.newPlot(chart, traces, layout, config).then(() => {
            requestAnimationFrame(() => { Plotly.Plots.resize(chart)})
        });
    });
    // Parity plot
    const parityChart = document.createElement('div');
    parityChart.className = 'comparison-chart';
    chartGrid.appendChild(parityChart);
    const traces = [], minValue = minMax[0], maxValue = minMax[1];
    const depthColors = [ '#1f77b4', '#ff7f0e', '#2ca02c',
        '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
    ];
    depths.forEach((depth, index) => {
        const rows = merge[String(depth)];
        if (!Array.isArray(rows) || rows.length === 0) { return; }
        const obsCol = `obs_${depth}`, simCol = `sim_${depth}`;
        const x = [], y = [];
        rows.forEach(row => {
            const obs = Number(row[obsCol]), sim = Number(row[simCol]);
            if (Number.isFinite(obs) && Number.isFinite(sim)) { x.push(obs); y.push(sim); }
        });
        if (x.length === 0) { return; }
        traces.push({
            x, y, type: 'scatter', mode: 'markers', name: `Depth ${depth} m`,
            marker: {
                color: depthColors[index % depthColors.length], size: 6, opacity: 0.5
            }
        });
    });
    // Add 1:1 line
    traces.push({
        x: [minValue, maxValue], y: [minValue, maxValue], 
        type: 'scatter', mode: 'lines', name: 'Line 1:1', showlegend: false,
        line: { color: 'black', dash: 'dash', width: 1.5 }, hoverinfo: 'skip'
    });
    // Plotly layout
    const layout = {
        title: {
            text:
                `<b>Simulation vs. Observation (Station: ${data.station})</b><br>` +
                `Total RMSE = ${totalRMSE.toFixed(3)}°C` + `</span>`,
            font: { size: 15, weight: "bold" }
        },
        xaxis: {
            title: {text: `<b>Observation (°C)<b>`, standoff: 15, 
                font: { size: 16, family: 'Arial', weight: 700}
            }, 
            range: [minValue, maxValue], showline: true, 
            linewidth: 1, linecolor: 'black', ticks: 'outside',
            ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
        },
        yaxis: {
            title: {text: `<b>Simulation (°C)<b>`, standoff: 10, 
                font: { size: 16, family: 'Arial', weight: 700}
            },
            range: [minValue, maxValue], showline: true, linewidth: 1, 
            linecolor: 'black', ticks: 'outside', ticklen: 6, 
            tickwidth: 1, tickcolor: 'black',
        },
        legend: {
            x: 0.03, y: 0.95, xanchor: 'left', yanchor: 'top', 
            bgcolor: 'rgba(228, 221, 221, 0.5)',
            bordercolor: 'gray', borderwidth: 1, font: { size: 12 }
        },
        margin: margin, autosize: true, hovermode: 'closest'
    };
    const config = { responsive: true, displaylogo: false };
    Plotly.newPlot(parityChart, traces, layout, config).then(() => {
        requestAnimationFrame(() => { Plotly.Plots.resize(parityChart)})
    });
}

function plotOptuna(container, trials, importance, key) {
    if (!container || !Array.isArray(trials) || trials.length === 0) {
        container.innerHTML = '<p>No optimal trial data available.</p>'; return;
    }
    const validTrials = trials.filter(
        trial =>
            trial.value !== null && trial.value !== undefined &&
            Number.isFinite(Number(trial.value))
    );
    if (validTrials.length === 0) {
        container.innerHTML = '<p>No valid trial results available.</p>'; return;
    }
    const values = validTrials.map(trial => Number(trial.value));
    const trialNumbers = validTrials.map((trial, index) => trial.number ?? index);
    const bestValue = Math.min(...values);
    container.innerHTML = `
        <div class="optuna-chart-grid">
            <div class="optuna-chart" id="optuna-convergence"></div>
            <div class="optuna-chart" id="optuna-distribution"></div>
            <div class="optuna-chart" id="optuna-importance"></div>
            <div class="optuna-chart" id="optuna-sensitivity"></div>
        </div>
    `;
    let currentBest = Infinity, name, y1, x2, unit;
    const cumulativeBest = values.map(value => {
        currentBest = Math.min(currentBest, value); return currentBest;
    });
    if (key === 'RMSE') {
        y1 = 'Smallest RMSE (°C)'; x2 = 'RMSE (°C)'; name = 'RMSE'; unit = ' (°C)';
    } else if (key === 'R2') {
        y1 = 'Highest R²'; x2 = 'R²'; name = 'R²'; unit = '';
    }
    // Convergence Process
    Plotly.newPlot(
        'optuna-convergence',
        [{
            x: trialNumbers, y: cumulativeBest, type: 'scatter',
            mode: 'lines', name: 'Best target so far', line: { width: 2 }
        }],
        {
            title: {
                text: "Optimal Convergence Process", font: { size: 20, weight: "bold" }
            },
            xaxis: { 
                title: {text: '<b>Iterations (Trials)<b>', standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            }, 
            yaxis: { 
                title: {text: `<b>${y1}<b>`, standoff: 10, 
                    font: { size: 16, family: 'Arial', weight: 700 }
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black',
            },
            margin: { l: 70, r: 30, t: 60, b: 60 }, hovermode: 'x unified'
        },
        { responsive: true, displaylogo: false }
    );
    // Error handling for the Optuna distribution plot
    Plotly.newPlot(
        'optuna-distribution',
        [{
            x: values, type: 'histogram', name: name, nbinsx: 25, opacity: 0.75
        },
        {
            x: [bestValue, bestValue], y: [0, Math.max(1, values.length)],
            type: 'scatter', mode: 'lines', name: `Best: ${bestValue.toFixed(4)}`,
            line: { dash: 'dash', width: 2 }
        }],
        {
            title: {
                text: 'Error Distribution Across Trials', font: { size: 20, weight: "bold" }
            },
            xaxis: { 
                title: {text: `<b>${x2}<b>`, standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            }, 
            yaxis: { 
                title: {text: '<b>Number of Trials<b>', standoff: 10, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black',
            },
            legend: {
                x: 1, y: 1, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(255,255,255,0.7)', bordercolor: 'gray', borderwidth: 1
            },
            margin: { l: 70, r: 30, t: 60, b: 60 }, bargap: 0.05
        },
        { responsive: true, displaylogo: false }
    );
    const importanceNames = importance.map(item => item[0]);
    const importanceValues = importance.map(item => item[1]);
    // Parameter Importance
    Plotly.newPlot(
        'optuna-importance',
        [{
            x: importanceValues, y: importanceNames, type: 'bar', orientation: 'h',
            marker: {
                color: importanceValues, colorscale: 'Viridis', showscale: false
            },
            text: importanceValues.map(value => `${(value * 100).toFixed(1)}%`),
            textposition: 'outside', font: { size: 14 },
            hovertemplate:'%{y}<br>Importance: %{x:.3f}<extra></extra>'
        }],
        {
            title: { text: 'Importance of Parameters', font: { size: 20, weight: "bold" }},
            xaxis: {
                title: {text: '<b>Relative Importance<b>', standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                range: [0, Math.max(...importanceValues, 0.1) * 1.15],
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            }, 
            yaxis: {
                title: {text: '', standoff: 10},
                tickfont: { size: 17, family: 'Arial', weight: 700 },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black',
            },
            margin: { l: 100, r: 60, t: 60, b: 60 }
        },
        { responsive: true, displaylogo: false }
    );
    const mostImportantParam = importanceNames.length > 0 
        ? importanceNames[importanceNames.length - 1] : null;
    if (!mostImportantParam) {
        document.getElementById('optuna-sensitivity').innerHTML =
            '<p>No parameter data available.</p>';
        return;
    }
    const sensitivityColumn = `params_${mostImportantParam}`;
    const sensitivityData = validTrials
        .map(trial => ({
            x: Number(trial[sensitivityColumn]), y: Number(trial.value), trial: trial.number
        }))
        .filter(item => Number.isFinite(item.x) && Number.isFinite(item.y));
    const bestTrial = validTrials.reduce(
        (best, trial) => Number(trial.value) < Number(best.value) ? trial : best, validTrials[0]
    );
    const bestParamValue = Number(bestTrial[sensitivityColumn]);
    const xLabel = calibrationParams.find(
        param => param.value === mostImportantParam)?.name ?? mostImportantParam;
    Plotly.newPlot(
        'optuna-sensitivity',
        [{
            x: sensitivityData.map(item => item.x), y: sensitivityData.map(item => item.y),
            type: 'scatter', mode: 'markers', name: 'Trials',
            marker: { 
                size: 6, opacity: 0.9, color: sensitivityData.map(item => item.y),
                colorscale: 'RdBu', showscale: false,
                colorbar: {title: { text: `<b>${name}${unit}</b>`, side: 'right' }}
            },
            text: sensitivityData.map(item => `Trial ${item.trial}`),
            hovertemplate:
                `${xLabel}: %{x}<br>` + `${name}: %{y:.4f}${unit}<br>` + `%{text}<extra></extra>`
        },
        {
            x: [bestParamValue, bestParamValue], y: [Math.min(...values),Math.max(...values)],
            type: 'scatter', mode: 'lines', name: `Best: ${numberFormatter(bestParamValue)}`,
            line: { dash: 'dash', width: 2 }
        }],
        {
            title: {
                text: `Sensitivity Analysis: ${xLabel} - ${mostImportantParam}`,
                font: { size: 20, weight: "bold" }
            },
            xaxis: { 
                title: {text: `<b>${xLabel} (${mostImportantParam})<b>`, standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            }, 
            yaxis: { 
                title: {text: `<b>${x2}<b>`, standoff: 10, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black',
            },
            legend: {
                x: 1, y: 1, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(255,255,255,0.7)', bordercolor: 'gray', borderwidth: 1
            },
            margin: { l: 70, r: 30, t: 60, b: 60 }, hovermode: 'closest'
        },
        { responsive: true, displaylogo: false }
    );
}

function updateLogOptimization(project, key, logObject, progressBarObject, progressTextObject, seconds, logFile, onFinish=false) {
    activeProject = project; isRunning = true;
    // Prevent multiple polling intervals
    if (logInterval !== null) { clearInterval(logInterval); logInterval = null; }
    const pollStatus = async () => {
        if (activeProject !== project) { clearInterval(logInterval); logInterval = null; return; }
        try {
            const content = { projectName: project, key: key };
            const statusRes = await jsonLoader('check_optimization_status_calibration', content);
            if (progressTextObject !== null) { progressTextObject.innerText = statusRes.message; }
            if (progressBarObject !== null) { progressBarObject.value = statusRes.progress; }
            if (statusRes.status !== "running") {
                logObject.value += statusRes.message; isRunning = false;
                if (statusRes.best_params !== '' && statusRes.best_params) {
                    obj.optimizationResults.style.display = 'block';
                    // Update optimal parameter container
                    const bestParams = statusRes.best_params;
                    const container = document.querySelector('.parameter-optimal');
                    container.innerHTML = `
                        <div class="parameter-header-optimal">
                            <div>Parameter</div><div>Abbreviation</div><div>Value</div>
                        </div>
                        ${Object.entries(bestParams).map(([key, value]) => {
                            // tham số tuỳ chọn (Key_factor, Key[i]) có thể không có trong danh sách mặc định
                            const param = calibrationParams.find(p => p.value === key) || { name: key, value: key };
                            return `
                                <div class="parameter-row-optimal" data-param="${param.value}">
                                    <div>${param.name}</div><div>${param.value}</div>
                                    <div><input type="text" value="${value}"></div>
                                </div>
                            `;
                        }).join('')}
                    `;
                }
                if (logInterval) { clearInterval(logInterval); logInterval = null; }
                if (statusRes.status === 'finished' && onFinish) { await onFinish(); }
            }
            const res = await fetch(`/calibration_log_tail/${project}?offset=${lastOffsetHYD}&log_file=${logFile}`);
            if (!res.ok) return;
            const data = await res.json();
            for (const line of data.lines) { logObject.value += line + "\n"; }
            lastOffsetHYD = data.offset;
        } catch (error) { 
            obj.optimizationResults.style.display = 'none';
            alert("Error: " + (error.message || error)); isRunning = false;
            clearInterval(logInterval); logInterval = null; 
            if (progressTextObject !== null) { 
                progressTextObject.innerText = "Error: " + (error.message || error); 
            }
        }
    }
    logInterval = setInterval(pollStatus, seconds * 1000); pollStatus(); 
}

function updateLogResampling(project, key, progressBarObject, progressTextObject, seconds){
    activeProject = project; isRunning = true;
    // Prevent multiple polling intervals
    if (logInterval !== null) { clearInterval(logInterval); logInterval = null; }
    const pollStatus = async () => {
        if (activeProject !== project) { clearInterval(logInterval); logInterval = null; return; }
        try {
            const content = { projectName: project, key: key };
            const statusRes = await jsonLoader('check_calibration', content);
            // Update progress
            if (typeof statusRes.progress === 'number') {
                progressBarObject.value = Math.max(0, Math.min(100, statusRes.progress));
            }
            // Update message
            if (statusRes.message) { progressTextObject.innerText = statusRes.message; }
            if (statusRes.status === "finished" || statusRes.status === "stopped") {
                progressBarObject.value = statusRes.progress; isRunning = false;
                progressTextObject.innerText = statusRes.message || 'Resampling completed.';
                clearInterval(logInterval); logInterval = null;
            }
            if (statusRes.status === "failed") {
                progressTextObject.innerText = statusRes.message || 'Resampling failed.';
                isRunning = false; clearInterval(logInterval); logInterval = null;
            }
            obj.calibrationLog.value = statusRes.his;
        } catch (error) { 
            alert("Polling error: " + (error.message || error)); isRunning = false;
            clearInterval(logInterval); logInterval = null; progressTextObject.innerText = 'Polling error.';
        }
    }
    pollStatus(); logInterval = setInterval(pollStatus, seconds * 1000);
}

function updateLogSimulation(hydProject, key, logObject, progressBarObject, progressTextObject, seconds, logFile){
    activeProject = hydProject;
    logInterval = setInterval(async () => {
        if (activeProject !== hydProject) { clearInterval(logInterval); logInterval = null; }
        try {
            const content = { projectName: hydProject, key: key };
            const statusRes = await jsonLoader('check_sim_status_calibration', content);
            progressTextObject.innerText = statusRes.message; progressBarObject.value = statusRes.progress;
            if (statusRes.status !== "running") {
                logObject.value += statusRes.message;
                if (logInterval) { clearInterval(logInterval); logInterval = null; }
            }
            const res = await fetch(`/calibration_log_tail/${hydProject}?offset=${lastOffsetHYD}&log_file=${logFile}`);
            if (!res.ok) return;
            const data = await res.json();
            for (const line of data.lines) { logObject.value += line + "\n"; }
            lastOffsetHYD = data.offset;
        } catch (error) { clearInterval(logInterval); logInterval = null; }
    }, seconds * 1000);
}

async function sensitivityRender(container, data, key) {
    if (!data || data.length === 0) { return; }
    const parameters = Object.keys(data[0]).filter(k => k !== key);
    const variables = [...parameters, key];
    const sensitivityColumns = 2, rowHeight = 300;
    const sensitivityRows = Math.ceil(parameters.length / sensitivityColumns);
    const traces = [], z = [], text = [];
    variables.forEach(v1 => {
        const row = [], textRow = [];
        variables.forEach(v2 => {
            const pairs = data
                .map(row => [Number(row[v1]), Number(row[v2])])
                .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
            const x = pairs.map(p => p[0]), y = pairs.map(p => p[1]);
            const corr = pearsonCorrelation(x, y);
            row.push(corr);
            textRow.push(Number.isFinite(corr) ? corr.toFixed(3) : "");
        });
        z.push(row); text.push(textRow);
    });
    // Reverse Y direction so first variable is at the top
    const zReversed = z.slice().reverse();
    const textReversed = text.slice().reverse();
    const yReversed = variables.slice().reverse();
    traces.push({
        type: "heatmap", z: zReversed, text: textReversed, texttemplate: "%{text}",
        x: variables, y: yReversed, xaxis: "x", yaxis: "y",
        zmin: -1, zmax: 1, colorscale: "RdBu", textfont: { size: 13 }, colorbar: { title: "Pearson" },
        hovertemplate: "%{x} vs %{y}<br>" + "r = %{z:.3f}" +  "<extra></extra>"
    });
    // Sensitivity plots
    parameters.forEach((parameter, index) => {
        const validData = data
            .map(row => ({ x: Number(row[parameter]), y: Number(row[key]) }))
            .filter(row => Number.isFinite(row.x) && Number.isFinite(row.y));
        if (validData.length === 0) { return; }
        const x = validData.map(d => d.x), y = validData.map(d => d.y);
        const row = Math.floor(index / 2), col = index % 2;
        const subplotIndex = row * 2 + col + 2;
        const xAxisName = subplotIndex === 1 ? "x" : `x${subplotIndex}`;
        const yAxisName = subplotIndex === 1 ? "y" : `y${subplotIndex}`;
        traces.push({
            type: "scatter", x: x, y: y, mode: "markers", xaxis: xAxisName,
            yaxis: yAxisName, marker: { size: 8, opacity: 0.7 }, 
            name: parameter, showlegend: false,
            hovertemplate: `${parameter}: %{x}<br>` + `${key}: %{y:.4f}` + `<extra></extra>`
        });
        if (validData.length >= 2) {
            const meanX = x.reduce((a, b) => a + b, 0) / x.length;
            const meanY = y.reduce((a, b) => a + b, 0) / y.length;
            let numerator = 0, denominator = 0;
            for (let i = 0; i < x.length; i++) {
                numerator += (x[i] - meanX) * (y[i] - meanY);
                denominator += (x[i] - meanX) ** 2;
            }
            if (denominator !== 0) {
                const slope = numerator / denominator;
                const intercept = meanY - slope * meanX;
                const minX = Math.min(...x), maxX = Math.max(...x);
                traces.push({
                    type: "scatter", x: [minX, maxX],
                    y: [slope * minX + intercept, slope * maxX + intercept],
                    mode: "lines", xaxis: xAxisName, yaxis: yAxisName,
                    line: { dash: "dash", width: 2 },
                    hoverinfo: "skip", showlegend: false
                });
            }
        }
    });
    const layout = {
        height: Math.max(500, sensitivityRows * rowHeight),
        margin: { l: 20, r: 30, t: 80, b: 60 },
        title: {
            text: "Correlation Analysis",
            font: { size: 24, weight: "bold" }
        },
        xaxis: {
            domain: [0.00, 0.28], tickangle: 0, 
            automargin: true, showgrid: false, fixedrange: true
        },
        yaxis: {
            domain: [0.20, 0.80], tickangle: -90,
            automargin: true, showgrid: false, fixedrange: true
        }, annotations: []
    };
    parameters.forEach((parameter, index) => {
        const validData = data
            .map(row => ({ x: Number(row[parameter]), y: Number(row[key])}))
            .filter(row => Number.isFinite(row.x) && Number.isFinite(row.y));
        if (validData.length === 0) { return; }
        const x = validData.map(d => d.x), y = validData.map(d => d.y);
        const r = pearsonCorrelation(x, y);
        const row = Math.floor(index / 2), col = index % 2;
        const subplotIndex = row * 2 + col + 2;
        const xAxisName = subplotIndex === 1 ? "xaxis" : `xaxis${subplotIndex}`;
        const yAxisName = subplotIndex === 1 ? "yaxis" : `yaxis${subplotIndex}`;
        const xDomain = col === 0 ? [0.34, 0.64] : [0.70, 1.00], gap = 0.21;
        const plotHeight = (1 - gap * (sensitivityRows - 1)) / sensitivityRows;
        const yTop = 1 - row * (plotHeight + gap), yBottom = yTop - plotHeight;
        layout[xAxisName] = {
            domain: xDomain,
            anchor: subplotIndex === 2 ? "y2" : `y${subplotIndex}`,
            title: {text: `<b>${parameter}</b>`, font: { size: 13 }, standoff: 0 },
            tickangle: 0, automargin: true, showgrid: true,
            zeroline: true, showline: true, ticks: "outside"
        };
        layout[yAxisName] = {
            domain: [ yBottom, yTop ],
            anchor: subplotIndex === 2 ? "x2" : `x${subplotIndex}`,
            title: {text: `<b>${key}</b>`, font: { size: 13 }, standoff: 0 },
            tickangle: -90, tickpadding: 0, ticklen: 3, 
            automargin: true, showgrid: true
        };
        const annotationX = col === 0 ? 0.495 : 0.85;
        layout.annotations.push({
            x: annotationX, y: yTop - 0.015, xref: "paper", yref: "paper",
            xanchor: "center", yanchor: "bottom",
            text:
                `<b>${parameter} vs ${key}</b><br>` +
                `<span style="font-size:12px">` +
                `Pearson r = ${r.toFixed(3)}</span>`,
            showarrow: false, font: { size: 15 }
        });
    });
    Plotly.newPlot(
        container, traces, layout, { responsive: true, displaylogo: false }
    );
}

function pearsonCorrelation(x, y) {
    if (x.length !== y.length || x.length < 2) { return NaN; }
    const meanX = x.reduce((a, b) => a + b, 0) / x.length;
    const meanY = y.reduce((a, b) => a + b, 0) / y.length;
    let numerator = 0, denominatorX = 0, denominatorY = 0;
    for (let i = 0; i < x.length; i++) {
        const dx = x[i] - meanX, dy = y[i] - meanY;
        numerator += dx * dy;
        denominatorX += dx * dx;
        denominatorY += dy * dy;
    }
    const denominator = Math.sqrt(denominatorX * denominatorY);
    if (denominator === 0) { return NaN; }
    return numerator/Math.sqrt(denominatorX * denominatorY);
}

// ======================================================================
// Kiểm tra điều kiện ban đầu (trước khi tạo các kịch bản hiệu chỉnh)
// ======================================================================
function esc(v) {
    return String(v ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function overrideRowHtml(row = ['', '', '']) {
    return `<tr>${row.map((v, i) => `<td><input type="text" value="${esc(v)}"
        placeholder="${['MDU key', 'Value', 'Note'][i]}"></td>`).join('')}</tr>`;
}

function fillOverrides(rows) {
    const tbody = obj.overridesTable.querySelector('tbody');
    tbody.innerHTML = (rows && rows.length ? rows : [['', '', '']]).map(overrideRowHtml).join('');
}

function getOverrides() {
    return [...obj.overridesTable.querySelectorAll('tbody tr')]
        .map(tr => [...tr.querySelectorAll('input')].map(i => i.value.trim()))
        .filter(r => r[0] !== '');
}

function mergeSuggestions(suggestions) {
    // Giữ các dòng hiện có, thêm/ghi đè bằng gợi ý (người dùng có thể sửa hoặc xoá giá trị)
    const rows = getOverrides(), idx = new Map(rows.map((r, i) => [r[0].toLowerCase(), i]));
    suggestions.forEach(s => {
        const r = [s.key, s.value, s.note || ''];
        if (idx.has(s.key.toLowerCase())) rows[idx.get(s.key.toLowerCase())] = r; else rows.push(r);
    });
    fillOverrides(rows);
}

function renderInitialCheck(c) {
    const m = c.model, o = c.obs, fmt = (v, d = 2) => (v === null || v === undefined) ? '–' : Number(v).toFixed(d);
    let html = `<b>Model</b>: ${esc(m.start)} → ${esc(m.end)} · InitialTemperature = ${fmt(m.initial_temperature)} °C ·
        Kmx = ${m.kmx} (Layertype ${esc(m.layertype)}) · DtUser = ${m.dtuser ?? '–'} s · AngLat/AngLon = ${m.anglat ?? '–'}/${m.anglon ?? '–'}<br>
        Secchidepth = ${m.secchidepth ?? '–'} · Stanton = ${m.stanton ?? '–'} · Dalton = ${m.dalton ?? '–'} ·
        Cdbreakpoints = ${esc(m.cdbreakpoints)} (Windspeedbreakpoints = ${esc(m.windspeedbreakpoints)})`;
    if (o) {
        html += `<br><b>First observed profile</b>: ${esc(o.time)} (${o.gap_days} days after start) ·
            mean ${fmt(o.mean)} °C · range ${fmt(o.min)}–${fmt(o.max)} °C`;
        const ex = c.existing_run ? new Map(c.existing_run.rows.map(r => [r[0], r])) : null;
        html += `<table><tr><th>Depth (m)</th><th>Obs (°C)</th>${ex ? `<th>Run ${esc(c.existing_run.his)} (°C)</th><th>Diff</th>` : ''}</tr>
            ${o.profile.map(([d, t]) => {
                const r = ex?.get(d); const diff = r?.[3];
                return `<tr><td>${d}</td><td>${fmt(t)}</td>${ex ? `<td>${fmt(r?.[2])}</td>
                    <td class="${diff > 0 ? 'pos' : 'neg'}">${diff == null ? '–' : (diff > 0 ? '+' : '') + fmt(diff)}</td>` : ''}</tr>`;
            }).join('')}</table>`;
    }
    if (c.warnings.length) html += `<b>Findings</b><ul>${c.warnings.map(w => `<li class="warn">${esc(w)}</li>`).join('')}</ul>`;
    if (c.suggestions.length) html += `<b>Suggested overrides</b> were added to the table below – review, then “Save Overrides”.`;
    obj.initResult.innerHTML = html;
}

function renderBaseline(content) {
    const fmt = (v, d = 2) => (v === null || v === undefined) ? '–' : Number(v).toFixed(d);
    const runs = Object.entries(content);
    const depths = runs[0][1].rows.map(r => r.depth);
    let html = `<b>Baseline evaluation</b> (bias = sim − obs)<table><tr><th>Depth</th>${runs.map(([n, r]) =>
        `<th>${n} RMSE</th><th>${n} Bias</th>`).join('')}</tr>`;
    depths.forEach((d, i) => {
        html += `<tr><td>${d}</td>${runs.map(([, r]) => { const x = r.rows[i];
            return `<td>${fmt(x.rmse)}</td><td class="${x.bias > 0 ? 'pos' : 'neg'}">${x.bias == null ? '–' : (x.bias > 0 ? '+' : '') + fmt(x.bias)}</td>`; }).join('')}</tr>`;
    });
    html += `<tr><td><b>All</b></td>${runs.map(([, r]) => `<td><b>${fmt(r.rmse, 3)}</b></td><td></td>`).join('')}</tr></table>`;
    const b = content.baseline?.rows.map(r => r.bias).filter(v => v != null) || [];
    if (b.length && Math.abs(b.reduce((a, v) => a + v, 0) / b.length) > 1)
        html += `<p class="init-hint">The mean bias is still > 1 °C: the error is systematic. Fix the initial conditions/forcing
            first – the calibration parameters mainly change the shape of the profile, not a constant offset.</p>`;
    obj.initResult.insertAdjacentHTML('beforeend', html);
}

function initialCheckManager() {
    fillOverrides([]);
    const project = () => {
        const p = obj.projectName.value.trim();
        if (p === '') alert('Please select and open a HYD scenario first.');
        return p;
    };
    const obsRows = () => getDataFromTable(obj.observationTable, true).rows;
    obj.initObsBtn.addEventListener('click', () => { if (project()) obj.observationFile.click(); });
    obj.initCheckBtn.addEventListener('click', async () => {
        const currentProject = project(); if (!currentProject) return;
        const obsData = obsRows();
        if (obsData.length === 0) { alert('Please open an observation file first.'); return; }
        signalSender('showOverlay', 'Checking initial conditions against the observations. Please wait...');
        const data = await jsonLoader('initial_check_calibration', {
            projectName: currentProject, obsData: obsData, station: obj.initStation.value, timeZone: getLastTimeZone()
        });
        signalSender('hideOverlay');
        if (data.status === 'error') { alert(data.message); return; }
        const c = data.content;
        if (c.stations?.length && obj.initStation.options.length <= 1) {
            obj.initStation.innerHTML = `<option value="">-- Station --</option>` +
                c.stations.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
        }
        if (c.overrides?.length && getOverrides().length === 0) fillOverrides(c.overrides);
        renderInitialCheck(c);
        mergeSuggestions(c.suggestions || []);
    });
    obj.addOverrideBtn.addEventListener('click', () => {
        obj.overridesTable.querySelector('tbody').insertAdjacentHTML('beforeend', overrideRowHtml());
    });
    obj.saveOverrideBtn.addEventListener('click', async () => {
        const currentProject = project(); if (!currentProject) return;
        const data = await jsonLoader('save_overrides_calibration', {
            projectName: currentProject, rows: getOverrides(), timeZone: getLastTimeZone()
        });
        alert(data.message);
        // TStart/TStop có thể đã đổi -> cập nhật thời gian mô phỏng
        if (data.status === 'ok') await openProject(obj.projectName, obj.simulationStartDate, obj.simulationEndDate, 'calibration');
    });
    obj.baselineBtn.addEventListener('click', async () => {
        const currentProject = project(); if (!currentProject) return;
        if (isRunning) { alert('A process is running. Please wait until it finishes.'); return; }
        const created = await jsonLoader('create_baseline_calibration', { projectName: currentProject });
        if (created.status === 'error') { alert(created.message); return; }
        const key = 'calibration', logFile = 'log_baseline.txt';
        obj.calibrationLog.value = ''; obj.progressBar.value = 0; obj.progressContainer.style.display = 'flex';
        const start = await jsonLoader('start_sim_calibration', {
            projectName: currentProject, key: key, samplingName: 'baseline', logFile: logFile
        });
        if (start.status === 'error') { alert(start.message); return; }
        obj.progressText.innerText = 'Running the baseline simulation...';
        updateLogSimulation(currentProject, key, obj.calibrationLog, obj.progressBar, obj.progressText, 5, logFile);
    });
    obj.baselineEvalBtn.addEventListener('click', async () => {
        const currentProject = project(); if (!currentProject) return;
        const station = obj.initStation.value || obj.stationSelector.value;
        if (!station) { alert('Please select a station.'); return; }
        const obsData = obsRows();
        if (obsData.length === 0) { alert('Please open an observation file first.'); return; }
        signalSender('showOverlay', 'Evaluating the baseline run. Please wait...');
        const data = await jsonLoader('evaluate_baseline_calibration', {
            projectName: currentProject, station: station, obsData: obsData,
            depthSelection: obj.depthSelection.value, timeZone: getLastTimeZone()
        });
        signalSender('hideOverlay');
        if (data.status === 'error') { alert(data.message); return; }
        renderBaseline(data.content);
    });
}