// shareDialog.js – "Share project" in Settings: who can open the current project ('view' = read only, 'edit' = may
// change and run it). Projects shared with you appear in Open Project as "<owner>:<project>".
import { getLastProject } from './constant.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const SHARE_SEP = ':';

async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok || d.status === 'error') throw new Error(d.message || d.detail || `HTTP ${res.status}`);
    return d;
}

// "<owner>:<project>" -> {owner, name}; own project -> {owner: null, name}
export function parseProjectRef(ref) {
    const s = String(ref || '');
    const i = s.indexOf(SHARE_SEP);
    return i > 0 ? { owner: s.slice(0, i), name: s.slice(i + 1) } : { owner: null, name: s };
}

export function projectLabel(ref) {
    const { owner, name } = parseProjectRef(ref);
    return owner ? `${name} (shared by ${owner})` : name;
}

export async function initShareDialog({ toast = () => {}, beforeOpen = () => {}, me = null } = {}) {
    const modal = $('share-modal'), body = $('share-modal-body');
    const project = getLastProject();
    // Chip: show "(shared by …, view only)" for projects of other users
    const { owner, name } = parseProjectRef(project);
    if (owner) {
        let access = '';
        try { access = (await api(`/auth/shares?project=${encodeURIComponent(project)}`)).my_access; } catch (e) { /* ignore */ }
        const chip = $('project-chip-label');
        if (chip) chip.textContent = `${name} · ${owner}${access === 'view' ? ' · view only' : ''}`;
        $('project-chip')?.setAttribute('title', `Project '${name}' shared by ${owner} (${access || '?'} access)`);
    }
    if (me && me.role === 'viewer') { $('btn-share-project').hidden = true; }

    async function render() {
        body.innerHTML = '<div class="empty-state">Loading…</div>';
        let d;
        try { d = await api(`/auth/shares?project=${encodeURIComponent(project)}`); }
        catch (e) { body.innerHTML = `<div class="empty-state error-state">${esc(e.message)}</div>`; return; }
        if (!d.can_manage) {
            body.innerHTML = `<p>Project <b>${esc(d.project)}</b> is shared with you by <b>${esc(d.owner)}</b>
                with <b>${esc(d.my_access)}</b> access${d.my_access === 'view' ? ' (read only: you can view results but not change or run it)' : ''}.</p>
                <button class="btn small danger" id="share-leave">Leave this project</button>`;
            $('share-leave').onclick = async () => {
                if (!confirm(`Remove '${d.project}' from your projects?`)) return;
                await api('/auth/shares/remove', { project, username: me?.username });
                modal.classList.remove('show'); toast('Project removed from your list');
            };
            return;
        }
        body.innerHTML = `
            <p style="margin:0 0 10px; color:var(--muted); font-size:12.5px">Project <b style="color:var(--text)">${esc(d.project)}</b>.
              <b>view</b>: open and look at results · <b>edit</b>: also change and run simulations. Only you (and administrators)
              can delete the project or change its sharing.</p>
            <form id="share-add" style="display:flex; gap:6px; margin-bottom:12px">
              <input class="tz-search" name="username" list="share-users" placeholder="username" autocomplete="off" required style="flex:1; margin:0">
              <datalist id="share-users"></datalist>
              <select name="access" class="tz-search" style="width:90px; margin:0"><option>view</option><option>edit</option></select>
              <button class="btn small primary" type="submit">Share</button>
            </form>
            <table class="alert-table"><thead><tr><th>User</th><th>Access</th><th>Since</th><th></th></tr></thead>
              <tbody>${d.shares.map(s => `<tr>
                <td>${esc(s.grantee)}</td>
                <td><select data-user="${esc(s.grantee)}" class="tz-search" style="width:80px; margin:0; padding:2px 6px">
                    <option ${s.access === 'view' ? 'selected' : ''}>view</option><option ${s.access === 'edit' ? 'selected' : ''}>edit</option></select></td>
                <td style="color:var(--muted)">${new Date(s.created_at * 1000).toLocaleDateString('en-GB')}</td>
                <td><button class="btn small ghost danger" data-remove="${esc(s.grantee)}" title="Stop sharing">×</button></td>
              </tr>`).join('') || '<tr><td colspan="4" style="color:var(--muted)">Not shared with anyone yet</td></tr>'}</tbody></table>
            <div id="share-msg" style="color:var(--sev-high); font-size:12px; margin-top:8px"></div>`;
        const form = $('share-add'), msg = $('share-msg');
        let t = 0;
        form.username.addEventListener('input', () => {
            clearTimeout(t);
            t = setTimeout(async () => {
                const q = form.username.value.trim();
                if (q.length < 2) return;
                try {
                    const r = await api(`/auth/users_lookup?q=${encodeURIComponent(q)}`);
                    $('share-users').innerHTML = r.users.map(u =>
                        `<option value="${esc(u.username)}">${esc(u.full_name || '')}${u.organisation ? ' · ' + esc(u.organisation) : ''}</option>`).join('');
                } catch (e) { /* ignore */ }
            }, 200);
        });
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            try {
                await api('/auth/shares/add', { project, username: form.username.value.trim().toLowerCase(), access: form.access.value });
                toast('Project shared'); render();
            } catch (err) { msg.textContent = err.message; }
        });
        body.querySelectorAll('select[data-user]').forEach(sel => sel.addEventListener('change', async () => {
            try { await api('/auth/shares/add', { project, username: sel.dataset.user, access: sel.value }); toast('Access updated'); }
            catch (err) { msg.textContent = err.message; }
        }));
        body.querySelectorAll('[data-remove]').forEach(b => b.addEventListener('click', async () => {
            if (!confirm(`Stop sharing with ${b.dataset.remove}?`)) return;
            try { await api('/auth/shares/remove', { project, username: b.dataset.remove }); render(); }
            catch (err) { msg.textContent = err.message; }
        }));
    }

    $('btn-share-project')?.addEventListener('click', () => {
        beforeOpen(); $('share-modal-title').textContent = owner ? 'Shared project' : 'Share project';
        modal.classList.add('show'); render();
    });
    modal?.addEventListener('click', (e) => { if (e.target === modal || e.target.closest('[data-close-modal]')) modal.classList.remove('show'); });
}
