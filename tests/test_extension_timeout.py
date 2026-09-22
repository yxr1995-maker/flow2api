import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, PropertyMock, patch

_repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from src.core.config import Config
from src.services.browser_captcha_extension import (
    SAFE_EXTENSION_ERROR_STAGES,
    ExtensionCaptchaError,
    ExtensionCaptchaService,
)
from src.services.flow_client import FlowClient


class FakeWebSocket:
    def __init__(self, route_key=""):
        self.query_params = {"route_key": route_key}
        self.sent = []
        self._accepted = False

    async def accept(self):
        self._accepted = True

    async def send_text(self, data):
        self.sent.append(data)


def _fresh_service():
    ExtensionCaptchaService._instance = None
    svc = ExtensionCaptchaService.__new__(ExtensionCaptchaService)
    svc.db = None
    svc.active_connections = []
    svc.pending_requests = {}
    return svc


def _run(coro):
    return asyncio.run(coro)


class TestExtensionCaptcha(unittest.TestCase):

    def test_service_image_zero_timeout_raises_timeout_stage(self):
        svc = _fresh_service()
        ws = FakeWebSocket()
        _run(svc.connect(ws))
        async def go():
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await svc.get_token("p", "IMAGE_GENERATION", timeout=0)
            self.assertEqual(ctx.exception.stage, "timeout")
            self.assertNotIn("p", str(ctx.exception))
        _run(go())

    def test_service_video_zero_timeout_raises_timeout_stage(self):
        svc = _fresh_service()
        ws = FakeWebSocket()
        _run(svc.connect(ws))
        async def go():
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await svc.get_token("p", "VIDEO_GENERATION", timeout=0)
            self.assertEqual(ctx.exception.stage, "timeout")
        _run(go())

    def test_success_returns_token(self):
        svc = _fresh_service()
        ws = FakeWebSocket()
        _run(svc.connect(ws))
        async def go():
            task = asyncio.create_task(svc.get_token("p", "IMAGE_GENERATION", timeout=2))
            await asyncio.sleep(0.05)
            req_id = json.loads(ws.sent[-1])["req_id"]
            fut, _ = svc.pending_requests[req_id]
            fut.set_result({"status": "success", "token": "tok_abc"})
            self.assertEqual(await task, "tok_abc")
            self.assertNotIn(req_id, svc.pending_requests)
        _run(go())

    def test_script_timeout_stage(self):
        svc = _fresh_service()
        ws = FakeWebSocket()
        _run(svc.connect(ws))
        async def go():
            task = asyncio.create_task(svc.get_token("p", "IMAGE_GENERATION", timeout=2))
            await asyncio.sleep(0.05)
            req_id = json.loads(ws.sent[-1])["req_id"]
            fut, _ = svc.pending_requests[req_id]
            fut.set_result({"status": "error", "error": "Extension script failed: Timeout generating reCAPTCHA locally"})
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await task
            self.assertEqual(ctx.exception.stage, "extension_script_timeout")
            self.assertNotIn("reCAPTCHA", str(ctx.exception))
            self.assertNotIn(req_id, svc.pending_requests)
        _run(go())

    def test_load_failed_stage(self):
        svc = _fresh_service()
        ws = FakeWebSocket()
        _run(svc.connect(ws))
        async def go():
            task = asyncio.create_task(svc.get_token("p", "IMAGE_GENERATION", timeout=2))
            await asyncio.sleep(0.05)
            req_id = json.loads(ws.sent[-1])["req_id"]
            fut, _ = svc.pending_requests[req_id]
            fut.set_result({"status": "error", "error": "Failed to load enterprise.js via network"})
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await task
            self.assertEqual(ctx.exception.stage, "extension_script_load_failed")
        _run(go())

    def test_generic_script_failed_stage(self):
        svc = _fresh_service()
        ws = FakeWebSocket()
        _run(svc.connect(ws))
        async def go():
            task = asyncio.create_task(svc.get_token("p", "IMAGE_GENERATION", timeout=2))
            await asyncio.sleep(0.05)
            req_id = json.loads(ws.sent[-1])["req_id"]
            fut, _ = svc.pending_requests[req_id]
            fut.set_result({"status": "error", "error": "Something unexpected"})
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await task
            self.assertEqual(ctx.exception.stage, "extension_script_failed")
            self.assertNotIn("unexpected", str(ctx.exception))
        _run(go())

    def test_whitelist_error_stages_and_secret_redaction(self):
        stages = [
            "extension_script_timeout",
            "extension_script_load_failed",
            "extension_sitekey_invalid",
            "extension_permission_denied",
            "extension_script_failed",
            "extension_empty_result",
        ]
        self.assertEqual(set(stages), SAFE_EXTENSION_ERROR_STAGES)
        for stage in stages:
            with self.subTest(stage=stage):
                svc = _fresh_service()
                ws = FakeWebSocket()
                _run(svc.connect(ws))

                async def go(expected_stage=stage):
                    task = asyncio.create_task(svc.get_token("p", "IMAGE_GENERATION", timeout=2))
                    await asyncio.sleep(0.02)
                    req_id = json.loads(ws.sent[-1])["req_id"]
                    fut, _ = svc.pending_requests[req_id]
                    fut.set_result({"status": "error", "error": expected_stage})
                    with self.assertRaises(ExtensionCaptchaError) as ctx:
                        await task
                    self.assertEqual(ctx.exception.stage, expected_stage)
                    self.assertEqual(str(ctx.exception), expected_stage)
                    self.assertNotIn(req_id, svc.pending_requests)

                _run(go())

        secret_errors = [
            "Unexpected error with secret_apikey_12345",
            "Failed inside eval: token=secret_token_9999",
            "some completely arbitrary third-party error text",
        ]
        for raw_err in secret_errors:
            with self.subTest(raw_error=raw_err):
                svc = _fresh_service()
                ws = FakeWebSocket()
                _run(svc.connect(ws))

                async def go_secret(err_text=raw_err):
                    task = asyncio.create_task(svc.get_token("p", "IMAGE_GENERATION", timeout=2))
                    await asyncio.sleep(0.02)
                    req_id = json.loads(ws.sent[-1])["req_id"]
                    fut, _ = svc.pending_requests[req_id]
                    fut.set_result({"status": "error", "error": err_text})
                    with self.assertRaises(ExtensionCaptchaError) as ctx:
                        await task
                    self.assertEqual(ctx.exception.stage, "extension_script_failed")
                    self.assertEqual(str(ctx.exception), "extension_script_failed")
                    self.assertNotIn("secret", str(ctx.exception).lower())
                    self.assertNotIn(err_text, str(ctx.exception))

                _run(go_secret())

    def test_not_connected(self):
        svc = _fresh_service()
        async def go():
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await svc.get_token("p", "IMAGE_GENERATION")
            self.assertEqual(ctx.exception.stage, "not_connected")
        _run(go())

    def test_route_unavailable(self):
        svc = _fresh_service()
        ws = FakeWebSocket(route_key="other")
        _run(svc.connect(ws))
        async def go():
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await svc.get_token("p", "IMAGE_GENERATION", token_id=None)
            self.assertEqual(ctx.exception.stage, "route_unavailable")
            self.assertNotIn("other", str(ctx.exception))
        _run(go())

    def test_pending_cleaned_after_timeout(self):
        svc = _fresh_service()
        ws = FakeWebSocket()
        _run(svc.connect(ws))
        async def go():
            with self.assertRaises(ExtensionCaptchaError):
                await svc.get_token("p", "IMAGE_GENERATION", timeout=0)
            self.assertEqual(len(svc.pending_requests), 0)
        _run(go())

    def test_no_secrets_in_message(self):
        svc = _fresh_service()
        ws = FakeWebSocket(route_key="secret_9223")
        _run(svc.connect(ws))
        async def go():
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await svc.get_token("proj_SECRET", "IMAGE_GENERATION", timeout=0)
            msg = str(ctx.exception)
            self.assertNotIn("secret_9223", msg)
            self.assertNotIn("proj_SECRET", msg)
            self.assertNotIn("token_id", msg)
        _run(go())

    def test_comm_error_stage(self):
        svc = _fresh_service()
        ws = FakeWebSocket()
        _run(svc.connect(ws))
        async def go():
            task = asyncio.create_task(svc.get_token("p", "IMAGE_GENERATION", timeout=2))
            await asyncio.sleep(0.05)
            req_id = json.loads(ws.sent[-1])["req_id"]
            fut, _ = svc.pending_requests[req_id]
            fut.set_exception(ConnectionResetError("gone"))
            with self.assertRaises(ExtensionCaptchaError) as ctx:
                await task
            self.assertEqual(ctx.exception.stage, "comm_error")
            self.assertNotIn("gone", str(ctx.exception))
            self.assertNotIn(req_id, svc.pending_requests)
        _run(go())

    def test_is_runtime_error(self):
        err = ExtensionCaptchaError("test", stage="x")
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(err.stage, "x")

    def test_stage_in_str(self):
        err = ExtensionCaptchaError("extension_script_timeout", stage="extension_script_timeout")
        self.assertIn("extension_script_timeout", str(err))

    def test_flow_client_image_timeout_uses_45(self):
        client = FlowClient(proxy_manager=None, db=None)
        with patch.object(Config, "captcha_method", new_callable=PropertyMock, return_value="extension"):
            mock_service = AsyncMock()
            mock_service.get_token = AsyncMock(return_value="tok_img")
            with patch.object(ExtensionCaptchaService, "get_instance", AsyncMock(return_value=mock_service)):
                token, browser_id = _run(client._get_recaptcha_token("test_proj", "IMAGE_GENERATION"))
                self.assertEqual(token, "tok_img")
                self.assertIsNone(browser_id)
                mock_service.get_token.assert_awaited_once_with(
                    "test_proj", "IMAGE_GENERATION", timeout=45, token_id=None
                )

    def test_flow_client_video_timeout_uses_60(self):
        client = FlowClient(proxy_manager=None, db=None)
        with patch.object(Config, "captcha_method", new_callable=PropertyMock, return_value="extension"):
            mock_service = AsyncMock()
            mock_service.get_token = AsyncMock(return_value="tok_vid")
            with patch.object(ExtensionCaptchaService, "get_instance", AsyncMock(return_value=mock_service)):
                token, browser_id = _run(client._get_recaptcha_token("test_proj", "VIDEO_GENERATION", token_id=123))
                self.assertEqual(token, "tok_vid")
                self.assertIsNone(browser_id)
                mock_service.get_token.assert_awaited_once_with(
                    "test_proj", "VIDEO_GENERATION", timeout=60, token_id=123
                )

    def test_flow_client_raises_extension_captcha_error(self):
        client = FlowClient(proxy_manager=None, db=None)
        with patch.object(Config, "captcha_method", new_callable=PropertyMock, return_value="extension"):
            mock_service = AsyncMock()
            mock_service.get_token = AsyncMock(
                side_effect=ExtensionCaptchaError("extension_sitekey_invalid", stage="extension_sitekey_invalid")
            )
            with patch.object(ExtensionCaptchaService, "get_instance", AsyncMock(return_value=mock_service)):
                with self.assertRaises(ExtensionCaptchaError) as ctx:
                    _run(client._get_recaptcha_token("test_proj", "IMAGE_GENERATION"))
                self.assertEqual(ctx.exception.stage, "extension_sitekey_invalid")


if __name__ == "__main__":
    unittest.main()
