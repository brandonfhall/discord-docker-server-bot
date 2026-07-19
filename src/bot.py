import asyncio
import logging
import os
import threading
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import docker_control, history, permissions
from .api import start_api
from .config import (
    BOT_TOKEN,
    STATUS_TOKEN,
    SHUTDOWN_DELAY,
    ALLOWED_CONTAINERS,
    DISCORD_GUILD_ID,
    LOG_FILE,
    LOG_LEVEL,
    ANNOUNCE_CHANNEL_ID,
    ANNOUNCE_ROLE_ID,
    ALLOWED_CHANNEL_IDS,
    COMMAND_COOLDOWN,
    CRASH_CHECK_INTERVAL,
    CRASH_ALERT_CHANNEL_ID,
    HISTORY_FILE,
    HEALTHCHECK_POLL_INTERVAL,
    HEALTHCHECK_MAX_WAIT,
    MAINTENANCE_FILE,
)
from .logging_config import setup_logging
from .state import state

VALID_ACTIONS = permissions.ALL_ACTIONS


setup_logging(LOG_FILE, LOG_LEVEL, [BOT_TOKEN, STATUS_TOKEN])

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

# when_mentioned_or keeps the "!" prefix while also accepting "@Bot <command>",
# so text commands work via both a literal prefix and an @-mention. Slash (/)
# commands are a separate mechanism (app_commands, registered below in
# _setup_hook) and need no prefix or mention at all.
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!"),
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none(),
)


async def _setup_hook():
    """Register slash (application) commands with Discord.

    Runs once before the gateway connection is established (unlike on_ready,
    which can fire repeatedly on reconnect -- re-syncing every reconnect risks
    rate limits). Every hybrid command doubles as an app command; syncing
    publishes them. A guild-locked bot syncs to that one guild, which is
    instant; a global sync (the ALLOW_ANY_GUILD path) can take up to ~1 hour to
    propagate and makes the commands appear in every server the bot is in.
    """
    try:
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=DISCORD_GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logging.info(f"Synced slash commands to guild {DISCORD_GUILD_ID}")
        else:
            await bot.tree.sync()
            logging.info("Synced slash commands globally (propagation may take up to ~1h)")
    except Exception as e:
        # A sync failure must not take the bot down -- the "!"/mention text
        # commands still work without it; only the slash surface is affected.
        logging.error(f"Failed to sync slash commands: {e}", exc_info=True)


bot.setup_hook = _setup_hook


class SilentCheckFailure(commands.CheckFailure):
    """Raised when the command origin (DM/guild/channel) is disallowed — never respond.

    Kept distinct from a permission-denial CheckFailure so on_command_error can stay
    silent for disallowed origins without leaking the bot's presence, while still
    telling a user in the home guild that they lack the role for a command.
    """


def _origin_allowed(ctx) -> bool:
    """True if ctx's guild/channel origin passes the same checks check_guild enforces.

    Shared by check_guild (registered commands) and on_command_error's
    CommandNotFound branch (unregistered/typo'd commands, which discord.py never
    routes through @bot.check predicates) so the two can't drift apart.
    """
    if ctx.guild is None:
        return False
    if DISCORD_GUILD_ID and ctx.guild.id != DISCORD_GUILD_ID:
        return False
    if ALLOWED_CHANNEL_IDS and ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return False
    return True


@bot.check
async def check_guild(ctx):
    if not _origin_allowed(ctx):
        raise SilentCheckFailure()
    return True


def has_permission(action: str):
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        allowed = await docker_control.run_blocking(permissions.is_member_allowed, action, ctx.author)
        return allowed

    return commands.check(predicate)


async def _bail_if_maintenance(ctx) -> bool:
    """If maintenance mode is active, send the maintenance message and return True.

    Callers should `return` immediately when this returns True. Only
    container-mutating commands (start, stop/restart via _delayed_container_op,
    announce, logs, stats) call this; cancel, status, maintenance itself,
    perm*, guide, and history deliberately do not (see C1/ARCHITECTURE.md).
    """
    if state.is_maintenance_active():
        await ctx.send(f"Bot is in maintenance mode. {state.maintenance_reason}")
        return True
    return False


async def _defer(ctx):
    """Ack a slash interaction early so a slow handler doesn't blow Discord's
    3-second interaction deadline (several handlers do a blocking Docker round
    trip via run_blocking before their first reply). No-op for text commands,
    which have no such deadline, and a no-op if the interaction is already
    acknowledged.
    """
    if ctx.interaction is not None and not ctx.interaction.response.is_done():
        try:
            await ctx.defer()
        except discord.HTTPException:
            pass


async def resolve_container(ctx, name: str):
    """Helper to resolve the target container name."""
    if name:
        if name in ALLOWED_CONTAINERS:
            return name
        await ctx.send(f"Container '{name}' is not in the allowed list.")
        return None

    if len(ALLOWED_CONTAINERS) == 1:
        return ALLOWED_CONTAINERS[0]

    # ALLOWED_CONTAINERS can never be empty here -- config.py raises at import
    # time if it is (C3) -- so the only remaining case is "more than one".
    await ctx.send(f"Multiple containers configured. Please specify one: {', '.join(ALLOWED_CONTAINERS)}")
    return None


