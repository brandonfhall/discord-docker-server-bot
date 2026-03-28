import asyncio
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from collections import deque

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse
import discord
from discord.ext import commands, tasks

from . import docker_control
from .config import (
    BOT_TOKEN, STATUS_TOKEN, STATUS_PORT, SHUTDOWN_DELAY, ALLOWED_CONTAINERS,
    DISCORD_GUILD_ID, LOG_FILE, ANNOUNCE_CHANNEL_ID, ANNOUNCE_ROLE_ID,
    ALLOWED_CHANNEL_IDS, COMMAND_COOLDOWN, CRASH_CHECK_INTERVAL,
    CRASH_ALERT_CHANNEL_ID, HISTORY_FILE
)
from . import permissions

VALID_ACTIONS = {"start", "stop", "stop_now", "restart", "restart_now", "announce",
                 "logs", "stats", "maintenance", "history"}

# Tracks in-flight stop/restart tasks per container name so duplicate
# commands don't stack up and trigger multiple Docker operations.
_pending_ops: dict = {}

# Maintenance mode: when True, all container-control commands are blocked.
_maintenance_mode = False
_maintenance_reason = ""

# Crash alerting: tracks last-known container status for change detection.
_last_known_status: dict = {}


def _cancel_pending(container: str):
    task = _pending_ops.pop(container, None)
    if task and not task.done():
        task.cancel()


# ---------------------------------------------------------------------------
# Command history / audit log
# ---------------------------------------------------------------------------

def _load_history() -> list:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_history(entries: list):
    hist_dir = os.path.dirname(HISTORY_FILE)
    if hist_dir and not os.path.exists(hist_dir):
        os.makedirs(hist_dir)
    # Keep only last 200 entries
    entries = entries[-200:]
    with open(HISTORY_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def _record_history(user: str, command: str, container: str = ""):
    entries = _load_history()
    entries.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": str(user),
        "command": command,
        "container": container,
    })
    _save_history(entries)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Ensure log directory exists
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir)

class _RedactingFilter(logging.Filter):
    """Strips sensitive token values from log records before they reach any handler."""
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


logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        # Rotate logs: Max 5MB, keep 1 backup.
        # This prevents the log file from growing indefinitely and crashing the status reader.
        RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=1)
    ]
)

# Attach the redacting filter to every handler so tokens never appear on disk or stdout.
_redact_filter = _RedactingFilter([BOT_TOKEN, STATUS_TOKEN])
for _handler in logging.root.handlers:
    _handler.addFilter(_redact_filter)

app = FastAPI()


async def verify_token(
    x_auth_token: str = Header(None, alias="X-Auth-Token"),
    query_token: str = Query(None, alias="token")
):
    # If STATUS_TOKEN is empty (user explicitly disabled it), allow access
    if not STATUS_TOKEN:
        return

    token = x_auth_token or query_token
    if not token or token != STATUS_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
def root():
    return RedirectResponse(url="/status")


@app.get("/status", dependencies=[Depends(verify_token)])
def status():
    out = {}
    for name in ALLOWED_CONTAINERS:
        out[name] = docker_control.container_status(name)

    # Get current permissions
    current_perms = permissions.list_permissions()

    # Get recent logs (last 50 lines)
    recent_logs = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                recent_logs = list(deque(f, maxlen=50))
                # Redact tokens
                recent_logs = [line.strip().replace(BOT_TOKEN, "[REDACTED]") for line in recent_logs]
                if STATUS_TOKEN and STATUS_TOKEN != BOT_TOKEN:
                    recent_logs = [line.replace(STATUS_TOKEN, "[REDACTED]") for line in recent_logs]
        except Exception as e:
            recent_logs = [f"Error reading logs: {e}"]

    return {
        "ok": True,
        "containers": out,
        "permissions": current_perms,
        "logs": recent_logs
    }


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
    global _last_known_status
    for name in ALLOWED_CONTAINERS:
        try:
            current = await docker_control.run_blocking(docker_control.container_status, name)
        except Exception:
            continue
        prev = _last_known_status.get(name)
        _last_known_status[name] = current
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
            _last_known_status[name] = await docker_control.run_blocking(docker_control.container_status, name)
        except Exception:
            pass


