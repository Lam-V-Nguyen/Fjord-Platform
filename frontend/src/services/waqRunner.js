import { getProjectList, jsonLoader } from "./commonFunctions.js";

const $ = (id) => document.getElementById(id);
const obj = { 
    scenarioSelector: $("options-scenario"), labelWAQ: $("label-waq"), 
    waqSelector: $("options-waq"), progressbar: $("progressbar"),
    progressText: $("progress-text"), runBtn: $("run-button"),
    textareaWrapper: $("form-row-textarea"), showCheckbox: $("show-checkbox"),
    infoArea: $("textarea"), checkboxContainer: $("checkbox-container")
};

let currentProject = null, logIntervalWAQ = null, lastOffsetWAQ = 0,
    WAQRunning = false, activeWAQProject = null, logFile = 'log_waq.txt';
const RUNNING_STATES = ["preparing", "running", "reorganizing"];

waqComponents();

async function waqComponents() {
    const respond = await getProjectList('', 'input');
    if (!respond) { obj.scenarioSelector.innerHTML = `<option value="">--- No projects found ---</option>`; return; }
    const options = respond.map(name => `<option value="${name}">${name}</option>`).join('');
    const defaultOption = `<option value="" selected>--- No selected ---</option>`;
    obj.scenarioSelector.innerHTML = defaultOption + options;
    obj.showCheckbox.checked = false;
    obj.showCheckbox.addEventListener('change', (e) => { 
        if (e.target.checked && (obj.progressText.innerText === 'No simulation running' 
            || obj.progressText.innerText === 'Simulation completed successfully')) { obj.infoArea.value = ''; }
        obj.textareaWrapper.style.display = e.target.checked ? 'block' : 'none';
    });
    obj.scenarioSelector.addEventListener('change', async() => {
        const projectName = obj.scenarioSelector.value;
        if (!projectName || projectName === '') {
            obj.progressText.innerText = ""; obj.progressbar.value = 0;
            obj.waqSelector.value = ''; obj.waqSelector.innerHTML = defaultOption;
            return;
        }
        // Work with WAQ simulation
        if (logIntervalWAQ) { clearInterval(logIntervalWAQ); logIntervalWAQ = null; }
        lastOffsetWAQ = 0; activeWAQProject = `${projectName}_${obj.waqSelector.value}`; obj.infoArea.value = '';
        const data = await jsonLoader('select_project', {filename: projectName, key: 'getWAQs', folder_check: 'input'});
        if (!data || data.status === "error") { alert(data?.message || 'Could not load the WAQ scenarios.'); obj.scenarioSelector.value = ''; return; }
        obj.waqSelector.innerHTML = (data.content || []).map(name => `<option value="${name}">${name}</option>`).join('');
        await loadWAQStatus(projectName, obj.waqSelector.value);
    });
    // Another WAQ scenario: show its own status (and resume following it if it is running)
    obj.waqSelector.addEventListener('change', async() => {
        if (logIntervalWAQ) { clearInterval(logIntervalWAQ); logIntervalWAQ = null; }
        obj.infoArea.value = ''; lastOffsetWAQ = 0;
        const projectName = obj.scenarioSelector.value;
        if (projectName && obj.waqSelector.value) await loadWAQStatus(projectName, obj.waqSelector.value);
    });
    // Run new simulation
    obj.runBtn.addEventListener('click', async () => {
        currentProject = obj.scenarioSelector.value;
        if (!currentProject || currentProject === '') { alert('Please select a scenario.'); return; }
        // Check if WAQ simulation is running
        if (WAQRunning) { alert("Detected a WAQ simulation is running. Please wait until it finishes."); return; }
        const content = { projectName: currentProject, waqName: obj.waqSelector.value, logFile: logFile, key: 'waq' };
        if (!obj.waqSelector.value) { alert('Please select a WAQ scenario.'); return; }
        const statusRes = await jsonLoader('check_sim_status_waq', content);
        if (RUNNING_STATES.includes(statusRes?.status)) { alert("WAQ simulation is running."); return; }
        const res = await jsonLoader('check_folder', content);
        if (res?.status === "ok") { if (!confirm("Output exists. Re-run will overwrite it. Continue?")) return; }
        showStatus('preparing', 'Preparing data for the WAQ simulation...', 0);
        obj.infoArea.value = ''; lastOffsetWAQ = 0;
        const start = await jsonLoader('start_sim_waq', content);
        if (!start || start.status === "error" || start.status === "running") {
            const msg = start?.message || 'Could not start the WAQ simulation.';
            showStatus(start?.status === "running" ? 'running' : 'error', msg); alert(msg); return;
        }
        WAQRunning = true;
        obj.showCheckbox.checked = true; obj.showCheckbox.dispatchEvent(new Event('change'));   // show the log
        updateLogWAQ(currentProject, obj.waqSelector.value, obj.progressbar, obj.progressText, obj.infoArea, 3, logFile);
    });
}

