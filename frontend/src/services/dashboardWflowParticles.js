// Hạt dòng chảy mặt cho lớp "Wflow results" – TÁCH RIÊNG khỏi hạt HYD (dashboardMovement.js).
// Thuật toán chuyển nguyên từ công cụ Flow Estimation (flowRunner.js) để cho kết quả giống hệt:
//  - vận tốc velocity.npy (float16, 2 × Nv: u đông, v bắc; NaN = khô) theo thứ tự ô hợp lệ của mask
//  - nội suy song tuyến 4 ô + nội suy tuyến tính giữa 2 bước thời gian (fa, fb, w)
//  - chặn hạt trong vùng nước (mask = 2) khi "Stop in water" bật, sinh hạt ở ô ướt trên đất
//  - bước con ≤ nửa ô (res_m), chiều dài ~1.5 px × speed × curve(|v|/vmax), tối đa 40 px
//  - canvas theo điểm ảnh thiết bị (dpr) như bản gốc; cắt theo đường viền lưu vực (basins) và xoá
//    vệt trên mặt nước (water area) khi "Stop in water" bật – như drawParticles() của flowRunner
// Lớp này không dùng chung trạng thái, canvas hay cài đặt với lớp Flow movement (HYD).

import { PALETTES } from './dashboardMap.js';

const L = window.L;
const M_PER_DEG = 111320, LOG_K = 100, MAX_STEP_PX = 40, PBUCKETS = 6, MAX_SUB = 60;
export const WFLOW_PARTICLE_RAMP = ['#e8f6ff', '#bfe9ff', '#d9f59a', '#ffe066', '#ffab40', '#ff6b3d'];
PALETTES.wflow_particle = WFLOW_PARTICLE_RAMP;          // để chú giải dùng đúng thang màu
const SOLID = { white: [255, 255, 255], black: [0, 0, 0], dark_blue: [12, 42, 107], blue: [0, 0, 255], cyan: [125, 211, 252] };
const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));

const F16 = (() => {
    const t = new Float32Array(65536);
    for (let h = 0; h < 65536; h++) {
        const s = h & 0x8000 ? -1 : 1, e = (h >> 10) & 0x1f, m = h & 0x3ff;
        t[h] = e === 0 ? s * m * 2 ** -24 : e === 31 ? (m ? NaN : s * Infinity) : s * (1 + m / 1024) * 2 ** (e - 15);
    }
    return t;
})();
function decodeF16(buf) {
    const h = new Uint16Array(buf), o = new Float32Array(h.length);
    for (let i = 0; i < h.length; i++) o[i] = F16[h[i]];
    return o;
}

export class WflowParticles {
    /**
     * @param map Leaflet map
     * @param S   cài đặt (đọc trực tiếp): on, count, pspeed, trail, curve, pcolor, opacity, water
     */
    constructor(map, { S }) {
        this.map = map; this.S = S;
        this.g = null; this.mask = null; this.valid = null; this.cellOf = null; this.vinfo = null;
        this.tms = []; this.t = 0; this.url = null;
        this.frames = new Map(); this.wet = new Map(); this.pending = new Map();
        this.canvas = null; this.ctx = null; this.raf = 0; this.visible = false; this.moving = false;
        this.P = { ax: 0, bx: 0, ay: 0, by: 0, dpr: 1, mPerPx: 1 };
        this.buckets = Array.from({ length: PBUCKETS }, () => []);
        this.vout = [0, 0]; this.drawn = 0; this.error = null;
        this.onView = () => { this.project(); this.clear(); this.moving = false; };
        this.onMoveStart = () => { this.moving = true; this.clear(); };
    }

