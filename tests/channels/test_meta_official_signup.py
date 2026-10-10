import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from fastapi import HTTPException

from app.api.routers.conexoes import _discover_waba_from_token_targets, _ensure_meta_waba_subscription, _resolve_and_persist_meta_number
from app.services.channels.resolver import context_from_connection
from app.services.channels.types import Channel, Provider, normalize_provider
from app.services.meta.credential_crypto import decrypt_meta_secret, encrypt_meta_secret
from app.services.meta.embedded_signup import detect_coexistence, discover_assets, encrypted_credentials_from_token, extract_signup_identifiers, sanitize_meta_error
from app.services.meta.graph_client import MetaGraphClient, MetaGraphError
from app.services.meta.settings import meta_settings
from app.services.meta.signup_state import (
    BUSINESS_APP_ONBOARDING,
    MARKETING_OAUTH_STATE_PURPOSE,
    MetaSignupStateError,
    STANDARD_EMBEDDED_SIGNUP,
    create_signup_state,
    validate_signup_state,
)
from db.models import TipoConexao


SECRET_ENV = {
    "SECRET_KEY": "test-secret-key-with-more-than-thirty-two-chars",
    "META_CREDENTIAL_ENCRYPTION_KEY": "test-meta-credential-key-with-32chars",
}


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = (value, ex)
        return True


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self.body = body or {}

    def json(self):
        return self.body


class FakeGraphTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, *, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        return self.responses.pop(0)

    async def post(self, url, *, data=None, json=None, headers=None, files=None):
        self.calls.append(("POST", url, data or json, headers))
        return self.responses.pop(0)


