import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

ALLOWED_CONTAINERS = [c.strip() for c in os.getenv("ALLOWED_CONTAINERS", "").split(",") if c.strip()]

DEFAULT_ALLOWED_ROLES = [r.strip() for r in os.getenv("DEFAULT_ALLOWED_ROLES", "ServerAdmin").split(",") if r.strip()]

IN_GAME_ANNOUNCE_CMD = os.getenv("IN_GAME_ANNOUNCE_CMD", "screen -S valheim -p 0 -X stuff \"say {message}\\015\"")

STATUS_PORT = int(os.getenv("STATUS_PORT", "8000"))

SHUTDOWN_DELAY = int(os.getenv("SHUTDOWN_DELAY", "300"))

PERMISSIONS_FILE = os.getenv("PERMISSIONS_FILE", "permissions.json")

DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
