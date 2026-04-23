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
