from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select

from app.services.meta.credential_crypto import decrypt_meta_secret, encrypt_meta_secret

MERCADO_PAGO_PROVIDER = "mercado_pago"
VALID_ENVIRONMENTS = frozenset({"test", "production"})


@dataclass(frozen=True)
class ResolvedPaymentCredentials:
    access_token: str
    webhook_secret: str
    environment: str
    source: str


async def get_payment_provider(db: Any, *, empresa_id: UUID | str, provider: str = MERCADO_PAGO_PROVIDER):
    from db.models import EmpresaPaymentProvider

    return await db.scalar(
        select(EmpresaPaymentProvider).where(
            EmpresaPaymentProvider.empresa_id == _uuid(empresa_id),
            EmpresaPaymentProvider.provider == provider,
        )
    )


async def resolve_mercado_pago_credentials(
    db: Any | None,
    *,
    empresa_id: UUID | str | None,
) -> ResolvedPaymentCredentials:
    if db is not None and empresa_id is not None:
        record = await get_payment_provider(db, empresa_id=empresa_id)
        if record is not None and bool(record.enabled):
            return ResolvedPaymentCredentials(
                access_token=_decrypt_optional(record.access_token_encrypted),
                webhook_secret=_decrypt_optional(record.webhook_secret_encrypted),
                environment=_environment(record.environment),
                source="database",
            )
    return ResolvedPaymentCredentials(
        access_token=str(os.getenv("MERCADO_PAGO_LEOFT_ACCESS_TOKEN") or "").strip(),
        webhook_secret=str(os.getenv("MERCADO_PAGO_LEOFT_WEBHOOK_SECRET") or "").strip(),
        environment=_environment(os.getenv("MERCADO_PAGO_LEOFT_ENVIRONMENT") or "test"),
        source="environment",
    )


async def save_mercado_pago_credentials(
    db: Any,
    *,
    empresa_id: UUID | str,
    enabled: bool,
    environment: str,
    access_token: str | None = None,
    webhook_secret: str | None = None,
):
    from db.models import EmpresaPaymentProvider

    normalized_environment = _environment(environment, strict=True)
    record = await get_payment_provider(db, empresa_id=empresa_id)
    current_access_token = bool(record and str(record.access_token_encrypted or "").strip())
    current_webhook_secret = bool(record and str(record.webhook_secret_encrypted or "").strip())
    next_access_token = current_access_token or bool(str(access_token or "").strip())
    next_webhook_secret = current_webhook_secret or bool(str(webhook_secret or "").strip())
    if enabled and not next_access_token:
        raise ValueError("access_token_required")
    if enabled and not next_webhook_secret:
        raise ValueError("webhook_secret_required")
    if record is None:
        record = EmpresaPaymentProvider(
            empresa_id=_uuid(empresa_id),
            provider=MERCADO_PAGO_PROVIDER,
        )
        db.add(record)
    record.enabled = bool(enabled)
    record.environment = normalized_environment
    if str(access_token or "").strip():
        record.access_token_encrypted = encrypt_meta_secret(str(access_token).strip())
    if str(webhook_secret or "").strip():
        record.webhook_secret_encrypted = encrypt_meta_secret(str(webhook_secret).strip())
    await db.commit()
    await db.refresh(record)
    return record


def serialize_mercado_pago_configuration(record: Any | None, *, webhook_url: str) -> dict[str, Any]:
    return {
        "provider": MERCADO_PAGO_PROVIDER,
        "configured": bool(record and record.access_token_encrypted and record.webhook_secret_encrypted),
        "enabled": bool(record.enabled) if record else False,
        "environment": _environment(record.environment if record else "test"),
        "access_token_configured": bool(record and record.access_token_encrypted),
        "webhook_secret_configured": bool(record and record.webhook_secret_encrypted),
        "webhook_url": webhook_url,
    }


def tenant_webhook_url(*, empresa_id: UUID | str, request: Any | None = None) -> str:
    path = f"/api/webhooks/mercado-pago/{_uuid(empresa_id)}"
    configured_base = str(
        os.getenv("AGENTOS_PUBLIC_BASE_URL")
        or os.getenv("PUBLIC_BASE_URL")
        or os.getenv("APP_BASE_URL")
        or ""
    ).strip().rstrip("/")
    if configured_base:
        return f"{configured_base}{path}"
    if request is not None:
        return f"{str(request.base_url).rstrip('/')}{path}"
    legacy = str(os.getenv("MERCADO_PAGO_LEOFT_NOTIFICATION_URL") or "").strip()
    legacy_parts = urlsplit(legacy)
    if legacy_parts.scheme and legacy_parts.netloc:
        return f"{legacy_parts.scheme}://{legacy_parts.netloc}{path}"
    return path


def _decrypt_optional(value: str | None) -> str:
    return decrypt_meta_secret(value) if str(value or "").strip() else ""


def _environment(value: Any, *, strict: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_ENVIRONMENTS:
        return normalized
    if strict:
        raise ValueError("environment deve ser 'test' ou 'production'")
    return "test"


def _uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
