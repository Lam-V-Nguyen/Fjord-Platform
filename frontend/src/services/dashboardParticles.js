// Moving-particle renderer shared by the extra layers (Flow movement HYD, Wflow results).
// Regular lon/lat grid: cell (ix, iy) has centre lon0 + ix·dlon, lat0 + iy·dlat (row 0 = south).
// Velocity per valid cell (in mask order): u (east), v (north) m/s, NaN = dry.
// Drawn on a canvas in its own pane; every setting is read directly from the settings object S:
//   count, pspeed, trail, width, curve (linear|sqrt|log), pcolor (speed|white|cyan|yellow|blue), opacity

import { paletteColor } from './dashboardMap.js';

const L = window.L;
const M_PER_DEG = 111320;
const BUCKETS = 6;
export const PARTICLE_COLORS = {
    white: 'rgba(255,255,255,0.9)', cyan: 'rgba(125,211,252,0.95)',
    yellow: 'rgba(253,224,71,0.95)', blue: 'rgba(96,165,250,0.95)',
};
const CURVES = {
    linear: (x) => x,
    sqrt: (x) => Math.sqrt(x),
    log: (x) => Math.log1p(100 * x) / Math.log1p(100),       // same as the Flow Estimation tool (LOG_K = 100)
};
const MAX_SUB = 60;          // maximum number of sub-steps per frame
const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));

export class ParticleField {
    /**
     * @param map       Leaflet map
     * @param S         settings object (read directly, may change at any time)
     * @param pane      pane name, zIndex
     * @param palette   colour palette when pcolor = 'speed'
     * @param shouldRun () => bool – false "freezes" the particles (e.g. only run while playing)
     */
    constructor(map, { S, pane = 'particles', zIndex = 410, palette = 'speed_light', shouldRun = () => true }) {
        this.map = map; this.S = S; this.paneName = pane; this.zIndex = zIndex;
        this.palette = palette; this.shouldRun = shouldRun;
        this.grid = null; this.idx = null; this.cells = null; this.frame = null; this.speedMax = 0.05;
        this.canvas = null; this.ctx = null; this.raf = 0; this.visible = false; this.moving = false; this.drawn = 0;
        this.P = { ax: 0, bx: 0, ay: 0, by: 0 };
        this.onView = () => this.resetView();
        this.onMoveStart = () => { this.moving = true; this.clear(); };
    }

    // grid: {nx, ny, lon0, lat0, dlon, dlat}; cells: indices k = iy·nx + ix of the valid cells (data order)
    setGrid(grid, cells, speedMax) {
        this.grid = grid; this.cells = Int32Array.from(cells);
        this.idx = new Int32Array(grid.nx * grid.ny).fill(-1);
        this.cells.forEach((k, j) => { this.idx[k] = j; });
        this.dxm = grid.dlon * M_PER_DEG * Math.cos(((grid.lat0 + grid.ny * grid.dlat / 2) * Math.PI) / 180);
        this.dym = grid.dlat * M_PER_DEG;
        this.speedMax = Number.isFinite(+speedMax) && +speedMax > 0 ? +speedMax : 0.05;
        this.frame = null;
        if (this.visible) this.resetView();
    }

    // f: {u, v} in valid-cell order. The list of "wet" cells (finite, non-zero velocity) is used to spawn particles –
    // particles are not placed in dry/still cells (which would only produce motionless dots).
    setFrame(f) {
        this.frame = f;
        if (f && !f.wet) {
            const wet = [];
            for (let j = 0; j < f.u.length; j++) {
                const u = f.u[j], v = f.v[j];
                if (Number.isFinite(u) && Number.isFinite(v) && (u !== 0 || v !== 0)) wet.push(j);
            }
            f.wet = Uint32Array.from(wet);
        }
    }

    // Velocity bilinearly interpolated from the 4 neighbouring cells (skipping dry/out-of-domain cells), as in Flow Estimation.
    // This lets particles travel along 1-cell-wide rivers instead of "dying" as soon as they leave the cell centre.
    sample(gx, gy, out) {
        const g = this.grid, f = this.frame;
        const ic = Math.round(gx), jc = Math.round(gy);
        if (ic < 0 || jc < 0 || ic >= g.nx || jc >= g.ny || this.idx[jc * g.nx + ic] < 0) return false;
        const i0 = Math.floor(gx), j0 = Math.floor(gy), fx = gx - i0, fy = gy - j0;
        let su = 0, sv = 0, sw = 0;
        for (let c = 0; c < 4; c++) {
            const i = i0 + (c & 1), j = j0 + (c >> 1);
            if (i < 0 || j < 0 || i >= g.nx || j >= g.ny) continue;
            const q = this.idx[j * g.nx + i];
            if (q < 0) continue;
            const u = f.u[q], v = f.v[q];
            if (!Number.isFinite(u) || !Number.isFinite(v)) continue;
            const w = ((c & 1) ? fx : 1 - fx) * ((c >> 1) ? fy : 1 - fy);
            su += w * u; sv += w * v; sw += w;
        }
        if (sw < 0.15) return false;
        out[0] = su / sw; out[1] = sv / sw;
        return true;
    }