async def send_announcement(ctx, message: str):
    """Helper to send announcements to the configured channel/role."""
    content = f"<@&{ANNOUNCE_ROLE_ID}> {message}" if ANNOUNCE_ROLE_ID else message

    target_channel = ctx.channel
    if ANNOUNCE_CHANNEL_ID:
        found = bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if found:
            target_channel = found
        else:
            logging.warning(f"Configured ANNOUNCE_CHANNEL_ID {ANNOUNCE_CHANNEL_ID} not found.")

    # Re-enable pinging only the configured announce role, never every role in the
    # server -- a maintenance/announce message can contain user-supplied text, and
    # AllowedMentions(roles=True) would let a stray "<@&other_role_id>" ping it too.
    allowed_mentions = (
        discord.AllowedMentions(roles=[discord.Object(id=ANNOUNCE_ROLE_ID)])
        if ANNOUNCE_ROLE_ID
        else discord.AllowedMentions.none()
    )

    try:
        await target_channel.send(
            content,
            allowed_mentions=allowed_mentions,
        )
    except Exception as e:
        logging.error(f"Failed to send announcement to {target_channel}: {e}", exc_info=True)
        return

    if target_channel.id != ctx.channel.id:
        await ctx.send(f"Announcement sent to {target_channel.mention}.")


@bot.event
async def on_ready():
    logging.info(f"Bot ready: {bot.user} at {datetime.now(timezone.utc).isoformat()} UTC")
    logging.info(f"Logging to file: {os.path.abspath(LOG_FILE)}")
    logging.info(f"Permissions file: {os.path.abspath(permissions.PERMISSIONS_FILE)}")
    if not STATUS_TOKEN:
        logging.warning("STATUS_TOKEN is not set — the /status API endpoint is open to unauthenticated access")
    if not crash_check_loop.is_running():
        crash_check_loop.start()


