import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src import api as api_module

client = TestClient(api_module.app)


class TestHealthzEndpoint(unittest.TestCase):
    """Tests for the unauthenticated /healthz liveness route."""

    def test_healthz_returns_ok(self):
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_healthz_requires_no_token(self):
        """Liveness check must be reachable without credentials."""
        with patch.object(api_module, "STATUS_TOKEN", "secret"):
            response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)

    def test_root_redirects_to_status(self):
        response = client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/status")


class TestStatusEndpoint(unittest.TestCase):
    """Tests for the FastAPI /status route."""

    def test_status_returns_expected_structure(self):
        with patch.object(api_module, "STATUS_TOKEN", None):
            with patch.object(api_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.api.docker_control.container_status", return_value="running"):
                    with patch("src.api.permissions.list_permissions", return_value={"start": ["admin"]}):
                        response = client.get("/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("containers", data)
        self.assertIn("permissions", data)
        self.assertIn("logs", data)
        self.assertEqual(data["containers"]["test_container"], "running")

    def test_status_requires_token_when_configured(self):
        with patch.object(api_module, "STATUS_TOKEN", "secret"):
            response = client.get("/status")
        self.assertEqual(response.status_code, 401)

    def test_status_accepts_token_via_header(self):
        with patch.object(api_module, "STATUS_TOKEN", "secret"):
            with patch.object(api_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.api.docker_control.container_status", return_value="running"):
                    with patch("src.api.permissions.list_permissions", return_value={}):
                        response = client.get("/status", headers={"X-Auth-Token": "secret"})
        self.assertEqual(response.status_code, 200)

    def test_status_accepts_token_via_query_param(self):
        with patch.object(api_module, "STATUS_TOKEN", "secret"):
            with patch.object(api_module, "ALLOWED_CONTAINERS", ["test_container"]):
                with patch("src.api.docker_control.container_status", return_value="running"):
                    with patch("src.api.permissions.list_permissions", return_value={}):
                        response = client.get("/status?token=secret")
        self.assertEqual(response.status_code, 200)
