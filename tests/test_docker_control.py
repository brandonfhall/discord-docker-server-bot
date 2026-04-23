import asyncio
import json
import logging
import os
import sys
import unittest
from io import StringIO
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from src import docker_control, permissions
from src.state import state


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
            result = docker_control.start_container("my_game_server")
            self.assertTrue(result.success)
            self.assertEqual(result.message, "started")
            mock_container.start.assert_called_once()

            # start: already running
            mock_container.status = "running"
            result = docker_control.start_container("my_game_server")
            self.assertFalse(result.success)
            self.assertEqual(result.message, "already running")

            # stop: running → stopped
            mock_container.status = "running"
            result = docker_control.stop_container("my_game_server")
            self.assertTrue(result.success)
            self.assertEqual(result.message, "stopped")
            mock_container.stop.assert_called_once()

            # stop: already stopped
            mock_container.status = "exited"
            result = docker_control.stop_container("my_game_server")
            self.assertFalse(result.success)
            self.assertEqual(result.message, "not running")

            # restart
            result = docker_control.restart_container("my_game_server")
            self.assertTrue(result.success)
            self.assertEqual(result.message, "restarted")
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
            self.assertTrue(result.success)
            self.assertIn("ok", result.message)
            self.assertTrue(mock_container.exec_run.called)

    def test_docker_security_checks(self):
        result = docker_control.start_container("evil_container")
        self.assertFalse(result.success)
        self.assertIn("not allowed", result.message)

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
        self.assertTrue(result.success)
        self.assertEqual(result.message, "ok")
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
        self.assertFalse(result.success)
        self.assertTrue(result.message.startswith("error:"))

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
        self.assertFalse(result.success)
        self.assertTrue(result.message.startswith("error (1):"))


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

