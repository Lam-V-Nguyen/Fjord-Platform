import { jsonLoader, signalSender } from "./commonFunctions.js";
import { L } from "./constant.js";


const $ = (id) => document.getElementById(id);
const LOG_K = 100, MAX_STEP_PX = 40;

const off = document.createElement("canvas");
let offCtx, offImg, tms = [], px, py, age, life, rasterCv, partCv, rctx, pctx;
let last = performance.now(), lastRaster = 0, moving = false, rasterKey = "";
let rafId = 0, outlineLayer = null, mapHandlers = null, keyHandler = null, session = 0;

const pad = (n) => String(n).padStart(2, "0");
const S = {
    meta: null, mask: null, N: 0, ND: -32768, valid: null,
    frames: new Map(), pending: new Map(),
    t: 0, playing: false, rate: 3, raster: "speed", pcolor: "white",
    count: 5000, pspeed: 1, trail: 0.93, probe: null, probeMarker: null,
    motion: "always", snapshot: 0, boost: 1, curve: "sqrt",
};
const clearParticles = (ctx) => ctx && ctx.clearRect(0, 0, partCv.width, partCv.height);
const RAMPS = {
    speed: ["#0b2f4f", "#1b5f8a", "#2a9a9a", "#79c26a", "#e6d34e", "#f08a2b"],
    depth: ["#d7eef5", "#9fd0e3", "#5fa8cf", "#2f7bb6", "#16509a", "#0b2a6b"],
    particle: ["#e8f6ff", "#bfe9ff", "#d9f59a", "#ffe066", "#ffab40", "#ff6b3d"],
};
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const P = { ax: 0, bx: 0, ay: 0, by: 0, dpr: 1, mPerPx: 1 };
const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const gx2x = (gx) => P.ax * (S.meta.lon0 + gx * S.meta.dlon) + P.bx;
const gy2y = (gy) => P.ay * merc(S.meta.lat0 + gy * S.meta.dlat) + P.by;
const BUCKETS = 6;
const bucketPaths = Array.from({ length: BUCKETS }, () => []);
const out = [0, 0];
const LUT = { speed: makeLUT(RAMPS.speed), depth: makeLUT(RAMPS.depth) };
function makeLUT(stops) {
    const c = stops.map(hex), lut = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i++) {
        const t = (i / 255) * (c.length - 1), k = Math.min(Math.floor(t), c.length - 2), f = t - k;
        for (let ch = 0; ch < 3; ch++) lut[i * 3 + ch] = c[k][ch] + (c[k + 1][ch] - c[k][ch]) * f;
    }
    return lut;
}

function parseTime(s) {
    const m = s.match(/(\d+)-(\d+)-(\d+)[T ](\d+):(\d+)(?::(\d+))?/);
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
}
function prefetch(name, i0) {
    const T = S.meta.frame_count;
    const ahead = Math.max(6, Math.min(30, Math.ceil(S.rate * 1.2) + 2));
    for (let k = 0; k < ahead; k++) loadFrame(name, (i0 + k) % T).catch(() => {});
}
function mkCanvas(cls, z, map) {
    const pane = map.getPane(cls) || map.createPane(cls);
    pane.style.zIndex = z; pane.style.pointerEvents = "none";
    pane.innerHTML = "";
    return L.DomUtil.create("canvas", "flow-canvas " + cls, pane);
}

function readRes(obj) {
    const v = (obj.resObj?.value || "").trim().toLowerCase();
    if (!v || v === "auto") return "auto";
    const n = Number(v.replace(",", "."));
    return Number.isFinite(n) && n > 0 ? n : "auto";
}
function curveF(x) {
    x = Math.max(0, Math.min(x, 1.5));
    if (S.curve === "linear") return x;
    if (S.curve === "log") return Math.log1p(LOG_K * x) / Math.log1p(LOG_K);
    return Math.sqrt(x);
}
function curveInv(y) {
    if (S.curve === "linear") return y;
    if (S.curve === "log") return Math.expm1(y * Math.log1p(LOG_K)) / LOG_K;
    return y * y;
}

