import { setupTabs } from "./tabManager.js";
import { getUser, signalSender, getProjectList, jsonLoader, nameChecker
} from "./commonFunctions.js";
import { initMap } from "./visualizationMap.js";
import { projectRender } from "./projectManager.js";
import { CENTER, ZOOM, getLastTimeZone } from "./constant.js";


const $ = (id) => document.getElementById(id);
const obj = {
    projectList: $('project-list'), projectName: $('project-name'), projectOpener: $('open-btn'), 
    showPanel: $('show-btn'), trace: $('wv-trace'), clockNote: $("wv-clock-note"),
    flowPanel: $('wflow-viewer'), gridVar: $("wv-grid-var"), riverVar: $("wv-river-var"),
    time: $("wv-time"), info: $("wv-info"), months: $("wv-months"), date: $("wv-date"),
    clock: $("wv-clock"), loading: $("wv-loading"), playIcon: $("wv-play-icon"), play: $("wv-play"),
    mode: $("wv-mode"), thresholdRow: $("wv-threshold-row"), threshold: $("wv-threshold"), 
    scale: $("wv-scale"), opacityOut: $("wv-opacity-out"), opacity: $("wv-opacity"), back: $("wv-back"),
    fwd: $("wv-fwd"), collapse: $("wv-collapse"), close: $("wv-close"), rate: $("wv-rate"),
    chart: $("wv-chart"), legendGrid: $("wv-legend-grid"), legendRiver: $("wv-legend-river"),
    legend: $("wv-legend"), probeHint: $("wv-probe-hint"), panel: $("wv-panel"), 
    status: $("wv-status"), download: $("download-data-btn"), 
    // Flow particles
    pOn: $("wv-particles"), pCount: $("wv-pcount"), pCountOut: $("wv-pcount-out"), 
    pSpeed: $("wv-pspeed"), pSpeedOut: $("wv-pspeed-out"), pTrail: $("wv-ptrail"), 
    pTrailOut: $("wv-ptrail-out"), pCurve: $("wv-pcurve"), pColor: $("wv-pcolor"), 
    pInfo: $("wv-pinfo"), pSection: $("wv-particle-section"), res: $("wv-res"), 
    resHint: $("wv-res-hint"), pWater: $("wv-pwater"),
}
const Lf = window.L;
const RAMPS = {
    depth:  ["#e8f4f8", "#b3dceb", "#6fb7d6", "#2f7bb6", "#16509a", "#0b2a6b"],
    flow:   ["#0b2f4f", "#1b5f8a", "#2a9a9a", "#79c26a", "#e6d34e", "#f08a2b"],
    runoff: ["#fff5d6", "#fdd49e", "#fc8d59", "#e34a33", "#b30000", "#6b0000"],
    time:   ["#2c105c", "#5b2a86", "#a83b8f", "#e46c5b", "#f7a541", "#f9e46c"],
    hours:  ["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f", "#2d0a5e"],
};
const MODES = { 
    animate: "Animation", max: "Maximum", mean: "Mean", sum: "Total", 
    tmax: "Time of peak", above: "Hours above" 
};
function makeLUT(stops) {
    const c = stops.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)));
    const lut = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i++) {
        const t = (i / 255) * (c.length - 1), k = Math.min(Math.floor(t), c.length - 2), f = t - k;
        for (let ch = 0; ch < 3; ch++) lut[i * 3 + ch] = c[k][ch] + (c[k + 1][ch] - c[k][ch]) * f;
    }
    return lut;
}
const LUTS = Object.fromEntries(Object.entries(RAMPS).map(([k, v]) => [k, makeLUT(v)]));
const F16 = (() => {
    const t = new Float32Array(65536);
    for (let h = 0; h < 65536; h++) {
        const s = h & 0x8000 ? -1 : 1, e = (h >> 10) & 0x1f, m = h & 0x3ff;
        t[h] = e === 0 ? s * m * 2 ** -24 : e === 31 ? (m ? NaN : s * Infinity) : s * (1 + m / 1024) * 2 ** (e - 15);
    }
    return t;
})();
const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
// Particles
const M_PER_DEG = 111320, LOG_K = 100, MAX_STEP_PX = 40, PBUCKETS = 6;
const PARTICLE_RAMP = ["#e8f6ff", "#bfe9ff", "#d9f59a", "#ffe066", "#ffab40", "#ff6b3d"];
const PT = {
    on: true, count: 4000, speed: 1, trail: 0.92, curve: "sqrt", color: "blue", stopInWater: true,
    px: null, py: null, age: null, life: null, buckets: Array.from({ length: PBUCKETS }, () => []),
    frames: new Map(), pending: new Map(), wet: new Map(),
};
const PARTICLE_SOLID = { white: [255, 255, 255], black: [0, 0, 0], dark_blue: [12, 42, 107], blue: [0, 0, 255] };
function curveF(x) {
    x = Math.max(0, Math.min(x, 1.5));
    if (PT.curve === "linear") return x;
    if (PT.curve === "log") return Math.log1p(LOG_K * x) / Math.log1p(LOG_K);
    return Math.sqrt(x);
}
function allocParticles() {
    const n = PT.count;
    PT.px = new Float32Array(n); PT.py = new Float32Array(n);
    PT.age = new Uint16Array(n); PT.life = new Uint16Array(n);
}

const S = {
    meta: null, mask: null, valid: null, rivers: null, t: 0, playing: false, rate: 3,
    gridVar: null, riverVar: null, mode: "animate", threshold: 0.01, scale: "auto", opacity: 0.8,
    frames: new Map(), pending: new Map(), stats: new Map(), probe: null, probeLayer: null,
    phase: 0, raf: 0, last: performance.now(), drawnKey: "", riverValues: null, gridValues: null,
};
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function resetState() {
    S.traceVars = new Set(); if (obj.trace) obj.trace.innerHTML = "";
    S.frames.clear(); S.pending.clear(); S.stats.clear();
    PT.frames.clear(); PT.pending.clear(); PT.wet.clear();
    Object.assign(S, { meta: null, mask: null, valid: null, rivers: null, t: 0, playing: false,
        probe: null, probeLayer: null, phase: 0, riverValues: null, gridValues: null });
    if (typeof tf !== "undefined") tf = 0; if (obj.clockNote) obj.clockNote.textContent = "";
}
const resolveTimeZone = () => {
    const tz = getLastTimeZone();
    if (tz) {
        try { new Intl.DateTimeFormat("en-GB", { timeZone: tz }); return tz;
        } catch { }
    }
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
};

let currentProject, viewer = null, lastFlowName, lastRes = "auto";

setupTabs(document); await getProject();
const mapObj = await initMap('leaflet-map-flow-estimation'); 
mapOptions(mapObj); flowManager(mapObj);


async function getProject() { 
    const userName = await getUser(); currentProject = userName.split('/').pop();
    const respond = await getProjectList(`${currentProject}/flows`, '');
    await projectRender(obj.projectName, obj.projectList, respond);
}

