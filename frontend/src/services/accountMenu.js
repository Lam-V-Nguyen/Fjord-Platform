// accountMenu.js – "Account" section in Settings: current user, change password, user management (admin), sign out
const $ = (id) => document.getElementById(id);

export async function initAccountMenu({ toast = () => {}, beforeOpen = () => {} } = {}) {
    let me = null;
    try {
        const r = await fetch('/auth/me');
        if (r.ok) me = (await r.json()).user;
    } catch (e) { /* ignore */ }
    if (me) {
        $('account-name').textContent = me.full_name ? `${me.full_name} (${me.username})` : me.username;
        $('account-role').textContent = me.role;
        $('btn-admin-users').hidden = me.role !== 'admin';
    }
    $('btn-logout')?.addEventListener('click', async () => {
        try { await fetch('/auth/logout', { method: 'POST' }); } finally { location.replace('/login'); }
    });
    $('btn-admin-users')?.addEventListener('click', () => window.open('/admin', '_blank', 'noopener'));
    const modal = $('password-modal'), form = $('f-change-password'), msg = $('pw-msg');
    $('btn-change-password')?.addEventListener('click', () => {
        beforeOpen(); form.reset(); msg.textContent = ''; modal.classList.add('show');
        form.querySelector('input')?.focus();
    });
    modal?.addEventListener('click', (e) => { if (e.target === modal || e.target.closest('[data-close-modal]')) modal.classList.remove('show'); });
    form?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const d = Object.fromEntries(new FormData(form).entries());
        if (d.new !== d.new2) { msg.textContent = 'New passwords do not match'; return; }
        try {
            const res = await fetch('/auth/change_password', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ current: d.current, new: d.new }) });
            const r = await res.json();
            if (!res.ok || r.status !== 'ok') throw new Error(r.message || `HTTP ${res.status}`);
            modal.classList.remove('show'); toast(r.message || 'Password changed');
        } catch (err) { msg.textContent = err.message; }
    });
    return me;
}
