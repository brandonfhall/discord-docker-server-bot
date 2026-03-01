import os

# Provide required env vars before any src module is imported.
# This satisfies config.py's startup validation without needing a real .env file.
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ALLOWED_CONTAINERS", "test_container")
