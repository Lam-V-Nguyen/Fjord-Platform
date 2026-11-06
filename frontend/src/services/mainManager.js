// =====================================================================
// mainManager.js – điểm vào của giao diện mới
//  1. Đăng nhập (auth_check), ghi chú dự án/múi giờ (giữ class .project-note cho iframe)
//  2. Điều hướng: Overview | Map View | Workspace (hash #overview / #map / #workspace)
//  3. Sidebar công cụ sinh từ toolRegistry.js -> mở widget Gridstack như trước
//  4. Bus postMessage với các iframe cũ (GET_USER, addMapWidget, hyd-waq, showOverlay, ...)
//     được giữ NGUYÊN hành vi.
//  5. Settings: múi giờ (get_timezone), reset layout, nạp lại ngưỡng cảnh báo
// =====================================================================
import { pdfOpener, projectModifier } from "./projectManager.js";
import { initGrid, addWidget, loadWidget, saveWidget, hasWidget } from "./widgetFunctions.js";
import { startLoading, stopLoading, jsonLoader } from "./commonFunctions.js";
import { setPendingRequest, clearPendingRequest, origin, getLastProject,
    getLastTimeZone, setLastTimeZone } from "./constant.js";
import { renderPreview, currentMap } from "./mapManager.js";
import { TOOL_GROUPS, QUICK_TOOLS, getTool, groupOf, standaloneLabel } from "./toolRegistry.js";
import { icon } from "./icons.js";
import { Dashboard, toast } from "./dashboardManager.js";

const app = document.getElementById('app');
const githubCache = {}, pendingRequests = new Map();
const VIEW_KEY = 'ui-view', SIDEBAR_KEY = 'ui-sidebar-collapsed';
const VIEW_TITLES = {
    overview: 'Fjord Water Intelligence Dashboard',
    map: 'Map View', workspace: 'Modelling Workspace',
};
let userName = null, prevSource = null, currentTimeZone = null, selectedTimeZone = null;
let dashboard = null, currentView = 'overview';

// ------------------------------------------------------------------ boot
hydrateIcons(document);
renderToolNav();
bindShell();
await login();
initWorkspace();
updateComponent();
dashboard = new Dashboard({ getProject: () => getLastProject() });
setView(initialView(), { replace: true });
dashboard.init({ onExpandMap: () => setView(currentView === 'map' ? 'overview' : 'map') });
showGitHubLastUpdate('Lam-V-Nguyen', 'HydroAI-Fjord-Platform', 'dev');


// =====================================================================
// 1. Đăng nhập & ghi chú
// =====================================================================
async function login() {
    currentTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (getLastTimeZone() === '') { setLastTimeZone(currentTimeZone); }
    try {
        const data = await jsonLoader('auth_check', {});
        userName = data.user === 'admin' ? '' : data.user;
    } catch (e) {
        console.error('auth_check failed', e); userName = '';
    }
    const project = getLastProject();
    showNotes(`${userName}/${project}`);
    document.getElementById('project-chip-label').textContent = project;
    selectedTimeZone = getLastTimeZone(); showTimeZone(currentTimeZone, selectedTimeZone);
}

