import os

# Set required env vars before any src imports so config.py validation passes.
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ALLOWED_CONTAINERS", "test_container")

import json
import sys
import unittest
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

from src import docker_control
from src import permissions


# ---------------------------------------------------------------------------
# config._int_env and config._parse_channel_ids
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# docker_control
# ---------------------------------------------------------------------------

class TestDockerControl(unittest.TestCase):

    def setUp(self):
        docker_control._docker_client = None

    # --- _validate_container_name ---

    def test_validate_container_name_valid(self):
        self.assertTrue(docker_control._validate_container_name("my_game_server"))
        self.assertTrue(docker_control._validate_container_name("my-server.1"))

    def test_validate_container_name_injection_rejected(self):
        # SECURITY TEST: These strings simulate injection attacks.
        # We assert the validator REJECTS them — they are NOT executed.
        self.assertFalse(docker_control._validate_container_name("server; rm -rf"))
        self.assertFalse(docker_control._validate_container_name("server&"))
        self.assertFalse(docker_control._validate_container_name(" "))

    def test_validate_container_name_edge_cases(self):
        self.assertFalse(docker_control._validate_container_name(""))
        self.assertFalse(docker_control._validate_container_name(None))
        self.assertFalse(docker_control._validate_container_name("a" * 256))

    # --- _sanitize ---

    def test_sanitize_removes_shell_metacharacters(self):
        clean = docker_control._sanitize("Hello; echo 'hack'")
        self.assertNotIn(";", clean)
        self.assertNotIn("'", clean)
        self.assertEqual(clean, "Hello echo hack")

    def test_sanitize_empty_and_none(self):
        self.assertEqual(docker_control._sanitize(""), "")
        self.assertEqual(docker_control._sanitize(None), "")

    def test_sanitize_truncates_to_100_chars(self):
        result = docker_control._sanitize("a" * 200)
        self.assertEqual(len(result), 100)

    # --- _find_container_by_name ---

    def test_find_container_not_found_returns_none(self):
        import docker
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found", MagicMock())
        result = docker_control._find_container_by_name(mock_client, "missing")
        self.assertIsNone(result)

    def test_find_container_unexpected_exception_is_logged(self):
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = RuntimeError("connection error")
        with self.assertLogs(level="WARNING") as cm:
            result = docker_control._find_container_by_name(mock_client, "server")
        self.assertIsNone(result)
        self.assertTrue(any("Unexpected error" in line for line in cm.output))

    # --- container operations ---

    @patch("src.docker_control.docker.from_env")
    def test_docker_actions(self, mock_from_env):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            # start: exited → started
            mock_container.status = "exited"
            self.assertEqual(docker_control.start_container("my_game_server"), "started")
            mock_container.start.assert_called_once()

            # start: already running
            mock_container.status = "running"
            self.assertEqual(docker_control.start_container("my_game_server"), "already running")

            # stop: running → stopped
            mock_container.status = "running"
            self.assertEqual(docker_control.stop_container("my_game_server"), "stopped")
            mock_container.stop.assert_called_once()

            # stop: already stopped
            mock_container.status = "exited"
            self.assertEqual(docker_control.stop_container("my_game_server"), "not running")

            # restart
            self.assertEqual(docker_control.restart_container("my_game_server"), "restarted")
            mock_container.restart.assert_called_once()

            # status
            mock_container.status = "running"
            self.assertEqual(docker_control.container_status("my_game_server"), "running")

            # announce
            mock_exec = MagicMock()
            mock_exec.exit_code = 0
            mock_exec.output = b"Message sent"
            mock_container.exec_run.return_value = mock_exec
            result = docker_control.announce_in_game("my_game_server", "Hello World")
            self.assertIn("ok", result)
            self.assertTrue(mock_container.exec_run.called)

    def test_docker_security_checks(self):
        self.assertIn("not allowed", docker_control.start_container("evil_container"))


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------

class TestPermissions(unittest.TestCase):

    def setUp(self):
        self.test_file = "test_permissions.json"
        self.original_file = permissions.PERMISSIONS_FILE
        permissions.PERMISSIONS_FILE = self.test_file

    def tearDown(self):
        permissions.PERMISSIONS_FILE = self.original_file
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

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
        for action in ("start", "stop", "restart", "announce"):
            self.assertIn(action, data)


