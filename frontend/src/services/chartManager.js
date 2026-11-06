import { jsonLoader, splitLines, colorbarTicks, formatDateTime, 
    interpolateJet, signalSender, numberFormatter
} from "./commonFunctions.js";
import { setStateVisualization, getColors, valueFormatter, getLastTimeZone } from "./constant.js";

let globalChartData = {
    title: "", data: null, checkBox: null, selectBox:null, 
    titleX: "", titleY: "", validColumns: []
};
let animationToken = 0, animating = false, frameIndex = 0, 
    colorTicks = [], colorTickLabels = [], nColors, duration;

export async function plotTimeSeries(plotContainer, title, data, titleChart, 
    titleX='Time', titleY='Value', selectedColumns=null) {
    const {columns, rows} = data;
    if (rows.length === 0) { alert('No data to plot. Please check the table.'); return; }
    const $ = (selector) => plotContainer.querySelector(selector);
    const obj = {
        dropdown: $(".select-object"), selectBox:$(".select-box"),
        titlePlot: $(".plot-title"), checkboxList: $(".checkbox-list"), 
        chartDiv: $("#myChart"), viewDataBtn: $("#viewDataBtn"),
        downloadBtn: $("#downloadExcel")
    };
    // Draw the chart using Plotly
    let checkboxInputs = obj.checkboxList.querySelectorAll('input[type="checkbox"]');
    if (selectedColumns === null) { checkboxInputs = []; checkboxInputs.legend = 0; }
    // Create checkbox list
    if (checkboxInputs.length === 0) {
        const validColumns = [];
        for (let i = 1; i < columns.length; i++) {
            const y = rows.map(r => r[i]);
            const hasValid = y.some(val => val !== null && !isNaN(val));
            if (hasValid) validColumns.push(data.columns[i]);
        }
        // Update global variable
        globalChartData = { 
            title: title, data: data, checkBox: obj.checkboxList, selectBox: obj.selectBox, 
            titleX: titleX, titleY: titleY, validColumns: validColumns 
        };
        createCheckboxList(plotContainer, obj.checkboxList, titleChart, validColumns);
        checkboxInputs = obj.checkboxList.querySelectorAll('input[type="checkbox"]');
    }
    // Get selected columns
    if (!selectedColumns) {
        selectedColumns = Array.from(checkboxInputs)
            .filter(cb => cb.checked && cb.value !== 'All').map(cb => cb.value);
    }
    const allCheckbox = Array.from(checkboxInputs).find(cb => cb.value === 'All');
    let drawColumns;
    if (allCheckbox && allCheckbox.checked) drawColumns = columns.slice(1);
    else drawColumns = selectedColumns;
    if (drawColumns.length === 0) { Plotly.purge(obj.chartDiv); return; }
    renderChart(obj.chartDiv, columns, rows, drawColumns, titleChart, titleX, titleY);
    // Update title
    plotContainer.style.display = 'flex'; obj.titlePlot.innerHTML = title;
    // Download chart
    if (!obj.viewDataBtn.dataset.bound) {
        obj.viewDataBtn.addEventListener("click", () => viewDatafromPlot(obj.chartDiv));
        obj.viewDataBtn.dataset.bound = true;
    }
    // Download data as Excel
    if (!obj.downloadBtn.dataset.bound) {
        obj.downloadBtn.addEventListener("click", () => saveToExcelFromPlot(obj.chartDiv));
        obj.downloadBtn.dataset.bound = true;
    }
    // Open dropdown
    if (!obj.selectBox.dataset.bound) {
        obj.selectBox.addEventListener("click", () => {
            obj.checkboxList.style.display = 
                obj.checkboxList.style.display === 'block' ? 'none' : 'block';
        });
        obj.selectBox.dataset.bound = true;
    }
    // Close dropdown when click outside
    plotContainer.addEventListener('click', e => {
        if (!obj.dropdown.contains(e.target)) obj.checkboxList.style.display = 'none';
    });
}

