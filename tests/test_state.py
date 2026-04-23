import os
import unittest
from unittest.mock import MagicMock

from src.state import state


class TestCancelPending(unittest.TestCase):
    """Tests for the state.cancel_pending helper that aborts scheduled stop/restart tasks."""

    def setUp(self):
        state.pending_ops.clear()

    def tearDown(self):
        state.pending_ops.clear()

    def test_cancels_active_task(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        state.pending_ops["srv"] = mock_task
        state.cancel_pending("srv")
        mock_task.cancel.assert_called_once()
        self.assertNotIn("srv", state.pending_ops)

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
