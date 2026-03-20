#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/app" && -f "/app/alembic.ini" ]]; then
  ROOT_DIR="/app"
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi
cd "${ROOT_DIR}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set; pre-deploy check failed." >&2
  exit 1
fi

python3 - <<'PY'
import os
import psycopg

raw = (os.getenv("DATABASE_URL") or "").strip()
dsn = raw
if dsn.startswith("postgresql+psycopg://"):
    dsn = "postgresql://" + dsn[len("postgresql+psycopg://") :]
elif dsn.startswith("postgres://"):
    dsn = "postgresql://" + dsn[len("postgres://") :]

with psycopg.connect(dsn) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.execute(
            """
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'alembic_version'
              AND column_name = 'version_num'
            """
        )
        row = cur.fetchone()
        max_len = int(row[0]) if row and row[0] is not None else None
        if max_len is not None and max_len < 255:
            print(f"predeploy_alembic_version_resize from={max_len} to=255", flush=True)
            cur.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)")
print("predeploy_db_ok", flush=True)
PY

python3 - <<'PY'
import sys

from app.predeploy_checks import run_client_form_probe

ok, message = run_client_form_probe()
print(message, flush=True)
if not ok:
    sys.exit(1)
PY

alembic -c "${ALEMBIC_INI:-${ROOT_DIR}/alembic.ini}" upgrade head
echo "predeploy_migrations_ok"
