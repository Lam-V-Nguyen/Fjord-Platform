// =====================================================================
// dashboardWeather.js – "Weather Conditions" section on the Overview
// Real-time weather over the model area: air temperature, precipitation, pressure, humidity.
// Data: POST /dashboard/weather (the backend picks the source: MET Frost / MET Locationforecast / Open-Meteo,
// cached per source). Refreshes automatically at the interval chosen by the user (saved in localStorage).
// =====================================================================
import { icon } from './icons.js';
import { cssVar, showEmpty } from './dashboardCharts.js';
import { getLastTimeZone } from './constant.js';

const Plotly = window.Plotly;
const STORE_KEY = 'dash-weather';
const DEFAULTS = { variable: 'temperature', sources: null, interval: 30 };   // sources null = backend default
// Fixed colour & line style per source so comparisons are easy to read
const SOURCE_STYLE = {
    frost: { color: '--text', short: 'Frost obs.', abbr: 'OBS', width: 2.4 },
    metno: { color: '--series-2', short: 'MET forecast', abbr: 'MET', width: 1.8 },
    openmeteo: { color: '--series-4', short: 'Open-Meteo', abbr: 'OM', width: 1.8 },
};
const $ = (id) => document.getElementById(id);

function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function readStore() {
    let st;
    try { 
        st = { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(STORE_KEY)) || {}) }; 
    } catch (e) { st = { ...DEFAULTS }; }
    if (!Array.isArray(st.sources) || !st.sources.length) st.sources = null;   // older versions saved 'source' (string)
    delete st.source;
    return st;
}

function timeZone() {
    const tz = getLastTimeZone();
    try { if (tz) { new Intl.DateTimeFormat('en-GB', { timeZone: tz }); return tz; } } catch (e) { /* invalid tz */ }
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

// ISO UTC -> 'YYYY-MM-DD HH:mm' in the user's time zone (Plotly does not handle time zones)
function toLocal(iso, tz) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return new Intl.DateTimeFormat('sv-SE', {
        timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).format(d).replace(',', '');
}

function hhmm(iso, tz) {
    if (!iso) return '-';
    return new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(iso));
}

function fmt(v, d) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
    return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

export class WeatherPanel {
    constructor({ getProject }) {
        this.getProject = getProject;
        this.state = readStore();
        this.data = null; this.chartSize = null;      // chart frame size at the previous draw (redraw when it changes / becomes visible)
        this.timer = null; this.loading = false; this.lastLoad = 0; this.project = null;
    }

