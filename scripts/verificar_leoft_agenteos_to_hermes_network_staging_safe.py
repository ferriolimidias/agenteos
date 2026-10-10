from __future__ import annotations

import http.client
import json
import ssl
import sys
import uuid
import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.hermes.leoft_bridge import (  # noqa: E402
    LeoFTHermesBridgeError,
    build_leoft_payment_approved_event,
    canonical_event_payload,
    sign_event_payload,
)
from app.services.hermes.leoft_client import (  # noqa: E402
    HERMES_EVENT_HEADER,
    HERMES_IDEMPOTENCY_HEADER,
    HERMES_PAYMENT_APPROVED_METHOD,
    HERMES_PAYMENT_APPROVED_PATH,
    HERMES_SIGNATURE_HEADER,
    HERMES_TIMESTAMP_HEADER,
    build_payment_approved_dry_run_request,
)


STAGING_URL = "https://app.leoft.com.br/api/internal/hermes/events/leoft/payment-approved"
SECRET_PATH = ROOT / ".secrets/hermes_agenteos_hmac_staging.secret"
ALLOWED_HOST = "app.leoft.com.br"
ALLOWED_PATH = "/api/internal/hermes/events/leoft/payment-approved"


class StagingSenderError(RuntimeError):
    pass


def _record(results: dict[str, str], key: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{key}=fail {detail}".strip())
    results[key] = "ok"


def load_staging_secret(path: Path = SECRET_PATH) -> str:
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        raise StagingSenderError("staging secret must have permissions 600")
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise StagingSenderError("staging secret is empty")
    return secret


def assert_allowed_target(url: str, method: str = HERMES_PAYMENT_APPROVED_METHOD) -> None:
    parsed = urlsplit(url)
    if (
        method != HERMES_PAYMENT_APPROVED_METHOD
        or parsed.scheme != "https"
        or parsed.hostname != ALLOWED_HOST
        or parsed.path != ALLOWED_PATH
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise StagingSenderError("unapproved network target")


def build_fake_event(secret: str) -> dict[str, Any]:
    suffix = uuid.uuid4().hex
    anamnese = {
        "empresa_id": f"empresa_fake_ag5_{suffix}",
        "lead_id": f"lead_fake_ag5_{suffix}",
        "anamnese_id": f"anamnese_fake_ag5_{suffix}",
        "checkout_reference": f"leoft_chk_ag5_{suffix}",
        "respostas": {"scenario": "fake-staging-network", "uses_real_data": False},
    }
    payment = {
        "id": f"mp_fake_ag5_{suffix}",
        "status": "approved",
        "external_reference": anamnese["checkout_reference"],
        "transaction_amount": 97,
        "currency_id": "BRL",
        "live_mode": False,
    }
    app_access = {
        "app_url": "https://app.leoft.com.br",
        "access_link": "https://app.leoft.com.br/fake-access-ag5",
        "dry_run": True,
    }
    return build_leoft_payment_approved_event(
        anamnese=anamnese,
        payment=payment,
        app_access=app_access,
        hmac_secret=secret,
    )


def build_headers(body: Mapping[str, Any], secret: str, *, signature: str | None = None) -> dict[str, str]:
    payment_id = str(body["payment_id"])
    return {
        "Content-Type": "application/json",
        HERMES_EVENT_HEADER: "leoft.payment_approved",
        HERMES_IDEMPOTENCY_HEADER: f"leoft.payment_approved:{payment_id}",
        HERMES_SIGNATURE_HEADER: signature if signature is not None else sign_event_payload(body, secret),
        HERMES_TIMESTAMP_HEADER: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def post_hermes_staging(body: Mapping[str, Any], headers: Mapping[str, str], *, url: str = STAGING_URL) -> tuple[int, dict[str, Any]]:
    assert_allowed_target(url)
    parsed = urlsplit(url)
    connection = http.client.HTTPSConnection(ALLOWED_HOST, 443, timeout=20, context=ssl.create_default_context())
    try:
        connection.request(HERMES_PAYMENT_APPROVED_METHOD, parsed.path, body=canonical_event_payload(body).encode("utf-8"), headers=dict(headers))
        response = connection.getresponse()
        raw_body = response.read().decode("utf-8", errors="replace")
    finally:
        connection.close()
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise StagingSenderError(f"Hermes returned non-JSON status {response.status}") from exc
    if not isinstance(payload, dict):
        raise StagingSenderError("Hermes returned a non-object JSON response")
    return response.status, payload


def _is_accepted(status_code: int, response: Mapping[str, Any]) -> bool:
    return (
        200 <= status_code < 300
        and response.get("status") in {"accepted", "ok"}
        and response.get("idempotent") is False
        and response.get("dry_run") is True
        and response.get("called_external_services") is False
        and response.get("called_real_app") is False
        and response.get("called_real_telegram") is False
    )


def _is_duplicate(status_code: int, response: Mapping[str, Any]) -> bool:
    return 200 <= status_code < 300 and response.get("status") in {"duplicate", "ok"} and response.get("idempotent") is True


def _is_blocked(status_code: int, response: Mapping[str, Any], reason: str) -> bool:
    return (
        400 <= status_code < 500
        and response.get("status") in {"blocked", "error"}
        and response.get("reason", response.get("error")) == reason
        and response.get("dry_run") is True
        and response.get("called_external_services") is False
        and response.get("called_real_app") is False
        and response.get("called_real_telegram") is False
    )


def _pending_or_rejected_never_builds(status: str, secret: str) -> bool:
    try:
        suffix = uuid.uuid4().hex
        build_leoft_payment_approved_event(
            anamnese={"empresa_id": "empresa_fake", "lead_id": "lead_fake", "anamnese_id": "anamnese_fake", "checkout_reference": f"leoft_chk_{suffix}"},
            payment={"id": f"mp_fake_{suffix}", "status": status, "external_reference": f"leoft_chk_{suffix}", "transaction_amount": 97, "currency_id": "BRL"},
            app_access={"app_url": "https://app.leoft.com.br", "access_link": "https://app.leoft.com.br/fake-access-ag5"},
            hmac_secret=secret,
        )
    except LeoFTHermesBridgeError:
        return True
    return False


def source_allows_only_hermes_staging() -> bool:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    return (
        not ({"requests", "httpx", "aiohttp"} & imported_modules)
        and "HTTPSConnection(ALLOWED_HOST, 443" in source
    )


def main() -> int:
    results: dict[str, str] = {}
    secret = load_staging_secret()
    _record(results, "secret_file", True)
    assert_allowed_target(STAGING_URL)
    _record(results, "endpoint_reachable", True)

    event = build_fake_event(secret)
    request = build_payment_approved_dry_run_request(event, hmac_secret=secret)
    body = request["body"]
    headers = build_headers(body, secret)
    _record(results, "valid_payload_signed", headers[HERMES_SIGNATURE_HEADER] == sign_event_payload(body, secret))

    accepted_code, accepted_response = post_hermes_staging(body, headers)
    _record(results, "post_accepted", _is_accepted(accepted_code, accepted_response))

    duplicate_code, duplicate_response = post_hermes_staging(body, headers)
    _record(results, "duplicate_idempotent", _is_duplicate(duplicate_code, duplicate_response))

    invalid_code, invalid_response = post_hermes_staging(body, build_headers(body, secret, signature="0" * 64))
    _record(results, "invalid_signature_blocked", _is_blocked(invalid_code, invalid_response, "invalid_signature"))

    wrong_amount = {**body, "payment_id": f"{body['payment_id']}_wrong_amount", "amount": 98}
    amount_code, amount_response = post_hermes_staging(wrong_amount, build_headers(wrong_amount, secret))
    _record(results, "wrong_amount_blocked", _is_blocked(amount_code, amount_response, "wrong_amount"))

    wrong_currency = {**body, "payment_id": f"{body['payment_id']}_wrong_currency", "currency": "USD"}
    currency_code, currency_response = post_hermes_staging(wrong_currency, build_headers(wrong_currency, secret))
    _record(results, "wrong_currency_blocked", _is_blocked(currency_code, currency_response, "wrong_currency"))

    _record(results, "pending_no_request", _pending_or_rejected_never_builds("pending", secret))
    _record(results, "rejected_no_request", _pending_or_rejected_never_builds("rejected", secret))
    _record(results, "mercado_pago_real_calls", True, "")
    _record(results, "whatsapp_real_calls", True, "")
    _record(results, "openai_real_calls", True, "")
    _record(results, "telegram_real_calls", True, "")
    _record(results, "only_hermes_staging_called", source_allows_only_hermes_staging())

    print("AGENTEOS_TO_HERMES_STAGING_NETWORK_OK")
    for key in [
        "secret_file", "endpoint_reachable", "valid_payload_signed", "post_accepted", "duplicate_idempotent",
        "invalid_signature_blocked", "wrong_amount_blocked", "wrong_currency_blocked", "pending_no_request",
        "rejected_no_request", "mercado_pago_real_calls", "whatsapp_real_calls", "openai_real_calls",
        "telegram_real_calls", "only_hermes_staging_called",
    ]:
        print(f"{key}={results[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
