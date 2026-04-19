# Data Model and Storage

## Storage Backends

### Control Plane Database

The primary control-plane state lives in SQLAlchemy models defined in `app/models/database.py`.

- Development fallback: SQLite
- Production target: PostgreSQL
- Optional shared coordination: Redis

### Filesystem Artifacts

The database is not the only durable store. The project also persists bot-owned files under `data/`.

Common layout:

```text
data/
├── chatbot_console.db
├── runtime_roles.json
├── runtime_secrets/
└── bots/
    └── <bot_id>/
        ├── documents/
        ├── embeddings/
        └── sessions/
```

## Core Tables

### `bots`

Canonical chatbot definition and runtime configuration.

Contains:

- identity and template/provider type
- prompt and model settings
- encrypted provider API key
- feature flags such as memory and analytics
- usage counters and limits
- UI settings such as accent color and welcome message

### `messages`

Per-message conversation log.

Contains:

- `bot_id`
- `session_id`
- user and assistant text
- token counts
- latency
- client metadata

### `knowledge_chunks`

Durable RAG source-of-truth text segments.

Contains:

- chunk content
- source path or URL
- chunk index and word count
- chunk metadata
- soft-delete marker via `deleted_at`

### `analytics`

Daily aggregated usage per bot.

Contains:

- total messages
- total tokens
- average latency
- unique sessions
- estimated cost

### `analytics_sessions`

Daily uniqueness guard used to count sessions correctly.

Constraint:

- unique on `(bot_id, date, session_id)`

### `conversation_memory`

Session-scoped message memory store.

Contains:

- `bot_id`
- `session_id`
- JSON list of role/content messages

### `admin_users`

Admin dashboard accounts.

Contains:

- username
- password hash
- last login

### `scraper_jobs`

Durable scrape job record.

Contains:

- target URL
- status
- crawl depth
- counts
- review payload
- heartbeat
- lease owner

### `rate_limit_events`

Durable fallback rate limiting for public APIs when Redis is unavailable.

### `component_leases`

Durable lease heartbeat for runtime components.

Tracks:

- component name
- owner
- state
- generation
- retry timing
- last heartbeat
- metadata

### `task_runs`

Durable unit-of-work records for background processing.

Tracks:

- task type and key
- state
- checkpoint JSON
- payload JSON
- attempts
- retry timing

### `chat_requests`

Idempotent public chat request ledger.

Tracks:

- explicit or derived idempotency key
- request hash
- lifecycle state
- response payload
- degradation flags
- attempt count

### `outbox_events`

Deferred background work queue.

Examples of event types:

- `analytics_write`
- `conversation_memory_persist`
- `session_transcript_persist`
- `semantic_cache_fill`
- `restart_tunnel`
- scraper and embedding events

### `index_manifests`

Versioned embedding/index manifest state.

Tracks:

- manifest version
- build path
- state
- chunk count
- activation time
- last error

### `semantic_cache_entries`

Persistent response cache for exact and near-match query reuse.

Scoped by:

- bot
- provider
- model
- manifest version
- system prompt hash
- normalized query hash

## Bot Filesystem Layout

### `documents/`

Source files uploaded or created for a bot.

### `embeddings/`

Vector artifacts and manifests, including legacy files such as:

- `chunks.json`
- `faiss.index`
- `active_manifest.json`
- version build subdirectories for hardened rebuilds

### `sessions/`

Per-session transcript JSON files written asynchronously by the scheduler.

## RAG and Index Versioning

The project is moving from legacy direct files toward manifest-driven indexing:

- chunks remain durable in the database
- index builds are created in versioned directories
- `active_manifest.json` points to the active build
- queries use the active manifest
- deleted chunks are tombstoned in the database and trigger rebuild work

## Cache Layers

### L1 In-Memory

- bot config cache
- provider instance cache
- FAISS bundle cache keyed by `(bot_id, manifest_version)`
- verified token cache
- local provider readiness/model cache

### L2 Persistent

- semantic response cache in `semantic_cache_entries`

## Logs as Data

Operational logs live under `logs/` and are part of the day-to-day operating surface:

- launcher diagnostics
- per-role stdout/stderr
- JSON application log

