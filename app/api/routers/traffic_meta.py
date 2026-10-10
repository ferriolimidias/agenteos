from __future__ import annotations

import base64
import binascii
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_tenant_access
from app.services.meta.conversion_credentials import MetaCredentialResolutionError
from app.services.meta.business_messaging_conversions import (
    BUSINESS_MESSAGING_TRIGGER_SOURCE_OPTIONS,
    DEFAULT_BUSINESS_MESSAGING_ALLOWED_SOURCES,
    conversions_status,
    save_conversion_token,
    setup_business_messaging_conversions,
)
from app.services.meta.embedded_signup import mask_meta_id
from app.services.meta.marketing_api import MetaMarketingApiClient, graph_post_data, normalize_ad_account_id, sanitize_graph_error
from app.services.meta.meta_tenant_provisioner import audit_business_meta_surfaces, audit_meta_tenant, provision_meta_conversion_infrastructure
from app.services.meta.marketing_credentials import (
    ADMIN_MARKETING_SOURCE,
    EMBEDDED_SIGNUP_TENANT,
    MetaMarketingCapability,
    MetaMarketingCredentialResolver,
    marketing_admin_status,
)
from app.services.meta.marketing_readiness import marketing_ads_readiness
from app.services.meta.settings import meta_settings
from app.services.meta.traffic_campaign_builder import (
    CAMPAIGN_OBJECTIVE,
    CONVERSION_CAMPAIGN_OBJECTIVE,
    TrafficCampaignValidationError,
    brl_to_meta_cents,
    build_ad_payload,
    build_adset_payload,
    build_brazil_targeting,
    build_campaign_payload,
    build_creative_payload,
    build_radius_targeting,
    build_targeting,
    creation_idempotency_key,
    generated_object_name,
)
from app.services.media_storage import persist_traffic_image_bytes, resolve_storage_path
from app.services.meta.ad_copy_generator import (
    DEFAULT_AD_COPY_VARIATION_COUNT,
    AdCopyGenerationError,
    generate_ad_copy,
    generate_image_ad_copy,
)
from app.services.meta.image_validation import ImageValidationError, validate_image_bytes
from app.services.meta.waba_dataset import is_generic_dataset_name
from app.services.meta.optimization_eligibility import get_meta_optimization_eligibility
from db.database import get_db
from db.models import (
    BusinessMessagingConversionMapping,
    Conexao,
    CRMLead,
    Empresa,
    MetaConversionEvent,
    TipoConexao,
    TrafficMetaConfig,
    TrafficMetaCampaignCreation,
    Usuario,
    is_admin_empresa_role,
    is_root_admin_email,
    is_super_admin_role,
)

router = APIRouter(prefix="/empresas/{empresa_id}/traffic/meta", tags=["Traffic Meta"])


class TrafficMetaConfigUpdate(BaseModel):
    business_id: str | None = None
    ad_account_id: str | None = None
    ad_account_validation_id: str | None = None
    page_id: str | None = None
    instagram_account_id: str | None = None
    whatsapp_connection_id: str | None = None
    dataset_id: str | None = None
    status: str = Field(default="draft", max_length=40)


class TrafficMetaCampaignCreateRequest(BaseModel):
    campaign_name: str
    daily_budget_brl: str
    location_type: str = Field(default="brazil", pattern="^(brazil|radius)$")
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    page_id: str | None = None
    whatsapp_connection_id: str | None = Field(default=None, max_length=80)
    business_goal: str | None = Field(default=None, max_length=40)
    conversion_goal_key: str | None = Field(default=None, max_length=40)


class TrafficMetaFullCampaignCreateRequest(BaseModel):
    daily_budget_brl: str
    location_type: str = Field(default="brazil", pattern="^(brazil|radius)$")
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    location_input: str | None = Field(default=None, max_length=240)
    location_display_name: str | None = Field(default=None, max_length=240)
    gender_mode: str = Field(default="all", pattern="^(all|women|men)$")
    page_id: str | None = None
    whatsapp_connection_id: str | None = Field(default=None, max_length=80)
    business_goal: str | None = Field(default=None, max_length=40)
    conversion_goal_key: str | None = Field(default=None, max_length=40)
    ads: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    idempotency_key: str | None = Field(default=None, max_length=160)
    resume_creation_id: str | None = Field(default=None, max_length=80)


class TrafficMetaAdCopyGenerateRequest(BaseModel):
    creative_brief: str = Field(min_length=1, max_length=2000)
    objective: str | None = Field(default=None, max_length=80)
    destination: str = Field(default="whatsapp", max_length=40)
    variation_count: int = Field(default=DEFAULT_AD_COPY_VARIATION_COUNT, ge=1, le=6)


class TrafficMetaImageCopyGenerateRequest(BaseModel):
    image_data_url: str = Field(min_length=32, max_length=14 * 1024 * 1024)
    filename: str | None = Field(default=None, max_length=240)
    objective: str | None = Field(default=None, max_length=80)
    destination: str = Field(default="whatsapp", max_length=40)


class TrafficMetaBatchCreateRequest(BaseModel):
    ads: list[dict[str, Any]] = Field(min_length=1, max_length=5)
    idempotency_key: str | None = Field(default=None, max_length=160)


class TrafficMetaDraftCreateRequest(BaseModel):
    daily_budget_brl: str = "0"
    location_type: str = Field(default="brazil", pattern="^(brazil|radius)$")
    location_input: str | None = Field(default=None, max_length=240)
    location_display_name: str | None = Field(default=None, max_length=240)
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float | None = None
    gender_mode: str = Field(default="all", pattern="^(all|women|men)$")
    draft_session_id: str | None = Field(default=None, max_length=160)
    page_id: str | None = None
    whatsapp_connection_id: str | None = Field(default=None, max_length=80)
    ads: list[dict[str, Any]] = Field(min_length=1, max_length=5)


class TrafficMetaCopyUpdateRequest(BaseModel):
    primary_text: str = Field(min_length=1, max_length=5000)
    headline: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=240)
    copy_source: str = Field(default="HUMAN", pattern="^(AI|HUMAN)$")


class ConversionTokenRequest(BaseModel):
    access_token: str = Field(min_length=20, max_length=4096)


class ConversionMappingSourcesRequest(BaseModel):
    allowed_sources: list[str] = Field(default_factory=list, max_length=8)
    test_event_code: str | None = Field(default=None, max_length=120)


TRIGGER_LABELS = {
    "ctwa_lead_created": "Novo lead CTWA",
    "checkout_created": "Checkout criado",
    "payment_confirmed": "Pagamento confirmado",
    "mercado_pago_approved": "Pagamento confirmado",
}

STATUS_LABELS = {
    "sent": "Enviado",
    "failed": "Erro",
    "pending": "Pendente",
    "dry_run": "Dry run",
    "blocked": "Bloqueado",
    "not_applicable": "Ignorado",
    "skipped": "Ignorado",
}

MODE_LABELS = {
    "disabled": "Desativado",
    "dry_run": "Dry run",
    "test_event": "Teste",
    "live": "Live",
}


def _settings_flags() -> dict[str, bool]:
    settings = meta_settings()
    return {
        "meta_write_enabled": settings.marketing_write_enabled,
        "campaign_create_enabled": settings.marketing_campaign_create_enabled,
        "adset_create_enabled": settings.marketing_adset_create_enabled,
        "creative_create_enabled": settings.marketing_creative_create_enabled,
        "ad_create_enabled": settings.marketing_ad_create_enabled,
    }


def _uuid_or_400(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} inválido.") from exc


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _payload_fields(payload: BaseModel) -> set[str]:
    return set(getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set())))


def _conversion_trigger_label(event: MetaConversionEvent, mapping: BusinessMessagingConversionMapping | None) -> str:
    trigger_ref = str(getattr(event, "trigger_ref", "") or getattr(mapping, "trigger_ref", "") or "").strip()
    if trigger_ref in TRIGGER_LABELS:
        return TRIGGER_LABELS[trigger_ref]
    trigger_type = str(getattr(event, "trigger_type", "") or getattr(mapping, "trigger_type", "") or "").strip()
    if trigger_type == "crm_stage_changed":
        return "Etapa CRM"
    if trigger_type == "tag_applied":
        return "Tag aplicada"
    if trigger_type == "payment_confirmed":
        return "Pagamento confirmado"
    if trigger_type == "system_event":
        return trigger_ref or "Evento do sistema"
    return trigger_ref or trigger_type or "Evento"


def _conversion_status_label(event: MetaConversionEvent) -> str:
    status = str(getattr(event, "status", "") or "").strip()
    label = STATUS_LABELS.get(status, status or "Desconhecido")
    error = str(getattr(event, "error_message", "") or "").strip()
    if status == "not_applicable" and error == "no_ctwa_attribution":
        return "Ignorado - sem atribuição CTWA"
    if status == "blocked" and error:
        return f"{label} - {error[:80]}"
    if status == "failed" and error:
        return f"{label} - {error[:80]}"
    return label


def _conversion_mode_label(event: MetaConversionEvent, mapping: BusinessMessagingConversionMapping | None) -> str:
    send_mode = str(getattr(mapping, "send_mode", "") or "").strip().lower()
    if send_mode in MODE_LABELS:
        return MODE_LABELS[send_mode]
    status = str(getattr(event, "status", "") or "").strip()
    if status in {"sent", "failed", "pending"}:
        return "Live"
    if status == "dry_run" and getattr(event, "test_event_code", None):
        return "Teste"
    if status == "dry_run":
        return "Dry run"
    return "Configurado"


def _serialize_conversion_history_event(
    event: MetaConversionEvent,
    lead: CRMLead | None,
    mapping: BusinessMessagingConversionMapping | None,
) -> dict[str, Any]:
    response = getattr(event, "response_preview", None) if isinstance(getattr(event, "response_preview", None), dict) else {}
    return {
        "id": str(event.id),
        "event_name": str(event.event_name or ""),
        "lead": {
            "id": str(getattr(lead, "id", "") or getattr(event, "lead_id", "") or "") or None,
            "name": str(getattr(lead, "nome_contato", "") or "Lead").strip()[:120],
        },
        "trigger": {
            "type": str(getattr(event, "trigger_type", "") or getattr(mapping, "trigger_type", "") or ""),
            "ref": str(getattr(event, "trigger_ref", "") or getattr(mapping, "trigger_ref", "") or ""),
            "label": _conversion_trigger_label(event, mapping),
            "source": str(getattr(event, "trigger_source", "") or getattr(event, "source", "") or ""),
        },
        "created_at": event.criado_em.isoformat() if getattr(event, "criado_em", None) else None,
        "mode": _conversion_mode_label(event, mapping),
        "status": str(event.status or ""),
        "status_label": _conversion_status_label(event),
        "meta": {
            "status_code": response.get("status_code"),
            "delivery": STATUS_LABELS.get(str(event.status or ""), str(event.status or "")),
            "credential_source": str(getattr(event, "credential_source", "") or "") or None,
            "waba_id_masked": getattr(event, "waba_id_masked", None),
            "dataset_id_masked": getattr(event, "dataset_id_masked", None),
            "ctwa_present": bool(getattr(event, "ctwa_present", False)),
        },
    }


def _ensure_ad_account_binding_admin(user: Usuario, empresa_id: uuid.UUID) -> None:
    if is_root_admin_email(user.email) or is_super_admin_role(user.role):
        return
    if is_admin_empresa_role(user.role) and user.empresa_id == empresa_id:
        return
    raise HTTPException(status_code=403, detail="Acesso administrativo necessário para vincular conta de anúncios.")


def _permission_flags(body: dict[str, Any]) -> dict[str, bool]:
    data = body.get("data") if isinstance(body.get("data"), list) else []
    granted = {
        str(item.get("permission") or "").strip()
        for item in data
        if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "granted"
    }
    return {cap.value: cap.value in granted for cap in MetaMarketingCapability}


def _serialize_config(config: TrafficMetaConfig | None) -> dict[str, Any]:
    if not config:
        return {
            "configured": False,
            "status": "missing",
            "business_id": None,
            "ad_account_id": None,
            "page_id": None,
            "instagram_account_id": None,
            "whatsapp_connection_id": None,
            "waba_id": None,
            "phone_number_id": None,
            "dataset_id": None,
            "dataset_name": None,
            "dataset_name_is_generic": False,
        }
    return {
        "configured": True,
        "id": str(config.id),
        "empresa_id": str(config.empresa_id),
        "business_id": config.business_id,
        "ad_account_id": config.ad_account_id,
        "page_id": config.page_id,
        "instagram_account_id": config.instagram_account_id,
        "whatsapp_connection_id": str(config.whatsapp_connection_id) if config.whatsapp_connection_id else None,
        "waba_id": mask_meta_id(config.waba_id),
        "phone_number_id": mask_meta_id(config.phone_number_id),
        "dataset_id": mask_meta_id(config.dataset_id),
        "dataset_name": None,
        "dataset_name_is_generic": False,
        "status": config.status,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def _serialize_whatsapp(connection: Conexao | None) -> dict[str, Any]:
    if not connection:
        return {"detected": False}
    metadata = dict(connection.metadata_json or {})
    dataset_id = metadata.get("dataset_id") or metadata.get("meta_dataset_id")
    dataset_name = str(metadata.get("dataset_name") or metadata.get("meta_dataset_name") or "").strip() or None
    verified_name = str(metadata.get("verified_name") or metadata.get("meta_verified_name") or connection.nome_instancia or "").strip() or None
    display_number = str(connection.display_number or metadata.get("display_phone_number") or metadata.get("phone_number") or "").strip() or None
    return {
        "detected": True,
        "connection_id": str(connection.id),
        "label": verified_name or connection.nome_instancia,
        "name": connection.nome_instancia,
        "channel": connection.channel,
        "provider": connection.provider,
        "status": connection.status,
        "verified_name": verified_name,
        "display_number": mask_meta_id(display_number),
        "waba_id": mask_meta_id(connection.waba_id or metadata.get("meta_waba_id")),
        "phone_number_id": mask_meta_id(connection.phone_number_id or metadata.get("meta_phone_number_id")),
        "dataset_id": mask_meta_id(dataset_id),
        "dataset_name": dataset_name,
        "dataset_name_is_generic": is_generic_dataset_name(dataset_name),
    }


async def _get_config(session: AsyncSession, empresa_id: uuid.UUID) -> TrafficMetaConfig | None:
    return (
        await session.execute(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == empresa_id))
    ).scalars().first()


async def _official_whatsapp(session: AsyncSession, empresa_id: uuid.UUID, connection_id: uuid.UUID | None = None) -> Conexao | None:
    query = select(Conexao).where(
        Conexao.empresa_id == empresa_id,
        Conexao.tipo == TipoConexao.META,
        Conexao.provider == "meta_official",
    )
    if connection_id:
        query = query.where(Conexao.id == connection_id)
    return (await session.execute(query.order_by(Conexao.criado_em.asc(), Conexao.id.asc()).limit(1))).scalars().first()


async def _official_whatsapp_connections(session: AsyncSession, empresa_id: uuid.UUID) -> list[Conexao]:
    result = await session.execute(
        select(Conexao)
        .where(
            Conexao.empresa_id == empresa_id,
            Conexao.tipo == TipoConexao.META,
            Conexao.provider == "meta_official",
        )
        .order_by(Conexao.criado_em.asc(), Conexao.id.asc())
    )
    return list(result.scalars().all())


