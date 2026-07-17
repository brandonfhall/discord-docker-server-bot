# CLAUDE.md

Guidance for Claude Code working in this repository. Keep this file small and pointed — architecture and user docs live elsewhere.

- **User-facing docs:** @README.md
- **Internal architecture & conventions:** @ARCHITECTURE.md

## What this project is

Dockerized Python service that bridges Discord `!` commands to the Docker daemon, controlling one or more allow-listed containers (typically game servers). Also exposes a FastAPI `/status` endpoint. See @ARCHITECTURE.md for module layout and runtime model.

## Common commands

```bash
# Install runtime + dev/test dependencies (pytest, pytest-cov, ruff, httpx)
pip install -r requirements-dev.txt

# Tests (no Docker daemon required — all Docker calls are mocked)
PYTHONPATH=. pytest -v tests/

# Coverage report (matches CI)
PYTHONPATH=. pytest -v --cov=src --cov-report=term-missing tests/

# Lint + format check (matches CI)
ruff check .
ruff format --check .

# Auto-fix lint issues and apply formatting
ruff check --fix . && ruff format .

# Local dev with live source mount
docker compose -f docker-compose.dev.yml up --build

# Image build smoke test
docker build . --file Dockerfile --tag bot-test:latest
```

Test env vars (`BOT_TOKEN`, `ALLOWED_CONTAINERS`, `DISCORD_GUILD_ID`) are set by [tests/conftest.py](tests/conftest.py); don't re-set them in individual tests. `conftest.py` also redirects `LOG_FILE`/`HISTORY_FILE`/`PERMISSIONS_FILE` to a tmp path so test runs don't write into the repo's `data/` directory.

## House rules

- **Prefer editing existing files over creating new ones.** This repo has clear module boundaries — add a handler in `bot.py`, a Docker call in `docker_control.py`, a permission action in `permissions.py`, etc.
- **All Docker SDK calls go through `run_blocking()`.** Never call `docker` SDK functions directly from an async handler — they're synchronous and will stall the event loop. The same applies to other blocking calls made from handlers, like `history.record()` — wrap them in `run_blocking()` too rather than calling them inline.
- **All mutable cross-handler state belongs in `state.py`** (`BotState` singleton). Don't add new module-level globals in `bot.py`.
- **Use the `Result` NamedTuple** in `docker_control.py` for operations with expected success/failure paths. Raise only on genuinely unexpected errors.
- **Container names and announcement messages are validated at the `docker_control` layer,** not just at the command layer. Don't weaken `_VALID_CONTAINER_NAME` or `_VALID_MSG_CHARS`: the message whitelist is what makes the `/bin/sh -c` template path in `announce_in_game` safe — widening it to quotes, `$`, or backticks reopens shell injection via `!announce`.
- **Log redaction is handler-level.** If you add a new secret env var, extend the token list passed to `setup_logging()` in [src/bot.py](src/bot.py).
- **Don't use `asyncio.get_event_loop()`** in new code — it's deprecated. Use `asyncio.run()` or a dedicated thread for sync-launched services.

## Adding a new command

1. Add the handler to [src/bot.py](src/bot.py) with `@bot.command()` and (if privileged) `@has_permission("<action>")`.
2. Add `@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)` unless there's a reason not to.
3. If the command introduces a new permission action, add it to `ALL_ACTIONS` in [src/permissions.py](src/permissions.py) (single source of truth — `bot.py` re-exports it as `VALID_ACTIONS`).
4. Call `await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, "<action>", target)` for auditable actions.
5. If the command mutates containers, start the handler with `if await _bail_if_maintenance(ctx): return` (it sends the maintenance message and returns True when blocked). Read-only/admin commands (`cancel`, `status`, `perm*`, `guide`, `history`, `maintenance`) deliberately skip this.
6. Add unit tests in the matching file under [tests/](tests/) (e.g. a new bot command goes in [tests/test_bot_commands.py](tests/test_bot_commands.py)). Follow the existing `unittest.IsolatedAsyncioTestCase` patterns with `AsyncMock` for `ctx.send`.
7. Update the Commands table in both [README.md](README.md) and [DOCKERHUB.md](DOCKERHUB.md).

## Adding a new env var

1. Parse it in [src/config.py](src/config.py). Use `_int_env` for integers so invalid values fall back with a warning.
2. Import it from `.config` wherever it's used — don't call `os.getenv()` in handler code.
3. Document it in [.env.example](.env.example) and the env-var table in [README.md](README.md).
4. If it's a secret, add it to the token list in `setup_logging()`.
5. **Add it to the `environment:` passthrough list in BOTH [docker-compose.yml](docker-compose.yml) and [docker-compose.dev.yml](docker-compose.dev.yml).** Both files use bare `- VAR` env passthrough — a var missing from either list is silently unconfigurable in that deployment (no error, no warning, just the default). This step is easy to skip because nothing fails without it — do it every time, not just when something breaks.

## Test conventions

Tests live in [tests/](tests/) split by concern:

| File | What it covers |
|---|---|
| `test_config.py` | `TestConfig`, `TestNewConfig`, `TestGuildLockRequired` |
| `test_docker_control.py` | `TestDockerControl`, `TestDockerControlLogs`, `TestDockerControlStats` |
| `test_permissions.py` | `TestPermissions` |
| `test_bot_commands.py` | `TestBotLogic`, `TestPendingOps`, `TestStopNow`, `TestRestartNow`, `TestLogsCommand`, `TestStatsCommand`, `TestMaintenanceMode`, `TestCancelCommand`, `TestHistoryCommand`, `TestCooldownError`, `TestGuideUpdated` |
| `test_api.py` | `TestHealthzEndpoint`, `TestStatusEndpoint` |
| `test_logging.py` | `TestRedactingFilter` |
| `test_crash_alerting.py` | `TestCrashAlerting` |
| `test_state.py` | `TestCancelPending`, `TestCommandHistory` |

- Unit tests mock the Docker SDK; don't introduce tests that require a real Docker daemon.
- Use `conftest.py` fixtures (`_reset_state`, `_reset_permissions_cache`) — they already run automatically.
- For command handlers, build a fake `ctx` with `AsyncMock` for `ctx.send` and a `MagicMock` for `ctx.author` with `guild_permissions.administrator`, `roles`, and `id` set.

## What to check before claiming done

- `ruff check .` is clean and `ruff format --check .` passes.
- `pytest` passes with no new warnings.
- If you touched command surface or env vars, both [README.md](README.md) and [DOCKERHUB.md](DOCKERHUB.md) reflect the change.
- If you touched architecture or conventions, update [ARCHITECTURE.md](ARCHITECTURE.md) too.
