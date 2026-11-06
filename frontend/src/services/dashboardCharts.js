// Biểu đồ cho Dashboard (Plotly): theme tối thống nhất, gauge, donut, chuỗi thời gian, profile.
// Mọi hàm nhận id phần tử + dữ liệu đã chuẩn hoá từ /dashboard/*.

const Plotly = window.Plotly;

export function cssVar(name, fallback = '#888') {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
}

export function seriesColors() {
    return [1, 2, 3, 4, 5, 6].map(i => cssVar(`--series-${i}`));
}

export const SEV_COLORS = () => ({
    high: cssVar('--sev-high'), medium: cssVar('--sev-medium'),
    low: cssVar('--sev-low'), info: cssVar('--sev-info'),
});

const CONFIG = { displayModeBar: false, responsive: true };

function baseLayout(extra = {}) {
    const muted = cssVar('--muted'), grid = 'rgba(148,170,200,0.10)';
    return {
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
        font: { family: cssVar('--font', 'Inter, sans-serif'), size: 11, color: muted },
        margin: { l: 40, r: 10, t: 6, b: 28 },
        hoverlabel: { bgcolor: cssVar('--panel-2'), bordercolor: cssVar('--border-strong'), font: { color: cssVar('--text'), size: 12 } },
        xaxis: { gridcolor: grid, zeroline: false, linecolor: grid, tickfont: { size: 10.5 }, tickformat: '%d %b' },
        yaxis: { gridcolor: grid, zeroline: false, linecolor: grid, tickfont: { size: 10.5 } },
        legend: { orientation: 'h', x: 0, y: 1.14, font: { size: 10.5, color: cssVar('--text-2') }, bgcolor: 'rgba(0,0,0,0)' },
        showlegend: false,
        ...extra,
    };
}

function el(id) {
    return typeof id === 'string' ? document.getElementById(id) : id;
}

export function showEmpty(id, message = 'No data for this period') {
    const node = el(id);
    if (!node) return;
    try { Plotly.purge(node); } catch (e) { /* ignore */ }
    node.innerHTML = `<div class="empty-state">${message}</div>`;
}

function clearEmpty(node) {
    const empty = node.querySelector(':scope > .empty-state');
    if (empty) empty.remove();
}

// ------------------------------------------------------------------ Gauge
export function renderGauge(id, value, { min = 0, max = 100, label = '' } = {}) {
    const node = el(id);
    if (!node) return;
    if (value === null || value === undefined) { showEmpty(node, '—'); return; }
    clearEmpty(node);
    const sev = SEV_COLORS();
    const color = value >= 65 ? sev.low : value >= 45 ? sev.medium : sev.high;
    const data = [{
        type: 'indicator', mode: 'gauge+number', value,
        number: { font: { size: 22, color: cssVar('--text') }, valueformat: '.0f' },
        gauge: {
            shape: 'angular', axis: { range: [min, max], visible: false },
            bar: { color, thickness: 0.32 }, bgcolor: 'rgba(148,170,200,0.12)', borderwidth: 0,
            steps: [], threshold: { line: { color: cssVar('--text-2'), width: 2 }, thickness: 0.4, value },
        },
        domain: { x: [0, 1], y: [0, 1] },
    }];
    Plotly.react(node, data, baseLayout({ margin: { l: 8, r: 8, t: 6, b: 0 } }), CONFIG);
    return { color, label };
}

// ------------------------------------------------------------------ Donut
export function renderDonut(id, distribution) {
    const node = el(id);
    if (!node) return;
    const sev = SEV_COLORS();
    const keys = ['high', 'medium', 'low'];
    const labels = { high: 'High risk', medium: 'Medium risk', low: 'Within limits' };
    const values = keys.map(k => distribution?.[k] || 0);
    if (values.every(v => v === 0)) { showEmpty(node, 'No checks evaluated'); return; }
    clearEmpty(node);
    const total = values.reduce((a, b) => a + b, 0);
    const data = [{
        type: 'pie', hole: 0.62, sort: false, direction: 'clockwise',
        values, labels: keys.map(k => labels[k]),
        marker: { colors: keys.map(k => sev[k]), line: { color: cssVar('--panel'), width: 3 } },
        textinfo: 'none', hovertemplate: '%{label}: %{value} (%{percent})<extra></extra>',
    }];
    const layout = baseLayout({
        margin: { l: 4, r: 4, t: 4, b: 4 },
        annotations: [{
            text: `<b>${total}</b><br><span style="font-size:10px">checks</span>`, showarrow: false,
            font: { size: 18, color: cssVar('--text') }, x: 0.5, y: 0.5,
        }],
    });
    Plotly.react(node, data, layout, CONFIG);
}