async function openScenario(map, name, opts = {}) {
    signalSender('showOverlay', "Preparing Wflow outputs (first time can take a few minutes).\nPlease wait...");
    const content = {
        projectName: currentProject, flowName: name, timeZone: getLastTimeZone(),
        res: opts.res ?? lastRes, rebuild: !!opts.rebuild,
    };
    try {
        const res = await jsonLoader("flow_init", content);
        if (!res || res.status === "error") { alert(res?.message || "Cannot open wflow results."); return; }
        viewer?.destroy(); viewer = null; cancelAnimationFrame(S.raf); S.playing = false;
        const panel = $("wflow-viewer"); if (panel) panel.style.display = "";
        viewer = await wflowmanager(map, res.content.meta, name, opts);
        obj.flowPanel.style.display = "block"; return true;
    } catch (err) { alert(`Cannot display wflow results: ${err.message || err}`); return false;
    } finally { signalSender("hideOverlay"); }
}

function flowManager(map, opts = {}) {
    obj.projectOpener.addEventListener('click', async () => {
        const name = obj.projectName.value.trim(); let res;
        if (!name || name === '') { alert('Please define scenario name.'); return; }
        if (nameChecker(name)) { alert('Scenario name contains invalid characters.'); return; }
        lastFlowName = name; lastRes = "auto"; await openScenario(map, name, opts);
        obj.showPanel.style.display = "none";
    });
    obj.showPanel.addEventListener('click', async(e) => {
        const name = lastFlowName || obj.projectName.value.trim();
        if (!name) { alert("Please select a scenario first."); return; }
        obj.projectName.value = name;
        const ok = await openScenario(map, name, {});
        if (ok) e.target.style.display = "none";
    });
}

