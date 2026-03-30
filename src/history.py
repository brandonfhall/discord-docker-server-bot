"""Command history / audit log with thread-safe file I/O."""

import json
import logging
import os
import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_MAX_ENTRIES = 200


def load(history_file: str) -> list:
    """Load command history from disk."""
    if not os.path.exists(history_file):
        return []
    try:
        with open(history_file, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save(history_file: str, entries: list):
    """Save command history to disk (capped at MAX_ENTRIES)."""
    hist_dir = os.path.dirname(history_file)
    if hist_dir and not os.path.exists(hist_dir):
        os.makedirs(hist_dir)
    entries = entries[-_MAX_ENTRIES:]
    with open(history_file, "w") as f:
        json.dump(entries, f, indent=2)


def record(history_file: str, user, command: str, container: str = ""):
    """Append a command entry to the history file. Thread-safe."""
    with _lock:
        entries = load(history_file)
        entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": str(user),
            "command": command,
            "container": container,
        })
        save(history_file, entries)
