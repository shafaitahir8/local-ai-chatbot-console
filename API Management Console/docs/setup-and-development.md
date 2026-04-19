# Setup and Development

## Prerequisites

- Python 3.10 or newer
- Node.js and npm for frontend development/builds
- PowerShell on Windows for local role management
- Optional: Ollama for local model execution
- Optional: PostgreSQL for production-grade control-plane storage
- Optional: Redis for shared rate limiting and multi-process coordination

## Quick Start on Windows

The recommended local entrypoint is:

```powershell
.\start_console.bat
```

What it does:

- verifies Python
- activates or creates the virtual environment
- installs Python dependencies when needed
- loads `.env`
- checks tunnel prerequisites
- checks Ollama
- starts supervised background roles
- starts Uvicorn with reload
- opens the admin dashboard

Default local URLs:

- Admin UI: `http://localhost:8000/admin`
- OpenAPI docs: `http://localhost:8000/docs`
- Metrics: `http://localhost:8000/metrics`

## Manual Backend Setup

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## CLI Setup

Install the editable package to make the `arctic` entrypoint available:

```powershell
.\venv\Scripts\activate
pip install -e .
arctic --help
```

If you do not want to install the entrypoint yet, you can always run:

```powershell
python -m app.cli --help
```

## Background Roles

The split runtime expects these roles:

- `scheduler`
- `scraper-worker`
- `embedding-worker`
- `gateway-agent`
- `tunnel-agent`
- `ollama-sentinel`

Manual role startup examples:

```powershell
python -m app.runtime_supervisor scheduler
python -m app.runtime_supervisor scraper-worker
python -m app.runtime_supervisor embedding-worker
```

Windows helper script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\manage_runtime_roles.ps1 start
powershell -ExecutionPolicy Bypass -File .\scripts\manage_runtime_roles.ps1 status
powershell -ExecutionPolicy Bypass -File .\scripts\manage_runtime_roles.ps1 stop
```

## Frontend Development

Frontend lives in `frontend/` and uses Vite.

```powershell
cd frontend
npm install
npm run dev
```

Production build:

```powershell
cd frontend
npm run build
```

The backend serves the built files from `frontend/dist`.

## Environment Variables

The full template lives in `.env.example`. These are the most important settings.

| Variable | Purpose |
| --- | --- |
| `APP_ENV` | `development` or `production` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Admin login credentials |
| `JWT_SECRET` | Required in production; used for admin bearer tokens |
| `ENCRYPTION_SECRET` | Required in production; used for encrypted API key storage |
| `DATABASE_URL` | PostgreSQL in production; empty falls back to SQLite |
| `REDIS_URL` | Optional shared backend for rate limiting and coordination |
| `OPENAI_API_KEY` | OpenAI provider credential |
| `ANTHROPIC_API_KEY` | Anthropic provider credential |
| `LOCAL_LLM_URL` | Local model endpoint |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW` | Public API rate limiting |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `TUNNEL_PROVIDER` | `cloudflare`, `localtunnel`, or `ngrok` |
| `TUNNEL_API_ONLY` | Restrict public tunnel to gateway-safe routes |
| `TUNNEL_SHARED_SECRET` | Public tunnel token gate |
| `CLOUDFLARE_TUNNEL_TOKEN` | Named tunnel auth |
| `CLOUDFLARE_PUBLIC_BASE_URL` | Stable public URL |
| `LOCALTUNNEL_SUBDOMAIN` | Requested LocalTunnel subdomain |
| `NGROK_AUTHTOKEN` / `NGROK_STATIC_DOMAIN` | ngrok static tunnel settings |

## Production Notes

- `APP_ENV=production` should be paired with explicit `JWT_SECRET` and `ENCRYPTION_SECRET`
- prefer PostgreSQL over SQLite in production
- optionally enable Redis for cross-process coordination
- use the deployment assets under `deploy/`

## Local Model Setup

Install recommended Ollama models:

```powershell
.\scripts\setup_ollama.bat
```

The script checks for existing models and only pulls missing ones.

## Common Developer Commands

```powershell
python -m compileall app
pytest
arctic sys health --json
arctic bot list --json
arctic scraper test-parse https://example.com --mock --json
```

## Data and Logs During Development

- SQLite database: `data/chatbot_console.db`
- Bot artifacts: `data/bots/<bot_id>/`
- Startup logs: `logs/startup_*.log`
- App logs: `logs/app_YYYYMMDD.log`
- Background role logs: `logs/<role>.stdout.log` and `logs/<role>.stderr.log`

