# Pro Website Content API

## Webhook input notes

The `/webhook/pro-form` endpoint accepts both `snake_case` and `camelCase` keys (for example
`business_domain`/`businessDomain`, `user_data`/`userData`, and `query_string`/`queryString`).
Unknown fields are ignored to prevent webhook breakage, and they are logged as schema drift.
Canonical internal field names remain `snake_case` for storage and downstream processing.