export function destroyMovement(map) {
    cancelAnimationFrame(rafId); rafId = 0; session++;
    if (mapHandlers && map) for (const [ev, fn] of mapHandlers) map.off(ev, fn);
    mapHandlers = null;
    if (keyHandler) document.removeEventListener("keydown", keyHandler);
    keyHandler = null;
    if (outlineLayer && map) map.removeLayer(outlineLayer);
    outlineLayer = null;
    if (S.probeMarker && map) map.removeLayer(S.probeMarker);
    S.probeMarker = null; S.probe = null;
    for (const c of [rasterCv, partCv]) if (c) c.remove();
    rasterCv = partCv = null;
    S.frames.clear(); S.pending.clear(); S.meta = null; S.t = 0;
}

export async function initMovement(projectName, layer, map, object, timeZone, options = {}) {
    destroyMovement(map); const mySession = session;
    signalSender("showOverlay", "Initiating database. Please wait...");
    const body = { projectName, res: readRes(object), layer: layer || "mean",
        mapPath: options.mapPath, rebuild: !!options.rebuild };
    let response;
    try { response = await jsonLoader("init_movement", body); }
    finally { signalSender("hideOverlay"); }
    if (mySession !== session) return;
    if (!response || response.status === "error") {
        const message = response?.message || "Cannot download data. Check meta.json, frames.npy, mask.npy.";
        object.modelName.innerHTML = message; alert(message); return;
    }
    const content = response.content;
    const data = { ...content.data, frame_count: Number(content.frame_count) };
    if (!Number.isInteger(data.frame_count) || data.frame_count < 2) {
        object.modelName.innerHTML = `Number of frames are invalid: ${data.frame_count}`; return;
    }
    S.meta = data; S.N = data.nx * data.ny; S.ND = data.nodata;
    tms = data.times.map(parseTime);
    const res1 = await fetch(`/mask_movement?projectName=${encodeURIComponent(projectName)}`);
    if (!res1.ok) { object.modelName.innerHTML = "Cannot get mask data."; return; }
    S.mask = new Uint8Array(await res1.arrayBuffer());
    const v = []; S.mask.forEach((x, k) => { if (x) v.push(k); }); S.valid = Uint32Array.from(v);
    if (data.format === 2 && S.valid.length !== data.n_valid) {
        object.modelName.innerHTML = "mask.npy and meta.json do not match. Rebuild the data."; return;
    }
    off.width = data.nx; off.height = data.ny;
    offCtx = off.getContext("2d"); offImg = offCtx.createImageData(data.nx, data.ny);
    object.modelName.innerHTML = `
        <div class="model-name">${data.name}</div>
        <div class="model-info">
            ${data.res_m} m · ${data.n_valid?.toLocaleString("en-US") ?? ""} cells
        </div>
    `;
    document.title = `Flow – ${data.name}`; object.timeObj.max = data.frame_count - 1;
    if (object.resObj) { object.resObj.value = `${data.res_m}`; object.resObj.title = data.res_auto ? "auto" : ""; }
    outlineLayer = L.geoJSON(data.polygon, {
        style: { color: "#ffffff", weight: 1.5, opacity: 0.8, fill: false, dashArray: "4 4" }, interactive: false,
    }).addTo(map);
    map.fitBounds(data.bounds, { padding: [20, 20] });
    rasterCv = mkCanvas("raster", 380, map); partCv = mkCanvas("particles", 390, map);
    rctx = rasterCv.getContext("2d"); pctx = partCv.getContext("2d");
    buildMonths(object); bindUI(projectName, object, map, layer, timeZone); updateLegend(object); updateProjection(map);
    allocParticles();
    await Promise.all([loadFrame(projectName, 0), loadFrame(projectName, 1)]);
    if (mySession !== session) return;
    showTime(object, timeZone); setPlaying(object, false); last = performance.now();
    rafId = requestAnimationFrame((now) => frame(projectName, object, now, mySession));
}

