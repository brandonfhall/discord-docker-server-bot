import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from . import docker_control, history, permissions
from .api import start_api
from .config import (
    BOT_TOKEN, STATUS_TOKEN, SHUTDOWN_DELAY, ALLOWED_CONTAINERS,
    DISCORD_GUILD_ID, LOG_FILE, ANNOUNCE_CHANNEL_ID, ANNOUNCE_ROLE_ID,
    ALLOWED_CHANNEL_IDS, COMMAND_COOLDOWN, CRASH_CHECK_INTERVAL,
    CRASH_ALERT_CHANNEL_ID, HISTORY_FILE
)
from .logging_config import setup_logging
from .state import state

VALID_ACTIONS = permissions.ALL_ACTIONS


setup_logging(LOG_FILE, os.getenv("LOG_LEVEL", "INFO"), [BOT_TOKEN, STATUS_TOKEN])

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.check
async def check_guild(ctx):
    # If DISCORD_GUILD_ID is set, reject commands from other guilds or DMs
    if DISCORD_GUILD_ID and (not ctx.guild or ctx.guild.id != DISCORD_GUILD_ID):
        return False

    # If ALLOWED_CHANNEL_IDS is set, reject commands from other channels
    if ALLOWED_CHANNEL_IDS and ctx.channel.id not in ALLOWED_CHANNEL_IDS:
        return False

    return True


def has_permission(action: str):
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        allowed = permissions.is_member_allowed(action, ctx.author)
        return allowed

    return commands.check(predicate)


async def resolve_container(ctx, name: str):
    """Helper to resolve the target container name."""
    if name:
        if name in ALLOWED_CONTAINERS:
            return name
        await ctx.send(f"Container '{name}' is not in the allowed list.")
        return None

    if len(ALLOWED_CONTAINERS) == 1:
        return ALLOWED_CONTAINERS[0]

    if len(ALLOWED_CONTAINERS) > 1:
        await ctx.send(f"Multiple containers configured. Please specify one: {', '.join(ALLOWED_CONTAINERS)}")
        return None

    await ctx.send("No allowed containers configured.")
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

    try:
        await target_channel.send(content)
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
    logging.info(f"Command received: '{ctx.message.content}' from {ctx.author} ({ctx.author.id})")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        logging.info(f"Bot sent: '{message.content}' to {message.channel}")
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        # If the command came from a disallowed channel, silently ignore it
        if ALLOWED_CHANNEL_IDS and ctx.channel.id not in ALLOWED_CHANNEL_IDS:
            return
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
        content = ctx.message.content or ""
        if content.startswith(f"{bot.command_prefix}perm"):
            # Only respond with usage if the user is allowed to use this command.
            if ctx.author.guild_permissions.administrator:
                await ctx.send("Usage: `!perm <add|remove|list> ...`")
    else:
        logging.error(f"Command error: {error}", exc_info=True)


@tasks.loop(seconds=CRASH_CHECK_INTERVAL)
async def crash_check_loop():
    """Polls container statuses and alerts if one unexpectedly stops."""
    # state.last_known_status is populated here on first run
    for name in ALLOWED_CONTAINERS:
        try:
            current = await docker_control.run_blocking(docker_control.container_status, name)
        except Exception:
            continue
        prev = state.last_known_status.get(name)
        state.last_known_status[name] = current
        # Alert when a container transitions from running to non-running
        if prev == "running" and current and current != "running":
            logging.warning(f"Crash alert: container '{name}' changed from running to {current}")
            channel_id = CRASH_ALERT_CHANNEL_ID or ANNOUNCE_CHANNEL_ID
            if channel_id:
                ch = bot.get_channel(channel_id)
                if ch:
                    try:
                        await ch.send(f"**Crash Alert:** Container `{name}` is now **{current}** (was running).")
                    except Exception as e:
                        logging.error(f"Failed to send crash alert: {e}")


@crash_check_loop.before_loop
async def _before_crash_check():
    await bot.wait_until_ready()
    # Seed initial statuses so we don't false-alert on startup
    for name in ALLOWED_CONTAINERS:
        try:
            state.last_known_status[name] = await docker_control.run_blocking(docker_control.container_status, name)
        except Exception:
            pass




@bot.command()
@has_permission("start")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def start(ctx, container_name: str = None):
    """Starts the container."""
    if state.is_maintenance_active(ctx.command.qualified_name if ctx.command else ""):
        await ctx.send(f"Bot is in maintenance mode. {state.maintenance_reason}")
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested START for container '{target}'")
    history.record(HISTORY_FILE,ctx.author, "start", target)
    await ctx.send(f"Starting {target}...")
    res = await docker_control.run_blocking(docker_control.start_container, target)
    logging.info(f"START result for {ctx.author}: {res.message}")
    await ctx.send(res.message)


