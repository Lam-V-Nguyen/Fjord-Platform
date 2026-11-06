import { signalSender, fileUploader, jsonLoader, getUser } from "./commonFunctions.js";
import { generalOptionsManager } from "./generalOptions.js";
import { plotChart } from "./chartManager.js";
import { spatialMapManager } from "./spatialMapManager.js";
import { projectModifier } from "./projectManager.js";
import { getState, getLastTimeZone } from "./constant.js";

let cachedMenus = {}, html = null, timeOut = null, checked = false, userName = null,
    currentProject = null, waqModel = null, waqName = null, currentParams = null,
    gisUploadInitialized = false;


function addGISMenu(menu) {
    const li = document.createElement("li"), a = document.createElement("a");
    a.className = "menu"; a.textContent = "GIS Layer"; a.id = "GISMenu";
    a.setAttribute("data-info", "4|gisLayer.html");
    li.appendChild(a); menu.appendChild(li);
}

function initializeGISUploader(project, params, name, model) {
    if (gisUploadInitialized) return;
    const gisUploadFile = document.getElementById('gis-file');
    if (!gisUploadFile) return;
    gisUploadInitialized = true;
    gisUploadFile.addEventListener('change', async (e) => { 
        const file = e.target.files[0]; if (!file) return;
        try {
            await fileUploader(gisUploadFile, null, project, 
                file.name, 'Uploading and Processing GIS data.\nPlease wait...', 'gis');
            const message = `Reloading project '${project}'.\nPlease wait...`;
            await projectChecker(project, params, name, model, message, true);
            await initializeMenu(project, params, name, model);
            // Reopen GIS menu
            const gisMenu = document.getElementById('GISMenu');
            if (gisMenu) { gisMenu.click(); }
        } catch (error) { alert(error);
        } finally { gisUploadFile.value = ''; }
    });
}

async function initProject() {
    userName = await getUser();
    let user = userName.split('/').shift(); projectModifier(user); 
}

