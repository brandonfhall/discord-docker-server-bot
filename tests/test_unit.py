import unittest
import asyncio
import json
import os
import sys
from unittest.mock import MagicMock, patch

# Add project root to path so we can import src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src import docker_control
from src import permissions

class TestDockerControl(unittest.TestCase):
    def test_validate_container_name(self):
        """Test that container names are validated correctly."""
        self.assertTrue(docker_control._validate_container_name("valheim_server"))
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