import { setupTabs } from "./tabManager.js";
import { projectRender } from "./projectManager.js";
import { getProjectList, signalSender, jsonLoader, fillTable, 
    getDataFromTable, numberFormatter, nameChecker, stringToUTC
} from "./commonFunctions.js";
import { calibrationParams, getLastTimeZone } from "./constant.js";
import { 
    fillOverrides, esc, updateLogSimulation, initParameterContainer, isBusy
} from "./calibrationFunctions.js";


const $ = (id) => document.getElementById(id);
const obj = {
    projectList: $('project-list'), projectName: $('project-name'), projectBtn: $('project-btn'),
    simulationStartDate: $('sim-start-date'), simulationEndDate: $('sim-end-date'),
    observationBtn: $('observation-btn'), observationFile: $('observation-file'),
    observationName: $('observation-name'), observationTable: $('observation-table'), 
    observationStartDate: $('observation-start-date'), observationEndDate: $('observation-end-date'),
    initCheckBtn: $('init-check-btn'), initStation: $('init-station-select'),
    initResult: $('init-result'), overridesTable: $('overrides-table'),
    saveOverrideBtn: $('save-override-btn'), baselineBtn: $('baseline-btn'), 
    baselineEvalBtn: $('baseline-eval-btn'), depthSelection: $('depth-selection'),
    customParamKey: $('custom-param-key'), customParamBtn: $('custom-param-btn'),
    calibrationLog: $('calibration-text'), progressBar: $("progressbar"), 
    progressText: $("progress-text"), progressContainer: $("progressbar-container"),
    mduKeyBtn: $('mdu-keys-btn'), mduKeyTable: $('mdu-keys-table'), 
    
    





    
    iterationNumber: $('iteration-number'),  
    
    iterationBtn: $('iteration-btn'), 
    runSimulationBtn: $('run-simulation-btn'),  
    
    extractStationBtn: $('extract-station-btn'), 
    stationSelector: $('station-select'), 
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
    
}
const project = () => {
    const p = obj.projectName.value.trim();
    if (p === '') alert('Please select and open a HYD scenario first.');
    return p;
};

setupTabs(document); await getProject();
const container = document.querySelector('.parameter-container');
await initParameterContainer(container, obj.customParamKey, obj.customParamBtn); 
initialCheckManager(); calibrationManager(); 


