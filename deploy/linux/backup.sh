#!/usr/bin/env bash
# Nightly backup (cron: 0 2 * * *  /opt/fjord/deploy/linux/backup.sh >> /var/log/fjord-backup.log 2>&1)
set -euo pipefail
ROOT=/opt/fjord
PROJECTS=${PROJECT_ROOT:-$ROOT/backend/projects}
DATA=${DATA_DIR:-$ROOT/backend/data}
DEST=/backup/fjord
STAMP=$(date +%F_%H%M)
mkdir -p "$DEST/$STAMP"
# Account database (consistent while running)
sqlite3 "$DATA/auth.sqlite3" ".backup '$DEST/$STAMP/auth.sqlite3'"
cp "$ROOT/.env" "$DEST/$STAMP/env.txt"; chmod 600 "$DEST/$STAMP/env.txt"
cp "$ROOT/backend/src/dashboard_thresholds.json" "$DEST/$STAMP/" || true
# Projects: rsync mirror, skipping caches that can be rebuilt
rsync -a --delete --exclude 'movement/' --exclude 'viewer/' --exclude '__pycache__/' "$PROJECTS/" "$DEST/projects-mirror/"
# Keep the 14 most recent copies
ls -1d "$DEST"/20??-??-??_* | sort -r | tail -n +15 | xargs -r rm -rf
# Off-site copy (e.g. restic / rclone to S3, Azure Blob…)
# restic -r s3:https://s3.example.no/fjord-backup backup "$DEST" && restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
echo "Backup OK -> $DEST/$STAMP"
