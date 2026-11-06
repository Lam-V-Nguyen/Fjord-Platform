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
    iterationNumber: $('iteration-number'), iterationStart: $('iteration-start'), 
    iterationEnd: $('iteration-end'), calibrationLog: $('calibration-text'), 
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
    calibrationLogLoadBtn: $("calibration-load-log-btn"),
}

const KEYS = { iteration: 'iteration', simulation: 'calibration', surrogate: 'surrogate_model', optuna: 'optuna' };
const LOG_FILES = { simulation: 'log_calibration.txt', surrogate: 'log_surrogate.txt', optuna: 'log_optuna.txt' };
// Tasks that must not run at the same time (they read/write the same files)
const CONFLICTS = {
    [KEYS.iteration]: [KEYS.iteration, KEYS.simulation],
    [KEYS.simulation]: [KEYS.iteration, KEYS.simulation],
    [KEYS.surrogate]: [KEYS.surrogate, KEYS.optuna],
    [KEYS.optuna]: [KEYS.surrogate, KEYS.optuna],
};
// One poller, one running flag and one log offset per task
const state = { activeProject: null, running: {}, pollers: {}, offsets: {} };


setupTabs(document); await getProject();
await initParameterContainer(); calibrationManager();


async function getProject() { 
    const respond = await getProjectList('', 'input');
    await projectRender(obj.projectName, obj.projectList, respond);
    await projectRender(obj.comparisonSimProject, obj.comparisonSimList, respond);
}

function selectedProject(input = obj.projectName) {
    const project = input.value.trim();
    if (project === '') { alert('Please select a HYD scenario first.'); return null; }
    return project;
}
function isBlocked(key) { return (CONFLICTS[key] || [key]).some(k => state.running[k]); }
 
function stopPoller(key) {
    if (state.pollers[key]) { clearInterval(state.pollers[key]); delete state.pollers[key]; }
}
function startPoller(key, task, seconds) {
    stopPoller(key); let busy = false;
    const tick = async () => {
        if (busy) return;
        busy = true;
        try { await task(); } finally { busy = false; }
    };
    state.pollers[key] = setInterval(tick, seconds * 1000); tick();
}

async function appendLogTail(project, logFile, key, logObject) {
    const offset = state.offsets[key] ?? 0;
    try {
        const data = await jsonLoader('calibration_log_tail', { projectName: project, logFile, offset });
        if (!data || data.status === 'error') {
            console.warn('[calibration] log tail failed:', data?.message ?? data); return;
        }
        if (Array.isArray(data.lines) && data.lines.length > 0) {
            logObject.value += data.lines.join('\n') + '\n';
            logObject.scrollTop = logObject.scrollHeight;
        }
        state.offsets[key] = data.offset ?? offset;
    } catch (error) { console.warn('[calibration] log tail error:', error); }
}

async function loadFullLog(project, logFile, key, logObject) {
    try {
        const data = await jsonLoader('calibration_log_full', { projectName: project, logFile });
        if (!data || data.status === 'error') {
            console.warn('[calibration] log read failed:', data?.message ?? data); return;
        }
        logObject.value = data.content || '';
        logObject.scrollTop = logObject.scrollHeight;
        state.offsets[key] = data.offset ?? 0;
    } catch (error) { console.warn('[calibration] log read error:', error); }
}

function fmt(value, digits = 3) {
    if (value === null || value === undefined) return 'N/A';
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits) : 'N/A';
}

function pollResampling(project, seconds=5) {
    const key = KEYS.iteration; state.activeProject = project; state.running[key] = true;
    startPoller(key, async () => {
        if (state.activeProject !== project) { stopPoller(key); state.running[key] = false; return; }
        try {
            const statusRes = await jsonLoader('check_calibration', { projectName: project, key: key });
            if (typeof statusRes.progress === 'number') {
                obj.progressBar.value = Math.max(0, Math.min(100, statusRes.progress));
            }
            if (statusRes.message) { obj.progressText.innerText = statusRes.message; }
            if (typeof statusRes.his === 'string' && statusRes.his !== '') {
                obj.calibrationLog.value = statusRes.his;
                obj.calibrationLog.scrollTop = obj.calibrationLog.scrollHeight;
            }
            if (['finished', 'stopped', 'failed', 'not_started'].includes(statusRes.status)) {
                stopPoller(key); state.running[key] = false;
                if (statusRes.status === 'failed') {
                    obj.progressText.innerText = statusRes.message || 'Resampling failed.';
                    alert(statusRes.message || 'Resampling failed.');
                }
            }
        } catch (error) {
            stopPoller(key); state.running[key] = false; obj.progressText.innerText = 'Polling error.';
            alert('Polling error: ' + (error.message || error));
        }
    }, seconds);
}

async function initParameterContainer() {
    const container = document.querySelector('.parameter-container');
    container.innerHTML = `
        <div class="parameter-header">
            <div>Checked</div><div>Parameter</div><div>Lower</div><div>Upper</div>
        </div>
        ${calibrationParams.map(param => `
        <div class="parameter-row" data-param="${param.value}">
            <div><input class="param-check" type="checkbox" ${param.checked ? 'checked' : ''}></div>
            <div>${param.name}</div>
            <div><input class="param-lower" type="text" value="${param.lower}" placeholder="Lower"></div>
            <div><input class="param-upper" type="text" value="${param.upper}" placeholder="Upper"></div>
        </div>
        `).join('')}
    `;
    container.addEventListener('change', e => {
        const row = e.target.closest('.parameter-row');
        if (!row) return;
        const param = getCalibrationParam(row.dataset.param);
        if (!param) return;
        if (e.target.classList.contains('param-check')) { param.checked = e.target.checked; }
        if (e.target.classList.contains('param-lower')) { param.lower = parseFloat(e.target.value); }
        if (e.target.classList.contains('param-upper')) { param.upper = parseFloat(e.target.value); }
    });
}

