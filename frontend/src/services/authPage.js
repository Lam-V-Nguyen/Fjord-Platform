// authPage.js – /login page: sign in, request access, forgot password, set password from a link (?invite= | ?reset=)
const $ = (s, r = document) => r.querySelector(s);
const params = new URLSearchParams(location.search);

async function post(url, body) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), credentials: 'same-origin' });
    let data = {};
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || data.status === 'error') throw new Error(data.message || data.detail || `HTTP ${res.status}`);
    return data;
}

function show(view) {
    document.querySelectorAll('[data-view]').forEach(s => { s.hidden = s.dataset.view !== view; });
    $(`[data-view="${view}"] input:not([type=hidden]):not(.hp)`)?.focus();
}

function msg(form, text, ok = false) {
    const m = $('.auth-msg', form);
    m.textContent = text; m.className = `auth-msg show ${ok ? 'ok' : 'err'}`;
}

function busy(form, on) { const b = $('button[type=submit]', form); b.disabled = on; b.style.opacity = on ? 0.6 : 1; }
const data = (form) => Object.fromEntries(new FormData(form).entries());

// Only return to internal paths (blocks ?next=https://other-site)
function nextUrl() {
    const n = params.get('next') || '/';
    return n.startsWith('/') && !n.startsWith('//') ? n : '/';
}

document.addEventListener('click', (e) => {
    const a = e.target.closest('[data-go]');
    if (a) { e.preventDefault(); show(a.dataset.go); }
});

$('#f-login').addEventListener('submit', async (e) => {
    e.preventDefault(); const f = e.currentTarget; busy(f, true);
    try { await post('/auth/login', data(f)); location.replace(nextUrl()); }
    catch (err) { msg(f, err.message); } finally { busy(f, false); }
});

$('#f-request').addEventListener('submit', async (e) => {
    e.preventDefault(); const f = e.currentTarget, d = data(f);
    d.username = (d.username || '').trim().toLowerCase();
    if (d.password !== d.password2) { msg(f, 'Passwords do not match'); return; }
    busy(f, true);
    try { const r = await post('/auth/request_access', d); msg(f, r.message, true); f.reset(); }
    catch (err) { msg(f, err.message); } finally { busy(f, false); }
});

$('#f-forgot').addEventListener('submit', async (e) => {
    e.preventDefault(); const f = e.currentTarget; busy(f, true);
    try { const r = await post('/auth/forgot', data(f)); msg(f, r.message, true); }
    catch (err) { msg(f, err.message); } finally { busy(f, false); }
});

// Invitation / reset link
const kind = params.get('invite') ? 'invite' : params.get('reset') ? 'reset' : null;
const token = kind ? params.get(kind) : null;
if (kind) {
    show('setpw');
    $('#setpw-title').textContent = kind === 'invite' ? 'Welcome – choose your password' : 'Choose a new password';
    fetch(`/auth/token_info?token=${encodeURIComponent(token)}&kind=${kind}`).then(r => r.json()).then(d => {
        if (d.status === 'ok') $('#setpw-sub').textContent = `Account: ${d.username}`;
        else { msg($('#f-setpw'), d.message); $('#f-setpw button').disabled = true; }
    });
    history.replaceState(null, '', '/login');          // keep the token out of the address bar / history
} else if (params.get('expired')) {
    msg($('#f-login'), 'Your session has expired. Please sign in again.');
}

$('#f-setpw').addEventListener('submit', async (e) => {
    e.preventDefault(); const f = e.currentTarget, d = data(f);
    if (d.password !== d.password2) { msg(f, 'Passwords do not match'); return; }
    busy(f, true);
    try { await post('/auth/set_password', { kind, token, password: d.password }); location.replace('/'); }
    catch (err) { msg(f, err.message); } finally { busy(f, false); }
});

