# Code Review Findings & Remediation Plan — Cycle 2

**Branch:** `fable-review` · **Reviewed at commit:** `618a71a` (main) · **Date:** 2026-07-17
**Reviewer:** Claude Fable 5 (senior architecture review) · **Implementers:** Sonnet / Opus

## Progress

Phases are implemented one at a time by a Sonnet worker, reviewed by Fable, and committed
individually. Each phase also updates the documentation its own change touches; Phase 9 is an
independent top-to-bottom doc/architecture audit on top of that. Completed findings are compressed to
a one-line entry here — only residual notes (deviations, decisions, gotchas) are kept.

| Phase | Findings | Status |
|---|---|---|
| 1 | M1 — compose env passthrough | ✅ done — `b64a986` |
| 2 | M2 → M4 → M3 — Docker error paths | ☐ **next** |
| 3 | M5 — atomic writes | ☐ not started |
| 4 | M6 — event-loop I/O | ☐ not started |
| 5 | L1, L2, L3 — docker_control | ☐ not started |
| 6 | L5, L6, C1, C2, C3 — bot.py UX + consolidation | ☐ not started |
| 7 | L7, L8, C4 — deps/config/test hygiene | ☐ not started |
| 8 | L4 — persist maintenance mode | ☐ not started |
| 9 | Docs + architecture accuracy audit | ☐ not started |
| 10 | Final plan.md prune | ☐ not started |

**Maintainer decisions (2026-07-17), binding for this cycle:**
- **L4 → persist.** Maintenance mode gets a state file in `data/`; it must survive restarts and be
  cleared only by an explicit `!maintenance off`. Sequenced as Phase 8 so it reuses M5's atomic-write
  helper rather than duplicating it.
- **L9 → CLOSED, won't fix.** The `?token=` query param on `/status` stays. Rationale: this bot runs
  locally and is not internet-reachable; any proxy access log capturing the token lives on the same
  host as `STATUS_TOKEN` itself, so the param leaks nothing an attacker wouldn't already hold.
  **Do not re-flag in cycle 3.**
- **L10 → document only.** No role-ID migration. README's permissions section must state that
  permissions match by role *name* and that renaming a Discord role requires re-granting.

**Cycle 1 status: ✅ CLOSED.** The previous version of this file (in git history at `5460432` and
earlier) tracked 22 findings — all implemented, final-reviewed, and signed off (including follow-ups
F1/F2). Nothing from cycle 1 remains open; do not re-investigate it. This document contains only
**new** findings from a fresh top-to-bottom pass of the current codebase.

This document is a handoff. Each finding is self-contained: location, failure scenario, prescribed
fix, and tests to add. Implementers should not need to re-investigate. Line numbers reference
commit `74f0ac2`.

**Baseline at review time (verify before starting, re-verify after every finding):**
- `PYTHONPATH=. pytest -q tests/` → **205 passed** (1 pre-existing `audioop` DeprecationWarning from discord.py — not ours)
- `ruff check .` → clean · `ruff format --check .` → clean
- House rules in [CLAUDE.md](CLAUDE.md) apply to every fix: Docker/blocking calls via `run_blocking()`,
  state in `state.py`, `Result` NamedTuple, tests per the conventions table, README + DOCKERHUB.md
  updated when the command surface or env vars change, ARCHITECTURE.md updated when conventions change.

**Overall assessment:** the codebase is in good shape after cycle 1 — no exploitable security hole was
found this pass. What remains is one shipped-deployment functional gap (M1), a cluster of
reliability/error-reporting bugs (M2–M6), and a batch of hardening/UX/consolidation items. Nothing
here is architectural; module boundaries are sound and should not be reorganized.

**Severity legend:**
- **MEDIUM** — real correctness/reliability bug reachable in normal operation; fix first, in order.
- **LOW** — hardening, UX, or hygiene; batch by file.
- **CLEANUP** — consolidation / dead code / test hygiene; fold into nearby PRs.