export function locationFinder(input, suggestion, map) {
    input.addEventListener('input', (e) => {
        clearTimeout(timeOut);
        const value = e.target.value.trim();
        if (value === '' || value.length < 2) { 
            suggestion.style.display = 'none'; return; 
        }
        timeOut = setTimeout(() => {
            fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(value)}&addressdetails=1&limit=100`)
            .then(response => response.json()).then(data => {
                if (data.length === 0) { suggestion.style.display = 'none'; return;}
                suggestion.innerHTML = '';
                data.forEach(location => {
                    var div = document.createElement('div');
                    div.textContent = location.display_name;
                    div.addEventListener('click', () => {
                        var lat = location.lat, lng = location.lon;
                        map.setView([lat, lng], 12);
                        input.value = location.display_name;
                        suggestion.style.display = 'none';
                    });
                    suggestion.appendChild(div);
                });
                suggestion.style.display = 'block';
            })
        }, 200);
    });
}

export async function projectChecker(project, params, name, model, message, gisChanged=false) {
    currentProject = project; currentParams = params; waqName = name; waqModel = model;
    cachedMenus = {}; // Clear cache of menus for new project
    signalSender('showOverlay', message);
    const content = { 
        projectName: project, params: params, waqName: waqName,
        waqModel: waqModel, gisChanged: gisChanged 
    };
    const data = await jsonLoader('setup_database', content); signalSender('hideOverlay');
    if (data.status === "error") { alert(data.message); checked = false; return; }
    checked = true;
    const menuLeft = document.getElementById('menu-left');
    if (!menuLeft) { return; }
    const gisMenu = document.getElementById('GISMenu');
    if (data.content.gis_layers.length > 0) {
        if (!gisMenu) { addGISMenu(menuLeft); }
    } else {if (gisMenu) { gisMenu.remove(); }}
}

export async function initializeMenu(project, params, name, model){
    // Work with pupup menu
    initializeGISUploader(project, params, name, model); // Initialize GIS uploader only once
    document.querySelectorAll('.nav ul li a:not([style*="display: none"])').forEach(link => {
        link.onclick = async(event) => {
            event.stopPropagation(); event.preventDefault();
            const rect = link.getBoundingClientRect();
            const pm = document.getElementById('popup-menu'); if (!pm) return;
            if (pm.classList.contains('show')) { pm.classList.remove('show'); }
            const info = link.dataset.info;
            if (info === 'home') { window.location.href = "https://ntnusmartwater.github.io/"; return; }
            else if (info === 'gis-upload') { 
                const gisUploadFile = document.getElementById('gis-file');
                if (!gisUploadFile) return;
                gisUploadFile.click(); return;
            }
            else if (info == 'project-open') { initProject(); return; }
            if (!checked) { 
                alert("No scenario was loaded.\nPlease select 'General Options/Reset Configuration' to reload the scenario."); 
            }
            const [id, htmlFile] = info.split('|');
            signalSender('showOverlay', 'Getting Information.\nPlease wait...');
            await showPopupMenu(project, model, id, htmlFile); signalSender('hideOverlay');
            pm.style.top = `${rect.bottom + 10 + window.scrollY}px`;
            pm.style.left = `${rect.left + window.scrollX}px`;
            pm.classList.add('show');
        };
    })
}

export async function showPopupMenu(project, waqName, id, htmlFile) {
    try {
        const popupContent = document.getElementById('popup-content');
        if (!popupContent) return; 
        if (cachedMenus[htmlFile]) { 
            popupContent.innerHTML = cachedMenus[htmlFile];
        } else {
            const response = await fetch(`/load_popupMenu?data=${htmlFile}|${waqName}&project_name=${project}`);
            if (!response.ok) { alert(response.message); return; }
            html = await response.text(); cachedMenus[htmlFile] = html;
            popupContent.innerHTML = html;
        }
        if (id === '1') generalOptionsManager(project); // Events on General Options submenu
        if (id === '2') timeSeriesManager(project); // Events on Time series Measurement submenu
        if (id === '3') spatialMapManager(project);  // Events on Map submenu
        if (id === '4') {
            const checkBox = popupContent.querySelectorAll('input[type="checkbox"]');
            if (checkBox === null || checkBox.length === 0 ) return;
            const savedGisLayers = getState().gisLayers || {};
            for (let i = 0; i < checkBox.length; i++) { 
                checkBox[i].checked = !!savedGisLayers[checkBox[i].id];
            }
        }
    } catch (error) { alert(error + ': ' + htmlFile); }
}

function timeSeriesManager(projectName) {
    // Set function for plot using Plotly
    const plotContainer = document.getElementById('time-series-container');
    const substanceContainer = document.getElementById('substance-container');
    const substancesContent = substanceContainer.querySelector('#substance-content');
    document.querySelectorAll('.function').forEach(plot => {
        plot.onclick = () => {
            const [key, titleY, chartTitle] = plot.dataset.info.split('|');
            plotChart(projectName, plotContainer, '', key, chartTitle, 'Time', titleY);
        };
    });
    // Process water quality selector
    document.querySelectorAll('.waq_his').forEach(item => {
        item.onclick = async () => {
            // substanceWindowMap().style.display = 'none';
            const content = { 
                query: item.dataset.info, key: 'substance_check', 
                projectName: projectName, timeZone: getLastTimeZone()
            };
            const data = await jsonLoader('process_data', content);
            if (data.status === "error") {
                alert(data.message); substanceContainer.style.display = 'none'; return;
            }
            const substanceTitle = substanceContainer.querySelector('#substance-title');
            substanceTitle.textContent = 'Substance - Time Series';
            substancesContent.innerHTML = ''; substanceContainer.style.display = 'flex';
            // Add content
            substancesContent.innerHTML = data.content.map((substance, i) => 
                `<label for="his-${substance}"><input type="radio" name="waq-substance-his" id="his-${substance}"
                    value="${data.message[i]}" ${i === 0 ? 'checked':''}>${data.message[i]}</label>`).join('');
            // hideMap();
            const chartTitle = `Substance: ${data.message[0]}`;
            plotChart(projectName, plotContainer, data.content[0], 'substance', chartTitle, 'Time', data.message[0]);
        };
    });
    // Listen to substance selection
    substancesContent.addEventListener('change', (e) => {
        if (e.target && e.target.name === "waq-substance-his") {
            const id = e.target.id, value = e.target.value;
            // hideMap(); 
            plotChart(projectName, plotContainer, id.replace('his-', ''), 'substance', `Substance: ${value}`, 'Time', value);
        }
    });
}
