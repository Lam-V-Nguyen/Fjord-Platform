import { initMap, currentMap } from "./mapManager.js"; 

let gridInstance = null; 

export function initGrid() { 
    if (gridInstance) return gridInstance; 
    const element = document.getElementById('grid-stack'); 
    if (!element) return; 
    gridInstance = GridStack.init({ 
        cellHeight: 80, column: 12, 
        draggable: { handle: '.widget-header' }, 
        resizable: { handles: 'all' } , minRow: 1
    }, element); 
    gridInstance.on('change', saveWidget); 
    gridInstance.on('added', saveWidget); 
    gridInstance.on('removed', saveWidget); 
    gridInstance.on('dragstop', saveWidget); 
    gridInstance.on('resizestop', saveWidget); 
    return gridInstance; 
} 

export function hasWidget(id) { 
    const grid = initGrid(); 
    const nodes = grid.engine.nodes; 
    return nodes.some(node => node.id === id); 
}

export function addWidget(w, h, title, id, iframeUrl=null) {
    const grid = initGrid();
    grid.addWidget({ 
        x: 0, y: 0, w: w, h: h, id: id, minW:2, minH:2,
        content: createWidgetHTML(title, id, iframeUrl)
    }); 
    if (id.includes('-map')) { setTimeout(() => initMap(id), 50); }
    saveWidget(); 
}

export function loadWidget() { 
    const saved = localStorage.getItem("grid-layout"); 
    if (!saved) return; 
    const layout = JSON.parse(saved), grid = initGrid(); 
    grid.load(layout.map(item => ({ 
        ...item, 
        content: createWidgetHTML(item.title, item.id, item.iframeUrl) 
    }))); 
    // Restore items
    setTimeout(() => { 
        layout.forEach(item => { 
            const el = document.querySelector(`[gs-id=${item.id}]`);
            if (!el) return;
            // Restore title 
            el.querySelector('.widget-title').textContent = item.title; 
            // Restore iframe 
            const iframe = el.querySelector('.widget-iframe'); 
            if (iframe) iframe.src = item.iframeUrl; 
            // Restore map 
            if (item.id.includes('-map')) {
                initMap(item.id); 
                if (item.mapState) { 
                    currentMap.setView(item.mapState.center, item.mapState.zoom); 
                    setTimeout(() => { currentMap.invalidateSize(); }, 100); 
                } 
            } 
        }); 
    }, 100); 
} 

export function saveWidget() { 
    const layout = gridInstance.save();
    layout.forEach(item => { 
        const el = document.querySelector(`[gs-id=${item.id}]`); 
        if (!el) return;
        // Title 
        item.title = el.querySelector('.widget-title')?.textContent; 
        // Iframe 
        const iframe = el.querySelector('.widget-iframe'); 
        if (iframe) item.iframeUrl = iframe?.src; 
        // Map 
        if (item.id.includes('-map') && currentMap) { 
            item.mapState = { 
                center: currentMap.getCenter(), zoom: currentMap.getZoom() 
            }; 
            setTimeout(() => { currentMap.invalidateSize(); }, 100); 
        } 
    }); 
    localStorage.setItem('grid-layout', JSON.stringify(layout)); 
} 

function createWidgetHTML(title, id, iframeUrl) {
    return `
        <div class="widget-header">
            <img src="/src_frontend/images/logo16x16.png">
            <span class="widget-title">${title}</span>
            <button class="remove-btn">x</button>
        </div>
        <div class="widget-body">
            ${id.includes('-map') ? 
                `<div 
                    id="leaflet-${id}" class="widget-leaflet">
                    <img class="leaflet-compass" src="/src_frontend/images/compass.png">
                    <div class="basemap-container">
                        <button type="button" class="leaflet-basemap-btn">
                            <img src="/src_frontend/images/basemap.png">
                        </button>
                        <div class="basemap-popup">
                            <div><strong>Select Base Map</strong></div>
                            <button class="basemap-option" data-url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png">Open Street Map</button>
                            <button class="basemap-option" data-url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}">Satellite</button>
                            <button class="basemap-option" data-url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}">Street</button>
                            <button class="basemap-option" data-url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png">Carto Light</button>
                            <button class="basemap-option" data-url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png">Carto Dark</button>
                        </div>
                    </div>
                    <div class="custom-colorbar"">
                        <div class="colorbar-wrapper">
                            <div class="colorbar-title">Legend</div>
                            <div class="colorbar-gradient"></div>
                            <div class="colorbar-labels">
                                <div></div> <!-- max -->
                                <div></div> <!-- 75% -->
                                <div></div> <!-- 50% -->
                                <div></div> <!-- 25% -->
                                <div></div> <!-- min -->
                            </div>
                        </div>
                    </div>
                </div>` : 
                `<iframe 
                    id="iframe-${id}" class="widget-iframe" 
                    src="${iframeUrl || '/src_frontend/htmls/error.html'}">
                </iframe>`
            }
        </div>
    `;
}