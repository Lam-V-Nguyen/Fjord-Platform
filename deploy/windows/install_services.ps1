# =====================================================================
# Install Fjord Platform as WINDOWS SERVICES (start at boot, restart on failure)
# Uses NSSM (https://nssm.cc) – unpack nssm.exe into C:\Tools\nssm\ first.
# Run in PowerShell "Run as Administrator":   .\deploy\windows\install_services.ps1
# =====================================================================
$ErrorActionPreference = "Stop"
$Root    = "C:\FjordPlatform"                       # repository root (PROJECT_DES)
$Python  = "C:\Envs\fjord_env\Scripts\python.exe"   # Python of the virtual environment
$Nssm    = "C:\Tools\nssm\nssm.exe"
$Caddy   = "C:\Tools\caddy\caddy.exe"
$Logs    = "C:\FjordData\logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

# ---- 1. Backend (uvicorn, 1 worker – simulation state lives in the process memory)
& $Nssm install FjordBackend $Python "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1"
& $Nssm set FjordBackend AppDirectory "$Root\backend"
& $Nssm set FjordBackend AppEnvironmentExtra "PYTHONUNBUFFERED=1" "PYTHONUTF8=1"
& $Nssm set FjordBackend AppStdout "$Logs\backend-stdout.log"
& $Nssm set FjordBackend AppStderr "$Logs\backend-stderr.log"
& $Nssm set FjordBackend AppRotateFiles 1
& $Nssm set FjordBackend AppRotateBytes 20000000
& $Nssm set FjordBackend Start SERVICE_AUTO_START
& $Nssm set FjordBackend AppExit Default Restart
& $Nssm set FjordBackend AppRestartDelay 5000
& $Nssm set FjordBackend DependOnService Redis        # name of the installed Redis/Memurai service
# Run under a dedicated service account, not Administrator:
# & $Nssm set FjordBackend ObjectName ".\svc_fjord" "PASSWORD"

# ---- 2. Caddy (HTTPS)
& $Nssm install FjordCaddy $Caddy "run --config $Root\deploy\Caddyfile"
& $Nssm set FjordCaddy AppDirectory "$Root\deploy"
& $Nssm set FjordCaddy Start SERVICE_AUTO_START
& $Nssm set FjordCaddy AppExit Default Restart

# ---- 3. Firewall: open only 80/443 (NEVER expose 8000 or 6379)
New-NetFirewallRule -DisplayName "Fjord HTTPS" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow -ErrorAction SilentlyContinue

Start-Service FjordBackend; Start-Service FjordCaddy
Write-Host "Done. Check: Invoke-WebRequest http://127.0.0.1:8000/health"