async def _delayed_container_op(ctx, arg1, arg2, *, action, now_action, docker_func,
                                immediate_msg, countdown_msg_tpl):
    """Shared logic for stop and restart commands with optional 'now' flag."""
    if state.is_maintenance_active(ctx.command.qualified_name if ctx.command else ""):
        await ctx.send(f"Bot is in maintenance mode. {state.maintenance_reason}")
        return

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
        if not ctx.author.guild_permissions.administrator and not permissions.is_member_allowed(now_action, ctx.author):
            await ctx.send(f"You do not have permission to use `!{action} now`.")
            return
        logging.info(f"User {ctx.author} requested immediate {action.upper()} for container '{target}'")
        history.record(HISTORY_FILE, ctx.author, f"{action} now", target)
        state.cancel_pending(target)
        await ctx.send(f"{action.capitalize()}{'ping' if action == 'stop' else 'ing'} {target} immediately...")
        await send_announcement(ctx, immediate_msg)
        await docker_control.run_blocking(docker_control.announce_in_game, target, immediate_msg)
        res = await docker_control.run_blocking(docker_func, target)
        logging.info(f"Immediate {action.upper()} result for {ctx.author}: {res.message}")
        await ctx.send(f"{action.capitalize()} result: {res.message}")
        return

    logging.info(f"User {ctx.author} requested {action.upper()} for container '{target}'")

    if state.has_pending_op(target):
        await ctx.send(f"A shutdown or restart is already scheduled for `{target}`. Ignoring duplicate request.")
        return

    history.record(HISTORY_FILE, ctx.author, action, target)
    state.pending_ops[target] = bot.loop.create_future()

    countdown_msg = countdown_msg_tpl.format(minutes=SHUTDOWN_DELAY // 60)
    await ctx.send(f"Server {target} will {action} in {SHUTDOWN_DELAY // 60} minutes (countdown started).")
    await send_announcement(ctx, countdown_msg)
    await docker_control.run_blocking(docker_control.announce_in_game, target, countdown_msg)

    async def do_operation():
        await asyncio.sleep(SHUTDOWN_DELAY)
        state.pending_ops.pop(target, None)
        try:
            result = await docker_control.run_blocking(docker_func, target)
            logging.info(f"{action.upper()} execution result for {target}: {result.message}")
            await ctx.send(f"{action.capitalize()} result: {result.message}")
        except Exception as e:
            logging.error(f"Error during scheduled {action} of {target}: {e}", exc_info=True)
            try:
                await ctx.send(f"Error during scheduled {action} of `{target}`: {e}")
            except Exception:
                pass

    state.pending_ops[target] = bot.loop.create_task(do_operation())


@bot.command()
@has_permission("stop")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def stop(ctx, arg1: str = None, arg2: str = None):
    """Stops the container. Use '!stop now' for immediate shutdown (requires stop_now permission)."""
    await _delayed_container_op(
        ctx, arg1, arg2,
        action="stop",
        now_action="stop_now",
        docker_func=docker_control.stop_container,
        immediate_msg="Server is shutting down NOW. Please disconnect immediately.",
        countdown_msg_tpl="Server will shut down in {minutes} minutes. Please prepare to log off.",
    )


@bot.command()
@has_permission("restart")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def restart(ctx, arg1: str = None, arg2: str = None):
    """Restarts the container (with countdown). Use '!restart now' for immediate restart (requires restart_now permission)."""
    await _delayed_container_op(
        ctx, arg1, arg2,
        action="restart",
        now_action="restart_now",
        docker_func=docker_control.restart_container,
        immediate_msg="Server is restarting NOW. Please disconnect immediately.",
        countdown_msg_tpl="Server will restart in {minutes} minutes. Please prepare to log off.",
    )


@bot.command(name="status")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def status_cmd(ctx, container_name: str = None):
    """Checks the container status."""
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested STATUS for container '{target}'")
    res = await docker_control.run_blocking(docker_control.container_status, target)
    await ctx.send(f"Status for {target}: {res}")


@bot.command()
@has_permission("announce")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def announce(ctx, arg1: str, *, arg2: str = None):
    """
    Send an in-game announcement.
    Usage: !announce <message> (if single container)
           !announce <container_name> <message> (if multiple)
    """
    if state.is_maintenance_active(ctx.command.qualified_name if ctx.command else ""):
        await ctx.send(f"Bot is in maintenance mode. {state.maintenance_reason}")
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

    history.record(HISTORY_FILE,ctx.author, "announce", target)
    res = await docker_control.run_blocking(docker_control.announce_in_game, target, message)
    await ctx.send(f"Sent to {target}: {res.message}")


@announce.error
async def announce_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: `!announce <message>` or `!announce <container_name> <message>`")
    else:
        await on_command_error(ctx, error)


@bot.command(name="guide")
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
        "`!perm remove` : Remove role from action"
    ]
    logging.info(f"User {ctx.author} requested GUIDE")
    await ctx.send("\n".join(lines))