async function wflowmanager(map, meta, flowName, opts = {}){
    resetState(); S.meta = meta; const g = meta.grid;
    const ac = new AbortController(); 
    const setStatus = (msg, err = false) => {
        if (obj.status) { 
            obj.status.textContent = msg; obj.status.classList.toggle("error", err); 
        } else if (err && msg) alert(msg);
    };
    const qs = new URLSearchParams({ 
        projectName: currentProject, flowName: flowName, modelFolder: opts.modelFolder ?? "wflow_model"
    }).toString();
    const maskRes = await fetch(`/wflow_mask?${qs}&v=${meta.version}`);
    if (!maskRes.ok) throw new Error((await maskRes.json()).message || `mask: HTTP ${maskRes.status}`);
    S.mask = new Uint8Array(await maskRes.arrayBuffer());
    if (S.mask.length !== g.nx * g.ny) throw new Error("Mask does not match the viewer grid – rebuild the viewer data.");
    const v = []; S.mask.forEach((m, k) => { if (m) v.push(k); }); S.valid = Uint32Array.from(v);
    if (meta.river_cells) S.rivers = await (await fetch(`/wflow_rivers?${qs}`)).json();
    const tms = meta.times.map((s) => { 
        const m = s.match(/(\d+)-(\d+)-(\d+)[T ](\d+):(\d+)/); 
        return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]); 
    });
    const vars = Object.entries(meta.variables);
    const groups = (kind) => groupsOf(meta.variables, kind);
    const fill = (sel, kind) => {
        if (!sel) return;
        const list = groups(kind);
        sel.innerHTML = `<option value="">— none —</option>` + [...list].map(([gname, items]) =>
            `<optgroup label="${esc(gname)}">` + items.map(([k, i]) =>
                `<option value="${esc(k)}">${esc(i.description)}${i.units ? ` (${esc(i.units)})` : ""}</option>`).join("") +
            `</optgroup>`).join("");
    };
    fill(obj.gridVar, "grid"); fill(obj.riverVar, "river");
    S.chartVars = new Set();
    const chartBox = $("wv-chart-vars");
    chartBox?.addEventListener("change", (e) => {
        if (e.target.type !== "checkbox") return;
        e.target.checked ? S.chartVars.add(e.target.value) : S.chartVars.delete(e.target.value);
        S.chartAuto = false; drawChart(obj, meta, S, tms);
    }, { signal: ac.signal });
    S.gridVar = opts.gridVar ?? (meta.variables.overland_depth ? "overland_depth" : vars.find(([, i]) => i.kind === "grid")?.[0] ?? null);
    S.riverVar = opts.riverVar ?? (meta.variables.river_discharge ? "river_discharge" : null);
    S.chartAuto = true;
    const syncChartVars = () => { 
        if (!S.chartAuto) return; 
        S.chartVars = new Set([S.gridVar, S.riverVar].filter(Boolean));
        renderChartPicker(chartBox, groupsOf(meta.variables, null)); drawChart(obj, meta, S, tms); 
    };
    if (obj.gridVar) obj.gridVar.value = S.gridVar ?? "";
    if (obj.riverVar) obj.riverVar.value = S.riverVar ?? "";
    if (obj.time) { 
        obj.time.min = 0; obj.time.max = meta.times.length - 1; 
        obj.time.value = 0; obj.time.step = "0.01";
    }
    if (obj.info) obj.info.textContent = `Res: ${g.res_m} m · ${g.n_valid.toLocaleString("en-US")} cells · ${meta.times.length} steps`;
    if (obj.months) {
        const t0 = tms[0], t1 = tms[tms.length - 1]; obj.months.innerHTML = "";
        const tz = resolveTimeZone(), spans = [];
        const fmt = new Intl.DateTimeFormat("en-GB", { timeZone: tz, month: "numeric", year: "numeric" });
        let cur = Date.UTC(new Date(t0).getUTCFullYear(), new Date(t0).getUTCMonth(), 1);
        while (cur <= t1) {
            if (cur >= t0) { 
                const sp = document.createElement("span");
                sp.style.left = `${((cur - t0) / Math.max(1, t1 - t0)) * 100}%`;
                const parts = fmt.formatToParts(new Date(cur));
                const mo = parts.find((x) => x.type === "month")?.value;
                const yr = parts.find((x) => x.type === "year")?.value;
                sp.textContent = `month ${mo}/${yr}`;
                obj.months.appendChild(sp); spans.push(sp);
            }
            const d = new Date(cur); cur = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
        }
        if (!obj.months.children.length) { 
            const sp = document.createElement("span"); sp.style.left = "0"; 
            sp.textContent = fmt.format(new Date(t0)); obj.months.appendChild(sp); spans.push(sp);
        }
        if (spans.length) spans[spans.length - 1].classList.add("last");
    }
    const mkCanvas = (name, z) => {
        const pane = map.getPane(name) || map.createPane(name);
        pane.style.zIndex = z; pane.style.pointerEvents = "none"; pane.innerHTML = "";
        const c = Lf.DomUtil.create("canvas", "wv-canvas", pane);
        c.style.position = "absolute"; return c;
    };
    const gridCv = mkCanvas("wvGrid", 370), riverCv = mkCanvas("wvRiver", 395);
    const partCv = mkCanvas("wvParticles", 390), pctx = partCv.getContext("2d");
    const gctx = gridCv.getContext("2d"), rctx = riverCv.getContext("2d");
    const off = document.createElement("canvas"); off.width = g.nx; off.height = g.ny;
    const offCtx = off.getContext("2d"), offImg = offCtx.createImageData(g.nx, g.ny);
    const P = { ax: 0, bx: 0, ay: 0, by: 0, dpr: 1 };
    const hasWater = (g.n_water || 0) > 0;
    let basinGeo = null, waterGeo = null, basinPath = null, waterPath = null, pathsDirty = true;
    const outline = opts.showBasin !== false ? await fetch(`/wflow_basins?${qs}`).then((r) => r.ok ? r.json() : null)
        .then((gj) => { basinGeo = gj; return gj && Lf.geoJSON(gj, {
            interactive: false, style: { color: "#fff", weight: 1.5, dashArray: "4 4", fill: false }
        }).addTo(map); }).catch(() => null) : null;
    if (hasWater) waterGeo = await fetch(`/wflow_water?${qs}&v=${meta.version}`)
        .then((r) => r.ok ? r.json() : null).catch(() => null);
    if (opts.fitBounds !== false) map.fitBounds(g.bounds, { padding: [20, 20] });
    function project() {
        const sz = map.getSize(), dpr = window.devicePixelRatio || 1, tl = map.containerPointToLayerPoint([0, 0]);
        for (const c of [gridCv, riverCv, partCv]) {
            Lf.DomUtil.setPosition(c, tl); c.width = sz.x * dpr; c.height = sz.y * dpr;
            c.style.width = sz.x + "px"; c.style.height = sz.y + "px";
        }
        const [[s, w], [n, e]] = g.bounds;
        const a = map.latLngToContainerPoint([s, w]), b = map.latLngToContainerPoint([n, e]);
        P.dpr = dpr; P.ax = ((b.x - a.x) / (e - w)) * dpr; P.bx = a.x * dpr - P.ax * w;
        P.ay = ((b.y - a.y) / (merc(n) - merc(s))) * dpr; P.by = a.y * dpr - P.ay * merc(s);
        P.mPerPx = (M_PER_DEG * Math.cos(((s + n) / 2) * Math.PI / 180)) / Math.max(P.ax, 1e-9);
        pathsDirty = true;
    }
    const X = (lon) => P.ax * lon + P.bx, Y = (lat) => P.ay * merc(lat) + P.by;
    // ===================== PARTICLES =====================
    function buildGeoPath(gj) {
        const path = new Path2D();
        for (const f of (gj && gj.features) || []) {
            const gm = f.geometry; if (!gm) continue;
            const polys = gm.type === "Polygon" ? [gm.coordinates] : gm.type === "MultiPolygon" ? gm.coordinates : [];
            for (const poly of polys) for (const ring of poly) {
                ring.forEach(([lon, lat], i) => i ? path.lineTo(X(lon), Y(lat)) : path.moveTo(X(lon), Y(lat)));
                path.closePath();
            }
        }
        return path;
    }
    const decode = (buf, dtype) => {
        if (dtype === "<f2") { 
            const h = new Uint16Array(buf), o = new Float32Array(h.length); 
            for (let i = 0; i < h.length; i++) o[i] = F16[h[i]]; return o; 
        }
        return new Float32Array(buf);
    };
    const vinfo = meta.velocity, Nv = S.valid.length;
    const cellOf = new Int32Array(g.nx * g.ny).fill(-1);
    S.valid.forEach((k, j) => { cellOf[k] = j; });
    const gx2x = (gx) => X(g.lon0 + gx * g.dlon), gy2y = (gy) => Y(g.lat0 + gy * g.dlat);
    const vout = [0, 0];
    PT.on = !!vinfo && (obj.pOn ? obj.pOn.checked : true);
    if (obj.pSection) obj.pSection.toggleAttribute("data-unavailable", !vinfo);
    if (obj.pOn) obj.pOn.disabled = !vinfo;
    if (obj.pInfo) obj.pInfo.textContent = vinfo
        ? `Direction from ${vinfo.direction}, speed = Q / (depth × width). Median ${vinfo.speed_p50.toFixed(3)} m/s, max ≈ ${vinfo.speed_max.toFixed(2)} m/s.`
        : "No velocity field: the output needs overland_flow (and overland_depth) on the grid.";
    function loadVel(i) {
        i = Math.max(0, Math.min(tms.length - 1, i | 0));
        if (PT.frames.has(i)) return Promise.resolve(PT.frames.get(i));
        if (PT.pending.has(i)) return PT.pending.get(i);
        const p = fetch(`/wflow_velocity/${i}?${qs}&v=${meta.version}`)
            .then((r) => { if (!r.ok) throw new Error(`velocity ${i}: HTTP ${r.status}`); return r.arrayBuffer(); })
            .then((buf) => {
                const a = decode(buf, vinfo.dtype);
                const all = [], land = [];
                for (let j = 0; j < Nv; j++) {
                    if (Number.isFinite(a[j]) && (a[j] !== 0 || a[Nv + j] !== 0)) {
                        all.push(j);
                        if (S.mask[S.valid[j]] !== 2) land.push(j);
                    }
                }
                PT.frames.set(i, a);
                PT.wet.set(i, { all: Uint32Array.from(all), land: Uint32Array.from(land) });
                if (PT.frames.size > 48) {
                    const keep = Math.floor(S.t);
                    for (const k of PT.frames.keys()) if (Math.abs(k - keep) > 24) { PT.frames.delete(k); PT.wet.delete(k); }
                }
                return a;
            }).finally(() => PT.pending.delete(i));
        PT.pending.set(i, p); return p;
    }
    const blocked = (k) => cellOf[k] < 0 || (PT.stopInWater && S.mask[k] === 2);
    function sampleVel(fa, fb, w, gx, gy) {
        const ic = Math.round(gx), jc = Math.round(gy);
        if (ic < 0 || jc < 0 || ic >= g.nx || jc >= g.ny || blocked(jc * g.nx + ic)) return null;
        const i0 = Math.floor(gx), j0 = Math.floor(gy), fx = gx - i0, fy = gy - j0;
        let su = 0, sv = 0, sw = 0;
        for (let c = 0; c < 4; c++) {
            const i = i0 + (c & 1), j = j0 + (c >> 1);
            if (i < 0 || j < 0 || i >= g.nx || j >= g.ny) continue;
            const kk = j * g.nx + i; if (blocked(kk)) continue;
            const q = cellOf[kk]; const ua = fa[q], ub = fb[q];
            const wa = Number.isFinite(ua) ? 1 - w : 0, wb = Number.isFinite(ub) ? w : 0;
            if (wa + wb < 0.5) continue;
            const ws = ((c & 1) ? fx : 1 - fx) * ((c >> 1) ? fy : 1 - fy);
            const u = ((wa ? wa * ua : 0) + (wb ? wb * ub : 0)) / (wa + wb);
            const v = ((wa ? wa * fa[Nv + q] : 0) + (wb ? wb * fb[Nv + q] : 0)) / (wa + wb);
            su += ws * u; sv += ws * v; sw += ws;
        }
        if (sw < 0.15) return null;
        vout[0] = su / sw; vout[1] = sv / sw; return vout;
    }
    function respawn(k, fa, fb, w, wet) {
        if (!wet || !wet.length) { PT.age[k] = 0; return; }
        for (let tries = 0; tries < 8; tries++) {
            const q = S.valid[wet[(Math.random() * wet.length) | 0]]; if (blocked(q)) continue;
            const gx = (q % g.nx) + Math.random() - 0.5, gy = ((q / g.nx) | 0) + Math.random() - 0.5;
            if (sampleVel(fa, fb, w, gx, gy)) {
                PT.px[k] = gx; PT.py[k] = gy; PT.age[k] = 1; PT.life[k] = 40 + ((Math.random() * 90) | 0); return;
            }
        }
        PT.age[k] = 0;
    }
    function stepParticles(fa, fb, w, wet) {
        const vmax = vinfo.speed_max, res = g.res_m, n = PT.count;
        const pxLen = 1.5 * PT.speed * P.mPerPx, maxStep = MAX_STEP_PX * P.mPerPx;
        for (const b of PT.buckets) b.length = 0;
        for (let k = 0; k < n; k++) {
            if (PT.age[k] === 0 || PT.age[k] > PT.life[k]) { respawn(k, fa, fb, w, wet); continue; }
            const first = sampleVel(fa, fb, w, PT.px[k], PT.py[k]);
            if (!first) { PT.age[k] = 0; continue; }
            let u = first[0], v = first[1], sp = Math.hypot(u, v);
            const s0 = sp, Ltot = Math.min(pxLen * curveF(sp / vmax), maxStep);
            const nsub = Math.max(1, Math.min(60, Math.ceil(Ltot / (0.5 * res))));
            const x0 = gx2x(PT.px[k]), y0 = gy2y(PT.py[k]);
            let gx = PT.px[k], gy = PT.py[k], dead = false;
            for (let m = 0; m < nsub; m++) {
                if (m > 0) {
                    const vel = sampleVel(fa, fb, w, gx, gy);
                    if (!vel) { dead = true; break; }
                    u = vel[0]; v = vel[1]; sp = Math.hypot(u, v);
                }
                if (sp < 1e-9) break;
                const l = Math.min(pxLen * curveF(sp / vmax), maxStep) / nsub;
                gx += (u / sp) * l / res; gy += (v / sp) * l / res;
            }
            if (dead || !sampleVel(fa, fb, w, gx, gy)) { PT.age[k] = 0; continue; }
            PT.px[k] = gx; PT.py[k] = gy; PT.age[k]++;
            const b = Math.min(PBUCKETS - 1, (curveF(s0 / vmax) * PBUCKETS) | 0);
            PT.buckets[b].push(x0, y0, gx2x(gx), gy2y(gy));
        }
    }
    function drawParticles() {
        const W = partCv.width, H = partCv.height;
        pctx.globalCompositeOperation = "destination-in";
        pctx.fillStyle = `rgba(0,0,0,${PT.trail})`; pctx.fillRect(0, 0, W, H);
        pctx.globalCompositeOperation = "source-over";
        if (pathsDirty) {
            basinPath = basinGeo ? buildGeoPath(basinGeo) : null;
            waterPath = waterGeo ? buildGeoPath(waterGeo) : null;
            pathsDirty = false;
        }
        pctx.save(); if (basinPath) pctx.clip(basinPath, "evenodd");
        pctx.lineWidth = (PT.color === "white" ? 1.3 : 1.5) * P.dpr; pctx.lineCap = "round";
        const solid = PARTICLE_SOLID[PT.color];
        for (let b = 0; b < PBUCKETS; b++) {
            const seg = PT.buckets[b];
            if (!seg.length) continue;
            pctx.strokeStyle = solid
                ? `rgba(${solid[0]},${solid[1]},${solid[2]},${0.55 + 0.45 * (b / (PBUCKETS - 1))})`
                : PARTICLE_RAMP[b];
            pctx.beginPath();
            for (let q = 0; q < seg.length; q += 4) { 
                pctx.moveTo(seg[q], seg[q + 1]); pctx.lineTo(seg[q + 2], seg[q + 3]); 
            }
            pctx.stroke();
        }
        pctx.restore();
        if (PT.stopInWater && waterPath) {
            pctx.globalCompositeOperation = "destination-out";
            pctx.fillStyle = "#000"; pctx.fill(waterPath, "evenodd");
            pctx.globalCompositeOperation = "source-over";
        }
    }
    const clearParticles = () => pctx.clearRect(0, 0, partCv.width, partCv.height);
    function particleTick(tCur) {
        if (!vinfo || !PT.on || S.mode !== "animate") return;
        const T = tms.length, i0 = Math.max(0, Math.min(T - 1, Math.floor(tCur))), i1 = Math.min(i0 + 1, T - 1);
        const fa = PT.frames.get(i0), fb = PT.frames.get(i1);
        if (!fa || !fb) { loadVel(i0).catch(() => {}); loadVel(i1).catch(() => {}); return; }
        const wl = PT.wet.get(i0);
        stepParticles(fa, fb, tCur - i0, wl && (PT.stopInWater ? wl.land : wl.all)); drawParticles();
    }
    if (vinfo) { allocParticles(); loadVel(0).catch(() => {}); loadVel(1).catch(() => {}); }
    // ===================== PARTICLES =====================
    function loadFrame(name, i) {
        i = Math.round(Number(i)); const key = `${name}|${i}`;
        if (S.frames.has(key)) return Promise.resolve(S.frames.get(key));
        if (S.pending.has(key)) return S.pending.get(key);
        const p = fetch(`/wflow_frame/${name}/${i}?${qs}&v=${meta.version}`)
            .then((r) => { if (!r.ok) throw new Error(`frame ${key}: HTTP ${r.status}`); return r.arrayBuffer(); })
            .then((buf) => { const a = decode(buf, meta.variables[name].dtype); S.frames.set(key, a);
                if (S.frames.size > 80) { 
                    for (const k of S.frames.keys()) { if (S.frames.size <= 60) break; S.frames.delete(k); } 
                }
                return a; })
            .finally(() => S.pending.delete(key));
        S.pending.set(key, p); return p;
    }
    async function loadStat(name, mode) {
        const key = `${name}|${mode}|${mode === "above" ? S.threshold : ""}`;
        if (S.stats.has(key)) return S.stats.get(key);
        const extra = mode === "above" ? `&threshold=${S.threshold}` : "";
        const r = await fetch(`/wflow_stat/${name}/${mode}?${qs}${extra}&v=${meta.version}`);
        if (!r.ok) throw new Error((await r.json()).message);
        const out = { values: new Float32Array(await r.arrayBuffer()),
            min: +r.headers.get("X-Stat-Min") || 0, max: +r.headers.get("X-Stat-Max") || 1 };
        S.stats.set(key, out); return out;
    }
    async function valuesFor(name) {
        if (!name) return null;
        const info = meta.variables[name];
        if (S.mode === "animate") {
            const frameIndex = Math.floor(S.t);
            return { 
                values: await loadFrame(name, frameIndex), min: 0, max: info.p99, 
                ramp: info.ramp, units: info.units 
            };
        }
        const st = await loadStat(name, S.mode);
        const ramp = S.mode === "tmax" ? "time" : S.mode === "above" ? "hours" : info.ramp;
        const units = S.mode === "tmax" || S.mode === "above" ? "h" : S.mode === "sum" && info.flux ? info.units.replace("/Δt", "") : info.units;
        return { 
            ...st, min: S.mode === "tmax" ? 0 : st.min, 
            max: S.mode === "tmax" ? (tms.length - 1) * meta.step_hours : st.max, ramp, units 
        };
    }
    const useLog = (name) => S.scale === "log" || (S.scale === "auto" && S.mode !== "tmax" && S.mode !== "above" && meta.variables[name]?.log);
    const norm = (v, lo, hi, log) => {
        if (log) { const l = Math.max(hi / 1000, 1e-9); return v <= l ? 0 : Math.log(v / l) / Math.log(hi / l); }
        return (v - lo) / ((hi - lo) || 1);
    };
    function buildGrid(d) {
        const px = offImg.data; px.fill(0);
        if (!d) { offCtx.putImageData(offImg, 0, 0); return; }
        const lut = LUTS[d.ramp] || LUTS.depth, lg = useLog(S.gridVar), V = S.valid, nx = g.nx, ny = g.ny;
        const lo = d.min, hi = d.max, a = Math.round(255 * S.opacity);
        for (let j = 0; j < V.length; j++) {
            const val = d.values[j];
            if (!Number.isFinite(val) || (S.mode === "animate" && val <= 0)) continue;
            const k = V[j], row = (k / nx) | 0, col = k - row * nx, o = ((ny - 1 - row) * nx + col) * 4;
            const li = Math.max(0, Math.min(255, (norm(val, lo, hi, lg) * 255) | 0)) * 3;
            px[o] = lut[li]; px[o + 1] = lut[li + 1]; px[o + 2] = lut[li + 2]; px[o + 3] = a;
        }
        offCtx.putImageData(offImg, 0, 0);
    }
    function drawGrid() {
        gctx.clearRect(0, 0, gridCv.width, gridCv.height);
        if (!S.gridVar) return;
        const x0 = X(g.lon0 - g.dlon / 2), x1 = X(g.lon0 + (g.nx - 0.5) * g.dlon);
        gctx.imageSmoothingEnabled = false;
        for (let j = 0; j < g.ny; j += 8) {
            const h = Math.min(8, g.ny - j), yS = Y(g.lat0 + (j - 0.5) * g.dlat), yN = Y(g.lat0 + (j + h - 0.5) * g.dlat);
            gctx.drawImage(off, 0, g.ny - j - h, g.nx, h, x0, yN, x1 - x0, yS - yN);
        }
    }
    function drawRivers() {
        rctx.clearRect(0, 0, riverCv.width, riverCv.height);
        const d = S.riverValues;
        if (!S.riverVar || !d || !S.rivers) return;
        const lut = LUTS[d.ramp] || LUTS.flow, lg = useLog(S.riverVar), feats = S.rivers.features, dpr = P.dpr;
        const groups = new Map();
        for (let k = 0; k < feats.length; k++) {
            const val = d.values[k];
            if (!Number.isFinite(val)) continue;
            const f = Math.max(0, Math.min(1, norm(val, d.min, d.max, lg)));
            const wb = Math.min(5, (f * 6) | 0), cb = Math.min(7, (f * 8) | 0), key = wb * 8 + cb;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(feats[k].geometry.coordinates);
        }
        rctx.lineCap = "round";
        const animate = S.mode === "animate";
        for (const [key, segs] of groups) {
            const wb = (key / 8) | 0, cb = key % 8, li = Math.round((cb / 7) * 255) * 3;
            rctx.strokeStyle = `rgb(${lut[li]},${lut[li + 1]},${lut[li + 2]})`;
            rctx.lineWidth = (1.5 + wb * 1.2) * dpr;
            rctx.setLineDash(animate ? [7 * dpr, 6 * dpr] : []);
            rctx.lineDashOffset = -S.phase * dpr * (0.6 + wb * 0.3);
            rctx.beginPath();
            for (const [[x0, y0], [x1, y1]] of segs) { rctx.moveTo(X(x0), Y(y0)); rctx.lineTo(X(x1), Y(y1)); }
            rctx.stroke();
        }
    }
    function legend(box, d, name) {
        if (!box) return;
        if (!name || !d) { box.hidden = true; return; }
        box.hidden = false;
        const info = meta.variables[name], lg = useLog(name);
        const at = (f) => lg ? Math.max(d.max / 1000, 1e-9) * Math.pow(1000, f) : d.min + f * (d.max - d.min);
        const fmt = (x) => Math.abs(x) >= 100 ? x.toFixed(0) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toPrecision(2);
        box.innerHTML = `<div class="legend-title">${info.description} · ${MODES[S.mode]}${lg ? " · log" : ""}</div>
            <div class="ramp" style="background:linear-gradient(90deg,${RAMPS[d.ramp].join(",")})"></div>
            <div class="ticks"><span>${fmt(at(0))}</span><span>${fmt(at(0.5))}</span><span>≥ ${fmt(at(1))} ${d.units}</span></div>`;
    }
    let busy = false, again = false;
    async function refresh() {
        if (busy) { again = true; return; }
        busy = true;
        try {
            const [gd, rd] = await Promise.all([valuesFor(S.gridVar), valuesFor(S.riverVar)]);
            S.gridValues = gd; S.riverValues = rd;
            buildGrid(gd); drawGrid(); drawRivers();
            legend(obj.legendGrid, gd, S.gridVar); legend(obj.legendRiver, rd, S.riverVar);
            setStatus("");
        } catch (e) { setStatus(String(e.message || e), true); }
        finally { busy = false; if (again) { again = false; refresh(); } }
    }
    function showTime() {
        const i = Math.min(tms.length - 1, Math.max(0, Math.round(S.t)));
        const d = new Date(tms[i]), tz = resolveTimeZone();
        const partsLocal = new Intl.DateTimeFormat("en-GB", {
            timeZone: tz, hour: "2-digit", minute: "2-digit",
            day: "2-digit", month: "2-digit", year: "numeric", hourCycle: "h23",
        }).formatToParts(d);
        const pL = Object.fromEntries(partsLocal.map(({ type, value }) => [type, value]));
        const partsUTC = new Intl.DateTimeFormat("en-GB", {
            timeZone: "UTC", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
        }).formatToParts(d);
        const pU = Object.fromEntries(partsUTC.map(({ type, value }) => [type, value]));
        const tzName = new Intl.DateTimeFormat("en-GB", { timeZone: tz, timeZoneName: "short",
        }).formatToParts(d).find((x) => x.type === "timeZoneName")?.value || "UTC";
        const hm = `${pL.hour}:${pL.minute}`;
        const dmy = `${pL.day}/${pL.month}/${pL.year}`;
        const utcHM = `${pU.hour}:${pU.minute}`;
        if (obj.clock) obj.clock.textContent = obj.date ? hm : `${dmy} ${hm}`;
        if (obj.date) obj.date.textContent = dmy;
        if (obj.clockNote) obj.clockNote.textContent = `${tz} ${tzName} · ${utcHM} UTC`;
        if (obj.time && document.activeElement !== obj.time) obj.time.value = S.t;
        drawChartMarker(obj.chart, tms.length, S.t); drawFooterTrace();
    }
    let tf = 0;
    function loop(now) {
        const dt = Math.min(0.1, (now - S.last) / 1000); S.last = now;
        if (S.playing && S.mode === "animate") {
            const T = tms.length;
            const next = tf + S.rate * dt / meta.step_hours;
            const i = Math.floor(next >= T - 1 ? 0 : next);
            const ready = [S.gridVar, S.riverVar].filter(Boolean).every((n) => S.frames.has(`${n}|${i}`))
                && (!vinfo || !PT.on || (PT.frames.has(i) && PT.frames.has(Math.min(i + 1, T - 1))));
            if (ready) {
                tf = next >= T - 1 ? 0 : next;
                if (Math.floor(tf) !== Math.floor(S.t) || tf < S.t) {
                    S.t = tf; showTime(); refresh();
                }
            }
            if (obj.loading) obj.loading.hidden = ready;
            for (let k = 1; k <= 4; k++)
                for (const n of [S.gridVar, S.riverVar]) if (n) loadFrame(n, (i + k) % T).catch(() => {});
            if (vinfo && PT.on) for (let k = 0; k <= 6; k++) loadVel((i + k) % T).catch(() => {});
        }
        particleTick(S.playing ? tf : S.t);
        if (S.riverVar && S.mode === "animate") { S.phase += dt * 25; drawRivers(); }
        S.raf = requestAnimationFrame(loop);
    }
    function setPlaying(on) {
        S.playing = on && S.mode === "animate"; S.last = performance.now(); tf = S.t;
        const icon = obj.play?.querySelector("#wv-play-icon") || document.getElementById("wv-play-icon");
        if (icon) {
            icon.setAttribute("d", S.playing ? "M6 4h4v16H6zM14 4h4v16h-4z" : "M7 4l13 8-13 8z");
        } else if (obj.play) { obj.play.textContent = S.playing ? "Pause" : "Play"; }
        if (obj.play) {
            obj.play.setAttribute("aria-label", S.playing ? "Pause" : "Play");
            obj.play.setAttribute("aria-pressed", String(S.playing));
            obj.play.classList.toggle("is-playing", S.playing);
        }
        if (!S.playing && obj.loading) obj.loading.hidden = true;
    }
    function seek(i) { 
        S.t = Math.max(0, Math.min(tms.length - 1, +i));
        tf = S.t; showTime(); refresh(); drawFooterTrace();
    }
    async function probe(latlng) {
        const r = await fetch(`/wflow_point?${qs}&lat=${latlng.lat}&lon=${latlng.lng}`).then((x) => x.json());
        if (r.status === "error") { setStatus(r.message, true); return; }
        S.probe = r.content;
        // Remove footer chart
        S.traceVars?.clear(); if (obj.trace) obj.trace.innerHTML = "";
        obj.chart?.querySelectorAll("figure.series.selected").forEach((f) => f.classList.remove("selected"));
        if (S.probeLayer) map.removeLayer(S.probeLayer);
        S.probeLayer = Lf.layerGroup([Lf.circleMarker([S.probe.lat, S.probe.lon], { radius: 6, color: "#fff", weight: 2, fillColor: "#c07f22", fillOpacity: 1 })]);
        if (S.probe.river) {
            S.probeLayer.addLayer(
                Lf.circleMarker(
                    [S.probe.river.lat, S.probe.river.lon], 
                    { radius: 5, color: "#fff", weight: 2, fillColor: "#2c7da0", fillOpacity: 1 }
                )
            );
        }
        S.probeLayer.addTo(map); drawChart(obj, meta, S, tms);
    }
    function drawFooterTrace() {
        if (!obj.trace) return;
        const p = S.probe;
        const names = [...S.traceVars];
        if (!p || !names.length) { obj.trace.innerHTML = ""; return; }
        const T = tms.length, W = 1000, H = 100;
        const paths = names.map((name) => {
            const s = p.series[name];
            if (!s) return "";
            const info = meta.variables[name] || { kind: "" };
            const fin = s.filter((x) => x != null);
            if (!fin.length) return "";
            const lo = 0, hi = Math.max(...fin, 1e-9);
            const X = (i) => ((i / Math.max(1, T - 1)) * W).toFixed(1);
            const Y = (v) => (H - ((v ?? 0) / hi) * H * 0.9).toFixed(1);
            let d = "";
            s.forEach((v, i) => { if (v == null) return; d += `${d ? " L" : "M"}${X(i)} ${Y(v)}`; });
            const isDepth = /depth|storage|volume/i.test(name);
            const dd = isDepth ? `M0 ${H} L${X(0)} ${Y(s[0] ?? 0)}` + d.slice(1) + ` L${W} ${H} Z` : d;
            return `<path class="${isDepth ? "area" : "line"}" data-var="${esc(name)}" d="${dd}"/>`;
        }).join("");
        obj.trace.setAttribute("viewBox", `0 0 ${W} ${H}`); obj.trace.innerHTML = paths;
    }
    const on = (node, ev, fn) => node && node.addEventListener(ev, fn, { signal: ac.signal });
    on(obj.gridVar, "change", (e) => { 
        if (e.target.value == '' && obj.riverVar.value == '') { obj.legend.style.display = 'none'; }
        else { obj.legend.style.display = 'block'; }
        S.gridVar = e.target.value || null; syncChartVars(); refresh(); 
    });
    on(obj.riverVar, "change", (e) => { 
        if (e.target.value == '' && obj.gridVar.value == '') { obj.legend.style.display = 'none'; }
        else { obj.legend.style.display = 'block'; }
        S.riverVar = e.target.value || null; syncChartVars(); refresh(); 
    });
    on(obj.mode, "change", (e) => {
        S.mode = e.target.value; if (S.mode !== "animate") clearParticles();
        if (obj.thresholdRow) obj.thresholdRow.hidden = S.mode !== "above";
        if (S.mode !== "animate") setPlaying(false);
        if (S.mode === "above") { obj.thresholdRow.style.display = 'flex'; }
        else { obj.thresholdRow.style.display = 'none'; }
        refresh();
    });
    on(obj.threshold, "change", (e) => { 
        const v = parseFloat(String(e.target.value).replace(",", "."));
        if (!Number.isFinite(v) || v < 0) return;
        S.threshold = v;
        if (S.mode === "above") { refresh(); }
        else { alert("Threshold only applies when Show = “Hours above threshold”."); return; }
    });
    on(obj.scale, "change", (e) => { S.scale = e.target.value; refresh(); });
    const showOpacity = () => { 
        if (obj.opacityOut) obj.opacityOut.textContent = `${Math.round(S.opacity * 100)}%`; 
    };
    on(obj.opacity, "input", (e) => { S.opacity = +e.target.value; showOpacity(); if (S.gridValues) { buildGrid(S.gridValues); drawGrid(); } });
    if (obj.opacity) S.opacity = +obj.opacity.value; showOpacity();
    on(obj.back, "click", () => { setPlaying(false); seek(S.t - 1); });
    on(obj.fwd, "click", () => { setPlaying(false); seek(S.t + 1); });
    on(obj.collapse, "click", () => {
        const panel = document.getElementById("wv-panel");
        if (!panel) return;
        const collapsed = panel.classList.toggle("collapsed");
        obj.collapse.setAttribute("aria-expanded", String(!collapsed));
        obj.collapse.textContent = collapsed ? "+" : "–";
        obj.collapse.title = collapsed ? "Expand panel" : "Collapse panel";
        setTimeout(() => map.invalidateSize({ pan: false }), 0);
    });
    on(obj.close, "click", async() => {
        const flowPanel = document.getElementById('wflow-viewer');
        if (flowPanel) flowPanel.style.display = 'none';
        if (obj.clockNote) obj.clockNote.textContent = "";
        obj.showPanel.style.display = 'block';
        destroyMovement(setPlaying);
    });
    on(obj.chart, "click", (e) => {
        const fig = e.target.closest("figure.series"); if (!fig) return;
        const name = fig.dataset.var; if (!name) return;
        // Remove old chart
        obj.chart.querySelectorAll("figure.series.selected").forEach((f) => f.classList.remove("selected"));
        S.traceVars = new Set([name]); fig.classList.add("selected"); drawFooterTrace();
    });
    on(obj.download, "click", () => { downloadTimeSeries(obj.trace, S, meta, tms); });
    if (obj.play) {
        const fresh = obj.play.cloneNode(true);
        obj.play.replaceWith(fresh); obj.play = fresh;
        obj.play.addEventListener("click", () => setPlaying(!S.playing));
    }
    on(obj.time, "input", (e) => { 
        setPlaying(false); S.t = +e.target.value;
        tf = S.t; showTime(); refresh(); drawFooterTrace();
    });
    on(obj.rate, "change", (e) => { S.rate = +e.target.value; });
    // Start Particles options
    const srcCell = g.source_cell_m || 1;
    const showRes = () => {
        if (obj.res) obj.res.value = g.res_auto ? "auto" : String(g.res_m);
        if (obj.resHint) {
            obj.resHint.classList.remove("error");
            obj.resHint.textContent = `Current: ${g.res_m} m${g.res_auto ? " (auto)" : ""} · model cell ${srcCell} m. `
                + "Smaller values are sharper but slower to prepare.";
        }
    };
    showRes();
    on(obj.res, "keydown", async (e) => {
        if (e.key === "Escape") { showRes(); obj.res.blur(); return; }
        if (e.key !== "Enter") return;
        e.preventDefault();
        const raw = obj.res.value.trim().toLowerCase().replace(",", ".");
        let res = "auto";
        if (raw && raw !== "auto") {
            const n = Number(raw);
            if (!Number.isFinite(n) || n <= 0) {
                obj.resHint.textContent = "Enter a positive number in metres, or 'auto'.";
                obj.resHint.classList.add("error"); return;
            }
            if (n < srcCell) {
                obj.resHint.textContent = `Minimum is the model cell size (${srcCell} m).`;
                obj.resHint.classList.add("error"); obj.res.value = String(srcCell); return;
            }
            res = n;
        }
        const same = res === "auto" ? g.res_auto : (!g.res_auto && Math.abs(res - g.res_m) < 1e-9);
        if (same) { showRes(); return; }
        if (typeof res === "number" && res < g.res_m / 2 &&
            !confirm(`Rebuild viewer data at ${res} m? Data will be about ${Math.round((g.res_m / res) ** 2)}× larger and take longer to prepare.`)) {
            showRes(); return;
        }
        lastRes = res;
        await openScenario(map, flowName, { ...opts, res });
    });
    const showP = () => {
        if (obj.pCountOut) obj.pCountOut.textContent = PT.count.toLocaleString("en-US");
        if (obj.pSpeedOut) obj.pSpeedOut.textContent = `×${PT.speed.toFixed(1)}`;
        if (obj.pTrailOut) obj.pTrailOut.textContent = PT.trail.toFixed(2);
    };
    if (obj.pCount) PT.count = +obj.pCount.value;
    if (obj.pSpeed) PT.speed = +obj.pSpeed.value;
    if (obj.pTrail) PT.trail = +obj.pTrail.value;
    if (obj.pCurve) PT.curve = obj.pCurve.value;
    if (obj.pColor) PT.color = obj.pColor.value;
    showP(); if (vinfo) allocParticles();
    on(obj.pOn, "change", (e) => { PT.on = e.target.checked && !!vinfo; clearParticles(); });
    on(obj.pCount, "input", (e) => { PT.count = +e.target.value; allocParticles(); showP(); });
    on(obj.pSpeed, "input", (e) => { PT.speed = +e.target.value; showP(); });
    on(obj.pTrail, "input", (e) => { PT.trail = +e.target.value; showP(); });
    on(obj.pCurve, "change", (e) => { PT.curve = e.target.value; });
    on(obj.pColor, "change", (e) => { PT.color = e.target.value; clearParticles();});
    const waterOutside = !hasWater && !!meta.water; 
    PT.stopInWater = hasWater && (obj.pWater ? obj.pWater.checked : true);
    if (obj.pWater) {
        obj.pWater.disabled = !hasWater || !vinfo;
        obj.pWater.title = hasWater ? ""
            : waterOutside ? "The water area lies outside the model basin – particles already stop at the shoreline."
            : "No water area in this scenario.";
    }
    on(obj.pWater, "change", (e) => {
        PT.stopInWater = e.target.checked && hasWater;
        if (PT.age) PT.age.fill(0); clearParticles();
    });
    // End Particles options
    if (obj.thresholdRow) obj.thresholdRow.hidden = true;
    if (obj.mode && !obj.mode.options.length) {
        obj.mode.innerHTML = Object.entries(MODES).map(
            ([k, t]) => `<option value="${k}">${t}</option>`
        ).join("");
    }
    S.rate = obj.rate ? +obj.rate.value : 3;
    const handlers = [
        ["zoomstart", () => { 
            gridCv.style.opacity = riverCv.style.opacity= partCv.style.opacity = 0; 
        }], ["move", () => { project(); drawGrid(); drawRivers(); clearParticles(); }],
        ["moveend zoomend resize", () => { 
            project(); drawGrid(); drawRivers(); clearParticles();
            gridCv.style.opacity = riverCv.style.opacity = partCv.style.opacity = 1; 
        }], ["click", (e) => probe(e.latlng)],
    ];
    for (const [ev, fn] of handlers) map.on(ev, fn);
    syncChartVars();
    project(); showTime(); await refresh();
    S.raf = requestAnimationFrame(loop);
    setPlaying(opts.autoplay === true); 
    requestAnimationFrame(() => setPlaying(S.playing));
    return {
        meta, setGridVariable: (n) => { 
            S.gridVar = n; if (obj.gridVar) obj.gridVar.value = n ?? ""; refresh(); 
        },
        setRiverVariable: (n) => { 
            S.riverVar = n; if (obj.riverVar) obj.riverVar.value = n ?? ""; refresh(); 
        },
        setMode: (m) => { S.mode = m; if (obj.mode) obj.mode.value = m; refresh(); },
        seek, play: () => setPlaying(true), pause: () => setPlaying(false),
        destroy() {
            cancelAnimationFrame(S.raf); S.playing = false; S.last = performance.now(); ac.abort();
            for (const [ev, fn] of handlers) map.off(ev, fn);
            for (const c of [gridCv, riverCv, partCv]) c.remove();
            PT.frames.clear(); PT.pending.clear(); PT.wet.clear();
            if (outline) map.removeLayer(outline);
            if (S.probeLayer) map.removeLayer(S.probeLayer);
            if (obj.time) { obj.time.value = 0; obj.time.max = 1; }
            S.traceVars?.clear();
            const te = document.getElementById("wv-trace");
            if (te) te.innerHTML = "";
        },
    };
}

