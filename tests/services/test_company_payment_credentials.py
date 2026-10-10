import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routers.auth import require_super_admin
from app.api.routers.company_payments import (
    test_mercado_pago_connection as run_connection_test,
)
from app.api.routers.mercado_pago_leoft import receive_company_mercado_pago_webhook
from app.services.leoft_checkout_service import create_or_get_checkout
from app.services.mercado_pago.leoft_webhook import validate_signature
from app.services.meta.credential_crypto import decrypt_meta_secret, encrypt_meta_secret
from app.services.payment_credentials import (
    ResolvedPaymentCredentials,
    resolve_mercado_pago_credentials,
    save_mercado_pago_credentials,
    serialize_mercado_pago_configuration,
    tenant_webhook_url,
)

KEY = "company-payment-provider-test-key-with-32-characters"


class FakeDb:
    def __init__(self, record=None, empresa=None):
        self.record = record
        self.empresa = empresa or SimpleNamespace(id=uuid4())
        self.added = []

    async def scalar(self, _query):
        return self.record

    async def get(self, _model, _key):
        return self.empresa

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None

    async def refresh(self, _value):
        return None


class FakeWebhookRequest:
    def __init__(self, body: bytes, *, signature: str, request_id: str = "req-1"):
        self._body = body
        self.headers = {"x-signature": signature, "x-request-id": request_id}

    async def body(self):
        return self._body


def _signed_webhook_request(payload: dict, secret: str = "db-secret", request_id: str = "req-1") -> FakeWebhookRequest:
    body = json.dumps(payload, separators=(",", ":")).encode()
    payment_id = str(((payload.get("data") or {}).get("id")) or "").strip()
    ts = "1700000000"
    manifest = f"id:{payment_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return FakeWebhookRequest(body, signature=f"ts={ts},v1={digest}", request_id=request_id)


def _record(*, token="db-token", secret="db-secret", enabled=True, environment="test"):
    with patch.dict("os.environ", {"META_CREDENTIAL_ENCRYPTION_KEY": KEY}):
        return SimpleNamespace(
            empresa_id=uuid4(),
            provider="mercado_pago",
            enabled=enabled,
            environment=environment,
            access_token_encrypted=encrypt_meta_secret(token),
            webhook_secret_encrypted=encrypt_meta_secret(secret),
        )


def test_a_checkout_without_database_configuration_uses_legacy_fallback():
    async def run():
        db = FakeDb(record=None)
        response = SimpleNamespace(
            status_code=201,
            content=b"{}",
            json=lambda: {"id": "pref", "init_point": "https://mp.example/pref"},
        )
        client = AsyncMock()
        client.post.return_value = response
        with patch.dict("os.environ", {"MERCADO_PAGO_LEOFT_ACCESS_TOKEN": "legacy-token"}, clear=False):
            with patch("app.services.leoft_checkout_service.httpx.AsyncClient") as factory:
                factory.return_value.__aenter__.return_value = client
                await create_or_get_checkout(
                    anamnese={}, empresa_id=str(uuid4()), lead_id="lead", anamnese_id="answer",
                    config={"checkout_provider": "mercado_pago", "checkout_amount": "97"}, db=db,
                )
        assert client.post.await_args.kwargs["headers"]["Authorization"] == "Bearer legacy-token"
        assert "notification_url" not in client.post.await_args.kwargs["json"]

    asyncio.run(run())