export function showNotes(note) {
    // Định dạng "Project: user/project" được iframe đọc qua GET_USER -> không đổi
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
// 2. Điều hướng
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
    const prevView = currentView;
    currentView = view;
    const isDash = view === 'overview' || view === 'map';
    document.getElementById('view-overview').classList.toggle('active', isDash);
    document.getElementById('view-workspace').classList.toggle('active', view === 'workspace');
    document.getElementById('dash-overview').classList.toggle('map-mode', view === 'map');
    document.getElementById('dash-toolbar').style.visibility = isDash ? 'visible' : 'hidden';
    document.querySelectorAll('#nav-main [data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
    document.getElementById('page-title').textContent = VIEW_TITLES[view];
    if (view === 'workspace') {
        document.getElementById('page-subtitle').textContent = 'Data preparation, grid generation, HYD/WAQ scenarios, flow estimation and calibration';
    }
    try { localStorage.setItem(VIEW_KEY, view); } catch (e) { /* ignore */ }
    const url = new URL(location.href); url.hash = view;
    replace ? history.replaceState(null, '', url) : history.pushState(null, '', url);
    app.classList.remove('sidebar-open');
    // Leaflet / Plotly / Gridstack cần tính lại kích thước sau khi đổi view
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
    // Khôi phục nhóm đang mở
    const open = JSON.parse(localStorage.getItem('ui-open-groups') || '[]');
    open.forEach(id => nav.querySelector(`[data-group="${id}"]`)?.classList.add('open'));

    // Nút nhanh khi workspace trống
    // Nút nhanh: nhãn đầy đủ ngữ cảnh + icon của nhóm (HYD / WAQ / Wflow…) để phân biệt các công cụ trùng tên ngắn
    document.getElementById('quick-tools').innerHTML = QUICK_TOOLS
        .map(id => getTool(id)).filter(Boolean)
        .map(t => `<button class="btn small" data-tool="${t.id}" title="${t.title}">${icon(groupOf(t.id)?.icon || t.icon, 15)}${standaloneLabel(t)}</button>`).join('');
}

function bindShell() {
    // Chọn view
    document.getElementById('nav-main').addEventListener('click', (e) => {
        const b = e.target.closest('[data-view]');
        if (b) setView(b.dataset.view);
    });
    // Mở công cụ (sidebar, nút nhanh, mục System)
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
    // Thu gọn sidebar (desktop) / mở drawer (mobile)
    if (localStorage.getItem(SIDEBAR_KEY) === '1') app.classList.add('sidebar-collapsed');
    document.getElementById('sidebar-toggle').addEventListener('click', () => {
        if (window.innerWidth <= 980) { app.classList.toggle('sidebar-open'); return; }
        app.classList.toggle('sidebar-collapsed');
        localStorage.setItem(SIDEBAR_KEY, app.classList.contains('sidebar-collapsed') ? '1' : '0');
        setTimeout(() => { dashboard?.resize(); window.dispatchEvent(new Event('resize')); }, 220);
    });
    // Đổi dự án (dùng modal sẵn có của projectManager)
    document.getElementById('project-chip').addEventListener('click', () => projectModifier(userName));

    // Settings popover
    const settings = document.getElementById('settings-popover');
    document.getElementById('btn-settings').addEventListener('click', async (e) => {
        e.stopPropagation();
        const r = e.currentTarget.getBoundingClientRect();
        settings.style.left = `${Math.min(r.right + 8, window.innerWidth - 310)}px`;
        settings.style.top = `${Math.max(10, r.top - 220)}px`;
        settings.classList.toggle('show');
        if (settings.classList.contains('show')) {
            const input = document.getElementById('tz-search');
            input.value = ''; input.focus();
            await renderTimezoneList(document.getElementById('tz-results'), '');
        }
    });
    settings.addEventListener('click', (e) => e.stopPropagation());
    bindTimezoneSearch();
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

    // Đóng popover khi bấm ra ngoài / Esc
    document.addEventListener('click', () => {
        document.querySelectorAll('.popover.show').forEach(p => p.classList.remove('show'));
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.popover.show, .modal-backdrop.show').forEach(p => p.classList.remove('show'));
    });
}

// =====================================================================
// 4. Workspace (Gridstack) – mở / đóng widget
// =====================================================================
function initWorkspace() {
    const grid = initGrid();
    loadWidget();
    grid.on('added removed', () => setTimeout(updateWorkspaceState, 0));
    setTimeout(updateWorkspaceState, 150);

    // Đóng widget & sửa tiêu đề (giống bản cũ)
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
    // Đóng widget bản đồ đi kèm (vd. new-hyd -> new-hyd-map)
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
// 5. Bus postMessage với các iframe (giữ nguyên hành vi bản cũ)
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
            if (!hasWidget(id)) { addWidget(12, 5, msg.content.title, id); setTimeout(updateWorkspaceState, 50); }
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
        } else if (msg.type === 'waqPoint' || msg.type === 'loadsPoint') {
            renderPreview({ requestId: msg.type, content: msg.content });
        } else if (msg.id === 'clearGridMap') {
            renderPreview({ source: event.source, requestId: msg.id });
        } else if (msg.id === 'gridPlot') {
            renderPreview({ source: event.source, requestId: msg.requestId, content: msg.content });
        } else if (msg.type === 'showNote') {
            showNotes(msg.content);
            const proj = String(msg.content || '').split('/').pop();
            if (proj) document.getElementById('project-chip-label').textContent = proj;
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
            // Mới: iframe có thể yêu cầu làm mới dashboard sau khi chạy xong mô phỏng
            dashboard?.refresh(true);
        }
    });
}

// =====================================================================
// 6. Múi giờ (dùng endpoint get_timezone sẵn có)
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
        const parts = new Intl.DateTimeFormat('en-US', { timeZone: tzName, timeZoneName: 'shortOffset' }).formatToParts(new Date());
        return parts.find(p => p.type === 'timeZoneName')?.value || '';
    } catch (e) { return ''; }
}

// =====================================================================
// 7. GitHub last update (cache 30 phút, không còn alert() khi lỗi)
// =====================================================================
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