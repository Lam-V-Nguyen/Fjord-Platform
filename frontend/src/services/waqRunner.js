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
        if (data.status === "error") { alert(data.message); obj.scenarioSelector.value = ''; return; }
        obj.waqSelector.innerHTML = data.content.map(name => `<option value="${name}">${name}</option>`).join('');
        const statusRes = await jsonLoader('check_sim_status_waq', {projectName: projectName, waqName: obj.waqSelector.value});
        if (["preparing", "running", "reorganizing"].includes(statusRes.status)) {
            console.log(statusRes.message);
            const res = await fetch(`/sim_log_full/${projectName}?log_file=${logFile}`);
            if (res.ok) {
                const data = await res.json();
                obj.infoArea.value = data.content || ''; lastOffsetWAQ = data.offset;
            }
            obj.showCheckbox.checked = true; WAQRunning = true;
            updateLogWAQ(projectName, obj.waqSelector.value, obj.progressbar, obj.progressText, obj.infoArea, 3, logFile);
        } else { obj.showCheckbox.checked = false; WAQRunning = false; }
        obj.progressText.innerText = statusRes.message; 
        obj.progressbar.value = statusRes.progress;
        obj.checkboxContainer.style.display = 'block'; 
        obj.showCheckbox.dispatchEvent(new Event('change'));
    });
    obj.waqSelector.addEventListener('change', async() => { obj.infoArea.value = ''; });
    // Run new simulation
    obj.runBtn.addEventListener('click', async () => {
        currentProject = obj.scenarioSelector.value;
        if (!currentProject || currentProject === '') { alert('Please select a scenario.'); return; }
        // Check if WAQ simulation is running
        if (WAQRunning) { alert("Detected a WAQ simulation is running. Please wait until it finishes."); return; }
        const content = { projectName: currentProject, waqName: obj.waqSelector.value, logFile: logFile, key: 'waq' };
        const statusRes = await jsonLoader('check_sim_status_waq', content);
        if (statusRes.status === "running") { alert("WAQ simulation is running."); return; }
        const res = await jsonLoader('check_folder', content);
        if (res.status === "ok") { if (!confirm("Output exists. Re-run will overwrite it. Continue?")) return; }
        obj.progressText.innerText = 'Preparing data for the WAQ simulation...';
        obj.infoArea.value = ''; obj.progressbar.value = 0;
        const start = await jsonLoader('start_sim_waq', content);
        if (start.status === "error") { alert(start.message); return; }
        updateLogWAQ(currentProject, obj.waqSelector.value, obj.progressbar, obj.progressText, obj.infoArea, 3, logFile);
    });
}

function updateLogWAQ(hydProject, waqProject, progress_bar, progress_text, info, seconds, log_file) {
    if (logIntervalWAQ) { clearInterval(logIntervalWAQ); logIntervalWAQ = null; }
    activeWAQProject = `${hydProject}_${waqProject}`; lastOffsetWAQ = 0;
    const stop = () => {
        if (logIntervalWAQ) { clearInterval(logIntervalWAQ); logIntervalWAQ = null; }
        WAQRunning = false;
    };
    logIntervalWAQ = setInterval(async () => {
        if (activeWAQProject !== `${hydProject}_${waqProject}`) { stop(); return; }
        try {
            const res = await fetch(`/sim_log_tail/${hydProject}?offset=${lastOffsetWAQ}&log_file=${log_file}`);
            if (res.ok) {
                const data = await res.json();
                if (data.lines.length > 0) {
                    info.value += data.lines.join("\n") + "\n"; info.scrollTop = info.scrollHeight;
                }
                lastOffsetWAQ = data.offset;
            }
            const statusRes = await jsonLoader('check_sim_status_waq', {projectName: hydProject, waqName: waqProject});
            if (!["preparing", "running", "reorganizing"].includes(statusRes.status)) {
                info.value += "\n" + statusRes.message + "\n"; info.scrollTop = info.scrollHeight; stop();
            }
            progress_text.innerText = statusRes.message; progress_bar.value = statusRes.progress;
        } catch (error) { stop(); info.value = `Error: ${error.message}`; }
    }, seconds * 1000);
}