// ------------------------------------------------------------------ Station time series (HYD/WAQ his)
// payload: {series:[{name,x,y}], label, unit}; opts: {threshold:{medium,high,direction}, highlight}
export function renderStationSeries(id, payload, opts = {}) {
    const node = el(id);
    if (!node) return;
    const series = (payload.series || []).filter(s => s.x && s.x.length);
    if (!series.length) { showEmpty(node, 'No station data for this variable'); return; }
    clearEmpty(node);
    const colors = seriesColors();
    const unit = payload.unit ? ` ${payload.unit}` : '';
    const traces = series.map((s, i) => {
        const hi = opts.highlight && s.name === opts.highlight;
        const dim = opts.highlight && !hi;
        return {
            type: 'scatter', mode: 'lines', name: s.name, x: s.x, y: s.y,
            line: { width: hi ? 2.6 : 1.6, color: colors[i % colors.length] }, opacity: dim ? 0.45 : 1,
            hovertemplate: `${s.name}: %{y:.3g}${unit}<extra></extra>`,
        };
    });
    const shapes = [], annotations = [];
    const th = opts.threshold;
    if (th) {
        [['medium', cssVar('--sev-medium')], ['high', cssVar('--sev-high')]].forEach(([k, c]) => {
            if (th[k] === undefined || th[k] === null) return;
            shapes.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: th[k], y1: th[k], line: { color: c, width: 1, dash: 'dash' } });
            annotations.push({ xref: 'paper', x: 1, y: th[k], xanchor: 'right', yanchor: 'bottom', showarrow: false,
                text: k, font: { size: 9.5, color: c } });
        });
    }
    const base = baseLayout();
    const layout = baseLayout({
        showlegend: true, margin: { l: 42, r: 8, t: 22, b: 26 }, shapes, annotations,
        yaxis: { ...base.yaxis, title: { text: payload.unit || '', font: { size: 10 } } },
    });
    Plotly.react(node, traces, layout, CONFIG);
}

// ------------------------------------------------------------------ Vertical profile
// prof: {depth:[], values:[], label, unit, depth_unit, station, time}; compare: profile khác (tuỳ chọn)
export function renderProfile(id, prof, opts = {}) {
    const node = el(id);
    if (!node) return;
    if (!prof || !prof.depth || !prof.depth.length) { showEmpty(node, opts.empty || 'No layered data'); return; }
    clearEmpty(node);
    const c = cssVar('--series-1');
    const traces = [{
        type: 'scatter', mode: 'lines+markers', x: prof.values, y: prof.depth, name: prof.time?.slice(0, 16).replace('T', ' '),
        line: { color: c, width: 2.4 }, marker: { size: 5, color: c },
        fill: 'tozerox', fillcolor: 'rgba(56,189,248,0.08)',
        hovertemplate: `%{y:.1f} ${prof.depth_unit}: %{x:.3g} ${prof.unit || ''}<extra></extra>`,
    }];
    const shapes = [];
    const th = opts.threshold;
    if (th) {
        [['medium', cssVar('--sev-medium')], ['high', cssVar('--sev-high')]].forEach(([k, col]) => {
            if (th[k] === undefined) return;
            shapes.push({ type: 'line', yref: 'paper', y0: 0, y1: 1, x0: th[k], x1: th[k], line: { color: col, width: 1, dash: 'dash' } });
        });
    }
    const base = baseLayout();
    const layout = baseLayout({
        margin: { l: 42, r: 10, t: 6, b: 32 }, shapes,
        xaxis: { ...base.xaxis, tickformat: '', title: { text: `${prof.label || ''}${prof.unit ? ' (' + prof.unit + ')' : ''}`, font: { size: 10 } } },
        yaxis: { ...base.yaxis, autorange: 'reversed', tickformat: '', title: { text: `Depth (${prof.depth_unit || 'm'})`, font: { size: 10 } } },
    });
    Plotly.react(node, traces, layout, CONFIG);
}

export function resizeCharts(ids) {
    ids.forEach(id => {
        const node = el(id);
        if (node && node.data) { try { Plotly.Plots.resize(node); } catch (e) { /* ignore */ } }
    });
}