export function createCheckboxList(plotContainer, checkboxObj, titleChart, columns) {
    checkboxObj.innerHTML = '';
    // Create "All" checkbox
    const allLabel = document.createElement('label');
    allLabel.innerHTML = `<input type="checkbox" value="All" checked> All`;
    const allCheckbox = allLabel.querySelector('input');
    checkboxObj.appendChild(allLabel);
    // Create checkbox for each column
    const colCheckBoxes = [];
    columns.forEach(col => {
        const label = document.createElement('label');
        label.innerHTML = `<input type="checkbox" value="${col}"> ${col}`;
        const cb = label.querySelector('input'); cb.checked = true;
        checkboxObj.appendChild(label); colCheckBoxes.push(cb);
    });
    // Select all columns by default
    allCheckbox.addEventListener('change', () => {
        if (allCheckbox.checked) colCheckBoxes.forEach(cb => cb.checked = true);
        else colCheckBoxes.forEach(cb => cb.checked = false);
        updateChart(plotContainer, checkboxObj, titleChart);
        checkboxObj.style.display = 'none';
    })
    // Select other columns
    colCheckBoxes.forEach(cb => {
        cb.addEventListener('change', () => {
            allCheckbox.checked = colCheckBoxes.every(cb => cb.checked);
            updateChart(plotContainer, checkboxObj, titleChart);
            checkboxObj.style.display = 'none';
        });
    });
}

async function updateChart(plotContainer, checkboxObj, titleChart) {
    const { data, titleX, titleY } = globalChartData;
    const { columns, rows } = data;
    const checkboxes = checkboxObj.querySelectorAll('input[type="checkbox"]');
    const selectedColumns = Array.from(checkboxes)
        .filter(cb => cb.checked && cb.value !== 'All').map(cb => cb.value);
    const chartDiv = plotContainer.querySelector("#myChart");
    if (selectedColumns.length === 0) { Plotly.purge(chartDiv); return; }
    renderChart(chartDiv, columns, rows, selectedColumns, titleChart, titleX, titleY);
}

function renderChart(chartDiv, columns, rows, drawColumns, titleChart, titleX, titleY) {
    const x = rows.map(r => r[0]);
    let traceIndex = 0;
    const traces = [], n = drawColumns.length;  
    for (const colName of drawColumns) {
        const i = columns.indexOf(colName);
        if (i === -1) continue;
        const y = rows.map(r => r[i]);
        const t = n <= 1 ? 0 : traceIndex / (n - 1);
        const color = interpolateJet(1-t);
        traces.push({ 
            x: x, y: y, name: columns[i], type: 'scatter', 
            mode: 'lines', line: { color: color } 
        });
        traceIndex++;
    }
    if (traces.length === 0) { Plotly.purge(chartDiv); return; }
    const layout = {
        margin: {l: 60, r: 20, t: 50, b: 20}, 
        paper_bgcolor: '#c2bdbdff', plot_bgcolor: '#c2bdbdff',
        title: { 
            text: titleChart, x: 0.5, xanchor: 'center', 
            font: { size: 20, color: 'black', weight: 'bold' } 
        },
        xaxis: {
            title:{ text: titleX, font: { size: 16, weight: 'bold', color: 'black' }},
            showgrid: false, linecolor: 'black', tickfont: { color: 'black' },
            automargin: true, ticks: 'outside', linewidth: 1, tickmode: 'auto'
        },
        yaxis: {
            title:{ text: titleY, automargin: true, 
                font: { size: 16, weight: 'bold', color: 'black' }
            }, 
            showgrid: false, linecolor: 'black', tickfont: { color: 'black' },
            automargin: true, ticks: 'outside', linewidth: 1, tickmode: 'auto'
        },
        legend: { 
            orientation: 'v', x: 1.02, xanchor: 'left', y: 1, yanchor: 'top',
            font: { size: 14, color: 'black', weight: 'bold' } 
        }
    };
    const config = { responsive: true, displaylogo: false };
    setTimeout(() => {
        Plotly.react(chartDiv, traces, layout, config);
        new ResizeObserver(() => {
            Plotly.Plots.resize(chartDiv);
        }).observe(chartDiv.parentElement);
    }, 50);
}

