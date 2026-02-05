# Pro Website Content API

## Webhook input notes

The `/webhook/pro-form` endpoint accepts both `snake_case` and `camelCase` keys (for example
`business_domain`/`businessDomain`, `user_data`/`userData`, and `query_string`/`queryString`).
Unknown fields are ignored to prevent webhook breakage, and they are logged as schema drift.
Canonical internal field names remain `snake_case` for storage and downstream processing.

## Database + migrations (Render)

- Set `DATABASE_URL` to your Render Postgres connection string.
- Option A (recommended): use `scripts/start_render.sh` as the Render start command. It runs `alembic upgrade head` before starting Uvicorn.
- Option B: open a Render shell and run `alembic upgrade head` manually.

## Delivery outbox

- `DELIVERY_BASE_URL_TEMPLATE` (required if `job_details.base_url` is missing). Example: `https://{slug}.example.com`
- `DELIVERY_TARGET_NAMESPACE` (used in the default target path template).
- `DELIVERY_TARGET_PATH_TEMPLATE` (default: `/wp-json/{namespace}/v1/content`).
- `DELIVERY_MODE` (`zapier` or `direct`, default `zapier`).
- `ZAPIER_WEBHOOK_URL` (required when `DELIVERY_MODE=zapier`).
- `DELIVERY_HTTP_TIMEOUT` (seconds, default `30`).
- `PREVIEW_BASE_DOMAIN` (default `wp-premium-hosting.com`).
- `PREVIEW_NAMESPACE` (default `kaseya`).
- `SITE_CHECK_TIMEOUT` (seconds, default `10`).
- `SITE_CHECK_INITIAL_INTERVAL_SECONDS` (default `300`).
- `SITE_CHECK_INITIAL_ATTEMPTS` (default `12`).
- `SITE_CHECK_LONG_INTERVAL_SECONDS` (default `3600`).
- If your webhook payload includes `job_details.base_url`, it overrides the base URL template.

## Admin UI

- `ADMIN_PASSWORD` is required for `/admin/*` routes (HTTP Basic auth, any username).
