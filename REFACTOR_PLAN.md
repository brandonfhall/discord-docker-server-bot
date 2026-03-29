# Refactoring Plan: Discord Docker Server Bot

**Author**: Opus 4.6 (Senior Architect Review)
**Date**: 2026-03-29
**Branch**: `claude/suggest-features-SAkKn`
**Status**: Ready for assignment

---

## Executive Summary

`src/bot.py` is a 786-line god object that handles Discord commands, FastAPI server, global state management, file I/O, logging setup, and crash monitoring. This plan breaks it into focused modules, fixes correctness bugs, deduplicates ~130 lines of nearly identical stop/restart logic, and modernizes the test infrastructure.

**Current architecture:**
```
src/
  bot.py             — 786 lines (everything)
  docker_control.py  — 204 lines (Docker SDK wrappers)
  config.py          — 72 lines (env var parsing)
  permissions.py     — 84 lines (JSON-backed role permissions)
tests/
  test_unit.py       — 2013 lines, 154 tests, unittest-style
```

**Target architecture:**
```
src/
  bot.py             — ~400 lines (Discord commands only)
  docker_control.py  — ~215 lines (gains Result namedtuple)
  config.py          — 72 lines (unchanged)
  permissions.py     — ~95 lines (gains caching + VALID_ACTIONS export)
  history.py         — ~45 lines (NEW: audit log with file locking)
  state.py           — ~35 lines (NEW: BotState class for mutable globals)
  logging_config.py  — ~40 lines (NEW: RedactingFilter + setup_logging)
  api.py             — ~55 lines (NEW: FastAPI app + status endpoint)
tests/
  conftest.py        — ~50 lines (gains shared fixtures)
  test_unit.py       — ~1800 lines (same tests, less boilerplate)
```

---

## Execution Order

```
Phase 1 ─ Safety/Correctness (no structural changes)
  Task 1.1: Unify VALID_ACTIONS / _EXPECTED_ACTIONS     ── standalone
  Task 1.2: Add threading lock to history file I/O       ── standalone
  Task 1.3: Notify users on scheduled stop/restart fail  ── standalone

Phase 2 ─ Extract Modules (break up the god object)
  Task 2.1: Extract history module (src/history.py)      ── after 1.2
  Task 2.2: Extract logging setup (src/logging_config.py)── standalone
  Task 2.3: Extract state management (src/state.py)      ── standalone
  Task 2.4: Extract FastAPI app (src/api.py)             ── after 2.2

Phase 3 ─ Deduplicate Command Logic
  Task 3.1: Shared stop/restart helper                   ── after 2.3

Phase 4 ─ Improve Error Handling
  Task 4.1: Structured Result type in docker_control     ── after 3.1

Phase 5 ─ Test & Infrastructure Improvements
  Task 5.1: Pytest fixtures + autouse state cleanup      ── after 2.3
  Task 5.2: Add coverage reporting to CI                 ── standalone
  Task 5.3: Dockerfile improvements (non-root, healthcheck) ── standalone
  Task 5.4: Add permissions caching                      ── standalone
```

**Recommended merge order**: 1.1 -> 1.2 -> 1.3 -> 2.1 -> 2.2 -> 2.3 -> 2.4 -> 3.1 -> 4.1 -> 5.2 -> 5.3 -> 5.4 -> 5.1

Run `PYTHONPATH=. pytest -v tests/` after each task. Test count must stay at 154 or increase.

---

## Phase 1: Safety and Correctness Fixes

These tasks fix real bugs within the existing file structure. Lowest risk, do first.

### Task 1.1: Unify VALID_ACTIONS and _EXPECTED_ACTIONS

**Problem**: `VALID_ACTIONS` in `bot.py` (line 24) and `_EXPECTED_ACTIONS` in `permissions.py` (line 10) are separate sets that must be kept in sync manually. There is a test that checks equality, but the duplication is the root cause.

**Changes**:

1. **`src/permissions.py`**: Rename `_EXPECTED_ACTIONS` to `ALL_ACTIONS`, make it public, use `frozenset`:
   ```python
   ALL_ACTIONS = frozenset({
       "start", "stop", "stop_now", "restart", "restart_now",
       "announce", "logs", "stats", "maintenance", "history"
   })
   ```
   Update all internal references from `_EXPECTED_ACTIONS` to `ALL_ACTIONS`.

