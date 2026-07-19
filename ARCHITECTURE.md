# Architecture

Internal architecture and conventions for the Discord Docker Controller Bot. For user-facing docs see [README.md](README.md); for Claude-specific contributor notes see [CLAUDE.md](CLAUDE.md).

## Overview

A single-process Python service that bridges Discord commands to the Docker daemon. Users with the right Discord roles can start, stop, restart, announce to, and inspect one or more allow-listed Docker containers. A FastAPI HTTP endpoint exposes container status, current permissions, and recent log lines for monitoring.

## Stack

| Layer | Technology |
|---|---|
| Bot framework | `discord.py` — hybrid commands: `!` prefix + `@Bot` mention + `/` slash |
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
  bot.py             — Discord bot, hybrid command handlers, slash-command sync (setup_hook), crash loop, main()
  api.py             — FastAPI app (/ redirect, /status) with token auth
  config.py          — Env var parsing + fail-fast validation
  docker_control.py  — Docker SDK wrappers; container-name allowlist + message sanitizer
  permissions.py     — JSON-backed role permission store with mtime cache
  history.py         — Thread-safe append-only command history/audit log
  atomic_io.py       — Atomic, fsync'd JSON write helper shared by permissions.py, history.py, and state.py
  logging_config.py  — Root logger setup + RedactingFilter
  state.py           — BotState singleton (pending_ops, pending_op_info, maintenance flags + persistence, last_known_status)