@bot.event
async def on_command(ctx):
    # ctx.message is None for a slash-invoked hybrid command (there's no
    # underlying message), so guard it -- dereferencing .content would raise on
    # every slash invocation.
    invoked = ctx.message.content if ctx.message else f"/{ctx.command} (slash)"
    logging.info(f"Command received: '{invoked}' from {ctx.author} ({ctx.author.id})")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        # L6: log metadata only, not content -- full content here would echo
        # !logs/!history replies (up to ~1900 chars of container log) into
        # bot.log, which /status then re-serves, amplifying noise and aging
        # real events out of the log window faster.
        logging.info(f"Bot sent {len(message.content)} chars to {message.channel}")
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, SilentCheckFailure):
        # Disallowed origin (DM, foreign guild, or disallowed channel).
        # Text path: never respond, so as not to leak the bot's presence.
        # Slash path: an unacknowledged interaction makes Discord show the user
        # "This interaction failed", so we must ack. Slash commands are synced
        # guild-wide and are visible in disallowed *channels* of the home guild,
        # so an ephemeral refusal there is expected, not a presence leak.
        if ctx.interaction is not None:
            try:
                await ctx.send("This command isn't available here.", ephemeral=True)
            except Exception:
                pass
        return
    if isinstance(error, commands.CheckFailure):
        logging.warning(f"Permission denied for user {ctx.author} on command {ctx.command}")
        await ctx.send("You do not have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        # Handle missing-argument cases explicitly so users see helpful usage.
        cmd = ctx.command.qualified_name if ctx.command else ""
        if cmd.startswith("perm "):
            # Subcommands under the perm group such as "perm add" / "perm remove".
            if "add" in cmd:
                await ctx.send("Usage: `!perm add <action> <role_name>`")
            elif "remove" in cmd:
                await ctx.send("Usage: `!perm remove <action> <role_name>`")
            else:
                await ctx.send("Usage: `!perm <add|remove|list> ...`")
        elif cmd == "perm":
            await ctx.send("Usage: `!perm <add|remove|list> ...`")
        else:
            # Generic fallback for other commands with missing args.
            await ctx.send(f"Usage: `!{cmd} ...` — see `!help` for details.")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Command on cooldown. Try again in {error.retry_after:.1f}s.")
    elif isinstance(error, commands.CommandNotFound):
        # For unknown commands, normally stay quiet to avoid noise.
        # However, special-case the permission management command so admins
        # get helpful usage feedback instead of silence.
        # CommandNotFound fires before @bot.check predicates ever run (there's no
        # command to prepare), so this branch must re-check the origin itself --
        # ctx.author.guild_permissions doesn't exist in a DM, and skipping the
        # origin check would let a typo'd !perm command leak the bot's presence
        # in a foreign guild/channel the same way M4 prevents for real commands.
        # Use ctx.invoked_with (the attempted command name, already stripped of
        # whatever prefix matched -- "!" or an @-mention) rather than
        # interpolating bot.command_prefix, which is now a callable
        # (when_mentioned_or) and would never match a literal string.
        if (ctx.invoked_with or "").lower().startswith("perm") and _origin_allowed(ctx):
            if ctx.author.guild_permissions.administrator:
                await ctx.send("Usage: `!perm <add|remove|list> ...`")
    elif isinstance(error, commands.UserInputError):
        # Catch-all for argument-conversion failures (e.g. `!history abc`) that
        # aren't a missing-argument case above -- usage instead of silent failure.
        cmd = ctx.command.qualified_name if ctx.command else ""
        await ctx.send(f"Usage: `!{cmd} ...` — see `!help` for details.")
    else:
        logging.error(f"Command error: {error}", exc_info=True)


@tasks.loop(seconds=CRASH_CHECK_INTERVAL)
async def crash_check_loop():
    """Polls container statuses and alerts if one unexpectedly stops or is removed."""
    # state.last_known_status is populated here on first run
    for name in ALLOWED_CONTAINERS:
        try:
            current = await docker_control.run_blocking(docker_control.container_status, name)
        except Exception:
            continue
        if current == "error":
            # container_status() returns the literal "error" when the Docker
            # daemon itself was unreachable for this poll (M2) -- that's a
            # transient daemon blip, not a real state change. Skip updating the
            # baseline and skip alerting: overwriting last_known_status here
            # would either mask a real crash that happened during the outage,
            # or -- if we alerted on it -- fire a false "removed" alert for
            # every container on every daemon hiccup (M4's ordering note).
            logging.warning(f"Skipping crash check for '{name}': docker daemon unreachable")
            continue
        prev = state.last_known_status.get(name)
        state.last_known_status[name] = current
        # Alert when a container transitions from running to anything else,
        # including removal -- container_status() returns None for a container
        # that was force-removed while running (`docker rm -f`), which is
        # exactly the scenario crash alerting exists to catch (M4).
        if prev == "running" and current != "running":
            status_desc = current or "removed/not found"
            logging.warning(f"Crash alert: container '{name}' changed from running to {status_desc}")
            channel_id = CRASH_ALERT_CHANNEL_ID or ANNOUNCE_CHANNEL_ID
            if channel_id:
                ch = bot.get_channel(channel_id)
                if ch:
                    try:
                        await ch.send(f"**Crash Alert:** Container `{name}` is now **{status_desc}** (was running).")
                    except Exception as e:
                        logging.error(f"Failed to send crash alert: {e}")


@crash_check_loop.before_loop
async def _before_crash_check():
    await bot.wait_until_ready()
    # Seed initial statuses so we don't false-alert on startup
    for name in ALLOWED_CONTAINERS:
        try:
            status = await docker_control.run_blocking(docker_control.container_status, name)
        except Exception:
            continue
        if status != "error":
            state.last_known_status[name] = status


@bot.hybrid_command()
@app_commands.describe(container_name="Container to start (optional if only one is configured)")
@has_permission("start")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def start(ctx, container_name: str = None):
    """Starts the container."""
    await _defer(ctx)
    if await _bail_if_maintenance(ctx):
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested START for container '{target}'")
    await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, "start", target)
    starting_msg = f"Starting {target}..."
    if state.has_pending_op(target):
        # L5: inform, don't block -- a pending stop/restart countdown will
        # still kill the container it's scheduled against a few minutes after
        # this start succeeds, unless the user cancels it.
        starting_msg += f"\nNote: a stop/restart countdown is scheduled for `{target}` -- `!cancel` to abort."
    await ctx.send(starting_msg)
    res = await docker_control.run_blocking(docker_control.start_container, target)
    logging.info(f"START result for {ctx.author}: {res.message}")
    if not res.success:
        await ctx.send(res.message)
        return

    await _reseed_crash_baseline(target)

    health = await docker_control.run_blocking(docker_control.container_health, target)
    if health is None:
        # No HEALTHCHECK defined for this container -- Docker's running state
        # is the only readiness signal available, so report success now, same
        # as before this container ever had a health-aware path.
        await ctx.send(res.message)
        return

    # A HEALTHCHECK is configured: don't claim "started" until it reports
    # healthy. No message here -- the background task below sends the single
    # follow-up reply once it knows the real outcome, so the user still sees
    # exactly two messages for !start: "Starting..." and the final result,
    # same as the no-healthcheck path above.
    bot.loop.create_task(_wait_for_healthy(ctx, target, res.message))


