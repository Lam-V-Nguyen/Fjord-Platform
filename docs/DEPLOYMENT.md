# Deploying Fjord Platform to production

This guide accompanies the `production-hardening` branch. It describes, **step by step**, how to move the platform from
a personal machine to a real multi-user server: professional sign-in, security fixes, HTTPS, running as a service,
backups and monitoring.

- Part A: what changed in the code (implemented and tested).
- Part B: installation steps, in order.
- Part C: day-to-day operations (user management, backups, upgrades).
- Part D: remaining work that is not coded yet (simulation queue, Linux, legal…).

---

## A. What changed

### A.1. Accounts and sign-in (replaces `allowed_users.json` + HTTP Basic)

| Before | After |
|---|---|
| Plain-text passwords in `allowed_users.json` | **Argon2id** hashes in the SQLite database `backend/data/auth.sqlite3` |
| Users send their password to the admin | The admin **invites by e-mail**, or the user **requests access** and the admin **approves**. Users choose their own password. |
| Browser sign-in dialog, no way to sign out | Dedicated `/login` page, **Sign out**, **Change password**, **Forgot password** |
| Adding a user needs a backend restart | Admin page `/admin`: invite, approve, change role, disable, create password-reset links, activity log |
| Unlimited password guessing | 5 failures within 15 minutes: locked for 15 minutes (per account and per IP) |
| No roles | `admin` · `user` · `viewer` (read only) |

**Sessions:** cookie `HttpOnly; Secure; SameSite=Lax` holding a random 256-bit token. The database stores only the
token's hash. Sessions expire after `SESSION_HOURS` idle hours and at most after `SESSION_MAX_DAYS` days.

**No changes needed in ~180 routes.** `functions.basic_auth` keeps **its name** but now reads the session cookie and
still returns the `username`.

New files: `services/auth_store.py`, `services/auth_router.py`, `services/mailer.py`, `tools/manage_users.py`,
`frontend/src/htmls/login.html`, `frontend/src/htmls/admin.html`, `frontend/src/services/authPage.js`,
`adminUsers.js`, `accountMenu.js`, `frontend/src/styles/auth.css`.

### A.2. Security fixes

1. **Every router requires a signed-in user at router level** (`main.py`). Previously 26 routes needed no sign-in,
   e.g. `/get_config_files`, `/data_upload_gis`, `/vertex_*`, `/save_era5`.
2. **`/get_config_files` takes the user from the session.** It used to trust `userName` in the request body, which
   allowed reading other users' projects.
3. **Path traversal:** `project_definer` validates the project name (`safe_project_name`) and the resolved path must
   stay inside `projects/<username>`. Names such as `../other_user/project` can no longer escape.
4. **`functions.error()` now RAISES.** It used to *return* a `JSONResponse` that none of its 149 callers returned, so
   the browser received `null` with status 200. Errors now come with the right HTTP status and an `error_id`. In
   production only a generic message is shown; details (traceback) go to `logs/app.log`, searchable by `error_id`.
5. **CSRF protection:** every POST/PUT/DELETE must come from this site (`Origin`/`Referer` check).
6. **Security headers:** `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options`, `Referrer-Policy`, and HSTS in production.
7. **Upload limit** `MAX_UPLOAD_MB` (default 200 MB). **Host check** (`ALLOWED_HOSTS`).
8. **`/src_backend/*` requires a signed-in user.**
9. **`credentials.json`:** the data services' `client_secret` and `client_password` are **encrypted** (Fernet, key
   derived from `SECRET_KEY`). Older plain values are still read and get encrypted on the next save.
10. **Saving alert thresholds is admin-only**, because the file is shared by everyone.
11. **`/docs` (Swagger) is disabled in production.**

### A.3. Configuration and operations

- `config.py` rewritten: **every setting comes from `.env`**, with a real `production` mode. In production the backend
  stops immediately if `SECRET_KEY`, `PUBLIC_URL` or Redis is missing.
- **Logs** go to `DATA_DIR/logs/app.log` (rotating, 10 × 10 MB) instead of `print`.
- **`/health`** for monitoring tools: checks Redis, the account database and free disk space.
- **Load limit:** when the number of running simulation processes (D-Flow FM, DELWAQ, Wflow) reaches
  `MAX_SOLVER_PROCESSES`, the routes `/start_sim_hyd`, `/start_sim_waq`, `/start_sim_calibration` and `/wflow_model`
  answer 503 "server is busy".
