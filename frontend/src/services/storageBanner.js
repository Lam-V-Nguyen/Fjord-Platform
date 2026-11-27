// storageBanner.js – storage quota of the signed-in user: banner on the main page when nearly full / full,
// and the "Storage: x of y" line in Settings → Account. The server blocks routes that add data with HTTP 507
// when the quota is full; commonFunctions.js forwards those answers here (also from the tool iframes).
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const POLL_MS = 120000;
let banner = null, lastLevel = 'ok';

const fmt = (b) => b >= 0.1 * 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} GB` : `${Math.round(b / 1024 ** 2)} MB`;

function dismissedKey(level) { return `storage-dismissed-${level}`; }

function ensureBanner() {
    if (banner) return banner;
    banner = document.createElement('div');
    banner.className = 'storage-banner'; banner.hidden = true; banner.setAttribute('role', 'alert');
    const main = document.querySelector('main.main') || document.body;
    main.insertBefore(banner, main.firstChild);
    banner.addEventListener('click', (e) => {
        if (e.target.closest('[data-storage-close]')) {
            try { sessionStorage.setItem(dismissedKey(banner.dataset.level), '1'); } catch (_) { /* ignore */ }
            banner.hidden = true;
        }
    });
    return banner;
}

function show(level, message) {
    const b = ensureBanner();
    if (level === 'ok' || !message) { b.hidden = true; return; }
    let dismissed = false;
    try { dismissed = level === 'warn' && sessionStorage.getItem(dismissedKey(level)) === '1'; } catch (_) { /* ignore */ }
    b.dataset.level = level; b.className = `storage-banner ${level}`;
    b.innerHTML = `<span class="storage-icon">${level === 'full' ? '⛔' : '⚠'}</span>
        <span class="storage-text">${esc(message)}</span>
        ${level === 'warn' ? '<button type="button" class="storage-close" data-storage-close title="Hide until next sign-in">✕</button>' : ''}`;
    b.hidden = dismissed;
}

// Storage bar at the bottom of the left panel; hovering it shows "used of quota"
function showMeter(st) {
    const box = $('storage-meter');
    if (!box) return;
    const pct = st.quota ? Math.min(100, st.percent) : 0;
    $('storage-meter-fill').style.width = `${pct}%`;
    $('storage-meter-pct').textContent = st.quota ? `${Math.round(st.percent)}%` : '';
    const free = st.quota ? Math.max(0, st.quota - st.used) : null;
    const tip = st.quota
        ? `${fmt(st.used)} of ${fmt(st.quota)} used (${st.percent}%) · ${fmt(free)} free`
        : `${fmt(st.used)} used · no storage limit`;
    $('storage-meter-tip').textContent = tip;
    box.setAttribute('aria-label', `Storage: ${tip}`);
    box.className = `sf-row storage-meter ${st.quota ? st.level : 'unlimited'}`;
    box.hidden = false;
}

function showAccountLine(st) {
    const el = $('account-storage');
    if (!el) return;
    el.textContent = st.quota ? `Storage: ${fmt(st.used)} of ${fmt(st.quota)} (${st.percent}%)` : `Storage: ${fmt(st.used)} (no limit)`;
    el.className = `account-storage ${st.level}`;
}

export async function refreshStorage() {
    try {
        const res = await fetch('/auth/storage');
        if (!res.ok) return null;
        const st = (await res.json()).storage;
        if (!st) return null;
        if (st.level !== lastLevel && st.level !== 'ok') {     // new level -> show again even if hidden before
            try { sessionStorage.removeItem(dismissedKey(st.level)); } catch (_) { /* ignore */ }
        }
        lastLevel = st.level;
        show(st.level, st.message); showAccountLine(st); showMeter(st);
        return st;
    } catch (_) { return null; }
}

export function initStorageBanner() {
    const blocked = (message) => { show('full', message); setTimeout(refreshStorage, 3000); };
    window.addEventListener('message', (e) => { if (e.data?.type === 'STORAGE_QUOTA') blocked(e.data.content); });
    window.addEventListener('storage-quota', (e) => blocked(e.detail));
    refreshStorage();
    setInterval(refreshStorage, POLL_MS);
    // after the user deletes something, the size is measured again in the background -> check a bit later
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStorage(); });
}
