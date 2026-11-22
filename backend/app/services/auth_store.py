"""
auth_store.py – Accounts, sessions, invitations / password resets (SQLite, no database server needed).

Security
- Passwords are hashed with Argon2id (argon2-cffi) – plain passwords are never stored.
- Sessions: the cookie holds a random 256-bit token; the database keeps only its SHA-256 (a leaked database cannot be used to log in).
- Invite / reset tokens: random, single use, expiring, also stored hashed.
- Username = project folder name (projects/<username>), hence only [a-z0-9_.-].

Roles:    admin (user management + everything) · user (create / run models) · viewer (read only)
Statuses: active · pending (awaiting approval) · invited (password not set yet) · disabled
"""
from __future__ import annotations
import hashlib, re, secrets, sqlite3, threading, time
from contextlib import contextmanager
from dataclasses import dataclass
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError
from config import AUTH_DB, SESSION_HOURS, SESSION_MAX_DAYS, INVITE_HOURS, RESET_HOURS, LOGIN_MAX_FAILS

ROLES = ("admin", "user", "viewer")
STATUSES = ("active", "pending", "invited", "disabled")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESERVED = {"admin", "demo", "root", "system", "public", "static", "api", "login", "logout"}
MIN_PASSWORD = 10

_ph = PasswordHasher()                    # Argon2id, recommended default parameters (t=3, m=64 MiB, p=4)
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT UNIQUE,
    full_name TEXT DEFAULT '',
    organisation TEXT DEFAULT '',
    password_hash TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT DEFAULT '',
    created_at REAL NOT NULL,
    last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    expires_at REAL NOT NULL,
    ip TEXT, user_agent TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,                 -- 'invite' | 'reset'
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS login_fails (
    key TEXT NOT NULL,                     -- 'u:<username>' or 'ip:<ip>'
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fails_key ON login_fails(key, at);
CREATE TABLE IF NOT EXISTS project_shares (
    owner TEXT NOT NULL,                   -- username owning projects/<owner>/<project>
    project TEXT NOT NULL,
    grantee TEXT NOT NULL,                 -- username the project is shared with
    access TEXT NOT NULL DEFAULT 'view',   -- 'view' | 'edit'
    created_by TEXT, created_at REAL NOT NULL,
    PRIMARY KEY (owner, project, grantee)
);
CREATE INDEX IF NOT EXISTS ix_shares_grantee ON project_shares(grantee);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL, actor TEXT, action TEXT NOT NULL, target TEXT, detail TEXT, ip TEXT
);
"""

@dataclass
class User:
    id: int
    username: str
    email: str | None
    full_name: str
    organisation: str
    role: str
    status: str
    created_at: float
    last_login: float | None

    def public(self) -> dict:
        return {"username": self.username, "email": self.email, "full_name": self.full_name,
                "organisation": self.organisation, "role": self.role, "status": self.status,
                "created_at": self.created_at, "last_login": self.last_login}

    @property
    def is_admin(self) -> bool: return self.role == "admin"

    @property
    def can_write(self) -> bool: return self.role in ("admin", "user")


class AuthError(ValueError):
    """Business error whose message is safe to show to the user."""


# ---------------------------------------------------------------- connection
@contextmanager
def db():
    con = sqlite3.connect(AUTH_DB, timeout=10, isolation_level=None)   # autocommit; use BEGIN for transactions
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try: yield con
    finally: con.close()

def init_db():
    with db() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)

def _now() -> float: return time.time()
def _h(token: str) -> str: return hashlib.sha256(token.encode()).hexdigest()

def _row_user(r) -> User | None:
    if r is None: return None
    return User(r["id"], r["username"], r["email"], r["full_name"] or "", r["organisation"] or "",
                r["role"], r["status"], r["created_at"], r["last_login"])

def audit(action: str, actor: str | None = None, target: str | None = None, detail: str = "", ip: str | None = None):
    with db() as con:
        con.execute("INSERT INTO audit(at, actor, action, target, detail, ip) VALUES (?,?,?,?,?,?)",
                    (_now(), actor, action, target, detail[:500], ip))

# ---------------------------------------------------------------- validation
def normalise_username(username: str) -> str:
    u = (username or "").strip().lower()
    if not USERNAME_RE.fullmatch(u):
        raise AuthError("Username: 3–32 characters, lower-case letters, digits, '_', '-' or '.'")
    return u

def check_email(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    if not e: return None
    if not EMAIL_RE.fullmatch(e): raise AuthError("Invalid e-mail address")
    return e

def check_password(password: str, username: str = ""):
    if not password or len(password) < MIN_PASSWORD:
        raise AuthError(f"Password must have at least {MIN_PASSWORD} characters")
    if len(password) > 256: raise AuthError("Password is too long")
    if username and username.lower() in password.lower():
        raise AuthError("Password must not contain the username")
    if len(set(password)) < 5: raise AuthError("Password is too simple")

# ---------------------------------------------------------------- users
def get_user(username: str) -> User | None:
    with db() as con:
        return _row_user(con.execute("SELECT * FROM users WHERE username = ?", ((username or "").lower(),)).fetchone())

def get_user_by_email(email: str) -> User | None:
    with db() as con:
        return _row_user(con.execute("SELECT * FROM users WHERE email = ?", ((email or "").lower(),)).fetchone())

def list_users() -> list[dict]:
    with db() as con:
        return [_row_user(r).public() for r in con.execute("SELECT * FROM users ORDER BY status, username")]

def count_admins() -> int:
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND status='active'").fetchone()[0]

def create_user(username: str, email: str | None = None, password: str | None = None, role: str = "user",
                status: str = "pending", full_name: str = "", organisation: str = "", note: str = "",
                allow_reserved: bool = False) -> User:
    u = normalise_username(username)
    if u in RESERVED and not allow_reserved: raise AuthError("This username is reserved")
    if role not in ROLES or status not in STATUSES: raise AuthError("Invalid role or status")
    email = check_email(email)
    if password is not None: check_password(password, u)
    pw = _ph.hash(password) if password else None
    with _lock, db() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (u,)).fetchone(): raise AuthError("Username already exists")
        if email and con.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AuthError("An account with this e-mail already exists")
        con.execute("""INSERT INTO users(username, email, full_name, organisation, password_hash, role, status, note, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (u, email, full_name.strip()[:120], organisation.strip()[:120], pw, role, status, note[:500], _now()))
    return get_user(u)

