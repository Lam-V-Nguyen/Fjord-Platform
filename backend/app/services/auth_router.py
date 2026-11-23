"""
auth_router.py – Sign in / out, invitations, access requests, forgotten passwords, user administration.

Pages:   GET /login (sign in · request access · forgot password · set password from a link)   GET /admin
API:     POST /auth/login · /auth/logout · GET /auth/me · POST /auth/change_password
         POST /auth/request_access · /auth/forgot · GET /auth/token_info · POST /auth/set_password
Admin:   GET /auth/admin/users · POST /auth/admin/invite · /auth/admin/update · /auth/admin/approve
         POST /auth/admin/reset_link · /auth/admin/delete · GET /auth/admin/audit · GET /auth/admin/shares
Sharing: GET /auth/shares?project= · POST /auth/shares/add · /auth/shares/remove · GET /auth/shares/with_me
         GET /auth/users_lookup?q=   (projects shared with a user are referenced as "<owner>:<project>")
"""
import os, time, logging
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi import HTTPException
from config import (PROJECT_ROOT, COOKIE_NAME, COOKIE_SECURE, SESSION_HOURS, SESSION_MAX_DAYS, 
    PUBLIC_URL, SOURCE_FRONTEND, INVITE_HOURS, RESET_HOURS, SELF_REGISTRATION, ADMIN_EMAILS)
from services import auth_store as store, mailer
from services.functions import current_user, require_admin, split_project_ref, SHARE_SEP, PUBLIC_OWNER

router = APIRouter()
log = logging.getLogger("fjord.auth")
HTML_DIR = os.path.join(SOURCE_FRONTEND, "htmls")

def _ip(request: Request) -> str:
    # Behind a reverse proxy run uvicorn with --proxy-headers --forwarded-allow-ips=127.0.0.1 so client.host is the real IP
    return request.client.host if request.client else "?"

def _ok(**kw): return {"status": "ok", **kw}
def _fail(msg: str, code: int = 400): return JSONResponse({"status": "error", "message": msg}, status_code=code)

async def _json(request: Request) -> dict:
    try: return await request.json()
    except Exception: return {}

def _set_cookie(resp, token: str):
    resp.set_cookie(COOKIE_NAME, token, max_age=SESSION_MAX_DAYS * 86400, httponly=True, 
        secure=COOKIE_SECURE, samesite="lax", path="/"
    )

def _link(kind: str, token: str) -> str:
    return f"{PUBLIC_URL}/login?{kind}={token}"

#  pages
@router.get("/login", include_in_schema=False)
def login_page(request: Request):
    if current_user(request) is not None and not (request.query_params.get("invite") or request.query_params.get("reset")):
        return RedirectResponse("/", status_code=303)
    return FileResponse(os.path.join(HTML_DIR, "login.html"), headers={"Cache-Control": "no-store"})

@router.get("/admin", include_in_schema=False)
def admin_page(request: Request):
    user = current_user(request)
    if user is None: return RedirectResponse("/login?next=/admin", status_code=303)
    if not user.is_admin: return RedirectResponse("/", status_code=303)
    return FileResponse(os.path.join(HTML_DIR, "admin.html"), headers={"Cache-Control": "no-store"})

#  sign in
@router.post("/auth/login")
async def auth_login(request: Request):
    body = await _json(request)
    username, password, ip = (body.get("username") or "").strip().lower(), body.get("password") or "", _ip(request)
    if not username or not password: return _fail("Enter username and password")
    if store.too_many_fails(username, ip):
        store.audit("login_blocked", target=username, ip=ip)
        return _fail("Too many failed attempts. Try again in 15 minutes.", 429)
    user = store.verify_password(username, password)
    if user is None:
        store.record_fail(username, ip); store.audit("login_failed", target=username, ip=ip)
        return _fail("Wrong username or password", 401)
    if user.status != "active":
        msg = {"pending": "Your account is waiting for approval by an administrator.",
            "disabled": "This account is disabled. Contact the administrator.",
            "invited": "Set your password with the invitation link first."
        }.get(user.status, "Account not active")
        return _fail(msg, 403)
    store.clear_fails(user.username)
    token = store.create_session(user, ip, request.headers.get("user-agent"))
    store.audit("login", actor=user.username, ip=ip)
    resp = JSONResponse(_ok(user=user.public()))
    _set_cookie(resp, token)
    return resp