function buildMonths(obj) {
    const box = obj.monthObj, T = tms.length, t0 = tms[0], t1 = tms[T - 1];
    box.innerHTML = "";
    const d = new Date(t0), names = (m) => `month ${m + 1}`;
    let cur = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
    while (cur <= t1) {
        if (cur >= t0) {
            const s = document.createElement("span"), dd = new Date(cur);
            s.style.left = `${((cur - t0) / (t1 - t0)) * 100}%`;
            s.innerHTML = `${names(dd.getUTCMonth())}/${dd.getUTCFullYear()}`;
            box.appendChild(s);
        }
        const n = new Date(cur); cur = Date.UTC(n.getUTCFullYear(), n.getUTCMonth() + 1, 1);
    }
    if (!box.children.length) {
        const s = document.createElement("span"); s.style.left = "0";
        const dd = new Date(t0); s.innerHTML = `${pad(dd.getUTCDate())}/${pad(dd.getUTCMonth() + 1)}`; box.appendChild(s);
    }
}

function bindUI(name, obj, map, layer, timeZone) {
    obj.playObj.onclick = () => setPlaying(obj, !S.playing, timeZone);
    obj.backObj.onclick = () => seek(obj, Math.round(S.t) - 1, timeZone);
    obj.fwdObj.onclick = () => seek(obj, Math.round(S.t) + 1, timeZone);
    obj.rateObj.onchange = (e) => { S.rate = +e.target.value; };
    S.rate = +obj.rateObj.value || S.rate;
    const tr = obj.timeObj;
    tr.oninput = () => { setPlaying(obj, false); seek(obj, +tr.value, timeZone); };
    const fmt = { count: (v) => v.toLocaleString("vi-VN"), pspeed: (v) => `×${(+v).toFixed(1)}`,
        trail: (v) => (v < 0.9 ? "short" : v < 0.95 ? "mid" : "long") };
    for (const [id, outId] of [["count", "countOut"], ["pspeed", "speedOut"], ["trail", "trailOut"]]) {
        const el = $(id), upd = () => {
            S[id] = +el.value; $(outId).innerHTML = fmt[id](S[id]);
            if (id === "count") { allocParticles(); clearParticles(pctx); S.snapshot = 30; }
        };
        el.oninput = upd; upd();
    }
    bindSeg("raster", (v) => { S.raster = v; rasterKey = ""; updateLegend(obj); if (v === "none") drawRaster(); });
    bindSeg("pcolor", (v) => { S.pcolor = v; S.snapshot = 30; });
    document.querySelector('.seg[data-key="raster"] button[data-v="speed"]')?.click();
    if (obj.motionObj) {
        S.motion = obj.motionObj.value || "always";
        obj.motionObj.onchange = (e) => {
            S.motion = e.target.value; age.fill(0); clearParticles(pctx); S.snapshot = 30;
        };
    }
    if (obj.resObj) {
        obj.resObj.onchange = () => {
            if (!S.meta) return;
            const res = readRes(obj);
            if (res !== "auto" && Math.abs(res - S.meta.res_m) < 1e-6) return;
            if (res === "auto" && S.meta.res_auto) return;
            initMovement(name, layer, map, obj, timeZone);
        };
        obj.resObj.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); obj.resObj.blur(); } };
    }
    if (obj.curveObj) {
        S.curve = obj.curveObj.value || "sqrt";
        obj.curveObj.onchange = (e) => {
            S.curve = e.target.value; rasterKey = ""; updateLegend(obj); clearParticles(pctx); S.snapshot = 30;
        };
    }
    obj.collapseObj.onclick = () => {
        const p = obj.panelObj, c = p.classList.toggle("collapsed");
        obj.collapseObj.setAttribute("aria-expanded", String(!c)); 
        obj.collapseObj.innerHTML = c ? "+" : "–"; obj.collapseObj.title = c ? "Expand panel" : "Collapse panel";
    };
    obj.closeObj.onclick = async () => {
        const flowPanel = document.getElementById('velocity-container');
        if (flowPanel) flowPanel.style.display = 'none';
        destroyMovement(map); await jsonLoader('close_movement', {projectName: name});
    };
    obj.downloadObj.onclick = () => { downloadTimeSeries(obj.traceObj, S, S.meta, tms); };
    keyHandler = (e) => {
        if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) && e.target.type !== "range") return;
        if (e.code === "Space") { e.preventDefault(); setPlaying(obj, !S.playing); }
        if (e.key === "ArrowRight" && e.target.id !== "time") seek(obj, Math.round(S.t) + 1, timeZone);
        if (e.key === "ArrowLeft" && e.target.id !== "time") seek(obj, Math.round(S.t) - 1, timeZone);
    };
    document.addEventListener("keydown", keyHandler);
    mapHandlers = [
        ["movestart zoomstart", () => { moving = true; clearParticles(pctx); }],
        ["zoomstart", () => { rasterCv.classList.add("hidden"); }],
        ["move", () => { updateProjection(map); drawRaster(); }],
        ["moveend zoomend resize", () => {
            updateProjection(map); drawRaster(); rasterCv.classList.remove("hidden");
            moving = false; clearParticles(pctx); S.snapshot = 30;
        }],
        ["click", (e) => probeAt(name, obj, e.latlng, map)],
    ];
    for (const [ev, fn] of mapHandlers) map.on(ev, fn);
}

