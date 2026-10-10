from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.hermes.leoft_bridge import (  # noqa: E402
    LeoFTHermesBridgeError,
    build_leoft_payment_approved_event,
    verify_event_signature,
)
from app.services.hermes.leoft_client import (  # noqa: E402
    LeoFTHermesClientDryRun,
    build_payment_approved_dry_run_request,
    hermes_accepted_response_contract,
    hermes_blocked_response_contract,
    hermes_duplicate_response_contract,
)


EMPRESA_ID = "empresa_fake_leoft_ag4_tests"
LEAD_ID = "lead_fake_leoft_ag4_tests"
ANAMNESE_ID = "anamnese_fake_leoft_ag4_tests"
CHECKOUT_REFERENCE = "leoft_chk_ag4_client_tests_safe"
HMAC_SECRET_FAKE = "fake-local-hermes-client-tests-secret-ag4"
TIMESTAMP_FAKE = "2026-07-09T00:00:00Z"


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
        "access_token": "fake-local-token-ag4-tests",
        "access_link": "https://app.leoft.com.br/acesso?token=fake-local-token-ag4-tests",
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
        ROOT / "app/services/hermes/leoft_client.py",
        ROOT / "app/services/hermes/leoft_bridge.py",
        ROOT / "scripts/verificar_leoft_agenteos_hermes_client_tests_safe.py",
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
    payment_id = "mp_fake_ag4_client_tests_valid"
    event = build_leoft_payment_approved_event(
        anamnese=anamnese(),
        payment=payment(payment_id),
        app_access=app_access(),
        hmac_secret=HMAC_SECRET_FAKE,
    )
    request = build_payment_approved_dry_run_request(
        event,
        hmac_secret=HMAC_SECRET_FAKE,
        timestamp=TIMESTAMP_FAKE,
    )
    headers = request["headers"]
    body = request["body"]

    record(
        results,
        "valid_request",
        request["ok"] is True
        and request["dry_run"] is True
        and request["real_call"] is False
        and request["future_endpoint"] == "POST /api/internal/hermes/events/leoft/payment-approved",
    )
    record(
        results,
        "headers",
        headers["Content-Type"] == "application/json"
        and headers["X-AgenteOS-Event"] == "leoft.payment_approved"
        and headers["X-AgenteOS-Idempotency-Key"] == f"leoft.payment_approved:{payment_id}"
        and headers["X-AgenteOS-Signature"] == request["signature"]["value"]
        and headers["X-AgenteOS-Timestamp"] == TIMESTAMP_FAKE,
    )
    record(results, "hmac", verify_event_signature(body, request["signature"]["value"], HMAC_SECRET_FAKE))
    record(results, "idempotency", request["idempotency_key"] == f"leoft.payment_approved:{payment_id}")
    record(
        results,
        "body",
        json.loads(request["body_json"]) == body
        and body["event"] == "leoft.payment_approved"
        and body["checkout_reference"] == CHECKOUT_REFERENCE
        and body["amount"] == 97
        and body["currency"] == "BRL",
    )
    record(
        results,
        "pending_no_request",
        expect_blocked(
            lambda: build_leoft_payment_approved_event(
                anamnese=anamnese(),
                payment=payment("mp_fake_ag4_client_tests_pending", status="pending"),
                app_access=app_access(),
            ),
            "payment status is not approved",
        ),
    )
    record(
        results,
        "rejected_no_request",
        expect_blocked(
            lambda: build_leoft_payment_approved_event(
                anamnese=anamnese(),
                payment=payment("mp_fake_ag4_client_tests_rejected", status="rejected"),
                app_access=app_access(),
            ),
            "payment status is not approved",
        ),
    )
    record(
        results,
        "wrong_amount_blocked",
        expect_blocked(
            lambda: build_leoft_payment_approved_event(
                anamnese=anamnese(),
                payment=payment("mp_fake_ag4_client_tests_wrong_amount", transaction_amount=98),
                app_access=app_access(),
            ),
            "wrong amount",
        ),
    )
    record(
        results,
        "wrong_currency_blocked",
        expect_blocked(
            lambda: build_leoft_payment_approved_event(
                anamnese=anamnese(),
                payment=payment("mp_fake_ag4_client_tests_wrong_currency", currency_id="USD"),
                app_access=app_access(),
            ),
            "wrong currency",
        ),
    )

    accepted = hermes_accepted_response_contract(
        checkout_reference=CHECKOUT_REFERENCE,
        payment_id=payment_id,
    )
    duplicate = hermes_duplicate_response_contract()
    blocked = hermes_blocked_response_contract("invalid_signature")
    record(
        results,
        "response_contracts",
        accepted["status"] == "accepted"
        and accepted["idempotent"] is False
        and duplicate == {"ok": True, "status": "duplicate", "idempotent": True}
        and blocked == {"ok": False, "status": "blocked", "reason": "invalid_signature"},
    )

    dry_client = LeoFTHermesClientDryRun()
    dry_client.prepare_payment_approved_request(event, hmac_secret=HMAC_SECRET_FAKE, timestamp=TIMESTAMP_FAKE)
    record(
        results,
        "dry_run_no_network",
        len(dry_client.prepared_requests) == 1
        and dry_client.network_calls_attempted == 0
        and source_has_no_real_calls(),
    )

    print("AGENTEOS_HERMES_CLIENT_TESTS_OK")
    for key in [
        "valid_request",
        "headers",
        "hmac",
        "idempotency",
        "pending_no_request",
        "rejected_no_request",
        "wrong_amount_blocked",
        "wrong_currency_blocked",
        "response_contracts",
        "dry_run_no_network",
    ]:
        print(f"{key}={results[key]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
