import asyncio
import logging
import os
from datetime import datetime
from collections import deque

import uvicorn
from fastapi import FastAPI
import discord
from discord.ext import commands

from . import docker_control
from .config import BOT_TOKEN, STATUS_PORT, SHUTDOWN_DELAY, ALLOWED_CONTAINERS, DISCORD_GUILD_ID, LOG_FILE
from . import permissions

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Ensure log directory exists
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)]
)

app = FastAPI()


@app.get("/status")
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
                recent_logs = [line.strip() for line in recent_logs]
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
    target = container_name or (ALLOWED_CONTAINERS[0] if ALLOWED_CONTAINERS else None)
    if not target:
        await ctx.send("No container specified.")
        return
    if target not in ALLOWED_CONTAINERS:
        await ctx.send(f"Container {target} is not allowed.")
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
    target = container_name or (ALLOWED_CONTAINERS[0] if ALLOWED_CONTAINERS else None)
    if not target:
        await ctx.send("No container specified.")
        return
    if target not in ALLOWED_CONTAINERS:
        await ctx.send(f"Container {target} is not allowed.")
        return
    logging.info(f"User {ctx.author} requested STOP for container '{target}'")
    await ctx.send(f"Server {target} will stop in {SHUTDOWN_DELAY//60} minutes (countdown started).")
    # announce immediately
    msg = f"Server will shut down in {SHUTDOWN_DELAY//60} minutes. Please prepare to log off."
    # announce in discord channel
    await ctx.send(msg)
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
    target = container_name or (ALLOWED_CONTAINERS[0] if ALLOWED_CONTAINERS else None)
    if not target:
        await ctx.send("No container specified.")
        return
    if target not in ALLOWED_CONTAINERS:
        await ctx.send(f"Container {target} is not allowed.")
        return
    logging.info(f"User {ctx.author} requested RESTART for container '{target}'")
    await ctx.send(f"Server {target} will restart in {SHUTDOWN_DELAY//60} minutes (countdown started).")
    msg = f"Server will restart in {SHUTDOWN_DELAY//60} minutes. Please prepare to log off."
    await ctx.send(msg)
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
    if container_name and container_name not in ALLOWED_CONTAINERS:
        return
    target = container_name or (ALLOWED_CONTAINERS[0] if ALLOWED_CONTAINERS else None)
    if not target:
        await ctx.send("No container configured")
        return
    logging.info(f"User {ctx.author} requested STATUS for container '{target}'")
    res = await docker_control.run_blocking(docker_control.container_status, target)
    await ctx.send(f"Status for {target}: {res}")


@bot.command(name="guide")
async def guide(ctx):
    """Shows a simple usage guide."""
    lines = [
        "**Valheim Bot Guide**",
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
