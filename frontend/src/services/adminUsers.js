// adminUsers.js – /admin page: invite, approve, change role / status, create password links, delete accounts
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (t) => t ? new Date(t * 1000).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' }) : '–';
let USERS = [], ROLES = [], STATUSES = [], DEFAULT_QUOTA = 0;
const gb = (b) => b == null ? '–' : (b / 1024 ** 3).toFixed(b >= 10 * 1024 ** 3 ? 0 : 1);

async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (res.status === 401) { location.replace('/login?next=/admin'); throw new Error('Not signed in'); }
    const d = await res.json().catch(() => ({}));
    if (!res.ok || d.status === 'error') throw new Error(d.message || d.detail || `HTTP ${res.status}`);
    return d;
}

function showLink(text, link) {
    const box = $('#link-box');
    box.innerHTML = `${esc(text)}<br><code>${esc(link)}</code>
        <button class="btn small" id="copy-link" style="margin-left:8px">Copy</button>`;
    box.classList.add('show');
    $('#copy-link').onclick = () => navigator.clipboard.writeText(link).then(() => { $('#copy-link').textContent = 'Copied'; });
    box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function render() {
    const q = $('#filter').value.trim().toLowerCase();
    const sel = (name, opts, val, user) => `<select data-field="${name}" data-user="${esc(user)}">${opts.map(o =>
        `<option ${o === val ? 'selected' : ''}>${o}</option>`).join('')}</select>`;
    $('#users').innerHTML = USERS.filter(u => !q || JSON.stringify(u).toLowerCase().includes(q)).map(u => `
        <tr>
          <td><b>${esc(u.username)}</b><div class="muted">${esc(u.full_name)}</div></td>
          <td>${esc(u.email || '-')}<div class="muted">${esc(u.organisation)}</div></td>
          <td>${sel('role', ROLES, u.role, u.username)}</td>
          <td><span class="pill ${u.status}">${u.status}</span> ${sel('status', STATUSES, u.status, u.username)}</td>
          <td class="muted">${fmt(u.last_login)}</td>
          <td><div class="storage-cell ${u.storage?.level || ''}" title="${esc(u.storage?.message || '')}">
            <span>${gb(u.storage?.used)} /</span>
            <input type="number" min="0" step="1" data-quota="${esc(u.username)}" style="width:62px"
              value="${u.storage?.override ? (u.storage.quota == null ? 0 : gb(u.storage.quota)) : ''}"
              placeholder="${u.storage?.override ? '' : (u.storage?.quota == null ? '∞' : gb(u.storage.quota))}"> GB
            ${u.storage?.percent != null ? `<div class="muted">${u.storage.percent}%</div>` : ''}</div></td>
          <td><div class="admin-actions">
            ${u.status === 'pending' ? `<button class="btn small primary" data-act="approve" data-user="${esc(u.username)}">Approve</button>` : ''}
            <button class="btn small" data-act="link" data-user="${esc(u.username)}" title="Create a password link (e-mailed when SMTP is configured)">
              ${u.status === 'invited' ? 'Resend invite' : 'Reset password'}</button>
            <button class="btn small danger" data-act="delete" data-user="${esc(u.username)}">Delete</button>
          </div></td>
        </tr>`).join('') || '<tr><td colspan="7" class="muted">No users</td></tr>';
}

async function load() {
    const d = await api('/auth/admin/users');
    USERS = d.users; ROLES = d.roles; STATUSES = d.statuses; DEFAULT_QUOTA = d.default_quota_gb || 0;
    $('#mail-note').textContent = d.mail_enabled ? 'Invitations are sent by e-mail.'
        : 'E-mail (SMTP) is not configured: copy the invitation link shown after sending and give it to the user.';
    render();
    const sh = await api('/auth/admin/shares');
    $('#shares').innerHTML = sh.shares.map(s => `<tr><td><b>${esc(s.project)}</b></td><td>${esc(s.owner)}</td>
        <td>${esc(s.grantee)}</td><td><span class="pill ${s.access === 'edit' ? 'invited' : 'active'}">${esc(s.access)}</span></td>
        <td class="muted">${fmt(s.created_at)}</td>
        <td><button class="btn small danger" data-unshare="${esc(s.owner)}:${esc(s.project)}|${esc(s.grantee)}">Revoke</button></td></tr>`).join('')
        || '<tr><td colspan="6" class="muted">No shared projects</td></tr>';
    const a = await api('/auth/admin/audit?limit=60');
    $('#audit').innerHTML = a.events.map(e => `<tr><td class="muted">${fmt(e.at)}</td><td>${esc(e.actor || '')}</td>
        <td>${esc(e.action)}</td><td>${esc(e.target || '')}</td><td class="muted">${esc(e.detail || '')}</td>
        <td class="muted">${esc(e.ip || '')}</td></tr>`).join('');
}

$('#filter').addEventListener('input', render);

$('#f-invite').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;                     // e.currentTarget is null after await
    const body = Object.fromEntries(new FormData(form).entries());
    body.username = body.username.trim().toLowerCase();
    try {
        const r = await api('/auth/admin/invite', body);
        form.reset();
        if (r.link) showLink(`Invitation for ${r.user.username} (not e-mailed) – send this link to the user:`, r.link);
        else { $('#link-box').textContent = `Invitation e-mailed to ${r.user.email}.`; $('#link-box').classList.add('show'); }
        await load();
    } catch (err) { alert(err.message); }
});

$('#users').addEventListener('change', async (e) => {
    const q = e.target.closest('input[data-quota]');
    if (q) {      // per-user storage quota: number of GB, 0 = unlimited, empty = default of the server (.env)
        try {
            await api('/auth/admin/quota', { username: q.dataset.quota, quota_gb: q.value.trim() });
            await load();
        } catch (err) { alert(err.message); }
        return;
    }
    const s = e.target.closest('select[data-field]');
    if (!s) return;
    try { await api('/auth/admin/update', { username: s.dataset.user, [s.dataset.field]: s.value }); }
    catch (err) { alert(err.message); }
    await load();
});

$('#users').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const username = b.dataset.user;
    try {
        if (b.dataset.act === 'approve') await api('/auth/admin/approve', { username });
        else if (b.dataset.act === 'link') {
            const r = await api('/auth/admin/reset_link', { username });
            showLink(`${r.kind === 'invite' ? 'Invitation' : 'Password reset'} link for ${username} (valid ${r.expires_hours} h)` +
                (r.mailed ? ' – also e-mailed:' : ':'), r.link);
        } else if (b.dataset.act === 'delete') {
            if (!confirm(`Delete account "${username}"?\nProject files on the server are kept.`)) return;
            await api('/auth/admin/delete', { username });
        }
        await load();
    } catch (err) { alert(err.message); }
});

$('#shares').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-unshare]');
    if (!b) return;
    const [project, username] = b.dataset.unshare.split('|');
    if (!confirm(`Stop sharing ${project} with ${username}?`)) return;
    try { await api('/auth/shares/remove', { project, username }); await load(); } catch (err) { alert(err.message); }
});

load().catch(err => alert(err.message));