def _check_maintenance(ctx) -> bool:
    """Return True if maintenance mode is active (and the command should be blocked)."""
    # Admin-only commands (perm, maintenance) are not blocked
    if ctx.command and ctx.command.qualified_name in ("maintenance", "perm", "perm add", "perm remove", "perm list", "guide", "history"):
        return False
    return _maintenance_mode


@bot.command()
@has_permission("start")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def start(ctx, container_name: str = None):
    """Starts the container."""
    if _check_maintenance(ctx):
        await ctx.send(f"Bot is in maintenance mode. {_maintenance_reason}")
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested START for container '{target}'")
    _record_history(ctx.author, "start", target)
    await ctx.send(f"Starting {target}...")
    res = await docker_control.run_blocking(docker_control.start_container, target)
    logging.info(f"START result for {ctx.author}: {res}")
    await ctx.send(res)


@bot.command()
@has_permission("stop")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def stop(ctx, arg1: str = None, arg2: str = None):
    """Stops the container. Use '!stop now' for immediate shutdown (requires stop_now permission)."""
    if _check_maintenance(ctx):
        await ctx.send(f"Bot is in maintenance mode. {_maintenance_reason}")
        return
    # Parse arguments: either could be the "now" flag or a container name
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
        if not ctx.author.guild_permissions.administrator and not permissions.is_member_allowed("stop_now", ctx.author):
            await ctx.send("You do not have permission to use `!stop now`.")
            return

        logging.info(f"User {ctx.author} requested immediate STOP for container '{target}'")
        _record_history(ctx.author, "stop now", target)
        _cancel_pending(target)
        await ctx.send(f"Stopping {target} immediately...")
        msg = "Server is shutting down NOW. Please disconnect immediately."
        await send_announcement(ctx, msg)
        await docker_control.run_blocking(docker_control.announce_in_game, target, msg)
        res = await docker_control.run_blocking(docker_control.stop_container, target)
        logging.info(f"Immediate STOP result for {ctx.author}: {res}")
        await ctx.send(f"Stop result: {res}")
        return

    logging.info(f"User {ctx.author} requested STOP for container '{target}'")

    if target in _pending_ops and not _pending_ops[target].done():
        await ctx.send(f"A shutdown or restart is already scheduled for `{target}`. Ignoring duplicate request.")
        return

    _record_history(ctx.author, "stop", target)

    # Reserve the slot immediately before any awaits so concurrent duplicate
    # commands see the container as pending even during the countdown sends.
    _pending_ops[target] = bot.loop.create_future()

    await ctx.send(f"Server {target} will stop in {SHUTDOWN_DELAY//60} minutes (countdown started).")
    msg = f"Server will shut down in {SHUTDOWN_DELAY//60} minutes. Please prepare to log off."
    await send_announcement(ctx, msg)
    await docker_control.run_blocking(docker_control.announce_in_game, target, msg)

    async def do_stop():
        await asyncio.sleep(SHUTDOWN_DELAY)
        _pending_ops.pop(target, None)
        try:
            result = await docker_control.run_blocking(docker_control.stop_container, target)
            logging.info(f"STOP execution result for {target}: {result}")
            await ctx.send(f"Stop result: {result}")
        except Exception as e:
            logging.error(f"Error during scheduled stop of {target}: {e}", exc_info=True)

    _pending_ops[target] = bot.loop.create_task(do_stop())


