# Architecture

## Overview

The Local AI Chatbot Deployment Console is a local-first control plane for building, operating, and exposing multiple chatbots. It combines:

- a FastAPI application for admin APIs, public chat APIs, embed routes, and health/metrics
- a React admin SPA served from the same backend
- a Typer-based CLI for headless administration and smoke testing
- a supervisor/outbox/lease control plane for self-healing background work
- provider integrations for OpenAI, Gemini, Claude, and local/Ollama models
- a scraper and RAG pipeline backed by SQLAlchemy, FAISS, and filesystem artifacts

## High-Level Components

### Backend Application

`app/main.py` boots the FastAPI process, configures logging, loads environment variables, initializes the database and templates, mounts the frontend assets, and exposes health/metrics endpoints.

Router groups:

- `app/routers/admin.py`: admin auth, dashboard stats, analytics, API key reveal
- `app/routers/bots.py`: bot CRUD, knowledge management, clone/toggle/recover, scrape enqueue
- `app/routers/chat.py`: public chat, status, and per-bot analytics
- `app/routers/scraper.py`: scrape job queue and review flows
- `app/routers/templates.py`: template CRUD and defaults
- `app/routers/embed.py`: embeddable widget HTML, config, and snippet generation
- `app/routers/auth.py`: JWT auth, rate limiting, and admin credential helpers

### Frontend

`frontend/` is a Vite + React + TypeScript SPA. It talks to the backend APIs and is served under `/admin`. The frontend build output is copied into `frontend/dist` and served directly by FastAPI.

### CLI

`app/cli.py` provides a headless administrative layer via Typer and Rich. The CLI intentionally reuses the same service layer, models, and database logic as the FastAPI app for parity.

### Runtime Supervision

`app/runtime_supervisor.py` starts specific background roles as separate processes. Each role writes a durable lease heartbeat into `component_leases` and uses outbox/task tables for recovery.

### Persistence

The control plane uses SQLAlchemy models in `app/models/database.py`.

- Development fallback: SQLite
- Production target: PostgreSQL via `DATABASE_URL`
- Optional shared coordination: Redis for shared rate limiting and cross-process state

### RAG and Vector Storage

The RAG pipeline uses:

- `knowledge_chunks` in the database as the durable source of truth
- bot-specific embedding files under `data/bots/<bot_id>/embeddings/`
- version manifests in `index_manifests`
- FAISS indices and chunk metadata managed by `app/services/embedding_service.py`
- persistent semantic response cache in `semantic_cache_entries`

## Runtime Roles

### `api`

Runs the FastAPI web surface. It does not own scraper, tunnel, gateway, or warmup loops directly anymore. It only serves requests and maintains an API lease heartbeat.

### `scheduler`

Processes general deferred outbox work and warmup logic. It handles events such as:

- `analytics_write`
- `conversation_memory_persist`
- `session_transcript_persist`
- `semantic_cache_fill`
- tunnel restart coordination

### `scraper-worker`

Consumes scrape jobs durably from the database. It owns task checkpoints, page persistence, and job recovery.

### `embedding-worker`

Consumes embedding rebuild work and manages the versioned vector index lifecycle.

### `gateway-agent`

Maintains the public API gateway process and its upstream circuit state.

### `tunnel-agent`

Owns the public tunnel lifecycle and can react to restart events from the outbox.

### `ollama-sentinel`

Monitors local model availability and keeps Ollama-related health state current.

## Request Flow: Public Chat

1. A client calls `POST /api/bot/{bot_id}/chat`.
2. The route applies async rate limiting and loads an async DB session.
3. The request is normalized into a durable `chat_requests` record for idempotency.
4. Safe modes are evaluated before expensive work begins.
5. `bot_service` loads bot config, memory summary, RAG context, and provider routing state.
6. `router_service` chooses a cheap, balanced, or premium route.
7. `semantic_cache_service` may return a cached answer before calling a model.
8. The selected provider generates a response.
9. Response metadata is persisted to `chat_requests`.
10. Deferred writes such as analytics, transcript persistence, memory persistence, and semantic cache fill are pushed to `outbox_events`.
11. The scheduler processes those writes asynchronously.

## Request Flow: Scraping

1. An admin queues a scrape job through the UI or CLI.
2. The job is stored in `scraper_jobs` and mirrored into `task_runs`.
3. `scraper-worker` claims the task and crawls with an `asyncio.Queue` frontier.
4. Pages are persisted once; checkpoints keep resumable state instead of full page bodies.
5. Extracted chunks are written to the database.
6. Embedding rebuild work is queued through the outbox/task system.

## Request Flow: Embeddings

1. Knowledge content is added, deleted, or scraped.
2. A rebuild event is queued.
3. `embedding-worker` rebuilds a versioned index in a build directory.
4. The new manifest is atomically activated.
5. Query-time RAG reads the active manifest and cached bundle.

## Key Services

- `bot_service.py`: central chat pipeline, bot CRUD logic, memory handling, transcript persistence, and provider orchestration
- `embedding_service.py`: chunking, embeddings, manifest management, index selection, and RAG retrieval
- `scraper_service.py`: crawl engine, revalidation, parsing, and page persistence
- `scraper_job_service.py`: durable scraper job orchestration
- `chat_request_service.py`: idempotent request lifecycle
- `router_service.py`: cost-aware deterministic provider routing
- `semantic_cache_service.py`: persistent exact and near-match cache
- `supervisor_service.py`: leases, outbox, retry timing, and durable task coordination
- `safe_mode_service.py`: degraded-mode enforcement
- `metrics_service.py`: in-process Prometheus metrics
- `api_gateway_service.py`: local proxy/gateway state
- `tunnel_service.py`: tunnel provider control

## Failure Isolation and Self-Healing

The control plane uses several explicit resilience patterns:

- component leases with stale-heartbeat detection
- outbox-driven deferred work instead of fire-and-forget background tasks
- durable task runs with checkpoints
- per-provider circuit breakers and bulkheads
- safe modes for dependency loss
- idempotent public chat request tracking

## Safe Modes

Safe modes are computed in `app/services/safe_mode_service.py`.

- `TUNNEL_OFFLINE_LOCAL_ONLY`: public endpoint unavailable, local admin/API still usable
- `SCRAPER_PAUSED`: scraper disabled because the worker is stale or backlog is too large
- `RAG_BYPASS_EXPLICIT`: RAG not trusted for the bot's current index state
- `PROVIDER_OPEN_CIRCUIT`: provider temporarily unavailable
- `DB_READ_ONLY`: mutating operations blocked

