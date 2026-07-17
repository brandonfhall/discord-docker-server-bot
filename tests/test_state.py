import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.state import BotState, state


class TestCancelPending(unittest.TestCase):
    """Tests for the state.cancel_pending helper that aborts scheduled stop/restart tasks."""

    def setUp(self):
        state.pending_ops.clear()
        state.pending_op_info.clear()

    def tearDown(self):
        state.pending_ops.clear()
        state.pending_op_info.clear()

    def test_cancels_active_task(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        state.pending_ops["srv"] = mock_task
        state.cancel_pending("srv")
        mock_task.cancel.assert_called_once()
        self.assertNotIn("srv", state.pending_ops)

    def test_cancel_also_clears_op_info(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        state.pending_ops["srv"] = mock_task
        state.pending_op_info["srv"] = {"action": "stop"}
        state.cancel_pending("srv")
        self.assertNotIn("srv", state.pending_op_info)

    def test_noop_for_unknown_container(self):
        state.cancel_pending("nonexistent")  # Should not raise

    def test_does_not_cancel_completed_task(self):
        mock_task = MagicMock()
        mock_task.done.return_value = True
        state.pending_ops["srv"] = mock_task
        state.cancel_pending("srv")
        mock_task.cancel.assert_not_called()
        self.assertNotIn("srv", state.pending_ops)


class TestCommandHistory(unittest.TestCase):
    def setUp(self):
        from src import history

        self.history = history
        self.test_file = "test_history.json"

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)
        directory = os.path.dirname(self.test_file) or "."
        prefix = os.path.basename(self.test_file) + "."
        for name in os.listdir(directory):
            if name.startswith(prefix) and name.endswith(".tmp"):
                os.remove(os.path.join(directory, name))

    def test_save_uses_atomic_replace_not_in_place_truncation(self):
        """history.save must go through os.replace (atomic rename) rather
        than truncating the live file in place."""
        with patch("src.atomic_io.os.replace") as mock_replace:
            self.history.save(self.test_file, [{"user": "X", "command": "start"}])
            mock_replace.assert_called_once()

    def test_record_and_load_history(self):
        self.history.record(self.test_file, "TestUser", "start", "server1")
        entries = self.history.load(self.test_file)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["user"], "TestUser")
        self.assertEqual(entries[0]["command"], "start")
        self.assertEqual(entries[0]["container"], "server1")

    def test_history_caps_at_200(self):
        for i in range(210):
            self.history.record(self.test_file, f"User{i}", "start", "server1")
        entries = self.history.load(self.test_file)
        self.assertEqual(len(entries), 200)

    def test_load_history_empty_file(self):
        entries = self.history.load(self.test_file)
        self.assertEqual(entries, [])

    def test_load_history_corrupted_file(self):
        with open(self.test_file, "w") as f:
            f.write("not json{{{")
        entries = self.history.load(self.test_file)
        self.assertEqual(entries, [])


class TestMaintenancePersistence(unittest.TestCase):
    """Tests for BotState.save_maintenance / load_maintenance (L4)."""

    def setUp(self):
        self.test_file = os.path.join(tempfile.gettempdir(), "discord-bot-test-maintenance.json")
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)
        directory = os.path.dirname(self.test_file) or "."
        prefix = os.path.basename(self.test_file) + "."
        for name in os.listdir(directory):
            if name.startswith(prefix) and name.endswith(".tmp"):
                os.remove(os.path.join(directory, name))

    def test_toggle_on_survives_simulated_restart(self):
        """Enabling maintenance, persisting, then constructing a fresh
        BotState (simulating a bot restart) and loading must restore both
        the flag and the reason."""
        state.maintenance_mode = True
        state.maintenance_reason = "Patching"
        state.save_maintenance(self.test_file)

        fresh = BotState()
        fresh.load_maintenance(self.test_file)
        self.assertTrue(fresh.maintenance_mode)
        self.assertEqual(fresh.maintenance_reason, "Patching")

    def test_toggle_off_persists_off(self):
        state.maintenance_mode = True
        state.maintenance_reason = "Patching"
        state.save_maintenance(self.test_file)
        state.maintenance_mode = False
        state.maintenance_reason = ""
        state.save_maintenance(self.test_file)

        fresh = BotState()
        fresh.load_maintenance(self.test_file)
        self.assertFalse(fresh.maintenance_mode)
        self.assertEqual(fresh.maintenance_reason, "")

    def test_missing_file_defaults_to_off(self):
        """First run: no maintenance file on disk yet -- load must leave/set
        maintenance off rather than raising."""
        fresh = BotState()
        fresh.maintenance_mode = True  # pre-seed to prove load overwrites it
        fresh.load_maintenance(self.test_file)
        self.assertFalse(fresh.maintenance_mode)
        self.assertEqual(fresh.maintenance_reason, "")

    def test_corrupt_file_defaults_to_off_without_raising(self):
        """A corrupt maintenance file must not crash startup -- default to
        off and let the bot continue starting."""
        with open(self.test_file, "w") as f:
            f.write("not json{{{")

        fresh = BotState()
        fresh.maintenance_mode = True
        try:
            fresh.load_maintenance(self.test_file)
        except Exception as e:
            self.fail(f"load_maintenance raised on a corrupt file: {e}")
        self.assertFalse(fresh.maintenance_mode)
        self.assertEqual(fresh.maintenance_reason, "")