class MetaOfficialSignupTests(unittest.IsolatedAsyncioTestCase):
    async def test_waba_target_discovery_requires_one_phone_match(self):
        class FakeClient:
            access_token = "token"

            async def debug_token(self, **_kwargs):
                return {"data": {"granular_scopes": [{"scope": "whatsapp_business_management", "target_ids": ["waba-one", "waba-two"]}]}}

            async def list_waba_phone_numbers(self, waba_id):
                return [{"id": "phone-current"}] if waba_id == "waba-one" else [{"id": "phone-other"}]

        with patch("app.api.routers.conexoes.meta_settings", return_value=SimpleNamespace(app_id="app", app_secret="secret")):
            resolved = await _discover_waba_from_token_targets(client=FakeClient(), phone_number_id="phone-current")
        self.assertEqual(resolved, "waba-one")

    async def test_waba_target_discovery_fails_closed_for_zero_or_multiple_matches(self):
        class FakeClient:
            access_token = "token"
            mode = "zero"

            async def debug_token(self, **_kwargs):
                return {"data": {"granular_scopes": [{"scope": "whatsapp_business_management", "target_ids": ["waba-one", "waba-two"]}]}}

            async def list_waba_phone_numbers(self, waba_id):
                if self.mode == "zero":
                    return []
                return [{"id": "phone-current"}]

        client = FakeClient()
        with patch("app.api.routers.conexoes.meta_settings", return_value=SimpleNamespace(app_id="app", app_secret="secret")):
            self.assertIsNone(await _discover_waba_from_token_targets(client=client, phone_number_id="phone-current"))
            client.mode = "multiple"
            self.assertIsNone(await _discover_waba_from_token_targets(client=client, phone_number_id="phone-current"))

    async def test_new_signup_phone_does_not_reuse_stale_waba(self):
        class FakeDb:
            async def commit(self):
                return None

            async def refresh(self, _connection):
                return None

        class FakeClient:
            def __init__(self):
                self.listed_wabas = []

            async def get_phone_number(self, phone_number_id):
                self.asserted_phone_number_id = phone_number_id
                return {
                    "id": phone_number_id,
                    "display_phone_number": "+55 54 8124-8964",
                    "verified_name": "Rosangela Ferrioli",
                    "quality_rating": "UNKNOWN",
                    "status": "CONNECTED",
                    "platform_type": "CLOUD_API",
                }

            async def list_waba_phone_numbers(self, waba_id):
                self.listed_wabas.append(waba_id)
                raise AssertionError("stale WABA must not be queried")

        connection = SimpleNamespace(
            id="connection-a",
            waba_id="old-waba",
            phone_number_id="old-phone",
            external_account_id="old-phone",
            display_number=None,
            nome_instancia="old",
            metadata_json={"meta_waba_id": None, "onboarding_mode": BUSINESS_APP_ONBOARDING},
            status="retryable_error",
        )
        client = FakeClient()
        result = await _resolve_and_persist_meta_number(
            db=FakeDb(),
            conexao=connection,
            empresa_id="empresa-a",
            usuario_id="user-a",
            client=client,
            identifiers={"meta_waba_id": None, "meta_phone_number_id": "new-phone", "meta_business_portfolio_id": "business"},
            onboarding_mode=BUSINESS_APP_ONBOARDING,
        )

        self.assertEqual(result["status"], "attention")
        self.assertEqual(client.asserted_phone_number_id, "new-phone")
        self.assertEqual(client.listed_wabas, [])
        self.assertEqual(connection.waba_id, "")
        self.assertEqual(connection.phone_number_id, "new-phone")
        self.assertEqual(connection.status, "attention")
        self.assertEqual(connection.metadata_json["phone_status"], "CONNECTED")
        self.assertEqual(connection.metadata_json["current_error"]["code"], "waba_not_resolved")
        self.assertTrue(connection.metadata_json["last_synced_at"])

    async def test_ensure_waba_subscription_distinguishes_missing_waba_from_missing_app(self):
        settings = SimpleNamespace(app_id="app-1")

        conexao_sem_waba = SimpleNamespace(id="c1", waba_id="", metadata_json={})
        with self.assertRaises(HTTPException) as ctx_waba:
            await _ensure_meta_waba_subscription(
                client=None,
                conexao=conexao_sem_waba,
                empresa_id="empresa-a",
                usuario_id="user-a",
                settings=settings,
            )
        self.assertEqual(ctx_waba.exception.status_code, 503)
        self.assertEqual(ctx_waba.exception.detail["code"], "waba_not_resolved")

        conexao_sem_app = SimpleNamespace(id="c2", waba_id="waba-1", metadata_json={})
        with self.assertRaises(HTTPException) as ctx_app:
            await _ensure_meta_waba_subscription(
                client=None,
                conexao=conexao_sem_app,
                empresa_id="empresa-a",
                usuario_id="user-a",
                settings=SimpleNamespace(app_id=""),
            )
        self.assertEqual(ctx_app.exception.status_code, 503)
        self.assertEqual(ctx_app.exception.detail["code"], "subscription_not_configured")

    async def test_state_valid_replay_tamper_expired_and_tenant_mismatch(self):
        redis = FakeRedis()
        with patch.dict(os.environ, SECRET_ENV, clear=False):
            token = create_signup_state(empresa_id="empresa-a", usuario_id="user-a", session_id="sess-a")
            state = await validate_signup_state(token, empresa_id="empresa-a", usuario_id="user-a", redis_client=redis)
            self.assertEqual(state.empresa_id, "empresa-a")

            with self.assertRaisesRegex(MetaSignupStateError, "utilizado"):
                await validate_signup_state(token, empresa_id="empresa-a", usuario_id="user-a", redis_client=redis)

            tampered = token[:-2] + "xx"
            with self.assertRaises(MetaSignupStateError):
                await validate_signup_state(tampered, empresa_id="empresa-a", usuario_id="user-a", redis_client=FakeRedis())

            other = create_signup_state(empresa_id="empresa-a", usuario_id="user-a", ttl_seconds=-1)
            with self.assertRaisesRegex(MetaSignupStateError, "expirado"):
                await validate_signup_state(other, empresa_id="empresa-a", usuario_id="user-a", redis_client=FakeRedis())

            tenant = create_signup_state(empresa_id="empresa-a", usuario_id="user-a")
            with self.assertRaisesRegex(MetaSignupStateError, "outra empresa"):
                await validate_signup_state(tenant, empresa_id="empresa-b", usuario_id="user-a", redis_client=FakeRedis())

    async def test_state_is_bound_to_onboarding_mode_and_user(self):
        with patch.dict(os.environ, SECRET_ENV, clear=False):
            token = create_signup_state(
                empresa_id="empresa-a",
                usuario_id="user-a",
                onboarding_mode=BUSINESS_APP_ONBOARDING,
            )
            state = await validate_signup_state(
                token,
                empresa_id="empresa-a",
                usuario_id="user-a",
                onboarding_mode=BUSINESS_APP_ONBOARDING,
                redis_client=FakeRedis(),
            )
            self.assertEqual(state.onboarding_mode, BUSINESS_APP_ONBOARDING)

            token_mode_mismatch = create_signup_state(
                empresa_id="empresa-a",
                usuario_id="user-a",
                onboarding_mode=BUSINESS_APP_ONBOARDING,
            )
            with self.assertRaisesRegex(MetaSignupStateError, "outro modo"):
                await validate_signup_state(
                    token_mode_mismatch,
                    empresa_id="empresa-a",
                    usuario_id="user-a",
                    onboarding_mode=STANDARD_EMBEDDED_SIGNUP,
                    redis_client=FakeRedis(),
                )

            token_user_mismatch = create_signup_state(empresa_id="empresa-a", usuario_id="user-a")
            with self.assertRaisesRegex(MetaSignupStateError, "outro usuario"):
                await validate_signup_state(
                    token_user_mismatch,
                    empresa_id="empresa-a",
                    usuario_id="user-b",
                    onboarding_mode=STANDARD_EMBEDDED_SIGNUP,
                    redis_client=FakeRedis(),
                )

            with self.assertRaisesRegex(MetaSignupStateError, "modo de onboarding"):
                create_signup_state(empresa_id="empresa-a", usuario_id="user-a", onboarding_mode="coexistence")

    async def test_marketing_oauth_state_has_separate_purpose_from_whatsapp_signup(self):
        with patch.dict(os.environ, SECRET_ENV, clear=False):
            token = create_signup_state(
                empresa_id="empresa-a",
                usuario_id="user-a",
                purpose=MARKETING_OAUTH_STATE_PURPOSE,
            )
            state = await validate_signup_state(
                token,
                empresa_id="empresa-a",
                usuario_id="user-a",
                purpose=MARKETING_OAUTH_STATE_PURPOSE,
                redis_client=FakeRedis(),
            )
            self.assertEqual(state.purpose, MARKETING_OAUTH_STATE_PURPOSE)
            with self.assertRaisesRegex(MetaSignupStateError, "finalidade"):
                await validate_signup_state(
                    token,
                    empresa_id="empresa-a",
                    usuario_id="user-a",
                    redis_client=FakeRedis(),
                )

    async def test_credentials_are_encrypted_and_not_plaintext(self):
        with patch.dict(os.environ, SECRET_ENV, clear=False):
            encrypted = encrypt_meta_secret("EAAT-secret-token")
            self.assertNotIn("EAAT-secret-token", encrypted)
            self.assertEqual(decrypt_meta_secret(encrypted), "EAAT-secret-token")
            stored = encrypted_credentials_from_token("EAAT-secret-token")
            self.assertIn("encrypted_access_token", stored)
            self.assertNotIn("access_token", stored)
            self.assertEqual(stored["token_last4"], "***oken")

    async def test_graph_client_discovers_assets_with_mock_transport_and_sends_no_real_request(self):
        transport = FakeGraphTransport(
            [
                FakeResponse(body={"data": [{"id": "biz-1", "name": "Business"}]}),
                FakeResponse(body={"data": [{"id": "waba-1", "name": "WABA"}]}),
                FakeResponse(body={"data": [{"id": "phone-1", "display_phone_number": "+55 11 99999-0000", "verified_name": "Empresa"}]}),
            ]
        )
        client = MetaGraphClient(access_token="secret-token", graph_api_version="v25.0", transport=transport)
        assets = await discover_assets(client)
        self.assertEqual(assets["businesses"][0]["wabas"][0]["phone_numbers"][0]["id"], "phone-1")
        self.assertEqual(len(transport.calls), 3)
        self.assertTrue(all(call[1].startswith("https://graph.facebook.com/v25.0/") for call in transport.calls))

    async def test_graph_pagination_counts_pages_and_follows_cursor(self):
        transport = FakeGraphTransport([
            FakeResponse(body={"data": [{"id": "one"}], "paging": {"cursors": {"after": "cursor-2"}}}),
            FakeResponse(body={"data": [{"id": "two"}]}),
        ])
        client = MetaGraphClient(access_token="tenant-token", transport=transport)
        pages = [0]
        result = await client._list_paginated("act_123/campaigns", fields="id,name", page_counter=pages)
        self.assertEqual([row["id"] for row in result], ["one", "two"])
        self.assertEqual(pages, [2])
        self.assertEqual(transport.calls[1][2]["after"], "cursor-2")

    async def test_graph_get_retries_rate_limit_once_without_posting(self):
        transport = FakeGraphTransport([
            FakeResponse(status_code=429, body={"error": {"code": 4, "message": "Rate limit"}}),
            FakeResponse(body={"data": []}),
        ])
        client = MetaGraphClient(access_token="tenant-token", transport=transport, max_attempts=2)
        sleeper = AsyncMock()
        with patch("app.services.meta.graph_client.asyncio.sleep", new=sleeper):
            body = await client._request("GET", "me/adaccounts")
        self.assertEqual(body, {"data": []})
        self.assertEqual([call[0] for call in transport.calls], ["GET", "GET"])
        sleeper.assert_awaited_once_with(1.0)

    async def test_graph_get_retries_5xx_with_bounded_backoff(self):
        transport = FakeGraphTransport([
            FakeResponse(status_code=503, body={"error": {"code": 2, "message": "Service unavailable"}}),
            FakeResponse(body={"data": []}),
        ])
        client = MetaGraphClient(access_token="tenant-token", transport=transport, max_attempts=2)
        sleeper = AsyncMock()
        with patch("app.services.meta.graph_client.asyncio.sleep", new=sleeper):
            body = await client._request("GET", "act_123/insights")
        self.assertEqual(body, {"data": []})
        sleeper.assert_awaited_once_with(1.0)

    async def test_graph_get_retries_timeout_with_bounded_backoff(self):
        class TimeoutThenSuccess:
            def __init__(self):
                self.calls = 0

            async def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise httpx.TimeoutException("timeout")
                return FakeResponse(body={"data": []})

            async def post(self, *_args, **_kwargs):
                raise AssertionError("GET retry must not become a write")

        transport = TimeoutThenSuccess()
        client = MetaGraphClient(access_token="tenant-token", transport=transport, max_attempts=2)
        sleeper = AsyncMock()
        with patch("app.services.meta.graph_client.asyncio.sleep", new=sleeper):
            body = await client._request("GET", "act_123/insights")
        self.assertEqual(body, {"data": []})
        self.assertEqual(transport.calls, 2)
        sleeper.assert_awaited_once_with(0.5)

    async def test_graph_client_lists_and_subscribes_waba_apps_with_mock_transport(self):
        transport = FakeGraphTransport(
            [
                FakeResponse(body={"data": [{"whatsapp_business_api_data": {"link": "configured"}}]}),
                FakeResponse(body={"success": True}),
            ]
        )
        client = MetaGraphClient(access_token="secret-token", graph_api_version="v25.0", transport=transport)

        apps = await client.list_waba_subscribed_apps("waba-1")
        response = await client.subscribe_waba("waba-1")

        self.assertEqual(len(apps), 1)
        self.assertTrue(response["success"])
        self.assertEqual(transport.calls[0][0], "GET")
        self.assertTrue(transport.calls[0][1].endswith("/v25.0/waba-1/subscribed_apps"))
        self.assertEqual(transport.calls[1][0], "POST")

    async def test_ensure_waba_subscription_subscribes_and_confirms_app(self):
        transport = FakeGraphTransport(
            [
                FakeResponse(body={"data": []}),
                FakeResponse(body={"success": True}),
                FakeResponse(body={"data": [{"whatsapp_business_api_data": {"id": "app-1"}}]}),
            ]
        )
        client = MetaGraphClient(access_token="secret-token", graph_api_version="v25.0", transport=transport)

        result = await client.ensure_waba_subscription("waba-1", "app-1")

        self.assertTrue(result["subscribed"])
        self.assertTrue(result["changed"])
        self.assertEqual([call[0] for call in transport.calls], ["GET", "POST", "GET"])
        self.assertTrue(transport.calls[1][1].endswith("/v25.0/waba-1/subscribed_apps"))

    async def test_ensure_waba_subscription_is_idempotent_when_app_exists(self):
        transport = FakeGraphTransport(
            [FakeResponse(body={"data": [{"id": "app-1"}]})]
        )
        client = MetaGraphClient(access_token="secret-token", transport=transport)

        result = await client.ensure_waba_subscription("waba-1", "app-1")

        self.assertTrue(result["subscribed"])
        self.assertFalse(result["changed"])
        self.assertEqual([call[0] for call in transport.calls], ["GET"])

    async def test_graph_error_classification_sanitizes_token(self):
        transport = FakeGraphTransport(
            [
                FakeResponse(
                    status_code=401,
                    body={"error": {"code": 190, "message": "Token EAAT-secret-token expired"}},
                )
            ]
        )
        client = MetaGraphClient(access_token="EAAT-secret-token", transport=transport, max_attempts=1)
        with self.assertRaises(MetaGraphError) as context:
            await client.get_phone_number("phone-1")
        self.assertEqual(context.exception.code, "token_expired")
        self.assertNotIn("EAAT-secret-token", str(context.exception))

    async def test_unsupported_get_request_is_classified_with_sanitized_graph_metadata(self):
        transport = FakeGraphTransport(
            [
                FakeResponse(
                    status_code=400,
                    body={
                        "error": {
                            "code": 100,
                            "error_subcode": 33,
                            "message": "Unsupported get request. Please read the Graph API documentation at https://developers.facebook.com/docs/graph-api",
                            "fbtrace_id": "TRACE123",
                        }
                    },
                )
            ]
        )
        client = MetaGraphClient(access_token="EAAT-secret-token", graph_api_version="v25.0", transport=transport, max_attempts=1)
        with self.assertRaises(MetaGraphError) as context:
            await client.get_phone_number("123456789")

        error = sanitize_meta_error(context.exception)
        self.assertEqual(error["code"], "invalid_object_id")
        self.assertEqual(error["meta_error_code"], "100")
        self.assertEqual(error["error_subcode"], "33")
        self.assertEqual(error["fbtrace_id"], "TRACE123")
        self.assertEqual(error["graph_path"], "123456789")
        self.assertNotIn("EAAT-secret-token", str(error))

    async def test_embedded_signup_identifiers_are_typed_and_business_is_not_waba(self):
        payload = {
            "type": "WA_EMBEDDED_SIGNUP",
            "event": "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
            "data": {
                "business_id": "111111111111111",
                "waba_id": "222222222222222",
                "phone_number_id": "333333333333333",
            },
        }
        identifiers = extract_signup_identifiers(payload)
        self.assertEqual(identifiers["meta_business_portfolio_id"], "111111111111111")
        self.assertEqual(identifiers["meta_waba_id"], "222222222222222")
        self.assertEqual(identifiers["meta_phone_number_id"], "333333333333333")
        self.assertNotEqual(identifiers["meta_business_portfolio_id"], identifiers["meta_waba_id"])
        self.assertNotEqual(identifiers["meta_phone_number_id"], identifiers["meta_waba_id"])

    async def test_coexistence_not_assumed_and_provider_alias_preserves_evolution(self):
        self.assertEqual(detect_coexistence({})["state"], "nao_verificado")
        self.assertEqual(normalize_provider("meta_official"), Provider.META_WHATSAPP)
        evolution = SimpleNamespace(
            id="conn-evo",
            empresa_id="empresa-a",
            tipo=TipoConexao.EVOLUTION,
            provider="evolution_legacy",
            channel="whatsapp",
            status="ativo",
            credenciais={"evolution_instance": "wa_empresa"},
            nome_instancia="wa_empresa",
        )
        meta = SimpleNamespace(
            id="conn-meta",
            empresa_id="empresa-a",
            tipo=TipoConexao.META,
            provider="meta_official",
            channel="whatsapp",
            status="connected",
            credenciais={},
            phone_number_id="phone-1",
            nome_instancia="Meta",
        )
        self.assertEqual(context_from_connection(evolution).provider, Provider.EVOLUTION_LEGACY)
        self.assertEqual(context_from_connection(meta).provider, Provider.META_WHATSAPP)
        self.assertEqual(context_from_connection(meta).external_account_id, "phone-1")

    async def test_settings_default_live_actions_false(self):
        with patch.dict(os.environ, {"META_APP_ID": "app", "META_EMBEDDED_SIGNUP_CONFIG_ID": "cfg"}, clear=False):
            os.environ.pop("META_LIVE_ACTIONS_ENABLED", None)
            settings = meta_settings()
            self.assertEqual(settings.graph_api_version, "v25.0")
            self.assertFalse(settings.live_actions_enabled)
            self.assertTrue(settings.public_configured)


if __name__ == "__main__":
    unittest.main()
