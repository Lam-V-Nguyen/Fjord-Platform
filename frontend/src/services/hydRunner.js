import { getProjectList, jsonLoader } from "./commonFunctions.js";

const $ = (id) => document.getElementById(id);
const obj = { 
    scenarioSelector: $("options-scenario"), showCheckbox: $("show-checkbox"),
    progressbar: $("progressbar"), progressText: $("progress-text"), 
    runBtn: $("run-button"), textareaWrapper: $("form-row-textarea"), 
    infoArea: $("textarea"), checkboxContainer: $("checkbox-container")
};

let currentProject = null, logIntervalHYD = null, logFile = 'log_hyd.txt',
    lastOffsetHYD = 0, HYDRunning = false, activeHYDProject = null;

hydComponents();

async function hydComponents() {
    const respond = await getProjectList('', 'input');
    if (respond.length === 0) { obj.scenarioSelector.innerHTML = `<option value="">--- No projects found ---</option>`; return; }
    const options = respond.map(name => `<option value="${name}">${name}</option>`).join('');
    const defaultOption = `<option value="" selected>--- No selected ---</option>`;
    obj.scenarioSelector.innerHTML = defaultOption + options; obj.showCheckbox.checked = false;
    obj.showCheckbox.addEventListener('change', (e) => { 
        if (e.target.checked && (obj.progressText.innerText === 'No simulation running' 
            || obj.progressText.innerText === 'Simulation completed successfully.')) { obj.infoArea.value = ''; }
        obj.textareaWrapper.style.display = e.target.checked ? 'flex' : 'none';
    });
    obj.scenarioSelector.addEventListener('change', async() => {
        const projectName = obj.scenarioSelector.value;
        if (!projectName) {
            obj.checkboxContainer.style.display = 'none'; obj.progressText.innerText = ""; 
            obj.progressbar.value = 0; obj.textareaWrapper.style.display = 'none'; return;
        }
        // Work with HYD simulation
        if (logIntervalHYD) { clearInterval(logIntervalHYD); logIntervalHYD = null; }
        lastOffsetHYD = 0; activeHYDProject = projectName;
        const statusRes = await jsonLoader('check_sim_status_hyd', {projectName: projectName});
        if (statusRes.status === "running" || statusRes.status === "reorganizing") {
            const res = await fetch(`/sim_log_full/${projectName}?log_file=${logFile}`);
            if (res.ok) {
                const data = await res.json();
                obj.infoArea.value = data.content || ''; lastOffsetHYD = data.offset;
            }
            obj.showCheckbox.checked = true; HYDRunning = true;
            // Run hydrodynamics simulation and Update logs every 10 seconds
            updateLogHYD(projectName, obj.progressbar, obj.infoArea, 10, logFile);
        } else { obj.showCheckbox.checked = false; HYDRunning = false; }
        showStatus(statusRes?.status, statusRes?.message ?? '', statusRes?.progress ?? 0);
        obj.checkboxContainer.style.display = 'block'; obj.showCheckbox.dispatchEvent(new Event('change'));
    });
    // Run new simulation
    obj.runBtn.addEventListener('click', async () => {
        currentProject = obj.scenarioSelector.value;
        if (!currentProject || currentProject === '') { alert('Please select a scenario.'); return; }
        if (currentProject === 'demo') {
            if (!confirm(
                `1. Running the default scenario 'demo' is NOT recommended.\n` + 
                `2. This might cause unknown problems while visualizing outputs.\n` +
                `3. It is hightly recommended to make a clone of this scenario before running a simulation.\n\n` +
                `Are you sure you want to move on?`
            )) { return; }
        }
        // Check if HYD simulation is running
        if (HYDRunning) { alert("Detected an HYD simulation is running. Please wait until it finishes."); return; }
        const statusRes = await jsonLoader('check_sim_status_hyd', { projectName: currentProject });
        if (statusRes.status === "running") { alert("HYD simulation is running."); return; }
        const res = await jsonLoader('check_folder', {projectName: currentProject, folder: 'output', key: 'hyd'});
        if (res.status === "ok") { if (!confirm("Output exists. Re-run will overwrite it. Continue?")) return; }
        const start = await jsonLoader('start_sim_hyd', { projectName: currentProject, log_file: logFile });
        if (!start || start.status === "error") { showStatus('error', start?.message || 'Could not start the HYD simulation.'); alert(start?.message || 'Could not start the HYD simulation.'); return; }
        showStatus('running', 'Preparing data for the HYD simulation...', 0);
        obj.infoArea.value = ''; lastOffsetHYD = 0; HYDRunning = true;
        obj.showCheckbox.checked = true; obj.showCheckbox.dispatchEvent(new Event('change'));   // show the log
        updateLogHYD(currentProject, obj.progressbar, obj.infoArea, 3, logFile);
    });
}

