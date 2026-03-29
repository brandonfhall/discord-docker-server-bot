import asyncio
import logging
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

    @patch("src.docker_control.docker.from_env")
    def test_announce_in_game_no_message_placeholder(self, mock_from_env):
        """When CONTAINER_MESSAGE_CMD has no {message} placeholder the else-branch is used."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_exec = MagicMock()
        mock_exec.exit_code = 0
        mock_exec.output = b""
        mock_container.exec_run.return_value = mock_exec

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            with patch("src.docker_control.CONTAINER_MESSAGE_CMD", "rcon-cli say"):
                result = docker_control.announce_in_game("my_game_server", "Hello")
        self.assertEqual(result, "ok")
        mock_container.exec_run.assert_called_once_with(["rcon-cli", "say", "Hello"])

    @patch("src.docker_control.docker.from_env")
    def test_announce_in_game_exec_exception(self, mock_from_env):
        """exec_run raising an exception returns an error string."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.exec_run.side_effect = RuntimeError("exec failed")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.announce_in_game("my_game_server", "Hello")
        self.assertTrue(result.startswith("error:"))

    @patch("src.docker_control.docker.from_env")
    def test_announce_in_game_nonzero_exit_code(self, mock_from_env):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container

        mock_exec = MagicMock()
        mock_exec.exit_code = 1
        mock_exec.output = b"command not found"
        mock_container.exec_run.return_value = mock_exec

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.announce_in_game("my_game_server", "Hello")
        self.assertTrue(result.startswith("error (1):"))


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
        for action in ("start", "stop", "stop_now", "restart", "restart_now", "announce", "logs", "stats", "maintenance", "history"):
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

    def test_root_redirects_to_status(self):
        from fastapi.testclient import TestClient
        from src.bot import app
        client = TestClient(app)
        response = client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/status")

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
        for action in ("start", "stop", "stop_now", "restart", "restart_now", "announce",
                        "logs", "stats", "maintenance", "history"):
            self.assertIn(action, bot_module.VALID_ACTIONS)

    # --- start command ---

    async def test_start_command(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="started")):
                await bot_module.start.callback(ctx, container_name=None)
        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("Starting" in c for c in calls))
        self.assertTrue(any("started" in c for c in calls))

    async def test_start_command_disallowed_container(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.start.callback(ctx, container_name="evil")
        self.assertIn("not in the allowed list", ctx.send.call_args[0][0])

    # --- restart command (normal path) ---

    async def test_restart_command_normal_path(self):
        from src import bot as bot_module
        bot_module._pending_ops.clear()
        bot_module._maintenance_mode = False
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        mock_task = MagicMock()
        mock_loop = MagicMock()
        mock_loop.create_task.side_effect = lambda coro: (coro.close(), mock_task)[1]

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="ok")):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.restart.callback(ctx, arg1=None, arg2=None)

        first_msg = ctx.send.call_args_list[0][0][0]
        self.assertIn("will restart", first_msg)
        self.assertIn("server1", bot_module._pending_ops)
        bot_module._pending_ops.clear()

    # --- status command ---

    async def test_status_command(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="running")):
                await bot_module.status_cmd.callback(ctx, container_name=None)
        ctx.send.assert_called_once()
        self.assertIn("running", ctx.send.call_args[0][0])

    # --- announce command ---

    async def test_announce_command_single_container(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="ok: Message sent")):
                await bot_module.announce.callback(ctx, arg1="Hello world", arg2=None)
        ctx.send.assert_called_once()
        self.assertIn("ok", ctx.send.call_args[0][0])

    async def test_announce_command_multi_container_with_name(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="ok")):
                await bot_module.announce.callback(ctx, arg1="server1", arg2="Hello world")
        ctx.send.assert_called_once()
        self.assertIn("server1", ctx.send.call_args[0][0])

    async def test_announce_command_no_target_shows_usage(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            await bot_module.announce.callback(ctx, arg1="some message", arg2=None)
        ctx.send.assert_called_once()
        self.assertIn("Usage", ctx.send.call_args[0][0])

    # --- guide command ---

    async def test_guide_command(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await bot_module.guide.callback(ctx)
        ctx.send.assert_called_once()
        output = ctx.send.call_args[0][0]
        self.assertIn("Docker Bot Guide", output)
        self.assertIn("!stop now", output)

    # --- perm add/remove/list happy paths ---

    async def test_perm_add_valid_action(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author = MagicMock()
        with patch("src.bot.permissions.add_role") as mock_add:
            await bot_module.perm_add.callback(ctx, "start", role_name="Moderator")
        mock_add.assert_called_once_with("start", "Moderator")
        self.assertIn("Added", ctx.send.call_args[0][0])

    async def test_perm_remove_valid_action(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author = MagicMock()
        with patch("src.bot.permissions.remove_role") as mock_remove:
            await bot_module.perm_remove.callback(ctx, "stop", role_name="Moderator")
        mock_remove.assert_called_once_with("stop", "Moderator")
        self.assertIn("Removed", ctx.send.call_args[0][0])

    async def test_perm_list(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch("src.bot.permissions.list_permissions", return_value={"start": ["Admin"], "stop": ["Admin"]}):
            await bot_module.perm_list.callback(ctx)
        ctx.send.assert_called_once()
        output = ctx.send.call_args[0][0]
        self.assertIn("start", output)
        self.assertIn("Admin", output)

    # --- announce_error handler ---

    async def test_announce_error_missing_arg(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        error = commands.MissingRequiredArgument(MagicMock())
        await bot_module.announce_error(ctx, error)
        ctx.send.assert_called_once()
        self.assertIn("Usage", ctx.send.call_args[0][0])

    # --- perm group with no subcommand ---

    async def test_perm_no_subcommand(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.invoked_subcommand = None
        await bot_module.perm.callback(ctx)
        ctx.send.assert_called_once()
        self.assertIn("!perm", ctx.send.call_args[0][0])

    # --- has_permission predicate ---

    async def test_has_permission_admin_bypasses(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.author.guild_permissions.administrator = True
        predicate = bot_module.has_permission("start")
        # The check decorator wraps a predicate; extract and call it
        self.assertTrue(await predicate.predicate(ctx))

    async def test_has_permission_checks_role(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.author.guild_permissions.administrator = False
        predicate = bot_module.has_permission("start")
        with patch("src.bot.permissions.is_member_allowed", return_value=False):
            self.assertFalse(await predicate.predicate(ctx))

    # --- check_guild ---

    async def test_check_guild_passes_all_checks(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 42
        ctx.channel.id = 100
        with patch.object(bot_module, "DISCORD_GUILD_ID", 42):
            with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", [100, 200]):
                self.assertTrue(await bot_module.check_guild(ctx))

    async def test_check_guild_dm_with_guild_restriction(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.guild = None  # DM has no guild
        with patch.object(bot_module, "DISCORD_GUILD_ID", 42):
            self.assertFalse(await bot_module.check_guild(ctx))

    # --- resolve_container ---

    async def test_resolve_container_empty_list(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", []):
            result = await bot_module.resolve_container(ctx, None)
        self.assertIsNone(result)
        ctx.send.assert_called_once()
        self.assertIn("No allowed containers", ctx.send.call_args[0][0])

    # --- send_announcement ---

    async def test_send_announcement_to_configured_channel(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.send = AsyncMock()

        target_channel = MagicMock()
        target_channel.id = 200
        target_channel.mention = "#announcements"
        target_channel.send = AsyncMock()

        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 200):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                with patch.object(bot_module.bot, "get_channel", return_value=target_channel):
                    await bot_module.send_announcement(ctx, "Hello!")

        target_channel.send.assert_called_once_with("Hello!")
        # Should also confirm to the command channel
        ctx.send.assert_called_once()
        self.assertIn(target_channel.mention, ctx.send.call_args[0][0])

    async def test_send_announcement_channel_not_found_falls_back(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 999):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                with patch.object(bot_module.bot, "get_channel", return_value=None):
                    with self.assertLogs(level="WARNING"):
                        await bot_module.send_announcement(ctx, "Hello!")

        ctx.channel.send.assert_called_once_with("Hello!")

    # --- on_command_error: MissingRequiredArgument ---

    async def test_on_command_error_missing_arg_perm_add(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.command = MagicMock()
        ctx.command.qualified_name = "perm add"
        error = commands.MissingRequiredArgument(MagicMock())
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            await bot_module.on_command_error(ctx, error)
        self.assertIn("perm add", ctx.send.call_args[0][0])

    async def test_on_command_error_missing_arg_perm_remove(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.command = MagicMock()
        ctx.command.qualified_name = "perm remove"
        error = commands.MissingRequiredArgument(MagicMock())
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            await bot_module.on_command_error(ctx, error)
        self.assertIn("perm remove", ctx.send.call_args[0][0])

    async def test_on_command_error_missing_arg_perm_generic(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.command = MagicMock()
        ctx.command.qualified_name = "perm"
        error = commands.MissingRequiredArgument(MagicMock())
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            await bot_module.on_command_error(ctx, error)
        self.assertIn("!perm", ctx.send.call_args[0][0])

    async def test_on_command_error_missing_arg_other_command(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.command = MagicMock()
        ctx.command.qualified_name = "announce"
        error = commands.MissingRequiredArgument(MagicMock())
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            await bot_module.on_command_error(ctx, error)
        self.assertIn("!announce", ctx.send.call_args[0][0])

    # --- on_command_error: CommandNotFound ---

    async def test_on_command_error_command_not_found_perm_admin_gets_usage(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.message.content = "!perm badsubcmd"
        ctx.author.guild_permissions.administrator = True
        error = commands.CommandNotFound("perm badsubcmd")
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            await bot_module.on_command_error(ctx, error)
        ctx.send.assert_called_once()
        self.assertIn("!perm", ctx.send.call_args[0][0])

    async def test_on_command_error_command_not_found_perm_non_admin_silent(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.message.content = "!perm badsubcmd"
        ctx.author.guild_permissions.administrator = False
        error = commands.CommandNotFound("perm badsubcmd")
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            await bot_module.on_command_error(ctx, error)
        ctx.send.assert_not_called()

    async def test_on_command_error_command_not_found_other_silent(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.message.content = "!unknowncmd"
        error = commands.CommandNotFound("unknowncmd")
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            await bot_module.on_command_error(ctx, error)
        ctx.send.assert_not_called()

    async def test_on_command_error_unexpected_error_is_logged(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        error = RuntimeError("something broke")
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            with self.assertLogs(level="ERROR"):
                await bot_module.on_command_error(ctx, error)

    async def test_on_command_error_silent_in_disallowed_channel(self):
        """CheckFailure from a disallowed channel should produce no response."""
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel.id = 999
        ctx.command = MagicMock()
        error = commands.CheckFailure("not allowed")
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", [100, 200]):
            await bot_module.on_command_error(ctx, error)
        ctx.send.assert_not_called()

    async def test_on_command_error_responds_in_allowed_channel(self):
        """CheckFailure from an allowed channel (role denied) should still respond."""
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel.id = 100
        ctx.author = MagicMock()
        ctx.command = MagicMock()
        error = commands.CheckFailure("not allowed")
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", [100, 200]):
            await bot_module.on_command_error(ctx, error)
        ctx.send.assert_called_once()
        self.assertIn("permission", ctx.send.call_args[0][0].lower())


# ---------------------------------------------------------------------------
# _RedactingFilter — token redaction at log source
# ---------------------------------------------------------------------------

class TestRedactingFilter(unittest.TestCase):
    """Tests for the log-level token redaction filter applied at startup."""

    def setUp(self):
        from src.bot import _RedactingFilter
        self._RedactingFilter = _RedactingFilter

    def _make_record(self, msg, args=()):
        return logging.LogRecord("test", logging.INFO, "", 0, msg, args, None)

    def test_redacts_token_in_message(self):
        f = self._RedactingFilter(["supersecret"])
        record = self._make_record("auth token supersecret leaked")
        f.filter(record)
        self.assertNotIn("supersecret", record.msg)
        self.assertIn("[REDACTED]", record.msg)

    def test_redacts_token_in_formatted_args(self):
        """Token appearing via %-style formatting is redacted after expansion."""
        f = self._RedactingFilter(["supersecret"])
        record = self._make_record("token is %s", ("supersecret",))
        f.filter(record)
        self.assertNotIn("supersecret", record.msg)
        self.assertEqual(record.args, ())

    def test_redacts_multiple_tokens(self):
        f = self._RedactingFilter(["tok1", "tok2"])
        record = self._make_record("tok1 and tok2 both present")
        f.filter(record)
        self.assertNotIn("tok1", record.msg)
        self.assertNotIn("tok2", record.msg)

    def test_skips_empty_and_none_tokens(self):
        """Empty/None entries in token list must not raise errors."""
        f = self._RedactingFilter(["", None, "realtoken"])
        record = self._make_record("realtoken here")
        f.filter(record)
        self.assertNotIn("realtoken", record.msg)

    def test_preserves_clean_message(self):
        f = self._RedactingFilter(["secrettoken"])
        record = self._make_record("normal log message")
        f.filter(record)
        self.assertEqual(record.msg, "normal log message")

    def test_always_returns_true(self):
        f = self._RedactingFilter(["token"])
        record = self._make_record("some message")
        self.assertTrue(f.filter(record))

    def test_empty_token_list_is_noop(self):
        f = self._RedactingFilter([])
        record = self._make_record("message stays unchanged")
        f.filter(record)
        self.assertEqual(record.msg, "message stays unchanged")


# ---------------------------------------------------------------------------
# _pending_ops — stop/restart deduplication
# ---------------------------------------------------------------------------

class TestPendingOps(unittest.IsolatedAsyncioTestCase):
    """Tests for the _pending_ops dict that prevents duplicate stop/restart tasks."""

    def setUp(self):
        from src import bot as bot_module
        self.bot_module = bot_module
        bot_module._pending_ops.clear()
        bot_module._maintenance_mode = False

    def tearDown(self):
        for task in list(self.bot_module._pending_ops.values()):
            if asyncio.isfuture(task) and not task.done():
                task.cancel()
        self.bot_module._pending_ops.clear()

    async def test_stop_rejects_duplicate_when_pending(self):
        """!stop while a task is already pending should send a rejection message."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()

        fake_task = MagicMock()
        fake_task.done.return_value = False
        bot_module._pending_ops["test_container"] = fake_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            await bot_module.stop.callback(ctx, arg1="test_container")

        ctx.send.assert_called_once()
        self.assertIn("already scheduled", ctx.send.call_args[0][0].lower())

    async def test_restart_rejects_duplicate_when_pending(self):
        """!restart while a task is already pending should send a rejection message."""
        bot_module = self.bot_module
        bot_module._maintenance_mode = False
        ctx = MagicMock()
        ctx.send = AsyncMock()

        fake_task = MagicMock()
        fake_task.done.return_value = False
        bot_module._pending_ops["test_container"] = fake_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            await bot_module.restart.callback(ctx, arg1="test_container", arg2=None)

        ctx.send.assert_called_once()
        self.assertIn("already scheduled", ctx.send.call_args[0][0].lower())

    async def test_stop_proceeds_and_registers_task_when_no_pending_op(self):
        """A fresh !stop should send the countdown message and register the task."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_loop = MagicMock()

        def _create_task(coro):
            coro.close()  # prevent "coroutine never awaited" warning
            return mock_task

        mock_loop.create_task.side_effect = _create_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="ok")):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.stop.callback(ctx, arg1="test_container")

        # Countdown message sent (not a rejection)
        first_msg = ctx.send.call_args_list[0][0][0]
        self.assertIn("will stop", first_msg)
        # Task registered
        self.assertIn("test_container", bot_module._pending_ops)
        self.assertIs(bot_module._pending_ops["test_container"], mock_task)

    async def test_second_stop_rejected_after_first_registers_task(self):
        """After the first !stop schedules a task, a second !stop is rejected."""
        bot_module = self.bot_module
        ctx1 = MagicMock()
        ctx1.send = AsyncMock()
        ctx1.channel = MagicMock()
        ctx1.channel.id = 100
        ctx1.channel.send = AsyncMock()

        ctx2 = MagicMock()
        ctx2.send = AsyncMock()

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_loop = MagicMock()

        def _create_task(coro):
            coro.close()  # prevent "coroutine never awaited" warning
            return mock_task

        mock_loop.create_task.side_effect = _create_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="ok")):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.stop.callback(ctx1, arg1="test_container")
                            await bot_module.stop.callback(ctx2, arg1="test_container")

        ctx2.send.assert_called_once()
        self.assertIn("already scheduled", ctx2.send.call_args[0][0].lower())


# ---------------------------------------------------------------------------
# !stop now — immediate stop with separate permission
# ---------------------------------------------------------------------------

class TestStopNow(unittest.IsolatedAsyncioTestCase):
    """Tests for the !stop now immediate-shutdown path."""

    def setUp(self):
        from src import bot as bot_module
        self.bot_module = bot_module
        bot_module._pending_ops.clear()
        bot_module._maintenance_mode = False

    def tearDown(self):
        for task in list(self.bot_module._pending_ops.values()):
            if asyncio.isfuture(task) and not task.done():
                task.cancel()
        self.bot_module._pending_ops.clear()

    async def test_stop_now_immediate_as_admin(self):
        """Admin using !stop now should bypass stop_now permission and stop immediately."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="stopped")):
                        await bot_module.stop.callback(ctx, arg1="now")

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("immediately" in c for c in calls))
        self.assertTrue(any("stopped" in c for c in calls))

    async def test_stop_now_sends_announcements(self):
        """!stop now should send Discord and in-game announcements before stopping."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        run_blocking_calls = []
        async def mock_run_blocking(func, *args, **kwargs):
            run_blocking_calls.append((func.__name__, args))
            return "stopped" if func.__name__ == "stop_container" else "ok"

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        await bot_module.stop.callback(ctx, arg1="now")

        # Discord announcement sent to channel
        ctx.channel.send.assert_called_once()
        announcement = ctx.channel.send.call_args[0][0]
        self.assertIn("NOW", announcement)

        # In-game announcement was called before stop
        func_names = [c[0] for c in run_blocking_calls]
        self.assertEqual(func_names, ["announce_in_game", "stop_container"])

    async def test_stop_now_with_container_name(self):
        """!stop server1 now should parse both args correctly."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="stopped")):
                        await bot_module.stop.callback(ctx, arg1="server1", arg2="now")

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("server1" in c and "immediately" in c for c in calls))

    async def test_stop_now_reversed_arg_order(self):
        """!stop now server1 should also work (arg order doesn't matter)."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="stopped")):
                        await bot_module.stop.callback(ctx, arg1="now", arg2="server1")

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("server1" in c and "immediately" in c for c in calls))

    async def test_stop_now_denied_without_permission(self):
        """Non-admin without stop_now role should be rejected."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author.guild_permissions.administrator = False

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.permissions.is_member_allowed", return_value=False):
                await bot_module.stop.callback(ctx, arg1="now")

        ctx.send.assert_called_once()
        self.assertIn("permission", ctx.send.call_args[0][0].lower())

    async def test_stop_now_allowed_with_role(self):
        """Non-admin with stop_now role should be allowed."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = False

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.permissions.is_member_allowed", return_value=True) as mock_perm:
                        with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="stopped")):
                            await bot_module.stop.callback(ctx, arg1="now")

        mock_perm.assert_called_once_with("stop_now", ctx.author)
        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("immediately" in c for c in calls))

    async def test_stop_now_cancels_pending_op(self):
        """!stop now should cancel any in-flight countdown for that container."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        pending_task = MagicMock()
        pending_task.done.return_value = False
        bot_module._pending_ops["server1"] = pending_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="stopped")):
                        await bot_module.stop.callback(ctx, arg1="now")

        pending_task.cancel.assert_called_once()
        self.assertNotIn("server1", bot_module._pending_ops)

    async def test_stop_without_now_still_uses_countdown(self):
        """Plain !stop should still go through the delayed path."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        mock_task = MagicMock()
        mock_loop = MagicMock()
        mock_loop.create_task.side_effect = lambda coro: (coro.close(), mock_task)[1]

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="ok")):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.stop.callback(ctx, arg1=None)

        first_msg = ctx.send.call_args_list[0][0][0]
        self.assertIn("will stop", first_msg)

    async def test_stop_now_case_insensitive(self):
        """'NOW', 'Now', etc. should all trigger the immediate path."""
        bot_module = self.bot_module
        for variant in ("NOW", "Now", "nOw"):
            ctx = MagicMock()
            ctx.send = AsyncMock()
            ctx.channel = MagicMock()
            ctx.channel.id = 100
            ctx.channel.send = AsyncMock()
            ctx.author.guild_permissions.administrator = True

            with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
                with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                    with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                        with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="stopped")):
                            await bot_module.stop.callback(ctx, arg1=variant)

            calls = [c[0][0] for c in ctx.send.call_args_list]
            self.assertTrue(any("immediately" in c for c in calls), f"Failed for variant {variant!r}")

    async def test_stop_now_disallowed_container(self):
        """!stop evil now should reject the container before reaching the now logic."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.stop.callback(ctx, arg1="evil", arg2="now")

        ctx.send.assert_called_once()
        self.assertIn("not in the allowed list", ctx.send.call_args[0][0])

    async def test_stop_now_multiple_containers_no_name(self):
        """!stop now with multiple containers and no name should prompt for a container."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            await bot_module.stop.callback(ctx, arg1="now")

        ctx.send.assert_called_once()
        self.assertIn("Please specify one", ctx.send.call_args[0][0])


# ---------------------------------------------------------------------------
# /status API endpoint
# ---------------------------------------------------------------------------

class TestStatusEndpoint(unittest.TestCase):
    """Tests for the FastAPI /status route."""

    def test_status_returns_expected_structure(self):
        from fastapi.testclient import TestClient
        from src import bot as bot_module
        with patch.object(bot_module, "STATUS_TOKEN", None):
            with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.bot.docker_control.container_status", return_value="running"):
                    with patch("src.bot.permissions.list_permissions", return_value={"start": ["admin"]}):
                        client = TestClient(bot_module.app)
                        response = client.get("/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("containers", data)
        self.assertIn("permissions", data)
        self.assertIn("logs", data)
        self.assertEqual(data["containers"]["test_container"], "running")

    def test_status_requires_token_when_configured(self):
        from fastapi.testclient import TestClient
        from src import bot as bot_module
        with patch.object(bot_module, "STATUS_TOKEN", "secret"):
            client = TestClient(bot_module.app)
            response = client.get("/status")
        self.assertEqual(response.status_code, 401)

    def test_status_accepts_token_via_header(self):
        from fastapi.testclient import TestClient
        from src import bot as bot_module
        with patch.object(bot_module, "STATUS_TOKEN", "secret"):
            with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.bot.docker_control.container_status", return_value="running"):
                    with patch("src.bot.permissions.list_permissions", return_value={}):
                        client = TestClient(bot_module.app)
                        response = client.get("/status", headers={"X-Auth-Token": "secret"})
        self.assertEqual(response.status_code, 200)

    def test_status_accepts_token_via_query_param(self):
        from fastapi.testclient import TestClient
        from src import bot as bot_module
        with patch.object(bot_module, "STATUS_TOKEN", "secret"):
            with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.bot.docker_control.container_status", return_value="running"):
                    with patch("src.bot.permissions.list_permissions", return_value={}):
                        client = TestClient(bot_module.app)
                        response = client.get("/status?token=secret")
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# _cancel_pending
# ---------------------------------------------------------------------------

class TestCancelPending(unittest.TestCase):
    """Tests for the _cancel_pending helper that aborts scheduled stop/restart tasks."""

    def setUp(self):
        from src import bot as bot_module
        self.bot_module = bot_module
        bot_module._pending_ops.clear()

    def tearDown(self):
        self.bot_module._pending_ops.clear()

    def test_cancels_active_task(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        self.bot_module._pending_ops["srv"] = mock_task
        self.bot_module._cancel_pending("srv")
        mock_task.cancel.assert_called_once()
        self.assertNotIn("srv", self.bot_module._pending_ops)

    def test_noop_for_unknown_container(self):
        self.bot_module._cancel_pending("nonexistent")  # Should not raise

    def test_does_not_cancel_completed_task(self):
        mock_task = MagicMock()
        mock_task.done.return_value = True
        self.bot_module._pending_ops["srv"] = mock_task
        self.bot_module._cancel_pending("srv")
        mock_task.cancel.assert_not_called()
        self.assertNotIn("srv", self.bot_module._pending_ops)


# ---------------------------------------------------------------------------
# docker_control — container_logs and container_stats
# ---------------------------------------------------------------------------

class TestDockerControlLogs(unittest.TestCase):

    def setUp(self):
        docker_control._docker_client = None

    @patch("src.docker_control.docker.from_env")
    def test_container_logs_returns_output(self, mock_from_env):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.logs.return_value = b"line1\nline2\nline3\n"

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.container_logs("my_game_server", 10)
        self.assertIn("line1", result)
        mock_container.logs.assert_called_once_with(tail=10, timestamps=False)

    @patch("src.docker_control.docker.from_env")
    def test_container_logs_not_allowed(self, mock_from_env):
        result = docker_control.container_logs("evil_container")
        self.assertIsNone(result)

    @patch("src.docker_control.docker.from_env")
    def test_container_logs_exception_returns_none(self, mock_from_env):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.logs.side_effect = RuntimeError("Docker error")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.container_logs("my_game_server")
        self.assertIsNone(result)


class TestDockerControlStats(unittest.TestCase):

    def setUp(self):
        docker_control._docker_client = None

    @patch("src.docker_control.docker.from_env")
    def test_container_stats_running(self, mock_from_env):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.status = "running"
        mock_container.stats.return_value = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 200, "percpu_usage": [100, 100]},
                "system_cpu_usage": 10000,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 100},
                "system_cpu_usage": 9000,
            },
            "memory_stats": {
                "usage": 100 * 1024 * 1024,
                "limit": 1024 * 1024 * 1024,
            },
        }

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.container_stats("my_game_server")
        self.assertEqual(result["status"], "running")
        self.assertIn("cpu_percent", result)
        self.assertIn("mem_usage_mb", result)
        self.assertIn("mem_percent", result)

    @patch("src.docker_control.docker.from_env")
    def test_container_stats_not_running(self, mock_from_env):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.status = "exited"

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.container_stats("my_game_server")
        self.assertEqual(result, {"status": "exited"})

    @patch("src.docker_control.docker.from_env")
    def test_container_stats_not_allowed(self, mock_from_env):
        result = docker_control.container_stats("evil_container")
        self.assertIsNone(result)

    @patch("src.docker_control.docker.from_env")
    def test_container_stats_exception(self, mock_from_env):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.status = "running"
        mock_container.stats.side_effect = RuntimeError("Stats error")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.container_stats("my_game_server")
        self.assertEqual(result["status"], "running")
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# New config vars
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# !logs command
# ---------------------------------------------------------------------------

class TestLogsCommand(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from src import bot as bot_module
        bot_module._maintenance_mode = False

    async def test_logs_blocked_during_maintenance(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        bot_module._maintenance_mode = True
        bot_module._maintenance_reason = "update"
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.logs_cmd.callback(ctx, arg1=None, arg2=None)
        self.assertIn("maintenance", ctx.send.call_args[0][0].lower())
        bot_module._maintenance_mode = False

    async def test_logs_command_returns_output(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="log line 1\nlog line 2")):
                await bot_module.logs_cmd.callback(ctx, arg1=None, arg2=None)
        ctx.send.assert_called_once()
        self.assertIn("log line 1", ctx.send.call_args[0][0])

    async def test_logs_command_with_line_count(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="output")) as mock_rb:
                await bot_module.logs_cmd.callback(ctx, arg1="10", arg2=None)
        # Verify the line count was passed through
        mock_rb.assert_called_once()

    async def test_logs_command_no_output(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value=None)):
                await bot_module.logs_cmd.callback(ctx, arg1=None, arg2=None)
        self.assertIn("Could not fetch", ctx.send.call_args[0][0])

    async def test_logs_command_empty_output(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="   ")):
                await bot_module.logs_cmd.callback(ctx, arg1=None, arg2=None)
        self.assertIn("No recent logs", ctx.send.call_args[0][0])


# ---------------------------------------------------------------------------
# !stats command
# ---------------------------------------------------------------------------

class TestStatsCommand(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from src import bot as bot_module
        bot_module._maintenance_mode = False

    async def test_stats_blocked_during_maintenance(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        bot_module._maintenance_mode = True
        bot_module._maintenance_reason = "patching"
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.stats_cmd.callback(ctx, container_name=None)
        self.assertIn("maintenance", ctx.send.call_args[0][0].lower())
        bot_module._maintenance_mode = False

    async def test_stats_command_error_field(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value={"status": "running", "error": "timeout"})):
                await bot_module.stats_cmd.callback(ctx, container_name=None)
        self.assertIn("Error", ctx.send.call_args[0][0])

    async def test_stats_command_running(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        stats_data = {
            "status": "running",
            "cpu_percent": 15.5,
            "mem_usage_mb": 256.0,
            "mem_limit_mb": 1024.0,
            "mem_percent": 25.0,
        }
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value=stats_data)):
                await bot_module.stats_cmd.callback(ctx, container_name=None)
        output = ctx.send.call_args[0][0]
        self.assertIn("15.5%", output)
        self.assertIn("256.0 MB", output)

    async def test_stats_command_not_running(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value={"status": "exited"})):
                await bot_module.stats_cmd.callback(ctx, container_name=None)
        self.assertIn("exited", ctx.send.call_args[0][0])

    async def test_stats_command_none(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value=None)):
                await bot_module.stats_cmd.callback(ctx, container_name=None)
        self.assertIn("Could not fetch", ctx.send.call_args[0][0])


# ---------------------------------------------------------------------------
# !restart now — immediate restart with separate permission
# ---------------------------------------------------------------------------

class TestRestartNow(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from src import bot as bot_module
        self.bot_module = bot_module
        bot_module._pending_ops.clear()
        bot_module._maintenance_mode = False

    def tearDown(self):
        for task in list(self.bot_module._pending_ops.values()):
            if asyncio.isfuture(task) and not task.done():
                task.cancel()
        self.bot_module._pending_ops.clear()

    async def test_restart_now_immediate_as_admin(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="restarted")):
                        await bot_module.restart.callback(ctx, arg1="now", arg2=None)

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("immediately" in c for c in calls))
        self.assertTrue(any("restarted" in c for c in calls))

    async def test_restart_now_denied_without_permission(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author.guild_permissions.administrator = False

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.permissions.is_member_allowed", return_value=False):
                await bot_module.restart.callback(ctx, arg1="now", arg2=None)

        ctx.send.assert_called_once()
        self.assertIn("permission", ctx.send.call_args[0][0].lower())

    async def test_restart_now_cancels_pending_op(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        pending_task = MagicMock()
        pending_task.done.return_value = False
        bot_module._pending_ops["server1"] = pending_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="restarted")):
                        await bot_module.restart.callback(ctx, arg1="now", arg2=None)

        pending_task.cancel.assert_called_once()

    async def test_restart_now_with_container_name(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="restarted")):
                        await bot_module.restart.callback(ctx, arg1="server1", arg2="now")

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("server1" in c and "immediately" in c for c in calls))


