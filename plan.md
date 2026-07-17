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
| 6 | L5, L6, C1, C2, C3 — bot.py UX + consolidation | ✅ done — `b898f2f` |
| 7 | L7, L8, L11, C4, L10 — hygiene + docs | ✅ done — `46aedbe` |
| 8 | L4 — persist maintenance mode | ✅ done — `f5d3cb5` |
| 9 | Docs + architecture accuracy audit | ☐ **next** |
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

### L4 — persist maintenance mode — ✅ DONE (Phase 8, `f5d3cb5`)

Maintenance `{mode, reason}` persisted to `MAINTENANCE_FILE` (default `data/maintenance.json`,
gitignored) on every toggle via `atomic_io.atomic_write_json`; loaded once in `main()` before
`bot.run()`. State mutation in state.py; path passed in (no config import). Corrupt/missing file →
off, never crashes startup. Survives a simulated restart (verified). 238 tests (+5). Docs updated
across .env.example, README, DOCKERHUB, ARCHITECTURE, CLAUDE.

### L5 / L6 — bot.py UX + logging — ✅ DONE (Phase 6, `b898f2f`)

L5: `!status` shows "not found" not "**None**"; `!logs 0` clamps to 1; `!start` warns (doesn't block)
on a pending countdown; `!logs` maintenance check moved to top. L6: `on_message` logs a char count, not
full bot-sent content (which was echoing `!logs`/`!history` replies back into `bot.log` → `/status`).

### L7 / L8 / L11 — deps + config hygiene — ✅ DONE (Phase 7, `46aedbe`)

L7: pinned `requests==2.32.5`, `pytest==9.0.2`, `pytest-cov==7.1.0`, `ruff==0.15.11` (verified against
`pip freeze`). L8: empty `PERMISSIONS_FILE`/`LOG_FILE`/`HISTORY_FILE` now fall back to defaults, not `""`.
L11: `LOG_LEVEL` parsed + validated in config.py; `os.getenv` now appears nowhere outside config.py.
233 tests (+6).

### L9 — `/status` accepts the token as a `?token=` query parameter — ✅ CLOSED, WON'T FIX

**Location:** [src/api.py](src/api.py):24–34. **No code change. Do not implement.**

**Maintainer decision (2026-07-17): accepted permanently.** The generic objection to query-string
tokens (they land in proxy access logs, browser history, Referer headers) does not buy an attacker
anything in this deployment: the bot runs locally, is not internet-reachable, and any proxy log
capturing the token sits on the same host as `STATUS_TOKEN` itself. Header auth remains documented as
preferred. **Do not re-flag in cycle 3** — this is a closed decision, not an oversight. Revisit only
if the deployment model changes (i.e. `/status` becomes reachable from an untrusted network, or the
proxy tier moves to a different trust boundary than the token).

### L11 — LOG_LEVEL bypass — ✅ DONE (Phase 7, folded into the L7/L8/L11 entry above).

### L10 — role-name permission limitation — ✅ DONE (Phase 7, `46aedbe`, docs-only)

README permissions section now states permissions match by role *name* and a rename requires
re-granting via `!perm add`. No code change (maintainer decision).

## CLEANUP / CONSOLIDATION

### C1 / C2 / C3 — bot.py consolidation — ✅ DONE (Phase 6, `b898f2f`)

C1: `_bail_if_maintenance(ctx)` replaces five copies; `is_maintenance_active` lost its dead parameter.
C2: `_reseed_crash_baseline(target)` replaces three copies. C3: dead empty-list branch removed from
`resolve_container`. **Maintenance policy verified unchanged** (start/stop/restart/announce/logs/stats
check; cancel/status/maintenance/perm*/guide/history don't). **Dedup invariant re-verified** at the new
line numbers (no await between bot.py:453 and :462). 227 tests. All four doc files updated.

**Residual note:** `test_resolve_container_empty_list` now asserts the "Multiple containers configured"
message for an artificially-patched empty list — it exercises `resolve_container`'s fallback for a
state config.py makes unreachable. Slightly odd but harmless; a future cleanup could just delete the
test since it covers no reachable path.


### C4 — test isolation for PERMISSIONS_FILE — ✅ DONE (Phase 7, `46aedbe`)

conftest now redirects `PERMISSIONS_FILE` to tmp; `test_permissions.py` uses a tempdir path instead of
the repo root. CLAUDE.md test-env note updated.


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
