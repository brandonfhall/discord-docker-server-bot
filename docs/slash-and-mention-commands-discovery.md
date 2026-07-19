# Discovery: `@Bot` mention + `/slash` command support

> **STATUS: IMPLEMENTED on this branch.** All three pieces below (mention prefix, hybrid/slash conversion, interaction-path fixes) are done and committed; this doc is retained as the design rationale. Items still requiring a *live* Discord guild to confirm (they can't be unit-tested without a gateway): (1) `setup_hook` sync fires — look for the "Synced slash commands" startup log; (2) `/perm add` is denied for non-admins — mitigated by explicit per-subcommand admin checks; (3) `/logs`/`/stats` don't time out (defer path); (4) `/stop` in a disallowed channel returns the ephemeral refusal.

**Branch:** `feature/slash-and-mention-commands`
**Goal:** Keep `!` prefix commands *and* add (a) `@Bot <cmd>` mention-triggered text commands and (b) native `/slash` commands. All three trigger styles live side by side. No existing command is removed.

**Key answer up front:** `/` commands do **not** require an @-mention. You type `/`, Discord shows its native command picker, and the bot receives an *interaction* (not a message). The @-mention only matters for the separate "mention as prefix" text-command feature.

---

## Three independent pieces (can ship separately)

| Piece | Effort | Blast radius |
|---|---|---|
| **A. `@Bot` mention prefix** | Trivial | 1 line + 1 bug fix |
| **B. `/slash` commands (hybrid)** | Moderate–large | Every command in `bot.py`, invite scope, sync, some tests |
| **C. Polish** (ephemeral replies, `guild_only`, help text) | Small | Cosmetic |

They're separable: A can merge on its own today; B is the real project.

---

## Piece A — `@Bot <command>` mention prefix

### Change
`src/bot.py:45` — swap the bare prefix for a mention-aware one:

```python
# before
command_prefix="!",
# after
command_prefix=commands.when_mentioned_or("!"),
```

That's the entire feature for text commands: `@Bot start`, `@Bot stop now`, etc. all work with **zero handler changes** because they still produce a normal `Context`.

### ⚠️ Bug this exposes — must fix in the same change
`src/bot.py:222`, in `on_command_error`'s `CommandNotFound` branch:

```python
if content.startswith(f"{bot.command_prefix}perm") and _origin_allowed(ctx):
```

`bot.command_prefix` is currently the string `"!"`, so the f-string yields `"!perm"`. After the swap, `command_prefix` becomes a **callable** (`when_mentioned_or`'s closure), so this f-string becomes something like `"<function ...>perm"` and the `startswith` check silently never matches — the admin usage hint for a typo'd `!perm` breaks.

**Fix:** stop interpolating `command_prefix`. Use `ctx.invoked_with`, which discord.py sets to the attempted command name **already stripped of the prefix** (`"perm"` for `!perm`/`@Bot perm`, `"permx"` for a typo) — this sidesteps mention-string length math entirely:

```python
if (ctx.invoked_with or "").lower().startswith("perm") and _origin_allowed(ctx):
    ...
```

**Test to add:** `tests/test_bot_commands.py` — a `CommandNotFound` case where `ctx.prefix` is a mention string and content is `"<@123> permx"`, asserting the admin still gets the usage hint. Existing `TestCommandNotFound`-style tests (if any) should be re-run with `ctx.prefix="!"` to confirm no regression.

### Docs
- `README.md` intro + Commands section: note commands also work by mentioning the bot.
- No new env var, no new intent (message-content intent is already on).

---

## Piece B — `/slash` commands via `hybrid_command`

### Approach: hybrid commands (one implementation, two invocation paths)
discord.py's `@bot.hybrid_command()` / `@bot.hybrid_group()` register a command that works as **both** a `!`/mention text command **and** a `/` slash command from a single callback. This is strongly preferred over maintaining a parallel `app_commands` tree — it keeps `has_permission`, cooldowns, and the `Context`-based handler bodies intact.

Conversion is mostly mechanical: `@bot.command()` → `@bot.hybrid_command()`, `@bot.group()` → `@bot.hybrid_group()`. But there are **five real friction points** below, in rough order of cost.

### B1. `*args` commands must get explicit typed signatures — biggest refactor
Slash commands cannot use variadic `*args` (Discord needs a declared parameter schema). Two handlers use it:

- `stop(ctx, *args)` — `src/bot.py:518`
- `restart(ctx, *args)` — `src/bot.py:535`

Both funnel into `_delayed_container_op`, which parses `*args` for the optional `now` flag and optional container name (`src/bot.py:405-407`). Hybrid needs a real signature, e.g.:

```python
async def stop(ctx, container: str = None, now: bool = False):
```

Then `_delayed_container_op` takes `container`/`now` directly instead of parsing `args`. For the **text** path this changes parsing: today `!stop now` (no container) works because `*args` collects `["now"]`; with `container: str = None, now: bool = False`, `!stop now` would bind `container="now"`. Options:
- Use a `commands.FlagConverter` / a custom converter so `now` is recognized positionally on the text path, **or**
- Keep the text path tolerant by re-parsing inside the handler (accept `container` possibly being the literal `"now"` and shuffle), **or**
- Accept a minor UX change: text users type `!stop <container> now` or `/stop now:true`. (Least friction to implement, small behavior change for the single-container `!stop now` shortcut — which is the common case, so probably **not** acceptable as-is.)

**Recommendation:** custom handling that treats a `container` value of `"now"` as "no container + now=True" when only one container is configured, preserving today's `!stop now` ergonomics while giving slash users a clean `now` boolean. This needs its own unit tests.

**Test churn:** every `stop.callback(ctx, "test_container")` / `restart.callback(ctx, ...)` call in `tests/test_bot_commands.py` (the `TestStopNow`/`TestRestartNow`/`TestPendingOps` classes) passes positionally into `*args` today and **will need rewriting** to the new keyword signature. This is the bulk of the test work.

### B2. The 3-second interaction ACK deadline — needs `await ctx.defer()`
A slash command must be acknowledged within **3 seconds** or Discord shows "the application did not respond." Several handlers do a blocking Docker round-trip (via `run_blocking` on the thread pool) **before** their first `ctx.send`, which can exceed 3s under load:

- `status_cmd` — `container_status` then `container_health` before first send (`src/bot.py:576-590`)
- `logs_cmd` — `container_logs` before send (`src/bot.py:711`)
- `stats_cmd` — `container_stats` before send (`src/bot.py:737`)
- `start` — records history then sends fast, mostly OK, but healthcheck path is backgrounded (fine)

**Fix:** add `await ctx.defer()` at the top of any handler that does work before its first reply. `ctx.defer()` is a no-op-ish "thinking…" ack for slash and harmless for text. Cleanest: defer in the handlers listed above (and probably `stop`/`restart`, which announce before executing).

### B3. Command registration / sync — new startup step
Slash commands must be pushed to Discord via `bot.tree.sync()`. Because this bot is **guild-locked** (`DISCORD_GUILD_ID`, `src/config.py:49`), sync to that one guild — it's **instant** (global sync takes up to ~1 hour to propagate):

```python
async def setup_hook():
    if DISCORD_GUILD_ID:
        guild = discord.Object(id=DISCORD_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)   # mirror hybrid app-commands into the guild
        await bot.tree.sync(guild=guild)
    else:  # ALLOW_ANY_GUILD path — global sync, slow propagation
        await bot.tree.sync()
```

Use `setup_hook` (runs once, pre-connect) rather than `on_ready` (can fire repeatedly on reconnect — re-syncing every reconnect risks rate limits). `ALLOW_ANY_GUILD=true` is the awkward case: global sync is slow and the command appears in *every* server the bot is in — worth calling out in docs as a known limitation.

### B4. Error handling + `ctx.message` audit for the interaction path
`ctx.message` is `None` for a slash-invoked hybrid command (`Context.from_interaction`), so **every** `ctx.message.*` access must be audited — not just in error handling:

- **`on_command` at `src/bot.py:171`** dereferences `ctx.message.content` on **every** command dispatch, including slash → `AttributeError` logged for every slash invocation. **Fix (version-independent, cheap):** guard it — `content = ctx.message.content if ctx.message else f"/{ctx.command} (slash)"`.
- The `CommandNotFound` branch reads `ctx.message.content` (`src/bot.py:221`) — but `CommandNotFound` cannot happen for slash (Discord validates command names before dispatch), so this is only reached on the text path. Piece A's fix already replaces it with `ctx.invoked_with`.
- Hybrid **app-command** invocation errors are re-raised as `commands.HybridCommandError` and *do* route to `on_command_error`, so the existing handler largely covers them. But the user-facing usage strings are hardcoded with `!` (e.g. `src/bot.py:200-229`) — a slash user seeing "Usage: `!perm add ...`" is confusing. Consider deriving the prefix or genericizing.
- Add `bot.tree.on_error` / an `on_app_command_error` handler for errors raised *before* the hybrid bridge (e.g. a check failing on the pure app-command path) so slash failures aren't swallowed silently.

### B4b. `SilentCheckFailure` + channel-lock breaks on slash — must not stay silent
`check_guild` raises `SilentCheckFailure` for disallowed origins (DM / foreign guild / disallowed channel), which `on_command_error` currently **returns silently** on (`src/bot.py:187-190`). That's correct for text (ignore the message), but **wrong for slash**: an interaction that's never acknowledged makes Discord show the user **"This interaction failed."** Slash commands are synced guild-wide, so they appear in *every* channel including `ALLOWED_CHANNEL_IDS`-disallowed ones — a user running `/start` there hits exactly this.

**Fix / decision:** in the `SilentCheckFailure` branch, if `ctx.interaction is not None`, acknowledge with an **ephemeral** refusal (`await ctx.send("This command isn't available here.", ephemeral=True)`) instead of returning silently; keep the pure-silent path for text. This is not a presence leak — the user is already in a guild where the command was synced and visible. **Chosen for this implementation.**

### B5. `perm` group → `hybrid_group`
`perm` (`src/bot.py:823`) with `add`/`remove`/`list` subcommands becomes a `hybrid_group`; subcommands become `/perm add`, `/perm remove`, `/perm list`. Nested slash groups are supported. `perm_add`/`perm_remove` use consume-rest `*, role_name: str` which maps cleanly to a slash string param. The `announce` handler's `*, arg2: str = None` (`src/bot.py:611`) is also consume-rest and maps fine, though its "is arg1 a container or the message?" branching (`src/bot.py:622-627`) reads awkwardly as two separate slash params — worth a dedicated `container` + `message` slash signature.

### B6. OAuth invite scope
The bot's invite URL currently requests only the `bot` scope (`README.md:43`). Slash commands require the **`applications.commands`** scope too, or the `/` commands won't appear. This is a one-line doc change + the user must re-invite (or add the scope to the existing invite) — existing installs need the updated invite link once.

### B7. Intents
No change. Slash commands don't need the message-content intent; we keep it for the `!`/mention paths anyway.

---

## Piece C — polish (optional, do after B works)
- `@app_commands.guild_only()` on hybrid commands to hide them from DMs (mirrors the existing `_origin_allowed` DM rejection).
- Ephemeral replies (`ctx.send(..., ephemeral=True)`) for permission-denied / usage errors so slash users don't clutter the channel.
- Slash **parameter descriptions** via `@app_commands.describe(...)` — these show in Discord's command picker UI and are the main UX payoff of slash over `!`.
- Autocomplete for the `container` param (offer `ALLOWED_CONTAINERS`) — nice-to-have, `@app_commands.autocomplete`.

---

## Test & CI impact summary
- **Survives unchanged:** most `*.callback(ctx, ...)` tests — hybrid commands still expose `.callback`, and handler bodies are unchanged except stop/restart.
- **Must rewrite:** `TestStopNow`, `TestRestartNow`, `TestPendingOps` positional `*args` calls → new `container=`/`now=` keyword signature (B1).
- **Add:** mention-prefix `CommandNotFound` test (Piece A); `now`-parsing tests for the new stop/restart signature; a sync smoke test is impractical (needs a live gateway) — skip.
- **CI:** no new deps. Slash sync can't be exercised in unit tests (no Discord connection), so it stays untested by CI — call this out; verify manually in a real guild.
- Docs to update per house rules: `README.md`, `DOCKERHUB.md` (commands table + invite scope + mention note), `ARCHITECTURE.md` (bot framework line + a "command surface: text + slash" subsection).

---

## Suggested sequencing
1. **PR 1 — Piece A** (mention prefix + the `command_prefix` f-string bug fix + test + docs). Small, self-contained, immediately useful.
2. **PR 2 — Piece B core** (hybrid conversion, `setup_hook` sync, stop/restart signature rework, `defer()`, invite-scope docs, test rewrites).
3. **PR 3 — Piece C** (describe/ephemeral/guild_only/autocomplete polish).

## Open decisions for the user
- **Slash `now` UX (B1):** preserve `!stop now` single-container shortcut via custom parsing (recommended, more code) vs. accept `/stop now:true` + `!stop <container> now` only (simpler, small behavior change)?
- **`ALLOW_ANY_GUILD` + slash:** accept slow global sync / commands-everywhere, or restrict slash registration to guild-locked deployments only and skip slash entirely when `ALLOW_ANY_GUILD=true`?
