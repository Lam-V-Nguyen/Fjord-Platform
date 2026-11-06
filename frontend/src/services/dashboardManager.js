// Điều phối trang Overview (Dashboard) – nội dung chính là KẾT QUẢ MÔ PHỎNG HYD / WAQ:
//  - Catalog: kịch bản HYD/WAQ, biến bản đồ & trạm, trạm, mốc thời gian (/dashboard/catalog)
//  - KPI, giám sát, trạng thái chất lượng nước, nguồn/xả, rủi ro, cảnh báo (/dashboard/summary, /alerts)
//  - Bản đồ trường mô phỏng + thanh thời gian (/dashboard/mesh, /field)
//  - Profile thẳng đứng & chuỗi thời gian tại trạm (/dashboard/profile, /station)
//  - Khi dự án chưa có kết quả: hiển thị các bước mô hình, mỗi bước mở công cụ tương ứng
//  - Trạng thái (period, end, field, layer, station) đồng bộ vào URL để chia sẻ

import { icon } from './icons.js';
import { DashboardMap, fmtVal } from './dashboardMap.js';
import { renderGauge, renderDonut, renderStationSeries, renderProfile, resizeCharts, showEmpty } from './dashboardCharts.js';

const FILTER_KEY = 'dash-filters';
const STATE_KEY = 'dash-state-v2';
const PERIOD_LABEL = { '1d': '1 day', '7d': '7 days', '30d': '30 days', all: 'whole simulation' };
const KPI_TONES = { surface_temperature: 'tone-pink', stratification: 'tone-blue', water_level: 'tone-violet', max_velocity: 'tone-amber', wqi: 'tone-green' };
const SEV_ICON = { high: 'alert', medium: 'alert', low: 'check', info: 'info' };
const STEP_ICONS = { data: 'clipboard', grid: 'grid', hyd: 'waves', waq: 'flask', calibration: 'sliders' };

