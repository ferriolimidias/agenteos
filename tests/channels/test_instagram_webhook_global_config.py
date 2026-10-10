import hashlib
import hmac
import io
import json
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from app.api.routers.instagram_simple_webhook import receive_webhook, verify_webhook
from app.services.meta.instagram_api_settings import (
    INSTAGRAM_GRAPH_VERSION,
    InstagramApiSettings,
    load_instagram_api_settings,
)


class _SettingsResult:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _SettingsSession:
    def __init__(self, row):
        self.row = row

    async def execute(self, _statement):
        return _SettingsResult(self.row)


def _settings(*, app_secret=None, verify_token=None):
    return InstagramApiSettings(
        app_secret=app_secret,
        verify_token=verify_token,
        graph_api_version=INSTAGRAM_GRAPH_VERSION,
        updated_at=None,
    )


def _request(method="GET", body=b"", query_string=b""):
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": "/api/webhooks/meta/instagram",
            "raw_path": b"/api/webhooks/meta/instagram",
            "query_string": query_string,
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
        },
        receive,
    )


class InstagramGlobalSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_environment_values_are_authoritative_over_legacy_database_fields(self):
        row = SimpleNamespace(
            instagram_app_secret_encrypted="db-secret-encrypted",
            instagram_verify_token_encrypted="db-verify-encrypted",
            instagram_graph_api_version="v26.0",
            instagram_config_updated_at=None,
        )
        with patch.dict(os.environ, {
            "META_APP_SECRET": "env-secret",
            "META_WEBHOOK_VERIFY_TOKEN": "env-verify",
        }):
            settings = await load_instagram_api_settings(_SettingsSession(row))

        self.assertEqual(settings.app_secret, "env-secret")
        self.assertEqual(settings.verify_token, "env-verify")

    async def test_environment_whitespace_is_cleaned(self):
        row = SimpleNamespace(
            instagram_app_secret_encrypted="   ",
            instagram_verify_token_encrypted=None,
            instagram_graph_api_version=None,
            instagram_config_updated_at=None,
        )
        with patch.dict(os.environ, {
            "META_APP_SECRET": " env-secret ",
            "META_WEBHOOK_VERIFY_TOKEN": " env-verify ",
        }, clear=False):
            settings = await load_instagram_api_settings(_SettingsSession(row))

        self.assertEqual(settings.app_secret, "env-secret")
        self.assertEqual(settings.verify_token, "env-verify")
        self.assertEqual(settings.graph_api_version, INSTAGRAM_GRAPH_VERSION)

    async def test_null_strings_do_not_block_environment_fallback(self):
        row = SimpleNamespace(
            instagram_app_secret_encrypted="null",
            instagram_verify_token_encrypted="None",
            instagram_graph_api_version="v26.0",
            instagram_config_updated_at=None,
        )
        with patch.dict(os.environ, {
            "META_APP_SECRET": "env-secret",
            "META_WEBHOOK_VERIFY_TOKEN": "env-verify",
        }, clear=False):
            settings = await load_instagram_api_settings(_SettingsSession(row))

        self.assertEqual(settings.app_secret, "env-secret")
        self.assertEqual(settings.verify_token, "env-verify")

    async def test_missing_database_row_uses_environment_without_writeback(self):
        session = _SettingsSession(None)
        with patch.dict(os.environ, {
            "META_APP_SECRET": "env-secret",
            "META_WEBHOOK_VERIFY_TOKEN": "env-verify",
        }, clear=False):
            settings = await load_instagram_api_settings(session)

        self.assertEqual(settings.app_secret, "env-secret")
        self.assertEqual(settings.verify_token, "env-verify")
        self.assertFalse(hasattr(session, "add"))

    async def test_missing_environment_values_fail_closed(self):
        with patch.dict(os.environ, {"META_APP_SECRET": "", "META_WEBHOOK_VERIFY_TOKEN": ""}, clear=False):
            settings = await load_instagram_api_settings(_SettingsSession(SimpleNamespace(
                instagram_app_secret_encrypted="legacy",
                instagram_verify_token_encrypted="legacy",
            )))
        self.assertIsNone(settings.app_secret)
        self.assertIsNone(settings.verify_token)


class InstagramWebhookAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_uses_verify_token_without_app_id_or_app_secret(self):
        request = _request(query_string=b"hub.verify_token=redacted")
        with patch(
            "app.api.routers.instagram_simple_webhook.load_instagram_api_settings",
            new=AsyncMock(return_value=_settings(verify_token="verify-existing")),
        ):
            response = await verify_webhook(
                request,
                mode="subscribe",
                verify_token="verify-existing",
                challenge="challenge-safe",
                db=object(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"challenge-safe")
        self.assertEqual(request.scope["query_string"], b"")

    async def test_missing_verify_token_blocks_only_get(self):
        with patch(
            "app.api.routers.instagram_simple_webhook.load_instagram_api_settings",
            new=AsyncMock(return_value=_settings(app_secret="app-secret")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await verify_webhook(
                    _request(), mode="subscribe", verify_token="anything",
                    challenge="challenge", db=object(),
                )
        self.assertEqual(raised.exception.status_code, 503)

    async def test_incorrect_verify_token_is_rejected_without_logging_credentials(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with patch(
                "app.api.routers.instagram_simple_webhook.load_instagram_api_settings",
                new=AsyncMock(return_value=_settings(verify_token="configured-secret-value")),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await verify_webhook(
                        _request(query_string=b"hub.verify_token=received-secret-value"),
                        mode="subscribe",
                        verify_token="received-secret-value",
                        challenge="challenge",
                        db=object(),
                    )
        finally:
            root_logger.removeHandler(handler)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertNotIn("configured-secret-value", stream.getvalue())
        self.assertNotIn("received-secret-value", stream.getvalue())

    async def test_post_uses_app_secret_without_app_id_or_verify_token(self):
        raw = json.dumps({"object": "instagram", "entry": []}, separators=(",", ":")).encode()
        secret = "app-secret"
        signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        request = _request(method="POST", body=raw)
        request.scope["headers"] = [(b"x-hub-signature-256", signature.encode())]
        with patch(
            "app.api.routers.instagram_simple_webhook.load_instagram_api_settings",
            new=AsyncMock(return_value=_settings(app_secret=secret)),
        ):
            result = await receive_webhook(request, db=object())

        self.assertEqual(result, {"received": True, "events": []})

    async def test_missing_app_secret_blocks_only_post(self):
        raw = b'{"object":"instagram","entry":[]}'
        request = _request(method="POST", body=raw)
        with patch(
            "app.api.routers.instagram_simple_webhook.load_instagram_api_settings",
            new=AsyncMock(return_value=_settings(verify_token="verify-token")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await receive_webhook(request, db=object())
        self.assertEqual(raised.exception.status_code, 503)

    async def test_invalid_hmac_is_rejected(self):
        raw = b'{"object":"instagram","entry":[]}'
        request = _request(method="POST", body=raw)
        request.scope["headers"] = [(b"x-hub-signature-256", b"sha256=invalid")]
        with patch(
            "app.api.routers.instagram_simple_webhook.load_instagram_api_settings",
            new=AsyncMock(return_value=_settings(app_secret="app-secret")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await receive_webhook(request, db=object())
        self.assertEqual(raised.exception.status_code, 401)

    def test_global_configuration_cannot_read_or_write_instagram_secrets(self):
        router = open("app/api/routers/configuracoes.py", encoding="utf-8").read()
        settings = open("app/services/meta/instagram_api_settings.py", encoding="utf-8").read()
        self.assertNotIn("instagram_app_secret", router)
        self.assertNotIn("instagram_verify_token", router)
        self.assertNotIn("ConfiguracoesGlobais", settings)
        self.assertIn('os.getenv("META_APP_SECRET")', settings)
        self.assertIn('os.getenv("META_WEBHOOK_VERIFY_TOKEN")', settings)


if __name__ == "__main__":
    unittest.main()
