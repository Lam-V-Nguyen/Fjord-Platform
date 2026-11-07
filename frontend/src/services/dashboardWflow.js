// Lớp "Wflow results": kết quả sau khi chạy mô hình Wflow, đọc từ cache viewer của nền tảng
// (flows/<flow>/wflow_model/viewer_dashboard) – cache RIÊNG, độc lập với công cụ Flow Estimation (viewer/).
//  - Lớp sông (river): tô màu & độ dày từng đoạn sông theo giá trị
//  - Lớp lưới (grid): ảnh raster trên lưới Wflow
//  Hai lớp có thể hiển thị đồng thời (vd. lưu lượng sông trên nền dòng chảy mặt).
// Chế độ: animate (theo thanh thời gian) | max | mean | sum | tmax | above (thống kê cả kỳ chạy,
// GET /dashboard/wflow/stat/{var}/{stat}). Cài đặt hiển thị lưu trong localStorage;
// độ phân giải/dựng lại cache là thao tác phía server (cache dùng chung).

import { paletteColor, fmtVal } from './dashboardMap.js';
// Không import từ dashboardMovement.js: lớp Wflow độc lập hoàn toàn với lớp HYD
function nearestIndex(times, iso) {
    if (!times?.length) return -1;
    if (!iso) return times.length - 1;
    const t = Date.parse(iso);
    let best = 0, bestD = Infinity;
    for (let i = 0; i < times.length; i++) {
        const d = Math.abs(Date.parse(times[i]) - t);
        if (d < bestD) { bestD = d; best = i; }
    }
    return best;
}
import { ui, bindSettings, loadSettings, saveSettings, readInput } from './dashboardLayerSettings.js';
import { WflowParticles } from './dashboardWflowParticles.js';      // riêng cho Wflow (không dùng chung với HYD)

const L = window.L;
const SETTINGS_KEY = 'dash.wflow.settings';
const DEFAULTS = { river: null, grid: null, mode: 'animate', threshold: '', scale: 'auto', opacity: 0.8, flow: null };
// Hạt dòng chảy mặt (trường vận tốc velocity.npy của cache viewer) – bật mặc định khi có dữ liệu
const P_KEY = 'dash.wflow.particles';
// Mặc định giống công cụ Flow Estimation (flowRunner.js: count 4000, speed 1, trail 0.92, curve sqrt)
const P_DEFAULTS = { on: true, count: 4000, pspeed: 1, trail: 0.92, width: 1.5, curve: 'sqrt', pcolor: 'speed', opacity: 1, water: true };
const MODES = [['animate', 'Animate'], ['max', 'Max'], ['mean', 'Mean'], ['sum', 'Sum'], ['tmax', 'Time of peak'], ['above', 'Hours above']];
const MODE_LABEL = Object.fromEntries(MODES);