function destroyMovement(player) {
    player(false); cancelAnimationFrame(S.raf); S.raf = 0;
    viewer?.destroy(); viewer = null;
    if (obj.trace) obj.trace.innerHTML = "";
    if (obj.chart) obj.chart.innerHTML = "";
    if (obj.months) obj.months.innerHTML = "";
    if (obj.legendGrid) { obj.legendGrid.innerHTML = ""; obj.legendGrid.hidden = true; }
    if (obj.legendRiver) { obj.legendRiver.innerHTML = ""; obj.legendRiver.hidden = true; }
    if (obj.time) { obj.time.value = 0; obj.time.max = 1; }
    if (obj.clock) obj.clock.textContent = "--:--";
    if (obj.date) obj.date.textContent = "";
    if (obj.info) obj.info.textContent = "";
    if (obj.probeHint) obj.probeHint.textContent = "Click on a point inside the basin to view the time series.";
    if (obj.status) obj.status.textContent = "";
    resetState();
}

function groupsOf(variables, kind = null) {
    const out = new Map();
    for (const [k, i] of Object.entries(variables || {})) {
        if (kind && i.kind !== kind) continue;
        const gname = i.group || "Other";
        if (!out.has(gname)) out.set(gname, []);
        out.get(gname).push([k, i]);
    }
    return out;
}