async function openProject(projectObj, startObj, endObj, folder='') {
    const project = projectObj.value.trim();
    if (project === '') { alert('Please select a HYD scenario first.'); return false; }
    signalSender('showOverlay', 'Reading information from project "' + project + '".\nPlease wait...');
    try {
        const content = { projectName: project, folder: folder, timeZone: getLastTimeZone() };
        const data = await jsonLoader('calibration_project', content);
        if (data.status === 'error') { alert(data.message); return false; }
        startObj.value = data.content['start']; endObj.value = data.content['end'];
        return true;
    } finally { signalSender('hideOverlay'); }
}

function calibrationManager() {
    obj.projectBtn.addEventListener('click', async () => {
        openProject(obj.projectName, obj.simulationStartDate, obj.simulationEndDate, 'calibration');
    });
    obj.iterationBtn.addEventListener('click', async () => {
        const project = selectedProject(); if (!project) return;
        const iterationNumber = parseInt(obj.iterationNumber.value);
        if (isNaN(iterationNumber) || iterationNumber <= 0) { 
            alert('Please specify the number of resampling.'); return; 
        }
        // Get checked parameters and their bounds
        const checkedParams = calibrationParams.filter(p => p.checked);
        if (checkedParams.length === 0) { 
            alert('Please specify at least one parameter to be calibrated.'); return; 
        }
        const invalid = checkedParams.filter(p => {
            const lower = Number(p.lower), upper = Number(p.upper);
            return !Number.isFinite(lower) || !Number.isFinite(upper) || lower >= upper;
        });
        if (invalid.length > 0) {
            alert('Invalid bounds (lower must be smaller than upper):\n' + invalid.map(p => p.name).join(', '));
            return;
        }
        if (isBlocked(KEYS.iteration)) { alert('Resampling or simulation is currently running.'); return; }
        const confirmed = confirm(
            'Creating new resamples removes the previous scenarios, summary, ' +
            'surrogate model and optimization results.\n\nContinue?'
        ); if (!confirmed) return;
        const params_in = checkedParams.map(p => [p.value, Number(p.lower), Number(p.upper)]);
        obj.progressText.innerText = 'Iteration is in progress. Please wait...'; 
        obj.progressContainer.style.display = 'flex'; obj.progressBar.value = 0; 
        obj.calibrationLog.value = '';         
        const content = {
            projectName: project, key: KEYS.iteration, 
            iterationNumber: iterationNumber, params: params_in
        };
        try {
            const start = await jsonLoader('start_calibration_iteration', content);
            if (start.status === 'failed' || start.status === 'error') {
                obj.progressBar.value = 0;
                obj.progressText.innerText = start.message || 'Failed to start iteration.';
                alert(start.message || 'Failed to start iteration.'); return;
            }
            if (start.status === 'running') {
                obj.progressBar.value = start.progress || 0;
                obj.progressText.innerText = start.message || 'Iteration is running.';
            }
            pollResampling(project);
        } catch (error) { 
            obj.progressText.innerText = 'Failed to start iteration.';
            alert(`Iteration is failed: ${error.message}`);
        }
    });
    obj.iterationNumber.addEventListener('input', (e) => {
        const value = e.target.value; if (isNaN(value)) return; 
        obj.iterationEnd.value = value;
    });
    obj.runSimulationBtn.addEventListener('click', async () => {
        const project = selectedProject(); if (!project) return;
        const key = KEYS.simulation, logFile = LOG_FILES.simulation;
        const start = parseInt(obj.iterationStart.value), end = parseInt(obj.iterationEnd.value);
        if (isNaN(start) || start <= 0 || isNaN(end) || end <= 0) { 
            alert('Please recheck iteration.'); return; 
        }
        if (isBlocked(key)) { 
            alert('Resampling or simulation is currently running.\nPlease wait until it finishes.'); return; 
        }
        const confirmed = confirm(
            'Running a series of the simulations will take long time to finish.\nPlease be patient.'
        ); if (!confirmed) return;
        obj.calibrationLog.value = ''; obj.progressBar.value = 0; obj.progressContainer.style.display = 'flex';
        try {
            const statusRes = await jsonLoader('check_sim_status_calibration', { projectName: project, key });
            if (statusRes.status === 'running') {
                // Re-attach to a simulation that is already running
                await loadFullLog(project, logFile, key, obj.calibrationLog);
                obj.progressText.innerText = statusRes.message; obj.progressBar.value = statusRes.progress;
                pollSimulation(project, logFile); alert('HYD simulation is running.'); return;
            }
            state.offsets[key] = 0;
            const content = { projectName: project, key: key, start: start, end: end, logFile: logFile }
            const res = await jsonLoader('start_sim_calibration', content);
            if (res.status === 'error') { alert(res.message); return; }
            obj.progressText.innerText = res.status === 'running' ? res.message : 'Preparing data for the HYD simulation...';
            pollSimulation(project, logFile);
        } catch (error) {
            obj.progressText.innerText = 'Failed to start simulation.';
            alert(`Simulation failed: ${error.message}`);
        }
    });
    obj.calibrationLogLoadBtn.addEventListener('click', async () => {
        const project = selectedProject(); if (!project) return;
        const key = KEYS.simulation, logFile = LOG_FILES.simulation; obj.calibrationLog.value = '';
        try {
            const content = { projectName: project, folder: key, logFile: logFile }
            const res = await jsonLoader('load_log_calibration', content);
            if (res.status === 'error') { alert(res.message); return; }
            obj.calibrationLog.value = res.content;
        } catch (error) { alert(`Load calibration log failed: ${error.message}`); }
    });
    obj.observationBtn.addEventListener('click', () => {
        if (!selectedProject()) return; obj.observationFile.click();
    });
    obj.observationFile.addEventListener('change', async (e) => {
        const file = e.target.files[0]; if (!file) return;
        e.target.value = '';
        const project = selectedProject(); if (!project) return;
        const simStart = obj.simulationStartDate.value, simEnd = obj.simulationEndDate.value;
        if (simStart === '' || simEnd === '') { alert('Please add the start/end of the simulation.'); return; }
        const formData = new FormData(); formData.append('file', file);
        formData.append('projectName', project);
        formData.append('simStart', simStart); formData.append('simEnd', simEnd);
        signalSender('showOverlay', 'Reading observation data. Please wait...');
        try {
            const response = await fetch('/obs_calibration_upload', { method: 'POST', body: formData });
            const data = await response.json();
            if (data.status === 'error') { alert(data.message); return; }
            fillTable(data.content.data, obj.observationTable);
            obj.observationName.value = file.name;
            obj.observationStartDate.value = data.content['start'];
            obj.observationEndDate.value = data.content['end'];
        } catch (error) { alert(`Uploading observation data failed: ${error.message}`); 
        } finally { signalSender('hideOverlay'); }
    });
    obj.extractStationBtn.addEventListener('click', async () => {
        const project = selectedProject(); if (!project) return;
        signalSender('showOverlay', 'Getting station data from simulation "' + project + '".\nPlease wait...');
        try {
            const data = await jsonLoader('get_stations_calibration', { projectName: project });
            if (data.status === 'error') { alert(data.message); return; }
            const defaultValue = `<option value="">-- Select a station --</option>`;
            const options = data.content.map(s => `<option value="${s}">${s}</option>`).join('');
            obj.stationSelector.innerHTML = defaultValue + options;
        } finally { signalSender('hideOverlay'); }
    });
    obj.summaryBtn.addEventListener('click', async () => {
        const project = selectedProject(); if (!project) return;
        const simStart = obj.simulationStartDate.value, simEnd = obj.simulationEndDate.value;
        if (simStart === '' || simEnd === '') { alert('Please add the start/end of HYD scenario.'); return; }
        const obsStart = obj.observationStartDate.value, obsEnd = obj.observationEndDate.value;
        if (obsStart === '' || obsEnd === '') { alert('Please add the start/end of observation.'); return; }
        const station = obj.stationSelector.value.trim();
        if (station === '') { alert('Please select a station first.'); return; }
        const depthSelection = obj.depthSelection.value.trim();
        if (depthSelection === '') { alert('Please specify the depth selection in the tab "Settings".'); return; }
        const observationData = getDataFromTable(obj.observationTable, true).rows;
        if (observationData.length === 0) { alert('Please upload observation data first.'); return; }
        if (state.running[KEYS.simulation]) { alert('Simulation is still running. Please wait until it finishes.'); return; }
        signalSender('showOverlay', 'Summarizing information from different iterations for station "' + station + '".\nPlease wait...');
        try {
            const content = {
                projectName: project, simStart, simEnd, obsStart, obsEnd, station, depthSelection,
                obsData: observationData, targetValue: obj.criteriaTarget.value,
                weightValue: obj.weightSelector.value, timeZone: getLastTimeZone()
            };
            const data = await jsonLoader('summarize_calibration', content); alert(data.message);
        } finally { signalSender('hideOverlay'); }
    });
    obj.correlationBtn.addEventListener('click', async () => {    
        const project = selectedProject(); if (!project) return;
        const respond = await jsonLoader('correlation_calibration', { projectName: project });
        if (respond.status === 'error') {
            obj.sensitivityObject.style.display = 'none';
            alert(respond.message); return;
        }
        obj.sensitivityObject.style.display = 'block';
        await sensitivityRender(obj.sensitivityContainer, respond.content, respond.key);
    });
    obj.surrogateBtn.addEventListener('click', async () => {
        const project = selectedProject(); if (!project) return;
        const key = KEYS.surrogate, logFile = LOG_FILES.surrogate;
        if (isBlocked(key)) { alert('Surrogate model or optimization is currently running.'); return; }
        obj.optimizationLogContainer.style.display = 'flex';
        obj.optimizationChartContainer.style.display = 'none';
        obj.optimizationLog.value = ''; state.offsets[key] = 0;
        const content = { projectName: project, model: obj.surrogateSelector.value.trim(), key, logFile };
        try {
            const start = await jsonLoader('start_optimization_calibration', content);
            if (start.status === 'error') { alert(start.message); return; }
            pollOptimization({
                project, key, logFile, logObject: obj.optimizationLog,
                onFinish: async () => { alert('Building surrogate model completed.'); }
            });
        } catch (error) { alert(`Starting surrogate model failed: ${error.message}`); }
    });
    obj.optimizationBtn.addEventListener('click', async () => {
        const project = selectedProject(); if (!project) return;
        const iterationNumber = parseInt(obj.optimizationIterations.value);
        if (isNaN(iterationNumber) || iterationNumber <= 0) { alert('Please specify the number of iterations.'); return; }
        const key = KEYS.optuna, logFile = LOG_FILES.optuna;
        if (isBlocked(key)) { alert('Surrogate model or optimization is currently running.'); return; }
        obj.optimizationProgressText.innerText = 'Optimization is in progress. Please wait...';
        obj.optimizationProgressBarContainer.style.display = 'flex';
        obj.optimizationProgressBar.value = 0;
        obj.optimizationLogContainer.style.display = 'flex';
        obj.optimizationChartContainer.style.display = 'none';
        obj.optimizationLog.value = ''; state.offsets[key] = 0;
        obj.optimizationResults.style.display = 'none';
        const content = {
            projectName: project, iterations: iterationNumber,
            direction: obj.optimizationDirection.value, key, logFile
        };
        try {
            const start = await jsonLoader('start_optimization_optuna', content);
            if (start.status === 'error') { alert(start.message); return; }
            if (start.direction && obj.optimizationDirection.value !== start.direction) {
                obj.optimizationDirection.value = start.direction;
            }
            pollOptimization({
                project, key, logFile, logObject: obj.optimizationLog,
                progressBar: obj.optimizationProgressBar, progressText: obj.optimizationProgressText
            });
        } catch (error) {
            obj.optimizationProgressText.innerText = 'Failed to start optimization.';
            alert(`Optimization failed: ${error.message}`);
        }
    });
    obj.optimizationPlotBtn.addEventListener('click', async () => {
        const project = selectedProject(); if (!project) return;
        obj.optimizationLogContainer.style.display = 'none';
        obj.optimizationChartContainer.style.display = 'block';
        signalSender('showOverlay', 'Getting data for plotting. Please wait...');
        let response;
        try { response = await jsonLoader('plot_optuna', { projectName: project }); }
        finally { signalSender('hideOverlay'); }
        if (response.status === 'error') {
            obj.optimizationChartContainer.style.display = 'none'; alert(response.message); return;
        }
        plotOptuna(obj.optimizationContainer, response.content, response.importance, response.target, response.direction);
    });
    obj.optimizationSaveBtn.addEventListener('click', async () => {
        const container = document.querySelector('.parameter-optimal');
        if (container === null) return;
        const rows = container.querySelectorAll('.parameter-row-optimal');
        const params = [...rows].map(row => ({
            name: row.children[0].textContent, abbreviation: row.dataset.param,
            value: row.children[2].querySelector('input').value.trim()
        }));
        if (params.length === 0) { alert('No optimal parameters found.'); return; }
        const invalid = params.filter(p => p.value === '' || !Number.isFinite(Number(p.value)));
        if (invalid.length > 0) { alert('Invalid value for: ' + invalid.map(p => p.name).join(', ')); return; }
        const project = selectedProject(); if (!project) return;
        const newName = prompt(
            '     - Please specify a name for the new scenario.\n' +
            '     - Optimal parameters will be assigned to this scenario.\n' +
            '     - It is recommended to run this scenario.'
        );
        if (newName === null) return;
        if (nameChecker(newName)) { alert('Name of clone scenario is invalid.'); return; }
        signalSender('showOverlay', `Creating scenario '${newName}'. Please wait...`);
        try {
            const data = await jsonLoader('save_scenario_calibration',
                { oldProject: project, newProject: newName, params });
            alert(data.message);
        } finally { signalSender('hideOverlay'); }
    });
    obj.comparisonSimBtn.addEventListener('click', async () => {
        const project = selectedProject(obj.comparisonSimProject); if (!project) return;
        const loaded = await openProject(obj.comparisonSimProject, obj.simStartDate, obj.simEndDate);
        if (!loaded) return;
        signalSender('showOverlay', 'Getting station data from simulation "' + project + '".\nPlease wait...');
        try {
            const data = await jsonLoader('get_stations_comparison', { projectName: project });
            if (data.status === 'error') { alert(data.message); return; }
            const defaultValue = `<option value="">-- Select a station --</option>`;
            const options = data.content.map(s => `<option value="${s}">${s}</option>`).join('');
            obj.stationComparison.innerHTML = defaultValue + options;
        } finally { signalSender('hideOverlay'); }
    });
    obj.comparisonObsBtn.addEventListener('click', async () => obj.comparisonObsFile.click());
    obj.comparisonObsFile.addEventListener('change', async (e) => {
        const file = e.target.files[0]; e.target.value = '';
        if (!file) return;
        const simStart = obj.simStartDate.value, simEnd = obj.simEndDate.value;
        if (simStart === '' || simEnd === '') { alert('Please add the start/end of the simulation.'); return; }
        const formData = new FormData(); formData.append('file', file);
        formData.append('simStart', simStart); formData.append('simEnd', simEnd);
        signalSender('showOverlay', 'Reading observation data. Please wait...');
        try {
            const response = await fetch('/obs_comparison_upload', { method: 'POST', body: formData });
            const data = await response.json();
            if (data.status === 'error') { alert(`Uploading observation data failed: ${data.message}`); return; }
            fillTable(data.content.data, obj.obsTable); obj.comparisonObsName.value = file.name;
            obj.obsStartDate.value = data.content['start']; obj.obsEndDate.value = data.content['end'];
        } catch (error) { alert(`Uploading observation data failed: ${error.message}`); }
        finally { signalSender('hideOverlay'); }
    });
    obj.comparisonPlot.addEventListener('click', async () => {
        const project = selectedProject(obj.comparisonSimProject); if (!project) return;
        const simStart = obj.simStartDate.value, simEnd = obj.simEndDate.value;
        if (simStart === '' || simEnd === '') { alert('Please add the start/end of HYD simulation.'); return; }
        const obsStart = obj.obsStartDate.value, obsEnd = obj.obsEndDate.value;
        if (obsStart === '' || obsEnd === '') { alert('Please add the start/end of the observation.'); return; }
        const depthSelection = obj.depthComparison.value.trim();
        if (depthSelection === '') { alert('Please specify the depth to compare.'); return; }
        const stationName = obj.stationComparison.value;
        if (stationName === '') { alert('Please specify the station in the simulation.'); return; }
        const obsData = getDataFromTable(obj.obsTable, true).rows;
        if (obsData.length === 0) { alert('Please upload observation data first.'); return; }
        signalSender('showOverlay', 'Preparing data for comparison. Please wait...');
        let data;
        try {
            data = await jsonLoader('comparison_plot', {
                projectName: project, timeZone: getLastTimeZone(), simStart, simEnd,
                obsStart, obsEnd, depthSelection, obsData, station: stationName
            });
        } finally { signalSender('hideOverlay'); }
        if (data.status === 'error') { alert(data.message); return; }
        plotComparison(obj.comparisonContainer, data.content);
        obj.plotContainer.style.display = 'block';
    });
}

