from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from app.services.hermes.leoft_bridge import (
    HERMES_SIGNATURE_ALGORITHM,
    HERMES_SIGNATURE_HEADER,
    LEOFT_HERMES_EVENT,
    canonical_event_payload,
    sign_event_payload,
    validate_leoft_payment_approved_body,
)


HERMES_PAYMENT_APPROVED_METHOD = "POST"
HERMES_PAYMENT_APPROVED_PATH = "/api/internal/hermes/events/leoft/payment-approved"
HERMES_PAYMENT_APPROVED_ENDPOINT = f"{HERMES_PAYMENT_APPROVED_METHOD} {HERMES_PAYMENT_APPROVED_PATH}"
HERMES_EVENT_HEADER = "X-AgenteOS-Event"
HERMES_IDEMPOTENCY_HEADER = "X-AgenteOS-Idempotency-Key"
HERMES_TIMESTAMP_HEADER = "X-AgenteOS-Timestamp"


class LeoFTHermesClientError(ValueError):
    pass


@dataclass
class LeoFTHermesClientDryRun:
    prepared_requests: list[dict[str, Any]] = field(default_factory=list)
    network_calls_attempted: int = 0

    def prepare_payment_approved_request(
        self,
        event: Mapping[str, Any],
        *,
        hmac_secret: str,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        request = build_payment_approved_dry_run_request(
            event,
            hmac_secret=hmac_secret,
            timestamp=timestamp,
        )
        self.prepared_requests.append(request)
        return request


def build_payment_approved_dry_run_request(
    event: Mapping[str, Any],
    *,
    hmac_secret: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    body = _event_body(event)
    validate_leoft_payment_approved_body(body)

    idempotency_key = _idempotency_key(event, body)
    signature_value = sign_event_payload(body, hmac_secret)
    canonical_payload = canonical_event_payload(body)
    headers = {
        "Content-Type": "application/json",
        HERMES_EVENT_HEADER: LEOFT_HERMES_EVENT,
        HERMES_IDEMPOTENCY_HEADER: idempotency_key,
        HERMES_SIGNATURE_HEADER: signature_value,
        HERMES_TIMESTAMP_HEADER: timestamp or _utc_iso(),
    }
    return {
        "ok": True,
        "dry_run": True,
        "real_call": False,
        "network_call": False,
        "method": HERMES_PAYMENT_APPROVED_METHOD,
        "path": HERMES_PAYMENT_APPROVED_PATH,
        "future_endpoint": HERMES_PAYMENT_APPROVED_ENDPOINT,
        "headers": headers,
        "body": dict(body),
        "body_json": canonical_payload,
        "idempotency_key": idempotency_key,
        "signature": {
            "algorithm": HERMES_SIGNATURE_ALGORITHM,
            "header": HERMES_SIGNATURE_HEADER,
            "signed_payload_sha256": hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
            "value": signature_value,
            "mode": "dry_run_signed_local_mock",
        },
        "expected_response_contracts": expected_response_contracts(body),
    }


def expected_response_contracts(body: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    checkout_reference = str(body.get("checkout_reference") or "")
    payment_id = str(body.get("payment_id") or "")
    return {
        "accepted": hermes_accepted_response_contract(
            checkout_reference=checkout_reference,
            payment_id=payment_id,
        ),
        "duplicate": hermes_duplicate_response_contract(),
        "blocked": hermes_blocked_response_contract("invalid_signature"),
    }


def hermes_accepted_response_contract(*, checkout_reference: str, payment_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "event": LEOFT_HERMES_EVENT,
        "status": "accepted",
        "idempotent": False,
        "checkout_reference": str(checkout_reference),
        "payment_id": str(payment_id),
    }


def hermes_duplicate_response_contract() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "duplicate",
        "idempotent": True,
    }


def hermes_blocked_response_contract(reason: str) -> dict[str, Any]:
    reason_text = str(reason or "").strip()
    if not reason_text:
        raise LeoFTHermesClientError("blocked reason is required")
    return {
        "ok": False,
        "status": "blocked",
        "reason": reason_text,
    }


def _event_body(event: Mapping[str, Any]) -> dict[str, Any]:
    body = event.get("body") if isinstance(event.get("body"), Mapping) else event
    if not isinstance(body, Mapping):
        raise LeoFTHermesClientError("event body is required")
    return dict(body)


def _idempotency_key(event: Mapping[str, Any], body: Mapping[str, Any]) -> str:
    existing = str(event.get("idempotency_key") or "").strip()
    if existing:
        return existing
    payment_id = str(body.get("payment_id") or "").strip()
    if not payment_id:
        raise LeoFTHermesClientError("payment_id is required for idempotency")
    return f"{LEOFT_HERMES_EVENT}:{payment_id}"


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