function renderChartPicker(chartBox, groups) {
    if (!chartBox) return;
    chartBox.innerHTML = [...groups].map(([gname, list]) => `<fieldset><legend>${esc(gname)}</legend>` +
        list.map(([k, i]) => `<label><input type="checkbox" value="${esc(k)}" ${S.chartVars.has(k) ? "checked" : ""}>
            ${esc(i.description)}</label>`).join("") + `</fieldset>`).join("");
}

function drawChart(obj, meta, S, tms) {
    const box = obj.chart, p = S.probe;
    if (!box || !p) return;
    if (!p) {
        box.innerHTML = `<p class="hint">Click on the map to pick an observation point.</p>`; return;
    }
    const W = 300, H = 54, T = tms.length;
    const wanted = [...S.chartVars];
    if (!wanted.length) {
        box.innerHTML = `<p class="hint">Select variables to plot below.</p>`;
        drawChartMarker(box, T, S.t); return;
    }
    box.innerHTML = wanted.map((name) => {
        const info = meta.variables[name] || { description: name, units: "", kind: "" };
        const s = p.series[name];
        if (!s || !s.length) {
            return `<figure class="series missing" data-var="${esc(name)}">
                <figcaption>${esc(info.description)} <span>no data at this point</span></figcaption>
            </figure>`;
        }
        const fin = s.filter((x) => x != null);
        if (!fin.length) {
            return `<figure class="series missing" data-var="${esc(name)}">
                <figcaption>${esc(info.description)} <span>no finite values</span></figcaption>
            </figure>`;
        }
        const lo = Math.min(0, ...fin);
        const hi = Math.max(...fin, lo + 1e-9);
        let d = "", pen = false;
        s.forEach((y, i) => {
            if (y == null) { pen = false; return; }
            const x = ((i / Math.max(1, T - 1)) * W).toFixed(1);
            const yPix = (H - 4 - ((y - lo) / (hi - lo)) * (H - 8)).toFixed(1);
            d += `${pen ? "L" : "M"}${x} ${yPix}`;
            pen = true;
        });
        const selected = S.traceVars?.has(name) ? " selected" : "";
        return `<figure class="series${selected}" data-var="${esc(name)}">
            <figcaption>${esc(info.description)} <span>max ${hi.toPrecision(3)} ${esc(info.units || "")}</span></figcaption>
            <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(info.description)} time series">
                <path class="line ${info.kind}" d="${d}"/>
                <line class="now" y1="0" y2="${H}"/>
            </svg>
        </figure>`;
    }).join("");
    if (obj.probeHint) {
        obj.probeHint.textContent =
            `Cell at (${p.lat.toFixed(5)}, ${p.lon.toFixed(5)})` +
            (p.river ? " · nearest river cell marked in blue" : "") + ".";
    }
    drawChartMarker(box, T, S.t);
}

