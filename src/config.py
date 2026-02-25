import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

ALLOWED_CONTAINERS = [c.strip() for c in os.getenv("ALLOWED_CONTAINERS", "").split(",") if c.strip()]

DEFAULT_ALLOWED_ROLES = [r.strip() for r in os.getenv("DEFAULT_ALLOWED_ROLES", "ServerAdmin").split(",") if r.strip()]

CONTAINER_MESSAGE_CMD = os.getenv("CONTAINER_MESSAGE_CMD", "echo \"Message: {message}\"")

STATUS_PORT = int(os.getenv("STATUS_PORT", "8000"))

DOCKER_MAX_WORKERS = int(os.getenv("DOCKER_MAX_WORKERS", "2"))

SHUTDOWN_DELAY = int(os.getenv("SHUTDOWN_DELAY", "300"))

PERMISSIONS_FILE = os.getenv("PERMISSIONS_FILE", "data/permissions.json").strip()

LOG_FILE = os.getenv("LOG_FILE", "data/bot.log").strip()

DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
