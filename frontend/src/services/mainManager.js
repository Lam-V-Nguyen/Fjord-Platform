// =====================================================================
// mainManager.js – entry point of the new UI
//  1. Sign-in (auth_check), project/time zone note (keeps the .project-note class for iframes)
//  2. Navigation: Overview | Map View | Workspace (hash #overview / #map / #workspace)
//  3. Tool sidebar generated from toolRegistry.js -> opens Gridstack widgets as before
//  4. postMessage bus with the legacy iframes (GET_USER, addMapWidget, hyd-waq, showOverlay, ...)
//     behaviour kept UNCHANGED.
//  5. Settings: time zone (get_timezone), reset layout, reload alert thresholds
// =====================================================================
import { pdfOpener, projectModifier } from "./projectManager.js";
import { initGrid, addWidget, loadWidget, saveWidget, hasWidget } from "./widgetFunctions.js";
import { startLoading, stopLoading, jsonLoader } from "./commonFunctions.js";
import { setPendingRequest, clearPendingRequest, origin,
    getLastProject, setLastProject, getLastTimeZone, setLastTimeZone
} from "./constant.js";
import { renderPreview, currentMap } from "./mapManager.js";
import { TOOL_GROUPS, QUICK_TOOLS, getTool, groupOf, standaloneLabel } from "./toolRegistry.js";
import { icon } from "./icons.js";
import { initThresholdEditor } from "./thresholdEditor.js";
import { initAccountMenu } from "./accountMenu.js";
import { initStorageBanner } from "./storageBanner.js";
import { initShareDialog, projectLabel } from "./shareDialog.js";
import { Dashboard, toast, initSidebarResize } from "./dashboardManager.js";

const app = document.getElementById('app');
const githubCache = {}, pendingRequests = new Map();
const VIEW_KEY = 'ui-view', SIDEBAR_KEY = 'ui-sidebar-collapsed';
const VIEW_TITLES = {
    overview: 'HydroAI Water Intelligence Dashboard', map: 'Map View', workspace: 'Modelling Workspace',
    subtitle: 'Catchment, lake and fjord modelling - Hydrodynamics, water quality and water surface runoff'
};

let userName = null, prevSource = null, currentTimeZone = null, selectedTimeZone = null;
let dashboard = null, currentView = 'overview';

// boot
hydrateIcons(document); renderToolNav(); bindShell(); await login();
initWorkspace(); updateComponent();
dashboard = new Dashboard({ getProject: () => getLastProject() });
setView(initialView(), { replace: true });
dashboard.init({ onExpandMap: () => setView(currentView === 'map' ? 'overview' : 'map') });
showGitHubLastUpdate('Lam-V-Nguyen', 'Fjord-Platform', 'main');


// =====================================================================
// 1. Sign-in & note
// =====================================================================
async function login() {
    currentTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (getLastTimeZone() === '') { setLastTimeZone(currentTimeZone); }
    try {
        const data = await jsonLoader('auth_check', { projectName: getLastProject() });
        userName = data.user === 'admin' ? '' : data.user;
        // The last project saved in this browser no longer exists for this user (deleted, renamed, saved by
        // another account on the same browser…) -> go back to the sample project instead of using a missing one
        if (data.project_exists === false) setLastProject('public:demo');
    } catch (e) {
        console.error('auth_check failed', e); userName = '';
    }
    const project = getLastProject();
    showNotes(`${userName}/${project}`);
    document.getElementById('project-chip-label').textContent = projectLabel(project);
    selectedTimeZone = getLastTimeZone(); showTimeZone(currentTimeZone, selectedTimeZone);
}

export function showNotes(note) {
    // The "Project: user/project" format is read by iframes via GET_USER -> do not change
    const noteDiv = document.querySelector('.project-note');
    if (!noteDiv) return;
    noteDiv.textContent = `Project: ${note}`;
    noteDiv.title = note;
}

function showTimeZone(current, selected) {
    const div = document.querySelector('.time-zone');
    if (!div) return;
    div.textContent = selected && selected !== current ? `TZ ${selected} (browser ${current})` : `TZ ${current}`;
    div.title = `Current: ${current} · Selected: ${selected}`;
}