async def _wait_for_healthy(ctx, target: str, success_message: str):
    """Poll container_health() after a !start until it leaves 'starting', then
    send the single follow-up message reporting the outcome. Only scheduled
    for containers with a Docker HEALTHCHECK configured -- start() reports
    readiness immediately (and alone) for those without one.

    Runs as a background task (not awaited by start()) so the command handler
    returns promptly, matching the pattern _delayed_container_op uses for
    stop/restart countdowns rather than blocking the command dispatch for
    however long the healthcheck takes to settle.
    """
    elapsed = 0
    try:
        while HEALTHCHECK_MAX_WAIT == 0 or elapsed < HEALTHCHECK_MAX_WAIT:
            await asyncio.sleep(HEALTHCHECK_POLL_INTERVAL)
            elapsed += HEALTHCHECK_POLL_INTERVAL
            health = await docker_control.run_blocking(docker_control.container_health, target)
            if health == "healthy":
                await ctx.send(success_message)
                return
            if health == "unhealthy":
                await ctx.send(f"`{target}` started but its healthcheck reports **unhealthy**. Check `!logs {target}`.")
                return
            if health is None:
                # We only ever schedule this watcher when the initial health
                # read was non-None (see start()), so a None here mid-wait
                # means the container went away, was recreated without a
                # HEALTHCHECK, or the daemon was briefly unreachable -- not
                # "still starting". Treat it as terminal instead of spinning
                # for the full HEALTHCHECK_MAX_WAIT (up to 30 min, or forever
                # if HEALTHCHECK_MAX_WAIT == 0) and then reporting a status
                # that was never true (M3).
                await ctx.send(
                    f"`{target}` no longer reports health status (it may have been stopped or recreated). "
                    f"Check `!status {target}`."
                )
                return
        await ctx.send(
            f"`{target}` is still `starting` after {HEALTHCHECK_MAX_WAIT}s -- giving up watching. "
            f"Check `!status {target}` for the current state."
        )
    except Exception as e:
        logging.error(f"Error while waiting for '{target}' to become healthy: {e}", exc_info=True)


async def _reseed_crash_baseline(target: str):
    """Re-seed the crash-alerting baseline after a bot-initiated start/stop/restart.

    Without this, the next crash_check_loop poll would still see the
    pre-operation status in state.last_known_status (e.g. "exited" right
    after a start, or "running" right after a stop) and either miss a real
    transition or fire a false crash alert for a change the bot itself just
    made. A stored "error" here (M2: daemon unreachable for this read) is
    self-correcting -- crash_check_loop's `prev == "running"` alert check
    never matches "error", so it just delays the next real baseline update
    rather than causing a false alert.
    """
    state.last_known_status[target] = await docker_control.run_blocking(docker_control.container_status, target)


def _format_delay(seconds: int) -> str:
    """Return a human-readable delay string, e.g. '5 minutes', '30 seconds', or
    '1 minute 30 seconds' (a bare minute count would silently drop the remainder)."""
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes, remainder = divmod(seconds, 60)
    result = f"{minutes} minute{'s' if minutes != 1 else ''}"
    if remainder:
        result += f" {remainder} second{'s' if remainder != 1 else ''}"
    return result


async def _delayed_container_op(ctx, *args, action, now_action, docker_func, immediate_msg, countdown_msg_tpl, verb):
    """Shared logic for stop and restart commands with optional 'now' flag."""
    await _defer(ctx)
    if await _bail_if_maintenance(ctx):
        return

    now = any(a.lower() == "now" for a in args)
    container_name_args = [a for a in args if a.lower() != "now"]
    container_name = container_name_args[0] if container_name_args else None

    target = await resolve_container(ctx, container_name)
    if not target:
        return

    async def _bail_if_not_running() -> bool:
        """For 'stop' only: reply and return True if the container isn't running.

        Restarting a stopped container is valid (Docker's restart also starts
        it), so this check does not apply to the 'restart' action.
        """
        if action != "stop":
            return False
        current_status = await docker_control.run_blocking(docker_control.container_status, target)
        if current_status == "error":
            # Daemon unreachable (M2) -- don't tell the user the container
            # isn't running when the truth is we couldn't check.
            await ctx.send(f"Could not check status for `{target}` -- the Docker daemon is unreachable. Try again shortly.")
            return True
        if current_status != "running":
            await ctx.send(f"Container `{target}` is not running.")
            return True
        return False

    if now:
        if not ctx.author.guild_permissions.administrator and not await docker_control.run_blocking(
            permissions.is_member_allowed, now_action, ctx.author
        ):
            await ctx.send(f"You do not have permission to use `!{action} now`.")
            return
        logging.info(f"User {ctx.author} requested immediate {action.upper()} for container '{target}'")
        await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, f"{action} now", target)
        state.cancel_pending(target)
        if await _bail_if_not_running():
            return
        await ctx.send(f"{verb} {target} immediately...")
        await send_announcement(ctx, immediate_msg)
        await docker_control.run_blocking(docker_control.announce_in_game, target, immediate_msg)
        res = await docker_control.run_blocking(docker_func, target)
        logging.info(f"Immediate {action.upper()} result for {ctx.author}: {res.message}")
        if res.success:
            await _reseed_crash_baseline(target)
        await ctx.send(f"{action.capitalize()} result: {res.message}")
        return

    logging.info(f"User {ctx.author} requested {action.upper()} for container '{target}'")

    if state.has_pending_op(target):
        await ctx.send(f"A shutdown or restart is already scheduled for `{target}`. Ignoring duplicate request.")
        return

    # Insert the placeholder immediately after the dedup check above, before any
    # further await -- _bail_if_not_running and history.record below both await,
    # and two rapid !stop calls could otherwise both pass has_pending_op while
    # interleaved at either one (F2: this is exactly what happened when those two
    # awaits were inserted between the dedup check and the placeholder).
    placeholder = bot.loop.create_future()
    state.pending_ops[target] = placeholder
    state.pending_op_info[target] = {"action": action, "scheduled_at": datetime.now(timezone.utc)}

    try:
        if await _bail_if_not_running():
            if state.pending_ops.get(target) is placeholder:
                state.cancel_pending(target)
            return

        await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, action, target)

        delay_str = _format_delay(SHUTDOWN_DELAY)
        countdown_msg = countdown_msg_tpl.format(delay=delay_str)
        await ctx.send(f"Server {target} will {action} in {delay_str} (countdown started).")
        await send_announcement(ctx, countdown_msg)
        await docker_control.run_blocking(docker_control.announce_in_game, target, countdown_msg)
    except Exception:
        # Don't leave a dead placeholder behind -- it would permanently block
        # future stop/restart attempts on this container (has_pending_op never clears).
        if state.pending_ops.get(target) is placeholder:
            state.cancel_pending(target)
        raise

    # A !cancel / !stop now / !maintenance on that ran while we were awaiting the
    # announcement above already popped and cancelled our placeholder. Don't
    # schedule the real operation on top of a cancellation the user was told succeeded.
    if state.pending_ops.get(target) is not placeholder or placeholder.cancelled():
        await ctx.send(f"The scheduled {action} for `{target}` was cancelled before the countdown completed.")
        return

    async def do_operation():
        await asyncio.sleep(SHUTDOWN_DELAY)
        state.pending_ops.pop(target, None)
        state.pending_op_info.pop(target, None)
        try:
            result = await docker_control.run_blocking(docker_func, target)
            logging.info(f"{action.upper()} execution result for {target}: {result.message}")
            if result.success:
                await _reseed_crash_baseline(target)
            await ctx.send(f"{action.capitalize()} result: {result.message}")
        except Exception as e:
            logging.error(f"Error during scheduled {action} of {target}: {e}", exc_info=True)
            try:
                await ctx.send(f"Error during scheduled {action} of `{target}`. Check the bot logs for details.")
            except Exception:
                pass

    state.pending_ops[target] = bot.loop.create_task(do_operation())