// Export chart data to new tab as CSV format
export function viewDatafromPlot(plotDiv) {
    signalSender('showOverlay', "Getting data from plot.\nPlease wait...");
    // Get data
    const traces = plotDiv.data;
    if (!traces || traces.length === 0) { alert("No data to view."); return; }
    const numTraces = traces.length;
    const titleY = plotDiv.layout?.yaxis?.title?.text || "Value";
    const xTitle = plotDiv.layout?.xaxis?.title?.text || "Time";
    // Header
    let headers = [xTitle];
    traces.forEach((trace, i) => { headers.push(trace.name || `Series_${i}`); });
    const xValues = traces[0].x || [];
    let csvContent = headers.join(",") + "\n";
    for (let i = 0; i < xValues.length; i++) {
        let rawTime = xValues[i];
        let formattedTime = rawTime ? formatDateTime(rawTime) : "";
        let row = [formattedTime];
        for (let j = 0; j < numTraces; j++) {
            const yArr = traces[j].y || [];
            let value = yArr[i];
            if (value === null || value === undefined || isNaN(value)) {
                row.push("");
            } else { row.push(numberFormatter(value, 5)); }
        }
        csvContent += row.join(",") + "\n";
    }
    const newWindow = window.open("", "_blank");
    if (newWindow) {
        const doc = newWindow.document;
        doc.title = titleY.split(' (')[0];
        const pre = doc.createElement("pre");
        pre.style.fontFamily = "monospace";
        pre.style.whiteSpace = "pre-wrap";
        pre.textContent = csvContent;
        doc.body.appendChild(pre);
    } else { alert("Pop-up blocked. Please allow popups for this site."); }
    signalSender('hideOverlay');
}

// Save to Excel
export function saveToExcelFromPlot(plotDiv) {
    signalSender('showOverlay', "Downloading data as Excel file.\nPlease wait...");
    const traces = plotDiv.data;
    if (!traces || traces.length === 0) { alert("No data to view."); return; }
    // Get the y values
    const numTraces = plotDiv.data.length;
    const title = plotDiv.layout?.title?.text || "Chart";
    const titleText = typeof title === "string"
        ? (title.includes(':') ? title.split(':')[1].trim() : title): "Chart";
    const titleY = plotDiv.layout?.yaxis?.title?.text || "Value";
    // Prepare the data
    const title_ = plotDiv.layout?.xaxis?.title?.text || 'Unknown';
    const headers = [title_];
    for (let i = 0; i < numTraces; i++) {
        const traceName = plotDiv.data[i].name || `${titleText}_${titleY}_${i}`;
        headers.push(traceName);
    }
    const table = [headers], numPoints = plotDiv.data[0].x.length;
    for (let i = 0; i < numPoints; i++) {
        const row = [plotDiv.data[0].x[i]];
        for (let j = 0; j < numTraces; j++) {
            row.push(numberFormatter(plotDiv.data[j].y[i], 4));
        }
        table.push(row);
    }
    // Create workbook and worksheet
    const worksheet = XLSX.utils.aoa_to_sheet(table);
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "ChartData");
    // Download the Excel file
    XLSX.writeFile(workbook, `${titleY.split(' (')[0]}.xlsx`);
    signalSender('hideOverlay');
}

export async function plotChart(projectName, plotContainer, query, key, chartTitle, titleX, titleY) {
    signalSender('showOverlay', 'Preparing Data for Chart.\nPlease wait...');
    const content = { projectName: projectName, key: key, query: query, timeZone: getLastTimeZone() };
    const response = await jsonLoader('process_data', content);
    if (response.status === 'error') { signalSender('hideOverlay'); alert(response.message); return; }
    plotTimeSeries(plotContainer, chartTitle, response.content, chartTitle, titleX, titleY);
    signalSender('hideOverlay');
}

