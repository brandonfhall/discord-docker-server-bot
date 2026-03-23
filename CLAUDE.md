# CLAUDE.md — Project Guide for AI Assistants

## Project Overview

A Dockerized Discord bot that controls one or more Docker containers (e.g., a game server) via `!` prefix commands. Users with the right Discord roles can start, stop, restart, and send announcements to containers. A FastAPI HTTP endpoint exposes container status and recent logs.

## Stack

| Layer | Technology |
|---|---|
| Bot framework | `discord.py` (prefix commands) |
| HTTP API | `fastapi` + `uvicorn` |
| Docker control | `docker` SDK |
| Config | `python-dotenv` + env vars |
| Tests | `unittest` (stdlib) + `pytest` runner |

## Directory Structure

```
src/
  config.py          — All env var parsing and validation; fails fast on missing required vars
  docker_control.py  — Docker SDK wrappers; input validation + sanitization lives here
  permissions.py     — JSON-backed role permission store (data/permissions.json)
  bot.py             — Discord bot, FastAPI app, command handlers, logging setup

tests/
  conftest.py        — Sets BOT_TOKEN + ALLOWED_CONTAINERS env vars before any src import
  test_unit.py       — All tests (unittest classes, run with pytest)

.github/
  dependabot.yml     — Weekly updates for pip, docker, github-actions; grouped where sensible
  workflows/
    tests.yaml       — CI: runs on every branch push and PR to main, uses pinned requirements
    docker-publish.yml — CD: builds + pushes Docker image on push to main or tags
    codeql.yml       — Security scanning on push/PR to main and weekly
```

## Running Locally

```bash
cp .env.example .env   # fill in BOT_TOKEN and ALLOWED_CONTAINERS at minimum
docker compose up -d --build
```

For local dev with live code reloading:
```bash
docker compose -f docker-compose.dev.yml up --build
```

## Running Tests

```bash
export PYTHONPATH=.
pytest -v tests/
```

No Docker daemon is required — all Docker calls are mocked.

## CI / CD

- **`tests.yaml`** — Runs on every branch push and PR to main with pinned `requirements.txt`. This is the pre-merge gate.
- **`docker-publish.yml`** — Runs tests (pinned), then builds and pushes the image on merge to main or a version tag. Also runs on a monthly schedule to pick up base image security patches.
- **`codeql.yml`** — Static security analysis, runs on PRs and weekly.
- **Dependabot** — Opens PRs for direct dep bumps (pip, GitHub Actions, Docker base image). Each Dependabot PR is validated by `tests.yaml`.

## Key Conventions

### Security

- Container names are validated against a strict allowlist regex (`^[a-zA-Z0-9_.-]+$`) before any Docker call.
- Announcement messages are sanitized (whitelist only, 100-char limit) before being passed to `exec_run`.
- The `_RedactingFilter` strips `BOT_TOKEN` and `STATUS_TOKEN` from all log output at the handler level.
- The bot can be locked to a single Discord guild via `DISCORD_GUILD_ID`.

### Docker operations

- All Docker SDK calls are blocking; they run in a `ThreadPoolExecutor` via `run_blocking()` to avoid blocking the asyncio event loop.
- A single lazy `_docker_client` instance is reused across calls.

### Pending ops deduplication

- `_pending_ops` (dict in `bot.py`) tracks in-flight `stop`/`restart` tasks per container. Duplicate commands while a task is pending are rejected with a user-facing message. A `Future` placeholder is registered *before* any `await` to prevent race conditions.

### Permissions

- Stored in `data/permissions.json` (path configurable via `PERMISSIONS_FILE`).
- Created on first run with defaults from `DEFAULT_ALLOWED_ROLES`.
- Corrupted file is automatically re-initialized with defaults.
- Discord `Administrator` permission bypasses the role check entirely.

## Environment Variables

See `.env.example` for the full list with descriptions. Required: `BOT_TOKEN`, `ALLOWED_CONTAINERS`.

## Adding a New Command

1. Add the handler in `src/bot.py` using `@bot.command()` + `@has_permission("<action>")`.
2. If it's a permissioned action, add the action name to `VALID_ACTIONS` in `bot.py`.
3. Add corresponding unit tests to `tests/test_unit.py`.
4. Update the Discord Commands section in `README.md` and `DOCKERHUB.md`.
