// Điều phối trang Overview (Dashboard) – nội dung chính là KẾT QUẢ MÔ PHỎNG HYD / WAQ:
//  - Catalog: kịch bản HYD/WAQ, biến bản đồ & trạm, trạm, mốc thời gian (/dashboard/catalog)
//  - KPI, giám sát, trạng thái chất lượng nước, nguồn/xả, rủi ro, cảnh báo (/dashboard/summary, /alerts)
//  - Bản đồ trường mô phỏng + thanh thời gian (/dashboard/mesh, /field)
//  - Profile thẳng đứng & chuỗi thời gian tại trạm (/dashboard/profile, /station)
//  - Khi dự án chưa có kết quả: hiển thị các bước mô hình, mỗi bước mở công cụ tương ứng
//  - Trạng thái (period, end, field, layer, station) đồng bộ vào URL để chia sẻ
//  - Lớp bổ sung "Flow movement (HYD)", "Wflow results" và "Pollutant spread (WAQ)": sáng khi có dữ liệu, mờ khi chưa có

import { icon } from './icons.js';
import { DashboardMap, fmtVal } from './dashboardMap.js';
import { MovementLayer } from './dashboardMovement.js';
import { WflowLayer } from './dashboardWflow.js';
import { Wflow3DView } from './wflow3DManager.js';
import { PollutionLayer } from './dashboardPollution.js';
import { 
    renderGauge, renderDonut, renderStationSeries, 
    renderProfile, resizeCharts, showEmpty 
} from './dashboardCharts.js';
import { WeatherPanel } from './dashboardWeather.js';


const FILTER_KEY = 'dash-filters', STATE_KEY = 'dash-state-v2';
const PERIOD_LABEL = { '1d': '1 day', '7d': '7 days', '30d': '30 days', all: 'whole simulation' };
const KPI_TONES = { 
    surface_temperature: 'tone-pink', stratification: 'tone-blue', 
    water_level: 'tone-violet', max_velocity: 'tone-amber', quality: 'tone-green'
};
const SEV_ICON = { high: 'alert', medium: 'alert', low: 'check', info: 'info' };
const STEP_ICONS = { data: 'clipboard', grid: 'grid', hyd: 'waves', waq: 'flask', calibration: 'sliders' };
const WIDTH_KEY = 'ui-sidebar-width';
const MIN_W = 200, MAX_W = 420, MOBILE_MAX = 980;
// Lớp bổ sung: lớp JS, tên hiển thị, tiêu đề bảng ⚙, mở bảng ⚙ ngay khi bật
const EXTRAS = {
    movement: { Cls: MovementLayer, name: 'Flow movement', title: 'Flow movement settings' },
    wflow: { Cls: WflowLayer, name: 'Wflow results', title: 'Wflow results settings' },
    pollution: { Cls: PollutionLayer, name: 'Pollutant spread', title: 'Pollutant spread (WAQ)', openOnShow: true },
    wflow3d: { Cls: Wflow3DView, name: 'Wflow 3D', title: 'Wflow 3D settings', statusKey: 'wflow' },
};


