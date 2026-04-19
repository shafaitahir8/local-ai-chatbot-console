# Project Structure

## Repository Layout

```text
API Management Console/
├── app/
│   ├── main.py
│   ├── cli.py
│   ├── runtime_supervisor.py
│   ├── models/
│   ├── routers/
│   └── services/
├── frontend/
├── templates/
├── scripts/
├── deploy/
├── docs/
├── data/
├── logs/
├── start_console.bat
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Top-Level Directories

### `app/`

Primary Python application code.

- `main.py`: FastAPI app, lifecycle hooks, SPA serving, health, and metrics
- `cli.py`: Typer/Rich CLI exposed as `arctic`
- `runtime_supervisor.py`: entrypoint for background roles
- `models/`: SQLAlchemy models and Pydantic schemas
- `routers/`: HTTP route handlers
- `services/`: operational and domain logic

### `frontend/`

React admin application built with Vite and TypeScript.

- `src/`: source code
- `dist/`: production build served by FastAPI
- `package.json`: frontend scripts and dependencies

### `templates/`

Bot template definitions. Current built-in templates include:

- `openai_template.json`
- `local_template.json`
- `claude_template.json`
- `gemini_template.json`
- `knowledge_base_template.json`
- `customer_support_template.json`

### `scripts/`

Operational helpers for local development and Windows startup.

- `manage_runtime_roles.ps1`: start, stop, or inspect background roles
- `setup_ollama.bat`: install recommended local models
- `build_frontend.bat`: frontend build helper
- `diagnose.bat`: diagnostics helper
- tunnel configuration scripts

### `deploy/`

Production-oriented deployment assets.

- `docker-compose.hardened.yml`
- `systemd/`
- `winsw/`
- `windows/install_nssm_services.ps1`
- `prometheus/alert_rules.yml`

### `docs/`

Project documentation, including this file plus runtime and deployment guides.

### `data/`

Durable runtime state.

- `chatbot_console.db` when SQLite is used
- `runtime_roles.json` for local role manager state
- `runtime_secrets/` for persisted fallback secrets
- `bots/<bot_id>/` for bot-specific documents, embeddings, and session transcripts

### `logs/`

Runtime logs and launcher output.

- `app_YYYYMMDD.log`
- `startup_*.log`
- `<role>.stdout.log`
- `<role>.stderr.log`

## `app/models/`

- `database.py`: engine setup, sessions, table definitions, and DB helpers
- `schemas.py`: request/response models used by the API layer

## `app/routers/`

- `admin.py`: admin-only APIs
- `auth.py`: auth and rate limiting primitives
- `bots.py`: bot management and knowledge ingestion
- `chat.py`: public chat surface
- `embed.py`: embeddable widget routes
- `scraper.py`: scrape queue management
- `templates.py`: template APIs

## `app/services/`

### Core orchestration

- `bot_service.py`
- `supervisor_service.py`
- `scheduler_service.py`
- `safe_mode_service.py`
- `system_inspection_service.py`

### Retrieval and embeddings

- `embedding_service.py`
- `embedding_worker_service.py`
- `semantic_cache_service.py`

### External access

- `api_gateway_service.py`
- `tunnel_service.py`
- `redis_service.py`

### Provider abstraction

- `llm_providers/base_provider.py`
- `llm_providers/openai_provider.py`
- `llm_providers/gemini_provider.py`
- `llm_providers/claude_provider.py`
- `llm_providers/local_provider.py`

### Resilience and performance

- `circuit_breaker.py`
- `bulkhead.py`
- `router_service.py`
- `health_service.py`
- `metrics_service.py`

### Domain support

- `analytics_service.py`
- `chat_request_service.py`
- `scraper_service.py`
- `scraper_job_service.py`
- `template_service.py`
- `encryption_service.py`
- `runtime_secret_service.py`
- `logging_utils.py`
- `ollama_sentinel_service.py`

## Key Entry Points

- Web app: `python -m uvicorn app.main:app --reload`
- CLI: `arctic ...` or `python -m app.cli ...`
- Background role: `python -m app.runtime_supervisor <role>`
- Windows launcher: `start_console.bat`

