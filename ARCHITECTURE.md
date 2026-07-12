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
| Lint / format | `ruff` (see [pyproject.toml](pyproject.toml)) |
| Static analysis | CodeQL (Python + Actions) |

Pinned runtime versions live in [requirements.txt](requirements.txt); [requirements-dev.txt](requirements-dev.txt) adds `httpx` (for FastAPI's `TestClient`), `pytest`, `pytest-cov`, and `ruff` on top via `-r requirements.txt`, and is what CI and local dev install. Python is pinned to **3.14** in both the Dockerfile and CI.

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
  state.py           — BotState singleton (pending_ops, pending_op_info, maintenance flags, last_known_status)

tests/
  conftest.py           — Sets required/test-only env vars (BOT_TOKEN, ALLOWED_CONTAINERS, DISCORD_GUILD_ID,
                          plus tmp paths for LOG_FILE/HISTORY_FILE so test runs don't write into the repo's
                          data/ directory), resets state/permissions cache per test
  test_config.py        — Config parsing tests
  test_docker_control.py — Docker SDK wrapper tests (logs, stats, control ops)
  test_permissions.py   — Permission store tests
  test_bot_commands.py  — Bot command handler tests (stop/restart/logs/stats/maintenance/history/etc.)
  test_api.py           — FastAPI /status endpoint tests
  test_logging.py       — RedactingFilter tests
  test_crash_alerting.py — Crash detection loop tests
  test_state.py         — BotState and command history tests

.github/
  dependabot.yml     — Weekly pip, github-actions, docker base image updates (grouped)
  workflows/
    tests-reusable.yml  — Reusable workflow: lint, test+coverage, Docker build+smoke test
    tests.yaml          — CI: runs on every branch push + PRs to main (calls tests-reusable)
    docker-publish.yml  — CD: builds + pushes the image on main, tags, and monthly (calls tests-reusable)
    codeql.yml          — Security scanning on push/PR to main + weekly
```

## Runtime model

- **Main thread:** discord.py's asyncio event loop running `bot.run()`.
- **API thread:** uvicorn serving FastAPI in a daemon `threading.Thread` started by `main()`. Using a daemon thread means it exits automatically when the main process ends, with no explicit shutdown needed. Shares the process (and therefore the module-level `_docker_client`, permissions cache, and root logger) with the bot.
- **Docker executor:** a `ThreadPoolExecutor(max_workers=DOCKER_MAX_WORKERS)` in [src/docker_control.py](src/docker_control.py). All Docker SDK calls are blocking and must be submitted via `run_blocking()` so they don't stall the event loop.

A single lazily-constructed `_docker_client` is reused for the lifetime of the process.

## Key conventions

### Security

- **Container names** are validated against `^[a-zA-Z0-9_.-]+$` and `ALLOWED_CONTAINERS` before any Docker call. Validation happens inside `docker_control` — not just at the command layer — so the allowlist survives mistakes in the caller.
- **Announcement messages** pass through `_sanitize()` in [src/docker_control.py](src/docker_control.py), which whitelists `[a-zA-Z0-9 .,!?:_-]`, strips leading hyphens (to prevent flag injection into downstream commands like `rcon-cli`), and truncates to 100 chars before any `exec_run`. Do **not** add shell metacharacters, quotes, or `$` to this whitelist without revisiting the injection surface.
- **Ping control:** The bot is constructed with `AllowedMentions.none()` so no handler can accidentally ping @everyone or arbitrary users. `send_announcement` re-enables mentions for exactly `ANNOUNCE_ROLE_ID` (via `AllowedMentions(roles=[Object(id=ANNOUNCE_ROLE_ID)])`) when it's set, and `AllowedMentions.none()` otherwise — never a blanket `roles=True`, since announcement content can include user-supplied text (e.g. a `!maintenance` reason) that must not be able to ping an arbitrary role.
- **Status API token** is compared with `secrets.compare_digest` to prevent timing attacks. The `/healthz` endpoint is intentionally unauthenticated — it is used by the Docker healthcheck and external uptime monitors.
- **Log redaction** is implemented at the handler level (`_RedactingFilter` in [src/logging_config.py](src/logging_config.py)) so every handler strips `BOT_TOKEN` and `STATUS_TOKEN` — not just the root logger.
- **Guild lock** (`DISCORD_GUILD_ID`) and **channel lock** (`ALLOWED_CHANNEL_IDS`) are enforced by `_origin_allowed()` in [src/bot.py](src/bot.py). The global `@bot.check` (`check_guild`) calls it and raises `SilentCheckFailure` — a `commands.CheckFailure` subclass `on_command_error` recognizes and silently ignores — for DMs (no guild), foreign-guild, and disallowed-channel commands, so none of these origins get any response. A genuine role/permission denial in the home guild still gets the "you do not have permission" message. `@bot.check` predicates only run for *registered* commands, though — `CommandNotFound` (e.g. a typo'd `!perm` command) fires before any check runs, so `on_command_error`'s `CommandNotFound` branch calls `_origin_allowed()` itself before touching `ctx.author.guild_permissions` (which doesn't exist on a DM's `discord.User`). This is why the check is factored into a standalone function rather than inlined in `check_guild` — both call sites need it and must not drift apart.
- **Admin bypass:** Discord's `Administrator` permission always short-circuits `has_permission` checks.

### Docker operations

- Every public function in [src/docker_control.py](src/docker_control.py) calls `_check_allowed(name)` first — it returns a typed `Result` rather than raising on expected failure modes.
- `announce_in_game` has two execution paths based on whether `CONTAINER_MESSAGE_CMD` contains `{message}`: the placeholder path uses `/bin/sh -c` (for templates like `screen -S foo -X stuff "say {message}\015"`), the no-placeholder path uses argv form (safer, preferred when possible). The placeholder substitution uses a literal `.replace("{message}", safe_msg)`, not `str.format()` — a template with other braces (e.g. Minecraft's `tellraw @a {"text":"{message}"}`) would make `.format()` raise.

### Pending op deduplication

- `state.pending_ops: dict[str, Future | Task]` in [src/state.py](src/state.py) tracks in-flight `stop`/`restart` countdowns per container.
- `state.pending_op_info: dict[str, dict]` mirrors `pending_ops`, storing `{"action": str, "scheduled_at": datetime}` so `!status` can compute time remaining and display the pending operation type. Both are set together, before the countdown announcement is sent, so `!status` reports accurately even mid-announcement.
- A `Future` placeholder is inserted **immediately after the `has_pending_op` dedup check**, before any further `await` in `_delayed_container_op` — so two rapid `!stop` commands can't both pass that check while interleaved at a later await. (F2: the status pre-check and `history.record` awaits below were briefly ordered *before* the placeholder insert, which reopened this exact window; both now live inside the same `try` as the announcement awaits, after the placeholder is in place.) That `try`/`except` removes the placeholder on any exception (otherwise a failed pre-check, `history.record`, or announcement would permanently block future `!stop`/`!restart` on that container), and a post-announcement identity check (`pending_ops.get(target) is placeholder and not placeholder.cancelled()`) detects whether a concurrent `!cancel` / `!stop now` / `!maintenance on` already cancelled it, in which case the countdown task is never scheduled.
- `!stop now` / `!restart now` call `state.cancel_pending()` to abort a scheduled delay before executing immediately. `cancel_pending` clears both `pending_ops` and `pending_op_info`.
- `!cancel` calls `state.cancel_all_pending()` directly (the same helper maintenance mode uses) to abort every pending countdown without stopping/restarting anything or entering maintenance mode.
- `!stop` (not `!restart` — Docker's restart legitimately starts a stopped container) checks the container's current status, inside the placeholder's `try` block, before announcing anything; if it isn't `running`, the handler replies immediately (and cleans up the placeholder via the same identity check) instead of announcing a countdown for an operation that's already a no-op.
- After any successful `start`/`stop`/`restart`, the handler re-seeds `state.last_known_status` with a fresh `container_status` call so `crash_check_loop`'s next poll doesn't mistake the bot's own action for an unexpected crash.

### Permissions

- Backed by a JSON file at `PERMISSIONS_FILE` (default `data/permissions.json`).
- Created on first run with defaults from `DEFAULT_ALLOWED_ROLES`; permission bits are 0o600 on initial create.
- New actions added to `ALL_ACTIONS` are auto-backfilled into existing files — upgrades don't need manual JSON edits.
- A corrupted JSON file is removed and re-initialized from defaults rather than crashing the bot.
- Cache in `_load()` uses the file's `mtime` as a coherence key — cheap lookups when the file hasn't changed.

### Maintenance mode

- Toggled via `!maintenance on/off`. `BotState.is_maintenance_active()` just returns `state.maintenance_mode` — only the six container-mutating commands (`start`, `stop`, `restart`, `announce`, `logs`, `stats`) call it at all. `guide`, `history`, `perm*`, and `maintenance` itself never call it, so they remain available during maintenance mode by construction rather than through an exemption list.
- `maintenance_cmd` intentionally has no `@commands.cooldown`: an admin must be able to toggle it again immediately during an active incident.
- Enabling maintenance calls `state.cancel_all_pending()`, which cancels and removes all in-flight stop/restart countdowns. The cancelled container names are included in the confirmation message.

### Crash detection loop

- [src/bot.py](src/bot.py) `crash_check_loop` polls every `CRASH_CHECK_INTERVAL` seconds (default 30s).
- Initial statuses are seeded in `before_loop` to avoid false alerts on startup.
- Alerts land in `CRASH_ALERT_CHANNEL_ID` or fall back to `ANNOUNCE_CHANNEL_ID`. If neither is set, alerts are logged but not broadcast.

## CI / CD

- [tests-reusable.yml](.github/workflows/tests-reusable.yml) — the canonical test job: sets up Python 3.14, installs `requirements-dev.txt`, runs ruff (lint + format check) + pytest with coverage, then — when the `docker-smoke` input is true (the default) — a Docker build and startup smoke test (which installs only `requirements.txt`, matching what ships). Called by both workflows below via `workflow_call` so there is a single source of truth.
- [tests.yaml](.github/workflows/tests.yaml) — triggers on every branch push, on PRs targeting `main`, and on manual `workflow_dispatch`; calls `tests-reusable`. Branch pushes skip the Docker build/smoke steps (`docker-smoke: false`) for fast feedback; PRs to `main`, pushes to `main`, and manual dispatches run the full job including the image smoke test. A branch with an open PR fires both a `push` and a `pull_request` run for the same commit, but both resolve to the same concurrency group (`tests-<branch>`), so the newer run cancels the older instead of running twice.
- **Merge gate:** the repo's `Default_protections` ruleset on `main` requires PRs and requires the `test / test` status check to pass — so the full job (Docker smoke test included, since it's a `pull_request` run) must be green before a PR can merge. To pre-verify the image on a branch before opening a PR: `gh workflow run tests.yaml --ref <branch>`.
- [docker-publish.yml](.github/workflows/docker-publish.yml) — triggers on merge to main, version tags, and monthly (to pick up base image patches), plus manual `workflow_dispatch`. Calls `tests-reusable` first, then builds and pushes multi-arch (amd64/arm64) images to Docker Hub and prunes old tags to the last 5. The prune step protects every tag `build-and-push` just pushed (passed via a job `outputs.tags`) from deletion regardless of what Docker Hub's tags API reports for `last_updated` — that API can lag a same-run push long enough for the brand-new tag to sort as "oldest" and get pruned seconds after being published; this happened in production once before the protection was added.
- [codeql.yml](.github/workflows/codeql.yml) — Python + Actions static analysis on PRs and weekly.
- **Dependabot** — weekly bumps for pip, GitHub Actions, and the Docker base image, grouped where it makes sense.
