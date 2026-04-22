# Architecture

Internal architecture and conventions for the Discord Docker Controller Bot. For user-facing docs see [README.md](README.md); for Claude-specific contributor notes see [CLAUDE.md](CLAUDE.md).

## Overview

A single-process Python service that bridges Discord commands to the Docker daemon. Users with the right Discord roles can start, stop, restart, announce to, and inspect one or more allow-listed Docker containers. A FastAPI HTTP endpoint exposes container status, current permissions, and recent log lines for monitoring.

## Stack

| Layer | Technology |
|---|---|
| Bot framework | `discord.py` (prefix `!` commands) |
| HTTP API | `fastapi` + `uvicorn` |
| Docker control | `docker` SDK for Python |
| Config | `python-dotenv` + env vars |
| Tests | `unittest` (stdlib) run via `pytest` |
| Lint | `flake8` (see [.flake8](.flake8)) |
| Static analysis | CodeQL (Python + Actions) |

Pinned versions live in [requirements.txt](requirements.txt); Python is pinned to **3.11** in both the Dockerfile and CI.

## Directory layout

```
src/
  bot.py             — Discord bot, command handlers, crash loop, main()
  api.py             — FastAPI app (/ redirect, /status) with token auth
  config.py          — Env var parsing + fail-fast validation
  docker_control.py  — Docker SDK wrappers; container-name allowlist + message sanitizer
  permissions.py     — JSON-backed role permission store with mtime cache
  history.py         — Thread-safe append-only command history/audit log
  logging_config.py  — Root logger setup + RedactingFilter
  state.py           — BotState singleton (pending_ops, maintenance flags, last_known_status)

tests/
  conftest.py        — Sets BOT_TOKEN + ALLOWED_CONTAINERS, resets state/permissions cache per test
  test_unit.py       — All unit tests (unittest classes executed by pytest)

.github/
  dependabot.yml     — Weekly pip, github-actions, docker base image updates (grouped)
  workflows/
    tests.yaml          — CI: runs on every branch push + PRs to main
    docker-publish.yml  — CD: builds + pushes the image on main, tags, and monthly
    codeql.yml          — Security scanning on push/PR to main + weekly
```

## Runtime model

- **Main thread:** discord.py's asyncio event loop running `bot.run()`.
- **API thread:** uvicorn serving FastAPI in a worker launched by `main()`. Shares the process (and therefore the module-level `_docker_client`, permissions cache, and root logger) with the bot.
- **Docker executor:** a `ThreadPoolExecutor(max_workers=DOCKER_MAX_WORKERS)` in [src/docker_control.py](src/docker_control.py). All Docker SDK calls are blocking and must be submitted via `run_blocking()` so they don't stall the event loop.

A single lazily-constructed `_docker_client` is reused for the lifetime of the process.

## Key conventions

### Security

- **Container names** are validated against `^[a-zA-Z0-9_.-]+$` and `ALLOWED_CONTAINERS` before any Docker call. Validation happens inside `docker_control` — not just at the command layer — so the allowlist survives mistakes in the caller.
- **Announcement messages** pass through `_sanitize()` in [src/docker_control.py](src/docker_control.py), which whitelists `[a-zA-Z0-9 .,!?:_-]` and truncates to 100 chars before any `exec_run`. Do **not** add shell metacharacters, quotes, or `$` to this whitelist without revisiting the injection surface.
- **Log redaction** is implemented at the handler level (`_RedactingFilter` in [src/logging_config.py](src/logging_config.py)) so every handler strips `BOT_TOKEN` and `STATUS_TOKEN` — not just the root logger.
- **Guild lock** (`DISCORD_GUILD_ID`) and **channel lock** (`ALLOWED_CHANNEL_IDS`) are enforced in a global `@bot.check`. Disallowed-channel rejections are silently ignored in `on_command_error` to avoid leaking the bot's presence.
- **Admin bypass:** Discord's `Administrator` permission always short-circuits `has_permission` checks.

### Docker operations

- Every public function in [src/docker_control.py](src/docker_control.py) calls `_check_allowed(name)` first — it returns a typed `Result` rather than raising on expected failure modes.
- `announce_in_game` has two execution paths based on whether `CONTAINER_MESSAGE_CMD` contains `{message}`: the placeholder path uses `/bin/sh -c` (for templates like `screen -S foo -X stuff "say {message}\015"`), the no-placeholder path uses argv form (safer, preferred when possible).

### Pending op deduplication

- `state.pending_ops: dict[str, Future | Task]` in [src/state.py](src/state.py) tracks in-flight `stop`/`restart` countdowns per container.
- A `Future` placeholder is inserted **before** any `await` in `_delayed_container_op` so two rapid `!stop` commands can't both pass the `has_pending_op` check.
- `!stop now` / `!restart now` call `state.cancel_pending()` to abort a scheduled delay before executing immediately.

### Permissions

- Backed by a JSON file at `PERMISSIONS_FILE` (default `data/permissions.json`).
- Created on first run with defaults from `DEFAULT_ALLOWED_ROLES`; permission bits are 0o600 on initial create.
- New actions added to `ALL_ACTIONS` are auto-backfilled into existing files — upgrades don't need manual JSON edits.
- A corrupted JSON file is removed and re-initialized from defaults rather than crashing the bot.
- Cache in `_load()` uses the file's `mtime` as a coherence key — cheap lookups when the file hasn't changed.

### Maintenance mode

- Toggled via `!maintenance on/off`. Blocks all container commands except `maintenance`, `perm*`, `guide`, and `history` (see `BotState.is_maintenance_active`).
- **Caveat:** maintenance does **not** cancel already-scheduled countdowns; an in-flight `!stop` fires regardless.

### Crash detection loop

- [src/bot.py](src/bot.py) `crash_check_loop` polls every `CRASH_CHECK_INTERVAL` seconds (default 30s).
- Initial statuses are seeded in `before_loop` to avoid false alerts on startup.
- Alerts land in `CRASH_ALERT_CHANNEL_ID` or fall back to `ANNOUNCE_CHANNEL_ID`. If neither is set, alerts are logged but not broadcast.

## CI / CD

- [tests.yaml](.github/workflows/tests.yaml) — runs on every branch push and PR to main. Installs pinned `requirements.txt`, runs flake8 + pytest + coverage, then does a Docker build and startup smoke test. This is the pre-merge gate.
- [docker-publish.yml](.github/workflows/docker-publish.yml) — on merge to main, on version tags, and monthly (to pick up base image patches). Re-runs tests, then builds+pushes multi-arch (amd64/arm64) images to Docker Hub and prunes old tags to the last 5.
- [codeql.yml](.github/workflows/codeql.yml) — Python + Actions static analysis on PRs and weekly.
- **Dependabot** — weekly bumps for pip, GitHub Actions, and the Docker base image, grouped where it makes sense.
