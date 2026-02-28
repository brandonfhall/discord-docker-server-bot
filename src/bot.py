import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
from collections import deque

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Depends, Query
import discord
from discord.ext import commands

from . import docker_control
from .config import (
    BOT_TOKEN, STATUS_TOKEN, STATUS_PORT, SHUTDOWN_DELAY, ALLOWED_CONTAINERS,
    DISCORD_GUILD_ID, LOG_FILE, ANNOUNCE_CHANNEL_ID, ANNOUNCE_ROLE_ID
)
from . import permissions

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Ensure log directory exists
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir)

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
    content = message
    if ANNOUNCE_ROLE_ID:
        content = f"<@&{ANNOUNCE_ROLE_ID}> {message}"

    target_channel = ctx.channel
    if ANNOUNCE_CHANNEL_ID:
        found = bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if found:
            target_channel = found
        else:
            logging.warning(f"Configured ANNOUNCE_CHANNEL_ID {ANNOUNCE_CHANNEL_ID} not found.")

    await target_channel.send(content)
    # If we sent it elsewhere, confirm in the command channel
    if target_channel.id != ctx.channel.id:
        await ctx.send(f"Announcement sent to {target_channel.mention}.")


@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user} at {datetime.utcnow().isoformat()} UTC")
    logging.info(f"Logging to file: {os.path.abspath(LOG_FILE)}")
    logging.info(f"Permissions file: {os.path.abspath(permissions.PERMISSIONS_FILE)}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        logging.warning(f"Permission denied for user {ctx.author} on command {ctx.command}")
        await ctx.send("You do not have permission to use this command.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        logging.error(f"Command error: {error}", exc_info=True)


@bot.command()
@has_permission("start")
async def start(ctx, container_name: str = None):
    """Starts the container."""
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested START for container '{target}'")
    await ctx.send(f"Starting {target}...")
    res = await docker_control.run_blocking(docker_control.start_container, target)
    logging.info(f"START result for {ctx.author}: {res}")
    await ctx.send(res)


@bot.command()
@has_permission("stop")
async def stop(ctx, container_name: str = None):
    """Stops the container (with countdown)."""
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested STOP for container '{target}'")
    await ctx.send(f"Server {target} will stop in {SHUTDOWN_DELAY//60} minutes (countdown started).")
    # announce immediately
    msg = f"Server will shut down in {SHUTDOWN_DELAY//60} minutes. Please prepare to log off."
    # announce in discord channel
    await send_announcement(ctx, msg)
    # announce in-game
    await docker_control.run_blocking(docker_control.announce_in_game, target, msg)

    # schedule stop
    async def do_stop():
        await asyncio.sleep(SHUTDOWN_DELAY)
        result = await docker_control.run_blocking(docker_control.stop_container, target)
        logging.info(f"STOP execution result for {target}: {result}")
        await ctx.send(f"Stop result: {result}")

    bot.loop.create_task(do_stop())


@bot.command()
@has_permission("restart")
async def restart(ctx, container_name: str = None):
    """Restarts the container (with countdown)."""
    target = await resolve_container(ctx, container_name)
    if not target:
        return
    logging.info(f"User {ctx.author} requested RESTART for container '{target}'")
    await ctx.send(f"Server {target} will restart in {SHUTDOWN_DELAY//60} minutes (countdown started).")
    msg = f"Server will restart in {SHUTDOWN_DELAY//60} minutes. Please prepare to log off."
    await send_announcement(ctx, msg)
    await docker_control.run_blocking(docker_control.announce_in_game, target, msg)

    async def do_restart():
        await asyncio.sleep(SHUTDOWN_DELAY)
        result = await docker_control.run_blocking(docker_control.restart_container, target)
        logging.info(f"RESTART execution result for {target}: {result}")
        await ctx.send(f"Restart result: {result}")

    bot.loop.create_task(do_restart())


@bot.command(name="status")
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
async def announce(ctx, arg1: str, *, arg2: str = None):
    """
    Send an in-game announcement.
    Usage: !announce <message> (if single container)
           !announce <container_name> <message> (if multiple)
    """
    target = None
    message = None

    if arg1 in ALLOWED_CONTAINERS:
        target = arg1
        message = arg2
    elif len(ALLOWED_CONTAINERS) == 1:
        target = ALLOWED_CONTAINERS[0]
        message = f"{arg1} {arg2}" if arg2 else arg1

    if not target or not message:
        await ctx.send(f"Usage: !announce <container_name> <message>\nAvailable: {', '.join(ALLOWED_CONTAINERS)}")
        return

    res = await docker_control.run_blocking(docker_control.announce_in_game, target, message)
    await ctx.send(f"Sent to {target}: {res}")


@bot.command(name="guide")
async def guide(ctx):
    """Shows a simple usage guide."""
    lines = [
        "**Docker Bot Guide**",
        "Use `!help` for detailed command usage.",
        "",
        "**Control**",
        "`!start`   : Start the server",
        "`!stop`    : Stop the server (with delay)",
        "`!restart` : Restart the server (with delay)",
        "`!status`  : Show server status",
        "",
        "**Permissions** (Admins)",
        "`!perm list`   : List allowed roles",
        "`!perm add`    : Add role to action",
        "`!perm remove` : Remove role from action"
    ]
    logging.info(f"User {ctx.author} requested GUIDE")
    await ctx.send("\n".join(lines))


@bot.group()
@commands.has_permissions(administrator=True)
async def perm(ctx):
    """Manages permissions (Admins only)."""
    if ctx.invoked_subcommand is None:
        await ctx.send("subcommands: add, remove, list")


@perm.command(name="add")
async def perm_add(ctx, action: str, *, role_name: str):
    """Adds a role to an action."""
    permissions.add_role(action, role_name)
    logging.info(f"User {ctx.author} ADDED role '{role_name}' to action '{action}'")
    await ctx.send(f"Added role {role_name} to {action}")


@perm.command(name="remove")
async def perm_remove(ctx, action: str, *, role_name: str):
    """Removes a role from an action."""
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