- **Process termination** uses `psutil`, so it works on both Windows and Linux (`functions.kill_process_tree`).
- `deploy/`: `.env.example`, `Caddyfile` (HTTPS), Windows (NSSM) and Linux (systemd) services, backup scripts.
- `backend/tests/test_auth.py` (7 tests) and `.github/workflows/ci.yml` (GitHub Actions).

### A.4. Project sharing

Every user works in their own folder `projects/<username>/`. An owner can share a project with other accounts:

| | Owner | *edit* share | *view* share |
|---|:-:|:-:|:-:|
| Open the project, dashboard, Visualization, results | ✓ | ✓ | ✓ |
| Change inputs, create / run HYD, WAQ, Wflow, calibration | ✓ | ✓ | ✗ |
| Delete the project, manage its sharing | ✓ | ✗ | ✗ |
| Copy it into their own folder (*copy project*) | ✓ | ✓ | ✗ |

- Share: **Settings → Share project** (for the project currently open). Type a username (suggestions appear after 2
  letters), choose *view* or *edit*, click *Share*. The other user gets an e-mail when SMTP is configured.
- The shared project appears in the other user's **Open Project** list as *"lake1 (shared by lam)"*. Internally it is
  referenced as `lam:lake1` (`<owner>:<project>`); the project chip shows *"lake1 · lam · view only"*.
- The receiver can remove it from their list with **Leave this project**; the owner or an admin can revoke it at any
  time; `/admin` lists every share.
- The account role still applies: a `viewer` account stays read-only even with an *edit* share.
- Each user's own `credentials.json` (data-download passwords) is never shared.
- Shares are stored in the `project_shares` table of `auth.sqlite3`; deleting a project or an account removes its shares.

Note: opening a scenario in Visualization records it as the project's "current WAQ scenario" (`output/config/config.json`),
also for users with a *view* share – the same file the owner's dashboard reads.

### A.5. Behaviour changes to be aware of

- A **missing** `projectName` (`None`) in a request body used to become the folder `user/None`; it is now
  **400 Project name is required**.
- The legacy account named `admin` still uses the shared `demo` project as before. New accounts cannot be called `admin`.
- The `viewer` role and users with a *view* share can only call read APIs (`functions._READ_PREFIXES` and
  `functions._READ_ROUTES`: dashboard, Visualization, status checks…). If a viewing feature is blocked for them, add
  its route to `_READ_ROUTES`.
- Invitation and password-reset links use `PUBLIC_URL`, so it must be the address users actually open.

---

## B. Installation, step by step

> Recommended order: do everything on a **personal machine** first (B.1–B.6), then on the **server** (B.7–B.15).

### B.1. Back up before updating

```powershell
# Back up the project folder and the legacy user file
robocopy C:\Users\vanln\Downloads\Fjord-Platform\backend\projects D:\backup\projects /MIR
copy C:\Users\vanln\Downloads\Fjord-Platform\backend\allowed_users.json D:\backup\
```

### B.2. Get the new code

```powershell
cd C:\Users\vanln\Downloads\Fjord-Platform
git fetch origin
git checkout production-hardening        # or review / merge the pull request on GitHub first
git status
```

### B.3. Install the new packages

```powershell
(& C:\Envs\fjord_env\Scripts\Activate.ps1)
pip install -r .\backend\requirements.txt          # adds: argon2-cffi, cryptography, psutil, python-dotenv, jinja2
```

### B.4. Create the `.env` file

Copy `deploy/.env.example` to `.env` in the **repository root**. On a personal machine this is enough:

```ini
ENV=development
PROJECT_DES=C:/Users/vanln/Downloads/Fjord-Platform
MET_ProstAPI_CLIENT_ID=...        # keep the keys already in use
```

With `ENV=development` cookies do not require HTTPS, `SECRET_KEY` is generated once and stored in
`backend/data/.secret_key`, and errors show full details.

### B.5. Create the accounts

Option 1, **migrate all legacy accounts** (old passwords get hashed):

```powershell
cd backend
python tools/manage_users.py import-json ..\backend\allowed_users.json --admin vlnguyen
python tools/manage_users.py list
```

