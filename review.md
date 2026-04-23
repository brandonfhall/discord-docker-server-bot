# Codebase Review — Discord Docker Controller Bot

**Reviewer:** Senior architect review
**Date:** 2026-04-22
**Scope:** Full repository (src/, tests/, CI workflows, Docker/compose, docs)
**Commit reviewed:** `ca35893` (main)

---

## TL;DR

This is a small, focused Python service that has been loved by multiple hands and shows it. The core is in good shape: a clean separation into `bot.py` / `docker_control.py` / `permissions.py`, solid input validation, token redaction, CI coverage, dependabot, CodeQL. No catastrophic security holes.

The issues are mostly **drift** (docs and CLAUDE.md no longer match the code), **polish** (a real healthcheck bug, missing `allowed_mentions` hardening, duplicated CI), and a few **architectural smells** (two-level env-var coupling, module-level `_docker_client`, deprecated `asyncio.get_event_loop()` pattern, argument injection surface in `announce_in_game`).

Nothing here is urgent. But several items are quick, low-risk wins.

---

## 1. Security

### 1.1 🟠 `allowed_mentions` not configured globally — user-echoed input can ping `@everyone` / roles

In [src/bot.py:30](src/bot.py#L30) the bot is created without `allowed_mentions`. Several handlers echo user input back into messages:

- [src/bot.py:61](src/bot.py#L61): `f"Container '{name}' is not in the allowed list."` — `name` is attacker-controlled.
- [src/bot.py:524](src/bot.py#L524), [src/bot.py:535](src/bot.py#L535): echoes `action` argument to `!perm add/remove`.
- [src/bot.py:444](src/bot.py#L444): `target` echoed in stats output.

Discord.py by default will resolve `@everyone`, `@here`, role pings, and user pings in any message content. Today the container-name allowlist + regex validation blocks names containing `@`, so the most obvious vector is closed. But defence-in-depth is one line:

```python
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none(),
)
```

Then explicitly opt in for the one command that needs it (`send_announcement` — the role ping). Today a future handler that echoes user text without thinking about this could hand the bot a ping primitive.

**Recommendation:** set `allowed_mentions=discord.AllowedMentions.none()` on the `Bot`, and pass `allowed_mentions=discord.AllowedMentions(roles=True)` on the announcement send path.

### 1.2 🟠 `/status` healthcheck baked into `Dockerfile` breaks when `STATUS_TOKEN` is set

[Dockerfile:23-24](Dockerfile#L23-L24):

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${STATUS_PORT:-8000}/')" || exit 1
```

`GET /` redirects to `/status` ([src/api.py:32-34](src/api.py#L32-L34)). When `STATUS_TOKEN` is set, `/status` requires auth and returns 401 ([src/api.py:19-29](src/api.py#L19-L29)). `urllib.request.urlopen` follows redirects and raises `HTTPError` on 401 → healthcheck fails → container marked unhealthy → restart loops.

The `docker-compose.yml` sidesteps this by overriding the healthcheck with a raw TCP check, but anyone running `docker run` directly (which is what `DOCKERHUB.md` promotes) will be bitten.

**Recommendation:** either (a) add an unauthenticated `/healthz` route that just returns 200, and point the Dockerfile healthcheck at it, or (b) replace the baked healthcheck with the TCP connect used in compose so behavior is identical regardless of how the image is launched.

### 1.3 🟡 `announce_in_game` shell path — safe today, but fragile *(docs portion ✅ done on `docs/restructure-and-refresh`: README + DOCKERHUB now call out the `--` separator and argument-injection risk; code-side hardening still pending)*

[src/docker_control.py:172-200](src/docker_control.py#L172-L200) has two execution paths:

- With `{message}` placeholder → `c.exec_run(["/bin/sh", "-c", cmd])` (shell).
- Without → `c.exec_run(argv)` (argv, no shell).

The sanitizer ([src/docker_control.py:161-169](src/docker_control.py#L161-L169)) whitelists `[a-zA-Z0-9 .,!?:_-]` and truncates to 100 chars. There are **no** shell metacharacters in the whitelist, so injection into `sh -c` is prevented today.

However:

1. **Argument injection via `-`**: hyphens are allowed. A message like `--help` or `-n -e` inside a command template like `rcon-cli {message}` becomes a flag. Not a shell-escape, but can change program behaviour.
2. **The whitelist is a trust boundary one edit away from being broken.** Adding `"` or `'` or `$` to "let people use contractions" would open real shell injection.

**Recommendations:**
- Always use argv form (never `sh -c`) unless absolutely required. Deprecate the `{message}` shell template in favour of `argv + [message]`.
- Document in the README / `.env.example` the argument-injection risk and recommend using `--` separators in templates: `CONTAINER_MESSAGE_CMD=rcon-cli say -- {message}`.
- Consider adding a length guard (100) server-side in `_sanitize` *and* rejecting messages that begin with `-`.

### 1.4 🟡 `STATUS_TOKEN` comparison is not constant-time

[src/api.py:28](src/api.py#L28): `if not token or token != STATUS_TOKEN`. This is a timing-attack-sensitive comparison. Low real risk (tokens are high-entropy, and practical HTTP timing attacks against LANs are hard), but trivial to fix:

```python
import secrets
if not token or not secrets.compare_digest(token, STATUS_TOKEN):
    raise HTTPException(status_code=401, detail="Unauthorized")
```

### 1.5 🟡 `/status` exposes recent log lines — review what lands in them

[src/api.py:45-55](src/api.py#L45-L55) returns the last 50 log lines with token redaction. But logs include:

- Discord usernames and user IDs ([src/bot.py:110](src/bot.py#L110), [src/bot.py:207](src/bot.py#L207)).
- Role names touched by `!perm`.
- Container names, announcement messages.

If `STATUS_TOKEN` is unset (the default! only a warning is logged), all of that is public to anyone who can reach the port. The `.env.example` and docs describe this as "open", which is fine as documentation, but I would make the bot **refuse to start** with no `STATUS_TOKEN` unless an explicit `STATUS_OPEN=true` opt-in is set. "Fail secure by default" is worth the one-line user friction.

### 1.6 🟡 Docker socket access = full host root *(docs portion ✅ done on `docs/restructure-and-refresh`)*

Well documented in README and DOCKERHUB.md ✅. Worth considering:

- ✅ **docker-socket-proxy** hardening is now documented in the README Security section with a copy-pasteable compose snippet, and DOCKERHUB.md links to it.
- The entrypoint falls back to `usermod -aG root botuser` ([entrypoint.sh:14-15](entrypoint.sh#L14-L15)) when the socket is owned by root:0. That makes `botuser` effectively root-group. If the host socket is root-owned (common on some distros), the "non-root user" story is mostly theatrical. Still worth calling out in the README hardening section in a follow-up.

### 1.7 🟢 Things working well

- Container name allowlist regex at the Docker-SDK layer, not just the command layer. Defence in depth. ✅
- `_RedactingFilter` is a handler-level filter, so every log handler benefits. ✅
- Permissions file created with `0o600` on initial write ([src/permissions.py:26](src/permissions.py#L26)).  Minor nit: subsequent saves via [src/permissions.py:74](src/permissions.py#L74) don't set mode — on most systems the existing inode retains the mode, so not a practical problem.
- Guild lock (`DISCORD_GUILD_ID`) and channel lock (`ALLOWED_CHANNEL_IDS`) both present. ✅
- Cooldowns per-user on the loud commands. ✅
- Dependabot + CodeQL + pinned requirements. ✅

---

## 2. Consistency / Documentation drift

### 2.1 ✅ ~~`CLAUDE.md` directory tree is stale~~ — **FIXED** on `docs/restructure-and-refresh`

Resolved by splitting the old monolithic CLAUDE.md:
- New [ARCHITECTURE.md](ARCHITECTURE.md) holds the directory tree, stack table, runtime model, and all internal conventions — now listing all 8 `src/` files.
- [CLAUDE.md](CLAUDE.md) is now a short contributor-focused guide that references ARCHITECTURE.md and README.md rather than duplicating them.

### 2.2 🟡 `HISTORY_FILE` and `COMMAND_COOLDOWN` missing from `docker-compose.yml`

[docker-compose.yml:11-26](docker-compose.yml#L11-L26) lists env vars to pass through from the host. Missing: `HISTORY_FILE`, `COMMAND_COOLDOWN`, `CRASH_CHECK_INTERVAL`, `CRASH_ALERT_CHANNEL_ID`, `LOG_LEVEL`. They all have sensible defaults in `config.py`, so this is a "you can't override them without editing compose" issue, not a correctness issue. Still, the list should either be complete or just use `env_file: .env`.

**Recommendation:** switch both compose files to `env_file: .env` (and document the full list in `.env.example`). This removes the drift class entirely.

### 2.3 ✅ ~~`DOCKERHUB.md` env var table is a strict subset of README's~~ — **Verified intentional**, refreshed on `docs/restructure-and-refresh`

[DOCKERHUB.md](DOCKERHUB.md) intentionally lists the common env vars and directs readers to the README for the full list — that's reasonable for a Docker Hub landing page. On this branch DOCKERHUB.md also now includes the docker-socket-proxy hardening cross-link and the `--` argument-injection note in the message template example.

### 2.4 🟡 `history.record` call style is inconsistent

Used as `history.record(HISTORY_FILE,ctx.author, ...)` — no space after the comma — in [src/bot.py:208](src/bot.py#L208), [src/bot.py:348](src/bot.py#L348), [src/bot.py:413](src/bot.py#L413), [src/bot.py:438](src/bot.py#L438), [src/bot.py:505](src/bot.py#L505). Space present in [src/bot.py:239](src/bot.py#L239), [src/bot.py:255](src/bot.py#L255), [src/bot.py:498](src/bot.py#L498). Cosmetic, but a `black` or `ruff format` pass would fix the whole file in one go.

### 2.5 🟡 `permissions.json.example` doesn't match `ALL_ACTIONS`

[permissions.json.example](permissions.json.example) has 10 keys and matches [src/permissions.py:11-14](src/permissions.py#L11-L14). ✅ — but the *on-disk* `data/permissions.json` only has 3 (`start`, `stop`, `restart`). That's fine because `_load()` auto-backfills missing actions, but worth verifying the backfill path is well tested. (It is — `TestPermissions` covers this.)

### 2.6 🟢 What's consistent

- README commands table matches `VALID_ACTIONS` / `ALL_ACTIONS` and the actual handlers.
- `.env.example` contains every var read by `config.py`.
- Python 3.11 pinned in both `Dockerfile` and CI workflows.

---

## 3. Code Quality / Tech Debt

### 3.1 🟠 `main()` uses the deprecated `asyncio.get_event_loop()` pattern

[src/bot.py:579-583](src/bot.py#L579-L583):

```python
def main():
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, start_api)
    bot.run(BOT_TOKEN)
```

`asyncio.get_event_loop()` emits `DeprecationWarning` when no loop is running (Python 3.12+) and is scheduled to **raise** in future versions. The current behaviour is also subtle: `loop.run_in_executor` schedules a task on the default ThreadPoolExecutor of `loop`, but `bot.run()` then creates and runs its **own** loop, ignoring the one you fetched. The uvicorn call happens to succeed because the threadpool executor submits the work in a thread that then spins up its own asyncio loop — but the coupling to `loop` is illusory.

**Recommendation:** launch uvicorn on a plain daemon thread:

```python
import threading

def main():
    t = threading.Thread(target=start_api, daemon=True, name="status-api")
    t.start()
    bot.run(BOT_TOKEN)
```

Simpler, more portable, and doesn't depend on deprecated behaviour. Also makes shutdown clearer (daemon thread dies with the process).

### 3.2 🟡 `_docker_client` is a module-level global used across threads

[src/docker_control.py:33-40](src/docker_control.py#L33-L40) lazily constructs a single `docker.from_env()` client. It's used both from the bot's ThreadPoolExecutor workers (via `run_blocking`) **and** directly from the FastAPI thread ([src/api.py:41](src/api.py#L41)).

The Docker SDK is not explicitly documented as thread-safe. In practice it mostly is (each call is an HTTP request on urllib3), but the `_find_container_by_name` + `c.reload()` + `c.status != "running"` + `c.start()` sequence in [src/docker_control.py:61-72](src/docker_control.py#L61-L72) is a **TOCTOU** (time-of-check-to-time-of-use): two concurrent `start_container` calls could both pass the "already running" check.

The `state.pending_ops` dedup in `bot.py` catches most duplicates, but the API endpoint reads status concurrently with a bot operation, and the user can hit `!start` exactly once while the crash-check loop also queries the same container. Risk is very low (Docker itself idempotently handles "start an already-running container" — you get an APIError that the code doesn't catch though).

**Recommendations:**
- Add a per-container `asyncio.Lock` for mutating operations, or at least wrap `c.start()` / `c.stop()` in a try/except for `docker.errors.APIError` and return a friendly "already running / not running" message rather than raising.
- Or: don't bother, and add a comment noting the single-client + TOCTOU tradeoff.

### 3.3 🟡 `_delayed_container_op` positional-arg parser is hacky

[src/bot.py:215-228](src/bot.py#L215-L228) accepts two positional args and tries to figure out which is `"now"` and which is the container. Edge cases:

- `!stop foo bar` — first loop iteration sets `container_name = "foo"`, second overwrites it to `"bar"`. Silent data loss.
- `!stop now now` — `now=True`, `container_name` stays None.

Works for the happy path but fragile. Cleaner:

```python
async def stop(ctx, *args):
    now = "now" in (a.lower() for a in args)
    remaining = [a for a in args if a.lower() != "now"]
    container_name = remaining[0] if remaining else None
    if len(remaining) > 1:
        await ctx.send("Usage: !stop [container] [now]")
        return
```

### 3.4 🟡 Duplicated CI between `tests.yaml` and `docker-publish.yml`

[.github/workflows/docker-publish.yml:18-52](.github/workflows/docker-publish.yml#L18-L52) re-implements the entire test job that [.github/workflows/tests.yaml](.github/workflows/tests.yaml) already runs. Two places to update. Small lint differences (`pytest-cov` in one, not the other) already diverging.

**Recommendation:** extract to a reusable workflow via `workflow_call`, or make `docker-publish.yml` depend on `tests.yaml` having passed on the same SHA via `workflow_run`.

### 3.5 🟡 `pip install "requests<2.32.0"` in Dockerfile — flag for follow-up

[Dockerfile:10](Dockerfile#L10):

```dockerfile
pip install "requests<2.32.0"
```

This is a workaround for the docker-py 7.1 incompatibility with requests 2.32 (`CVE-2024-35195`-adjacent chardet bug). The requirements.txt pins `docker==7.1.0`. At some point docker-py will ship a release that's compatible with newer requests; until then this pin is quietly shipping an older `requests` with known fixes you're missing. Worth a comment in the Dockerfile and a tracker issue.

### 3.6 🟡 `tests/test_unit.py` is a 2016-line monolith

One file, 22 test classes. Works, runs fast. But: finding a test, understanding what is and isn't covered, and reviewing failures is harder than it needs to be. Natural split:

```
tests/
  test_config.py          # TestConfig, TestNewConfig
  test_docker_control.py  # TestDockerControl, TestDockerControlLogs, TestDockerControlStats
  test_permissions.py     # TestPermissions
  test_bot_commands.py    # TestBotLogic, TestPendingOps, TestStopNow, TestRestartNow, TestLogsCommand, TestStatsCommand, TestMaintenanceMode, TestHistoryCommand, TestCooldownError, TestGuideUpdated
  test_api.py             # TestStatusEndpoint
  test_logging.py         # TestRedactingFilter
  test_crash_alerting.py  # TestCrashAlerting
  test_state.py           # TestCancelPending, TestCommandHistory
```

Zero behavior change; big navigability win.

### 3.7 🟡 Duplicated env bootstrapping in `conftest.py` and `test_unit.py`

[tests/conftest.py:5-6](tests/conftest.py#L5-L6) and [tests/test_unit.py:6-7](tests/test_unit.py#L6-L7) both `os.environ.setdefault("BOT_TOKEN", ...)`. Conftest is loaded first by pytest, so the test_unit.py copy is dead code. Drop it.

### 3.8 🟡 `announce_in_game` has a duplicated try/except block

[src/docker_control.py:183-200](src/docker_control.py#L183-L200) — two `try/except` blocks differ only in how `cmd`/`argv` is built. Refactor:

```python
cmd = (["/bin/sh", "-c", CONTAINER_MESSAGE_CMD.format(message=safe_msg)]
       if "{message}" in CONTAINER_MESSAGE_CMD
       else CONTAINER_MESSAGE_CMD.split() + [safe_msg])
try:
    res = c.exec_run(cmd)
    out = res.output.decode("utf-8").strip()
    if res.exit_code != 0:
        return Result(False, f"error ({res.exit_code}): {out}")
    return Result(True, f"ok: {out}" if out else "ok")
except Exception as e:
    return Result(False, f"error: {e}")
```

### 3.9 🟡 `maintenance_mode` doesn't cancel in-flight countdowns

If an admin runs `!stop` (triggering a 5-minute countdown), then runs `!maintenance on`, the scheduled stop still fires. That's defensible behaviour ("maintenance" means "no new commands") but surprising. Either:

- Make `!maintenance on` also cancel every entry in `state.pending_ops`, **or**
- Document that maintenance doesn't affect scheduled ops (add a line to the maintenance handler output and to the README).

### 3.10 🟡 No `.dockerignore`

Build context is the entire repo. No real harm since Dockerfile only `COPY`s `requirements.txt`, `src/`, `entrypoint.sh` — but the context transfer includes `.git/`, `.venv/` (if present), `data/`, `.pytest_cache/`, etc. Slower builds, more disk, and a risk that someone later adds a `COPY . .` and ships secrets. One-line fix:

```
# .dockerignore
.git
.venv
data
.pytest_cache
__pycache__
*.pyc
tests
.env
.env.*
```

### 3.11 🟡 No formatter / type checker in CI

flake8 is configured with reasonable rules but:
- Line-length is 127 (high). Most modern Python projects are at 88 (black) or 100.
- No autoformatter (black / ruff format) — explains the whitespace inconsistencies above.
- No type checker — `docker_control.py` and `history.py` have partial type hints; they'd benefit from `mypy --strict` or at least `--warn-return-any`.

Low urgency. If you're not going to add them, consider noting the style intent in CONTRIBUTING (or just accept flake8 as the bar).

### 3.12 🟢 What's good

- `Result` NamedTuple gives calling code a clean success/message pair instead of exceptions for expected failure modes. ✅
- `state.py` centralises mutable globals into one testable object with proper fixture reset. ✅
- `RedactingFilter` is elegant and correct. ✅
- `_ensure_file` + mtime-based cache in `permissions.py` is a nice lightweight pattern — avoids re-reading on every command. ✅
- Cooldowns, guild/channel locks, crash loop with seed-on-startup all look thought-through.

---

## 4. Minor / Nitpicks

| Location | Issue |
|---|---|
| [src/bot.py:241](src/bot.py#L241) | `"Stop{'ping' if action == 'stop' else 'ing'}"` — works but brittle. Pass `present_participle` as an explicit kwarg. |
| [src/bot.py:258-259](src/bot.py#L258-L259) | Countdown shown as "X minutes" even if `SHUTDOWN_DELAY < 60` (e.g., dev config uses `10`). Displays "0 minutes". Use `timedelta` or format conditionally. |
| [src/bot.py:419](src/bot.py#L419) | Truncating logs to `[-1900:]` — if log contains ``` ``` ```, the code block breaks. Escape or strip backticks. |
| [src/bot.py:361](src/bot.py#L361) | `!guide` has no cooldown and no permission check — spammable. Add `@commands.cooldown(1, COMMAND_COOLDOWN, ...)`. |
| [src/bot.py:97-105](src/bot.py#L97-L105) | `on_ready` can fire multiple times (reconnects). `crash_check_loop.is_running()` guards re-start, ✅ — but the info log re-fires every reconnect too. Minor log noise. |
| [src/docker_control.py:69](src/docker_control.py#L69) | `c.reload()` after `containers.get(name)` is redundant — `get()` already returns a fresh container. |
| [src/permissions.py:20-21](src/permissions.py#L20-L21) | `os.makedirs(directory)` should be `os.makedirs(directory, exist_ok=True)` to avoid a TOCTOU race (vanishingly unlikely, but idiomatic). |
| [src/history.py:26-28](src/history.py#L26-L28) | Same thing — `os.makedirs` should have `exist_ok=True`. |
| [src/logging_config.py:27-29](src/logging_config.py#L27-L29) | Same thing. |
| [src/api.py:50-52](src/api.py#L50-L52) | Redaction here duplicates `RedactingFilter` logic — could just be `" ".join(line for line in recent_logs)`. The filter already redacted at write time. |
| [src/config.py:40-42](src/config.py#L40-L42) | `PERMISSIONS_FILE` and `LOG_FILE` are plain `.strip()`'d — no validation that the resolved path is inside the container's writable volume. If a user sets `LOG_FILE=/etc/passwd`, they'd get a config-time failure at first write, not at startup. Minor; document or fail-fast. |
| `.pytest_cache/` is checked in | Listed in `.gitignore` but the directory is tracked (see `ls -la`). Remove once. |

---

## 5. Suggested Priority Queue

If I had a backlog to drain, this is the order:

### P1 — small, high-value fixes (< 30 min each)

1. **Set `allowed_mentions=AllowedMentions.none()` on the `Bot`** and explicitly allow role mentions only in `send_announcement`. (§1.1)
2. **Fix the Dockerfile healthcheck** to work with `STATUS_TOKEN` set — either add an unauthenticated `/healthz` or use the TCP-connect version from compose. (§1.2)
3. ✅ ~~**Update `CLAUDE.md`** directory tree to include `api.py`, `history.py`, `logging_config.py`, `state.py`.~~ (§2.1) — **DONE:** CLAUDE.md split into a Claude-focused guide plus a new [ARCHITECTURE.md](ARCHITECTURE.md) that lists all 8 modules.
4. **Use `secrets.compare_digest`** for `STATUS_TOKEN`. (§1.4)
5. **Switch compose files to `env_file: .env`** to kill the env-var drift class. (§2.2)
6. **Add `.dockerignore`.** (§3.10)
7. **Add cooldown to `!guide`.** (§4)

**Also completed on `docs/restructure-and-refresh`:**
- Created [ARCHITECTURE.md](ARCHITECTURE.md) (architecture, runtime model, conventions, CI/CD).
- Rewrote [CLAUDE.md](CLAUDE.md) as a short contributor guide with `@README.md` / `@ARCHITECTURE.md` references.
- Added docker-socket-proxy hardening section to README Security (§1.6).
- Added `--` argument-injection note to README and DOCKERHUB message-template examples (§1.3, docs portion).
- Added Development → ARCHITECTURE.md / CLAUDE.md cross-links in README.

### P2 — modest refactors (1–3 hours)

1. **Rewrite `main()`** to use a daemon thread for the API instead of `loop.run_in_executor` on a deprecated event loop. (§3.1)
2. **Consolidate CI** — extract the test matrix into a reusable workflow, stop duplicating between `tests.yaml` and `docker-publish.yml`. (§3.4)
3. **Split `tests/test_unit.py`** into module-per-concern files. (§3.6)
4. **Clean up `announce_in_game`** duplication. (§3.8)
5. **Document or implement argument-injection hardening** (`--` separators, reject leading `-`). (§1.3)

### P3 — strategic / nice-to-have

1. **Default-refuse to start without `STATUS_TOKEN`** unless `STATUS_OPEN=true`. Fail secure by default. (§1.5)
2. **Add mypy** to CI (even just `--follow-imports=skip` on src/). (§3.11)
3. **Consider `docker-socket-proxy`** as the recommended deployment pattern. (§1.6)
4. **Unpin `requests<2.32.0`** once docker-py ships a compatible release. (§3.5)
5. **Add a small integration-test pass** that exercises the FastAPI endpoint with `httpx.AsyncClient` — right now `TestStatusEndpoint` mocks at the function level; a real FastAPI test client would be cheap insurance.

---

## 6. Overall Assessment

**Architecture:** sound. The module decomposition after the recent `api.py` / `state.py` / `history.py` / `logging_config.py` splits is better than what CLAUDE.md still documents. Keep going in that direction — `bot.py` is still the "everything goes here" file at 588 lines and could lose its command handlers to a cogs-based layout.

**Security posture:** good for a small project. The two things I'd address first are `allowed_mentions` hardening and the healthcheck+STATUS_TOKEN interaction. Everything else is incremental.

**Operational readiness:** the crash-alert loop, maintenance mode, command history, cooldowns, and Discord guild/channel locking show real operational experience. Someone's been running this in production and fixing what went wrong.

**Tech debt:** mild. The single biggest shape-of-code issue is the `main()` event-loop juggling; after that it's mostly whitespace, duplicated CI, and doc drift. None of it is urgent.

**Tests:** well-covered (22 classes, 2000 lines) but organised as a monolith. Splitting the file is a clear win.

**Recommendation:** prioritise the P1 list above. The whole thing is ~2 hours of work and closes the highest-leverage items. Then decide whether P2/P3 is worth the time.

---

## 7. Implementation Phases

> Engineer's breakdown from the architect's review. Phase 1 is already done. Each subsequent phase is designed to be a self-contained branch + PR that can be merged independently.

---

### ✅ Decisions recorded

| # | Question | Decision |
|---|---|---|
| Q1 | `env_file` vs passthrough list | **Add the 4 missing vars** to the existing passthrough list in both compose files |
| Q2 | `STATUS_TOKEN` fail-secure | **Non-breaking** — keep open-by-default, but make the warning louder in `on_ready` and docs |
| Q3 | Maintenance + in-flight countdowns | **Cancel pending ops** when `!maintenance on` is invoked |
| Q4 | Docker TOCTOU | **Friendly `try/except APIError`** wrapping in `start_container` / `stop_container` |
| Q5 | Formatter | **Adopt `ruff`** at 127-char line length, after Phase 6 is complete |

---

### Phase 1 — Documentation restructure ✅ Complete

*Merged on `docs/restructure-and-refresh`.*

- Created [ARCHITECTURE.md](ARCHITECTURE.md) (all 8 modules, runtime model, conventions).
- Rewrote [CLAUDE.md](CLAUDE.md) as a Claude-focused contributor guide.
- Added docker-socket-proxy hardening section to README.
- Added argument-injection `--` note to README and DOCKERHUB.
- Struck completed items from the architect's priority queue.

---

### Phase 2 — Zero-risk housekeeping

> **Risk:** very low. Pure cleanup, no behavior change. All changes verified against tests. Can be a single branch + PR.

**2a. Add `.dockerignore`** (§3.10)

Create `.dockerignore` excluding `.git`, `.venv`, `data`, `.pytest_cache`, `__pycache__`, `*.pyc`, `tests`, `.env`, `.env.*`. No behavior change — Dockerfile only COPYs specific paths — but speeds up builds and prevents a future `COPY . .` from accidentally shipping secrets.

**2b. Fix `os.makedirs` → `exist_ok=True` in three places** (§4)

- [src/permissions.py:21](src/permissions.py#L21)
- [src/history.py:28](src/history.py#L28)
- [src/logging_config.py:29](src/logging_config.py#L29)

All three have a TOCTOU between `os.path.exists(dir)` and `os.makedirs(dir)`. Vanishingly unlikely to hit in practice but idiomatic Python is `exist_ok=True` and removes the check entirely.

**2c. Add cooldown to `!guide`** (§4)

One decorator line. Prevents a non-permissioned user from spamming the bot into Discord rate-limit territory.

**2d. Fix `history.record` spacing** (§2.4)

Five call sites in `bot.py` are missing the space after the first comma. Cosmetic, but consistent code is easier to read/grep. Fix all five to `history.record(HISTORY_FILE, ctx.author, ...)`.

**2e. Remove dead env bootstrapping from `test_unit.py`** (§3.7)

[tests/test_unit.py:6-7](tests/test_unit.py#L6-L7) duplicates what `conftest.py` already does. The conftest version runs first; the test file version is dead code. Remove the two lines.

**2f. Add comment to Dockerfile about the `requests` pin** (§3.5)

Not unpinning yet (docker-py upstream hasn't released a fix), but adding a comment explaining why the pin exists so future maintainers don't just delete it thinking it's stale.

**2g. Complete the missing env vars in `docker-compose.yml`** (§2.2)

*Conditional on Q1 answer.* If not switching to `env_file`, add `HISTORY_FILE`, `COMMAND_COOLDOWN`, `CRASH_CHECK_INTERVAL`, `CRASH_ALERT_CHANNEL_ID` to the existing passthrough list in both compose files.

**2h. Add a note about `c.reload()` calls** (§4)

The architect flagged four `c.reload()` calls immediately after `_find_container_by_name` as redundant (since `containers.get()` fetches fresh state). This is technically correct. However, the `reload()` in `start_container` and `stop_container` exists to ensure the status check reads real-time data before taking action — removing it saves one API call but makes the intent less explicit. **Recommendation:** keep the reloads in the mutation functions (`start_container`, `stop_container`) but add a brief comment; remove the reload in `container_status` which is genuinely redundant since `reload()` is the whole point of that function.

---

### Phase 3 — Security hardening

> **Risk:** low-to-medium. Touches `bot.py`, `api.py`, and `docker_control.py`. Each change is small and testable. Tests will need updates for the `allowed_mentions` change.

**3a. `allowed_mentions` global default** (§1.1) — **highest priority**

Add `allowed_mentions=discord.AllowedMentions.none()` to the `commands.Bot(...)` constructor. Then in `send_announcement`, add `allowed_mentions=discord.AllowedMentions(roles=True)` to the `target_channel.send(content)` call so role pings still work there. Test update: verify the announcement test still asserts a role mention goes through.

**3b. Fix Dockerfile healthcheck** (§1.2)

Add an unauthenticated `GET /healthz` route to [src/api.py](src/api.py) that returns `{"ok": True}` with no auth dependency. Update the `HEALTHCHECK` in `Dockerfile` to hit `/healthz` instead of `/`. This eliminates the restart loop when `STATUS_TOKEN` is set. The compose healthcheck (TCP connect) can stay as-is.

**3c. `secrets.compare_digest` for STATUS_TOKEN** (§1.4)

Replace `token != STATUS_TOKEN` in [src/api.py:28](src/api.py#L28) with `not secrets.compare_digest(token, STATUS_TOKEN)`. Two-line change. Add `import secrets` at the top.

**3d. Sanitize leading `-` in announce messages** (§1.3)

In `_sanitize()` in [src/docker_control.py:161-169](src/docker_control.py#L161-L169), after stripping characters, strip any leading hyphens from the result. This closes the argument-injection vector documented in Phase 1.

**3e. Refactor `announce_in_game` try/except duplication** (§3.8)

Consolidate the two identical try/except blocks into one (as shown in the architect's recommendation). Zero behavior change, easier to maintain. Pair with 3d since we're touching the same function.

**3f. Fix countdown display for sub-60s `SHUTDOWN_DELAY`** (§4)

[src/bot.py:258-259](src/bot.py#L258-L259) shows "0 minutes" when `SHUTDOWN_DELAY` is under 60 seconds (e.g., `10` in the dev compose). Fix: display seconds when delay < 60, minutes otherwise.

**3g. Fix code-block backtick escaping in `!logs` output** (§4)

[src/bot.py:419](src/bot.py#L419) wraps logs in triple-backtick fences. If the log output itself contains ` ``` `, the Discord message breaks. Strip or escape backticks from `output` before wrapping.

---

### Phase 4 — Architecture / event loop

> **Risk:** medium. `main()` change touches startup behavior. Test carefully with an actual bot token before merging. The arg parser fix requires updating tests.

**4a. Rewrite `main()` to use a daemon thread** (§3.1)

Replace the `asyncio.get_event_loop()` + `run_in_executor` pattern with a plain `threading.Thread(target=start_api, daemon=True)`. This is simpler, removes the deprecated call, and makes shutdown behavior explicit (daemon thread dies with the process). The functional behavior is identical but no longer relies on a deprecated API.

**4b. Fix `_delayed_container_op` positional-arg parser** (§3.3)

Rewrite the two-arg parser that identifies `"now"` vs container name using `*args` and list filtering (as shown in §3.3). Covers the `!stop foo bar` silent-overwrite edge case. Requires test updates to cover the new `*args` signature.

**4c. Maintenance mode + pending ops** (§3.9)

*Conditional on Q3 answer.* Either cancel pending ops on `!maintenance on`, or add a sentence to the maintenance handler response and README stating that scheduled countdowns are not affected.

---

### Phase 5 — CI / infrastructure

> **Risk:** low. Workflow changes don't affect runtime. Test locally with `act` or push to a branch and inspect Actions output.

**5a. Eliminate duplicated CI test job** (§3.4)

The test job in [docker-publish.yml](.github/workflows/docker-publish.yml) is a copy of [tests.yaml](.github/workflows/tests.yaml), already diverging (`pytest-cov` missing). Extract a reusable workflow (`tests-reusable.yml` with `on: workflow_call`) that both `tests.yaml` and `docker-publish.yml` call. Single source of truth, one place to update.

**5b. Add `pytest-cov` to `docker-publish.yml`** (§3.4)

*Can be folded into 5a.* Currently the publish pipeline runs tests without coverage. Align with `tests.yaml`.

---

### Phase 6 — Test organization

> **Risk:** very low. Zero behavior change — pure file moves. The only thing that can go wrong is a broken import. Run `pytest` immediately after splitting to confirm.

**6a. Split `test_unit.py` into module files** (§3.6)

Move the 22 test classes into 8 focused files as the architect outlined. Preserve all `import` statements and conftest fixtures (they're already in `conftest.py` and apply globally). Update `CLAUDE.md` test-conventions section to reference the new layout.

---

### Phase 7 — Ruff formatter adoption

> Run after Phase 6 is merged. Single standalone PR — "style only, no logic."

- Install `ruff` and add to `requirements.txt` (dev dependency) or CI inline install.
- Run `ruff format .` at 127-char line length (matching current flake8 config) — rewrites whitespace/spacing across all src files.
- Replace the two `flake8` CI steps in `tests.yaml` and `docker-publish.yml` with `ruff check .` (lint) + `ruff format --check .` (format gate).
- Remove `flake8` from CI install steps; add `ruff` in its place.
- Update `CLAUDE.md` and `ARCHITECTURE.md` to reference ruff instead of flake8.

### Phase 8 — Remaining strategic items

> Low urgency. Revisit after Phase 7.

| Item | Action |
|---|---|
| §3.5 Unpin `requests<2.32.0` | Monitor Dependabot — remove pin when docker-py ships a compatible release |
| §1.6 `usermod -aG root` entrypoint caveat | Add a note to README hardening section |
| API integration tests | Add `httpx.AsyncClient` test for `/healthz` + `/status` after Phase 3b lands |
