from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.hermes.leoft_bridge import verify_event_signature  # noqa: E402
from app.services.hermes.leoft_client import (  # noqa: E402
    HERMES_PAYMENT_APPROVED_ENDPOINT,
    HERMES_PAYMENT_APPROVED_PATH,
    LeoFTHermesClientDryRun,
    hermes_accepted_response_contract,
    hermes_blocked_response_contract,
    hermes_duplicate_response_contract,
)
from app.services.mercado_pago.leoft_fake_contract import (  # noqa: E402
    LEOFT_AMOUNT,
    LEOFT_CURRENCY,
    LeoFTFakeStore,
    handle_mercado_pago_webhook_fake,
)


EMPRESA_ID = "empresa_fake_leoft_ag4"
LEAD_ID = "lead_fake_leoft_ag4"
ANAMNESE_ID = "anamnese_fake_leoft_ag4"
HMAC_SECRET_FAKE = "fake-local-hermes-client-secret-ag4"
TIMESTAMP_FAKE = "2026-07-09T00:00:00Z"


def record(results: dict[str, str], key: str, condition: bool, detail: Any = None, value: str = "ok") -> None:
    if not condition:
        raise AssertionError(f"{key}=fail {detail if detail is not None else ''}".strip())
    results[key] = value


def webhook(payment_id: str) -> dict[str, Any]:
    return {
        "action": "payment.updated",
        "api_version": "v1",
        "data": {"id": payment_id},
        "type": "payment",
        "live_mode": False,
    }


def payment(payment_id: str, checkout_reference: str, **overrides: Any) -> dict[str, Any]:
    data = {
        "id": payment_id,
        "status": "approved",
        "external_reference": checkout_reference,
        "transaction_amount": int(LEOFT_AMOUNT),
        "currency_id": LEOFT_CURRENCY,
        "live_mode": False,
    }
    data.update(overrides)
    return data


def store_with_anamnese() -> tuple[LeoFTFakeStore, dict[str, Any]]:
    store = LeoFTFakeStore()
    store.add_lead(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        nome="Lead Fake LeoFT AG4",
        telefone="5551999990000",
    )
    anamnese = store.add_anamnese(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        anamnese_id=ANAMNESE_ID,
        respostas={"objetivo": "treino hibrido", "fase": "ag4"},
    )
    return store, anamnese


def source_has_no_real_calls() -> bool:
    source_paths = [
        ROOT / "app/services/hermes/leoft_client.py",
        ROOT / "app/services/hermes/leoft_bridge.py",
        ROOT / "app/services/mercado_pago/leoft_fake_contract.py",
        ROOT / "scripts/verificar_leoft_agenteos_hermes_client_safe.py",
    ]
    forbidden = [
        "import " + "requests",
        "import " + "httpx",
        "import " + "aiohttp",
        "Async" + "Client",
        "client." + "post(",
        "client." + "get(",
        "urllib." + "request",
        "EVOLUTION" + "_API",
        "EVOLUTION" + "_API_TOKEN",
        "OPENAI" + "_API",
        "APP_USR" + "-",
    ]
    for path in source_paths:
        source = path.read_text(encoding="utf-8")
        if any(item in source for item in forbidden):
            return False
    return True