// =====================================================================
// 2. Navigation
// =====================================================================
function initialView() {
    const hash = location.hash.replace('#', '');
    if (VIEW_TITLES[hash]) return hash;
    const params = new URLSearchParams(location.search).get('view');
    if (VIEW_TITLES[params]) return params;
    return localStorage.getItem(VIEW_KEY) || 'overview';
}

function setView(view, { replace = false } = {}) {
    if (!VIEW_TITLES[view]) view = 'overview';
    const prevView = currentView; currentView = view;
    const isDash = view === 'overview' || view === 'map';
    document.getElementById('view-overview').classList.toggle('active', isDash);
    document.getElementById('view-workspace').classList.toggle('active', view === 'workspace');
    document.getElementById('dash-overview').classList.toggle('map-mode', view === 'map');
    document.getElementById('dash-toolbar').style.visibility = isDash ? 'visible' : 'hidden';
    document.querySelectorAll('#nav-main [data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    document.getElementById('page-title').textContent = VIEW_TITLES[view];
    if (view === 'workspace') {
        document.getElementById('page-subtitle').textContent = VIEW_TITLES.subtitle;
    }
    try { localStorage.setItem(VIEW_KEY, view); } catch (e) { /* ignore */ }
    const url = new URL(location.href); url.hash = view;
    replace ? history.replaceState(null, '', url) : history.pushState(null, '', url);
    app.classList.remove('sidebar-open');
    // Leaflet / Plotly / Gridstack must recompute their size after a view change
    const refit = isDash && (prevView === 'map' || view === 'map') && prevView !== view;
    requestAnimationFrame(() => {
        dashboard?.resize(refit);
        if (view === 'workspace') {
            try { currentMap?.invalidateSize(); } catch (e) { /* ignore */ }
            window.dispatchEvent(new Event('resize'));
        }
    });
}

window.addEventListener('popstate', () => {
    const v = location.hash.replace('#', '');
    if (VIEW_TITLES[v] && v !== currentView) setView(v, { replace: true });
});

// =====================================================================
// 3. Sidebar & shell
// =====================================================================
function hydrateIcons(root) {
    root.querySelectorAll('[data-icon]').forEach(el => {
        el.outerHTML = icon(el.dataset.icon, 18);
    });
}

function renderToolNav() {
    const nav = document.getElementById('nav-tools');
    nav.innerHTML = TOOL_GROUPS.map(g => {
        if (g.tools.length === 1) {
            const t = g.tools[0];
            return `<button class="nav-item" data-tool="${t.id}" title="${t.title}">${icon(g.icon)}<span class="label">${g.title}</span></button>`;
        }
        return `<div class="nav-group" data-group="${g.id}">
            <button class="nav-item" data-toggle-group="${g.id}" title="${g.title}">${icon(g.icon)}<span class="label">${g.title}</span>${icon('chevronRight', 14, 'chev')}</button>
            <div class="nav-sub">${g.tools.map(t =>
                `<button class="nav-item" data-tool="${t.id}" title="${t.title}"><span class="label">${t.short || t.title}</span></button>`).join('')}
            </div>
        </div>`;
    }).join('');
    // Restore the open group
    const open = JSON.parse(localStorage.getItem('ui-open-groups') || '[]');
    open.forEach(id => nav.querySelector(`[data-group="${id}"]`)?.classList.add('open'));

    // Quick buttons when the workspace is empty
    // Quick buttons: full context label + group icon (HYD / WAQ / Wflow…) to tell apart tools with the same short name
    document.getElementById('quick-tools').innerHTML = QUICK_TOOLS
        .map(id => getTool(id)).filter(Boolean)
        .map(t => `<button class="btn small" data-tool="${t.id}" title="${t.title}">${icon(groupOf(t.id)?.icon || t.icon, 15)}${standaloneLabel(t)}</button>`).join('');
}

function bindShell() {
    // Select view
    document.getElementById('nav-main').addEventListener('click', (e) => {
        const b = e.target.closest('[data-view]');
        if (b) setView(b.dataset.view);
    });
    // Open a tool (sidebar, quick buttons, System items)
    document.addEventListener('click', (e) => {
        const t = e.target.closest('[data-tool]');
        if (t) { e.preventDefault(); openTool(t.dataset.tool); return; }
        const g = e.target.closest('[data-toggle-group]');
        if (g) {
            const group = g.closest('.nav-group');
            if (app.classList.contains('sidebar-collapsed')) {
                app.classList.remove('sidebar-collapsed'); localStorage.setItem(SIDEBAR_KEY, '0');
                group.classList.add('open');
            } else group.classList.toggle('open');
            const open = [...document.querySelectorAll('.nav-group.open')].map(x => x.dataset.group);
            localStorage.setItem('ui-open-groups', JSON.stringify(open));
        }
    });
    // Collapse the sidebar (desktop) / open the drawer (mobile)
    if (localStorage.getItem(SIDEBAR_KEY) === '1') app.classList.add('sidebar-collapsed');
    document.getElementById('sidebar-toggle').addEventListener('click', () => {
        if (window.innerWidth <= 980) { app.classList.toggle('sidebar-open'); return; }
        app.classList.toggle('sidebar-collapsed');
        localStorage.setItem(SIDEBAR_KEY, app.classList.contains('sidebar-collapsed') ? '1' : '0');
        setTimeout(() => { dashboard?.resize(); window.dispatchEvent(new Event('resize')); }, 220);
    });
    // Drag the right edge of the sidebar to change its width
    initSidebarResize({
        app, sidebar: document.getElementById('sidebar'),
        onResize: () => { dashboard?.resize(); window.dispatchEvent(new Event('resize')); },
    });
    // Switch project (uses the existing projectManager modal)
    document.getElementById('project-chip').addEventListener('click', () => projectModifier(userName));
    // Settings popover
    const settings = document.getElementById('settings-popover');
    document.getElementById('btn-settings').addEventListener('click', async (e) => {
        e.stopPropagation();
        const r = e.currentTarget.getBoundingClientRect();
        settings.style.left = `${Math.min(r.right + 8, window.innerWidth - 310)}px`;
        settings.classList.toggle('show');
        if (settings.classList.contains('show')) {
            const input = document.getElementById('tz-search');
            input.value = '';
            await renderTimezoneList(document.getElementById('tz-results'), '');
            // Position from the panel's REAL height: panel bottom level with the button bottom, never off-screen
            const h = settings.offsetHeight;
            settings.style.top = `${Math.max(10, Math.min(r.bottom - h, window.innerHeight - h - 10))}px`;
            input.focus();
        }
    });
    settings.addEventListener('click', (e) => e.stopPropagation());
    bindTimezoneSearch();
    initStorageBanner();
    initAccountMenu({ toast, beforeOpen: () => settings.classList.remove('show') })
        .then(me => initShareDialog({ toast, me, beforeOpen: () => settings.classList.remove('show') }));
    initThresholdEditor({
        toast, beforeOpen: () => settings.classList.remove('show'),
        onSaved: () => dashboard?.refresh(true),          // recompute alerts / WQI with the new thresholds
    });
    document.getElementById('reset-layout').addEventListener('click', () => {
        if (!confirm('Close all widgets and reset the workspace layout?')) return;
        closeAllWidgets(); localStorage.removeItem('grid-layout'); settings.classList.remove('show');
        toast('Workspace layout reset');
    });
    document.getElementById('reload-thresholds').addEventListener('click', async () => {
        try {
            await fetch('/dashboard/thresholds', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reload: true }) });
            settings.classList.remove('show'); toast('Thresholds reloaded'); dashboard?.refresh(true);
        } catch (e) { toast(`Error: ${e.message}`); }
    });
    document.getElementById('ws-close-all').addEventListener('click', () => {
        if (confirm('Close all open tools?')) closeAllWidgets();
    });
    // Close popovers on outside click / Esc
    document.addEventListener('click', () => {
        document.querySelectorAll('.popover.show').forEach(p => p.classList.remove('show'));
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.popover.show, .modal-backdrop.show').forEach(p => p.classList.remove('show'));
    });
}