- `--admin <name>`: which legacy accounts become administrators (repeat for several).
- Accounts with invalid names (capital letters, fewer than 3 characters, special characters) are skipped and listed;
  re-create them through invitations. The username is also the **project folder name**: if a name changes, rename
  `projects/<name>` accordingly.
- **After the import: delete `allowed_users.json`** (or move it off the server), because it holds plain-text passwords.
- Legacy passwords may be shorter than the new policy (10 characters). Ask users to change them via
  *Settings → Change password*.

Option 2, **start fresh**:

```powershell
cd backend
python tools/manage_users.py create-admin lam --email lam.nguyen@example.no      # asks for the password twice
```

Other commands: `set-password <user>`, `set-role <user> admin|user|viewer`, `set-status <user> active|disabled`.

### B.6. Test on the personal machine

```powershell
cd backend
python app/main.py
```

Open http://localhost:8000; it redirects to `/login`. Check:

1. Wrong password: "Wrong username or password". Five failures: temporary lock.
2. Correct password: the dashboard opens. **Settings → Account** shows the name and role.
3. **Settings → Manage users** (admins only) opens `/admin`. Invite a test account: without SMTP the invitation link
   is shown immediately for copying.
4. Open the invitation link in a private window and set a password: you are signed in.
5. Open the Workspace tools (HYD, WAQ, Visualization, Calibration…) to confirm every widget still works.
6. **Sign out**: back to `/login`. Reloading an old page also redirects to `/login`.
7. Run the automated tests:
   ```powershell
   cd backend
   pip install pytest httpx
   python -m pytest -q tests          # 7 passed
   ```

When everything works, merge the pull request into `main`.

### B.7. Prepare the server

| Item | Minimum recommendation |
|---|---|
| Operating system | **Windows Server 2022** (the Delft3D binaries in `backend/softs` work as they are). Linux: see D.2. |
| CPU / RAM | 16 cores / 64 GB for a few users running in parallel. Each D-Flow FM run uses 1–4 cores; calibration runs several scenarios at once. |
| Disks | System drive + **separate data drive ≥ 1 TB SSD** for `projects` (`zarr` outputs are large). Separate backup drive or NAS. |
| Network | A domain name (e.g. `fjord.ntnu.no`) pointing to the server IP. Ports 80 and 443 open to the Internet. |
| Account | Dedicated Windows account `svc_fjord`, not an administrator, with write access to the data folders. |

Install Python 3.12 (same version as in development), Git and the Visual C++ Redistributable (for Delft3D).

```powershell
git clone https://github.com/Lam-V-Nguyen/Fjord-Platform.git C:\FjordPlatform
python -m venv C:\Envs\fjord_env
C:\Envs\fjord_env\Scripts\pip install -r C:\FjordPlatform\backend\requirements.txt
# Copy the simulation software (not in git): backend\softs\x64, wflow, whitebox
robocopy <old machine>\backend\softs C:\FjordPlatform\backend\softs /E
```

### B.8. Redis

Windows: install **Memurai** (Redis-compatible for Windows, free Developer edition) or Redis in WSL. Linux:
`sudo apt install redis-server`.

Configuration (`memurai.conf` / `redis.conf`):

```conf
bind 127.0.0.1               # listen locally only
protected-mode yes
requirepass A_LONG_PASSWORD
maxmemory 2gb
maxmemory-policy allkeys-lru # cache: evict old keys when full
```

In `.env`: `REDIS_URL=redis://:A_LONG_PASSWORD@127.0.0.1:6379/0`. **Never** open port 6379 in the firewall.

### B.9. The server's `.env`

Copy `deploy/.env.example` to `C:\FjordPlatform\.env` and fill it in. **Required** entries:

```ini
ENV=production
PROJECT_DES=C:/FjordPlatform
PROJECT_ROOT=D:/FjordData/projects        # project data on its own drive
DATA_DIR=D:/FjordData/system              # account database, logs
PUBLIC_URL=https://fjord.example.no
SECRET_KEY=<string from the command below>
REDIS_URL=redis://:PASSWORD@127.0.0.1:6379/0
```

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

- **Keep `SECRET_KEY` secret and never change it once in use.** Changing it means stored data-service passwords must
  be re-entered.
- Restrict read access to `.env`: only `svc_fjord` and Administrators.
- Move the existing project data: `robocopy <old>\backend\projects D:\FjordData\projects /E`.

