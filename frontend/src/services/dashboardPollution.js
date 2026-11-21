// Lớp "Pollutant spread (WAQ)" của dashboard:
//  - MỨC ĐỘ: nồng độ một chất WAQ trên lưới tính toán (đa giác, canvas), thang màu CỐ ĐỊNH cho toàn bộ
//    thời gian mô phỏng (0 – P99.5); ô dưới ngưỡng (x % của giá trị lớn nhất) để trong suốt.
//  - LAN TRUYỀN: hạt trôi theo vận tốc HYD của CÙNG LỚP và thời điểm gần nhất, chỉ sinh ra và chỉ sống
//    trong vùng có ô nhiễm (vận tốc ngoài vùng = NaN -> hạt "chết" khi ra khỏi vùng ô nhiễm).
//  - Mật độ hạt cố định theo diện tích ô nhiễm, tốc độ tuyến tính theo |v| / speed_max (cố định theo
//    lớp, toàn thời gian). Người dùng KHÔNG chỉnh được mật độ / tốc độ để tránh đọc sai.
// Dữ liệu: POST /dashboard/pollution/init (chất, lớp, lưới hạt), /dashboard/pollution/frame (theo thời điểm).

import { paletteColor, fmtVal } from './dashboardMap.js';
import { ParticleField } from './dashboardParticles.js';
import { ui, bindSettings, loadSettings, saveSettings } from './dashboardLayerSettings.js';