# Slash commands can't take variadic *args, so stop/restart expose two explicit
# optional string params and reconstruct the free-form token tuple that
# _delayed_container_op parses. This preserves every text form and ordering --
# "!stop", "!stop now", "!stop <container>", "!stop <container> now",
# "!stop now <container>" -- while giving slash users labeled "container" and
# "now" fields. Keeping "now" a plain string (not a bool) is deliberate: a bool
# param would make discord.py's converter reject the literal token "now" on the
# text path (e.g. "!stop server1 now"), breaking backward compatibility.
_NOW_HELP = 'Type "now" to skip the countdown and act immediately (needs the *_now permission)'


@bot.hybrid_command()
@app_commands.describe(
    container="Container to stop (optional if only one is configured)",
    now=_NOW_HELP,
)
@has_permission("stop")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def stop(ctx, container: str = None, now: str = None):
    """Stops the container. Use 'now' for immediate shutdown (requires stop_now permission)."""
    args = tuple(a for a in (container, now) if a is not None)
    await _delayed_container_op(
        ctx,
        *args,
        action="stop",
        now_action="stop_now",
        docker_func=docker_control.stop_container,
        immediate_msg="Server is shutting down NOW. Please disconnect immediately.",
        countdown_msg_tpl="Server will shut down in {delay}. Please prepare to log off.",
        verb="Stopping",
    )


@bot.hybrid_command()
@app_commands.describe(
    container="Container to restart (optional if only one is configured)",
    now=_NOW_HELP,
)
@has_permission("restart")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def restart(ctx, container: str = None, now: str = None):
    """Restart the container (with countdown). Use 'now' to restart immediately (needs restart_now)."""
    args = tuple(a for a in (container, now) if a is not None)
    await _delayed_container_op(
        ctx,
        *args,
        action="restart",
        now_action="restart_now",
        docker_func=docker_control.restart_container,
        immediate_msg="Server is restarting NOW. Please disconnect immediately.",
        countdown_msg_tpl="Server will restart in {delay}. Please prepare to log off.",
        verb="Restarting",
    )


@bot.hybrid_command()
@has_permission("cancel")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def cancel(ctx):
    """Cancels all pending stop/restart countdowns across every container."""
    await _defer(ctx)
    cancelled = state.cancel_all_pending()
    logging.info(f"User {ctx.author} requested CANCEL of pending operations")
    await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, "cancel", "")
    if not cancelled:
        await ctx.send("No pending stop/restart operations to cancel.")
        return
    logging.info(f"Cancelled pending operations for: {', '.join(cancelled)}")
    await ctx.send(f"Cancelled pending countdowns for: {', '.join(f'`{c}`' for c in cancelled)}.")
    await send_announcement(ctx, "**Scheduled shutdown/restart has been cancelled.**")


