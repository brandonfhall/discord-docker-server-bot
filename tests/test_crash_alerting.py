import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.state import state


class TestCrashAlerting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from src import bot as bot_module

        self.bot_module = bot_module
        state.last_known_status.clear()

    def tearDown(self):
        state.last_known_status.clear()

    async def test_crash_detected_sends_alert(self):
        bot_module = self.bot_module
        state.last_known_status["server1"] = "running"

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
        self.assertEqual(state.last_known_status["server1"], "exited")

    async def test_no_alert_after_bot_initiated_stop(self):
        """M2: after a bot-initiated stop re-seeds last_known_status to 'exited',
        the next poll must not treat that already-known state as a crash."""
        bot_module = self.bot_module
        state.last_known_status["server1"] = "exited"  # as set by the stop handler

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "CRASH_ALERT_CHANNEL_ID", 123):
                with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="exited")):
                    with patch.object(bot_module.bot, "get_channel", return_value=mock_channel):
                        await bot_module.crash_check_loop.coro()

        mock_channel.send.assert_not_called()
        self.assertEqual(state.last_known_status["server1"], "exited")

    async def test_no_alert_when_still_running(self):
        bot_module = self.bot_module
        state.last_known_status["server1"] = "running"

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="running")):
                await bot_module.crash_check_loop.coro()

        self.assertEqual(state.last_known_status["server1"], "running")

    async def test_no_alert_on_first_check(self):
        """First poll seeds status without alerting."""
        bot_module = self.bot_module

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="exited")):
                await bot_module.crash_check_loop.coro()

        # No alert because previous status was None (first check)
        self.assertEqual(state.last_known_status["server1"], "exited")

    async def test_crash_alert_fires_when_container_removed(self):
        """M4: a container removed while running (docker rm -f) makes
        container_status() return None. The alert must still fire, naming the
        container and reporting it as removed/not found -- this is the exact
        scenario crash alerting exists for."""
        bot_module = self.bot_module
        state.last_known_status["server1"] = "running"

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "CRASH_ALERT_CHANNEL_ID", 123):
                with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value=None)):
                        with patch.object(bot_module.bot, "get_channel", return_value=mock_channel):
                            await bot_module.crash_check_loop.coro()

        mock_channel.send.assert_called_once()
        sent = mock_channel.send.call_args[0][0]
        self.assertIn("Crash Alert", sent)
        self.assertIn("server1", sent)
        self.assertIsNone(state.last_known_status["server1"])

    async def test_no_false_alert_on_daemon_error(self):
        """M2/M4 ordering: container_status() returns the literal "error" when
        the Docker daemon itself is unreachable. A daemon blip must not fire a
        false "removed" alert, and must not clobber the last-known baseline."""
        bot_module = self.bot_module
        state.last_known_status["server1"] = "running"

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()

        with patch.object(bot_module, "ALLOWED_CONTAINERS", ["server1"]):
            with patch.object(bot_module, "CRASH_ALERT_CHANNEL_ID", 123):
                with patch.object(bot_module, "ANNOUNCE_CHANNEL_ID", 0):
                    with patch("src.bot.docker_control.run_blocking", new=AsyncMock(return_value="error")):
                        with patch.object(bot_module.bot, "get_channel", return_value=mock_channel):
                            await bot_module.crash_check_loop.coro()

        mock_channel.send.assert_not_called()
        # Baseline must be preserved, not overwritten with "error".
        self.assertEqual(state.last_known_status["server1"], "running")

    async def test_crash_alert_uses_announce_channel_fallback(self):
        bot_module = self.bot_module
        state.last_known_status["server1"] = "running"

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
