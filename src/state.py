"""Centralized mutable state for the bot."""

import json
import logging
import os

from .atomic_io import atomic_write_json


class BotState:
    """Container for all mutable bot state."""

    def __init__(self):
        self.pending_ops: dict = {}
        self.pending_op_info: dict = {}  # {container: {"action": str, "scheduled_at": datetime}}
        self.maintenance_mode: bool = False
        self.maintenance_reason: str = ""
        self.last_known_status: dict = {}

    def cancel_pending(self, container: str):
        """Cancel and remove a pending stop/restart task."""
        task = self.pending_ops.pop(container, None)
        self.pending_op_info.pop(container, None)
        if task and not task.done():
            task.cancel()

    def cancel_all_pending(self) -> list:
        """Cancel all pending stop/restart tasks. Returns list of cancelled container names."""
        cancelled = [name for name in list(self.pending_ops) if self.has_pending_op(name)]
        for name in cancelled:
            self.cancel_pending(name)
        return cancelled

    def has_pending_op(self, container: str) -> bool:
        """Check if a container has a pending operation."""
        task = self.pending_ops.get(container)
        return task is not None and not task.done()

    def is_maintenance_active(self) -> bool:
        """Return True if maintenance mode is currently active.

        Only container-mutating commands (start, stop, restart, announce, logs,
        stats) call this at all -- admin/read-only commands (maintenance itself,
        perm*, guide, history, status, cancel) never do, so they remain
        available during maintenance mode simply by not calling it.
        """
        return self.maintenance_mode

    def load_maintenance(self, path: str) -> None:
        """Load persisted {mode, reason} from *path* into state.

        Called once from bot.py's startup flow (main(), before bot.run()) --
        deliberately NOT from __init__: BotState is a module-level singleton
        constructed at import time, before config/logging are ready, and
        __init__ doing file I/O would couple this module to the filesystem
        at import. The path itself is passed in by the caller (state.py does
        not import config.py) so this method stays a pure state/I-O helper.

        A missing file means normal first run -- maintenance defaults to off.
        A corrupt/unreadable file must never crash startup: log at ERROR and
        default to off, matching the resilience posture of permissions.py's
        corruption handling (the bot must always start).
        """
        if not os.path.exists(path):
            self.maintenance_mode = False
            self.maintenance_reason = ""
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            self.maintenance_mode = bool(data.get("mode", False))
            self.maintenance_reason = str(data.get("reason") or "")
        except (ValueError, OSError, AttributeError, TypeError) as e:
            logging.error(f"Maintenance state file {path} is corrupted or unreadable ({e}); defaulting to maintenance off.")
            self.maintenance_mode = False
            self.maintenance_reason = ""

    def save_maintenance(self, path: str) -> None:
        """Atomically persist current {mode, reason} to *path*.

        Called from bot.py's maintenance_cmd on both the "on" and "off"
        branches, wrapped in docker_control.run_blocking since it's blocking
        file I/O invoked from an async handler.
        """
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        atomic_write_json(path, {"mode": self.maintenance_mode, "reason": self.maintenance_reason})


# Module-level singleton
state = BotState()