2. **`src/bot.py`**: Replace the `VALID_ACTIONS` definition with:
   ```python
   from .permissions import ALL_ACTIONS as VALID_ACTIONS
   ```

3. **`tests/test_unit.py`**: Update `test_expected_actions_matches_valid_actions` to assert identity:
   ```python
   self.assertIs(bot_module.VALID_ACTIONS, permissions.ALL_ACTIONS)
   ```

**Verify**: `PYTHONPATH=. pytest -v tests/ -k "valid_actions or expected_actions or permissions"`

**Dependencies**: None.

---

### Task 1.2: Add threading lock to history file I/O

**Problem**: `_record_history()` calls `_load_history()` then `_save_history()` with no synchronization. Concurrent commands can read stale data and overwrite each other's entries.

**Changes in `src/bot.py`**:
1. Add `import threading` at the top.
2. Add `_history_lock = threading.Lock()` near the global state declarations.
3. Wrap the body of `_record_history()` in `with _history_lock:` (this covers the entire load-append-save cycle).
4. In `history_cmd`, wrap the `_load_history()` call in `with _history_lock:`.

**Verify**: `PYTHONPATH=. pytest -v tests/ -k "history"`

**Dependencies**: None.

---

### Task 1.3: Notify users on scheduled stop/restart failures

**Problem**: `do_stop()` (lines 420-428) and `do_restart()` (lines 489-498) catch `Exception`, log it, but never tell the user. User schedules a stop, walks away, and never learns it failed.

**Changes in `src/bot.py`**:
- In `do_stop()`, after `logging.error(...)`, add:
  ```python
  try:
      await ctx.send(f"Error during scheduled stop of {target}: {e}")
  except Exception:
      pass  # ctx may be stale if channel was deleted
  ```
- Apply identical pattern to `do_restart()`.
- Add tests that verify error message is sent when the scheduled operation raises.

**Verify**: `PYTHONPATH=. pytest -v tests/ -k "stop or restart"`

**Dependencies**: None.

---

## Phase 2: Extract Modules from bot.py

Each task extracts one concern. Do NOT change behavior or function signatures -- just move code to new files and update imports.

### Task 2.1: Extract history module -- `src/history.py`

**Problem**: History I/O is a self-contained feature embedded in the god object.

**Create `src/history.py`**:
```python
import json
import os
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_MAX_ENTRIES = 200


def load(history_file: str) -> list:
    """Load command history from disk."""
    if not os.path.exists(history_file):
        return []
    try:
        with open(history_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save(history_file: str, entries: list):
    """Save command history to disk."""
    hist_dir = os.path.dirname(history_file)
    if hist_dir and not os.path.exists(hist_dir):
        os.makedirs(hist_dir)
    entries = entries[-_MAX_ENTRIES:]
    with open(history_file, "w") as f:
        json.dump(entries, f, indent=2)


def record(history_file: str, user, command: str, container: str = ""):
    """Append a command entry. Thread-safe."""
    with _lock:
        entries = load(history_file)
        entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": str(user),
            "command": command,
            "container": container,
        })
        save(history_file, entries)
```

**Changes in `src/bot.py`**:
- Delete `_load_history`, `_save_history`, `_record_history` functions.
- Delete `_history_lock` (from Task 1.2).
- Remove `import json` if unused elsewhere.
- Add `from . import history`.
- Replace calls: `_record_history(x, y, z)` -> `history.record(HISTORY_FILE, x, y, z)`.
- Replace calls: `_load_history()` -> `history.load(HISTORY_FILE)`.

**Changes in `tests/test_unit.py`**:
- `TestCommandHistory`: Test `src.history` functions directly instead of `bot_module._record_history`.
- `TestHistoryCommand`: Patch `"src.bot.history.load"` instead of `bot_module._load_history`.

**Verify**: `PYTHONPATH=. pytest -v tests/`

**Dependencies**: Task 1.2 (absorbs the lock into the new module).

---

### Task 2.2: Extract logging setup -- `src/logging_config.py`

**Problem**: Logging setup is infrastructure, not bot logic.