def test_database_checkout_uses_tenant_webhook_and_legacy_checkout_keeps_legacy_url():
    async def run():
        empresa_id = uuid4()
        response = SimpleNamespace(
            status_code=201,
            content=b"{}",
            json=lambda: {"id": "pref", "init_point": "https://mp.example/pref"},
        )
        client = AsyncMock()
        client.post.return_value = response
        database_credentials = ResolvedPaymentCredentials("db-token", "db-secret", "test", "database")
        legacy_credentials = ResolvedPaymentCredentials("legacy-token", "legacy-secret", "test", "environment")
        with patch.dict("os.environ", {
            "AGENTOS_PUBLIC_BASE_URL": "https://agenteos.example",
            "MERCADO_PAGO_LEOFT_NOTIFICATION_URL": "https://legacy.example/api/webhooks/mercado-pago/leoft",
        }, clear=False):
            with patch("app.services.leoft_checkout_service.httpx.AsyncClient") as factory:
                factory.return_value.__aenter__.return_value = client
                with patch("app.services.leoft_checkout_service.resolve_mercado_pago_credentials", return_value=database_credentials):
                    await create_or_get_checkout(
                        anamnese={}, empresa_id=str(empresa_id), lead_id="lead-a", anamnese_id="answer-a",
                        config={"checkout_provider": "mercado_pago", "checkout_amount": "97"}, db=object(),
                    )
                database_payload = client.post.await_args.kwargs["json"]
                with patch("app.services.leoft_checkout_service.resolve_mercado_pago_credentials", return_value=legacy_credentials):
                    await create_or_get_checkout(
                        anamnese={}, empresa_id=str(empresa_id), lead_id="lead-b", anamnese_id="answer-b",
                        config={
                            "checkout_provider": "mercado_pago",
                            "checkout_amount": "97",
                            "checkout_notification_url": "https://legacy.example/api/webhooks/mercado-pago/leoft",
                        }, db=object(),
                    )
                legacy_payload = client.post.await_args.kwargs["json"]
        assert database_payload["notification_url"] == f"https://agenteos.example/api/webhooks/mercado-pago/{empresa_id}"
        assert legacy_payload["notification_url"] == "https://legacy.example/api/webhooks/mercado-pago/leoft"

    asyncio.run(run())


def test_b_c_database_configuration_wins_and_tenants_do_not_mix_tokens():
    async def run():
        empresa_a, empresa_b = uuid4(), uuid4()
        records = {empresa_a: _record(token="token-a"), empresa_b: _record(token="token-b")}

        async def lookup(_db, *, empresa_id, provider="mercado_pago"):
            return records[empresa_id]

        with patch("app.services.payment_credentials.get_payment_provider", side_effect=lookup):
            with patch.dict("os.environ", {"META_CREDENTIAL_ENCRYPTION_KEY": KEY, "MERCADO_PAGO_LEOFT_ACCESS_TOKEN": "legacy"}):
                resolved_a = await resolve_mercado_pago_credentials(object(), empresa_id=empresa_a)
                resolved_b = await resolve_mercado_pago_credentials(object(), empresa_id=empresa_b)
        assert resolved_a.access_token == "token-a"
        assert resolved_b.access_token == "token-b"
        assert resolved_a.source == resolved_b.source == "database"

    asyncio.run(run())


def test_d_e_blank_update_preserves_and_nonempty_update_replaces_encrypted_token():
    async def run():
        record = _record(token="original")
        original_ciphertext = record.access_token_encrypted
        db = FakeDb(record=record)
        with patch.dict("os.environ", {"META_CREDENTIAL_ENCRYPTION_KEY": KEY}):
            await save_mercado_pago_credentials(
                db, empresa_id=record.empresa_id, enabled=True, environment="test", access_token="",
            )
            assert record.access_token_encrypted == original_ciphertext
            await save_mercado_pago_credentials(
                db, empresa_id=record.empresa_id, enabled=True, environment="test", access_token="replacement",
            )
            assert record.access_token_encrypted != original_ciphertext
            assert decrypt_meta_secret(record.access_token_encrypted) == "replacement"
            assert "replacement" not in record.access_token_encrypted

    asyncio.run(run())


def test_first_enabled_configuration_requires_both_secrets():
    async def run():
        db = FakeDb(record=None)
        with pytest.raises(ValueError, match="access_token_required"):
            await save_mercado_pago_credentials(
                db, empresa_id=uuid4(), enabled=True, environment="test", access_token="", webhook_secret="secret",
            )
        with pytest.raises(ValueError, match="webhook_secret_required"):
            await save_mercado_pago_credentials(
                db, empresa_id=uuid4(), enabled=True, environment="test", access_token="token", webhook_secret="",
            )

    asyncio.run(run())


def test_f_serialized_get_never_contains_real_secrets():
    record = _record(token="sensitive-token", secret="sensitive-secret")
    payload = serialize_mercado_pago_configuration(record, webhook_url="https://example.test/webhook")
    assert payload["configured"] is True
    assert payload["access_token_configured"] is True
    assert payload["webhook_secret_configured"] is True
    assert "sensitive-token" not in repr(payload)
    assert "sensitive-secret" not in repr(payload)
    assert "access_token" not in payload
    assert "webhook_secret" not in payload


def test_g_non_super_admin_is_forbidden():
    user = SimpleNamespace(email="tenant@example.test", role="admin_empresa")
    with pytest.raises(HTTPException) as raised:
        asyncio.run(require_super_admin(user))
    assert raised.value.status_code == 403


