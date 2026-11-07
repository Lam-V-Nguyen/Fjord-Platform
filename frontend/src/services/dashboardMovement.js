// Lớp "Flow movement (HYD)" của dashboard.
// Thuật toán hạt và nền raster CHUYỂN NGUYÊN từ công cụ Visualization (moveManager.js) để hai nơi
// hiển thị giống nhau với cùng dữ liệu đầu vào:
//  - khung int16 (u, v, depth) mở rộng ra toàn lưới như expandFrame(); nodata = meta.nodata
//  - nội suy song tuyến 4 ô + nội suy tuyến tính giữa 2 khung (fa, fb, w), không kiểm tra ô tâm
//  - sinh hạt ở ô hợp lệ bất kỳ (12 lần thử), tuổi 40–130 khung (physical: 400–490)
//  - bước con ≤ nửa ô (res_m), chiều dài 1.5 px × speed × curve(|v|/vmax), tối đa 40 px;
//    chế độ "physical": quãng đường = |v| × thời gian mô phỏng (rate h/s)
//  - "Freeze when paused": khi dừng chỉ chạy 30 bước "snapshot" rồi đứng yên
//  - nền raster speed (đã qua curve) / depth, alpha 190, làm mịn – như buildRaster/drawRaster
//  - mặc định giống visualization.html: 5000 hạt, ×1, vệt 0.93, √, hạt trắng, nền Velocity
// Dữ liệu: cache RIÊNG của dashboard (output/movement_dashboard), dựng bằng cùng
// movement_functions.init_data, cùng file HYD map và cùng quy ước lớp như Visualization.
// Không dùng chung mã/canvas/cài đặt với lớp Wflow.

import { PALETTES } from './dashboardMap.js';
import { ui, bindSettings, loadSettings, saveSettings, readInput } from './dashboardLayerSettings.js';

const L = window.L;
const SETTINGS_KEY = 'dash.movement.settings.v2';
const DEFAULTS = {                                    // = giá trị mặc định trong visualization.html
    count: 5000, pspeed: 1, trail: 0.93, curve: 'sqrt', pcolor: 'white', motion: 'always',
    raster: 'speed', rasterOpacity: 1, rate: 3,
};
const LOG_K = 100, MAX_STEP_PX = 40, BUCKETS = 6, SNAPSHOT = 30;
const RAMPS = {                                       // = RAMPS trong moveManager.js
    speed: ['#0b2f4f', '#1b5f8a', '#2a9a9a', '#79c26a', '#e6d34e', '#f08a2b'],
    depth: ['#d7eef5', '#9fd0e3', '#5fa8cf', '#2f7bb6', '#16509a', '#0b2a6b'],
    particle: ['#e8f6ff', '#bfe9ff', '#d9f59a', '#ffe066', '#ffab40', '#ff6b3d'],
};
PALETTES.hyd_speed = RAMPS.speed; PALETTES.hyd_depth = RAMPS.depth; PALETTES.hyd_particle = RAMPS.particle;
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
function makeLUT(stops) {
    const c = stops.map(hex), lut = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i++) {
        const t = (i / 255) * (c.length - 1), k = Math.min(Math.floor(t), c.length - 2), f = t - k;
        for (let ch = 0; ch < 3; ch++) lut[i * 3 + ch] = c[k][ch] + (c[k + 1][ch] - c[k][ch]) * f;
    }
    return lut;
}
const LUT = { speed: makeLUT(RAMPS.speed), depth: makeLUT(RAMPS.depth) };
const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));

