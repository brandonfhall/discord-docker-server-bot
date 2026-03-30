import os

# Provide required env vars before any src module is imported.
# This satisfies config.py's startup validation without needing a real .env file.
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ALLOWED_CONTAINERS", "test_container")

import pytest
from src.state import state
from src import permissions


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset shared mutable state before each test to prevent cross-test leakage."""
    state.pending_ops.clear()
    state.maintenance_mode = False
    state.maintenance_reason = ""
    state.last_known_status.clear()
    yield
    state.pending_ops.clear()
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
