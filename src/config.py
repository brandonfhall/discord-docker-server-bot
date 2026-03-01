import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _int_env(key: str, default: int) -> int:
    """Parse an integer environment variable, falling back to *default* if missing or invalid."""
    val = (os.getenv(key) or "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"WARNING: {key}={val!r} is not a valid integer, using default {default}", file=sys.stderr)
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required and must not be empty")

STATUS_TOKEN = os.getenv("STATUS_TOKEN")

ALLOWED_CONTAINERS = [c.strip() for c in os.getenv("ALLOWED_CONTAINERS", "").split(",") if c.strip()]
if not ALLOWED_CONTAINERS:
    raise ValueError("ALLOWED_CONTAINERS environment variable is required and must not be empty")

DEFAULT_ALLOWED_ROLES = [r.strip() for r in os.getenv("DEFAULT_ALLOWED_ROLES", "ServerAdmin").split(",") if r.strip()]

CONTAINER_MESSAGE_CMD = os.getenv("CONTAINER_MESSAGE_CMD", "echo \"Message: {message}\"")

STATUS_PORT = _int_env("STATUS_PORT", 8000)

DOCKER_MAX_WORKERS = _int_env("DOCKER_MAX_WORKERS", 2)

SHUTDOWN_DELAY = _int_env("SHUTDOWN_DELAY", 300)

PERMISSIONS_FILE = os.getenv("PERMISSIONS_FILE", "data/permissions.json").strip()

LOG_FILE = os.getenv("LOG_FILE", "data/bot.log").strip()

DISCORD_GUILD_ID = _int_env("DISCORD_GUILD_ID", 0)

ANNOUNCE_CHANNEL_ID = _int_env("ANNOUNCE_CHANNEL_ID", 0)

ANNOUNCE_ROLE_ID = _int_env("ANNOUNCE_ROLE_ID", 0)

ALLOWED_CHANNEL_IDS = [int(c.strip()) for c in os.getenv("ALLOWED_CHANNEL_IDS", "").split(",") if c.strip().isdigit()]