tests/
  conftest.py           — Sets required/test-only env vars (BOT_TOKEN, ALLOWED_CONTAINERS, DISCORD_GUILD_ID,
                          plus tmp paths for LOG_FILE/HISTORY_FILE/PERMISSIONS_FILE/MAINTENANCE_FILE so test
                          runs don't write into the repo's data/ directory), resets state/permissions cache per test
  test_config.py        — Config parsing tests
  test_docker_control.py — Docker SDK wrapper tests (logs, stats, control ops)
  test_permissions.py   — Permission store tests
  test_bot_commands.py  — Bot command handler tests (stop/restart/logs/stats/maintenance/history/etc.)
  test_api.py           — FastAPI /status endpoint tests
  test_logging.py       — RedactingFilter tests
  test_crash_alerting.py — Crash detection loop tests
  test_state.py         — BotState, command history, and maintenance-persistence tests

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
- **Guild lock** (`DISCORD_GUILD_ID`) and **channel lock** (`ALLOWED_CHANNEL_IDS`) are enforced by `_origin_allowed()` in [src/bot.py](src/bot.py). The global `@bot.check` (`check_guild`) calls it and raises `SilentCheckFailure` — a `commands.CheckFailure` subclass `on_command_error` recognizes and silently ignores **on the text path** — for DMs (no guild), foreign-guild, and disallowed-channel commands, so none of these origins get any response. A genuine role/permission denial in the home guild still gets the "you do not have permission" message. `@bot.check` predicates only run for *registered* commands, though — `CommandNotFound` (e.g. a typo'd `!perm` command) fires before any check runs, so `on_command_error`'s `CommandNotFound` branch calls `_origin_allowed()` itself before touching `ctx.author.guild_permissions` (which doesn't exist on a DM's `discord.User`). It uses `ctx.invoked_with` (the prefix-stripped command name) rather than interpolating `bot.command_prefix`, which is now the `when_mentioned_or("!")` **callable** (a leftover f-string interpolation of it would never match). This is why the check is factored into a standalone function rather than inlined in `check_guild` — both call sites need it and must not drift apart.
  - **Slash exception to the "silent" rule:** an *unacknowledged* slash interaction makes Discord show the user "This interaction failed", which is a worse presence signal than a quiet refusal. So when `SilentCheckFailure` fires and `ctx.interaction is not None` (a `/` invocation), `on_command_error` acknowledges with an **ephemeral** "This command isn't available here." instead of staying silent. This is not a leak: slash commands are synced guild-wide (see command surface below), so they are already visible in a disallowed *channel* of the home guild. The text path stays fully silent as before.
- **Admin bypass:** Discord's `Administrator` permission always short-circuits `has_permission` checks.

### Command surface (text + mention + slash)

Every command is a **hybrid command** (`@bot.hybrid_command` / `@bot.hybrid_group`), so one callback serves all three invocation styles from a single implementation:

- **`!` prefix** and **`@Bot` mention** — both are text commands. The bot's `command_prefix` is `commands.when_mentioned_or("!")`, so a mention is just another prefix; the same parser, `Context`, checks, and cooldowns apply. (`help` stays text-only — it's discord.py's built-in, not a hybrid command.)
- **`/` slash** — registered as application commands in `bot.tree` and published by `bot.setup_hook` (`_setup_hook` in [src/bot.py](src/bot.py)). Sync happens **once** in `setup_hook` (not `on_ready`, which can re-fire on reconnect and risks sync rate limits). A guild-locked bot (`DISCORD_GUILD_ID` set) does `copy_global_to(guild)` + `sync(guild=…)` — **instant** propagation; the `ALLOW_ANY_GUILD` path does a global `sync()`, which can take ~1h and makes commands appear in every server the bot is in. A sync failure is caught and logged, never fatal — the text/mention paths work without it.

Three constraints the slash path imposes on handler code:

- **No `*args`.** Slash commands need a declared parameter schema, so `stop`/`restart` expose explicit `container: str = None, now: str = None` params and **reconstruct the free-form token tuple** that `_delayed_container_op` parses. This preserves every text ordering (`!stop`, `!stop now`, `!stop <c>`, `!stop <c> now`, `!stop now <c>`) while giving slash users labeled fields. `now` is deliberately a **string, not a bool**: a bool param would make discord.py's converter reject the literal token `now` on the text path (e.g. `!stop server1 now`), breaking backward compatibility.
- **3-second interaction deadline.** A slash command must be acknowledged within 3s. Handlers that do a blocking Docker round trip (via `run_blocking`) before their first reply call `await _defer(ctx)` first. `_defer` is a no-op for text commands (no such deadline) and when the interaction is already acked.
- **`ctx.message` is `None` for slash.** Anything reading `ctx.message.*` must guard it — `on_command` logs `ctx.message.content if ctx.message else f"/{ctx.command} (slash)"`. (The `CommandNotFound` branch's old `ctx.message.content` read only ever runs on the text path, and now uses `ctx.invoked_with` anyway.)

Slash parameter hints come from `@app_commands.describe(...)` on each command — the main UX payoff of the slash surface. Adding `applications.commands` to the bot's OAuth invite scope is required for `/` commands to appear (see [README.md](README.md)).

### Docker operations

- Every public function in [src/docker_control.py](src/docker_control.py) calls `_check_allowed(name)` first — it returns a typed `Result` rather than raising on expected failure modes.
- `announce_in_game` has two execution paths based on whether `CONTAINER_MESSAGE_CMD` contains `{message}`: the placeholder path uses `/bin/sh -c` (for templates like `screen -S foo -X stuff "say {message}\015"`), the no-placeholder path uses argv form (safer, preferred when possible). The placeholder substitution uses a literal `.replace("{message}", safe_msg)`, not `str.format()` — a template with other braces (e.g. Minecraft's `tellraw @a {"text":"{message}"}`) would make `.format()` raise.
- `container_status` (Docker's `running`/`exited`/etc. state) and `container_health` (Docker's `HEALTHCHECK` result: `starting`/`healthy`/`unhealthy`) are deliberately separate functions rather than one combined call. Most allow-listed containers don't define a `HEALTHCHECK` at all — `container_health` returns `None` in that case (also for disallowed/not-found containers), and every caller (`!status`, `/status`) must treat `None` as "no health data to show," not as an error. Docker exposes health at `container.attrs["State"]["Health"]["Status"]`, a different field from `container.status`, so it can't be folded into `container_status`'s single string return without overloading its meaning.
- `!start` branches on `container_health` right after a successful `start_container()`: `None` (no healthcheck defined) reports success immediately, same as always — two messages total (`Starting {target}...`, then `res.message`). A configured healthcheck instead sends nothing further itself and hands `res.message` off to `_wait_for_healthy`, a `bot.loop.create_task()` background task (the same fire-and-forget pattern `_delayed_container_op` uses for stop/restart countdowns, so the command handler itself returns promptly rather than blocking command dispatch for however long the healthcheck takes). It polls every `HEALTHCHECK_POLL_INTERVAL` seconds until health leaves `starting`, then sends the single follow-up message: the original success message on `healthy` (so the two-message shape — `Starting {target}...` then `started` — is identical whether or not a healthcheck is configured), or an unhealthy/gave-up message otherwise. `HEALTHCHECK_MAX_WAIT` bounds the wait (`0` disables the cap). A concurrent second `!start` while this is in flight just hits `start_container()`'s existing `"already running"` short-circuit — no separate dedup/`pending_ops` entry needed, unlike stop/restart, since starting an already-running container is naturally idempotent. Because `_wait_for_healthy` is only ever scheduled when the initial health read was non-`None`, a health read of `None` mid-wait (container stopped, removed, or recreated without a `HEALTHCHECK` while the watcher was polling) is treated as terminal — the watcher sends a "no longer reports health status" message and returns immediately, rather than polling every `HEALTHCHECK_POLL_INTERVAL`s for the full `HEALTHCHECK_MAX_WAIT` (up to 30 minutes by default, or forever if `HEALTHCHECK_MAX_WAIT` is `0`) and then reporting a stale "still starting" that was never true.
- **Daemon-down vs. not-found vs. genuinely unexpected.** `_find_container_by_name` only converts `docker.errors.NotFound` to `None`. A connection-level failure — `docker.errors.DockerException` (the SDK's own hierarchy, including `APIError`) or a bare `requests.exceptions.RequestException` surfacing from the underlying HTTP/unix-socket transport (confirmed empirically: a vanished socket raises `requests.exceptions.ConnectionError`, which is *not* a `DockerException` subclass) — propagates out of `_find_container_by_name` instead of being swallowed. Every public function wraps its body and turns that into an honest, non-leaking outcome instead of letting it escape to Discord or misreporting "not found":
  - `Result`-returning functions (`start_container`, `stop_container`, `restart_container`, `announce_in_game`) catch `docker.errors.APIError` (raised by `c.start()`/`c.stop()`/`c.restart()`/`exec_run` for driver/runtime-level failures) and return `Result(False, "docker error: ...")`; the broader daemon/connection tuple is caught separately and returns `Result(False, "docker daemon error: <ExceptionType>")`. Either way the caller always gets a `Result` — never an exception escaping `run_blocking()` into `on_command_error`'s logging-only `else` branch, which used to leave the user with no reply at all.
  - `container_health`, `container_logs`, and `container_stats` catch the same daemon/connection tuple and return `None`, consistent with their existing "no data" contract.
  - `container_status` is the one exception: on a daemon/connection error it returns the literal string `"error"` (not `None`), specifically so `!status`, `/status`, and crash alerting can each tell "the daemon is unreachable" apart from `None` ("the container was removed or never existed"). Every consumer of `container_status` handles this sentinel explicitly: `status_cmd` and `_bail_if_not_running` in `bot.py` reply with an explicit "Docker daemon is unreachable" message instead of rendering `**error**` or claiming the container "is not running"; `/status` in `api.py` passes the string straight through as an honest status value for monitoring; `crash_check_loop` (see below) skips the poll entirely rather than treating it as a state transition. No error messages here include the socket path or daemon URL.

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
- Writes (`_save`) go through `atomic_io.atomic_write_json()`: JSON is written to a temp file in the same directory, `fsync`'d, `chmod`'d to `0o600`, then moved into place with `os.replace` — a crash mid-write can never leave a truncated `permissions.json`, and the 0o600 mode survives every write, not just the initial create. The in-memory `_cache`/`_cache_mtime` are only updated after the replace succeeds, so a failed write can't leave the cache claiming data that isn't on disk.
- A corrupted JSON file is **not** deleted: `_load()` renames it to `permissions.json.corrupt` (via `os.replace`) and logs at ERROR, then re-initializes the live file from defaults. The bot keeps running on defaults either way — only the destruction of evidence changed. An operator who finds the bot back on defaults can inspect the `.corrupt` sibling to recover any custom role grants. If the preserve-and-reinit itself fails (e.g. a read-only/permission-broken `data/` dir, so the corrupt file can't be moved or replaced), `_load()` still never raises: it degrades to in-memory-only defaults for that call and logs at ERROR. This fallback is deliberately **not** cached — every subsequent call retries the preserve-and-reload, so the bot self-heals as soon as the underlying filesystem issue is fixed, without needing a restart.
- Cache in `_load()` uses the file's `mtime` as a coherence key — cheap lookups when the file hasn't changed.

### Maintenance mode

- Toggled via `!maintenance on/off`. `BotState.is_maintenance_active()` takes no argument and just returns `state.maintenance_mode`. The six container-mutating commands (`start`, `stop`/`restart` via `_delayed_container_op`, `announce`, `logs`, `stats`) each start with `if await _bail_if_maintenance(ctx): return` — a shared `bot.py` helper that checks `is_maintenance_active()`, sends the maintenance message, and reports whether the caller should bail (C1: replaces five copies of the same three-line check). `cancel`, `status`, `guide`, `history`, `perm*`, and `maintenance` itself never call it, so they remain available during maintenance mode by construction rather than through an exemption list — `cancel` deliberately so, since enabling maintenance already cancels everything and cancelling during maintenance is harmless.
- `maintenance_cmd` intentionally has no `@commands.cooldown`: an admin must be able to toggle it again immediately during an active incident.
- Enabling maintenance calls `state.cancel_all_pending()`, which cancels and removes all in-flight stop/restart countdowns. The cancelled container names are included in the confirmation message.
- **Persistence (L4):** `{mode, reason}` is persisted to `MAINTENANCE_FILE` (default `data/maintenance.json`) on every toggle — both the `on` and `off` branches of `maintenance_cmd` call `BotState.save_maintenance(path)` via `docker_control.run_blocking()`, reusing `atomic_io.atomic_write_json()` (the same helper `permissions.py`/`history.py` use) rather than a fourth hand-rolled JSON writer. This is deliberate: a bot restart (crash, host reboot, image update — `restart: unless-stopped` makes this routine) must never silently resume scheduled work on a server the operator believes is frozen. Maintenance is cleared only by an explicit `!maintenance off`, never by a restart.
  - **Startup load:** `BotState.load_maintenance(path)` is called once from `main()` in [src/bot.py](src/bot.py), *before* `bot.run()` — not from `BotState.__init__`, since `state` is a module-level singleton constructed at import time (before config/logging are ready), and not from `on_ready` either, since `on_ready` can fire again on reconnect and would re-read the file needlessly. `main()` runs synchronously before the event loop starts, so the blocking read happens directly rather than via `run_blocking`.
  - State mutation (`load_maintenance`/`save_maintenance`) lives in `state.py` per house rules; `state.py` still doesn't import `config.py` — the caller in `bot.py` passes `MAINTENANCE_FILE` in as a parameter, keeping `state.py` a pure state/I-O helper with no config coupling.
  - **Corruption tolerance:** a missing file means normal first run (maintenance defaults off). A corrupt/unreadable file logs at ERROR and defaults to off rather than crashing startup, matching the resilience posture of `permissions.py`'s corruption handling — the bot must always start.
  - `data/maintenance.json` is runtime state (covered by `.gitignore`'s `data/`), not a secret, though it holds the operator-supplied reason string verbatim.

### Crash detection loop

- [src/bot.py](src/bot.py) `crash_check_loop` polls every `CRASH_CHECK_INTERVAL` seconds (default 30s).
- Initial statuses are seeded in `before_loop` to avoid false alerts on startup.
- Alerts land in `CRASH_ALERT_CHANNEL_ID` or fall back to `ANNOUNCE_CHANNEL_ID`. If neither is set, alerts are logged but not broadcast.
- The alert condition is `prev == "running" and current != "running"` — deliberately including `current is None`. A container force-removed while running (`docker rm -f`, a compose-down of the stack, a botched recreate) makes `container_status` return `None`; that must still alert (rendered as "removed/not found"), which is the scenario crash alerting exists for in the first place. Both the polling loop and `before_loop`'s seeding step special-case `container_status`'s `"error"` sentinel (daemon unreachable — see the Docker-operations section above): on `"error"` the iteration is skipped entirely — `last_known_status` is left untouched and no alert fires — so a transient daemon blip can neither mask a real crash that happened during the outage nor fire a false "removed" alert for every allow-listed container at once.

## CI / CD

- [tests-reusable.yml](.github/workflows/tests-reusable.yml) — the canonical test job: sets up Python 3.14, installs `requirements-dev.txt`, runs ruff (lint + format check) + pytest with coverage, then — when the `docker-smoke` input is true (the default) — a Docker build and startup smoke test (which installs only `requirements.txt`, matching what ships). Called by both workflows below via `workflow_call` so there is a single source of truth.
- [tests.yaml](.github/workflows/tests.yaml) — triggers on every branch push, on PRs targeting `main`, and on manual `workflow_dispatch`; calls `tests-reusable`. Branch pushes skip the Docker build/smoke steps (`docker-smoke: false`) for fast feedback; PRs to `main`, pushes to `main`, and manual dispatches run the full job including the image smoke test. A branch with an open PR fires both a `push` and a `pull_request` run for the same commit; the concurrency group is scoped by `event_name` as well as branch (`tests-<event>-<branch>`) specifically so those two never cancel each other — `test / test` is a required status check on `main`, and an early version of this workflow shared one group across both event types, so the push run (which starts first) would get cancelled by the pull_request run seconds later, leaving a permanent `CANCELLED` context on the required check that blocked merging regardless of the actual PR-gate result (hit in practice on PR #72). Rapid re-pushes still self-cancel within each event type.
- **Merge gate:** the repo's `Default_protections` ruleset on `main` requires PRs and requires the `test / test` status check to pass — so the full job (Docker smoke test included, since it's a `pull_request` run) must be green before a PR can merge. To pre-verify the image on a branch before opening a PR: `gh workflow run tests.yaml --ref <branch>`.
- [docker-publish.yml](.github/workflows/docker-publish.yml) — triggers on merge to main, version tags, and monthly (to pick up base image patches), plus manual `workflow_dispatch`. Calls `tests-reusable` first, then builds and pushes multi-arch (amd64/arm64) images to Docker Hub and prunes old tags to the last 5. The prune step protects every tag `build-and-push` just pushed from deletion, regardless of what Docker Hub's tags API reports for `last_updated` — that API can lag a same-run push long enough for the brand-new tag to sort as "oldest" and get pruned seconds after being published; this happened in production twice before the fix held. The first attempt passed `steps.meta.outputs.tags` (newline-separated) straight through as a job `outputs` value, which silently arrives empty in the dependent job — GitHub Actions job-to-job outputs (`needs.<job>.outputs.<x>`) don't reliably survive a multi-line value, only single-line step outputs are documented to work. Fixed by flattening the tag list to a single-line, comma-separated string (a dedicated "Flatten tag names" step) *before* exposing it as a job output.
- [codeql.yml](.github/workflows/codeql.yml) — Python + Actions static analysis on PRs and weekly.
- **Dependabot** — weekly bumps for pip, GitHub Actions, and the Docker base image, grouped where it makes sense.