// Status of one WAQ scenario; resumes following it when it is still running
async function loadWAQStatus(projectName, waqName) {
    activeWAQProject = `${projectName}_${waqName}`;
    let statusRes;
    try { statusRes = await jsonLoader('check_sim_status_waq', {projectName: projectName, waqName: waqName}); }
    catch (error) { statusRes = null; }
    if (!statusRes) { showStatus('error', 'Could not get the WAQ simulation status from the server.'); obj.checkboxContainer.style.display = 'block'; return; }
    if (RUNNING_STATES.includes(statusRes.status)) {
        const res = await fetch(`/sim_log_full/${projectName}?log_file=${logFile}`);
        if (res.ok) { const data = await res.json(); obj.infoArea.value = data.content || ''; lastOffsetWAQ = data.offset || 0; }
        obj.showCheckbox.checked = true; WAQRunning = true;
        updateLogWAQ(projectName, waqName, obj.progressbar, obj.progressText, obj.infoArea, 3, logFile, false);
    } else { obj.showCheckbox.checked = statusRes.status === 'failed'; WAQRunning = false; }
    showStatus(statusRes.status, statusRes.message, statusRes.progress);
    obj.checkboxContainer.style.display = 'block';
    obj.showCheckbox.dispatchEvent(new Event('change'));
}

// Progress text: red for errors. pre-line keeps the line breaks of multi-line error messages.
function showStatus(status, message, progress) {
    const failed = status === 'failed' || status === 'error';
    obj.progressText.innerText = message || (failed ? 'Simulation failed.' : '');
    obj.progressText.style.color = failed ? 'red' : '';
    obj.progressText.style.whiteSpace = 'pre-line';
    if (progress !== undefined && progress !== null) obj.progressbar.value = progress;
}

async function fetchLogTailWAQ(hydProject, info, log_file) {
    const res = await fetch(`/sim_log_tail/${hydProject}?offset=${lastOffsetWAQ}&log_file=${log_file}`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.lines.length > 0) { info.value += data.lines.join("\n") + "\n"; info.scrollTop = info.scrollHeight; }
    lastOffsetWAQ = data.offset;
}

function updateLogWAQ(hydProject, waqProject, progress_bar, progress_text, info, seconds, log_file, resetOffset = true) {
    if (logIntervalWAQ) { clearInterval(logIntervalWAQ); logIntervalWAQ = null; }
    activeWAQProject = `${hydProject}_${waqProject}`;
    if (resetOffset) lastOffsetWAQ = 0;        // false when the full log was just loaded (avoids duplicated lines)
    let busy = false, failures = 0;
    const stop = () => {
        if (logIntervalWAQ) { clearInterval(logIntervalWAQ); logIntervalWAQ = null; }
        WAQRunning = false;
    };
    logIntervalWAQ = setInterval(async () => {
        if (activeWAQProject !== `${hydProject}_${waqProject}`) { stop(); return; }
        if (busy) return;                                   // previous request still running
        busy = true;
        try {
            // Status first, log second: the backend writes the full error to the log BEFORE it sets "failed",
            // so the last log lines are never lost when polling stops
            const statusRes = await jsonLoader('check_sim_status_waq', {projectName: hydProject, waqName: waqProject});
            if (!statusRes || !statusRes.status) throw new Error('Empty response from check_sim_status_waq');
            failures = 0;
            await fetchLogTailWAQ(hydProject, info, log_file).catch(() => {});
            if (RUNNING_STATES.includes(statusRes.status)) {
                showStatus(statusRes.status, statusRes.message, statusRes.progress);
                return;
            }
            stop();
            const failed = statusRes.status === 'failed' || statusRes.status === 'error';
            showStatus(statusRes.status, statusRes.message, failed ? progress_bar.value : statusRes.progress);
            if (failed) {
                obj.showCheckbox.checked = true; obj.showCheckbox.dispatchEvent(new Event('change'));
                alert(statusRes.message || 'WAQ simulation failed.');
            }
        } catch (error) {
            // Before: one failed request stopped polling and replaced the whole log with the error
            failures += 1;
            console.error('check_sim_status_waq:', error);
            if (failures >= 3) {
                stop();
                showStatus('error', `Lost contact with the server while checking the WAQ simulation: ${error.message || error}`);
            }
        } finally { busy = false; }
    }, seconds * 1000);
}