    cellOf(gx, gy) {
        const g = this.grid, ix = Math.round(gx), iy = Math.round(gy);
        if (!g || ix < 0 || iy < 0 || ix >= g.nx || iy >= g.ny) return -1;
        return this.idx[iy * g.nx + ix];
    }

    cellAt(latlng) {
        const g = this.grid;
        return g ? this.cellOf((latlng.lng - g.lon0) / g.dlon, (latlng.lat - g.lat0) / g.dlat) : -1;
    }

    // view
    show() {
        if (this.visible) return;
        this.visible = true;
        if (!this.map.getPane(this.paneName)) {
            const p = this.map.createPane(this.paneName);
            p.style.zIndex = this.zIndex; p.style.pointerEvents = 'none';
        }
        this.canvas = L.DomUtil.create('canvas', 'movement-canvas', this.map.getPane(this.paneName));
        this.ctx = this.canvas.getContext('2d');
        this.applyOpacity();
        this.map.on('movestart zoomstart', this.onMoveStart);
        this.map.on('moveend zoomend resize viewreset', this.onView);
        // The map may have no size yet when the layer is enabled (another view is shown) -> watch the container
        this._ro = new ResizeObserver(() => {
            const el = this.map.getContainer();
            if (!el.clientWidth || !el.clientHeight || !this.canvas) return;
            const s = this.map.getSize();
            if (s.x !== el.clientWidth || s.y !== el.clientHeight) this.map.invalidateSize({ pan: false });  // -> 'resize'
            else if (!this.canvas.width) this.resetView();
        });
        this._ro.observe(this.map.getContainer());
        this.resetView();
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

    applyOpacity() { if (this.canvas) this.canvas.style.opacity = String(this.S.opacity ?? 1); }

    clear() { if (this.ctx) this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); }

    resetView() {
        if (!this.canvas || !this.grid) return;
        this.moving = false;
        const size = this.map.getSize(), dpr = window.devicePixelRatio || 1;
        if (!size.x || !size.y) { this.moving = false; return; }       // the map has no size yet
        this.canvas.width = size.x * dpr; this.canvas.height = size.y * dpr;
        this.canvas.style.width = `${size.x}px`; this.canvas.style.height = `${size.y}px`;
        L.DomUtil.setPosition(this.canvas, this.map.containerPointToLayerPoint([0, 0]));
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        // Linear mapping lon -> x, mercator(lat) -> y (exact for Web Mercator)
        const g = this.grid, lat = g.lat0, lon = g.lon0;
        const p0 = this.map.latLngToContainerPoint([lat, lon]);
        const p1 = this.map.latLngToContainerPoint([lat + 0.01, lon + 0.01]);
        this.P.ax = (p1.x - p0.x) / 0.01; this.P.bx = p0.x - this.P.ax * lon;
        this.P.ay = (p1.y - p0.y) / (merc(lat + 0.01) - merc(lat)); this.P.by = p0.y - this.P.ay * merc(lat);
        // Step length: maximum speed ~1.6 px/frame × particle speed factor, independent of the zoom level
        this.pxPerM = Math.max(1e-9, Math.abs(this.P.ax) * g.dlon / this.dxm);
        this.spawnAll();
    }

    spawnAll() {
        const n = Math.max(200, Math.min(20000, this.S.count | 0));
        this.px = new Float32Array(n); this.py = new Float32Array(n);
        this.age = new Uint16Array(n); this.life = new Uint16Array(n);
        for (let i = 0; i < n; i++) this.respawn(i, true);
        this.clear();
    }

    // Respawn a particle in a wet cell (no frame yet: any cell of the domain)
    respawn(i, randomAge = false) {
        const wet = this.frame?.wet, nx = this.grid?.nx;
        const pool = wet?.length ? wet : null;
        if (!pool && !this.cells?.length) return;
        const tmp = this._v || (this._v = new Float64Array(2));
        for (let t = 0; t < 8; t++) {
            const j = pool ? pool[(Math.random() * pool.length) | 0] : (Math.random() * this.cells.length) | 0;
            const c = this.cells[j];
            const gx = (c % nx) + Math.random() - 0.5, gy = ((c / nx) | 0) + Math.random() - 0.5;
            if (this.frame && !this.sample(gx, gy, tmp)) continue;
            this.px[i] = gx; this.py[i] = gy;
            this.life[i] = 40 + ((Math.random() * 90) | 0);
            this.age[i] = randomAge ? (Math.random() * this.life[i]) | 0 : 0;
            return;
        }
        this.age[i] = this.life[i] = 0;          // retry in the next frame
    }