**Create `src/logging_config.py`**:
```python
import logging
import os
from logging.handlers import RotatingFileHandler


class RedactingFilter(logging.Filter):
    """Strips sensitive token values from log records."""
    def __init__(self, tokens):
        super().__init__()
        self._tokens = [t for t in tokens if t]

    def filter(self, record):
        if self._tokens:
            msg = record.getMessage()
            for token in self._tokens:
                msg = msg.replace(token, "[REDACTED]")
            record.msg = msg
            record.args = ()
        return True


def setup_logging(log_file: str, log_level: str, tokens: list):
    """Configure root logger with console + rotating file handler and token redaction."""
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=1),
        ],
    )

    redact_filter = RedactingFilter(tokens)
    for handler in logging.root.handlers:
        handler.addFilter(redact_filter)
```

**Changes in `src/bot.py`**:
- Remove `_RedactingFilter` class and all logging setup code (lines 79-116).
- Remove `from logging.handlers import RotatingFileHandler`.
- Add at the top (right after config imports):
  ```python
  from .logging_config import setup_logging
  setup_logging(LOG_FILE, os.getenv("LOG_LEVEL", "INFO"), [BOT_TOKEN, STATUS_TOKEN])
  ```

**Changes in `tests/test_unit.py`**:
- `TestRedactingFilter`: Change `from src.bot import _RedactingFilter` to `from src.logging_config import RedactingFilter`. Update all references.

**Verify**: `PYTHONPATH=. pytest -v tests/ -k "Redact"`

**Dependencies**: None.

---

### Task 2.3: Extract state management -- `src/state.py`

**Problem**: Four global mutable variables and two helper functions scattered in bot.py with no encapsulation or synchronization.

**Create `src/state.py`**:
```python
"""Centralized mutable state for the bot."""


class BotState:
    """Container for all mutable bot state."""

    def __init__(self):
        self.pending_ops: dict = {}
        self.maintenance_mode: bool = False
        self.maintenance_reason: str = ""
        self.last_known_status: dict = {}

    def cancel_pending(self, container: str):
        """Cancel and remove a pending stop/restart task."""
        task = self.pending_ops.pop(container, None)
        if task and not task.done():
            task.cancel()

    def has_pending_op(self, container: str) -> bool:
        """Check if a container has a pending operation."""
        task = self.pending_ops.get(container)
        return task is not None and not task.done()

    def is_maintenance_active(self, command_name: str) -> bool:
        """Return True if maintenance mode blocks the given command."""
        exempt = {
            "maintenance", "perm", "perm add", "perm remove",
            "perm list", "guide", "history",
        }
        if command_name in exempt:
            return False
        return self.maintenance_mode


# Module-level singleton
state = BotState()
```

**Changes in `src/bot.py`**:
- Remove: `_pending_ops`, `_maintenance_mode`, `_maintenance_reason`, `_last_known_status` declarations.
- Remove: `_cancel_pending()` function.
- Remove: `_check_maintenance()` function.
- Add: `from .state import state`.
- Replace ALL references:
  | Old | New |
  |-----|-----|
  | `_pending_ops` | `state.pending_ops` |
  | `_maintenance_mode` | `state.maintenance_mode` |
  | `_maintenance_reason` | `state.maintenance_reason` |
  | `_last_known_status` | `state.last_known_status` |
  | `_cancel_pending(target)` | `state.cancel_pending(target)` |
  | `_check_maintenance(ctx)` | `state.is_maintenance_active(ctx.command.qualified_name if ctx.command else "")` |
  | `target in _pending_ops and not _pending_ops[target].done()` | `state.has_pending_op(target)` |
  | `global _maintenance_mode, _maintenance_reason` | (delete -- use `state.maintenance_mode = ...` directly) |

**Changes in `tests/test_unit.py`**:
- Add `from src.state import state` where needed.
- Replace `bot_module._pending_ops` with `state.pending_ops`.
- Replace `bot_module._maintenance_mode` with `state.maintenance_mode`.
- Replace `bot_module._maintenance_reason` with `state.maintenance_reason`.
- Replace `bot_module._last_known_status` with `state.last_known_status`.
- Tests for `_check_maintenance` -> test `state.is_maintenance_active()`.
- Tests for `_cancel_pending` -> test `state.cancel_pending()`.

**Verify**: `PYTHONPATH=. pytest -v tests/`

**Dependencies**: None.

---

### Task 2.4: Extract FastAPI app -- `src/api.py`

**Problem**: HTTP API is a separate concern from the Discord bot.

