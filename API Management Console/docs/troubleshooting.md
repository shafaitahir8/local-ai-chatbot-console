# Troubleshooting

## Console Loads `/admin` But Then Redirects to a Missing Login Page

Expected behavior now:

- `/login` should redirect to `/admin/login`

If login navigation still looks broken:

- hard-refresh the admin page to drop stale frontend assets
- confirm `/admin` returns the SPA HTML
- confirm `/login` returns a redirect instead of `404`

## Launcher Says Background Roles Failed To Start

Check:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\manage_runtime_roles.ps1 status
arctic sys health --json
```

If roles are not up:

- inspect `logs/<role>.stderr.log`
- inspect `logs/startup_*.log`
- run `manage_runtime_roles.ps1 start` manually

## Health Is Degraded

Use:

```powershell
arctic sys health --json
```

Look for:

- stale or missing `component_leases`
- active safe modes
- missing tunnel or gateway metadata
- scraper backlog

## Public Tunnel Is Offline

Check:

- `TUNNEL_PROVIDER`
- `TUNNEL_SHARED_SECRET`
- provider-specific variables in `.env`
- tunnel-agent lease state in `arctic sys health --json`

Try:

```powershell
arctic sys restart-tunnel --json
```

For static tunnel guidance, see [static_tunnel_setup.md](static_tunnel_setup.md).

## Ollama Issues

Symptoms:

- launcher warns about missing models
- local bots fail or degrade
- `ollama-sentinel` is unhealthy

Actions:

```powershell
ollama list
.\scripts\setup_ollama.bat
```

Also confirm the local endpoint in `.env` and the actual Ollama server port align with your setup.

## RAG Degraded or Missing

Symptoms:

- chat returns `degraded_components=["rag"]`
- `data_freshness` becomes `stale`
- embedding worker is unhealthy

Actions:

- check `index_manifests` and embedding-worker health
- inspect bot embeddings under `data/bots/<bot_id>/embeddings/`
- trigger `POST /api/bots/{bot_id}/rebuild-embeddings` or use the admin UI

## Scraper Jobs Stuck

Symptoms:

- jobs remain `pending` or `running`
- safe mode contains `SCRAPER_PAUSED`

Actions:

```powershell
arctic sys health --json
arctic scraper status <job_id> --json
arctic scraper clear-queue --yes
```

Then inspect:

- `logs/scraper-worker.stderr.log`
- `task_runs.checkpoint_json`
- `scraper_jobs.heartbeat_at`

## CLI Chat Fails

Useful smoke command:

```powershell
arctic bot chat <bot_id> --message "smoke" --mock --provider local --json
```

If you are testing replay behavior, use an explicit idempotency key:

```powershell
arctic bot chat <bot_id> --message "smoke" --mock --idempotency-key smoke-001 --json
```

If the second identical run does not return `idempotent_hit=true`, check whether the session id or message changed between runs.

## Database Backend Problems

Development:

- SQLite lives at `data/chatbot_console.db`

Production:

- set `DATABASE_URL`
- verify `psycopg` and `asyncpg` are installed

Quick schema check:

```powershell
arctic db migrate --json
```

## Useful Files For Diagnosis

- `logs/startup_*.log`
- `logs/app_YYYYMMDD.log`
- `logs/<role>.stderr.log`
- `data/runtime_roles.json`
- `data/chatbot_console.db`