@bot.hybrid_command(name="status")
@app_commands.describe(container_name="Container to check (optional if only one is configured)")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def status_cmd(ctx, container_name: str = None):
    """Checks the container status and any pending operations."""
    await _defer(ctx)
    # Deliberately not recorded to HISTORY_FILE, unlike logs/stats: !status is
    # expected to be checked far more often (routine "is it up?" polling) and
    # would otherwise flood the audit log with low-value entries.
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested STATUS for container '{target}'")
    res = await docker_control.run_blocking(docker_control.container_status, target)
    if res == "error":
        # container_status() returns the literal "error" when the Docker daemon
        # itself was unreachable (M2) -- tell the user that honestly instead of
        # rendering "Status for `x`: **error**" with no explanation, or the old
        # "not found" behavior a daemon outage used to produce.
        await ctx.send(f"Could not check status for `{target}` -- the Docker daemon is unreachable. Try again shortly.")
        return
    if res is None:
        # L5: container_status() returns None when no container by this name
        # exists (as opposed to "error", handled above, for a daemon outage).
        # Without this branch the fallback below renders "Status for `x`: **None**".
        await ctx.send(f"Container `{target}` not found.")
        return
    health = await docker_control.run_blocking(docker_control.container_health, target)
    lines = [f"Status for `{target}`: **{res}**"]
    if health:
        emoji = {"healthy": "✅", "unhealthy": "❌", "starting": "⏳"}.get(health, "")
        lines.append(f"Health: **{health}** {emoji}".rstrip())
    if state.has_pending_op(target):
        info = state.pending_op_info.get(target, {})
        op_action = info.get("action", "operation")
        scheduled_at = info.get("scheduled_at")
        if scheduled_at:
            elapsed = (datetime.now(timezone.utc) - scheduled_at).total_seconds()
            remaining = max(0, SHUTDOWN_DELAY - elapsed)
            lines.append(f"⚠️ Pending **{op_action}** — executes in ~{_format_delay(int(remaining))}.")
        else:
            lines.append(f"⚠️ Pending **{op_action}** in progress.")
    await ctx.send("\n".join(lines))


@bot.hybrid_command()
@app_commands.describe(
    arg1="Container name, or the first word of the message if only one container is configured",
    arg2="The message to send (when the first argument is a container name)",
)
@has_permission("announce")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def announce(ctx, arg1: str, *, arg2: str = None):
    """
    Send an in-game announcement.
    Usage: !announce <message> (if single container)
           !announce <container_name> <message> (if multiple)
    """
    await _defer(ctx)
    if await _bail_if_maintenance(ctx):
        return
    target = None
    message = None

    if arg1 in ALLOWED_CONTAINERS:
        target = arg1
        message = arg2
    elif len(ALLOWED_CONTAINERS) == 1:
        target = ALLOWED_CONTAINERS[0]
        message = f"{arg1} {arg2}" if arg2 else arg1

    if not target or not message:
        await ctx.send(f"Usage: `!announce <container_name> <message>`\nAvailable: {', '.join(ALLOWED_CONTAINERS)}")
        return

    await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, "announce", target)
    res = await docker_control.run_blocking(docker_control.announce_in_game, target, message)
    await ctx.send(f"Sent to {target}: {res.message}")


@announce.error
async def announce_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: `!announce <message>` or `!announce <container_name> <message>`")
    else:
        await on_command_error(ctx, error)


@bot.hybrid_command(name="guide")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def guide(ctx):
    """Shows a simple usage guide."""
    lines = [
        "**Docker Bot Guide**",
        "Use `!help` for detailed command usage.",
        "",
        "**Control**",
        "`!start`       : Start the server",
        "`!stop`        : Stop the server (with delay)",
        "`!stop now`    : Stop the server immediately",
        "`!restart`     : Restart the server (with delay)",
        "`!restart now` : Restart the server immediately",
        "`!cancel`      : Cancel a pending stop/restart countdown",
        "`!status`      : Show server status",
        "",
        "**Info**",
        "`!logs [lines]`  : View recent container logs",
        "`!stats`         : Show container CPU/memory usage",
        "`!history [n]`   : View recent command history",
        "",
        "**Admin**",
        "`!maintenance on/off [reason]` : Toggle maintenance mode",
        "`!perm list`   : List allowed roles",
        "`!perm add`    : Add role to action",
        "`!perm remove` : Remove role from action",
    ]
    logging.info(f"User {ctx.author} requested GUIDE")
    await ctx.send("\n".join(lines))