function pollSimulation(project, logFile, seconds=5) {
    const key = KEYS.simulation;
    state.activeProject = project; state.running[key] = true;
    startPoller(key, async () => {
        if (state.activeProject !== project) { stopPoller(key); state.running[key] = false; return; }
        try {
            const statusRes = await jsonLoader('check_sim_status_calibration', { projectName: project, key });
            await appendLogTail(project, logFile, key, obj.calibrationLog);
            obj.progressText.innerText = statusRes.message;
            obj.progressBar.value = Number(statusRes.progress) || 0;
            if (statusRes.status !== 'running') {
                stopPoller(key); state.running[key] = false;
                if (statusRes.message) { obj.calibrationLog.value += statusRes.message + '\n'; }
                obj.calibrationLog.scrollTop = obj.calibrationLog.scrollHeight;
            }
        } catch (error) {
            stopPoller(key); state.running[key] = false;
            obj.progressText.innerText = 'Polling error: ' + (error.message || error);
        }
    }, seconds);
}

function pollOptimization({ project, key, logFile, logObject, progressBar = null,
    progressText = null, seconds = 2, onFinish = null }) {
    state.activeProject = project; state.running[key] = true;
    startPoller(key, async () => {
        if (state.activeProject !== project) { stopPoller(key); state.running[key] = false; return; }
        try {
            const statusRes = await jsonLoader('check_optimization_status_calibration', { projectName: project, key });
            await appendLogTail(project, logFile, key, logObject);
            if (progressText) { progressText.innerText = statusRes.message; }
            if (progressBar) { progressBar.value = Number(statusRes.progress) || 0; }
            if (statusRes.status === 'running') return;
            stopPoller(key); state.running[key] = false;
            if (statusRes.message) { logObject.value += `\n${statusRes.message}\n`; }
            logObject.scrollTop = logObject.scrollHeight;
            const bestParams = statusRes.best_params;
            if (bestParams && typeof bestParams === 'object' && Object.keys(bestParams).length > 0) {
                renderOptimalParams(bestParams);
            }
            if (statusRes.status === 'finished') { if (onFinish) await onFinish(); }
            else { alert(statusRes.message || 'Optimization failed.'); }
        } catch (error) {
            stopPoller(key); state.running[key] = false;
            obj.optimizationResults.style.display = 'none';
            if (progressText) { progressText.innerText = 'Error: ' + (error.message || error); }
            alert('Error: ' + (error.message || error));
        }
    }, seconds);
}

