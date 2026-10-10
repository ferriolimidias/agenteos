from __future__ import annotations

import json
from pathlib import Path

from app.services.mercado_pago.leoft_fake_contract import (
    LEOFT_AMOUNT,
    LEOFT_CURRENCY,
    LEOFT_PRODUCT_ID,
    LeoFTFakeStore,
    build_leoft_preference_payload,
    handle_mercado_pago_webhook_fake,
)


EMPRESA_ID = "empresa-fake-leoft"
LEAD_ID = "lead-fake-leoft"
ANAMNESE_ID = "anamnese-fake-leoft"
CHECKOUT_REFERENCE = "leoft_chk_test_contract"


def _store() -> LeoFTFakeStore:
    store = LeoFTFakeStore()
    store.add_lead(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        nome="Lead Fake",
        telefone="5551999991234",
    )
    store.add_anamnese(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        anamnese_id=ANAMNESE_ID,
        checkout_reference=CHECKOUT_REFERENCE,
    )
    return store


def _webhook(payment_id: str = "123456") -> dict:
    return {
        "action": "payment.updated",
        "api_version": "v1",
        "data": {"id": payment_id},
        "type": "payment",
        "live_mode": False,
    }


def _payment(payment_id: str = "123456", **overrides):
    payment = {
        "id": payment_id,
        "status": "approved",
        "external_reference": CHECKOUT_REFERENCE,
        "transaction_amount": int(LEOFT_AMOUNT),
        "currency_id": LEOFT_CURRENCY,
    }
    payment.update(overrides)
    return payment


def test_checkout_preference_fake_payload_contract():
    payload = build_leoft_preference_payload(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        anamnese_id=ANAMNESE_ID,
        checkout_reference=CHECKOUT_REFERENCE,
        payer={"email": "lead.fake@example.invalid", "name": "Lead Fake"},
    )

    assert payload["contract_mode"] == "fake_local"
    assert payload["real_api_call"] is False
    assert payload["external_reference"] == CHECKOUT_REFERENCE
    assert payload["items"] == [
        {
            "title": "Método Híbrido LeoFT",
            "unit_price": 97,
            "currency_id": "BRL",
            "quantity": 1,
        }
    ]
    assert payload["metadata"] == {
        "empresa_id": EMPRESA_ID,
        "lead_id": LEAD_ID,
        "anamnese_id": ANAMNESE_ID,
        "checkout_reference": CHECKOUT_REFERENCE,
        "product_id": LEOFT_PRODUCT_ID,
    }
    assert payload["notification_url"].endswith("/api/webhooks/mercado-pago/leoft")
    assert payload["back_urls"]["success"].endswith("/success")
    assert payload["auto_return"] == "approved"
    assert payload["payer"]["email"].endswith("@example.invalid")


def test_webhook_extracts_payment_id_and_approved_matches_anamnese():
    store = _store()
    fetched_ids: list[str] = []

    def fetcher(payment_id: str):
        fetched_ids.append(payment_id)
        return _payment(payment_id)

    result = handle_mercado_pago_webhook_fake(_webhook("123456"), store=store, payment_fetcher=fetcher)

    assert fetched_ids == ["123456"]
    assert result["status"] == "approved"
    assert result["anamnese_id"] == ANAMNESE_ID
    assert store.payment_status["123456"]["status"] == "approved"
    assert len(store.hermes_events) == 1
    assert len(store.whatsapp_messages) == 1


def test_pending_and_rejected_do_not_dispatch_hermes_or_whatsapp():
    store = _store()

    pending = handle_mercado_pago_webhook_fake(
        _webhook("pending-1"),
        store=store,
        payment_fetcher=lambda payment_id: _payment(payment_id, status="pending"),
    )
    rejected = handle_mercado_pago_webhook_fake(
        _webhook("rejected-1"),
        store=store,
        payment_fetcher=lambda payment_id: _payment(payment_id, status="rejected"),
    )

    assert pending["status"] == "pending"
    assert rejected["status"] == "rejected"
    assert store.hermes_events == []
    assert store.whatsapp_messages == []


def test_duplicate_webhook_is_idempotent():
    store = _store()
    fetcher = lambda payment_id: _payment(payment_id)

    first = handle_mercado_pago_webhook_fake(_webhook("dup-1"), store=store, payment_fetcher=fetcher)
    second = handle_mercado_pago_webhook_fake(_webhook("dup-1"), store=store, payment_fetcher=fetcher)

    assert first["status"] == "approved"
    assert second["status"] == "duplicate"
    assert second["hermes_event_created"] is False
    assert len(store.hermes_events) == 1
    assert len(store.whatsapp_messages) == 1


def test_wrong_external_reference_amount_and_currency_are_blocked():
    cases = [
        ("wrong-ref", _payment("wrong-ref", external_reference="leoft_chk_other"), "wrong_external_reference"),
        ("wrong-amount", _payment("wrong-amount", transaction_amount=98), "wrong_amount"),
        ("wrong-currency", _payment("wrong-currency", currency_id="USD"), "wrong_currency"),
    ]

    for payment_id, payment, reason in cases:
        store = _store()
        result = handle_mercado_pago_webhook_fake(
            _webhook(payment_id),
            store=store,
            payment_fetcher=lambda _payment_id, payment=payment: payment,
        )
        assert result["status"] == "blocked"
        assert result["reason"] == reason
        assert store.hermes_events == []
        assert store.whatsapp_messages == []


def test_hermes_event_dry_run_contract_and_hmac_structure():
    store = _store()
    result = handle_mercado_pago_webhook_fake(
        _webhook("approved-1"),
        store=store,
        payment_fetcher=lambda payment_id: _payment(payment_id),
    )
    event = result["hermes_event"]

    assert event["dry_run"] is True
    assert event["future_endpoint"] == "POST /api/internal/hermes/events/leoft/payment-approved"
    assert event["idempotency_key"] == "leoft.payment_approved:approved-1"
    assert event["body"]["event"] == "leoft.payment_approved"
    assert event["body"]["payment_provider"] == "mercado_pago"
    assert event["body"]["amount"] == 97
    assert event["body"]["currency"] == "BRL"
    assert event["body"]["app_access"]["access_link_created"] is True
    assert event["signature"]["algorithm"] == "HMAC-SHA256"
    assert event["signature"]["mode"] == "dry_run_unsigned"
    assert event["signature"]["value"] is None


def test_whatsapp_dry_run_masks_phone_and_token_in_safe_log():
    store = _store()
    result = handle_mercado_pago_webhook_fake(
        _webhook("approved-2"),
        store=store,
        payment_fetcher=lambda payment_id: _payment(payment_id),
    )
    message = result["whatsapp_message"]
    token = result["whatsapp_message"]["message"].split("token=", 1)[1].splitlines()[0]

    assert message["dry_run"] is True
    assert message["real_send"] is False
    assert message["telefone_masked"] == "***1234"
    assert "Pagamento confirmado!" in message["message"]
    assert "https://app.leoft.com.br/acesso?token=" in message["message"]
    assert token not in json.dumps(message["safe_log"], ensure_ascii=False)
    assert "token=%2A%2A%2A" in json.dumps(message["safe_log"]) or "token=***" in json.dumps(message["safe_log"])


def test_contract_module_has_no_real_external_call_imports():
    source = Path("app/services/mercado_pago/leoft_fake_contract.py").read_text(encoding="utf-8")
    forbidden = [
        "import requests",
        "import httpx",
        "mercadopago",
        "AsyncClient",
        "client.post(",
        "client.get(",
        "EVOLUTION_API",
        "OPENAI_API",
    ]
    for item in forbidden:
        assert item not in source
