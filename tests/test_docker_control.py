import unittest
from unittest.mock import MagicMock, patch

from src import docker_control


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

    # --- M2: daemon-down vs. genuinely-unexpected errors ---

    @patch("src.docker_control.docker.from_env")
    def test_find_container_daemon_error_propagates(self, mock_from_env):
        """A connection-level docker.errors.DockerException must NOT be
        swallowed by _find_container_by_name -- it has to propagate so callers
        can tell "daemon unreachable" apart from "container not found" (M2)."""
        import docker

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.side_effect = docker.errors.DockerException("socket refused")

        with self.assertRaises(docker.errors.DockerException):
            docker_control._find_container_by_name(mock_client, "server")

    @patch("src.docker_control.docker.from_env")
    def test_start_container_daemon_down_reports_daemon_error_not_not_found(self, mock_from_env):
        import docker

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.side_effect = docker.errors.DockerException("cannot connect to the docker daemon")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.start_container("my_game_server")

        self.assertFalse(result.success)
        self.assertNotIn("not found", result.message)
        self.assertIn("docker daemon error", result.message)

    @patch("src.docker_control.docker.from_env")
    def test_start_container_start_raises_api_error_returns_result(self, mock_from_env):
        """c.start() raising docker.errors.APIError must not escape as an
        exception -- the caller (bot.py's !start handler) needs a Result so the
        user gets a reply instead of silence (M2)."""
        import docker

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.status = "exited"
        mock_container.start.side_effect = docker.errors.APIError("boom")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.start_container("my_game_server")

        self.assertIsInstance(result, docker_control.Result)
        self.assertFalse(result.success)
        self.assertIn("docker error", result.message)

    @patch("src.docker_control.docker.from_env")
    def test_stop_container_stop_raises_api_error_returns_result(self, mock_from_env):
        import docker

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.status = "running"
        mock_container.stop.side_effect = docker.errors.APIError("boom")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.stop_container("my_game_server")

        self.assertIsInstance(result, docker_control.Result)
        self.assertFalse(result.success)
        self.assertIn("docker error", result.message)

    @patch("src.docker_control.docker.from_env")
    def test_restart_container_restart_raises_api_error_returns_result(self, mock_from_env):
        import docker

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.restart.side_effect = docker.errors.APIError("boom")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            result = docker_control.restart_container("my_game_server")

        self.assertIsInstance(result, docker_control.Result)
        self.assertFalse(result.success)
        self.assertIn("docker error", result.message)

    @patch("src.docker_control.docker.from_env")
    def test_container_status_daemon_error_returns_error_string(self, mock_from_env):
        """container_status() returns the literal "error" (not None) when the
        daemon is unreachable, so !status/`/status` can display something
        honest instead of "not found" (M2)."""
        import docker

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.side_effect = docker.errors.DockerException("cannot connect")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            self.assertEqual(docker_control.container_status("my_game_server"), "error")

    @patch("src.docker_control.docker.from_env")
    def test_container_status_requests_connection_error_returns_error_string(self, mock_from_env):
        """Empirically, a dropped unix socket can surface as a bare
        requests.exceptions.ConnectionError rather than a
        docker.errors.DockerException -- both must be treated as a daemon
        outage, not swallowed as "not found" or left to escape uncaught."""
        import requests

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.side_effect = requests.exceptions.ConnectionError("connection refused")

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            self.assertEqual(docker_control.container_status("my_game_server"), "error")

    # --- container_health ---

    @patch("src.docker_control.docker.from_env")
    def test_container_health_reports_status(self, mock_from_env):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.attrs = {"State": {"Health": {"Status": "healthy"}}}

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            self.assertEqual(docker_control.container_health("my_game_server"), "healthy")

    @patch("src.docker_control.docker.from_env")
    def test_container_health_none_when_no_healthcheck_configured(self, mock_from_env):
        """A container with no HEALTHCHECK has no "Health" key in State at all --
        this must come back as None, not raise or return a bogus status."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container
        mock_container.attrs = {"State": {"Status": "running"}}

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            self.assertIsNone(docker_control.container_health("my_game_server"))

    def test_container_health_disallowed_container(self):
        self.assertIsNone(docker_control.container_health("evil_container"))

    @patch("src.docker_control.docker.from_env")
    def test_container_health_container_not_found(self, mock_from_env):
        import docker

        mock_client = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found", MagicMock())

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            self.assertIsNone(docker_control.container_health("my_game_server"))

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
    def test_announce_in_game_template_with_extra_braces(self, mock_from_env):
        """M7: a template with braces besides {message} (e.g. Minecraft's tellraw
        JSON payload) must not crash -- str.format() would raise KeyError/ValueError
        on the extra braces, so the {message} substitution must use a literal
        replace instead."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container

        mock_exec = MagicMock()
        mock_exec.exit_code = 0
        mock_exec.output = b"ok"
        mock_container.exec_run.return_value = mock_exec

        with patch("src.docker_control.ALLOWED_CONTAINERS", ["my_game_server"]):
            with patch(
                "src.docker_control.CONTAINER_MESSAGE_CMD",
                'rcon-cli tellraw @a {"text":"{message}"}',
            ):
                result = docker_control.announce_in_game("my_game_server", "Hello")
        self.assertTrue(result.success)
        mock_container.exec_run.assert_called_once_with(["/bin/sh", "-c", 'rcon-cli tellraw @a {"text":"Hello"}'])

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
