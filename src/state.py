"""Centralized mutable state for the bot."""


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

    def is_maintenance_active(self, command_name: str) -> bool:
        """Return True if maintenance mode blocks the given command."""
        exempt = {
            "maintenance",
            "perm",
            "perm add",
            "perm remove",
            "perm list",
            "guide",
            "history",
        }
        if command_name in exempt:
            return False
        return self.maintenance_mode


# Module-level singleton
state = BotState()
