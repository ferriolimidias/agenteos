from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from typing import Any


DEFAULT_HERMES_SIGNATURE_HEADER = "X-AgenteOS-Signature"
DEFAULT_HERMES_TIMESTAMP_HEADER = "X-AgenteOS-Timestamp"
DEFAULT_HERMES_EVENT_HEADER = "X-AgenteOS-Event"
DEFAULT_HERMES_IDEMPOTENCY_HEADER = "X-AgenteOS-Idempotency-Key"
HERMES_EVENT = "leoft.payment_approved"
HERMES_SIGNATURE_ALGORITHM = "HMAC-SHA256"
REDACTED = "***REDACTED***"


class HermesHMACConfigError(RuntimeError):
    pass


def canonical_json_body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_hermes_payload(body: bytes, secret: str, timestamp: str | None = None) -> dict:
    secret_text = str(secret or "").strip()
    if not secret_text:
        raise HermesHMACConfigError("HERMES_LEOFT_HMAC_SECRET ausente.")
    signature = hmac.new(secret_text.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "algorithm": HERMES_SIGNATURE_ALGORITHM,
        "timestamp": timestamp or _utc_iso(),
        "signature": signature,
        "signed_payload_sha256": hashlib.sha256(body).hexdigest(),
    }


def build_hermes_hmac_headers(payload: dict) -> dict:
    config = hmac_config_status()
    missing = config["missing"]
    if missing:
        raise HermesHMACConfigError("BLOQUEADO_CONFIGURACAO_HMAC_HEADER: " + ", ".join(missing))
    body = canonical_json_body(payload)
    signed = sign_hermes_payload(body, str(os.getenv("HERMES_LEOFT_HMAC_SECRET") or ""), config["timestamp"])
    idempotency_key = _idempotency_key_from_payload(payload)
    event = str((payload or {}).get("event") or HERMES_EVENT).strip()
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        str(config["event_header"]): event,
        str(config["timestamp_header"]): str(signed["timestamp"]),
        str(config["signature_header"]): str(signed["signature"]),
        str(config["idempotency_header"]): idempotency_key,
    }


def mask_hmac_config_for_logs(config: dict) -> dict:
    masked = {}
    for key, value in dict(config or {}).items():
        key_text = str(key)
        if key_text.lower() in {"secret", "hmac_secret", "signature"}:
            masked[key] = _mask_value(value)
        else:
            masked[key] = value
    return masked


def hmac_config_status() -> dict[str, Any]:
    signature_header = str(os.getenv("HERMES_LEOFT_HMAC_SIGNATURE_HEADER") or DEFAULT_HERMES_SIGNATURE_HEADER).strip()
    timestamp_header = str(os.getenv("HERMES_LEOFT_HMAC_TIMESTAMP_HEADER") or DEFAULT_HERMES_TIMESTAMP_HEADER).strip()
    event_header = str(os.getenv("HERMES_LEOFT_HMAC_EVENT_HEADER") or DEFAULT_HERMES_EVENT_HEADER).strip()
    idempotency_header = str(os.getenv("HERMES_LEOFT_HMAC_IDEMPOTENCY_HEADER") or DEFAULT_HERMES_IDEMPOTENCY_HEADER).strip()
    secret = str(os.getenv("HERMES_LEOFT_HMAC_SECRET") or "").strip()
    missing = []
    if not secret:
        missing.append("HERMES_LEOFT_HMAC_SECRET")
    if not signature_header:
        missing.append("HERMES_LEOFT_HMAC_SIGNATURE_HEADER")
    if not timestamp_header:
        missing.append("HERMES_LEOFT_HMAC_TIMESTAMP_HEADER")
    if not event_header:
        missing.append("HERMES_LEOFT_HMAC_EVENT_HEADER")
    if not idempotency_header:
        missing.append("HERMES_LEOFT_HMAC_IDEMPOTENCY_HEADER")
    return {
        "secret_present": bool(secret),
        "secret_masked": _mask_value(secret),
        "signature_header": signature_header,
        "timestamp_header": timestamp_header,
        "event_header": event_header,
        "idempotency_header": idempotency_header,
        "timestamp": _utc_iso(),
        "defaults": {
            "signature_header": DEFAULT_HERMES_SIGNATURE_HEADER,
            "timestamp_header": DEFAULT_HERMES_TIMESTAMP_HEADER,
            "event_header": DEFAULT_HERMES_EVENT_HEADER,
            "idempotency_header": DEFAULT_HERMES_IDEMPOTENCY_HEADER,
        },
        "missing": missing,
    }


def _utc_iso() -> str:
    return str(int(time.time()))


def _idempotency_key_from_payload(payload: dict) -> str:
    for field_name in ("idempotency_key", "checkout_reference", "payment_id"):
        value = " ".join(str((payload or {}).get(field_name) or "").split()).strip()
        if value:
            return value
    raise HermesHMACConfigError("Payload Hermes inválido: idempotency_key, checkout_reference ou payment_id obrigatório para idempotência.")


def _mask_value(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return REDACTED
    return text[:3] + REDACTED + text[-3:]
