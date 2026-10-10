from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import httpx

from app.services.payment_credentials import resolve_mercado_pago_credentials, tenant_webhook_url


class LeoFTCheckoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class LeoFTCheckout:
    reference: str
    url: str
    provider: str
    preference_id: str | None = None
    created: bool = False
    reused: bool = False


async def create_or_get_checkout(
    *,
    anamnese: dict[str, Any],
    empresa_id: str,
    lead_id: str,
    anamnese_id: str,
    config: Mapping[str, Any],
    payer: Mapping[str, Any] | None = None,
    db: Any | None = None,
) -> LeoFTCheckout:
    """Create one Mercado Pago preference per submitted anamnese.

    The caller must hold the lead row lock while invoking this function. The
    checkout data is stored in the canonical anamnese JSON by the caller.
    """
    existing = anamnese.get("checkout")
    if isinstance(existing, Mapping) and str(existing.get("url") or "").strip():
        return LeoFTCheckout(
            reference=str(existing.get("reference") or ""),
            url=str(existing["url"]),
            provider=str(existing.get("provider") or "mercado_pago"),
            preference_id=str(existing.get("preference_id") or "") or None,
            reused=True,
        )

    provider = str(config.get("checkout_provider") or "mercado_pago").strip().lower()
    if provider != "mercado_pago":
        raise LeoFTCheckoutError("checkout_provider_nao_suportado")
    credentials = await resolve_mercado_pago_credentials(db, empresa_id=empresa_id)
    access_token = credentials.access_token
    if not access_token:
        raise LeoFTCheckoutError("mercado_pago_access_token_ausente")

    reference = _stable_reference(empresa_id, lead_id, anamnese_id)
    amount = _amount(config)
    currency = str(config.get("checkout_currency") or "BRL").strip().upper()[:3]
    title = str(config.get("checkout_item_title") or config.get("oferta_nome") or "Método Híbrido LeoFT").strip()[:120]
    payload: dict[str, Any] = {
        "items": [{"title": title, "quantity": 1, "unit_price": float(amount), "currency_id": currency}],
        "external_reference": reference,
        "metadata": {
            "empresa_id": str(empresa_id),
            "lead_id": str(lead_id),
            "anamnese_id": str(anamnese_id),
            "checkout_reference": reference,
            "product_id": str(config.get("product_id") or config.get("offer_code") or "leoft_metodo_hibrido")[:160],
            "offer_code": str(config.get("offer_code") or config.get("product_id") or "leoft_metodo_hibrido")[:160],
            "product_type": str(config.get("product_type") or "PERSONALIZED_PROGRAM")[:80],
            "fulfillment_type": str(config.get("fulfillment_type") or "PROGRAM_AND_APP")[:80],
        },
        "payment_methods": _payment_methods_payload(config),
    }
    if payer:
        safe_payer = {k: str(v)[:160] for k, v in payer.items() if k in {"name", "email", "phone"} and v}
        if safe_payer:
            payload["payer"] = safe_payer
    if credentials.source == "database":
        notification_url = tenant_webhook_url(empresa_id=empresa_id)
    else:
        notification_url = str(config.get("checkout_notification_url") or os.getenv("MERCADO_PAGO_LEOFT_NOTIFICATION_URL") or "").strip()
    if notification_url:
        payload["notification_url"] = notification_url

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(
                "https://api.mercadopago.com/checkout/preferences",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "X-Idempotency-Key": reference,
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise LeoFTCheckoutError("mercado_pago_request_failed") from exc
    body: dict[str, Any] = {}
    try:
        body = response.json() if response.content else {}
    except (ValueError, TypeError):
        body = {}
    if response.status_code >= 400:
        detail = str(body.get("message") or body.get("error") or body.get("cause") or "").strip()
        detail = " ".join(detail.split())[:240]
        reason = f"mercado_pago_http_{response.status_code}"
        if detail:
            reason = f"{reason}:{detail}"
        raise LeoFTCheckoutError(reason)
    url = str(body.get("init_point") or body.get("sandbox_init_point") or "").strip()
    if not url:
        raise LeoFTCheckoutError("mercado_pago_checkout_url_ausente")
    return LeoFTCheckout(
        reference=reference,
        url=url,
        provider="mercado_pago",
        preference_id=str(body.get("id") or "") or None,
        created=True,
    )


def _stable_reference(empresa_id: str, lead_id: str, anamnese_id: str) -> str:
    digest = hashlib.sha256(f"{empresa_id}:{lead_id}:{anamnese_id}".encode()).hexdigest()[:32]
    return f"leoft_chk_{digest}"


def _payment_methods_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = config.get("payment_methods")
    payment_methods = dict(raw) if isinstance(raw, Mapping) else {}
    payment_methods["excluded_payment_types"] = [{"id": "ticket"}]
    return payment_methods


def _amount(config: Mapping[str, Any]) -> Decimal:
    raw = config.get("checkout_amount") or os.getenv("LEOFT_CHECKOUT_AMOUNT") or "97"
    try:
        amount = Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise LeoFTCheckoutError("checkout_amount_invalido") from exc
    if amount <= 0:
        raise LeoFTCheckoutError("checkout_amount_invalido")
    return amount
