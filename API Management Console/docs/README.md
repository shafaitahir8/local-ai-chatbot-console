# Project Documentation

This folder is the canonical documentation set for the Local AI Chatbot Deployment Console.

## Start Here

- [setup-and-development.md](setup-and-development.md): install, configure, run, and develop locally
- [architecture.md](architecture.md): system design, request flows, runtime roles, and service boundaries
- [project-structure.md](project-structure.md): repository layout and file-by-file orientation
- [api-reference.md](api-reference.md): HTTP routes, auth expectations, and public/admin surfaces
- [cli.md](cli.md): headless operations with the `arctic` CLI
- [data-model.md](data-model.md): database tables, filesystem layout, vector storage, and cache state
- [operations.md](operations.md): health probes, metrics, runtime supervision, deployment, and safe modes
- [troubleshooting.md](troubleshooting.md): startup, auth, tunnel, RAG, scraper, and CLI recovery

## System At A Glance

This project is a local-first chatbot control plane with these major parts:

- FastAPI backend in `app/`
- React/Vite admin frontend in `frontend/`
- SQLAlchemy models and persistence in `app/models/`
- LLM, RAG, scraper, tunnel, and supervision services in `app/services/`
- Typer/Rich CLI in `app/cli.py`
- Background role entrypoints in `app/runtime_supervisor.py`
- Windows-oriented startup automation in `start_console.bat` and `scripts/`
- Deployment assets for Docker, `systemd`, WinSW, and NSSM in `deploy/`

## Core Runtime Concepts

- Admin UI lives at `/admin`
- Public chat lives under `/api/bot/{bot_id}/...`
- Admin APIs live under `/api/admin/...`
- Bot CRUD lives under `/api/bots/...`
- Scraper APIs live under `/api/scraper/...`
- Embed widget lives under `/embed/{bot_id}`
- Health probes live at `/health/live`, `/health/ready`, `/health/fresh`, and `/health`
- Prometheus metrics live at `/metrics`

The runtime is intentionally split into supervised units:

- `api`
- `scheduler`
- `scraper-worker`
- `embedding-worker`
- `gateway-agent`
- `tunnel-agent`
- `ollama-sentinel`

## Existing Focused Guides

These older focused guides are still useful and are kept alongside the broader docs:

- [hardened_deployment.md](hardened_deployment.md)
- [static_tunnel_setup.md](static_tunnel_setup.md)

