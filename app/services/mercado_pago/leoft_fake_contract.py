from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.hermes.leoft_bridge import (
    build_leoft_payment_approved_event as build_bridge_payment_approved_event,
)


LEOFT_PRODUCT_ID = "leoft_metodo_hibrido"
LEOFT_ITEM_TITLE = "Método Híbrido LeoFT"
LEOFT_AMOUNT = Decimal("97")
LEOFT_CURRENCY = "BRL"
LEOFT_APP_URL = "https://app.leoft.com.br"
LEOFT_NOTIFICATION_URL = "https://agenteos.example.invalid/api/webhooks/mercado-pago/leoft"
LEOFT_BACK_URL_BASE = "https://agenteos.example.invalid/anamnese/leoft/checkout"


class LeoFTPaymentContractError(ValueError):
    pass


@dataclass
class LeoFTFakeStore:
    leads: dict[str, dict[str, Any]] = field(default_factory=dict)
    anamneses: dict[str, dict[str, Any]] = field(default_factory=dict)
    checkout_index: dict[str, str] = field(default_factory=dict)
    payment_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    processed_payment_ids: set[str] = field(default_factory=set)
    hermes_events: list[dict[str, Any]] = field(default_factory=list)
    whatsapp_messages: list[dict[str, Any]] = field(default_factory=list)
    app_access_links: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add_lead(
        self,
        *,
        empresa_id: str,
        lead_id: str,
        nome: str = "Lead Fake LeoFT",
        telefone: str = "5551999990000",
    ) -> dict[str, Any]:
        lead = {
            "empresa_id": str(empresa_id),
            "lead_id": str(lead_id),
            "nome": str(nome),
            "telefone": str(telefone),
        }
        self.leads[str(lead_id)] = lead
        return lead

    def add_anamnese(
        self,
        *,
        empresa_id: str,
        lead_id: str,
        anamnese_id: str,
        checkout_reference: str | None = None,
        respostas: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        checkout_reference = checkout_reference or create_checkout_reference(
            empresa_id=empresa_id,
            lead_id=lead_id,
            anamnese_id=anamnese_id,
        )
        anamnese = {
            "empresa_id": str(empresa_id),
            "lead_id": str(lead_id),
            "anamnese_id": str(anamnese_id),
            "checkout_reference": str(checkout_reference),
            "respostas": dict(respostas or {}),
            "submitted": True,
            "submitted_at": _utc_iso(),
        }
        self.anamneses[str(anamnese_id)] = anamnese
        self.checkout_index[str(checkout_reference)] = str(anamnese_id)
        return anamnese

    def find_anamnese_by_checkout_reference(self, checkout_reference: str) -> dict[str, Any] | None:
        anamnese_id = self.checkout_index.get(str(checkout_reference or ""))
        if not anamnese_id:
            return None
        return self.anamneses.get(anamnese_id)


def create_checkout_reference(*, empresa_id: str, lead_id: str, anamnese_id: str) -> str:
    seed = f"{empresa_id}:{lead_id}:{anamnese_id}:{uuid.uuid4().hex}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"leoft_chk_{digest}"


def build_leoft_preference_payload(
    *,
    empresa_id: str,
    lead_id: str,
    anamnese_id: str,
    checkout_reference: str,
    payer: Mapping[str, Any] | None = None,
    notification_url: str = LEOFT_NOTIFICATION_URL,
    back_url_base: str = LEOFT_BACK_URL_BASE,
) -> dict[str, Any]:
    _require(checkout_reference, "checkout_reference")
    payload = {
        "items": [
            {
                "title": LEOFT_ITEM_TITLE,
                "unit_price": int(LEOFT_AMOUNT),
                "currency_id": LEOFT_CURRENCY,
                "quantity": 1,
            }
        ],
        "external_reference": str(checkout_reference),
        "metadata": {
            "empresa_id": str(empresa_id),
            "lead_id": str(lead_id),
            "anamnese_id": str(anamnese_id),
            "checkout_reference": str(checkout_reference),
            "product_id": LEOFT_PRODUCT_ID,
        },
        "notification_url": str(notification_url),
        "back_urls": {
            "success": f"{back_url_base.rstrip('/')}/success",
            "pending": f"{back_url_base.rstrip('/')}/pending",
            "failure": f"{back_url_base.rstrip('/')}/failure",
        },
        "auto_return": "approved",
        "contract_mode": "fake_local",
        "real_api_call": False,
    }
    if payer:
        payload["payer"] = _safe_payer(payer)
    return payload


def handle_mercado_pago_webhook_fake(
    payload: Mapping[str, Any],
    *,
    store: LeoFTFakeStore,
    payment_fetcher: Callable[[str], Mapping[str, Any] | None],
    hmac_secret: str | None = None,
) -> dict[str, Any]:
    if str(payload.get("type") or "").strip() != "payment":
        return {"ok": False, "status": "ignored", "reason": "unsupported_type", "hermes_event_created": False}

    payment_id = str((payload.get("data") or {}).get("id") or "").strip()
    if not payment_id:
        return {"ok": False, "status": "blocked", "reason": "missing_payment_id", "hermes_event_created": False}

    if payment_id in store.processed_payment_ids:
        return {
            "ok": True,
            "status": "duplicate",
            "payment_id": payment_id,
            "duplicate": True,
            "hermes_event_created": False,
            "whatsapp_message_created": False,
        }

    payment = payment_fetcher(payment_id)
    if not payment:
        return {"ok": False, "status": "blocked", "payment_id": payment_id, "reason": "payment_not_found", "hermes_event_created": False}

    status = str(payment.get("status") or "").strip().lower()
    if status in {"pending", "in_process"}:
        _record_payment_status(store, payment_id, status="pending", payment=payment)
        store.processed_payment_ids.add(payment_id)
        return {"ok": True, "status": "pending", "payment_id": payment_id, "hermes_event_created": False}

    if status in {"rejected", "cancelled", "canceled"}:
        _record_payment_status(store, payment_id, status=status, payment=payment)
        store.processed_payment_ids.add(payment_id)
        return {"ok": True, "status": status, "payment_id": payment_id, "hermes_event_created": False}

    if status != "approved":
        _record_payment_status(store, payment_id, status="blocked", payment=payment, reason="unsupported_status")
        store.processed_payment_ids.add(payment_id)
        return {"ok": False, "status": "blocked", "payment_id": payment_id, "reason": "unsupported_status", "hermes_event_created": False}

    checkout_reference = str(payment.get("external_reference") or "").strip()
    anamnese = store.find_anamnese_by_checkout_reference(checkout_reference)
    if not anamnese:
        _record_payment_status(store, payment_id, status="blocked", payment=payment, reason="wrong_external_reference")
        store.processed_payment_ids.add(payment_id)
        return {"ok": False, "status": "blocked", "payment_id": payment_id, "reason": "wrong_external_reference", "hermes_event_created": False}

    if _money(payment.get("transaction_amount")) != LEOFT_AMOUNT:
        _record_payment_status(store, payment_id, status="blocked", payment=payment, reason="wrong_amount", anamnese=anamnese)
        store.processed_payment_ids.add(payment_id)
        return {"ok": False, "status": "blocked", "payment_id": payment_id, "reason": "wrong_amount", "hermes_event_created": False}

    currency = str(payment.get("currency_id") or payment.get("currency") or "").strip().upper()
    if currency != LEOFT_CURRENCY:
        _record_payment_status(store, payment_id, status="blocked", payment=payment, reason="wrong_currency", anamnese=anamnese)
        store.processed_payment_ids.add(payment_id)
        return {"ok": False, "status": "blocked", "payment_id": payment_id, "reason": "wrong_currency", "hermes_event_created": False}

    app_access = _create_app_access(store, anamnese)
    event = build_hermes_payment_approved_event(
        anamnese=anamnese,
        payment_id=payment_id,
        app_access=app_access,
        hmac_secret=hmac_secret,
    )
    message = build_whatsapp_payment_approved_dry_run(
        lead=store.leads.get(str(anamnese["lead_id"]), {}),
        app_access=app_access,
    )

    _record_payment_status(store, payment_id, status="approved", payment=payment, anamnese=anamnese)
    store.hermes_events.append(event)
    store.whatsapp_messages.append(message)
    store.processed_payment_ids.add(payment_id)

    return {
        "ok": True,
        "status": "approved",
        "payment_id": payment_id,
        "anamnese_id": anamnese["anamnese_id"],
        "checkout_reference": checkout_reference,
        "hermes_event_created": True,
        "whatsapp_message_created": True,
        "app_link_created": True,
        "hermes_event": event,
        "whatsapp_message": message,
    }


def build_hermes_payment_approved_event(
    *,
    anamnese: Mapping[str, Any],
    payment_id: str,
    app_access: Mapping[str, Any],
    hmac_secret: str | None = None,
) -> dict[str, Any]:
    payment = {
        "id": str(payment_id),
        "status": "approved",
        "external_reference": str(anamnese["checkout_reference"]),
        "transaction_amount": int(LEOFT_AMOUNT),
        "currency_id": LEOFT_CURRENCY,
    }
    return build_bridge_payment_approved_event(
        anamnese=anamnese,
        payment=payment,
        app_access=app_access,
        hmac_secret=hmac_secret,
    )


def build_whatsapp_payment_approved_dry_run(
    *,
    lead: Mapping[str, Any],
    app_access: Mapping[str, Any],
) -> dict[str, Any]:
    access_link = str(app_access.get("access_link") or "")
    message = (
        "Pagamento confirmado! Seu treino está sendo preparado pelo Leonardo.\n\n"
        "Você já pode acessar seu app por aqui:\n"
        f"{access_link}\n\n"
        "Assim que o Leonardo aprovar seu treino, ele aparecerá no app."
    )
    return {
        "dry_run": True,
        "provider": "whatsapp",
        "future_provider": "evolution_or_meta",
        "lead_id": str(lead.get("lead_id") or ""),
        "telefone_masked": _mask_phone(str(lead.get("telefone") or "")),
        "message": message,
        "safe_log": {
            "message": message.replace(access_link, mask_app_access_link(access_link)),
            "access_link": mask_app_access_link(access_link),
        },
        "real_send": False,
    }


def mask_app_access_link(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        pairs.append((key, "***" if key.lower() in {"token", "access_token"} else value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _create_app_access(store: LeoFTFakeStore, anamnese: Mapping[str, Any]) -> dict[str, Any]:
    anamnese_id = str(anamnese["anamnese_id"])
    current = store.app_access_links.get(anamnese_id)
    if current:
        return current
    token = "app_fake_" + secrets.token_urlsafe(32)
    access = {
        "app_url": LEOFT_APP_URL,
        "access_token": token,
        "access_link": f"{LEOFT_APP_URL}/acesso?token={token}",
        "created_at": _utc_iso(),
        "dry_run": True,
    }
    store.app_access_links[anamnese_id] = access
    return access


def _record_payment_status(
    store: LeoFTFakeStore,
    payment_id: str,
    *,
    status: str,
    payment: Mapping[str, Any],
    reason: str | None = None,
    anamnese: Mapping[str, Any] | None = None,
) -> None:
    store.payment_status[str(payment_id)] = {
        "payment_id": str(payment_id),
        "status": status,
        "reason": reason,
        "external_reference": str(payment.get("external_reference") or ""),
        "amount": str(payment.get("transaction_amount") or ""),
        "currency": str(payment.get("currency_id") or payment.get("currency") or ""),
        "anamnese_id": str((anamnese or {}).get("anamnese_id") or ""),
        "updated_at": _utc_iso(),
        "dry_run": True,
    }


def _safe_payer(payer: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    email = str(payer.get("email") or "").strip()
    name = str(payer.get("name") or payer.get("first_name") or "").strip()
    if email:
        safe["email"] = email
    if name:
        safe["name"] = name
    return safe


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("1"))
    except (InvalidOperation, ValueError):
        return None


def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def _require(value: Any, name: str) -> None:
    if not str(value or "").strip():
        raise LeoFTPaymentContractError(f"{name} is required")


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
