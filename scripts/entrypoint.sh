#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

# Usage:
#   ./scripts/entrypoint.sh web
#   ./scripts/entrypoint.sh worker
#   ./scripts/entrypoint.sh beat
#   ./scripts/entrypoint.sh call app.tasks.upload_previous_month_queue_logs
#   ./scripts/entrypoint.sh migrate
#   ./scripts/entrypoint.sh <any other command...>   # passthrough

MODE="${1:-web}"
shift || true

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

print("migrations_lock_acquire", flush=True)
with psycopg.connect(dsn) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
    try:
        print("migrations_start", flush=True)
        subprocess.check_call(["alembic", "upgrade", "head"])
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
    run_migrations
    exec celery -A app.celery_app.celery_app worker -l info --concurrency="${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    run_migrations
    exec celery -A app.celery_app.celery_app beat -l info
    ;;
  call)
    run_migrations
    exec celery -A app.celery_app.celery_app call "$@"
    ;;
  *)
    # Passthrough: still migrate first for safety, then exec the provided command.
    run_migrations
    exec "${MODE}" "$@"
    ;;
esac