def _is_active_meta_whatsapp_connection(connection: Conexao) -> bool:
    status = str(getattr(connection, "status", "") or "").strip().lower()
    return (
        getattr(connection, "tipo", None) == TipoConexao.META
        and str(getattr(connection, "channel", "") or "").strip().lower() == "whatsapp"
        and str(getattr(connection, "provider", "") or "").strip().lower() == "meta_official"
        and status in {"active", "ativo", "connected", "conectado"}
    )


def _connection_phone_number_id(connection: Conexao) -> str | None:
    metadata = dict(getattr(connection, "metadata_json", None) or {})
    return _clean(getattr(connection, "phone_number_id", None) or metadata.get("meta_phone_number_id"))


def _connection_waba_id(connection: Conexao) -> str | None:
    metadata = dict(getattr(connection, "metadata_json", None) or {})
    return _clean(getattr(connection, "waba_id", None) or metadata.get("meta_waba_id"))


def _connection_display_number(connection: Conexao) -> str | None:
    metadata = dict(getattr(connection, "metadata_json", None) or {})
    return _clean(getattr(connection, "display_number", None) or metadata.get("display_phone_number") or metadata.get("phone_number"))


def _connection_dataset_id(connection: Conexao) -> str | None:
    metadata = dict(getattr(connection, "metadata_json", None) or {})
    return _clean(metadata.get("dataset_id") or metadata.get("meta_dataset_id"))


def _digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _page_instagram_id(page: dict[str, Any] | None) -> str | None:
    if not page:
        return None
    for key in ("instagram_business_account", "connected_instagram_account"):
        value = page.get(key)
        if isinstance(value, dict) and _clean(value.get("id")):
            return _clean(value.get("id"))
    return None


def _serialize_page(page: dict[str, Any], *, selected: bool = False) -> dict[str, Any]:
    instagram = page.get("instagram_business_account") if isinstance(page.get("instagram_business_account"), dict) else page.get("connected_instagram_account")
    return {
        "id": str(page.get("id") or ""),
        "name": page.get("name"),
        "selected": selected,
        "instagram_account_id": str((instagram or {}).get("id") or "") or None if isinstance(instagram, dict) else None,
        "instagram_username": (instagram or {}).get("username") if isinstance(instagram, dict) else None,
        "id_masked": mask_meta_id(page.get("id")),
    }


def _current_configured_ad_account_id(config: TrafficMetaConfig) -> str:
    account_id = normalize_ad_account_id(config.ad_account_id)
    if not account_id:
        raise HTTPException(status_code=409, detail="Conta de anúncios não vinculada para esta empresa.")
    return account_id


def _validate_campaign_whatsapp_connection(connection: Conexao | None, empresa_id: uuid.UUID) -> Conexao:
    if not connection or connection.empresa_id != empresa_id:
        raise HTTPException(status_code=404, detail="Conexão WhatsApp não encontrada para esta empresa.")
    if not _is_active_meta_whatsapp_connection(connection):
        raise HTTPException(status_code=409, detail="Selecione uma conexão WhatsApp Oficial ativa.")
    if not _connection_phone_number_id(connection):
        raise HTTPException(status_code=409, detail="WhatsApp Oficial sem Phone Number ID válido.")
    if not _connection_waba_id(connection):
        raise HTTPException(status_code=409, detail="WhatsApp Oficial sem WABA válido.")
    return connection


async def _active_campaign_whatsapp_connections(session: AsyncSession, empresa_id: uuid.UUID) -> list[Conexao]:
    return [
        connection
        for connection in await _official_whatsapp_connections(session, empresa_id)
        if _is_active_meta_whatsapp_connection(connection)
    ]


async def _resolve_campaign_whatsapp_for_write(
    session: AsyncSession,
    empresa_id: uuid.UUID,
    selected_connection_id: uuid.UUID | None,
) -> Conexao:
    if selected_connection_id:
        connection = await _official_whatsapp(session, empresa_id, selected_connection_id)
        return _validate_campaign_whatsapp_connection(connection, empresa_id)
    connections = await _active_campaign_whatsapp_connections(session, empresa_id)
    if not connections:
        raise HTTPException(status_code=409, detail="Conecte um WhatsApp Oficial ativo antes de criar campanha.")
    if len(connections) > 1:
        raise HTTPException(status_code=409, detail="Selecione qual WhatsApp Oficial será usado nesta campanha.")
    return connections[0]


async def _resolve_campaign_assets_for_write(
    session: AsyncSession,
    empresa_id: uuid.UUID,
    *,
    config: TrafficMetaConfig,
    page_id: str | None,
    whatsapp_connection_id: uuid.UUID | None,
    client: MetaMarketingApiClient,
    ad_account: dict[str, Any],
) -> dict[str, Any]:
    ad_account_id = _current_configured_ad_account_id(config)
    pages = await client.list_pages()
    selected_page_id = _clean(page_id or config.page_id)
    if not selected_page_id:
        raise HTTPException(status_code=409, detail="Selecione uma Página Facebook antes de criar campanha.")
    page = next((item for item in pages if str(item.get("id") or "").strip() == selected_page_id), None)
    if not page:
        raise HTTPException(status_code=409, detail="Página Facebook não disponível para esta credencial.")
    page_instagram_id = _page_instagram_id(page)
    requested_instagram_id = _clean(config.instagram_account_id)
    if requested_instagram_id and requested_instagram_id != page_instagram_id:
        raise HTTPException(status_code=409, detail="Instagram configurado não pertence à Página Facebook selecionada.")

    whatsapp = await _resolve_campaign_whatsapp_for_write(session, empresa_id, whatsapp_connection_id)
    await _validate_page_whatsapp_compatibility(client, ad_account_id, whatsapp, selected_page_id)

    business = ad_account.get("business") if isinstance(ad_account.get("business"), dict) else {}
    return {
        "ad_account_id": ad_account_id,
        "business_id": _clean((business or {}).get("id") or config.business_id),
        "page_id": selected_page_id,
        "instagram_account_id": page_instagram_id,
        "whatsapp_connection_id": str(whatsapp.id),
        "phone_number_id": _connection_phone_number_id(whatsapp),
        "whatsapp_phone_number": _connection_display_number(whatsapp),
        "waba_id": _connection_waba_id(whatsapp),
        "dataset_id": _clean(config.dataset_id or _connection_dataset_id(whatsapp)),
        "page": page,
        "whatsapp": whatsapp,
        "ad_account": ad_account,
    }


async def _client(
    session: AsyncSession,
    empresa_id: uuid.UUID,
    capability: MetaMarketingCapability,
    *,
    ad_account_id: str | None = None,
    allow_admin_fallback: bool = False,
) -> tuple[MetaMarketingApiClient, dict[str, Any]]:
    credential = await MetaMarketingCredentialResolver().resolve(
        session,
        empresa_id,
        capability,
        ad_account_id=ad_account_id,
        allow_admin_fallback=allow_admin_fallback,
    )
    return (
        MetaMarketingApiClient(
            credential.access_token,
            graph_version=credential.graph_version,
            read_only=credential.read_only,
        ),
        credential.sanitized_metadata(),
    )


def _raise_marketing_read_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    code = str(getattr(exc, "code", "") or exc).strip()
    if code in {
        "MARKETING_ASSET_OUT_OF_SCOPE",
        "MARKETING_ACCOUNT_OUT_OF_SCOPE",
        "ADMIN_MARKETING_BUSINESS_BINDING_REQUIRED",
    }:
        raise HTTPException(status_code=403, detail="Ativo Meta fora do escopo autorizado para esta empresa.") from exc
    raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/config")
async def get_traffic_meta_config(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    config = await _get_config(db, empresa_uuid)
    whatsapp = await _official_whatsapp(db, empresa_uuid, config.whatsapp_connection_id if config else None)
    whatsapp_connections = await _official_whatsapp_connections(db, empresa_uuid)
    return {
        "marketing_graph_version": meta_settings().marketing_graph_api_version,
        "config": _serialize_config(config),
        "official_whatsapp": _serialize_whatsapp(whatsapp),
        "official_whatsapps": [_serialize_whatsapp(item) for item in whatsapp_connections],
        "active_official_whatsapps": [_serialize_whatsapp(item) for item in whatsapp_connections if _is_active_meta_whatsapp_connection(item)],
        "admin_fallback": marketing_admin_status(empresa_uuid),
        **_settings_flags(),
    }


@router.put("/config")
async def upsert_traffic_meta_config(
    empresa_id: str,
    payload: TrafficMetaConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    fields = _payload_fields(payload)
    config = await _get_config(db, empresa_uuid)
    if not config:
        config = TrafficMetaConfig(empresa_id=empresa_uuid)
        db.add(config)

    connection_uuid = (
        _uuid_or_400(payload.whatsapp_connection_id, "whatsapp_connection_id")
        if "whatsapp_connection_id" in fields and payload.whatsapp_connection_id
        else None
    )
    if "whatsapp_connection_id" in fields:
        whatsapp = await _official_whatsapp(db, empresa_uuid, connection_uuid) if connection_uuid else await _official_whatsapp(db, empresa_uuid)
    elif not config.whatsapp_connection_id:
        whatsapp = await _official_whatsapp(db, empresa_uuid)
    else:
        whatsapp = await _official_whatsapp(db, empresa_uuid, config.whatsapp_connection_id)
    if "whatsapp_connection_id" in fields and connection_uuid and not whatsapp:
        raise HTTPException(status_code=404, detail="Conexão WhatsApp oficial não encontrada para esta empresa.")

    normalized_ad_account_id = normalize_ad_account_id(payload.ad_account_id) if "ad_account_id" in fields and payload.ad_account_id else None
    validated_account: dict[str, Any] | None = None
    if "ad_account_id" in fields and normalized_ad_account_id:
        normalized_validation_id = normalize_ad_account_id(payload.ad_account_validation_id)
        if normalized_validation_id != normalized_ad_account_id:
            raise HTTPException(status_code=400, detail="Valide a conta de anúncios antes de salvar.")
        try:
            client, _meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ)
            validated_account = await client.validate_ad_account(normalized_ad_account_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc
        existing = (
            await db.execute(
                select(TrafficMetaConfig).where(
                    TrafficMetaConfig.ad_account_id == normalized_ad_account_id,
                    TrafficMetaConfig.empresa_id != empresa_uuid,
                )
            )
        ).scalars().first()
        if existing:
            raise HTTPException(status_code=409, detail="Ad Account já vinculada a outra empresa.")
    metadata = dict(getattr(whatsapp, "metadata_json", None) or {}) if whatsapp else {}
    if "ad_account_id" in fields:
        config.ad_account_id = normalized_ad_account_id
        business = validated_account.get("business") if isinstance((validated_account or {}).get("business"), dict) else {}
        config.business_id = _clean((business or {}).get("id")) or config.business_id
    elif "business_id" in fields:
        config.business_id = _clean(payload.business_id)
    if "page_id" in fields:
        selected_page_id = _clean(payload.page_id)
        config.page_id = selected_page_id
        if selected_page_id and config.ad_account_id:
            try:
                client, _meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id)
                page = await _validate_page_access(client, selected_page_id)
                config.instagram_account_id = _page_instagram_id(page)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc
    if "instagram_account_id" in fields:
        requested_instagram_id = _clean(payload.instagram_account_id)
        if requested_instagram_id and config.page_id:
            client, _meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id)
            page = await _validate_page_access(client, config.page_id)
            if _page_instagram_id(page) != requested_instagram_id:
                raise HTTPException(status_code=409, detail="Instagram selecionado não pertence à Página Facebook configurada.")
        config.instagram_account_id = requested_instagram_id
    if "dataset_id" in fields:
        config.dataset_id = _clean(payload.dataset_id)
    if whatsapp and ("whatsapp_connection_id" in fields or not config.whatsapp_connection_id):
        config.whatsapp_connection_id = whatsapp.id
        config.waba_id = _clean(getattr(whatsapp, "waba_id", None) or metadata.get("meta_waba_id"))
        config.phone_number_id = _clean(getattr(whatsapp, "phone_number_id", None) or metadata.get("meta_phone_number_id"))
        config.dataset_id = _clean(metadata.get("dataset_id") or metadata.get("meta_dataset_id"))
        if config.page_id and config.ad_account_id:
            try:
                client, _meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id)
                await _validate_page_whatsapp_compatibility(client, config.ad_account_id, whatsapp, config.page_id)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc
    if "status" in fields:
        config.status = _clean(payload.status) or "draft"
    await db.commit()
    await db.refresh(config)
    whatsapp_connections = await _official_whatsapp_connections(db, empresa_uuid)
    return {
        "config": _serialize_config(config),
        "official_whatsapp": _serialize_whatsapp(whatsapp),
        "official_whatsapps": [_serialize_whatsapp(item) for item in whatsapp_connections],
        "active_official_whatsapps": [_serialize_whatsapp(item) for item in whatsapp_connections if _is_active_meta_whatsapp_connection(item)],
    }


@router.get("/assets")
async def get_meta_asset_graph(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    config = await _get_config(db, empresa_uuid)
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Conta de anúncios não vinculada para esta empresa.")
    ad_account_id = _current_configured_ad_account_id(config)
    client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=ad_account_id)
    ad_account = await client.validate_ad_account(ad_account_id)
    business = ad_account.get("business") if isinstance(ad_account.get("business"), dict) else {}
    pages = await client.list_pages()
    selected_page = next((page for page in pages if str(page.get("id") or "").strip() == str(config.page_id or "").strip()), None)
    active_whatsapps = await _active_campaign_whatsapp_connections(db, empresa_uuid)
    compatible: list[dict[str, Any]] = []
    for connection in active_whatsapps:
        if selected_page:
            try:
                proof = await _validate_page_whatsapp_compatibility(client, ad_account_id, connection, str(selected_page.get("id") or ""))
            except HTTPException:
                proof = None
        else:
            proof = None
        serialized = _serialize_whatsapp(connection)
        serialized["page_compatible"] = bool(proof)
        serialized["compatibility_source"] = (proof or {}).get("source") if proof else None
        compatible.append(serialized)
    return {
        "credential": meta,
        "ad_account": {
            "id": normalize_ad_account_id(ad_account.get("account_id") or ad_account.get("id")),
            "id_masked": mask_meta_id(ad_account.get("account_id") or ad_account.get("id")),
            "name": ad_account.get("name"),
            "account_status": ad_account.get("account_status"),
            "currency": ad_account.get("currency"),
            "timezone_name": ad_account.get("timezone_name"),
            "business_id": (business or {}).get("id"),
            "business_id_masked": mask_meta_id((business or {}).get("id")),
            "business_name": (business or {}).get("name"),
        },
        "pages": [_serialize_page(page, selected=str(page.get("id") or "") == str(config.page_id or "")) for page in pages],
        "selected_page": _serialize_page(selected_page, selected=True) if selected_page else None,
        "instagram": {
            "account_id": _page_instagram_id(selected_page),
            "account_id_masked": mask_meta_id(_page_instagram_id(selected_page)),
            "username": (
                (selected_page.get("instagram_business_account") or selected_page.get("connected_instagram_account") or {}).get("username")
                if selected_page
                else None
            ),
            "linked_to_page": bool(selected_page and _page_instagram_id(selected_page)),
        },
        "whatsapps": compatible,
        "dataset": {
            "id": config.dataset_id,
            "id_masked": mask_meta_id(config.dataset_id),
            "source": "traffic_meta_config",
            "website_tracking_supported_for_current_ad_format": False,
            "website_tracking_reason": "OUTCOME_LEADS/CONVERSATIONS/WHATSAPP não usa Dataset no AdSet; tracking_specs não foi inferido sem contrato Graph comprovado.",
        },
        "asset_graph_valid": bool(selected_page and any(item.get("page_compatible") for item in compatible)),
    }