def update_user(username: str, *, role: str | None = None, status: str | None = None, email: str | None = None,
                full_name: str | None = None, organisation: str | None = None) -> User:
    user = get_user(username)
    if user is None: raise AuthError("User not found")
    fields, vals = [], []
    if role is not None:
        if role not in ROLES: raise AuthError("Invalid role")
        fields.append("role = ?"); vals.append(role)
    if status is not None:
        if status not in STATUSES: raise AuthError("Invalid status")
        fields.append("status = ?"); vals.append(status)
    if email is not None: fields.append("email = ?"); vals.append(check_email(email))
    if full_name is not None: fields.append("full_name = ?"); vals.append(full_name.strip()[:120])
    if organisation is not None: fields.append("organisation = ?"); vals.append(organisation.strip()[:120])
    # Never lose the last active administrator
    demote = (role is not None and role != "admin") or (status is not None and status != "active")
    if user.is_admin and user.status == "active" and demote and count_admins() <= 1:
        raise AuthError("Cannot remove the last active administrator")
    if fields:
        with db() as con: con.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", (*vals, user.id))
    if status in ("disabled", "pending"): revoke_sessions(user.id)
    return get_user(username)

def delete_user(username: str):
    user = get_user(username)
    if user is None: raise AuthError("User not found")
    if user.is_admin and count_admins() <= 1: raise AuthError("Cannot delete the last administrator")
    with db() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user.id,))
        con.execute("DELETE FROM project_shares WHERE owner = ? OR grantee = ?", (user.username, user.username))

def set_password(username: str, password: str, activate: bool = False):
    user = get_user(username)
    if user is None: raise AuthError("User not found")
    check_password(password, user.username)
    with db() as con:
        con.execute("UPDATE users SET password_hash = ?" + (", status = 'active'" if activate else "") + " WHERE id = ?",
                    (_ph.hash(password), user.id))
    revoke_sessions(user.id)                   # password change -> sign out on every device