**Create `src/api.py`**:
Move from `bot.py`:
- FastAPI imports (`FastAPI`, `Header`, `HTTPException`, `Depends`, `Query`, `RedirectResponse`)
- `from collections import deque`
- `app = FastAPI()`
- `verify_token()` function
- `root()` route
- `status()` route
- `start_api()` function (the uvicorn startup)

The new module imports `config`, `docker_control`, `permissions` directly. It does NOT import `bot`.

**Changes in `src/bot.py`**:
- Remove all FastAPI-related code (~60 lines).
- Remove `import uvicorn` and FastAPI imports.
- Add `from .api import start_api` (used in `main()`).
- Keep `from .api import app` only if needed for the reference in `main()`.

**Changes in `tests/test_unit.py`**:
- `test_root_redirects_to_status`: `from src.api import app`.
- `test_verify_token_*` tests: patch `src.api.STATUS_TOKEN`.
- `TestStatusEndpoint`: import `app` from `src.api`, patch `src.api.STATUS_TOKEN`, `src.api.ALLOWED_CONTAINERS`, etc.

**Verify**: `PYTHONPATH=. pytest -v tests/ -k "status or token or root"`

**Dependencies**: Task 2.2 (so logging is set up independently of bot.py imports).

---

## Phase 3: Deduplicate Command Logic

### Task 3.1: Shared stop/restart helper

**Problem**: `stop()` (lines 366-430) and `restart()` (lines 433-499) are ~65 lines each and nearly identical. They differ only in: action name, Docker function called, announcement wording, `_now` permission name.

**Add to `src/bot.py`**:
```python
async def _delayed_container_op(
    ctx, arg1, arg2, *,
    action: str,           # "stop" or "restart"
    now_action: str,       # "stop_now" or "restart_now"
    docker_func,           # docker_control.stop_container or restart_container
    immediate_msg: str,    # "Server is shutting down NOW..."
    countdown_msg_tpl: str,# "Server will shut down in {minutes} minutes..."
):
    """Shared logic for delayed container operations (stop/restart)."""
    # Maintenance check
    if state.is_maintenance_active(ctx.command.qualified_name if ctx.command else ""):
        await ctx.send(f"Bot is in maintenance mode. {state.maintenance_reason}")
        return

    # Parse arguments
    now = False
    container_name = None
    for arg in (arg1, arg2):
        if arg and arg.lower() == "now":
            now = True
        elif arg:
            container_name = arg

    target = await resolve_container(ctx, container_name)
    if not target:
        return

    if now:
        # ... immediate path (permission check, cancel pending, execute)
        return

    # ... delayed path (pending ops check, countdown, scheduled task)
```

**Rewrite `stop()` and `restart()`** as thin wrappers:
```python
@bot.command()
@has_permission("stop")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def stop(ctx, arg1: str = None, arg2: str = None):
    """Stops the container (with optional delay or 'now')."""
    await _delayed_container_op(
        ctx, arg1, arg2,
        action="stop",
        now_action="stop_now",
        docker_func=docker_control.stop_container,
        immediate_msg="Server is shutting down NOW. Please disconnect immediately.",
        countdown_msg_tpl="Server will shut down in {minutes} minutes. Please prepare to log off.",
    )
```

This reduces ~130 lines to ~70 lines (one helper + two wrappers).

**Verify**: `PYTHONPATH=. pytest -v tests/ -k "stop or restart"`

**Dependencies**: Task 2.3 (uses `state.` references).

---

## Phase 4: Improve Error Handling

### Task 4.1: Structured Result type in docker_control

**Problem**: `docker_control` functions return bare strings like `"started"` or `"container X not found"`. Callers cannot distinguish success from failure programmatically.

**Changes in `src/docker_control.py`**:
```python
from typing import NamedTuple

class Result(NamedTuple):
    success: bool
    message: str
```

Change all action functions to return `Result`:
- `start_container`: `return Result(True, "started")` / `return Result(False, "container X not found")`
- `stop_container`: same pattern
- `restart_container`: same pattern
- `announce_in_game`: same pattern

Leave `container_status`, `container_stats`, `container_logs` unchanged (different return types).

**Changes in `src/bot.py`**:
- Where `res` is sent to users: `await ctx.send(f"Stop result: {res}")` -> `await ctx.send(f"Stop result: {res.message}")`
- Optionally log differently on failure: `if not res.success: logging.warning(...)`

