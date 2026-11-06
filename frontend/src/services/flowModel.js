import { setupTabs } from "./tabManager.js";
import { getUser, signalSender, getProjectList, jsonLoader, updateLog, nameChecker
} from "./commonFunctions.js";
import { projectRender } from "./projectManager.js";
import { getLastTimeZone } from "./constant.js";
import { isInvalidNumber } from "./flowFunctions.js";


const $ = (id) => document.getElementById(id);
const obj = {
    projectList: $('project-list'), projectName: $('project-name'), projectOpener: $('open-btn'),
    modelInterval: $('model-step'), modelStart: $('model-start'), modelEnd: $('model-end'),
    modelCheckBtn: $('model-check-btn'), modelLogText: $('model-log'), 
    modelLat: $('model-lat'), modelLon: $('model-lon'), modelRunBtn: $('model-run-btn')
}
const intervalOption = {'1h': 3600, '1d': 86400, '1w': 604800, '1ME': 2592000};

let currentProject;
const REQUIRED = {
    overland_flow: "flow direction and particle speed on land",
    overland_depth: "particle speed and the water depth layer on land",
    river_discharge: "particle speed in rivers",
    river_depth: "particle speed and the water depth layer in rivers",
};

setupTabs(document); await getProject(); 
setupMovementOutputGuard(); modelManager();


async function getProject() { 
    const userName = await getUser(); currentProject = userName.split('/').pop();
    const respond = await getProjectList(`${currentProject}/flows`, '');
    await projectRender(obj.projectName, obj.projectList, respond);
}


function positionNotice(box, notice) {
    const rect = box.getBoundingClientRect();
    // Temporarily show the notice so its dimensions can be measured.
    const wasHidden = notice.hidden; notice.hidden = false;
    const width = notice.offsetWidth, height = notice.offsetHeight;
    const gap = 8, margin = 12;
    // Align with the checkbox, but keep the notice inside the viewport.
    const left = Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin));
    // Prefer below the checkbox.
    let top = rect.bottom + gap;
    // If there is not enough room below, show it above.
    if (top + height > window.innerHeight - margin) {
        top = rect.top - height - gap;
    }
    // Prevent overflow at the top of the viewport.
    top = Math.max(margin, top);
    notice.style.left = `${left}px`; notice.style.top = `${top}px`;
    notice.hidden = wasHidden;
}
function noticeFor(box) {
    const id = `${box.id}-keep-notice`;
    let n = document.getElementById(id);
    if (n) return n;
    // Create a floating notice.
    n = document.createElement("div"); n.id = id;
    n.className = "output-keep-notice";
    n.setAttribute("role", "status"); n.hidden = true;
    n.innerHTML = `
        <span class="output-keep-text"></span>
        <button type="button" class="output-keep-btn">Keep it</button>`;
    n.querySelector(".output-keep-btn").addEventListener("click", () => {
        box.checked = true;
        box.dispatchEvent(new Event("change", { bubbles: true }));
        box.focus();
    });
    // Append to body so the notice is not constrained by the checkbox row's layout or stacking context.
    document.body.appendChild(n);
    box.setAttribute("aria-describedby", id);
    return n;
}
function update(box) {
    const n = noticeFor(box);
    if (box.checked) { n.hidden = true; return; }
    n.querySelector(".output-keep-text").textContent =
        `Recommended: keep this output. It is needed for ${REQUIRED[box.id]} in the flow animation.`;
    // Position the notice immediately below the checkbox.
    const rect = box.getBoundingClientRect();
    n.hidden = false; positionNotice(box, n);
}
export function setupMovementOutputGuard(root = document) {
    for (const id of Object.keys(REQUIRED)) {
        const box = root.querySelector(`#${id}`);
        if (!box) { console.warn(`[outputs] Cannot find checkbox #${id}`); continue; }
        box.addEventListener("change", () => update(box));
        if (!box.checked) update(box);
    }
}