def verify_password(username: str, password: str) -> User | None:
    """Correct name + password -> User. Always hashes once so response time does not reveal which usernames exist."""
    with db() as con:
        r = con.execute("SELECT * FROM users WHERE username = ? OR email = ?",
                        ((username or "").strip().lower(), (username or "").strip().lower())).fetchone()
    stored = r["password_hash"] if r is not None and r["password_hash"] else _DUMMY_HASH
    try: ok = _ph.verify(stored, password or "")
    except (VerifyMismatchError, InvalidHashError, VerificationError): ok = False
    if not ok or r is None or not r["password_hash"]: return None
    if _ph.check_needs_rehash(stored):
        with db() as con: con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_ph.hash(password), r["id"]))
    return _row_user(r)

# ---------------------------------------------------------------- brute-force protection
_WINDOW = 15 * 60

def too_many_fails(username: str, ip: str) -> bool:
    since = _now() - _WINDOW
    with db() as con:
        nu = con.execute("SELECT COUNT(*) FROM login_fails WHERE key = ? AND at > ?", (f"u:{username.lower()}", since)).fetchone()[0]
        ni = con.execute("SELECT COUNT(*) FROM login_fails WHERE key = ? AND at > ?", (f"ip:{ip}", since)).fetchone()[0]
    return nu >= LOGIN_MAX_FAILS or ni >= LOGIN_MAX_FAILS * 4

def record_fail(username: str, ip: str):
    with db() as con:
        con.executemany("INSERT INTO login_fails(key, at) VALUES (?, ?)",
                        [(f"u:{(username or '').lower()}", _now()), (f"ip:{ip}", _now())])
        con.execute("DELETE FROM login_fails WHERE at < ?", (_now() - 86400,))

def clear_fails(username: str):
    with db() as con: con.execute("DELETE FROM login_fails WHERE key = ?", (f"u:{username.lower()}",))

# ---------------------------------------------------------------- sessions
def create_session(user: User, ip: str | None, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with db() as con:
        con.execute("""INSERT INTO sessions(token_hash, user_id, created_at, last_seen, expires_at, ip, user_agent)
                       VALUES (?,?,?,?,?,?,?)""",
                    (_h(token), user.id, now, now, now + SESSION_HOURS * 3600, ip, (user_agent or "")[:200]))
        con.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user.id))
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token

# short in-memory cache: avoids opening SQLite for EVERY request (map tiles, frames…)
_SESSION_CACHE: dict[str, tuple[float, User]] = {}
_CACHE_SEC = 30

def session_user(token: str | None) -> User | None:
    if not token: return None
    th, now = _h(token), _now()
    hit = _SESSION_CACHE.get(th)
    if hit and now - hit[0] < _CACHE_SEC: return hit[1]
    with db() as con:
        r = con.execute("""SELECT u.*, s.created_at AS s_created, s.last_seen AS s_seen, s.expires_at AS s_exp
                           FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?""", (th,)).fetchone()
        if r is None or r["s_exp"] < now or r["status"] != "active" or now - r["s_created"] > SESSION_MAX_DAYS * 86400:
            if r is not None: con.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            _SESSION_CACHE.pop(th, None)
            return None
        if now - r["s_seen"] > 300:            # sliding expiry (at most once per 5 minutes)
            con.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                        (now, now + SESSION_HOURS * 3600, th))
    user = _row_user(r)
    _SESSION_CACHE[th] = (now, user)
    if len(_SESSION_CACHE) > 5000: _SESSION_CACHE.clear()
    return user

def end_session(token: str | None):
    if not token: return
    _SESSION_CACHE.pop(_h(token), None)
    with db() as con: con.execute("DELETE FROM sessions WHERE token_hash = ?", (_h(token),))

def revoke_sessions(user_id: int):
    with db() as con:
        hashes = [r[0] for r in con.execute("SELECT token_hash FROM sessions WHERE user_id = ?", (user_id,))]
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    for h in hashes: _SESSION_CACHE.pop(h, None)