**Changes in `tests/test_unit.py`**:
- Docker control tests: `assertEqual(result, "started")` -> `assertTrue(result.success)` and `assertEqual(result.message, "started")`.
- Bot command tests that mock `run_blocking` returning `"started"`: change to return `Result(True, "started")`.

**IMPORTANT**: This is the highest-risk task -- it touches the most test assertions. Do it atomically: update docker_control and all callers/tests in one commit. Run the full test suite before committing.

**Verify**: `PYTHONPATH=. pytest -v tests/`

**Dependencies**: Best done after Task 3.1 (so the deduplicated helper handles `res.message` in one place).

---

## Phase 5: Test and Infrastructure Improvements

### Task 5.1: Pytest fixtures and autouse state cleanup

**Problem**: Tests mutate module globals directly, reimplement setUp/tearDown across 10+ classes, create temp files in CWD.

**Changes in `tests/conftest.py`**:
```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def mock_ctx():
    """Pre-configured mock Discord context."""
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.channel = MagicMock()
    ctx.channel.id = 100
    ctx.channel.send = AsyncMock()
    ctx.author = MagicMock()
    ctx.author.guild_permissions.administrator = True
    return ctx


@pytest.fixture(autouse=True)
def clean_bot_state():
    """Reset global bot state before each test."""
    from src.state import state
    state.pending_ops.clear()
    state.maintenance_mode = False
    state.maintenance_reason = ""
    state.last_known_status.clear()
    yield
    import asyncio
    for task in list(state.pending_ops.values()):
        if asyncio.isfuture(task) and not task.done():
            task.cancel()
    state.pending_ops.clear()


@pytest.fixture
def tmp_permissions(tmp_path):
    """Provide a temporary permissions file."""
    from src import permissions
    original = permissions.PERMISSIONS_FILE
    test_file = str(tmp_path / "permissions.json")
    permissions.PERMISSIONS_FILE = test_file
    yield test_file
    permissions.PERMISSIONS_FILE = original
```

**Changes in `tests/test_unit.py`** (incremental -- one class at a time):
- Remove manual `setUp`/`tearDown` in `TestPendingOps`, `TestStopNow`, `TestRestartNow`, `TestMaintenanceMode`, `TestCrashAlerting` -- the `clean_bot_state` autouse fixture handles state reset.
- Replace `ctx = MagicMock(); ctx.send = AsyncMock()` boilerplate with `mock_ctx` fixture (where applicable).
- Replace `self.test_file = "test_permissions.json"` patterns with `tmp_permissions` / `tmp_path` fixtures.

**NOTE**: Do this incrementally. Update one test class at a time. Run tests after each change.

**Verify**: `PYTHONPATH=. pytest -v tests/`

**Dependencies**: Task 2.3 (uses `state` module).

---

### Task 5.2: Add coverage reporting to CI

**Problem**: CI runs tests but doesn't track or report coverage.

**Changes**:
1. **`.github/workflows/tests.yaml`**:
   - Change: `pip install flake8 pytest` -> `pip install flake8 pytest pytest-cov`
   - Change: `pytest -v tests/` -> `pytest -v --cov=src --cov-report=term-missing tests/`
   - Optionally add: `--cov-fail-under=80` to fail if coverage drops.

**Verify**: Push branch, check CI output includes coverage table.

**Dependencies**: None.

---

### Task 5.3: Dockerfile improvements

**Problem**: Container runs as root. No HEALTHCHECK in image.

**Docker socket constraint**: The bot bind-mounts `/var/run/docker.sock` which is owned by `root:docker` on the host. A non-root container user needs group-level access to this socket. Since the host's Docker group GID varies across systems, we use an entrypoint script that dynamically creates a group matching the socket's GID at runtime.

**New/modified files**:

1. **Create `entrypoint.sh`**:
```bash
#!/bin/sh
set -e

# If running as root (default), set up non-root user with Docker socket access
if [ "$(id -u)" = "0" ]; then
    # Detect the GID of the Docker socket
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo "")

    if [ -n "$DOCKER_GID" ] && [ "$DOCKER_GID" != "0" ]; then
        # Create a group with the same GID as the Docker socket
        groupadd -g "$DOCKER_GID" -o dockersock 2>/dev/null || true
        usermod -aG dockersock botuser
    elif [ -n "$DOCKER_GID" ] && [ "$DOCKER_GID" = "0" ]; then
        # Socket owned by root group -- botuser needs root group membership
        usermod -aG root botuser
    fi

    # Fix data dir ownership
    chown -R botuser:botuser /app/data 2>/dev/null || true

    # Re-exec as botuser
    exec gosu botuser "$@"
fi

# If already non-root, just exec
exec "$@"
```