function setPlaying(obj, on) {
    S.playing = on;
    obj.playIcon.setAttribute("d", on ? "M6 4h4v16H6zM14 4h4v16h-4z" : "M7 4l13 8-13 8z");
    obj.playObj.setAttribute("aria-label", on ? "Pause" : "Play");
    if (!on) S.snapshot = 30;
}
function seek(obj, t, timeZone) {
    const tt = S.meta.frame_count;
    S.t = Math.max(0, Math.min(tt - 1, t));
    clearParticles(pctx); S.snapshot = 30;
    if (S.motion !== "always" && age) age.fill(0);
    showTime(obj, timeZone);
}
function allocParticles() {
    const n = S.count;
    px = new Float32Array(n); py = new Float32Array(n);
    age = new Uint16Array(n); life = new Uint16Array(n);
}
function bindSeg(key, onChange) {
    const g = document.querySelector(`.seg[data-key="${key}"]`);
    g.onclick = (e) => {
        const b = e.target.closest("button"); if (!b) return;
        g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", x === b ? "true" : "false"));
        onChange(b.dataset.v);
    };
}
function updateLegend(obj) {
    const leg = obj.legendObj;
    leg.classList.toggle("off", S.raster === "none");
    if (S.raster === "none") return;
    const vmax = S.raster === "speed" ? S.meta.speed_max : S.meta.depth_max;
    const unit = S.raster === "speed" ? "m/s" : "m";
    obj.rampObj.style.background = `linear-gradient(90deg, ${RAMPS[S.raster].join(",")})`;
    obj.legendMinObj.innerHTML = "0";
    const mid = S.raster === "speed" ? curveInv(0.5) * vmax : vmax / 2;
    obj.legendMidObj.innerHTML = mid.toFixed(mid < 0.1 ? 3 : vmax < 2 ? 2 : 1);
    obj.legendMaxObj.innerHTML = `≥ ${vmax.toFixed(vmax < 2 ? 2 : 1)} ${unit}`;
}
function drawRaster() {
    if (!rasterCv || !S.meta) return;
    rctx.clearRect(0, 0, rasterCv.width, rasterCv.height);
    if (S.raster === "none") return;
    const { nx, ny } = S.meta, x0 = gx2x(-0.5), x1 = gx2x(nx - 0.5);
    rctx.imageSmoothingEnabled = true;
    for (let j = 0; j < ny; j += 8) {
        const h = Math.min(8, ny - j), yS = gy2y(j - 0.5), yN = gy2y(j + h - 0.5);
        rctx.drawImage(off, 0, ny - j - h, nx, h, x0, yN, x1 - x0, yS - yN);
    }
}
function updateProjection(map) {
    if (!rasterCv || !S.meta) return;
    const m = S.meta, sz = map.getSize(), dpr = window.devicePixelRatio || 1;
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    for (const c of [rasterCv, partCv]) {
        L.DomUtil.setPosition(c, topLeft);
        c.width = sz.x * dpr; c.height = sz.y * dpr;
        c.style.width = sz.x + "px"; c.style.height = sz.y + "px";
    }
    const [[s, w], [n, e]] = m.bounds;
    const a = map.latLngToContainerPoint([s, w]), b = map.latLngToContainerPoint([n, e]);
    P.dpr = dpr; P.ax = ((b.x - a.x) / (e - w)) * dpr; P.bx = a.x * dpr - P.ax * w;
    P.ay = ((b.y - a.y) / (merc(n) - merc(s))) * dpr; P.by = a.y * dpr - P.ay * merc(s);
    const latMid = (s + n) / 2;
    P.mPerPx = ((1 / P.ax) * dpr) * 111320 * Math.cos((latMid * Math.PI) / 180);
}
async function probeAt(name, obj, latlng, map) {
    try {
        const response = await fetch(
            `/point_movement?projectName=${encodeURIComponent(name)}&lat=${latlng.lat}&lon=${latlng.lng}`);
        const result = await response.json();
        if (result.status === "error") {
            obj.probeHintObj.innerHTML = "Point is outside of the area. Select another point inside the boundary.";
            obj.probeHintObj.hidden = false; return;
        }
        S.probe = result;
        if (S.probeMarker) S.probeMarker.setLatLng([S.probe.lat, S.probe.lon]);
        else S.probeMarker = L.circleMarker([S.probe.lat, S.probe.lon],
            { radius: 6, color: "#fff", weight: 2, fillColor: "#c07f22", fillOpacity: 1 }).addTo(map);
        obj.probeHintObj.innerHTML = `Coordinate: (${S.probe.lat.toFixed(5)}, ${S.probe.lon.toFixed(5)}).<br>Green line: Depth - Yellow line: Velocity.`;
        obj.readoutObj.hidden = false; drawTrace(obj); updateReadout(obj);
    } catch (e) { alert(e); }
}
function drawTrace(obj) {
    const svg = obj.traceObj, p = S.probe;
    if (!p) { svg.innerHTML = ""; return; }
    const T = p.speed.length, W = 1000, H = 100;
    const dmax = Math.max(...p.depth.map((v) => v ?? 0), 0.01);
    const smax = Math.max(...p.speed.map((v) => v ?? 0), 0.01);
    const X = (i) => ((i / (T - 1)) * W).toFixed(1);
    let dPath = `M0 ${H}`, sPath = "";
    p.depth.forEach((v, i) => { dPath += ` L${X(i)} ${(H - ((v ?? 0) / dmax) * H * 0.9).toFixed(1)}`; });
    dPath += ` L${W} ${H} Z`;
    p.speed.forEach((v, i) => { sPath += `${i ? " L" : "M"}${X(i)} ${(H - ((v ?? 0) / smax) * H * 0.9).toFixed(1)}`; });
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = `<path class="depth" data-var="depth" d="${dPath}"/><path class="speed" data-var="speed" d="${sPath}"/>`;
}
function updateReadout(obj) {
    if (!S.probe) return;
    const i = Math.round(S.t), p = S.probe;
    const s = p.speed[i], d = p.depth[i], dir = p.direction[i];
    obj.rSpeedObj.innerHTML = s == null ? "dry" : `${s.toFixed(2)} m/s`;
    obj.rDirObj.innerHTML = s == null ? "–" : `${compass(dir)} ${dir.toFixed(0)}°`;
    obj.rDepthObj.innerHTML = d == null ? "dry" : `${d.toFixed(2)} m`;
}
function compass(deg) {
    const names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
    return names[Math.round(deg / 22.5) % 16];
}

