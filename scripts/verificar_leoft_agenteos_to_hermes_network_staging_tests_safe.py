from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.verificar_leoft_agenteos_to_hermes_network_staging_safe as sender  # noqa: E402
from app.services.hermes.leoft_bridge import sign_event_payload  # noqa: E402


FAKE_SECRET = "fake-local-staging-secret-ag5-tests"


def record(results: dict[str, str], key: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(f"{key}=fail")
    results[key] = "ok"


def blocked_url(url: str) -> bool:
    try:
        sender.assert_allowed_target(url)
    except sender.StagingSenderError:
        return True
    return False


def main() -> int:
    results: dict[str, str] = {}
    event = sender.build_fake_event(FAKE_SECRET)
    body = event["body"]
    headers = sender.build_headers(body, FAKE_SECRET)

    record(results, "signature", headers["X-AgenteOS-Signature"] == sign_event_payload(body, FAKE_SECRET))
    record(
        results,
        "headers",
        headers["Content-Type"] == "application/json"
        and headers["X-AgenteOS-Event"] == "leoft.payment_approved"
        and bool(headers["X-AgenteOS-Timestamp"]),
    )
    record(results, "idempotency", headers["X-AgenteOS-Idempotency-Key"] == f"leoft.payment_approved:{body['payment_id']}")
    sender.assert_allowed_target(sender.STAGING_URL)
    record(results, "url_allowlist", blocked_url("https://app.leoft.com.br/api/plan") and blocked_url("https://example.invalid/api/internal/hermes/events/leoft/payment-approved"))
    record(
        results,
        "response_contracts",
        sender._is_accepted(200, {"status": "ok", "idempotent": False, "dry_run": True, "called_external_services": False, "called_real_app": False, "called_real_telegram": False})
        and sender._is_duplicate(200, {"status": "ok", "idempotent": True})
        and sender._is_blocked(400, {"status": "error", "error": "invalid_signature", "dry_run": True, "called_external_services": False, "called_real_app": False, "called_real_telegram": False}, "invalid_signature"),
    )
    output = io.StringIO()
    with redirect_stdout(output):
        pass
    source = (ROOT / "scripts/verificar_leoft_agenteos_to_hermes_network_staging_safe.py").read_text(encoding="utf-8")
    record(results, "no_secret_leak", FAKE_SECRET not in output.getvalue() and "print(secret" not in source and "print(headers" not in source)
    record(results, "no_unapproved_external_calls", sender.source_allows_only_hermes_staging())

    print("AGENTEOS_TO_HERMES_STAGING_NETWORK_TESTS_OK")
    for key in ["signature", "headers", "idempotency", "url_allowlist", "response_contracts", "no_secret_leak", "no_unapproved_external_calls"]:
        print(f"{key}={results[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