def main() -> int:
    results: dict[str, str] = {}
    store, anamnese = store_with_anamnese()
    checkout_reference = anamnese["checkout_reference"]
    approved_payment_id = "mp_fake_ag4_approved"
    payments = {
        approved_payment_id: payment(approved_payment_id, checkout_reference),
    }

    approved = handle_mercado_pago_webhook_fake(
        webhook(approved_payment_id),
        store=store,
        payment_fetcher=lambda payment_id: payments.get(payment_id),
        hmac_secret=HMAC_SECRET_FAKE,
    )
    event = approved["hermes_event"]
    body = event["body"]
    record(results, "event_created", approved["hermes_event_created"] is True and len(store.hermes_events) == 1)

    client = LeoFTHermesClientDryRun()
    request = client.prepare_payment_approved_request(
        event,
        hmac_secret=HMAC_SECRET_FAKE,
        timestamp=TIMESTAMP_FAKE,
    )
    headers = request["headers"]

    record(
        results,
        "future_endpoint",
        request["method"] == "POST"
        and request["path"] == HERMES_PAYMENT_APPROVED_PATH
        and request["future_endpoint"] == HERMES_PAYMENT_APPROVED_ENDPOINT,
    )
    record(
        results,
        "headers",
        headers == {
            "Content-Type": "application/json",
            "X-AgenteOS-Event": "leoft.payment_approved",
            "X-AgenteOS-Idempotency-Key": f"leoft.payment_approved:{approved_payment_id}",
            "X-AgenteOS-Signature": request["signature"]["value"],
            "X-AgenteOS-Timestamp": TIMESTAMP_FAKE,
        },
    )
    record(
        results,
        "idempotency_key",
        request["idempotency_key"] == f"leoft.payment_approved:{approved_payment_id}"
        and headers["X-AgenteOS-Idempotency-Key"] == request["idempotency_key"],
    )
    record(
        results,
        "hmac_signature",
        request["signature"]["mode"] == "dry_run_signed_local_mock"
        and headers["X-AgenteOS-Signature"] == request["signature"]["value"]
        and verify_event_signature(body, request["signature"]["value"], HMAC_SECRET_FAKE),
    )
    record(
        results,
        "body_payload",
        request["body"] == body
        and json.loads(request["body_json"]) == body
        and body["checkout_reference"] == checkout_reference
        and body["payment_id"] == approved_payment_id,
    )

    accepted = hermes_accepted_response_contract(
        checkout_reference=checkout_reference,
        payment_id=approved_payment_id,
    )
    duplicate = hermes_duplicate_response_contract()
    blocked = hermes_blocked_response_contract("invalid_signature")
    record(
        results,
        "accepted_response_contract",
        accepted == {
            "ok": True,
            "event": "leoft.payment_approved",
            "status": "accepted",
            "idempotent": False,
            "checkout_reference": checkout_reference,
            "payment_id": approved_payment_id,
        },
    )
    record(results, "duplicate_response_contract", duplicate == {"ok": True, "status": "duplicate", "idempotent": True})
    record(results, "blocked_response_contract", blocked == {"ok": False, "status": "blocked", "reason": "invalid_signature"})

    pending_store, pending_anamnese = store_with_anamnese()
    pending_payment_id = "mp_fake_ag4_pending"
    pending = handle_mercado_pago_webhook_fake(
        webhook(pending_payment_id),
        store=pending_store,
        payment_fetcher=lambda payment_id: payment(payment_id, pending_anamnese["checkout_reference"], status="pending"),
    )
    record(results, "pending_no_request", pending["status"] == "pending" and len(pending_store.hermes_events) == 0)

    rejected_store, rejected_anamnese = store_with_anamnese()
    rejected_payment_id = "mp_fake_ag4_rejected"
    rejected = handle_mercado_pago_webhook_fake(
        webhook(rejected_payment_id),
        store=rejected_store,
        payment_fetcher=lambda payment_id: payment(payment_id, rejected_anamnese["checkout_reference"], status="rejected"),
    )
    record(results, "rejected_no_request", rejected["status"] == "rejected" and len(rejected_store.hermes_events) == 0)

    wrong_amount_store, wrong_amount_anamnese = store_with_anamnese()
    wrong_amount_payment_id = "mp_fake_ag4_wrong_amount"
    wrong_amount = handle_mercado_pago_webhook_fake(
        webhook(wrong_amount_payment_id),
        store=wrong_amount_store,
        payment_fetcher=lambda payment_id: payment(payment_id, wrong_amount_anamnese["checkout_reference"], transaction_amount=98),
    )
    record(
        results,
        "wrong_amount_blocked",
        wrong_amount["status"] == "blocked"
        and wrong_amount["reason"] == "wrong_amount"
        and len(wrong_amount_store.hermes_events) == 0,
    )

    wrong_currency_store, wrong_currency_anamnese = store_with_anamnese()
    wrong_currency_payment_id = "mp_fake_ag4_wrong_currency"
    wrong_currency = handle_mercado_pago_webhook_fake(
        webhook(wrong_currency_payment_id),
        store=wrong_currency_store,
        payment_fetcher=lambda payment_id: payment(payment_id, wrong_currency_anamnese["checkout_reference"], currency_id="USD"),
    )
    record(
        results,
        "wrong_currency_blocked",
        wrong_currency["status"] == "blocked"
        and wrong_currency["reason"] == "wrong_currency"
        and len(wrong_currency_store.hermes_events) == 0,
    )

    record(
        results,
        "dry_run_no_network",
        request["dry_run"] is True
        and request["real_call"] is False
        and request["network_call"] is False
        and client.network_calls_attempted == 0,
    )
    record(results, "hermes_real_calls", source_has_no_real_calls(), value="blocked")
    record(results, "external_calls", source_has_no_real_calls(), value="blocked")

    print("AGENTEOS_HERMES_CLIENT_DRY_RUN_OK")
    for key in [
        "event_created",
        "future_endpoint",
        "headers",
        "idempotency_key",
        "hmac_signature",
        "body_payload",
        "accepted_response_contract",
        "duplicate_response_contract",
        "blocked_response_contract",
        "pending_no_request",
        "rejected_no_request",
        "wrong_amount_blocked",
        "wrong_currency_blocked",
        "dry_run_no_network",
        "hermes_real_calls",
        "external_calls",
    ]:
        print(f"{key}={results[key]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
