import { jsonLoader } from "./commonFunctions.js";
import { origin, setLastProject } from "./constant.js";

export async function projectModifier(user) {
    const overlay = document.createElement("div");
    overlay.className = "pm-overlay";
    const modal = document.createElement("div");
    modal.className = "pm-modal";
    modal.innerHTML = `<h3>Open Project</h3>
        <div class="pm-row">
            <label for="projectList">Select Project</label>
            <select id="projectList">
                <option value="">-- No selected --</option>
            </select>
        </div>
        <div class="pm-actions">
            <button class="pm-open" id="okBtn">Open</button>
            <button class="pm-cancel" id="closeBtn">Cancel</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    const projectList = modal.querySelector("#projectList");
    const contents = {filename: '',  key: 'getProjects', folder_check: ''};
    const data = await jsonLoader('select_project', contents);
    if (data.status === "error") { alert(data.message); overlay.remove(); return; }
    data.content.forEach(project => {
        const option = document.createElement("option"); option.value = project;
        option.textContent = project; projectList.appendChild(option);
    });
    if (data.content.length > 0) { projectList.selectedIndex = 1; }
    modal.querySelector("#okBtn").onclick = async () => {
        const value = projectList.value.trim();
        if (value === "") {
            alert("Please select a project from the list."); return;
        }
        setLastProject(value);
        window.parent.postMessage(
            { type: 'showNote', content: `${user}/${value}` }, origin
        );
        overlay.remove(); location.reload();
    };
    modal.querySelector("#closeBtn").onclick = () => { overlay.remove(); };
}

export function pdfOpener(pdfName=null) {
    if (!pdfName) return;
    const win = window.open(`src_frontend/pdfs/${pdfName}`, '_blank');
    if (!win) alert('Please allow popups for this document');
}

export async function projectRender(objectInput, objectList, fullList) {
    // Update project list
    objectInput.addEventListener('input', (e) => { 
        const value = e.target.value.trim();
        if (fullList.length === 0) { objectList.style.display = "none"; return; }
        objectList.innerHTML = "";
        const filtered = fullList.filter(p => p.toLowerCase().includes(value.toLowerCase()));
        if (filtered.length === 0) { objectList.style.display = "none"; return; }
        filtered.forEach(p => {
            const li = document.createElement("li");
            li.textContent = p;
            li.addEventListener('mousedown', () => { 
                objectInput.value = p; objectList.style.display = "none";
            });
            objectList.appendChild(li);
        });
        objectList.style.display = filtered.length > 0 ? "block": "none";
    });
    objectInput.addEventListener('click', async () => {
        objectInput.dispatchEvent(new Event('input'));
    });
    objectInput.addEventListener('blur', () => { 
        setTimeout(() => { objectList.style.display = "none"; }, 100);
    });
}