@bot.command(name="logs")
@has_permission("logs")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def logs_cmd(ctx, arg1: str = None, arg2: str = None):
    """View recent container logs. Usage: !logs [container] [lines]"""
    container_name = None
    lines = 25
    for arg in (arg1, arg2):
        if arg is None:
            continue
        if arg.isdigit():
            lines = min(int(arg), 50)
        elif arg in ALLOWED_CONTAINERS:
            container_name = arg

    if state.is_maintenance_active(ctx.command.qualified_name if ctx.command else ""):
        await ctx.send(f"Bot is in maintenance mode. {state.maintenance_reason}")
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested LOGS for container '{target}' ({lines} lines)")
    history.record(HISTORY_FILE,ctx.author, f"logs {lines}", target)
    result = await docker_control.run_blocking(docker_control.container_logs, target, lines)
    if result is None:
        await ctx.send(f"Could not fetch logs for {target}.")
        return
    # Truncate to fit Discord's 2000 char limit
    output = result[-1900:] if len(result) > 1900 else result
    if not output.strip():
        await ctx.send(f"No recent logs for {target}.")
        return
    await ctx.send(f"**Logs for {target}** (last {lines} lines):\n```\n{output}\n```")


@bot.command(name="stats")
@has_permission("stats")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def stats_cmd(ctx, container_name: str = None):
    """Show container CPU and memory usage."""
    if state.is_maintenance_active(ctx.command.qualified_name if ctx.command else ""):
        await ctx.send(f"Bot is in maintenance mode. {state.maintenance_reason}")
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested STATS for container '{target}'")
    history.record(HISTORY_FILE,ctx.author, "stats", target)
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


@bot.command(name="history")
@has_permission("history")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def history_cmd(ctx, count: int = 10):
    """View recent command history. Usage: !history [count]"""
    logging.info(f"User {ctx.author} requested HISTORY")
    count = max(1, min(count, 25))
    entries = history.load(HISTORY_FILE)
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


@bot.command(name="maintenance")
@has_permission("maintenance")
async def maintenance_cmd(ctx, toggle: str = None, *, reason: str = ""):
    """Toggle maintenance mode. Usage: !maintenance on/off [reason]"""
    if toggle is None:
        status = "ON" if state.maintenance_mode else "OFF"
        await ctx.send(f"Maintenance mode is **{status}**." + (f" Reason: {state.maintenance_reason}" if state.maintenance_reason else ""))
        return
    toggle = toggle.lower()
    if toggle == "on":
        state.maintenance_mode = True
        state.maintenance_reason = reason or "No reason given."
        logging.info(f"User {ctx.author} enabled maintenance mode: {state.maintenance_reason}")
        history.record(HISTORY_FILE, ctx.author, "maintenance on", "")
        await ctx.send(f"Maintenance mode **enabled**. Reason: {state.maintenance_reason}")
        await send_announcement(ctx, f"**Maintenance mode enabled.** {state.maintenance_reason}")
    elif toggle == "off":
        state.maintenance_mode = False
        state.maintenance_reason = ""
        logging.info(f"User {ctx.author} disabled maintenance mode")
        history.record(HISTORY_FILE,ctx.author, "maintenance off", "")
        await ctx.send("Maintenance mode **disabled**. All commands are available again.")
        await send_announcement(ctx, "**Maintenance mode ended.** All commands are available again.")
    else:
        await ctx.send("Usage: `!maintenance on [reason]` or `!maintenance off`")


@bot.group()
@commands.has_permissions(administrator=True)
async def perm(ctx):
    """Manages permissions (Admins only)."""
    if ctx.invoked_subcommand is None:
        await ctx.send("Usage: `!perm <add|remove|list>`")


@perm.command(name="add")
async def perm_add(ctx, action: str, *, role_name: str):
    """Adds a role to an action."""
    if action not in VALID_ACTIONS:
        await ctx.send(f"Unknown action `{action}`. Valid actions: {', '.join(sorted(VALID_ACTIONS))}")
        return
    permissions.add_role(action, role_name)
    logging.info(f"User {ctx.author} ADDED role '{role_name}' to action '{action}'")
    await ctx.send(f"Added role {role_name} to {action}")


@perm.command(name="remove")
async def perm_remove(ctx, action: str, *, role_name: str):
    """Removes a role from an action."""
    if action not in VALID_ACTIONS:
        await ctx.send(f"Unknown action `{action}`. Valid actions: {', '.join(sorted(VALID_ACTIONS))}")
        return
    permissions.remove_role(action, role_name)
    logging.info(f"User {ctx.author} REMOVED role '{role_name}' from action '{action}'")
    await ctx.send(f"Removed role {role_name} from {action}")


@perm.command(name="list")
async def perm_list(ctx):
    """Lists all permissions."""
    logging.info(f"User {ctx.author} requested PERM LIST")
    data = permissions.list_permissions()
    lines = [f"{k}: {', '.join(v)}" for k, v in data.items()]
    await ctx.send("\n".join(lines))


@perm.error
async def perm_error(ctx, error):
    logging.warning(f"Perm command error: {error} (Command: {ctx.command}, Subcommand: {ctx.invoked_subcommand}, Passed: {ctx.subcommand_passed})")
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
    loop = asyncio.get_event_loop()
    # start API in background thread via loop.run_in_executor
    loop.run_in_executor(None, start_api)
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
