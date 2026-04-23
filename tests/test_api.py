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


class TestStatusEndpoint(unittest.TestCase):
    """Tests for the FastAPI /status route."""

    def test_status_returns_expected_structure(self):
        from fastapi.testclient import TestClient
        from src import api as api_module
        with patch.object(api_module, "STATUS_TOKEN", None):
            with patch.object(api_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.api.docker_control.container_status", return_value="running"):
                    with patch("src.api.permissions.list_permissions", return_value={"start": ["admin"]}):
                        client = TestClient(api_module.app)
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
        from src import api as api_module
        with patch.object(api_module, "STATUS_TOKEN", "secret"):
            client = TestClient(api_module.app)
            response = client.get("/status")
        self.assertEqual(response.status_code, 401)

    def test_status_accepts_token_via_header(self):
        from fastapi.testclient import TestClient
        from src import api as api_module
        with patch.object(api_module, "STATUS_TOKEN", "secret"):
            with patch.object(api_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.api.docker_control.container_status", return_value="running"):
                    with patch("src.api.permissions.list_permissions", return_value={}):
                        client = TestClient(api_module.app)
                        response = client.get("/status", headers={"X-Auth-Token": "secret"})
        self.assertEqual(response.status_code, 200)

    def test_status_accepts_token_via_query_param(self):
        from fastapi.testclient import TestClient
        from src import api as api_module
        with patch.object(api_module, "STATUS_TOKEN", "secret"):
            with patch.object(api_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.api.docker_control.container_status", return_value="running"):
                    with patch("src.api.permissions.list_permissions", return_value={}):
                        client = TestClient(api_module.app)
                        response = client.get("/status?token=secret")
        self.assertEqual(response.status_code, 200)