// helpers
async function api(path, body = {}) {
    const res = await fetch(`/dashboard/${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || !data || data.status === 'error') throw new Error(data?.message || data?.detail || `HTTP ${res.status}`);
    return data;
}

function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function fmtNumber(v, d = 2) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
    return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtDate(iso, withTime = false) {
    if (!iso) return '-';
    const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
    if (Number.isNaN(d.getTime())) return iso;
    const opts = { year: 'numeric', month: 'short', day: 'numeric' };
    if (withTime) Object.assign(opts, { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleString('en-GB', opts);
}

function toast(msg) {
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg; t.classList.add('show');
    clearTimeout(t._timer); t._timer = setTimeout(() => t.classList.remove('show'), 2400);
}

function debounce(fn, ms) {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// Hint tự động cho chữ bị cắt bằng dấu "…" (CSS text-overflow: ellipsis):
//  - chỉ gắn title khi chữ THỰC SỰ bị cắt; giữ hint sẵn có của khối cha (vd. KPI) ở dòng sau
//  - <select>: hint = nhãn gốc + mục đang chọn (tên biến dài bị cắt trong ô chọn)
const HINT_SEL = ['.m-label', '.m-sub', '.m-value', '.panel-title', '.panel-sub', '.kpi-label', '.kpi-name', '.kpi-sub',
    '.dl-name', '.q-name', '.b-name', '.alert-title', '.alert-meta', '.lf-title', '.lr-name', '.layer-name',
    '.w-label', '#page-title', '#page-subtitle', '.project-chip .label', 'select'].join(',');
export function initAutoHints(root = document) {
    root.addEventListener('mouseover', (e) => {
        const el = e.target.closest?.(HINT_SEL);
        if (!el) return;
        if (el.tagName === 'SELECT') {
            if (el.dataset.baseTitle === undefined) el.dataset.baseTitle = el.title || '';
            const cur = el.selectedOptions[0]?.textContent.trim() || '';
            el.title = [el.dataset.baseTitle, cur].filter(Boolean).join(': ');
            return;
        }
        if (el.title && !el.dataset.autoHint) return;          // phần tử đã có hint riêng
        const text = el.textContent.replace(/\s+/g, ' ').trim();
        if (text && el.scrollWidth > el.clientWidth + 1) {
            const parent = el.parentElement?.closest('[title]:not([data-auto-hint])')?.title || '';
            el.title = parent && !parent.includes(text) ? `${text}\n${parent}` : (parent || text);
            el.dataset.autoHint = '1';
        } else if (el.dataset.autoHint) { el.removeAttribute('title'); delete el.dataset.autoHint; }
    });
}

function readJson(key) { 
    try { return JSON.parse(localStorage.getItem(key)) || null; } catch (e) { return null; } 
}

const $ = (id) => document.getElementById(id);

// ======================================================================
export class Dashboard {
    constructor({ getProject }) {
        this.getProject = getProject;
        const p = new URLSearchParams(location.search);
        const saved = readJson(STATE_KEY) || {};
        this.state = {
            period: p.get('period') || saved.period || 'all',
            end: p.get('end') || null,
            field: p.get('field') || saved.field || 'hyd|mesh2d_tem1',
            layer: p.get('layer') || saved.layer || 'surface',
            station: p.get('station') || saved.station || null,
            tsVar: saved.tsVar || null, tsLayer: saved.tsLayer || 'surface',
            profileVar: saved.profileVar || 'hyd|temperature',
            statusTab: 'quality', tIndex: null,
        };
        this.filters = readJson(FILTER_KEY) || { severities: ['high', 'medium', 'info'], categories: null };
        this.catalog = null; this.summary = null; this.alerts = []; this.thresholds = { substances: {}, physics: {} };
        this.meshVersion = null; this.contextLoaded = false;
        this.loading = false; this.playing = false; this.fieldToken = 0;
        this.map = null; this.profileDebounced = debounce(() => this.loadProfile(), 250);
        this.extraLayers = {};            // key -> MovementLayer | WflowLayer | PollutionLayer
        this.extrasProject = null;
        this.extraTimeDebounced = debounce(() => this.syncExtraTime(), 120);
        this.weather = new WeatherPanel({ getProject });
    }

    // init
    init({ onExpandMap }) {
        this.map = new DashboardMap({
            mapId: 'main-map', legendEl: $('map-legend'), layerListEl: $('layer-list'),
            switchEl: $('basemap-switch'), coordsEl: $('map-coords'), toggleAllBtn: $('layers-toggle-all'),
            onExpand: onExpandMap, onStationSelect: (name) => this.selectStation(name),
        }).init();
        this.map.registerExtra('movement', (on) => this.toggleExtra('movement', on), () => this.openLayerSettings('movement'));
        this.map.registerExtra('wflow', (on) => this.toggleExtra('wflow', on), () => this.openLayerSettings('wflow'));
        this.map.registerExtra('wflow3d', (on) => this.toggleExtra('wflow3d', on), () => this.openLayerSettings('wflow3d'));
        this.map.registerExtra('pollution', (on) => this.toggleExtra('pollution', on), () => this.openLayerSettings('pollution'));
        this.renderSkeletons(); this.bindToolbar(); this.bindPanels(); this.weather.init();
        initAutoHints();     // tên bị cắt (…) -> hint hiện đầy đủ khi rê chuột
        window.addEventListener('resize', () => this.resize());
        api('thresholds').then(t => { this.thresholds = t; }).catch(() => {});
        return this.refresh();
    }

    renderSkeletons() {
        $('kpi-row').innerHTML = Array.from({ length: 6 }, () =>
            `<div class="kpi-card skeleton"><div class="kpi-icon"></div><div class="kpi-body">
                <div class="kpi-label">&nbsp;</div><div class="kpi-value">&nbsp;</div><div class="kpi-sub">&nbsp;</div></div></div>
            `).join('');
    }

    saveState() {
        const { period, field, layer, station, tsVar, tsLayer, profileVar } = this.state;
        try { localStorage.setItem(STATE_KEY, JSON.stringify({ period, field, layer, station, tsVar, tsLayer, profileVar })); } catch (e) { /* ignore */ }
    }

    body(extra = {}) {
        return { projectName: this.getProject(), end: this.state.end, period: this.state.period, ...extra };
    }

    // bindings
    bindToolbar() {
        const seg = $('period-select');
        seg.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.period === this.state.period));
        seg.addEventListener('click', (e) => {
            const b = e.target.closest('[data-period]');
            if (!b || b.dataset.period === this.state.period) return;
            this.state.period = b.dataset.period; this.saveState();
            seg.querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
            this.refresh();
        });
        $('end-date').addEventListener('change', (e) => { this.state.end = e.target.value || null; this.state.tIndex = null; this.refresh(); });
        $('btn-refresh').addEventListener('click', () => this.refresh(true));
        $('waq-scenario').addEventListener('change', (e) => this.switchWaq(e.target.value));
        $('btn-share').addEventListener('click', () => this.share());
        const pop = $('filters-popover');
        $('btn-filters').addEventListener('click', (e) => {
            e.stopPropagation(); this.renderFilterPopover();
            positionPopover(pop, e.currentTarget, 'right'); pop.classList.toggle('show');
        });
        $('filters-apply').addEventListener('click', () => {
            const sev = [...pop.querySelectorAll('[data-sev]')].filter(i => i.checked).map(i => i.dataset.sev);
            const cats = [...pop.querySelectorAll('[data-cat]')];
            const chosen = cats.filter(i => i.checked).map(i => i.dataset.cat);
            this.filters = { severities: sev, categories: chosen.length === cats.length ? null : chosen };
            localStorage.setItem(FILTER_KEY, JSON.stringify(this.filters));
            pop.classList.remove('show'); this.renderAlerts(); this.updateFilterBadge();
        });
        $('filters-reset').addEventListener('click', () => {
            this.filters = { severities: ['high', 'medium', 'info'], categories: null };
            localStorage.removeItem(FILTER_KEY);
            pop.classList.remove('show'); this.renderAlerts(); this.updateFilterBadge();
        });
        pop.addEventListener('click', (e) => e.stopPropagation());
        this.updateFilterBadge();
    }

    bindPanels() {
        // Trường mô phỏng
        $('field-var').addEventListener('change', (e) => { this.state.field = e.target.value; this.saveState(); this.onFieldVarChanged(); });
        $('field-layer').addEventListener('change', (e) => { this.state.layer = e.target.value; this.saveState(); this.loadField(); this.updateUrl(); });
        // Thanh thời gian
        const slider = $('tb-slider');
        const fetchDebounced = debounce(() => this.loadField(), 140);
        slider.addEventListener('input', () => { this.state.tIndex = +slider.value; this.updateTimeLabel(); fetchDebounced(); });
        $('tb-play').addEventListener('click', () => this.togglePlay());
        // Profile
        $('profile-station').addEventListener('change', (e) => this.selectStation(e.target.value));
        $('profile-var').addEventListener('change', (e) => { this.state.profileVar = e.target.value; this.saveState(); this.loadProfile(); });
        // Chuỗi thời gian
        $('ts-var').addEventListener('change', (e) => { this.state.tsVar = e.target.value; this.saveState(); this.loadSeries(); });
        $('ts-layer').addEventListener('change', (e) => { this.state.tsLayer = e.target.value; this.saveState(); this.loadSeries(); });
        // Tab trạng thái
        $('status-tabs').addEventListener('click', (e) => {
            const b = e.target.closest('[data-tab]');
            if (!b) return;
            $('status-tabs').querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
            this.state.statusTab = b.dataset.tab; this.renderStatus();
        });
        // Cảnh báo
        $('btn-all-alerts').addEventListener('click', () => this.openAlertsModal());
        document.querySelectorAll('[data-close-modal]').forEach(b => b.addEventListener('click', () => b.closest('.modal-backdrop').classList.remove('show')));
        $('alerts-modal').addEventListener('click', (e) => { if (e.target.id === 'alerts-modal') e.currentTarget.classList.remove('show'); });
        $('alert-list').addEventListener('click', (e) => {
            const item = e.target.closest('[data-alert]');
            if (!item) return;
            const a = this.alerts.find(x => x.id === item.dataset.alert);
            if (a) this.focusAlert(a);
        });
    }

    // data flow
    async refresh(force = false) {
        if (this.loading) return;
        this.loading = true;
        const btn = $('btn-refresh');
        btn.classList.add('spinning');
        try {
            const catalog = await api('catalog', this.body());
            const changed = force || !this.catalog || catalog.version !== this.catalog.version || catalog.project !== this.catalog.project;
            this.catalog = catalog; this.loadWaqScenarios();
            if (force) {   // Refresh / Reload thresholds / Save: lấy ngưỡng mới cho biểu đồ
                try { 
                    this.thresholds = await api('thresholds'); 
                    this.weather.load(true)
                } catch (e) { /* giữ bản cũ */ }
            } else this.weather.onProjectChanged();
            const [summary, alerts] = await Promise.all([api('summary', this.body()), api('alerts', this.body({ limit: 100 }))]);
            this.summary = summary; this.alerts = alerts.alerts || [];
            if (force) this.contextLoaded = false;       // Refresh -> đọc lại lớp nền (file trong 'others' có thể vừa thêm)
            this.loadContext(); this.updateExtras();
            if (!catalog.has_results || summary.mode === 'empty') { this.renderEmpty(summary); return; }
            $('dash-overview').classList.remove('is-empty');
            if (changed) await this.setupSimulation();
            this.syncDateInput(summary); this.renderKpis(summary);
            this.renderMonitor(summary); this.renderStatus();
            renderDonut('donut-chart', summary.risk_distribution);
            this.renderDonutLegend(summary.risk_distribution);
            this.renderFooter(summary); this.renderAlerts();
            // Thời điểm bản đồ = cuối cửa sổ phân tích (trừ khi người dùng đã kéo thanh thời gian)
            if (this.state.tIndex === null || !changed) this.state.tIndex = this.indexOf(this.fieldSource(), summary.window?.end);
            this.syncSlider();
            await Promise.all([this.loadField(), this.loadSeries(), this.loadProfile()]);
            this.updateUrl();
            if (force) toast('Dashboard refreshed');
        } catch (err) {
            console.error(err); this.renderError(err);
            // Dự án không tồn tại / không đọc được: xoá bản đồ, mọi lớp mờ
            this.catalog = null; this.meshVersion = null; this.contextLoaded = false;
            this.map.setMesh(null); this.map.setStations([]); this.map.setAlerts([]); this.map.setContext(null);
            this.updateExtras();
        } finally { this.loading = false; btn.classList.remove('spinning'); }
    }

    // Bộ chọn nhanh kịch bản WAQ (cách 2, song song với Visualization): danh sách từ /dashboard/waq_scenarios
    async loadWaqScenarios() {
        const field = $('waq-field'), sel = $('waq-scenario');
        try {
            const res = await api('waq_scenarios', this.body());
            this.waqInfo = res;
            const list = res.scenarios || [];
            field.hidden = list.length === 0;
            sel.innerHTML = list.map(s => `<option value="${esc(s.name)}" ${!s.his && !s.map ? 'disabled' : ''}>${esc(s.name)}</option>`).join('');
            sel.value = list.some(s => s.name === res.current) ? res.current : '';
            if (!sel.value && list.length) sel.insertAdjacentHTML('afterbegin', '<option value="" selected disabled>Select WAQ scenario</option>');
        } catch (err) { console.warn('waq scenarios', err); field.hidden = true; }
    }
    // Đổi kịch bản WAQ: dùng đúng endpoint /setup_database như Visualization -> config.json, cache dự án,
    // lớp WAQ… cập nhật giống hệt; sau đó dashboard tải lại toàn bộ.
    async switchWaq(name) {
        const sel = $('waq-scenario'), info = this.waqInfo || {};
        const sc = (info.scenarios || []).find(s => s.name === name);
        if (!sc || name === info.current) return;
        sel.disabled = true; toast(`Opening WAQ scenario '${name}'…`);
        try {
            const res = await fetch('/setup_database', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ projectName: this.getProject(), params: [...info.hyd_params, sc.his, sc.map],
                    waqName: name, waqModel: sc.model_type, gisChanged: false }),
            }).then(r => r.json());
            if (!res || res.status === 'error') throw new Error(res?.message || res?.detail || 'Cannot open the WAQ scenario');
            // Lớp ô nhiễm giữ danh sách chất của kịch bản cũ -> dựng lại (tự bật lại nếu đang tick)
            this.extraLayers.pollution?.destroy(); delete this.extraLayers.pollution;
            this.closeLayerSettings('pollution'); this.map.setExtraLegend('pollution', null);
            this.state.tIndex = null;
            await this.refresh(true);
            toast(`WAQ scenario: ${name}`);
        } catch (err) {
            console.warn(err); toast(`WAQ: ${err.message}`);
            sel.value = info.current || '';
        } finally { sel.disabled = false; }
    }

    async loadContext() {
        if (this.contextLoaded === this.getProject()) return;
        try {
            const res = await api('layers', this.body());
            this.map.setContext(res.layers); this.contextLoaded = this.getProject();
            if (!this.catalog?.has_results) this.map.fit();
        } catch (err) { console.warn('context layers', err); this.map.setContext(null); }   // lớp nền: mờ
    }

    async setupSimulation() {
        const c = this.catalog;
        // Bộ chọn biến bản đồ (nhóm theo HYD/WAQ)
        const groups = { hyd: 'Hydrodynamics (HYD)', waq: 'Water quality (WAQ)' };
        const opt = (v) => `<option value="${v.source}|${esc(v.name)}">${esc(v.label)}${v.unit ? ` (${esc(v.unit)})` : ''}</option>`;
        const build = (vars) => Object.entries(groups).map(([src, label]) => {
            const list = vars.filter(v => v.source === src);
            return list.length ? `<optgroup label="${label}">${list.map(opt).join('')}</optgroup>` : '';
        }).join('');
        $('field-var').innerHTML = build(c.map_variables);
        // Không có file map -> chỉ kết quả tại trạm: ẩn bộ chọn trường & thanh thời gian
        $('dash-overview').classList.toggle('no-map', !c.map_variables.length);
        if (!c.map_variables.some(v => `${v.source}|${v.name}` === this.state.field) && c.map_variables.length) {
            this.state.field = `${c.map_variables[0].source}|${c.map_variables[0].name}`;
        }
        $('field-var').value = this.state.field;
        $('field-layer').value = this.state.layer;
        // Trạm: gộp HYD & WAQ cùng tên CHỈ KHI cùng vị trí (< 150 m) hoặc một bên chưa có toạ độ;
        // nếu cùng tên mà khác vị trí thì giữ hai điểm riêng (toạ độ WAQ lấy từ kịch bản WAQ).
        const distM = (a, b) => {
            const R = 6371000, toRad = Math.PI / 180;
            const dLat = (b.lat - a.lat) * toRad, dLon = (b.lon - a.lon) * toRad * Math.cos(((a.lat + b.lat) / 2) * toRad);
            return R * Math.hypot(dLat, dLon);
        };
        const list = [];
        ['hyd', 'waq'].forEach(src => (c.stations[src] || []).forEach(s => {
            const same = list.find(m => m.name === s.name && (m.lat === null || s.lat === null || distM(m, s) < 150));
            if (same) {
                if (same.lat === null && s.lat !== null) { same.lat = s.lat; same.lon = s.lon; }
                same.sources.push(src); same.coords[src] = s.lat !== null ? [s.lat, s.lon] : null;
            } else {
                list.push({ name: s.name, lat: s.lat, lon: s.lon, sources: [src], coords: { [src]: s.lat !== null ? [s.lat, s.lon] : null } });
            }
        }));
        this.stations = list;
        const stationNames = [...new Set(list.map(s => s.name))];
        if (!this.state.station || !stationNames.includes(this.state.station)) {
            const firstAlert = this.alerts.find(a => stationNames.includes(a.location));
            this.state.station = firstAlert ? firstAlert.location : stationNames[0] || null;
        }
        $('profile-station').innerHTML = stationNames.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
        $('profile-station').value = this.state.station || '';
        // Biến trạm
        const his = c.his_variables;
        $('ts-var').innerHTML = build(his);
        // Biến vẽ được profile: his có lớp, hoặc biến map tương ứng có lớp (WAQ his thường chỉ có lớp mặt)
        const profVars = c.profile_variables || his.filter(v => v.layered);
        $('profile-var').innerHTML = build(profVars);
        const critical = this.summary?.gauges?.critical?.[0];
        if (!this.state.tsVar || !his.some(v => `${v.source}|${v.name}` === this.state.tsVar)) {
            this.state.tsVar = critical && his.some(v => v.source === 'waq' && v.name === critical.key) ? `waq|${critical.key}` : 'hyd|temperature';
        }
        if (!profVars.some(v => `${v.source}|${v.name}` === this.state.profileVar)) this.state.profileVar = profVars.length ? `${profVars[0].source}|${profVars[0].name}` : '';
        $('ts-var').value = this.state.tsVar; $('profile-var').value = this.state.profileVar; $('ts-layer').value = this.state.tsLayer;
        this.map.selectStation(this.state.station);
        this.map.setStations(this.stations);
        // Lưới
        if (this.meshVersion !== c.version) {
            try {
                const res = await api('mesh', this.body());
                this.map.setMesh(res.mesh); this.meshVersion = c.version;
                this.map.fit();
            } catch (err) { console.warn(err); this.map.setMesh(null); toast(`Mesh: ${err.message}`); }
        }
        this.onFieldVarChanged(false);
    }

    // field & time
    fieldSource() { return (this.state.field || 'hyd|').split('|')[0]; }

    fieldMeta() {
        const [source, name] = this.state.field.split('|');
        return this.catalog?.map_variables.find(v => v.source === source && v.name === name) || { source, name, layered: true };
    }

    indexOf(source, iso) {
        const times = this.catalog?.times?.[source] || [];
        if (!times.length) return 0;
        if (!iso) return times.length - 1;
        let idx = times.length - 1;
        for (let i = 0; i < times.length; i++) { if (times[i] > iso) { idx = Math.max(0, i - 1); break; } }
        return idx;
    }

    onFieldVarChanged(load = true) {
        const meta = this.fieldMeta();
        $('field-layer').disabled = !meta.layered;
        $('field-layer').title = meta.layered ? 'Layer' : 'Single-layer variable';
        // Đổi nguồn HYD <-> WAQ: giữ cùng thời điểm
        const times = this.catalog?.times?.[meta.source] || [];
        const iso = this._currentTime;
        if (iso) this.state.tIndex = this.indexOf(meta.source, iso);
        this.state.tIndex = Math.min(this.state.tIndex ?? times.length - 1, times.length - 1);
        this.syncSlider();
        if (load) { this.loadField(); this.updateUrl(); }
    }

    syncSlider() {
        const times = this.catalog?.times?.[this.fieldSource()] || [];
        const slider = $('tb-slider');
        slider.max = Math.max(0, times.length - 1);
        slider.value = this.state.tIndex ?? times.length - 1;
        this.updateTimeLabel();
    }

    updateTimeLabel() {
        const times = this.catalog?.times?.[this.fieldSource()] || [];
        const iso = times[this.state.tIndex];
        this._currentTime = iso;
        $('tb-label').textContent = iso ? iso.replace('T', ' ').slice(0, 16) : '-';
        this.extraTimeDebounced();
    }

    // lớp bổ sung: movement & Wflow
    async updateExtras() {
        const project = this.getProject();
        if (this.extrasProject !== project) {          // đổi dự án -> huỷ lớp cũ
            Object.values(this.extraLayers).forEach(l => l.destroy());
            this.extraLayers = {}; this.extrasProject = project;
            this.closeLayerSettings();
        }
        let status;
        try { status = await api('extras', this.body()); }
        catch (err) { status = Object.fromEntries(Object.keys(EXTRAS).map(k => [k, { available: false, reason: err.message }])); }
        for (const key of Object.keys(EXTRAS)) {
            const st = status[EXTRAS[key].statusKey || key] || { available: false };
            this.map.setAvailability(key, st);
            if (!st.available && this.extraLayers[key]) { this.extraLayers[key].destroy(); delete this.extraLayers[key]; this.closeLayerSettings(key); }
            // Đã bật từ lần trước và có dữ liệu -> bật lại
            if (st.available && this.map.isOn(key) && !this.extraLayers[key]?.visible) this.toggleExtra(key, true);
        }
    }

    async toggleExtra(key, on) {
        if (!on) {
            this.extraLayers[key]?.hide(); this.map.setExtraLegend(key, null);
            this.closeLayerSettings(key); return;
        }
        try {
            let layer = this.extraLayers[key];
            if (!layer) {
                this.map.setLoading(key, true);
                const Cls = EXTRAS[key].Cls;
                layer = await new Cls(this.map.map, { projectName: this.getProject() }).init();
                this.extraLayers[key] = layer;
            }
            layer.setPlaying?.(this.playing);
            await layer.setTime(this._currentTime);
            if (!this.map.isOn(key)) return;            // người dùng đã tắt trong lúc chờ
            layer.show();
            this.map.setExtraLegend(key, layer.legend());
            if (EXTRAS[key].openOnShow && $('layer-settings')?.dataset.key !== key) this.openLayerSettings(key);
        } catch (err) {
            console.warn(key, err); toast(`${EXTRAS[key].name}: ${err.message}`); this.map.setChecked(key, false);
        } finally { this.map.setLoading(key, false); }
    }

    // Bảng ⚙ nổi trên bản đồ: nội dung do từng lớp dựng (renderSettings).
    // Kéo tiêu đề để di chuyển, kéo góc dưới-phải để đổi kích thước; vị trí/kích thước lưu theo lớp.
    // Nút × đóng bảng VÀ tắt lớp (bỏ tick trong Map Layers); bấm lại ⚙ chỉ đóng bảng.
    openLayerSettings(key) {
        const layer = this.extraLayers[key];
        let panel = $('layer-settings');
        if (panel?.dataset.key === key) { this.closeLayerSettings(); return; }   // bấm lại ⚙ = đóng bảng
        this.closeLayerSettings();
        if (!layer?.renderSettings) { toast('Layer is still loading…'); return; }
        const title = EXTRAS[key].title; panel = document.createElement('div');
        panel.id = 'layer-settings'; panel.className = 'layer-settings'; panel.dataset.key = key;
        panel.innerHTML = `<div class="ls-head" title="Drag to move"><span>${title}</span>
            <button type="button" class="ls-close" title="Close and hide this layer">×</button></div>
            <div class="ls-scroll"><div class="ls-body"></div></div>
            <div class="ls-grip ls-grip-e" data-dir="e"></div><div class="ls-grip ls-grip-s" data-dir="s"></div>
            <div class="ls-grip ls-grip-se" data-dir="se" title="Drag to resize"></div>`;
        const host = $('panel-map');
        host.appendChild(panel); panel.style.overflow = 'hidden';     // chỉ .ls-scroll cuộn -> tay nắm resize cố định ở góc
        if (window.L) { L.DomEvent.disableClickPropagation(panel); L.DomEvent.disableScrollPropagation(panel); }
        panel.querySelector('.ls-close').onclick = () => {
            this.closeLayerSettings();
            this.map.toggle(key, false);                 // bỏ tick + ẩn lớp (gọi toggleExtra(key, false))
        };
        this.restorePanelRect(panel, key);
        this.makePanelDraggable(panel, key);
        this.makePanelResizable(panel, key);
        // Dòng trạng thái (số hạt đang chạy, khung thời gian…) – cập nhật mỗi giây khi bảng mở
        panel._timer = setInterval(() => {
            const st = panel.querySelector('.ls-status');
            if (st && layer.status) st.textContent = layer.status();
        }, 1000);
        layer.renderSettings(panel.querySelector('.ls-body'), {
            refreshLegend: () => { if (layer.visible) this.map.setExtraLegend(key, layer.legend()); },
            busy: (on) => this.map.setLoading(key, on), toast,
            // Mở công cụ trong Workspace (mainManager bắt click trên [data-tool] ở document;
            // bảng nổi chặn lan truyền click nên phát một click riêng)
            openTool: (id) => {
                const a = document.createElement('a');
                a.dataset.tool = id; a.hidden = true; document.body.appendChild(a); a.click(); a.remove();
            },
        });
    }

    closeLayerSettings(key = null) {
        const panel = $('layer-settings');
        if (panel && (!key || panel.dataset.key === key)) { 
            panel._ro?.disconnect(); clearInterval(panel._timer); panel.remove(); 
        }
    }

    restorePanelRect(panel, key) {
        let r = null;
        try { r = JSON.parse(localStorage.getItem(`dash.layerSettings.rect.${key}`)); } catch (e) { /* ignore */ }
        if (r) {
            if (r.w) panel.style.width = `${r.w}px`;
            if (r.h) panel.style.height = `${r.h}px`;
            if (Number.isFinite(r.x)) { panel.style.left = `${r.x}px`; panel.style.top = `${r.y}px`; }
        }
        this.clampPanel(panel);
        // Lưu kích thước khi người dùng kéo góc resize
        let t = 0;
        panel._ro = new ResizeObserver(() => { 
            clearTimeout(t); t = setTimeout(() => this.savePanelRect(panel, key), 200); 
        });
        panel._ro.observe(panel);
    }

    savePanelRect(panel, key) {
        if (!panel.isConnected) return;
        const r = { x: panel.offsetLeft, y: panel.offsetTop, w: panel.offsetWidth,
                    h: panel.style.height ? panel.offsetHeight : null };
        try { localStorage.setItem(`dash.layerSettings.rect.${key}`, JSON.stringify(r)); } catch (e) { /* ignore */ }
    }

    // Giữ bảng nằm trong khung bản đồ
    clampPanel(panel) {
        const host = $('panel-map');
        const maxX = Math.max(0, host.clientWidth - panel.offsetWidth), maxY = Math.max(0, host.clientHeight - 40);
        panel.style.left = `${Math.min(Math.max(0, panel.offsetLeft), maxX)}px`;
        panel.style.top = `${Math.min(Math.max(0, panel.offsetTop), maxY)}px`;
        const hMax = host.clientHeight - panel.offsetTop - 8;
        panel.style.maxHeight = `${Math.max(160, hMax)}px`;
    }

    // Đổi kích thước bằng tay nắm riêng (cạnh phải, cạnh dưới, góc dưới-phải) – không dựa vào
    // CSS resize vì thanh cuộn của nội dung che mất góc resize trên Windows.
    makePanelResizable(panel, key) {
        panel.querySelectorAll('.ls-grip').forEach(grip => {
            grip.addEventListener('pointerdown', (e) => {
                if (e.button !== 0) return;
                e.preventDefault(); e.stopPropagation();
                grip.setPointerCapture(e.pointerId);
                const dir = grip.dataset.dir, x0 = e.clientX, y0 = e.clientY;
                const w0 = panel.offsetWidth, h0 = panel.offsetHeight, host = $('panel-map');
                panel.classList.add('resizing');
                const move = (ev) => {
                    if (dir.includes('e')) {
                        const wMax = host.clientWidth - panel.offsetLeft - 4;
                        panel.style.width = `${Math.min(wMax, Math.max(240, w0 + ev.clientX - x0))}px`;
                    }
                    if (dir.includes('s')) {
                        const hMax = host.clientHeight - panel.offsetTop - 4;
                        panel.style.maxHeight = `${hMax}px`;
                        panel.style.height = `${Math.min(hMax, Math.max(140, h0 + ev.clientY - y0))}px`;
                    }
                };
                const up = () => {
                    grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up);
                    grip.removeEventListener('pointercancel', up);
                    panel.classList.remove('resizing');
                    this.savePanelRect(panel, key);
                };
                grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
                grip.addEventListener('pointercancel', up);
            });
        });
    }

    makePanelDraggable(panel, key) {
        const head = panel.querySelector('.ls-head');
        head.addEventListener('pointerdown', (e) => {
            if (e.button !== 0 || e.target.closest('button')) return;
            e.preventDefault();
            head.setPointerCapture(e.pointerId);
            const x0 = e.clientX - panel.offsetLeft, y0 = e.clientY - panel.offsetTop;
            panel.classList.add('dragging');
            const move = (ev) => {
                panel.style.left = `${ev.clientX - x0}px`; panel.style.top = `${ev.clientY - y0}px`;
                this.clampPanel(panel);
            };
            const up = () => {
                head.removeEventListener('pointermove', move); head.removeEventListener('pointerup', up);
                head.removeEventListener('pointercancel', up);
                panel.classList.remove('dragging');
                this.savePanelRect(panel, key);
            };
            head.addEventListener('pointermove', move); head.addEventListener('pointerup', up);
            head.addEventListener('pointercancel', up);
        });
        head.addEventListener('dblclick', () => {           // nhấp đúp tiêu đề: về vị trí/kích thước mặc định
            try { localStorage.removeItem(`dash.layerSettings.rect.${key}`); } catch (e) { /* ignore */ }
            panel.style.left = panel.style.top = panel.style.width = panel.style.height = '';
            this.clampPanel(panel);
        });
    }

    async syncExtraTime() {
        for (const [key, layer] of Object.entries(this.extraLayers)) {
            if (!layer.visible) continue;
            try { await layer.setTime(this._currentTime); this.map.setExtraLegend(key, layer.legend()); }
            catch (err) {
                console.warn(key, err);
                if (!layer._warned) { layer._warned = true; toast(`${EXTRAS[key].name}: ${err.message}`); }
            }
        }
    }

    paletteFor(meta) {
        if (meta.source === 'waq') {
            const rule = this.findRule(meta.name);
            return rule?.direction === 'below' ? 'quality_rev' : 'quality';
        }
        return meta.palette || 'thermal';
    }

    async loadField() {
        if (!this.catalog?.has_results) return;
        const meta = this.fieldMeta();
        const times = this.catalog.times[meta.source] || [];
        const token = ++this.fieldToken;
        try {
            const res = await api('field', this.body({ source: meta.source, name: meta.name, layer: meta.layered ? this.state.layer : 'surface', time: times[this.state.tIndex] }));
            if (token !== this.fieldToken) return;     // đã có yêu cầu mới hơn
            const layerLbl = meta.layered ? ` · ${$('field-layer').selectedOptions[0]?.textContent.toLowerCase()}` : '';
            this.map.setField({ ...res, label: `${res.label}${layerLbl}`, palette: this.paletteFor(meta) });
            if (!this.playing) this.profileDebounced();
        } catch (err) {
            console.warn(err); toast(`Field: ${err.message}`);
        }
    }

    async togglePlay() {
        const btn = $('tb-play');
        this.playing = !this.playing;
        Object.values(this.extraLayers).forEach(l => l.setPlaying?.(this.playing));
        btn.classList.toggle('playing', this.playing);
        btn.innerHTML = icon(this.playing ? 'pause' : 'play', 16);
        const times = this.catalog?.times?.[this.fieldSource()] || [];
        while (this.playing && times.length) {
            this.state.tIndex = (this.state.tIndex + 1) % times.length;
            $('tb-slider').value = this.state.tIndex; this.updateTimeLabel();
            const t0 = performance.now();
            await this.loadField();
            await new Promise(r => setTimeout(r, Math.max(120, 450 - (performance.now() - t0))));
        }
        if (!this.playing) this.loadProfile();   // dừng -> cập nhật profile tại thời điểm hiện tại
    }

    // station / profile / series
    selectStation(name) {
        if (!name) return;
        this.state.station = name; this.saveState();
        $('profile-station').value = name;
        this.map.selectStation(name);
        this.loadProfile();
        this.loadSeries();
        this.updateUrl();
    }

    async loadProfile() {
        if (!this.catalog?.has_results || !this.state.profileVar || !this.state.station) { showEmpty('profile-chart', 'Select a station'); return; }
        // Backend tự lấy profile từ file map khi file his không có lớp hoặc không có trạm này
        const [source, name] = this.state.profileVar.split('|');
        const times = this.catalog.map_variables.length ? (this.catalog.times[this.fieldSource()] || []) : [];
        try {
            const prof = await api('profile', this.body({ source, name, station: this.state.station, time: times[this.state.tIndex] || this.summary?.window?.end }));
            if (prof.status === 'empty') {
                $('profile-sub').textContent = this.state.station;
                showEmpty('profile-chart', `No profile: ${esc(prof.message)}`);
                return;
            }
            const origin = prof.from === 'map' ? ` · ${source.toUpperCase()} map, cell ${prof.cell} (${prof.cell_distance_m} m)` : '';
            $('profile-sub').textContent = `${prof.station} · ${fmtDate(prof.time, true)}${origin}`;
            renderProfile('profile-chart', prof, { threshold: source === 'waq' ? this.findRule(name) : null });
        } catch (err) {
            $('profile-sub').textContent = this.state.station;
            showEmpty('profile-chart', esc(err.message));
        }
    }

    async loadSeries() {
        if (!this.catalog?.has_results || !this.state.tsVar) return;
        const [source, name] = this.state.tsVar.split('|');
        const meta = this.catalog.his_variables.find(v => v.source === source && v.name === name);
        $('ts-layer').disabled = !meta?.layered;
        try {
            const data = await api('station', this.body({ source, name, layer: meta?.layered ? this.state.tsLayer : 'surface' }));
            $('ts-sub').textContent = `${source.toUpperCase()} · ${data.label}${data.unit ? ` (${data.unit})` : ''} · ${PERIOD_LABEL[this.state.period] || ''}`;
            renderStationSeries('ts-chart', data, { threshold: source === 'waq' ? this.findRule(name) : null, highlight: this.state.station });
        } catch (err) {
            showEmpty('ts-chart', `<span class="error-state">${esc(err.message)}</span>`);
        }
    }

    findRule(name) {
        const key = String(name || '').replace('mesh2d_2d_', '').replace('mesh2d_', '');
        const subs = this.thresholds.substances || {};
        const hit = Object.keys(subs).find(k => k.toLowerCase() === key.toLowerCase());
        return hit ? subs[hit] : null;
    }

    // render
    syncDateInput(summary) {
        const input = $('end-date');
        if (summary.range?.start) input.min = summary.range.start.slice(0, 10);
        if (summary.range?.end) input.max = summary.range.end.slice(0, 10);
        input.value = (summary.window?.end || '').slice(0, 10);
    }

    renderKpis(summary) {
        $('kpi-row').innerHTML = summary.kpis.map(k => {
            const tone = k.key === 'risk' ? `sev-${k.severity || 'low'}` : (KPI_TONES[k.key] || 'tone-blue');
            const isText = typeof k.value === 'string';
            const value = isText ? esc(k.value) : fmtNumber(k.value, k.decimals ?? 2);
            let sub;
            if (k.delta !== null && k.delta !== undefined) {
                const cls = k.delta > 0 ? 'up' : k.delta < 0 ? 'down' : 'flat';
                const arrow = k.delta > 0 ? '▲' : k.delta < 0 ? '▼' : '■';
                sub = `<span class="${cls}">${arrow} ${Math.abs(k.delta).toFixed(1)} ${esc(k.delta_unit || '')}</span><span>${esc(k.delta_label || '')}</span>`;
            } else if (k.status) {
                const sevCls = k.key === 'quality' ? `sev-${k.severity || 'info'}` : '';
                sub = `<span class="kpi-status ${sevCls}">${esc(k.status)}</span>`;
            } else sub = `<span>${esc(k.hint || '')}</span>`;
            // Ngưỡng vật lý (nhiệt độ, dòng chảy): nhãn nhỏ cạnh tên KPI khi vượt mức medium/high
            const lim = k.limit, flagged = lim && (lim.severity === 'high' || lim.severity === 'medium');
            const flag = flagged ? `<span class="kpi-flag sev-${lim.severity}">${lim.severity === 'high' ? 'High' : 'Medium'}</span>` : '';
            const tip = [k.hint || k.label, lim?.text].filter(Boolean).join('\n');
            return `<div class="kpi-card ${tone}" title="${esc(tip)}">
                <div class="kpi-icon">${icon(k.icon, 20)}</div>
                <div class="kpi-body">
                    <div class="kpi-label"><span class="kpi-name">${esc(k.label)}</span>${flag}</div>
                    <div class="kpi-value ${k.key === 'risk' ? 'kpi-status' : ''} ${isText && String(k.value).length > 8 ? 'long' : ''}" ${k.key === 'risk' ? 'style="color:var(--tone)"' : ''}>${value}${k.unit && !isText && k.value !== null ? `<span class="unit">${esc(k.unit)}</span>` : ''}</div>
                    <div class="kpi-sub">${sub}</div>
                </div></div>`;
        }).join('');
    }

    renderMonitor(summary) {
        const g = summary.gauges || {};
        const strat = g.stratification || {};
        const sevColor = (s) => ({ high: 'var(--sev-high)', medium: 'var(--sev-medium)', low: 'var(--sev-low)' }[s] || 'var(--text-2)');
        const q = g.quality || {};
        const qTip = q.total ? `${q.ok} of ${q.total} WAQ substances within limits`
            + (q.high?.length ? ` · high: ${q.high.join(', ')}` : '') + (q.medium?.length ? ` · medium: ${q.medium.join(', ')}` : '') : (q.summary || '');
        const cells = [`
            <div class="monitor-cell" title="${esc(qTip)}">
                <span class="m-label">Within limits</span>
                <div id="gauge-wqi"></div>
                <span class="m-sub" style="color:${sevColor(q.severity)}">${q.total ? `${q.ok}/${q.total} · ${esc(q.label)}` : esc(q.label || '--')}</span>
            </div>`];
        const critical = (g.critical || []).slice(0, 2);
        if (critical.length < 2) {
            cells.push(`<div class="monitor-cell" title="${esc(strat.hint || '')}">
                <span class="m-label">Stratification ΔT</span>
                <span class="m-icon" style="background:var(--accent-2-soft); color:var(--accent-2)">${icon('thermo', 18)}</span>
                <span class="m-value">${fmtNumber(strat.value, 1)}<small>°C</small></span>
                <span class="m-sub" style="color:${strat.value >= 3 ? 'var(--sev-medium)' : 'var(--sev-low)'}">${esc(strat.label || '--')}</span>
            </div>`);
        }
        critical.forEach(c => cells.push(`
            <div class="monitor-cell" title="${esc(c.label)}: ${esc(fmtVal(c.value))} ${esc(c.unit)} at ${esc(c.location)} (${esc(c.severity)})">
                <span class="m-label">${esc(c.label)}</span>
                <span class="m-icon" style="background:color-mix(in srgb, ${sevColor(c.severity)} 18%, transparent); color:${sevColor(c.severity)}">${icon(c.key === 'OXY' ? 'droplet' : 'flask', 18)}</span>
                <span class="m-value">${fmtVal(c.value)}<small> ${esc(c.unit)}</small></span>
                <span class="m-sub" style="color:${sevColor(c.severity)}">${esc(c.location)}</span>
            </div>`));
        while (cells.length < 3) cells.push('<div class="monitor-cell"></div>');
        $('monitor-grid').innerHTML = cells.join('');
        if (q.pct !== null && q.pct !== undefined) renderGauge('gauge-wqi', q.pct, { min: 0, max: 100, severity: q.severity, suffix: '%' });
        else showEmpty('gauge-wqi', 'No WAQ');
        $('monitor-sub').textContent = strat.surface !== undefined && strat.surface !== null
            ? `Surface ${fmtNumber(strat.surface, 1)}°C · Bottom ${fmtNumber(strat.bottom, 1)}°C · ΔT ${fmtNumber(strat.value, 1)}°C`
            : 'Simulated lake condition';
    }

    renderStatus() {
        const body = $('status-body');
        const s = this.summary;
        if (!s) return;
        if (this.state.statusTab === 'sources') {
            $('status-title').textContent = 'Sources & Sinks';
            $('status-sub').textContent = `Mean discharge (HYD) · ${PERIOD_LABEL[this.state.period] || ''}`;
            const items = s.contributions || [];
            if (!items.length) { body.innerHTML = '<div class="empty-state">No source/sink data in the HYD his file</div>'; return; }
            const srcs = items.filter(i => i.kind === 'source'), sinks = items.filter(i => i.kind === 'sink');
            const colors = ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--series-6'];
            const max = Math.max(...srcs.map(i => i.share || 0), 1);
            body.innerHTML = `<div class="bars">${srcs.slice(0, 5).map((it, i) => `
                <div class="bar-row" style="--c:var(${colors[i % colors.length]})" title="${esc(it.name)}: ${fmtNumber(it.value, 3)} m³/s">
                    <span class="b-ico">${icon('droplet', 15)}</span><span class="b-name">${esc(it.name)}</span>
                    <div class="bar-track"><div class="bar-fill" data-w="${(100 * (it.share || 0) / max).toFixed(1)}"></div></div>
                    <span class="b-val">${fmtNumber(it.value, 3)}</span>
                </div>`).join('')}</div>
                <div class="bars-foot"><span>In <strong>${fmtNumber(srcs.reduce((a, b) => a + (b.value || 0), 0), 3)} m³/s</strong></span>
                <span>Out ${sinks.length ? sinks.map(k => `${esc(k.name)} <strong>${fmtNumber(Math.abs(k.value), 3)}</strong>`).join(', ') : '<strong>-</strong>'}</span></div>`;
            requestAnimationFrame(() => body.querySelectorAll('.bar-fill').forEach(b => { b.style.width = b.dataset.w + '%'; }));
            return;
        }
        $('status-title').textContent = 'Water Quality Status';
        $('status-sub').textContent = 'Worst value in window vs. high limit';
        const rows = (s.quality || []).slice(0, 6);
        if (!rows.length) { body.innerHTML = '<div class="empty-state">No thresholds match the WAQ substances.<br>Edit backend/src/dashboard_thresholds.json</div>'; return; }
        body.innerHTML = rows.map(c => {
            const th = c.threshold || {};
            const medPos = th.direction === 'below' ? 100 * th.high / th.medium : 100 * th.medium / th.high;
            return `<div class="q-row sev-${c.severity}" title="${esc(c.label)} · ${esc(c.location)} · ${fmtDate(c.time, true)}${c.exceed_pct !== undefined && c.exceed_pct !== null ? ` · above medium ${c.exceed_pct}% of time` : ''}">
                <span class="q-name">${esc(c.label)} <small>· ${esc(c.location)}</small></span>
                <span class="q-val">${fmtVal(c.value)} <small style="color:var(--muted); font-weight:500">${esc(c.unit)}</small></span>
                <div class="q-track"><div class="q-fill" data-w="${Math.min(100, c.ratio || 0)}"></div>
                    <span class="q-tick med" style="left:${Math.min(99, medPos).toFixed(1)}%"></span><span class="q-tick" style="left:calc(100% - 2px)"></span></div>
            </div>`;
        }).join('');
        requestAnimationFrame(() => body.querySelectorAll('.q-fill').forEach(b => { b.style.width = b.dataset.w + '%'; }));
    }

    renderDonutLegend(dist) {
        const total = (dist?.high || 0) + (dist?.medium || 0) + (dist?.low || 0);
        const row = (k, label) => `<div class="dl-row sev-${k}"><span class="dl-dot"></span><span class="dl-name">${label}</span>
            <span class="dl-val">${total ? Math.round(100 * (dist?.[k] || 0) / total) : 0}%</span></div>`;
        $('donut-legend').innerHTML = row('high', 'High') + row('medium', 'Medium') + row('low', 'Within limits');
    }

    filteredAlerts() {
        const { severities, categories } = this.filters;
        return this.alerts.filter(a => severities.includes(a.severity) && (!categories || categories.includes(a.category)));
    }

    renderAlerts() {
        const list = $('alert-list');
        const alerts = this.filteredAlerts();
        this.map.setAlerts(alerts);
        $('alerts-sub').textContent = `${alerts.length} active · ${alerts.filter(a => a.severity === 'high').length} high`;
        if (!alerts.length) { list.innerHTML = '<div class="empty-state">No alerts match the current filters</div>'; return; }
        list.innerHTML = alerts.slice(0, 12).map(a => `
            <div class="alert-item sev-${a.severity}" data-alert="${esc(a.id)}" title="${esc(a.detail || a.title)}">
                <span class="alert-ico">${icon(SEV_ICON[a.severity], 16)}</span>
                <div class="alert-text"><div class="alert-title">${esc(a.title)}</div>
                    <div class="alert-meta">${esc(a.location || '-')} · ${esc(a.category)}</div></div>
                <span class="alert-date">${a.time ? fmtDate(a.time) : ''}</span>
            </div>`).join('');
    }

    focusAlert(a) {
        // Nhảy tới trạm, chọn trạm, hiển thị chất tương ứng trong chuỗi thời gian & bản đồ
        if (a.source === 'waq' && this.catalog?.his_variables.some(v => v.source === 'waq' && v.name === a.key)) {
            this.state.tsVar = `waq|${a.key}`; $('ts-var').value = this.state.tsVar;
            const mapVar = this.catalog.map_variables.find(v => v.source === 'waq' && (v.name === a.key || v.name === `mesh2d_${a.key}`));
            if (mapVar) { this.state.field = `waq|${mapVar.name}`; $('field-var').value = this.state.field; this.onFieldVarChanged(false); }
            if (a.time) { this.state.tIndex = this.indexOf(this.fieldSource(), a.time); this.syncSlider(); }
            this.loadField();
        }
        if (this.stations?.some(s => s.name === a.location)) this.selectStation(a.location);
        if (!this.map.focusAlert(a)) toast(`${a.title}`);
    }

    openAlertsModal() {
        const checks = this.summary?.checks || [];
        $('alerts-modal-body').innerHTML = `
            <p style="margin:0 0 10px; color:var(--muted); font-size:12.5px">
                ${this.filteredAlerts().length} alerts after filters · ${checks.length} checks for
                ${fmtDate(this.summary?.window?.start, true)} – ${fmtDate(this.summary?.window?.end, true)}.
                Thresholds: <code>backend/src/dashboard_thresholds.json</code>.</p>
            <table class="alert-table">
                <thead><tr><th>Severity</th><th>Check</th><th>Worst value</th><th>% time &gt; medium</th><th>Location</th><th>Time</th></tr></thead>
                <tbody>${checks.map(c => `
                    <tr class="sev-${c.severity}">
                        <td><span class="sev-pill">${c.severity === 'low' ? 'ok' : c.severity}</span></td>
                        <td>${esc(c.label)}<div style="color:var(--muted); font-size:11px">${esc(c.category)} · ${esc((c.source || '').toUpperCase())}${c.detail ? ' · ' + esc(c.detail) : ''}</div></td>
                        <td class="num">${c.value === null || c.value === undefined ? '-' : fmtVal(c.value, c.unit)}</td>
                        <td class="num">${c.exceed_pct === undefined || c.exceed_pct === null ? '-' : c.exceed_pct + ' %'}</td>
                        <td>${esc(c.location || '-')}</td>
                        <td>${c.time ? fmtDate(c.time, true) : '-'}</td>
                    </tr>`).join('')}</tbody>
            </table>`;
        $('alerts-modal').classList.add('show');
    }

    renderEmpty(summary) {
        $('dash-overview').classList.add('is-empty');
        const pipe = summary.inventory?.pipeline || [];
        $('empty-steps').innerHTML = pipe.map((p, i) => `
            <button class="step ${p.done ? 'done' : ''}" data-tool="${esc(p.tool)}" title="Open ${esc(p.label)} tool">
                <span class="n">${p.done ? '✓' : i + 1}</span>${icon(STEP_ICONS[p.key] || 'play', 18)}<span>${esc(p.label)}</span>
            </button>`).join('');
        this.renderFooter(summary);
        $('page-subtitle').textContent = `Project ${this.getProject()} · no HYD/WAQ results yet`;
        this.map.setMesh(null); this.map.setStations([]); this.map.setAlerts([]);
        this.map.invalidate(true);
    }

    renderFilterPopover() {
        const pop = $('filters-popover');
        pop.querySelectorAll('[data-sev]').forEach(i => { i.checked = this.filters.severities.includes(i.dataset.sev); });
        const cats = [...new Set(this.alerts.map(a => a.category))].sort();
        $('filter-categories').innerHTML = cats.length
            ? cats.map(c => `<label class="check-row"><input type="checkbox" data-cat="${esc(c)}" ${!this.filters.categories || this.filters.categories.includes(c) ? 'checked' : ''}> ${esc(c)}</label>`).join('')
            : '<div style="color:var(--muted); font-size:12px">No categories</div>';
    }

    updateFilterBadge() {
        const btn = $('btn-filters');
        const active = this.filters.severities.length < 3 || !!this.filters.categories;
        btn.classList.toggle('active-filter', active);
        btn.title = active ? 'Alert filters (active)' : 'Alert filters';
    }

    renderFooter(summary) {
        $('data-source').textContent = summary.data_source;
        $('source-dot').classList.toggle('warn', summary.mode === 'empty' || /synthetic/i.test(summary.data_source || ''));
        $('last-updated').textContent = fmtDate(summary.inventory?.last_result || summary.generated, true);
        const pipe = summary.inventory?.pipeline || [];
        $('pipeline').innerHTML = pipe.map(p => `<span class="${p.done ? 'done' : ''}" title="${esc(p.label)}: ${p.done ? 'done' : 'pending'}"></span>`).join('');
        $('pipeline-pct').textContent = pipe.length ? `· ${summary.inventory.pipeline_progress}%` : '';
        const sim = summary.simulation || {};
        if (summary.mode !== 'empty' && sim.hyd) {
            const h = sim.hyd, w = sim.waq;
            const parts = [`HYD: ${h.scenario || '-'} (${fmtDate(h.start)} - ${fmtDate(h.end)}${h.step_minutes ? `, ${h.step_minutes / 60} h step` : ''}${h.n_layers ? `, ${h.n_layers} layers` : ''})`];
            parts.push(w?.available ? `WAQ: ${w.scenario}` : 'no WAQ');
            $('page-subtitle').textContent = parts.join(' · ');
        }
    }

    renderError(err) {
        $('kpi-row').innerHTML = `<div class="kpi-card" style="grid-column:1/-1"><div class="kpi-icon" style="--tone:var(--sev-high)">${icon('alert', 20)}</div>
            <div class="kpi-body"><div class="kpi-label">Could not load dashboard data</div>
            <div class="kpi-sub error-state">${esc(err.message)}</div></div></div>`;
    }

    // misc
    updateUrl() {
        const url = new URL(location.href);
        url.searchParams.set('period', this.state.period);
        if (this.state.end) url.searchParams.set('end', this.state.end); else url.searchParams.delete('end');
        url.searchParams.set('field', this.state.field);
        url.searchParams.set('layer', this.state.layer);
        if (this.state.station) url.searchParams.set('station', this.state.station);
        history.replaceState(null, '', url);
    }

    async share() {
        this.updateUrl();
        try { await navigator.clipboard.writeText(location.href); toast('Link copied to clipboard'); }
        catch (e) { prompt('Copy this link:', location.href); }
    }

    resize(refit = false) {
        this.map?.invalidate(refit);
        resizeCharts(['gauge-wqi', 'donut-chart', 'ts-chart', 'profile-chart', 'weather-chart']);
    }
}

export function positionPopover(pop, anchor, align = 'left') {
    const r = anchor.getBoundingClientRect();
    pop.style.top = `${r.bottom + 8 + window.scrollY}px`;
    if (align === 'right') { pop.style.left = 'auto'; pop.style.right = `${Math.max(8, window.innerWidth - r.right)}px`; }
    else { pop.style.right = 'auto'; pop.style.left = `${Math.min(r.left, window.innerWidth - 320)}px`; }
}

export { toast };

export function initSidebarResize({ app, sidebar, onResize = () => {} }) {
    if (!app || !sidebar) return;
    const sash = document.createElement('div');
    sash.className = 'sidebar-sash';
    sash.setAttribute('role', 'separator');
    sash.setAttribute('aria-orientation', 'vertical');
    sash.setAttribute('aria-label', 'Resize sidebar');
    sash.tabIndex = 0;
    sash.title = 'Drag to resize · double-click to reset';
    sash.innerHTML = '<span class="sash-grip" aria-hidden="true"><i></i><i></i><i></i></span>';
    sidebar.appendChild(sash);
    const clamp = (w) => Math.round(Math.min(MAX_W, Math.max(MIN_W, w)));
    const current = () => sidebar.getBoundingClientRect().width;
    let frame = null;
    const notify = () => {                       // bản đồ / biểu đồ co giãn theo, tối đa 1 lần mỗi khung hình
        if (frame) return;
        frame = requestAnimationFrame(() => { frame = null; onResize(); });
    };
    const apply = (w, save = true) => {
        const v = clamp(w);
        app.style.setProperty('--sidebar-w', `${v}px`);
        sash.setAttribute('aria-valuenow', String(v));
        if (save) { try { localStorage.setItem(WIDTH_KEY, String(v)); } catch (e) { /* ignore */ } }
        notify();
    };
    const reset = () => {
        app.style.removeProperty('--sidebar-w');
        try { localStorage.removeItem(WIDTH_KEY); } catch (e) { /* ignore */ }
        notify();
    };
    sash.setAttribute('aria-valuemin', String(MIN_W));
    sash.setAttribute('aria-valuemax', String(MAX_W));
    // Độ rộng đã lưu
    let saved = null;
    try { saved = Number(localStorage.getItem(WIDTH_KEY)); } catch (e) { /* ignore */ }
    if (saved) apply(saved, false);
    // Kéo bằng chuột / cảm ứng
    let startX = 0, startW = 0;
    sash.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || app.classList.contains('sidebar-collapsed') || window.innerWidth <= MOBILE_MAX) return;
        e.preventDefault();
        startX = e.clientX; startW = current();
        sash.setPointerCapture(e.pointerId);
        document.body.classList.add('sash-dragging');
        sash.classList.add('active');
    });
    sash.addEventListener('pointermove', (e) => {
        if (!sash.hasPointerCapture(e.pointerId)) return;
        apply(startW + (e.clientX - startX), false);
    });
    const end = (e) => {
        if (!sash.hasPointerCapture(e.pointerId)) return;
        sash.releasePointerCapture(e.pointerId);
        document.body.classList.remove('sash-dragging');
        sash.classList.remove('active');
        apply(current());                        // lưu giá trị cuối
    };
    sash.addEventListener('pointerup', end);
    sash.addEventListener('pointercancel', end);
    sash.addEventListener('dblclick', reset);
    // Bàn phím
    sash.addEventListener('keydown', (e) => {
        const step = e.shiftKey ? 40 : 10;
        if (e.key === 'ArrowLeft') apply(current() - step);
        else if (e.key === 'ArrowRight') apply(current() + step);
        else if (e.key === 'Home') reset();
        else return;
        e.preventDefault();
    });
}