    // Status shown in the ⚙ panel (helps to see why no particles appear)
    status() {
        if (!this.visible) return 'Particles hidden';
        if (!this.grid || !this.cells?.length) return 'No valid water cells in the grid';
        if (!this.frame) return 'Waiting for velocity frame…';
        if (!this.shouldRun()) return 'Paused (motion = when playing)';
        if (!this.drawn) return `No moving particles – velocity is zero/NaN in this frame (${this.cells.length} cells)`;
        return `${this.drawn.toLocaleString()} / ${this.px?.length?.toLocaleString() || 0} particles moving · max ${this.speedMax.toPrecision(2)} m/s`;
    }

    tick() {
        if (!this.ctx || !this.frame || !this.px || this.moving || document.hidden || !this.shouldRun()) return;
        const ctx = this.ctx, g = this.grid, f = this.frame, S = this.S;
        const w = this.canvas.width, h = this.canvas.height;
        // Fade old trails: the larger the trail, the longer the trails
        ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalCompositeOperation = 'destination-in'; ctx.fillStyle = `rgba(0,0,0,${S.trail})`; ctx.fillRect(0, 0, w, h);
        ctx.restore(); ctx.globalCompositeOperation = 'source-over';
        const byColor = S.pcolor === 'speed';
        const paths = Array.from({ length: byColor ? BUCKETS : 1 }, () => []);
        const vmax = this.speedMax, P = this.P, curve = CURVES[S.curve] || CURVES.linear;
        // Distance per frame (m): ~1.5 px × speed factor × curve(|v|/vmax); travelled in several sub-steps
        // (≤ half a cell), re-sampling the velocity at each step -> particles follow the flow (as in Flow Estimation).
        const pxLen = (1.5 * (S.pspeed || 1)) / this.pxPerM, maxStep = 40 / this.pxPerM;
        const cellM = Math.min(this.dxm, this.dym), vel = this._v || (this._v = new Float64Array(2));
        for (let i = 0; i < this.px.length; i++) {
            if (this.age[i] === 0 || ++this.age[i] > this.life[i]) { this.respawn(i); if (!this.age[i]) this.age[i] = 1; continue; }
            if (!this.sample(this.px[i], this.py[i], vel)) { this.age[i] = 0; continue; }
            let u = vel[0], v = vel[1], sp = Math.hypot(u, v);
            const s0 = sp, Ltot = Math.min(pxLen * curve(Math.min(1, sp / vmax)), maxStep);
            const nsub = Math.max(1, Math.min(MAX_SUB, Math.ceil(Ltot / (0.5 * cellM))));
            const x0 = this.px[i], y0 = this.py[i];
            let gx = x0, gy = y0, dead = false;
            for (let m = 0; m < nsub; m++) {
                if (m > 0) {
                    if (!this.sample(gx, gy, vel)) { dead = true; break; }
                    u = vel[0]; v = vel[1]; sp = Math.hypot(u, v);
                }
                if (sp < 1e-9) break;
                const l = Math.min(pxLen * curve(Math.min(1, sp / vmax)), maxStep) / nsub;
                gx += (u / sp) * l / this.dxm; gy += (v / sp) * l / this.dym;
            }
            if (dead || !this.sample(gx, gy, vel)) { this.age[i] = 0; continue; }
            this.px[i] = gx; this.py[i] = gy;
            const lon0 = g.lon0 + x0 * g.dlon, lat0 = g.lat0 + y0 * g.dlat;
            const lon1 = g.lon0 + gx * g.dlon, lat1 = g.lat0 + gy * g.dlat;
            const b = byColor ? Math.min(BUCKETS - 1, (curve(Math.min(1, s0 / vmax)) * BUCKETS) | 0) : 0;
            paths[b].push(P.ax * lon0 + P.bx, P.ay * merc(lat0) + P.by, P.ax * lon1 + P.bx, P.ay * merc(lat1) + P.by);
        }
        this.drawn = paths.reduce((a, p) => a + p.length / 4, 0);
        ctx.lineWidth = S.width || 1.4; ctx.lineCap = 'round';
        for (let b = 0; b < paths.length; b++) {
            const p = paths[b];
            if (!p.length) continue;
            ctx.strokeStyle = byColor ? paletteColor((b + 0.5) / BUCKETS, this.palette) : (PARTICLE_COLORS[S.pcolor] || PARTICLE_COLORS.white);
            ctx.beginPath();
            for (let q = 0; q < p.length; q += 4) { ctx.moveTo(p[q], p[q + 1]); ctx.lineTo(p[q + 2], p[q + 3]); }
            ctx.stroke();
        }
    }
}