# ---------------------------------------------------------------------------
# Maintenance mode
# ---------------------------------------------------------------------------

class TestMaintenanceMode(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from src import bot as bot_module
        self.bot_module = bot_module
        bot_module._maintenance_mode = False
        bot_module._maintenance_reason = ""

    def tearDown(self):
        self.bot_module._maintenance_mode = False
        self.bot_module._maintenance_reason = ""

    async def test_maintenance_on(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                await bot_module.maintenance_cmd.callback(ctx, toggle="on", reason="Updating server")

        self.assertTrue(bot_module._maintenance_mode)
        self.assertEqual(bot_module._maintenance_reason, "Updating server")
        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("enabled" in c.lower() for c in calls))

    async def test_maintenance_off(self):
        bot_module = self.bot_module
        bot_module._maintenance_mode = True
        bot_module._maintenance_reason = "Test"
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                await bot_module.maintenance_cmd.callback(ctx, toggle="off", reason="")

        self.assertFalse(bot_module._maintenance_mode)
        self.assertEqual(bot_module._maintenance_reason, "")

    async def test_maintenance_status(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await bot_module.maintenance_cmd.callback(ctx, toggle=None, reason="")
        self.assertIn("OFF", ctx.send.call_args[0][0])

    async def test_maintenance_blocks_start(self):
        bot_module = self.bot_module
        bot_module._maintenance_mode = True
        bot_module._maintenance_reason = "Scheduled downtime"
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.command = MagicMock()
        ctx.command.qualified_name = "start"

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.start.callback(ctx, container_name=None)
        self.assertIn("maintenance", ctx.send.call_args[0][0].lower())

    async def test_maintenance_invalid_toggle(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await bot_module.maintenance_cmd.callback(ctx, toggle="maybe", reason="")
        self.assertIn("Usage", ctx.send.call_args[0][0])

    def test_check_maintenance_allows_admin_commands(self):
        bot_module = self.bot_module
        bot_module._maintenance_mode = True
        ctx = MagicMock()
        ctx.command = MagicMock()
        for cmd_name in ("maintenance", "perm", "guide", "history"):
            ctx.command.qualified_name = cmd_name
            self.assertFalse(bot_module._check_maintenance(ctx))

    def test_check_maintenance_blocks_control_commands(self):
        bot_module = self.bot_module
        bot_module._maintenance_mode = True
        ctx = MagicMock()
        ctx.command = MagicMock()
        ctx.command.qualified_name = "start"
        self.assertTrue(bot_module._check_maintenance(ctx))


# ---------------------------------------------------------------------------
# Command history
# ---------------------------------------------------------------------------

class TestCommandHistory(unittest.TestCase):

    def setUp(self):
        from src import bot as bot_module
        self.bot_module = bot_module
        self.test_file = "test_history.json"
        self.original_file = bot_module.HISTORY_FILE
        bot_module.HISTORY_FILE = self.test_file

    def tearDown(self):
        self.bot_module.HISTORY_FILE = self.original_file
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_record_and_load_history(self):
        bot_module = self.bot_module
        bot_module._record_history("TestUser", "start", "server1")
        entries = bot_module._load_history()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["user"], "TestUser")
        self.assertEqual(entries[0]["command"], "start")
        self.assertEqual(entries[0]["container"], "server1")

    def test_history_caps_at_200(self):
        bot_module = self.bot_module
        for i in range(210):
            bot_module._record_history(f"User{i}", "start", "server1")
        entries = bot_module._load_history()
        self.assertEqual(len(entries), 200)

    def test_load_history_empty_file(self):
        bot_module = self.bot_module
        entries = bot_module._load_history()
        self.assertEqual(entries, [])

    def test_load_history_corrupted_file(self):
        bot_module = self.bot_module
        with open(self.test_file, "w") as f:
            f.write("not json{{{")
        entries = bot_module._load_history()
        self.assertEqual(entries, [])


class TestHistoryCommand(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from src import bot as bot_module
        self.bot_module = bot_module

    async def test_history_command_empty(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "_load_history", return_value=[]):
            await bot_module.history_cmd.callback(ctx, count=10)
        self.assertIn("No command history", ctx.send.call_args[0][0])

    async def test_history_command_with_entries(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        entries = [
            {"timestamp": "2026-01-01T00:00:00+00:00", "user": "TestUser", "command": "start", "container": "server1"},
        ]
        with patch.object(bot_module, "_load_history", return_value=entries):
            await bot_module.history_cmd.callback(ctx, count=10)
        output = ctx.send.call_args[0][0]
        self.assertIn("TestUser", output)
        self.assertIn("start", output)


# ---------------------------------------------------------------------------
# Command cooldowns (on_command_error handling)
# ---------------------------------------------------------------------------

class TestCooldownError(unittest.IsolatedAsyncioTestCase):

    async def test_cooldown_error_sends_message(self):
        from src import bot as bot_module
        from discord.ext import commands
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel.id = 100
        error = commands.CommandOnCooldown(commands.Cooldown(1, 5), 3.5, commands.BucketType.user)
        with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
            await bot_module.on_command_error(ctx, error)
        self.assertIn("cooldown", ctx.send.call_args[0][0].lower())


# ---------------------------------------------------------------------------
# Crash alerting
# ---------------------------------------------------------------------------

class TestCrashAlerting(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from src import bot as bot_module
        self.bot_module = bot_module
        bot_module._last_known_status.clear()

    def tearDown(self):
        self.bot_module._last_known_status.clear()

    async def test_crash_detected_sends_alert(self):
        bot_module = self.bot_module
        bot_module._last_known_status["server1"] = "running"

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "CRASH_ALERT_CHANNEL_ID", 123):
                with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="exited")):
                        with patch.object(bot_module.bot, "get_channel", return_value=mock_channel):
                            await bot_module.crash_check_loop.coro()

        mock_channel.send.assert_called_once()
        self.assertIn("Crash Alert", mock_channel.send.call_args[0][0])
        self.assertEqual(bot_module._last_known_status["server1"], "exited")

    async def test_no_alert_when_still_running(self):
        bot_module = self.bot_module
        bot_module._last_known_status["server1"] = "running"

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="running")):
                await bot_module.crash_check_loop.coro()

        self.assertEqual(bot_module._last_known_status["server1"], "running")

    async def test_no_alert_on_first_check(self):
        """First poll seeds status without alerting."""
        bot_module = self.bot_module

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="exited")):
                await bot_module.crash_check_loop.coro()

        # No alert because previous status was None (first check)
        self.assertEqual(bot_module._last_known_status["server1"], "exited")

    async def test_crash_alert_uses_announce_channel_fallback(self):
        bot_module = self.bot_module
        bot_module._last_known_status["server1"] = "running"

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "CRASH_ALERT_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 456):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="exited")):
                        with patch.object(bot_module.bot, "get_channel", return_value=mock_channel) as mock_get:
                            await bot_module.crash_check_loop.coro()

        mock_get.assert_called_with(456)
        mock_channel.send.assert_called_once()


# ---------------------------------------------------------------------------
# Guide command — updated content
# ---------------------------------------------------------------------------

class TestGuideUpdated(unittest.IsolatedAsyncioTestCase):

    async def test_guide_shows_new_commands(self):
        from src import bot as bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await bot_module.guide.callback(ctx)
        output = ctx.send.call_args[0][0]
        self.assertIn("!logs", output)
        self.assertIn("!stats", output)
        self.assertIn("!history", output)
        self.assertIn("!maintenance", output)
        self.assertIn("!restart now", output)


if __name__ == "__main__":
    unittest.main()
