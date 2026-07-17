# Code Review — Cycle 2 (CLOSED)

**Branch:** `fable-review` · **Base:** `618a71a` (main) · **Reviewed & implemented:** 2026-07-17
**Reviewer:** Claude Fable 5 · **Implementer:** Sonnet (per-phase) · **All findings shipped.**

Cycle 1 (22 findings) closed earlier; see git history. This cycle found **16** (6 medium, the rest
low/cleanup) plus 2 discovered mid-implementation (L11, L12). All are implemented, reviewed, and
committed. Tests **205 → 238**; `ruff check`/`ruff format --check` clean throughout. No exploitable
security hole was found either cycle; nothing here was architectural.

The full finding text lives in git history (this file's earlier revisions on this branch). What
remains below is only what a future contributor or cycle-3 reviewer needs: the commit map, the
durable invariants, and the decisions that are closed.

## Commits (in order)

| Phase | Findings | Commit |
|---|---|---|
| 1 | M1 compose env passthrough | `fb26b5d` (+ `2ff36ed` DOCKERHUB) |
| 2 | M2/M4/M3 Docker error paths | `5d67c8e` |
| 3 | M5 atomic writes | `f523c6d` |
| 4 | M6 event-loop I/O + L12 | `6fd6e23` |
| 5 | L1/L2/L3 announce hardening | `a91083e` |
| 6 | L5/L6 UX + C1/C2/C3 consolidation | `578455d` |
| 7 | L7/L8/L11 hygiene + C4 + L10 docs | `c3f9566` |
| 8 | L4 persist maintenance mode | `7b0856e` |
| 9 | doc/architecture accuracy audit | `5ed7b0a` |

(Commit hashes are post-amend; each phase's plan.md update was amended into its code commit.)

## Durable invariants — do not regress these

These were verified this cycle and several were *reinforced* by it. Breaking one silently reintroduces
a bug we already paid for.

- **Docker daemon errors: catch `_DAEMON_CONNECTION_ERRORS`, never `DockerException` alone.**
  `_get_client()` caches the client for the process lifetime, so a daemon dying *mid-life* raises
  `requests.exceptions.ConnectionError` — **not** a `DockerException` subclass (that only surfaces at
  client construction). The tuple in [src/docker_control.py](src/docker_control.py) covers both. The
  original plan got this wrong; it was caught empirically. `test_container_status_requests_connection_error_returns_error_string`
  pins it.
- **`container_status` has three return values:** a Docker state string, `None` (not found), or the
  literal `"error"` (daemon unreachable). Any new caller must handle `"error"` explicitly or it will
  compare/render it as a real state. Current handlers: `crash_check_loop`/`_before_crash_check` (skip),
  `status_cmd`/`_bail_if_not_running` ("daemon unreachable" reply), `/status` (passes through).
- **Crash-alert condition is `prev == "running" and current != "running"`** — deliberately fires on
  `None` (container removed). Excludes `"error"` (daemon blip) at both the poll and the seed. Don't
  re-add a truthy guard on `current`.
- **Pending-op dedup invariant** in `_delayed_container_op`: **no `await` between the `has_pending_op`
  check and the `state.pending_ops[target] = placeholder` insertion.** Broken once in cycle 1, and
  M6/C1/C2 all edited nearby this cycle without breaking it. The comment block above the placeholder
  says so. Re-verify after any edit to that function.
- **Atomic writes:** permissions, history, and maintenance all persist via
  `atomic_io.atomic_write_json` (temp + fsync + `os.replace`). Don't hand-roll a fourth JSON writer.
  Permissions preserve 0o600 via explicit chmod before the replace. `permissions._save` updates its
  cache only *after* the replace succeeds. There is no directory fsync (accepted: `os.replace` is
  atomic, so a reader never sees a torn file; a power loss reverts to the previous intact file, which
  is recoverable — a corrupt store is not).
- **`permissions._load` never raises**, even if the `.corrupt` preserve fails on a read-only disk — it
  degrades to uncached in-memory defaults so the bot self-heals when the disk recovers. Escaping here
  would silently kill every privileged command (that was L12).
- **Maintenance policy** (unchanged by the C1 consolidation): `start`, `stop`/`restart`, `announce`,
  `logs`, `stats` check maintenance via `_bail_if_maintenance(ctx)`; `cancel`, `status`, `maintenance`,
  `perm*`, `guide`, `history` deliberately do not. `cancel` is intentionally exempt.
- **Maintenance mode persists across restarts** (`data/maintenance.json`), cleared only by
  `!maintenance off`. Loaded in `main()` before `bot.run()` — not `__init__` (import-time I/O) or
  `on_ready` (re-fires on reconnect). A corrupt/missing file defaults to off, never crashes startup.
- **`os.getenv` appears nowhere outside `config.py`.** Every env var — including `LOG_LEVEL`, which
  used to bypass it — is parsed and validated there. New vars: parse in config.py, document in
  README + .env.example, **and add to the `environment:` passthrough list in BOTH compose files**
  (the CLAUDE.md checklist step exists because this drifted twice — M1, and again for MAINTENANCE_FILE
  in Phase 8, both caught by the mechanical gap-check).
- **`_sanitize` strips only the *leading* run of hyphens** (`-rf -x` → `rf -x`). Safe for the current
  argv/`/bin/sh -c` sinks; don't assume all hyphens are neutralized if reused elsewhere. Order is
  `strip().lstrip("-").strip()` — the leading strip matters (a leading space used to shield a hyphen).

## Closed decisions (do not re-flag in cycle 3)

- **L9 — `?token=` query param on `/status`: accepted permanently, won't fix.** Local-only,
  non-internet-reachable deployment; any proxy log capturing the token lives on the same host as the
  token. Revisit only if `/status` becomes reachable from an untrusted network.
- **L10 — permissions match by role *name*, not ID: documented, not migrated.** Rename → grants
  silently revoked → re-grant with `!perm add`. Role-ID migration deferred (file-format change +
  migration path); only worth it if this ships beyond single-server use.

## Mechanical gap-check (re-run in any future cycle)

```sh
grep -oE '(os\.getenv\("|_int_env\(")[A-Z_]+' src/config.py | grep -oE '[A-Z_]+$' | sort -u > /tmp/cfg.txt
for f in docker-compose.yml docker-compose.dev.yml; do
  sed -n '/environment:/,/^    [a-z]/p' $f | grep -oE '^\s+- [A-Z_]+' | grep -oE '[A-Z_]+' | sort -u > /tmp/have.txt
  echo "MISSING from $f:"; comm -23 /tmp/cfg.txt /tmp/have.txt
done
```

Both lists empty at cycle close. Also verified clean: README/DOCKERHUB command tables ↔ 12 registered
commands; README valid-actions ↔ `permissions.ALL_ACTIONS`; every config var ↔ README table +
.env.example; every test class ↔ CLAUDE.md test table; `import src.bot` (CI smoke) clean.