function expandFrame(compact) {
    const N = S.N, V = S.valid, nv = V.length, ND = S.ND;
    if (compact.length === 3 * N) return compact;            // dữ liệu cũ (format 1)
    if (compact.length !== 3 * nv) throw new Error(`Frame size ${compact.length} ≠ 3 × ${nv}`);
    const full = new Int16Array(3 * N).fill(ND);
    for (let c = 0; c < 3; c++) {
        const o = c * N, oc = c * nv;
        for (let j = 0; j < nv; j++) full[o + V[j]] = compact[oc + j];
    }
    return full;
}
async function loadFrame(projectName, i) {
    if (!Number.isInteger(i) || i < 0) throw new Error(`Frame index is not valid: ${i}`);
    if (S.frames.has(i)) return S.frames.get(i);
    if (S.pending.has(i)) return S.pending.get(i);
    const mySession = session;
    const p = (async () => {
        try {
            const ver = encodeURIComponent(S.meta?.version ?? "");
            const res = await fetch(`/frame/${i}?projectName=${encodeURIComponent(projectName)}&v=${ver}`);
            if (!res.ok) throw new Error(`Frame ${i}: HTTP ${res.status} ${await res.text()}`);
            const buf = await res.arrayBuffer();
            if (buf.byteLength % 2 !== 0) throw new Error(`Frame ${i}: invalid size (${buf.byteLength} bytes)`);
            if (mySession !== session) return null;
            const arr = expandFrame(new Int16Array(buf));
            S.frames.set(i, arr);
            if (S.frames.size > 60) {
                const keep = Math.floor(S.t);
                for (const k of S.frames.keys()) if (Math.abs(k - keep) > 30) S.frames.delete(k);
            }
            return arr;
        } catch (err) { console.error(`Cannot load frame ${i}:`, err); throw err; }
        finally { if (S.pending.get(i) === p) S.pending.delete(i); }
    })();
    S.pending.set(i, p); return p;
}
function timeAt(t) {
    const i = Math.floor(t), T = tms.length;
    if (i >= T - 1) return tms[T - 1];
    return tms[i] + (tms[i + 1] - tms[i]) * (t - i);
}
function showTime(obj, timeZone) {
    const d = new Date(timeAt(S.t));
    const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: timeZone, hour: "2-digit", minute: "2-digit",
        day: "2-digit", month: "2-digit", year: "numeric", hourCycle: "h23"
    }).formatToParts(d);
    const p = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
    obj.clockObj.textContent = `${p.hour}:${p.minute}`;
    obj.dateObj.textContent = `${p.day}/${p.month}/${p.year}`;
    if (document.activeElement !== obj.timeObj) obj.timeObj.value = S.t;
    updateReadout(obj);
}