// ------------------------------------------------------------------ helpers
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
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
    return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtDate(iso, withTime = false) {
    if (!iso) return '—';
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

function readJson(key) { try { return JSON.parse(localStorage.getItem(key)) || null; } catch (e) { return null; } }

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
        this.map = null;
        this.profileDebounced = debounce(() => this.loadProfile(), 250);
    }

    // ------------------------------------------------------------ init
    init({ onExpandMap }) {
        this.map = new DashboardMap({
            mapId: 'main-map', legendEl: $('map-legend'), layerListEl: $('layer-list'),
            switchEl: $('basemap-switch'), coordsEl: $('map-coords'), toggleAllBtn: $('layers-toggle-all'),
            onExpand: onExpandMap, onStationSelect: (name) => this.selectStation(name),
        }).init();
        this.renderSkeletons();
        this.bindToolbar();
        this.bindPanels();
        window.addEventListener('resize', () => this.resize());
        api('thresholds').then(t => { this.thresholds = t; }).catch(() => {});
        return this.refresh();
    }

    renderSkeletons() {
        $('kpi-row').innerHTML = Array.from({ length: 6 }, () =>
            `<div class="kpi-card skeleton"><div class="kpi-icon"></div><div class="kpi-body">
                <div class="kpi-label">&nbsp;</div><div class="kpi-value">&nbsp;</div><div class="kpi-sub">&nbsp;</div></div></div>`).join('');
    }

    saveState() {
        const { period, field, layer, station, tsVar, tsLayer, profileVar } = this.state;
        try { localStorage.setItem(STATE_KEY, JSON.stringify({ period, field, layer, station, tsVar, tsLayer, profileVar })); } catch (e) { /* ignore */ }
    }

    body(extra = {}) {
        return { projectName: this.getProject(), end: this.state.end, period: this.state.period, ...extra };
    }

    // ------------------------------------------------------------ bindings
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

    // ------------------------------------------------------------ data flow
    async refresh(force = false) {
        if (this.loading) return;
        this.loading = true;
        const btn = $('btn-refresh');
        btn.classList.add('spinning');
        try {
            const catalog = await api('catalog', this.body());
            const changed = force || !this.catalog || catalog.version !== this.catalog.version || catalog.project !== this.catalog.project;
            this.catalog = catalog;
            const [summary, alerts] = await Promise.all([api('summary', this.body()), api('alerts', this.body({ limit: 100 }))]);
            this.summary = summary; this.alerts = alerts.alerts || [];
            this.loadContext();
            if (!catalog.has_results || summary.mode === 'empty') { this.renderEmpty(summary); return; }
            $('dash-overview').classList.remove('is-empty');
            if (changed) await this.setupSimulation();
            this.syncDateInput(summary);
            this.renderKpis(summary);
            this.renderMonitor(summary);
            this.renderStatus();
            renderDonut('donut-chart', summary.risk_distribution);
            this.renderDonutLegend(summary.risk_distribution);
            this.renderFooter(summary);
            this.renderAlerts();
            // Thời điểm bản đồ = cuối cửa sổ phân tích (trừ khi người dùng đã kéo thanh thời gian)
            if (this.state.tIndex === null || !changed) this.state.tIndex = this.indexOf(this.fieldSource(), summary.window?.end);
            this.syncSlider();
            await Promise.all([this.loadField(), this.loadSeries(), this.loadProfile()]);
            this.updateUrl();
            if (force) toast('Dashboard refreshed');
        } catch (err) {
            console.error(err); this.renderError(err);
        } finally {
            this.loading = false; btn.classList.remove('spinning');
        }
    }

    async loadContext() {
        if (this.contextLoaded === this.getProject()) return;
        try {
            const res = await api('layers', this.body());
            this.map.setContext(res.layers); this.contextLoaded = this.getProject();
            if (!this.catalog?.has_results) this.map.fit();
        } catch (err) { console.warn('context layers', err); }
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
        const profVars = c.profile_variables || his.filter(v => v.layered);
        $('profile-var').innerHTML = build(profVars);
        const critical = this.summary?.gauges?.critical?.[0];
        if (!this.state.tsVar || !his.some(v => `${v.source}|${v.name}` === this.state.tsVar)) {
            this.state.tsVar = critical && his.some(v => v.source === 'waq' && v.name === critical.key) ? `waq|${critical.key}` : 'hyd|temperature';
        }
        if (!his.some(v => `${v.source}|${v.name}` === this.state.profileVar)) this.state.profileVar = his.find(v => v.layered) ? `${his.find(v => v.layered).source}|${his.find(v => v.layered).name}` : '';
        $('ts-var').value = this.state.tsVar; 
        if (!profVars.some(v => `${v.source}|${v.name}` === this.state.profileVar)) this.state.profileVar = profVars.length ? `${profVars[0].source}|${profVars[0].name}` : '';
        $('ts-layer').value = this.state.tsLayer;
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

    // ------------------------------------------------------------ field & time
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
        $('tb-label').textContent = iso ? iso.replace('T', ' ').slice(0, 16) : '—';
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

    // ------------------------------------------------------------ station / profile / series
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

    // ------------------------------------------------------------ render
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
                const sevCls = k.key === 'wqi' ? wqiSev(k.value) : k.key === 'risk' ? '' : '';
                sub = `<span class="kpi-status ${sevCls}">${esc(k.status)}</span>${k.key === 'wqi' ? `<span>· ${esc(k.hint || '')}</span>` : ''}`;
            } else sub = `<span>${esc(k.hint || '')}</span>`;
            return `<div class="kpi-card ${tone}" title="${esc(k.hint || k.label)}">
                <div class="kpi-icon">${icon(k.icon, 20)}</div>
                <div class="kpi-body">
                    <div class="kpi-label">${esc(k.label)}</div>
                    <div class="kpi-value ${k.key === 'risk' ? 'kpi-status' : ''} ${isText && String(k.value).length > 8 ? 'long' : ''}" ${k.key === 'risk' ? 'style="color:var(--tone)"' : ''}>${value}${k.unit && !isText && k.value !== null ? `<span class="unit">${esc(k.unit)}</span>` : ''}</div>
                    <div class="kpi-sub">${sub}</div>
                </div></div>`;
        }).join('');
    }

    renderMonitor(summary) {
        const g = summary.gauges || {};
        const strat = g.stratification || {};
        const sevColor = (s) => ({ high: 'var(--sev-high)', medium: 'var(--sev-medium)', low: 'var(--sev-low)' }[s] || 'var(--text-2)');
        const cells = [`
            <div class="monitor-cell">
                <span class="m-label">Water Quality Index</span>
                <div id="gauge-wqi"></div>
                <span class="m-sub" style="color:${g.wqi?.value >= 65 ? 'var(--sev-low)' : g.wqi?.value >= 45 ? 'var(--sev-medium)' : 'var(--sev-high)'}">${esc(g.wqi?.label || '—')}</span>
            </div>`];
        const critical = (g.critical || []).slice(0, 2);
        if (critical.length < 2) {
            cells.push(`<div class="monitor-cell">
                <span class="m-label">Stratification ΔT</span>
                <span class="m-icon" style="background:var(--accent-2-soft); color:var(--accent-2)">${icon('thermo', 18)}</span>
                <span class="m-value">${fmtNumber(strat.value, 1)}<small> °C</small></span>
                <span class="m-sub" style="color:${strat.value >= 3 ? 'var(--sev-medium)' : 'var(--sev-low)'}">${esc(strat.label || '—')}</span>
            </div>`);
        }
        critical.forEach(c => cells.push(`
            <div class="monitor-cell" title="${esc(c.label)} at ${esc(c.location)}">
                <span class="m-label">${esc(c.label)}</span>
                <span class="m-icon" style="background:color-mix(in srgb, ${sevColor(c.severity)} 18%, transparent); color:${sevColor(c.severity)}">${icon(c.key === 'OXY' ? 'droplet' : 'flask', 18)}</span>
                <span class="m-value">${fmtVal(c.value)}<small> ${esc(c.unit)}</small></span>
                <span class="m-sub" style="color:${sevColor(c.severity)}">${esc(c.location)}</span>
            </div>`));
        while (cells.length < 3) cells.push('<div class="monitor-cell"></div>');
        $('monitor-grid').innerHTML = cells.join('');
        if (g.wqi?.value !== null && g.wqi?.value !== undefined) renderGauge('gauge-wqi', g.wqi.value, { min: 0, max: 100 });
        else showEmpty('gauge-wqi', 'No WAQ');
        $('monitor-sub').textContent = strat.surface !== undefined && strat.surface !== null
            ? `Surface ${fmtNumber(strat.surface, 1)} °C · bottom ${fmtNumber(strat.bottom, 1)} °C · ΔT ${fmtNumber(strat.value, 1)} °C`
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
                <span>Out ${sinks.length ? sinks.map(k => `${esc(k.name)} <strong>${fmtNumber(Math.abs(k.value), 3)}</strong>`).join(', ') : '<strong>—</strong>'}</span></div>`;
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
                    <div class="alert-meta">${esc(a.location || '—')} · ${esc(a.category)}</div></div>
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
                        <td class="num">${c.value === null || c.value === undefined ? '—' : fmtVal(c.value, c.unit)}</td>
                        <td class="num">${c.exceed_pct === undefined || c.exceed_pct === null ? '—' : c.exceed_pct + ' %'}</td>
                        <td>${esc(c.location || '—')}</td>
                        <td>${c.time ? fmtDate(c.time, true) : '—'}</td>
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
            const parts = [`HYD: ${h.scenario || '—'} (${fmtDate(h.start)} – ${fmtDate(h.end)}${h.step_minutes ? `, ${h.step_minutes / 60} h step` : ''}${h.n_layers ? `, ${h.n_layers} layers` : ''})`];
            parts.push(w?.available ? `WAQ: ${w.scenario}` : 'no WAQ'); $('page-subtitle').textContent = parts.join(' -- ');
        }
    }

    renderError(err) {
        $('kpi-row').innerHTML = `<div class="kpi-card" style="grid-column:1/-1"><div class="kpi-icon" style="--tone:var(--sev-high)">${icon('alert', 20)}</div>
             <div class="kpi-body"><div class="kpi-label">Could not load dashboard data</div>
             <div class="kpi-sub error-state">${esc(err.message)}</div></div></div>`;
    }

    // ------------------------------------------------------------ misc
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
        resizeCharts(['gauge-wqi', 'donut-chart', 'ts-chart', 'profile-chart']);
    }
}

function wqiSev(v) { return v >= 65 ? 'sev-low' : v >= 45 ? 'sev-medium' : 'sev-high'; }

export function positionPopover(pop, anchor, align = 'left') {
    const r = anchor.getBoundingClientRect();
    pop.style.top = `${r.bottom + 8 + window.scrollY}px`;
    if (align === 'right') { pop.style.left = 'auto'; pop.style.right = `${Math.max(8, window.innerWidth - r.right)}px`; }
    else { pop.style.right = 'auto'; pop.style.left = `${Math.min(r.left, window.innerWidth - 320)}px`; }
}

export { toast };