// =====================================================================
// 4. Workspace (Gridstack) – open / close widgets
// =====================================================================
function initWorkspace() {
    const grid = initGrid();
    loadWidget();
    grid.on('added removed', () => setTimeout(updateWorkspaceState, 0));
    setTimeout(updateWorkspaceState, 150);
    // Close widgets & edit titles (as in the old version)
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('remove-btn')) {
            const widget = e.target.closest('.grid-stack-item');
            if (widget) removeWidget(widget.getAttribute('gs-id'));
        }
        if (e.target.classList.contains('widget-title')) {
            const newTitle = prompt('Enter new title:', e.target.textContent);
            if (newTitle) { e.target.textContent = newTitle; saveWidget(); }
        }
    });
}

function removeWidget(widgetId) {
    saveWidget();
    // Close the attached map widget (e.g. new-hyd -> new-hyd-map)
    const mapEl = document.querySelector(`[gs-id="${widgetId}-map"]`);
    if (mapEl) removeWidget(`${widgetId}-map`);
    const grid = initGrid();
    const node = document.querySelector(`[gs-id="${widgetId}"]`);
    if (grid && node) {
        grid.removeWidget(node, true);
        setTimeout(() => { grid.update(); saveWidget(); updateWorkspaceState(); }, 100);
    }
}

