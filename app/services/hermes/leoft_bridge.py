from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


LEOFT_HERMES_EVENT = "leoft.payment_approved"
LEOFT_PAYMENT_PROVIDER = "mercado_pago"
LEOFT_EXPECTED_AMOUNT = Decimal("97")
LEOFT_EXPECTED_CURRENCY = "BRL"
LEOFT_APP_URL = "https://app.leoft.com.br"
HERMES_FUTURE_ENDPOINT = "POST /api/internal/hermes/events/leoft/payment-approved"
HERMES_SIGNATURE_HEADER = "X-AgenteOS-Signature"
HERMES_SIGNATURE_ALGORITHM = "HMAC-SHA256"


class LeoFTHermesBridgeError(ValueError):
    pass


@dataclass
class LeoFTHermesBridgeDryRun:
    processed_keys: set[tuple[str, str]] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)
    real_calls_attempted: int = 0

    def emit_payment_approved(
        self,
        *,
        anamnese: Mapping[str, Any],
        payment: Mapping[str, Any],
        app_access: Mapping[str, Any],
        hmac_secret: str | None = None,
    ) -> dict[str, Any]:
        payment_id = _require_text(payment.get("id") or payment.get("payment_id"), "payment_id")
        checkout_reference = _require_text(
            anamnese.get("checkout_reference") or payment.get("external_reference"),
            "checkout_reference",
        )
        idempotency_scope = (payment_id, checkout_reference)
        if idempotency_scope in self.processed_keys:
            return {
                "ok": True,
                "status": "duplicate",
                "event_created": False,
                "duplicate": True,
                "payment_id": payment_id,
                "checkout_reference": checkout_reference,
                "dry_run": True,
                "real_call": False,
            }

        envelope = build_leoft_payment_approved_event(
            anamnese=anamnese,
            payment=payment,
            app_access=app_access,
            hmac_secret=hmac_secret,
        )
        self.processed_keys.add(idempotency_scope)
        self.events.append(envelope)
        return {
            "ok": True,
            "status": "created",
            "event_created": True,
            "duplicate": False,
            "payment_id": payment_id,
            "checkout_reference": checkout_reference,
            "dry_run": True,
            "real_call": False,
            "event": envelope,
        }


def build_leoft_payment_approved_event(
    *,
    anamnese: Mapping[str, Any],
    payment: Mapping[str, Any],
    app_access: Mapping[str, Any],
    hmac_secret: str | None = None,
) -> dict[str, Any]:
    body = _build_body(anamnese=anamnese, payment=payment, app_access=app_access)
    validate_leoft_payment_approved_body(body)
    signature_base = canonical_event_payload(body)
    signature_value = sign_event_payload(body, hmac_secret) if hmac_secret else None
    payment_id = str(body["payment_id"])
    checkout_reference = str(body["checkout_reference"])
    return {
        "dry_run": True,
        "real_call": False,
        "future_endpoint": HERMES_FUTURE_ENDPOINT,
        "idempotency_key": f"{LEOFT_HERMES_EVENT}:{payment_id}",
        "idempotency_scope": {
            "payment_id": payment_id,
            "checkout_reference": checkout_reference,
        },
        "body": body,
        "signature": {
            "algorithm": HERMES_SIGNATURE_ALGORITHM,
            "header": HERMES_SIGNATURE_HEADER,
            "signed_payload_sha256": hashlib.sha256(signature_base.encode("utf-8")).hexdigest(),
            "value": signature_value,
            "mode": "dry_run_signed_local" if signature_value else "dry_run_unsigned",
        },
    }


def validate_leoft_payment_approved_body(body: Mapping[str, Any]) -> None:
    required_fields = [
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
    for field_name in required_fields:
        if field_name not in body:
            raise LeoFTHermesBridgeError(f"{field_name} is required")
        if field_name != "app_access":
            _require_text(body.get(field_name), field_name)

    if str(body["event"]) != LEOFT_HERMES_EVENT:
        raise LeoFTHermesBridgeError("invalid event")
    if str(body["payment_provider"]) != LEOFT_PAYMENT_PROVIDER:
        raise LeoFTHermesBridgeError("invalid payment_provider")
    if _money(body["amount"]) != LEOFT_EXPECTED_AMOUNT:
        raise LeoFTHermesBridgeError("wrong amount")
    if str(body["currency"]).strip().upper() != LEOFT_EXPECTED_CURRENCY:
        raise LeoFTHermesBridgeError("wrong currency")

    app_access = body.get("app_access")
    if not isinstance(app_access, Mapping):
        raise LeoFTHermesBridgeError("app_access is required")
    if str(app_access.get("app_url") or "").strip() != LEOFT_APP_URL:
        raise LeoFTHermesBridgeError("invalid app_access.app_url")
    if app_access.get("access_link_created") is not True:
        raise LeoFTHermesBridgeError("app_access_link not prepared")


def canonical_event_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sign_event_payload(payload: Mapping[str, Any], secret: str) -> str:
    secret_text = _require_text(secret, "hmac_secret")
    return hmac.new(
        secret_text.encode("utf-8"),
        canonical_event_payload(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_event_signature(payload: Mapping[str, Any], signature: str, secret: str) -> bool:
    expected = sign_event_payload(payload, secret)
    return hmac.compare_digest(expected, str(signature or ""))


def _build_body(
    *,
    anamnese: Mapping[str, Any],
    payment: Mapping[str, Any],
    app_access: Mapping[str, Any],
) -> dict[str, Any]:
    anamnese_checkout_reference = _require_text(anamnese.get("checkout_reference"), "checkout_reference")
    payment_checkout_reference = _require_text(payment.get("external_reference"), "payment.external_reference")
    if payment_checkout_reference != anamnese_checkout_reference:
        raise LeoFTHermesBridgeError("payment external_reference does not match anamnese checkout_reference")

    status = str(payment.get("status") or "").strip().lower()
    if status != "approved":
        raise LeoFTHermesBridgeError("payment status is not approved")

    app_url = _require_text(app_access.get("app_url"), "app_access.app_url")
    access_link = _require_text(app_access.get("access_link"), "app_access.access_link")
    if app_url != LEOFT_APP_URL:
        raise LeoFTHermesBridgeError("invalid app_access.app_url")

    amount = _money(payment.get("transaction_amount") or payment.get("amount"))
    if amount != LEOFT_EXPECTED_AMOUNT:
        raise LeoFTHermesBridgeError("wrong amount")

    currency = str(payment.get("currency_id") or payment.get("currency") or "").strip().upper()
    if currency != LEOFT_EXPECTED_CURRENCY:
        raise LeoFTHermesBridgeError("wrong currency")

    return {
        "event": LEOFT_HERMES_EVENT,
        "empresa_id": _require_text(anamnese.get("empresa_id"), "empresa_id"),
        "lead_id": _require_text(anamnese.get("lead_id"), "lead_id"),
        "anamnese_id": _require_text(anamnese.get("anamnese_id"), "anamnese_id"),
        "checkout_reference": anamnese_checkout_reference,
        "payment_provider": LEOFT_PAYMENT_PROVIDER,
        "payment_id": _require_text(payment.get("id") or payment.get("payment_id"), "payment_id"),
        "amount": int(LEOFT_EXPECTED_AMOUNT),
        "currency": LEOFT_EXPECTED_CURRENCY,
        "app_access": {
            "app_url": LEOFT_APP_URL,
            "access_link_created": bool(access_link),
        },
    }


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("1"))
    except (InvalidOperation, ValueError):
        return None


def _require_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LeoFTHermesBridgeError(f"{name} is required")
    return text