function drawChartMarker(chart, time, t) {
    if (!chart) return;
    const x = (t / Math.max(1, time - 1)) * 300;
    chart.querySelectorAll(".now").forEach((l) => { l.setAttribute("x1", x); l.setAttribute("x2", x); });
}

function downloadTimeSeries(trace, S, meta, tms) {
    if (!trace || !S.probe?.series) {
        alert("No time-series data available. Please select a chart on the panel first."); return;
    }
    const plottedNames = [
        ...new Set(
            [...trace.querySelectorAll("path[data-var]")].map(path => path.dataset.var).filter(Boolean)
        )
    ];
    const plottedSeries = plottedNames.map(name => {
        const values = S.probe.series[name];
        if (!Array.isArray(values)) return null;
        const info = meta.variables[name] || {};
        return {
            name, label: info.description || name, units: info.units || "", values
        };
    }).filter(Boolean);
    if (plottedSeries.length === 0) {
        alert("No time-series data is currently plotted."); return;
    }
    const escapeCSV = value => {
        const text = String(value ?? ""); return `${text.replace(/"/g, '""')}`;
    };
    const headers = [
        "Time(UTC)", ...plottedSeries.map(s => s.units ? `${s.label} (${s.units})` : s.label)
    ];
    const rows = [headers];
    const rowCount = Math.max(tms.length, ...plottedSeries.map(s => s.values.length));
    for (let i = 0; i < rowCount; i++) {
        const time = meta.times?.[i] ?? (tms[i] != null ? new Date(tms[i]).toISOString() : "");
        const row = [
            time.replace("T", " "), ...plottedSeries.map(s => {
                const value = s.values[i];
                return value != null && Number.isFinite(Number(value)) ? value : "";
            })
        ]; rows.push(row);
    }
    const csv = rows.map(row => row.map(escapeCSV).join(",")).join("\r\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;link.download = "wflow_time_series.csv";
    document.body.appendChild(link);
    link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function mapOptions(map) {
    const mapElement = map.getContainer();
    const mapContainer = mapElement.parentElement;
    if (map._resizeObserver) {map._resizeObserver.disconnect();}
    map._resizeObserver = new ResizeObserver(() => {
        if (mapContainer.clientWidth > 0 && mapContainer.clientHeight > 0) {
            map.invalidateSize({ pan: false });
        }
    });
    map._resizeObserver.observe(mapContainer); 
    requestAnimationFrame(() => {
        map.invalidateSize({ pan: false }); map.setView(CENTER, ZOOM, { animate: false });
    });
}
