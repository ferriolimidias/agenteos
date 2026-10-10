from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conexao


META_PROVIDER = "meta_whatsapp"
EVOLUTION_PROVIDER = "evolution"
WHATSAPP_CHANNEL = "whatsapp"

_CONNECTED_STATUSES = {"ativo", "connected", "open", "online", "conectado"}
_PENDING_STATUSES = {"pending", "pending_session", "exchanging_code", "token_obtained", "discovering_assets", "number_selection_required", "connecting", "qrcode", "qr", "pairing"}
_DEGRADED_STATUSES = {"attention", "retryable_error", "token_expired", "revoked", "reauth_required"}
_ERROR_STATUSES = {"error", "failed", "invalid", "blocked"}


@dataclass(frozen=True)
class OperationalConnection:
    id: str
    provider: str
    channel: str
    status: str
    nome_instancia: str | None = None
    display_number: str | None = None
    waba_id: str | None = None
    phone_number_id: str | None = None
    metadata_json: dict[str, Any] | None = None
    atualizado_em: datetime | None = None


def _metadata(conexao: Any) -> dict[str, Any]:
    metadata = getattr(conexao, "metadata_json", None)
    return metadata if isinstance(metadata, dict) else {}


def normalize_provider(conexao: Any) -> str:
    raw_provider = str(getattr(conexao, "provider", "") or "").strip().lower()
    raw_tipo = str(getattr(conexao, "tipo", "") or "").strip().lower()
    if raw_provider in {"meta", "meta_official", "meta_whatsapp", "whatsapp_meta_official"} or raw_tipo.endswith("meta"):
        return META_PROVIDER
    if raw_provider in {"evolution", "evolution_legacy"} or "evolution" in raw_tipo:
        return EVOLUTION_PROVIDER
    return raw_provider or "unknown"


def normalize_channel(conexao: Any) -> str:
    raw_channel = str(getattr(conexao, "channel", "") or "").strip().lower()
    provider = normalize_provider(conexao)
    if raw_channel:
        return raw_channel
    if provider in {META_PROVIDER, EVOLUTION_PROVIDER}:
        return WHATSAPP_CHANNEL
    return "unknown"


def normalize_status(status: str | None) -> str:
    raw = str(status or "").strip().lower()
    if raw in _CONNECTED_STATUSES:
        return "connected"
    if raw in _PENDING_STATUSES:
        return "pending"
    if raw in _DEGRADED_STATUSES:
        return "degraded"
    if raw in _ERROR_STATUSES:
        return "error"
    if raw in {"disconnected", "inactive", "desconectado", "offline"}:
        return "disconnected"
    return raw or "disconnected"


def _is_meta_usable(conexao: Any) -> bool:
    metadata = _metadata(conexao)
    return bool(
        getattr(conexao, "waba_id", None)
        and getattr(conexao, "phone_number_id", None)
        and (
            metadata.get("encrypted_access_token")
            or metadata.get("token_encrypted")
            or metadata.get("access_token_encrypted")
            or (getattr(conexao, "credenciais", None) or {}).get("encrypted_access_token")
        )
    )


def _serialize_connection(conexao: Any) -> dict[str, Any]:
    provider = normalize_provider(conexao)
    metadata = _metadata(conexao)
    return {
        "connection_id": str(getattr(conexao, "id", "")),
        "channel": normalize_channel(conexao),
        "provider": provider,
        "provider_label": "API Oficial da Meta" if provider == META_PROVIDER else "Evolution - legado",
        "status": normalize_status(getattr(conexao, "status", None)),
        "display_name": getattr(conexao, "nome_instancia", None),
        "display_phone_number": getattr(conexao, "display_number", None) or metadata.get("display_phone_number"),
        "coexistence": bool(metadata.get("coexistence")),
        "capabilities": metadata.get("capabilities") or {},
        "last_sync_at": metadata.get("last_synced_at") or metadata.get("last_sync_at") or getattr(conexao, "atualizado_em", None),
        "waba_configured": bool(getattr(conexao, "waba_id", None)),
        "phone_number_configured": bool(getattr(conexao, "phone_number_id", None)),
    }


