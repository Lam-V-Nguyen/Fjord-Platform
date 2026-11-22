# =====================================================================
# Nightly backup: user projects + account database + configuration (.env, thresholds)
# Schedule: Task Scheduler -> Daily 02:00 -> powershell.exe -ExecutionPolicy Bypass -File C:\FjordPlatform\deploy\windows\backup.ps1
# Copy the backups further to a location OUTSIDE the server (NAS, OneDrive/SharePoint, S3…) – see $Offsite.
# =====================================================================
$ErrorActionPreference = "Stop"
$Root     = "C:\FjordPlatform"
$Projects = "$Root\backend\projects"            # or PROJECT_ROOT from .env
$DataDir  = "$Root\backend\data"                # or DATA_DIR
$Dest     = "E:\FjordBackup"                    # target drive / folder (not the data drive)
$Offsite  = "\\nas01\backup\fjord"             # set to "" if not available yet
$Stamp    = Get-Date -Format "yyyy-MM-dd_HHmm"
$Target   = Join-Path $Dest $Stamp
New-Item -ItemType Directory -Force -Path $Target | Out-Null

# 1. Account database: consistent copy through the SQLite backup API (safe while the backend runs)
& "C:\Envs\fjord_env\Scripts\python.exe" -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" "$DataDir\auth.sqlite3" "$Target\auth.sqlite3"

# 2. Configuration
Copy-Item "$Root\.env" "$Target\env.txt" -ErrorAction SilentlyContinue
Copy-Item "$Root\backend\src\dashboard_thresholds.json" $Target -ErrorAction SilentlyContinue

# 3. Projects: robocopy mirror (copies changed files only, skips caches that can be rebuilt)
robocopy $Projects "$Dest\projects-mirror" /MIR /R:2 /W:5 /NFL /NDL /NP /XD movement viewer __pycache__ /LOG:"$Target\robocopy.log" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

# 4. Keep the 14 most recent database/configuration copies
Get-ChildItem $Dest -Directory | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_' } |
    Sort-Object Name -Descending | Select-Object -Skip 14 | Remove-Item -Recurse -Force

# 5. Off-site copy
if ($Offsite) { robocopy $Dest $Offsite /MIR /R:2 /W:10 /NFL /NDL /NP | Out-Null }
Write-Host "Backup OK -> $Target"