@bot.hybrid_command(name="logs")
@app_commands.describe(
    arg1="Container name, or a line count if only one container is configured",
    arg2="Number of lines to show (max 50)",
)
@has_permission("logs")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def logs_cmd(ctx, arg1: str = None, arg2: str = None):
    """View recent container logs. Usage: !logs [container] [lines]"""
    await _defer(ctx)
    # L5: checked first, before arg-parsing, for consistency with every other
    # maintenance-gated handler (start, _delayed_container_op, announce, stats).
    if await _bail_if_maintenance(ctx):
        return

    container_name = None
    lines = 25
    unrecognized = []
    for arg in (arg1, arg2):
        if arg is None:
            continue
        if arg.isdigit():
            # L5: clamp to at least 1 -- "0" passes isdigit() but tail=0 yields
            # an empty result and a confusing "No recent logs" reply.
            lines = min(max(int(arg), 1), 50)
        elif arg in ALLOWED_CONTAINERS:
            container_name = arg
        else:
            unrecognized.append(arg)

    if unrecognized:
        await ctx.send(f"Unrecognized argument(s): {', '.join(unrecognized)}. Usage: `!logs [container] [lines]`")
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested LOGS for container '{target}' ({lines} lines)")
    await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, f"logs {lines}", target)
    result = await docker_control.run_blocking(docker_control.container_logs, target, lines)
    if result is None:
        await ctx.send(f"Could not fetch logs for {target}.")
        return
    # Truncate to fit Discord's 2000 char limit, then strip backticks so they
    # can't break out of the code fence Discord renders around the output.
    output = result[-1900:] if len(result) > 1900 else result
    output = output.replace("`", "'")
    if not output.strip():
        await ctx.send(f"No recent logs for {target}.")
        return
    await ctx.send(f"**Logs for {target}** (last {lines} lines):\n```\n{output}\n```")


@bot.hybrid_command(name="stats")
@app_commands.describe(container_name="Container to inspect (optional if only one is configured)")
@has_permission("stats")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def stats_cmd(ctx, container_name: str = None):
    """Show container CPU and memory usage."""
    await _defer(ctx)
    if await _bail_if_maintenance(ctx):
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested STATS for container '{target}'")
    await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, "stats", target)
    data = await docker_control.run_blocking(docker_control.container_stats, target)
    if data is None:
        await ctx.send(f"Could not fetch stats for {target}.")
        return
    if data.get("status") != "running":
        await ctx.send(f"Container {target} is **{data.get('status', 'unknown')}** — stats are only available while running.")
        return
    if "error" in data:
        await ctx.send(f"Error fetching stats for {target}: {data['error']}")
        return
    lines = [
        f"**Stats for {target}**",
        f"CPU: {data['cpu_percent']}%",
        f"Memory: {data['mem_usage_mb']} MB / {data['mem_limit_mb']} MB ({data['mem_percent']}%)",
    ]
    await ctx.send("\n".join(lines))


@bot.hybrid_command(name="history")
@app_commands.describe(count="How many recent entries to show (max 25)")
@has_permission("history")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def history_cmd(ctx, count: int = 10):
    """View recent command history. Usage: !history [count]"""
    await _defer(ctx)
    logging.info(f"User {ctx.author} requested HISTORY")
    count = max(1, min(count, 25))
    entries = await docker_control.run_blocking(history.load, HISTORY_FILE)
    if not entries:
        await ctx.send("No command history recorded yet.")
        return
    recent = entries[-count:]
    lines = [f"**Command History** (last {len(recent)})"]
    for entry in reversed(recent):
        ts = entry.get("timestamp", "?")[:19].replace("T", " ")
        user = entry.get("user", "?")
        cmd = entry.get("command", "?")
        container = entry.get("container", "")
        line = f"`{ts}` **{user}**: `!{cmd}`"
        if container:
            line += f" on `{container}`"
        lines.append(line)
    output = "\n".join(lines)
    if len(output) > 1900:
        output = output[:1900] + "\n..."
    await ctx.send(output)


@bot.hybrid_command(name="maintenance")
@app_commands.describe(
    toggle='"on" or "off" (omit to show current status)',
    reason="Optional reason shown to users while maintenance mode is on",
)
@has_permission("maintenance")
# Intentionally no @commands.cooldown: during an active incident an admin must be
# able to toggle maintenance mode again immediately (e.g. on/off/on to adjust the
# reason), without waiting out a per-user cooldown.
async def maintenance_cmd(ctx, toggle: str = None, *, reason: str = ""):
    """Toggle maintenance mode. Usage: !maintenance on/off [reason]"""
    await _defer(ctx)
    if toggle is None:
        status = "ON" if state.maintenance_mode else "OFF"
        await ctx.send(
            f"Maintenance mode is **{status}**." + (f" Reason: {state.maintenance_reason}" if state.maintenance_reason else "")
        )
        return
    toggle = toggle.lower()
    if toggle == "on":
        state.maintenance_mode = True
        state.maintenance_reason = reason or "No reason given."
        cancelled = state.cancel_all_pending()
        logging.info(f"User {ctx.author} enabled maintenance mode: {state.maintenance_reason}")
        if cancelled:
            logging.info(f"Cancelled pending operations for: {', '.join(cancelled)}")
        await docker_control.run_blocking(state.save_maintenance, MAINTENANCE_FILE)
        await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, "maintenance on", "")
        msg = f"Maintenance mode **enabled**. Reason: {state.maintenance_reason}"
        if cancelled:
            msg += f" Cancelled pending countdowns for: {', '.join(f'`{c}`' for c in cancelled)}."
        await ctx.send(msg)
        await send_announcement(ctx, f"**Maintenance mode enabled.** {state.maintenance_reason}")
    elif toggle == "off":
        state.maintenance_mode = False
        state.maintenance_reason = ""
        logging.info(f"User {ctx.author} disabled maintenance mode")
        await docker_control.run_blocking(state.save_maintenance, MAINTENANCE_FILE)
        await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, "maintenance off", "")
        await ctx.send("Maintenance mode **disabled**. All commands are available again.")
        await send_announcement(ctx, "**Maintenance mode ended.** All commands are available again.")
    else:
        await ctx.send("Usage: `!maintenance on [reason]` or `!maintenance off`")