async function getProject() { 
    const respond = await getProjectList('', 'input');
    await projectRender(obj.projectName, obj.projectList, respond);
    await projectRender(obj.comparisonSimProject, obj.comparisonSimList, respond);
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

function renderInitialCheck(c) {
    const m = c.model, o = c.obs, fmt = (v, d = 2) => (v === null || v === undefined) ? '–' : Number(v).toFixed(d);
    let html = `
        <div class="initial-check-content">
            <div class="initial-check-section">
                <b>Model</b>
                <div class="initial-check-details">
                    <div>Period: ${esc(m.start)} → ${esc(m.end)}</div>
                    <div>InitialTemperature = ${fmt(m.initial_temperature)}°C</div>
                    <div>Kmx = ${m.kmx} (Layertype ${esc(m.layertype)})</div>
                    <div>DtUser = ${m.dtuser ?? '-'}s</div>
                    <div>AngLat/AngLon = ${m.anglat ?? '-'}/${m.anglon ?? '-'}</div>
                    <div>Secchidepth = ${m.secchidepth ?? '-'}</div>
                    <div>Stanton = ${m.stanton ?? '-'}</div>
                    <div>Dalton = ${m.dalton ?? '-'}</div>
                    <div>Cdbreakpoints = ${esc(m.cdbreakpoints)}</div>
                    <div>Windspeedbreakpoints = ${esc(m.windspeedbreakpoints)}</div>
                </div>
            </div>
    `;
    if (o) {
        const ex = c.existing_run ? new Map(c.existing_run.rows.map(r => [r[0], r])) : null;
        html += `
            <div class="initial-check-section">
                <b style="margin-top: 10px;">First observed profile</b>
                ${esc(o.time)} (${o.gap_days} days after start) ·
                mean ${fmt(o.mean)}°C · range: ${fmt(o.min)} - ${fmt(o.max)}°C
                <div class="initial-check-table-wrapper">
                    <table class="initial-check-table">
                        <thead>
                            <tr>
                                <th>Depth (m)</th><th>Obs (°C)</th>
                                ${ex ? `
                                    <th>Run ${esc(c.existing_run.his)} (°C)</th><th>Diff</th>
                                ` : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${o.profile.map(([d, t]) => {
                                const r = ex?.get(d); const diff = r?.[3];
                                return `
                                    <tr>
                                        <td>${d}</td>
                                        <td>${fmt(t)}</td>
                                        ${ex ? `
                                            <td>${fmt(r?.[2])}</td>
                                            <td class="${diff > 0 ? 'pos' : 'neg'}">
                                                ${diff == null ? '-' : (diff > 0 ? '+' : '') + fmt(diff)}
                                            </td>
                                        ` : ''}
                                    </tr>
                                `;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }
    if (c.warnings.length) {
        html += `
            <div class="initial-check-section">
                <b>Findings</b>
                <ul>${c.warnings.map(w => `<li class="warn">${esc(w)}</li>`).join('')}</ul>
            </div>
        `;
    }
    if (c.suggestions.length) {
        html += `
            <div class="initial-check-section">
                <b>Suggested overrides</b> were added to the table below - review, then “Save Overrides”.
            </div>
        `;
    }
    html += '</div>'; obj.initResult.innerHTML = html;
}
function getOverrides() {
    return [...obj.overridesTable.querySelectorAll('tbody tr')]
        .map(tr => [...tr.querySelectorAll('input')].map(i => i.value.trim()))
        .filter(r => r[0] !== '');
}

function mergeSuggestions(suggestions) {
    const rows = getOverrides(), idx = new Map(rows.map((r, i) => [r[0].toLowerCase(), i]));
    suggestions.forEach(s => {
        const r = [s.key, s.value, s.note || ''];
        if (idx.has(s.key.toLowerCase())) rows[idx.get(s.key.toLowerCase())] = r; else rows.push(r);
    });
    fillOverrides(rows, obj.overridesTable);
}

function renderBaseline(content) {
    const fmt = (v, d = 2) => (v === null || v === undefined) ? '–' : Number(v).toFixed(d);
    const runs = Object.entries(content);
    const depths = runs[0][1].rows.map(r => r.depth);
    let html = `<br><br><b>Baseline evaluation</b> (bias = sim − obs)<table><tr><th>Depth</th>${runs.map(([n, r]) =>
        `<th>${n} RMSE</th><th>${n} Bias</th>`).join('')}</tr>`;
    depths.forEach((d, i) => {
        html += `<tr><td>${d}</td>${runs.map(([, r]) => { const x = r.rows[i];
            return `<td>${fmt(x.rmse)}</td><td class="${x.bias > 0 ? 'pos' : 'neg'}">${x.bias == null ? '–' : (x.bias > 0 ? '+' : '') + fmt(x.bias)}</td>`; }).join('')}</tr>`;
    });
    html += `<tr><td><b>All</b></td>${runs.map(([, r]) => `<td><b>${fmt(r.rmse, 3)}</b></td><td></td>`).join('')}</tr>`;
    html += `<tr><td>n</td>${runs.map(([, r]) => `<td colspan="2">${r.rows.map(x => x.n).join(' / ')}</td>`).join('')}</tr></table>`;
    runs.filter(([, r]) => r.note).forEach(([n, r]) => {
        html += `<p class="init-hint"><b>${esc(n)}: no observation could be matched</b> – ${esc(r.note)}</p>`;
    });
    const b = content.baseline?.rows.map(r => r.bias).filter(v => v != null) || [];
    if (b.length && Math.abs(b.reduce((a, v) => a + v, 0) / b.length) > 1)
        html += `<p class="init-hint">The mean bias is still > 1°C: the error is systematic. Fix the initial conditions/forcing
            first – the calibration parameters mainly change the shape of the profile, not a constant offset.</p>`;
    obj.initResult.insertAdjacentHTML('beforeend', html);
}

// Existing calibration data on the server (null when the check fails – the caller then just continues)
async function getDataStatus(projectName) {
    try {
        const res = await jsonLoader('calibration_data_status', { projectName });
        return res.status === 'ok' ? res.content : null;
    } catch (error) { console.warn('[calibration] data status:', error); return null; }
}

function initialCheckManager() {
    fillOverrides([], obj.overridesTable); fillOverrides([], obj.mduKeyTable);
    obj.initCheckBtn.addEventListener('click', async () => {
        const currentProject = project(); if (!currentProject) return;
        const obsData = getDataFromTable(obj.observationTable, true).rows;
        if (obsData.length === 0) { alert('Please open an observation file first.'); return; }
        signalSender('showOverlay', 'Checking initial conditions against the observations. Please wait...');
        const data = await jsonLoader('initial_check_calibration', {
            projectName: currentProject, obsData: obsData, 
            station: obj.initStation.value, timeZone: getLastTimeZone()
        }); signalSender('hideOverlay');
        if (data.status === 'error') { alert(data.message); return; }
        const c = data.content;
        if (c.stations?.length && obj.initStation.options.length <= 1) {
            obj.initStation.innerHTML = `<option value="">-- Station --</option>` +
                c.stations.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
        }
        if (c.overrides?.length && getOverrides().length === 0) fillOverrides(c.overrides, obj.overridesTable);
        renderInitialCheck(c); mergeSuggestions(c.suggestions || []);
    });
    obj.saveOverrideBtn.addEventListener('click', async () => {
        const currentProject = project(); if (!currentProject) return;
        const ov = (await getDataStatus(currentProject))?.overrides;
        if (ov?.exists) {
            const keys = ov.keys.slice(0, 10).join(', ') + (ov.keys.length > 10 ? ', ...' : '');
            const ok = confirm(`Overrides already exist for "${currentProject}"` +
                ` (${ov.count} key(s), saved ${ov.modified}):\n${keys}\n\n` +
                'Continue?\nThe saved overrides will be overwritten by the current table.');
            if (!ok) return;
        }
        const data = await jsonLoader('save_overrides_calibration', {
            projectName: currentProject, rows: getOverrides(), timeZone: getLastTimeZone()
        }); alert(data.message);
        if (data.status === 'ok') {
            await openProject(
                obj.projectName, obj.simulationStartDate, obj.simulationEndDate, 'calibration'
            );
        }
    });
    obj.baselineBtn.addEventListener('click', async () => {
        const currentProject = project(); if (!currentProject) return;
        if (isBusy()) { alert('A process is running. Please wait until it finishes.'); return; }
        // Ask the server: a run may have been started before a page reload or from another tab
        for (const [runKey, runLog, label] of [
            ['baseline', 'log_baseline.txt', 'The baseline simulation'],
            ['calibration', 'log_calibration.txt', 'The calibration simulations']
        ]) {
            const st = await jsonLoader('check_sim_status_calibration', { projectName: currentProject, key: runKey });
            if (st.status === 'running') {
                alert(`${label} of project "${currentProject}" is still running.\n` +
                    `${st.message || ''}\nPlease wait until it finishes.`);
                // Re-attach the log and progress bar to the running simulation
                obj.calibrationLog.value = ''; obj.progressContainer.style.display = 'flex';
                obj.progressText.innerText = st.message || ''; obj.progressBar.value = Number(st.progress) || 0;
                updateLogSimulation(
                    currentProject, runKey, obj.calibrationLog, obj.progressBar, obj.progressText, 5, runLog
                ); return;
            }
        }
        // Create a baseline
        const bl = (await getDataStatus(currentProject))?.baseline;
        if (bl?.exists || bl?.case_exists) {
            const what = bl.exists ? `Baseline results already exist for "${currentProject}" (${bl.his}, ${bl.modified}).`
                : `A baseline case already exists for "${currentProject}" (without results).`;
            if (!confirm(`${what}\n\nContinue?\nThe existing baseline will be deleted and overwritten.`)) return;
        }
        const created = await jsonLoader('create_baseline_calibration', { projectName: currentProject });
        if (created.status === 'error') { alert(created.message); return; }
        // Run baseline simulation
        const key = 'baseline', logFile = 'log_baseline.txt';
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
        const obsData = getDataFromTable(obj.observationTable, true).rows;
        if (obsData.length === 0) { alert('Please open an observation file first.'); return; }
        const depths = obj.depthSelection.value;
        if (depths === '') { alert('Please add depth layer to compare to observations.'); return; }
        signalSender('showOverlay', 'Evaluating the baseline run. Please wait...');
        const data = await jsonLoader('evaluate_baseline_calibration', {
            projectName: currentProject, station: station, obsData: obsData,
            depthSelection: depths, timeZone: getLastTimeZone()
        }); signalSender('hideOverlay');
        if (data.status === 'error') { alert(data.message); return; }
        renderBaseline(data.content); 
        alert("Computation complete. See final part in 'Initial Conditions Check' for more detailed."); 
    });
}

function calibrationManager() {
    obj.projectBtn.addEventListener('click', async () => {
        openProject(obj.projectName, obj.simulationStartDate, obj.simulationEndDate, 'calibration');
    });
    obj.observationBtn.addEventListener('click', () => { if (project()) obj.observationFile.click(); });
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
    obj.mduKeyBtn.addEventListener('click', async () => {
        const currentProject = project(); if (!currentProject) return;
        signalSender('showOverlay', 'Getting all keys from *.mdu file. Please wait...');
        const data = await jsonLoader('get_mdu_keys', {projectName: currentProject,}); 
        signalSender('hideOverlay'); 
        if (data.status === 'error') { alert(data.message); return; }
        fillTable(data.content, obj.mduKeyTable);
    });






    
    // obj.iterationBtn.addEventListener('click', async () => {
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     const iterationNumber = parseInt(obj.iterationNumber.value);
    //     if (isNaN(iterationNumber) || iterationNumber <= 0) { alert('Please specify the number of resampling.'); return; }
    //     // Get checked parameters and their bounds
    //     const checkedParams = calibrationParams.filter(p => p.checked);
    //     if (checkedParams.length === 0) { alert('Please specify at least one parameter to be calibrated.'); return; }
    //     for (const p of checkedParams) {
    //         if (!Number.isFinite(p.lower) || !Number.isFinite(p.upper) || p.lower >= p.upper) {
    //             alert(`'${p.name}': lower must be smaller than upper.`); return;
    //         }
    //         if (p.scale === 'log' && p.lower <= 0) { alert(`'${p.name}': log scale needs lower > 0.`); return; }
    //     }
    //     const params_in = checkedParams.map(p => [p.value, p.lower, p.upper, p.scale || 'linear']);
    //     if (isBusy()) { alert("Iteration is currently running."); return; }
    //     // Reset UI
    //     activeProject = currentProject; 
    //     obj.progressText.innerText = 'Iteration is in progress. Please wait...'; 
    //     obj.progressContainer.style.display = 'flex'; obj.progressBar.value = 0; 
    //     obj.calibrationLog.value = ''; const key = 'iteration';
    //     const content = { 
    //         projectName: currentProject, key: key, 
    //         iterationNumber: iterationNumber, params: params_in
    //     };
    //     try {
    //         const start = await jsonLoader('start_calibration_iteration', content);
    //         if (start.status === "running") {
    //             obj.progressBar.value = start.progress || 0;
    //             obj.progressText.innerText = start.message || 'Iteration is running.';
    //         }
    //         if (start.status === "failed") { 
    //             obj.progressBar.value = 0;
    //             obj.progressText.innerText = start.message || 'Failed to start iteration.';
    //             alert(start.message || 'Failed to start iteration.'); return; 
    //         }
    //         updateLogResampling(currentProject, key, obj.calibrationLog, obj.progressBar, obj.progressText, 1);
    //     } catch (error) { 
    //         obj.progressText.innerText = 'Failed to start iteration.';
    //         alert(`Iteration is failed: ${error.message}`);
    //     }
    // });
    // obj.runSimulationBtn.addEventListener('click', async () => {
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     // obj.resamplingName không tồn tại trong HTML -> lỗi TypeError; '' = các kịch bản hiệu chỉnh
    //     const samplingName = '', key = 'calibration', logFile = 'log_calibration.txt';
    //     if (isBusy()) { alert("Detected an HYD simulation is running. Please wait until it finishes."); return; }
    //     obj.calibrationLog.value = ''; obj.progressBar.value = 0;
    //     obj.progressContainer.style.display = 'flex';
    //     const checkContent = { projectName: currentProject, key: key };
    //     const statusRes = await jsonLoader('check_sim_status_calibration', checkContent);
    //     if (statusRes.status === "running") {
    //         const res = await fetch(`/calibration_log_full/${currentProject}?log_file=${logFile}`);
    //         if (res.ok) {
    //             const data = await res.json();
    //             obj.calibrationLog.value = data.content || ''; lastOffsetHYD = data.offset;
    //         }; 
    //         updateLogSimulation(
    //             currentProject, key, obj.calibrationLog, obj.progressBar, obj.progressText, 5, logFile
    //         );
    //         obj.progressText.innerText = statusRes.message; obj.progressBar.value = statusRes.progress;
    //         alert("HYD simulation is running."); return; 
    //     } 
    //     const content = { projectName: currentProject, key: key, samplingName: samplingName, logFile: logFile };
    //     const start = await jsonLoader('start_sim_calibration', content);
    //     if (start.status === "error") { alert(start.message); return; }
    //     obj.progressText.innerText = 'Preparing data for the HYD simulation...';
    //     updateLogSimulation(currentProject, key, obj.calibrationLog, obj.progressBar, obj.progressText, 5, logFile);
    // });
    
    
    // obj.extractStationBtn.addEventListener('click', async () => {
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     signalSender('showOverlay', 'Getting station data from simulation "' + currentProject + '".\nPlease wait...');
    //     const data = await jsonLoader('get_stations_calibration', { projectName: currentProject });
    //     signalSender('hideOverlay');
    //     if (data.status === "error") { alert(data.message); return; }
    //     // Add content to the selector
    //     const defaultValue = `<option value="">-- Select a station --</option>`;
    //     const options = data.content.map(s => `<option value="${s}">${s}</option>`).join(''); 
    //     obj.stationSelector.innerHTML = defaultValue + options;
    // });
    // obj.summaryBtn.addEventListener('click', async () => {
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     const simStart = obj.simulationStartDate.value; const simEnd = obj.simulationEndDate.value;
    //     if (simStart === '' || simEnd === '') { alert('Please add the start/end of HYD scenario.'); return; }
    //     const obsStart = obj.observationStartDate.value; const obsEnd = obj.observationEndDate.value;
    //     if (obsStart === '' || obsEnd === '') { alert('Please add the start/end of observation.'); return; }
    //     const station = obj.stationSelector.value.trim();
    //     if (station === '') { alert('Please select a station first.'); return; }
    //     const depthSelection = obj.depthSelection.value.trim();
    //     if (depthSelection === '') { alert('Please specify the depth selection in the tab "Settings".'); return; }
    //     const targetValue = obj.criteriaTarget.value;
    //     const weightValue = obj.weightSelector.value;
    //     const observationData = getDataFromTable(obj.observationTable, true).rows;
    //     if (observationData.length === 0) { alert('Please upload observation data first.'); return; }
    //     signalSender('showOverlay', 'Summarizing information from different iterations for station "' + station + '".\nPlease wait...');
    //     const content = { 
    //         projectName: currentProject, simStart: simStart, simEnd: simEnd, timeZone: getLastTimeZone(),
    //         obsStart: obsStart, obsEnd: obsEnd, station: station, depthSelection: depthSelection,
    //         obsData: observationData, targetValue: targetValue, weightValue: weightValue, timeZone: getLastTimeZone()
    //     };
    //     const data = await jsonLoader('summarize_calibration', content);
    //     signalSender('hideOverlay'); alert(data.message); 
    // });
    // obj.correlationBtn.addEventListener('click', async () => {    
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     const content = { projectName: currentProject};
    //     const respond = await jsonLoader('correlation_calibration', content);
    //     if (respond.status === "error") { 
    //         obj.sensitivityObject.style.display = 'none';
    //         alert(respond.message); return; 
    //     }
    //     obj.sensitivityObject.style.display = 'block';
    //     await sensitivityRender(obj.sensitivityContainer, respond.content, respond.key);
    // });
    // obj.surrogateBtn.addEventListener('click', async () => {
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     const model = obj.surrogateSelector.value.trim();
    //     const key = 'surrogate_model', logFile = 'log_surrogate.txt';
    //     if (isBusy()) { alert("Optimization is currently running."); return; }
    //     obj.optimizationLogContainer.style.display = 'flex';
    //     obj.optimizationChartContainer.style.display = 'none';
    //     obj.optimizationLog.value = ''; 
    //     const content = { projectName: currentProject, model: model, key: key, logFile: logFile };
    //     try {
    //         const start = await jsonLoader('start_optimization_calibration', content);
    //         if (start.status === "error") { 
    //             alert(start.message); return; 
    //         }
    //         updateLogOptimization(
    //             currentProject, key, obj.optimizationLog, null, obj.resultsObject, null, 1, logFile,
    //             async () => {alert('Building surrogate model completed.');}
    //         );
    //     } catch (error) { 
    //         alert(`Starting optimization failed: ${error.message}`);
    //     }
    // });
    // obj.optimizationBtn.addEventListener('click', async () => {
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     const iterationNumber = parseInt(obj.optimizationIterations.value);
    //     if (isNaN(iterationNumber) || iterationNumber <= 0) { alert('Please specify the number of iterations.'); return; }
    //     const direction = obj.optimizationDirection.value;
    //     if (isBusy()) { alert("Optimization is currently running."); return; }
    //     // Reset UI
    //     activeProject = currentProject;
    //     obj.optimizationProgressText.innerText = 'Iteration is in progress. Please wait...'; 
    //     obj.optimizationProgressBarContainer.style.display = 'flex'; 
    //     obj.optimizationProgressBar.value = 0; 
    //     obj.optimizationLogContainer.style.display = 'flex';
    //     obj.optimizationChartContainer.style.display = 'none';
    //     obj.optimizationLog.value = ''; 
    //     obj.optimizationResults.style.display = 'none';
    //     const key = 'optuna', logFile = 'log_optuna.txt';
    //     const content = { 
    //         projectName: currentProject, iterations: iterationNumber, 
    //         direction: direction, key: key, logFile: logFile
    //     };
    //     try {
    //         const start = await jsonLoader('start_optimization_optuna', content);
    //         if (start.status === "error") { 
    //             alert(start.message); return; 
    //         }
    //         updateLogOptimization(
    //             currentProject, key, obj.optimizationLog, obj.optimizationProgressBar, 
    //             obj.optimizationProgressText, 1, logFile
    //         );
    //     } catch (error) { 
    //         obj.optimizationProgressText.innerText = 'Failed to start optimization.';
    //         obj.optimizationResults.style.display = 'none';
    //         alert(`Optimization is failed: ${error.message}`);
    //     }
    // });
    // obj.optimizationPlotBtn.addEventListener('click', async () => {
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     obj.optimizationLogContainer.style.display = 'none';
    //     obj.optimizationChartContainer.style.display = 'block';
    //     signalSender('showOverlay', `Getting data for plotting. Please wait...`);
    //     const response = await jsonLoader('plot_optuna', { projectName: currentProject });
    //     signalSender('hideOverlay');
    //     if (response.status === "error") { 
    //         obj.optimizationLogContainer.style.display = 'none';
    //         obj.optimizationChartContainer.style.display = 'none';
    //         alert(response.message); return; 
    //     }
    //     plotOptuna(
    //         obj.optimizationContainer, response.content, response.importance, response.target
    //     );
    // });
    // obj.optimizationSaveBtn.addEventListener('click', async () => {
    //     const container = document.querySelector('.parameter-optimal');
    //     if (container === null) { return; }
    //     const rows = container.querySelectorAll('.parameter-row-optimal');
    //     const params = [...rows].map(row => ({
    //         value: row.dataset.param, name: row.children[0].textContent,
    //         abbreviation: row.children[1].textContent,
    //         value: row.children[2].querySelector('input').value
    //     }));
    //     if (params.length === 0) { alert('No optimal parameters found.'); return;}
    //     // Ask for a new name
    //     const newName = prompt(
    //         'Please specify a name for the new scenario.' +
    //         '\nOptimal parameters will be asigned for this scenario.' +
    //         '\nIt is recommened to run this scenario.'
    //     );
    //     // User clicked Cancel
    //     if (newName === null) { return; }
    //     if (nameChecker(newName)) { alert('Name of clone scenario is invalid.'); return; }
    //     const currentProject = obj.projectName.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     signalSender('showOverlay', `Creating scenario '${newName}'. Please wait...`);
    //     const content = { oldProject: currentProject, newProject: newName, params: params }
    //     const data = await jsonLoader('save_scenario_calibration', content);
    //     signalSender('hideOverlay'); alert(data.message);
    //     if (data.status === "error") { return; }
    // });
    // obj.comparisonSimBtn.addEventListener('click', async () => {
    //     const currentProject = obj.comparisonSimProject.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     openProject(obj.comparisonSimProject, obj.simStartDate, obj.simEndDate);
    //     signalSender('showOverlay', 'Getting station data from simulation "' + currentProject + '".\nPlease wait...');
    //     const data = await jsonLoader('get_stations_comparison', { projectName: currentProject });
    //     if (data.status === "error") { signalSender('hideOverlay'); alert(data.message); return; }
    //     await new Promise(resolve => requestAnimationFrame(resolve));
    //     // Add content to the selector
    //     const defaultValue = `<option value="">-- Select a station --</option>`;
    //     const options = data.content.map(s => `<option value="${s}">${s}</option>`).join(''); 
    //     obj.stationComparison.innerHTML = defaultValue + options; signalSender('hideOverlay');
    // });
    // obj.comparisonObsBtn.addEventListener('click', async () => obj.comparisonObsFile.click());
    // obj.comparisonObsFile.addEventListener('change', async (e) => {
    //     const simStart = obj.simStartDate.value; const simEnd = obj.simEndDate.value;
    //     if (simStart === '' || simEnd === '') { alert('Please add the start/end of the simulation.'); return; }
    //     const file = e.target.files[0]; if (!file) return;
    //     const formData = new FormData(); formData.append('file', file); 
    //     formData.append('simStart', simStart); formData.append('simEnd', simEnd);
    //     signalSender('showOverlay', 'Reading observation data. Please wait...');
    //     const response = await fetch('/obs_comparison_upload', { method: 'POST', body: formData });
    //     const data = await response.json(); signalSender('hideOverlay'); e.target.value = '';
    //     if (data.status === 'error') { 
    //         alert(`Uploading observation data failed: ${data.message}`); return; 
    //     }
    //     fillTable(data.content.data, obj.obsTable); obj.comparisonObsName.value = file.name;
    //     obj.obsStartDate.value = data.content['start']; obj.obsEndDate.value = data.content['end'];
    // });
    // obj.comparisonPlot.addEventListener('click', async () => {
    //     const currentProject = obj.comparisonSimProject.value.trim();
    //     if (currentProject === '') { alert('Please select a HYD scenario first.'); return; }
    //     const simStart = obj.simStartDate.value; const simEnd = obj.simEndDate.value;
    //     if (simStart === '' || simEnd === '') { alert('Please add the start/end of HYD simulation.'); return; }
    //     const obsStart = obj.obsStartDate.value; const obsEnd = obj.obsEndDate.value;
    //     if (obsStart === '' || obsEnd === '') { alert('Please add the start/end of the observation.'); return; }
    //     const depthSelection = obj.depthComparison.value.trim();
    //     if (depthSelection === '') { alert('Please specify the depth to compare.'); return; }
    //     const stationName = obj.stationComparison.value;
    //     if (stationName === '') { alert('Please specify the station in the simulation.'); return; }
    //     const obsData = getDataFromTable(obj.obsTable, true).rows;
    //     if (obsData.length === 0) { alert('Please upload observation data first.'); return; }
    //     signalSender('showOverlay', 'Preparing data for comparison. Please wait...');
    //     const content = { 
    //         projectName: currentProject, timeZone: getLastTimeZone(), simStart: simStart, 
    //         simEnd: simEnd, obsStart: obsStart, obsEnd: obsEnd, 
    //         depthSelection: depthSelection, obsData: obsData, station: stationName
    //     };
    //     const data = await jsonLoader('comparison_plot', content); signalSender('hideOverlay'); 
    //     if (data.status === "error") { alert(data.message); return; }
    //     plotComparison(obj.comparisonContainer, data.content);
    //     obj.plotContainer.style.display = 'block';
    // });
}