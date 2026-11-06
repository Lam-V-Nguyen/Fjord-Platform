export function setElementsEnabled(enabled, ...elements) {
    elements.forEach(e => {
        e.style.pointerEvents = enabled ? 'auto' : 'none';
        e.style.opacity = enabled ? '1' : '0.4';
    });
}

export function setElementsDisplayed(display, ...elements) {
    elements.forEach(e => {
        e.style.display = display ? 'flex' : 'none';
    });
}

export function isInvalidNumber(value) {
    if (value === null || value === undefined) return true;
    const str = value.toString().trim();
    if (str === '' || str.toLowerCase() === 'none') return true;
    return !Number.isFinite(Number(str));
}

export async function geoJSONExporter(data, fileName) {
    try {
        const json = JSON.stringify(data, null, 2);
        if ('showSaveFilePicker' in window) {
            // --- Chrome/Edge/Opera ---
            const fileHandle = await window.showSaveFilePicker({
                suggestedName: fileName,
                types: [{
                    description: 'GeoJSON',
                    accept: { 'application/json': ['.geojson'] }
                }]
            });
            const writable = await fileHandle.createWritable();
            await writable.write(json); await writable.close();
            await new Promise(res => setTimeout(res, 200));
        } else {
            // --- Fallback cho Firefox, Safari ---
            const blob = new Blob([json], { type: 'application/geo+json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = fileName;
            document.body.appendChild(a);
            setTimeout(() => a.click(), 0);
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
        alert(`Exporting succeeded.`);
    } catch (error) { alert(`Exporting failed: ${error.message}`); }
}