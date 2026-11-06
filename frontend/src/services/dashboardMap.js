// Bản đồ của Dashboard (Leaflet) – trung tâm là KẾT QUẢ MÔ PHỎNG:
//  - Trường HYD/WAQ trên lưới tính toán (canvas), tô màu theo biến/lớp/thời điểm
//  - Trạm quan trắc HYD/WAQ (bấm để chọn trạm cho profile & chuỗi thời gian)
//  - Vùng cảnh báo tại trạm vượt ngưỡng
//  - Lớp ngữ cảnh: ranh giới hồ, lưu vực, sông, độ sâu mẫu, lớp GIS của dự án
//  - Danh sách bật/tắt lớp ở sidebar (lưu localStorage) + chú giải có thang màu

import { icon } from './icons.js';
import { cssVar, seriesColors } from './dashboardCharts.js';

const L = window.L;
const LAYER_STATE_KEY = 'dash-layer-state-v2';
const BASEMAP_KEY = 'dash-basemap';

export const BASEMAPS = {
    dark: {
        label: 'Dark', url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
        attribution: 'Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap', maxZoom: 16,
    },
    satellite: {
        label: 'Satellite', url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attribution: 'Tiles &copy; Esri', maxZoom: 19,
    },
    light: {
        label: 'Light', url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
        attribution: 'Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap', maxZoom: 16,
    },
    osm: {
        label: 'OSM', url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution: '&copy; OpenStreetMap contributors', maxZoom: 19,
    },
};

// Thang màu cho từng loại biến (từ thấp -> cao)
export const PALETTES = {
    thermal: ['#1e3a8a', '#2563eb', '#06b6d4', '#a3e635', '#facc15', '#f97316', '#dc2626'],
    haline: ['#0c4a6e', '#0284c7', '#22d3ee', '#a7f3d0', '#fef9c3'],
    speed: ['#0f172a', '#1e3a8a', '#7c3aed', '#db2777', '#f97316', '#fde047'],
    level: ['#312e81', '#4f46e5', '#38bdf8', '#a5f3fc'],
    depth: ['#a5f3fc', '#38bdf8', '#2563eb', '#4c1d95', '#1e1b4b'],
    quality: ['#10b981', '#a3e635', '#facc15', '#f97316', '#dc2626', '#7f1d1d'],
    quality_rev: ['#7f1d1d', '#dc2626', '#f97316', '#facc15', '#a3e635', '#10b981'],
    bathymetry: ['#22d3ee', '#3b82f6', '#8b5cf6', '#ec4899', '#f97316', '#fde047'],
};

export const LAYER_DEFS = [
    { key: 'field', label: 'Simulation field', kind: 'gradient', on: true },
    { key: 'stations', label: 'Observation stations', kind: 'point', color: '#e879f9', icon: 'eye', on: true },
    { key: 'alerts', label: 'Alert zones', kind: 'zone', color: '#f43f5e', on: true },
    { key: 'lake', label: 'Lake boundary', kind: 'polygon', color: '#38bdf8', on: true },
    { key: 'catchment', label: 'Catchment', kind: 'polygon-dashed', color: '#a3e635', on: false },
    { key: 'rivers', label: 'River network', kind: 'line', color: '#60a5fa', on: false },
    { key: 'bathymetry', label: 'Bathymetry points', kind: 'gradient', palette: 'bathymetry', on: false },
];

// ------------------------------------------------------------------ colour helpers
function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }

