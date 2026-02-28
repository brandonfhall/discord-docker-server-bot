import unittest
import asyncio
import json
import os
from unittest.mock import MagicMock, patch

from src import docker_control
from src import permissions


class TestDockerControl(unittest.TestCase):
    def setUp(self):
        # Reset the global client before each test to ensure mocks are used
        docker_control._docker_client = None

    def test_validate_container_name(self):
        """Test that container names are validated correctly."""
        self.assertTrue(docker_control._validate_container_name("my_game_server"))
        self.assertTrue(docker_control._validate_container_name("my-server.1"))

        # SECURITY TEST: These strings simulate injection attacks.
        # They are NOT executed. We are asserting that the validator correctly REJECTS them.
        self.assertFalse(docker_control._validate_container_name("server; rm -rf"))
        self.assertFalse(docker_control._validate_container_name("server&"))
        self.assertFalse(docker_control._validate_container_name(" "))

    def test_sanitize_message(self):
        """Test that messages are sanitized of shell metacharacters."""
        msg = "Hello; echo 'hack'"
        clean = docker_control._sanitize(msg)
        # Expecting shell chars to be removed
        self.assertNotIn(";", clean)
        self.assertNotIn("'", clean)
        # With strict whitelist, it removes the chars entirely
        self.assertEqual(clean, "Hello echo hack")

    @patch('src.docker_control.docker.from_env')
    def test_docker_actions(self, mock_from_env):
        """Test start, stop, and announce logic with mocked docker client."""
        print("\nTesting Docker actions (Start/Stop/Announce)...")

        # Setup Mock
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_from_env.return_value = mock_client
        mock_client.containers.get.return_value = mock_container

        # Patch ALLOWED_CONTAINERS in docker_control module
        with patch('src.docker_control.ALLOWED_CONTAINERS', ['my_game_server']):

            # 1. Test Start
            mock_container.status = "exited"
            res = docker_control.start_container("my_game_server")
            print(f"  Start (exited): {res}")
            self.assertEqual(res, "started")
            mock_container.start.assert_called_once()

            mock_container.status = "running"
            res = docker_control.start_container("my_game_server")
            print(f"  Start (running): {res}")
            self.assertEqual(res, "already running")

            # 2. Test Stop
            mock_container.status = "running"
            res = docker_control.stop_container("my_game_server")
            print(f"  Stop (running): {res}")
            self.assertEqual(res, "stopped")
            mock_container.stop.assert_called_once()

            # 3. Test Announce
            # Mock exec_run result
            mock_exec = MagicMock()
            mock_exec.exit_code = 0
            mock_exec.output = b"Message sent"
            mock_container.exec_run.return_value = mock_exec

            res = docker_control.announce_in_game("my_game_server", "Hello World")
            print(f"  Announce: {res}")
            self.assertIn("ok", res)
            # Verify sanitization happened in the call
            args, _ = mock_container.exec_run.call_args
            # The command is usually passed as a list or string depending on config
            # We just check that exec_run was called
            self.assertTrue(mock_container.exec_run.called)

    def test_docker_security_checks(self):
        """Test that unauthorized containers are rejected."""
        print("\nTesting Docker security checks...")
        res = docker_control.start_container("evil_container")
        print(f"  Security Check Result: {res}")
        self.assertIn("not allowed", res)


class TestPermissions(unittest.TestCase):
    def setUp(self):
        # Use a temporary file for permission tests
        self.test_file = "test_permissions.json"
        self.original_file = permissions.PERMISSIONS_FILE
        permissions.PERMISSIONS_FILE = self.test_file

    def tearDown(self):
        permissions.PERMISSIONS_FILE = self.original_file
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_default_permissions_creation(self):
        """Test that the permissions file is created with defaults if missing."""
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

        # This should trigger file creation
        data = permissions._load()
        self.assertTrue(os.path.exists(self.test_file))
        self.assertIn("start", data)

    def test_is_member_allowed(self):
        """Test role checking logic."""
        # Create a dummy permissions file
        with open(self.test_file, 'w') as f:
            json.dump({"start": ["SuperUser"]}, f)

        # Mock a Discord member with specific roles
        member = MagicMock()
        role = MagicMock()
        role.name = "SuperUser"
        member.roles = [role]

        self.assertTrue(permissions.is_member_allowed("start", member))

        role.name = "Peasant"
        self.assertFalse(permissions.is_member_allowed("start", member))

    def test_modify_permissions(self):
        """Test adding and removing roles."""
        print("\nTesting Permission modification...")
        permissions.add_role("stop", "Moderator")
        data = permissions._load()
        self.assertIn("Moderator", data["stop"])
        print("  Added 'Moderator' to 'stop'.")

        permissions.remove_role("stop", "Moderator")
        data = permissions._load()
        self.assertNotIn("Moderator", data["stop"])
        print("  Removed 'Moderator' from 'stop'.")


class TestBotLogic(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_container(self):
        """Test the container resolution logic."""
        # Import bot here to avoid top-level execution issues if any
        from src import bot

        # Mock the context
        ctx = MagicMock()
        # ctx.send must be awaitable
        f = asyncio.Future()
        f.set_result(None)
        ctx.send.return_value = f

        # Case 1: Specific name provided and allowed
        bot.ALLOWED_CONTAINERS = ["server1", "server2"]
        res = await bot.resolve_container(ctx, "server1")
        self.assertEqual(res, "server1")

        # Case 2: No name provided, multiple allowed -> Error/None
        res = await bot.resolve_container(ctx, None)
        self.assertIsNone(res)
        # Verify it asked for specification
        ctx.send.assert_called()

        # Case 3: No name provided, single allowed -> Auto-resolve
        bot.ALLOWED_CONTAINERS = ["server1"]
        res = await bot.resolve_container(ctx, None)
        self.assertEqual(res, "server1")

    async def test_verify_token(self):
        """Test the API token verification logic."""
        print("\nTesting API Token Verification...")
        from src import bot
        from fastapi import HTTPException

        # Save original token
        original_token = bot.STATUS_TOKEN

        try:
            # Case 1: No token configured (Open API)
            bot.STATUS_TOKEN = None
            await bot.verify_token(None, None)
            print("  Case 1 (No Token Configured): Passed (Allowed)")

            # Case 2: Token configured, correct token provided
            bot.STATUS_TOKEN = "secret123"
            await bot.verify_token("secret123", None)  # Header
            await bot.verify_token(None, "secret123")  # Query param
            print("  Case 2 (Correct Token): Passed (Allowed)")

            # Case 3: Token configured, wrong token provided
            with self.assertRaises(HTTPException):
                await bot.verify_token("wrongpass", None)
            print("  Case 3 (Wrong Token): Passed (Denied)")

        finally:
            bot.STATUS_TOKEN = original_token