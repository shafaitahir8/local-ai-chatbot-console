# Hardened Deployment

## Production invariants

- Set `APP_ENV=production`.
- Set `JWT_SECRET` and `ENCRYPTION_SECRET` explicitly. Production startup now fails if either is missing.
- Set `DATABASE_URL` to PostgreSQL. SQLite remains for local development only.
- Set `REDIS_URL` only when you need shared rate limiting across multiple API processes.

## Runtime roles

- API: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Scheduler: `python -m app.runtime_supervisor scheduler`
- Scraper worker: `python -m app.runtime_supervisor scraper-worker`
- Embedding worker: `python -m app.runtime_supervisor embedding-worker`
- Gateway agent: `python -m app.runtime_supervisor gateway-agent`
- Tunnel agent: `python -m app.runtime_supervisor tunnel-agent`
- Optional Ollama sentinel: `python -m app.runtime_supervisor ollama-sentinel`

## Health and metrics

- Liveness: `/health/live`
- Readiness: `/health/ready`
- Freshness: `/health/fresh`
- Aggregate report: `/health`
- Prometheus: `/metrics`

## Service managers

- Linux: use `deploy/systemd/chatbot-api.service` and `deploy/systemd/chatbot-role@.service`.
- Windows: use `deploy/winsw/*.xml` or `deploy/windows/install_nssm_services.ps1`.
- Containers: use `Dockerfile` and `deploy/docker-compose.hardened.yml`, with readiness-based health checks and `restart: on-failure:5`.