function frame(name, obj, now, mySession, timeZone) {
    if (mySession !== session) return;
    const dtReal = Math.min(0.1, (now - last) / 1000); last = now;
    const T = S.meta.frame_count;
    const i0 = Math.floor(S.t), i1 = Math.min(i0 + 1, T - 1);
    const fa = S.frames.get(i0), fb = S.frames.get(i1);
    obj.loadingObj.hidden = !!(fa && fb);
    if (fa && fb) {
        const w = S.t - i0;
        if (!moving) {
            if (S.playing || S.motion === "always") {
                const dtSim = S.motion === "physical" && S.playing ? S.rate * 3600 * dtReal : null;
                stepParticles(fa, fb, w, dtSim); drawParticles();
            } else if (S.snapshot > 0) {
                for (let k = 0; k < S.snapshot; k++) { stepParticles(fa, fb, w, null); drawParticles(); }
                S.snapshot = 0;
            }
        }
        const key = `${S.raster}|${S.t.toFixed(2)}`;
        if (S.raster !== "none" && key !== rasterKey && now - lastRaster > 120) {
            buildRaster(fa, fb, w); drawRaster(); rasterKey = key; lastRaster = now;
        }
        if (S.playing) {
            const tn = S.t + S.rate * dtReal, j = Math.floor(tn >= T - 1 ? 0 : tn);
            if (S.frames.has(j) && S.frames.has(Math.min(j + 1, T - 1))) {
                S.t = tn >= T - 1 ? 0 : tn; showTime(obj, timeZone);
            }
        }
    }
    prefetch(name, Math.floor(S.t));
    rafId = requestAnimationFrame((t) => frame(name, obj, t, mySession, timeZone));
}

