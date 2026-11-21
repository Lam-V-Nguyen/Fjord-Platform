// Chế độ "Wflow 3D" của Dashboard – hiển thị kết quả Wflow trên địa hình 3D.
//
// Tách riêng khỏi workflow hiện có:
//  - module này chỉ được nạp khi người dùng tick "Wflow 3D"; Three.js được tải động lúc đó;
//  - đọc cache viewer của dashboard qua các endpoint sẵn có (POST /dashboard/wflow/init, GET /dashboard/wflow/{mask,
//    frame, velocity, rivers, geometry}) + 3 endpoint riêng GET /dashboard/wflow3d/{terrain, scene, series};
//  - cài đặt lưu ở localStorage 'dash.wflow3d.settings' (không đụng tới cài đặt của lớp "Wflow results" 2D).
//
// Khi bật: khung 3D chiếm chỗ bản đồ trong #panel-map, bản đồ Leaflet và các thành phần của nó (nút zoom, chọn trường,
// basemap, chú giải, toạ độ, thanh thời gian) được ẩn bằng class .w3d-on. Khi tắt: bỏ class, bản đồ hiện lại.
//
// Giao diện lớp giống các lớp bổ sung khác (MovementLayer, WflowLayer):
//   new Wflow3DView(leafletMap, {projectName}).init() · setTime(iso) · show() · hide() · legend() ·
//   renderSettings(el, ctx) · status() · destroy() · visible

import { paletteColor } from './dashboardMap.js';
import { ui, bindSettings, loadSettings, saveSettings } from './dashboardLayerSettings.js';

const THREE_URL = 'https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js';
const CSS_URL = '/src_frontend/styles/wflow3D.css';
const SAT_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const SETTINGS_KEY = 'dash.wflow3d.settings';
const DEFAULTS = {
    flow: null, river: 'auto', grid: '', base: 'satellite', exag: 2, dim: true, basins: true, outlets: true,
    riverWidth: 1, gridOpacity: 0.65,
    rain: true, rainCount: 6000,
    particles: true, pCount: 9000, pSpeed: 1, pSize: 2.4, pColor: 'speed',
    speed: 2, follow: false, rotate: false, hydro: true, pickPoints: true,
};
const POINT_COLORS = ['#f8fafc', '#fde047', '#f472b6', '#5eead4', '#fdba74', '#c4b5fd'];
const MAX_POINTS = POINT_COLORS.length;
const OUTLET_COLORS = ['#38bdf8', '#f97316', '#a3e635', '#e879f9', '#facc15', '#f43f5e', '#2dd4bf', '#818cf8', '#fb923c', '#4ade80', '#c084fc', '#fda4af'];
const MAX_MESH = 640;                 // số đỉnh tối đa mỗi chiều của lưới địa hình

let threePromise = null;
const loadThree = () => (threePromise ||= import(THREE_URL));

function ensureCss() {
    if (document.querySelector('link[data-w3d]')) return;
    const l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = CSS_URL; l.dataset.w3d = '1';
    document.head.appendChild(l);
}

// ---- data helpers
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
async function getJson(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data || data.status === 'error') throw new Error(data?.message || `${url.split('?')[0]}: HTTP ${res.status}`);
    return data;
}
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
// Thời gian Wflow là chuỗi ISO không có múi giờ: đọc như UTC để không bị lệch theo múi giờ của trình duyệt
const tparse = (s) => Date.parse(/(Z|[+-]\d\d:?\d\d)$/.test(s) ? s : `${s}Z`);
const tfmt = (ms) => new Date(ms).toISOString().slice(0, 19);
function nearestIndex(times, iso) {
    if (!times?.length || !iso) return -1;
    const t = tparse(iso); let best = 0, bestD = Infinity;
    for (let i = 0; i < times.length; i++) { const d = Math.abs(tparse(times[i]) - t); if (d < bestD) { bestD = d; best = i; } }
    return best;
}
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || !Number.isFinite(v)) ? '-' : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(d);
const clamp01 = (x) => Math.max(0, Math.min(1, x));
function lut(palette, n = 256) {
    const out = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
        const m = paletteColor(i / (n - 1), palette).match(/\d+/g).map(Number);
        out[i * 3] = m[0] / 255; out[i * 3 + 1] = m[1] / 255; out[i * 3 + 2] = m[2] / 255;
    }
    return out;
}
const SPEED_STOPS = ['#93c5fd', '#67e8f9', '#a7f3d0', '#fef08a', '#fdba74', '#fca5a5'];

// ---- textures
function lon2x(lon, z) { return (lon + 180) / 360 * 2 ** z; }
function lat2y(lat, z) { const r = lat * Math.PI / 180; return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 2 ** z; }

function loadImg(url, ms = 15000) {
    return new Promise((ok, fail) => {
        const im = new Image(); im.crossOrigin = 'anonymous';
        const t = setTimeout(() => { im.src = ''; fail(new Error('tile timeout')); }, ms);
        im.onload = () => { clearTimeout(t); ok(im); }; im.onerror = () => { clearTimeout(t); fail(new Error('tile error')); };
        im.src = url;
    });
}

// Ảnh vệ tinh (Esri World Imagery) cắt theo bounds, chiếu lại về lưới kinh/vĩ đều (giống lưới viewer)
async function satelliteCanvas([[south, west], [north, east]], W, H) {
    let z = Math.ceil(Math.log2(W * 360 / ((east - west) * 256)));
    z = Math.max(3, Math.min(18, z));
    let x0, x1, y0, y1;
    for (; z > 3; z--) {
        x0 = Math.floor(lon2x(west, z)); x1 = Math.floor(lon2x(east, z));
        y0 = Math.floor(lat2y(north, z)); y1 = Math.floor(lat2y(south, z));
        if ((x1 - x0 + 1) * (y1 - y0 + 1) <= 160) break;
    }
    const mosaic = document.createElement('canvas');
    mosaic.width = (x1 - x0 + 1) * 256; mosaic.height = (y1 - y0 + 1) * 256;
    const mctx = mosaic.getContext('2d');
    const jobs = [];
    for (let ty = y0; ty <= y1; ty++) for (let tx = x0; tx <= x1; tx++) {
        jobs.push(loadImg(SAT_URL.replace('{z}', z).replace('{x}', tx).replace('{y}', ty))
            .then(im => mctx.drawImage(im, (tx - x0) * 256, (ty - y0) * 256)));
    }
    await Promise.all(jobs);
    const out = document.createElement('canvas'); out.width = W; out.height = H;
    const ctx = out.getContext('2d');
    const sx = (lon2x(west, z) - x0) * 256, sw = (lon2x(east, z) - lon2x(west, z)) * 256;
    for (let r = 0; r < H; r++) {                                 // từng hàng: vĩ độ đều -> y Mercator
        const lat = north - (r + 0.5) / H * (north - south);
        const sy = (lat2y(lat, z) - y0) * 256;
        ctx.drawImage(mosaic, sx, Math.floor(sy), sw, 1, 0, r, W, 1);
    }
    return out;
}