# ---------------------------------------------------------------- invite / password-reset tokens
def issue_token(user: User, purpose: str) -> str:
    hours = INVITE_HOURS if purpose == "invite" else RESET_HOURS
    token = secrets.token_urlsafe(32)
    with db() as con:
        # invalidate older tokens with the same purpose
        con.execute("UPDATE tokens SET used_at = ? WHERE user_id = ? AND purpose = ? AND used_at IS NULL",
                    (_now(), user.id, purpose))
        con.execute("INSERT INTO tokens(token_hash, user_id, purpose, expires_at) VALUES (?,?,?,?)",
                    (_h(token), user.id, purpose, _now() + hours * 3600))
    return token

def peek_token(token: str, purpose: str) -> User | None:
    with db() as con:
        r = con.execute("""SELECT u.* FROM tokens t JOIN users u ON u.id = t.user_id
                           WHERE t.token_hash = ? AND t.purpose = ? AND t.used_at IS NULL AND t.expires_at > ?""",
                        (_h(token or ""), purpose, _now())).fetchone()
    return _row_user(r)

def consume_token(token: str, purpose: str, new_password: str) -> User:
    user = peek_token(token, purpose)
    if user is None: raise AuthError("This link is invalid or has expired. Ask for a new one.")
    if user.status == "disabled": raise AuthError("This account is disabled")
    set_password(user.username, new_password, activate=(purpose == "invite" or user.status == "invited"))
    with db() as con: con.execute("UPDATE tokens SET used_at = ? WHERE token_hash = ?", (_now(), _h(token)))
    return get_user(user.username)


# ---------------------------------------------------------------- project sharing
SHARE_ACCESS = ("view", "edit")

def share_access(owner: str, project: str, grantee: str) -> str | None:
    """'view' | 'edit' when <owner>/<project> is shared with <grantee> (and the grantee is active), else None."""
    with db() as con:
        r = con.execute("""SELECT s.access FROM project_shares s JOIN users u ON u.username = s.grantee
                           WHERE s.owner = ? AND s.project = ? AND s.grantee = ? AND u.status = 'active'""",
                        (owner, project, grantee)).fetchone()
    return r[0] if r else None

def add_share(owner: str, project: str, grantee: str, access: str, created_by: str) -> dict:
    grantee = (grantee or "").strip().lower()
    if access not in SHARE_ACCESS: raise AuthError("Access must be 'view' or 'edit'")
    if grantee == owner: raise AuthError("A project cannot be shared with its owner")
    if get_user(grantee) is None: raise AuthError(f"User '{grantee}' does not exist")
    with db() as con:
        con.execute("""INSERT INTO project_shares(owner, project, grantee, access, created_by, created_at)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(owner, project, grantee) DO UPDATE SET access = excluded.access""",
                    (owner, project, grantee, access, created_by, _now()))
    return {"owner": owner, "project": project, "grantee": grantee, "access": access}

def remove_share(owner: str, project: str, grantee: str) -> bool:
    with db() as con:
        return con.execute("DELETE FROM project_shares WHERE owner = ? AND project = ? AND grantee = ?",
                           (owner, project, (grantee or "").lower())).rowcount > 0

def rename_shares(owner: str, old_project: str, new_project: str):
    with db() as con:
        con.execute("UPDATE project_shares SET project = ? WHERE owner = ? AND project = ?", (new_project, owner, old_project))

def drop_project_shares(owner: str, project: str):
    with db() as con:
        con.execute("DELETE FROM project_shares WHERE owner = ? AND project = ?", (owner, project))

def list_shares(owner: str | None = None, project: str | None = None, grantee: str | None = None) -> list[dict]:
    q, args = "SELECT owner, project, grantee, access, created_by, created_at FROM project_shares WHERE 1=1", []
    for col, val in (("owner", owner), ("project", project), ("grantee", grantee)):
        if val is not None: q += f" AND {col} = ?"; args.append(val)
    with db() as con:
        return [dict(r) for r in con.execute(q + " ORDER BY owner, project, grantee", args)]
