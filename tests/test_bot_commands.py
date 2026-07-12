import asyncio
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from src import docker_control
from src.state import state


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
        from src.api import app

        client = TestClient(app)
        response = client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/status")

    async def test_verify_token_no_token_configured(self):
        from src import api as api_module

        original = api_module.STATUS_TOKEN
        try:
            api_module.STATUS_TOKEN = None
            await api_module.verify_token(None, None)  # Should not raise
        finally:
            api_module.STATUS_TOKEN = original

    async def test_verify_token_correct_header(self):
        from src import api as api_module

        original = api_module.STATUS_TOKEN
        try:
            api_module.STATUS_TOKEN = "secret123"
            await api_module.verify_token("secret123", None)
        finally:
            api_module.STATUS_TOKEN = original

    async def test_verify_token_correct_query_param(self):
        from src import api as api_module

        original = api_module.STATUS_TOKEN
        try:
            api_module.STATUS_TOKEN = "secret123"
            await api_module.verify_token(None, "secret123")
        finally:
            api_module.STATUS_TOKEN = original

    async def test_verify_token_wrong_token_rejected(self):
        from src import api as api_module
        from fastapi import HTTPException

        original = api_module.STATUS_TOKEN
        try:
            api_module.STATUS_TOKEN = "secret123"
            with self.assertRaises(HTTPException):
                await api_module.verify_token("wrongpass", None)
        finally:
            api_module.STATUS_TOKEN = original

    async def test_send_announcement_no_config(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                await bot_module.send_announcement(ctx, "Hello!")
        ctx.channel.send.assert_called_once_with("Hello!", allowed_mentions=ANY)

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

    async def test_send_announcement_scopes_mention_to_announce_role_only(self):
        """M6: allowed_mentions must scope to exactly ANNOUNCE_ROLE_ID, not every
        role in the server -- otherwise a user-supplied message (e.g. a maintenance
        reason) containing another role mention would ping it too."""
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 999):
                await bot_module.send_announcement(ctx, "Hello!")

        mentions = ctx.channel.send.call_args.kwargs["allowed_mentions"]
        self.assertEqual([r.id for r in mentions.roles], [999])

    async def test_send_announcement_disables_all_mentions_without_announce_role(self):
        """M6: with ANNOUNCE_ROLE_ID unset, no roles should be re-enabled at all."""
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                await bot_module.send_announcement(ctx, "Hello!")

        mentions = ctx.channel.send.call_args.kwargs["allowed_mentions"]
        self.assertEqual(mentions.roles, False)

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
            with self.assertRaises(bot_module.SilentCheckFailure):
                await bot_module.check_guild(ctx)

    async def test_check_guild_wrong_channel(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 1
        ctx.channel.id = 456
        with patch.object(bot_module, "DISCORD_GUILD_ID", 0):
            with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", [100, 200]):
                with self.assertRaises(bot_module.SilentCheckFailure):
                    await bot_module.check_guild(ctx)

    async def test_check_guild_dm_rejected_even_without_guild_lock(self):
        """M3: a DM (ctx.guild is None) is rejected even when DISCORD_GUILD_ID is unset,
        since ctx.author has no guild_permissions/roles outside a guild context."""
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.guild = None
        with patch.object(bot_module, "DISCORD_GUILD_ID", 0):
            with patch.object(bot_module, "ALLOWED_CHANNEL_IDS", []):
                with self.assertRaises(bot_module.SilentCheckFailure):
                    await bot_module.check_guild(ctx)

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
            self.assertIn(action, bot_module.VALID_ACTIONS)

    # --- start command ---

    async def test_start_command(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch(
                "src.bot.docker_control.run_blocking", new=AsyncMock(return_value=docker_control.Result(True, "started"))
            ):
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

        state.pending_ops.clear()
        state.maintenance_mode = False
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
                    with patch(
                        "src.bot.docker_control.run_blocking", new=AsyncMock(return_value=docker_control.Result(True, "ok"))
                    ):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.restart.callback(ctx)

        first_msg = ctx.send.call_args_list[0][0][0]
        self.assertIn("will restart", first_msg)
        self.assertIn("server1", state.pending_ops)
        state.pending_ops.clear()

    # --- status command ---

    async def test_status_command(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="running")):
                await bot_module.status_cmd.callback(ctx, container_name=None)
        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        self.assertIn("running", msg)
        self.assertNotIn("Pending", msg)

    async def test_status_command_shows_pending_op(self):
        from datetime import datetime, timezone

        from src import bot as bot_module
        from src.state import state

        mock_task = MagicMock()
        mock_task.done.return_value = False
        state.pending_ops["server1"] = mock_task
        state.pending_op_info["server1"] = {"action": "stop", "scheduled_at": datetime.now(timezone.utc)}
        try:
            ctx = MagicMock()
            ctx.send = AsyncMock()
            with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
                with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="running")):
                    await bot_module.status_cmd.callback(ctx, container_name=None)
            msg = ctx.send.call_args[0][0]
            self.assertIn("running", msg)
            self.assertIn("stop", msg)
            self.assertIn("Pending", msg)
        finally:
            state.pending_ops.clear()
            state.pending_op_info.clear()

    # --- announce command ---

    async def test_announce_command_single_container(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch(
                "src.bot.docker_control.run_blocking",
                new=AsyncMock(return_value=docker_control.Result(True, "ok: Message sent")),
            ):
                await bot_module.announce.callback(ctx, arg1="Hello world", arg2=None)
        ctx.send.assert_called_once()
        self.assertIn("ok", ctx.send.call_args[0][0])

    async def test_announce_command_multi_container_with_name(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value=docker_control.Result(True, "ok"))):
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
            with patch("src.bot.history.record") as mock_record:
                await bot_module.perm_add.callback(ctx, "start", role_name="Moderator")
        mock_add.assert_called_once_with("start", "Moderator")
        self.assertIn("Added", ctx.send.call_args[0][0])
        # L4: permission changes are audit-worthy and must be recorded.
        mock_record.assert_called_once_with(bot_module.HISTORY_FILE, ctx.author, "perm add start Moderator", "")

    async def test_perm_remove_valid_action(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author = MagicMock()
        with patch("src.bot.permissions.remove_role") as mock_remove:
            with patch("src.bot.history.record") as mock_record:
                await bot_module.perm_remove.callback(ctx, "stop", role_name="Moderator")
        mock_remove.assert_called_once_with("stop", "Moderator")
        self.assertIn("Removed", ctx.send.call_args[0][0])
        # L4: permission changes are audit-worthy and must be recorded.
        mock_record.assert_called_once_with(bot_module.HISTORY_FILE, ctx.author, "perm remove stop Moderator", "")

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

    # --- _format_delay ---

    def test_format_delay_seconds_only(self):
        from src import bot as bot_module

        self.assertEqual(bot_module._format_delay(30), "30 seconds")
        self.assertEqual(bot_module._format_delay(1), "1 second")

    def test_format_delay_whole_minutes(self):
        from src import bot as bot_module

        self.assertEqual(bot_module._format_delay(300), "5 minutes")
        self.assertEqual(bot_module._format_delay(60), "1 minute")

    def test_format_delay_minutes_with_remainder(self):
        """L13: a delay like 90s must not silently drop the remainder seconds."""
        from src import bot as bot_module

        self.assertEqual(bot_module._format_delay(90), "1 minute 30 seconds")
        self.assertEqual(bot_module._format_delay(150), "2 minutes 30 seconds")
        self.assertEqual(bot_module._format_delay(121), "2 minutes 1 second")

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
            with self.assertRaises(bot_module.SilentCheckFailure):
                await bot_module.check_guild(ctx)

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

        target_channel.send.assert_called_once_with("Hello!", allowed_mentions=ANY)
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

        ctx.channel.send.assert_called_once_with("Hello!", allowed_mentions=ANY)

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

    async def test_on_command_error_bad_argument_shows_usage(self):
        """L6: `!history abc` raises BadArgument during int conversion; it must
        not be silently swallowed (previously fell through to the logged-only
        else branch, leaving the user with no response at all)."""
        from src import bot as bot_module
        from discord.ext import commands

        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.command = MagicMock()
        ctx.command.qualified_name = "history"
        error = commands.BadArgument("abc is not an int")
        await bot_module.on_command_error(ctx, error)
        ctx.send.assert_called_once()
        self.assertIn("!history", ctx.send.call_args[0][0])

    async def test_on_command_error_silent_in_disallowed_channel(self):
        """SilentCheckFailure (as raised by check_guild for a disallowed origin)
        should produce no response."""
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel.id = 999
        ctx.command = MagicMock()
        error = bot_module.SilentCheckFailure("not allowed")
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


class TestPendingOps(unittest.IsolatedAsyncioTestCase):
    """Tests for the _pending_ops dict that prevents duplicate stop/restart tasks."""

    def setUp(self):
        from src import bot as bot_module

        self.bot_module = bot_module
        state.pending_ops.clear()
        state.maintenance_mode = False

    def tearDown(self):
        for task in list(state.pending_ops.values()):
            if asyncio.isfuture(task) and not task.done():
                task.cancel()
        state.pending_ops.clear()

    async def test_stop_rejects_duplicate_when_pending(self):
        """!stop while a task is already pending should send a rejection message."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()

        fake_task = MagicMock()
        fake_task.done.return_value = False
        state.pending_ops["test_container"] = fake_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            await bot_module.stop.callback(ctx, "test_container")

        ctx.send.assert_called_once()
        self.assertIn("already scheduled", ctx.send.call_args[0][0].lower())

    async def test_restart_rejects_duplicate_when_pending(self):
        """!restart while a task is already pending should send a rejection message."""
        bot_module = self.bot_module
        state.maintenance_mode = False
        ctx = MagicMock()
        ctx.send = AsyncMock()

        fake_task = MagicMock()
        fake_task.done.return_value = False
        state.pending_ops["test_container"] = fake_task

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
        # M1: the fix reads .cancelled()/.done() on the placeholder Future, so it
        # must be a real Future rather than a bare MagicMock.
        mock_loop.create_future.side_effect = asyncio.get_running_loop().create_future

        # L7: the pre-flight "is it running" check queries container_status
        # before proceeding -- must report "running" for the countdown to start.
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "ok")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.stop.callback(ctx, "test_container")

        # Countdown message sent (not a rejection)
        first_msg = ctx.send.call_args_list[0][0][0]
        self.assertIn("will stop", first_msg)
        # Task registered
        self.assertIn("test_container", state.pending_ops)
        self.assertIs(state.pending_ops["test_container"], mock_task)

    async def test_stop_on_already_stopped_container_skips_countdown(self):
        """L7: !stop on a container that isn't running must not announce a
        countdown (Discord + in-game) for an operation that's already a no-op --
        it should reply immediately instead."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "exited"
            return docker_control.Result(True, "ok")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                await bot_module.stop.callback(ctx, "test_container")

        ctx.send.assert_called_once()
        self.assertIn("not running", ctx.send.call_args[0][0].lower())
        ctx.channel.send.assert_not_called()
        self.assertNotIn("test_container", state.pending_ops)

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
        # M1: the fix reads .cancelled()/.done() on the placeholder Future, so it
        # must be a real Future rather than a bare MagicMock.
        mock_loop.create_future.side_effect = asyncio.get_running_loop().create_future

        # L7: the pre-flight "is it running" check must see "running" for ctx1's
        # call to proceed; ctx2 is rejected by the dedup check before ever
        # reaching it.
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "ok")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.stop.callback(ctx1, "test_container")
                            await bot_module.stop.callback(ctx2, "test_container")

        ctx2.send.assert_called_once()
        self.assertIn("already scheduled", ctx2.send.call_args[0][0].lower())

    async def test_announcement_exception_cleans_up_placeholder(self):
        """M1: if ctx.send (or another announcement step) raises while the countdown
        is being announced, the pending_ops placeholder must not be left behind --
        it would otherwise permanently block every future stop/restart for this
        container (has_pending_op would never clear)."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock(side_effect=RuntimeError("discord hiccup"))
        ctx.channel = MagicMock()
        ctx.channel.id = 100

        mock_loop = MagicMock()
        mock_loop.create_future.side_effect = asyncio.get_running_loop().create_future

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    # L7: the pre-flight status check must pass ("running") for
                    # execution to reach the countdown announcement at all.
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="running")):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            with self.assertRaises(RuntimeError):
                                await bot_module.stop.callback(ctx, "test_container")

        self.assertNotIn("test_container", state.pending_ops)
        self.assertNotIn("test_container", state.pending_op_info)
        mock_loop.create_task.assert_not_called()

    async def test_cancel_during_announcement_prevents_scheduling(self):
        """M1: if !cancel (or !stop now / !maintenance on) runs while the countdown
        announcement for a prior !stop is still in flight, the real countdown task
        must not be scheduled on top of a cancellation the user was already told
        succeeded."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100

        async def _send_and_cancel(*args, **kwargs):
            state.cancel_all_pending()

        ctx.channel.send = AsyncMock(side_effect=_send_and_cancel)

        mock_loop = MagicMock()
        mock_loop.create_future.side_effect = asyncio.get_running_loop().create_future

        # L7: the pre-flight status check must pass ("running") for execution to
        # reach the countdown announcement (where the cancel-during-flight happens).
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "ok")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.stop.callback(ctx, "test_container")

        mock_loop.create_task.assert_not_called()
        self.assertNotIn("test_container", state.pending_ops)
        last_msg = ctx.send.call_args_list[-1][0][0]
        self.assertIn("cancelled", last_msg.lower())

    async def test_pending_op_info_set_before_announcement_completes(self):
        """M1 (ordering): pending_op_info must be populated before the countdown
        announcement awaits complete, so !status reports an accurate action/remaining
        time during that window instead of hitting the info-less fallback branch."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100

        snapshot = {}

        async def _capture_send(msg):
            snapshot["info"] = dict(state.pending_op_info.get("test_container", {}))

        ctx.send = AsyncMock(side_effect=_capture_send)

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_loop = MagicMock()

        def _create_task(coro):
            coro.close()  # prevent "coroutine never awaited" warning
            return mock_task

        mock_loop.create_task.side_effect = _create_task
        mock_loop.create_future.side_effect = asyncio.get_running_loop().create_future

        # L7: the pre-flight status check must pass ("running") for execution to
        # reach the countdown announcement at all.
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "ok")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["test_container"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.stop.callback(ctx, "test_container")

        self.assertEqual(snapshot["info"].get("action"), "stop")
        self.assertIn("scheduled_at", snapshot["info"])


class TestStopNow(unittest.IsolatedAsyncioTestCase):
    """Tests for the !stop now immediate-shutdown path."""

    def setUp(self):
        from src import bot as bot_module

        self.bot_module = bot_module
        state.pending_ops.clear()
        state.maintenance_mode = False

    def tearDown(self):
        for task in list(state.pending_ops.values()):
            if asyncio.isfuture(task) and not task.done():
                task.cancel()
        state.pending_ops.clear()

    async def test_stop_now_immediate_as_admin(self):
        """Admin using !stop now should bypass stop_now permission and stop immediately."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        # L7: the pre-flight status check must see "running" to proceed.
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "stopped")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        await bot_module.stop.callback(ctx, "now")

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("immediately" in c for c in calls))
        self.assertTrue(any("stopped" in c for c in calls))

    async def test_stop_now_on_already_stopped_container_skips_announcements(self):
        """L7: !stop now on a container that isn't running must not send the
        "shutting down NOW" announcements for an operation that's already a
        no-op -- it should reply immediately instead. A pending countdown is
        still cancelled, since that's a useful side effect regardless of the
        container's current state."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        pending_task = MagicMock()
        pending_task.done.return_value = False
        state.pending_ops["server1"] = pending_task

        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "exited"
            return docker_control.Result(True, "ok")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                await bot_module.stop.callback(ctx, "now")

        self.assertIn("not running", ctx.send.call_args[0][0].lower())
        ctx.channel.send.assert_not_called()
        # The pre-existing pending countdown was still cancelled.
        pending_task.cancel.assert_called_once()
        self.assertNotIn("server1", state.pending_ops)

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
            if func.__name__ == "stop_container":
                return docker_control.Result(True, "stopped")
            if func.__name__ == "container_status":
                # First call is L7's pre-flight check (must be "running" to
                # proceed); second call is M2's post-stop crash-alerting re-seed.
                status_calls = sum(1 for name, _ in run_blocking_calls if name == "container_status")
                return "running" if status_calls <= 1 else "exited"
            return docker_control.Result(True, "ok")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        await bot_module.stop.callback(ctx, "now")

        # Discord announcement sent to channel
        ctx.channel.send.assert_called_once()
        announcement = ctx.channel.send.call_args[0][0]
        self.assertIn("NOW", announcement)

        # L12: history.record now goes through run_blocking too, first; then
        # L7's pre-flight check; then in-game announcement before stop; then
        # the crash-alerting baseline (container_status) re-seeded after a
        # successful stop (M2).
        func_names = [c[0] for c in run_blocking_calls]
        self.assertEqual(func_names, ["record", "container_status", "announce_in_game", "stop_container", "container_status"])

    async def test_stop_now_reseeds_crash_alerting_baseline(self):
        """M2: a successful !stop now must update state.last_known_status so the
        crash-check loop doesn't mistake this bot-initiated stop for a crash."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        state.last_known_status["server1"] = "running"

        status_calls = []

        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "stop_container":
                return docker_control.Result(True, "stopped")
            if func.__name__ == "container_status":
                # First call is L7's pre-flight check (must be "running" to
                # proceed); second call is M2's post-stop crash-alerting re-seed.
                status_calls.append(1)
                return "running" if len(status_calls) <= 1 else "exited"
            return docker_control.Result(True, "ok")

        try:
            with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
                with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                    with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                        with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                            await bot_module.stop.callback(ctx, "now")

            self.assertEqual(state.last_known_status["server1"], "exited")
        finally:
            state.last_known_status.clear()

    async def test_stop_now_with_container_name(self):
        """!stop server1 now should parse both args correctly."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        # L7: the pre-flight status check must see "running" to proceed.
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "stopped")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        await bot_module.stop.callback(ctx, "server1", "now")

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

        # L7: the pre-flight status check must see "running" to proceed.
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "stopped")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        await bot_module.stop.callback(ctx, "now", "server1")

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
                await bot_module.stop.callback(ctx, "now")

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

        # L7: the pre-flight status check must see "running" to proceed.
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "stopped")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.permissions.is_member_allowed", return_value=True) as mock_perm:
                        with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                            await bot_module.stop.callback(ctx, "now")

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
        state.pending_ops["server1"] = pending_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch(
                        "src.bot.docker_control.run_blocking",
                        new=AsyncMock(return_value=docker_control.Result(True, "stopped")),
                    ):
                        await bot_module.stop.callback(ctx, "now")

        pending_task.cancel.assert_called_once()
        self.assertNotIn("server1", state.pending_ops)

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
        # The fix reads .cancelled()/.done() on the placeholder Future, so it
        # must be a real Future rather than a bare MagicMock.
        mock_loop.create_future.side_effect = asyncio.get_running_loop().create_future

        # L7: the pre-flight status check must see "running" to proceed.
        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "running"
            return docker_control.Result(True, "ok")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        with patch.object(bot_module.bot, "loop", mock_loop):
                            await bot_module.stop.callback(ctx)

        first_msg = ctx.send.call_args_list[0][0][0]
        self.assertIn("will stop", first_msg)
        # Task registered (not treated as cancelled-during-announcement).
        self.assertIs(state.pending_ops.get("server1"), mock_task)

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

            # L7: the pre-flight status check must see "running" to proceed.
            async def mock_run_blocking(func, *args, **kwargs):
                if func.__name__ == "container_status":
                    return "running"
                return docker_control.Result(True, "stopped")

            with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
                with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                    with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                        with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                            await bot_module.stop.callback(ctx, variant)

            calls = [c[0][0] for c in ctx.send.call_args_list]
            self.assertTrue(any("immediately" in c for c in calls), f"Failed for variant {variant!r}")

    async def test_stop_now_disallowed_container(self):
        """!stop evil now should reject the container before reaching the now logic."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.stop.callback(ctx, "evil", "now")

        ctx.send.assert_called_once()
        self.assertIn("not in the allowed list", ctx.send.call_args[0][0])

    async def test_stop_now_multiple_containers_no_name(self):
        """!stop now with multiple containers and no name should prompt for a container."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1", "server2"]):
            await bot_module.stop.callback(ctx, "now")

        ctx.send.assert_called_once()
        self.assertIn("Please specify one", ctx.send.call_args[0][0])


class TestLogsCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state.maintenance_mode = False

    async def test_logs_blocked_during_maintenance(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        state.maintenance_mode = True
        state.maintenance_reason = "update"
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.logs_cmd.callback(ctx, arg1=None, arg2=None)
        self.assertIn("maintenance", ctx.send.call_args[0][0].lower())
        state.maintenance_mode = False

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
        # L12: run_blocking is also used for history.record now, so check the
        # specific container_logs call rather than asserting a single call.
        mock_rb.assert_any_call(docker_control.container_logs, "server1", 10)

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

    async def test_logs_command_unrecognized_arg_shows_usage(self):
        """L6: a typo'd container name (e.g. `!logs tyop_container`) must not
        silently fall back to the default container -- it should show usage."""
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.logs_cmd.callback(ctx, arg1="tyop_container", arg2=None)
        ctx.send.assert_called_once()
        self.assertIn("Unrecognized argument", ctx.send.call_args[0][0])
        self.assertIn("tyop_container", ctx.send.call_args[0][0])


class TestStatsCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        state.maintenance_mode = False

    async def test_stats_blocked_during_maintenance(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        state.maintenance_mode = True
        state.maintenance_reason = "patching"
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            await bot_module.stats_cmd.callback(ctx, container_name=None)
        self.assertIn("maintenance", ctx.send.call_args[0][0].lower())
        state.maintenance_mode = False

    async def test_stats_command_error_field(self):
        from src import bot as bot_module

        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch(
                "src.bot.docker_control.run_blocking", new=AsyncMock(return_value={"status": "running", "error": "timeout"})
            ):
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


class TestRestartNow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from src import bot as bot_module

        self.bot_module = bot_module
        state.pending_ops.clear()
        state.maintenance_mode = False

    def tearDown(self):
        for task in list(state.pending_ops.values()):
            if asyncio.isfuture(task) and not task.done():
                task.cancel()
        state.pending_ops.clear()

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
                    with patch(
                        "src.bot.docker_control.run_blocking",
                        new=AsyncMock(return_value=docker_control.Result(True, "restarted")),
                    ):
                        await bot_module.restart.callback(ctx, "now")

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("immediately" in c for c in calls))
        self.assertTrue(any("restarted" in c for c in calls))

    async def test_restart_now_succeeds_on_stopped_container(self):
        """L7: unlike !stop, !restart must NOT be blocked by a 'not running'
        pre-check -- Docker's restart legitimately starts a stopped container,
        so gating it on current status would be a real behavior regression."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()
        ctx.author.guild_permissions.administrator = True

        async def mock_run_blocking(func, *args, **kwargs):
            if func.__name__ == "container_status":
                return "exited"
            return docker_control.Result(True, "restarted")

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", side_effect=mock_run_blocking):
                        await bot_module.restart.callback(ctx, "now")

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("restarted" in c for c in calls))
        self.assertFalse(any("not running" in c.lower() for c in calls))

    async def test_restart_now_denied_without_permission(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author.guild_permissions.administrator = False

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.permissions.is_member_allowed", return_value=False):
                await bot_module.restart.callback(ctx, "now")

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
        state.pending_ops["server1"] = pending_task

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                    with patch(
                        "src.bot.docker_control.run_blocking",
                        new=AsyncMock(return_value=docker_control.Result(True, "restarted")),
                    ):
                        await bot_module.restart.callback(ctx, "now")

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
                    with patch(
                        "src.bot.docker_control.run_blocking",
                        new=AsyncMock(return_value=docker_control.Result(True, "restarted")),
                    ):
                        await bot_module.restart.callback(ctx, "server1", "now")

        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("server1" in c and "immediately" in c for c in calls))