export function plotProfileSingleLayer(plotContainer, pointContainer, polygonCentroids, title, titleX, titleY) {
    const interpolatedPoints = splitLines(pointContainer, polygonCentroids, 20).map(([dist, val]) => [dist, val]);
    const data = { columns: ['index', titleY], rows: interpolatedPoints };
    plotTimeSeries(plotContainer, title, data, title, titleX, titleY);
}

export function plotProfileMultiLayer(projectName, profileContainer, key, query, data, title, unit) { 
    animationToken++;
    const myToken = animationToken;
    if (profileContainer._resizeObserver) {
        profileContainer._resizeObserver.disconnect();
        profileContainer._resizeObserver = null;
    }
    const colorCombobox = profileContainer.querySelector('#chart-color-combobox');
    const minValue = profileContainer.querySelector('#chart-min-value');
    const maxValue = profileContainer.querySelector('#chart-max-value');
    const colorComboLabel = profileContainer.querySelector('#chart-color-label');
    const minLabel = profileContainer.querySelector('#chart-min-label');
    const maxLabel = profileContainer.querySelector('#chart-max-label');
    colorCombobox.style.display = "block";  minValue.style.display = "block"; 
    maxValue.style.display = "block"; colorComboLabel.style.display = "block"; 
    minLabel.style.display = "block"; maxLabel.style.display = "block";
    const { timestamps, distance, values, depths, local_minmax } = data;
    minValue.value = valueFormatter(local_minmax[0], 1e-3);
    maxValue.value = valueFormatter(local_minmax[1], 1e-3);
    nColors = parseInt(colorCombobox.value);
    // Set up time slider
    const timeSlider = profileContainer.querySelector('#time-slider');
    const timeLabelStart = profileContainer.querySelector('#time-start');
    const timeLabelEnd = profileContainer.querySelector('#time-end');
    const timeLabel = profileContainer.querySelector('#time-center');
    timeSlider.min = 0; timeSlider.max = timestamps.length - 1;
    // timeSlider.step = 1; timeSlider.value = 0;
    timeLabelStart.textContent = `Start: ${timestamps[0]}`;
    timeLabelEnd.textContent = `End: ${timestamps[timestamps.length - 1]}`;
    timeLabel.textContent = `Time: ${timestamps[0]}`;
    // Render plot
    const chartDiv = profileContainer.querySelector('#chart-div');
    const controlBtn = profileContainer.querySelector('#profile-btn');
    const durationValue = profileContainer.querySelector('#chart-duration-value');
    const profileTitle = profileContainer.querySelector('#profile-title');
    profileContainer._resizeObserver = renderPlotMulti(chartDiv, distance, depths, 
            values, local_minmax[0], local_minmax[1], nColors, title, unit);
    // Change header title of window
    profileTitle.textContent = 'Profile Chart';
    // Update a single frame
    async function updateMultiLayerFrame(index) {
        if (myToken !== animationToken) return;
        const queryContents = { 
            key: key, query: query, idx: index, 
            projectName: projectName, timeZone: getLastTimeZone()
        };
        const data = await jsonLoader('select_meshes', queryContents);
        if (data.status === "error") { 
            alert(data.message); animating = false;
            controlBtn.textContent = '▶ Play'; return;
        }
        const { values, local_minmax } = data.content;
        minValue.value = valueFormatter(local_minmax[0], 1e-3); 
        maxValue.value = valueFormatter(local_minmax[1], 1e-3);
        nColors = parseInt(colorCombobox.value);
        const discreteColors = getColors(nColors);
        const colorScale = [], step = 1 / nColors;
        for (let i = 0; i < nColors; i++) {
            colorScale.push([i * step, discreteColors[i]]);
            colorScale.push([(i + 1) * step, discreteColors[i]]);
        }
        // Update the frame
        colorTicks = colorbarTicks(local_minmax[0], local_minmax[1], nColors);
        colorTickLabels = colorTicks.map(v => valueFormatter(v, 1e-3));
        await Plotly.update(chartDiv, { z: [values], zmin: [local_minmax[0]], 
            zmax: [local_minmax[1]], colorscale: [colorScale], showscale: [true], 
            colorbar: [{ title: { text: unit, font: { color: 'black' } }, tickvals: colorTicks, 
                ticktext: colorTickLabels, tickfont: { color: 'black' } }]
        }, {}, [0]);
        // Update time slider
        timeSlider.value = index; timeLabel.textContent = `Time: ${timestamps[index]}`;
    }
    // === Play / Pause control === 
    async function playAnimation() { 
        duration = parseFloat(durationValue.value)*1000
        while (animating && frameIndex < timestamps.length && myToken === animationToken) { 
            await updateMultiLayerFrame(frameIndex);
            frameIndex++;
            await new Promise(r => setTimeout(r, duration)); 
        }
        if (myToken !== animationToken) return;
        if (frameIndex >= timestamps.length) { 
            animating = false; controlBtn.textContent = '▶ Play'; 
            frameIndex = 0; // Reset index
        }
    }
    controlBtn.onclick = () => { 
        if (!animating){ 
            animating = true; controlBtn.textContent = '⏸ Pause'; 
            playAnimation(); 
        } else { animating = false; controlBtn.textContent = '▶ Play'; } 
    }
    // === Slider control === 
    timeSlider.addEventListener('input', async(e) => {
        animating = false; controlBtn.textContent = '▶ Play';
        frameIndex = parseInt(e.target.value);
    });
    // === Duration control ===
    durationValue.addEventListener('change', () => { 
        animating = false; controlBtn.textContent = '▶ Play';
    });
    // === Color control === 
    colorCombobox.addEventListener('change', async() => { 
        animating = false; controlBtn.textContent = '▶ Play';
        const queryContents = { 
            key: key, query: query, idx: frameIndex, 
            projectName: projectName, timeZone: getLastTimeZone()
        };
        const refreshed = await jsonLoader('select_meshes', queryContents);
        if (refreshed.status === "error") { alert(data.message); return; }
        const { values, local_minmax } = refreshed.content;
        minValue.value = valueFormatter(local_minmax[0], 1e-3); 
        maxValue.value = valueFormatter(local_minmax[1], 1e-3);
        renderPlotMulti(chartDiv, distance, depths, values, local_minmax[0],
            local_minmax[1], parseInt(colorCombobox.value), title, unit); 
    })
    profileContainer.style.display = "flex";
}

