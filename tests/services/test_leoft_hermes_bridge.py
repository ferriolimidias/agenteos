from __future__ import annotations

from pathlib import Path

from app.services.hermes.leoft_bridge import (
    LEOFT_HERMES_EVENT,
    LeoFTHermesBridgeDryRun,
    LeoFTHermesBridgeError,
    build_leoft_payment_approved_event,
    verify_event_signature,
)


EMPRESA_ID = "empresa-fake-leoft"
LEAD_ID = "lead-fake-leoft"
ANAMNESE_ID = "anamnese-fake-leoft"
CHECKOUT_REFERENCE = "leoft_chk_bridge_test"
HMAC_SECRET = "fake-local-hermes-secret"


def _anamnese(**overrides):
    data = {
        "empresa_id": EMPRESA_ID,
        "lead_id": LEAD_ID,
        "anamnese_id": ANAMNESE_ID,
        "checkout_reference": CHECKOUT_REFERENCE,
    }
    data.update(overrides)
    return data


def _payment(payment_id: str = "pay_bridge_1", **overrides):
    data = {
        "id": payment_id,
        "status": "approved",
        "external_reference": CHECKOUT_REFERENCE,
        "transaction_amount": 97,
        "currency_id": "BRL",
    }
    data.update(overrides)
    return data


def _app_access(**overrides):
    data = {
        "app_url": "https://app.leoft.com.br",
        "access_link": "https://app.leoft.com.br/acesso?token=fake-local-token",
        "dry_run": True,
    }
    data.update(overrides)
    return data


def _blocked(fn, expected_reason: str):
    try:
        fn()
    except LeoFTHermesBridgeError as exc:
        assert expected_reason in str(exc)
        return
    raise AssertionError("expected LeoFTHermesBridgeError")


def test_valid_payment_approved_event_contract():
    envelope = build_leoft_payment_approved_event(
        anamnese=_anamnese(),
        payment=_payment("pay_valid"),
        app_access=_app_access(),
        hmac_secret=HMAC_SECRET,
    )
    body = envelope["body"]

    assert envelope["dry_run"] is True
    assert envelope["real_call"] is False
    assert envelope["future_endpoint"] == "POST /api/internal/hermes/events/leoft/payment-approved"
    assert envelope["idempotency_key"] == "leoft.payment_approved:pay_valid"
    assert envelope["idempotency_scope"] == {
        "payment_id": "pay_valid",
        "checkout_reference": CHECKOUT_REFERENCE,
    }
    assert body == {
        "event": LEOFT_HERMES_EVENT,
        "empresa_id": EMPRESA_ID,
        "lead_id": LEAD_ID,
        "anamnese_id": ANAMNESE_ID,
        "checkout_reference": CHECKOUT_REFERENCE,
        "payment_provider": "mercado_pago",
        "payment_id": "pay_valid",
        "amount": 97,
        "currency": "BRL",
        "app_access": {
            "app_url": "https://app.leoft.com.br",
            "access_link_created": True,
        },
    }


def test_hmac_fake_signature_and_invalid_signature_blocked():
    envelope = build_leoft_payment_approved_event(
        anamnese=_anamnese(),
        payment=_payment("pay_signed"),
        app_access=_app_access(),
        hmac_secret=HMAC_SECRET,
    )

    assert envelope["signature"]["mode"] == "dry_run_signed_local"
    assert envelope["signature"]["value"]
    assert verify_event_signature(envelope["body"], envelope["signature"]["value"], HMAC_SECRET) is True
    assert verify_event_signature(envelope["body"], "invalid-signature", HMAC_SECRET) is False


def test_pending_and_rejected_do_not_generate_event():
    for status in ["pending", "rejected"]:
        bridge = LeoFTHermesBridgeDryRun()
        _blocked(
            lambda status=status: bridge.emit_payment_approved(
                anamnese=_anamnese(),
                payment=_payment(f"pay_{status}", status=status),
                app_access=_app_access(),
            ),
            "payment status is not approved",
        )
        assert bridge.events == []


def test_duplicate_is_idempotent_by_payment_id_and_checkout_reference():
    bridge = LeoFTHermesBridgeDryRun()
    first = bridge.emit_payment_approved(
        anamnese=_anamnese(),
        payment=_payment("pay_duplicate"),
        app_access=_app_access(),
    )
    second = bridge.emit_payment_approved(
        anamnese=_anamnese(),
        payment=_payment("pay_duplicate"),
        app_access=_app_access(),
    )

    assert first["event_created"] is True
    assert second["status"] == "duplicate"
    assert second["event_created"] is False
    assert len(bridge.events) == 1


def test_amount_currency_checkout_reference_and_app_access_are_required():
    cases = [
        (lambda: build_leoft_payment_approved_event(anamnese=_anamnese(), payment=_payment(transaction_amount=98), app_access=_app_access()), "wrong amount"),
        (lambda: build_leoft_payment_approved_event(anamnese=_anamnese(), payment=_payment(currency_id="USD"), app_access=_app_access()), "wrong currency"),
        (lambda: build_leoft_payment_approved_event(anamnese=_anamnese(checkout_reference=""), payment=_payment(), app_access=_app_access()), "checkout_reference is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=_anamnese(), payment=_payment(external_reference=""), app_access=_app_access()), "payment.external_reference is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=_anamnese(), payment=_payment(), app_access=_app_access(access_link="")), "app_access.access_link is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=_anamnese(lead_id=""), payment=_payment(), app_access=_app_access()), "lead_id is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=_anamnese(empresa_id=""), payment=_payment(), app_access=_app_access()), "empresa_id is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=_anamnese(anamnese_id=""), payment=_payment(), app_access=_app_access()), "anamnese_id is required"),
    ]

    for fn, reason in cases:
        _blocked(fn, reason)


def test_bridge_module_has_no_real_external_call_imports():
    source = Path("app/services/hermes/leoft_bridge.py").read_text(encoding="utf-8")
    forbidden = [
        "import requests",
        "import httpx",
        "AsyncClient",
        "client.post(",
        "client.get(",
        "urllib.request",
        "OPENAI_API",
        "EVOLUTION_API",
    ]
    for item in forbidden:
        assert item not in source