---

## MEDIUM

### M1 — compose env passthrough gaps — ✅ DONE (Phase 1, `b64a986`)

Both compose files now pass all 21 vars `config.py` reads; CLAUDE.md's "Adding a new env var"
checklist gained the compose-passthrough step as the regression guard.

**Residual notes worth keeping:**
- **Scope grew during review.** Beyond M1's four named vars, a mechanical cross-check of config.py's
  full inventory against both files found `CONTAINER_MESSAGE_CMD` missing from `docker-compose.dev.yml`
  — same bug class, and the worst instance: a dev setting a real screen/rcon template in `.env`
  silently got the `echo` default, so `!announce` never exercised the `/bin/sh -c` template path in the
  one environment that exists to test it. Fixed in the same commit with an inline comment explaining why.
- **The gap-check is mechanical, and worth re-running** rather than eyeballing (this is how the
  original drift went unnoticed):
  ```sh
  grep -oE '(os\.getenv\("|_int_env\(")[A-Z_]+' src/config.py | grep -oE '[A-Z_]+$' | sort -u > /tmp/cfg.txt
  for f in docker-compose.yml docker-compose.dev.yml; do
    sed -n '/environment:/,/^    [a-z]/p' $f | grep -oE '^\s+- [A-Z_]+' | grep -oE '[A-Z_]+' | sort -u > /tmp/have.txt
    echo "MISSING from $f:"; comm -23 /tmp/cfg.txt /tmp/have.txt
  done
  ```
  Both lists came back empty at `b64a986`. **Phase 9 should re-run this** after all code phases land.
- **No automated test was added.** The optional pytest guard (assert every config.py var appears in
  both compose lists) was considered and skipped — the CLAUDE.md checklist plus the snippet above are
  the guard. If this drifts a *second* time, promote it to a real test; a third occurrence means the
  procedural guard doesn't work.
- **L11 was discovered by this cross-check** (`LOG_LEVEL` bypasses config.py) — see below, deferred to Phase 7.

---

### M2 — A down/unreachable Docker daemon is reported to users as "container not found"; unexpected `APIError`s from start/stop/restart reach the user as *nothing at all*

**Location:** [src/docker_control.py](src/docker_control.py):45–52 (`_find_container_by_name`), 63–99 (`start_container`/`stop_container`/`restart_container`).

**Problem (two related halves):**
1. `_find_container_by_name` catches **all** exceptions — including `docker.errors.DockerException` /
   connection errors when the socket is unreachable — logs a warning, and returns `None`. Every caller
   then reports `"container {name} not found"`. During an actual daemon outage (socket permission
   regression, docker-socket-proxy down, dockerd restart) the operator is told the container doesn't
   exist, which sends troubleshooting in exactly the wrong direction. Same effect in `crash_check_loop`
   and `/status` (`"status": null` instead of any error indication).
2. `c.start()` / `c.stop()` / `c.restart()` / `c.reload()` can raise `docker.errors.APIError` (e.g.
   driver failure, OCI runtime error — reachable in normal operation, not "genuinely unexpected").
   The exception propagates out of `run_blocking()` into the command handler; discord.py routes it to
   `on_command_error`'s final `else` ([src/bot.py](src/bot.py):212–213), which only logs. The user who
   typed `!start` gets **no reply at all** — the bot appears to hang.