# default_permissions hides /perm from non-admins in Discord's slash picker, but
# that's a UI hint only -- the @commands.has_permissions checks below are the
# actual security boundary and are applied to every subcommand explicitly (not
# just the group) so a non-admin's /perm add is denied regardless of whether the
# hybrid slash bridge walks parent-group checks the same way the text path does.
@bot.hybrid_group()
@app_commands.default_permissions(administrator=True)
@commands.has_permissions(administrator=True)
async def perm(ctx):
    """Manages permissions (Admins only)."""
    if ctx.invoked_subcommand is None:
        await ctx.send("Usage: `!perm <add|remove|list>`")


@perm.command(name="add")
@app_commands.describe(action="Permission action to grant", role_name="Discord role name")
@commands.has_permissions(administrator=True)
async def perm_add(ctx, action: str, *, role_name: str):
    """Adds a role to an action."""
    await _defer(ctx)
    if action not in VALID_ACTIONS:
        await ctx.send(f"Unknown action `{action}`. Valid actions: {', '.join(sorted(VALID_ACTIONS))}")
        return
    await docker_control.run_blocking(permissions.add_role, action, role_name)
    logging.info(f"User {ctx.author} ADDED role '{role_name}' to action '{action}'")
    await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, f"perm add {action} {role_name}", "")
    await ctx.send(f"Added role {role_name} to {action}")


@perm.command(name="remove")
@app_commands.describe(action="Permission action to revoke", role_name="Discord role name")
@commands.has_permissions(administrator=True)
async def perm_remove(ctx, action: str, *, role_name: str):
    """Removes a role from an action."""
    await _defer(ctx)
    if action not in VALID_ACTIONS:
        await ctx.send(f"Unknown action `{action}`. Valid actions: {', '.join(sorted(VALID_ACTIONS))}")
        return
    await docker_control.run_blocking(permissions.remove_role, action, role_name)
    logging.info(f"User {ctx.author} REMOVED role '{role_name}' from action '{action}'")
    await docker_control.run_blocking(history.record, HISTORY_FILE, ctx.author, f"perm remove {action} {role_name}", "")
    await ctx.send(f"Removed role {role_name} from {action}")


@perm.command(name="list")
@commands.has_permissions(administrator=True)
async def perm_list(ctx):
    """Lists all permissions."""
    await _defer(ctx)
    logging.info(f"User {ctx.author} requested PERM LIST")
    data = await docker_control.run_blocking(permissions.list_permissions)
    lines = [f"{k}: {', '.join(v)}" for k, v in data.items()]
    await ctx.send("\n".join(lines))


@perm.error
async def perm_error(ctx, error):
    logging.warning(
        f"Perm command error: {error} "
        f"(Command: {ctx.command}, Subcommand: {ctx.invoked_subcommand}, Passed: {ctx.subcommand_passed})"
    )
    if isinstance(error, commands.MissingRequiredArgument):
        # Check which subcommand was attempted.
        # If argument parsing fails, invoked_subcommand is often None, but subcommand_passed is set.
        attempted_sub = ctx.subcommand_passed
        if ctx.invoked_subcommand:
            attempted_sub = ctx.invoked_subcommand.name

        if attempted_sub:
            attempted_sub = attempted_sub.lower()

        if attempted_sub == "add":
            await ctx.send("Usage: `!perm add <action> <role_name>`")
        elif attempted_sub == "remove":
            await ctx.send("Usage: `!perm remove <action> <role_name>`")
        else:
            await ctx.send("Usage: `!perm <add|remove|list> ...`")
    elif isinstance(error, commands.UserInputError):
        # Any other user input error within the perm group should show generic usage.
        await ctx.send("Usage: `!perm <add|remove|list> ...`")
    elif isinstance(error, commands.CheckFailure):
        return  # global on_command_error already handles CheckFailure
    else:
        await on_command_error(ctx, error)


def main():
    # Load persisted maintenance mode before the event loop starts. This runs
    # exactly once per process (unlike on_ready, which can fire again on
    # reconnect) and there's no event loop yet to stall, so the blocking read
    # happens directly rather than via run_blocking.
    state.load_maintenance(MAINTENANCE_FILE)
    status = "ON" if state.maintenance_mode else "OFF"
    logging.info(f"Maintenance mode loaded from {os.path.abspath(MAINTENANCE_FILE)}: {status}")
    threading.Thread(target=start_api, daemon=True).start()
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