    /**
     * grid: meta.grid của cache viewer; mask: Uint8Array nx·ny (0 ngoài, 1 đất, 2 nước);
     * velocity: meta.velocity; times: meta.times; url(i): địa chỉ GET khung vận tốc i
     */
    setup({ grid, mask, velocity, times, url, basinGeo = null, waterGeo = null }) {
        this.g = grid; this.mask = mask; this.vinfo = velocity; this.url = url;
        this.basinGeo = basinGeo; this.waterGeo = waterGeo; this.pathsDirty = true;
        this.tms = times.map((s) => Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`));
        const v = []; mask.forEach((m, k) => { if (m) v.push(k); });
        this.valid = Uint32Array.from(v);
        this.cellOf = new Int32Array(grid.nx * grid.ny).fill(-1);
        this.valid.forEach((k, j) => { this.cellOf[k] = j; });
        this.hasWater = (grid.n_water || 0) > 0;
        this.frames.clear(); this.wet.clear(); this.pending.clear(); this.error = null;
        this.alloc();
        if (this.visible) { this.project(); this.clear(); }
    }

    alloc() {
        const n = Math.max(100, Math.min(20000, this.S.count | 0));
        this.px = new Float32Array(n); this.py = new Float32Array(n);
        this.age = new Uint16Array(n); this.life = new Uint16Array(n);
    }

    curveF(x) {
        x = Math.max(0, Math.min(x, 1.5));
        if (this.S.curve === 'linear') return x;
        if (this.S.curve === 'log') return Math.log1p(LOG_K * x) / Math.log1p(LOG_K);
        return Math.sqrt(x);
    }

    get stopInWater() { return this.hasWater && this.S.water !== false; }

    // ------------------------------------------------------------ thời gian & dữ liệu
    // Thời điểm của thanh thời gian -> chỉ số thực (vd. 12.4 = giữa bước 12 và 13)
    setTime(iso) {
        const T = this.tms.length;
        if (!T) return;
        const t = Date.parse(iso || '');
        if (!Number.isFinite(t) || t >= this.tms[T - 1]) this.t = T - 1;
        else if (t <= this.tms[0]) this.t = 0;
        else {
            let i = 0;
            while (i < T - 2 && this.tms[i + 1] <= t) i++;
            this.t = i + (t - this.tms[i]) / Math.max(1, this.tms[i + 1] - this.tms[i]);
        }
        const i0 = Math.floor(this.t);
        for (let k = 0; k <= 2; k++) this.loadVel(Math.min(T - 1, i0 + k)).catch(() => {});
    }

    get index() { return Math.round(this.t); }

    loadVel(i) {
        i = Math.max(0, Math.min(this.tms.length - 1, i | 0));
        if (this.frames.has(i)) return Promise.resolve(this.frames.get(i));
        if (this.pending.has(i)) return this.pending.get(i);
        const Nv = this.valid.length;
        const p = fetch(this.url(i), { credentials: 'same-origin' })
            .then((r) => { if (!r.ok) throw new Error(`velocity ${i}: HTTP ${r.status}`); return r.arrayBuffer(); })
            .then((buf) => {
                const a = decodeF16(buf);
                if (a.length !== 2 * Nv) throw new Error(`velocity ${i}: ${a.length} values ≠ 2 × ${Nv} cells – rebuild the viewer data`);
                const all = [], land = [];
                for (let j = 0; j < Nv; j++) {
                    if (Number.isFinite(a[j]) && (a[j] !== 0 || a[Nv + j] !== 0)) {
                        all.push(j);
                        if (this.mask[this.valid[j]] !== 2) land.push(j);
                    }
                }
                this.frames.set(i, a);
                this.wet.set(i, { all: Uint32Array.from(all), land: Uint32Array.from(land) });
                if (this.frames.size > 48) {
                    const keep = Math.floor(this.t);
                    for (const k of this.frames.keys()) if (Math.abs(k - keep) > 24) { this.frames.delete(k); this.wet.delete(k); }
                }
                this.error = null;
                return a;
            })
            .catch((e) => { this.error = e.message; throw e; })
            .finally(() => this.pending.delete(i));
        this.pending.set(i, p);
        return p;
    }

    // ------------------------------------------------------------ view
    show() {
        if (this.visible) return;
        this.visible = true;
        if (!this.map.getPane('wflowParticles')) {
            const p = this.map.createPane('wflowParticles'); p.style.zIndex = 408; p.style.pointerEvents = 'none';
        }
        this.canvas = L.DomUtil.create('canvas', 'wv-canvas', this.map.getPane('wflowParticles'));
        this.canvas.style.position = 'absolute';
        this.ctx = this.canvas.getContext('2d');
        this.applyOpacity();
        this.map.on('movestart zoomstart', this.onMoveStart);
        this.map.on('moveend zoomend resize viewreset', this.onView);
        this._ro = new ResizeObserver(() => {
            const el = this.map.getContainer(), s = this.map.getSize();
            if (el.clientWidth && el.clientHeight && (s.x !== el.clientWidth || s.y !== el.clientHeight)) this.map.invalidateSize({ pan: false });
        });
        this._ro.observe(this.map.getContainer());
        this.project();
        const loop = () => { this.tick(); this.raf = requestAnimationFrame(loop); };
        this.raf = requestAnimationFrame(loop);
    }

    hide() {
        this.visible = false;
        cancelAnimationFrame(this.raf);
        this.map.off('movestart zoomstart', this.onMoveStart);
        this.map.off('moveend zoomend resize viewreset', this.onView);
        this._ro?.disconnect(); this._ro = null;
        if (this.canvas) L.DomUtil.remove(this.canvas);
        this.canvas = this.ctx = null;
    }

    destroy() { this.hide(); this.frames.clear(); this.wet.clear(); this.pending.clear(); }

    applyOpacity() { if (this.canvas) this.canvas.style.opacity = String(this.S.opacity ?? 1); }

    clear() { if (this.ctx) this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); }

    reset() { if (this.age) this.age.fill(0); this.clear(); }

    // Như project() của flowRunner: toạ độ theo điểm ảnh thiết bị
    project() {
        if (!this.canvas || !this.g) return;
        const sz = this.map.getSize(), dpr = window.devicePixelRatio || 1, P = this.P;
        if (!sz.x || !sz.y) return;
        L.DomUtil.setPosition(this.canvas, this.map.containerPointToLayerPoint([0, 0]));
        this.canvas.width = sz.x * dpr; this.canvas.height = sz.y * dpr;
        this.canvas.style.width = `${sz.x}px`; this.canvas.style.height = `${sz.y}px`;
        const [[s, w], [n, e]] = this.g.bounds;
        const a = this.map.latLngToContainerPoint([s, w]), b = this.map.latLngToContainerPoint([n, e]);
        P.dpr = dpr; P.ax = ((b.x - a.x) / (e - w)) * dpr; P.bx = a.x * dpr - P.ax * w;
        P.ay = ((b.y - a.y) / (merc(n) - merc(s))) * dpr; P.by = a.y * dpr - P.ay * merc(s);
        P.mPerPx = (M_PER_DEG * Math.cos(((s + n) / 2) * Math.PI / 180)) / Math.max(P.ax, 1e-9);
        this.pathsDirty = true;
    }

    // = buildGeoPath() của flowRunner.js (toạ độ điểm ảnh thiết bị)
    buildGeoPath(gj) {
        const path = new Path2D(), P = this.P;
        const X = (lon) => P.ax * lon + P.bx, Y = (lat) => P.ay * merc(lat) + P.by;
        for (const f of (gj && gj.features) || []) {
            const gm = f.geometry; if (!gm) continue;
            const polys = gm.type === 'Polygon' ? [gm.coordinates] : gm.type === 'MultiPolygon' ? gm.coordinates : [];
            for (const poly of polys) for (const ring of poly) {
                ring.forEach(([lon, lat], i) => (i ? path.lineTo(X(lon), Y(lat)) : path.moveTo(X(lon), Y(lat))));
                path.closePath();
            }
        }
        return path;
    }

    gx2x(gx) { return this.P.ax * (this.g.lon0 + gx * this.g.dlon) + this.P.bx; }
    gy2y(gy) { return this.P.ay * merc(this.g.lat0 + gy * this.g.dlat) + this.P.by; }

    // ------------------------------------------------------------ thuật toán (giống flowRunner.js)
    blocked(k) { return this.cellOf[k] < 0 || (this.stopInWater && this.mask[k] === 2); }

    sampleVel(fa, fb, w, gx, gy) {
        const g = this.g, Nv = this.valid.length, cellOf = this.cellOf;
        const ic = Math.round(gx), jc = Math.round(gy);
        if (ic < 0 || jc < 0 || ic >= g.nx || jc >= g.ny || this.blocked(jc * g.nx + ic)) return null;
        const i0 = Math.floor(gx), j0 = Math.floor(gy), fx = gx - i0, fy = gy - j0;
        let su = 0, sv = 0, sw = 0;
        for (let c = 0; c < 4; c++) {
            const i = i0 + (c & 1), j = j0 + (c >> 1);
            if (i < 0 || j < 0 || i >= g.nx || j >= g.ny) continue;
            const kk = j * g.nx + i; if (this.blocked(kk)) continue;
            const q = cellOf[kk], ua = fa[q], ub = fb[q];
            const wa = Number.isFinite(ua) ? 1 - w : 0, wb = Number.isFinite(ub) ? w : 0;
            if (wa + wb < 0.5) continue;
            const ws = ((c & 1) ? fx : 1 - fx) * ((c >> 1) ? fy : 1 - fy);
            const u = ((wa ? wa * ua : 0) + (wb ? wb * ub : 0)) / (wa + wb);
            const v = ((wa ? wa * fa[Nv + q] : 0) + (wb ? wb * fb[Nv + q] : 0)) / (wa + wb);
            su += ws * u; sv += ws * v; sw += ws;
        }
        if (sw < 0.15) return null;
        this.vout[0] = su / sw; this.vout[1] = sv / sw;
        return this.vout;
    }

    respawn(k, fa, fb, w, wet) {
        if (!wet || !wet.length) { this.age[k] = 0; return; }
        const g = this.g;
        for (let tries = 0; tries < 8; tries++) {
            const q = this.valid[wet[(Math.random() * wet.length) | 0]];
            if (this.blocked(q)) continue;
            const gx = (q % g.nx) + Math.random() - 0.5, gy = ((q / g.nx) | 0) + Math.random() - 0.5;
            if (this.sampleVel(fa, fb, w, gx, gy)) {
                this.px[k] = gx; this.py[k] = gy; this.age[k] = 1; this.life[k] = 40 + ((Math.random() * 90) | 0);
                return;
            }
        }
        this.age[k] = 0;
    }

    step(fa, fb, w, wet) {
        const vmax = this.vinfo.speed_max, res = this.g.res_m, n = this.px.length, P = this.P;
        const pxLen = 1.5 * (this.S.pspeed || 1) * P.mPerPx, maxStep = MAX_STEP_PX * P.mPerPx;
        for (const b of this.buckets) b.length = 0;
        for (let k = 0; k < n; k++) {
            if (this.age[k] === 0 || this.age[k] > this.life[k]) { this.respawn(k, fa, fb, w, wet); continue; }
            const first = this.sampleVel(fa, fb, w, this.px[k], this.py[k]);
            if (!first) { this.age[k] = 0; continue; }
            let u = first[0], v = first[1], sp = Math.hypot(u, v);
            const s0 = sp, Ltot = Math.min(pxLen * this.curveF(sp / vmax), maxStep);
            const nsub = Math.max(1, Math.min(MAX_SUB, Math.ceil(Ltot / (0.5 * res))));
            const x0 = this.gx2x(this.px[k]), y0 = this.gy2y(this.py[k]);
            let gx = this.px[k], gy = this.py[k], dead = false;
            for (let m = 0; m < nsub; m++) {
                if (m > 0) {
                    const vel = this.sampleVel(fa, fb, w, gx, gy);
                    if (!vel) { dead = true; break; }
                    u = vel[0]; v = vel[1]; sp = Math.hypot(u, v);
                }
                if (sp < 1e-9) break;
                const l = Math.min(pxLen * this.curveF(sp / vmax), maxStep) / nsub;
                gx += (u / sp) * l / res; gy += (v / sp) * l / res;
            }
            if (dead || !this.sampleVel(fa, fb, w, gx, gy)) { this.age[k] = 0; continue; }
            this.px[k] = gx; this.py[k] = gy; this.age[k]++;
            const b = Math.min(PBUCKETS - 1, (this.curveF(s0 / vmax) * PBUCKETS) | 0);
            this.buckets[b].push(x0, y0, this.gx2x(gx), this.gy2y(gy));
        }
    }

    draw() {
        const ctx = this.ctx, W = this.canvas.width, H = this.canvas.height, P = this.P;
        ctx.globalCompositeOperation = 'destination-in';
        ctx.fillStyle = `rgba(0,0,0,${this.S.trail})`; ctx.fillRect(0, 0, W, H);
        ctx.globalCompositeOperation = 'source-over';
        if (this.pathsDirty) {
            this.basinPath = this.basinGeo?.features?.length ? this.buildGeoPath(this.basinGeo) : null;
            this.waterPath = this.waterGeo?.features?.length ? this.buildGeoPath(this.waterGeo) : null;
            this.pathsDirty = false;
        }
        ctx.save(); if (this.basinPath) ctx.clip(this.basinPath, 'evenodd');
        const color = this.S.pcolor || 'speed', solid = SOLID[color];
        ctx.lineWidth = (color === 'white' ? 1.3 : 1.5) * (this.S.width ? this.S.width / 1.5 : 1) * P.dpr;
        ctx.lineCap = 'round';
        let drawn = 0;
        for (let b = 0; b < PBUCKETS; b++) {
            const seg = this.buckets[b];
            if (!seg.length) continue;
            drawn += seg.length / 4;
            ctx.strokeStyle = solid
                ? `rgba(${solid[0]},${solid[1]},${solid[2]},${0.55 + 0.45 * (b / (PBUCKETS - 1))})`
                : WFLOW_PARTICLE_RAMP[b];
            ctx.beginPath();
            for (let q = 0; q < seg.length; q += 4) { ctx.moveTo(seg[q], seg[q + 1]); ctx.lineTo(seg[q + 2], seg[q + 3]); }
            ctx.stroke();
        }
        ctx.restore();
        if (this.stopInWater && this.waterPath) {
            ctx.globalCompositeOperation = 'destination-out';
            ctx.fillStyle = '#000'; ctx.fill(this.waterPath, 'evenodd');
            ctx.globalCompositeOperation = 'source-over';
        }
        this.drawn = drawn;
    }

    tick() {
        if (!this.ctx || !this.vinfo || !this.S.on || this.moving || document.hidden) return;
        const T = this.tms.length, tCur = this.t;
        const i0 = Math.max(0, Math.min(T - 1, Math.floor(tCur))), i1 = Math.min(i0 + 1, T - 1);
        const fa = this.frames.get(i0), fb = this.frames.get(i1);
        if (!fa || !fb) { this.loadVel(i0).catch(() => {}); this.loadVel(i1).catch(() => {}); return; }
        if (!this.P.ax) this.project();
        const wl = this.wet.get(i0);
        this.step(fa, fb, tCur - i0, wl && (this.stopInWater ? wl.land : wl.all));
        this.draw();
    }

    status() {
        if (!this.vinfo) return 'No velocity field – particles unavailable';
        if (!this.S.on) return 'Particles off';
        if (!this.visible) return 'Particles hidden';
        if (this.error) return `Velocity data error: ${this.error}`;
        const i0 = Math.floor(this.t);
        if (!this.frames.has(i0)) return 'Waiting for velocity frame…';
        const wl = this.wet.get(i0), nWet = wl ? (this.stopInWater ? wl.land.length : wl.all.length) : 0;
        if (!nWet) return `No flowing cells at this Wflow step (${this.valid.length} cells)`;
        return `${this.drawn.toLocaleString()} / ${this.px.length.toLocaleString()} particles · ${nWet.toLocaleString()} wet cells · max ${this.vinfo.speed_max.toPrecision(2)} m/s`;
    }
}