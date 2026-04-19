# CLI Guide

The project exposes a Typer/Rich CLI as `arctic`.

Fallback entrypoint if the console script is not installed:

```powershell
python -m app.cli --help
```

Installed entrypoint:

```powershell
arctic --help
```

## Command Tree

```text
arctic
├── bot
│   ├── create
│   ├── list
│   ├── delete
│   ├── update-config
│   └── chat
├── scraper
│   ├── run
│   ├── status
│   ├── clear-queue
│   └── test-parse
├── db
│   ├── migrate
│   ├── reset-hard
│   ├── query-logs
│   └── prune-orphans
└── sys
    ├── health
    ├── restart-tunnel
    └── view-metrics
```

## Global CLI Principles

- Uses the same service layer as the FastAPI app
- Supports `--json` for script-friendly output
- Supports `--mock` on relevant commands for credit-free testing
- Uses Rich tables and panels for human-readable output
- Supports explicit idempotency keys for repeatable chat testing

## Bot Commands

### Create a bot

```powershell
arctic bot create "My Bot" --template openai_template --provider openai --model gpt-4.1-mini
```

Useful flags:

- `--template`
- `--provider`
- `--api-key`
- `--system-prompt`
- `--temperature`
- `--max-tokens`
- `--memory/--no-memory`
- `--analytics/--no-analytics`
- `--active/--inactive`
- `--allow-duplicate`
- `--json`

### List bots

```powershell
arctic bot list
arctic bot list --json
```

### Update bot configuration

```powershell
arctic bot update-config <bot_id> --set temperature=0.2 --set memory_enabled=false
```

### Delete a bot

```powershell
arctic bot delete <bot_id> --yes
```

### Interactive or one-shot chat

One-shot:

```powershell
arctic bot chat <bot_id> --message "Hello" --json
```

Mock smoke:

```powershell
arctic bot chat <bot_id> --message "smoke" --mock --provider local --json
```

Stable idempotent replay:

```powershell
arctic bot chat <bot_id> --message "smoke" --mock --idempotency-key smoke-001 --json
```

Important flags:

- `--message`
- `--session-id`
- `--no-rag`
- `--no-memory`
- `--provider`
- `--model`
- `--idempotency-key`
- `--mock`
- `--json`

When an explicit idempotency key is supplied, the CLI now reuses a stable session id so repeated one-shot runs resolve to the same durable `chat_request`.

## Scraper Commands

### Queue a scrape job

```powershell
arctic scraper run https://example.com --depth 2 --bot-id <bot_id>
```

### Inspect a job

```powershell
arctic scraper status <job_id>
arctic scraper status <job_id> --json
```

### Clear queued jobs

```powershell
arctic scraper clear-queue --yes
arctic scraper clear-queue --include-running --yes
```

### Test parsing without queuing

```powershell
arctic scraper test-parse https://example.com --mock --json
```

## Database Commands

### Initialize or verify schema

```powershell
arctic db migrate
```

### Hard reset

```powershell
arctic db reset-hard --yes
```

### Query message logs

```powershell
arctic db query-logs --limit 25
arctic db query-logs --bot-id <bot_id> --search error --json
```

### Prune orphaned state

```powershell
arctic db prune-orphans --yes
```

## System Commands

### Health

```powershell
arctic sys health
arctic sys health --json
```

### Restart tunnel

```powershell
arctic sys restart-tunnel
arctic sys restart-tunnel --json
```

### View metrics

```powershell
arctic sys view-metrics
arctic sys view-metrics --raw
arctic sys view-metrics --json
```

## JSON Output

Every operational command used in automation should prefer `--json`.

Examples:

```powershell
arctic sys health --json
arctic bot list --json
arctic scraper test-parse https://example.com --mock --json
```

## Mock Mode

`--mock` is designed for CLI validation without hitting external model providers.

Use it for:

- chatbot smoke tests
- route and auth verification
- CI or local scripting
- quick degraded-state checks

