import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

import src.api.admin as admin
from src.api.admin import ExtensionCaptchaProbeRequest, probe_extension_captcha
from src.services.browser_captcha_extension import ExtensionCaptchaError, ExtensionCaptchaService


class ExtensionProbeApiTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.orig_db = admin.db
        admin.db = SimpleNamespace(
            get_captcha_config=AsyncMock(return_value=SimpleNamespace(captcha_method="extension")),
            get_token=AsyncMock(return_value=SimpleNamespace(id=1, current_project_id="proj_uuid_123")),
            get_projects_by_token=AsyncMock(return_value=[]),
        )

    async def asyncTearDown(self):
        admin.db = self.orig_db

    async def test_probe_success_discards_token_and_returns_safe_structure(self):
        mock_service = AsyncMock()
        mock_service.get_token = AsyncMock(return_value="super_secret_captcha_token_xyz")

        with patch.object(ExtensionCaptchaService, "get_instance", AsyncMock(return_value=mock_service)):
            req = ExtensionCaptchaProbeRequest(token_id=1)
            resp = await probe_extension_captcha(req, _token="valid_admin_token")

            self.assertTrue(resp["success"])
            self.assertEqual(resp["stage"], "success")
            self.assertIsInstance(resp["elapsed_ms"], int)
            self.assertEqual(set(resp.keys()), {"success", "stage", "elapsed_ms"})
            self.assertNotIn("super_secret_captcha_token_xyz", str(resp))

            mock_service.get_token.assert_awaited_once_with(
                project_id="proj_uuid_123",
                action="IMAGE_GENERATION",
                timeout=90,
                token_id=1,
            )

    async def test_probe_empty_token_returns_extension_empty_result(self):
        for empty_val in (None, "", "   "):
            with self.subTest(empty_val=empty_val):
                mock_service = AsyncMock()
                mock_service.get_token = AsyncMock(return_value=empty_val)

                with patch.object(ExtensionCaptchaService, "get_instance", AsyncMock(return_value=mock_service)):
                    req = ExtensionCaptchaProbeRequest(token_id=1)
                    resp = await probe_extension_captcha(req, _token="valid_admin_token")

                    self.assertFalse(resp["success"])
                    self.assertEqual(resp["stage"], "extension_empty_result")
                    self.assertIsInstance(resp["elapsed_ms"], int)
                    self.assertEqual(set(resp.keys()), {"success", "stage", "elapsed_ms"})

    async def test_token_id_validation_rejects_non_positive(self):
        with self.assertRaises(ValidationError):
            ExtensionCaptchaProbeRequest(token_id=0)
        with self.assertRaises(ValidationError):
            ExtensionCaptchaProbeRequest(token_id=-1)

    async def test_probe_token_not_found_raises_404(self):
        admin.db.get_token = AsyncMock(return_value=None)
        req = ExtensionCaptchaProbeRequest(token_id=999)
        with self.assertRaises(HTTPException) as ctx:
            await probe_extension_captcha(req, _token="valid_admin_token")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("Token 999 not found", ctx.exception.detail)

    async def test_probe_failure_maps_to_safe_stage_without_leak(self):
        mock_service = AsyncMock()
        mock_service.get_token = AsyncMock(
            side_effect=ExtensionCaptchaError("Secret fail details", stage="extension_script_timeout")
        )

        with patch.object(ExtensionCaptchaService, "get_instance", AsyncMock(return_value=mock_service)):
            req = ExtensionCaptchaProbeRequest(token_id=1, project_id="custom_proj_456")
            resp = await probe_extension_captcha(req, _token="valid_admin_token")

            self.assertFalse(resp["success"])
            self.assertEqual(resp["stage"], "extension_script_timeout")
            self.assertEqual(set(resp.keys()), {"success", "stage", "elapsed_ms"})
            self.assertNotIn("Secret fail details", str(resp))

    async def test_probe_not_connected_stage(self):
        mock_service = AsyncMock()
        mock_service.get_token = AsyncMock(
            side_effect=ExtensionCaptchaError("No worker", stage="not_connected")
        )

        with patch.object(ExtensionCaptchaService, "get_instance", AsyncMock(return_value=mock_service)):
            resp = await probe_extension_captcha(None, _token="valid_admin_token")
            self.assertFalse(resp["success"])
            self.assertEqual(resp["stage"], "not_connected")

    async def test_probe_rejects_non_extension_method(self):
        admin.db.get_captcha_config = AsyncMock(return_value=SimpleNamespace(captcha_method="personal"))
        with self.assertRaises(HTTPException) as ctx:
            await probe_extension_captcha(None, _token="valid_admin_token")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Only extension captcha method supported", ctx.exception.detail)

    async def test_probe_missing_project_id_raises_400(self):
        admin.db.get_token = AsyncMock(return_value=SimpleNamespace(id=2, current_project_id=None))
        admin.db.get_projects_by_token = AsyncMock(return_value=[])
        req = ExtensionCaptchaProbeRequest(token_id=2, project_id=None)
        with self.assertRaises(HTTPException) as ctx:
            await probe_extension_captcha(req, _token="valid_admin_token")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("No project_id found", ctx.exception.detail)

    def test_mounted_route_auth_with_test_client(self):
        app = FastAPI()
        app.include_router(admin.router)
        client = TestClient(app)

        # 1. No credentials -> 401
        res_no_auth = client.post("/api/captcha/extension-probe", json={"token_id": 1})
        self.assertEqual(res_no_auth.status_code, 401)
        self.assertEqual(res_no_auth.json().get("detail"), "Missing authorization")

        # 2. Invalid credentials -> 401
        res_bad_auth = client.post(
            "/api/captcha/extension-probe",
            headers={"Authorization": "Bearer fake_invalid_token"},
            json={"token_id": 1}
        )
        self.assertEqual(res_bad_auth.status_code, 401)
        self.assertEqual(res_bad_auth.json().get("detail"), "Invalid or expired admin token")

        # 3. Validation error: token_id < 1 -> 422
        token = "test_active_admin_session_token"
        admin.active_admin_tokens.add(token)
        try:
            res_invalid_body = client.post(
                "/api/captcha/extension-probe",
                headers={"Authorization": f"Bearer {token}"},
                json={"token_id": 0}
            )
            self.assertEqual(res_invalid_body.status_code, 422)
        finally:
            admin.active_admin_tokens.discard(token)


if __name__ == "__main__":
    unittest.main()