function stepParticles(fa, fb, w, dtSim) {
    const n = S.count, vmax = S.meta.speed_max, res = S.meta.res_m;
    const physical = dtSim != null;
    const pxLen = 1.5 * S.pspeed * S.boost * P.mPerPx;
    const maxStep = MAX_STEP_PX * P.mPerPx;
    const tSim = physical ? dtSim * S.boost : 0;
    for (const b of bucketPaths) b.length = 0;
    for (let i = 0; i < n; i++) {
        if (age[i] === 0 || age[i] > life[i]) { respawn(i, fa, fb, w); continue; }
        const first = sample(fa, fb, w, px[i], py[i]);
        if (!first) { age[i] = 0; continue; }
        let u = first[0], v = first[1], s = Math.hypot(u, v);
        const s0 = s;
        let Ltot = physical ? s * tSim : pxLen * curveF(s / vmax);
        Ltot = Math.min(Ltot, maxStep);
        const nsub = Math.max(1, Math.min(60, Math.ceil(Ltot / (0.5 * res))));
        const x0 = gx2x(px[i]), y0 = gy2y(py[i]);
        let gx = px[i], gy = py[i], dead = false;
        for (let k = 0; k < nsub; k++) {
            if (k > 0) {
                const vel = sample(fa, fb, w, gx, gy);
                if (!vel) { dead = true; break; }
                u = vel[0]; v = vel[1]; s = Math.hypot(u, v);
            }
            if (s < 1e-9) break;
            const l = physical ? Math.min(s * tSim, maxStep) / nsub
                               : Math.min(pxLen * curveF(s / vmax), maxStep) / nsub;
            gx += (u / s) * l / res; gy += (v / s) * l / res;
        }
        if (dead || !sample(fa, fb, w, gx, gy)) { age[i] = 0; continue; }
        px[i] = gx; py[i] = gy; age[i]++;
        const x1 = gx2x(gx), y1 = gy2y(gy);
        const b = Math.min(BUCKETS - 1, (curveF(s0 / vmax) * BUCKETS) | 0);
        bucketPaths[b].push(x0, y0, x1, y1);
    }
}

function drawParticles() {
    const ctx = pctx, W = partCv.width, H = partCv.height;
    ctx.globalCompositeOperation = "destination-in";
    ctx.fillStyle = `rgba(0,0,0,${S.trail})`;
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = "source-over";
    ctx.lineWidth = 1.3 * P.dpr; ctx.lineCap = "round";
    for (let b = 0; b < BUCKETS; b++) {
        const seg = bucketPaths[b];
        if (!seg.length) continue;
        ctx.strokeStyle = S.pcolor === "white"
            ? `rgba(255,255,255,${0.55 + 0.45 * (b / (BUCKETS - 1))})` : RAMPS.particle[b];
        ctx.beginPath();
        for (let k = 0; k < seg.length; k += 4) { ctx.moveTo(seg[k], seg[k + 1]); ctx.lineTo(seg[k + 2], seg[k + 3]); }
        ctx.stroke();
    }
}

function respawn(i, fa, fb, w) {
    const nx = S.meta.nx, V = S.valid;
    for (let tries = 0; tries < 12; tries++) {
        const k = V[(Math.random() * V.length) | 0];
        const gx = (k % nx) + Math.random() - 0.5, gy = ((k / nx) | 0) + Math.random() - 0.5;
        if (sample(fa, fb, w, gx, gy)) {
            px[i] = gx; py[i] = gy; age[i] = 1;
            life[i] = (S.motion === "physical" ? 400 : 40) + ((Math.random() * 90) | 0);
            return true;
        }
    }
    age[i] = 0; return false;
}
function sample(fa, fb, w, gx, gy) {
    const { nx, ny } = S.meta, N = S.N, ND = S.ND;
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
    out[0] = (su / sw) * S.meta.scale.u; out[1] = (sv / sw) * S.meta.scale.v;
    return out;
}

