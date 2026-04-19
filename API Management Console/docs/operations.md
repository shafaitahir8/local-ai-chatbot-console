# Operations

## Runtime Entry Points

### Main local launcher

```powershell
.\start_console.bat
```

### Manual web app

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Manual roles

```powershell
python -m app.runtime_supervisor scheduler
python -m app.runtime_supervisor scraper-worker
python -m app.runtime_supervisor embedding-worker
python -m app.runtime_supervisor gateway-agent
python -m app.runtime_supervisor tunnel-agent
python -m app.runtime_supervisor ollama-sentinel
```

### Windows role helper

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\manage_runtime_roles.ps1 start
powershell -ExecutionPolicy Bypass -File .\scripts\manage_runtime_roles.ps1 status
powershell -ExecutionPolicy Bypass -File .\scripts\manage_runtime_roles.ps1 stop
```

## Health Endpoints

- `/health/live`: use for process liveness
- `/health/ready`: use for dependency readiness
- `/health/fresh`: use for freshness and lease-based staleness checks
- `/health`: aggregate compatibility endpoint

The health report includes:

- component states
- dependency metadata
- safe modes
- public base URL
- gateway state

## Metrics

Prometheus metrics are exposed at `/metrics`.

Important metrics currently emitted:

- `heartbeat_age_seconds`
- `task_restart_total`
- `provider_timeout_total`
- `provider_circuit_open`
- `rag_bypass_total`
- `index_version_age`
- `scraper_checkpoint_lag`
- `db_write_fail_total`

CLI inspection:

```powershell
arctic sys view-metrics
arctic sys view-metrics --raw
arctic sys view-metrics --json
```

## Logs

### Startup logs

- `logs/startup_*.log`

Used by `start_console.bat` to capture launcher diagnostics.

### Application log

- `logs/app_YYYYMMDD.log`

JSON structured log from the FastAPI process.

### Background role logs

- `logs/scheduler.stdout.log`
- `logs/scheduler.stderr.log`
- same pattern for all other roles

## Safe Modes

Safe modes are computed from lease and dependency state.

- `TUNNEL_OFFLINE_LOCAL_ONLY`
- `SCRAPER_PAUSED`
- `RAG_BYPASS_EXPLICIT`
- `PROVIDER_OPEN_CIRCUIT`
- `DB_READ_ONLY`

Operational rule of thumb:

- if a safe mode is active, the system should degrade explicitly instead of failing silently

## Gateway and Tunnel

The public surface is split:

- local admin/API origin
- gateway-agent proxy for public-safe routes
- tunnel-agent for actual external exposure

Useful commands:

```powershell
arctic sys health --json
arctic sys restart-tunnel --json
```

## Deployment Assets

### Docker

- `Dockerfile`
- `deploy/docker-compose.hardened.yml`

### Linux

- `deploy/systemd/chatbot-api.service`
- `deploy/systemd/chatbot-role@.service`

### Windows services

- `deploy/winsw/chatbot-api.xml`
- `deploy/winsw/chatbot-role.xml.template`
- `deploy/windows/install_nssm_services.ps1`

### Monitoring

- `deploy/prometheus/alert_rules.yml`

## Recommended Operational Checks

At startup:

- open `/health/ready`
- confirm all expected role leases are fresh
- verify `/metrics` is reachable
- verify tunnel and gateway metadata in `sys health`

After a deploy or config change:

- `arctic sys health --json`
- `arctic bot list --json`
- `arctic scraper test-parse https://example.com --mock --json`
- `arctic bot chat <bot_id> --message "smoke" --mock --json`

## Related Guides

- [hardened_deployment.md](hardened_deployment.md)
- [static_tunnel_setup.md](static_tunnel_setup.md)