function modelManager() {
    obj.projectOpener.addEventListener('click', async () => {
        const name = obj.projectName.value.trim();
        if (!name || name === '') { alert('Please define scenario name.'); return; }
        if (nameChecker(name)) { alert('Scenario name contains invalid characters.'); return; }
        const content = { projectName: currentProject, flowName: name };
        signalSender('showOverlay', 'Reading forcing data to get start and end dates.\nPlease wait...');
        const data = await jsonLoader('flow_project', content); signalSender('hideOverlay');
        if (data.status === 'error') { alert(data.message); return; }
        const forcing = data.content['forcing'];
        obj.modelStart.value = forcing.start; obj.modelEnd.value = forcing.end;
        obj.modelInterval.value = intervalOption[forcing.interval];
    });
    obj.modelCheckBtn.addEventListener('click', async () => {
        const name = obj.projectName.value.trim(), key = 'wflow_check', logFile = 'log_wflow_check.txt';
        if (!name || name === '') { alert('Please define scenario name.'); return; }
        if (nameChecker(name)) { alert('Scenario name contains invalid characters.'); return; }
        try {
            obj.modelLogText.value = '';
            const content = { 
                projectName: currentProject, flowName: name, key: 'check', 
                keyChecker: key, timeZone: getLastTimeZone(), logFile: logFile
            };
            const request = await jsonLoader('wflow_model', content);
            if (request.status === 'error') { 
                obj.modelLogText.value = request.message; return; 
            }
            updateLog(currentProject, obj.modelLogText, 2, key, async () => {
                alert('Checking model parameters completed.');
                obj.modelLat.value = request.content.lat_lon[0];
                obj.modelLon.value = request.content.lat_lon[1];
            }, logFile);
        } catch (error) { alert(`Checking model parameters failed: ${error.message}`); }
    });
    obj.modelRunBtn.addEventListener('click', async () => {
        const name = obj.projectName.value.trim(), keyChecker = 'wflow_run', logFile = 'log_wflow_run.txt';
        if (!name || name === '') { alert('Please define scenario name.'); return; }
        if (nameChecker(name)) { alert('Scenario name contains invalid characters.'); return; }
        const start = obj.modelStart.value, end = obj.modelEnd.value;
        if (start === '' || end === '') { alert('Please select a start and end date.'); return; }
        const lat = obj.modelLat.value, lon = obj.modelLon.value;
        if (lat === '' || lon === '') { alert('Please add a upstream location on map first.'); return; }
        try {
            const contentCheck = { projectName: currentProject, key: keyChecker };
            const statusRes = await jsonLoader('check_download_status', contentCheck);
            if (statusRes.status === "running") { alert("Running Wflow model is in progress."); return; }
            obj.modelLogText.value = '';
            const inputs = [...document.querySelectorAll(".input-parameters input")];
            const bad = inputs.filter(i => isInvalidNumber(i.value)).map(i => i.id);
            if (bad.length) { alert(`Invalid parameters: ${bad.join(', ')}`); return; }
            const params_in = Object.fromEntries(inputs.map(i => [i.id, Number(i.value)]));
            const outputs = [...document.querySelectorAll(".output-parameters input")];
            const params_out = Object.fromEntries(outputs.map(o => [o.id, o.checked]));
            const content = { 
                projectName: currentProject, flowName: name, key: 'run', 
                start: start, end: end, timeZone: getLastTimeZone(), 
                keyChecker: keyChecker, step: obj.modelInterval.value, lat: lat, lon: lon, 
                params_input: params_in, params_output: params_out
            };
            const request = await jsonLoader('wflow_model', content);
            if (request.status === 'error') { alert(request.message); return; }
            updateLog(currentProject, obj.modelLogText, 5, keyChecker, async () => {
                alert('Running Wflow model completed.'); 
            }, logFile, true);
        } catch (error) { alert(`Running Wflow model failed: ${error.message}`); }
    });
}