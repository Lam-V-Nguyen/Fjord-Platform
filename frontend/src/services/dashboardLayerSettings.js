// Floating settings panel on the map for the extra layers (Flow movement, Wflow results).
// Each layer describes its own controls via renderSettings(el, ctx); this module only provides
// compact HTML builders + a shared event binder + saving settings to localStorage.

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function loadSettings(key, defaults) {
    try { return { ...defaults, ...(JSON.parse(localStorage.getItem(key)) || {}) }; }
    catch (e) { return { ...defaults }; }
}

export function saveSettings(key, S) {
    try { localStorage.setItem(key, JSON.stringify(S)); } catch (e) { /* ignore */ }
}

export const ui = {
    section: (title, body, note = '') =>
        `<div class="ls-section"><div class="ls-title">${esc(title)}</div>${body}${note ? `<div class="ls-note">${note}</div>` : ''}</div>`,
    row: (label, control) => `<div class="ls-row"><span class="ls-label">${esc(label)}</span>${control}</div>`,
    select: (k, opts, val) =>
        `<select data-k="${k}">${opts.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(val) ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`,
    seg: (k, opts, val) =>
        `<div class="ls-seg" data-seg="${k}">${opts.map(([v, t]) => `<button type="button" data-v="${esc(v)}" class="${String(v) === String(val) ? 'active' : ''}">${esc(t)}</button>`).join('')}</div>`,
    range: (k, min, max, step, val, fmt = (v) => v) =>
        `<div class="ls-range"><input type="range" data-k="${k}" min="${min}" max="${max}" step="${step}" value="${val}"><output data-o="${k}">${fmt(val)}</output></div>`,
    input: (k, val, ph = '', type = 'text') => `<input type="${type}" data-k="${k}" value="${esc(val)}" placeholder="${esc(ph)}">`,
    button: (act, text, cls = '') => `<button type="button" class="ls-btn ${cls}" data-act="${act}">${esc(text)}</button>`,
};

/**
 * Bind events for all controls inside el.
 *  onSet(k, value, live)  – live=true while a slider is being dragged ('input' event)
 *  onAction(act)          – data-act buttons
 *  fmt                    – {k: fn} formats the value shown next to the slider
 */
export function bindSettings(el, { onSet, onAction, fmt = {} }) {
    el.addEventListener('input', (e) => {
        const t = e.target;
        if (t.type !== 'range' || !t.dataset.k) return;
        const o = el.querySelector(`[data-o="${t.dataset.k}"]`);
        if (o) o.textContent = (fmt[t.dataset.k] || ((v) => v))(Number(t.value));
        onSet(t.dataset.k, Number(t.value), true);
    });
    el.addEventListener('change', (e) => {
        const t = e.target;
        if (!t.dataset.k) return;
        if (t.type === 'range') onSet(t.dataset.k, Number(t.value), false);
        else if (t.tagName === 'SELECT') onSet(t.dataset.k, t.value, false);
        else if (t.tagName === 'INPUT') onSet(t.dataset.k, t.value.trim(), false);   // text input: on leaving the field
    });
    el.addEventListener('keydown', (e) => {          // text input: Enter = apply
        if (e.key === 'Enter' && e.target.dataset.k && e.target.tagName === 'INPUT' && e.target.type !== 'range') {
            e.preventDefault();
            e.target.blur();                                  // -> 'change' calls onSet
            onAction?.('enter:' + e.target.dataset.k);
        }
    });
    el.addEventListener('click', (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        const seg = b.closest('[data-seg]');
        if (seg) {
            seg.querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
            onSet(seg.dataset.seg, b.dataset.v, false);
        } else if (b.dataset.act) onAction?.(b.dataset.act);
    });
}

export function readInput(el, k) { return el.querySelector(`[data-k="${k}"]`)?.value?.trim() ?? ''; }

export { esc };