function buildRaster(fa, fb, w) {
    const { nx, ny } = S.meta, N = S.N, ND = S.ND, d = offImg.data;
    const mode = S.raster, lut = LUT[mode];
    const vmax = mode === "speed" ? S.meta.speed_max : S.meta.depth_max;
    const su = S.meta.scale.u, sd = S.meta.scale.depth;
    for (let j = 0; j < ny; j++) {
        const row = (ny - 1 - j) * nx;
        for (let i = 0; i < nx; i++) {
            const k = j * nx + i, o = (row + i) * 4;
            const a = fa[k], b = fb[k];
            if (a === ND && b === ND) { d[o + 3] = 0; continue; }
            const src = a === ND ? fb : b === ND ? fa : null;
            let val;
            if (mode === "speed") {
                const u = src ? src[k] : a * (1 - w) + b * w;
                const v = src ? src[N + k] : fa[N + k] * (1 - w) + fb[N + k] * w;
                val = Math.hypot(u, v) * su;
            } else {
                val = (src ? src[2 * N + k] : fa[2 * N + k] * (1 - w) + fb[2 * N + k] * w) * sd;
            }
            const x = mode === "speed" ? curveF(val / vmax) : val / vmax;
            const li = Math.max(0, Math.min(255, (x * 255) | 0)) * 3;
            d[o] = lut[li]; d[o + 1] = lut[li + 1]; d[o + 2] = lut[li + 2]; d[o + 3] = 190;
        }
    }
    offCtx.putImageData(offImg, 0, 0);
}

function downloadTimeSeries(trace, S, meta, tms) {
    if (!trace || !S.probe || !meta) {
        alert("No time-series data available. Please select a point first."); return;
    }
    const plottedNames = [
        ...new Set(
            [...trace.querySelectorAll("path[data-var]")]
                .map(path => path.dataset.var).filter(Boolean)
        )
    ];
    if (plottedNames.length === 0) {
        alert("No time-series variables are currently plotted."); return;
    }
    const seriesMap = {
        depth: S.probe.depth, speed: S.probe.speed, direction: S.probe.direction
    };
    const plottedSeries = plottedNames.map(name => {
        const values = seriesMap[name];
        if (!values || typeof values.length !== "number") {
            console.warn(`No time-series data found for variable: ${name}`); return null;
        }
        const info = meta.variables?.[name] || {};
        return {
            name, label: info.description || name, units: info.units || "", values
        };
    }).filter(Boolean);
    if (plottedSeries.length === 0) {
        alert("No data available for the plotted variables."); return;
    }
    const escapeCSV = value => {
        const text = String(value ?? "");
        return `${text.replace(/"/g, '""')}`;
    };
    const headers = [
        "Time (UTC)", ...plottedSeries.map(s => s.units ? `${s.label} (${s.units})` : s.label)
    ];
    const rows = [headers];
    const rowCount = Math.max(tms?.length || 0, ...plottedSeries.map(s => s.values.length));
    for (let i = 0; i < rowCount; i++) {
        const time = meta.times?.[i] ?? (tms?.[i] != null ? new Date(tms[i]).toISOString() : "");
        const row = [
            time ? time.replace("T", " ").replace(/Z$/, "") : "",
            ...plottedSeries.map(s => {
                const value = s.values[i];
                return value != null && Number.isFinite(Number(value)) ? value : "";
            })
        ]; rows.push(row);
    }
    const csv = rows.map(row => row.map(escapeCSV).join(",")).join("\r\n");
    const blob = new Blob(["\uFEFF" + csv], {type: "text/csv;charset=utf-8;"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "time_series.csv";
    document.body.appendChild(link);
    link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}