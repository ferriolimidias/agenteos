from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.hermes.leoft_bridge import (  # noqa: E402
    LeoFTHermesBridgeDryRun,
    LeoFTHermesBridgeError,
    verify_event_signature,
)
from app.services.mercado_pago.leoft_fake_contract import (  # noqa: E402
    LEOFT_AMOUNT,
    LEOFT_CURRENCY,
    LeoFTFakeStore,
)


EMPRESA_ID = "empresa_fake_leoft_ag2"
LEAD_ID = "lead_fake_leoft_ag2"
ANAMNESE_ID = "anamnese_fake_leoft_ag2"
CHECKOUT_REFERENCE = "leoft_chk_ag2_hermes_bridge_safe"
HMAC_SECRET_FAKE = "fake-local-hermes-secret-ag2"


def record(results: dict[str, str], key: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{key}=fail {detail if detail is not None else ''}".strip())
    results[key] = "ok"


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


def app_access() -> dict[str, Any]:
    return {
        "app_url": "https://app.leoft.com.br",
        "access_token": "fake-local-token-ag2",
        "access_link": "https://app.leoft.com.br/acesso?token=fake-local-token-ag2",
        "dry_run": True,
    }


def expect_blocked(fn: Callable[[], Any], reason_part: str) -> bool:
    try:
        fn()
    except LeoFTHermesBridgeError as exc:
        return reason_part in str(exc)
    return False


def source_has_no_real_calls() -> bool:
    source_paths = [
        ROOT / "app/services/hermes/leoft_bridge.py",
        ROOT / "app/services/mercado_pago/leoft_fake_contract.py",
        ROOT / "scripts/verificar_leoft_agenteos_hermes_bridge_safe.py",
    ]
    forbidden = [
        "import " + "requests",
        "import " + "httpx",
        "Async" + "Client",
        "client." + "post(",
        "client." + "get(",
        "urllib." + "request",
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
    bridge = LeoFTHermesBridgeDryRun()

    lead = store.add_lead(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        nome="Lead Fake LeoFT AG2",
        telefone="5551999990000",
    )
    anamnese = store.add_anamnese(
        empresa_id=EMPRESA_ID,
        lead_id=LEAD_ID,
        anamnese_id=ANAMNESE_ID,
        checkout_reference=CHECKOUT_REFERENCE,
        respostas={"objetivo": "treino hibrido"},
    )
    approved_payment = payment("pay_ag2_approved")
    access = app_access()

    record(results, "payment_approved_validated", approved_payment["status"] == "approved")
    record(results, "checkout_reference", anamnese["checkout_reference"] == approved_payment["external_reference"])
    record(results, "lead_anamnese_link", lead["lead_id"] == anamnese["lead_id"] and lead["empresa_id"] == anamnese["empresa_id"])
    record(results, "app_access_link", access["dry_run"] is True and access["access_link"].startswith(access["app_url"]))

    emitted = bridge.emit_payment_approved(
        anamnese=anamnese,
        payment=approved_payment,
        app_access=access,
        hmac_secret=HMAC_SECRET_FAKE,
    )
    event = emitted["event"]
    body = event["body"]
    record(results, "hermes_event_created", emitted["event_created"] is True and len(bridge.events) == 1)
    record(
        results,
        "required_fields",
        set(
            [
                "event",
                "empresa_id",
                "lead_id",
                "anamnese_id",
                "checkout_reference",
                "payment_provider",
                "payment_id",
                "amount",
                "currency",
                "app_access",
            ]
        ).issubset(body.keys()),
    )
    record(
        results,
        "hmac_signature_mock",
        event["signature"]["mode"] == "dry_run_signed_local"
        and verify_event_signature(body, event["signature"]["value"], HMAC_SECRET_FAKE),
    )
    record(results, "invalid_signature_blocked", not verify_event_signature(body, "invalid-signature", HMAC_SECRET_FAKE))

    record(
        results,
        "missing_fields_blocked",
        expect_blocked(
            lambda: bridge.emit_payment_approved(
                anamnese={**anamnese, "lead_id": ""},
                payment=payment("pay_missing_field"),
                app_access=access,
            ),
            "lead_id is required",
        ),
    )
    record(
        results,
        "pending_no_event",
        expect_blocked(
            lambda: bridge.emit_payment_approved(
                anamnese=anamnese,
                payment=payment("pay_pending", status="pending"),
                app_access=access,
            ),
            "payment status is not approved",
        )
        and len(bridge.events) == 1,
    )
    record(
        results,
        "rejected_no_event",
        expect_blocked(
            lambda: bridge.emit_payment_approved(
                anamnese=anamnese,
                payment=payment("pay_rejected", status="rejected"),
                app_access=access,
            ),
            "payment status is not approved",
        )
        and len(bridge.events) == 1,
    )
    duplicate = bridge.emit_payment_approved(
        anamnese=anamnese,
        payment=approved_payment,
        app_access=access,
        hmac_secret=HMAC_SECRET_FAKE,
    )
    record(results, "duplicate_idempotent", duplicate["status"] == "duplicate" and len(bridge.events) == 1)
    record(
        results,
        "wrong_amount_blocked",
        expect_blocked(
            lambda: bridge.emit_payment_approved(
                anamnese=anamnese,
                payment=payment("pay_wrong_amount", transaction_amount=98),
                app_access=access,
            ),
            "wrong amount",
        )
        and len(bridge.events) == 1,
    )
    record(
        results,
        "wrong_currency_blocked",
        expect_blocked(
            lambda: bridge.emit_payment_approved(
                anamnese=anamnese,
                payment=payment("pay_wrong_currency", currency_id="USD"),
                app_access=access,
            ),
            "wrong currency",
        )
        and len(bridge.events) == 1,
    )
    record(
        results,
        "missing_checkout_reference_blocked",
        expect_blocked(
            lambda: bridge.emit_payment_approved(
                anamnese={**anamnese, "checkout_reference": ""},
                payment=payment("pay_missing_checkout"),
                app_access=access,
            ),
            "checkout_reference is required",
        ),
    )
    record(results, "dry_run_no_network", event["dry_run"] is True and event["real_call"] is False and bridge.real_calls_attempted == 0)
    record(results, "hermes_real_calls", source_has_no_real_calls())
    record(results, "external_calls", source_has_no_real_calls())

    print("AGENTEOS_HERMES_BRIDGE_OK")
    for key in [
        "payment_approved_validated",
        "hermes_event_created",
        "required_fields",
        "checkout_reference",
        "lead_anamnese_link",
        "app_access_link",
        "hmac_signature_mock",
        "invalid_signature_blocked",
        "missing_fields_blocked",
        "pending_no_event",
        "rejected_no_event",
        "duplicate_idempotent",
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