@router.get("/conversions")
async def get_meta_conversions_status(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        return await conversions_status(db, empresa_uuid)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/provisioning")
async def get_meta_tenant_provisioning(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    """Read-only consolidated tenant asset/readiness audit."""
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        return (await audit_meta_tenant(db, empresa_uuid)).sanitized()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.post("/provisioning")
async def provision_meta_tenant(
    empresa_id: str,
    create_dataset: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    """Idempotent setup operation; never creates mappings or live delivery."""
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        return (await provision_meta_conversion_infrastructure(db, empresa_uuid, create_dataset=create_dataset)).sanitized()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/provisioning/business-surfaces")
async def get_meta_business_surfaces(
    empresa_id: str,
    business_ids: str | None = Query(default=None, max_length=240),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    """Read-only Business-level AdsDataset/offsite/event-source audit."""
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    ids = [item.strip() for item in str(business_ids or "").split(",") if item.strip()]
    try:
        return await audit_business_meta_surfaces(db, empresa_uuid, business_ids=ids)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/conversions/dataset")
async def get_meta_conversions_dataset(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        status = await conversions_status(db, empresa_uuid)
        return {
            "dataset_configured": status.get("dataset_configured"),
            "dataset_valid": status.get("dataset_valid"),
            "dataset_meta_id_masked": status.get("dataset_meta_id_masked"),
            "dataset_agentos_display_name": status.get("dataset_agentos_display_name"),
            "dataset_meta_name": status.get("dataset_meta_name"),
            "traffic_meta_dataset_role": status.get("traffic_meta_dataset_role"),
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/campaigns/conversion-readiness")
async def get_meta_campaign_conversion_readiness(
    empresa_id: str,
    conversion_goal_key: str | None = Query(default=None, max_length=40),
    page_id: str | None = Query(default=None, max_length=80),
    whatsapp_connection_id: str | None = Query(default=None, max_length=80),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        selected_whatsapp_id = _uuid_or_400(whatsapp_connection_id, "whatsapp_connection_id") if whatsapp_connection_id else None
        return await _campaign_conversion_readiness(
            db,
            empresa_uuid,
            conversion_goal_key=conversion_goal_key,
            page_id=page_id,
            whatsapp_connection_id=selected_whatsapp_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/conversions/optimization-eligibility")
async def get_meta_optimization_eligibility_status(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    """Return signal accumulation separately from Campaign readiness/probes."""
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        base_status = await conversions_status(db, empresa_uuid)
        return await get_meta_optimization_eligibility(
            db,
            empresa_uuid,
            infrastructure_ready=bool(base_status.get("conversion_ready")),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/conversions/history")
async def get_meta_conversions_history(
    empresa_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    rows = (
        await db.execute(
            select(MetaConversionEvent, CRMLead, BusinessMessagingConversionMapping)
            .outerjoin(CRMLead, (CRMLead.id == MetaConversionEvent.lead_id) & (CRMLead.empresa_id == MetaConversionEvent.empresa_id))
            .outerjoin(
                BusinessMessagingConversionMapping,
                (BusinessMessagingConversionMapping.empresa_id == MetaConversionEvent.empresa_id)
                & (BusinessMessagingConversionMapping.trigger_type == MetaConversionEvent.trigger_type)
                & (BusinessMessagingConversionMapping.trigger_ref == MetaConversionEvent.trigger_ref)
                & (BusinessMessagingConversionMapping.event_name == MetaConversionEvent.event_name),
            )
            .where(
                MetaConversionEvent.empresa_id == empresa_uuid,
                MetaConversionEvent.conversion_context == "business_messaging_whatsapp",
            )
            .order_by(MetaConversionEvent.criado_em.desc(), MetaConversionEvent.id.desc())
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            _serialize_conversion_history_event(event, lead, mapping)
            for event, lead, mapping in rows
        ],
        "limit": limit,
    }


@router.post("/conversions/token")
async def save_meta_conversions_token(
    empresa_id: str,
    payload: ConversionTokenRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    result = await save_conversion_token(db, empresa_uuid, payload.access_token)
    if not result.valid:
        raise HTTPException(status_code=400, detail={"message": "Token válido não confirmado para esta WABA.", "validation": result.sanitized()})
    return {"saved": True, "validation": result.sanitized(), "status": await conversions_status(db, empresa_uuid)}


@router.post("/conversions/setup")
async def setup_meta_conversions(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    try:
        result = await setup_business_messaging_conversions(db, empresa_uuid)
        return {"setup": result, "status": await conversions_status(db, empresa_uuid)}
    except MetaCredentialResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.patch("/conversions/mappings/{mapping_id}/sources")
async def update_meta_conversion_mapping_sources(
    empresa_id: str,
    mapping_id: str,
    payload: ConversionMappingSourcesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    mapping_uuid = _uuid_or_400(mapping_id, "mapping_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    valid_sources = {item["value"] for item in BUSINESS_MESSAGING_TRIGGER_SOURCE_OPTIONS}
    sources = sorted({str(item or "").strip().lower() for item in payload.allowed_sources}.intersection(valid_sources))
    if not sources:
        sources = list(DEFAULT_BUSINESS_MESSAGING_ALLOWED_SOURCES)
    mapping = (
        await db.execute(
            select(BusinessMessagingConversionMapping).where(
                BusinessMessagingConversionMapping.id == mapping_uuid,
                BusinessMessagingConversionMapping.empresa_id == empresa_uuid,
            )
        )
    ).scalars().first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping de conversão não encontrado para esta empresa.")
    metadata = dict(getattr(mapping, "metadata_json", None) or {})
    metadata["allowed_sources"] = sources
    if payload.test_event_code is not None:
        code = str(payload.test_event_code or "").strip()
        if code:
            metadata["test_event_code"] = code
        else:
            metadata.pop("test_event_code", None)
    mapping.metadata_json = metadata
    await db.commit()
    return {"saved": True, "status": await conversions_status(db, empresa_uuid)}


def _serialize_creation(row: TrafficMetaCampaignCreation | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": str(row.id),
        "name": row.name,
        "adset_name": row.adset_name,
        "daily_budget_cents": row.daily_budget_cents,
        "location_type": row.location_type,
        "meta_campaign_id": mask_meta_id(row.meta_campaign_id),
        "meta_adset_id": mask_meta_id(row.meta_adset_id),
        "ad_account_id": mask_meta_id(row.ad_account_id),
        "business_id": mask_meta_id(row.business_id),
        "page_id": mask_meta_id(row.page_id),
        "instagram_account_id": mask_meta_id(row.instagram_account_id),
        "whatsapp_connection_id": str(row.whatsapp_connection_id) if row.whatsapp_connection_id else None,
        "phone_number_id": mask_meta_id(row.phone_number_id),
        "whatsapp_phone_number": mask_meta_id(row.whatsapp_phone_number),
        "waba_id": mask_meta_id(row.waba_id),
        "dataset_id": mask_meta_id(row.dataset_id),
        "campaign_status": row.campaign_status,
        "adset_status": row.adset_status,
        "campaign_sequence": row.campaign_sequence,
        "business_goal": (row.payload_json or {}).get("business_goal") or (row.payload_json or {}).get("conversion_goal_key"),
        "conversion_goal_key": (row.payload_json or {}).get("conversion_goal_key"),
        "conversion_goal_label": (row.payload_json or {}).get("conversion_goal_label"),
        "conversion_goal_meta": (row.payload_json or {}).get("conversion_goal_meta"),
        "campaign_conversion_contract": (row.payload_json or {}).get("campaign_conversion_contract"),
        "draft_session_id": (row.payload_json or {}).get("draft_session_id"),
        "ad_count": len((row.response_json or {}).get("ads") or []),
        "status": row.status,
        "error": row.error_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _write_audit(
    *,
    operation: str,
    graph_version: str,
    ad_account_id: str | None,
    failed_stage: str | None = None,
    response: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "failed_stage": failed_stage,
        "graph_version": graph_version,
        "ad_account_masked": mask_meta_id(normalize_ad_account_id(ad_account_id)),
        "http_status": (error or {}).get("http_status"),
        "graph_error_code": (error or {}).get("meta_error_code"),
        "graph_error_subcode": (error or {}).get("error_subcode"),
        "graph_error_type": (error or {}).get("meta_error_type"),
        "graph_error_user_title": (error or {}).get("error_user_title"),
        "graph_error_user_msg": (error or {}).get("error_user_msg"),
        "graph_error_blame_field_specs": (error or {}).get("blame_field_specs"),
        "graph_error_details": (error or {}).get("error_details"),
        "fbtrace_id": (error or {}).get("fbtrace_id"),
        "meta_object_id_masked": mask_meta_id((response or {}).get("id")),
        "post_write_verification": bool(response and response.get("id") and response.get("status") == "PAUSED"),
    }


def _validate_only_audit(
    *,
    operation: str,
    graph_version: str | None,
    ad_account_id: str,
    goal: str | None,
    response: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "validate_only": True,
        "result": "failed" if error else "passed",
        "goal": goal,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "graph_version": graph_version,
        "ad_account_masked": mask_meta_id(normalize_ad_account_id(ad_account_id)),
        "http_status": (error or {}).get("http_status"),
        "graph_error_code": (error or {}).get("meta_error_code"),
        "graph_error_subcode": (error or {}).get("error_subcode"),
        "graph_error_type": (error or {}).get("meta_error_type"),
        "graph_error_user_title": (error or {}).get("error_user_title"),
        "graph_error_user_msg": (error or {}).get("error_user_msg"),
        "graph_error_blame_field_specs": (error or {}).get("blame_field_specs"),
        "graph_error_details": (error or {}).get("error_details"),
        "fbtrace_id": (error or {}).get("fbtrace_id"),
        "meta_object_id_masked": mask_meta_id((response or {}).get("id") or (response or {}).get("campaign_id") or (response or {}).get("adset_id")),
    }


async def _validate_meta_write_payload(
    client: MetaMarketingApiClient,
    ad_account_id: str,
    *,
    object_type: str,
    payload: dict[str, Any],
    graph_version: str | None,
    goal: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    operation = f"{object_type}.validate_only"
    try:
        if object_type == "campaign":
            response = await client.create_campaign(ad_account_id, payload, validate_only=True)
        elif object_type == "adset":
            response = await client.create_adset(ad_account_id, payload, validate_only=True)
        else:
            raise ValueError("unsupported_validate_only_object")
        return (
            _validate_only_audit(
                operation=operation,
                graph_version=graph_version,
                ad_account_id=ad_account_id,
                goal=goal,
                response=response,
            ),
            None,
        )
    except Exception as exc:
        error = sanitize_graph_error(exc)
        return (
            _validate_only_audit(
                operation=operation,
                graph_version=graph_version,
                ad_account_id=ad_account_id,
                goal=goal,
                error=error,
            ),
            error,
        )


def _raise_validate_only_failure(record: dict[str, Any], error: dict[str, Any] | None) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "message": "A Meta rejeitou a validação da campanha antes da criação real.",
            "code": "META_VALIDATE_ONLY_FAILED",
            "validate_only": record,
            "error": error,
        },
    )


def _append_write_audit(row: TrafficMetaCampaignCreation, audit: dict[str, Any]) -> None:
    response_json = dict(row.response_json or {})
    audits = list(response_json.get("write_audit") or [])
    audits.append(audit)
    response_json["write_audit"] = audits
    row.response_json = response_json


def _sanitize_http_form(data: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in dict(data or {}).items():
        safe[key] = mask_meta_id(value) if key in {"campaign_id", "page_id", "whats_app_business_phone_number_id", "whatsapp_phone_number"} else value
    return safe


def _ensure_meta_paused_read(body: dict[str, Any], *, expected_id: str, object_name: str) -> None:
    if str(body.get("id") or "").strip() != str(expected_id or "").strip():
        raise HTTPException(status_code=502, detail=f"{object_name} não confirmado pela Meta.")
    if str(body.get("status") or "").strip().upper() != "PAUSED":
        raise HTTPException(status_code=502, detail=f"{object_name} não retornou PAUSED no GET pós-write.")


async def _read_ad_from_ad_account(
    client: MetaMarketingApiClient,
    ad_account_id: str,
    *,
    ad_id: str,
    adset_id: str,
) -> dict[str, Any]:
    try:
        direct = await client.get_ad(ad_id)
        if str(direct.get("adset_id") or "").strip() == str(adset_id or "").strip():
            return direct
    except Exception:
        pass
    ads = await client.list_ads(ad_account_id, adset_id=adset_id)
    for ad in ads:
        if str(ad.get("id") or "").strip() == str(ad_id or "").strip():
            return ad
    raise HTTPException(status_code=502, detail="Ad não confirmado pela Meta no GET pós-write.")


@router.post("/ad-copy/generate")
async def generate_traffic_meta_ad_copy(
    empresa_id: str,
    payload: TrafficMetaAdCopyGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    destination = str(payload.destination or "whatsapp").strip().lower()
    if destination != "whatsapp":
        raise HTTPException(status_code=400, detail="Destino não suportado para copy CTWA.")
    try:
        result, context, meta = await generate_ad_copy(
            session=db,
            empresa_id=empresa_uuid,
            creative_brief=payload.creative_brief,
            objective=payload.objective or CAMPAIGN_OBJECTIVE,
            destination=destination,
            variation_count=payload.variation_count,
        )
    except AdCopyGenerationError as exc:
        raise HTTPException(status_code=502, detail={"message": "Não foi possível gerar os textos agora.", "code": str(exc)}) from exc
    return {
        "success": True,
        "objective": payload.objective or CAMPAIGN_OBJECTIVE,
        "destination": destination,
        "variation_count": len(result.variations),
        "variations": [item.model_dump() for item in result.variations],
        "tenant_context_used": True,
        "business_rules_used": bool(context.ia_regras_negocio),
        "rag_context_used": bool(context.rag_context),
        "structured_output": True,
        "llm": {
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "latency_ms": meta.get("latency_ms"),
        },
    }


@router.post("/ad-copy/analyze-image")
async def analyze_traffic_meta_image_copy(
    empresa_id: str,
    payload: TrafficMetaImageCopyGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    if str(payload.destination or "whatsapp").strip().lower() != "whatsapp":
        raise HTTPException(status_code=400, detail="Destino não suportado para copy CTWA.")
    try:
        mime_type, _, content = _decode_media_data_url(payload.image_data_url)
        width, height, image_format = validate_image_bytes(content, mime_type)
        result, context, meta = await generate_image_ad_copy(
            session=db,
            empresa_id=empresa_uuid,
            image_data_url=payload.image_data_url,
            objective=payload.objective or CAMPAIGN_OBJECTIVE,
            destination="whatsapp",
        )
    except (ImageValidationError, AdCopyGenerationError) as exc:
        code = str(exc)
        status_code = 400 if code.startswith("IMAGE_") else 502
        raise HTTPException(status_code=status_code, detail={"message": "Não foi possível analisar a imagem.", "code": code}) from exc
    return {
        "success": True,
        "filename": payload.filename,
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "image_format": image_format,
        "copy": result.model_dump(exclude={"image_evidence"}),
        "tenant_context_used": True,
        "business_rules_used": bool(context.ia_regras_negocio),
        "rag_context_used": bool(context.rag_context),
        "provider": meta.get("provider"),
        "model": meta.get("model"),
        "structured_output": True,
    }


def _phone_number_id_from_connection(connection: Conexao, config: TrafficMetaConfig) -> str | None:
    return _connection_phone_number_id(connection) or _clean(config.phone_number_id)


async def _validate_page_access(client: MetaMarketingApiClient, page_id: str) -> dict[str, Any]:
    pages = await client.list_pages()
    for page in pages:
        if str(page.get("id") or "").strip() == str(page_id or "").strip():
            return page
    raise HTTPException(status_code=409, detail="Página Meta não disponível para esta credencial.")


async def _validate_page_whatsapp_compatibility(
    client: MetaMarketingApiClient,
    ad_account_id: str,
    connection: Conexao,
    page_id: str,
) -> dict[str, Any]:
    phone_number_id = _connection_phone_number_id(connection)
    phone_display = _connection_display_number(connection)
    if not phone_number_id:
        raise HTTPException(status_code=409, detail="WhatsApp Oficial sem Phone Number ID válido.")
    phone = await client.get_whatsapp_phone_number(phone_number_id)
    if str(phone.get("id") or "").strip() != str(phone_number_id).strip():
        raise HTTPException(status_code=409, detail="Phone Number ID do WhatsApp não foi confirmado pela Meta.")

    expected_phone = _digits(phone_display or phone.get("display_phone_number"))
    adsets = await client.list_adsets(ad_account_id)
    for adset in adsets:
        promoted = adset.get("promoted_object") if isinstance(adset.get("promoted_object"), dict) else {}
        if str(promoted.get("page_id") or "").strip() != str(page_id or "").strip():
            continue
        persisted_phone_id = str(promoted.get("whats_app_business_phone_number_id") or "").strip()
        persisted_phone = _digits(promoted.get("whatsapp_phone_number"))
        if persisted_phone_id == str(phone_number_id) or (expected_phone and persisted_phone and persisted_phone.endswith(expected_phone[-8:])):
            if str(adset.get("effective_status") or "").strip().upper() != "WITH_ISSUES":
                return {
                    "source": "meta_adset_readback",
                    "adset_id_masked": mask_meta_id(adset.get("id")),
                    "page_id": str(page_id),
                    "phone_number_id_present": True,
                    "whatsapp_phone_number_present": bool(persisted_phone),
                }

    metadata = dict(getattr(connection, "metadata_json", None) or {})
    page_ids = {str(item or "").strip() for item in (metadata.get("page_ids") or metadata.get("connected_page_ids") or [])}
    if str(page_id or "").strip() in page_ids:
        return {"source": "connection_metadata_page_ids", "page_id": str(page_id), "phone_number_id_present": True}

    raise HTTPException(
        status_code=409,
        detail="Não foi possível validar que a Página Facebook selecionada está vinculada ao WhatsApp Oficial escolhido.",
    )


async def _validate_whatsapp_asset_for_ads(client: MetaMarketingApiClient, connection: Conexao, page_id: str) -> dict[str, Any]:
    page = await _validate_page_access(client, page_id)
    phone_number_id = _connection_phone_number_id(connection)
    if not phone_number_id:
        raise HTTPException(status_code=409, detail="WhatsApp Oficial sem Phone Number ID válido.")
    phone = await client.get_whatsapp_phone_number(phone_number_id)
    if str(phone.get("id") or "").strip() != str(phone_number_id).strip():
        raise HTTPException(status_code=409, detail="Phone Number ID do WhatsApp não foi confirmado pela Meta.")
    return {
        "page_id": str(page.get("id") or "").strip(),
        "whatsapp_connection_id": str(connection.id),
        "phone_number_id_present": True,
        "waba_id_present": bool(_connection_waba_id(connection)),
    }


CONVERSION_GOAL_CATALOG = [
    {
        "key": "purchase",
        "label": "Compra",
        "meta_goal": "MESSAGING_PURCHASE_CONVERSION",
        "event_name": "Purchase",
    },
    {
        "key": "quality_lead",
        "label": "Lead qualificado",
        "meta_goal": "QUALITY_LEAD",
        "event_name": "QualifiedLead",
    },
    {
        "key": "appointment",
        "label": "Agendamento",
        "meta_goal": "MESSAGING_APPOINTMENT_CONVERSION",
        "event_name": "OrderCreated",
    },
]


def _conversion_goal_by_key(value: str | None) -> dict[str, Any] | None:
    key = str(value or "").strip().lower()
    if not key:
        return None
    return next((item for item in CONVERSION_GOAL_CATALOG if item["key"] == key), None)


def _requested_business_goal(payload: Any) -> str | None:
    value = getattr(payload, "business_goal", None) or getattr(payload, "conversion_goal_key", None)
    key = str(value or "").strip().lower()
    return key or None


def _conversion_goal_options(goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {item["key"]: item for item in goals}
    return [
        {
            "key": item["key"],
            "label": item["label"],
            "meta_goal": item["meta_goal"],
            "event_name": item["event_name"],
            "supported": item["key"] in by_key,
        }
        for item in CONVERSION_GOAL_CATALOG
    ]


def _campaign_conversion_contract(readiness: dict[str, Any]) -> dict[str, Any]:
    optimization = readiness.get("optimization_eligibility") if isinstance(readiness.get("optimization_eligibility"), dict) else {}
    status = readiness.get("status") if isinstance(readiness.get("status"), dict) else {}
    selected_goal = readiness.get("selected_goal")
    selected_catalog = _conversion_goal_by_key(selected_goal)
    eligible_goals = [goal for goal in readiness.get("supported_goals") or [] if goal.get("supported")]
    selected_probe = next(
        (
            probe
            for probe in readiness.get("probe_results") or []
            if probe.get("goal_key") == selected_goal
            or probe.get("meta_goal") == readiness.get("best_available_conversion_optimization")
        ),
        None,
    )
    return {
        "infrastructure_ready": bool(optimization.get("infrastructure_ready") if "infrastructure_ready" in optimization else status.get("conversion_ready")),
        "optimization_state": optimization.get("state") or ("READY_TO_CREATE" if readiness.get("ready") else readiness.get("reason") or "UNKNOWN"),
        "eligible_goals": eligible_goals,
        "selected_goal": selected_goal,
        "conversion_signal": selected_catalog.get("event_name") if selected_catalog else None,
        "reason": readiness.get("reason"),
        "accepted_signal_counts": optimization.get("accepted_signal_counts") or {},
        "meta_probe": selected_probe,
    }


def _with_campaign_conversion_contract(readiness: dict[str, Any]) -> dict[str, Any]:
    readiness["campaign_conversion_contract"] = _campaign_conversion_contract(readiness)
    return readiness


async def _resolve_conversion_probe_campaign_id(
    client: MetaMarketingApiClient,
    ad_account_id: str,
    *,
    empresa_name: str,
    empresa_id: uuid.UUID,
) -> str | None:
    try:
        probe = build_campaign_payload(
            generated_object_name(empresa_name, empresa_id, "CAMPAIGN", 1),
            objective=CONVERSION_CAMPAIGN_OBJECTIVE,
        )
        validation = await client.create_campaign(ad_account_id, probe, validate_only=True)
        candidate = str(validation.get("id") or validation.get("campaign_id") or "").strip()
        if candidate:
            return candidate
    except Exception:
        pass
    campaigns = await client.list_campaigns(ad_account_id)
    for campaign in campaigns:
        objective = str(campaign.get("objective") or "").strip().upper()
        status = str(campaign.get("status") or "").strip().upper()
        effective_status = str(campaign.get("effective_status") or "").strip().upper()
        if objective == CONVERSION_CAMPAIGN_OBJECTIVE and (status == "PAUSED" or effective_status == "PAUSED"):
            campaign_id = str(campaign.get("id") or "").strip()
            if campaign_id:
                return campaign_id
    return None


async def _probe_conversion_goal_support(
    client: MetaMarketingApiClient,
    ad_account_id: str,
    *,
    probe_campaign_id: str,
    page_id: str,
    phone_number: str,
    targeting: dict[str, Any],
    goal: dict[str, Any],
) -> dict[str, Any]:
    payload = build_adset_payload(
        name=f"READINESS | {goal['label']}",
        campaign_id=probe_campaign_id,
        daily_budget_cents=100,
        page_id=page_id,
        phone_number=phone_number,
        targeting=targeting,
        optimization_goal=goal["meta_goal"],
    )
    try:
        response = await client.create_adset(ad_account_id, payload, validate_only=True)
        return {
            "goal_key": goal["key"],
            "label": goal["label"],
            "meta_goal": goal["meta_goal"],
            "event_name": goal["event_name"],
            "supported": True,
            "response_id": str(response.get("id") or response.get("adset_id") or "").strip() or None,
        }
    except Exception as exc:
        error = sanitize_graph_error(exc)
        return {
            "goal_key": goal["key"],
            "label": goal["label"],
            "meta_goal": goal["meta_goal"],
            "event_name": goal["event_name"],
            "supported": False,
            "error": error,
        }


async def _campaign_conversion_readiness(
    session: AsyncSession,
    empresa_id: uuid.UUID,
    *,
    conversion_goal_key: str | None = None,
    page_id: str | None = None,
    whatsapp_connection_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    base_status = await conversions_status(session, empresa_id)
    config = await _get_config(session, empresa_id)
    if not config or not config.ad_account_id:
        return _with_campaign_conversion_contract({
            "ready": False,
            "reason": "AD_ACCOUNT_NOT_CONFIGURED",
            "status": base_status,
            "supported_goals": [],
            "best_available_conversion_optimization": None,
            "legacy_conversations_supported": False,
        })
    ad_account_id = _current_configured_ad_account_id(config)
    try:
        client, meta = await _client(session, empresa_id, MetaMarketingCapability.ADS_MANAGEMENT, ad_account_id=ad_account_id, allow_admin_fallback=False)
    except MetaCredentialResolutionError as exc:
        return _with_campaign_conversion_contract({
            "ready": False,
            "reason": str(exc),
            "status": base_status,
            "supported_goals": [],
            "best_available_conversion_optimization": None,
            "legacy_conversations_supported": False,
            "permission_model": {
                "tenant_token_required_for_mutation": True,
                "reconnect_required": str(exc) == "TENANT_MARKETING_PERMISSION_REQUIRED",
                "admin_token_mutation_fallback_allowed": False,
            },
        })
    ad_account = await client.validate_ad_account(ad_account_id)
    selected_page_id = _clean(page_id or config.page_id)
    if not selected_page_id:
        return _with_campaign_conversion_contract({
            "ready": False,
            "reason": "PAGE_NOT_CONFIGURED",
            "status": base_status,
            "supported_goals": [],
            "best_available_conversion_optimization": None,
            "legacy_conversations_supported": False,
            "credential": meta,
        })
    asset_snapshot = await _resolve_campaign_assets_for_write(
        session,
        empresa_id,
        config=config,
        page_id=selected_page_id,
        whatsapp_connection_id=whatsapp_connection_id,
        client=client,
        ad_account=ad_account,
    )
    empresa = (await session.execute(select(Empresa).where(Empresa.id == empresa_id))).scalars().first()
    empresa_name = str(getattr(empresa, "nome_empresa", "") or "Empresa").strip() or "Empresa"
    probe_campaign_id = await _resolve_conversion_probe_campaign_id(
        client,
        ad_account_id,
        empresa_name=empresa_name,
        empresa_id=empresa_id,
    )
    if not probe_campaign_id:
        return _with_campaign_conversion_contract({
            "ready": False,
            "reason": "CONVERSION_PROBE_CAMPAIGN_MISSING",
            "status": base_status,
            "supported_goals": [],
            "best_available_conversion_optimization": None,
            "legacy_conversations_supported": False,
            "asset_snapshot": {
                "page_id": asset_snapshot.get("page_id"),
                "whatsapp_connection_id": asset_snapshot.get("whatsapp_connection_id"),
                "dataset_id": asset_snapshot.get("dataset_id"),
                "waba_id": asset_snapshot.get("waba_id"),
            },
            "credential": meta,
        })
    goal_key = str(conversion_goal_key or "").strip().lower()
    goals = [_conversion_goal_by_key(goal_key)] if goal_key else list(CONVERSION_GOAL_CATALOG)
    goals = [goal for goal in goals if goal]
    if not goals:
        goals = list(CONVERSION_GOAL_CATALOG)
    probe_results = [
        await _probe_conversion_goal_support(
            client,
            ad_account_id,
            probe_campaign_id=probe_campaign_id,
            page_id=asset_snapshot["page_id"],
            phone_number=asset_snapshot["whatsapp_phone_number"],
            targeting=build_brazil_targeting(),
            goal=goal,
        )
        for goal in goals
    ]
    supported_goals = [item for item in probe_results if item.get("supported")]
    legacy_conversations_supported = False
    try:
        legacy_probe = build_adset_payload(
            name="READINESS | CONVERSATIONS",
            campaign_id=probe_campaign_id,
            daily_budget_cents=100,
            page_id=asset_snapshot["page_id"],
            phone_number=asset_snapshot["whatsapp_phone_number"],
            targeting=build_brazil_targeting(),
        )
        await client.create_adset(ad_account_id, legacy_probe, validate_only=True)
        legacy_conversations_supported = True
    except Exception:
        legacy_conversations_supported = False
    best_supported = supported_goals[0] if supported_goals else None
    base_ready = bool(base_status.get("conversion_ready"))
    ready = bool(base_ready and best_supported)
    purchase_probe = None
    lead_probe = None
    if base_ready:
        purchase_probe = "eligible" if any(item.get("goal_key") == "purchase" and item.get("supported") for item in probe_results) else "not_eligible_yet"
        lead_probe = "eligible" if any(item.get("goal_key") == "quality_lead" and item.get("supported") for item in probe_results) else "not_eligible_yet"
    optimization_eligibility = await get_meta_optimization_eligibility(
        session,
        empresa_id,
        infrastructure_ready=base_ready,
        purchase_probe=purchase_probe,
        lead_probe=lead_probe,
    )
    readiness_reason = None if ready else (
        "PURCHASE_ELIGIBILITY_ACCUMULATING"
        if base_ready and optimization_eligibility["state"] in {"NOT_ENOUGH_SIGNAL", "SIGNAL_ACCUMULATING"}
        else ("CONVERSION_GOAL_NOT_ELIGIBLE" if base_ready else "CONVERSION_BASE_NOT_READY")
    )
    return _with_campaign_conversion_contract({
        "ready": ready,
        "reason": readiness_reason,
        "status": base_status,
        "credential": meta,
        "ad_account": {
            "id": normalize_ad_account_id(ad_account.get("account_id") or ad_account.get("id")),
            "name": ad_account.get("name"),
            "business_id": (ad_account.get("business") or {}).get("id") if isinstance(ad_account.get("business"), dict) else None,
        },
        "asset_snapshot": {
            "page_id": asset_snapshot.get("page_id"),
            "whatsapp_connection_id": asset_snapshot.get("whatsapp_connection_id"),
            "phone_number_id": asset_snapshot.get("phone_number_id"),
            "waba_id": asset_snapshot.get("waba_id"),
            "dataset_id": asset_snapshot.get("dataset_id"),
        },
        "probe_campaign_id": mask_meta_id(probe_campaign_id),
        "supported_goals": _conversion_goal_options(supported_goals),
        "probe_results": probe_results,
        "best_available_conversion_optimization": best_supported["meta_goal"] if best_supported else None,
        "selected_goal": best_supported["goal_key"] if best_supported else None,
        "selected_goal_label": best_supported["label"] if best_supported else None,
        "legacy_conversations_supported": legacy_conversations_supported,
        "optimization_eligibility": optimization_eligibility,
    })


def _decode_media_data_url(value: Any) -> tuple[str, str, bytes]:
    match = re.fullmatch(r"data:([^;,]+);base64,(.+)", str(value or ""), re.DOTALL)
    if not match:
        raise HTTPException(status_code=400, detail="Mídia inválida.")
    mime_type, encoded = match.groups()
    allowed = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/quicktime", "video/webm"}
    if mime_type.lower() not in allowed:
        raise HTTPException(status_code=400, detail="Tipo de mídia não suportado.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Mídia base64 inválida.") from exc
    limit = 50 * 1024 * 1024 if mime_type.lower().startswith("video/") else 10 * 1024 * 1024
    if not content or len(content) > limit:
        raise HTTPException(status_code=400, detail="Tamanho de mídia inválido.")
    extension = mime_type.split("/", 1)[1].replace("jpeg", "jpg").replace("quicktime", "mov")
    return mime_type.lower(), f"agentos.{extension}", content


def _ensure_full_ad_input(item: dict[str, Any], index: int, *, require_copy: bool = True) -> dict[str, Any]:
    media_type = str(item.get("media_type") or "").strip().lower()
    if media_type not in {"image", "video"}:
        raise HTTPException(status_code=400, detail=f"Anúncio {index} precisa de mídia de imagem ou vídeo.")
    primary_text, headline = _clean(item.get("primary_text")) or "", _clean(item.get("headline")) or ""
    if require_copy and (not primary_text or not headline):
        raise HTTPException(status_code=400, detail=f"Anúncio {index} precisa de texto principal e título.")
    mime_type, filename, content = _decode_media_data_url(item.get("media_data_url"))
    if not mime_type.startswith(f"{media_type}/"):
        raise HTTPException(status_code=400, detail=f"O tipo de mídia do anúncio {index} não corresponde ao arquivo enviado.")
    if media_type == "image":
        try:
            validate_image_bytes(content, mime_type)
        except ImageValidationError as exc:
            raise HTTPException(status_code=400, detail=f"Imagem do anúncio {index} inválida: {exc}") from exc
    return {"media_type": media_type, "mime_type": mime_type, "filename": filename, "content": content, "primary_text": primary_text[:5000], "headline": headline[:240], "description": (_clean(item.get("description")) or "")[:240], "item_key": (_clean(item.get("item_key")) or "")[:160]}


def _creation_pipeline(row: TrafficMetaCampaignCreation) -> dict[str, Any]:
    pipeline = dict(row.response_json or {})
    return {"campaign": pipeline.get("campaign"), "adset": pipeline.get("adset"), "ads": list(pipeline.get("ads") or []), "status": row.status, "error": row.error_json}


def _ensure_resume_item_names(*, state: dict[str, Any], company_name: str, empresa_id: uuid.UUID, campaign_sequence: int, item_sequence: int) -> dict[str, Any]:
    """Complete deterministic names missing from draft-originated item states."""
    normalized = dict(state or {})
    normalized.setdefault("creative_name", generated_object_name(company_name, empresa_id, "CREATIVE", campaign_sequence, ad_sequence=item_sequence))
    normalized.setdefault("ad_name", generated_object_name(company_name, empresa_id, "AD", campaign_sequence, ad_sequence=item_sequence))
    return normalized


def _is_golden_whatsapp_binding(promoted_object: dict[str, Any], *, page_id: str, whatsapp_phone_number: str | None) -> bool:
    """Compare the Graph canonical phone with the locally formatted display number."""
    return (
        str(promoted_object.get("page_id") or "") == str(page_id)
        and promoted_object.get("smart_pse_enabled") is False
        and _digits(promoted_object.get("whatsapp_phone_number")) == _digits(whatsapp_phone_number)
        and "whats_app_business_phone_number_id" not in promoted_object
    )


def _resume_parent_write_plan(*, meta_campaign_id: str | None, meta_adset_id: str | None) -> dict[str, bool]:
    return {"campaign": not bool(str(meta_campaign_id or "").strip()), "adset": not bool(str(meta_adset_id or "").strip())}


def _resume_item_write_plan(state: dict[str, Any]) -> dict[str, bool]:
    return {
        "media": not bool(str((state or {}).get("media_reference") or "").strip()),
        "creative": not bool(str((state or {}).get("creative_id") or "").strip()),
        "ad": not bool(str((state or {}).get("ad_id") or "").strip()),
    }


@router.get("/location/resolve")
async def resolve_traffic_meta_pin(
    empresa_id: str,
    q: str = Query(min_length=3, max_length=240),
    _: Usuario = Depends(require_tenant_access),
):
    """Resolve a human location to a pin; the input is never serialized as a ZIP target."""
    _uuid_or_400(empresa_id, "empresa_id")
    query = q.strip()
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": "AgentOS/traffic-meta-pin"}) as client:
            response = await client.get("https://nominatim.openstreetmap.org/search", params={"q": f"{query}, Brasil", "format": "jsonv2", "limit": 1, "countrycodes": "br"})
            response.raise_for_status()
            results = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Não foi possível localizar este ponto agora.") from exc
    if not isinstance(results, list) or not results:
        raise HTTPException(status_code=404, detail="Local não encontrado. Informe um CEP, endereço ou local mais específico.")
    result = results[0]
    try:
        latitude, longitude = float(result["lat"]), float(result["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="O provedor não retornou um ponto válido.") from exc
    return {"display_name": str(result.get("display_name") or query)[:240], "latitude": latitude, "longitude": longitude, "provider": "nominatim_openstreetmap"}


@router.post("/campaigns/draft")
async def persist_traffic_meta_campaign_draft(
    empresa_id: str,
    payload: TrafficMetaDraftCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    """Persist the complete image batch before AI or any Meta operation."""
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    config = await _get_config(db, empresa_uuid)
    if not config:
        raise HTTPException(status_code=409, detail="Configure os ativos Meta antes de salvar o rascunho.")
    selected = _uuid_or_400(payload.whatsapp_connection_id, "whatsapp_connection_id") if payload.whatsapp_connection_id else None
    whatsapp = await _resolve_campaign_whatsapp_for_write(db, empresa_uuid, selected)
    try:
        targeting = build_targeting(location_mode=payload.location_type, latitude=payload.latitude, longitude=payload.longitude, radius_km=payload.radius_km, gender_mode=payload.gender_mode)
    except TrafficCampaignValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    company = (await db.execute(select(Empresa).where(Empresa.id == empresa_uuid))).scalars().first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    batch_id = uuid.uuid4()
    states: list[dict[str, Any]] = []
    hashes: list[str] = []
    for sequence, raw_item in enumerate(payload.ads, start=1):
        try:
            item = _ensure_full_ad_input(raw_item, sequence, require_copy=False)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Não foi possível validar esta imagem."
            raise HTTPException(status_code=exc.status_code, detail={"stage": "SAVE_BATCH_ITEMS", "error_code": "INVALID_DRAFT_ITEM", "friendly_message": detail, "meta_objects_confirmed": 0, "can_retry": True, "can_resume": False, "draft_id": None}) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail={"stage": "SAVE_BATCH_ITEMS", "error_code": "INVALID_DRAFT_ITEM", "friendly_message": f"Não foi possível preparar a imagem {sequence}.", "meta_objects_confirmed": 0, "can_retry": True, "can_resume": False}) from exc
        if item["media_type"] != "image":
            raise HTTPException(status_code=400, detail="O método de campanha aceita somente imagens.")
        item_key = item["item_key"] or hashlib.sha256(item["content"]).hexdigest()
        storage_key, digest = persist_traffic_image_bytes(empresa_id=str(empresa_uuid), batch_id=str(batch_id), item_key=item_key, original_name=item["filename"], data=item["content"])
        hashes.append(digest)
        states.append({
            "sequence": sequence,
            "item_key": item_key,
            "original_filename": item["filename"],
            "content_hash": digest,
            "server_image_reference": storage_key,
            "media_name": item["filename"],
            "media_type": "image",
            "ai_status": "pending",
            "primary_text": item["primary_text"],
            "headline": item["headline"],
            "description": item["description"],
            "copy_source": "HUMAN" if raw_item.get("copy_source") == "HUMAN" else "AI",
            "media_status": "pending",
            "creative_status": "pending",
            "ad_status": "pending",
            "last_error_stage": None,
        })
    key = creation_idempotency_key(empresa_uuid, {"draft": True, "draft_session_id": payload.draft_session_id or str(uuid.uuid4()), "hashes": hashes, "location_type": payload.location_type, "gender_mode": payload.gender_mode})
    existing = (await db.execute(select(TrafficMetaCampaignCreation).where(TrafficMetaCampaignCreation.empresa_id == empresa_uuid, TrafficMetaCampaignCreation.idempotency_key == key))).scalars().first()
    if not existing and payload.draft_session_id:
        draft_rows = (await db.execute(select(TrafficMetaCampaignCreation).where(TrafficMetaCampaignCreation.empresa_id == empresa_uuid, TrafficMetaCampaignCreation.status == "draft"))).scalars().all()
        existing = next((candidate for candidate in draft_rows if str((candidate.payload_json or {}).get("draft_session_id") or "") == payload.draft_session_id), None)
    if existing:
        if existing.status == "draft" and payload.draft_session_id and str((existing.payload_json or {}).get("draft_session_id") or "") == payload.draft_session_id and existing.idempotency_key != key:
            existing.response_json = {**(existing.response_json or {}), "ads": states, "server_items": len(states), "batch_persisted": True}
            existing.payload_json = {**(existing.payload_json or {}), "server_items": len(states), "targeting": targeting}
            await db.commit()
        return {"success": True, "creation": _serialize_creation(existing), "pipeline": _creation_pipeline(existing), "meta_writes": 0, "already_persisted": True}
    sequence = int((await db.execute(select(func.coalesce(func.max(TrafficMetaCampaignCreation.campaign_sequence), 0) + 1).where(TrafficMetaCampaignCreation.empresa_id == empresa_uuid))).scalar_one())
    row = TrafficMetaCampaignCreation(
        empresa_id=empresa_uuid, traffic_meta_config_id=config.id, ad_account_id=normalize_ad_account_id(config.ad_account_id), business_id=config.business_id,
        whatsapp_connection_id=whatsapp.id, idempotency_key=key, name=generated_object_name(company.nome_empresa, empresa_uuid, "CAMPAIGN", sequence),
        adset_name=generated_object_name(company.nome_empresa, empresa_uuid, "ADSET", sequence), daily_budget_cents=brl_to_meta_cents(payload.daily_budget_brl) if str(payload.daily_budget_brl or "").strip() and str(payload.daily_budget_brl).strip() not in {"0", "0,00", "0.00"} else 1,
        location_type=payload.location_type, page_id=_clean(payload.page_id or config.page_id) or "pending", instagram_account_id=config.instagram_account_id,
        phone_number_id=_connection_phone_number_id(whatsapp) or "pending", whatsapp_phone_number=_connection_display_number(whatsapp), waba_id=_connection_waba_id(whatsapp), dataset_id=config.dataset_id,
        campaign_sequence=sequence, status="draft", payload_json={"draft_session_id": payload.draft_session_id, "targeting": targeting, "targeting_snapshot": {"location_mode": payload.location_type, "location_input": payload.location_input, "location_display_name": payload.location_display_name, "latitude": payload.latitude, "longitude": payload.longitude, "radius": payload.radius_km, "distance_unit": "kilometer", "gender_mode": payload.gender_mode}, "batch_persisted": True, "server_items": len(states)},
        response_json={"batch_id": str(batch_id), "batch_persisted": True, "server_items": len(states), "ads": states}, created_by_user_id=current_user.id,
    )
    db.add(row)
    try:
        await db.commit()
        await db.refresh(row)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail={"stage": "CREATE_DRAFT", "error_code": "DRAFT_PERSISTENCE_FAILED", "friendly_message": "Não foi possível preparar a campanha. Nenhum anúncio foi enviado à Meta. Tente novamente.", "meta_objects_confirmed": 0, "can_retry": True, "can_resume": False, "draft_id": None}) from exc
    return {"success": True, "creation": _serialize_creation(row), "pipeline": _creation_pipeline(row), "meta_writes": 0, "server_items": len(states), "image_references": len(states)}


@router.get("/campaigns/{creation_id}/draft")
async def get_traffic_meta_campaign_draft(
    empresa_id: str,
    creation_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    creation_uuid = _uuid_or_400(creation_id, "creation_id")
    row = (await db.execute(select(TrafficMetaCampaignCreation).where(TrafficMetaCampaignCreation.id == creation_uuid, TrafficMetaCampaignCreation.empresa_id == empresa_uuid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Rascunho não encontrado.")
    return {"creation": _serialize_creation(row), "pipeline": _creation_pipeline(row), "meta_writes": 0}


@router.patch("/campaigns/{creation_id}/items/{item_key}/copy")
async def update_traffic_meta_campaign_copy(
    empresa_id: str,
    creation_id: str,
    item_key: str,
    payload: TrafficMetaCopyUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    creation_uuid = _uuid_or_400(creation_id, "creation_id")
    row = (await db.execute(select(TrafficMetaCampaignCreation).where(TrafficMetaCampaignCreation.id == creation_uuid, TrafficMetaCampaignCreation.empresa_id == empresa_uuid))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Rascunho não encontrado.")
    pipeline = dict(row.response_json or {})
    items = list(pipeline.get("ads") or [])
    item = next((value for value in items if str(value.get("item_key") or "") == item_key), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item do batch não encontrado.")
    item.update({"primary_text": payload.primary_text.strip(), "headline": payload.headline.strip(), "description": payload.description.strip(), "copy_source": payload.copy_source, "ai_status": "ready"})
    row.response_json = {**pipeline, "ads": items}
    await db.commit()
    return {"saved": True, "item_key": item_key, "meta_writes": 0}


@router.post("/campaigns/full")
async def create_full_paused_campaign(
    empresa_id: str,
    payload: TrafficMetaFullCampaignCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    settings = meta_settings()
    if not all((settings.marketing_write_enabled, settings.marketing_campaign_create_enabled, settings.marketing_adset_create_enabled, settings.marketing_creative_create_enabled, settings.marketing_ad_create_enabled)):
        raise HTTPException(status_code=403, detail="A criação completa de campanhas Meta está desabilitada.")
    config = (await db.execute(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == empresa_uuid).with_for_update())).scalars().first()
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Conta de anúncios não vinculada para esta empresa.")
    selected_whatsapp_id = _uuid_or_400(payload.whatsapp_connection_id, "whatsapp_connection_id") if payload.whatsapp_connection_id else None
    whatsapp = await _resolve_campaign_whatsapp_for_write(db, empresa_uuid, selected_whatsapp_id)
    phone_number_id = _phone_number_id_from_connection(whatsapp, config)
    if not phone_number_id:
        raise HTTPException(status_code=409, detail="WhatsApp Oficial sem Phone Number ID válido.")
    ad_account_id_for_write = _current_configured_ad_account_id(config)
    prepared_ads = [_ensure_full_ad_input(item, index) for index, item in enumerate(payload.ads, start=1)]
    if not prepared_ads and not payload.resume_creation_id:
        raise HTTPException(status_code=400, detail="Persist the image batch before creating the campaign.")
    try:
        daily_budget_cents = brl_to_meta_cents(payload.daily_budget_brl)
        targeting = build_targeting(location_mode=payload.location_type, latitude=payload.latitude, longitude=payload.longitude, radius_km=payload.radius_km, gender_mode=payload.gender_mode)
    except TrafficCampaignValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_MANAGEMENT, ad_account_id=ad_account_id_for_write, allow_admin_fallback=False)
    ad_account = await client.validate_ad_account(ad_account_id_for_write)
    asset_snapshot = await _resolve_campaign_assets_for_write(
        db,
        empresa_uuid,
        config=config,
        page_id=payload.page_id,
        whatsapp_connection_id=selected_whatsapp_id,
        client=client,
        ad_account=ad_account,
    )
    readiness = await _campaign_conversion_readiness(
        db,
        empresa_uuid,
        conversion_goal_key=_requested_business_goal(payload),
        page_id=asset_snapshot["page_id"],
        whatsapp_connection_id=selected_whatsapp_id,
    )
    if not readiness.get("ready"):
        raise HTTPException(status_code=409, detail={"message": "Campanha WhatsApp de conversão não está pronta.", "readiness": readiness})
    whatsapp = asset_snapshot["whatsapp"]
    page_id = asset_snapshot["page_id"]
    phone_number_id = asset_snapshot["phone_number_id"]
    company = (await db.execute(select(Empresa).where(Empresa.id == empresa_uuid))).scalars().first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    hashes = [hashlib.sha256(item["content"]).hexdigest() for item in prepared_ads]
    stable_input = {"budget": daily_budget_cents, "location_type": payload.location_type, "latitude": payload.latitude, "longitude": payload.longitude, "radius_km": payload.radius_km, "gender_mode": payload.gender_mode, "page_id": page_id, "ad_account_id": ad_account_id_for_write, "whatsapp_connection_id": str(whatsapp.id), "business_id": asset_snapshot.get("business_id"), "instagram_account_id": asset_snapshot.get("instagram_account_id"), "business_goal": readiness.get("selected_goal"), "conversion_goal_key": readiness.get("selected_goal"), "conversion_goal_meta": readiness.get("best_available_conversion_optimization"), "ads": [{"media_type": item["media_type"], "primary_text": item["primary_text"], "headline": item["headline"], "description": item["description"], "media_hash": hashes[index]} for index, item in enumerate(prepared_ads)]}
    key = creation_idempotency_key(empresa_uuid, stable_input)
    if payload.idempotency_key:
        key = f"{empresa_uuid}:full:{str(payload.idempotency_key).strip()}"
    if payload.resume_creation_id:
        resume_id = _uuid_or_400(payload.resume_creation_id, "resume_creation_id")
        row = (
            await db.execute(
                select(TrafficMetaCampaignCreation).where(
                    TrafficMetaCampaignCreation.id == resume_id,
                    TrafficMetaCampaignCreation.empresa_id == empresa_uuid,
                ).with_for_update()
            )
        ).scalars().first()
        if not row:
            raise HTTPException(status_code=404, detail="Estrutura parcial não encontrada para retomada.")
        if row.status != "draft" and (not row.meta_campaign_id or not row.meta_adset_id):
            raise HTTPException(status_code=409, detail="A estrutura selecionada não possui Campaign e AdSet confirmados.")
        if row.status == "created":
            return {"success": True, "already_created": True, "creation": _serialize_creation(row), "pipeline": _creation_pipeline(row)}
        if row.status not in {"partial_error", "draft"}:
            raise HTTPException(status_code=409, detail="Somente estruturas em estado parcial podem ser retomadas.")
        if normalize_ad_account_id(row.ad_account_id) != ad_account_id_for_write:
            raise HTTPException(status_code=409, detail="A conta configurada mudou; a estrutura parcial não pode ser retomada nesta conta.")
        if not prepared_ads:
            recovered_items = list((row.response_json or {}).get("ads") or [])
            for item in sorted(recovered_items, key=lambda value: int(value.get("sequence") or 0)):
                storage_key = str(item.get("server_image_reference") or "").strip()
                if not storage_key:
                    raise HTTPException(status_code=409, detail="O item do batch não possui referência de imagem server-side.")
                path = resolve_storage_path(storage_key, empresa_id=str(empresa_uuid))
                if not path.is_file():
                    raise HTTPException(status_code=409, detail="A imagem server-side do batch não está disponível.")
                prepared_ads.append({"media_type": "image", "mime_type": "image/png" if path.suffix.lower() == ".png" else "image/jpeg", "filename": item.get("original_filename") or item.get("media_name") or path.name, "content": path.read_bytes(), "primary_text": item.get("primary_text") or "", "headline": item.get("headline") or "", "description": item.get("description") or "", "item_key": item.get("item_key") or ""})
    else:
        row = (await db.execute(select(TrafficMetaCampaignCreation).where(TrafficMetaCampaignCreation.empresa_id == empresa_uuid, TrafficMetaCampaignCreation.idempotency_key == key))).scalars().first()
    if row and row.status == "created":
        return {"success": True, "already_created": True, "creation": _serialize_creation(row), "pipeline": _creation_pipeline(row)}
    if not row:
        sequence = int((await db.execute(select(func.coalesce(func.max(TrafficMetaCampaignCreation.campaign_sequence), 0) + 1).where(TrafficMetaCampaignCreation.empresa_id == empresa_uuid))).scalar_one())
        row = TrafficMetaCampaignCreation(
            empresa_id=empresa_uuid,
            traffic_meta_config_id=config.id,
            ad_account_id=ad_account_id_for_write,
            business_id=asset_snapshot.get("business_id"),
            whatsapp_connection_id=whatsapp.id,
            idempotency_key=key,
            name=generated_object_name(company.nome_empresa, empresa_uuid, "CAMPAIGN", sequence),
            adset_name=generated_object_name(company.nome_empresa, empresa_uuid, "ADSET", sequence),
            daily_budget_cents=daily_budget_cents,
            location_type=payload.location_type,
            page_id=page_id,
            instagram_account_id=asset_snapshot.get("instagram_account_id"),
            phone_number_id=phone_number_id,
            whatsapp_phone_number=asset_snapshot.get("whatsapp_phone_number"),
            waba_id=asset_snapshot.get("waba_id"),
            dataset_id=asset_snapshot.get("dataset_id"),
            campaign_sequence=sequence,
            status="pending",
            payload_json={
                "targeting": targeting,
                "targeting_snapshot": {"location_mode": payload.location_type, "location_input": payload.location_input, "location_display_name": payload.location_display_name, "latitude": payload.latitude, "longitude": payload.longitude, "radius": payload.radius_km, "distance_unit": "kilometer", "gender_mode": payload.gender_mode},
                "full": True,
                "business_goal": readiness.get("selected_goal"),
                "conversion_goal_key": readiness.get("selected_goal"),
                "conversion_goal_label": readiness.get("selected_goal_label"),
                "conversion_goal_meta": readiness.get("best_available_conversion_optimization"),
                "campaign_conversion_contract": readiness.get("campaign_conversion_contract"),
                "asset_snapshot": {key: value for key, value in asset_snapshot.items() if key not in {"page", "whatsapp", "ad_account"}},
            },
            response_json={"ads": []},
            created_by_user_id=current_user.id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    ad_account_id_for_write = normalize_ad_account_id(row.ad_account_id or ad_account_id_for_write)
    pipeline = dict(row.response_json or {})
    pipeline_ads = list(pipeline.get("ads") or [])
    if not pipeline.get("batch_persisted"):
        persisted_states = []
        for sequence, item in enumerate(prepared_ads, start=1):
            item_key = item.get("item_key") or hashlib.sha256(item["content"]).hexdigest()
            storage_key, digest = persist_traffic_image_bytes(empresa_id=str(empresa_uuid), batch_id=str(row.id), item_key=item_key, original_name=item["filename"], data=item["content"])
            state = next((value for value in pipeline_ads if value.get("sequence") == sequence), None) or {"sequence": sequence}
            state.update({"item_key": item_key, "original_filename": item["filename"], "content_hash": digest, "server_image_reference": storage_key, "media_name": item["filename"], "primary_text": item["primary_text"], "headline": item["headline"], "description": item["description"], "copy_source": "HUMAN" if item.get("copy_source") == "HUMAN" else "AI", "ai_status": "ready"})
            persisted_states.append(state)
        pipeline["ads"] = sorted(persisted_states, key=lambda value: int(value.get("sequence") or 0))
        pipeline["batch_persisted"] = True
        pipeline["server_items"] = len(persisted_states)
        pipeline_ads = pipeline["ads"]
        row.response_json = pipeline
        await db.commit()
    failed_stage = "CAMPAIGN"
    failed_operation = "campaign.create"
    try:
        parent_write_plan = _resume_parent_write_plan(meta_campaign_id=row.meta_campaign_id, meta_adset_id=row.meta_adset_id)
        if parent_write_plan["campaign"]:
            campaign_payload = build_campaign_payload(row.name, objective=CONVERSION_CAMPAIGN_OBJECTIVE)
            row.payload_json = {
                **(row.payload_json or {}),
                "campaign": campaign_payload,
                "campaign_http_form": _sanitize_http_form(graph_post_data(campaign_payload)),
            }
            validation, validation_error = await _validate_meta_write_payload(
                client,
                ad_account_id_for_write,
                object_type="campaign",
                payload=campaign_payload,
                graph_version=meta.get("graph_version"),
                goal=readiness.get("selected_goal"),
            )
            _append_write_audit(row, validation)
            await db.commit()
            if validation_error:
                row.error_json = {"stage": "CAMPAIGN_VALIDATE_ONLY", "validation": validation, "error": validation_error}
                await db.commit()
                _raise_validate_only_failure(validation, validation_error)
            response = await client.create_campaign(ad_account_id_for_write, campaign_payload)
            row.meta_campaign_id = str(response.get("id") or "").strip()
            if not row.meta_campaign_id:
                raise HTTPException(status_code=502, detail="Meta não retornou ID da campanha.")
            failed_operation = "campaign.read_after_create"
            read = await client.get_campaign(row.meta_campaign_id)
            _ensure_meta_paused_read(read, expected_id=row.meta_campaign_id, object_name="Campaign")
            row.campaign_status, row.status = str(read.get("status") or ""), "campaign_created"
            pipeline["campaign"] = {"id": row.meta_campaign_id, "name": row.name, "status": row.campaign_status}
            await db.commit()
        if parent_write_plan["adset"]:
            failed_stage = "ADSET"
            failed_operation = "adset.create"
            adset_payload = build_adset_payload(
                name=row.adset_name,
                campaign_id=row.meta_campaign_id,
                daily_budget_cents=row.daily_budget_cents,
                page_id=row.page_id,
                phone_number=row.whatsapp_phone_number,
                targeting=targeting,
                optimization_goal=readiness["best_available_conversion_optimization"],
            )
            row.payload_json = {
                **(row.payload_json or {}),
                "adset": adset_payload,
                "adset_http_form": _sanitize_http_form(graph_post_data(adset_payload)),
            }
            validation, validation_error = await _validate_meta_write_payload(
                client,
                ad_account_id_for_write,
                object_type="adset",
                payload=adset_payload,
                graph_version=meta.get("graph_version"),
                goal=readiness.get("selected_goal"),
            )
            _append_write_audit(row, validation)
            await db.commit()
            if validation_error:
                row.error_json = {"stage": "ADSET_VALIDATE_ONLY", "validation": validation, "error": validation_error}
                await db.commit()
                _raise_validate_only_failure(validation, validation_error)
            response = await client.create_adset(
                ad_account_id_for_write,
                adset_payload,
            )
            row.meta_adset_id = str(response.get("id") or "").strip()
            if not row.meta_adset_id:
                raise HTTPException(status_code=502, detail="Meta não retornou ID do conjunto de anúncios.")
            failed_operation = "adset.read_after_create"
            read = await client.get_adset(row.meta_adset_id)
            _ensure_meta_paused_read(read, expected_id=row.meta_adset_id, object_name="AdSet")
            row.adset_status, row.status = str(read.get("status") or ""), "adset_created"
            pipeline["adset"] = {"id": row.meta_adset_id, "name": row.adset_name, "status": row.adset_status}
            await db.commit()
        if payload.resume_creation_id and not pipeline.get("whatsapp_binding_fix_applied"):
            failed_stage = "ADSET"
            failed_operation = "adset.binding_read"
            binding_payload = {
                "promoted_object": {
                    "page_id": row.page_id,
                    "smart_pse_enabled": False,
                    "whatsapp_phone_number": row.whatsapp_phone_number,
                }
            }
            row.payload_json = {**(row.payload_json or {}), "adset_whatsapp_binding_update": binding_payload}
            await db.commit()
            binding_read = await client.get_adset(row.meta_adset_id)
            _ensure_meta_paused_read(binding_read, expected_id=row.meta_adset_id, object_name="AdSet")
            persisted_promoted = binding_read.get("promoted_object") if isinstance(binding_read.get("promoted_object"), dict) else {}
            golden_binding = _is_golden_whatsapp_binding(persisted_promoted, page_id=row.page_id, whatsapp_phone_number=row.whatsapp_phone_number)
            if not golden_binding:
                failed_operation = "adset.binding_update"
                await client.update_adset(row.meta_adset_id, binding_payload)
                failed_operation = "adset.binding_read_after_update"
                binding_read = await client.get_adset(row.meta_adset_id)
                _ensure_meta_paused_read(binding_read, expected_id=row.meta_adset_id, object_name="AdSet")
                persisted_promoted = binding_read.get("promoted_object") if isinstance(binding_read.get("promoted_object"), dict) else {}
                golden_binding = _is_golden_whatsapp_binding(persisted_promoted, page_id=row.page_id, whatsapp_phone_number=row.whatsapp_phone_number)
            if not golden_binding:
                raise HTTPException(status_code=502, detail="Meta não confirmou o binding WhatsApp do AdSet retomado.")
            pipeline["whatsapp_binding_fix_applied"] = True
            pipeline["adset"] = {**(pipeline.get("adset") or {}), "promoted_object": persisted_promoted, "status": binding_read.get("status")}
            row.response_json = pipeline
            await db.commit()
        failed_operation = "ad.list_for_reconciliation"
        existing_ads_by_name: dict[str, dict[str, Any]] = {
            str(existing_ad["name"]): existing_ad
            for existing_ad in await client.list_ads_for_adset(row.meta_adset_id)
            if existing_ad.get("name")
        }
        failed_operation = "creative.list_for_reconciliation"
        existing_creatives_by_name: dict[str, dict[str, Any]] = {
            str(existing_creative["name"]): existing_creative
            for existing_creative in await client.list_creatives(ad_account_id_for_write)
            if existing_creative.get("name")
        }

        for index, item in enumerate(prepared_ads, start=1):
            state = next((value for value in pipeline_ads if value.get("sequence") == index), None) or {"sequence": index, "creative_name": generated_object_name(company.nome_empresa, empresa_uuid, "CREATIVE", row.campaign_sequence, ad_sequence=index), "ad_name": generated_object_name(company.nome_empresa, empresa_uuid, "AD", row.campaign_sequence, ad_sequence=index)}
            state = _ensure_resume_item_names(state=state, company_name=company.nome_empresa, empresa_id=empresa_uuid, campaign_sequence=row.campaign_sequence, item_sequence=index)
            state.update({"item_key": item.get("item_key"), "media_name": item.get("filename"), "primary_text": item["primary_text"], "headline": item["headline"], "description": item["description"]})
            existing_ad = existing_ads_by_name.get(str(state.get("ad_name") or ""))
            if not state.get("ad_id") and existing_ad:
                existing_creative = existing_ad.get("creative") if isinstance(existing_ad.get("creative"), dict) else {}
                state.update({
                    "ad_id": str(existing_ad.get("id") or "").strip(),
                    "creative_id": str(existing_creative.get("id") or "").strip() or state.get("creative_id"),
                    "ad_status": str(existing_ad.get("status") or ""),
                    "status": "ad_created",
                })
                pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
                pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
                row.response_json, row.error_json, row.status = pipeline, None, "created"
                await db.commit()
                continue
            if state.get("ad_id"):
                try:
                    recovered_ad = await _read_ad_from_ad_account(client, ad_account_id_for_write, ad_id=state["ad_id"], adset_id=row.meta_adset_id)
                    _ensure_meta_paused_read(recovered_ad, expected_id=state["ad_id"], object_name="Ad")
                    state["ad_status"] = str(recovered_ad.get("status") or "")
                    state["status"] = "ad_created"
                    pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
                    pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
                    row.response_json, row.error_json = pipeline, None
                    await db.commit()
                    continue
                except Exception:
                    state["status"] = "ad_created_unverified"
                    pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
                    pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
                    row.response_json = pipeline
                    await db.commit()
                    raise HTTPException(status_code=502, detail=f"Ad do anúncio {index} existe, mas não foi confirmado pela Meta.")
            if _resume_item_write_plan(state)["media"]:
                failed_stage = "MEDIA"
                failed_operation = "media.upload"
                uploaded = await (client.upload_image(ad_account_id_for_write, item["content"], item["filename"], item["mime_type"]) if item["media_type"] == "image" else client.upload_video(ad_account_id_for_write, item["content"], item["filename"], item["mime_type"]))
                if item["media_type"] == "image":
                    image_entry = next(iter((uploaded.get("images") or {}).items()), (None, {}))[1]
                    state["media_reference"] = str((image_entry or {}).get("hash") or "")
                else:
                    state["media_reference"] = str(uploaded.get("id") or uploaded.get("video_id") or "")
                if not state["media_reference"]:
                    raise HTTPException(status_code=502, detail=f"Meta não confirmou a mídia do anúncio {index}.")
                state["status"] = "media_uploaded"
                pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
                pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
                row.response_json = pipeline
                await db.commit()
            if item["media_type"] == "video":
                video_read = await client.get_video(state["media_reference"])
                video_status = str((video_read.get("status") or {}).get("video_status") if isinstance(video_read.get("status"), dict) else video_read.get("status") or "").lower()
                if video_status not in {"ready", "completed", "processed"}:
                    state["status"] = "media_processing"
                    pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
                    pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
                    row.response_json, row.status = pipeline, "partial_error"
                    row.error_json = {"code": "MEDIA_PROCESSING_PENDING", "message": f"A mídia do anúncio {index} ainda está sendo processada pela Meta."}
                    await db.commit()
                    return JSONResponse(status_code=207, content={"success": False, "credential": meta, "partial_failure": True, "creation": _serialize_creation(row), "pipeline": _creation_pipeline(row)})
            if _resume_item_write_plan(state)["creative"]:
                failed_stage = "CREATIVE"
                existing_creative = existing_creatives_by_name.get(str(state.get("creative_name") or ""))
                if existing_creative:
                    state["creative_id"] = str(existing_creative.get("id") or "").strip()
            if _resume_item_write_plan(state)["creative"]:
                failed_operation = "creative.create"
                creative = await client.create_creative(ad_account_id_for_write, build_creative_payload(name=state["creative_name"], page_id=row.page_id, primary_text=item["primary_text"], headline=item["headline"], description=item["description"], phone_number_id=row.phone_number_id, media_type=item["media_type"], media_reference=state["media_reference"], instagram_account_id=row.instagram_account_id))
                state["creative_id"] = str(creative.get("id") or "").strip()
                if not state["creative_id"]:
                    raise HTTPException(status_code=502, detail=f"Meta não retornou Creative ID para o anúncio {index}.")
                state["status"] = "creative_created_pending_verification"
                pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
                pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
                row.response_json = pipeline
                await db.commit()
            if state.get("creative_id") and state.get("status") != "creative_created":
                failed_operation = "creative.read_after_create"
                creative_read = await client.get_creative(state["creative_id"])
                if str(creative_read.get("id") or "").strip() != state["creative_id"]:
                    raise HTTPException(status_code=502, detail=f"Creative do anúncio {index} não confirmado pela Meta.")
                state["status"] = "creative_created"
                pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
                pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
                row.response_json = pipeline
                await db.commit()
            if _resume_item_write_plan(state)["ad"]:
                failed_stage = "AD"
                failed_operation = "ad.create"
                ad = await client.create_ad(ad_account_id_for_write, build_ad_payload(name=state["ad_name"], adset_id=row.meta_adset_id, creative_id=state["creative_id"]))
                state["ad_id"] = str(ad.get("id") or "").strip()
                if not state["ad_id"]:
                    raise HTTPException(status_code=502, detail=f"Meta não retornou Ad ID para o anúncio {index}.")
                state["status"] = "ad_created_pending_verification"
                pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
                pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
                row.response_json = pipeline
                await db.commit()
                failed_operation = "ad.read_after_create"
                ad_read = await _read_ad_from_ad_account(client, ad_account_id_for_write, ad_id=state["ad_id"], adset_id=row.meta_adset_id)
                _ensure_meta_paused_read(ad_read, expected_id=state["ad_id"], object_name="Ad")
                state["ad_status"] = str(ad_read.get("status") or "")
                state["status"] = "ad_created"
            pipeline_ads = [value for value in pipeline_ads if value.get("sequence") != index] + [state]
            pipeline["ads"] = sorted(pipeline_ads, key=lambda value: value.get("sequence", 0))
            row.response_json = pipeline
            await db.commit()
            row.status, row.error_json, row.response_json = "created", None, pipeline
        await db.commit()
        await db.refresh(row)
        return {"success": True, "credential": meta, "creation": _serialize_creation(row), "pipeline": _creation_pipeline(row)}
    except Exception as exc:
        error = sanitize_graph_error(exc)
        row.response_json = pipeline
        row.status = "partial_error" if row.meta_campaign_id else "error"
        row.error_json = {
            **error,
            "failed_stage": failed_stage,
            "write_audit": _write_audit(
                operation=failed_operation,
                failed_stage=failed_stage,
                graph_version=meta.get("graph_version"),
                ad_account_id=ad_account_id_for_write,
                error=error,
            ),
        }
        await db.commit()
        await db.refresh(row)
        body = {"success": False, "credential": meta, "partial_failure": bool(row.meta_campaign_id), "creation": _serialize_creation(row), "pipeline": _creation_pipeline(row)}
        if row.meta_campaign_id:
            return JSONResponse(status_code=207, content=body)
        raise HTTPException(status_code=502, detail=body)


@router.post("/campaigns/{creation_id}/batch")
async def create_batch_ads_on_existing_structure(
    empresa_id: str,
    creation_id: str,
    payload: TrafficMetaBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    """Create only the missing image creatives/ads on an existing structure."""
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    creation_uuid = _uuid_or_400(creation_id, "creation_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    config = (await db.execute(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == empresa_uuid))).scalars().first()
    row = (
        await db.execute(
            select(TrafficMetaCampaignCreation).where(
                TrafficMetaCampaignCreation.id == creation_uuid,
                TrafficMetaCampaignCreation.empresa_id == empresa_uuid,
            )
        )
    ).scalars().first()
    if not row or not row.meta_campaign_id or not row.meta_adset_id:
        raise HTTPException(status_code=404, detail="Estrutura Meta existente não encontrada ou incompleta.")
    if not config or normalize_ad_account_id(row.ad_account_id) != normalize_ad_account_id(config.ad_account_id):
        raise HTTPException(status_code=409, detail="A estrutura não pertence à conta Meta atualmente configurada.")
    try:
        if any(str(item.get("media_type") or "").strip().lower() != "image" for item in payload.ads):
            raise HTTPException(status_code=400, detail="O batch inicial aceita somente imagens.")
        prepared_ads = [_ensure_full_ad_input(item, index) for index, item in enumerate(payload.ads, start=1)]
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_MANAGEMENT, ad_account_id=normalize_ad_account_id(row.ad_account_id), allow_admin_fallback=False)
        ad_account_id = normalize_ad_account_id(row.ad_account_id)
        pipeline = dict(row.response_json or {})
        pipeline_ads = list(pipeline.get("ads") or [])
        next_sequence = max([int(item.get("sequence") or 0) for item in pipeline_ads] or [0]) + 1
        for item in prepared_ads:
            item_key = _clean(item.get("item_key")) if isinstance(item, dict) else ""
            item_key = item_key or hashlib.sha256(item["content"] + item["primary_text"].encode("utf-8") + item["headline"].encode("utf-8")).hexdigest()
            state = next((value for value in pipeline_ads if value.get("item_key") == item_key), None)
            if state and state.get("ad_id") and state.get("ad_status") == "PAUSED":
                continue
            if not state:
                sequence = next_sequence
                next_sequence += 1
                state = {
                    "sequence": sequence,
                    "item_key": item_key,
                    "media_name": item.get("filename"),
                    "primary_text": item["primary_text"],
                    "headline": item["headline"],
                    "description": item["description"],
                    "creative_name": generated_object_name(row.name.split(" | CAMPAIGN")[0], empresa_uuid, "CREATIVE", row.campaign_sequence or 1, ad_sequence=sequence),
                    "ad_name": generated_object_name(row.name.split(" | CAMPAIGN")[0], empresa_uuid, "AD", row.campaign_sequence or 1, ad_sequence=sequence),
                }
                pipeline_ads.append(state)
            if not state.get("media_reference"):
                uploaded = await client.upload_image(ad_account_id, item["content"], item["filename"], item["mime_type"])
                image_entry = next(iter((uploaded.get("images") or {}).items()), (None, {}))[1]
                state["media_reference"] = str((image_entry or {}).get("hash") or "")
                if not state["media_reference"]:
                    raise HTTPException(status_code=502, detail="Meta não confirmou a imagem do item.")
                state["media_status"] = "uploaded"
            if not state.get("creative_id"):
                creative = await client.create_creative(
                    ad_account_id,
                    build_creative_payload(
                        name=state["creative_name"],
                        page_id=row.page_id,
                        primary_text=item["primary_text"],
                        headline=item["headline"],
                        description=item["description"],
                        phone_number_id=row.phone_number_id,
                        media_type="image",
                        media_reference=state["media_reference"],
                        instagram_account_id=row.instagram_account_id,
                    ),
                )
                state["creative_id"] = str(creative.get("id") or "")
                if not state["creative_id"]:
                    raise HTTPException(status_code=502, detail="Meta não retornou Creative ID do item.")
                creative_read = await client.get_creative(state["creative_id"])
                if str(creative_read.get("id") or "") != state["creative_id"]:
                    raise HTTPException(status_code=502, detail="Creative do item não confirmado pela Meta.")
                state["creative_status"] = "created"
            if not state.get("ad_id"):
                ad = await client.create_ad(ad_account_id, build_ad_payload(name=state["ad_name"], adset_id=row.meta_adset_id, creative_id=state["creative_id"]))
                state["ad_id"] = str(ad.get("id") or "")
                if not state["ad_id"]:
                    raise HTTPException(status_code=502, detail="Meta não retornou Ad ID do item.")
                ad_read = await client.get_ad(state["ad_id"])
                _ensure_meta_paused_read(ad_read, expected_id=state["ad_id"], object_name="Ad")
                state["ad_status"] = str(ad_read.get("status") or "")
                state["ad_effective_status"] = str(ad_read.get("effective_status") or "")
                state["status"] = "ad_created"
            pipeline["ads"] = sorted(pipeline_ads, key=lambda value: int(value.get("sequence") or 0))
            row.response_json = pipeline
            row.status = "created"
            row.error_json = None
            await db.commit()
        await db.refresh(row)
        return {"success": True, "credential": meta, "creation": _serialize_creation(row), "pipeline": _creation_pipeline(row)}
    except Exception as exc:
        error = sanitize_graph_error(exc)
        row.response_json = pipeline if "pipeline" in locals() else dict(row.response_json or {})
        row.status = "partial_error"
        row.error_json = {**error, "failed_stage": "BATCH_ITEM"}
        await db.commit()
        await db.refresh(row)
        return JSONResponse(status_code=207, content={"success": False, "credential": meta if "meta" in locals() else {}, "partial_failure": True, "creation": _serialize_creation(row), "pipeline": _creation_pipeline(row)})


@router.post("/campaign-adsets")
async def create_paused_campaign_and_adset(
    empresa_id: str,
    payload: TrafficMetaCampaignCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    settings = meta_settings()
    if not settings.marketing_write_enabled or not settings.marketing_campaign_create_enabled or not settings.marketing_adset_create_enabled:
        raise HTTPException(status_code=403, detail="Criação de campanhas Meta está desabilitada.")
    config = await _get_config(db, empresa_uuid)
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Conta de anúncios não vinculada para esta empresa.")
    ad_account_id_for_write = _current_configured_ad_account_id(config)
    selected_whatsapp_id = _uuid_or_400(payload.whatsapp_connection_id, "whatsapp_connection_id") if payload.whatsapp_connection_id else None
    try:
        daily_budget_cents = brl_to_meta_cents(payload.daily_budget_brl)
        targeting = (
            build_brazil_targeting()
            if payload.location_type == "brazil"
            else build_radius_targeting(payload.latitude, payload.longitude, payload.radius_km)
        )
    except TrafficCampaignValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_MANAGEMENT, ad_account_id=ad_account_id_for_write, allow_admin_fallback=False)
    ad_account = await client.validate_ad_account(ad_account_id_for_write)
    asset_snapshot = await _resolve_campaign_assets_for_write(
        db,
        empresa_uuid,
        config=config,
        page_id=payload.page_id,
        whatsapp_connection_id=selected_whatsapp_id,
        client=client,
        ad_account=ad_account,
    )
    readiness = await _campaign_conversion_readiness(
        db,
        empresa_uuid,
        conversion_goal_key=_requested_business_goal(payload),
        page_id=asset_snapshot["page_id"],
        whatsapp_connection_id=selected_whatsapp_id,
    )
    if not readiness.get("ready"):
        raise HTTPException(status_code=409, detail={"message": "Campanha WhatsApp de conversão não está pronta.", "readiness": readiness})
    campaign_payload = build_campaign_payload(payload.campaign_name, objective=CONVERSION_CAMPAIGN_OBJECTIVE)
    idempotency_payload = {
        "campaign": campaign_payload,
        "daily_budget_cents": daily_budget_cents,
        "location_type": payload.location_type,
        "targeting": targeting,
        "ad_account_id": ad_account_id_for_write,
        "page_id": _clean(payload.page_id or config.page_id),
        "whatsapp_connection_id": str(selected_whatsapp_id or config.whatsapp_connection_id or ""),
        "business_goal": readiness.get("selected_goal"),
        "conversion_goal_key": readiness.get("selected_goal"),
    }
    key = creation_idempotency_key(empresa_uuid, idempotency_payload)
    existing = (
        await db.execute(
            select(TrafficMetaCampaignCreation).where(
                TrafficMetaCampaignCreation.empresa_id == empresa_uuid,
                TrafficMetaCampaignCreation.idempotency_key == key,
            )
        )
    ).scalars().first()
    if existing and existing.meta_campaign_id and existing.meta_adset_id:
        return {"success": True, "creation": _serialize_creation(existing), "already_created": True}
    whatsapp = asset_snapshot["whatsapp"]
    page_id = asset_snapshot["page_id"]
    phone_number_id = asset_snapshot["phone_number_id"]

    row = existing
    if not row:
        row = TrafficMetaCampaignCreation(
            empresa_id=empresa_uuid,
            traffic_meta_config_id=config.id,
            ad_account_id=ad_account_id_for_write,
            business_id=asset_snapshot.get("business_id"),
            whatsapp_connection_id=whatsapp.id,
            idempotency_key=key,
            name=campaign_payload["name"],
            adset_name=campaign_payload["name"],
            daily_budget_cents=daily_budget_cents,
            location_type=payload.location_type,
            page_id=page_id,
            instagram_account_id=asset_snapshot.get("instagram_account_id"),
            phone_number_id=phone_number_id,
            whatsapp_phone_number=asset_snapshot.get("whatsapp_phone_number"),
            waba_id=asset_snapshot.get("waba_id"),
            dataset_id=asset_snapshot.get("dataset_id"),
            status="pending",
            payload_json={
                "campaign": campaign_payload,
                "campaign_http_form": _sanitize_http_form(graph_post_data(campaign_payload)),
                "targeting": targeting,
                "business_goal": readiness.get("selected_goal"),
                "conversion_goal_key": readiness.get("selected_goal"),
                "conversion_goal_label": readiness.get("selected_goal_label"),
                "conversion_goal_meta": readiness.get("best_available_conversion_optimization"),
                "campaign_conversion_contract": readiness.get("campaign_conversion_contract"),
                "asset_snapshot": {key: value for key, value in asset_snapshot.items() if key not in {"page", "whatsapp", "ad_account"}},
            },
            response_json={},
            created_by_user_id=current_user.id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

    try:
        ad_account_id_for_write = normalize_ad_account_id(row.ad_account_id or ad_account_id_for_write)
        if not row.meta_campaign_id:
            validation, validation_error = await _validate_meta_write_payload(
                client,
                ad_account_id_for_write,
                object_type="campaign",
                payload=campaign_payload,
                graph_version=meta.get("graph_version"),
                goal=readiness.get("selected_goal"),
            )
            _append_write_audit(row, validation)
            await db.commit()
            if validation_error:
                row.error_json = {"stage": "CAMPAIGN_VALIDATE_ONLY", "validation": validation, "error": validation_error}
                await db.commit()
                _raise_validate_only_failure(validation, validation_error)
            campaign_response = await client.create_campaign(ad_account_id_for_write, campaign_payload)
            campaign_id = str(campaign_response.get("id") or "").strip()
            if not campaign_id:
                raise HTTPException(status_code=400, detail="Meta não retornou ID da campanha.")
            campaign_read = await client.get_campaign(campaign_id)
            _ensure_meta_paused_read(campaign_read, expected_id=campaign_id, object_name="Campaign")
            row.meta_campaign_id = campaign_id
            row.campaign_status = str(campaign_read.get("status") or "")
            row.status = "campaign_created"
            row.response_json = {**(row.response_json or {}), "campaign": campaign_read}
            _append_write_audit(row, _write_audit(operation="campaign.create", graph_version=meta.get("graph_version"), ad_account_id=ad_account_id_for_write, response=campaign_read))
            await db.commit()
            await db.refresh(row)

        adset_payload = build_adset_payload(
            name=row.adset_name,
            campaign_id=str(row.meta_campaign_id),
            daily_budget_cents=row.daily_budget_cents,
            page_id=row.page_id,
            phone_number=row.whatsapp_phone_number,
            targeting=targeting,
            optimization_goal=readiness["best_available_conversion_optimization"],
        )
        row.payload_json = {**(row.payload_json or {}), "adset": adset_payload}
        row.payload_json = {**(row.payload_json or {}), "adset_http_form": _sanitize_http_form(graph_post_data(adset_payload))}
        if not row.meta_adset_id:
            validation, validation_error = await _validate_meta_write_payload(
                client,
                ad_account_id_for_write,
                object_type="adset",
                payload=adset_payload,
                graph_version=meta.get("graph_version"),
                goal=readiness.get("selected_goal"),
            )
            _append_write_audit(row, validation)
            await db.commit()
            if validation_error:
                row.error_json = {"stage": "ADSET_VALIDATE_ONLY", "validation": validation, "error": validation_error}
                await db.commit()
                _raise_validate_only_failure(validation, validation_error)
            adset_response = await client.create_adset(ad_account_id_for_write, adset_payload)
            adset_id = str(adset_response.get("id") or "").strip()
            if not adset_id:
                raise HTTPException(status_code=400, detail="Meta não retornou ID do conjunto de anúncios.")
            adset_read = await client.get_adset(adset_id)
            _ensure_meta_paused_read(adset_read, expected_id=adset_id, object_name="AdSet")
            row.meta_adset_id = adset_id
            row.adset_status = str(adset_read.get("status") or "")
            row.status = "created"
            row.response_json = {**(row.response_json or {}), "adset": adset_read}
            _append_write_audit(row, _write_audit(operation="adset.create", graph_version=meta.get("graph_version"), ad_account_id=ad_account_id_for_write, response=adset_read))
            row.error_json = None
            if not config.page_id:
                config.page_id = row.page_id
            await db.commit()
            await db.refresh(row)
        return {"success": True, "credential": meta, "creation": _serialize_creation(row), "already_created": False}
    except Exception as exc:
        error = sanitize_graph_error(exc)
        row.status = "partial_error" if row.meta_campaign_id else "error"
        row.error_json = {
            **error,
            "failed_stage": "ADSET" if row.meta_campaign_id else "CAMPAIGN",
            "write_audit": _write_audit(
                operation="adset.create" if row.meta_campaign_id else "campaign.create",
                failed_stage="ADSET" if row.meta_campaign_id else "CAMPAIGN",
                graph_version=meta.get("graph_version"),
                ad_account_id=ad_account_id_for_write,
                error=error,
            ),
        }
        await db.commit()
        await db.refresh(row)
        body = {"success": False, "credential": meta, "creation": _serialize_creation(row), "partial_failure": bool(row.meta_campaign_id)}
        if row.meta_campaign_id:
            return JSONResponse(status_code=207, content=body)
        raise HTTPException(status_code=502, detail=body)


@router.get("/credentials/status")
async def credential_status(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        readiness = await marketing_ads_readiness(db, empresa_id=empresa_uuid)
        return {
            **readiness,
            "credential_source": readiness.get("token_source"),
            "graph_version": meta_settings().marketing_graph_api_version,
            "ads_read": bool(readiness.get("permissions", {}).get("ads_read")),
            "ads_management": bool(readiness.get("permissions", {}).get("ads_management")),
            "business_management": bool(readiness.get("permissions", {}).get("business_management")),
            "admin_fallback": marketing_admin_status(empresa_uuid),
            "app_capability": "unknown_requires_meta_dashboard",
            "token_granted_permission": "verified_with_me_permissions",
            "asset_access_contract": "verified_by_tenant_ad_account_and_graph_reads",
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "marketing_readiness_status": "ERROR",
            "token_present": False,
            "credential_source": None,
            "credential_error": sanitize_graph_error(exc),
            "action_required": "Não foi possível consultar o status da Marketing API.",
            "admin_fallback": marketing_admin_status(empresa_uuid),
        }


@router.get("/me")
async def get_me(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ)
        return {"credential": meta, "me": await client.get_me()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/permissions")
async def get_permissions(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ)
        return {"credential": meta, "permissions": await client.get_permissions()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/adaccounts")
async def list_ad_accounts(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ)
        return {"credential": meta, "adaccounts": await client.list_ad_accounts()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_graph_error(exc)) from exc


@router.get("/adaccounts/{ad_account_id}")
async def validate_ad_account(
    empresa_id: str,
    ad_account_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    _ensure_ad_account_binding_admin(current_user, empresa_uuid)
    config = await _get_config(db, empresa_uuid)
    if config and config.ad_account_id and normalize_ad_account_id(config.ad_account_id) != normalize_ad_account_id(ad_account_id):
        raise HTTPException(status_code=403, detail="Ad Account fora do escopo configurado para esta empresa.")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ)
        return {"credential": meta, "adaccount": await client.validate_ad_account(ad_account_id)}
    except Exception as exc:
        _raise_marketing_read_error(exc)


@router.get("/pages")
async def list_pages(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ)
        return {"credential": meta, "pages": await client.list_pages()}
    except Exception as exc:
        _raise_marketing_read_error(exc)


@router.get("/campaigns")
async def list_campaigns(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    config = await _get_config(db, empresa_uuid)
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Ad Account não vinculada para esta empresa.")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id, allow_admin_fallback=True)
        return {"credential": meta, "ad_account_id": config.ad_account_id, "campaigns": await client.list_campaigns(config.ad_account_id)}
    except Exception as exc:
        _raise_marketing_read_error(exc)


@router.get("/adsets")
async def list_adsets(
    empresa_id: str,
    campaign_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    config = await _get_config(db, empresa_uuid)
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Ad Account não vinculada para esta empresa.")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id, allow_admin_fallback=True)
        if campaign_id:
            await client.get_scoped_campaign(campaign_id, config.ad_account_id)
        return {"credential": meta, "ad_account_id": config.ad_account_id, "adsets": await client.list_adsets(config.ad_account_id, campaign_id=campaign_id)}
    except Exception as exc:
        _raise_marketing_read_error(exc)


@router.get("/ads")
async def list_ads(
    empresa_id: str,
    campaign_id: str | None = None,
    adset_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    config = await _get_config(db, empresa_uuid)
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Ad Account não vinculada para esta empresa.")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id, allow_admin_fallback=True)
        if campaign_id:
            await client.get_scoped_campaign(campaign_id, config.ad_account_id)
        if adset_id:
            await client.get_scoped_adset(adset_id, config.ad_account_id)
        return {
            "credential": meta,
            "ad_account_id": config.ad_account_id,
            "ads": await client.list_ads(config.ad_account_id, campaign_id=campaign_id, adset_id=adset_id),
        }
    except Exception as exc:
        _raise_marketing_read_error(exc)


@router.get("/creatives/{creative_id}")
async def get_creative(
    empresa_id: str,
    creative_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    config = await _get_config(db, empresa_uuid)
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Ad Account não vinculada para esta empresa.")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id, allow_admin_fallback=True)
        return {"credential": meta, "creative": await client.get_scoped_creative(creative_id, config.ad_account_id)}
    except Exception as exc:
        _raise_marketing_read_error(exc)


@router.get("/campaigns/{campaign_id}/inspect")
async def inspect_campaign(
    empresa_id: str,
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    config = await _get_config(db, empresa_uuid)
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Ad Account não vinculada para esta empresa.")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id, allow_admin_fallback=True)
        campaign = await client.get_scoped_campaign(campaign_id, config.ad_account_id)
        adsets = await client.list_adsets(config.ad_account_id, campaign_id=campaign_id)
        ads = await client.list_ads(config.ad_account_id, campaign_id=campaign_id)
        creative_ids = []
        for ad in ads:
            creative = ad.get("creative") if isinstance(ad.get("creative"), dict) else {}
            creative_id = str(creative.get("id") or "").strip()
            if creative_id and creative_id not in creative_ids:
                creative_ids.append(creative_id)
        creatives = [await client.get_scoped_creative(item, config.ad_account_id) for item in creative_ids[:100]]
        return {
            "credential": meta,
            "campaign": campaign,
            "adsets": adsets,
            "ads": ads,
            "creatives": creatives,
            "ctwa_structure_inspector": True,
        }
    except Exception as exc:
        _raise_marketing_read_error(exc)


@router.get("/insights")
async def read_insights(
    empresa_id: str,
    level: str = Query(default="account", pattern="^(account|campaign|adset|ad)$"),
    object_id: str | None = None,
    date_preset: str = "last_30d",
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    config = await _get_config(db, empresa_uuid)
    if not config or not config.ad_account_id:
        raise HTTPException(status_code=409, detail="Ad Account não vinculada para esta empresa.")
    target_id = object_id or (config.ad_account_id if level == "account" else None)
    if not target_id:
        raise HTTPException(status_code=409, detail="Objeto de insights não informado ou Ad Account não vinculada.")
    try:
        client, meta = await _client(db, empresa_uuid, MetaMarketingCapability.ADS_READ, ad_account_id=config.ad_account_id, allow_admin_fallback=True)
        if level == "account":
            if normalize_ad_account_id(target_id) != normalize_ad_account_id(config.ad_account_id):
                raise HTTPException(status_code=403, detail="Ad Account fora do escopo desta empresa.")
        elif level == "campaign":
            await client.get_scoped_campaign(target_id, config.ad_account_id)
        elif level == "adset":
            await client.get_scoped_adset(target_id, config.ad_account_id)
        elif level == "ad":
            await client.get_scoped_ad(target_id, config.ad_account_id)
        return {"credential": meta, "level": level, "object_id": target_id, "insights": await client.get_insights(target_id, level=level, date_preset=date_preset)}
    except Exception as exc:
        _raise_marketing_read_error(exc)