@router.post("/auth/logout")
async def auth_logout(request: Request):
    user = current_user(request)
    store.end_session(request.cookies.get(COOKIE_NAME))
    if user: store.audit("logout", actor=user.username, ip=_ip(request))
    resp = JSONResponse(_ok())
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp

@router.get("/auth/me")
def auth_me(request: Request):
    user = current_user(request)
    if user is None: return _fail("Not authenticated", 401)
    return _ok(user=user.public(), session_hours=SESSION_HOURS)

@router.post("/auth/change_password")
async def auth_change_password(request: Request):
    user = current_user(request)
    if user is None: return _fail("Not authenticated", 401)
    body = await _json(request)
    if store.verify_password(user.username, body.get("current") or "") is None: return _fail("Current password is wrong")
    try: store.set_password(user.username, body.get("new") or "")
    except store.AuthError as e: return _fail(str(e))
    store.audit("password_changed", actor=user.username, ip=_ip(request))
    resp = JSONResponse(_ok(message="Password changed. Other devices have been signed out."))
    _set_cookie(resp, store.create_session(user, _ip(request), request.headers.get("user-agent")))
    return resp

#  self-service
@router.post("/auth/request_access")
async def auth_request_access(request: Request):
    if not SELF_REGISTRATION: return _fail("Self-registration is disabled. Contact the administrator.", 403)
    body = await _json(request)
    if (body.get("website") or "").strip(): return _ok(message="Request received.")          # bot trap (hidden field)
    try:
        if not store.check_email(body.get("email")): raise store.AuthError("E-mail is required")
        if not body.get("password"): raise store.AuthError("Choose a password")
        user = store.create_user(body.get("username", ""), email=body.get("email"), password=body.get("password"),
            role="user", status="pending", full_name=body.get("full_name", ""),
            organisation=body.get("organisation", ""), note=body.get("note", "")
        )
    except store.AuthError as e: return _fail(str(e))
    store.audit("access_requested", target=user.username, ip=_ip(request))
    admins = ADMIN_EMAILS or [u["email"] for u in store.list_users() if u["role"] == "admin" and u["email"]]
    mailer.send(admins, f"Access request: {user.username}", mailer.access_request_text(
        user.username, user.email or "", user.full_name, user.organisation, body.get("note", "")))
    return _ok(message="Request received. You will get an e-mail when an administrator has approved your account.")

@router.post("/auth/forgot")
async def auth_forgot(request: Request):
    body = await _json(request)
    who = (body.get("email") or body.get("username") or "").strip().lower()
    user = store.get_user_by_email(who) or store.get_user(who)
    # Always the same answer: never reveal whether an account exists
    msg = "If the account exists and has an e-mail address, a reset link has been sent."
    if user and user.email and user.status in ("active", "invited"):
        token = store.issue_token(user, "reset")
        sent = mailer.send(user.email, "Reset your password",
            mailer.reset_text(user.full_name, user.username, _link("reset", token), RESET_HOURS)
        )
        store.audit("reset_requested", target=user.username, detail=f"mailed={sent}", ip=_ip(request))
    if not mailer.enabled(): msg += " (E-mail is not configured on this server – ask the administrator for a reset link.)"
    return _ok(message=msg)

@router.get("/auth/token_info")
def auth_token_info(token: str, kind: str):
    if kind not in ("invite", "reset"): return _fail("Invalid link")
    user = store.peek_token(token, kind)
    if user is None: return _fail("This link is invalid or has expired. Ask for a new one.", 404)
    return _ok(username=user.username, full_name=user.full_name, kind=kind)

@router.post("/auth/set_password")
async def auth_set_password(request: Request):
    body = await _json(request)
    kind = body.get("kind")
    if kind not in ("invite", "reset"): return _fail("Invalid link")
    try: user = store.consume_token(body.get("token") or "", kind, body.get("password") or "")
    except store.AuthError as e: return _fail(str(e))
    store.audit("password_set", actor=user.username, detail=kind, ip=_ip(request))
    resp = JSONResponse(_ok(message="Password saved. You are now signed in."))
    _set_cookie(resp, store.create_session(user, _ip(request), request.headers.get("user-agent")))
    return resp