const L = window.L;
const SETTINGS_KEY = 'dash.pollution.settings.v1';
const DEFAULTS = { name: '', layer: 'max', threshold: 0.01 };
const PALETTE = 'quality';
const PER_CELL = 0.06;                 // hạt / ô lưới hạt bị ô nhiễm -> cùng mật độ ở mọi vùng, mọi thời điểm
const MAX_PARTICLES = 20000;
// Cài đặt hạt cố định (đọc bởi ParticleField) – không hiển thị trong bảng cài đặt
const PARTICLE_S = { count: 0, pspeed: 1, trail: 0.9, width: 1.2, curve: 'linear', pcolor: 'white', opacity: 0.9 };
const THRESHOLDS = [['0.001', '0.1 % of max'], ['0.01', '1 % of max'], ['0.05', '5 % of max'], ['0.1', '10 % of max']];

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function post(path, body) {
    const res = await fetch(`/dashboard/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || !data) throw new Error(data?.message || data?.detail || `HTTP ${res.status}`);
    return data;
}

function f32(b64) {
    const s = atob(b64), u8 = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) u8[i] = s.charCodeAt(i);
    return new Float32Array(u8.buffer);
}

// ParticleField dùng tối thiểu 200 hạt; ở đây số hạt tỷ lệ với diện tích ô nhiễm nên cho phép ít hơn
class PlumeParticles extends ParticleField {
    spawnAll() {
        const n = Math.max(1, Math.min(MAX_PARTICLES, this.S.count | 0));
        this.px = new Float32Array(n); this.py = new Float32Array(n);
        this.age = new Uint16Array(n); this.life = new Uint16Array(n);
        for (let i = 0; i < n; i++) this.respawn(i, true);
        this.clear();
    }
}

export class PollutionLayer {
    constructor(map, { projectName }) {
        this.map = map; this.projectName = projectName;
        this.S = loadSettings(SETTINGS_KEY, DEFAULTS);
        this.PS = { ...PARTICLE_S };
        this.visible = false; this.info = null; this.frame = null; this.time = null; this.token = 0;
        this.polys = []; this.group = null; this.nPolluted = 0;
        if (!map.getPane('pollution')) { const p = map.createPane('pollution'); p.style.zIndex = 395; }
        this.renderer = L.canvas({ padding: 0.4, pane: 'pollution', tolerance: 1 });
        this.particles = new PlumeParticles(map, { S: this.PS, pane: 'pollution-particles', zIndex: 415 });
    }

    // data
    async init() {
        const res = await post('pollution/init', { projectName: this.projectName });
        if (res.status !== 'ok') throw new Error(res.message || 'Pollutant data not available');
        this.info = res;
        if (!res.substances?.length) throw new Error('No WAQ substance on the model grid');
        if (!res.substances.some(s => s.name === this.S.name)) this.S.name = res.substances[0].name;
        if (!res.layers.some(([k]) => k === String(this.S.layer))) this.S.layer = res.layers[0]?.[0] || 'average';
        this.cellFace = Int32Array.from(res.cell_face);
        const mesh = await post('mesh', { projectName: this.projectName });
        if (mesh.status !== 'ok') throw new Error(mesh.message || 'No mesh');
        this.buildPolygons(mesh.mesh);
        return this;
    }

    buildPolygons(mesh) {
        this.group = L.layerGroup(); this.polys = [];
        mesh.features.forEach((f, i) => {
            const ring = f.geometry.coordinates[0];
            if (!ring || ring.length < 4) { this.polys.push(null); return; }
            const p = L.polygon(ring.map(([x, y]) => [y, x]), {
                renderer: this.renderer, pane: 'pollution', stroke: false, fillOpacity: 0, fillColor: '#000', interactive: true,
            });
            p.on('click', (e) => this.popup(e.latlng, i));
            this.polys.push(p); this.group.addLayer(p);
        });
    }

    substance() { return this.info?.substances.find(s => s.name === this.S.name) || this.info?.substances[0]; }
    layerLabel() { return (this.info?.layers.find(([k]) => k === String(this.S.layer)) || [null, 'Depth average'])[1]; }

    async setTime(iso) {
        this.time = iso ?? this.time;
        if (!this.info) return;
        const token = ++this.token;
        const res = await post('pollution/frame', { projectName: this.projectName, name: this.S.name, layer: this.S.layer, time: this.time });
        if (res.status !== 'ok') throw new Error(res.message || 'Cannot load pollutant frame');
        if (token !== this.token) return;                     // đã có yêu cầu mới hơn
        const f = this.frame = { ...res, conc: f32(res.conc), u: res.u ? f32(res.u) : null, v: res.v ? f32(res.v) : null };
        if (f.conc.length !== this.polys.length) throw new Error('WAQ values do not match the dashboard mesh');
        this.paint(); this.updateParticles();
    }

    setPlaying() { /* hạt luôn chuyển động theo vận tốc của thời điểm đang hiển thị */ }

    // drawing
    paint() {
        const f = this.frame, cmax = f.cmax || 1, thr = this.S.threshold * cmax;
        let n = 0;
        for (let i = 0; i < this.polys.length; i++) {
            const p = this.polys[i];
            if (!p) continue;
            const c = f.conc[i];
            if (Number.isFinite(c) && c >= thr && c > 0) {
                p.setStyle({ fillColor: paletteColor(c / cmax, PALETTE), fillOpacity: 0.8 }); n++;
            } else p.setStyle({ fillOpacity: 0 });
        }
        this.nPolluted = n;
    }

    updateParticles() {
        const f = this.frame;
        if (!f.u || !this.info.cells?.length) { this.particles.hide(); return; }
        const thr = this.S.threshold * (f.cmax || 1), cf = this.cellFace, n = cf.length;
        const u = new Float32Array(n), v = new Float32Array(n), wet = [];
        for (let j = 0; j < n; j++) {
            const k = cf[j], c = f.conc[k], uu = f.u[k], vv = f.v[k];
            if (Number.isFinite(c) && c >= thr && c > 0 && Number.isFinite(uu) && Number.isFinite(vv) && (uu || vv)) {
                u[j] = uu; v[j] = vv; wet.push(j);
            } else { u[j] = NaN; v[j] = NaN; }           // ngoài vùng ô nhiễm: hạt không đi vào được
        }
        const key = `${this.S.layer}|${f.speed_max}`;
        if (this.gridKey !== key) { this.particles.setGrid(this.info.grid, this.info.cells, f.speed_max); this.gridKey = key; }
        this.particles.setFrame({ u, v, wet: Uint32Array.from(wet) });
        const target = Math.min(MAX_PARTICLES, Math.round(wet.length * PER_CELL));
        if (target !== this.PS.count) {
            this.PS.count = target;
            if (this.particles.visible && this.particles.canvas) this.particles.spawnAll();
        }
        if (this.visible && wet.length && !this.particles.visible) this.particles.show();
        if (!wet.length && this.particles.visible) this.particles.hide();
    }

    popup(latlng, i) {
        const f = this.frame, s = this.substance();
        if (!f) return;
        L.popup({ closeButton: false }).setLatLng(latlng)
            .setContent(`<div class="popup-title">${esc(s?.label || f.label)}</div>
                <div class="popup-kv"><span>Cell</span><span>#${i}</span>
                <span>Value</span><span>${fmtVal(f.conc[i], f.unit)}</span>
                <span>Layer</span><span>${esc(this.layerLabel())}</span>
                <span>Time</span><span>${esc((f.time || '').replace('T', ' ').slice(0, 16))}</span></div>`)
            .openOn(this.map);
    }

    //  view
    show() {
        if (this.visible) return;
        this.visible = true;
        this.group?.addTo(this.map);
        if (this.frame) this.updateParticles();
    }

    hide() {
        this.visible = false;
        if (this.group) this.map.removeLayer(this.group);
        this.particles.hide();
    }

    destroy() { this.hide(); this.group = null; this.polys = []; }

    legend() {
        const f = this.frame, s = this.substance();
        if (!f) return null;
        const cmax = f.cmax || 1, pct = `${+(this.S.threshold * 100).toFixed(1)} %`;
        const parts = [`WAQ ${(f.time || '').replace('T', ' ').slice(0, 16)}`, this.layerLabel(), `cells < ${pct} of max hidden`];
        if (f.u) parts.push(`particles: HYD current ${(f.hyd_time || '').replace('T', ' ').slice(0, 16)}, linear speed, max ${fmtVal(f.speed_max, 'm/s')}`);
        return { title: `Pollutant · ${s?.label || f.label}${f.unit ? ` (${f.unit})` : ''}`, palette: PALETTE,
            min: this.S.threshold * cmax, max: cmax, maxPrefix: '≥ ', note: parts.join(' · ') 
        };
    }

    status() {
        if (!this.frame) return 'Loading pollutant data…';
        const ps = this.frame.u ? this.particles.status() : 'No HYD velocities - particles disabled';
        return `${this.nPolluted.toLocaleString()} polluted cells · ${ps}`;
    }

    // settings panel
    renderSettings(el, ctx) {
        const s = this.substance(), S = this.S;
        el.innerHTML =
            ui.section('Pollutant',
                ui.row('Substance', ui.select('name', this.info.substances.map(x => [x.name, x.unit ? `${x.label} (${x.unit})` : x.label]), S.name)) +
                (s?.layered !== false && this.info.layers.length > 1 ? ui.row('Layer', ui.select('layer', this.info.layers, S.layer)) : '') +
                ui.row('Show cells ≥', ui.select('threshold', THRESHOLDS, String(S.threshold))),
                'Colour = concentration on a <b>fixed scale for the whole simulation</b> (up to the 99.5th percentile). ' +
                '<b>Max over depth</b> shows where the pollutant is at any depth (best to follow the spread); ' +
                '<b>Depth average</b> dilutes a surface plume over deep water, so it can seem to vanish mid-lake and reappear near the shallow far shore. ' +
                'Particles follow the HYD current of the same layer (depth-averaged for Max over depth) and only move where the pollutant is above the threshold. ' +
                'Particle density is the same in every polluted area and particle speed is proportional to the current speed, ' +
                'so neither can be changed - this keeps times and areas comparable.') +
            `<div class="ls-status"></div>`;
        bindSettings(el, {
            onSet: async (k, val) => {
                S[k] = k === 'threshold' ? Number(val) : val;
                saveSettings(SETTINGS_KEY, S);
                if (k === 'name') this.renderSettings(el, ctx);               // lớp có/không theo chất
                if (k === 'threshold' && this.frame) { this.paint(); this.updateParticles(); ctx.refreshLegend(); return; }
                ctx.busy(true);
                try { await this.setTime(this.time); ctx.refreshLegend(); }
                catch (err) { ctx.toast(`Pollutant: ${err.message}`); }
                finally { ctx.busy(false); }
            },
        });
    }
}