@bot.command()
@has_permission("restart")
@commands.cooldown(1, COMMAND_COOLDOWN, commands.BucketType.user)
async def restart(ctx, arg1: str = None, arg2: str = None):
    """Restarts the container (with countdown). Use '!restart now' for immediate restart (requires restart_now permission)."""
    if _check_maintenance(ctx):
        await ctx.send(f"Bot is in maintenance mode. {_maintenance_reason}")
        return

    # Parse arguments: either could be the "now" flag or a container name
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
        if not ctx.author.guild_permissions.administrator and not permissions.is_member_allowed("restart_now", ctx.author):
            await ctx.send("You do not have permission to use `!restart now`.")
            return

        logging.info(f"User {ctx.author} requested immediate RESTART for container '{target}'")
        _record_history(ctx.author, "restart now", target)
        _cancel_pending(target)
        await ctx.send(f"Restarting {target} immediately...")
        msg = "Server is restarting NOW. Please disconnect immediately."
        await send_announcement(ctx, msg)
        await docker_control.run_blocking(docker_control.announce_in_game, target, msg)
        res = await docker_control.run_blocking(docker_control.restart_container, target)
        logging.info(f"Immediate RESTART result for {ctx.author}: {res}")
        await ctx.send(f"Restart result: {res}")
        return

    logging.info(f"User {ctx.author} requested RESTART for container '{target}'")

    if target in _pending_ops and not _pending_ops[target].done():
        await ctx.send(f"A shutdown or restart is already scheduled for `{target}`. Ignoring duplicate request.")
        return

    _record_history(ctx.author, "restart", target)

    # Reserve the slot immediately before any awaits so concurrent duplicate
    # commands see the container as pending even during the countdown sends.
    _pending_ops[target] = bot.loop.create_future()

    await ctx.send(f"Server {target} will restart in {SHUTDOWN_DELAY//60} minutes (countdown started).")
    msg = f"Server will restart in {SHUTDOWN_DELAY//60} minutes. Please prepare to log off."
    await send_announcement(ctx, msg)
    await docker_control.run_blocking(docker_control.announce_in_game, target, msg)

    async def do_restart():
        await asyncio.sleep(SHUTDOWN_DELAY)
        _pending_ops.pop(target, None)
        try:
            result = await docker_control.run_blocking(docker_control.restart_container, target)
            logging.info(f"RESTART execution result for {target}: {result}")
            await ctx.send(f"Restart result: {result}")
        except Exception as e:
            logging.error(f"Error during scheduled restart of {target}: {e}", exc_info=True)

    _pending_ops[target] = bot.loop.create_task(do_restart())


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
    if _check_maintenance(ctx):
        await ctx.send(f"Bot is in maintenance mode. {_maintenance_reason}")
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

    _record_history(ctx.author, "announce", target)
    res = await docker_control.run_blocking(docker_control.announce_in_game, target, message)
    await ctx.send(f"Sent to {target}: {res}")


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

    if _check_maintenance(ctx):
        await ctx.send(f"Bot is in maintenance mode. {_maintenance_reason}")
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested LOGS for container '{target}' ({lines} lines)")
    _record_history(ctx.author, f"logs {lines}", target)
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
    if _check_maintenance(ctx):
        await ctx.send(f"Bot is in maintenance mode. {_maintenance_reason}")
        return
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested STATS for container '{target}'")
    _record_history(ctx.author, "stats", target)
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
    entries = _load_history()
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
    global _maintenance_mode, _maintenance_reason
    if toggle is None:
        state = "ON" if _maintenance_mode else "OFF"
        await ctx.send(f"Maintenance mode is **{state}**." + (f" Reason: {_maintenance_reason}" if _maintenance_reason else ""))
        return
    toggle = toggle.lower()
    if toggle == "on":
        _maintenance_mode = True
        _maintenance_reason = reason or "No reason given."
        logging.info(f"User {ctx.author} enabled maintenance mode: {_maintenance_reason}")
        _record_history(ctx.author, "maintenance on", "")
        await ctx.send(f"Maintenance mode **enabled**. Reason: {_maintenance_reason}")
        await send_announcement(ctx, f"**Maintenance mode enabled.** {_maintenance_reason}")
    elif toggle == "off":
        _maintenance_mode = False
        _maintenance_reason = ""
        logging.info(f"User {ctx.author} disabled maintenance mode")
        _record_history(ctx.author, "maintenance off", "")
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


def start_api():
    config = uvicorn.Config(app, host="0.0.0.0", port=STATUS_PORT, log_level="warning")
    server = uvicorn.Server(config)
    return server.run()


def main():
    loop = asyncio.get_event_loop()
    # start API in background thread via loop.run_in_executor
    loop.run_in_executor(None, start_api)
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