export function thermoclinePlotter(projectName, profileContainer, key, 
    data, name, titleX, titleY, chartTitle) {
    animationToken++;
    const myToken = animationToken;
    if (profileContainer._resizeObserver) {
        profileContainer._resizeObserver.disconnect();
        profileContainer._resizeObserver = null;
    }
    // Hide components
    const colorCombobox = profileContainer.querySelector('#chart-color-combobox');
    const minValue = profileContainer.querySelector('#chart-min-value');
    const maxValue = profileContainer.querySelector('#chart-max-value');
    const colorComboLabel = profileContainer.querySelector('#chart-color-label');
    const minLabel = profileContainer.querySelector('#chart-min-label');
    const maxLabel = profileContainer.querySelector('#chart-max-label');
    colorCombobox.style.display = "none"; minValue.style.display = "none"; 
    maxValue.style.display = "none"; colorComboLabel.style.display = "none"; 
    minLabel.style.display = "none"; maxLabel.style.display = "none";
    let animating = false, frameIndex = 0, duration;
    const { timestamps, depths, values } = data;
    // Set up time slider
    const timeSlider = profileContainer.querySelector('#time-slider');
    const timeLabelStart = profileContainer.querySelector('#time-start');
    const timeLabelEnd = profileContainer.querySelector('#time-end');
    const timeLabel = profileContainer.querySelector('#time-center');
    timeSlider.min = 0; timeSlider.max = timestamps.length - 1;
    timeSlider.step = 1; timeSlider.value = 0;
    timeLabelStart.textContent = `Start: ${timestamps[0]}`;
    timeLabelEnd.textContent = `End: ${timestamps[timestamps.length - 1]}`;
    timeLabel.textContent = `Time: ${timestamps[0]}`;
    // Render plot
    const chartDiv = profileContainer.querySelector('#chart-div');
    const controlBtn = profileContainer.querySelector('#profile-btn');
    const durationValue = profileContainer.querySelector('#chart-duration-value');
    const profileTitle = profileContainer.querySelector('#profile-title');
    profileContainer._resizeObserver = renderThermocline(
        key, chartDiv, values, depths, name, titleX, titleY, chartTitle
    );
    // Change header title of window
    profileTitle.textContent = 'Thermocline Chart';
    // Update a single frame
    async function updateSingleLayerFrame(index) {
        if (myToken !== animationToken) return;
        const queryContents = { 
            idx: index, type: 'thermocline_update', 
            projectName: projectName, timeZone: getLastTimeZone()
        };
        const updateData = await jsonLoader('select_thermocline', queryContents);
        if (updateData.status === "error") { 
            alert(updateData.message); animating = false;
            controlBtn.textContent = '▶ Play'; return;
        }
        const values = updateData.content;
        // Update the frame
        await Plotly.update(chartDiv, { x: [values], y: [depths]}, {}, [0]);
        // Update time slider
        timeSlider.value = index; timeLabel.textContent = `Time: ${timestamps[index]}`;
    }
    // === Play / Pause control === 
    async function playAnimation() { 
        duration = parseFloat(durationValue.value)*1000
        while (animating && frameIndex < timestamps.length && myToken === animationToken) {
            await updateSingleLayerFrame(frameIndex);
            frameIndex++;
            await new Promise(r => setTimeout(r, duration)); 
        }
        if (myToken !== animationToken) return;
        if (frameIndex >= timestamps.length) { 
            animating = false; controlBtn.textContent = '▶ Play'; 
            frameIndex = 0; // Reset index
        }
    }
    controlBtn.onclick = () => { 
        if (!animating){ 
            animating = true; controlBtn.textContent = '⏸ Pause'; 
            playAnimation(); 
        } else { 
            animating = false; controlBtn.textContent = '▶ Play'; 
        } 
    };
    // === Slider control === 
    timeSlider.addEventListener('input', async(e) => {
        animating = false; controlBtn.textContent = '▶ Play';
        frameIndex = parseInt(e.target.value);
    });
    // === Duration control ===
    durationValue.addEventListener('change', () => { 
        animating = false; controlBtn.textContent = '▶ Play';
    });
    profileContainer.style.display = "flex"; 
    setStateVisualization({isThemocline: false});
}