#  administration
@router.get("/auth/admin/users")
def admin_users(admin: str = Depends(require_admin)):
    return _ok(users=store.list_users(), mail_enabled=mailer.enabled(), roles=store.ROLES, statuses=store.STATUSES)

@router.post("/auth/admin/invite")
async def admin_invite(request: Request, admin: str = Depends(require_admin)):
    body = await _json(request)
    try:
        user = store.create_user(body.get("username", ""), email=body.get("email"), role=body.get("role", "user"),
            status="invited", full_name=body.get("full_name", ""), organisation=body.get("organisation", "")
        )
    except store.AuthError as e: return _fail(str(e))
    token = store.issue_token(user, "invite")
    link = _link("invite", token)
    sent = mailer.send(user.email, "You are invited", mailer.invite_text(user.full_name, user.username, link, INVITE_HOURS, admin)) \
        if user.email else False
    store.audit("user_invited", actor=admin, target=user.username, detail=f"mailed={sent}", ip=_ip(request))
    return _ok(user=user.public(), link=None if sent else link, mailed=sent)

@router.post("/auth/admin/approve")
async def admin_approve(request: Request, admin: str = Depends(require_admin)):
    body = await _json(request)
    try: user = store.update_user(body.get("username", ""), status="active", role=body.get("role"))
    except store.AuthError as e: return _fail(str(e))
    store.audit("user_approved", actor=admin, target=user.username, ip=_ip(request))
    if user.email:
        mailer.send(user.email, "Your account has been approved",
            f"Hello {user.full_name or user.username},\n\nYour account '{user.username}' is active. "
            f"Sign in at {PUBLIC_URL}/login\n"
        )
    return _ok(user=user.public())

@router.post("/auth/admin/update")
async def admin_update(request: Request, admin: str = Depends(require_admin)):
    body = await _json(request)
    try:
        user = store.update_user(body.get("username", ""), role=body.get("role"), status=body.get("status"),
            email=body.get("email"), full_name=body.get("full_name"), organisation=body.get("organisation")
        )
    except store.AuthError as e: return _fail(str(e))
    store.audit("user_updated", actor=admin, target=user.username,
        detail=",".join(f"{k}={body[k]}" for k in ("role", "status") if body.get(k)), ip=_ip(request)
    )
    return _ok(user=user.public())

@router.post("/auth/admin/reset_link")
async def admin_reset_link(request: Request, admin: str = Depends(require_admin)):
    """Create a new password link (or resend the invitation) – e-mailed when SMTP is set up, always returned to the admin."""
    body = await _json(request)
    user = store.get_user(body.get("username", ""))
    if user is None: return _fail("User not found", 404)
    kind = "invite" if user.status == "invited" else "reset"
    token = store.issue_token(user, kind)
    link = _link(kind, token)
    sent = False
    if user.email and body.get("send", True):
        text = mailer.invite_text(user.full_name, user.username, link, INVITE_HOURS, admin) if kind == "invite" \
            else mailer.reset_text(user.full_name, user.username, link, RESET_HOURS)
        sent = mailer.send(user.email, "You are invited" if kind == "invite" else "Reset your password", text)
    store.audit("reset_link_created", actor=admin, target=user.username, detail=f"{kind}, mailed={sent}", ip=_ip(request))
    return _ok(link=link, mailed=sent, kind=kind, expires_hours=INVITE_HOURS if kind == "invite" else RESET_HOURS)

@router.post("/auth/admin/delete")
async def admin_delete(request: Request, admin: str = Depends(require_admin)):
    body = await _json(request)
    if (body.get("username") or "").lower() == admin: return _fail("You cannot delete your own account")
    try: store.delete_user(body.get("username", ""))
    except store.AuthError as e: return _fail(str(e))
    store.audit("user_deleted", actor=admin, target=body.get("username"), ip=_ip(request))
    # Project data (projects/<username>) is NOT deleted automatically – see docs/DEPLOYMENT.md
    return _ok(message="Account deleted. Project files are kept on the server.")

@router.get("/auth/admin/audit")
def admin_audit(limit: int = 200, admin: str = Depends(require_admin)):
    with store.db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(limit, 2000),))]
    return _ok(events=rows, now=time.time())