async function post(path, body) {
    const res = await fetch(`/dashboard/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status === 'error') throw new Error(data.message || `HTTP ${res.status}`);
    return data;
}

async function getBin(url, withHeaders = false) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) {
        const msg = await res.json().then(d => d.message).catch(() => null);
        throw new Error(msg || `${url.split('?')[0]}: HTTP ${res.status}`);
    }
    const buf = await res.arrayBuffer();
    return withHeaders ? { buf, headers: res.headers } : buf;
}

// float16 (little-endian) -> number
function halfToFloat(h) {
    const s = (h & 0x8000) ? -1 : 1, e = (h >> 10) & 0x1f, f = h & 0x03ff;
    if (e === 0) return s * 2 ** -14 * (f / 1024);
    if (e === 31) return f ? NaN : s * Infinity;
    return s * 2 ** (e - 15) * (1 + f / 1024);
}

function decode(buf, dtype) {
    if (dtype === '<f2') {
        const h = new Uint16Array(buf), out = new Float32Array(h.length);
        for (let i = 0; i < h.length; i++) out[i] = halfToFloat(h[i]);
        return out;
    }
    return new Float32Array(buf);
}

export class WflowLayer {
    constructor(map, { projectName }) {
        this.map = map; this.projectName = projectName;
        this.S = loadSettings(SETTINGS_KEY, DEFAULTS);
        this.info = null; this.group = L.layerGroup();
        this.frames = new Map(); this.stats = new Map();
        this.frameIndex = -1; this.visible = false; this.lastIso = null;
        this.parts = { grid: { var: null }, river: { var: null } };
        this.riverGeo = null; this.cellsOf = null;
        this.PS = loadSettings(P_KEY, P_DEFAULTS);
        this.mask = null;
        this.particles = new WflowParticles(map, { S: this.PS });
    }

    q(extra = '') {
        // v = phiên bản cache viewer -> sau khi dựng lại, trình duyệt không dùng dữ liệu cũ
        return `projectName=${encodeURIComponent(this.projectName)}&flowName=${encodeURIComponent(this.info.flow)}&v=${encodeURIComponent(this.info.version || '')}${extra}`;
    }

    // opts: {flowName, res, rebuild}
    async init(opts = {}) {
        const flowName = opts.flowName || this.S.flow || undefined;
        let res;
        try { res = await post('wflow/init', { projectName: this.projectName, ...opts, flowName }); }
        catch (err) {                                      // kịch bản đã lưu không còn -> dùng mặc định
            if (!flowName || opts.flowName) throw err;
            res = await post('wflow/init', { projectName: this.projectName, ...opts, flowName: undefined });
        }
        if (res.status !== 'ok') throw new Error(res.message || 'Wflow results not available');
        this.info = res; this.S.flow = res.flow;
        this.lastRebuild = res.rebuilt ? res.reason : null;
        if (!this.map.getPane('wflow')) {
            const pane = this.map.createPane('wflow');
            pane.style.zIndex = 395;                 // trên trường mô phỏng, dưới hạt movement
        }
        this.frames.clear(); this.stats.clear(); this.frameIndex = -1;
        this.riverGeo = null; this.cellsOf = null; this.mask = null;
        // Biến: giữ lựa chọn đã lưu nếu còn tồn tại; '' = tắt có chủ ý
        const ok = (name, kind) => name && res.variables[name]?.kind === kind;
        const river = this.S.river === '' ? '' : ok(this.S.river, 'river') ? this.S.river : (res.river_default || '');
        let grid = this.S.grid === '' ? '' : ok(this.S.grid, 'grid') ? this.S.grid : '';
        if (!river && !grid) grid = res.grid_default || '';
        await this.setVariables({ river, grid });
        await this.initParticles();
        return this;
    }

    // Thanh thời gian (theo kết quả HYD/WAQ) nằm ngoài kỳ mô phỏng Wflow?
    outsidePeriod() {
        const t = Date.parse(this.lastIso || ''), ts = this.info?.times || [];
        if (!Number.isFinite(t) || !ts.length) return false;
        const half = (this.info.step_hours || 24) * 1800e3;
        return t < Date.parse(ts[0]) - half || t > Date.parse(ts[ts.length - 1]) + half;
    }

    status() {
        const st = this.particles.status();
        if (!this.hasVelocity || !this.PS.on) return st;
        return `${st} · Wflow step ${this.particles.index + 1}/${this.info.times.length}${this.outsidePeriod() ? ' (nearest)' : ''}`;
    }

    get hasVelocity() { return !!this.info?.velocity; }

    // mask.npy của cache viewer: 0 ngoài miền, 1 đất, 2 vùng nước (water area)
    async loadMask() {
        if (!this.cellsOf) {
            this.mask = new Uint8Array(await getBin(`/dashboard/wflow/mask?${this.q()}`));
            this.cellsOf = [];
            for (let k = 0; k < this.mask.length; k++) if (this.mask[k]) this.cellsOf.push(k);
        }
        return this.cellsOf;
    }

    // Hạt Wflow: bộ vẽ riêng (dashboardWflowParticles.js), dữ liệu GET /dashboard/wflow/velocity/{i}
    async initParticles() {
        if (!this.hasVelocity) { this.particles.hide(); return; }
        await this.loadMask();
        // Đường viền lưu vực + vùng nước: hạt được cắt/xoá giống hệt Flow Estimation
        const geo = (kind) => fetch(`/dashboard/wflow/geometry/${kind}?${this.q()}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
        const [basinGeo, waterGeo] = await Promise.all([geo('basins'), (this.info.grid?.n_water || 0) > 0 ? geo('water') : null]);
        this.particles.setup({
            grid: this.info.grid, mask: this.mask, velocity: this.info.velocity, times: this.info.times,
            url: (i) => `/dashboard/wflow/velocity/${i}?${this.q()}`, basinGeo, waterGeo,
        });
        this.particles.setTime(this.lastIso);
        this.updateParticleVisibility();
    }

    async syncParticles() {
        if (this.hasVelocity) this.particles.setTime(this.lastIso);     // nội suy giữa 2 bước Wflow
    }

    updateParticleVisibility() {
        if (this.visible && this.PS.on && this.hasVelocity) this.particles.show(); else this.particles.hide();
    }

    get vars() { return this.info?.variables || {}; }
    get statMode() { return this.S.mode !== 'animate'; }

    async setVariables({ river, grid }) {
        this.S.river = river; this.S.grid = grid; saveSettings(SETTINGS_KEY, this.S);
        this.group.clearLayers();
        this.parts = { grid: { var: grid || null }, river: { var: river || null } };
        if (grid) {
            await this.loadMask();
            const g = this.info.grid, p = this.parts.grid;
            p.canvas = document.createElement('canvas'); p.canvas.width = g.nx; p.canvas.height = g.ny;
            p.overlay = L.imageOverlay(p.canvas.toDataURL(), g.bounds, { pane: 'wflow', opacity: this.S.opacity, className: 'wflow-raster' });
            this.group.addLayer(p.overlay);
        }
        if (river) {
            if (!this.riverGeo) this.riverGeo = await (await fetch(`/dashboard/wflow/rivers?${this.q()}`)).json();
            const p = this.parts.river, renderer = L.canvas({ padding: 0.3, pane: 'wflow' });
            p.layers = [];
            p.geo = L.geoJSON(this.riverGeo, {
                pane: 'wflow', renderer, interactive: true,
                style: { color: '#334155', weight: 1.2, opacity: this.S.opacity },
                onEachFeature: (f, lyr) => { p.layers[f.properties.id] = lyr; },
            });
            p.geo.on('click', (e) => this.popup(e.latlng, e.layer?.feature?.properties?.id));
            this.group.addLayer(p.geo);                       // sông vẽ sau -> nằm trên raster
        }
        await this.refreshValues(true);
    }

    async loadFrame(name, i) {
        const key = `${name}:${i}`;
        if (this.frames.has(key)) return this.frames.get(key);
        const vals = decode(await getBin(`/dashboard/wflow/frame/${encodeURIComponent(name)}/${i}?${this.q()}`), this.vars[name].dtype);
        this.frames.set(key, vals);
        if (this.frames.size > 24) this.frames.delete(this.frames.keys().next().value);
        return vals;
    }

    async loadStat(name) {
        const { mode, threshold } = this.S;
        const key = `${name}:${mode}:${mode === 'above' ? threshold : ''}`;
        if (this.stats.has(key)) return this.stats.get(key);
        const extra = mode === 'above' ? `&threshold=${encodeURIComponent(threshold)}` : '';
        const { buf, headers } = await getBin(`/dashboard/wflow/stat/${encodeURIComponent(name)}/${mode}?${this.q(extra)}`, true);
        const out = { vals: new Float32Array(buf), lo: Number(headers.get('X-Stat-Min')) || 0, hi: Number(headers.get('X-Stat-Max')) || 1 };
        this.stats.set(key, out);
        return out;
    }

    // Nạp giá trị cho các lớp đang bật theo chế độ hiện tại rồi vẽ lại
    async refreshValues(force = false) {
        const i = nearestIndex(this.info.times, this.lastIso);
        if (!force && !this.statMode && i === this.frameIndex) return;
        for (const p of Object.values(this.parts)) {
            if (!p.var) continue;
            if (this.statMode) {
                const s = await this.loadStat(p.var);
                p.values = s.vals; p.range = [s.lo, s.hi];
            } else if (i >= 0) {
                p.values = await this.loadFrame(p.var, i);
                const m = this.vars[p.var];
                p.range = [Math.max(0, m.vmin ?? 0), m.p99 || m.max || 1];
            }
        }
        this.frameIndex = i;
        this.paint();
    }

    useLog(p) {
        if (this.S.scale === 'log') return true;
        if (this.S.scale === 'linear') return false;
        return !!this.vars[p.var]?.log && !['tmax', 'above'].includes(this.S.mode);
    }

    scale(p, v) {
        const [lo, hi] = p.range || [0, 1];
        if (this.useLog(p)) return Math.log1p(Math.max(0, v - lo)) / (Math.log1p(Math.max(0, hi - lo)) || 1);
        return (v - lo) / ((hi - lo) || 1);
    }

    async setTime(iso) {
        this.lastIso = iso;
        if (!this.info) return null;
        if (!this.statMode) await this.refreshValues();
        await this.syncParticles();                           // hạt luôn theo thanh thời gian
        const i = nearestIndex(this.info.times, iso);
        return i >= 0 ? this.info.times[i] : null;
    }

    paint() {
        const r = this.parts.river, gp = this.parts.grid, op = this.S.opacity;
        if (r.var && r.values && r.layers) {
            for (let k = 0; k < r.layers.length; k++) {
                const lyr = r.layers[k], v = r.values[k];
                if (!lyr) continue;
                if (!Number.isFinite(v)) { lyr.setStyle({ color: '#334155', weight: 1, opacity: op }); continue; }
                const t = Math.max(0, Math.min(1, this.scale(r, v)));
                lyr.setStyle({ color: paletteColor(t, 'flow'), weight: 1.2 + 3.3 * t, opacity: op });
            }
        }
        if (gp.var && gp.values && gp.overlay) {
            const g = this.info.grid, ctx = gp.canvas.getContext('2d');
            const img = ctx.createImageData(g.nx, g.ny);
            const lut = Array.from({ length: 64 }, (_, i) => paletteColor(i / 63, 'flow').match(/\d+/g).map(Number));
            for (let j = 0; j < this.cellsOf.length; j++) {
                const v = gp.values[j];
                if (!Number.isFinite(v)) continue;
                const k = this.cellsOf[j], ix = k % g.nx, iy = (k / g.nx) | 0;
                const o = ((g.ny - 1 - iy) * g.nx + ix) * 4;              // hàng 0 của lưới = phía nam
                const rgb = lut[Math.round(Math.max(0, Math.min(1, this.scale(gp, v))) * 63)];
                img.data[o] = rgb[0]; img.data[o + 1] = rgb[1]; img.data[o + 2] = rgb[2]; img.data[o + 3] = 230;
            }
            ctx.putImageData(img, 0, 0);
            gp.overlay.setUrl(gp.canvas.toDataURL());
            gp.overlay.setOpacity(op);
        }
    }

    unitsFor(name) {
        const m = this.vars[name] || {};
        if (this.S.mode === 'tmax' || this.S.mode === 'above') return 'h';
        if (this.S.mode === 'sum' && m.units) return `${m.units} · Σ steps`;
        return m.units || '';
    }

    popup(latlng, id) {
        const p = this.parts.river;
        if (id === undefined || !p.values) return;
        const m = this.vars[p.var], v = p.values[id];
        const when = this.statMode ? `${MODE_LABEL[this.S.mode]} over run` : this.info.times[this.frameIndex].slice(0, 10);
        L.popup({ closeButton: false }).setLatLng(latlng).setContent(
            `<div class="popup-title">${m.description || p.var}</div>
             <div class="popup-kv"><span>Value</span><span>${Number.isFinite(v) ? fmtVal(v) : '—'} ${this.unitsFor(p.var)}</span>
             <span>Time</span><span>${when}</span>
             <span>Scenario</span><span>${this.info.flow}</span></div>`).openOn(this.map);
    }

    legend() {
        const t = this.statMode ? `${MODE_LABEL[this.S.mode]}${this.S.mode === 'above' ? ` ${this.S.threshold}` : ''} over run`
            : this.frameIndex >= 0 ? this.info.times[this.frameIndex].slice(0, 10) : '';
        const note = `${this.info.flow}${this.info.synthetic ? ' (synthetic)' : ''} · ${t}`;
        const out = ['river', 'grid'].map(kind => this.parts[kind]).filter(p => p.var && p.range).map(p => {
            const m = this.vars[p.var], u = this.unitsFor(p.var);
            return { title: `Wflow · ${m.description || p.var}${u ? ` (${u})` : ''}${this.useLog(p) ? ' · log' : ''}`,
                     palette: 'flow', min: p.range[0], max: p.range[1], note };
        });
        if (this.hasVelocity && this.PS.on && this.PS.pcolor === 'speed') {
            const when = this.info.times[this.particles.index]?.slice(0, 10) || '';
            out.push({ title: 'Wflow · surface flow speed (m/s)', palette: 'wflow_particle', min: 0,
                       max: this.info.velocity.speed_max, note: `particles · ${when}${this.outsidePeriod() ? ' (nearest Wflow step)' : ''}` });
        }
        return out;
    }

    show() { if (!this.visible) { this.group.addTo(this.map); this.visible = true; } this.updateParticleVisibility(); }
    hide() { if (this.visible) { this.map.removeLayer(this.group); this.visible = false; } this.particles.hide(); }
    destroy() { this.hide(); this.group.clearLayers(); this.frames.clear(); this.stats.clear(); this.particles.destroy(); }

    // ------------------------------------------------------------ bảng cài đặt
    renderSettings(el, ctx) {
        if (el._bound) { const n = el.cloneNode(false); n.classList.remove('busy'); el.replaceWith(n); el = n; }   // vẽ lại -> bỏ listener cũ
        el._bound = true;
        const S = this.S, info = this.info, g = info.grid || {};
        const opts = (kind) => [['', '— none —'], ...Object.entries(this.vars).filter(([, v]) => v.kind === kind)
            .map(([k, v]) => [k, `${v.description || k}${v.units ? ` (${v.units})` : ''}`])];
        const scen = (info.scenarios || []).map(s => typeof s === 'string' ? s : s.name);
        el.innerHTML =
            ui.section('Scenario',
                ui.row('Flow', ui.select('flow', scen.map(s => [s, s]), info.flow))) +
            ui.section('Resolution (dashboard only)',
                ui.row('Resolution (m)', ui.input('res', g.res_auto ? '' : g.res_m, `auto (${+Number(g.res_m).toFixed(1)} m)`, 'number')) +
                `<div class="ls-actions">${ui.button('apply', 'Apply')}${ui.button('rebuild', 'Force rebuild', 'ghost')}</div>`,
                'Rebuilds the dashboard\'s own cache <b>wflow_model/viewer_dashboard</b>; the Flow Estimation tool is not affected.') +
            ui.section('Variables',
                ui.row('River lines', ui.select('river', opts('river'), S.river || '')) +
                ui.row('Map layer', ui.select('grid', opts('grid'), S.grid || ''))) +
            ui.section('Display',
                ui.row('Mode', ui.select('mode', MODES, S.mode)) +
                `<div class="ls-row ls-thr" ${S.mode === 'above' ? '' : 'hidden'}><span class="ls-label">Threshold</span>${ui.input('threshold', S.threshold, 'value in variable units', 'number')}</div>` +
                ui.row('Colour scale', ui.seg('scale', [['auto', 'Auto'], ['linear', 'Linear'], ['log', 'Log']], S.scale)) +
                ui.row('Opacity', ui.range('opacity', 0.2, 1, 0.05, S.opacity, (v) => v.toFixed(2))),
                'Statistic modes summarise the whole run and ignore the time bar.') +
            (this.hasVelocity
                ? ui.section('Flow particles',
                    ui.row('Show', ui.seg('p.on', [['1', 'On'], ['0', 'Off']], this.PS.on ? '1' : '0')) +
                    ui.row('Opacity', ui.range('p.opacity', 0.1, 1, 0.05, this.PS.opacity, (v) => v.toFixed(2))) +
                    ui.row('Count', ui.range('p.count', 500, 20000, 500, this.PS.count)) +
                    ui.row('Speed ×', ui.range('p.pspeed', 0.2, 4, 0.1, this.PS.pspeed, (v) => v.toFixed(1))) +
                    ui.row('Trail', ui.range('p.trail', 0.8, 0.985, 0.005, this.PS.trail, (v) => v.toFixed(3))) +
                    ui.row('Width', ui.range('p.width', 0.6, 3, 0.1, this.PS.width, (v) => v.toFixed(1))) +
                    ui.row('Speed curve', ui.seg('p.curve', [['linear', 'Linear'], ['sqrt', '√'], ['log', 'Log']], this.PS.curve)) +
                    ui.row('Colour', ui.select('p.pcolor', [['speed', 'By speed'], ['white', 'White'], ['blue', 'Blue'], ['dark_blue', 'Dark blue'], ['cyan', 'Cyan'], ['black', 'Black']], this.PS.pcolor)) +
                    ((this.info.grid?.n_water || 0) > 0
                        ? ui.row('Stop in water', ui.seg('p.water', [['1', 'On'], ['0', 'Off']], this.PS.water !== false ? '1' : '0')) : ''),
                    `Velocity from overland flow / river discharge (${this.info.velocity.direction || 'flow direction'}), follows the time bar. `
                    + `Wflow is independent of the HYD/WAQ run: when the time bar is outside the Wflow period (${this.info.times[0].slice(0, 10)} – ${this.info.times.at(-1).slice(0, 10)}) the nearest Wflow step is shown.`)
                : ui.section('Flow particles', '', 'No velocity field in this Wflow output (needs overland_flow / river_discharge on the grid).')) +
            ui.section('Time series at a point',
                `<div class="ls-actions">${ui.button('open-flow', 'Open Flow Estimation')}</div>`,
                'Use the <b>Flow Estimation</b> tool to download the time series of any Wflow variable for the whole simulation period at any point (click a location on its map).') +
            `<div class="ls-status"></div>`;
        const run = async (fn, okMsg) => {
            el.classList.add('busy'); ctx.busy(true);
            try { await fn(); if (okMsg) ctx.toast(okMsg()); ctx.refreshLegend(); }
            catch (err) { ctx.toast(`Wflow: ${err.message}`); }
            finally { el.classList.remove('busy'); ctx.busy(false); }
        };
        const reinit = (opts2, msg) => run(async () => {
            await this.init(opts2);
            await this.refreshValues(true);
            this.renderSettings(el, ctx);
        }, msg);
        bindSettings(el, {
            fmt: { opacity: (v) => v.toFixed(2), 'p.opacity': (v) => v.toFixed(2), 'p.pspeed': (v) => v.toFixed(1), 'p.trail': (v) => v.toFixed(3), 'p.width': (v) => v.toFixed(1) },
            onSet: (k, v, live) => {
                if (k === 'res') return;
                if (k.startsWith('p.')) {                         // cài đặt hạt
                    const pk = k.slice(2), PS = this.PS;
                    PS[pk] = (pk === 'on' || pk === 'water') ? v === '1' : v;
                    if (!live) saveSettings(P_KEY, PS);
                    if (pk === 'on') { this.syncParticles().then(() => { this.updateParticleVisibility(); ctx.refreshLegend(); }); }
                    else if (pk === 'opacity') this.particles.applyOpacity();
                    else if (pk === 'count') { if (!live) { this.particles.alloc(); this.particles.clear(); } }
                    else if (pk === 'water') this.particles.reset();
                    else { this.particles.clear(); if (pk === 'pcolor') ctx.refreshLegend(); }
                    return;
                }
                if (k === 'opacity') {
                    S.opacity = v;
                    this.parts.grid.overlay?.setOpacity(v);
                    this.parts.river.geo?.setStyle({ opacity: v });
                    if (!live) saveSettings(SETTINGS_KEY, S);
                    return;
                }
                if (k === 'flow') { reinit({ flowName: v }, () => `Scenario: ${this.info.flow}`); return; }
                if (k === 'river' || k === 'grid') {
                    const next = { river: S.river, grid: S.grid, [k]: v };
                    run(() => this.setVariables(next));
                    return;
                }
                if (k === 'threshold') {
                    S.threshold = v; saveSettings(SETTINGS_KEY, S);
                    if (S.mode === 'above') run(() => this.refreshValues(true));
                    return;
                }
                if (k === 'mode') {
                    el.querySelector('.ls-thr').hidden = v !== 'above';
                    if (v === 'above' && S.threshold === '') {
                        const m = this.vars[this.parts.river.var || this.parts.grid.var] || {};
                        S.threshold = String(+(((m.p99 || m.max || 1) / 2).toPrecision(2)));
                        el.querySelector('[data-k="threshold"]').value = S.threshold;
                    }
                }
                S[k] = v; saveSettings(SETTINGS_KEY, S);
                run(() => this.refreshValues(true));
            },
            onAction: (act) => {
                if (act === 'enter:threshold') return;           // đã xử lý ở onSet
                if (act === 'open-flow') { ctx.openTool?.('flow-estimation'); return; }
                if (act === 'apply' || act === 'enter:res' || act === 'rebuild') {
                    const txt = readInput(el, 'res'), res = txt === '' ? 'auto' : Number(txt);
                    if (res !== 'auto' && !(res > 0)) { ctx.toast('Resolution must be a positive number (or empty = auto)'); return; }
                    reinit({ flowName: this.info.flow, res, rebuild: act === 'rebuild' },
                        () => this.lastRebuild ? `Wflow viewer data rebuilt: ${this.lastRebuild}` : 'Wflow viewer data unchanged (same settings)');
                }
            },
        });
    }
}