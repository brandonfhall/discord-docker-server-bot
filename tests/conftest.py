import os
import tempfile

# Provide required env vars before any src module is imported.
# This satisfies config.py's startup validation without needing a real .env file.
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ALLOWED_CONTAINERS", "test_container")
os.environ.setdefault("DISCORD_GUILD_ID", "123456789")
# Without this, importing src.bot (which calls setup_logging() at module load
# time) creates/rotates data/bot.log in the working tree on every test run.
os.environ.setdefault("LOG_FILE", os.path.join(tempfile.gettempdir(), "discord-bot-tests.log"))
# Same problem for HISTORY_FILE: handlers call history.record()/history.load()
# unmocked in several tests, writing real entries to data/history.json otherwise.
os.environ.setdefault("HISTORY_FILE", os.path.join(tempfile.gettempdir(), "discord-bot-tests-history.json"))
# Same problem for PERMISSIONS_FILE: several tests exercise real permissions.py
# read/write paths, which would otherwise create/mutate data/permissions.json
# in the working tree.
os.environ.setdefault("PERMISSIONS_FILE", os.path.join(tempfile.gettempdir(), "discord-bot-tests-permissions.json"))

import pytest
from src.state import state
from src import permissions


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset shared mutable state before each test to prevent cross-test leakage."""
    state.pending_ops.clear()
    state.pending_op_info.clear()
    state.maintenance_mode = False
    state.maintenance_reason = ""
    state.last_known_status.clear()
    yield
    state.pending_ops.clear()
    state.pending_op_info.clear()
    state.maintenance_mode = False
    state.maintenance_reason = ""
    state.last_known_status.clear()


@pytest.fixture(autouse=True)
def _reset_permissions_cache():
    """Reset the permissions module cache between tests."""
    permissions._cache = None
    permissions._cache_mtime = 0.0
    yield
    permissions._cache = None
    permissions._cache_mtime = 0.0
