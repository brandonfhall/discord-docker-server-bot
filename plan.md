# Code Review Findings & Remediation Plan

**Branch:** `Fable-review` · **Reviewed at commit:** `a8c82b8` · **Date:** 2026-07-11
**Reviewer:** Claude Fable 5 (senior review pass) · **Implementers:** hand each finding to an implementing agent (Sonnet/Opus)
**Status: all 22 findings implemented and committed as of `8442cd1`.** See the handoff section immediately below for a final-review entry point.
**Final review (Fable, 2026-07-11): 20/22 verified clean; two follow-ups required — see "Final review" section below (F1: M3/M4 residual in the `CommandNotFound` branch — ✅ fixed; F2: L7+L12 reopened M1's dedup window — ✅ fixed).**

This document is a handoff. Each finding is self-contained: location, root cause, failure scenario,
a prescribed fix (with code sketches), tests to add, and acceptance criteria. Implementers should not
need to re-investigate. Line numbers reference commit `a8c82b8`.

**Baseline at review time (verify before starting, re-verify after every finding):**
- `PYTHONPATH=. pytest -v tests/` → **163 passed** (use `.venv/Scripts/python.exe -m pytest` on the dev box)
- `ruff check .` → clean · `ruff format --check .` → clean
- House rules in [CLAUDE.md](CLAUDE.md) apply to every fix (Docker calls via `run_blocking()`, state in
  `state.py`, `Result` NamedTuple, tests per the conventions table, README + DOCKERHUB.md updated when
  the command surface or env vars change).

**Severity legend:**
- **HIGH** — exploitable trust/security gap or shipping known-vulnerable code; fix first.
- **MEDIUM** — real correctness/security bug reachable in normal operation.
- **LOW** — hardening, consistency, UX, or hygiene; batch these.

---

## Handoff: final-review entry point

Implemented by Sonnet 5 across 10 commits on `Fable-review` (pushed to `origin/Fable-review`), commit
range `f29d843..8442cd1` (parent `ca6d6dc` is Fable's original plan.md commit). Final state: **188 tests
passing** (up from 163 at review time), `ruff check` / `ruff format --check` both clean.

**Commits, in order:**
1. `f29d843` — H2 (requests CVE pin / docker SDK bump)
2. `d00a237` — M3 + M4 (DM handling, silent guild/channel rejection)
3. `a772c21` — H1 (require `DISCORD_GUILD_ID`, **breaking change**)
4. `d2de940` — M1 (pending-op cancel race / placeholder leak)
5. `c108fa8` — M2 (false crash alerts)
6. `cbe0e25` — M6 (scoped announcement mentions)
7. `41aaf0b` — M7 (`.replace()` instead of `.format()`)
8. `2889349` — M5 (`/status` loopback binding, docs)
9. `301b02a` — L-batch A (L2–L7, L13)
10. `c0f188b` — L-batch B (L1, L8–L12)
11. `8442cd1` — final documentation consistency pass (ARCHITECTURE.md, CLAUDE.md)

**What a final review should focus on** (highest-value places to spend review time, rather than
re-deriving what's already below): every finding's own `**Resolution:**` note documents exactly what
was done and why, including several places where the implementation deliberately diverged from the
prescribed fix after finding a concrete reason. Those deviations are the parts most worth a second pair
of eyes:

- **H1** is a breaking change: any deployment currently running without `DISCORD_GUILD_ID` set will
  refuse to start. Confirm the commit message and README/DOCKERHUB changes communicate this clearly
  enough for an operator upgrading blind.
- **L7** was scoped to `!stop` only, excluding `!restart` — verified that Docker's `restart` legitimately
  succeeds on a stopped container (starts it), so gating it on "must be running" would have been a
  regression. Worth confirming that reasoning holds; it's the single largest deviation from the plan's
  literal text in this batch.
- **L5** replaced the prescribed "wire up the check in five more handlers" approach with simplifying
  `is_maintenance_active` instead, after finding an existing test that encoded the old (dead) exempt set
  as a contract at the state layer. Confirm the replacement test
  (`test_maintenance_mode_does_not_block_guide_history_or_perm`) actually verifies the right thing.
  Also, this — plus H1 and M3/M4 both editing `state.is_maintenance_active` and `check_guild` /
  `on_command_error` — means `src/bot.py` and `src/state.py` accumulated changes from multiple findings
  each; worth reading those two files fresh top-to-bottom rather than only diffing per-commit.
- **M5** and **L1** were resolved per explicit maintainer decisions (docs-only for M5, keep-the-query-param
  for L1) rather than the plan's own recommended default — both decisions were asked for and given via
  `AskUserQuestion` mid-session, not assumed.
- **L9** turned up a second, un-diagnosed instance of the same bug it was fixing: `HISTORY_FILE` had the
  identical repo-pollution problem as `LOG_FILE` (confirmed `data/history.json` had grown to 1200+ lines
  from unmocked test calls). Fixed using the same pattern, but it's not in Fable's original finding text —
  worth confirming the fix is complete and no other env-var-backed file path has the same issue
  (`PERMISSIONS_FILE` was checked and is not currently affected, but wasn't exhaustively audited).
- **Two pre-existing gaps** unrelated to any specific finding were noticed and fixed in passing during the
  final documentation pass: `TestHealthzEndpoint` (in `tests/test_api.py`) and `TestGuildLockRequired`
  (new, from H1) were both missing from CLAUDE.md's test-conventions table.
- **Not touched, by design:** `permissions.py`'s internal caching (L12 explicitly said not to refactor it),
  the docker-socket-proxy hardening guide (already correct, no finding touched it), CodeQL/dependabot config.
- **M2's residual race** (a crash-check poll firing during the few seconds a `restart` is actually in
  flight, mid-transition) was left as an accepted, documented gap per the finding's own explicit guidance
  — not a follow-up item, just worth knowing it's intentional if a reviewer notices it.

**Verification commands to re-run before signing off:**
```bash
PYTHONPATH=. pytest -v tests/                      # expect 188 passed
ruff check . && ruff format --check .              # expect clean
docker build . --file Dockerfile --tag bot-test .  # expect success
docker run --rm bot-test pip show requests httpx   # requests present (>=2.32.4), httpx absent
docker run --rm -e BOT_TOKEN=t -e ALLOWED_CONTAINERS=c bot-test python -c "import src.bot"
  # expect ValueError (DISCORD_GUILD_ID/ALLOW_ANY_GUILD required) -- confirms H1's fail-closed behavior
docker compose config --quiet                      # both compose files, if reviewing them too
```

---

## Final review — Fable, 2026-07-11 (post-implementation verification)

**Verdict: 20 of 22 findings fully verified against the code at `685f455`. Two needed a follow-up pass
(F1, F2 below) — both additive, no reverts. F1 and F2 are both now fixed; see their `Resolution` notes.**

**✅ Sign-off (Fable, 2026-07-11, at `1ad38b2`): F1 and F2 verified fixed — all 22 findings plus both
follow-ups are now closed. Verified independently, not from the resolution notes: read both diffs in
full; re-ran the suite (191 passed) and ruff (clean); confirmed all three new regression tests fail
against the pre-fix `src/bot.py` with exactly the expected failure modes (DM → `AttributeError:
guild_permissions`, foreign guild → unexpected `send`, F2 → placeholder absent at the pre-check) and
pass with the fixes; confirmed ARCHITECTURE.md's guild-lock and pending-op-dedup sections now match the
code. Also checked that `CommandNotFound` was the only `on_command_error` branch reachable without
passing `check_guild` — `UserInputError`, `MissingRequiredArgument`, and `CommandOnCooldown` are all
raised in `Command.prepare`/argument parsing, which runs after global checks pass, so no other branch
has F1's problem. The `_origin_allowed()` extraction is the right call: single source of truth for
origin policy, and the F2 ordering comment in `_delayed_container_op` should keep the invariant from
being silently broken a second time. No further findings. Remaining housekeeping only: push the branch
(3 commits ahead of `origin/Fable-review` at sign-off time).**

Verification actually re-run on this box, not taken from the handoff's claims:
- `pytest`: **188 passed**, ruff check + format both clean, `data/` stays empty across runs (L9 confirmed
  empirically, including the `HISTORY_FILE` extension).
- Docker image rebuilt: `requests 2.34.2` present, `httpx` absent (H2/L11 confirmed in the shipped image).
- H1 fail-closed confirmed end-to-end: `docker run` without `DISCORD_GUILD_ID`/`ALLOW_ANY_GUILD` exits
  with the intended `ValueError`. The `a772c21` commit message has a proper `BREAKING CHANGE:` footer and
  a `fix(config)!` marker — communicates the break clearly. (Handoff review-focus item: satisfied.)
- The handoff's other flagged deviations all hold up: **L7**'s restart exclusion is correct (Docker's
  `restart` legitimately starts a stopped container — gating it would be a regression); **L5**'s
  replacement test exercises the real `guide`/`history_cmd`/`perm_list` handlers under
  `maintenance_mode=True`, which is the right contract to test; **M5**/**L1** match the recorded
  maintainer decisions; **M6**'s tests assert the exact `allowed_mentions` object.
- Handoff says "pushed to origin/Fable-review" — true for the 10 fix commits; the final docs commit
  `685f455` is local-only (branch is ahead 1). Push before closing out.

### F1 — M3/M4 residual: the `CommandNotFound` `!perm` branch bypasses every origin check ✅ FIXED

- **Location:** [src/bot.py:187-195](src/bot.py) (`on_command_error`, `CommandNotFound` branch)
- **Problem:** M3/M4's resolution claims DM/foreign-guild safety "by construction" because `check_guild`
  raises `SilentCheckFailure` first. That's true for **registered** commands only. `CommandNotFound` is
  raised by `process_commands` *before* any global check runs (discord.py only invokes `bot.check`
  predicates inside `Command.prepare`, and there is no command to prepare). So a **typo'd** perm command
  (`!perms`, `!permadd`, …) reaches this branch from any origin:
  1. **Via DM:** `ctx.author` is a `discord.User` with no `guild_permissions` → `AttributeError` inside
     the error handler → traceback log spam. This is the *exact* latent bug Fable's M3 finding flagged at
     old line 170 ("if a `!perm` typo arrives via DM") — the fix covered the registered-command path but
     not this one.
  2. **From a foreign guild** (with `DISCORD_GUILD_ID` set): the inviter is an Administrator in their own
     guild, so the bot **replies with the usage line in the foreign guild** — the presence leak M4 exists
     to prevent.
  3. **From a disallowed channel** in the home guild: same usage reply, bypassing the channel lock.
- **Fix (prescribed):** Guard the branch with the same origin conditions `check_guild` enforces, before
  touching `ctx.author`:
  ```python
  elif isinstance(error, commands.CommandNotFound):
      content = ctx.message.content or ""
      if content.startswith(f"{bot.command_prefix}perm"):
          if (
              ctx.guild is not None
              and (not DISCORD_GUILD_ID or ctx.guild.id == DISCORD_GUILD_ID)
              and (not ALLOWED_CHANNEL_IDS or ctx.channel.id in ALLOWED_CHANNEL_IDS)
              and ctx.author.guild_permissions.administrator
          ):
              await ctx.send("Usage: `!perm <add|remove|list> ...`")
  ```
  (Alternatively extract a small `_origin_allowed(ctx) -> bool` helper shared by `check_guild` and this
  branch so the two can't drift; implementer's choice.)
- **Tests** (`tests/test_bot_commands.py`, next to the existing
  `test_on_command_error_command_not_found_*` trio): (a) DM: `ctx.guild = None`, author mock **without**
  `guild_permissions` (use `spec=` or `del ctx.author.guild_permissions` — a bare `MagicMock` would
  auto-create the attribute and mask the bug) → no send, no raise; (b) foreign guild: `ctx.guild.id != 
  DISCORD_GUILD_ID` (patch it set), admin author → no send; (c) existing home-guild-admin test stays green.
- **Acceptance:** all three pass; the three existing CommandNotFound tests unchanged.
- **Resolution:** Extracted `_origin_allowed(ctx) -> bool` in [src/bot.py](src/bot.py) (the alternative
  offered in this finding, taken to keep `check_guild` and the `CommandNotFound` branch from drifting
  apart again) and had `check_guild` call it instead of inlining the three conditions. The
  `CommandNotFound` `!perm` branch now calls `_origin_allowed(ctx)` before touching
  `ctx.author.guild_permissions`, short-circuiting on DM/foreign-guild/disallowed-channel origins exactly
  like `check_guild` does for registered commands. Updated the two existing admin/non-admin
  `CommandNotFound` tests to set a valid origin (`ctx.guild.id`/`ctx.channel.id` matching, `DISCORD_GUILD_ID`
  patched to 0) since they previously relied on `ALLOWED_CHANNEL_IDS=[]` alone and would otherwise now be
  rejected by the new origin check on an unset `ctx.guild.id` MagicMock. Added
  `test_on_command_error_command_not_found_perm_dm_silent` (DM: `ctx.guild = None`, `ctx.author`'s
  `guild_permissions` attribute deleted via `del` rather than left as an auto-vivifying `MagicMock`, so the
  test would actually fail on the old code instead of silently passing) and
  `test_on_command_error_command_not_found_perm_foreign_guild_silent`. Updated the "Guild lock" bullet in
  ARCHITECTURE.md to describe `_origin_allowed()` and explain why `CommandNotFound` needs its own call to
  it. 190 tests pass, ruff clean.

### F2 — L7+L12 reopened the pending-op dedup window M1's design closed; ARCHITECTURE.md now documents an invariant the code no longer holds ✅ FIXED

- **Location:** [src/bot.py:326-336](src/bot.py) (countdown path of `_delayed_container_op`),
  [ARCHITECTURE.md:87](ARCHITECTURE.md)
- **Problem:** ARCHITECTURE.md still says *"A `Future` placeholder is inserted **before** any `await` in
  `_delayed_container_op` so two rapid `!stop` commands can't both pass the `has_pending_op` check."*
  After this batch that's false: two awaits now sit between the `has_pending_op` check (line 326) and the
  placeholder insert (lines 334-336) — L7's `_bail_if_not_running()` (awaits `container_status`) and
  L12's `await run_blocking(history.record, …)`. Two rapid `!stop`s can interleave at either await and
  both pass the dedup check. **Consequences (traced, not speculative):** both run the full announcement
  sequence (duplicate Discord + in-game countdown announcements, duplicate history records); the second
  insert overwrites the first placeholder, so the first invocation's post-announcement identity check
  fails and it tells its user *"The scheduled stop for `X` was cancelled before the countdown completed"*
  — which nobody did. M1's identity check does prevent double *execution* (only one countdown task is
  ever scheduled), which is why this is a correctness/UX bug and not a duplicate-stop bug — but the
  documented invariant is gone, and each finding was individually correct while their composition
  regressed it. Neither the L7 nor L12 resolution notes caught the interaction.
- **Fix (prescribed):** Restore the invariant by inserting the placeholder + `pending_op_info` immediately
  after the `has_pending_op` check, and move the status pre-check and `history.record` await inside the
  cleanup scope:
  ```python
  if state.has_pending_op(target):
      await ctx.send(...duplicate message...)
      return

  placeholder = bot.loop.create_future()
  state.pending_ops[target] = placeholder
  state.pending_op_info[target] = {"action": action, "scheduled_at": datetime.now(timezone.utc)}

  try:
      if await _bail_if_not_running():
          if state.pending_ops.get(target) is placeholder:
              state.cancel_pending(target)
          return
      await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, action, target)
      ...existing announcement awaits...
  except Exception:
      if state.pending_ops.get(target) is placeholder:
          state.cancel_pending(target)
      raise
  ```
  The `now` path needs no change (it doesn't use `pending_ops` for dedup). The existing
  `test_stop_on_already_stopped_container_skips_countdown` asserts `pending_ops` is empty after the bail,
  so the identity-checked cleanup on the bail path is load-bearing — keep it.
- **Tests:** add to `TestPendingOps`: make the mocked `run_blocking` capture
  `state.has_pending_op("test_container")` at the moment `func.__name__ == "container_status"` is called
  (countdown path) and assert it was already `True` — that pins the closed window and fails on the current
  code. Existing M1/L7 tests should pass unchanged.
- **Acceptance:** new test passes; ARCHITECTURE.md:87 is accurate again (re-read it after the code change
  — if the ordering ends up "dedup check → placeholder → status pre-check → announce", the "checks the
  container's current status before announcing anything" bullet at line 90 stays true as written).
- **Resolution:** Implemented as prescribed in [src/bot.py](src/bot.py) `_delayed_container_op`: the
  placeholder `Future` and `pending_op_info` are now inserted immediately after the `has_pending_op` dedup
  check, before `_bail_if_not_running()` or `history.record` run. Both of those, plus the existing
  countdown-announcement awaits, now live inside the same `try` block; the bail branch (not-running) does
  its own identity-checked cleanup (`if pending_ops.get(target) is placeholder: cancel_pending(target)`)
  before returning, and the shared `except Exception` clause (unchanged from M1) still covers the
  announcement-failure case. The `now` path was untouched, per the finding's own note (it doesn't use
  `pending_ops` for dedup). Added `test_placeholder_registered_before_status_precheck` to `TestPendingOps`,
  which captures `state.has_pending_op("test_container")` at the moment the mocked `container_status` call
  fires and asserts it was already `True` — verified this test fails on the pre-fix code
  (`git stash` the `src/bot.py` change and re-run: `AssertionError: False is not true`) before confirming
  it passes with the fix, so it actually pins the regression rather than passing vacuously.
  `test_stop_on_already_stopped_container_skips_countdown` needed a mocked `bot.loop.create_future` added
  (it now reaches the real placeholder-creation code on the not-running path, which it didn't before this
  fix) — also added an assertion that `pending_op_info` is cleaned up alongside `pending_ops` on that path,
  which wasn't previously checked. Updated the "Pending op deduplication" section in ARCHITECTURE.md
  (bullets 87 and 90) to describe the corrected ordering and reference F2 directly, so a future reader
  doesn't need to reconstruct the history to trust the invariant. 191 tests pass, ruff clean.

### Minor notes (no code action required)

- The verification block in the handoff above has a typo: `docker build . --file Dockerfile --tag
  bot-test .` passes the build context twice (trailing `.`). The correct form (used for this review) is
  `docker build . --file Dockerfile --tag bot-test:latest`.
- `logs_cmd` ([src/bot.py:545](src/bot.py)) replies to unrecognized args *before* the maintenance-mode
  check, so a typo'd `!logs` during maintenance gets a usage reply rather than the maintenance message.
  Harmless — arguably the more helpful order — noting it only so the inconsistency is a decision, not an
  accident.
- L9's open question ("no other env-var-backed file path has the same issue") is now verified
  empirically: the full suite leaves `data/` untouched, so `PERMISSIONS_FILE` is genuinely unaffected
  as claimed.

---

## HIGH

### H1 — Multi-guild trust model: anyone who can invite the bot gets full container control ✅ FIXED

- **Category:** security / privilege escalation
- **Location:** [src/bot.py:47-57](src/bot.py) (`check_guild`), [src/bot.py:60-67](src/bot.py) (`has_permission`), [src/config.py:44](src/config.py), [.env.example:14](.env.example)
- **Problem:** `DISCORD_GUILD_ID` defaults to `0` (unlocked). Three mechanisms compound:
  1. Any Discord user can generate an OAuth invite URL for the bot's client ID and invite the bot into
     a guild **they** own. Nothing stops the bot from operating there.
  2. In their own guild they hold the Administrator permission, and `has_permission` short-circuits on
     `ctx.author.guild_permissions.administrator` — full bypass of the role permission store.
  3. Even without admin, permissions are matched by **role name** ([src/permissions.py:94-98](src/permissions.py)):
     creating a role named `ServerAdmin` in any guild grants every default action.
- **Failure scenario:** Bot is deployed with `DISCORD_GUILD_ID` unset (the `.env.example` default). A user
  who shares any server with the bot grabs the client ID from the bot's profile, invites it to their own
  throwaway guild, and issues `!stop now` / `!restart` against the real game servers. Full control, no
  audit trail they can't fake a username around.
- **Fix (prescribed):** Fail closed at startup rather than silently running unlocked.
  1. In [src/config.py](src/config.py), after parsing `DISCORD_GUILD_ID`, add an explicit opt-out env var:
     ```python
     ALLOW_ANY_GUILD = (os.getenv("ALLOW_ANY_GUILD") or "").strip().lower() in ("1", "true", "yes")
     if not DISCORD_GUILD_ID and not ALLOW_ANY_GUILD:
         raise ValueError(
             "DISCORD_GUILD_ID is not set. Anyone able to invite this bot to their own server "
             "would gain container control. Set DISCORD_GUILD_ID, or set ALLOW_ANY_GUILD=true "
             "to accept that risk explicitly."
         )
     ```
  2. Follow the "Adding a new env var" checklist in CLAUDE.md for `ALLOW_ANY_GUILD` (config.py,
     `.env.example`, README table, DOCKERHUB.md table). Not a secret; no redaction needed.
  3. Move `DISCORD_GUILD_ID` from "optional" to "required (or explicit opt-out)" wording in README's
     env table + Security section, and in DOCKERHUB.md's quick-start (it already shows it set — keep).
  4. Note in README Security section that permissions are matched by role **name**, so the guild lock is
     the boundary that makes that safe.
- **Tests:** In `tests/test_config.py` (pattern: existing `TestNewConfig` uses `importlib.reload` with
  patched env): (a) unset `DISCORD_GUILD_ID` + unset `ALLOW_ANY_GUILD` → `ValueError` on import;
  (b) unset guild + `ALLOW_ANY_GUILD=true` → loads; (c) `DISCORD_GUILD_ID=123` → loads.
  `tests/conftest.py` must set `DISCORD_GUILD_ID` (or `ALLOW_ANY_GUILD`) via `os.environ.setdefault`
  next to `BOT_TOKEN` so the whole suite still imports. Also update the CI env block in
  [.github/workflows/tests-reusable.yml:33-35](.github/workflows/tests-reusable.yml) and the smoke-test
  `docker run` line 44 to pass one of them.
- **Acceptance:** Startup refuses to run unlocked unless explicitly opted out; suite + smoke test green;
  README/DOCKERHUB/.env.example updated. **Breaking change for existing deployments — call it out in the
  commit message body.**
- **Resolution:** Added `ALLOW_ANY_GUILD` to [src/config.py](src/config.py); raises `ValueError` at import
  time if `DISCORD_GUILD_ID` is unset and `ALLOW_ANY_GUILD` isn't truthy. Confirmed via Docker: the
  container fails to start without either var set, and starts cleanly with `DISCORD_GUILD_ID` set OR with
  `ALLOW_ANY_GUILD=true`. Updated `tests/conftest.py` to set a default `DISCORD_GUILD_ID` for the whole
  suite, added `TestGuildLockRequired` in `tests/test_config.py` (3 new tests using `importlib.reload` —
  no existing reload-based config test pattern existed, so this establishes one), and updated the CI
  `tests-reusable.yml` env block and Docker smoke-test line. Docs updated: README (Quick Start, env table,
  Security section), DOCKERHUB.md (env table, Security callout), `.env.example`, both compose files'
  environment passthrough lists, and CLAUDE.md's "Test env vars" line. 167 tests pass, ruff clean.
  **This is a breaking change** — any existing deployment running without `DISCORD_GUILD_ID` set will
  fail to start until the operator sets it or sets `ALLOW_ANY_GUILD=true`.

### H2 — Dockerfile pins `requests<2.32.0`, shipping a version with known CVEs; image deps diverge from CI-tested deps ✅ FIXED

- **Category:** security / vulnerable dependency
- **Location:** [Dockerfile:9-13](Dockerfile), [requirements.txt](requirements.txt)
- **Problem:** The image does `pip install "requests<2.32.0"` after installing requirements, forcing
  requests 2.31.0. That predates the fix for **CVE-2024-35195** (`verify=False` persisting across a
  `Session`, fixed in 2.32.0) and **CVE-2024-47081** (`.netrc` credential leak, fixed in 2.32.4). The
  workaround targets [docker-py#3256](https://github.com/docker/docker-py/issues/3256), which was a
  requests-2.32.0-era breakage that newer docker SDK releases resolved. Second problem: CI installs
  `requirements.txt` **without** the pin, so unit tests exercise a different dependency set than the
  shipped image — only the smoke test runs against what users actually get.
- **Fix (prescribed):**
  1. Bump `docker==7.1.0` in [requirements.txt](requirements.txt) to the latest release on PyPI
     (check `pip index versions docker`; anything ≥ 7.1.0 released after mid-2024 supports requests ≥ 2.32).
  2. Delete the extra `pip install "requests<2.32.0"` line and the workaround comment from the Dockerfile.
  3. If reproducibility of transitive deps matters, add an explicit `requests>=2.32.4` line to
     requirements.txt instead of leaving it floating — but do **not** re-pin below 2.32.
  4. Rebuild and run the existing startup smoke test locally:
     `docker build . -t bot-test && docker run --rm -e BOT_TOKEN=t -e ALLOWED_CONTAINERS=c -e DISCORD_GUILD_ID=1 bot-test python -c "import src.bot"`
     (add the guild var if H1 lands first).
- **Tests:** No new unit tests; the CI Docker build + startup smoke test in
  [.github/workflows/tests-reusable.yml:40-44](.github/workflows/tests-reusable.yml) is the gate.
  Run the full pytest suite after the docker SDK bump — `tests/test_docker_control.py` mocks the SDK
  surface and will catch API drift.
- **Acceptance:** Image contains requests ≥ 2.32.4 (`docker run --rm bot-test pip show requests`);
  no version pins exist in the Dockerfile that aren't in requirements.txt; suite + smoke test green.
- **Resolution:** Bumped `docker` to `7.2.0` in requirements.txt (confirmed via wheel metadata:
  `Requires-Dist: requests>=2.26.0`, no upper bound). Added explicit `requests>=2.32.4` floor to
  requirements.txt. Removed the workaround pin + comment from the Dockerfile. Verified locally: `pytest`
  163 passed, `ruff check`/`ruff format --check` clean, Docker image built successfully, `pip show requests`
  inside the image reports `2.34.2`, and the startup smoke test (`python -c "import src.bot"`) passed.

---

## MEDIUM

### M1 — Pending-op race: a cancel/maintenance during the announcement phase is silently overwritten; an exception leaks the placeholder and bricks the container's stop/restart ✅ FIXED

- **Category:** correctness / race condition
- **Location:** [src/bot.py:269-298](src/bot.py) (`_delayed_container_op`, non-`now` path)
- **Problem:** Two related defects around the placeholder `Future` inserted at line 274:
  1. **Cancel is undone.** Between line 274 and line 298 the handler awaits `ctx.send`,
     `send_announcement`, and `announce_in_game` (Docker exec — can take seconds). If `!cancel`,
     `!stop now`, or `!maintenance on` runs in that window, `state.cancel_pending()` pops and cancels
     the placeholder — but line 298 then **unconditionally** assigns the real countdown task. The
     stop/restart proceeds even though the user was told it was cancelled (and even during maintenance
     mode, since `do_operation` never re-checks it).
  2. **Placeholder leak.** If any of those awaits raises (e.g. `ctx.send` → `discord.Forbidden`), the
     placeholder Future stays in `state.pending_ops` forever and is never done, so `has_pending_op`
     returns `True` permanently: every future `!stop`/`!restart` for that container is rejected as a
     "duplicate" until the bot restarts or someone runs `!cancel`.
  3. Cosmetic but fix together: `pending_op_info` is only set at line 297, so `!status` during the
     announcement window hits the info-less fallback branch ([src/bot.py:367-368](src/bot.py)).
- **Fix (prescribed):** Keep a reference to the placeholder; set `pending_op_info` alongside it; wrap the
  announcement awaits; verify the placeholder is still ours before scheduling:
  ```python
  placeholder = bot.loop.create_future()
  state.pending_ops[target] = placeholder
  state.pending_op_info[target] = {"action": action, "scheduled_at": datetime.now(timezone.utc)}

  try:
      delay_str = _format_delay(SHUTDOWN_DELAY)
      countdown_msg = countdown_msg_tpl.format(delay=delay_str)
      await ctx.send(f"Server {target} will {action} in {delay_str} (countdown started).")
      await send_announcement(ctx, countdown_msg)
      await docker_control.run_blocking(docker_control.announce_in_game, target, countdown_msg)
  except Exception:
      if state.pending_ops.get(target) is placeholder:
          state.cancel_pending(target)
      raise

  if state.pending_ops.get(target) is not placeholder or placeholder.cancelled():
      await ctx.send(f"The scheduled {action} for `{target}` was cancelled before the countdown completed.")
      return

  state.pending_ops[target] = bot.loop.create_task(do_operation())
  ```
  Notes for the implementer: `scheduled_at` should be captured when the countdown message is computed so
  `!status` remaining-time math stays right; `state.cancel_pending` already handles Futures
  (`Future.cancel()` is valid). Don't move `history.record` — recording the *attempt* is correct.
- **Tests:** In `tests/test_bot_commands.py` (patterns exist in `TestPendingOps`, which already tests the
  post-registration duplicate at its `test_second_stop_rejected_after_first_registers_task`):
  (a) make `send_announcement`'s `ctx.send` (or the mocked `announce_in_game`) raise → assert
  `state.pending_ops` does not contain the target afterwards;
  (b) simulate cancel-during-announcement: patch `announce_in_game`'s run_blocking mock with a side
  effect that calls `state.cancel_all_pending()` → assert no task is scheduled, no countdown fires, and
  the "was cancelled" message was sent;
  (c) assert `!status` during the announcement phase (placeholder present, info set) reports the action
  and remaining time.
- **Acceptance:** All three new tests pass; existing `TestPendingOps`/`TestCancelCommand`/
  `TestMaintenanceMode` tests unchanged and green.
- **Resolution:** Implemented as prescribed in [src/bot.py](src/bot.py) `_delayed_container_op`: the
  placeholder is captured in a local variable, `pending_op_info` is now set alongside it (before the
  announcement awaits, not after), the announcement awaits are wrapped in try/except that cleans up the
  placeholder and re-raises on failure, and a post-announcement identity check (`pending_ops.get(target)
  is not placeholder or placeholder.cancelled()`) bails with a "was cancelled" message instead of
  unconditionally scheduling the real countdown task. Added three new tests to `TestPendingOps` in
  `tests/test_bot_commands.py`: exception-during-announcement cleanup, cancel-during-announcement
  preventing scheduling, and an ordering test confirming `pending_op_info` is populated before the
  announcement completes. Two existing tests in the same class (`test_stop_proceeds_and_registers_task_
  when_no_pending_op`, `test_second_stop_rejected_after_first_registers_task`) needed their `mock_loop`
  updated to return a real `asyncio.Future` from `create_future()` — the fix now calls `.cancelled()`/
  `.done()` on the placeholder, which a bare `MagicMock` answers as truthy and would have broken the
  happy path. Also added `state.pending_op_info.clear()` to `TestPendingOps`'s `setUp`/`tearDown` (the
  class's own hygiene; the global conftest gap is still L9). 170 tests pass, ruff clean.

### M2 — Bot-initiated `!stop`/`!restart` triggers a false "Crash Alert" ✅ FIXED

- **Category:** correctness / false alerting
- **Location:** [src/bot.py:176-198](src/bot.py) (`crash_check_loop`), stop paths at
  [src/bot.py:262](src/bot.py) (immediate) and [src/bot.py:287](src/bot.py) (`do_operation`)
- **Problem:** `crash_check_loop` alerts on any `running → non-running` transition. Nothing updates
  `state.last_known_status` when the **bot itself** stops a container, so every successful `!stop`
  (immediate or countdown) is followed—within `CRASH_CHECK_INTERVAL` seconds—by
  "**Crash Alert:** Container `X` is now **exited**". Confirmed: no code path outside the loop writes
  `last_known_status`, and no test covers this (tests/test_crash_alerting.py treats every transition as
  a crash).
- **Failure scenario:** Admin runs `!stop`; 30 s later the announce channel gets a crash alert; operators
  learn to ignore crash alerts, defeating the feature.
- **Fix (prescribed):** After each successful bot-initiated container operation, re-seed the loop's
  baseline. In `_delayed_container_op` (both the immediate path and inside `do_operation`) and in the
  `start` handler, after `res`/`result` comes back with `success=True`:
  ```python
  state.last_known_status[target] = await docker_control.run_blocking(docker_control.container_status, target)
  ```
  This records `exited` after a stop (no alert) and `running` after start/restart. Do it via the status
  call rather than hardcoding strings so restart landing back in `running` is captured accurately.
  **Known residual race (accept it, document in a comment):** if the poll fires during the few seconds a
  blocking `restart` is in flight it can still observe a transient non-running status. If the implementer
  wants to close it fully, the pattern is a suppression window in `BotState`
  (`self.alert_suppressed_until: dict[str, float]`) set before the op and checked in the loop — optional.
- **Tests:** `tests/test_crash_alerting.py`: after simulating a bot-initiated stop (set
  `state.last_known_status[name] = "exited"` the way the handler now does), run one loop iteration with
  the container `exited` → assert **no** alert. In `tests/test_bot_commands.py` `TestStopNow`: assert
  `state.last_known_status` is updated after a successful `!stop now`.
- **Acceptance:** New tests pass; the four existing crash-alerting tests still pass unmodified (genuine
  crashes still alert).
- **Resolution:** Added a `state.last_known_status[target] = await docker_control.run_blocking(docker_
  control.container_status, target)` re-seed after every successful bot-initiated operation in
  [src/bot.py](src/bot.py): the `start` handler, the immediate (`now`) path in `_delayed_container_op`,
  and inside `do_operation` for the countdown path -- each gated on `res.success`/`result.success` so a
  failed operation doesn't paper over a real problem. Added `test_no_alert_after_bot_initiated_stop` to
  `tests/test_crash_alerting.py` and `test_stop_now_reseeds_crash_alerting_baseline` to
  `tests/test_bot_commands.py`. Updated `test_stop_now_sends_announcements`, which asserted an exact
  `run_blocking` call sequence (`["announce_in_game", "stop_container"]`) that the new `container_status`
  call extends to three. 172 tests pass, ruff clean. The residual race noted in the finding (a poll firing
  mid-restart) was left as documented, not implemented -- accepted per the finding's own guidance.

### M3 — Commands in DMs: permission-checked commands crash; `!status`/`!guide` answer anyone who shares a guild ✅ FIXED

- **Category:** security / robustness
- **Location:** [src/bot.py:47-57](src/bot.py) (`check_guild`), [src/bot.py:62](src/bot.py),
  [src/bot.py:170](src/bot.py), [src/permissions.py:97](src/permissions.py)
- **Problem:** When `DISCORD_GUILD_ID` is unset and `ALLOWED_CHANNEL_IDS` is empty, `check_guild` passes
  for DMs (`ctx.guild is None`). In a DM `ctx.author` is a `discord.User`, which has **no**
  `guild_permissions` and **no** `roles` attributes:
  - Permission-checked commands (`!stop`, `!start`, …) raise `AttributeError` inside the check → noisy
    unhandled-error tracebacks in the logs on every attempt (fails closed, but ugly and log-spamming).
  - Unchecked commands work: any user sharing any guild with the bot can DM `!status` / `!guide` and
    read container states.
  - Same latent `AttributeError` at [src/bot.py:170](src/bot.py) (`on_command_error`'s `!perm` branch)
    if a `!perm` typo arrives via DM.
- **Fix (prescribed):** Reject DMs globally, first thing in `check_guild`:
  ```python
  @bot.check
  async def check_guild(ctx):
      # Never accept commands via DM — permission checks are guild-role based.
      if ctx.guild is None:
          return False
      ...
  ```
  This also makes every downstream `ctx.author.guild_permissions` / `.roles` access safe by construction.
  (H1 reduces exposure but doesn't remove it: DMs from home-guild members would still hit this without
  the explicit check.)
- **Tests:** `tests/test_bot_commands.py` has `test_check_guild_dm_with_guild_restriction` covering the
  locked case — add the sibling: `ctx.guild = None` with `DISCORD_GUILD_ID` **unset** →
  `check_guild` returns `False`.
- **Acceptance:** New test passes; existing guild/channel-lock tests green; a DM'd command produces no
  response and no traceback.
- **Resolution:** Implemented together with M4 (see below) — `check_guild` now raises `SilentCheckFailure`
  for `ctx.guild is None` before any other check, so DMs never reach `has_permission`/`ctx.author.roles`.
- **⚠️ Final review:** Incomplete — the `CommandNotFound` `!perm` branch in `on_command_error` never
  passes through `check_guild` (unknown commands skip global checks), so the DM `AttributeError` this
  finding called out at old line 170 is still reachable via a typo'd `!perm` command. See **F1** in the
  final-review section at the top.

### M4 — Guild-lock rejections reply "You do not have permission…" in foreign guilds, leaking the bot's presence ✅ FIXED

- **Category:** security / information disclosure (contradicts documented behavior)
- **Location:** [src/bot.py:138-144](src/bot.py) (`on_command_error`), ARCHITECTURE.md "Guild lock" bullet
- **Problem:** `on_command_error` only stays silent for `CheckFailure` when the **channel** lock rejected
  the command. When the **guild** lock (`DISCORD_GUILD_ID` set, command from another guild) rejects it —
  and `ALLOWED_CHANNEL_IDS` is empty — control falls through to
  `await ctx.send("You do not have permission to use this command.")`, in the foreign guild.
  ARCHITECTURE.md claims disallowed origins are silently ignored; only the channel case is.
- **Fix (prescribed):** Make the origin checks distinguishable from real permission denials with a custom
  exception instead of inferring from config in the error handler:
  ```python
  class SilentCheckFailure(commands.CheckFailure):
      """Raised when the command origin (guild/channel) is disallowed — never respond."""

  @bot.check
  async def check_guild(ctx):
      if ctx.guild is None:                      # M3
          raise SilentCheckFailure()
      if DISCORD_GUILD_ID and ctx.guild.id != DISCORD_GUILD_ID:
          raise SilentCheckFailure()
      if ALLOWED_CHANNEL_IDS and ctx.channel.id not in ALLOWED_CHANNEL_IDS:
          raise SilentCheckFailure()
      return True
  ```
  In `on_command_error`, handle `SilentCheckFailure` (just `return`) **before** the generic
  `CheckFailure` branch, and drop the now-redundant `ALLOWED_CHANNEL_IDS` re-check. Note
  `perm_error` ([src/bot.py:638](src/bot.py)) already returns on `CheckFailure`; subclassing keeps that
  correct. Implement M3+M4 together — they touch the same function.
- **Tests:** `tests/test_bot_commands.py`: invoke `on_command_error` with a `SilentCheckFailure` →
  `ctx.send` not called; with a plain `CheckFailure` (permission denial) → the denial message is sent.
  Existing channel-lock silence test must stay green (behavior unchanged, mechanism different).
- **Acceptance:** Foreign-guild and disallowed-channel commands produce zero responses; real permission
  denials in the home guild still get the denial message; ARCHITECTURE.md bullet updated to say guild
  **and** channel rejections are silent.
- **Resolution:** Added `SilentCheckFailure(commands.CheckFailure)` in [src/bot.py](src/bot.py). `check_guild`
  now raises it for DM / foreign-guild / disallowed-channel origins instead of returning `False` or relying
  on `on_command_error` to re-infer the reason from config. `on_command_error` checks `isinstance(error,
  SilentCheckFailure)` first (silent return) before the generic `CheckFailure` branch (permission-denial
  message); the old ad hoc `ALLOWED_CHANNEL_IDS` re-check inside the error handler was removed. Updated
  three existing `check_guild` unit tests that asserted `assertFalse(...)` to `assertRaises(SilentCheckFailure)`
  instead (the function's contract changed from return-False to raise-on-reject), added a new DM+unlocked-guild
  test, and updated `test_on_command_error_silent_in_disallowed_channel` to construct a `SilentCheckFailure`
  rather than a plain `CheckFailure` (mechanism changed from config-inference to exception type). Also updated
  the "Guild lock" bullet in ARCHITECTURE.md. 164 tests pass, ruff clean.
- **⚠️ Final review:** One residual origin leak remains via the `CommandNotFound` `!perm` branch, which
  bypasses `check_guild` entirely (foreign-guild admins get a usage reply). See **F1** in the
  final-review section at the top.

### M5 — `/status` API: open by default, published on all interfaces, and it exposes logs + the permission map ✅ FIXED (docs-only, per maintainer decision)

- **Category:** security / exposure defaults
- **Location:** [src/api.py:29-34, 47-72, 76](src/api.py), [docker-compose.yml:41-42](docker-compose.yml),
  [DOCKERHUB.md:26-27](DOCKERHUB.md)
- **Problem:** Three defaults compound: `STATUS_TOKEN` unset ⇒ no auth (`verify_token` returns
  immediately); uvicorn binds `0.0.0.0` (necessary inside a container); and both compose examples map
  `"8000:8000"`, publishing on **every host interface**. The payload isn't just container states — it
  includes the **last 50 log lines** (Discord usernames, user IDs, channel names, every command typed)
  and the full **permission map** (role names per action). On a VPS with no firewall this is
  internet-readable. The startup warning at [src/bot.py:119-120](src/bot.py) is good but easy to miss.
- **Fix (prescribed):** Documentation/deployment-default change, not code:
  1. Change the `ports` mapping in [docker-compose.yml](docker-compose.yml) and the DOCKERHUB.md
     quick-start to `"127.0.0.1:8000:8000"`, with a comment: *"exposes the status API to the docker host
     only; change to `8000:8000` and set STATUS_TOKEN to expose it beyond localhost"*.
  2. README "HTTP Status API" + Security sections: state explicitly that `/status` includes recent log
     lines and the permission map, and that `STATUS_TOKEN` should be considered required whenever the
     port is reachable beyond localhost.
  3. Optional code hardening (implementer's discretion): when `STATUS_TOKEN` is unset, omit `logs` and
     `permissions` from the response and add `"note": "set STATUS_TOKEN to enable logs/permissions"`. If
     taken, update `tests/test_api.py::test_status_returns_expected_structure` (it currently patches
     `STATUS_TOKEN=None` and asserts the full open payload).
  4. `docker-compose.dev.yml` may keep `8000:8000` (dev), but add the localhost note there too.
- **Tests:** If option 3 is taken: unset-token → response has `containers` but not `logs`/`permissions`;
  with token → full payload. Otherwise docs-only, no tests.
- **Acceptance:** Fresh `docker compose up` on a host exposes nothing off-box by default; README/
  DOCKERHUB updated consistently.
- **Resolution:** Maintainer chose the docs-only path (option 3's code hardening was declined). Changed
  `docker-compose.yml`'s port mapping to `127.0.0.1:8000:8000` with an inline comment explaining why and
  how to widen it. `docker-compose.dev.yml` keeps `8000:8000` for local dev convenience per the finding's
  own guidance, with a comment pointing at the production default. Updated DOCKERHUB.md's quick-start
  port mapping and Security callout, and README's "HTTP Status API" section (payload contents spelled
  out, header auth marked preferred) and Security section (loopback default + STATUS_TOKEN guidance).
  Validated both compose files with `docker compose config --quiet`. 175 tests pass, ruff clean (no test
  changes needed -- docs/compose only).

### M6 — Announcements re-enable pinging **all** roles; user-supplied maintenance reason can ping arbitrary roles ✅ FIXED

- **Category:** security / mention injection
- **Location:** [src/bot.py:104](src/bot.py) (`send_announcement`), [src/bot.py:562](src/bot.py)
  (maintenance reason interpolated into an announcement)
- **Problem:** The bot is correctly constructed with `AllowedMentions.none()`, but `send_announcement`
  passes `allowed_mentions=discord.AllowedMentions(roles=True)` — which re-enables mentions for **every
  role**, not just `ANNOUNCE_ROLE_ID`. `!maintenance on <reason>` interpolates the free-text reason into
  the announcement, so anyone with the `maintenance` permission can embed `<@&ROLE_ID>` and mass-ping any
  role in the server (`@everyone`/user pings stay blocked by the client default merge — role pings are the
  only gap).
- **Fix (prescribed):** Scope the re-enable to exactly the configured role:
  ```python
  allowed = (
      discord.AllowedMentions(roles=[discord.Object(id=ANNOUNCE_ROLE_ID)])
      if ANNOUNCE_ROLE_ID
      else discord.AllowedMentions.none()
  )
  await target_channel.send(content, allowed_mentions=allowed)
  ```
  (When `ANNOUNCE_ROLE_ID` is 0 the content contains no intentional mention, so `none()` is correct.)
- **Tests:** `tests/test_bot_commands.py` has send_announcement coverage near
  `test_send_announcement_send_failure_is_logged` — add: with `ANNOUNCE_ROLE_ID` patched to a value,
  assert the `allowed_mentions` kwarg passed to `channel.send` has `roles == [Object(id=...)]` (compare
  `.roles[0].id`); with it 0, assert an everything-off AllowedMentions.
- **Acceptance:** New tests pass; a maintenance reason containing `<@&123>` renders as text, while the
  configured announce role still pings. Update the "Ping control" bullet in ARCHITECTURE.md.
- **Resolution:** Implemented exactly as prescribed in [src/bot.py](src/bot.py) `send_announcement`.
  Added two tests asserting the exact `allowed_mentions` object passed to `channel.send`: scoped to
  `[Object(id=ANNOUNCE_ROLE_ID)]` when set, `AllowedMentions.none()` (roles=False) otherwise. Updated the
  "Ping control" bullet in ARCHITECTURE.md. 174 tests pass, ruff clean.

### M7 — `CONTAINER_MESSAGE_CMD` templates with any brace besides `{message}` crash `announce_in_game` ✅ FIXED

- **Category:** correctness / robustness
- **Location:** [src/docker_control.py:184-187](src/docker_control.py)
- **Problem:** The placeholder path calls `CONTAINER_MESSAGE_CMD.format(message=safe_msg)`. `str.format`
  interprets **all** braces: a template like `rcon-cli tellraw @a {"text":"{message}"}` (Minecraft
  tellraw — a realistic config) raises `KeyError: '"text"'`; a stray `{` raises `ValueError`. The call
  happens **outside** the `try` at line 189, so it propagates out of `run_blocking` into the command
  handlers as an unhandled error instead of a `Result`. Confirmed untested: docker_control tests only
  cover a clean `{message}` template and the no-placeholder argv path.
- **Fix (prescribed):** Replace, don't format:
  ```python
  if "{message}" in CONTAINER_MESSAGE_CMD:
      cmd = ["/bin/sh", "-c", CONTAINER_MESSAGE_CMD.replace("{message}", safe_msg)]
  ```
  `safe_msg` cannot contain braces (`_VALID_MSG_CHARS` strips them), so `.replace` is exact and cannot
  inject further placeholders. No behavior change for existing simple templates.
- **Tests:** `tests/test_docker_control.py`: patch `CONTAINER_MESSAGE_CMD` to
  `'tellraw @a {"text":"{message}"}'` → assert `announce_in_game` returns a `Result` and the exec cmd
  contains the substituted message with the other braces intact.
- **Acceptance:** New test passes; the two existing announce tests pass unchanged.
- **Resolution:** Replaced `.format(message=safe_msg)` with `.replace("{message}", safe_msg)` in
  [src/docker_control.py](src/docker_control.py) `announce_in_game`, exactly as prescribed. Added
  `test_announce_in_game_template_with_extra_braces` in `tests/test_docker_control.py` using a Minecraft
  `tellraw` template with a JSON payload brace, asserting the exec command substitutes correctly instead
  of raising. 175 tests pass, ruff clean.

---

## LOW (batch these; several touch the same files)

### L1 — `/status` accepts the token as a query parameter ✅ FIXED
[src/api.py:26-32](src/api.py). Query strings land in proxy/access logs and browser history. Keep header
auth as primary; either drop the query param (breaking — check with maintainer) or keep it and add a
README note that the header form is preferred. If dropped, update
`tests/test_api.py::test_status_accepts_token_via_query_param`.
- **Resolution:** Maintainer chose to keep the query param (no breaking change) and document the header
  form as preferred. This was folded into the M5 commit's README update (the "HTTP Status API" section
  now reads "Header: `X-Auth-Token: <token>` (preferred — doesn't land in proxy/access logs)"). No code or
  test changes.

### L2 — `RedactingFilter` doesn't redact exception tracebacks ✅ FIXED
[src/logging_config.py:15-22](src/logging_config.py). Only `record.getMessage()` is redacted; a token
inside an exception message (`exc_info`) reaches handlers verbatim. Fix inside `filter()`:
```python
if record.exc_info and not record.exc_text:
    import traceback
    text = "".join(traceback.format_exception(*record.exc_info))
    for token in self._tokens:
        text = text.replace(token, "[REDACTED]")
    record.exc_text = text
    record.exc_info = None
```
(Formatters prefer `exc_text` when set.) Test in `tests/test_logging.py`: log an exception whose message
contains the token; assert the formatted handler output has `[REDACTED]`.
- **Resolution:** Implemented exactly as prescribed in [src/logging_config.py](src/logging_config.py).
  Added `test_redacts_token_in_exception_traceback` in `tests/test_logging.py`.

### L3 — No bounds validation on integer env vars ✅ FIXED
[src/config.py:8-17](src/config.py). `CRASH_CHECK_INTERVAL=0` → `tasks.loop(seconds=0)` busy-loop
hammering the Docker socket; `DOCKER_MAX_WORKERS=0` → `ThreadPoolExecutor` raises `ValueError` at import
with a confusing traceback; negative `SHUTDOWN_DELAY`/`COMMAND_COOLDOWN` are nonsense. Add a
`minimum: int | None = None` param to `_int_env` that falls back to the default (with the existing
stderr warning) when `value < minimum`. Apply: `DOCKER_MAX_WORKERS` min 1, `CRASH_CHECK_INTERVAL` min 5,
`SHUTDOWN_DELAY` min 0, `COMMAND_COOLDOWN` min 0, `STATUS_PORT` min 1. Tests in `tests/test_config.py`
mirroring the existing invalid-int tests.
- **Resolution:** Implemented as prescribed in [src/config.py](src/config.py). Added 4 tests to
  `tests/test_config.py` covering the `minimum` parameter directly (below/at/no minimum, warning printed).
  Did not add reload-based tests for each individual module-level constant (`DOCKER_MAX_WORKERS`, etc.) --
  the `_int_env` helper tests cover the logic; the module-level assignments are one-line, obviously-correct
  usages of it.

### L4 — `!perm add`/`!perm remove` aren't recorded in the audit history ✅ FIXED
[src/bot.py:582-601](src/bot.py). Permission changes are the most audit-worthy action the bot has.
Add `history.record(HISTORY_FILE, ctx.author, f"perm add {action} {role_name}", "")` (and the remove
equivalent) after the mutation. Test: assert `history.record` called (mock it as other handler tests do).
- **Resolution:** Implemented exactly as prescribed in [src/bot.py](src/bot.py) `perm_add`/`perm_remove`.
  Updated `test_perm_add_valid_action`/`test_perm_remove_valid_action` to assert `history.record` is
  called with the expected action string.

### L5 — Maintenance exempt-set is half dead code; `!maintenance` lacks a cooldown ✅ FIXED
[src/state.py:33-46](src/state.py), [src/bot.py:539-541](src/bot.py). The exempt set lists `guide`,
`history`, and `perm*`, but those handlers never call `is_maintenance_active` — only
start/stop/restart/announce/logs/stats do, so the entries are unreachable. Either add the check to those
handlers (behavior-neutral: they're exempt) or shrink the set to what's real and comment why. Also
`maintenance_cmd` is the only command without `@commands.cooldown`, contradicting CLAUDE.md's rule —
add it or add a code comment stating why it's exempt (e.g. "must never be rate-limited during incidents"
— that's a defensible choice; make it explicit).
- **Resolution:** Diverged from the literal "add the check to those handlers" option after finding an
  existing test (`test_check_maintenance_allows_admin_commands`) that encoded the exempt set as a live
  contract on `is_maintenance_active` itself, independent of any caller. Wiring a no-op maintenance check
  into `guide`/`history`/`perm*` would have added pointless dead-branch boilerplate to five handlers for a
  check that can never trigger (they're exempt by definition). Instead: simplified
  `is_maintenance_active` in [src/state.py](src/state.py) to just return `self.maintenance_mode` (the
  exempt set matched nothing any real caller ever passed), with a docstring explaining that only the six
  container-mutating commands call it at all. Replaced the now-invalid state-layer test with
  `test_maintenance_mode_does_not_block_guide_history_or_perm`, which verifies the real, observable
  contract by invoking the actual `guide`/`history_cmd`/`perm_list` handlers under `maintenance_mode=True`
  and asserting they don't return the maintenance-block message. Added a comment above `maintenance_cmd`
  explaining the intentional missing `@commands.cooldown` (admins must be able to toggle it again
  immediately during an incident).

### L6 — Silent arg-parsing surprises in `!logs` and `!history` ✅ FIXED
[src/bot.py:446-456](src/bot.py): `!logs tyop_container` ignores the unrecognized arg and serves the
default container's logs — surprising for a typo. Track unmatched args and reply with usage instead.
[src/bot.py:514](src/bot.py): `!history abc` raises `BadArgument`, which `on_command_error` doesn't
handle → user gets silence. Add a `commands.BadArgument`/`commands.UserInputError` branch to
`on_command_error` sending the generic usage line. Tests: one per behavior in `TestLogsCommand` /
`TestHistoryCommand`.
- **Resolution:** Implemented both as prescribed in [src/bot.py](src/bot.py): `logs_cmd` now tracks
  unrecognized args and replies with usage instead of silently falling back to the default container;
  `on_command_error` gained a `commands.UserInputError` branch (placed after the more specific
  `MissingRequiredArgument` branch, so that one still wins for its own cases) that replies with generic
  usage instead of silently logging. Added `test_logs_command_unrecognized_arg_shows_usage` and
  `test_on_command_error_bad_argument_shows_usage`.

### L7 — Countdown/immediate ops announce before checking the container can actually stop ✅ FIXED (stop only — see resolution)
[src/bot.py:252-265, 276-280](src/bot.py). `!stop` on an already-stopped container announces
"shutting down in 5 minutes" in Discord and in-game, then 5 minutes later reports "not running".
Cheap fix: call `container_status` (via `run_blocking`) after `resolve_container`; if the op is `stop`/
`restart` and status isn't `running`, reply `Container X is not running.` and skip announcements.
Test in `TestPendingOps`.
- **Resolution:** Scoped to `stop` only, deliberately excluding `restart` -- verified in
  [src/docker_control.py](src/docker_control.py) that `restart_container` succeeds unconditionally
  (Docker's restart legitimately starts a stopped container), and an existing test
  (`test_docker_actions`) already asserts restart succeeds regardless of prior status. Gating `restart` on
  "must be running" would have been a real behavior regression, not a cosmetic fix, despite the finding's
  literal wording. Added a `_bail_if_not_running()` local helper inside `_delayed_container_op`
  ([src/bot.py](src/bot.py)), called once in the `now` path (after the permission check and
  `cancel_pending`, so permission denials and pending-op cancellation are unaffected) and once in the
  countdown path (after the `has_pending_op` dedup check, so duplicate-request rejection is unaffected).
  This ordering choice kept ~15 of the ~20 affected tests passing unchanged; the remainder had their
  `run_blocking` mocks converted from blanket returns to `func.__name__`-aware side effects returning
  `"running"` for the pre-check (two tests needed call-order-aware mocks, since M2's post-op re-seed also
  calls `container_status` with a *different* expected value). Added 3 new behavior tests: `!stop` and
  `!stop now` on an already-stopped container skip announcements and reply "not running"
  (`test_stop_on_already_stopped_container_skips_countdown`,
  `test_stop_now_on_already_stopped_container_skips_announcements`), and a confirming test that `!restart
  now` still succeeds on a stopped container
  (`test_restart_now_succeeds_on_stopped_container`). One additional latent gap fixed in passing:
  `test_stop_without_now_still_uses_countdown` never received M1's `create_future`-must-be-a-real-Future
  fix (it's in a different test class than the ones M1 touched) and was only passing because it didn't
  assert past the first message; now fixed and given a real assertion. 188 tests pass, ruff clean.
- **⚠️ Final review:** The fix itself is correct, but the new pre-check `await` (together with L12's
  awaited `history.record`) landed *between* the `has_pending_op` dedup check and M1's placeholder
  insert, reopening the rapid-double-`!stop` window and making ARCHITECTURE.md's "placeholder before any
  await" bullet inaccurate. See **F2** in the final-review section at the top.

### L8 — Compose healthchecks hardcode port 8000 ✅ FIXED
[docker-compose.yml:35-40](docker-compose.yml), [docker-compose.dev.yml:33-38](docker-compose.dev.yml).
`STATUS_PORT` is passed through as an env var, but the compose healthcheck probes 8000 regardless →
set `STATUS_PORT=9000` and the container reports permanently unhealthy. The Dockerfile HEALTHCHECK
already expands `${STATUS_PORT:-8000}` correctly. Simplest fix: delete the compose-level healthchecks and
let the image's HEALTHCHECK apply (it also hits `/healthz` properly instead of a bare TCP connect).
- **Resolution:** Removed the `healthcheck:` block from both compose files, with a comment pointing at
  the Dockerfile's own HEALTHCHECK. Validated both files with `docker compose config --quiet`.

### L9 — Test hygiene: `conftest` doesn't reset `pending_op_info`; importing `src.bot` writes `data/bot.log` into the repo ✅ FIXED
[tests/conftest.py:14-24](tests/conftest.py): `_reset_state` clears `pending_ops` but not
`pending_op_info` (verified: some test classes clear it manually as a workaround — remove those lines
when fixing). Add `state.pending_op_info.clear()` to both sides of the fixture.
[src/bot.py:33](src/bot.py): `setup_logging` runs at import time, so the test run creates/rotates
`data/bot.log` in the working tree. Cheap fix: `os.environ.setdefault("LOG_FILE", ...)` a tmp path in
conftest before `src` imports. (Moving `setup_logging` into `main()` is cleaner but changes startup
ordering — implementer's call; if moved, module-level code that logs before `main()` loses handlers.)
- **Resolution:** Added `state.pending_op_info.clear()` to both sides of `_reset_state` in
  [tests/conftest.py](tests/conftest.py); removed the now-redundant manual `pending_op_info.clear()` calls
  from `TestPendingOps` and `TestCancelCommand` in `tests/test_bot_commands.py` (left `test_state.py`'s
  `TestCancelPending` alone -- it's testing `state.py` directly and its own setUp/tearDown is legitimate
  self-contained unit-test hygiene, not a workaround). Set `LOG_FILE` to a tmp path via
  `os.environ.setdefault` in conftest, using the cheap fix rather than moving `setup_logging()` into
  `main()`. **Found and fixed the same problem for `HISTORY_FILE` while implementing this** (not in the
  original finding's text, but the identical root cause): several tests call unmocked `history.record`,
  which was writing real entries into `data/history.json` on every test run (confirmed: the file had grown
  to 1201 lines / 37KB before this fix). Added the same tmp-path pattern for `HISTORY_FILE`. Verified
  `data/` stays empty across repeated test runs.

### L10 — CLAUDE.md references `review.md §1.3`, which doesn't exist (never committed) ✅ FIXED
[CLAUDE.md](CLAUDE.md) house rule: *"Don't weaken `_VALID_CONTAINER_NAME` or `_VALID_MSG_CHARS` without
reading review.md §1.3 first."* `git log -- review.md` is empty — the file never existed in history. An
implementing agent told to consult it will dead-end. Replace the pointer with the actual rationale
inline (one sentence: the message whitelist is what makes the `/bin/sh -c` template path safe; widening
it to quotes/`$`/backticks reopens shell injection via `!announce`) or with a pointer to this file's M7.
- **Resolution:** Replaced the dead link with the inline rationale, exactly as prescribed, in
  [CLAUDE.md](CLAUDE.md). Confirmed no other `review.md` references exist anywhere in the repo.

### L11 — `requirements.txt` ships `httpx` in the production image ✅ FIXED
`httpx` is only used by FastAPI's `TestClient` in tests. Move it (plus `pytest`/`pytest-cov`/`ruff`,
which CI installs ad hoc) to a `requirements-dev.txt`, update
[.github/workflows/tests-reusable.yml:20-24](.github/workflows/tests-reusable.yml) to install both files,
and drop `httpx` from the image. Slims the image and its dependabot surface.
- **Resolution:** Removed `httpx` from [requirements.txt](requirements.txt); created
  [requirements-dev.txt](requirements-dev.txt) (`-r requirements.txt` plus `httpx`, `pytest`,
  `pytest-cov`, `ruff`). Updated the CI workflow's install step to a single `pip install -r
  requirements-dev.txt`. Updated README's "Running Tests" section and CLAUDE.md's "Common commands" to
  reference it. Verified: `pip install -r requirements-dev.txt` resolves cleanly, rebuilt the Docker image
  and confirmed `pip show httpx` reports "Package(s) not found" inside it, and the startup smoke test still
  passes.

### L12 — Sync file I/O on the event loop in handlers ✅ FIXED
`history.record` (read+write JSON under a `threading.Lock`) and `permissions._load`/`_save` run directly
in async handlers. Files are small (≤200 entries) so this is latency noise today, but it contradicts the
spirit of the `run_blocking()` house rule. When convenient: `await docker_control.run_blocking(history.record, ...)`
in handlers (the lock already makes it thread-safe). Low urgency; don't refactor permissions caching for
this.
- **Resolution:** Wrapped all 11 `history.record(...)` call sites in [src/bot.py](src/bot.py) with
  `await docker_control.run_blocking(history.record, ...)`, exactly as prescribed. Left `permissions.py`'s
  internal caching untouched per the finding's own instruction. Updated two tests whose assertions
  depended on the exact `run_blocking` call count/sequence (`test_stop_now_sends_announcements`'s
  `func_names` list gained a leading `"record"` entry; `test_logs_command_with_line_count` now checks the
  specific `container_logs` call via `assert_any_call` instead of asserting a single total call).
- **⚠️ Final review:** In the countdown path, the awaited `history.record` is one of the two new awaits
  that reopened the pending-op dedup window (with L7's pre-check). See **F2** in the final-review section
  at the top.

### L13 — Cosmetic polish (fold into any nearby PR) ✅ FIXED
- `_format_delay(90)` → "1 minute" (drops 30 s) — [src/bot.py:230-235](src/bot.py); include remainder
  seconds (`"1 minute 30 seconds"`) or round.
- `!stop`'s immediate-path message renders as "Stopping" via `'ping' if action == 'stop'` string surgery —
  [src/bot.py:259](src/bot.py); works but fragile if a third action is ever added; consider a
  `verb` parameter alongside `action`.
- `!status` records no history while the other read-only commands (`logs`, `stats`) do —
  [src/bot.py:349-357](src/bot.py); pick one convention.
- **Resolution:** All three implemented in [src/bot.py](src/bot.py). `_format_delay` now uses
  `divmod` and appends remainder seconds when nonzero; added 3 tests
  (`test_format_delay_seconds_only`, `test_format_delay_whole_minutes`,
  `test_format_delay_minutes_with_remainder`). `_delayed_container_op` gained an explicit `verb`
  parameter (`"Stopping"`/`"Restarting"`) passed from the `stop`/`restart` command definitions, replacing
  the string-surgery trick -- identical rendered output, no test changes needed. `!status` was kept
  unrecorded (chosen over adding recording to it) with a comment explaining why: it's expected to be
  polled far more often than `logs`/`stats`, and recording it would flood the audit log with low-value
  entries.

---

## Suggested implementation order

Work in small PRs/commits; each finding independently testable. Re-run `ruff check . && ruff format --check .` and the full suite after each.

1. **H2** (Dockerfile/requirements — isolated, unblocks a clean dependency baseline)
2. **M3 + M4** together (same function), then **H1** (config + CI env changes; breaking, needs the
   clearest commit message)
3. **M1**, then **M2** (both in `_delayed_container_op`; M1 first because M2 adds lines inside the same paths)
4. **M6**, **M7** (small, independent)
5. **M5** (mostly docs; decide on the option-3 payload change before touching tests)
6. **L-batch A (code):** L2, L3, L4, L5, L6, L7, L13
7. **L-batch B (infra/docs/tests):** L1, L8, L9, L10, L11, L12

Documentation sync required by CLAUDE.md when the above land: H1 (README, DOCKERHUB, .env.example),
M4/M6 (ARCHITECTURE.md), M5 (README, DOCKERHUB, compose), L1/L8 (README), L10 (CLAUDE.md itself).

## What's already solid (don't regress these)

- Container-name allowlist enforced **inside** `docker_control`, not just at the command layer.
- Message sanitizer whitelist + leading-hyphen strip + 100-char truncation; argv path preferred when no
  placeholder; the `--` guidance in README for flag injection.
- `AllowedMentions.none()` as the client default (M6 is a scoping fix, not a redesign).
- `secrets.compare_digest` for the status token; unauthenticated `/healthz` kept separate deliberately.
- Placeholder-future dedup concept for pending ops (M1 fixes its edges, keep the design).
- Permissions file: 0o600 on create, mtime cache, corrupted-file self-heal, action backfill.
- Handler-level log redaction (L2 extends it to tracebacks).
- CI: reusable workflow, lint+format+coverage+image smoke test, CodeQL, grouped dependabot, docker-socket-proxy
  hardening documented in README.
- Test suite: 163 tests, disciplined mocking, autouse state-reset fixtures.