export function paletteColor(t, palette) {
    const stops = PALETTES[palette] || PALETTES.thermal;
    if (t === null || t === undefined || Number.isNaN(t)) return null;
    const x = Math.max(0, Math.min(1, t)) * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(x)), f = x - i;
    const a = hexToRgb(stops[i]), b = hexToRgb(stops[i + 1]);
    return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`;
}

export function paletteCss(palette, dir = 'to right') {
    return `linear-gradient(${dir}, ${(PALETTES[palette] || PALETTES.thermal).join(', ')})`;
}

function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function fmtVal(v, unit = '') {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    const a = Math.abs(v);
    const s = a >= 1000 ? v.toFixed(0) : a >= 100 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : a >= 0.01 ? v.toFixed(3) : v.toExponential(2);
    return unit ? `${s} ${unit}` : s;
}

function loadState() { try { return JSON.parse(localStorage.getItem(LAYER_STATE_KEY)) || {}; } catch (e) { return {}; } }
function saveState(s) { try { localStorage.setItem(LAYER_STATE_KEY, JSON.stringify(s)); } catch (e) { /* ignore */ } }

// ======================================================================
export class DashboardMap {
    constructor({ mapId, legendEl, layerListEl, switchEl, coordsEl, toggleAllBtn, onExpand, onStationSelect }) {
        Object.assign(this, { mapId, legendEl, layerListEl, switchEl, coordsEl, toggleAllBtn, onExpand, onStationSelect });
        this.map = null; this.base = null;
        this.groups = {};
        this.defs = LAYER_DEFS.map(d => ({ ...d }));
        this.state = { ...Object.fromEntries(this.defs.map(d => [d.key, d.on])), ...loadState() };
        this.meshBounds = null; this.contextBounds = null;
        this.meshLayers = [];      // L.Polygon theo chỉ số ô
        this.field = null;         // {values, min, max, label, unit, palette}
        this.stationLookup = {};   // tên trạm -> [lat, lon]
        this.stationMarkers = {};
        this.selectedStation = null;
        this.alertMarkers = {};
        this.scaleMode = localStorage.getItem('dash-scale-mode') || 'fixed';
        this.rawField = null;
    }

    setScaleMode(mode) {
        this.scaleMode = mode;
        try { localStorage.setItem('dash-scale-mode', mode); } catch (e) { /* ignore */ }
        if (this.rawField) this.setField(this.rawField);
    }

    // ------------------------------------------------------------ init
    init() {
        this.map = L.map(this.mapId, {
            zoomControl: false, attributionControl: true, minZoom: 3, zoomSnap: 0.25, zoomDelta: 0.5,
        }).setView([62.476969, 6.471598], 12);
        L.control.scale({ position: 'bottomleft', imperial: false, maxWidth: 140 }).addTo(this.map);
        this.map.createPane('field'); this.map.getPane('field').style.zIndex = 390;
        this.map.createPane('zones'); this.map.getPane('zones').style.zIndex = 420;
        this.canvas = L.canvas({ padding: 0.4, pane: 'field', tolerance: 1 });
        this.map.on('mousemove', (e) => this.showCoords(e.latlng));
        this.renderBasemapSwitch();
        this.setBasemap(localStorage.getItem(BASEMAP_KEY) || 'dark');
        document.getElementById('map-zoom-in')?.addEventListener('click', () => this.map.zoomIn());
        document.getElementById('map-zoom-out')?.addEventListener('click', () => this.map.zoomOut());
        document.getElementById('map-home')?.addEventListener('click', () => this.fit());
        document.getElementById('map-expand')?.addEventListener('click', () => this.onExpand && this.onExpand());
        this.toggleAllBtn?.addEventListener('click', () => this.toggleAll());
        this.renderLayerList();
        return this;
    }

    renderBasemapSwitch() {
        if (!this.switchEl) return;
        this.switchEl.innerHTML = Object.entries(BASEMAPS).map(([k, b]) => `<button data-basemap="${k}">${b.label}</button>`).join('');
        this.switchEl.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-basemap]');
            if (btn) this.setBasemap(btn.dataset.basemap);
        });
    }

    setBasemap(key) {
        const def = BASEMAPS[key] || BASEMAPS.dark;
        if (this.base) this.map.removeLayer(this.base);
        this.base = L.tileLayer(def.url, { attribution: def.attribution, subdomains: def.subdomains || 'abc', maxNativeZoom: def.maxZoom, maxZoom: 20 }).addTo(this.map);
        this.base.bringToBack();
        try { localStorage.setItem(BASEMAP_KEY, key); } catch (e) { /* ignore */ }
        this.switchEl?.querySelectorAll('[data-basemap]').forEach(b => b.classList.toggle('active', b.dataset.basemap === key));
    }

    showCoords(latlng) {
        if (!this.coordsEl) return;
        let txt = `${latlng.lat.toFixed(5)}°N  ${latlng.lng.toFixed(5)}°E`;
        if (this.hoverValue !== undefined && this.hoverValue !== null && this.field) {
            txt += `  ·  ${fmtVal(this.hoverValue, this.field.unit)}`;
        }
        this.coordsEl.textContent = txt;
    }

    _addGroup(key, group) {
        if (this.groups[key]) this.map.removeLayer(this.groups[key]);
        this.groups[key] = group;
        if (this.state[key] ?? true) group.addTo(this.map);
    }

    // ------------------------------------------------------------ mesh + field
    setMesh(mesh) {
        this.meshLayers = [];
        if (!mesh || !mesh.features?.length) { this._addGroup('field', L.layerGroup()); return; }
        const group = L.layerGroup();
        mesh.features.forEach((f, i) => {
            const ring = f.geometry.coordinates[0];
            if (!ring || ring.length < 4) { this.meshLayers.push(null); return; }
            const poly = L.polygon(ring.map(([x, y]) => [y, x]), {
                renderer: this.canvas, pane: 'field', stroke: true, weight: 0.3, color: 'rgba(10,18,32,0.35)',
                fillOpacity: 0.85, fillColor: '#1e293b', interactive: true,
            });
            poly.on('mouseover', () => { this.hoverValue = this.field?.values?.[i]; });
            poly.on('mouseout', () => { this.hoverValue = null; });
            poly.on('click', (e) => {
                const v = this.field?.values?.[i];
                L.popup({ closeButton: false }).setLatLng(e.latlng)
                    .setContent(`<div class="popup-title">${esc(this.field?.label || 'Value')}</div>
                        <div class="popup-kv"><span>Cell</span><span>#${i}</span><span>Value</span><span>${fmtVal(v, this.field?.unit)}</span>
                        <span>Time</span><span>${esc((this.field?.time || '').replace('T', ' ').slice(0, 16))}</span></div>`)
                    .openOn(this.map);
            });
            this.meshLayers.push(poly);
            group.addLayer(poly);
        });
        this._addGroup('field', group);
        this.meshBounds = L.latLngBounds(mesh.bounds);
        this.renderLegend();
    }

    setField(field) {
        // field: {values, min, max, label, unit, palette, time}
        this.rawField = field;
        // Thang màu: 'fixed' = P2–P98 trên toàn thời gian (ổn định khi chạy animation)
        //           'frame' = P2–P98 của khung hình hiện tại (tương phản tốt hơn)
        if (this.scaleMode === 'frame') {
            const v = field.values.filter(x => x !== null && x !== undefined).sort((a, b) => a - b);
            if (v.length) { field = { ...field, min: v[Math.floor(v.length * 0.02)], max: v[Math.ceil(v.length * 0.98) - 1] }; }
        }
        this.field = field;
        const { values, min, max } = field;
        const span = (max - min) || 1;
        for (let i = 0; i < this.meshLayers.length; i++) {
            const p = this.meshLayers[i];
            if (!p) continue;
            const v = values[i];
            const c = v === null || v === undefined ? null : paletteColor((v - min) / span, field.palette);
            p.setStyle(c ? { fillColor: c, fillOpacity: 0.88 } : { fillColor: '#0b1220', fillOpacity: 0.25 });
        }
        this.renderLegend();
    }

    // ------------------------------------------------------------ stations
    setStations(stations) {
        // stations: [{name, lat, lon, sources:['hyd','waq'], coords:{hyd:[lat,lon], waq:[lat,lon]}}]
        // Có thể có 2 điểm cùng tên (HYD và WAQ ở hai vị trí khác nhau).
        this.stationLookup = {}; this.stationMarkers = {};
        const g = L.layerGroup();
        const def = this.defs.find(d => d.key === 'stations');
        stations.filter(s => s.lat !== null && s.lon !== null).forEach(s => {
            if (!this.stationLookup[s.name]) this.stationLookup[s.name] = [s.lat, s.lon];
            s.sources.forEach(src => { this.stationLookup[`${src}:${s.name}`] = (s.coords && s.coords[src]) || [s.lat, s.lon]; });
            const m = L.marker([s.lat, s.lon], { icon: this._stationIcon(def.color, s.name === this.selectedStation), riseOnHover: true, zIndexOffset: 500 });
            m.bindTooltip(`${esc(s.name)} <span style="color:var(--muted)">${s.sources.map(x => x.toUpperCase()).join(' · ')}</span>`,
                { className: 'dash-tooltip', direction: 'top', offset: [0, -12] });
            m.on('click', () => { this.selectStation(s.name); this.onStationSelect && this.onStationSelect(s.name); });
            (this.stationMarkers[s.name] = this.stationMarkers[s.name] || []).push(m);
            g.addLayer(m);
        });
        this._addGroup('stations', g);
        this.groups.alerts = this.groups.alerts || L.layerGroup();
        if ((this.state.alerts ?? true) && !this.map.hasLayer(this.groups.alerts)) this.groups.alerts.addTo(this.map);
        this.renderLayerList(); this.renderLegend();
    }

    _stationIcon(color, selected) {
        return L.divIcon({
            className: '', iconSize: [24, 24], iconAnchor: [12, 12],
            html: `<div class="dash-marker${selected ? ' selected' : ''}" style="--c:${selected ? '#facc15' : color}">${icon('eye', 12)}</div>`,
        });
    }

    selectStation(name) {
        this.selectedStation = name;
        const def = this.defs.find(d => d.key === 'stations');
        Object.entries(this.stationMarkers).forEach(([n, ms]) => ms.forEach(m => m.setIcon(this._stationIcon(def.color, n === name))));
    }

    // ------------------------------------------------------------ context layers
    setContext(layers) {
        const parts = [];
        if (layers.lake) {
            const g = L.geoJSON(layers.lake, {
                style: { color: '#7dd3fc', weight: 1.4, fill: false, opacity: 0.9 }, interactive: false,
            });
            this._addGroup('lake', g); parts.push(g.getBounds());
        }
        if (layers.catchment) {
            this._addGroup('catchment', L.geoJSON(layers.catchment, {
                style: { color: '#a3e635', weight: 1.3, dashArray: '5 5', fillColor: '#a3e635', fillOpacity: 0.03 }, interactive: false,
            }));
        }
        if (layers.rivers) {
            this._addGroup('rivers', L.geoJSON(layers.rivers, {
                style: (f) => ({ color: '#60a5fa', weight: Math.min(4, 1 + (f.properties?.Width || 0.5)), opacity: 0.8 }), interactive: false,
            }));
        }
        if (Array.isArray(layers.bathymetry) && layers.bathymetry.length) {
            const maxD = Math.max(...layers.bathymetry.map(p => p[2]));
            const r = L.canvas({ padding: 0.3 });
            this._addGroup('bathymetry', L.layerGroup(layers.bathymetry.map(([la, lo, d]) => L.circleMarker([la, lo], {
                renderer: r, radius: 2.4, stroke: false, fillOpacity: 0.85, fillColor: paletteColor(d / maxD, 'bathymetry'),
            }).bindTooltip(`${d.toFixed(0)} m`, { className: 'dash-tooltip' }))));
        }
        this.defs = this.defs.filter(d => !d.key.startsWith('gis:'));
        const palette = seriesColors();
        Object.entries(layers.gis || {}).forEach(([name, gj], i) => {
            const key = `gis:${name}`, color = palette[i % palette.length];
            this.defs.push({ key, label: name, kind: 'polygon', color, on: true });
            this._addGroup(key, L.geoJSON(gj, {
                style: { color, weight: 1.6, fillColor: color, fillOpacity: 0.1 },
                pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, color, weight: 2, fillOpacity: 0.7 }),
                onEachFeature: (f, lyr) => {
                    const props = Object.entries(f.properties || {}).slice(0, 8).map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join('');
                    lyr.bindPopup(`<div class="popup-title">${esc(name)}</div><div class="popup-kv">${props}</div>`);
                },
            }));
        });
        this.contextBounds = parts.length ? parts[0] : null;
        this.renderLayerList(); this.renderLegend();
    }

    // ------------------------------------------------------------ alerts
    setAlerts(alerts) {
        if (!this.groups.alerts) { this.groups.alerts = L.layerGroup(); if (this.state.alerts ?? true) this.groups.alerts.addTo(this.map); }
        const g = this.groups.alerts;
        g.clearLayers(); this.alertMarkers = {};
        const sevColor = { high: cssVar('--sev-high'), medium: cssVar('--sev-medium'), info: cssVar('--sev-info') };
        const byLoc = {};
        (alerts || []).forEach(a => {
            const loc = this.stationLookup[`${a.source}:${a.location}`] || this.stationLookup[a.location];
            if (!loc) return;
            const key = loc.join(',');   // nhóm theo VỊ TRÍ (hai trạm cùng tên có thể ở hai nơi)
            (byLoc[key] = byLoc[key] || { name: a.location, loc, items: [] }).items.push(a);
        });
        Object.values(byLoc).forEach(({ name, loc, items }) => {
            const worst = items.some(i => i.severity === 'high') ? 'high' : items.some(i => i.severity === 'medium') ? 'medium' : 'info';
            const circle = L.circle(loc, {
                pane: 'zones', radius: worst === 'high' ? 380 : 280, color: sevColor[worst], weight: 1.6, dashArray: '4 4',
                fillColor: sevColor[worst], fillOpacity: 0.12, className: 'pulse-zone',
            });
            circle.bindPopup(`<div class="popup-title">${esc(name)}</div>` +
                items.map(i => `<div style="margin:3px 0"><span class="sev-pill sev-${i.severity}">${i.severity}</span> ${esc(i.title)}</div>`).join(''));
            g.addLayer(circle);
            items.forEach(i => { this.alertMarkers[i.id] = circle; });
        });
        this.renderLegend();
    }
    focusAlert(alert) {
        const layer = this.alertMarkers[alert.id];
        const loc = layer ? layer.getLatLng() : ((this.stationLookup[`${alert.source}:${alert.location}`] || this.stationLookup[alert.location])
            ? L.latLng(this.stationLookup[`${alert.source}:${alert.location}`] || this.stationLookup[alert.location]) : null);
        if (!loc) return false;
        this.map.flyTo(loc, Math.max(this.map.getZoom(), 14), { duration: 0.6 });
        if (layer) setTimeout(() => layer.openPopup(), 650);
        return true;
    }

    // ------------------------------------------------------------ list & legend
    renderLayerList() {
        if (!this.layerListEl) return;
        this.layerListEl.innerHTML = this.defs.map(d => {
            const on = this.state[d.key] ?? d.on;
            const sw = d.kind === 'gradient' ? paletteCss(d.palette || this.field?.palette || 'thermal') : d.color;
            return `<label class="layer-item" title="${esc(d.label)}">
                <input type="checkbox" data-layer="${esc(d.key)}" ${on ? 'checked' : ''}>
                <span class="layer-check"></span><span class="layer-name">${esc(d.label)}</span>
                <span class="swatch" style="background:${sw}"></span></label>`;
        }).join('');
        this.layerListEl.onchange = (e) => {
            const input = e.target.closest('[data-layer]');
            if (input) this.toggle(input.dataset.layer, input.checked);
        };
    }

    toggle(key, on) {
        this.state[key] = on; saveState(this.state);
        const g = this.groups[key];
        if (g) { on ? g.addTo(this.map) : this.map.removeLayer(g); }
        const cb = this.layerListEl?.querySelector(`[data-layer="${CSS.escape(key)}"]`);
        if (cb) cb.checked = on;
        this.renderLegend();
    }

    toggleAll() {
        const anyOff = this.defs.some(d => !(this.state[d.key] ?? d.on));
        this.defs.forEach(d => this.toggle(d.key, anyOff));
    }

    renderLegend() {
        if (!this.legendEl) return;
        const sym = (d) => {
            if (d.kind === 'point') return `<span class="pt" style="background:${d.color}"></span>`;
            if (d.kind === 'line') return `<span class="ln" style="background:${d.color}"></span>`;
            if (d.kind === 'zone') return `<span class="zone" style="border-color:${d.color}"></span>`;
            if (d.kind === 'gradient') return `<span class="ln" style="background:${paletteCss(d.palette || 'bathymetry')}"></span>`;
            const dash = d.kind === 'polygon-dashed' ? 'dashed' : 'solid';
            return `<span class="pg" style="border-color:${d.color}; border-style:${dash}; background:${d.color}22"></span>`;
        };
        const hasContent = (g) => !g.getLayers || g.getLayers().length > 0;
        const active = this.defs.filter(d => d.key !== 'field' && (this.state[d.key] ?? d.on) && this.groups[d.key] && hasContent(this.groups[d.key]));
        let html = '';
        if (this.field && (this.state.field ?? true)) {
            const mid = (this.field.min + this.field.max) / 2;
            html += `<div class="legend-field">
                <div class="lf-title">${esc(this.field.label)}${this.field.unit ? ` (${esc(this.field.unit)})` : ''}</div>
                <div class="lf-mode">Scale: <button data-scale="fixed" class="${this.scaleMode === 'fixed' ? 'active' : ''}">all times</button><button data-scale="frame" class="${this.scaleMode === 'frame' ? 'active' : ''}">this frame</button></div>
                <div class="lf-bar" style="background:${paletteCss(this.field.palette)}"></div>
                <div class="lf-scale"><span>${fmtVal(this.field.min)}</span><span>${fmtVal(mid)}</span><span>${fmtVal(this.field.max)}</span></div>
            </div>`;
        }
        html += active.map(d => `<div class="legend-row"><span class="legend-sym">${sym(d)}</span>${esc(d.label)}</div>`).join('');
        this.legendEl.style.display = html ? '' : 'none';
        this.legendEl.innerHTML = html ? `<h5>Legend</h5>${html}` : '';
        this.legendEl.querySelectorAll('[data-scale]').forEach(b => b.addEventListener('click', () => this.setScaleMode(b.dataset.scale)));
    }

    // ------------------------------------------------------------ utils
    fit() {
        const b = this.meshBounds || this.contextBounds;
        if (b && b.isValid()) this.map.fitBounds(b, { padding: [28, 28] });
    }

    invalidate(refit = false) {
        setTimeout(() => { this.map?.invalidateSize(); if (refit) this.fit(); }, 60);
    }
}
