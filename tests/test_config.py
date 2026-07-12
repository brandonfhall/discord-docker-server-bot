import importlib
import os
import unittest
from io import StringIO
from unittest.mock import patch


class TestConfig(unittest.TestCase):
    """Tests for config helper functions."""

    def setUp(self):
        from src import config

        self.config = config

    # --- _int_env ---

    def test_int_env_valid(self):
        with patch.dict(os.environ, {"_BOT_TEST_INT": "42"}):
            self.assertEqual(self.config._int_env("_BOT_TEST_INT", 10), 42)

    def test_int_env_missing_uses_default(self):
        key = "_BOT_TEST_MISSING_KEY_99999"
        os.environ.pop(key, None)
        self.assertEqual(self.config._int_env(key, 99), 99)

    def test_int_env_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {"_BOT_TEST_INT": "not_a_number"}):
            result = self.config._int_env("_BOT_TEST_INT", 55)
        self.assertEqual(result, 55)

    def test_int_env_invalid_prints_warning(self):
        captured = StringIO()
        with patch.dict(os.environ, {"_BOT_TEST_INT": "bad_value"}):
            with patch("sys.stderr", captured):
                self.config._int_env("_BOT_TEST_INT", 0)
        self.assertIn("WARNING", captured.getvalue())

    def test_int_env_whitespace_uses_default(self):
        with patch.dict(os.environ, {"_BOT_TEST_INT": "   "}):
            self.assertEqual(self.config._int_env("_BOT_TEST_INT", 7), 7)

    def test_int_env_padded_value_parsed(self):
        with patch.dict(os.environ, {"_BOT_TEST_INT": " 123 "}):
            self.assertEqual(self.config._int_env("_BOT_TEST_INT", 0), 123)

    # --- _parse_channel_ids ---

    def test_parse_channel_ids_valid(self):
        result = self.config._parse_channel_ids("100,200,300")
        self.assertEqual(result, [100, 200, 300])

    def test_parse_channel_ids_empty_string(self):
        self.assertEqual(self.config._parse_channel_ids(""), [])

    def test_parse_channel_ids_skips_invalid(self):
        captured = StringIO()
        with patch("sys.stderr", captured):
            result = self.config._parse_channel_ids("100,abc,200")
        self.assertEqual(result, [100, 200])
        self.assertIn("WARNING", captured.getvalue())

    def test_parse_channel_ids_ignores_blank_entries(self):
        result = self.config._parse_channel_ids("100,,200")
        self.assertEqual(result, [100, 200])


class TestNewConfig(unittest.TestCase):
    def test_command_cooldown_has_default(self):
        from src import config

        self.assertIsInstance(config.COMMAND_COOLDOWN, int)
        self.assertGreater(config.COMMAND_COOLDOWN, 0)

    def test_crash_check_interval_has_default(self):
        from src import config

        self.assertIsInstance(config.CRASH_CHECK_INTERVAL, int)
        self.assertGreater(config.CRASH_CHECK_INTERVAL, 0)

    def test_history_file_has_default(self):
        from src import config

        self.assertIsInstance(config.HISTORY_FILE, str)
        self.assertTrue(config.HISTORY_FILE.endswith(".json"))


class TestGuildLockRequired(unittest.TestCase):
    """H1: config must fail closed unless DISCORD_GUILD_ID or ALLOW_ANY_GUILD is set."""

    def tearDown(self):
        # Restore config to the harness-standard state for every other test module.
        os.environ["DISCORD_GUILD_ID"] = "123456789"
        os.environ.pop("ALLOW_ANY_GUILD", None)
        from src import config

        importlib.reload(config)

    def test_missing_guild_id_and_no_opt_out_raises(self):
        from src import config

        with patch.dict(os.environ, {"DISCORD_GUILD_ID": "", "ALLOW_ANY_GUILD": ""}):
            with self.assertRaises(ValueError):
                importlib.reload(config)

    def test_missing_guild_id_with_opt_out_loads(self):
        from src import config

        with patch.dict(os.environ, {"DISCORD_GUILD_ID": "", "ALLOW_ANY_GUILD": "true"}):
            importlib.reload(config)
            self.assertEqual(config.DISCORD_GUILD_ID, 0)
            self.assertTrue(config.ALLOW_ANY_GUILD)

    def test_guild_id_set_loads_without_opt_out(self):
        from src import config

        with patch.dict(os.environ, {"DISCORD_GUILD_ID": "555", "ALLOW_ANY_GUILD": ""}):
            importlib.reload(config)
            self.assertEqual(config.DISCORD_GUILD_ID, 555)
            self.assertFalse(config.ALLOW_ANY_GUILD)