function renderOptimalParams(bestParams) {
    const container = document.querySelector('.parameter-optimal');
    if (!container) return;
    obj.optimizationResults.style.display = 'block';
    container.innerHTML = `
        <div class="parameter-header-optimal">
            <div>Parameter</div><div>Abbreviation</div><div>Value</div>
        </div>
        ${Object.entries(bestParams).map(([key, value]) => {
            const param = getCalibrationParam(key);
            const name = param ? param.name : key;
            return `
                <div class="parameter-row-optimal" data-param="${key}">
                    <div>${name}</div><div>${key}</div>
                    <div><input type="text" value="${value}"></div>
                </div>
            `;
        }).join('')}
    `;
}

function plotComparison(container, data) {
    const dfObs = data.obs, minMax = data.min_max || [], depths = data.depth;
    // Each simulation: calibrated (selected project) and, if available, the original one
    const runs = [{
        label: `Calibrated (${data.project ?? 'scenario'})`, short: 'Calibrated',
        sim: data.sim, metrics: data.metrics || {}, merge: data.merge || {}, rmse: data.rmse,
        color: '#1f77b4', dash: 'solid', symbol: 'circle'
    }];
    if (data.base && Array.isArray(data.base.sim)) {
        runs.unshift({
            label: `Original (${data.base.project})`, short: 'Original',
            sim: data.base.sim, metrics: data.base.metrics || {}, merge: data.base.merge || {}, rmse: data.base.rmse,
            color: '#7f7f7f', dash: 'dash', symbol: 'circle-open'
        });
    }
    if (!container || !Array.isArray(dfObs) || !Array.isArray(depths) || depths.length === 0 ||
        !runs.some(r => Array.isArray(r.sim) && r.sim.length > 0)) {
        if (container) { container.innerHTML = '<p>No simulation or observation data available.</p>'; }
        return;
    }
    const margin = { l: 65, r: 15, t: 80, b: 50 };
    const axisTitle = (text, standoff) => ({ text: `<b>${text}</b>`, standoff, font: { size: 16, family: 'Arial', weight: 700 } });
    const axisStyle = { linecolor: 'black', ticks: 'outside', ticklen: 6, tickwidth: 1, tickcolor: 'black' };
    const config = { responsive: true, displaylogo: false };
    container.innerHTML = `<div class="comparison-grid"></div>`;
    const chartGrid = container.querySelector('.comparison-grid');
    // Temperature time series per depth
    depths.forEach((d, idx) => {
        const chart = document.createElement('div');
        chart.id = `comparison-${idx}`; chart.className = 'comparison-chart';
        chartGrid.appendChild(chart);
        const obsCol = `obs_${d}`, simCol = `sim_${d}`, traces = [];
        // Observation
        const ox = [], oy = [];
        dfObs.forEach(row => {
            const raw = row[obsCol];
            if (raw === null || raw === undefined || (typeof raw === 'string' && raw.trim() === '')) return;
            const time = stringToUTC(row.Time), value = Number(raw);
            if (time && Number.isFinite(value)) { ox.push(time); oy.push(value); }
        });
        if (ox.length > 0) {
            traces.push({
                x: ox, y: oy, type: 'scatter', mode: 'lines+markers', name: 'Observation',
                line: { color: '#d62728', width: 1.4 }, marker: { size: 3 }, opacity: 0.85,
                hovertemplate: 'Observation: %{y:.3f} °C<extra></extra>'
            });
        }
        // Simulations
        runs.forEach(run => {
            const x = [], y = [];
            (run.sim || []).forEach(row => {
                const raw = row[simCol];
                if (raw === null || raw === undefined) return;
                const time = stringToUTC(row.Time), value = Number(raw);
                if (time && Number.isFinite(value)) { x.push(time); y.push(value); }
            });
            if (x.length === 0) return;
            traces.push({
                x, y, type: 'scatter', mode: 'lines', name: run.label, opacity: 0.9,
                line: { color: run.color, width: 1.6, dash: run.dash },
                hovertemplate: `${run.short}: %{y:.3f} °C<extra></extra>`
            });
        });
        const metricText = runs.map(run => {
            const m = run.metrics[String(d)] || [];
            return `${run.short}: RMSE = ${fmt(m[0])}°C, R² = ${fmt(m[1])}`;
        }).join('<br>');
        const layout = {
            title: { text: `<b>Temperature Comparison - Station: ${data.station}<br>Water Depth: ${d}m</b>`, font: { size: 15, weight: 'bold' } },
            xaxis: { title: axisTitle('Time', 15), type: 'date', showgrid: true, gridcolor: 'rgba(0,0,0,0.15)', linewidth: 1, ...axisStyle },
            yaxis: { title: axisTitle('Temperature (°C)', 10), showgrid: true, gridcolor: 'rgba(0,0,0,0.15)', ...axisStyle },
            legend: { x: 0.03, y: 0.95, xanchor: 'left', yanchor: 'top', bgcolor: 'rgba(255,255,255,0.65)',
                bordercolor: 'rgba(128,128,128,0.4)', borderwidth: 1, font: { size: 12 } },
            annotations: [{
                x: 0.98, y: 0.04, xref: 'paper', yref: 'paper', xanchor: 'right', yanchor: 'bottom',
                text: metricText, showarrow: false, align: 'right', font: { size: 11 },
                bgcolor: 'rgba(255,255,255,0.75)', bordercolor: 'rgba(128,128,128,0.4)', borderwidth: 1
            }],
            margin, autosize: true, hovermode: 'x unified'
        };
        Plotly.newPlot(chart, traces, layout, config).then(() => {
            requestAnimationFrame(() => { Plotly.Plots.resize(chart); });
        });
    });
    // Parity plot: calibrated = filled markers, original = open markers
    const parityChart = document.createElement('div');
    parityChart.className = 'comparison-chart';
    chartGrid.appendChild(parityChart);
    const traces = [], minValue = minMax[0], maxValue = minMax[1];
    const depthColors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'];
    runs.forEach(run => {
        depths.forEach((depth, index) => {
            const rows = run.merge[String(depth)];
            if (!Array.isArray(rows) || rows.length === 0) return;
            const obsCol = `obs_${depth}`, simCol = `sim_${depth}`, x = [], y = [];
            rows.forEach(row => {
                const o = Number(row[obsCol]), s = Number(row[simCol]);
                if (row[obsCol] !== null && row[simCol] !== null && Number.isFinite(o) && Number.isFinite(s)) { x.push(o); y.push(s); }
            });
            if (x.length === 0) return;
            traces.push({
                x, y, type: 'scatter', mode: 'markers', name: `${run.short} - ${depth} m`,
                legendgroup: run.short,
                marker: { color: depthColors[index % depthColors.length], size: 6, symbol: run.symbol,
                    opacity: run.short === 'Original' ? 0.6 : 0.5 }
            });
        });
    });
    const hasRange = Number.isFinite(minValue) && Number.isFinite(maxValue);
    if (hasRange) {
        traces.push({
            x: [minValue, maxValue], y: [minValue, maxValue], type: 'scatter', mode: 'lines',
            name: 'Line 1:1', showlegend: false, line: { color: 'black', dash: 'dash', width: 1.5 }, hoverinfo: 'skip'
        });
    }
    const rmseText = runs.map(run => `${run.short}: ${fmt(run.rmse)}°C`).join(' | ');
    const layout = {
        title: { text: `<b>Simulation vs. Observation (Station: ${data.station})</b><br>Total RMSE - ${rmseText}`, font: { size: 15, weight: 'bold' } },
        xaxis: { title: axisTitle('Observation (°C)', 15), range: hasRange ? [minValue, maxValue] : undefined,
            showline: true, linewidth: 1, showgrid: true, ...axisStyle },
        yaxis: { title: axisTitle('Simulation (°C)', 10), range: hasRange ? [minValue, maxValue] : undefined,
            showline: true, linewidth: 1, ...axisStyle },
        legend: { x: 0.03, y: 0.95, xanchor: 'left', yanchor: 'top', bgcolor: 'rgba(228, 221, 221, 0.5)',
            bordercolor: 'gray', borderwidth: 1, font: { size: 12 } },
        margin, autosize: true, hovermode: 'closest'
    };
    Plotly.newPlot(parityChart, traces, layout, config).then(() => {
        requestAnimationFrame(() => { Plotly.Plots.resize(parityChart); });
    });
}

