from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.mercado_pago.leoft_fake_contract import (  # noqa: E402
    LEOFT_AMOUNT,
    LEOFT_CURRENCY,
    LEOFT_PRODUCT_ID,
    LeoFTFakeStore,
    build_leoft_preference_payload,
    handle_mercado_pago_webhook_fake,
)


EMPRESA_ID = "empresa_fake_leoft_ag1"
LEAD_ID = "lead_fake_leoft_ag1"
ANAMNESE_ID = "anamnese_fake_leoft_ag1"
CHECKOUT_REFERENCE = "leoft_chk_ag1_e2e_safe"


def record(results: dict[str, str], key: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{key}=fail {detail if detail is not None else ''}".strip())
    results[key] = "ok"


def webhook(payment_id: str) -> dict[str, Any]:
    return {
        "action": "payment.updated",
        "api_version": "v1",
        "data": {"id": payment_id},
        "type": "payment",
        "live_mode": False,
    }


def payment(payment_id: str, **overrides: Any) -> dict[str, Any]:
    data = {
        "id": payment_id,
        "status": "approved",
        "external_reference": CHECKOUT_REFERENCE,
        "transaction_amount": int(LEOFT_AMOUNT),
        "currency_id": LEOFT_CURRENCY,
        "live_mode": False,
    }
    data.update(overrides)
    return data


def fetcher_factory(payments: dict[str, dict[str, Any]], fetched: list[str]):
    def fetcher(payment_id: str) -> dict[str, Any] | None:
        fetched.append(payment_id)
        return payments.get(payment_id)

    return fetcher


def source_has_no_real_calls() -> bool:
    source_paths = [
        ROOT / "app/services/mercado_pago/leoft_fake_contract.py",
        ROOT / "scripts/verificar_leoft_agenteos_mp_e2e_safe.py",
    ]
    forbidden = [
        "import " + "requests",
        "import " + "httpx",
        "mercado" + "pago",
        "Async" + "Client",
        "client." + "post(",
        "client." + "get(",
        "EVOLUTION" + "_API",
        "EVOLUTION" + "_API_TOKEN",
        "OPENAI" + "_API",
    ]
    for path in source_paths:
        source = path.read_text(encoding="utf-8")
        if any(item in source for item in forbidden):
            return False
    return True


def main() -> int:
    results: dict[str, str] = {}
    store = LeoFTFakeStore()

    lead = store.add_lead(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        nome="Lead Fake LeoFT",
        telefone="5551999990000",
    )
    record(results, "lead_fake", lead["lead_id"] == LEAD_ID)

    anamnese = store.add_anamnese(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        anamnese_id=ANAMNESE_ID,
        checkout_reference=CHECKOUT_REFERENCE,
        respostas={"objetivo": "treino hibrido", "nivel": "iniciante"},
    )
    record(results, "anamnese_fake", anamnese["submitted"] is True)
    record(results, "checkout_reference", anamnese["checkout_reference"] == CHECKOUT_REFERENCE)

    preference = build_leoft_preference_payload(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        anamnese_id=ANAMNESE_ID,
        checkout_reference=CHECKOUT_REFERENCE,
        payer={"email": "lead.fake@example.invalid", "name": "Lead Fake LeoFT"},
    )
    record(results, "preference_payload", preference["contract_mode"] == "fake_local" and preference["real_api_call"] is False)
    record(results, "external_reference", preference["external_reference"] == CHECKOUT_REFERENCE)
    record(
        results,
        "metadata",
        preference["metadata"] == {
            "empresa_id": EMPRESA_ID,
            "lead_id": LEAD_ID,
            "anamnese_id": ANAMNESE_ID,
            "checkout_reference": CHECKOUT_REFERENCE,
            "product_id": LEOFT_PRODUCT_ID,
        },
    )
    record(results, "webhook_payment_updated", webhook("pay_approved")["data"]["id"] == "pay_approved")

    fetched: list[str] = []
    payments = {"pay_approved": payment("pay_approved")}
    approved = handle_mercado_pago_webhook_fake(
        webhook("pay_approved"),
        store=store,
        payment_fetcher=fetcher_factory(payments, fetched),
    )
    record(results, "payment_fetch_mock", fetched == ["pay_approved"])
    record(results, "approved_matches_anamnese", approved["status"] == "approved" and approved["anamnese_id"] == ANAMNESE_ID)
    record(results, "hermes_event_dry_run", len(store.hermes_events) == 1 and store.hermes_events[0]["dry_run"] is True)
    record(results, "app_link_created", approved["app_link_created"] is True)
    whatsapp = approved["whatsapp_message"]
    token = whatsapp["message"].split("token=", 1)[1].splitlines()[0]
    safe_log_text = json.dumps(whatsapp["safe_log"], ensure_ascii=False)
    record(
        results,
        "whatsapp_message_dry_run",
        whatsapp["dry_run"] is True
        and whatsapp["real_send"] is False
        and token not in safe_log_text
        and whatsapp["telefone_masked"] == "***0000",
    )

    pending_before = len(store.hermes_events)
    pending = handle_mercado_pago_webhook_fake(
        webhook("pay_pending"),
        store=store,
        payment_fetcher=lambda payment_id: payment(payment_id, status="pending"),
    )
    record(results, "pending_no_hermes_event", pending["status"] == "pending" and len(store.hermes_events) == pending_before)

    rejected_before = len(store.hermes_events)
    rejected = handle_mercado_pago_webhook_fake(
        webhook("pay_rejected"),
        store=store,
        payment_fetcher=lambda payment_id: payment(payment_id, status="rejected"),
    )
    record(results, "rejected_no_hermes_event", rejected["status"] == "rejected" and len(store.hermes_events) == rejected_before)

    duplicate_before = (len(store.hermes_events), len(store.whatsapp_messages))
    duplicate = handle_mercado_pago_webhook_fake(
        webhook("pay_approved"),
        store=store,
        payment_fetcher=lambda payment_id: payment(payment_id),
    )
    record(
        results,
        "duplicate_idempotent",
        duplicate["status"] == "duplicate" and duplicate_before == (len(store.hermes_events), len(store.whatsapp_messages)),
    )

    wrong_ref_store = _store_for_negative()
    wrong_ref = handle_mercado_pago_webhook_fake(
        webhook("pay_wrong_ref"),
        store=wrong_ref_store,
        payment_fetcher=lambda payment_id: payment(payment_id, external_reference="leoft_chk_wrong"),
    )
    record(results, "wrong_external_reference_blocked", wrong_ref["reason"] == "wrong_external_reference")

    wrong_amount_store = _store_for_negative()
    wrong_amount = handle_mercado_pago_webhook_fake(
        webhook("pay_wrong_amount"),
        store=wrong_amount_store,
        payment_fetcher=lambda payment_id: payment(payment_id, transaction_amount=98),
    )
    record(results, "wrong_amount_blocked", wrong_amount["reason"] == "wrong_amount")

    wrong_currency_store = _store_for_negative()
    wrong_currency = handle_mercado_pago_webhook_fake(
        webhook("pay_wrong_currency"),
        store=wrong_currency_store,
        payment_fetcher=lambda payment_id: payment(payment_id, currency_id="USD"),
    )
    record(results, "wrong_currency_blocked", wrong_currency["reason"] == "wrong_currency")

    record(results, "external_calls", source_has_no_real_calls())
    record(results, "mercado_pago_real_calls", source_has_no_real_calls())
    record(results, "whatsapp_real_calls", source_has_no_real_calls())
    record(results, "hermes_real_calls", source_has_no_real_calls())

    print("AGENTEOS_LEOFT_MP_E2E_OK")
    for key in [
        "lead_fake",
        "anamnese_fake",
        "checkout_reference",
        "preference_payload",
        "external_reference",
        "metadata",
        "webhook_payment_updated",
        "payment_fetch_mock",
        "approved_matches_anamnese",
        "pending_no_hermes_event",
        "rejected_no_hermes_event",
        "duplicate_idempotent",
        "wrong_external_reference_blocked",
        "wrong_amount_blocked",
        "wrong_currency_blocked",
        "hermes_event_dry_run",
        "app_link_created",
        "whatsapp_message_dry_run",
        "external_calls",
        "mercado_pago_real_calls",
        "whatsapp_real_calls",
        "hermes_real_calls",
    ]:
        print(f"{key}={results[key]}")

    return 0


def _store_for_negative() -> LeoFTFakeStore:
    store = LeoFTFakeStore()
    store.add_lead(empresa_id=EMPRESA_ID, lead_id=LEAD_ID, telefone="5551999990000")
    store.add_anamnese(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        anamnese_id=ANAMNESE_ID,
        checkout_reference=CHECKOUT_REFERENCE,
    )
    return store


if __name__ == "__main__":
    raise SystemExit(main())