// Màu địa hình + đổ bóng (dùng khi không có ảnh vệ tinh)
function hillshadeCanvas(elev, nx, ny, res, plain = false) {
    const c = document.createElement('canvas'); c.width = nx; c.height = ny;
    const ctx = c.getContext('2d'), img = ctx.createImageData(nx, ny);
    let zmin = Infinity, zmax = -Infinity;
    for (const v of elev) { if (v < zmin) zmin = v; if (v > zmax) zmax = v; }
    const hyps = [[0, [52, 92, 64]], [0.25, [92, 122, 72]], [0.5, [146, 140, 96]], [0.75, [150, 120, 96]], [1, [226, 222, 214]]];
    const az = 315 * Math.PI / 180, alt = 45 * Math.PI / 180;
    for (let iy = 0; iy < ny; iy++) for (let ix = 0; ix < nx; ix++) {
        const k = iy * nx + ix, z = elev[k];
        const zx = (elev[iy * nx + Math.min(nx - 1, ix + 1)] - elev[iy * nx + Math.max(0, ix - 1)]) / (2 * res) * 2;
        const zy = (elev[Math.min(ny - 1, iy + 1) * nx + ix] - elev[Math.max(0, iy - 1) * nx + ix]) / (2 * res) * 2;
        const slope = Math.atan(Math.hypot(zx, zy)), aspect = Math.atan2(zy, -zx);
        const hs = clamp01(Math.cos(alt) * Math.cos(slope) + Math.sin(alt) * Math.sin(slope) * Math.cos(az - aspect));
        let col;
        if (plain) col = [70, 82, 98];
        else {
            const t = (z - zmin) / ((zmax - zmin) || 1);
            let a = hyps[0], b = hyps[hyps.length - 1];
            for (let i = 0; i < hyps.length - 1; i++) if (t >= hyps[i][0] && t <= hyps[i + 1][0]) { a = hyps[i]; b = hyps[i + 1]; break; }
            const f = (t - a[0]) / ((b[0] - a[0]) || 1);
            col = a[1].map((v, i) => v + (b[1][i] - v) * f);
        }
        const o = ((ny - 1 - iy) * nx + ix) * 4, sh = 0.35 + 0.75 * hs;   // canvas: hàng 0 = phía bắc
        img.data[o] = Math.min(255, col[0] * sh); img.data[o + 1] = Math.min(255, col[1] * sh); img.data[o + 2] = Math.min(255, col[2] * sh); img.data[o + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return c;
}

// ---- camera controls (orbit / pan / zoom)
class Orbit {
    constructor(THREE, camera, el, onChange) {
        this.T = THREE; this.camera = camera; this.el = el; this.onChange = onChange;
        this.target = new THREE.Vector3(); this.r = 100; this.theta = -0.4; this.phi = 0.95;
        this.minR = 5; this.maxR = 5000; this.autoRotate = false;
        this.ptrs = new Map(); this.moved = 0;
        const opt = { passive: false };
        el.addEventListener('pointerdown', (e) => this.down(e));
        el.addEventListener('pointermove', (e) => this.move(e));
        el.addEventListener('pointerup', (e) => this.up(e)); el.addEventListener('pointercancel', (e) => this.up(e));
        el.addEventListener('wheel', (e) => { e.preventDefault(); this.zoom(Math.exp(e.deltaY * 0.0012)); }, opt);
        el.addEventListener('contextmenu', (e) => e.preventDefault());
    }
    set(target, r, theta, phi) { this.target.copy(target); this.r = r; this.theta = theta; this.phi = phi; this.apply(); }
    apply() {
        this.phi = Math.max(0.05, Math.min(1.53, this.phi)); this.r = Math.max(this.minR, Math.min(this.maxR, this.r));
        const { r, theta, phi, target } = this;
        this.camera.position.set(target.x + r * Math.sin(phi) * Math.sin(theta), target.y + r * Math.cos(phi), target.z + r * Math.sin(phi) * Math.cos(theta));
        this.camera.lookAt(target); this.onChange?.();
    }
    zoom(f) { this.r *= f; this.apply(); }
    pan(dx, dy) {
        const s = this.r * 0.0016, T = this.T;
        const right = new T.Vector3().setFromMatrixColumn(this.camera.matrix, 0).setY(0).normalize();
        const fwd = new T.Vector3(-Math.sin(this.theta), 0, -Math.cos(this.theta));
        this.target.addScaledVector(right, -dx * s).addScaledVector(fwd, dy * s); this.apply();
    }
    down(e) {
        this.el.setPointerCapture(e.pointerId);
        this.ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY, b: e.button, shift: e.shiftKey || e.ctrlKey });
        this.moved = 0; this.pinch = null;
    }
    move(e) {
        const p = this.ptrs.get(e.pointerId); if (!p) return;
        const dx = e.clientX - p.x, dy = e.clientY - p.y; p.x = e.clientX; p.y = e.clientY;
        this.moved += Math.abs(dx) + Math.abs(dy);
        if (this.ptrs.size === 2) {                               // chạm 2 ngón: chụm = zoom, kéo = pan
            const [a, b] = [...this.ptrs.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
            if (this.pinch) this.zoom(this.pinch / Math.max(d, 1));
            this.pinch = d; this.pan(dx / 2, dy / 2); return;
        }
        if (p.b === 2 || p.b === 1 || p.shift) this.pan(dx, dy);
        else { this.theta -= dx * 0.0055; this.phi -= dy * 0.0055; this.apply(); }
    }
    up(e) { this.ptrs.delete(e.pointerId); this.pinch = null; }
    tick(dt) { if (this.autoRotate && !this.ptrs.size) { this.theta += dt * 0.08; this.apply(); } }
}

// ---- main view
export class Wflow3DView {
    constructor(map, { projectName }) {
        this.lmap = map; this.projectName = projectName;
        this.host = map.getContainer().closest('.panel-map') || map.getContainer().parentElement;
        this.S = loadSettings(SETTINGS_KEY, DEFAULTS);
        this.visible = false; this.playing = false; this.t = 0; this.raf = null;
        this.velCache = new Map(); this.varCache = new Map(); this.pending = new Map();
        this.extraSeries = [];
    }

    q(extra = '') {
        return `projectName=${encodeURIComponent(this.projectName)}&flowName=${encodeURIComponent(this.info.flow)}&v=${encodeURIComponent(this.info.version || '')}${extra}`;
    }

    //  init / data
    async init(opts = {}) {
        ensureCss();
        const [THREE] = await Promise.all([loadThree()]);
        this.THREE = THREE;
        let flowName = opts.flowName || this.S.flow;
        if (!flowName) { try { flowName = JSON.parse(localStorage.getItem('dash.wflow.settings'))?.flow; } catch (e) { /* ignore */ } }
        let info;
        try { info = await post('wflow/init', { projectName: this.projectName, flowName: flowName || undefined, ...(opts.res !== undefined ? { res: opts.res } : {}) }); }
        catch (err) { if (!flowName || opts.flowName) throw err; info = await post('wflow/init', { projectName: this.projectName }); }
        if (info.status !== 'ok') throw new Error(info.message || 'Wflow results not available');
        this.info = info; this.S.flow = info.flow; saveSettings(SETTINGS_KEY, this.S);
        this.velCache.clear(); this.varCache.clear(); this.pending.clear(); this.extraSeries = [];
        const g = this.g = info.grid;
        const geo = (kind) => fetch(`/dashboard/wflow/geometry/${kind}?${this.q()}`).then(r => r.ok ? r.json() : null).catch(() => null);
        const [maskBuf, ter, scene, rivers, basins] = await Promise.all([
            getBin(`/dashboard/wflow/mask?${this.q()}`),
            getBin(`/dashboard/wflow3d/terrain?${this.q()}`, true),
            getJson(`/dashboard/wflow3d/scene?${this.q()}`).catch((e) => ({ outlets: [], precip: null, notes: [e.message] })),
            info.river_cells ? fetch(`/dashboard/wflow/rivers?${this.q()}`).then(r => r.json()) : null,
            geo('basins'),
        ]);
        this.mask = new Uint8Array(maskBuf);
        this.elev = new Float32Array(ter.buf);
        this.zmin = Number(ter.headers.get('X-Z-Min')) || 0; this.zmax = Number(ter.headers.get('X-Z-Max')) || 1;
        this.scene3d = scene; this.riverGeo = rivers; this.basinGeo = basins;
        // chỉ số ô hợp lệ (thứ tự giống cache viewer: mask > 0, hàng 0 = phía nam)
        const N = g.nx * g.ny; this.idxOf = new Int32Array(N).fill(-1); this.cellsOf = [];
        for (let k = 0; k < N; k++) if (this.mask[k]) { this.idxOf[k] = this.cellsOf.length; this.cellsOf.push(k); }
        this.T = info.times.length;
        this.pickVariables();
        this.buildDom();
        this.buildScene();
        await this.ensureFrames(0);
        this.applyTime(this.S.follow && this.lastIso ? Math.max(0, nearestIndex(info.times, this.lastIso)) : this.defaultStart());
        this.renderHydro();
        return this;
    }

    defaultStart() {                       // bắt đầu ngay trước khi mưa (nếu có), không thì bước 0
        const p = this.scene3d?.precip?.mm_h;
        if (!p) return 0;
        const i = p.findIndex(v => v > 0);
        return Math.max(0, i - 1);
    }

    get vars() { return this.info?.variables || {}; }
    get hasVelocity() { return !!this.info?.velocity; }

    pickVariables() {
        const ok = (n, kind) => n && this.vars[n]?.kind === kind;
        this.riverVar = this.S.river === '' ? null : ok(this.S.river, 'river') ? this.S.river
            : (ok(this.scene3d?.discharge_var, 'river') ? this.scene3d.discharge_var : this.info.river_default || null);
        this.gridVar = ok(this.S.grid, 'grid') ? this.S.grid : null;
    }

    async loadVar(name, i) {
        const key = `${name}:${i}`;
        if (this.varCache.has(key)) return this.varCache.get(key);
        const v = decode(await getBin(`/dashboard/wflow/frame/${encodeURIComponent(name)}/${i}?${this.q()}`), this.vars[name].dtype);
        this.varCache.set(key, v);
        if (this.varCache.size > 40) this.varCache.delete(this.varCache.keys().next().value);
        return v;
    }

    async loadVel(i) {
        if (this.velCache.has(i)) return this.velCache.get(i);
        const g = this.g, raw = decode(await getBin(`/dashboard/wflow/velocity/${i}?${this.q()}`), '<f2');
        const n = this.cellsOf.length, U = new Float32Array(g.nx * g.ny).fill(NaN), V = new Float32Array(g.nx * g.ny).fill(NaN);
        const wet = []; const riverWet = [];
        for (let j = 0; j < n; j++) {
            const u = raw[j], v = raw[n + j], k = this.cellsOf[j];
            if (!Number.isFinite(u) || !Number.isFinite(v) || this.mask[k] === 2) continue;
            U[k] = u; V[k] = v;
            const s = Math.hypot(u, v);
            if (s > 1e-5) { wet.push(k); if (this.riverCell?.[k]) riverWet.push(k); }
        }
        const out = { U, V, wet: Int32Array.from(wet), riverWet: Int32Array.from(riverWet) };
        this.velCache.set(i, out);
        if (this.velCache.size > 8) this.velCache.delete(this.velCache.keys().next().value);
        return out;
    }

    // Nạp dữ liệu của bước i và i+1 (nội suy giữa hai bước)
    ensureFrames(i) {
        const key = i;
        if (this.pending.has(key)) return this.pending.get(key);
        const jobs = [];
        for (const k of [i, Math.min(this.T - 1, i + 1)]) {
            if (this.hasVelocity && this.S.particles) jobs.push(this.loadVel(k));
            if (this.riverVar) jobs.push(this.loadVar(this.riverVar, k));
        }
        if (this.gridVar) jobs.push(this.loadVar(this.gridVar, i));
        const p = Promise.all(jobs).finally(() => this.pending.delete(key));
        this.pending.set(key, p);
        return p;
    }
    framesReady(i) {
        const j = Math.min(this.T - 1, i + 1);
        if (this.hasVelocity && this.S.particles && !(this.velCache.has(i) && this.velCache.has(j))) return false;
        if (this.riverVar && !(this.varCache.has(`${this.riverVar}:${i}`) && this.varCache.has(`${this.riverVar}:${j}`))) return false;
        if (this.gridVar && !this.varCache.has(`${this.gridVar}:${i}`)) return false;
        return true;
    }

    //  DOM
    buildDom() {
        this.root?.remove();
        const r = this.root = document.createElement('div');
        r.className = 'w3d-root'; r.hidden = !this.visible;
        r.innerHTML = `
            <canvas class="w3d-canvas"></canvas>
            <div class="w3d-hud w3d-tl"><div class="w3d-title">Wflow 3D · <span data-r="flow"></span></div>
                <div class="w3d-sub" data-r="sub"></div><div class="w3d-stats" data-r="stats"></div></div>
            <div class="w3d-hud w3d-tr">
                <button type="button" data-a="reset" title="Reset view">⟲</button>
                <button type="button" data-a="top" title="Top view">⊤</button>
                <button type="button" data-a="rotate" title="Auto-rotate">↻</button>
                <button type="button" data-a="pick" title="Click a river to add a hydrograph point (on/off)">📍</button>
                <button type="button" data-a="hydro" title="Show / hide hydrograph">📈</button>
                <button type="button" data-a="expand" title="Toggle large view">⤢</button>
            </div>
            <div class="w3d-hud w3d-legend" data-r="legend"></div>
            <div class="w3d-hud w3d-hydro" data-r="hydro"><div class="w3d-hydro-head"><span>Discharge at outlets</span>
                <span class="w3d-hint" data-r="hint"></span><button type="button" class="w3d-clear" data-a="clearpts" hidden title="Remove all added points">Clear points</button></div><div class="w3d-hydro-plot" data-r="plot"></div></div>
            <div class="w3d-hud w3d-time">
                <button type="button" class="tb-btn" data-a="play" title="Play / pause">▶</button>
                <input type="range" data-r="slider" min="0" max="${Math.max(0, this.T - 1)}" step="0.01" value="0">
                <span class="tb-label" data-r="tlabel">-</span>
            </div>
            <div class="w3d-tip" data-r="tip" hidden></div>
            <div class="w3d-note" data-r="note" hidden></div>`;
        this.host.appendChild(r);
        this.$ = (k) => r.querySelector(`[data-r="${k}"]`);
        if (window.L) { L.DomEvent.disableClickPropagation(r); L.DomEvent.disableScrollPropagation(r); }
        r.querySelector('.w3d-tr').addEventListener('click', (e) => {
            const a = e.target.closest('[data-a]')?.dataset.a; if (!a) return;
            if (a === 'reset') this.resetView();
            if (a === 'top') this.orbit.set(this.orbit.target, this.orbit.r, this.orbit.theta, 0.05);
            if (a === 'rotate') { this.S.rotate = !this.S.rotate; this.orbit.autoRotate = this.S.rotate; saveSettings(SETTINGS_KEY, this.S); this.syncButtons(); }
            if (a === 'hydro') { this.S.hydro = !this.S.hydro; saveSettings(SETTINGS_KEY, this.S); this.renderHydro(); this.syncButtons(); }
            if (a === 'expand') document.getElementById('map-expand')?.click();
            if (a === 'pick') { this.S.pickPoints = !this.S.pickPoints; saveSettings(SETTINGS_KEY, this.S); this.syncButtons(); this.renderHydro(); }
        });
        r.querySelector('[data-a="clearpts"]').addEventListener('click', () => this.clearPoints());
        r.querySelector('[data-a="play"]').addEventListener('click', () => this.setPlayingLocal(!this.playing));
        const slider = this.$('slider');
        slider.addEventListener('input', () => { this.applyTime(Number(slider.value)); });
        const info = this.info, p = this.scene3d?.precip;
        this.$('flow').textContent = info.flow;
        const total = p?.mm_h ? p.mm_h.reduce((a, v) => a + (v || 0), 0) * (info.step_hours || 1) : null;
        this.$('sub').textContent = [
            this.scene3d?.area_km2 ? `${fmt(this.scene3d.area_km2)} km²` : null,
            `${this.g.res_m} m grid`,
            this.scene3d?.outlets?.length ? `${this.scene3d.outlets.length} outlet${this.scene3d.outlets.length > 1 ? 's' : ''}` : null,
            total ? `rain ${fmt(total, 1)} mm (peak ${fmt(p.max_mm_h, 1)} mm/h)` : null,
        ].filter(Boolean).join(' · ');
        const notes = [...(this.scene3d?.notes || [])];
        if (!this.hasVelocity) notes.push('No velocity field in this Wflow output: particles are off.');
        if (this.info.step_hours >= 24) notes.push('Daily Wflow output: run Wflow hourly for a storm-scale animation.');
        if (notes.length) { const n = this.$('note'); n.hidden = false; n.textContent = notes.join(' '); setTimeout(() => { n.hidden = true; }, 9000); }
        this.syncButtons();
    }

    syncButtons() {
        const r = this.root; if (!r) return;
        r.querySelector('[data-a="rotate"]').classList.toggle('on', !!this.S.rotate);
        r.querySelector('[data-a="hydro"]').classList.toggle('on', !!this.S.hydro);
        r.querySelector('[data-a="pick"]').classList.toggle('on', !!this.S.pickPoints);
        r.querySelector('[data-a="play"]').textContent = this.playing ? '❚❚' : '▶';
        r.querySelector('[data-a="play"]').classList.toggle('playing', this.playing);
    }

    //  3D scene
    buildScene() {
        const THREE = this.THREE, g = this.g, canvas = this.root.querySelector('.w3d-canvas');
        this.renderer?.dispose();
        const renderer = this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, preserveDrawingBuffer: false });
        renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        const scene = this.scene = new THREE.Scene();
        scene.background = new THREE.Color('#07111f');
        const span = Math.max(g.nx, g.ny);
        scene.fog = new THREE.Fog('#07111f', span * 1.6, span * 4);
        this.camera = new THREE.PerspectiveCamera(40, 1, 0.3, span * 12);
        scene.add(new THREE.HemisphereLight('#dbeafe', '#1e293b', 1.4));
        const sun = new THREE.DirectionalLight('#fff7e6', 1.6); sun.position.set(-span, span * 0.9, -span * 0.6); scene.add(sun);
        this.orbit = new Orbit(THREE, this.camera, canvas, null);
        this.orbit.minR = 8; this.orbit.maxR = span * 4; this.orbit.autoRotate = !!this.S.rotate;
        // địa hình
        const st = this.stride = Math.max(1, Math.ceil(Math.max(g.nx, g.ny) / MAX_MESH));
        this.LX = Math.floor((g.nx - 1) / st) + 1; this.LY = Math.floor((g.ny - 1) / st) + 1;
        this.HL = new Float32Array(this.LX * this.LY);
        const geom = new THREE.BufferGeometry();
        const pos = new Float32Array(this.LX * this.LY * 3), uv = new Float32Array(this.LX * this.LY * 2);
        for (let ly = 0; ly < this.LY; ly++) for (let lx = 0; lx < this.LX; lx++) {
            const v = ly * this.LX + lx, ix = lx * st, iy = ly * st;
            pos[v * 3] = this.wx(ix); pos[v * 3 + 2] = this.wz(iy);
            uv[v * 2] = (ix + 0.5) / g.nx; uv[v * 2 + 1] = (iy + 0.5) / g.ny;
        }
        const idx = [];
        for (let ly = 0; ly < this.LY - 1; ly++) for (let lx = 0; lx < this.LX - 1; lx++) {
            const a = ly * this.LX + lx, b = a + 1, c = a + this.LX, d = c + 1;
            idx.push(a, b, c, b, d, c);              // ngược chiều kim đồng hồ khi nhìn từ trên xuống
        }
        geom.setIndex(idx);
        geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        geom.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
        this.terrainGeom = geom;
        this.terrainMat = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.96, metalness: 0 });
        this.terrain = new THREE.Mesh(geom, this.terrainMat); scene.add(this.terrain);
        // lớp phủ biến lưới (dùng chung hình học)
        this.overlayData = new Uint8Array(g.nx * g.ny * 4);
        this.overlayTex = new THREE.DataTexture(this.overlayData, g.nx, g.ny, THREE.RGBAFormat);
        this.overlayTex.magFilter = THREE.LinearFilter; this.overlayTex.minFilter = THREE.LinearFilter; this.overlayTex.needsUpdate = true;
        this.overlay = new THREE.Mesh(geom, new THREE.MeshBasicMaterial({ map: this.overlayTex, transparent: true, depthWrite: false,
            polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2, opacity: this.S.gridOpacity }));
        this.overlay.visible = false; scene.add(this.overlay);
        // thành bên ("khối" địa hình)
        this.skirt = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshStandardMaterial({ color: '#3b2f25', roughness: 1, side: THREE.DoubleSide }));
        scene.add(this.skirt);
        this.buildRivers(); this.buildBasins(); this.buildOutlets(); this.buildParticles();
        this.updateHeights(); this.applyBase();
        this.resetView();
        // tương tác: hover / click
        this.raycaster = new THREE.Raycaster();
        let last = 0;
        canvas.addEventListener('pointermove', (e) => { const now = performance.now(); if (now - last > 70) { last = now; this.hover(e); } });
        canvas.addEventListener('pointerleave', () => { this.$('tip').hidden = true; });
        canvas.addEventListener('pointerup', (e) => { if (this.orbit.moved < 4 && e.button === 0) this.pick(e); });
        this.ro?.disconnect();
        this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(this.root);
        this.resize();
    }

    // toạ độ lưới (ô, hàng 0 = nam) -> thế giới (1 đơn vị = 1 ô)
    wx(fx) { return fx - (this.g.nx - 1) / 2; }
    wz(fy) { return (this.g.ny - 1) / 2 - fy; }
    yOf(z) { return (z - this.zmin) / this.g.res_m * this.S.exag; }
    // độ cao bề mặt lưới hiển thị (nội suy trên lưới đỉnh) tại toạ độ ô
    hAt(fx, fy) {
        const st = this.stride, LX = this.LX, LY = this.LY;
        let x = fx / st, y = fy / st;
        x = Math.max(0, Math.min(LX - 1.0001, x)); y = Math.max(0, Math.min(LY - 1.0001, y));
        const x0 = x | 0, y0 = y | 0, tx = x - x0, ty = y - y0, H = this.HL, a = y0 * LX + x0;
        return (H[a] * (1 - tx) + H[a + 1] * tx) * (1 - ty) + (H[a + LX] * (1 - tx) + H[a + LX + 1] * tx) * ty;
    }

    updateHeights() {
        const g = this.g, st = this.stride, pos = this.terrainGeom.attributes.position.array;
        for (let ly = 0; ly < this.LY; ly++) for (let lx = 0; lx < this.LX; lx++) {
            const v = ly * this.LX + lx, y = this.yOf(this.elev[(ly * st) * g.nx + lx * st]);
            this.HL[v] = y; pos[v * 3 + 1] = y;
        }
        this.terrainGeom.attributes.position.needsUpdate = true;
        this.terrainGeom.computeVertexNormals(); this.terrainGeom.computeBoundingSphere(); this.terrainGeom.computeBoundingBox();
        this.buildSkirt();
        this.layoutRivers(); this.layoutBasins(); this.layoutOutlets();
        this.rainTop = this.yOf(this.zmax) + Math.max(this.g.nx, this.g.ny) * 0.12;
    }

    buildSkirt() {
        const THREE = this.THREE, LX = this.LX, LY = this.LY, st = this.stride;
        const base = -2 - (this.yOf(this.zmax) - this.yOf(this.zmin)) * 0.08;
        const ring = [];
        for (let lx = 0; lx < LX; lx++) ring.push([lx, 0]);
        for (let ly = 1; ly < LY; ly++) ring.push([LX - 1, ly]);
        for (let lx = LX - 2; lx >= 0; lx--) ring.push([lx, LY - 1]);
        for (let ly = LY - 2; ly > 0; ly--) ring.push([0, ly]);
        ring.push(ring[0]);
        const pos = [];
        for (let i = 0; i < ring.length - 1; i++) {
            const [ax, ay] = ring[i], [bx, by] = ring[i + 1];
            const A = [this.wx(ax * st), this.HL[ay * LX + ax], this.wz(ay * st)], B = [this.wx(bx * st), this.HL[by * LX + bx], this.wz(by * st)];
            pos.push(...A, A[0], base, A[2], ...B, ...B, A[0], base, A[2], B[0], base, B[2]);
        }
        // đáy
        const x0 = this.wx(0), x1 = this.wx((LX - 1) * st), z0 = this.wz(0), z1 = this.wz((LY - 1) * st);
        pos.push(x0, base, z0, x1, base, z0, x1, base, z1, x0, base, z0, x1, base, z1, x0, base, z1);
        const gm = new THREE.BufferGeometry(); gm.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); gm.computeVertexNormals();
        this.skirt.geometry.dispose(); this.skirt.geometry = gm;
    }

    async applyBase() {
        const THREE = this.THREE, g = this.g, S = this.S, token = (this.baseToken = (this.baseToken || 0) + 1);
        let canvas = null;
        if (S.base === 'satellite') {
            const W = Math.min(4096, Math.max(1024, g.nx * 6)), H = Math.min(4096, Math.round(W * g.ny / g.nx));
            try { canvas = await satelliteCanvas(g.bounds, W, H); }
            catch (e) { console.warn('Wflow 3D: satellite imagery not available', e); this.flash('Satellite imagery not available: using shaded relief.'); }
        }
        if (token !== this.baseToken) return;
        if (!canvas) canvas = hillshadeCanvas(this.elev, g.nx, g.ny, g.res_m, S.base === 'plain');
        if (S.dim) {                                               // làm tối vùng ngoài lưu vực
            const m = document.createElement('canvas'); m.width = g.nx; m.height = g.ny;
            const mc = m.getContext('2d'), img = mc.createImageData(g.nx, g.ny);
            for (let iy = 0; iy < g.ny; iy++) for (let ix = 0; ix < g.nx; ix++) {
                if (this.mask[iy * g.nx + ix]) continue;
                const o = ((g.ny - 1 - iy) * g.nx + ix) * 4; img.data[o] = 4; img.data[o + 1] = 10; img.data[o + 2] = 22; img.data[o + 3] = 150;
            }
            mc.putImageData(img, 0, 0);
            const ctx = canvas.getContext('2d'); ctx.imageSmoothingEnabled = true; ctx.drawImage(m, 0, 0, canvas.width, canvas.height);
        }
        const tex = new THREE.CanvasTexture(canvas);
        tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
        this.terrainMat.map?.dispose(); this.terrainMat.map = tex; this.terrainMat.needsUpdate = true;
    }

    //  rivers (dải băng, bề rộng & màu theo giá trị)
    buildRivers() {
        const THREE = this.THREE, g = this.g;
        this.riverCell = new Uint8Array(g.nx * g.ny);
        this.riverAt = new Map();
        const feats = this.riverGeo?.features || [];
        const n = feats.length, seg = this.seg = new Float32Array(n * 4);
        feats.forEach((f, k) => {
            const c = f.geometry.coordinates, id = f.properties?.id ?? k;
            const ax = (c[0][0] - g.lon0) / g.dlon, ay = (c[0][1] - g.lat0) / g.dlat;
            const bx = (c[1][0] - g.lon0) / g.dlon, by = (c[1][1] - g.lat0) / g.dlat;
            seg.set([ax, ay, bx, by], id * 4);
            const ix = Math.round(ax), iy = Math.round(ay);
            if (ix >= 0 && ix < g.nx && iy >= 0 && iy < g.ny) {
                const kk = iy * g.nx + ix; this.riverCell[kk] = 1;
                (this.riverAt.get(kk) || this.riverAt.set(kk, []).get(kk)).push(id);
            }
        });
        this.nRiver = n;
        const geom = new THREE.BufferGeometry();
        geom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 4 * 3), 3));
        geom.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 4 * 3), 3));
        const index = new Uint32Array(n * 6);
        for (let i = 0; i < n; i++) index.set([i * 4, i * 4 + 1, i * 4 + 2, i * 4 + 2, i * 4 + 1, i * 4 + 3], i * 6);
        geom.setIndex(new THREE.BufferAttribute(index, 1));
        this.riverMesh?.geometry.dispose();
        this.riverMesh = new THREE.Mesh(geom, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.95,
            depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4, side: THREE.DoubleSide }));
        this.riverMesh.renderOrder = 2; this.riverMesh.frustumCulled = false;
        this.scene.add(this.riverMesh);
        this.riverLut = lut('flow');
        this.riverVals = new Float32Array(n).fill(NaN);
    }

    layoutRivers() {
        if (!this.nRiver) return;
        const pos = this.riverMesh.geometry.attributes.position.array, seg = this.seg, lift = 0.25 + 0.05 * this.S.exag;
        const vals = this.riverVals, m = this.vars[this.riverVar] || {}, hi = m.p99 || m.max || 1;
        for (let i = 0; i < this.nRiver; i++) {
            let ax = seg[i * 4], ay = seg[i * 4 + 1], bx = seg[i * 4 + 2], by = seg[i * 4 + 3];
            let dx = bx - ax, dy = by - ay, L = Math.hypot(dx, dy);
            if (L < 1e-6) { dx = 0.5; dy = 0; L = 0.5; bx = ax + 0.5; }
            dx /= L; dy /= L; ax -= dx * 0.3; ay -= dy * 0.3; bx += dx * 0.3; by += dy * 0.3;
            const v = vals[i], t = Number.isFinite(v) ? clamp01(Math.sqrt(Math.max(0, v) / hi)) : 0;
            const w = (Number.isFinite(v) ? 0.25 + 2.2 * t : 0.15) * this.S.riverWidth * 0.5;
            const px = -dy * w, py = dx * w;
            const ha = this.hAt(ax, ay) + lift, hb = this.hAt(bx, by) + lift, o = i * 12;
            pos[o] = this.wx(ax + px); pos[o + 1] = ha; pos[o + 2] = this.wz(ay + py);
            pos[o + 3] = this.wx(ax - px); pos[o + 4] = ha; pos[o + 5] = this.wz(ay - py);
            pos[o + 6] = this.wx(bx + px); pos[o + 7] = hb; pos[o + 8] = this.wz(by + py);
            pos[o + 9] = this.wx(bx - px); pos[o + 10] = hb; pos[o + 11] = this.wz(by - py);
        }
        this.riverMesh.geometry.attributes.position.needsUpdate = true;
    }

    paintRivers() {
        if (!this.nRiver) return;
        const col = this.riverMesh.geometry.attributes.color.array, L = this.riverLut, vals = this.riverVals;
        const m = this.vars[this.riverVar] || {}, hi = m.p99 || m.max || 1;
        for (let i = 0; i < this.nRiver; i++) {
            const v = vals[i];
            let r = 0.2, g = 0.27, b = 0.36;
            if (Number.isFinite(v)) { const t = clamp01(Math.sqrt(Math.max(0, v) / hi)), k = Math.round(t * 255) * 3; r = L[k]; g = L[k + 1]; b = L[k + 2]; }
            for (let q = 0; q < 4; q++) { col[i * 12 + q * 3] = r; col[i * 12 + q * 3 + 1] = g; col[i * 12 + q * 3 + 2] = b; }
        }
        this.riverMesh.geometry.attributes.color.needsUpdate = true;
        this.riverMesh.visible = !!this.riverVar;
    }

    //  basin outlines, outlets
    buildBasins() {
        const THREE = this.THREE;
        this.basinLines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: '#f8fafc', transparent: true, opacity: 0.75, depthWrite: false }));
        this.basinLines.renderOrder = 3; this.scene.add(this.basinLines);
    }
    layoutBasins() {
        const g = this.g, pts = [], lift = 0.35 + 0.05 * this.S.exag;
        const ring = (coords) => {
            for (let i = 0; i < coords.length - 1; i++) {
                const ax = (coords[i][0] - g.lon0) / g.dlon, ay = (coords[i][1] - g.lat0) / g.dlat;
                const bx = (coords[i + 1][0] - g.lon0) / g.dlon, by = (coords[i + 1][1] - g.lat0) / g.dlat;
                const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / 0.7));
                for (let s = 0; s < n; s++) {
                    const x0 = ax + (bx - ax) * s / n, y0 = ay + (by - ay) * s / n, x1 = ax + (bx - ax) * (s + 1) / n, y1 = ay + (by - ay) * (s + 1) / n;
                    pts.push(this.wx(x0), this.hAt(x0, y0) + lift, this.wz(y0), this.wx(x1), this.hAt(x1, y1) + lift, this.wz(y1));
                }
            }
        };
        for (const f of this.basinGeo?.features || []) {
            const gm = f.geometry; if (!gm) continue;
            const polys = gm.type === 'Polygon' ? [gm.coordinates] : gm.type === 'MultiPolygon' ? gm.coordinates : [];
            polys.forEach(p => p.forEach(ring));
        }
        const geom = new this.THREE.BufferGeometry(); geom.setAttribute('position', new this.THREE.Float32BufferAttribute(pts, 3));
        this.basinLines.geometry.dispose(); this.basinLines.geometry = geom;
        this.basinLines.visible = !!this.S.basins && pts.length > 0;
    }

    // nhãn dạng "ghim" (sprite + chân) dùng cho cửa ra Q1.. và điểm người dùng thêm P1..
    makePin(text, col, group, userData) {
        const THREE = this.THREE, c = document.createElement('canvas'); c.width = 128; c.height = 64;
        const x = c.getContext('2d');
        x.fillStyle = 'rgba(7,17,31,0.85)'; x.strokeStyle = col; x.lineWidth = 4;
        x.beginPath(); x.roundRect(4, 4, 120, 56, 14); x.fill(); x.stroke();
        x.fillStyle = '#f8fafc'; x.font = 'bold 30px system-ui, sans-serif'; x.textAlign = 'center'; x.textBaseline = 'middle';
        x.fillText(text, 64, 34);
        const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false, transparent: true }));
        sprite.renderOrder = 10; sprite.userData = userData;
        const line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: col, depthTest: false, transparent: true, opacity: 0.9 }));
        line.renderOrder = 9;
        group.add(sprite, line);
        return { sprite, line };
    }
    placePin(pin, lon, lat, scale = 1) {
        const g = this.g, span = Math.max(g.nx, g.ny), stem = span * 0.05 * scale;
        const fx = (lon - g.lon0) / g.dlon, fy = (lat - g.lat0) / g.dlat, h = this.hAt(fx, fy), V = this.THREE.Vector3;
        pin.sprite.position.set(this.wx(fx), h + stem, this.wz(fy));
        pin.sprite.scale.set(span * 0.045 * scale, span * 0.0225 * scale, 1);
        pin.line.geometry.dispose();
        pin.line.geometry = new this.THREE.BufferGeometry().setFromPoints([new V(this.wx(fx), h, this.wz(fy)), new V(this.wx(fx), h + stem * 0.8, this.wz(fy))]);
    }
    disposePin(pin, group) {
        if (!pin) return;
        group.remove(pin.sprite, pin.line);
        pin.sprite.material.map?.dispose(); pin.sprite.material.dispose(); pin.line.geometry.dispose(); pin.line.material.dispose();
    }

    buildOutlets() {
        const THREE = this.THREE;
        this.outletGroup = new THREE.Group(); this.scene.add(this.outletGroup);
        this.pointGroup = new THREE.Group(); this.scene.add(this.pointGroup);
        (this.scene3d?.outlets || []).slice(0, 6).forEach((o, n) => {        // 6 cửa ra lớn nhất (giống biểu đồ)
            o._pin = this.makePin(`Q${o.id}`, OUTLET_COLORS[n % OUTLET_COLORS.length], this.outletGroup, o);
        });
    }
    layoutOutlets() {
        for (const o of this.scene3d?.outlets || []) if (o._pin) this.placePin(o._pin, o.lon, o.lat);
        this.outletGroup.visible = !!this.S.outlets;
        this.layoutPoints();
    }

    //  điểm hydrograph do người dùng thêm (P1..P6)
    rebuildPoints() {                                  // đánh số & tô màu lại sau khi thêm / xoá
        if (!this.pointGroup) return;
        this.extraSeries.forEach((s) => { this.disposePin(s._pin, this.pointGroup); s._pin = null; });
        this.extraSeries.forEach((s, n) => {
            s.color = POINT_COLORS[n % POINT_COLORS.length];
            s._pin = this.makePin(`P${n + 1}`, s.color, this.pointGroup, { point: n });
        });
        this.layoutPoints();
    }
    layoutPoints() { for (const s of this.extraSeries || []) if (s._pin) this.placePin(s._pin, s.lon, s.lat, 0.8); }
    removePoint(n) {
        const s = this.extraSeries[n]; if (!s) return;
        this.disposePin(s._pin, this.pointGroup); s._pin = null;
        this.extraSeries.splice(n, 1);
        this.rebuildPoints(); this.renderHydro();
    }
    clearPoints() {
        this.extraSeries.forEach((s) => { this.disposePin(s._pin, this.pointGroup); s._pin = null; });
        this.extraSeries = [];
        this.renderHydro();
    }

    //  particles (dòng chảy) & rain
    buildParticles() {
        const THREE = this.THREE;
        for (const o of [this.flowLines, this.flowPts, this.rainLines]) { if (o) { this.scene.remove(o); o.geometry.dispose(); } }
        const N = this.pN = Math.round(this.S.pCount);
        this.px = new Float32Array(N); this.py = new Float32Array(N); this.pu = new Float32Array(N); this.pv = new Float32Array(N);
        this.pAge = new Float32Array(N); this.pLife = new Float32Array(N); this.pSpd = new Float32Array(N);
        for (let i = 0; i < N; i++) { this.pAge[i] = 1; this.pLife[i] = 0; }      // chưa sinh
        this.pNext = 0;
        const lg = new THREE.BufferGeometry();
        lg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 6), 3));
        lg.setAttribute('color', new THREE.BufferAttribute(new Float32Array(N * 6), 3));
        this.flowLines = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
        this.flowLines.frustumCulled = false; this.flowLines.renderOrder = 5;
        const pg = new THREE.BufferGeometry();
        pg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 3), 3));
        pg.setAttribute('color', new THREE.BufferAttribute(new Float32Array(N * 3), 3));
        if (!this.dotTex) {
            const c = document.createElement('canvas'); c.width = c.height = 32; const x = c.getContext('2d');
            const gr = x.createRadialGradient(16, 16, 0, 16, 16, 16); gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.4, 'rgba(255,255,255,0.6)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
            x.fillStyle = gr; x.fillRect(0, 0, 32, 32); this.dotTex = new THREE.CanvasTexture(c);
        }
        this.flowPts = new THREE.Points(pg, new THREE.PointsMaterial({ size: this.S.pSize, sizeAttenuation: false, map: this.dotTex, vertexColors: true,
            transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
        this.flowPts.frustumCulled = false; this.flowPts.renderOrder = 6;
        this.scene.add(this.flowLines, this.flowPts);
        // mưa
        const R = this.rN = Math.round(this.S.rainCount);
        this.rx = new Float32Array(R); this.ry = new Float32Array(R); this.rh = new Float32Array(R); this.rOn = new Uint8Array(R); this.rNext = 0;
        const rg = new THREE.BufferGeometry();
        rg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(R * 6), 3));
        this.rainLines = new THREE.LineSegments(rg, new THREE.LineBasicMaterial({ color: '#bfdbfe', transparent: true, opacity: 0.42, depthWrite: false, blending: THREE.AdditiveBlending }));
        this.rainLines.frustumCulled = false; this.rainLines.renderOrder = 7;
        this.scene.add(this.rainLines);
        this.speedLut = (() => {
            const out = new Float32Array(256 * 3), stops = SPEED_STOPS.map(h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255));
            for (let i = 0; i < 256; i++) {
                const x = i / 255 * (stops.length - 1), a = Math.min(stops.length - 2, Math.floor(x)), f = x - a;
                for (let c = 0; c < 3; c++) out[i * 3 + c] = stops[a][c] + (stops[a + 1][c] - stops[a][c]) * f;
            }
            return out;
        })();
        this.rainAcc = 0;
    }

    // vận tốc (m/s) tại toạ độ ô, nội suy song tuyến bỏ ô khô; nội suy giữa 2 bước theo f
    sampleUV(fx, fy, A, B, f) {
        const nx = this.g.nx, ny = this.g.ny, x0 = Math.floor(fx), y0 = Math.floor(fy), tx = fx - x0, ty = fy - y0;
        let su = 0, sv = 0, sw = 0;
        for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
            const x = x0 + dx, y = y0 + dy;
            if (x < 0 || y < 0 || x >= nx || y >= ny) continue;
            const k = y * nx + x, w = (dx ? tx : 1 - tx) * (dy ? ty : 1 - ty);
            let u = A.U[k], v = A.V[k];
            if (B && f > 0) { const u2 = B.U[k], v2 = B.V[k]; if (Number.isFinite(u2)) { u = Number.isFinite(u) ? u + (u2 - u) * f : u2; v = Number.isFinite(v) ? v + (v2 - v) * f : v2; } }
            if (!Number.isFinite(u) || !Number.isFinite(v) || w <= 0) continue;
            su += u * w; sv += v * w; sw += w;
        }
        if (sw < 1e-6) return null;
        return [su / sw, sv / sw];
    }

    spawnFlow(i, A, fx = null, fy = null) {
        let x = fx, y = fy;
        if (x === null) {
            const pool = (A.riverWet.length && Math.random() < 0.45) ? A.riverWet : A.wet;
            if (!pool.length) { this.pAge[i] = 1; this.pLife[i] = 0; return; }
            const k = pool[(Math.random() * pool.length) | 0];
            x = (k % this.g.nx) + Math.random() - 0.5; y = ((k / this.g.nx) | 0) + Math.random() - 0.5;
        }
        this.px[i] = x; this.py[i] = y; this.pu[i] = 0; this.pv[i] = 0;
        this.pAge[i] = 0; this.pLife[i] = 2.5 + Math.random() * 4.5;
    }

    stepParticles(dt) {
        const N = this.pN, i0 = Math.floor(this.t), f = this.t - i0;
        const A = this.velCache.get(i0), B = this.velCache.get(Math.min(this.T - 1, i0 + 1));
        const show = this.S.particles && this.hasVelocity && A;
        this.flowLines.visible = this.flowPts.visible = !!show;
        if (!show) return;
        const vmax = this.info.velocity.speed_max || 1, rate = 7 * this.S.pSpeed;         // ô / giây ở vmax
        const lp = this.flowLines.geometry.attributes.position.array, lc = this.flowLines.geometry.attributes.color.array;
        const pp = this.flowPts.geometry.attributes.position.array, pc = this.flowPts.geometry.attributes.color.array;
        const lift = 0.45 + 0.06 * this.S.exag, mode = this.S.pColor, SL = this.speedLut;
        const fixed = mode === 'white' ? [0.95, 0.97, 1] : mode === 'cyan' ? [0.35, 0.9, 1] : null;
        for (let i = 0; i < N; i++) {
            this.pAge[i] += dt;
            if (this.pAge[i] >= this.pLife[i]) this.spawnFlow(i, A);
            let x = this.px[i], y = this.py[i], moved = 0, sp = 0;
            if (this.pLife[i] > 0) {
                let remain = dt;
                while (remain > 1e-4) {
                    const uv = this.sampleUV(x, y, A, B, f);
                    if (!uv) { this.pAge[i] = this.pLife[i]; break; }
                    sp = Math.hypot(uv[0], uv[1]);
                    if (sp < 1e-6) { break; }
                    const c = Math.sqrt(Math.min(1, sp / vmax)), step = rate * c;
                    const h = Math.min(remain, 0.5 / Math.max(step, 1e-6));
                    x += uv[0] / sp * step * h; y += uv[1] / sp * step * h; moved += step * h; remain -= h;
                    const k = Math.round(y) * this.g.nx + Math.round(x);
                    if (this.mask[k] === 2) { this.pAge[i] = this.pLife[i]; break; }       // vào hồ: dừng
                }
                this.pu[i] = this.pu[i] * 0.7 + (x - this.px[i]) * 0.3; this.pv[i] = this.pv[i] * 0.7 + (y - this.py[i]) * 0.3;
                this.px[i] = x; this.py[i] = y; this.pSpd[i] = sp;
            }
            const alive = this.pLife[i] > 0 && this.pAge[i] < this.pLife[i];
            const fade = alive ? Math.min(1, this.pAge[i] * 3, (this.pLife[i] - this.pAge[i]) * 2) : 0;
            let r, g, b;
            if (fixed) [r, g, b] = fixed;
            else { const k = Math.round(Math.sqrt(clamp01(this.pSpd[i] / vmax)) * 255) * 3; r = SL[k]; g = SL[k + 1]; b = SL[k + 2]; }
            const hx = this.wx(x), hz = this.wz(y), hy = this.hAt(x, y) + lift;
            const tl = 5 + 9 * Math.sqrt(clamp01(this.pSpd[i] / vmax));
            const tx = x - this.pu[i] * tl, ty = y - this.pv[i] * tl;
            const o = i * 6;
            lp[o] = hx; lp[o + 1] = hy; lp[o + 2] = hz;
            lp[o + 3] = this.wx(tx); lp[o + 4] = this.hAt(tx, ty) + lift; lp[o + 5] = this.wz(ty);
            lc[o] = r * fade; lc[o + 1] = g * fade; lc[o + 2] = b * fade; lc[o + 3] = 0; lc[o + 4] = 0; lc[o + 5] = 0;
            pp[i * 3] = hx; pp[i * 3 + 1] = hy; pp[i * 3 + 2] = hz;
            pc[i * 3] = r * fade; pc[i * 3 + 1] = g * fade; pc[i * 3 + 2] = b * fade;
        }
        this.flowLines.geometry.attributes.position.needsUpdate = true; this.flowLines.geometry.attributes.color.needsUpdate = true;
        this.flowPts.geometry.attributes.position.needsUpdate = true; this.flowPts.geometry.attributes.color.needsUpdate = true;
        this.flowPts.material.size = this.S.pSize;
    }

    rainRate() {
        const p = this.scene3d?.precip; if (!p?.mm_h) return 0;
        const i0 = Math.floor(this.t), f = this.t - i0, a = p.mm_h[i0] || 0, b = p.mm_h[Math.min(this.T - 1, i0 + 1)] || 0;
        return a + (b - a) * f;
    }

    stepRain(dt) {
        const pr = this.scene3d?.precip, on = this.S.rain && pr?.mm_h && pr.max_mm_h > 0;
        this.rainLines.visible = !!on;
        if (!on) return;
        const R = this.rN, mmh = this.rainRate(), frac = clamp01(mmh / pr.max_mm_h);
        const fall = Math.max(this.g.nx, this.g.ny) * 0.22;          // đơn vị / giây
        // số giọt sinh ra mỗi giây sao cho ~frac*R giọt đang rơi
        const lifeS = (this.rainTop) / fall;
        this.rainAcc += frac * R / Math.max(lifeS, 0.2) * dt;
        const cells = this.cellsOf, A = this.velCache.get(Math.floor(this.t));
        while (this.rainAcc >= 1) {
            this.rainAcc -= 1;
            const i = this.rNext; this.rNext = (this.rNext + 1) % R;
            const k = cells[(Math.random() * cells.length) | 0];
            this.rx[i] = (k % this.g.nx) + Math.random() - 0.5; this.ry[i] = ((k / this.g.nx) | 0) + Math.random() - 0.5;
            this.rh[i] = this.rainTop * (0.7 + Math.random() * 0.3); this.rOn[i] = 1;
        }
        const pos = this.rainLines.geometry.attributes.position.array, len = Math.max(this.g.nx, this.g.ny) * 0.008;
        for (let i = 0; i < R; i++) {
            const o = i * 6;
            if (!this.rOn[i]) { pos[o + 1] = pos[o + 4] = -1e5; continue; }
            this.rh[i] -= fall * dt;
            const ground = this.hAt(this.rx[i], this.ry[i]);
            if (this.rh[i] <= ground) {                            // chạm đất -> thành hạt dòng chảy
                this.rOn[i] = 0; pos[o + 1] = pos[o + 4] = -1e5;
                if (A && this.S.particles && this.pN) {
                    const j = this.pNext; this.pNext = (this.pNext + 1) % this.pN;
                    if (this.sampleUV(this.rx[i], this.ry[i], A, null, 0)) this.spawnFlow(j, A, this.rx[i], this.ry[i]);
                }
                continue;
            }
            const x = this.wx(this.rx[i]), z = this.wz(this.ry[i]);
            pos[o] = x; pos[o + 1] = this.rh[i]; pos[o + 2] = z; pos[o + 3] = x - len * 0.15; pos[o + 4] = this.rh[i] + len; pos[o + 5] = z;
        }
        this.rainLines.geometry.attributes.position.needsUpdate = true;
    }

    //  grid overlay
    paintOverlay() {
        const v = this.gridVar ? this.varCache.get(`${this.gridVar}:${Math.floor(this.t)}`) : null;
        this.overlay.visible = !!v;
        if (!v) return;
        const m = this.vars[this.gridVar], lo = Math.max(0, m.vmin ?? 0), hi = m.p99 || m.max || 1, useLog = !!m.log;
        const L = this.gridLut ||= lut('flow'), D = this.overlayData; D.fill(0);
        for (let j = 0; j < this.cellsOf.length; j++) {
            const x = v[j]; if (!Number.isFinite(x)) continue;
            const t = clamp01(useLog ? Math.log1p(Math.max(0, x - lo)) / (Math.log1p(hi - lo) || 1) : (x - lo) / ((hi - lo) || 1));
            if (t < 0.01) continue;
            const k = this.cellsOf[j], c = Math.round(t * 255) * 3, o = k * 4;
            D[o] = L[c] * 255; D[o + 1] = L[c + 1] * 255; D[o + 2] = L[c + 2] * 255; D[o + 3] = 90 + 165 * t;
        }
        this.overlayTex.needsUpdate = true; this.overlay.material.opacity = this.S.gridOpacity;
    }

    //  time
    applyTime(t) {
        this.t = Math.max(0, Math.min(this.T - 1, t));
        const i0 = Math.floor(this.t);
        if (!this.framesReady(i0)) {
            this.ensureFrames(i0).then(() => { if (Math.floor(this.t) === i0) this.updateValues(true); }).catch((e) => this.flash(e.message));
        } else this.updateValues(i0 !== this._lastI);
        if (i0 + 1 < this.T) this.ensureFrames(i0 + 1).catch(() => {});        // nạp trước bước kế tiếp
        const slider = this.$('slider'); if (slider && document.activeElement !== slider) slider.value = this.t;
        this.updateClock();
    }

    updateValues(stepChanged) {
        const i0 = Math.floor(this.t), f = this.t - i0, i1 = Math.min(this.T - 1, i0 + 1);
        if (this.riverVar) {
            const a = this.varCache.get(`${this.riverVar}:${i0}`), b = this.varCache.get(`${this.riverVar}:${i1}`);
            if (a) { const R = this.riverVals; for (let k = 0; k < R.length; k++) { const x = a[k], y = b ? b[k] : x; R[k] = Number.isFinite(y) && Number.isFinite(x) ? x + (y - x) * f : x; } }
            this.layoutRivers(); this.paintRivers();
        } else if (this.riverMesh) this.riverMesh.visible = false;
        if (stepChanged) { this.paintOverlay(); this._lastI = i0; }
    }

    updateClock() {
        const times = this.info.times, i0 = Math.floor(this.t), f = this.t - i0;
        const t0 = tparse(times[i0]), t1 = tparse(times[Math.min(this.T - 1, i0 + 1)]);
        const d = tfmt(t0 + (t1 - t0) * f);
        this.$('tlabel').textContent = `${d.slice(0, 10)} ${d.slice(11, 16)}`;
        const rain = this.rainRate();
        const outs = (this.scene3d?.outlets || []).slice(0, 3).map(o => {
            const a = o.series[i0], b = o.series[Math.min(this.T - 1, i0 + 1)];
            const q = Number.isFinite(a) ? (Number.isFinite(b) ? a + (b - a) * f : a) : null;
            return `Q${o.id} ${fmt(q)} m³/s`;
        });
        this.$('stats').innerHTML = [
            this.scene3d?.precip?.mm_h ? `<span class="w3d-rain">☂ ${fmt(rain, 1)} mm/h</span>` : '',
            ...outs.map(s => `<span>${esc(s)}</span>`),
        ].join('');
        this._hydroDirty = true;
    }

    setPlayingLocal(on) { this.playing = on; this.syncButtons(); }
    setPlaying() { /* thanh thời gian của dashboard bị ẩn ở chế độ 3D: dùng nút play riêng */ }

    async setTime(iso) {
        this.lastIso = iso;
        if (this.info && this.S.follow && iso) {
            const i = nearestIndex(this.info.times, iso);
            if (i >= 0) this.applyTime(i);
        }
        return this.info?.times?.[Math.floor(this.t)] || null;
    }

    //  hydrograph (Plotly)
    renderHydro() {
        const box = this.$('hydro'), el = this.$('plot');
        const outs = this.scene3d?.outlets || [];
        box.hidden = !this.S.hydro || (!outs.length && !this.extraSeries.length);
        const np = this.extraSeries.length;
        this.$('hint').textContent = np ? 'remove: click P✕ or a P pin'
            : this.S.pickPoints ? 'click a river in 3D to add a point' : '';
        this.root.querySelector('[data-a="clearpts"]').hidden = !np;
        if (box.hidden || !window.Plotly) return;
        const x = this.info.times, traces = [];
        const p = this.scene3d?.precip;
        if (p?.mm_h) traces.push({ x, y: p.mm_h, type: 'bar', name: 'Rain (mm/h)', yaxis: 'y2', marker: { color: 'rgba(96,165,250,0.55)' }, hovertemplate: '%{y:.1f} mm/h<extra>Rain</extra>' });
        outs.slice(0, 6).forEach((o, n) => traces.push({ x, y: o.series, type: 'scatter', mode: 'lines', name: `Q${o.id}${o.upstream_area_km2 ? ` · ${fmt(o.upstream_area_km2, 1)} km²` : ''}`,
            line: { color: OUTLET_COLORS[n % OUTLET_COLORS.length], width: 2 }, hovertemplate: '%{y:.2f} m³/s' }));
        this.extraSeries.forEach((s, n) => traces.push({ x, y: s.series, type: 'scatter', mode: 'lines', name: `P${n + 1} ✕`, meta: { point: n },
            line: { color: s.color || POINT_COLORS[n % POINT_COLORS.length], width: 1.8, dash: 'dot' }, hovertemplate: '%{y:.2f} m³/s' }));
        const pmax = p?.max_mm_h || 1;
        const layout = {
            margin: { l: 42, r: 34, t: 6, b: 26 }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
            font: { color: '#cbd5e1', size: 10 }, showlegend: true,
            legend: { orientation: 'h', y: -0.22, x: 0, font: { size: 9 } },
            xaxis: { gridcolor: 'rgba(148,163,184,0.12)', tickformat: '%d %b\n%H:%M' },
            yaxis: { title: { text: 'm³/s', font: { size: 10 } }, gridcolor: 'rgba(148,163,184,0.12)', rangemode: 'tozero' },
            yaxis2: { overlaying: 'y', side: 'right', autorange: false, range: [pmax * 3.2, 0], showgrid: false, title: { text: 'mm/h', font: { size: 10 } } },
            shapes: [this.cursorShape()], bargap: 0.05,
        };
        window.Plotly.react(el, traces, layout, { displayModeBar: false, responsive: true });
        if (!el._w3dLegend) {                          // bấm "Pn ✕" trong chú giải -> xoá điểm đó
            el._w3dLegend = true;
            el.on('plotly_legendclick', (ev) => {
                const n = ev.data?.[ev.curveNumber]?.meta?.point;
                if (n === undefined) return true;      // các đường khác: ẩn/hiện như mặc định
                this.removePoint(n); return false;
            });
            el.on('plotly_legenddoubleclick', (ev) => ev.data?.[ev.curveNumber]?.meta?.point === undefined);
        }
        this._hydroDirty = false;
    }
    cursorShape() {
        const times = this.info.times, i0 = Math.floor(this.t), f = this.t - i0;
        const t0 = tparse(times[i0]), t1 = tparse(times[Math.min(this.T - 1, i0 + 1)]);
        const x = tfmt(t0 + (t1 - t0) * f);
        return { type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x, y0: 0, y1: 1, line: { color: '#f8fafc', width: 1, dash: 'dot' } };
    }
    updateHydroCursor() {
        if (!this._hydroDirty || this.$('hydro').hidden || !window.Plotly) return;
        const now = performance.now(); if (now - (this._hydroT || 0) < 160) return;
        this._hydroT = now; this._hydroDirty = false;
        window.Plotly.relayout(this.$('plot'), { shapes: [this.cursorShape()] });
    }

    //  hover / pick
    gridAt(e) {
        const rect = this.renderer.domElement.getBoundingClientRect();
        const m = new this.THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
        this.raycaster.setFromCamera(m, this.camera);
        const hit = this.raycaster.intersectObject(this.terrain, false)[0];
        if (!hit) return null;
        return { fx: hit.point.x + (this.g.nx - 1) / 2, fy: (this.g.ny - 1) / 2 - hit.point.z };
    }
    nearestRiver(fx, fy, radius = 3) {
        const g = this.g, cx = Math.round(fx), cy = Math.round(fy);
        let best = null, bd = Infinity;
        for (let dy = -radius; dy <= radius; dy++) for (let dx = -radius; dx <= radius; dx++) {
            const ids = this.riverAt.get((cy + dy) * g.nx + cx + dx); if (!ids) continue;
            const d = Math.hypot(dx, dy); if (d < bd) { bd = d; best = ids[0]; }
        }
        return best;
    }
    hover(e) {
        const tip = this.$('tip'), canvas = this.renderer.domElement, pin = this.pinAt(e);
        canvas.style.cursor = (pin !== null && pin !== undefined) ? 'pointer' : '';
        if (pin !== null && pin !== undefined) {
            const rect = this.root.getBoundingClientRect();
            tip.innerHTML = `<b>P${pin + 1}</b> · click to remove`; tip.hidden = false;
            tip.style.left = `${e.clientX - rect.left + 14}px`; tip.style.top = `${e.clientY - rect.top + 12}px`;
            return;
        }
        const p = this.gridAt(e);
        if (!p) { tip.hidden = true; return; }
        const g = this.g, ix = Math.round(p.fx), iy = Math.round(p.fy);
        if (ix < 0 || iy < 0 || ix >= g.nx || iy >= g.ny) { tip.hidden = true; return; }
        const k = iy * g.nx + ix, rows = [`<b>${fmt(this.elev[k], 0)} m</b> a.s.l.`];
        const r = this.riverVar ? this.nearestRiver(p.fx, p.fy, 1) : null;
        if (r !== null) rows.push(`${esc(this.vars[this.riverVar].description || this.riverVar)}: <b>${fmt(this.riverVals[r])}</b> ${esc(this.vars[this.riverVar].units || '')}`);
        if (this.gridVar && this.idxOf[k] >= 0) {
            const v = this.varCache.get(`${this.gridVar}:${Math.floor(this.t)}`)?.[this.idxOf[k]];
            rows.push(`${esc(this.vars[this.gridVar].description || this.gridVar)}: <b>${fmt(v)}</b> ${esc(this.vars[this.gridVar].units || '')}`);
        }
        if (!this.mask[k]) rows.push('<i>outside the Wflow domain</i>');
        const rect = this.root.getBoundingClientRect();
        tip.innerHTML = rows.join('<br>'); tip.hidden = false;
        tip.style.left = `${e.clientX - rect.left + 14}px`; tip.style.top = `${e.clientY - rect.top + 12}px`;
    }
    pinAt(e) {                                         // ghim P.. dưới con trỏ (nếu có)
        if (!this.extraSeries.length) return null;
        const rect = this.renderer.domElement.getBoundingClientRect();
        const m = new this.THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
        this.raycaster.setFromCamera(m, this.camera);
        const sprites = this.pointGroup.children.filter(o => o.isSprite);
        const hit = this.raycaster.intersectObjects(sprites, false)[0];
        return hit ? hit.object.userData.point : null;
    }
    async pick(e) {
        const pin = this.pinAt(e);
        if (pin !== null && pin !== undefined) { this.removePoint(pin); return; }      // bấm vào ghim P.. -> xoá
        if (!this.S.pickPoints) return;
        const p = this.gridAt(e); if (!p || !this.riverVar) return;
        const r = this.nearestRiver(p.fx, p.fy, 3);
        if (r === null) { this.flash('No river cell here – click closer to a river.'); return; }
        const same = this.extraSeries.findIndex(s => s.river_id === r && s.var === this.riverVar);
        if (same >= 0) { this.removePoint(same); return; }                             // bấm lại đúng ô sông -> xoá
        try {
            const s = await getJson(`/dashboard/wflow3d/series/${r}?${this.q(`&var=${encodeURIComponent(this.riverVar)}`)}`);
            if (this.extraSeries.length >= MAX_POINTS) {
                const old = this.extraSeries.shift(); this.disposePin(old._pin, this.pointGroup);
                this.flash(`At most ${MAX_POINTS} points: the oldest point was removed.`);
            }
            this.extraSeries.push(s);
            if (!this.S.hydro) { this.S.hydro = true; this.syncButtons(); }
            this.rebuildPoints(); this.renderHydro();
        } catch (err) { this.flash(err.message); }
    }

    flash(msg) {
        const n = this.$('note'); if (!n) return;
        n.textContent = msg; n.hidden = false; clearTimeout(this._flashT); this._flashT = setTimeout(() => { n.hidden = true; }, 5000);
    }

    //  legend (trong khung 3D)
    renderLegend() {
        const items = [];
        const bar = (title, css, lo, hi, note = '') => `<div class="w3d-lg"><div class="w3d-lg-t">${esc(title)}</div>
            <div class="w3d-lg-bar" style="background:${css}"></div><div class="w3d-lg-s"><span>${fmt(lo)}</span><span>${fmt(hi)}</span></div>${note ? `<div class="w3d-lg-n">${esc(note)}</div>` : ''}</div>`;
        const grad = (pal) => `linear-gradient(to right, ${Array.from({ length: 6 }, (_, i) => paletteColor(i / 5, pal)).join(',')})`;
        if (this.riverVar) { const m = this.vars[this.riverVar]; items.push(bar(`${m.description || this.riverVar} (${m.units || ''})`, grad('flow'), 0, m.p99 || m.max || 1, 'line width & colour · √ scale')); }
        if (this.gridVar) { const m = this.vars[this.gridVar]; items.push(bar(`${m.description || this.gridVar} (${m.units || ''})`, grad('flow'), Math.max(0, m.vmin ?? 0), m.p99 || m.max || 1, m.log ? 'log scale' : '')); }
        if (this.S.particles && this.hasVelocity && this.S.pColor === 'speed') items.push(bar('Surface flow speed (m/s)', `linear-gradient(to right, ${SPEED_STOPS.join(',')})`, 0, this.info.velocity.speed_max, 'particles · √ scale'));
        if (this.S.rain && this.scene3d?.precip?.mm_h) items.push(`<div class="w3d-lg"><div class="w3d-lg-t"><span class="w3d-rain-sw"></span>Rain drops ∝ basin-mean rainfall</div></div>`);
        if (this.S.exag !== 1) items.push(`<div class="w3d-lg-n">Vertical exaggeration ×${this.S.exag}</div>`);
        this.$('legend').innerHTML = items.join('');
    }

    //  loop
    resize() {
        if (!this.renderer || !this.root || this.root.hidden) return;
        const w = this.root.clientWidth, h = this.root.clientHeight;
        if (!w || !h) return;
        this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
        if (!this.$('hydro').hidden && window.Plotly) window.Plotly.Plots.resize(this.$('plot'));
    }
    resetView() {
        const g = this.g, span = Math.max(g.nx, g.ny), ymid = (this.yOf(this.zmax) + this.yOf(this.zmin)) / 2;
        this.orbit.set(new this.THREE.Vector3(0, ymid * 0.4, span * 0.04), span * 0.85, -0.3, 0.92);
    }
    loop = (now) => {
        if (!this.visible) { this.raf = null; return; }
        this.raf = requestAnimationFrame(this.loop);
        const dt = Math.min(0.05, ((now - (this._lastNow || now)) / 1000)); this._lastNow = now;
        if (this.playing && !this.S.follow) {
            const next = this.t + dt * this.S.speed;
            const i0 = Math.floor(next);
            if (this.framesReady(Math.min(this.T - 1, i0))) this.applyTime(next >= this.T - 1 ? 0 : next);
            else this.ensureFrames(Math.min(this.T - 1, i0)).catch(() => {});
        } else if (this.riverVar && this._lastI === undefined) this.updateValues(true);
        this.orbit.tick(dt);
        this.stepRain(dt);
        this.stepParticles(dt);
        this.updateHydroCursor();
        this.renderer.render(this.scene, this.camera);
    };

    //  show / hide / destroy
    show() {
        if (!this.root) return;
        // Nhớ khung nhìn bản đồ 2D trước khi ẩn (khôi phục khi tắt 3D)
        if (!this.visible && this.lmap) this._savedView = { center: this.lmap.getCenter(), zoom: this.lmap.getZoom() };
        this.host.classList.add('w3d-on');
        this.root.hidden = false; this.visible = true;
        this.resize(); this.renderLegend(); this.renderHydro();
        if (this.g && this.orbit) this.resetView();       // bật 3D -> camera bao trọn mô hình
        if (!this.raf) { this._lastNow = performance.now(); this.raf = requestAnimationFrame(this.loop); }
    }
    hide() {
        this.visible = false; this.playing = false; this.syncButtons();
        if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
        if (this.root) this.root.hidden = true;
        this.host.classList.remove('w3d-on');
        const saved = this._savedView; this._savedView = null;
        setTimeout(() => {                                  // bản đồ có kích thước lại -> trả về đúng khung nhìn cũ
            if (!this.lmap) return;
            this.lmap.invalidateSize({ pan: false });
            if (saved) this.lmap.setView(saved.center, saved.zoom, { animate: false });
        }, 40);
    }
    destroy() {
        this.hide(); this.ro?.disconnect();
        if (this.scene) this.scene.traverse((o) => { o.geometry?.dispose?.(); const m = o.material; if (m) { m.map?.dispose?.(); m.dispose?.(); } });
        this.renderer?.dispose(); this.renderer = null;
        this.root?.remove(); this.root = null;
        this.velCache.clear(); this.varCache.clear();
    }
    legend() { return []; }        // chú giải của bản đồ 2D bị ẩn; khung 3D có chú giải riêng

    status() {
        if (!this.info) return '';
        let alive = 0, drops = 0;
        for (let i = 0; i < (this.pN || 0); i++) if (this.pLife[i] > 0 && this.pAge[i] < this.pLife[i]) alive++;
        for (let i = 0; i < (this.rN || 0); i++) drops += this.rOn[i];
        return `${alive.toLocaleString()} flow particles · ${drops.toLocaleString()} rain drops · step ${Math.floor(this.t) + 1}/${this.T}`;
    }

    //  settings (bảng ⚙)
    renderSettings(el, ctx) {
        if (el._bound) { const n = el.cloneNode(false); n.classList.remove('busy'); el.replaceWith(n); el = n; }
        el._bound = true;
        const S = this.S, info = this.info;
        const opts = (kind) => [['', '- none -'], ...Object.entries(this.vars).filter(([, v]) => v.kind === kind)
            .map(([k, v]) => [k, `${v.description || k}${v.units ? ` (${v.units})` : ''}`])];
        const scen = (info.scenarios || []).map(s => typeof s === 'string' ? s : s.name);
        const onoff = (k) => ui.seg(k, [['1', 'On'], ['0', 'Off']], S[k] ? '1' : '0');
        const hasRain = !!this.scene3d?.precip?.mm_h;
        el.innerHTML =
            ui.section('Scenario', ui.row('Flow', ui.select('flow', scen.map(s => [s, s]), info.flow)),
                'Uses the same dashboard Wflow cache as the 2D "Wflow results" layer (built on first use).') +
            ui.section('Terrain',
                ui.row('Base map', ui.seg('base', [['satellite', 'Satellite'], ['relief', 'Relief'], ['plain', 'Plain']], S.base)) +
                ui.row('Vertical exaggeration', ui.range('exag', 1, 8, 0.5, S.exag, (v) => `×${v}`)) +
                ui.row('Dim outside catchment', onoff('dim')) +
                ui.row('Sub-basin outlines', onoff('basins')) +
                ui.row('Outlet labels', onoff('outlets'))) +
            ui.section('Water',
                ui.row('River lines', ui.select('river', opts('river'), this.riverVar || '')) +
                ui.row('River width ×', ui.range('riverWidth', 0.3, 3, 0.1, S.riverWidth, (v) => v.toFixed(1))) +
                ui.row('Map layer', ui.select('grid', opts('grid'), this.gridVar || '')) +
                ui.row('Layer opacity', ui.range('gridOpacity', 0.1, 1, 0.05, S.gridOpacity, (v) => v.toFixed(2)))) +
            ui.section('Rain', hasRain
                ? ui.row('Show', onoff('rain')) + ui.row('Max drops', ui.range('rainCount', 1000, 20000, 500, S.rainCount))
                : '', hasRain ? `Basin-mean rainfall from ${esc(this.scene3d.precip.source || '')}. Drops that land on a wet cell become flow particles.`
                : 'No precipitation found in output.nc or in the Wflow forcing file.') +
            (this.hasVelocity ? ui.section('Flow particles',
                ui.row('Show', onoff('particles')) +
                ui.row('Count', ui.range('pCount', 1000, 30000, 500, S.pCount)) +
                ui.row('Speed ×', ui.range('pSpeed', 0.2, 4, 0.1, S.pSpeed, (v) => v.toFixed(1))) +
                ui.row('Dot size', ui.range('pSize', 1, 6, 0.2, S.pSize, (v) => v.toFixed(1))) +
                ui.row('Colour', ui.select('pColor', [['speed', 'By speed'], ['cyan', 'Cyan'], ['white', 'White']], S.pColor)),
                'Velocity field = overland flow / river discharge of the Wflow run (velocity.npy).')
                : ui.section('Flow particles', '', 'No velocity field in this Wflow output.')) +
            ui.section('Animation',
                ui.row('Wflow steps / second', ui.range('speed', 0.25, 12, 0.25, S.speed, (v) => v.toFixed(2))) +
                ui.row('Follow dashboard time', onoff('follow')) +
                ui.row('Auto-rotate', onoff('rotate')) +
                ui.row('Hydrograph', onoff('hydro')) +
                ui.row('Click river adds point', onoff('pickPoints')),
                'The 3D view has its own time bar over the Wflow steps. "Follow dashboard time" jumps to the Wflow step nearest to the HYD/WAQ time instead.') +
            `<div class="ls-status"></div>`;
        const run = async (fn) => {
            el.classList.add('busy'); ctx.busy(true);
            try { await fn(); } catch (err) { ctx.toast(`Wflow 3D: ${err.message}`); }
            finally { el.classList.remove('busy'); ctx.busy(false); }
        };
        bindSettings(el, {
            fmt: { exag: (v) => `×${v}`, riverWidth: (v) => v.toFixed(1), gridOpacity: (v) => v.toFixed(2), pSpeed: (v) => v.toFixed(1), pSize: (v) => v.toFixed(1), speed: (v) => v.toFixed(2) },
            onSet: (k, v, live) => {
                const bool = ['dim', 'basins', 'outlets', 'rain', 'particles', 'follow', 'rotate', 'hydro', 'pickPoints'].includes(k);
                const val = bool ? v === '1' : v;
                if (k === 'flow') { run(async () => { const was = this.visible; this.destroy(); this.S.flow = v; await this.init({ flowName: v }); if (was) this.show(); this.renderSettings(el, ctx); }); return; }
                if (k === 'river') { S.river = v; this.riverVar = v || null; saveSettings(SETTINGS_KEY, S); run(async () => { await this.ensureFrames(Math.floor(this.t)); this.updateValues(true); this.renderLegend(); }); return; }
                if (k === 'grid') { S.grid = v; this.gridVar = v || null; saveSettings(SETTINGS_KEY, S); run(async () => { await this.ensureFrames(Math.floor(this.t)); this._lastI = undefined; this.updateValues(true); this.renderLegend(); }); return; }
                S[k] = val;
                if (!live) saveSettings(SETTINGS_KEY, S);
                if (k === 'exag') { this.updateHeights(); this.renderLegend(); }
                else if (k === 'riverWidth') this.layoutRivers();
                else if (k === 'gridOpacity') this.overlay.material.opacity = val;
                else if (k === 'base' || k === 'dim') { if (!live) this.applyBase(); }
                else if (k === 'basins') this.basinLines.visible = val;
                else if (k === 'outlets') this.outletGroup.visible = val;
                else if (k === 'rotate') { this.orbit.autoRotate = val; this.syncButtons(); }
                else if (k === 'hydro' || k === 'pickPoints') { this.renderHydro(); this.syncButtons(); }
                else if (k === 'pCount' || k === 'rainCount') { if (!live) this.buildParticles(); }
                else if (k === 'particles') { if (val) this.ensureFrames(Math.floor(this.t)); this.renderLegend(); }
                else if (k === 'pColor' || k === 'rain') this.renderLegend();
                else if (k === 'follow' && val && this.lastIso) { this.playing = false; this.syncButtons(); this.setTime(this.lastIso); }
            },
        });
    }
}