// thresholdEditor.js – Xem / sửa ngưỡng cảnh báo (backend/src/dashboard_thresholds.json) từ Settings
//  - Mở: nạp giá trị hiện tại từ /dashboard/thresholds
//  - Sửa trực tiếp trong bảng; thêm / xoá chất (WAQ); nhóm "physics" cố định khoá
//  - Save: kiểm tra phía trình duyệt rồi gửi {save: {...}} -> backend kiểm tra lại, ghi đè file (giữ bản .bak)
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const FIELDS = ['label', 'unit', 'direction', 'medium', 'high', 'category'];

async function post(body) {
    const res = await fetch('/dashboard/thresholds', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || !data || data.status !== 'ok') throw new Error(data?.message || data?.detail || `HTTP ${res.status}`);
    return data;
}

function rowHtml(group, key, r = {}) {
    const fixed = group === 'physics';                     // khoá physics do code dùng -> không đổi tên / xoá
    const num = (k, v) => `<input type="number" step="any" data-f="${k}" value="${esc(v)}">`;
    const txt = (k, v, ph = '') => `<input type="text" data-f="${k}" value="${esc(v)}" placeholder="${ph}">`;
    return `<tr data-group="${group}">
        <td><input type="text" data-f="key" value="${esc(key)}" ${fixed ? 'readonly' : ''} placeholder="e.g. OXY"></td>
        <td>${txt('label', r.label, 'Label')}</td><td>${txt('unit', r.unit, 'g/m³')}</td>
        <td><select data-f="direction">
            <option value="above" ${r.direction !== 'below' ? 'selected' : ''}>above ▲</option>
            <option value="below" ${r.direction === 'below' ? 'selected' : ''}>below ▼</option></select></td>
        <td>${num('medium', r.medium)}</td><td>${num('high', r.high)}</td>
        <td>${txt('category', r.category, 'Category')}</td>
        <td>${fixed ? '' : '<button type="button" class="btn small ghost danger" data-del title="Remove">×</button>'}</td>
    </tr>`;
}

function tableHtml(group, title, rows, note) {
    return `<h4 class="thr-title">${title}</h4><div class="thr-note">${note}</div>
        <table class="alert-table thr-table"><thead><tr>
            <th>Key</th><th>Label</th><th>Unit</th><th>Bad when</th><th>Medium</th><th>High</th><th>Category</th><th></th>
        </tr></thead><tbody data-body="${group}">${Object.entries(rows || {}).map(([k, r]) => rowHtml(group, k, r)).join('')}</tbody></table>`;
}

function render(data) {
    $('thresholds-modal-body').innerHTML =
        tableHtml('substances', 'Water quality (WAQ substances)', data.substances,
            'Key = WAQ variable name without the <code>mesh2d_</code> prefix (e.g. <code>OXY</code>, <code>EColi</code>).') +
        `<button type="button" class="btn small" data-add>+ Add substance</button>` +
        tableHtml('physics', 'Hydrodynamics (HYD)', data.physics, 'Fixed keys used by the dashboard.') +
        `<div class="thr-error" id="thr-error"></div>`;
}

// Đọc bảng -> {substances, physics}; trả về danh sách lỗi
function collect() {
    const out = { substances: {}, physics: {} }, errors = [];
    document.querySelectorAll('#thresholds-modal-body tr[data-group]').forEach((tr, i) => {
        const g = tr.dataset.group, v = (f) => tr.querySelector(`[data-f="${f}"]`).value.trim();
        const key = v('key');
        tr.classList.remove('thr-bad');
        const bad = (msg) => { errors.push(`${key || `row ${i + 1}`}: ${msg}`); tr.classList.add('thr-bad'); };
        if (!key) return bad('key is empty');
        if (out[g][key]) return bad('duplicate key');
        const r = Object.fromEntries(FIELDS.map(f => [f, v(f)]));
        ['medium', 'high'].forEach(f => { r[f] = r[f] === '' ? NaN : Number(r[f]); });
        if (!Number.isFinite(r.medium) || !Number.isFinite(r.high)) return bad('medium and high must be numbers');
        if (r.direction === 'above' && r.medium > r.high) return bad('"above": medium must be ≤ high');
        if (r.direction === 'below' && r.medium < r.high) return bad('"below": medium must be ≥ high');
        r.label = r.label || key;
        out[g][key] = r;
    });
    return { data: out, errors };
}

export function initThresholdEditor({ toast = () => {}, onSaved = () => {}, beforeOpen = () => {} } = {}) {
    const modal = $('thresholds-modal'), body = $('thresholds-modal-body');
    if (!modal) return;
    const showError = (msgs) => { const e = $('thr-error'); if (e) e.innerHTML = msgs.map(esc).join('<br>'); };
    const load = async (reload = false) => {
        body.innerHTML = '<div class="empty-state">Loading thresholds…</div>';
        try { render(await post(reload ? { reload: true } : {})); }
        catch (e) { body.innerHTML = `<div class="empty-state error-state">${esc(e.message)}</div>`; }
    };
    $('edit-thresholds')?.addEventListener('click', async () => {
        beforeOpen(); modal.classList.add('show'); await load();
    });
    body.addEventListener('click', (e) => {
        if (e.target.closest('[data-del]')) e.target.closest('tr').remove();
        if (e.target.closest('[data-add]')) {
            const tb = body.querySelector('[data-body="substances"]');
            tb.insertAdjacentHTML('beforeend', rowHtml('substances', '', {}));
            tb.lastElementChild.querySelector('[data-f="key"]').focus();
        }
    });
    $('thr-reload').addEventListener('click', () => load(true));     // bỏ thay đổi chưa lưu, đọc lại file
    $('thr-save').addEventListener('click', async () => {
        const { data, errors } = collect();
        if (errors.length) { showError(errors); return; }
        if (!confirm('Overwrite dashboard_thresholds.json with these values?\n(A backup .bak file is kept.)')) return;
        try {
            const res = await post({ save: data });
            render(res); modal.classList.remove('show');
            toast('Thresholds saved'); onSaved();
        } catch (e) { showError([e.message]); }
    });
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.remove('show'); });
}