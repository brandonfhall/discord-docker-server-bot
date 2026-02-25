import asyncio
import logging
import os
from datetime import datetime

import uvicorn
from fastapi import FastAPI
import discord
from discord.ext import commands

from . import docker_control
from .config import BOT_TOKEN, STATUS_PORT, SHUTDOWN_DELAY, ALLOWED_CONTAINERS, DISCORD_GUILD_ID
from . import permissions

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL)

app = FastAPI()


@app.get("/status")
def status():
    out = {}
    for name in ALLOWED_CONTAINERS:
        out[name] = docker_control.container_status(name)
    return {"ok": True, "containers": out}


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


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("You do not have permission to use this command.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        logging.error(f"Command error: {error}", exc_info=True)

@bot.command()
@has_permission("start")
async def start(ctx, container_name: str = None):
    target = container_name or (ALLOWED_CONTAINERS[0] if ALLOWED_CONTAINERS else None)
    if not target:
        await ctx.send("No container specified.")
        return
    if target not in ALLOWED_CONTAINERS:
        await ctx.send(f"Container {target} is not allowed.")
        return
    await ctx.send(f"Starting {target}...")
    res = await docker_control.run_blocking(docker_control.start_container, target)
    await ctx.send(res)


@bot.command()
@has_permission("stop")
async def stop(ctx, container_name: str = None):
    target = container_name or (ALLOWED_CONTAINERS[0] if ALLOWED_CONTAINERS else None)
    if not target:
        await ctx.send("No container specified.")
        return
    if target not in ALLOWED_CONTAINERS:
        await ctx.send(f"Container {target} is not allowed.")
        return
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
        await ctx.send(f"Stop result: {result}")

    bot.loop.create_task(do_stop())


@bot.command()
@has_permission("restart")
async def restart(ctx, container_name: str = None):
    target = container_name or (ALLOWED_CONTAINERS[0] if ALLOWED_CONTAINERS else None)
    if not target:
        await ctx.send("No container specified.")
        return
    if target not in ALLOWED_CONTAINERS:
        await ctx.send(f"Container {target} is not allowed.")
        return
    await ctx.send(f"Server {target} will restart in {SHUTDOWN_DELAY//60} minutes (countdown started).")
    msg = f"Server will restart in {SHUTDOWN_DELAY//60} minutes. Please prepare to log off."
    await ctx.send(msg)
    await docker_control.run_blocking(docker_control.announce_in_game, target, msg)

    async def do_restart():
        await asyncio.sleep(SHUTDOWN_DELAY)
        result = await docker_control.run_blocking(docker_control.restart_container, target)
        await ctx.send(f"Restart result: {result}")

    bot.loop.create_task(do_restart())


@bot.command(name="status")
async def status_cmd(ctx, container_name: str = None):
    if container_name and container_name not in ALLOWED_CONTAINERS:
        return
    target = container_name or (ALLOWED_CONTAINERS[0] if ALLOWED_CONTAINERS else None)
    if not target:
        await ctx.send("No container configured")
        return
    res = await docker_control.run_blocking(docker_control.container_status, target)
    await ctx.send(f"Status for {target}: {res}")


@bot.group()
@commands.has_permissions(administrator=True)
async def perm(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send("subcommands: add, remove, list")


@perm.command(name="add")
async def perm_add(ctx, action: str, *, role_name: str):
    permissions.add_role(action, role_name)
    await ctx.send(f"Added role {role_name} to {action}")


@perm.command(name="remove")
async def perm_remove(ctx, action: str, *, role_name: str):
    permissions.remove_role(action, role_name)
    await ctx.send(f"Removed role {role_name} from {action}")


@perm.command(name="list")
async def perm_list(ctx):
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