#  project sharing
def _signed_in(request: Request):
    user = current_user(request)
    if user is None: raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def _owned_project(ref: str, user) -> tuple[str, str]:
    """(owner, project) of a project the caller may manage: its owner, or an administrator."""
    owner, project = split_project_ref(ref, user.username)
    if owner == PUBLIC_OWNER:
        raise HTTPException(status_code=400, detail="The sample project is already visible to everyone")
    if owner != user.username and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only the owner of the project (or an administrator) can manage sharing")
    if not os.path.isdir(os.path.join(PROJECT_ROOT, owner, project)):
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    return owner, project

@router.get("/auth/shares")
def shares_of_project(project: str, request: Request):
    user = _signed_in(request)
    owner, name = split_project_ref(project, user.username)
    if owner == PUBLIC_OWNER:                                     # sample project: everyone reads, admins edit
        return _ok(owner=owner, project=name, can_manage=False, public=True,
            my_access="edit" if user.is_admin else "view", shares=[]
        )
    if owner != user.username and not user.is_admin:             # shared with me: show who shared it, not the list
        access = store.share_access(owner, name, user.username)
        if access is None: raise HTTPException(status_code=403, detail="Project not shared with you")
        return _ok(owner=owner, project=name, can_manage=False, my_access=access, shares=[])
    owner, name = _owned_project(project, user)
    return _ok(owner=owner, project=name, can_manage=True, shares=store.list_shares(owner=owner, project=name))

@router.post("/auth/shares/add")
async def shares_add(request: Request):
    user = _signed_in(request)
    if not user.can_write: return _fail("Read-only accounts cannot share projects", 403)
    body = await _json(request)
    owner, name = _owned_project(body.get("project") or "", user)
    try: share = store.add_share(owner, name, body.get("username"), body.get("access", "view"), user.username)
    except store.AuthError as e: return _fail(str(e))
    store.audit("project_shared", actor=user.username, target=share["grantee"],
                detail=f"{owner}/{name} ({share['access']})", ip=_ip(request))
    grantee = store.get_user(share["grantee"])
    if grantee and grantee.email:
        mailer.send(grantee.email, f"Project shared with you: {name}",
            f"Hello {grantee.full_name or grantee.username},\n\n{user.full_name or user.username} has shared the project "
            f"'{name}' with you ({share['access']} access).\nOpen it in {PUBLIC_URL} via Open Project: "
            f"'{owner}{SHARE_SEP}{name}'.\n"
        )
    return _ok(share=share, shares=store.list_shares(owner=owner, project=name))

@router.post("/auth/shares/remove")
async def shares_remove(request: Request):
    user = _signed_in(request)
    body = await _json(request)
    owner, name = split_project_ref(body.get("project") or "", user.username)
    grantee = (body.get("username") or "").lower()
    # owner / admin may remove anyone; a user may also leave a project shared with them
    if not (owner == user.username or user.is_admin or grantee == user.username):
        return _fail("Not allowed", 403)
    store.remove_share(owner, name, grantee)
    store.audit("project_unshared", actor=user.username, target=grantee, detail=f"{owner}/{name}", ip=_ip(request))
    return _ok(shares=store.list_shares(owner=owner, project=name) if owner == user.username or user.is_admin else [])

@router.get("/auth/shares/with_me")
def shares_with_me(request: Request):
    user = _signed_in(request)
    return _ok(shares=[{**s, "ref": f'{s["owner"]}{SHARE_SEP}{s["project"]}'} for s in store.list_shares(grantee=user.username)])

@router.get("/auth/users_lookup")
def users_lookup(q: str, request: Request):
    """Usernames to share with (active accounts only, at least 2 characters, max 10 results)."""
    user = _signed_in(request)
    q = (q or "").strip().lower()
    if len(q) < 2: return _ok(users=[])
    with store.db() as con:
        rows = con.execute("""SELECT username, full_name, organisation FROM users WHERE status = 'active' AND username != ?
            AND (username LIKE ? OR lower(full_name) LIKE ?) ORDER BY username LIMIT 10""", (user.username, f"{q}%", f"%{q}%")
        ).fetchall()
    return _ok(users=[dict(r) for r in rows])

@router.get("/auth/admin/shares")
def admin_shares(admin: str = Depends(require_admin)):
    return _ok(shares=store.list_shares())