function renderThermocline(key, plotDiv, xValues, yValues, legend, xTitle, yTitle, title){
    // === Layout ===
    const layout = { title: { text: title, font: { color: 'black', weight: 'bold', size: 20 } },
        paper_bgcolor: '#c2bdbdff', plot_bgcolor: '#c2bdbdff', showlegend: true,
        xaxis: {
            title: {text: xTitle, font: { color: 'black' }, standoff: 10}, type: 'category', zeroline: false,
            automargin: true, mirror: true, showgrid: false, tickmode: 'auto', ticks: 'outside',
            showline: true, linewidth: 1, linecolor: 'black', tickfont: { color: 'black' }
        },
        yaxis: {
            title: {text: yTitle, font: { color: 'black' }}, automargin: true, zeroline: false,
            mirror: true, showline: true, linewidth: 1, linecolor: 'black', 
            autorange: key === 'thermocline_hyd' ? 'reversed' : true,
            showgrid: false, tickfont: { color: 'black' }, tickmode: 'auto', ticks: 'outside'
        },
        margin: { l: 70, r: true ? 60 : 20, t: 50, b: 50 }
    };
    const config = {
        responsive: true, displaylogo: false, displayModeBar: true,
        modeBarButtonsToRemove: ['lasso2d', 'select2d']
    };
    // === Trace ===
    const trace = { x: xValues, y: yValues, mode: 'lines',
        type: 'scatter', line: { color: 'blue', width: 2 }, name: legend
    };
    // === Plot ===
    Plotly.purge(plotDiv);
    Plotly.newPlot(plotDiv, [trace], layout, config).then(() => {
        const resizeObserver = new ResizeObserver(() => Plotly.Plots.resize(plotDiv));
        resizeObserver.observe(plotDiv);
        return resizeObserver;
    });
}