function plotOptuna(container, trials, importance, key, direction) {
    if (!container) return;
    if (!Array.isArray(trials) || trials.length === 0) {
        container.innerHTML = '<p>No optimal trial data available.</p>'; return;
    }
    const validTrials = trials.filter(trial =>
        trial.value !== null && trial.value !== undefined && Number.isFinite(Number(trial.value)));
    if (validTrials.length === 0) {
        container.innerHTML = '<p>No valid trial results available.</p>'; return;
    }
    // R² is maximized, RMSE is minimized
    const maximize = (direction || (key === 'R2' ? 'maximize' : 'minimize')) === 'maximize';
    const better = (a, b) => maximize ? a > b : a < b;
    const values = validTrials.map(trial => Number(trial.value));
    const trialNumbers = validTrials.map((trial, index) => trial.number ?? index);
    const bestValue = maximize ? Math.max(...values) : Math.min(...values);
    container.innerHTML = `
        <div class="optuna-chart-grid">
            <div class="optuna-chart" id="optuna-convergence"></div>
            <div class="optuna-chart" id="optuna-distribution"></div>
            <div class="optuna-chart" id="optuna-importance"></div>
            <div class="optuna-chart" id="optuna-sensitivity"></div>
        </div>
    `;
    let currentBest = maximize ? -Infinity : Infinity;
    const cumulativeBest = values.map(value => {
        if (better(value, currentBest)) currentBest = value;
        return currentBest;
    });
    let y1 = 'Best target', x2 = key || 'Target', name = key || 'Target', unit = '';
    if (key === 'RMSE') { y1 = 'Smallest RMSE (°C)'; x2 = 'RMSE (°C)'; name = 'RMSE'; unit = ' (°C)'; }
    else if (key === 'R2') { y1 = 'Highest R²'; x2 = 'R²'; name = 'R²'; unit = ''; }
    const axisStyle = {
        showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
        ticklen: 6, tickwidth: 1, tickcolor: 'black',
    };
    const axisTitle = (text, standoff) => ({
        text: `<b>${text}</b>`, standoff, font: { size: 16, family: 'Arial', weight: 700 }
    });
    const config = { responsive: true, displaylogo: false };
    // Convergence process
    Plotly.newPlot('optuna-convergence',
        [{ x: trialNumbers, y: cumulativeBest, type: 'scatter', mode: 'lines',
            name: 'Best target so far', line: { width: 2 } }],
        {
            title: { text: 'Optimal Convergence Process', font: { size: 20, weight: 'bold' } },
            xaxis: { title: axisTitle('Iterations (Trials)', 15), ...axisStyle, showgrid: true },
            yaxis: { title: axisTitle(y1, 10), ...axisStyle },
            margin: { l: 70, r: 30, t: 60, b: 60 }, hovermode: 'x unified'
        }, config);
    // Distribution
    Plotly.newPlot('optuna-distribution',
        [{ x: values, type: 'histogram', name: name, nbinsx: 25, opacity: 0.75 },
         { x: [bestValue, bestValue], y: [0, Math.max(1, values.length)],
            type: 'scatter', mode: 'lines', name: `Best: ${bestValue.toFixed(4)}`,
            line: { dash: 'dash', width: 2 } }],
        {
            title: { text: 'Target Distribution Across Trials', font: { size: 20, weight: 'bold' } },
            xaxis: { title: axisTitle(x2, 15), ...axisStyle, showgrid: true },
            yaxis: { title: axisTitle('Number of Trials', 10), ...axisStyle },
            legend: { x: 1, y: 1, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(255,255,255,0.7)', bordercolor: 'gray', borderwidth: 1 },
            margin: { l: 70, r: 30, t: 60, b: 60 }, bargap: 0.05
        }, config);
    // Parameter importance (sorted ascending -> most important is last)
    const importanceList = Array.isArray(importance) ? importance : [];
    const importanceNames = importanceList.map(item => item[0]);
    const importanceValues = importanceList.map(item => Number(item[1]));
    Plotly.newPlot('optuna-importance',
        [{
            x: importanceValues, y: importanceNames, type: 'bar', orientation: 'h',
            marker: { color: importanceValues, colorscale: 'Viridis', showscale: false },
            text: importanceValues.map(value => `${(value * 100).toFixed(1)}%`),
            textposition: 'outside', hovertemplate: '%{y}<br>Importance: %{x:.3f}<extra></extra>'
        }],
        {
            title: { text: 'Importance of Parameters', font: { size: 20, weight: 'bold' } },
            xaxis: { title: axisTitle('Relative Importance', 15),
                range: [0, Math.max(...importanceValues, 0.1) * 1.15], ...axisStyle, showgrid: true },
            yaxis: { title: { text: '', standoff: 10 },
                tickfont: { size: 17, family: 'Arial', weight: 700 }, ...axisStyle },
            margin: { l: 100, r: 60, t: 60, b: 60 }
        }, config);
    const mostImportantParam = importanceNames.length > 0 ? importanceNames[importanceNames.length - 1] : null;
    if (!mostImportantParam) {
        document.getElementById('optuna-sensitivity').innerHTML = '<p>No parameter data available.</p>';
        return;
    }
    const sensitivityColumn = `params_${mostImportantParam}`;
    const sensitivityData = validTrials
        .map(trial => ({ x: Number(trial[sensitivityColumn]), y: Number(trial.value), trial: trial.number }))
        .filter(item => Number.isFinite(item.x) && Number.isFinite(item.y));
    const bestTrial = validTrials.reduce(
        (best, trial) => better(Number(trial.value), Number(best.value)) ? trial : best, validTrials[0]);
    const bestParamValue = Number(bestTrial[sensitivityColumn]);
    const xLabel = calibrationParams.find(param => param.value === mostImportantParam)?.name ?? mostImportantParam;
    Plotly.newPlot('optuna-sensitivity',
        [{
            x: sensitivityData.map(item => item.x), y: sensitivityData.map(item => item.y),
            type: 'scatter', mode: 'markers', name: 'Trials',
            marker: { size: 6, opacity: 0.9, color: sensitivityData.map(item => item.y),
                colorscale: 'RdBu', reversescale: maximize, showscale: false },
            text: sensitivityData.map(item => `Trial ${item.trial}`),
            hovertemplate: `${xLabel}: %{x}<br>${name}: %{y:.4f}${unit}<br>%{text}<extra></extra>`
        },
        {
            x: [bestParamValue, bestParamValue], y: [Math.min(...values), Math.max(...values)],
            type: 'scatter', mode: 'lines', name: `Best: ${numberFormatter(bestParamValue)}`,
            line: { dash: 'dash', width: 2 }
        }],
        {
            title: { text: `Sensitivity Analysis: ${xLabel} - ${mostImportantParam}`, font: { size: 20, weight: 'bold' } },
            xaxis: { title: axisTitle(`${xLabel} (${mostImportantParam})`, 15), ...axisStyle, showgrid: true },
            yaxis: { title: axisTitle(x2, 10), ...axisStyle },
            legend: { x: 1, y: 1, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(255,255,255,0.7)', bordercolor: 'gray', borderwidth: 1 },
            margin: { l: 70, r: 30, t: 60, b: 60 }, hovermode: 'closest'
        }, config);
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
                .map(r => [Number(r[v1]), Number(r[v2])])
                .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
            const corr = pearsonCorrelation(pairs.map(p => p[0]), pairs.map(p => p[1]));
            row.push(corr);
            textRow.push(Number.isFinite(corr) ? corr.toFixed(3) : "");
        });
        z.push(row); text.push(textRow);
    });
    // Reverse Y direction so first variable is at the top
    traces.push({
        type: "heatmap", z: z.slice().reverse(), text: text.slice().reverse(), texttemplate: "%{text}",
        x: variables, y: variables.slice().reverse(), xaxis: "x", yaxis: "y",
        zmin: -1, zmax: 1, colorscale: "RdBu", textfont: { size: 13 }, colorbar: { title: "Pearson" },
        hovertemplate: "%{x} vs %{y}<br>r = %{z:.3f}<extra></extra>"
    });
    const layout = {
        height: Math.max(500, sensitivityRows * rowHeight),
        margin: { l: 20, r: 30, t: 80, b: 60 },
        title: { text: "Correlation Analysis", font: { size: 24, weight: "bold" } },
        xaxis: { domain: [0.00, 0.28], tickangle: 0, automargin: true, showgrid: false, fixedrange: true },
        yaxis: { domain: [0.20, 0.80], tickangle: -90, automargin: true, showgrid: false, fixedrange: true },
        annotations: []
    };
    const gap = 0.21, plotHeight = (1 - gap * (sensitivityRows - 1)) / sensitivityRows;
    parameters.forEach((parameter, index) => {
        const validData = data
            .map(r => ({ x: Number(r[parameter]), y: Number(r[key]) }))
            .filter(r => Number.isFinite(r.x) && Number.isFinite(r.y));
        if (validData.length === 0) { return; }
        const x = validData.map(d => d.x), y = validData.map(d => d.y);
        const row = Math.floor(index / 2), col = index % 2;
        const subplotIndex = row * 2 + col + 2;
        const xAxisName = `x${subplotIndex}`, yAxisName = `y${subplotIndex}`;
        traces.push({
            type: "scatter", x, y, mode: "markers", xaxis: xAxisName, yaxis: yAxisName,
            marker: { size: 8, opacity: 0.7 }, name: parameter, showlegend: false,
            hovertemplate: `${parameter}: %{x}<br>${key}: %{y:.4f}<extra></extra>`
        });
        // Linear trend line
        if (validData.length >= 2) {
            const meanX = x.reduce((a, b) => a + b, 0) / x.length;
            const meanY = y.reduce((a, b) => a + b, 0) / y.length;
            let numerator = 0, denominator = 0;
            for (let i = 0; i < x.length; i++) {
                numerator += (x[i] - meanX) * (y[i] - meanY);
                denominator += (x[i] - meanX) ** 2;
            }
            if (denominator !== 0) {
                const slope = numerator / denominator, intercept = meanY - slope * meanX;
                const minX = Math.min(...x), maxX = Math.max(...x);
                traces.push({
                    type: "scatter", x: [minX, maxX], y: [slope * minX + intercept, slope * maxX + intercept],
                    mode: "lines", xaxis: xAxisName, yaxis: yAxisName,
                    line: { dash: "dash", width: 2 }, hoverinfo: "skip", showlegend: false
                });
            }
        }
        const r = pearsonCorrelation(x, y);
        const xDomain = col === 0 ? [0.34, 0.64] : [0.70, 1.00];
        const yTop = 1 - row * (plotHeight + gap), yBottom = yTop - plotHeight;
        layout[`xaxis${subplotIndex}`] = {
            domain: xDomain, anchor: yAxisName,
            title: { text: `<b>${parameter}</b>`, font: { size: 13 }, standoff: 0 },
            tickangle: 0, automargin: true, showgrid: true, zeroline: true, showline: true, ticks: "outside"
        };
        layout[`yaxis${subplotIndex}`] = {
            domain: [yBottom, yTop], anchor: xAxisName,
            title: { text: `<b>${key}</b>`, font: { size: 13 }, standoff: 0 },
            tickangle: -90, tickpadding: 0, ticklen: 3, automargin: true, showgrid: true
        };
        layout.annotations.push({
            x: col === 0 ? 0.495 : 0.85, y: yTop - 0.015, xref: "paper", yref: "paper",
            xanchor: "center", yanchor: "bottom",
            text: `<b>${parameter} vs ${key}</b><br><span style="font-size:12px">Pearson r = ${fmt(r)}</span>`,
            showarrow: false, font: { size: 15 }
        });
    });
    Plotly.newPlot(container, traces, layout, { responsive: true, displaylogo: false });
}

function pearsonCorrelation(x, y) {
    if (x.length !== y.length || x.length < 2) { return NaN; }
    const meanX = x.reduce((a, b) => a + b, 0) / x.length;
    const meanY = y.reduce((a, b) => a + b, 0) / y.length;
    let numerator = 0, denominatorX = 0, denominatorY = 0;
    for (let i = 0; i < x.length; i++) {
        const dx = x[i] - meanX, dy = y[i] - meanY;
        numerator += dx * dy; denominatorX += dx * dx; denominatorY += dy * dy;
    }
    const denominator = Math.sqrt(denominatorX * denominatorY);
    return denominator === 0 ? NaN : numerator / denominator;
}