# Pro Website Content API

## Webhook input notes

The `/webhook/pro-form` endpoint accepts both `snake_case` and `camelCase` keys (for example
`business_domain`/`businessDomain`, `user_data`/`userData`, and `query_string`/`queryString`).
Unknown fields are ignored to prevent webhook breakage, and they are logged as schema drift.
Canonical internal field names remain `snake_case` for storage and downstream processing.

## Database + migrations (Render)

- Set `DATABASE_URL` to your Render Postgres connection string.
- If you are using the Dockerfile + `scripts/entrypoint.sh`, run the container in `web` mode so it runs migrations before starting Uvicorn.
  - Render "Docker Command" for the web service: `web`
  - Render "Docker Command" for the worker service: `worker`
  - Render "Docker Command" for the beat service: `beat`
- If you are *not* using Docker, you can use `scripts/start_render.sh` as a start command (it runs `alembic upgrade head` before Uvicorn).
- Or: open a Render shell and run `alembic -c /app/alembic.ini upgrade head` manually.

## Delivery outbox

- `DELIVERY_BASE_URL_TEMPLATE` (required if `job_details.base_url` is missing). Example: `https://{slug}.example.com`
- `DELIVERY_TARGET_NAMESPACE` (used in the default target path template).
- `DELIVERY_TARGET_PATH_TEMPLATE` (default: `/wp-json/{namespace}/v1/content`).
- `DELIVERY_MODE` (default `manual`):
  - `manual`: always requires a user-entered Delivery URL in `/ui/deliveries`
  - `zapier`: prefill Delivery URL if available (future), otherwise manual entry
  - `automatic`: reserved for future DB-driven Delivery URL resolution (not implemented yet)
  - `direct`: POST directly to the WP endpoint (bypasses Zapier)
- `ZAPIER_WEBHOOK_URL` (required when `DELIVERY_MODE=zapier`).
- `DELIVERY_HTTP_TIMEOUT` (seconds, default `30`).
- Payload storage (waiting-to-send):
  - Canonical storage: Postgres `job_copies` table (for admin viewing + durability across deploys).
  - On-disk mirror (optional): `PAYLOAD_DISK_DIR` (default: `/var/data/procontentapi`).
    - For Render, attach a Persistent Disk and mount it at `/var/data` if you want the on-disk payloads to persist.
    - Delivery sending will fall back to Postgres if the on-disk file is missing.
  - `ARCHIVE_TO_S3_ON_SEND` (default: `0`). If enabled (`1`/`true`), after a successful send the payload is archived to S3.
  - `S3_DELIVERED_PREFIX` (default: `delivered/`) for S3 archival keys.
- `PREVIEW_BASE_DOMAIN` (default `wp-premium-hosting.com`).
- `PREVIEW_NAMESPACE` (default `kaseya`).
- `SITE_CHECK_TIMEOUT` (seconds, default `10`).
- `SITE_CHECK_INITIAL_INTERVAL_SECONDS` (default `300`).
- `SITE_CHECK_INITIAL_ATTEMPTS` (default `12`).
- `SITE_CHECK_LONG_INTERVAL_SECONDS` (default `3600`).
- If your webhook payload includes `job_details.base_url`, it overrides the base URL template.

## Admin UI

- `ADMIN_PASSWORD` is required for `/admin/*` routes (HTTP Basic auth, any username).
- Job copy payloads admin:
  - `/admin/copies` lists stored job copy payloads
  - `/admin/copies/{job_id}` views a payload
  - Deleting a payload moves it to `recently_deleted_job_copies` for 48 hours, then it is destroyed.