# ---------------------------------------------------------------------------
# bot logic
# ---------------------------------------------------------------------------

class TestBotLogic(unittest.IsolatedAsyncioTestCase):

    async def test_resolve_container_allowed(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            self.assertEqual(await bot_module.resolve_container(ctx, "server1"), "server1")

    async def test_resolve_container_multiple_no_name(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            result = await bot_module.resolve_container(ctx, None)
        self.assertIsNone(result)
        ctx.send.assert_called()

    async def test_resolve_container_single_no_name(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            self.assertEqual(await bot_module.resolve_container(ctx, None), "server1")

    async def test_resolve_container_disallowed(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["allowed"]):
            result = await bot_module.resolve_container(ctx, "evil")
        self.assertIsNone(result)
        self.assertIn("not in the allowed list", ctx.send.call_args[0][0])

    async def test_verify_token_no_token_configured(self):
        from src import bot as bot_module
        original = bot_module.STATUS_TOKEN
        try:
            bot_module.STATUS_TOKEN = None
            await bot_module.verify_token(None, None)  # Should not raise
        finally:
            bot_module.STATUS_TOKEN = original

    async def test_verify_token_correct_header(self):
        from src import bot as bot_module
        original = bot_module.STATUS_TOKEN
        try:
            bot_module.STATUS_TOKEN = "secret123"
            await bot_module.verify_token("secret123", None)
        finally:
            bot_module.STATUS_TOKEN = original

    async def test_verify_token_correct_query_param(self):
        from src import bot as bot_module
        original = bot_module.STATUS_TOKEN
        try:
            bot_module.STATUS_TOKEN = "secret123"
            await bot_module.verify_token(None, "secret123")
        finally:
            bot_module.STATUS_TOKEN = original

    async def test_verify_token_wrong_token_rejected(self):
        from src import bot as bot_module
        from fastapi import HTTPException
        original = bot_module.STATUS_TOKEN
        try:
            bot_module.STATUS_TOKEN = "secret123"
            with self.assertRaises(HTTPException):
                await bot_module.verify_token("wrongpass", None)
        finally:
            bot_module.STATUS_TOKEN = original

    async def test_send_announcement_no_config(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                await bot_module.send_announcement(ctx, "Hello!")
        ctx.channel.send.assert_called_once_with("Hello!")

    async def test_send_announcement_with_role_mention(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 999):
                await bot_module.send_announcement(ctx, "Hello!")
        sent = ctx.channel.send.call_args[0][0]
        self.assertIn("<@&999>", sent)
        self.assertIn("Hello!", sent)

    async def test_send_announcement_send_failure_is_logged(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock(side_effect=Exception("Discord error"))
        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                with self.assertLogs(level="ERROR"):
                    await bot_module.send_announcement(ctx, "Hello!")

    async def test_check_guild_no_restrictions(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 1
        ctx.channel.id = 1
        with patch.object(bot_module, "DISCORD_GUILD_ID", 0):
            with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
                self.assertTrue(await bot_module.check_guild(ctx))

    async def test_check_guild_wrong_guild(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 999
        with patch.object(bot_module, "DISCORD_GUILD_ID", 123):
            self.assertFalse(await bot_module.check_guild(ctx))

    async def test_check_guild_wrong_channel(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.guild = None
        ctx.channel.id = 456
        with patch.object(bot_module, "DISCORD_GUILD_ID", 0):
            with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", [100, 200]):
                self.assertFalse(await bot_module.check_guild(ctx))

    async def test_perm_add_rejects_invalid_action(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await bot_module.perm_add.callback(ctx, "invalid_action", role_name="SomeRole")
        ctx.send.assert_called_once()
        self.assertIn("Unknown action", ctx.send.call_args[0][0])

    async def test_perm_remove_rejects_invalid_action(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await bot_module.perm_remove.callback(ctx, "bad_action", role_name="SomeRole")
        ctx.send.assert_called_once()
        self.assertIn("Unknown action", ctx.send.call_args[0][0])

    async def test_valid_actions_constant_complete(self):
        from src import bot as bot_module
        for action in ("start", "stop", "restart", "announce"):
            self.assertIn(action, bot_module.VALID_ACTIONS)


if __name__ == "__main__":
    unittest.main()
