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
    build_leoft_payment_approved_event,
    verify_event_signature,
)


EMPRESA_ID = "empresa_fake_leoft_ag21"
LEAD_ID = "lead_fake_leoft_ag21"
ANAMNESE_ID = "anamnese_fake_leoft_ag21"
CHECKOUT_REFERENCE = "leoft_chk_ag21_hermes_bridge_tests_safe"
HMAC_SECRET_FAKE = "fake-local-hermes-secret-ag21"


def record(results: dict[str, str], key: str, condition: bool, detail: Any = None, value: str = "ok") -> None:
    if not condition:
        raise AssertionError(f"{key}=fail {detail if detail is not None else ''}".strip())
    results[key] = value


def anamnese(**overrides: Any) -> dict[str, Any]:
    data = {
        "empresa_id": EMPRESA_ID,
        "lead_id": LEAD_ID,
        "anamnese_id": ANAMNESE_ID,
        "checkout_reference": CHECKOUT_REFERENCE,
    }
    data.update(overrides)
    return data


def payment(payment_id: str, **overrides: Any) -> dict[str, Any]:
    data = {
        "id": payment_id,
        "status": "approved",
        "external_reference": CHECKOUT_REFERENCE,
        "transaction_amount": 97,
        "currency_id": "BRL",
        "live_mode": False,
    }
    data.update(overrides)
    return data


def app_access(**overrides: Any) -> dict[str, Any]:
    data = {
        "app_url": "https://app.leoft.com.br",
        "access_token": "fake-local-token-ag21",
        "access_link": "https://app.leoft.com.br/acesso?token=fake-local-token-ag21",
        "dry_run": True,
    }
    data.update(overrides)
    return data


def expect_blocked(fn: Callable[[], Any], reason_part: str) -> bool:
    try:
        fn()
    except LeoFTHermesBridgeError as exc:
        return reason_part in str(exc)
    return False


def source_has_no_real_calls() -> bool:
    source_paths = [
        ROOT / "app/services/hermes/leoft_bridge.py",
        ROOT / "scripts/verificar_leoft_agenteos_hermes_bridge_safe.py",
        ROOT / "scripts/verificar_leoft_agenteos_hermes_bridge_tests_safe.py",
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

    envelope = build_leoft_payment_approved_event(
        anamnese=anamnese(),
        payment=payment("pay_ag21_valid"),
        app_access=app_access(),
        hmac_secret=HMAC_SECRET_FAKE,
    )
    body = envelope["body"]
    required = {
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
    }

    record(
        results,
        "valid_event",
        envelope["dry_run"] is True
        and envelope["real_call"] is False
        and body["event"] == "leoft.payment_approved"
        and body["checkout_reference"] == CHECKOUT_REFERENCE
        and body["lead_id"] == LEAD_ID
        and body["anamnese_id"] == ANAMNESE_ID
        and body["app_access"]["access_link_created"] is True,
    )
    record(results, "required_fields", required.issubset(body.keys()))
    record(
        results,
        "hmac_signature_mock",
        envelope["signature"]["mode"] == "dry_run_signed_local"
        and verify_event_signature(body, envelope["signature"]["value"], HMAC_SECRET_FAKE),
    )
    record(results, "invalid_signature_blocked", not verify_event_signature(body, "invalid-signature", HMAC_SECRET_FAKE))

    missing_checks = [
        (lambda: build_leoft_payment_approved_event(anamnese=anamnese(empresa_id=""), payment=payment("pay_missing_empresa"), app_access=app_access()), "empresa_id is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=anamnese(lead_id=""), payment=payment("pay_missing_lead"), app_access=app_access()), "lead_id is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=anamnese(anamnese_id=""), payment=payment("pay_missing_anamnese"), app_access=app_access()), "anamnese_id is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=anamnese(checkout_reference=""), payment=payment("pay_missing_checkout"), app_access=app_access()), "checkout_reference is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=anamnese(), payment=payment("pay_missing_external", external_reference=""), app_access=app_access()), "payment.external_reference is required"),
        (lambda: build_leoft_payment_approved_event(anamnese=anamnese(), payment=payment("pay_missing_app"), app_access=app_access(access_link="")), "app_access.access_link is required"),
    ]
    record(results, "missing_fields_blocked", all(expect_blocked(fn, reason) for fn, reason in missing_checks))

    bridge = LeoFTHermesBridgeDryRun()
    first = bridge.emit_payment_approved(
        anamnese=anamnese(),
        payment=payment("pay_ag21_duplicate"),
        app_access=app_access(),
        hmac_secret=HMAC_SECRET_FAKE,
    )
    duplicate = bridge.emit_payment_approved(
        anamnese=anamnese(),
        payment=payment("pay_ag21_duplicate"),
        app_access=app_access(),
        hmac_secret=HMAC_SECRET_FAKE,
    )

    record(
        results,
        "pending_no_event",
        expect_blocked(
            lambda: bridge.emit_payment_approved(
                anamnese=anamnese(),
                payment=payment("pay_ag21_pending", status="pending"),
                app_access=app_access(),
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
                anamnese=anamnese(),
                payment=payment("pay_ag21_rejected", status="rejected"),
                app_access=app_access(),
            ),
            "payment status is not approved",
        )
        and len(bridge.events) == 1,
    )
    record(results, "duplicate_idempotent", first["event_created"] is True and duplicate["status"] == "duplicate" and len(bridge.events) == 1)
    record(
        results,
        "wrong_amount_blocked",
        expect_blocked(
            lambda: bridge.emit_payment_approved(
                anamnese=anamnese(),
                payment=payment("pay_ag21_wrong_amount", transaction_amount=98),
                app_access=app_access(),
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
                anamnese=anamnese(),
                payment=payment("pay_ag21_wrong_currency", currency_id="USD"),
                app_access=app_access(),
            ),
            "wrong currency",
        )
        and len(bridge.events) == 1,
    )
    record(results, "dry_run_no_network", envelope["dry_run"] is True and envelope["real_call"] is False and bridge.real_calls_attempted == 0)
    record(results, "external_calls", source_has_no_real_calls(), value="blocked")

    print("AGENTEOS_HERMES_BRIDGE_TESTS_OK")
    for key in [
        "valid_event",
        "required_fields",
        "hmac_signature_mock",
        "invalid_signature_blocked",
        "missing_fields_blocked",
        "pending_no_event",
        "rejected_no_event",
        "duplicate_idempotent",
        "wrong_amount_blocked",
        "wrong_currency_blocked",
        "dry_run_no_network",
        "external_calls",
    ]:
        print(f"{key}={results[key]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
