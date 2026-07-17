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
| 1 | M1 — compose env passthrough | ✅ done — `fb26b5d` (+ `2ff36ed` DOCKERHUB) |
| 2 | M2 → M4 → M3 — Docker error paths | ✅ done — `18a3798` |
| 3 | M5 — atomic writes | ✅ done — `5a5e2a0` |
| 4 | M6 — event-loop I/O + L12 | ✅ done — `f5550a0` |
| 5 | L1, L2, L3 — docker_control | ✅ done — `9dea118` |
| 6 | L5, L6, C1, C2, C3 — bot.py UX + consolidation | ☐ **next** |
| 7 | L7, L8, L11, C4, L10 — deps/config/test/docs hygiene | ☐ not started |
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
- `PYTHONPATH=. pytest -q tests/` → **215 passed** as of Phase 2 (was 205 at review time; 1 pre-existing `audioop` DeprecationWarning from discord.py — not ours)
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

### M1 — compose env passthrough gaps — ✅ DONE (Phase 1, `fb26b5d`)

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
  Both lists came back empty at `fb26b5d`. **Phase 9 should re-run this** after all code phases land.
- **No automated test was added.** The optional pytest guard (assert every config.py var appears in
  both compose lists) was considered and skipped — the CLAUDE.md checklist plus the snippet above are
  the guard. If this drifts a *second* time, promote it to a real test; a third occurrence means the
  procedural guard doesn't work.
- **L11 was discovered by this cross-check** (`LOG_LEVEL` bypasses config.py) — see below, deferred to Phase 7.

---

### M2 / M4 / M3 — Docker error paths — ✅ DONE (Phase 2, `18a3798`)

Daemon-down now reports honestly instead of "container not found"; `APIError` can no longer escape
into a no-reply; removal fires a crash alert; the health watcher exits on a `None` read. 215 tests
(+10), ARCHITECTURE.md updated ("Docker operations" + "Crash detection loop").

**Residual notes worth keeping:**
- **⚠️ THE PLAN WAS WRONG HERE — carry this forward.** M2 as written said to catch
  `docker.errors.DockerException`. That is insufficient. `_get_client()` caches the client for the
  process lifetime, so the real failure is a daemon that dies *mid-life* — which raises
  `requests.exceptions.ConnectionError`, **not** a `DockerException` subclass. `DockerException` only
  surfaces at client *construction* (which this code does once, at startup). Verified empirically
  against `docker==7.2.0`. Catching only `DockerException` would have left the bug live in production
  while mocked tests passed green. Both are caught via `_DAEMON_CONNECTION_ERRORS` in
  [src/docker_control.py](src/docker_control.py); `test_container_status_requests_connection_error_returns_error_string`
  pins the real path. **Any future code catching docker errors must use that tuple, not
  `DockerException` alone.**
- **`APIError` subclasses BOTH `DockerException` and `RequestException`** (MRO: `APIError → HTTPError →
  RequestException → OSError → DockerException`). So `except docker.errors.APIError` **must** precede
  `except _DAEMON_CONNECTION_ERRORS` — it does in the `Result`-returning functions. The
  `Optional`-returning ones have no separate `APIError` catch, so an API error there collapses into
  `"error"`/`None`; acceptable given their "no data" contract, but know it's deliberate, not an oversight.
- **`container_status` now has a third return value: the literal `"error"`.** Not `None`, not raising.
  Consumers handling it: `crash_check_loop` + `_before_crash_check` (skip the iteration entirely),
  `status_cmd` and `_bail_if_not_running` (explicit "daemon unreachable" reply), `api.py` `/status`
  (passes through as an honest monitoring signal). **Any new `container_status` caller must handle
  `"error"`** or it will render/compare it as if it were a real Docker state.
- **The three crash-baseline re-seed sites (bot.py:289, 414, 470) are deliberately left unguarded.**
  A stored `"error"` is self-correcting: `prev == "error"` never matches `prev == "running"`, so it
  cannot fire a false alert, and it clears on the next poll. The only loss is a crash coinciding
  exactly with a daemon blip at re-seed time — undetectable either way. Verified, not overlooked.
  Note C2 will consolidate these three into one helper — preserve this property.
- **Dedup invariant verified intact** post-change: no `await` between `has_pending_op` (bot.py:420)
  and the placeholder insertion (bot.py:429).
- **Known gap, deferred:** `!status` still renders `**None**` for a not-found container — that's L5.1,
  Phase 6.


### M5 — Non-atomic writes + destructive corruption recovery — ✅ DONE (Phase 3, `5a5e2a0`)

Both writers go through the new `src/atomic_io.py` (`atomic_write_json`): temp file in the same dir →
`json.dump` → `flush` → `fsync` → `os.replace`, temp cleaned up on failure. Permissions keep 0o600 via
explicit `chmod` before the replace. Corrupt files are renamed to `<path>.corrupt`, not deleted.
219 tests (+4). ARCHITECTURE.md updated (Permissions section + directory layout).

**Residual notes worth keeping:**
- **New module `src/atomic_io.py`, deliberately against the "edit existing files" house rule.**
  Rationale (agreed on review): `history.py` has zero project-internal imports; `config.py` has
  import-time side effects (`load_dotenv()`, fail-fast validation) that must not leak into the audit
  log; `state.py` is the BotState singleton, not I/O infra; and `permissions.py` ↔ `history.py`
  importing each other would couple the permission store to the audit log. Stdlib-only leaf module.
  **Phase 8 must reuse this for the maintenance-state file — do not write a fourth JSON writer.**
