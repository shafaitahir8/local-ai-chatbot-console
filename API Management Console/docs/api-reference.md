# API Reference

The fastest way to inspect live schema details is the built-in OpenAPI UI at:

- `http://localhost:8000/docs`
- `http://localhost:8000/redoc`

This file documents the route groups and operational behavior.

## Auth Model

### Admin Authentication

Admin APIs require a bearer token obtained from:

- `POST /api/admin/login`

Protected admin routes depend on `get_current_user`.

### Public Chat Authentication

Public chat routes do not use admin login, but they are still protected by:

- per-request rate limiting
- bot availability checks
- safe mode enforcement
- idempotent request records

## Root and SPA Routes

- `GET /`: redirect to `/admin`
- `GET /admin`: serve the React admin app
- `GET /admin/{path}`: serve SPA routes under `/admin`
- `GET /login`: compatibility redirect to `/admin/login`
- `GET /favicon.ico`: redirect to frontend favicon when available

## Health and Metrics

- `GET /health/live`: process liveness probe
- `GET /health/ready`: dependency readiness probe
- `GET /health/fresh`: freshness probe for leases and background components
- `GET /health`: aggregate health report
- `GET /metrics`: Prometheus text output

## Admin API: `/api/admin`

Purpose: dashboard auth, analytics, system overview, and admin-only utilities.

Endpoints:

- `POST /api/admin/login`
- `POST /api/admin/logout`
- `GET /api/admin/me`
- `GET /api/admin/system-status`
- `POST /api/admin/change-password`
- `POST /api/admin/verify-password`
- `GET /api/admin/api-keys`
- `POST /api/admin/api-keys/reveal`
- `GET /api/admin/dashboard/stats`
- `GET /api/admin/analytics/all`
- `GET /api/admin/analytics/{bot_id}`
- `GET /api/admin/analytics/{bot_id}/messages`
- `GET /api/admin/analytics/{bot_id}/usage`

## Bot API: `/api/bots`

Purpose: authenticated CRUD for chatbot definitions and bot-owned knowledge data.

Endpoints:

- `GET /api/bots`
- `POST /api/bots`
- `GET /api/bots/{bot_id}`
- `PUT /api/bots/{bot_id}`
- `DELETE /api/bots/{bot_id}`
- `POST /api/bots/{bot_id}/clone`
- `POST /api/bots/{bot_id}/toggle`
- `POST /api/bots/{bot_id}/expire`
- `POST /api/bots/{bot_id}/recover`
- `GET /api/bots/{bot_id}/model-files`
- `POST /api/bots/{bot_id}/model-files/open`
- `POST /api/bots/{bot_id}/knowledge`
- `POST /api/bots/{bot_id}/knowledge/file`
- `POST /api/bots/{bot_id}/rebuild-embeddings`
- `GET /api/bots/{bot_id}/knowledge/stats`
- `GET /api/bots/{bot_id}/knowledge/chunks`
- `DELETE /api/bots/{bot_id}/knowledge/chunks/{chunk_id}`
- `POST /api/bots/{bot_id}/scrape`

## Public Chat API: `/api/bot`

Purpose: embeddable/public chatbot interaction surface.

Endpoints:

- `POST /api/bot/{bot_id}/chat`
- `GET /api/bot/{bot_id}/status`
- `GET /api/bot/{bot_id}/analytics`

### Chat Response Behavior

Chat responses can include runtime metadata beyond the model reply:

- `degraded_components`
- `data_freshness`
- `safe_modes`
- `retry_count`
- `fallback_used`

Repeated identical requests are coordinated through the durable `chat_requests` table.

## Scraper API: `/api/scraper`

Purpose: enqueue, review, inspect, and clean up scraper jobs.

Endpoints:

- `POST /api/scraper/start`
- `GET /api/scraper/jobs`
- `GET /api/scraper/jobs/{job_id}`
- `GET /api/scraper/jobs/{job_id}/preview`
- `POST /api/scraper/jobs/{job_id}/create-bot`
- `DELETE /api/scraper/jobs/{job_id}`

## Template API: `/api/templates`

Purpose: list template definitions and manage custom templates.

Endpoints:

- `GET /api/templates`
- `GET /api/templates/{template_id}`
- `GET /api/templates/{template_id}/defaults`
- `POST /api/templates`
- `PUT /api/templates/{template_id}`
- `DELETE /api/templates/{template_id}`

## Embed API: `/embed`

Purpose: provide a drop-in chatbot widget outside the admin dashboard.

Endpoints:

- `GET /embed/{bot_id}`: widget HTML
- `GET /embed/{bot_id}/config`: embed config JSON
- `GET /embed/{bot_id}/snippet`: copy-paste embed snippet

## Error and Degradation Semantics

The project intentionally prefers explicit degradation over silent failure.

Examples:

- provider circuit open: fail fast or route downward only when policy allows it
- RAG unavailable: surface `degraded_components=["rag"]`
- DB read-only: block mutating routes with `503`
- scraper paused: return a controlled `503` instead of accepting work that cannot run safely