def test_h_connection_test_uses_readonly_users_me_get_and_never_posts():
    async def run():
        empresa_id = uuid4()
        db = FakeDb(empresa=SimpleNamespace(id=empresa_id))
        client = AsyncMock()
        client.get.return_value = SimpleNamespace(status_code=200)
        credentials = ResolvedPaymentCredentials("token", "secret", "test", "database")
        with patch("app.api.routers.company_payments.resolve_mercado_pago_credentials", return_value=credentials):
            with patch("app.api.routers.company_payments.httpx.AsyncClient") as factory:
                factory.return_value.__aenter__.return_value = client
                result = await run_connection_test(str(empresa_id), db=db, _=SimpleNamespace())
        assert result["valid"] is True
        client.get.assert_awaited_once()
        assert client.get.await_args.args[0] == "https://api.mercadopago.com/users/me"
        client.post.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("environment", ["test", "production"])
def test_i_j_environment_is_returned_without_secret(environment):
    payload = serialize_mercado_pago_configuration(_record(environment=environment), webhook_url="/webhook")
    assert payload["environment"] == environment


def test_legacy_environment_defaults_to_test_and_webhook_origin_can_be_reused_for_tenant_route():
    async def run():
        with patch.dict("os.environ", {
            "MERCADO_PAGO_LEOFT_ACCESS_TOKEN": "legacy",
            "MERCADO_PAGO_LEOFT_NOTIFICATION_URL": "https://legacy.example/api/webhooks/mercado-pago/leoft",
        }, clear=True):
            credentials = await resolve_mercado_pago_credentials(None, empresa_id=None)
            assert credentials.environment == "test"
            empresa_id = uuid4()
            assert tenant_webhook_url(empresa_id=empresa_id) == f"https://legacy.example/api/webhooks/mercado-pago/{empresa_id}"

    asyncio.run(run())


def test_k_tenant_a_webhook_secret_does_not_validate_tenant_b_signature():
    import hashlib
    import hmac
    import json

    raw = json.dumps({"type": "payment", "data": {"id": "pay-1"}}).encode()
    manifest = "id:pay-1;request-id:req-1;ts:1700000000;"
    tenant_b_digest = hmac.new(b"tenant-b-secret", manifest.encode(), hashlib.sha256).hexdigest()
    signature = f"ts=1700000000,v1={tenant_b_digest}"
    assert not validate_signature(
        raw_body=raw, x_signature=signature, x_request_id="req-1", secret="tenant-a-secret",
    )


def test_tenant_webhook_rejects_legacy_credentials_when_company_has_no_database_configuration():
    async def run():
        empresa_id = uuid4()
        db = FakeDb(empresa=SimpleNamespace(id=empresa_id))
        legacy = ResolvedPaymentCredentials("legacy-token", "legacy-secret", "test", "environment")
        with patch("app.api.routers.mercado_pago_leoft.resolve_mercado_pago_credentials", return_value=legacy):
            with pytest.raises(HTTPException) as raised:
                await receive_company_mercado_pago_webhook(
                    str(empresa_id), request=SimpleNamespace(), db=db,
                )
        assert raised.value.status_code == 503
        assert raised.value.detail == "payment_credentials_unavailable"

    asyncio.run(run())


def test_production_valid_signature_live_mode_false_accepts_simulation_without_payment_side_effects():
    async def run():
        empresa_id = uuid4()
        request = _signed_webhook_request({
            "type": "payment",
            "action": "payment.updated",
            "data": {"id": "123456"},
            "live_mode": False,
        })
        db = FakeDb(empresa=SimpleNamespace(id=empresa_id))
        credentials = ResolvedPaymentCredentials("db-token", "db-secret", "production", "database")
        lookup = AsyncMock()
        persist = AsyncMock()
        with patch("app.api.routers.mercado_pago_leoft.resolve_mercado_pago_credentials", return_value=credentials):
            with patch("app.api.routers.mercado_pago_leoft.lookup_payment", lookup):
                with patch("app.api.routers.mercado_pago_leoft.persist_payment_result", persist):
                    result = await receive_company_mercado_pago_webhook(str(empresa_id), request=request, db=db)
        assert result == {"ok": True, "status": "simulation_received", "event_type": "payment", "live_mode": False}
        lookup.assert_not_awaited()
        persist.assert_not_awaited()
        assert db.added == []

    asyncio.run(run())