2. **New Dockerfile**:
```dockerfile
FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install "requests<2.32.0"

COPY src/ ./src/
COPY entrypoint.sh /entrypoint.sh

RUN useradd -r -m botuser \
    && mkdir -p /app/data \
    && chown -R botuser:botuser /app \
    && chmod +x /entrypoint.sh

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${STATUS_PORT:-8000}/')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "src.bot"]
```

3. **Update `docker-compose.yml`**: No changes needed -- the entrypoint auto-detects the socket GID.

**How it works**: The container starts as root, the entrypoint detects the GID of `/var/run/docker.sock`, creates a matching group, adds `botuser` to it, then re-execs the CMD as `botuser` via `gosu`. The bot process itself never runs as root.

**Fallback**: If `/var/run/docker.sock` is not mounted (e.g., in CI tests), the entrypoint skips group setup and runs as `botuser` directly.

**Verify**:
```bash
docker build . -t bot-test
# Verify process runs as botuser:
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock bot-test whoami
# Should print: botuser
# Verify Docker socket access:
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock bot-test python -c "import docker; print(docker.from_env().ping())"
# Should print: True
```

**Dependencies**: None.

---

### Task 5.4: Add permissions caching

**Problem**: `permissions.py` reads from disk on every `is_member_allowed()` call. No caching.

**Changes in `src/permissions.py`**:
```python
_cache = None
_cache_mtime = 0.0

def _load():
    global _cache, _cache_mtime
    _ensure_file()
    try:
        current_mtime = os.path.getmtime(PERMISSIONS_FILE)
    except OSError:
        current_mtime = 0.0
    if _cache is not None and current_mtime == _cache_mtime:
        return _cache
    # ... existing load logic ...
    _cache = data
    _cache_mtime = current_mtime
    return data

def _save(data):
    global _cache, _cache_mtime
    # ... existing save logic ...
    _cache = data
    try:
        _cache_mtime = os.path.getmtime(PERMISSIONS_FILE)
    except OSError:
        _cache_mtime = 0.0

def invalidate_cache():
    """For testing: force next _load() to read from disk."""
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = 0.0
```

**Changes in `tests/test_unit.py`**:
- Call `permissions.invalidate_cache()` in `TestPermissions.setUp()` to ensure test isolation.

**Verify**: `PYTHONPATH=. pytest -v tests/ -k "permission"`

**Dependencies**: None.

---

## Key Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Import ordering in tests | `conftest.py` sets env vars before imports. New `src/` modules importing from `config` at module level must be imported AFTER env vars are set. Verify by running tests after each change. |
| Module-level side effects | `setup_logging()` must be called explicitly. Place call at top of `bot.py` module body (right after imports) to maintain same behavior. |
| Circular imports | `api.py` imports `docker_control` and `permissions`. `bot.py` imports `state`, `history`, `docker_control`, `permissions`, `api`. No cycles exist. Do NOT import `bot` from any new module. |
| Task 4.1 (Result type) is highest risk | Touches most test assertions. Do atomically in one commit. Run full suite before committing. |
| Test count regression | After every task: `PYTHONPATH=. pytest -v tests/ \| tail -5` -- test count must stay at 154 or increase. |

---

## Notes for Implementers

1. **Run tests constantly**: `PYTHONPATH=. python3 -m pytest -v tests/` after every change. Not after every task -- after every change.
2. **One task per commit**: Each task should be its own commit with a clear message.
3. **Don't change behavior**: Phase 2 is pure refactoring. If a test breaks, you changed behavior -- revert and try again.
4. **Import carefully**: The test file sets env vars in `conftest.py` lines 6-7 before importing `src` modules. Any new module that imports from `config` at module level will be fine as long as it's imported after conftest runs (which pytest guarantees).
5. **Update CLAUDE.md**: After completing all phases, update the Directory Structure section in CLAUDE.md to reflect the new file layout.
