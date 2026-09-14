#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
stamp="$(date +%Y%m%d-%H%M%S)"
docker compose exec -T app python - <<'PY' | gzip > "backups/registro-${stamp}.sql.gz"
import sqlite3
connection=sqlite3.connect('/app/data/registro.db')
for line in connection.iterdump(): print(line)
PY
find backups -type f -name 'registro-*.sql.gz' -mtime +30 -delete
echo "Backup creato: backups/registro-${stamp}.sql.gz"