function renderPlotMulti(plotDiv, distance, depths, values, vmin, vmax, nColors, title, unit){
    const discreteColors = getColors(nColors);
    const xLabels = distance.map(String), reversedDepths = [...depths];
    const reverseDepth = reversedDepths.every(d => d >= 0);
    // Build colorScale for Plotly (discrete)
    const colorScale = [], step = 1 / nColors;
    for (let i = 0; i < nColors; i++) {
        colorScale.push([i * step, discreteColors[i]]);
        colorScale.push([(i + 1) * step, discreteColors[i]]);
    }
    // === Layout ===
    let tickvals = xLabels, ticktext = xLabels;
    const maxXTicks = 20;
    if (xLabels.length > maxXTicks) {
        const step = Math.ceil(xLabels.length / maxXTicks);
        tickvals = xLabels.filter((_, i) => i % step === 0);
        ticktext = tickvals;
    }
    const layout = { title: { text: title, font: { color: 'black', weight: 'bold', size: 20 } },
        paper_bgcolor: '#c2bdbdff', plot_bgcolor: '#c2bdbdff',
        xaxis: {
            title: {text: 'Distance (m)', font: { color: 'black' }}, 
            type: 'category', automargin: true, mirror: true, tickmode: 'array',
            showgrid: false, tickvals: tickvals, ticktext: ticktext,
            showline: true, linewidth: 1, linecolor: 'black', tickfont: { color: 'black' }
        },
        yaxis: {
            title: {text: 'Depth (m)', font: { color: 'black' }}, autorange: reverseDepth ? 'reversed' : true,
            mirror: true, showline: true, linewidth: 1, linecolor: 'black', 
            showgrid: false, tickfont: { color: 'black' }, tickmode: 'auto'
        },
        margin: { l: 70, r: true ? 60 : 20, t: 50, b: 50 }
    };
    const config = {
        responsive: true, displaylogo: false, displayModeBar: true,
        modeBarButtonsToRemove: ['lasso2d', 'select2d']
    };
    // Generate colorbar ticks
    colorTicks = colorbarTicks(vmin, vmax, nColors);
    colorTickLabels = colorTicks.map(v => valueFormatter(v, 1e-3));
    // === Plot ===
    Plotly.purge(plotDiv);
    Plotly.newPlot(plotDiv, [{
        z: values, x: xLabels, y: reversedDepths, type: 'heatmap', zsmooth: 'best',
        colorscale: colorScale, zmin: vmin, zmax: vmax, showscale: true,
        colorbar: {
            title: { text: unit, font: { color: 'black' }}, tickfont: { color: 'black' },
            tickvals: colorTicks, ticktext: colorTickLabels
        }
    }], layout, config).then(() => {
        const resizeObserver = new ResizeObserver(() => Plotly.Plots.resize(plotDiv));
        resizeObserver.observe(plotDiv);
        return resizeObserver;
    });
}