def test_production_invalid_signature_live_mode_false_is_rejected_before_simulation():
    async def run():
        empresa_id = uuid4()
        payload = {
            "type": "payment",
            "action": "payment.updated",
            "data": {"id": "123456"},
            "live_mode": False,
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        request = FakeWebhookRequest(body, signature="ts=1700000000,v1=invalid")
        db = FakeDb(empresa=SimpleNamespace(id=empresa_id))
        credentials = ResolvedPaymentCredentials("db-token", "db-secret", "production", "database")
        lookup = AsyncMock()
        with patch("app.api.routers.mercado_pago_leoft.resolve_mercado_pago_credentials", return_value=credentials):
            with patch("app.api.routers.mercado_pago_leoft.lookup_payment", lookup):
                with pytest.raises(HTTPException) as raised:
                    await receive_company_mercado_pago_webhook(str(empresa_id), request=request, db=db)
        assert raised.value.status_code == 401
        assert raised.value.detail == "invalid_webhook_signature"
        lookup.assert_not_awaited()

    asyncio.run(run())


def test_production_valid_signature_live_mode_true_keeps_real_payment_lookup_flow():
    async def run():
        empresa_id = uuid4()
        request = _signed_webhook_request({
            "type": "payment",
            "action": "payment.updated",
            "data": {"id": "real-payment-1"},
            "live_mode": True,
        })
        db = FakeDb(empresa=SimpleNamespace(id=empresa_id))
        credentials = ResolvedPaymentCredentials("db-token", "db-secret", "production", "database")
        payment = {"id": "real-payment-1", "status": "pending"}
        lookup = AsyncMock(return_value=payment)
        persist = AsyncMock(return_value={"ok": True, "status": "processed"})
        with patch("app.api.routers.mercado_pago_leoft.resolve_mercado_pago_credentials", return_value=credentials):
            with patch("app.api.routers.mercado_pago_leoft.lookup_payment", lookup):
                with patch("app.api.routers.mercado_pago_leoft.persist_payment_result", persist):
                    result = await receive_company_mercado_pago_webhook(str(empresa_id), request=request, db=db)
        assert result == {"ok": True, "status": "processed"}
        lookup.assert_awaited_once_with("real-payment-1", access_token="db-token")
        persist.assert_awaited_once_with(db, payment, expected_empresa_id=str(empresa_id))

    asyncio.run(run())


def test_real_payment_lookup_404_remains_422_and_is_not_masked_as_simulation():
    async def run():
        from app.services.mercado_pago.leoft_webhook import LeoFTMercadoPagoWebhookError

        empresa_id = uuid4()
        request = _signed_webhook_request({
            "type": "payment",
            "action": "payment.updated",
            "data": {"id": "missing-real-payment"},
            "live_mode": True,
        })
        db = FakeDb(empresa=SimpleNamespace(id=empresa_id))
        credentials = ResolvedPaymentCredentials("db-token", "db-secret", "production", "database")
        lookup = AsyncMock(side_effect=LeoFTMercadoPagoWebhookError("payment_lookup_http_404"))
        with patch("app.api.routers.mercado_pago_leoft.resolve_mercado_pago_credentials", return_value=credentials):
            with patch("app.api.routers.mercado_pago_leoft.lookup_payment", lookup):
                with pytest.raises(HTTPException) as raised:
                    await receive_company_mercado_pago_webhook(str(empresa_id), request=request, db=db)
        assert raised.value.status_code == 422
        assert raised.value.detail == "payment_lookup_http_404"
        lookup.assert_awaited_once_with("missing-real-payment", access_token="db-token")

    asyncio.run(run())


def test_migration_contract_and_legacy_webhook_route_are_preserved():
    root = Path(__file__).resolve().parents[2]
    migration = (root / "db/migrations/2026_08_17_empresa_payment_providers.sql").read_text()
    rollback = (root / "db/migrations/2026_08_17_empresa_payment_providers_rollback.sql").read_text()
    router_source = (root / "app/api/routers/mercado_pago_leoft.py").read_text()
    assert "UNIQUE (empresa_id, provider)" in migration
    assert "access_token_encrypted" in migration and "webhook_secret_encrypted" in migration
    assert "DROP TABLE IF EXISTS empresa_payment_providers" in rollback
    assert '@router.post("/leoft")' in router_source