class TestMaintenanceMode(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from src import bot as bot_module

        self.bot_module = bot_module
        state.maintenance_mode = False
        state.maintenance_reason = ""

    def tearDown(self):
        state.maintenance_mode = False
        state.maintenance_reason = ""

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

        self.assertTrue(state.maintenance_mode)
        self.assertEqual(state.maintenance_reason, "Updating server")
        calls = [c[0][0] for c in ctx.send.call_args_list]
        self.assertTrue(any("enabled" in c.lower() for c in calls))

    async def test_maintenance_on_cancels_pending_ops(self):
        """Enabling maintenance mode should cancel any in-flight countdowns."""
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        mock_task = MagicMock()
        mock_task.done.return_value = False
        state.pending_ops["server1"] = mock_task

        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                await bot_module.maintenance_cmd.callback(ctx, toggle="on", reason="Patching")

        mock_task.cancel.assert_called_once()
        self.assertNotIn("server1", state.pending_ops)
        calls = " ".join(c[0][0] for c in ctx.send.call_args_list)
        self.assertIn("server1", calls)

    async def test_maintenance_off(self):
        bot_module = self.bot_module
        state.maintenance_mode = True
        state.maintenance_reason = "Test"
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                await bot_module.maintenance_cmd.callback(ctx, toggle="off", reason="")

        self.assertFalse(state.maintenance_mode)
        self.assertEqual(state.maintenance_reason, "")

    async def test_maintenance_status(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await bot_module.maintenance_cmd.callback(ctx, toggle=None, reason="")
        self.assertIn("OFF", ctx.send.call_args[0][0])

    async def test_maintenance_blocks_start(self):
        bot_module = self.bot_module
        state.maintenance_mode = True
        state.maintenance_reason = "Scheduled downtime"
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

    async def test_maintenance_mode_does_not_block_guide_history_or_perm(self):
        """L5: guide/history/perm* never call is_maintenance_active at all, so
        they must keep working during maintenance mode -- verified via the real
        handlers rather than by probing hardcoded command-name strings against
        the state method in isolation."""
        bot_module = self.bot_module
        state.maintenance_mode = True

        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.author = MagicMock()

        block_message = "bot is in maintenance mode."

        await bot_module.guide.callback(ctx)
        self.assertNotIn(block_message, ctx.send.call_args[0][0].lower())

        ctx.send.reset_mock()
        with patch("src.history.load", return_value=[]):
            await bot_module.history_cmd.callback(ctx, count=10)
        self.assertNotIn(block_message, ctx.send.call_args[0][0].lower())

        ctx.send.reset_mock()
        with patch("src.bot.permissions.list_permissions", return_value={"start": ["ServerAdmin"]}):
            await bot_module.perm_list.callback(ctx)
        self.assertNotIn(block_message, ctx.send.call_args[0][0].lower())

    def test_check_maintenance_blocks_control_commands(self):
        state.maintenance_mode = True
        self.assertTrue(state.is_maintenance_active("start"))


class TestCancelCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from src import bot as bot_module

        self.bot_module = bot_module

    async def test_cancel_no_pending_ops(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()

        await bot_module.cancel.callback(ctx)

        self.assertIn("No pending", ctx.send.call_args[0][0])

    async def test_cancel_cancels_all_pending_ops(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.channel = MagicMock()
        ctx.channel.id = 100
        ctx.channel.send = AsyncMock()

        mock_task_1 = MagicMock()
        mock_task_1.done.return_value = False
        mock_task_2 = MagicMock()
        mock_task_2.done.return_value = False
        state.pending_ops["server1"] = mock_task_1
        state.pending_ops["server2"] = mock_task_2

        with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
            with patch.object(bot_module, "ANNOUNCE_ROLE_ID", 0):
                await bot_module.cancel.callback(ctx)

        mock_task_1.cancel.assert_called_once()
        mock_task_2.cancel.assert_called_once()
        self.assertEqual(state.pending_ops, {})
        self.assertEqual(state.pending_op_info, {})
        calls = " ".join(c[0][0] for c in ctx.send.call_args_list)
        self.assertIn("server1", calls)
        self.assertIn("server2", calls)
        ctx.channel.send.assert_awaited_once()

    async def test_cancel_records_history(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()

        with patch("src.bot.history.record") as mock_record:
            await bot_module.cancel.callback(ctx)

        mock_record.assert_called_once_with(bot_module.HISTORY_FILE, ctx.author, "cancel", "")


class TestHistoryCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from src import bot as bot_module

        self.bot_module = bot_module

    async def test_history_command_empty(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch("src.history.load", return_value=[]):
            await bot_module.history_cmd.callback(ctx, count=10)
        self.assertIn("No command history", ctx.send.call_args[0][0])

    async def test_history_command_with_entries(self):
        bot_module = self.bot_module
        ctx = MagicMock()
        ctx.send = AsyncMock()
        entries = [
            {"timestamp": "2026-01-01T00:00:00+00:00", "user": "TestUser", "command": "start", "container": "server1"},
        ]
        with patch("src.history.load", return_value=entries):
            await bot_module.history_cmd.callback(ctx, count=10)
        output = ctx.send.call_args[0][0]
        self.assertIn("TestUser", output)
        self.assertIn("start", output)


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