def resolve_whatsapp_operational_status_from_connections(conexoes: Iterable[Any]) -> dict[str, Any]:
    candidates = [
        conexao
        for conexao in conexoes
        if normalize_channel(conexao) == WHATSAPP_CHANNEL and normalize_provider(conexao) in {META_PROVIDER, EVOLUTION_PROVIDER}
    ]
    meta = [c for c in candidates if normalize_provider(c) == META_PROVIDER]
    evolution = [c for c in candidates if normalize_provider(c) == EVOLUTION_PROVIDER]

    meta_connected = [c for c in meta if normalize_status(getattr(c, "status", None)) == "connected" and _is_meta_usable(c)]
    if meta_connected:
        return _result(meta_connected[0], "connected", "meta_connected")

    meta_non_disconnected = [c for c in meta if normalize_status(getattr(c, "status", None)) in {"pending", "degraded", "error"} or _is_meta_usable(c)]
    if meta_non_disconnected:
        chosen = meta_non_disconnected[0]
        status = normalize_status(getattr(chosen, "status", None))
        if status == "connected" and not _is_meta_usable(chosen):
            status = "degraded"
        return _result(chosen, status, "meta_not_ready")

    evolution_connected = [c for c in evolution if normalize_status(getattr(c, "status", None)) == "connected"]
    if evolution_connected:
        return _result(evolution_connected[0], "connected", "evolution_legacy_connected", warning="Integração legada Evolution.")

    if evolution:
        return _result(evolution[0], "disconnected", "evolution_legacy_disconnected", warning="Integração legada Evolution desconectada.")

    return {
        "status": "not_configured",
        "online": False,
        "conectado": False,
        "provider": None,
        "provider_label": None,
        "channel": WHATSAPP_CHANNEL,
        "connection_id": None,
        "conexao_id": None,
        "display_name": None,
        "display_phone_number": None,
        "display_number": None,
        "coexistence": False,
        "capabilities": {},
        "last_sync_at": None,
        "ultima_atualizacao": None,
        "warning": None,
        "reason": "no_whatsapp_connection",
        "detail": "WhatsApp ainda não conectado para esta empresa.",
    }


def _result(conexao: Any, status: str, reason: str, warning: str | None = None) -> dict[str, Any]:
    serialized = _serialize_connection(conexao)
    connected = status == "connected"
    detail = None
    if reason == "meta_not_ready":
        detail = "Conexão oficial Meta registrada, mas ainda exige atenção."
    elif reason == "evolution_legacy_disconnected":
        detail = "Conexão Evolution legada desconectada."
    result = {
        **serialized,
        "status": status,
        "online": connected,
        "conectado": connected,
        "connection_id": serialized["connection_id"],
        "conexao_id": serialized["connection_id"],
        "display_number": serialized["display_phone_number"],
        "ultima_atualizacao": serialized["last_sync_at"],
        "warning": warning,
        "reason": reason,
        "detail": detail,
    }
    return result


async def resolve_whatsapp_operational_status(db: AsyncSession, empresa_id: str) -> dict[str, Any]:
    empresa_uuid = empresa_id
    try:
        import uuid

        empresa_uuid = uuid.UUID(str(empresa_id))
    except (TypeError, ValueError):
        empresa_uuid = empresa_id
    result = await db.execute(
        select(Conexao)
        .where(
            Conexao.empresa_id == empresa_uuid,
            func.coalesce(func.lower(Conexao.channel), WHATSAPP_CHANNEL) == WHATSAPP_CHANNEL,
            func.lower(func.coalesce(Conexao.provider, cast(Conexao.tipo, String))).in_(
                ["meta", "meta_official", META_PROVIDER, "whatsapp_meta_official", "evolution", "evolution_legacy"]
            ),
        )
        .order_by(Conexao.criado_em.desc())
    )
    return resolve_whatsapp_operational_status_from_connections(result.scalars().all())