// Progress text: red for errors, normal otherwise. pre-line keeps the line breaks of multi-line error messages.
function showStatus(status, message, progress) {
    const failed = status === 'failed' || status === 'error';
    obj.progressText.innerText = message || (failed ? 'Simulation failed.' : '');
    obj.progressText.style.color = failed ? 'red' : '';
    obj.progressText.style.whiteSpace = 'pre-line';
    if (progress !== undefined && progress !== null) obj.progressbar.value = progress;
}

async function fetchLogTail(hydProject, info, log_file) {
    const res = await fetch(`/sim_log_tail/${hydProject}?offset=${lastOffsetHYD}&log_file=${log_file}`);
    if (!res.ok) return;
    const data = await res.json();
    for (const line of data.lines) info.value += line + "\n";
    info.scrollTop = info.scrollHeight; lastOffsetHYD = data.offset;
}

// "small flow links discarded": OK -> repair the grid of the scenario, Cancel -> user has to create a new grid
async function handleSmallFlowLinks(project, message) {
    const repair = confirm(`${message}\n\n` +
        `========================o0o========================` +
        `OK: Remove the small flow links from the grid of this scenario automatically ` +
        `(neighbouring cells are merged and the grid file is replaced).\n` +
        `Cancel: Keep the grid. You then have to create a new grid instead.`);
    if (!repair) {
        showStatus('failed', 'HYD simulation failed: the grid has small flow links.\n' +
            'Create a new grid in the Grid tab (use "Check Grid" before saving) and use it in this scenario.');
        return;
    }
    showStatus('running', 'Removing small flow links from the grid...', 0);
    let res = null;
    try { res = await jsonLoader('fix_hyd_grid', { projectName: project }); } catch (error) { res = null; }
    if (!res || res.status !== 'ok') {
        const msg = `Could not repair the grid: ${res?.message || 'no response from the server'}\n` +
            'Please create a new grid in the Grid tab.';
        showStatus('failed', msg); alert(msg); return;
    }
    showStatus('finished', `Grid repaired. ${res.message}`, 0);
    obj.infoArea.value += `\n[GRID] ${res.message}\n`; obj.infoArea.scrollTop = obj.infoArea.scrollHeight;
    if (confirm(`Grid repaired.\n${res.message}\n\nRun the HYD simulation again now?`)) obj.runBtn.click();
}

function stopPolling() { if (logIntervalHYD) { clearInterval(logIntervalHYD); logIntervalHYD = null; } }

function updateLogHYD(hydProject, progress_bar, info, seconds, log_file){
    stopPolling(); activeHYDProject = hydProject;
    let busy = false, failures = 0;
    logIntervalHYD = setInterval(async () => {
        if (activeHYDProject !== hydProject) { stopPolling(); return; }
        if (busy) return;                                   // previous request still running
        busy = true;
        try {
            const statusRes = await jsonLoader('check_sim_status_hyd', { projectName: hydProject });
            if (!statusRes || !statusRes.status) throw new Error('Empty response from check_sim_status_hyd');
            failures = 0;
            const running = statusRes.status === "running" || statusRes.status === "reorganizing";
            if (running) { showStatus(statusRes.status, statusRes.message, statusRes.progress); }
            await fetchLogTail(hydProject, info, log_file).catch(() => {});   // log first, so it is complete
            if (!running) {
                stopPolling(); HYDRunning = false;
                const failed = statusRes.status === 'failed' || statusRes.status === 'error';
                showStatus(statusRes.status, statusRes.message, failed ? progress_bar.value : statusRes.progress);
                // the backend already wrote the final message into the log -> only add it when it is missing
                if (statusRes.message && !info.value.includes(statusRes.message)) {
                    info.value += "\n" + statusRes.message + "\n"; info.scrollTop = info.scrollHeight;
                }
                if (failed) {
                    obj.showCheckbox.checked = true; obj.showCheckbox.dispatchEvent(new Event('change'));
                    if (statusRes.error_code === 'small_flow_links') await handleSmallFlowLinks(hydProject, statusRes.message);
                    else alert(statusRes.message || 'HYD simulation failed.');
                }
            }
        } catch (error) {
            // Before: the interval was cleared silently and the text stayed "Preparing data ..."
            failures += 1;
            console.error('check_sim_status_hyd:', error);
            if (failures >= 3) {
                stopPolling(); HYDRunning = false;
                showStatus('error', `Lost contact with the server while checking the HYD simulation: ${error.message || error}`);
            }
        } finally { busy = false; }
    }, seconds * 1000);
}