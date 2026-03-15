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
        for action in ("start", "stop", "restart", "announce"):
            self.assertIn(action, bot_module.VALID_ACTIONS)

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
            await bot_module.stop.callback(ctx, "test_container")

        ctx.send.assert_called_once()
        self.assertIn("already scheduled", ctx.send.call_args[0][0].lower())

    async def test_restart_rejects_duplicate_when_pending(self):
        """!restart while a task is already pending should send a rejection message."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()

        fake_task = MagicMock()
        fake_task.done.return_value = False
        bot_module._pending_ops["test_container"] = fake_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            await bot_module.restart.callback(ctx, "test_container")

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
                            await bot_module.stop.callback(ctx, "test_container")

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
                            await bot_module.stop.callback(ctx1, "test_container")
                            await bot_module.stop.callback(ctx2, "test_container")

        ctx2.send.assert_called_once()
        self.assertIn("already scheduled", ctx2.send.call_args[0][0].lower())


if __name__ == "__main__":
    unittest.main()
