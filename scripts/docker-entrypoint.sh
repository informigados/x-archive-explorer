#!/bin/sh
set -eu

echo "[entrypoint] Bootstrapping X Archive Explorer..."

MIGRATION_STATE="NO_STAMP"
if [ -f "instance/x_archive.db" ]; then
  if ! MIGRATION_STATE="$(python scripts/detect_legacy_db.py 2>/dev/null)"; then
    MIGRATION_STATE="NO_STAMP"
  fi
fi

if [ "$MIGRATION_STATE" = "STAMP_HEAD" ]; then
  echo "[entrypoint] Legacy database detected without Alembic history. Stamping head..."
  flask --app run.py db stamp head
fi

echo "[entrypoint] Applying database migrations..."
flask --app run.py db upgrade

echo "[entrypoint] Starting process: $*"
exec "$@"