**E-mail (SMTP):** recommended, so invitations, approvals and password resets are sent automatically:

- Your organisation's **Microsoft 365**: `SMTP_HOST=smtp.office365.com`, port 587. Use a service mailbox such as
  `noreply@…` with SMTP AUTH enabled.
- Or a mail service: Brevo, Mailgun (EU), SendGrid. Set up SPF/DKIM for the domain so mail does not land in spam.
- Without SMTP everything still works: the admin copies invitation / reset links from `/admin` and sends them.

Create the administrator on the server:

```powershell
cd C:\FjordPlatform\backend
C:\Envs\fjord_env\Scripts\python tools/manage_users.py create-admin lam --email lam.nguyen@example.no
```

### B.10. HTTPS with Caddy

1. Download `caddy.exe` (https://caddyserver.com/download) into `C:\Tools\caddy\`.
2. Edit `deploy/Caddyfile`: replace `fjord.example.no` with the real domain and adjust the log path.
3. Check DNS: `nslookup fjord.example.no` must return the server IP.
4. On first start Caddy obtains a Let's Encrypt certificate (ports 80 and 443 must be open).

If your organisation already has a reverse proxy or its own certificates (IIS, F5, Nginx), forward to
`127.0.0.1:8000` with the `X-Forwarded-For`, `X-Forwarded-Proto` and `Host` headers, allow request bodies up to
200 MB and a 30-minute timeout.

### B.11. Run as Windows services

```powershell
# PowerShell "Run as Administrator"; edit the paths at the top of the script first
.\deploy\windows\install_services.ps1
Get-Service FjordBackend, FjordCaddy
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -Expand Content
```

- The backend runs with **1 worker**. Simulation state, project caches and locks live in the process memory, so
  **do not increase `--workers`** until D.1 is done.
- The services restart on failure and start at boot.
- Run the backend service as `svc_fjord` (the `ObjectName` line in the script).

### B.12. Firewall

- Open only **80/443** to the outside. **8000** (uvicorn) and **6379** (Redis) are internal only.
- For internal use only: restrict 443 to the organisation's IP ranges or the VPN.
- Remote Desktop: only through the VPN, with NLA; never expose 3389 to the Internet.

### B.13. Automatic backups

1. Edit the paths at the top of `deploy/windows/backup.ps1` (target on **another drive** and **somewhere outside the server**).
2. Task Scheduler → *Create Task*: run as `svc_fjord`, *Run whether user is logged on or not*, daily trigger at 02:00.
   Action: `powershell.exe -ExecutionPolicy Bypass -File C:\FjordPlatform\deploy\windows\backup.ps1`.
3. **Test a restore right away:** copy `auth.sqlite3` and one project from the backup to a test machine, start the
   backend, sign in and open that project.

The backup script covers:

| Item | Method |
|---|---|
| Account database `auth.sqlite3` | SQLite backup API, safe while the backend runs |
| `.env`, `dashboard_thresholds.json` | Plain copy |
| Project folders | robocopy mirror, skipping rebuildable caches (`movement`, `viewer`) |
| Daily copies | 14 kept, then copied to the NAS / off-site storage |

### B.14. Monitoring

- **Uptime Kuma** (self-hosted, free) or **UptimeRobot**: check `https://fjord.example.no/health` every 5 minutes and
  alert by e-mail or Teams on anything other than 200.
- `/health` returns 503 when Redis or the account database fails, or the disk is more than 95 % full.
- Logs: `D:\FjordData\system\logs\app.log` (errors carry the same `error_id` the user sees) and `backend-stderr.log`.
- Add a disk-space alert at 80 % (Windows Performance Monitor or your organisation's monitoring).

### B.15. Go-live checklist

- [ ] `https://…` has a valid certificate; `http://` redirects to `https://`
- [ ] `/docs` returns 404; errors only show "Internal error (id …)"
- [ ] Without a session every API returns 401; the home page redirects to `/login`
- [ ] Account A cannot open account B's project (try `projectName` = `../B/project`)
- [ ] Five wrong passwords: temporary lock
- [ ] Invitation, forgot password and approval e-mails arrive and do not land in spam
- [ ] `allowed_users.json` has been removed from the server
- [ ] The first nightly backup succeeded and **a restore has been tested**
- [ ] `/health` is monitored and someone receives the alerts
- [ ] 2–3 HYD runs and 1 calibration in parallel do not overload the server; tune `MAX_SOLVER_PROCESSES` if needed
- [ ] OWASP ZAP baseline scan shows no High findings
- [ ] Terms of use and privacy pages are published (see D.4)

---

## C. Day-to-day operations

### C.1. User management (`/admin`)

| Task | How |
|---|---|
| Add a user | *Invite a user* → username, e-mail, role → *Send invitation*. The e-mail is sent automatically (or copy the link). Links are single use and expire after 72 hours. |
| Approve an access request | Account with status **pending** → *Approve* (the user receives an e-mail) |
| Forgotten password | The user clicks *Forgot password*, or the admin clicks *Reset password* and sends the link |
| Suspend | Set the status to **disabled**: all of that user's sessions are signed out immediately |
| Read-only access | Role **viewer**, or a *view* share of a single project (Settings → Share project) |
| Review shares | Card *Shared projects*: every share, with *Revoke* |
| Delete | *Delete*: **project files are kept** in `projects/<username>`. Remove them manually if the user asks (GDPR). |
| Audit | *Recent activity*: sign-ins, failed sign-ins, invitations, approvals, role changes (with IP) |

The last **active administrator** cannot be deleted or demoted.

### C.2. Upgrading

```powershell
Stop-Service FjordBackend
cd C:\FjordPlatform
git pull
C:\Envs\fjord_env\Scripts\pip install -r backend\requirements.txt
cd backend; C:\Envs\fjord_env\Scripts\python -m pytest -q tests
Start-Service FjordBackend
```

- Stop the service only when **no simulation is running** (Task Manager shows no `dflowfm` / `delwaq`).
- Keep a **staging** machine or folder to try new versions first, with another port and its own `.env`.
- Version releases with git tags: `git tag v1.0.0 && git push --tags`.

### C.3. When a user reports an error

The user sees a message such as *"Internal error (id 3fa9c21b)"*. Find that id in the log:

```powershell
Select-String -Path D:\FjordData\system\logs\app.log* -Pattern 3fa9c21b -Context 0,30
```

---

## D. Remaining work (not coded yet)

### D.1. Simulation queue (high priority from ~10 users)

Simulations still start with `subprocess.Popen` **inside the web process**, and `MAX_SOLVER_PROCESSES` only *refuses*
new runs when the server is busy. The next step is a **job queue**:

- Clicking Run puts the job in a queue; the interface shows "Queued (position 3)".
- A separate worker runs the jobs in turn.
- Restarting the web server does not affect running simulations.
- Only then can `--workers` be increased.

Suggested approach with **RQ** (Redis Queue), lightweight and using the existing Redis:

```python
# services/jobs.py
from redis import Redis
from rq import Queue, Retry
from config import REDIS_URL
q = Queue("simulations", connection=Redis.from_url(REDIS_URL), default_timeout=48 * 3600)

def enqueue_hyd(project_name: str, user: str, mdu_path: str):
    # Worker function: move the body of start_sim_hyd (after the MDU is prepared) into run_hyd_job(...)
    job = q.enqueue("services.sim_jobs.run_hyd_job", project_name, mdu_path,
                    job_id=f"hyd:{project_name}", meta={"user": user}, retry=Retry(max=0))
    return job.id, q.get_job_position(job.id)
```

```powershell
# Worker: run as a separate NSSM service (FjordWorker); number of workers = simulations in parallel
C:\Envs\fjord_env\Scripts\rq.exe worker simulations --url redis://:PASSWORD@127.0.0.1:6379/0 --worker-class rq.worker.SimpleWorker
```

(Windows needs `SimpleWorker`, because RQ's default worker relies on Linux `fork`.) Status and progress: the worker
writes `.simulation_status_*.json` files like the existing calibration mechanism, so the `check_sim_status_*` routes
can be reused.

### D.2. Linux server (optional; cheaper and more stable for servers)

- Delft3D FM / D-Water Quality for Linux: build from the open-source code (https://oss.deltares.nl) or use the Deltares
  containers. Update the paths in `sim_router.py` (`x64/dflowfm/scripts/run_dflowfm.bat` → `run_dflowfm.sh`) and drop
  `cmd.exe /c` and the `CREATE_NO_WINDOW` / `BELOW_NORMAL_PRIORITY_CLASS` flags (use `nice` instead).
- Wflow: the Linux build of `wflow_cli`.
- Services: `deploy/linux/fjord-backend.service` (systemd), `deploy/linux/backup.sh` (cron), Caddy from `apt`.

### D.3. Organisation sign-in (SSO), when most users come from universities or institutes

Can be added on top of the existing accounts:

- **Feide** (Norwegian universities and research institutes): register the service at https://dashboard.feide.no
  (OpenID Connect).
- **Microsoft Entra ID**: register an app in the organisation's Azure portal.
- **Keycloak** (self-hosted): combines Feide, Microsoft, Google and local accounts in one place.

Implementation with `Authlib`: add `/auth/oidc/login` and `/auth/oidc/callback`. On a successful callback, find the
account by e-mail (or create a **pending** account awaiting approval) and call `auth_store.create_session()` as for a
normal sign-in. Everything else (cookie, roles, `/admin`) stays the same.

### D.4. Legal and terms

| Item | To do |
|---|---|
| **GDPR** (Norway is in the EEA) | Privacy page: what is stored (name, e-mail, organisation, IP in the activity log), for how long, who to contact for deletion. Purge logs older than 12 months. |
| **Open-Meteo** | The free API is for **non-commercial** use only. Paid or commercial use requires an API plan. |
| **MET Norway (Frost, Locationforecast)** | Attribution (CC BY 4.0); `User-Agent` with a contact e-mail; keep the server-side cache as now. |
| **Basemaps** | `tile.openstreetmap.org` **forbids heavy use** → switch to MapTiler, Stadia Maps or your own tile server. Esri World Imagery/Canvas: public use needs an ArcGIS account and proper attribution. |
| **Copernicus (ERA5), NVE** | Attribution according to their licences |
| **Delft3D, Wflow, WhiteboxTools** | Check the redistribution terms of the binaries in `backend/softs` |
| **Terms of use** | Simulation results are indicative; users are responsible for uploaded data; resource limits |

### D.5. Quality and performance

- Extend `backend/tests`: `face_order`, `quality_status`, `validate_thresholds`, reading HYD/WAQ results from a small
  sample project.
- CI is in place (`.github/workflows/ci.yml`): backend tests, JavaScript syntax check, `pip-audit` for vulnerable packages.
- Pin the CDN library versions (Leaflet, Plotly, Gridstack, Three.js). If the server has no Internet access, vendor
  them into `frontend/src/vendor/`.
- Replace `?v=Date.now()` (icons reloaded every time) with the application version.
- Per-user disk quota: add a `quota_gb` column to `users` and check it before starting a simulation; show usage on `/admin`.
- Load test: 5–10 users opening the dashboard and running simulations at once; watch CPU, RAM and response times.

---

## Appendix: `.env` reference

| Variable | Default | Meaning |
|---|---|---|
| `ENV` | `development` | `production` enables all mandatory checks |
| `PROJECT_DES` | (required) | Repository root |
| `PROJECT_ROOT` | `<repo>/backend/projects` | Project data |
| `DATA_DIR` | `<repo>/backend/data` | Account database, logs, development key |
| `PUBLIC_URL` | `http://localhost:8000` | Address users open (used in e-mail links) |
| `SECRET_KEY` | generated (dev) | Encryption key – required in production |
| `ALLOWED_HOSTS` / `ALLOWED_ORIGINS` | from `PUBLIC_URL` | Accepted host names / origins |
| `SESSION_HOURS` / `SESSION_MAX_DAYS` | 12 / 14 | Session lifetime |
| `LOGIN_MAX_FAILS` | 5 | Failures before a 15-minute lock |
| `SELF_REGISTRATION` | `true` | Enables "Request access" |
| `INVITE_HOURS` / `RESET_HOURS` | 72 / 2 | Link validity |
| `SMTP_*`, `ADMIN_EMAILS` | empty | Outgoing e-mail; recipients of access requests |
| `REDIS_URL` / `REQUIRE_REDIS` | `redis://localhost:6379/0` / per `ENV` | Redis |
| `MAX_UPLOAD_MB` | 200 | Maximum upload size |
| `MAX_SOLVER_PROCESSES` | CPU cores / 2 | Maximum simultaneous simulation processes |
| `SHOW_ERROR_DETAILS` | per `ENV` | Show error details in the browser |
| `LOG_LEVEL` | `INFO` | Log level |