- **Behavior change:** `history.json` narrows from umask-default (~0o644) to 0o600 (`mkstemp` default;
  history passes no explicit `mode`). Same-user reader and the log holds Discord usernames, so this is
  a tightening. Noted so it isn't rediscovered as a mystery later.
- **A second corruption silently overwrites the first `.corrupt`** (verified). Accepted: the most
  recent corruption is the useful evidence. If forensic history ever matters, timestamp the suffix.
- **No directory fsync after `os.replace`.** Accepted, and the distinction matters: `os.replace` is
  atomic, so a reader can never observe a torn file — M5's actual goal. Without a dir fsync, a power
  loss can revert to the *previous intact* file. Losing the last `!perm add` is recoverable; a corrupt
  permission store is not. Don't "fix" this without a reason.

---

### L12 — A failed `os.replace` in permissions' corruption path makes `_load` raise, silently killing every privileged command *(found during Phase 3 review; pre-existing, not a Phase 3 regression)*

**Location:** [src/permissions.py](src/permissions.py) `_load`'s `except json.JSONDecodeError` branch.

If preserving the corrupt file fails (read-only or permission-broken `data/` dir), the branch logs a
warning and calls `_ensure_file()` — which no-ops, because the corrupt file still exists — then
re-reads it and raises `JSONDecodeError` uncaught. That escapes `is_member_allowed` → the
`has_permission` predicate → `on_command_error`'s logging-only `else`, so **the user gets no reply at
all** and every privileged command silently dies. Same failure shape M2 just fixed for Docker errors.

**Verified pre-existing:** the old `os.remove` version raises identically (tested at `18a3798` by
patching `os.remove` to fail). Phase 3 changed the call, not the shape. Rare — needs a broken data
dir — but the consequence is total, silent loss of privileged commands.

**Fix:** make the recovery path tolerate a failed preserve. After the `os.replace` attempt, if the
file still parses as garbage, fall back to in-memory defaults (`dict(DEFAULT_ALLOWED_ROLES)` per
action) and log at ERROR rather than re-reading and raising. The bot must degrade to defaults, never
to "no privileged commands work and nothing says why".
**Test:** patch `os.replace` to raise `OSError` in `_load`'s corruption path → assert `_load` returns
defaults and does not raise.
**Sequencing:** fold into **Phase 4** (M6 touches this exact call path) or Phase 7. Not urgent, but
don't lose it.

---

### M6 — sync file I/O on the event loop — ✅ DONE (Phase 4, `f5550a0`)

All six call sites now go through `run_blocking()`. The permissions module stays synchronous by
design — `api.py` calls `list_permissions()` from the uvicorn thread. 220 tests (+1 from L12).

**Residual notes worth keeping:**
- **Dedup invariant verified intact** post-change: no `await` between `has_pending_op` (bot.py:422)
  and the placeholder insertion (bot.py:431). The `now`-path edit is inside `if now:`, which returns
  before that region. **Phase 6 (C1/C2) touches this function — re-verify.**
- **One existing test's `run_blocking` double needed updating** (`test_stop_now_allowed_with_role`):
  it dispatches on `func.__name__`, and `is_member_allowed` — now routed through `run_blocking` — is a
  `MagicMock` with no `__name__`. Both assertions unchanged verbatim; mock plumbing, not behavior.
  **Watch for this pattern in later phases:** any test faking `run_blocking` with a `__name__`
  dispatcher will break the moment a new call is routed through it. That's the double being wrong,
  not the code — fix the double, never the assertion.

---

### L12 — `_load` raised on corrupt perms when preserve failed — ✅ DONE (Phase 4, `f5550a0`)

`_load()` now degrades to in-memory defaults + ERROR log instead of raising, so a broken `data/` dir
can no longer silently kill every privileged command.

**Residual notes worth keeping:**
- **The fallback is deliberately uncached.** The disk file is still corrupt, so each call retries the
  preserve-and-reload and the bot self-heals the moment the filesystem clears — no restart needed.
  Verified empirically. Cost: repeated ERROR logs + a retried `os.replace` per privileged command while
  broken. Accepted (rare, operator-visible, and off the event loop thanks to M6).
- **⚠️ Security nuance, accepted but worth knowing:** the fallback returns `DEFAULT_ALLOWED_ROLES` for
  *every* action. If an operator had deliberately *tightened* permissions (e.g. `!perm remove` of a
  role from `stop`), a disk failure silently *restores* that role's access until the disk is fixed.
  This fails toward the operator's configured baseline, not toward "everyone" — and it's logged at
  ERROR — so it was judged the right trade against locking admins out mid-incident. If this ever needs
  to fail closed instead, that's a deliberate policy change, not a bug fix.


## LOW (batch these; grouped by file)

### L1 / L2 / L3 — docker_control announce hardening — ✅ DONE (Phase 5, `9dea118`)

L1 `_sanitize` reordered to `strip().lstrip("-").strip()` (a leading space no longer shields a hyphen);
L2 empty-after-sanitize now returns a failure Result before running the template; L3 exec output
decodes with `errors="replace"`. 224 tests (+4).

**Residual note:** `lstrip("-")` strips only the *leading* run of hyphens — an interior token like
`-rf -x` → `rf -x` keeps the interior `-x`. Safe here: no-placeholder path passes `safe_msg` as a
single argv element (not re-split), and the `/bin/sh -c` path embeds it as literal text. Don't assume
"all hyphens neutralized" if this sanitizer is ever reused for a differently-parsed sink.

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