**Fix:**
1. In `_find_container_by_name`, keep `docker.errors.NotFound` → `None`, but stop swallowing daemon
   errors: narrow the broad `except Exception` so `docker.errors.DockerException` (connection-level)
   propagates. Then have each public `Result`-returning function wrap its body:
   `except docker.errors.DockerException as e: return Result(False, f"docker daemon error: {type(e).__name__}")`
   (don't leak socket paths/URLs into Discord). For the Optional-returning functions
   (`container_status`/`container_health`/`container_logs`/`container_stats`), catch the daemon error,
   log at ERROR, and return `None` — **except** `container_status`, where returning the literal string
   `"error"` is recommended so `!status` and `/status` can display something honest instead of
   "not found". If that distinction is adopted, update `!status` rendering and note it in
   ARCHITECTURE.md's Docker-operations section.
2. Wrap the mutating calls (`c.start()` etc.) in `try/except docker.errors.APIError` returning
   `Result(False, f"docker error: {e.explanation or e}")` — this keeps the `Result` contract
   ("raise only on genuinely unexpected errors") honest and guarantees the user always gets a reply.

**Test:** in `tests/test_docker_control.py`: mock `client.containers.get` to raise
`docker.errors.DockerException` and assert the `Result` failure mentions the daemon (not "not
found"); mock `c.start` to raise `docker.errors.APIError("boom")` and assert `Result(False, ...)`
rather than an exception escaping.

---

### M3 — `_wait_for_healthy` spins for up to `HEALTHCHECK_MAX_WAIT` (default **30 minutes**) when the container stops or disappears mid-wait, then sends a wrong "still starting" message

**Location:** [src/bot.py](src/bot.py):291–319, specifically the loop at 304–313.

**Problem:** the loop only exits on `"healthy"` or `"unhealthy"`. `container_health()` returns `None`
for not-found, disallowed, **or no health state** — and a container that was `!stop`ped (or crashed
and was removed, or was recreated without a HEALTHCHECK) during the wait returns `None` forever. The
task then silently polls every 5s for 30 minutes and finally tells the user
``still `starting` after 1800s`` — false on both counts. With `HEALTHCHECK_MAX_WAIT=0` it polls
forever, leaking one background task plus one threadpool round-trip every 5s per orphaned start.

**Fix:** treat `None` mid-wait as terminal. `start()` only schedules the watcher when health was
non-None, so `None` here means the container went away or was recreated: break and send something like
``f"`{target}` no longer reports health status (it may have been stopped or recreated). Check `!status {target}`."``

**Test:** in `tests/test_bot_commands.py`, drive `_wait_for_healthy` with mocked
`docker_control.container_health` returning `"starting"` then `None`; assert prompt exit and the
terminal message (patch `asyncio.sleep` or use a tiny poll interval, following existing async-mock
patterns).

---

### M4 — Crash alerting never fires when a running container is *removed* (status transitions `running → None`)

**Location:** [src/bot.py](src/bot.py):225–228.

**Problem:** the alert condition is `prev == "running" and current and current != "running"`. When a
container is deleted while running (`docker rm -f`, a compose down of the game stack, a botched
re-create), `container_status` returns `None`, `current` is falsy, and no alert fires — precisely the
scenario crash alerting exists for. The baseline is then overwritten with `None`, so the event is
permanently invisible.

**Fix:** alert on `prev == "running" and current != "running"`, rendering `current or "removed/not found"`
in the message. **Ordering dependency on M2:** implement M2 first and preserve the property that a
daemon-level error *raises* out of `container_status` (so the `except Exception: continue` at
bot.py:222–224 skips the poll) rather than returning `None` — otherwise a daemon blip would fire a
false "removed" alert for every container. If M2's `"error"` string return is adopted for
`container_status`, also exclude `current == "error"` from alerting.

**Test:** in `tests/test_crash_alerting.py`: seed `state.last_known_status["x"] = "running"`, mock
`container_status` → `None`, assert an alert is sent naming the container.

---

### M5 — Permissions/history writes are non-atomic, and the permissions corruption-recovery path silently destroys all custom role grants

**Location:** [src/permissions.py](src/permissions.py):83–91 (`_save`), 57–68 (corruption recovery in `_load`); [src/history.py](src/history.py):23–30 (`save`).

**Problem:** both files are written with plain `open(path, "w")` + `json.dump`. A crash, OOM-kill, or
host power loss mid-write leaves a truncated file. For history this quietly loses the audit log
(`load` returns `[]`). For permissions it's worse: the next `_load` hits `JSONDecodeError`, **deletes
the file**, and re-initializes from `DEFAULT_ALLOWED_ROLES` — every `!perm add` ever made is gone,
with only a log line to show for it. Since permissions gate container control, a truncation event
silently *changes who can do what*.

**Fix:**
1. In both `_save` (permissions) and `save` (history): write to a temp file in the same directory,
   `json.dump` + `f.flush()` + `os.fsync(f.fileno())`, then `os.replace(tmp, path)` — atomic on POSIX.
   Preserve the 0o600 mode on the permissions file (`os.chmod` the temp file before replace; the
   initial-create opener at [src/permissions.py](src/permissions.py):37 handles first creation only).
2. In the corruption path in `_load`, **rename** the corrupted file to `PERMISSIONS_FILE + ".corrupt"`
   (via `os.replace`) instead of `os.remove`, and log at ERROR that defaults were restored and the
   original preserved. Recovery behavior (bot keeps running on defaults) stays the same — only the
   destruction of evidence changes.
3. Keep the cache coherent: `_save` currently updates `_cache`/`_cache_mtime` in step — update them
   after the `os.replace` succeeds.

**Test:** in `tests/test_permissions.py`: write garbage to the permissions file, call `_load`, assert
a `.corrupt` sibling now holds the garbage and the live file has defaults. For atomicity, at minimum
patch-and-assert `os.replace` is used by `_save` rather than in-place truncation.

---

### M6 — Remaining synchronous file I/O on the event loop: `history.load`, all three `!perm` handlers, and every `has_permission` check

**Location:** [src/bot.py](src/bot.py):85 (`permissions.is_member_allowed` inside the `has_permission` predicate), 363 (same, the `now`-path check), 680 (`history.load` in `history_cmd`), 753 (`permissions.add_role`), 765 (`permissions.remove_role`), 775 (`permissions.list_permissions`).

**Problem:** cycle 1 moved `history.record` behind `run_blocking()` and CLAUDE.md now states the rule
("the same applies to other blocking calls made from handlers"), but these six call sites still do
disk I/O directly on the event loop. The permission predicate runs on **every privileged command**: a
`stat()` per command always, plus a full file read + JSON parse whenever the mtime changed. On a
slow/contended volume this stalls the event loop (heartbeats, all other handlers). `history.load`
reads and parses up to 200 entries inline.

**Fix:** wrap each in `await docker_control.run_blocking(...)`. The `has_permission` predicate is
already `async`, so
`allowed = await docker_control.run_blocking(permissions.is_member_allowed, action, ctx.author)`
is a drop-in; same shape for the other five. Do **not** make the permissions module itself async —
sync-and-wrapped matches the existing pattern and its use from the API thread.
**Caution:** this adds awaits inside `_delayed_container_op`'s `now` path only — the non-`now` dedup
region is untouched, but re-read the invariant note in "What's already solid" below before editing.

**Test:** existing handler tests exercise these paths and should pass unchanged — that is the
acceptance criterion (no behavior change). Spot-check one test per touched handler.

---

## LOW (batch these; grouped by file)

### L1 — `_sanitize` order-of-operations lets a leading hyphen survive (defense-in-depth gap, not currently reachable)

**Location:** [src/docker_control.py](src/docker_control.py):178–187.

A message like `" -n hello"` (leading space) passes the whitelist, `lstrip("-")` is a no-op (the
string starts with a space), then the final `.strip()` removes the space — output `"-n hello"`,
defeating the flag-injection guard the lstrip exists for. discord.py's argument parsing strips
leading whitespace, so no current Discord input reaches this — but the function must be safe
standalone (house rule: validation lives at the docker_control layer, not the caller).
**Fix:** `s = s.strip().lstrip("-").strip()` (the final strip handles `"- foo"` → `"foo"`).
**Test:** `test_docker_control.py`: `_sanitize(" -n hello") == "n hello"`; `_sanitize("- x") == "x"`.

### L2 — `announce_in_game` executes the template even when the message sanitizes to empty

**Location:** [src/docker_control.py](src/docker_control.py):198–206.

A message of only non-whitelisted characters (all emoji/quotes) becomes `""` and the command still
runs — an in-game `say` with empty text, reported as success.
**Fix:** after `_sanitize`: `if not safe_msg: return Result(False, "message is empty after sanitization")`.
**Test:** announce with `"$$$"` → failure `Result`, `exec_run` not called.

### L3 — `res.output.decode("utf-8")` in `announce_in_game` can throw on non-UTF-8 exec output

**Location:** [src/docker_control.py](src/docker_control.py):210.

A game console emitting latin-1/binary turns a successful announce into
`Result(False, "error: 'utf-8' codec…")`.
**Fix:** `decode("utf-8", errors="replace")` — matching `container_logs` at line 138.
**Test:** mock `exec_run` output `b"\xff"` with `exit_code=0` → success `Result`.

### L4 — Maintenance mode does not survive a bot restart *(DECIDED: persist — Phase 8)*

**Location:** [src/state.py](src/state.py):10–11; toggled at [src/bot.py](src/bot.py):715–733.

**Maintainer decision (2026-07-17): persist it.** Maintenance is a deliberate operator action and must
be cleared only by a deliberate `!maintenance off` — never silently by a restart. Implement the
persist option below; ignore the warn-on-restart alternative. Sequenced after Phase 3 so it reuses the
atomic-write helper M5 introduces rather than duplicating it. Docs: README `!maintenance` rows +
ARCHITECTURE.md Maintenance-mode section must state that the flag survives restarts and where it's stored.

An operator enables maintenance, the bot restarts (crash, host reboot, image update —
`restart: unless-stopped` makes this routine), and maintenance is silently off; scheduled work
resumes on a server the operator believes is frozen.
**Fix (recommended = persist):** persist `{mode, reason}` to a small JSON in `data/` (same atomic-write
pattern as M5) on toggle; load at startup. Keep it out of config.py — it's runtime state. The cheaper
alternative is a startup WARNING + announce-channel message "bot restarted; maintenance mode was
reset" — acceptable, but leaves the footgun. Document the chosen behavior in README (`!maintenance`
rows) and ARCHITECTURE.md's Maintenance-mode section.
**Test:** `test_state.py`: toggle on → construct a fresh `BotState` (reload path) → still on, reason
preserved.

### L5 — UX batch in `bot.py` handlers

1. **`!status` renders "None"** for a not-found container ([src/bot.py](src/bot.py):508–510):
   `Status for x: **None**`. When `res is None`, show "not found" (and "docker error" if M2's
   `"error"` status is adopted).
2. **`!logs 0` accepted** ([src/bot.py](src/bot.py):609–610): `isdigit()` allows `0`; docker `tail=0`
   yields empty output and a confusing "No recent logs". Clamp: `lines = min(max(int(arg), 1), 50)`.
3. **`!start` during a pending stop countdown doesn't warn** ([src/bot.py](src/bot.py):254–288): the
   start succeeds, then the countdown kills the container minutes later. After resolving `target`, if
   `state.has_pending_op(target)`, append a note to the reply ("a stop/restart countdown is scheduled
   for this container — `!cancel` to abort"). Inform, don't block.
4. **`!logs` checks maintenance after arg-parsing** ([src/bot.py](src/bot.py):616–622) while every
   other handler checks first — harmless, but move it to the top for consistency (C1's decorator
   resolves this by construction if that route is taken).

**Tests:** one per bullet in `test_bot_commands.py`, following existing patterns.

### L6 — `on_message` logs every bot-sent message at INFO, echoing `!logs`/`!history` output back into the log file (which `/status` then re-serves)

**Location:** [src/bot.py](src/bot.py):160–164.

Every `!logs` invocation writes up to ~1900 chars of container log content into `bot.log`, which
`/status` then includes in its `logs` field — content amplification and noise that ages real events
out of the 50-line window and the 5 MB rotation faster.
**Fix:** drop to `logging.debug`, or log only metadata:
`logging.info(f"Bot sent {len(message.content)} chars to {message.channel}")`. Keep `on_command`'s
INFO line as-is — that's the useful audit trail.

### L7 — Dependency pinning drift: `requests` floating in an otherwise fully-pinned runtime set; `pytest`/`pytest-cov`/`ruff` unpinned in dev requirements

**Location:** [requirements.txt](requirements.txt) line 6 (`requests>=2.32.4`); [requirements-dev.txt](requirements-dev.txt) lines 3–5.

Dependabot maintains pins weekly, so floating specs buy nothing and cost reproducibility: a new ruff
release with changed default rules can break CI on a commit that touched nothing, and the shipped
image's `requests` can differ from what CI tested that week.
**Fix:** pin all four at whatever versions resolve today (`requests==…`, `pytest==…`, `pytest-cov==…`,
`ruff==…`); dependabot's weekly pip group takes over from there. (The `>=2.32.4` floor was cycle-1
H2's CVE fix — any pin at or above that version preserves it.)

### L8 — Path-type env vars set to empty string crash or misbehave at startup

**Location:** [src/config.py](src/config.py):45, 47, 86 (`PERMISSIONS_FILE`, `LOG_FILE`, `HISTORY_FILE`).

An explicit `LOG_FILE=` (empty) in `.env` → `.strip()` → `""` → `RotatingFileHandler("")` raises an
unhelpful error at import time; empty `PERMISSIONS_FILE`/`HISTORY_FILE` misbehave similarly later.
**Fix:** apply the `_int_env` philosophy to paths: `os.getenv("LOG_FILE", "").strip() or "data/bot.log"`
for all three.
**Test:** `test_config.py`: reload config with `LOG_FILE=""` → default used (follow the existing
`TestNewConfig` reload pattern).

### L9 — `/status` accepts the token as a `?token=` query parameter — ✅ CLOSED, WON'T FIX

**Location:** [src/api.py](src/api.py):24–34. **No code change. Do not implement.**

**Maintainer decision (2026-07-17): accepted permanently.** The generic objection to query-string
tokens (they land in proxy access logs, browser history, Referer headers) does not buy an attacker
anything in this deployment: the bot runs locally, is not internet-reachable, and any proxy log
capturing the token sits on the same host as `STATUS_TOKEN` itself. Header auth remains documented as
preferred. **Do not re-flag in cycle 3** — this is a closed decision, not an oversight. Revisit only
if the deployment model changes (i.e. `/status` becomes reachable from an untrusted network, or the
proxy tier moves to a different trust boundary than the token).

### L11 — `LOG_LEVEL` is read with `os.getenv` in `bot.py`, bypassing `config.py` entirely *(found during Phase 1 review)*

**Location:** [src/bot.py](src/bot.py):35 — `setup_logging(LOG_FILE, os.getenv("LOG_LEVEL", "INFO"), [...])`.

`LOG_LEVEL` is the only env var the app reads outside `config.py`, directly violating CLAUDE.md's
"Adding a new env var" rule #2 ("Import it from `.config` wherever it's used — don't call
`os.getenv()` in handler code"). It is not parsed or validated in `config.py` at all, so unlike every
other var it gets no fallback warning: an invalid value (`LOG_LEVEL=verbose`) silently resolves to
INFO via `getattr(logging, ..., logging.INFO)` in [src/logging_config.py](src/logging_config.py):42.

Not a bug — both compose files pass it and the fallback is safe — but it's the exact drift M1 exists
to prevent, and it means the env-var inventory in `config.py` is incomplete for anyone auditing it.
**Fix:** parse `LOG_LEVEL = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()` in `config.py`
(warning on an unrecognized level, following the `_int_env` philosophy), import it in `bot.py`, and
drop the `os` import there if it becomes unused (it won't — `os.path.abspath` is used at lines 147–148).
Fold into **Phase 7** with the other config hygiene (L8).
**Test:** `test_config.py` — an invalid `LOG_LEVEL` falls back to INFO with a warning.

### L10 — Role-name-based permissions silently break when a Discord role is renamed *(DECIDED: document only — Phase 7)*

**Location:** [src/permissions.py](src/permissions.py):94–98; the storage format is role *names*.

Renaming "ServerAdmin" in Discord instantly revokes every grant tied to it, with no signal to anyone.
Role IDs are rename-stable, but migrating (store IDs, resolve names on `!perm add`, display names on
`!perm list`) changes the file format and needs a migration path for existing installs.

**Maintainer decision (2026-07-17): document the limitation, no code change.** No role-ID migration
this cycle. **Deliverable:** a note in README's permissions section stating that permissions are
matched by role *name*, and that renaming a Discord role silently revokes its grants and requires
re-granting via `!perm add`. Fold into Phase 7 (docs-only). Known limitation, deliberately accepted —
revisit only if this bot is distributed beyond single-server use, where the migration cost only grows.

---

## CLEANUP / CONSOLIDATION

### C1 — Maintenance-check boilerplate repeated in five handlers; `is_maintenance_active` ignores its argument

**Location:** [src/bot.py](src/bot.py):256–258, 336–338, 536–538, 620–622, 647–649; [src/state.py](src/state.py):33–42.

The same three lines (`if state.is_maintenance_active(ctx.command.qualified_name if ctx.command else "")…`)
appear five times, and `is_maintenance_active`'s own docstring admits the parameter is decorative.
**Fix (two acceptable routes):**
- *Decorator:* a `commands.check` raising a dedicated `MaintenanceActive(commands.CheckFailure)`,
  handled in `on_command_error` with the maintenance message. **Careful:** it must be distinct from
  both the generic permission-denial branch and `SilentCheckFailure`, and the maintenance reply must
  only be sent for allowed origins (checks run after `check_guild`, so ordering already protects this
  — verify, don't assume).
- *Helper:* `async def _bail_if_maintenance(ctx) -> bool` — smaller diff, lower risk.

Either way: drop `is_maintenance_active`'s unused parameter, update call sites, and update
ARCHITECTURE.md's Maintenance-mode section. **Preserve current policy exactly** — note `!cancel`
deliberately does *not* check maintenance (enabling maintenance already cancels everything, and
cancelling during maintenance is harmless) while `!logs`/`!stats` do. This is a refactor, not a
policy change.

### C2 — Crash-baseline re-seed snippet duplicated three times

**Location:** [src/bot.py](src/bot.py):271–273, 377–379, 432–435.

`state.last_known_status[target] = await docker_control.run_blocking(docker_control.container_status, target)`
plus its explanatory comment appears in `start`, the `now` path, and `do_operation`.
**Fix:** extract `async def _reseed_crash_baseline(target: str)` in `bot.py` (the helper just wires
the awaited call; the state write stays a `state` attribute mutation); keep one authoritative comment
at the definition.

### C3 — Dead branch in `resolve_container`

**Location:** [src/bot.py](src/bot.py):106–107.

`ALLOWED_CONTAINERS` can never be empty (config.py:32–33 raises at import), so the "No allowed
containers configured" branch is unreachable. Remove it. (The redundant empty-list guard in
docker_control.py:58–59 **stays** — that layer is deliberately defensive per house rules.)

### C4 — Test hygiene: `test_permissions.py` writes into the repo root; `conftest.py` doesn't redirect `PERMISSIONS_FILE`

**Location:** [tests/test_permissions.py](tests/test_permissions.py):11–16; [tests/conftest.py](tests/conftest.py).

`test_permissions.json` is created/deleted in the CWD (a mid-run crash strands it in the repo), and
`PERMISSIONS_FILE` is the one data-path env var conftest does *not* redirect to tmp. Verified no
current test writes `data/permissions.json` unpatched (its mtime predates the suite run) — but any
future test invoking a real permission check on a non-admin ctx would silently write into the repo.
**Fix:** point `test_permissions.py` at a temp path, and add
`os.environ.setdefault("PERMISSIONS_FILE", os.path.join(tempfile.gettempdir(), "discord-bot-tests-permissions.json"))`
to conftest alongside the existing LOG_FILE/HISTORY_FILE lines (update the conftest comment and
CLAUDE.md's test-env note).

---

## Phase plan (execution order)

One phase per Sonnet worker, reviewed by Fable, one commit each. See the Progress table at the top for
live status.

1. **M1** — compose env passthrough + CLAUDE.md checklist guard. Tiny; unblocks the healthcheck feature.
2. **M2 → M4 → M3** — Docker error paths. M4's correctness depends on M2's exception behavior (see
   M4's ordering note); M3 is the same neighborhood. Implement in that order within the phase.
3. **M5** — atomic writes for permissions/history + non-destructive corruption recovery.
4. **M6** — remaining event-loop I/O behind `run_blocking()`. Mechanical, six call sites, zero
   behavior change. **Re-read the dedup-invariant warning below before touching `_delayed_container_op`.**
5. **L1, L2, L3** — docker_control sanitizer/announce hardening.
6. **L5, L6, C1, C2, C3** — bot.py UX + consolidation. C1 and L5.4 overlap; do C1 first.
7. **L7, L8, L11, C4, L10** — deps/config/test hygiene + the L10 README note (docs-only).
8. **L4** — persist maintenance mode. Reuses M5's atomic-write helper; do not duplicate it.
9. **Docs + architecture accuracy audit** — independent top-to-bottom pass over README, DOCKERHUB.md,
   ARCHITECTURE.md, CLAUDE.md and any diagrams, verified against the code as it stands after phase 8.
   This is *on top of* each phase's own doc updates, not a substitute — it catches pre-existing drift
   and anything that falls between phases.
10. **Final plan.md prune** — remove completed detail, leave only what stays relevant.

**Every phase must:** update the documentation its own change touches (README / DOCKERHUB.md /
ARCHITECTURE.md / CLAUDE.md, wherever the change is user-visible or convention-changing — M1, L4, L10
and C1 all have doc impact); keep `ruff check .` and `ruff format --check .` clean; keep the full
pytest suite green; and update this file's Progress table + compress its finished findings to a
one-line entry.

---

## What's already solid (don't regress these)

- **Input validation layering** — container allowlist + message sanitization enforced inside
  `docker_control`, not just at the command layer; the `/bin/sh -c` template path is safe *because*
  of `_VALID_MSG_CHARS`. Never widen the whitelist.
- **Origin gating** — `_origin_allowed()` shared between `check_guild` and the `CommandNotFound`
  branch; `SilentCheckFailure` keeps foreign-guild/DM presence-leak protection intact. Any new
  error-handling branch (C1's `MaintenanceActive` included) must not respond before an origin check.
- **Pending-op dedup invariant** — the placeholder-before-first-await pattern in
  `_delayed_container_op` (bot.py:389–396) was broken once in cycle 1 by inserting awaits between the
  dedup check and the placeholder insertion. M6 and C1 both touch this function's neighborhood:
  **do not add an `await` between `has_pending_op` (line 385) and the placeholder insertion (line 395).**
- **Mention scoping** — `AllowedMentions.none()` bot-wide, exact-role re-enable in
  `send_announcement`. Never `roles=True`.
- **CI structure** — single reusable test workflow, event-scoped concurrency groups, prune-job
  new-tag protection. All three carry comments explaining production incidents that shaped them; read
  those comments before "simplifying" anything. (Verified this pass: ARCHITECTURE.md's CI section
  matches the workflows as they exist — no drift.)
- **Permissions cache coherence** — `_save` updates `_cache`/`_cache_mtime` in step with the write;
  M5's atomic-write change must keep that ordering (cache update after `os.replace` succeeds).