    init() {
        $('weather-interval').value = String(this.state.interval);
        $('weather-var').addEventListener('change', (e) => this.selectVariable(e.target.value));
        // Source list with checkboxes (popover)
        const btn = $('weather-source-btn'), pop = $('weather-source-pop');
        btn.addEventListener('click', (e) => { e.stopPropagation(); pop.classList.toggle('show'); });
        pop.addEventListener('click', (e) => e.stopPropagation());
        document.addEventListener('click', () => pop.classList.remove('show'));
        pop.addEventListener('change', () => {
            const chosen = [...pop.querySelectorAll('input[data-src]:checked')].map(i => i.dataset.src);
            if (!chosen.length) {                  // always keep at least 1 source
                pop.querySelector(`input[data-src="${this.selected()[0]}"]`).checked = true; return;
            }
            this.state.sources = chosen; this.save(); this.load();
        });
        $('weather-interval').addEventListener('change', (e) => {
            this.state.interval = Number(e.target.value) || 0; this.save(); this.schedule();
        });
        $('weather-refresh').addEventListener('click', () => this.load(true));
        $('weather-now').addEventListener('click', (e) => {
            const t = e.target.closest('[data-wvar]');
            if (t) this.selectVariable(t.dataset.wvar);
        });
        // Tab hidden for a long time (sleep, tab switch): on return, refresh if the interval has passed
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && this.state.interval > 0
                && Date.now() - this.lastLoad > this.state.interval * 60000) this.load();
        });
        // The chart is drawn at the REAL size of its frame. When the panel is hidden (Map View, no results yet)
        // Plotly uses its default size (~700×450) and overflows the panel -> redraw when the frame appears / resizes.
        const wrap = $('weather-chart')?.parentElement;
        if (wrap && window.ResizeObserver) {
            new ResizeObserver(() => {
                const w = wrap.clientWidth, h = wrap.clientHeight;
                if (!w || !h) return;
                if (!this.chartSize || this.chartSize[0] !== w || this.chartSize[1] !== h) this.renderChart();
            }).observe(wrap);
        }
        this.schedule();
        return this;
    }

    save() {
        try { localStorage.setItem(STORE_KEY, JSON.stringify(this.state)); } catch (e) { /* ignore */ }
    }

    schedule() {
        clearInterval(this.timer); this.timer = null;
        if (this.state.interval > 0) this.timer = setInterval(() => this.load(), this.state.interval * 60000);
        this.renderNote();
    }

    // Called when the project changes (model location changes) or when the Overview refreshes
    onProjectChanged() {
        if (this.project !== this.getProject()) { this.project = this.getProject(); this.load(); }
    }

    selectVariable(key) {
        this.state.variable = key; this.save();
        $('weather-var').value = key;
        this.renderNow(); this.renderChart();
    }

    async load(force = false) {
        const project = this.getProject();
        if (!project || this.loading) return;
        this.loading = true;
        const btn = $('weather-refresh');
        btn.classList.add('spinning');
        try {
            const res = await fetch('/dashboard/weather', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ projectName: project, sources: this.state.sources, force }),
            });
            const data = await res.json();
            this.data = data; this.lastLoad = Date.now();
            this.render();
        } catch (err) {
            console.error(err);
            this.data = { status: 'error', message: `Weather request failed: ${err.message}` };
            this.render();
        } finally {
            this.loading = false; btn.classList.remove('spinning');
        }
    }

    variables() {
        return this.data?.variables || [];
    }

    selected() {
        return this.data?.selected || this.state.sources || [];
    }

    // Sources displayed: selected sources that worked + fallback sources added automatically (backend: 'shown')
    okSources() {
        const r = this.data?.results || {};
        return (this.data?.shown || this.selected()).filter(k => r[k]?.status === 'ok');
    }

    primary() {
        return this.data?.results?.[this.data?.primary];
    }

    meta(key = this.state.variable) {
        return this.variables().find(v => v.key === key) || this.variables()[0];
    }

    render() {
        const vars = this.variables();
        const sel = $('weather-var');
        if (vars.length && sel.options.length !== vars.length) {
            sel.innerHTML = vars.map(v => `<option value="${v.key}">${esc(v.label)} (${esc(v.unit)})</option>`).join('');
        }
        if (vars.length && !vars.some(v => v.key === this.state.variable)) this.state.variable = vars[0].key;
        sel.value = this.state.variable; this.renderSourceList(); this.renderSite();
        if (this.data?.status !== 'ok') {
            $('weather-now').innerHTML = '';
            showEmpty('weather-chart', esc(this.data?.message || 'No weather data'));
            this.renderNote();
            return;
        }
        this.renderNow(); this.renderChart(); this.renderNote();
    }

    // Subtitle line: nearest weather station providing measurements, or the grid point of the fallback source
    renderSite() {
        const sub = $('weather-sub'), d = this.data, site = d?.site, loc = d?.location, fb = d?.fallback;
        const ll = (o) => `${fmt(o.lat, 3)}°N, ${fmt(o.lon, 3)}°E`;
        const elev = (o) => o.elevation != null ? ` · ${fmt(o.elevation, 0)} m a.s.l.` : '';
        let text, tip = []; const km = (o) => o?.distance_km != null ? `${fmt(o.distance_km, 1)} km` : 'distance unknown';
        if (site?.kind === 'station') {
            // Distance right after the station name (nearest station with measurements, no distance limit)
            text = `Station ${site.name} (${site.id}) · ${km(site)} from model area · ${ll(site)}${elev(site)}`
                + `${site.others?.length ? ` · +${site.others.length} station${site.others.length > 1 ? 's' : ''}` : ''}`;
            tip.push(`Nearest MET Norway station with observations: ${site.name} (${site.id}), ${km(site)} from the model area:`);
            const byId = Object.fromEntries([site, ...(site.others || [])].map(x => [x.id, x]));
            Object.entries(site.per_variable || {}).forEach(([k, x]) => {
                const st = typeof x === 'object' ? x : Object.values(byId).find(o => o.name === x);
                tip.push(`     + ${this.meta(k)?.label || k}: ${st?.name || x}${st ? ` (${km(st)})` : ''}`);
            });
        } else if (site?.kind === 'model') {
            text = `${fb ? 'No nearby station data → ' : ''}${SOURCE_STYLE[site.source]?.short || site.label} at ${ll(site)}${elev(site)}`;
            tip.push(`${site.label}: model grid point ${ll(site)}`);
        } else {
            text = loc ? `Model area ${ll(loc)}` : 'Live data at the model area';
        }
        if (fb) tip.push(`Fallback: ${fb.from ? (SOURCE_STYLE[fb.from]?.short || fb.from) + ' → ' : ''}${SOURCE_STYLE[fb.to]?.short || fb.to}`
            + ` for ${fb.variables.map(k => this.meta(k)?.short || k).join(', ')}`, `Reason: ${fb.reason}`);
        if (loc) tip.push(`Model area: ${ll(loc)} (${loc.basis})`);
        sub.textContent = text;
        sub.title = tip.join('\n');
        sub.classList.toggle('warn', site?.kind === 'model' && !!fb);   // orange: NO station measurements (fully replaced)
    }

    // Source popover: checkbox + status; unusable sources (e.g. Frost without a client ID) are locked
    renderSourceList() {
        const list = this.data?.sources || [], sel = this.selected(), res = this.data?.results || {};
        if (!list.length) return;
        $('weather-source-pop').innerHTML = list.map(s => {
            const err = res[s.key]?.status === 'error' ? res[s.key].message : '';
            const auto = !sel.includes(s.key) && (this.data?.shown || []).includes(s.key);   // fallback source added automatically
            const dot = `<span class="w-src-dot" style="background:${cssVar(SOURCE_STYLE[s.key]?.color || '--muted')}"></span>`;
            return `<label class="w-src-item ${s.available ? '' : 'disabled'}" title="${esc(err || s.note || s.kind)}">
                <input type="checkbox" data-src="${s.key}" ${sel.includes(s.key) ? 'checked' : ''} ${s.available ? '' : 'disabled'}>
                ${dot}<span><b>${esc(s.label)}</b><small>${esc(s.kind)}${s.note ? ` · ${esc(s.note)}` : ''}${err ? ' · failed' : ''}${auto ? ' · shown as fallback' : ''}</small></span>
            </label>`;
        }).join('');
        const names = sel.map(k => SOURCE_STYLE[k]?.short || k);
        $('weather-source-btn').textContent = sel.length > 1 ? `Compare (${sel.length})` : (names[0] || 'Sources');
        $('weather-source-btn').title = `Data sources: ${names.join(', ')}`;
    }

    renderNow() {
        const p = this.primary();
        if (this.data?.status !== 'ok' || !p) return;
        // Current value per variable (merged by the backend: station measurements first, else the fallback source)
        const cur = this.data.current || p.current || {}, main = this.data.primary;
        const node = $('weather-now');
        node.title = `Latest values · ${timeZone()}`;
        node.innerHTML = this.variables().map(v => {
            const c = cur[v.key] || {}, src = c.source || main;
            const name = SOURCE_STYLE[src]?.short || src || '';
            const where = c.station ? `${c.station.name} (${c.station.id}, ${c.station.distance_km != null ? fmt(c.station.distance_km, 1) + ' km' : 'distance unknown'})` : name;
            const tag = src && src !== main
                ? `<span class="w-tag" title="Not observed at the station – value from ${esc(name)}">${esc(SOURCE_STYLE[src]?.abbr || name)}</span>` : '';
            return `<button type="button" class="w-tile ${v.key === this.state.variable ? 'active' : ''}" data-wvar="${v.key}"
                title="${esc(v.label)} at ${esc(hhmm(c.time, timeZone()))} · ${esc(where)}">
                <span class="w-ico">${icon(v.icon || 'activity', 14)}</span>
                <span class="w-label">${esc(v.short || v.label)}</span>${tag}
                <span class="w-value">${fmt(c.value, v.decimals ?? 1)}<small>${esc(v.unit)}</small></span>
            </button>`;
        }).join('');
    }

    renderChart() {
        const node = $('weather-chart');
        if (!node || this.data?.status !== 'ok' || !Plotly) return;
        const wrap = node.parentElement, W = wrap.clientWidth, H = wrap.clientHeight;
        if (!W || !H) { this.chartSize = null; return; }         // panel hidden -> draw when it appears (ResizeObserver)
        this.chartSize = [W, H];
        const v = this.meta(), tz = timeZone();
        const nowIso = this.data.server_now, nowT = new Date(nowIso).getTime();
        const hover = (name) => `${name}: %{y:.${v.decimals ?? 1}f} ${v.unit}<extra></extra>`;
        const traces = [], srcs = this.okSources();
        srcs.forEach(k => {
            const s = this.data.results[k].series?.[v.key];
            if (!s || !s.x.length || s.y.every(y => y === null)) return;
            const style = SOURCE_STYLE[k] || { color: '--muted', short: k, width: 1.8 };
            const color = cssVar(style.color), x = s.x.map(t => toLocal(t, tz));
            const past = s.x.map(t => new Date(t).getTime() <= nowT);
            if (v.key === 'precipitation') {
                // Side-by-side bars per source; the forecast part (after 'now') is lighter
                traces.push({ type: 'bar', name: style.short, x, y: s.y, hovertemplate: hover(style.short),
                    marker: { color, opacity: past.map(p => p ? 0.95 : 0.45) } });
                return;
            }
            // Measurements: solid line. Model/forecast: past thin solid, future dotted; joined at 'now'
            const lastPast = past.lastIndexOf(true);
            const ys = (cond) => s.y.map((y, i) => cond(i) ? y : null);
            if (k === 'frost') {
                traces.push({ type: 'scatter', mode: 'lines', name: style.short, x, y: s.y,
                    line: { color, width: style.width }, hovertemplate: hover(style.short) });
                return;
            }
            if (lastPast >= 0) traces.push({ type: 'scatter', mode: 'lines', name: style.short, legendgroup: k, x,
                y: ys(i => i <= lastPast), line: { color, width: style.width - 0.4 }, opacity: 0.8, hovertemplate: hover(style.short) });
            traces.push({ type: 'scatter', mode: 'lines', name: style.short, legendgroup: k, showlegend: lastPast < 0, x,
                y: ys(i => i >= Math.max(lastPast, 0)), line: { color, width: style.width, dash: 'dot' }, hovertemplate: hover(style.short) });
        });
        if (!traces.length) {
            showEmpty(node, `No ${v.label.toLowerCase()} from the selected source${srcs.length > 1 ? 's' : ''}`); return;
        }
        node.querySelector(':scope > .empty-state')?.remove();
        const nowX = toLocal(nowIso, tz), grid = 'rgba(148,170,200,0.10)', muted = cssVar('--muted');
        const multi = srcs.length > 1;
        const layout = { width: W, height: H, autosize: false,
            paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', showlegend: multi,
            barmode: 'group', bargap: 0.15, bargroupgap: 0.05, hovermode: 'x unified',
            // Low chart: one-line legend on top, right-aligned (does not cover the lines), minimal margins
            legend: { orientation: 'h', x: 1, y: 1, xanchor: 'right', yanchor: 'bottom', 
                font: { size: 9.5, color: cssVar('--text-2') }, bgcolor: 'rgba(0,0,0,0)', itemwidth: 24 },
            font: { family: cssVar('--font', 'Inter, sans-serif'), size: 10, color: muted },
            margin: { l: 38, r: 6, t: multi ? 14 : 4, b: 22 },
            hoverlabel: { bgcolor: cssVar('--panel-2'), bordercolor: cssVar('--border-strong'), font: { color: cssVar('--text'), size: 12 } },
            xaxis: { gridcolor: grid, zeroline: false, linecolor: grid, tickfont: { size: 9.5 }, tickformat: '%d %b %H:%M',
                nticks: Math.max(3, Math.floor(W / 150)), automargin: false, ticklabeloverflow: 'hide past div' },
            yaxis: { gridcolor: grid, zeroline: false, linecolor: grid, tickfont: { size: 9.5 }, nticks: 4, automargin: true,
                rangemode: v.key === 'precipitation' ? 'tozero' : 'normal' },
            shapes: [{ type: 'line', xref: 'x', yref: 'paper', x0: nowX, x1: nowX, y0: 0, y1: 1,
                line: { color: cssVar('--accent-2'), width: 1, dash: 'dash' } }],
            annotations: [{ xref: 'x', yref: 'paper', x: nowX, y: 1, yanchor: 'top', xanchor: 'left', xshift: 3,
                showarrow: false, text: 'now', font: { size: 9.5, color: cssVar('--accent-2') } }],
        };
        Plotly.react(node, traces, layout, { displayModeBar: false, responsive: false });
    }

    // Source legend: bottom-right corner, small text
    renderNote() {
        const note = $('weather-note');
        if (!note) return;
        const d = this.data, tz = timeZone();
        const next = this.state.interval > 0 && this.lastLoad
            ? ` · next update ${hhmm(new Date(this.lastLoad + this.state.interval * 60000).toISOString(), tz)}` : ' · auto-update off';
        if (!d || d.status !== 'ok') {
            note.classList.toggle('warn', !!d);
            note.textContent = d ? `Weather: ${d.message || 'unavailable'}${next}` : '';
            return;
        }
        // One line per selected source (full tooltip); compact display line: sources joined with ' | '
        const shown = this.okSources(), list = [...new Set([...this.selected(), ...shown])];
        const parts = [], tips = [], multi = shown.length > 1; let failed = false;
        list.forEach(k => {
            const r = d.results?.[k];
            if (!r) return;
            const s = r.source;
            if (r.status !== 'ok') {
                failed = true; parts.push(`${esc(s.label)}: failed`); tips.push(`${s.label}: ${r.message}`); return;
            }
            const st = Object.values(r.stations || {});
            const names = [...new Map(st.map(x => [x.id, x])).values()]
                .map(x => `${x.name}${x.distance_km != null ? ` (${fmt(x.distance_km, 1)} km)` : ''}`).join(', ');
            const link = (t) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(t)}</a>`;
            parts.push(multi   // several sources: compact form (full text in the tooltip)
                ? `${link(SOURCE_STYLE[k]?.short || s.label)}${names ? ` (${esc(names)})` : ''} ${hhmm(r.fetched_at, tz)}`
                : `${link(s.label)} – ${esc(s.kind.toLowerCase())}, ${esc(s.resolution)}${names ? ` · station ${esc(names)}` : ''}`
                  + ` · data ${hhmm(r.fetched_at, tz)}`);
            tips.push(`${s.label} – ${s.kind}, ${s.resolution}${names ? `, station ${names}` : ''}, ${s.license}`);
        });
        if (d.fallback) {
            const fb = d.fallback, to = SOURCE_STYLE[fb.to]?.short || fb.to;
            const partial = fb.variables.length < this.variables().length;
            parts.push(partial
                ? `${esc(fb.variables.map(k => this.meta(k)?.short || k).join(', '))} from ${esc(to)} (not observed at station)`
                : `fallback → ${esc(to)}${fb.from ? ' (no station data)' : ''}`);
            tips.push(`Fallback to ${to}: ${fb.reason}`);
        }
        const lic = [...new Set(this.okSources().map(k => d.results[k].source.license))].join(' · ');
        note.classList.toggle('warn', failed || d.site?.kind === 'model' && !!d.fallback);
        note.title = tips.join('\n');
        note.innerHTML = `Source${multi ? 's (data time)' : ''}: ${parts.join(multi ? ' · ' : ' | ')}${esc(next)} · ${esc(lic)}`;
    }

    resize() {
        this.renderChart();       // redraw at the current frame size (skipped while the panel is hidden)
    }

    destroy() {
        clearInterval(this.timer); this.timer = null;
    }
}