async function post(path, body) {
    const res = await fetch(`/dashboard/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status === 'error') throw new Error(data.message || `HTTP ${res.status}`);
    return data;
}

async function getBin(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`${url.split('?')[0]}: HTTP ${res.status}`);
    return res.arrayBuffer();
}

// "-1"/"Average" -> mean (quy ước giao diện: k = thứ tự từ mặt xuống)
export function normLayer(layer) {
    const s = String(layer ?? '').trim().toLowerCase();
    if (['', '-1', 'mean', 'average', 'avg'].includes(s)) return 'mean';
    if (s === 'surface' || s === 'top') return 'surface';
    return String(parseInt(s, 10));
}

export function nearestIndex(times, iso) {
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

const parseTime = (s) => Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`);

export class MovementLayer {
    constructor(map, { projectName }) {
        this.map = map; this.projectName = projectName;
        this.S = loadSettings(SETTINGS_KEY, DEFAULTS);
        this.meta = null; this.valid = null; this.N = 0; this.ND = -32768; this.tms = [];
        this.frames = new Map(); this.pending = new Map();
        this.t = 0; this.lastIso = null; this.playing = false; this.visible = false;
        this.rasterCv = this.partCv = null; this.rctx = this.pctx = null;
        this.off = document.createElement('canvas'); this.offCtx = null; this.offImg = null;
        this.P = { ax: 0, bx: 0, ay: 0, by: 0, dpr: 1, mPerPx: 1 };
        this.buckets = Array.from({ length: BUCKETS }, () => []);
        this.out = [0, 0]; this.moving = false; this.snapshot = 0; this.rasterKey = ''; this.lastRaster = 0;
        this.last = performance.now(); this.raf = 0; this.drawn = 0; this.probeEl = null;
        this.onMoveStart = () => { this.moving = true; this.clear(); };
        this.onMove = () => { this.project(); this.drawRaster(); };
        this.onZoomStart = () => { this.rasterCv?.classList.add('hidden'); };
        this.onView = () => {
            this.project(); this.drawRaster(); this.rasterCv?.classList.remove('hidden');
            this.moving = false; this.clear(); this.snapshot = SNAPSHOT;
        };
        this.onMouseMove = (e) => this.probe(e.latlng);
    }

    // ------------------------------------------------------------ data
    // opts: {res, layer, rebuild} – chỉ gửi khi người dùng đổi; mặc định dùng cache đang có
    async init(opts = {}) {
        const res = await post('movement/init', { projectName: this.projectName, ...opts });
        if (res.status !== 'ok') throw new Error(res.message || 'Movement data not available');
        const m = this.meta = { ...res.meta, frame_count: res.frame_count };
        this.nLayers = res.n_layers || 1; this.canRebuild = res.can_rebuild !== false;
        this.layerLabels = res.layers || []; this.lastRebuild = res.rebuilt ? res.reason : null;
        this.N = m.nx * m.ny; this.ND = m.nodata; this.tms = m.times.map(parseTime);
        const mask = new Uint8Array(await getBin(`/dashboard/movement/mask?projectName=${encodeURIComponent(this.projectName)}&v=${encodeURIComponent(m.version || '')}`));
        if (mask.length !== this.N) throw new Error('Movement mask size mismatch – rebuild movement data');
        const v = []; mask.forEach((x, k) => { if (x) v.push(k); }); this.valid = Uint32Array.from(v);
        if (m.n_valid && this.valid.length !== m.n_valid) throw new Error('mask.npy and meta.json do not match – rebuild movement data');
        this.off.width = m.nx; this.off.height = m.ny;
        this.offCtx = this.off.getContext('2d'); this.offImg = this.offCtx.createImageData(m.nx, m.ny);
        this.frames.clear(); this.pending.clear(); this.rasterKey = '';
        this.alloc();
        if (this.visible) { this.project(); this.clear(); this.snapshot = SNAPSHOT; }
        return this;
    }

    // Khung gọn (3 × Nv) -> toàn lưới (3 × N), như expandFrame() của moveManager.js
    expand(compact) {
        const N = this.N, V = this.valid, nv = V.length, ND = this.ND;
        if (compact.length === 3 * N) return compact;
        if (compact.length !== 3 * nv) throw new Error(`Frame size ${compact.length} ≠ 3 × ${nv}`);
        const full = new Int16Array(3 * N).fill(ND);
        for (let c = 0; c < 3; c++) {
            const o = c * N, oc = c * nv;
            for (let j = 0; j < nv; j++) full[o + V[j]] = compact[oc + j];
        }
        return full;
    }

    loadFrame(i) {
        const T = this.tms.length;
        i = Math.max(0, Math.min(T - 1, i | 0));
        if (this.frames.has(i)) return Promise.resolve(this.frames.get(i));
        if (this.pending.has(i)) return this.pending.get(i);
        const p = getBin(`/dashboard/movement/frame/${i}?projectName=${encodeURIComponent(this.projectName)}&v=${encodeURIComponent(this.meta.version || '')}`)
            .then((buf) => {
                const arr = this.expand(new Int16Array(buf));
                this.frames.set(i, arr);
                if (this.frames.size > 60) {
                    const keep = Math.floor(this.t);
                    for (const k of this.frames.keys()) if (Math.abs(k - keep) > 30) this.frames.delete(k);
                }
                this.error = null;
                return arr;
            })
            .catch((e) => { this.error = e.message; throw e; })
            .finally(() => this.pending.delete(i));
        this.pending.set(i, p);
        return p;
    }

    // Thanh thời gian dashboard -> chỉ số thực S.t (nội suy giữa 2 khung như Visualization)
    async setTime(iso) {
        this.lastIso = iso;
        if (!this.meta) return null;
        const T = this.tms.length, t = Date.parse(iso || '');
        let tt;
        if (!Number.isFinite(t) || t >= this.tms[T - 1]) tt = T - 1;
        else if (t <= this.tms[0]) tt = 0;
        else {
            let i = 0;
            while (i < T - 2 && this.tms[i + 1] <= t) i++;
            tt = i + (t - this.tms[i]) / Math.max(1, this.tms[i + 1] - this.tms[i]);
        }
        if (tt !== this.t) {
            this.t = tt;
            if (!this.playing) {                              // như seek() của moveManager.js
                this.clear(); this.snapshot = SNAPSHOT;
                if (this.S.motion !== 'always' && this.age) this.age.fill(0);
            }
        }
        const i0 = Math.floor(this.t);
        await Promise.all([this.loadFrame(i0), this.loadFrame(Math.min(i0 + 1, T - 1))]).catch(() => {});
        for (let k = 2; k < 6; k++) this.loadFrame(Math.min(i0 + k, T - 1)).catch(() => {});
        return this.meta.times[Math.round(this.t)];
    }

    setPlaying(on) {
        this.playing = !!on;
        if (!on) this.snapshot = SNAPSHOT;
        this.last = performance.now();
    }

    curveF(x) {
        x = Math.max(0, Math.min(x, 1.5));
        if (this.S.curve === 'linear') return x;
        if (this.S.curve === 'log') return Math.log1p(LOG_K * x) / Math.log1p(LOG_K);
        return Math.sqrt(x);
    }

    // Menu Layer: Depth-mean, Surface + các lớp theo độ sâu (output/config/layers_hyd.json)
    layerOptions() {
        const sigma = this.layerLabels.length ? this.layerLabels
            : Array.from({ length: this.nLayers }, (_, k) => [String(k), `Layer ${k}${k === 0 ? ' (surface)' : k === this.nLayers - 1 ? ' (bottom)' : ''}`]);
        return [['mean', 'Depth-mean'], ['surface', 'Surface'], ...sigma];
    }

    layerLabel(layer) {
        const k = normLayer(layer);
        if (k === 'mean') return 'depth-mean';
        if (k === 'surface') return 'surface';
        return this.layerOptions().find(([v]) => v === k)?.[1] || `layer ${k}`;
    }

    status() {
        if (!this.meta) return 'Not initialised';
        if (!this.visible) return 'Particles hidden';
        if (this.error) return `Frame error: ${this.error}`;
        const i0 = Math.floor(this.t);
        if (!this.frames.has(i0)) return 'Waiting for frame…';
        if (this.S.motion === 'playing' && !this.playing) return `Frozen (motion = freeze when paused) · frame ${Math.round(this.t) + 1}/${this.tms.length}`;
        return `${this.drawn.toLocaleString()} / ${this.px.length.toLocaleString()} particles · frame ${Math.round(this.t) + 1}/${this.tms.length}`;
    }

    legend() {
        const m = this.meta || {}, S = this.S;
        const when = `HYD ${(m.times?.[Math.round(this.t)] || '').replace('T', ' ').slice(0, 16)}`;
        const note = [when, this.layerLabel(m.layer), m.res_m ? `${+Number(m.res_m).toFixed(1)} m${m.res_auto ? ' (auto)' : ''}` : ''].filter(Boolean).join(' · ');
        const out = [];
        if (S.raster === 'speed') out.push({ title: `Flow movement · velocity (m/s)${S.curve !== 'linear' ? ` · ${S.curve} scale` : ''}`, palette: 'hyd_speed', min: 0, max: m.speed_max || 0.1, note });
        else if (S.raster === 'depth') out.push({ title: 'Flow movement · water depth (m)', palette: 'hyd_depth', min: 0, max: m.depth_max || 1, note });
        if (S.pcolor === 'speed') out.push({ title: 'Particles · speed (m/s)', palette: 'hyd_particle', min: 0, max: m.speed_max || 0.1, note: S.raster === 'none' ? note : '' });
        if (!out.length) out.push({ title: 'Flow movement (HYD)', palette: 'hyd_particle', min: 0, max: m.speed_max || 0.1, note });
        return out;
    }

    // ------------------------------------------------------------ view
    show() {
        if (this.visible) return;
        this.visible = true;
        const mk = (name, z, cls) => {
            const pane = this.map.getPane(name) || this.map.createPane(name);
            pane.style.zIndex = z; pane.style.pointerEvents = 'none';
            const c = L.DomUtil.create('canvas', `flow-canvas ${cls}`, pane);
            c.style.position = 'absolute';
            return c;
        };
        this.rasterCv = mk('movementRaster', 404, 'movement-raster'); this.partCv = mk('movement', 410, 'movement-canvas');
        this.rctx = this.rasterCv.getContext('2d'); this.pctx = this.partCv.getContext('2d');
        this.applyRasterOpacity();
        this.map.on('movestart zoomstart', this.onMoveStart);
        this.map.on('zoomstart', this.onZoomStart);
        this.map.on('move', this.onMove);
        this.map.on('moveend zoomend resize viewreset', this.onView);
        this.map.on('mousemove', this.onMouseMove);
        this._ro = new ResizeObserver(() => {
            const el = this.map.getContainer(), s = this.map.getSize();
            if (el.clientWidth && el.clientHeight && (s.x !== el.clientWidth || s.y !== el.clientHeight)) this.map.invalidateSize({ pan: false });
        });
        this._ro.observe(this.map.getContainer());
        this.project(); this.rasterKey = ''; this.snapshot = SNAPSHOT; this.last = performance.now();
        const loop = (now) => { this.frame(now); this.raf = requestAnimationFrame(loop); };
        this.raf = requestAnimationFrame(loop);
    }

    hide() {
        this.visible = false;
        cancelAnimationFrame(this.raf);
        this.map.off('movestart zoomstart', this.onMoveStart);
        this.map.off('zoomstart', this.onZoomStart);
        this.map.off('move', this.onMove);
        this.map.off('moveend zoomend resize viewreset', this.onView);
        this.map.off('mousemove', this.onMouseMove);
        this._ro?.disconnect(); this._ro = null;
        for (const c of [this.rasterCv, this.partCv]) if (c) L.DomUtil.remove(c);
        this.rasterCv = this.partCv = this.rctx = this.pctx = null;
    }

    destroy() { this.hide(); this.frames.clear(); this.pending.clear(); }

    clear() { if (this.pctx) this.pctx.clearRect(0, 0, this.partCv.width, this.partCv.height); }

    applyRasterOpacity() { if (this.rasterCv) this.rasterCv.style.opacity = String(this.S.rasterOpacity ?? 1); }

    alloc() {
        const n = Math.max(100, Math.min(20000, this.S.count | 0));
        this.px = new Float32Array(n); this.py = new Float32Array(n);
        this.age = new Uint16Array(n); this.life = new Uint16Array(n);
    }

    // = updateProjection() của moveManager.js
    project() {
        if (!this.rasterCv || !this.meta) return;
        const m = this.meta, sz = this.map.getSize(), dpr = window.devicePixelRatio || 1, P = this.P;
        if (!sz.x || !sz.y) return;
        const topLeft = this.map.containerPointToLayerPoint([0, 0]);
        for (const c of [this.rasterCv, this.partCv]) {
            L.DomUtil.setPosition(c, topLeft);
            c.width = sz.x * dpr; c.height = sz.y * dpr;
            c.style.width = `${sz.x}px`; c.style.height = `${sz.y}px`;
        }
        const [[s, w], [n, e]] = m.bounds;
        const a = this.map.latLngToContainerPoint([s, w]), b = this.map.latLngToContainerPoint([n, e]);
        P.dpr = dpr; P.ax = ((b.x - a.x) / (e - w)) * dpr; P.bx = a.x * dpr - P.ax * w;
        P.ay = ((b.y - a.y) / (merc(n) - merc(s))) * dpr; P.by = a.y * dpr - P.ay * merc(s);
        P.mPerPx = ((1 / P.ax) * dpr) * 111320 * Math.cos((((s + n) / 2) * Math.PI) / 180);
    }

    gx2x(gx) { return this.P.ax * (this.meta.lon0 + gx * this.meta.dlon) + this.P.bx; }
    gy2y(gy) { return this.P.ay * merc(this.meta.lat0 + gy * this.meta.dlat) + this.P.by; }

    // ------------------------------------------------------------ vòng lặp (= frame() của moveManager.js)
    frame(now) {
        const dtReal = Math.min(0.1, (now - this.last) / 1000); this.last = now;
        if (!this.meta || !this.pctx || document.hidden) return;
        const T = this.tms.length, i0 = Math.floor(this.t), i1 = Math.min(i0 + 1, T - 1);
        const fa = this.frames.get(i0), fb = this.frames.get(i1);
        if (!fa || !fb) { this.loadFrame(i0).catch(() => {}); this.loadFrame(i1).catch(() => {}); return; }
        if (!this.P.ax) this.project();
        const w = this.t - i0, S = this.S;
        if (!this.moving) {
            if (this.playing || S.motion === 'always') {
                const dtSim = S.motion === 'physical' && this.playing ? S.rate * 3600 * dtReal : null;
                this.step(fa, fb, w, dtSim); this.drawParticles();
            } else if (this.snapshot > 0) {
                for (let k = 0; k < this.snapshot; k++) { this.step(fa, fb, w, null); this.drawParticles(); }
                this.snapshot = 0;
            }
        }
        const key = `${S.raster}|${S.curve}|${this.t.toFixed(2)}`;
        if (S.raster !== 'none' && key !== this.rasterKey && now - this.lastRaster > 120) {
            this.buildRaster(fa, fb, w); this.drawRaster(); this.rasterKey = key; this.lastRaster = now;
        }
    }

    sample(fa, fb, w, gx, gy) {
        const { nx, ny } = this.meta, N = this.N, ND = this.ND, out = this.out;
        const i0 = Math.floor(gx), j0 = Math.floor(gy), fx = gx - i0, fy = gy - j0;
        let su = 0, sv = 0, sw = 0;
        for (let c = 0; c < 4; c++) {
            const i = i0 + (c & 1), j = j0 + (c >> 1);
            if (i < 0 || j < 0 || i >= nx || j >= ny) continue;
            const k = j * nx + i, ua = fa[k], ub = fb[k];
            const wa = ua === ND ? 0 : 1 - w, wb = ub === ND ? 0 : w;
            if (wa + wb < 0.5) continue;
            const ws = ((c & 1) ? fx : 1 - fx) * ((c >> 1) ? fy : 1 - fy);
            const u = (wa * ua + wb * ub) / (wa + wb), v = (wa * fa[N + k] + wb * fb[N + k]) / (wa + wb);
            su += ws * u; sv += ws * v; sw += ws;
        }
        if (sw < 0.15) return null;
        out[0] = (su / sw) * this.meta.scale.u; out[1] = (sv / sw) * this.meta.scale.v;
        return out;
    }

    respawn(i, fa, fb, w) {
        const nx = this.meta.nx, V = this.valid;
        for (let tries = 0; tries < 12; tries++) {
            const k = V[(Math.random() * V.length) | 0];
            const gx = (k % nx) + Math.random() - 0.5, gy = ((k / nx) | 0) + Math.random() - 0.5;
            if (this.sample(fa, fb, w, gx, gy)) {
                this.px[i] = gx; this.py[i] = gy; this.age[i] = 1;
                this.life[i] = (this.S.motion === 'physical' ? 400 : 40) + ((Math.random() * 90) | 0);
                return true;
            }
        }
        this.age[i] = 0; return false;
    }

    step(fa, fb, w, dtSim) {
        const n = this.px.length, vmax = this.meta.speed_max, res = this.meta.res_m, P = this.P;
        const physical = dtSim != null;
        const pxLen = 1.5 * this.S.pspeed * P.mPerPx, maxStep = MAX_STEP_PX * P.mPerPx;
        const tSim = physical ? dtSim : 0;
        for (const b of this.buckets) b.length = 0;
        for (let i = 0; i < n; i++) {
            if (this.age[i] === 0 || this.age[i] > this.life[i]) { this.respawn(i, fa, fb, w); continue; }
            const first = this.sample(fa, fb, w, this.px[i], this.py[i]);
            if (!first) { this.age[i] = 0; continue; }
            let u = first[0], v = first[1], s = Math.hypot(u, v);
            const s0 = s;
            let Ltot = physical ? s * tSim : pxLen * this.curveF(s / vmax);
            Ltot = Math.min(Ltot, maxStep);
            const nsub = Math.max(1, Math.min(60, Math.ceil(Ltot / (0.5 * res))));
            const x0 = this.gx2x(this.px[i]), y0 = this.gy2y(this.py[i]);
            let gx = this.px[i], gy = this.py[i], dead = false;
            for (let k = 0; k < nsub; k++) {
                if (k > 0) {
                    const vel = this.sample(fa, fb, w, gx, gy);
                    if (!vel) { dead = true; break; }
                    u = vel[0]; v = vel[1]; s = Math.hypot(u, v);
                }
                if (s < 1e-9) break;
                const l = physical ? Math.min(s * tSim, maxStep) / nsub
                                   : Math.min(pxLen * this.curveF(s / vmax), maxStep) / nsub;
                gx += (u / s) * l / res; gy += (v / s) * l / res;
            }
            if (dead || !this.sample(fa, fb, w, gx, gy)) { this.age[i] = 0; continue; }
            this.px[i] = gx; this.py[i] = gy; this.age[i]++;
            const b = Math.min(BUCKETS - 1, (this.curveF(s0 / vmax) * BUCKETS) | 0);
            this.buckets[b].push(x0, y0, this.gx2x(gx), this.gy2y(gy));
        }
    }

    drawParticles() {
        const ctx = this.pctx, W = this.partCv.width, H = this.partCv.height;
        ctx.globalCompositeOperation = 'destination-in';
        ctx.fillStyle = `rgba(0,0,0,${this.S.trail})`;
        ctx.fillRect(0, 0, W, H);
        ctx.globalCompositeOperation = 'source-over';
        ctx.lineWidth = 1.3 * this.P.dpr; ctx.lineCap = 'round';
        let drawn = 0;
        for (let b = 0; b < BUCKETS; b++) {
            const seg = this.buckets[b];
            if (!seg.length) continue;
            drawn += seg.length / 4;
            ctx.strokeStyle = this.S.pcolor === 'white'
                ? `rgba(255,255,255,${0.55 + 0.45 * (b / (BUCKETS - 1))})` : RAMPS.particle[b];
            ctx.beginPath();
            for (let k = 0; k < seg.length; k += 4) { ctx.moveTo(seg[k], seg[k + 1]); ctx.lineTo(seg[k + 2], seg[k + 3]); }
            ctx.stroke();
        }
        this.drawn = drawn;
    }

    // ------------------------------------------------------------ nền raster (= buildRaster/drawRaster)
    buildRaster(fa, fb, w) {
        const m = this.meta, { nx, ny } = m, N = this.N, ND = this.ND, d = this.offImg.data;
        const mode = this.S.raster, lut = LUT[mode];
        const vmax = mode === 'speed' ? m.speed_max : m.depth_max;
        const su = m.scale.u, sd = m.scale.depth;
        for (let j = 0; j < ny; j++) {
            const row = (ny - 1 - j) * nx;
            for (let i = 0; i < nx; i++) {
                const k = j * nx + i, o = (row + i) * 4;
                const a = fa[k], b = fb[k];
                if (a === ND && b === ND) { d[o + 3] = 0; continue; }
                const src = a === ND ? fb : b === ND ? fa : null;
                let val;
                if (mode === 'speed') {
                    const u = src ? src[k] : a * (1 - w) + b * w;
                    const v = src ? src[N + k] : fa[N + k] * (1 - w) + fb[N + k] * w;
                    val = Math.hypot(u, v) * su;
                } else {
                    val = (src ? src[2 * N + k] : fa[2 * N + k] * (1 - w) + fb[2 * N + k] * w) * sd;
                }
                const x = mode === 'speed' ? this.curveF(val / vmax) : val / vmax;
                const li = Math.max(0, Math.min(255, (x * 255) | 0)) * 3;
                d[o] = lut[li]; d[o + 1] = lut[li + 1]; d[o + 2] = lut[li + 2]; d[o + 3] = 190;
            }
        }
        this.offCtx.putImageData(this.offImg, 0, 0);
    }

    drawRaster() {
        if (!this.rasterCv || !this.meta) return;
        const rctx = this.rctx;
        rctx.clearRect(0, 0, this.rasterCv.width, this.rasterCv.height);
        if (this.S.raster === 'none') return;
        const { nx, ny } = this.meta, x0 = this.gx2x(-0.5), x1 = this.gx2x(nx - 0.5);
        rctx.imageSmoothingEnabled = true;
        for (let j = 0; j < ny; j += 8) {
            const h = Math.min(8, ny - j), yS = this.gy2y(j - 0.5), yN = this.gy2y(j + h - 0.5);
            rctx.drawImage(this.off, 0, ny - j - h, nx, h, x0, yN, x1 - x0, yS - yN);
        }
    }

    // ------------------------------------------------------------ đọc giá trị dưới con trỏ
    probe(latlng) {
        if (!this.probeEl || !this.probeEl.isConnected || !this.meta) return;
        const m = this.meta, f = this.frames.get(Math.round(this.t));
        const ix = Math.round((latlng.lng - m.lon0) / m.dlon), iy = Math.round((latlng.lat - m.lat0) / m.dlat);
        if (!f || ix < 0 || iy < 0 || ix >= m.nx || iy >= m.ny || f[iy * m.nx + ix] === this.ND) {
            this.probeEl.textContent = 'Hover the water to read values'; return;
        }
        const k = iy * m.nx + ix, u = f[k] * m.scale.u, v = f[this.N + k] * m.scale.v;
        const dir = (Math.atan2(u, v) * 180 / Math.PI + 360) % 360, d = f[2 * this.N + k] * m.scale.depth;
        this.probeEl.textContent = `Speed ${Math.hypot(u, v).toFixed(3)} m/s · to ${dir.toFixed(0)}° · depth ${d.toFixed(2)} m`;
    }

    // ------------------------------------------------------------ bảng cài đặt (giống bảng của Visualization)
    renderSettings(el, ctx) {
        if (el._bound) { const n = el.cloneNode(false); n.classList.remove('busy'); el.replaceWith(n); el = n; }   // vẽ lại -> bỏ listener cũ
        el._bound = true;
        const S = this.S, m = this.meta;
        el.innerHTML =
            ui.section('Data (dashboard only)',
                ui.row('Resolution (m)', ui.input('res', m.res_auto ? '' : m.res_m, `auto (${+Number(m.res_m).toFixed(1)} m)`, 'number')) +
                ui.row('Layer', ui.select('layer', this.layerOptions(), normLayer(m.layer))) +
                `<div class="ls-actions">${ui.button('apply', 'Apply')}${ui.button('rebuild', 'Force rebuild', 'ghost')}</div>`,
                `Same input and builder as the Visualization tool (movement_functions.init_data) in the dashboard's own cache <b>output/movement_dashboard</b>. ${this.canRebuild ? '' : '<br><b>No HYD map source – rebuild disabled.</b>'}`) +
            ui.section('Background',
                ui.row('Raster', ui.seg('raster', [['speed', 'Velocity'], ['depth', 'Depth'], ['none', 'Hide']], S.raster)) +
                ui.row('Raster opacity', ui.range('rasterOpacity', 0.1, 1, 0.05, S.rasterOpacity, (v) => v.toFixed(2)))) +
            ui.section('Particles',
                ui.row('Motion', ui.select('motion', [['always', 'Always moving'], ['playing', 'Freeze when paused'], ['physical', 'Real simulation time']], S.motion)) +
                `<div class="ls-row ls-rate" ${S.motion === 'physical' ? '' : 'hidden'}><span class="ls-label">Sim. rate</span>${ui.select('rate', [['0.5', '0.5 h/s'], ['1', '1 h/s'], ['3', '3 h/s'], ['6', '6 h/s'], ['12', '12 h/s'], ['24', '1 d/s']], String(S.rate))}</div>` +
                ui.row('Speed curve', ui.select('curve', [['linear', 'Linear'], ['sqrt', 'Square root – boost slow flow'], ['log', 'Logarithmic – strong boost']], S.curve)) +
                ui.row('Count', ui.range('count', 1000, 20000, 500, S.count)) +
                ui.row('Speed ×', ui.range('pspeed', 0.2, 4, 0.1, S.pspeed, (v) => v.toFixed(1))) +
                ui.row('Trail', ui.range('trail', 0.8, 0.985, 0.005, S.trail, (v) => v.toFixed(3))) +
                ui.row('Colour', ui.seg('pcolor', [['white', 'White'], ['speed', 'By speed']], S.pcolor)) +
                `<div class="ls-actions">${ui.button('reset', 'Reset to Visualization defaults', 'ghost')}</div>`) +
            `<div class="ls-probe">Hover the water to read values</div><div class="ls-status"></div>`;
        this.probeEl = el.querySelector('.ls-probe');
        const rebuild = async (force) => {
            const resTxt = readInput(el, 'res'), res = resTxt === '' ? 'auto' : Number(resTxt);
            if (res !== 'auto' && !(res > 0)) { ctx.toast('Resolution must be a positive number (or empty = auto)'); return; }
            const layer = el.querySelector('[data-k="layer"]').value;
            el.classList.add('busy'); ctx.busy(true);
            try {
                await this.init({ res, layer, rebuild: !!force });
                await this.setTime(this.lastIso);
                ctx.toast(this.lastRebuild ? `Movement data rebuilt: ${this.lastRebuild}` : 'Movement data unchanged (same settings)');
                ctx.refreshLegend(); this.renderSettings(el, ctx);
            } catch (err) { ctx.toast(`Flow movement: ${err.message}`); }
            finally { el.classList.remove('busy'); ctx.busy(false); }
        };
        bindSettings(el, {
            fmt: { rasterOpacity: (v) => v.toFixed(2), pspeed: (v) => v.toFixed(1), trail: (v) => v.toFixed(3) },
            onSet: (k, v, live) => {
                if (k === 'res' || k === 'layer') return;                   // chỉ áp dụng khi bấm Apply / Enter
                S[k] = k === 'rate' ? Number(v) : v;
                if (!live) saveSettings(SETTINGS_KEY, S);
                if (k === 'rasterOpacity') this.applyRasterOpacity();
                else if (k === 'count') { this.alloc(); this.clear(); this.snapshot = SNAPSHOT; }
                else if (k === 'raster') { this.rasterKey = ''; if (v === 'none') this.drawRaster(); ctx.refreshLegend(); }
                else if (k === 'pcolor') { this.snapshot = SNAPSHOT; ctx.refreshLegend(); }
                else if (k === 'curve') { this.rasterKey = ''; this.clear(); this.snapshot = SNAPSHOT; ctx.refreshLegend(); }
                else if (k === 'motion') {
                    el.querySelector('.ls-rate').hidden = v !== 'physical';
                    this.age?.fill(0); this.clear(); this.snapshot = SNAPSHOT;
                }
            },
            onAction: (act) => {
                if (act === 'apply' || act === 'enter:res') rebuild(false);
                else if (act === 'rebuild') rebuild(true);
                else if (act === 'reset') {
                    Object.assign(S, DEFAULTS); saveSettings(SETTINGS_KEY, S);
                    this.alloc(); this.clear(); this.applyRasterOpacity(); this.rasterKey = ''; this.snapshot = SNAPSHOT;
                    if (S.raster === 'none') this.drawRaster();
                    ctx.refreshLegend(); this.renderSettings(el, ctx);
                }
            },
        });
        if (!this.canRebuild) el.querySelectorAll('[data-act="apply"],[data-act="rebuild"]').forEach(b => { b.disabled = true; });
    }
}