function closeAllWidgets() {
    const grid = initGrid();
    grid.removeAll(true); saveWidget(); updateWorkspaceState();
}

function updateWorkspaceState() {
    const nodes = initGrid().engine.nodes;
    document.getElementById('ws-count').textContent = nodes.length;
    document.getElementById('workspace-empty').classList.toggle('show', nodes.length === 0);
    const open = new Set(nodes.map(n => n.id));
    document.querySelectorAll('#nav-tools [data-tool]').forEach(b => b.classList.toggle('open-widget', open.has(b.dataset.tool)));
}

function openTool(id) {
    const tool = getTool(id);
    if (!tool) return;
    if (tool.type === 'pdf') { pdfOpener(tool.url); return; }
    setView('workspace');
    if (hasWidget(id)) {
        const el = document.querySelector(`[gs-id="${id}"]`);
        if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
        }
        return;
    }
    addWidget(tool.w || 6, tool.h || 7, tool.title, id, tool.url);
    setTimeout(updateWorkspaceState, 50);
}

// =====================================================================
// 5. postMessage bus with the iframes 
// =====================================================================
function updateComponent() {
    clearPendingRequest();
    window.addEventListener('message', async (event) => {
        const msg = event.data || {};
        if (msg.type === 'GET_USER') { // Get project destination
            const project = document.querySelector('.project-note');
            if (!project) return;
            const content = project.textContent.split(':').pop();
            event.source.postMessage({ type: 'USER', content: content }, origin);
        } else if (msg.type === 'addMapWidget') { // Add map
            const id = msg.content.id;
            if (!hasWidget(id)) { 
                addWidget(12, 5, msg.content.title, id); setTimeout(updateWorkspaceState, 50); 
            }
        } else if (msg.id === 'hyd-waq') {
            const req = {
                source: event.source, lineType: msg.lineType,
                requestId: msg.requestId, content: msg.content,
            };
            setPendingRequest(req); renderPreview(req);
        } else if (msg.type === 'showOverlay') {
            startLoading(msg.content);
            await new Promise(requestAnimationFrame);
        } else if (msg.type === 'hideOverlay') {
            stopLoading(); await new Promise(requestAnimationFrame);
        } else if (msg.type === 'updateObsPoint') {
            const req = { source: event.source, requestId: msg.type, content: msg.content.content };
            setPendingRequest(req); renderPreview(req);
        } else if (msg.type === 'clearCrossSection') {
            renderPreview({ requestId: msg.type });
        } else if (msg.type === 'clearBoundary') {
            renderPreview({ requestId: msg.type });
        } else if (msg.type === 'drawScenario') {     // draw the objects of an existing HYD scenario
            renderPreview({ requestId: msg.type, content: msg.content });
        } else if (msg.type === 'waqPoint' || msg.type === 'loadsPoint') {
            renderPreview({ requestId: msg.type, content: msg.content });
        } else if (msg.id === 'clearGridMap') {
            renderPreview({ source: event.source, requestId: msg.id });
        } else if (msg.id === 'gridPlot') {
            renderPreview({ source: event.source, requestId: msg.requestId, content: msg.content });
        } else if (msg.type === 'showNote') {
            showNotes(msg.content);
            const proj = String(msg.content || '').split('/').pop();
            if (proj) document.getElementById('project-chip-label').textContent = projectLabel(proj);
        } else if (msg.type === 'updateUIState') {
            const { requestId } = msg.content;
            if (requestId && pendingRequests.has(requestId)) {
                const { source } = pendingRequests.get(requestId);
                prevSource = source;
                source.postMessage({ type: 'updateReturn', content: msg.content, requestId: requestId }, origin);
                pendingRequests.delete(requestId);
            } else if (prevSource) {
                prevSource.postMessage({ type: 'updateUIDelay', content: msg.content }, origin);
            }
        } else if (msg.type === 'refreshDashboard') {
            dashboard?.refresh(true);
        }
    });
}

