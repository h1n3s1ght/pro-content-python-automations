#!/usr/bin/env bash
set -euo pipefail

# Determine repo root. On Docker/Render images we expect the app at /app.
# When the script is invoked via a PATH alias/symlink (e.g. `web`, `worker`),
# $0 and BASH_SOURCE can point at /usr/local/bin/*, so don't derive ROOT_DIR
# from those in containers.
if [[ -d "/app" && -f "/app/alembic.ini" ]]; then
  ROOT_DIR="/app"
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi
cd "${ROOT_DIR}"

# Usage:
#   ./scripts/entrypoint.sh web
#   ./scripts/entrypoint.sh worker
#   ./scripts/entrypoint.sh beat
#   ./scripts/entrypoint.sh call app.tasks.upload_previous_month_queue_logs
#   ./scripts/entrypoint.sh migrate
#   ./scripts/entrypoint.sh <any other command...>   # passthrough

INVOKED="$(basename "$0")"
case "${INVOKED}" in
  web|worker|beat|call|migrate)
    MODE="${INVOKED}"
    ;;
  *)
    MODE="${1:-web}"
    shift || true
    ;;
esac

echo "entrypoint_mode=${MODE}" >&2

run_migrations() {
  if [[ "${RUN_MIGRATIONS:-1}" == "0" ]]; then
    echo "RUN_MIGRATIONS=0; skipping alembic upgrade." >&2
    return 0
  fi

  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is not set; refusing to run migrations." >&2
    exit 1
  fi

  # Prevent concurrent migration runs across multiple services.
  # Uses a Postgres advisory lock held for the duration of `alembic upgrade head`.
  ALEMBIC_INI="${ALEMBIC_INI:-${ROOT_DIR}/alembic.ini}"
  export ALEMBIC_INI
  python - <<'PY'
import os
import subprocess
import sys

import psycopg

raw = (os.getenv("DATABASE_URL") or "").strip()
if not raw:
    print("DATABASE_URL missing", file=sys.stderr)
    sys.exit(1)

# psycopg can't parse SQLAlchemy driver urls.
dsn = raw
if dsn.startswith("postgresql+psycopg://"):
    dsn = "postgresql://" + dsn[len("postgresql+psycopg://") :]
elif dsn.startswith("postgres://"):
    dsn = "postgresql://" + dsn[len("postgres://") :]

LOCK_ID = 917203041901  # stable int64; arbitrary but constant for this app

cfg = (os.getenv("ALEMBIC_INI") or "").strip() or "alembic.ini"
if not os.path.exists(cfg):
    print(f"alembic_config_missing: {cfg}", file=sys.stderr)
    sys.exit(1)

print("migrations_lock_acquire", flush=True)
with psycopg.connect(dsn) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
    try:
        print("migrations_start", flush=True)
        subprocess.check_call(["alembic", "-c", cfg, "upgrade", "head"])
        print("migrations_ok", flush=True)
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        print("migrations_lock_release", flush=True)
PY
}

case "${MODE}" in
  migrate)
    run_migrations
    ;;
  web)
    run_migrations
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  worker)
    exec celery -A app.celery_app.celery_app worker -l info --concurrency="${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    exec celery -A app.celery_app.celery_app beat -l info
    ;;
  call)
    exec celery -A app.celery_app.celery_app call "$@"
    ;;
  *)
    # Passthrough: exec the provided command as-is.
    exec "${MODE}" "$@"
    ;;
esac
