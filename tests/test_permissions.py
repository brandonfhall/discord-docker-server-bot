import json
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src import permissions


class TestPermissions(unittest.TestCase):
    def setUp(self):
        self.test_file = os.path.join(tempfile.gettempdir(), "discord-bot-test-permissions.json")
        self.original_file = permissions.PERMISSIONS_FILE
        permissions.PERMISSIONS_FILE = self.test_file

    def tearDown(self):
        permissions.PERMISSIONS_FILE = self.original_file
        if os.path.exists(self.test_file):
            os.remove(self.test_file)
        corrupt_file = self.test_file + ".corrupt"
        if os.path.exists(corrupt_file):
            os.remove(corrupt_file)
        # Clean up any leftover atomic-write temp files from mocked-out
        # os.replace tests (the real rename never happens in those cases).
        directory = os.path.dirname(self.test_file) or "."
        prefix = os.path.basename(self.test_file) + "."
        for name in os.listdir(directory):
            if name.startswith(prefix) and name.endswith(".tmp"):
                os.remove(os.path.join(directory, name))

    def test_default_permissions_creation(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)
        data = permissions._load()
        self.assertTrue(os.path.exists(self.test_file))
        self.assertIn("start", data)

    def test_default_permissions_includes_announce(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)
        data = permissions._load()
        self.assertIn("announce", data)

    def test_load_corrupted_file_reinitializes(self):
        with open(self.test_file, "w") as f:
            f.write("not valid json {{{")
        data = permissions._load()
        self.assertIsInstance(data, dict)
        self.assertIn("start", data)
        self.assertTrue(os.path.exists(self.test_file))

    def test_load_corrupted_file_preserves_original_as_corrupt_sibling(self):
        """Corruption recovery must not destroy evidence: the bad file is
        renamed to <file>.corrupt (not deleted), and the live file gets
        fresh defaults."""
        garbage = "not valid json {{{ this is garbage"
        with open(self.test_file, "w") as f:
            f.write(garbage)

        data = permissions._load()

        corrupt_path = self.test_file + ".corrupt"
        self.assertTrue(os.path.exists(corrupt_path))
        with open(corrupt_path, "r") as f:
            self.assertEqual(f.read(), garbage)

        # Live file holds fresh defaults, not the garbage.
        self.assertIsInstance(data, dict)
        self.assertIn("start", data)
        with open(self.test_file, "r") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk, data)

    def test_load_falls_back_to_defaults_when_preserve_fails(self):
        """If os.replace (the corrupt-file preserve step) itself fails -- e.g. a
        read-only/permission-broken data dir -- _load must degrade to in-memory
        defaults rather than let JSONDecodeError escape (L12)."""
        garbage = "not valid json {{{ this is garbage"
        with open(self.test_file, "w") as f:
            f.write(garbage)

        with patch("src.permissions.os.replace", side_effect=OSError("read-only filesystem")):
            data = permissions._load()

        self.assertIsInstance(data, dict)
        self.assertIn("start", data)
        self.assertEqual(set(data.keys()), set(permissions.ALL_ACTIONS))
        # The on-disk file is untouched -- still the original garbage -- since
        # the preserve/reinit never actually succeeded.
        with open(self.test_file, "r") as f:
            self.assertEqual(f.read(), garbage)

    def test_save_uses_atomic_replace_not_in_place_truncation(self):
        """_save must go through os.replace (atomic rename) rather than
        truncating the live file in place."""
        with patch("src.atomic_io.os.replace") as mock_replace:
            permissions._save({"start": ["Admin"]})
            mock_replace.assert_called_once()

    def test_save_preserves_0o600_mode(self):
        """An atomic replace must not silently widen the permissions file's
        mode to a temp-file default."""
        if sys.platform.startswith("win"):
            self.skipTest("POSIX file mode bits not meaningful on Windows")
        permissions._save({"start": ["Admin"]})
        actual_mode = stat.S_IMODE(os.stat(self.test_file).st_mode)
        self.assertEqual(actual_mode, 0o600)

    def test_is_member_allowed(self):
        with open(self.test_file, "w") as f:
            json.dump({"start": ["SuperUser"]}, f)
        member = MagicMock()
        role = MagicMock()
        role.name = "SuperUser"
        member.roles = [role]
        self.assertTrue(permissions.is_member_allowed("start", member))
        role.name = "Peasant"
        self.assertFalse(permissions.is_member_allowed("start", member))

    def test_add_role_no_duplicates(self):
        permissions.add_role("start", "DupeRole")
        permissions.add_role("start", "DupeRole")
        data = permissions._load()
        self.assertEqual(data["start"].count("DupeRole"), 1)

    def test_remove_role_nonexistent_no_error(self):
        # Should not raise any exception
        permissions.remove_role("start", "NonExistentRole")

    def test_modify_permissions(self):
        permissions.add_role("stop", "Moderator")
        data = permissions._load()
        self.assertIn("Moderator", data["stop"])

        permissions.remove_role("stop", "Moderator")
        data = permissions._load()
        self.assertNotIn("Moderator", data["stop"])

    def test_list_permissions_returns_all_default_actions(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)
        data = permissions.list_permissions()
        for action in (
            "start",
            "stop",
            "stop_now",
            "restart",
            "restart_now",
            "announce",
            "logs",
            "stats",
            "maintenance",
            "history",
        ):
            self.assertIn(action, data)

    def test_load_backfills_missing_actions(self):
        """Existing permissions files without stop_now get it added automatically."""
        with open(self.test_file, "w") as f:
            json.dump({"start": ["Admin"], "stop": ["Admin"], "restart": ["Admin"], "announce": ["Admin"]}, f)
        data = permissions._load()
        self.assertIn("stop_now", data)
        # Verify it was persisted to disk
        with open(self.test_file, "r") as f:
            on_disk = json.load(f)
        self.assertIn("stop_now", on_disk)

    def test_backfill_preserves_existing_custom_roles(self):
        """Backfilling stop_now must not overwrite customized roles on other actions."""
        custom = {"start": ["Moderator", "VIP"], "stop": ["Moderator"], "restart": ["Admin"], "announce": ["Admin"]}
        with open(self.test_file, "w") as f:
            json.dump(custom, f)
        data = permissions._load()
        # stop_now was backfilled
        self.assertIn("stop_now", data)
        # Existing custom roles are untouched
        self.assertEqual(data["start"], ["Moderator", "VIP"])
        self.assertEqual(data["stop"], ["Moderator"])

    def test_expected_actions_matches_valid_actions(self):
        """VALID_ACTIONS in bot.py must be the same object as ALL_ACTIONS in permissions.py."""
        from src import bot as bot_module

        self.assertIs(bot_module.VALID_ACTIONS, permissions.ALL_ACTIONS)