// =====================================================================
// 6. Time zone (uses the existing get_timezone endpoint)
// =====================================================================
function bindTimezoneSearch() {
    const searchInput = document.getElementById('tz-search');
    const resultsBox = document.getElementById('tz-results');
    if (!searchInput || !resultsBox) return;
    let timer = null;
    searchInput.addEventListener('input', () => {
        clearTimeout(timer);
        const q = searchInput.value.trim();
        timer = setTimeout(() => renderTimezoneList(resultsBox, q), 150);
    });
    resultsBox.addEventListener('click', (e) => {
        const item = e.target.closest('.tz-result-item');
        if (!item?.dataset.tz) return;
        e.stopPropagation();
        selectedTimeZone = item.dataset.tz;
        setLastTimeZone(selectedTimeZone);
        showTimeZone(currentTimeZone, selectedTimeZone);
        document.getElementById('settings-popover').classList.remove('show');
        toast(`Time zone set to ${selectedTimeZone}`);
    });
}

async function renderTimezoneList(resultsBox, query = '') {
    if (!resultsBox) return;
    resultsBox.innerHTML = ''; let list = [];
    try { list = await jsonLoader('get_timezone', { query: query, n: 20 }); }
    catch (e) { console.error(e); list = []; }
    if (!list || !list.length) {
        resultsBox.innerHTML = `<div class="tz-result-item">Couldn't find timezone.</div>`; return;
    }
    const frag = document.createDocumentFragment();
    list.forEach(tz => {
        const item = document.createElement('div');
        item.className = 'tz-result-item';
        if (tz === selectedTimeZone) item.classList.add('active');
        item.dataset.tz = tz;
        item.innerHTML = `<span>${tz}</span><span class="tz-offset">${getOffsetLabel(tz)}</span>`;
        frag.appendChild(item);
    });
    resultsBox.appendChild(frag);
}

function getOffsetLabel(tzName) {
    try {
        const parts = new Intl.DateTimeFormat('en-US', { 
            timeZone: tzName, timeZoneName: 'shortOffset' 
        }).formatToParts(new Date());
        return parts.find(p => p.type === 'timeZoneName')?.value || '';
    } catch (e) { return ''; }
}

// ======================
// 7. GitHub last update
// =====================
async function showGitHubLastUpdate(username, repo, branch = 'main', duration = 30 * 60 * 1000) {
    const url = `https://api.github.com/repos/${username}/${repo}/commits?sha=${branch}&per_page=1`;
    const key = `${username}/${repo}/${branch}`;
    const displayDiv = document.querySelector('.github-last-update');
    if (!displayDiv) return;
    const cacheData = localStorage.getItem(key);
    if (cacheData) {
        try {
            const parsed = JSON.parse(cacheData);
            if (Date.now() - parsed.timestamp < duration) { displayDiv.textContent = parsed.data; return; }
        } catch (e) { /* ignore */ }
    }
    try {
        const response = await fetch(url, { headers: { Accept: 'application/vnd.github+json' } });
        if (!response.ok) throw new Error('GitHub API error');
        const data = await response.json();
        if (data.length > 0) {
            const date = new Date(data[0].commit.committer.date);
            const text = `Branch ${branch} · ${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
            githubCache[key] = text; displayDiv.textContent = text;
            localStorage.setItem(key, JSON.stringify({ data: text, timestamp: Date.now() }));
        } else { displayDiv.textContent = 'Last update: unknown'; }
    } catch (err) {
        console.warn(err); displayDiv.textContent = `Branch ${branch}`;
    }
}