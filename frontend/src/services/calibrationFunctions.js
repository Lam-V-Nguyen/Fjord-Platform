import { calibrationParams, getCalibrationParam } from "./constant.js";
import { jsonLoader } from "./commonFunctions.js";


let activeProject = null, logInterval = null, lastOffsetHYD = 0, running = false;
// true while a simulation / resampling / optimization is being polled
export const isBusy = () => running;


export function esc(v) {
    return String(v ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function overrideRowHtml(row = ['', '', '']) {
    return `<tr>${row.map((v, i) => `<td><input type="text" value="${esc(v)}"
        placeholder="${['MDU key', 'Value', 'Note'][i]}"></td>`).join('')}</tr>`;
}

export function fillOverrides(rows, table) {
    const tbody = table.querySelector('tbody');
    tbody.innerHTML = (rows && rows.length ? rows : [['', '', '']]).map(overrideRowHtml).join('');
}

export function updateLogSimulation(hydProject, key, logObject, progressBarObject, progressTextObject, seconds, logFile) {
    if (logInterval) { clearInterval(logInterval); logInterval = null; }
    activeProject = hydProject; lastOffsetHYD = 0; running = true;
    const fetchLog = async () => {
        const res = await fetch(
            `/calibration_log_tail/${hydProject}?offset=${lastOffsetHYD}&log_file=${logFile}`
        );
        if (!res.ok) { 
            const txt = await res.text();
            progressTextObject.innerText = txt;
            console.error('log tail', res.status, txt); return; 
        }
        const data = await res.json();
        if (data.lines.length) {
            logObject.value += data.lines.join("\n") + "\n";
            logObject.scrollTop = logObject.scrollHeight;
        }
        lastOffsetHYD = data.offset;
    };
    logInterval = setInterval(async () => {
        if (activeProject !== hydProject) { 
            clearInterval(logInterval); logInterval = null; running = false; return; 
        }
        try {
            await fetchLog();
            const statusRes = await jsonLoader('check_sim_status_calibration', { projectName: hydProject, key });
            progressTextObject.innerText = statusRes.message;
            progressBarObject.value = statusRes.progress;
            if (statusRes.status !== "running") {
                clearInterval(logInterval); logInterval = null; running = false; 
                await fetchLog(); logObject.value += statusRes.message + "\n";
            }
        } catch (error) {
            progressTextObject.innerText = `Error: ${error}`;
            clearInterval(logInterval); logInterval = null; running = false;
        }
    }, seconds * 1000);
}

function parameterRowHtml(param) {
    const scale = param.scale || 'linear';
    return `
        <div class="parameter-row" data-param="${param.value}" title="MDU: ${param.value}">
            <div><input class="param-check" type="checkbox" ${param.checked ? 'checked' : ''}></div>
            <div>${param.name}</div>
            <div><input class="param-lower" type="text" value="${param.lower}" placeholder="Lower"></div>
            <div><input class="param-upper" type="text" value="${param.upper}" placeholder="Upper"></div>
            <div><select class="param-scale">
                <option value="linear" ${scale === 'linear' ? 'selected' : ''}>linear</option>
                <option value="log" ${scale === 'log' ? 'selected' : ''}>log</option>
            </select></div>
        </div>`;
}

export async function initParameterContainer(container, addParamKey, addParamBtn) {
    container.innerHTML = `
        <div class="parameter-header">
            <div>Use</div><div>Parameter</div>
            <div>Lower</div><div>Upper</div><div>Scale</div>
        </div>
    ${calibrationParams.map(parameterRowHtml).join('')}
    `;
    container.addEventListener('change', e => {
        const row = e.target.closest('.parameter-row');
        if (!row) return;
        const param = getCalibrationParam(row.dataset.param);
        if (!param) return;
        if (e.target.classList.contains('param-check')) param.checked = e.target.checked;
        if (e.target.classList.contains('param-lower')) param.lower = parseFloat(e.target.value);
        if (e.target.classList.contains('param-upper')) param.upper = parseFloat(e.target.value);
        if (e.target.classList.contains('param-scale')) param.scale = e.target.value;
    });
    addParamBtn.addEventListener('click', () => {
        const key = addParamKey.value.trim();
        if (!/^[A-Za-z0-9_]+(\[\d+\])?$/.test(key)) { alert("Use an MDU key, 'Key_factor' or 'Key[i]'."); return; }
        if (getCalibrationParam(key)) { alert(`'${key}' is already in the list.`); return; }
        const isFactor = key.endsWith('_factor');
        const param = { name: key, value: key, checked: true, lower: isFactor ? 0.5 : 0, upper: isFactor ? 1.5 : 1, scale: 'linear' };
        calibrationParams.push(param);
        container.insertAdjacentHTML('beforeend', parameterRowHtml(param)); addParamKey.value = '';
    });
}

export function plotComparison(container, data) {
    const dfSim = data.sim, dfObs = data.obs, merge = data.merge, minMax = data.min_max;
    const depths = data.depth, metrics = data.metrics, totalRMSE = data.rmse;
    if (!container || !Array.isArray(dfSim) || !Array.isArray(dfObs) ||
        !Array.isArray(depths) || depths.length === 0) {
        if (container) { container.innerHTML = '<p>No simulation or observation data available.</p>'; }
        return;
    }
    if (depths.length === 0) { container.innerHTML = '<p>No depth data available.</p>'; return; }
    const margin = { l: 65, r: 15, t: 65, b: 50 };
    container.innerHTML = `<div class="comparison-grid"></div>`;
    const chartGrid = container.querySelector('.comparison-grid');
    // Temperature plots
    depths.forEach((d, idx) => {
        const chart = document.createElement('div'); 
        chart.id = `comparison-${idx}`; chart.className = 'comparison-chart'; 
        chartGrid.appendChild(chart); 
        const obsCol = `obs_${d}`, simCol = `sim_${d}`, traces = [];
        const metric = metrics[String(d)];
        const rmse = metric?.[0], r2 = metric?.[1], bias = metric?.[2];
        // Observation
        if (Array.isArray(dfObs) && dfObs.length > 0) { 
            const x = [], y = []; 
            dfObs.forEach(row => { 
                const time = stringToUTC(row.Time), rawValue = row[obsCol];
                // Skip missing observation values
                if (rawValue === null || rawValue === undefined || rawValue === '' ||
                    (typeof rawValue === 'string' && rawValue.trim() === '')
                ) { return; }
                const value = Number(rawValue);
                if (time && Number.isFinite(value)) { x.push(time); y.push(value); } 
            }); 
            if (x.length > 0) { 
                traces.push({ 
                    x, y, type: 'scatter', mode: 'lines+markers', name: `Observation`,
                    line: { color: '#d62728', width: 1.4 }, marker: { size: 3 }, 
                    opacity: 0.85, hovertemplate: 'Observation: %{y:.3f}°C<extra></extra>',
                });
            }
        }
        // Simulation
        if (Array.isArray(dfSim) && dfSim.length > 0) { 
            const x = [], y = []; 
            dfSim.forEach(row => { 
                const time = stringToUTC(row.Time), value = Number(row[simCol]); 
                if (time && Number.isFinite(value)) { x.push(time); y.push(value); } 
            }); 
            if (x.length > 0) { 
                traces.push({ 
                    x, y, type: 'scatter', mode: 'lines+markers', 
                    opacity: 0.85, name: `Simulation`, marker: { size: 3 },
                    line: { color: '#1f77b4', width: 1.4 }, 
                    hovertemplate: 'Simulation: %{y:.3f}°C<extra></extra>',
                });
            }
        }
        // Layout
        const layout = {
            title: {
                text: `<b>Temperature Comparation - Station: ${data.station}<br>Water Depth: ${d}m </b>`,
                font: { size: 15, weight: "bold" }
            },
            xaxis: {
                title: {text: `<b>Time<b>`, standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                type: 'date', showgrid: true, gridcolor: 'rgba(0,0,0,0.15)',
                linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            },
            yaxis: {
                title: {text: `<b>Temperature (°C)<b>`, standoff: 10, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showgrid: true, gridcolor: 'rgba(0,0,0,0.15)',
                linecolor: 'black', ticks: 'outside', ticklen: 6, 
                tickwidth: 1, tickcolor: 'black',
            },
            legend: {
                x: 0.03, y: 0.95, xanchor: 'left', yanchor: 'top', bgcolor: 'rgba(255,255,255,0.65)',
                bordercolor: 'rgba(128,128,128,0.4)', borderwidth: 1, font: { size: 12 }
            },
            annotations: [{
                x: 0.45, y: 1.03, xref: 'paper', yref: 'paper', xanchor: 'center', yanchor: 'top',
                text: `(RMSE = ${rmse == null ? '–' : Number(rmse).toFixed(3)}°C · Bias = ${bias == null ? '–' : (bias > 0 ? '+' : '') + Number(bias).toFixed(2)}°C · R² = ${r2 == null ? '–' : Number(r2).toFixed(3)})`,
                showarrow: false, align: 'center', font: { size: 12 }, 
            }],
            margin: margin, autosize: true, hovermode: 'x unified'
        };
        const config = { responsive: true, displaylogo: false };
        Plotly.newPlot(chart, traces, layout, config).then(() => {
            requestAnimationFrame(() => { Plotly.Plots.resize(chart)})
        });
    });
    // Parity plot
    const parityChart = document.createElement('div');
    parityChart.className = 'comparison-chart';
    chartGrid.appendChild(parityChart);
    const traces = [], minValue = minMax[0], maxValue = minMax[1];
    const depthColors = [ '#1f77b4', '#ff7f0e', '#2ca02c',
        '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
    ];
    depths.forEach((depth, index) => {
        const rows = merge[String(depth)];
        if (!Array.isArray(rows) || rows.length === 0) { return; }
        const obsCol = `obs_${depth}`, simCol = `sim_${depth}`;
        const x = [], y = [];
        rows.forEach(row => {
            const obs = Number(row[obsCol]), sim = Number(row[simCol]);
            if (Number.isFinite(obs) && Number.isFinite(sim)) { x.push(obs); y.push(sim); }
        });
        if (x.length === 0) { return; }
        traces.push({
            x, y, type: 'scatter', mode: 'markers', name: `Depth ${depth} m`,
            marker: {
                color: depthColors[index % depthColors.length], size: 6, opacity: 0.5
            }
        });
    });
    // Add 1:1 line
    traces.push({
        x: [minValue, maxValue], y: [minValue, maxValue], 
        type: 'scatter', mode: 'lines', name: 'Line 1:1', showlegend: false,
        line: { color: 'black', dash: 'dash', width: 1.5 }, hoverinfo: 'skip'
    });
    // Plotly layout
    const layout = {
        title: {
            text:
                `<b>Simulation vs. Observation (Station: ${data.station})</b><br>` +
                `Total RMSE = ${totalRMSE.toFixed(3)}°C` + `</span>`,
            font: { size: 15, weight: "bold" }
        },
        xaxis: {
            title: {text: `<b>Observation (°C)<b>`, standoff: 15, 
                font: { size: 16, family: 'Arial', weight: 700}
            }, 
            range: [minValue, maxValue], showline: true, 
            linewidth: 1, linecolor: 'black', ticks: 'outside',
            ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
        },
        yaxis: {
            title: {text: `<b>Simulation (°C)<b>`, standoff: 10, 
                font: { size: 16, family: 'Arial', weight: 700}
            },
            range: [minValue, maxValue], showline: true, linewidth: 1, 
            linecolor: 'black', ticks: 'outside', ticklen: 6, 
            tickwidth: 1, tickcolor: 'black',
        },
        legend: {
            x: 0.03, y: 0.95, xanchor: 'left', yanchor: 'top', 
            bgcolor: 'rgba(228, 221, 221, 0.5)',
            bordercolor: 'gray', borderwidth: 1, font: { size: 12 }
        },
        margin: margin, autosize: true, hovermode: 'closest'
    };
    const config = { responsive: true, displaylogo: false };
    Plotly.newPlot(parityChart, traces, layout, config).then(() => {
        requestAnimationFrame(() => { Plotly.Plots.resize(parityChart)})
    });
}

export function plotOptuna(container, trials, importance, key) {
    if (!container || !Array.isArray(trials) || trials.length === 0) {
        container.innerHTML = '<p>No optimal trial data available.</p>'; return;
    }
    const validTrials = trials.filter(
        trial =>
            trial.value !== null && trial.value !== undefined &&
            Number.isFinite(Number(trial.value))
    );
    if (validTrials.length === 0) {
        container.innerHTML = '<p>No valid trial results available.</p>'; return;
    }
    const values = validTrials.map(trial => Number(trial.value));
    const trialNumbers = validTrials.map((trial, index) => trial.number ?? index);
    const bestValue = Math.min(...values);
    container.innerHTML = `
        <div class="optuna-chart-grid">
            <div class="optuna-chart" id="optuna-convergence"></div>
            <div class="optuna-chart" id="optuna-distribution"></div>
            <div class="optuna-chart" id="optuna-importance"></div>
            <div class="optuna-chart" id="optuna-sensitivity"></div>
        </div>
    `;
    let currentBest = Infinity, name, y1, x2, unit;
    const cumulativeBest = values.map(value => {
        currentBest = Math.min(currentBest, value); return currentBest;
    });
    if (key === 'RMSE') {
        y1 = 'Smallest RMSE (°C)'; x2 = 'RMSE (°C)'; name = 'RMSE'; unit = ' (°C)';
    } else if (key === 'R2') {
        y1 = 'Highest R²'; x2 = 'R²'; name = 'R²'; unit = '';
    }
    // Convergence Process
    Plotly.newPlot(
        'optuna-convergence',
        [{
            x: trialNumbers, y: cumulativeBest, type: 'scatter',
            mode: 'lines', name: 'Best target so far', line: { width: 2 }
        }],
        {
            title: {
                text: "Optimal Convergence Process", font: { size: 20, weight: "bold" }
            },
            xaxis: { 
                title: {text: '<b>Iterations (Trials)<b>', standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            }, 
            yaxis: { 
                title: {text: `<b>${y1}<b>`, standoff: 10, 
                    font: { size: 16, family: 'Arial', weight: 700 }
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black',
            },
            margin: { l: 70, r: 30, t: 60, b: 60 }, hovermode: 'x unified'
        },
        { responsive: true, displaylogo: false }
    );
    // Error handling for the Optuna distribution plot
    Plotly.newPlot(
        'optuna-distribution',
        [{
            x: values, type: 'histogram', name: name, nbinsx: 25, opacity: 0.75
        },
        {
            x: [bestValue, bestValue], y: [0, Math.max(1, values.length)],
            type: 'scatter', mode: 'lines', name: `Best: ${bestValue.toFixed(4)}`,
            line: { dash: 'dash', width: 2 }
        }],
        {
            title: {
                text: 'Error Distribution Across Trials', font: { size: 20, weight: "bold" }
            },
            xaxis: { 
                title: {text: `<b>${x2}<b>`, standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            }, 
            yaxis: { 
                title: {text: '<b>Number of Trials<b>', standoff: 10, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black',
            },
            legend: {
                x: 1, y: 1, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(255,255,255,0.7)', bordercolor: 'gray', borderwidth: 1
            },
            margin: { l: 70, r: 30, t: 60, b: 60 }, bargap: 0.05
        },
        { responsive: true, displaylogo: false }
    );
    const importanceNames = importance.map(item => item[0]);
    const importanceValues = importance.map(item => item[1]);
    // Parameter Importance
    Plotly.newPlot(
        'optuna-importance',
        [{
            x: importanceValues, y: importanceNames, type: 'bar', orientation: 'h',
            marker: {
                color: importanceValues, colorscale: 'Viridis', showscale: false
            },
            text: importanceValues.map(value => `${(value * 100).toFixed(1)}%`),
            textposition: 'outside', font: { size: 14 },
            hovertemplate:'%{y}<br>Importance: %{x:.3f}<extra></extra>'
        }],
        {
            title: { text: 'Importance of Parameters', font: { size: 20, weight: "bold" }},
            xaxis: {
                title: {text: '<b>Relative Importance<b>', standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                range: [0, Math.max(...importanceValues, 0.1) * 1.15],
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            }, 
            yaxis: {
                title: {text: '', standoff: 10},
                tickfont: { size: 17, family: 'Arial', weight: 700 },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black',
            },
            margin: { l: 100, r: 60, t: 60, b: 60 }
        },
        { responsive: true, displaylogo: false }
    );
    const mostImportantParam = importanceNames.length > 0 
        ? importanceNames[importanceNames.length - 1] : null;
    if (!mostImportantParam) {
        document.getElementById('optuna-sensitivity').innerHTML =
            '<p>No parameter data available.</p>';
        return;
    }
    const sensitivityColumn = `params_${mostImportantParam}`;
    const sensitivityData = validTrials
        .map(trial => ({
            x: Number(trial[sensitivityColumn]), y: Number(trial.value), trial: trial.number
        }))
        .filter(item => Number.isFinite(item.x) && Number.isFinite(item.y));
    const bestTrial = validTrials.reduce(
        (best, trial) => Number(trial.value) < Number(best.value) ? trial : best, validTrials[0]
    );
    const bestParamValue = Number(bestTrial[sensitivityColumn]);
    const xLabel = calibrationParams.find(
        param => param.value === mostImportantParam)?.name ?? mostImportantParam;
    Plotly.newPlot(
        'optuna-sensitivity',
        [{
            x: sensitivityData.map(item => item.x), y: sensitivityData.map(item => item.y),
            type: 'scatter', mode: 'markers', name: 'Trials',
            marker: { 
                size: 6, opacity: 0.9, color: sensitivityData.map(item => item.y),
                colorscale: 'RdBu', showscale: false,
                colorbar: {title: { text: `<b>${name}${unit}</b>`, side: 'right' }}
            },
            text: sensitivityData.map(item => `Trial ${item.trial}`),
            hovertemplate:
                `${xLabel}: %{x}<br>` + `${name}: %{y:.4f}${unit}<br>` + `%{text}<extra></extra>`
        },
        {
            x: [bestParamValue, bestParamValue], y: [Math.min(...values),Math.max(...values)],
            type: 'scatter', mode: 'lines', name: `Best: ${numberFormatter(bestParamValue)}`,
            line: { dash: 'dash', width: 2 }
        }],
        {
            title: {
                text: `Sensitivity Analysis: ${xLabel} - ${mostImportantParam}`,
                font: { size: 20, weight: "bold" }
            },
            xaxis: { 
                title: {text: `<b>${xLabel} (${mostImportantParam})<b>`, standoff: 15, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black', showgrid: true,
            }, 
            yaxis: { 
                title: {text: `<b>${x2}<b>`, standoff: 10, 
                    font: { size: 16, family: 'Arial', weight: 700}
                },
                showline: true, linewidth: 1, linecolor: 'black', ticks: 'outside',
                ticklen: 6, tickwidth: 1, tickcolor: 'black',
            },
            legend: {
                x: 1, y: 1, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(255,255,255,0.7)', bordercolor: 'gray', borderwidth: 1
            },
            margin: { l: 70, r: 30, t: 60, b: 60 }, hovermode: 'closest'
        },
        { responsive: true, displaylogo: false }
    );
}

export function updateLogOptimization(project, key, logObject, progressBarObject, progressTextObject, resultsObject, seconds, logFile, onFinish=false) {
    activeProject = project; running = true; lastOffsetHYD = 0;
    // Prevent multiple polling intervals
    if (logInterval !== null) { clearInterval(logInterval); logInterval = null; }
    const pollStatus = async () => {
        if (activeProject !== project) { clearInterval(logInterval); logInterval = null; }
        try {
            const content = { projectName: project, key: key };
            const statusRes = await jsonLoader('check_optimization_status_calibration', content);
            if (progressTextObject !== null) { progressTextObject.innerText = statusRes.message; }
            if (progressBarObject !== null) { progressBarObject.value = statusRes.progress; }
            if (statusRes.status !== "running") {
                logObject.value += statusRes.message; running = false;
                if (statusRes.best_params !== '' && statusRes.best_params) {
                    if (resultsObject) resultsObject.style.display = 'block';
                    // Update optimal parameter container
                    const bestParams = statusRes.best_params;
                    const container = document.querySelector('.parameter-optimal');
                    container.innerHTML = `
                        <div class="parameter-header-optimal">
                            <div>Parameter</div><div>Abbreviation</div><div>Value</div>
                        </div>
                        ${Object.entries(bestParams).map(([key, value]) => {
                            const param = calibrationParams.find(p => p.value === key) || { name: key, value: key };
                            return `
                                <div class="parameter-row-optimal" data-param="${param.value}">
                                    <div>${param.name}</div><div>${param.value}</div>
                                    <div><input type="text" value="${value}"></div>
                                </div>
                            `;
                        }).join('')}
                    `;
                }
                if (logInterval) { clearInterval(logInterval); logInterval = null; }
                if (statusRes.status === 'finished' && onFinish) { await onFinish(); }
            }
            const res = await fetch(`/calibration_log_tail/${project}?offset=${lastOffsetHYD}&log_file=${logFile}`);
            if (!res.ok) return;
            const data = await res.json();
            for (const line of data.lines) { logObject.value += line + "\n"; }
            lastOffsetHYD = data.offset;
        } catch (error) { 
            if (resultsObject) resultsObject.style.display = 'none';
            alert("Error: " + (error.message || error)); running = false;
            clearInterval(logInterval); logInterval = null; 
            if (progressTextObject !== null) { 
                progressTextObject.innerText = "Error: " + (error.message || error); 
            }
        }
    }
    logInterval = setInterval(pollStatus, seconds * 1000); pollStatus(); 
}

export function updateLogResampling(project, key, logArea, progressBarObject, progressTextObject, seconds){
    activeProject = project; running = true;
    // Prevent multiple polling intervals
    if (logInterval !== null) { clearInterval(logInterval); logInterval = null; }
    const pollStatus = async () => {
        if (activeProject !== project) { clearInterval(logInterval); logInterval = null; running = false; return; }
        try {
            const content = { projectName: project, key: key };
            const statusRes = await jsonLoader('check_calibration', content);
            // Update progress
            if (typeof statusRes.progress === 'number') {
                progressBarObject.value = Math.max(0, Math.min(100, statusRes.progress));
            }
            // Update message
            if (statusRes.message) { progressTextObject.innerText = statusRes.message; }
            if (statusRes.status === "finished" || statusRes.status === "stopped") {
                progressBarObject.value = statusRes.progress; running = false;
                progressTextObject.innerText = statusRes.message || 'Resampling completed.';
                clearInterval(logInterval); logInterval = null;
            }
            if (statusRes.status === "failed") {
                progressTextObject.innerText = statusRes.message || 'Resampling failed.';
                running = false; clearInterval(logInterval); logInterval = null;
            }
            if (logArea && typeof statusRes.his === 'string') logArea.value = statusRes.his;
        } catch (error) { 
            alert("Polling error: " + (error.message || error)); running = false;
            clearInterval(logInterval); logInterval = null; progressTextObject.innerText = 'Polling error.';
        }
    }
    pollStatus(); logInterval = setInterval(pollStatus, seconds * 1000);
}

export async function sensitivityRender(container, data, key) {
    if (!data || data.length === 0) { return; }
    const parameters = Object.keys(data[0]).filter(k => k !== key);
    const variables = [...parameters, key];
    const sensitivityColumns = 2, rowHeight = 300;
    const sensitivityRows = Math.ceil(parameters.length / sensitivityColumns);
    const traces = [], z = [], text = [];
    variables.forEach(v1 => {
        const row = [], textRow = [];
        variables.forEach(v2 => {
            const pairs = data
                .map(row => [Number(row[v1]), Number(row[v2])])
                .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
            const x = pairs.map(p => p[0]), y = pairs.map(p => p[1]);
            const corr = pearsonCorrelation(x, y);
            row.push(corr);
            textRow.push(Number.isFinite(corr) ? corr.toFixed(3) : "");
        });
        z.push(row); text.push(textRow);
    });
    // Reverse Y direction so first variable is at the top
    const zReversed = z.slice().reverse();
    const textReversed = text.slice().reverse();
    const yReversed = variables.slice().reverse();
    traces.push({
        type: "heatmap", z: zReversed, text: textReversed, texttemplate: "%{text}",
        x: variables, y: yReversed, xaxis: "x", yaxis: "y",
        zmin: -1, zmax: 1, colorscale: "RdBu", textfont: { size: 13 }, colorbar: { title: "Pearson" },
        hovertemplate: "%{x} vs %{y}<br>" + "r = %{z:.3f}" +  "<extra></extra>"
    });
    // Sensitivity plots
    parameters.forEach((parameter, index) => {
        const validData = data
            .map(row => ({ x: Number(row[parameter]), y: Number(row[key]) }))
            .filter(row => Number.isFinite(row.x) && Number.isFinite(row.y));
        if (validData.length === 0) { return; }
        const x = validData.map(d => d.x), y = validData.map(d => d.y);
        const row = Math.floor(index / 2), col = index % 2;
        const subplotIndex = row * 2 + col + 2;
        const xAxisName = subplotIndex === 1 ? "x" : `x${subplotIndex}`;
        const yAxisName = subplotIndex === 1 ? "y" : `y${subplotIndex}`;
        traces.push({
            type: "scatter", x: x, y: y, mode: "markers", xaxis: xAxisName,
            yaxis: yAxisName, marker: { size: 8, opacity: 0.7 }, 
            name: parameter, showlegend: false,
            hovertemplate: `${parameter}: %{x}<br>` + `${key}: %{y:.4f}` + `<extra></extra>`
        });
        if (validData.length >= 2) {
            const meanX = x.reduce((a, b) => a + b, 0) / x.length;
            const meanY = y.reduce((a, b) => a + b, 0) / y.length;
            let numerator = 0, denominator = 0;
            for (let i = 0; i < x.length; i++) {
                numerator += (x[i] - meanX) * (y[i] - meanY);
                denominator += (x[i] - meanX) ** 2;
            }
            if (denominator !== 0) {
                const slope = numerator / denominator;
                const intercept = meanY - slope * meanX;
                const minX = Math.min(...x), maxX = Math.max(...x);
                traces.push({
                    type: "scatter", x: [minX, maxX],
                    y: [slope * minX + intercept, slope * maxX + intercept],
                    mode: "lines", xaxis: xAxisName, yaxis: yAxisName,
                    line: { dash: "dash", width: 2 },
                    hoverinfo: "skip", showlegend: false
                });
            }
        }
    });
    const layout = {
        height: Math.max(500, sensitivityRows * rowHeight),
        margin: { l: 20, r: 30, t: 80, b: 60 },
        title: {
            text: "Correlation Analysis",
            font: { size: 24, weight: "bold" }
        },
        xaxis: {
            domain: [0.00, 0.28], tickangle: 0, 
            automargin: true, showgrid: false, fixedrange: true
        },
        yaxis: {
            domain: [0.20, 0.80], tickangle: -90,
            automargin: true, showgrid: false, fixedrange: true
        }, annotations: []
    };
    parameters.forEach((parameter, index) => {
        const validData = data
            .map(row => ({ x: Number(row[parameter]), y: Number(row[key])}))
            .filter(row => Number.isFinite(row.x) && Number.isFinite(row.y));
        if (validData.length === 0) { return; }
        const x = validData.map(d => d.x), y = validData.map(d => d.y);
        const r = pearsonCorrelation(x, y);
        const row = Math.floor(index / 2), col = index % 2;
        const subplotIndex = row * 2 + col + 2;
        const xAxisName = subplotIndex === 1 ? "xaxis" : `xaxis${subplotIndex}`;
        const yAxisName = subplotIndex === 1 ? "yaxis" : `yaxis${subplotIndex}`;
        const xDomain = col === 0 ? [0.34, 0.64] : [0.70, 1.00], gap = 0.21;
        const plotHeight = (1 - gap * (sensitivityRows - 1)) / sensitivityRows;
        const yTop = 1 - row * (plotHeight + gap), yBottom = yTop - plotHeight;
        layout[xAxisName] = {
            domain: xDomain,
            anchor: subplotIndex === 2 ? "y2" : `y${subplotIndex}`,
            title: {text: `<b>${parameter}</b>`, font: { size: 13 }, standoff: 0 },
            tickangle: 0, automargin: true, showgrid: true,
            zeroline: true, showline: true, ticks: "outside"
        };
        layout[yAxisName] = {
            domain: [ yBottom, yTop ],
            anchor: subplotIndex === 2 ? "x2" : `x${subplotIndex}`,
            title: {text: `<b>${key}</b>`, font: { size: 13 }, standoff: 0 },
            tickangle: -90, tickpadding: 0, ticklen: 3, 
            automargin: true, showgrid: true
        };
        const annotationX = col === 0 ? 0.495 : 0.85;
        layout.annotations.push({
            x: annotationX, y: yTop - 0.015, xref: "paper", yref: "paper",
            xanchor: "center", yanchor: "bottom",
            text:
                `<b>${parameter} vs ${key}</b><br>` +
                `<span style="font-size:12px">` +
                `Pearson r = ${r.toFixed(3)}</span>`,
            showarrow: false, font: { size: 15 }
        });
    });
    Plotly.newPlot(
        container, traces, layout, { responsive: true, displaylogo: false }
    );
}

function pearsonCorrelation(x, y) {
    if (x.length !== y.length || x.length < 2) { return NaN; }
    const meanX = x.reduce((a, b) => a + b, 0) / x.length;
    const meanY = y.reduce((a, b) => a + b, 0) / y.length;
    let numerator = 0, denominatorX = 0, denominatorY = 0;
    for (let i = 0; i < x.length; i++) {
        const dx = x[i] - meanX, dy = y[i] - meanY;
        numerator += dx * dy; denominatorX += dx * dx; denominatorY += dy * dy;
    }
    const denominator = Math.sqrt(denominatorX * denominatorY);
    if (denominator === 0) { return NaN; }
    return numerator/Math.sqrt(denominatorX * denominatorY);
}