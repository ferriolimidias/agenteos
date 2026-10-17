import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import get_current_user, require_super_admin, require_tenant_access
from app.core.secret_sanitizer import sanitize_for_api, sanitize_for_log
from app.services.evolution_service import (
    aplicar_settings_padrao_evolution,
    consultar_status_conexao,
    evolution_base_url,
    evolution_global_api_key,
    logout_evolution_whatsapp_empresa,
    obter_qr_code,
    provisionar_whatsapp_empresa,
    reset_evolution_whatsapp_empresa,
)
from app.schemas import ConexaoCreate, ConexaoResponse, ConexaoUpdate
from app.services.meta.embedded_signup import (
    CONNECTION_MODE_CLOUD_API,
    CONNECTION_MODE_COEXISTENCE,
    META_OFFICIAL_CHANNEL,
    META_OFFICIAL_PROVIDER,
    classify_meta_error_for_user,
    decrypt_connection_token,
    detect_coexistence,
    discover_assets,
    encrypted_credentials_from_token,
    extract_signup_identifiers,
    mask_meta_id,
    public_embedded_signup_config,
    sanitize_meta_error,
    utc_now_iso,
)
from app.services.meta.graph_client import MetaGraphClient
from app.services.meta.credential_crypto import encrypt_meta_secret
from app.services.meta.marketing_api import MetaMarketingApiClient
from app.services.meta.marketing_api import normalize_ad_account_id
from app.services.meta.marketing_credentials import (
    META_MARKETING_CHANNEL,
    META_MARKETING_OAUTH_PERMISSIONS,
    META_MARKETING_PROVIDER,
)
from app.services.meta.marketing_readiness import _business_account_binding, granted_permissions
from app.services.meta.settings import meta_settings
from app.services.meta.signup_state import (
    BUSINESS_APP_ONBOARDING,
    MARKETING_OAUTH_STATE_PURPOSE,
    MetaSignupStateError,
    create_signup_state,
    normalize_onboarding_mode,
    validate_signup_state,
)
from app.services.whatsapp_operational_status import resolve_whatsapp_operational_status
from db.database import get_db
from db.models import Conexao, Empresa, TipoConexao, TrafficMetaConfig, Usuario, is_admin_empresa_role, is_meta_app_reviewer_role, is_root_admin_email, is_super_admin_role


router = APIRouter(
    prefix="/empresas/{empresa_id}/conexoes",
    tags=["Conexoes"]
)

status_router = APIRouter(
    prefix="/conexoes",
    tags=["Conexoes"]
)

whatsapp_router = APIRouter(
    prefix="/empresas/{empresa_id}/whatsapp",
    tags=["WhatsApp Tenant"],
)

STATUSS_CONEXAO_ATIVOS = {"ativo", "connected", "open", "online"}
META_CONNECTION_STATUSES = {
    "pending",
    "pending_session",
    "exchanging_code",
    "token_obtained",
    "discovering_assets",
    "number_selection_required",
    "connected",
    "attention",
    "retryable_error",
    "reauth_required",
    "token_expired",
    "revoked",
    "disconnected",
    "error",
}


class MetaCompleteRequest(BaseModel):
    state: str
    onboarding_mode: str = "standard_embedded_signup"
    code: str | None = None
    error: str | None = None
    error_reason: str | None = None
    cancelled: bool = False
    signup_payload: dict[str, Any] = Field(default_factory=dict)
    mock_token: str | None = None
    mock_assets: dict[str, Any] | None = None


class MetaSelectNumberRequest(BaseModel):
    conexao_id: str
    business_id: str | None = None
    waba_id: str
    phone_number_id: str


class MetaConnectionActionRequest(BaseModel):
    confirm: bool = False
    mock_assets: dict[str, Any] | None = None


class MetaSessionRequest(BaseModel):
    onboarding_mode: str = "standard_embedded_signup"


class MetaMarketingOAuthCompleteRequest(BaseModel):
    state: str = Field(min_length=20, max_length=4096)
    code: str = Field(min_length=8, max_length=4096)


def _audit_meta(event: str, *, empresa_id: str, usuario_id: str | None = None, conexao_id: str | None = None, detail: Any = None) -> None:
    payload = sanitize_for_log(
        {
            "event": event,
            "empresa_id": empresa_id,
            "usuario_id": usuario_id,
            "conexao_id": conexao_id,
            "detail": detail or {},
        }
    )
    print(f"[AUDIT META OFFICIAL] {payload}")


def _normalizar_tipo(tipo: str) -> TipoConexao:
    try:
        return TipoConexao(tipo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Tipo de conexão inválido.") from exc


def _webhook_suffix(tipo: TipoConexao) -> str:
    if tipo == TipoConexao.EVOLUTION:
        return "evolution"
    if tipo in (TipoConexao.META, TipoConexao.INSTAGRAM):
        return "meta"
    return tipo.value


def _montar_webhook_url(request: Request, empresa_id: str, tipo: TipoConexao) -> tuple[str, str]:
    suffix = _webhook_suffix(tipo)
    base_url = str(request.base_url).rstrip("/")
    path = f"/api/webhook/{empresa_id}/{suffix}"
    return f"{base_url}{path}", path


def _mask_credenciais(tipo: TipoConexao, credenciais: dict[str, Any] | None) -> dict[str, Any]:
    del tipo
    return sanitize_for_api(dict(credenciais or {}))


def _normalizar_status_conexao(status: str | None, default: str = "ativo") -> str:
    valor = str(status or "").strip()
    if not valor:
        return default
    if valor.lower() in STATUSS_CONEXAO_ATIVOS:
        return "ativo"
    return valor


def _validar_payload(tipo: TipoConexao, payload: ConexaoCreate) -> tuple[str, dict[str, Any]]:
    credenciais = payload.credenciais or {}

    if tipo == TipoConexao.INSTAGRAM:
        raise HTTPException(
            status_code=410,
            detail="O Instagram possui uma conexão canônica em /api/empresas/{empresa_id}/instagram.",
        )

    if tipo == TipoConexao.EVOLUTION:
        base_url = evolution_base_url()
        if not str(credenciais.get("evolution_url") or "").strip():
            credenciais["evolution_url"] = base_url
        required_fields = ["evolution_apikey", "evolution_instance"]
        missing = [field for field in required_fields if not credenciais.get(field)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Campos obrigatórios ausentes para Evolution: {', '.join(missing)}"
            )
        if not str(credenciais.get("evolution_url") or "").strip():
            raise HTTPException(
                status_code=503,
                detail="EVOLUTION_API_URL não configurada no servidor.",
            )
        evolution_instance = str(credenciais.get("evolution_instance") or "").strip()
        credenciais["evolution_instance"] = evolution_instance
        # "Nome amigável" opcional: quando vazio, usa automaticamente o identificador da instância.
        nome_instancia = (payload.nome_instancia or evolution_instance or "").strip()
        if not nome_instancia:
            raise HTTPException(status_code=400, detail="Nome da instância é obrigatório para Evolution.")
        return nome_instancia, credenciais

    required_fields = ["access_token", "resource_id"]
    missing = [field for field in required_fields if not credenciais.get(field)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Campos obrigatórios ausentes para {tipo.value}: {', '.join(missing)}"
        )

    nome_instancia = (payload.nome_instancia or str(credenciais.get("resource_id"))).strip()
    if not nome_instancia:
        raise HTTPException(status_code=400, detail="ID do telefone/página é obrigatório.")
    return nome_instancia, credenciais


async def _validar_instancia_evolution_existe(credenciais: dict[str, Any]) -> None:
    evolution_url = str(credenciais.get("evolution_url") or "").rstrip("/") or evolution_base_url().rstrip("/")
    evolution_apikey = str(credenciais.get("evolution_apikey") or "").strip()
    evolution_instance = str(credenciais.get("evolution_instance") or "").strip()

    if not all([evolution_url, evolution_apikey, evolution_instance]):
        raise HTTPException(
            status_code=400,
            detail="Não foi possível validar a instância Evolution: credenciais incompletas.",
        )

    endpoint = f"{evolution_url}/instance/connectionState/{evolution_instance}"
    headers = {"apikey": evolution_apikey}
    timeout = httpx.Timeout(10.0, connect=5.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            response = await client.get(endpoint, headers=headers)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Falha ao validar instância '{evolution_instance}' na Evolution API: {exc}",
            ) from exc

    response_text = str(response.text or "")
    response_lower = response_text.lower()

    if response.status_code == 404:
        raise HTTPException(
            status_code=400,
            detail=f"A instância '{evolution_instance}' não existe na Evolution API.",
        )

    if response.status_code >= 400:
        if "not found" in response_lower or "não encontrada" in response_lower or "nao encontrada" in response_lower:
            raise HTTPException(
                status_code=400,
                detail=f"A instância '{evolution_instance}' não existe na Evolution API.",
            )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Falha ao validar instância '{evolution_instance}' na Evolution API: "
                f"status {response.status_code}."
            ),
        )


async def _testar_conectividade(tipo: TipoConexao, credenciais: dict[str, Any]) -> None:
    timeout = httpx.Timeout(8.0, connect=5.0)

    if tipo == TipoConexao.EVOLUTION:
        base_url = str(credenciais.get("evolution_url") or evolution_base_url()).rstrip("/")
        headers = {"apikey": credenciais["evolution_apikey"]}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            try:
                response = await client.get(base_url, headers=headers)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Falha no teste de conectividade com Evolution: {exc}"
                ) from exc

        if response.status_code >= 500:
            raise HTTPException(
                status_code=400,
                detail=f"Evolution indisponível no teste de conectividade (status {response.status_code})."
            )
        return

    resource_id = credenciais["resource_id"]
    access_token = credenciais["access_token"]
    url = f"https://graph.facebook.com/v21.0/{resource_id}"
    params = {
        "fields": "id,name",
        "access_token": access_token,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url, params=params)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Falha no teste de conectividade com {tipo.value}: {exc}"
            ) from exc

    if response.status_code != 200:
        detail = str(sanitize_for_log(response.text))[:300] if response.text else f"status {response.status_code}"
        raise HTTPException(
            status_code=400,
            detail=f"Falha no teste de conectividade com {tipo.value}: {detail}"
        )


async def _consultar_status_conexao(conexao: Conexao) -> tuple[str, bool]:
    credenciais = dict(conexao.credenciais or {})
    timeout = httpx.Timeout(8.0, connect=5.0)

    if conexao.tipo == TipoConexao.EVOLUTION:
        evolution_url = str(credenciais.get("evolution_url") or "").rstrip("/") or evolution_base_url().rstrip("/")
        evolution_apikey = credenciais.get("evolution_apikey")
        evolution_instance = credenciais.get("evolution_instance")

        if not all([evolution_url, evolution_apikey, evolution_instance]):
            return "DISCONNECTED", False

        endpoint = f"{evolution_url}/instance/connectionState/{evolution_instance}"
        headers = {"apikey": evolution_apikey}

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(endpoint, headers=headers)

        if response.status_code != 200:
            return "DISCONNECTED", False

        data = response.json() if response.content else {}
        state = (
            data.get("instance", {}).get("state")
            or data.get("state")
            or data.get("status")
            or data.get("connectionStatus")
            or ""
        )
        state_upper = str(state).upper()
        if state_upper == "CONNECTED":
            return "CONNECTED", True
        return state_upper or "DISCONNECTED", False

    resource_id = credenciais.get("resource_id")
    access_token = credenciais.get("access_token")
    if not all([resource_id, access_token]):
        return "DISCONNECTED", False

    url = f"https://graph.facebook.com/v21.0/{resource_id}"
    params = {
        "fields": "id",
        "access_token": access_token,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, params=params)

    if response.status_code == 200:
        return "CONNECTED", True

    return "DISCONNECTED", False


async def _buscar_empresa(db: AsyncSession, empresa_id: str) -> Empresa:
    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ID da empresa inválido.") from exc

    result = await db.execute(select(Empresa).where(Empresa.id == empresa_uuid))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    return empresa


async def _validar_acesso_conexao_usuario(
    conexao: Conexao,
    usuario_bd: Usuario,
) -> Usuario:
    if is_root_admin_email(usuario_bd.email):
        return usuario_bd
    if is_super_admin_role(usuario_bd.role):
        return usuario_bd
    if is_admin_empresa_role(usuario_bd.role) and usuario_bd.empresa_id == conexao.empresa_id:
        return usuario_bd
    raise HTTPException(status_code=403, detail="Acesso negado para esta conexão.")


def _campaign_send_mode(conexao: Conexao) -> str:
    raw = (dict(conexao.credenciais or {})).get("campaign_send_mode")
    return "production" if str(raw or "").strip().lower() in {"production", "live"} else "test_only"


def _serialize_conexao(request: Request, empresa_id: str, conexao: Conexao, reviewer: bool = False) -> ConexaoResponse:
    campaign_send_mode = _campaign_send_mode(conexao)
    if reviewer:
        return ConexaoResponse(
            id=conexao.id, empresa_id=conexao.empresa_id, tipo=conexao.tipo.value,
            channel=conexao.channel, provider=conexao.provider,
            nome_instancia="API Oficial da Meta", status=conexao.status,
            is_default=bool(conexao.is_default), external_account_id=None,
            waba_id=None, phone_number_id=None, instagram_business_account_id=None,
            page_id=None, display_number=_mask_display_number(conexao.display_number),
            campaign_send_mode=campaign_send_mode,
            metadata_json={}, credenciais_masked={}, webhook_url="", webhook_path="",
        )
    webhook_url, webhook_path = _montar_webhook_url(request, empresa_id, conexao.tipo)
    return ConexaoResponse(
        id=conexao.id,
        empresa_id=conexao.empresa_id,
        tipo=conexao.tipo.value,
        channel=conexao.channel,
        provider=conexao.provider,
        nome_instancia=conexao.nome_instancia,
        status=conexao.status,
        is_default=bool(conexao.is_default),
        external_account_id=conexao.external_account_id,
        waba_id=conexao.waba_id,
        phone_number_id=conexao.phone_number_id,
        instagram_business_account_id=conexao.instagram_business_account_id,
        page_id=conexao.page_id,
        display_number=conexao.display_number,
        campaign_send_mode=campaign_send_mode,
        metadata_json=dict(conexao.metadata_json or {}),
        credenciais_masked=_mask_credenciais(conexao.tipo, conexao.credenciais),
        webhook_url=webhook_url,
        webhook_path=webhook_path,
    )


def _mask_display_number(value: str | None) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 6:
        return str(value or "").strip() or None
    return f"+{digits[:2]} *** *** {digits[-4:]}"


def _serialize_meta_connection(conexao: Conexao | None, reviewer: bool = False) -> dict[str, Any]:
    if not conexao:
        return {
            "configured": False,
            "status": "disconnected",
            "connection": None,
        }
    if reviewer:
        return {
            "configured": True,
            "status": conexao.status,
            "provider": "meta_official",
            "channel": conexao.channel,
            "display_number": _mask_display_number(conexao.display_number),
            "waba_configured": bool(conexao.waba_id),
            "official_api": True,
        }
    metadata = dict(conexao.metadata_json or {})
    credentials = dict(conexao.credenciais or {})
    return {
        "configured": bool(credentials.get("encrypted_access_token")),
        "conexao_id": str(conexao.id),
        "status": conexao.status,
        "channel": conexao.channel,
        "provider": conexao.provider,
        "connection_mode": metadata.get("connection_mode") or CONNECTION_MODE_CLOUD_API,
        "waba_id": conexao.waba_id,
        "phone_number_id": conexao.phone_number_id,
        "business_id": metadata.get("business_id"),
        "display_number": _mask_display_number(conexao.display_number),
        "verified_name": metadata.get("verified_name"),
        "quality_rating": metadata.get("quality_rating"),
        "phone_status": metadata.get("phone_status"),
        "graph_api_version": metadata.get("graph_api_version"),
        "capabilities": metadata.get("capabilities") or [],
        "coexistence": metadata.get("coexistence") or detect_coexistence({}),
        "connected_at": metadata.get("connected_at"),
        "last_validated_at": metadata.get("last_validated_at"),
        "last_synced_at": metadata.get("last_synced_at"),
        "current_error": classify_meta_error_for_user(metadata.get("current_error")),
        "token_configured": bool(credentials.get("encrypted_access_token")),
        "token_last4": credentials.get("token_last4"),
    }


async def _buscar_meta_official_conexao(db: AsyncSession, empresa_uuid: uuid.UUID) -> Conexao | None:
    result = await db.execute(
        select(Conexao)
        .where(
            Conexao.empresa_id == empresa_uuid,
            Conexao.tipo == TipoConexao.META,
            Conexao.channel == META_OFFICIAL_CHANNEL,
            Conexao.provider == META_OFFICIAL_PROVIDER,
        )
        .order_by(Conexao.criado_em.desc(), Conexao.id.desc())
    )
    return result.scalars().first()


async def _buscar_conexao_empresa(db: AsyncSession, empresa_id: str, conexao_id: str) -> Conexao:
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc
    result = await db.execute(select(Conexao).where(Conexao.id == conexao_uuid, Conexao.empresa_id == empresa_uuid))
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")
    return conexao


def _ensure_meta_official(conexao: Conexao) -> None:
    if conexao.tipo != TipoConexao.META or conexao.provider != META_OFFICIAL_PROVIDER:
        raise HTTPException(status_code=400, detail="Ação disponível apenas para conexão API Oficial da Meta.")


def _extract_assets(metadata: dict[str, Any]) -> dict[str, Any]:
    assets = metadata.get("authorized_assets") if isinstance(metadata.get("authorized_assets"), dict) else {}
    return assets


def _find_selected_assets(assets: dict[str, Any], business_id: str | None, waba_id: str, phone_number_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    businesses = assets.get("businesses") if isinstance(assets.get("businesses"), list) else []
    selected_business = None
    selected_waba = None
    selected_phone = None
    for business in businesses:
        if not isinstance(business, dict):
            continue
        if business_id and str(business.get("id")) != str(business_id):
            continue
        for waba in business.get("wabas") or []:
            if not isinstance(waba, dict) or str(waba.get("id")) != str(waba_id):
                continue
            for phone in waba.get("phone_numbers") or []:
                if isinstance(phone, dict) and str(phone.get("id")) == str(phone_number_id):
                    selected_business = business
                    selected_waba = waba
                    selected_phone = phone
                    return selected_business, selected_waba, selected_phone
    return selected_business, selected_waba, selected_phone


def _embedded_signup_stage_error(stage: str, exc: Exception) -> dict[str, Any]:
    error = sanitize_meta_error(exc)
    return {
        **error,
        "stage": stage,
        "classification": error.get("code") or "unknown_graph_error",
    }


async def _ensure_meta_waba_subscription(
    *,
    client: MetaGraphClient,
    conexao: Conexao,
    empresa_id: str,
    usuario_id: str | None,
    settings: Any,
) -> dict[str, Any]:
    waba_id = str(conexao.waba_id or "").strip()
    app_id = str(settings.app_id or "").strip()
    if not app_id:
        raise HTTPException(
            status_code=503,
            detail={"code": "subscription_not_configured", "message": "App da Meta não configurado para confirmar a inscrição da WABA."},
        )
    if not waba_id:
        raise HTTPException(
            status_code=503,
            detail={"code": "waba_not_resolved", "message": "WABA não resolvida para esta conexão; a inscrição do app não pode ser confirmada."},
        )
    result = await client.ensure_waba_subscription(waba_id, app_id)
    _audit_meta(
        "waba_subscription_verified",
        empresa_id=empresa_id,
        usuario_id=usuario_id,
        conexao_id=str(conexao.id),
        detail={"changed": bool(result.get("changed")), "app_found": bool(result.get("subscribed"))},
    )
    return result


def _audit_graph_read(event: str, *, empresa_id: str, usuario_id: str | None, conexao_id: str | None, stage: str, path: str, id_value: Any = None) -> None:
    _audit_meta(
        event,
        empresa_id=empresa_id,
        usuario_id=usuario_id,
        conexao_id=conexao_id,
        detail={
            "stage": stage,
            "method": "GET",
            "path": path,
            "id_masked": mask_meta_id(id_value),
        },
    )


def _metadata_from_phone(
    *,
    metadata: dict[str, Any],
    phone: dict[str, Any],
    waba: dict[str, Any] | None,
    meta_business_portfolio_id: str | None,
    onboarding_mode: str,
) -> dict[str, Any]:
    coexistence = detect_coexistence(phone, waba)
    return {
        **metadata,
        "meta_business_portfolio_id": meta_business_portfolio_id or metadata.get("meta_business_portfolio_id"),
        "business_id": meta_business_portfolio_id or metadata.get("business_id"),
        "connection_mode": CONNECTION_MODE_COEXISTENCE if onboarding_mode == BUSINESS_APP_ONBOARDING else CONNECTION_MODE_CLOUD_API,
        "verified_name": phone.get("verified_name"),
        "quality_rating": phone.get("quality_rating"),
        "phone_status": phone.get("status") or phone.get("code_verification_status"),
        "code_verification_status": phone.get("code_verification_status"),
        "platform_type": phone.get("platform_type"),
        "capabilities": phone.get("capabilities") if isinstance(phone.get("capabilities"), list) else [],
        "coexistence": {
            **coexistence,
            "requested": onboarding_mode == BUSINESS_APP_ONBOARDING or coexistence.get("requested"),
        },
        "connected_at": metadata.get("connected_at") or utc_now_iso(),
        "last_validated_at": utc_now_iso(),
        "last_synced_at": utc_now_iso(),
        "current_error": None,
    }


async def _discover_waba_from_token_targets(*, client: MetaGraphClient, phone_number_id: str) -> str | None:
    """Resolve a signup phone only from WABA targets granted to its token.

    The lookup is deliberately fail-closed: an old connection WABA is never a
    candidate, and a phone matching zero or multiple granted WABAs is not
    persisted.
    """
    settings = meta_settings()
    access_token = getattr(client, "access_token", None)
    if not access_token or not settings.app_id or not settings.app_secret:
        return None
    try:
        debug = await client.debug_token(
            input_token=access_token,
            app_access_token=f"{settings.app_id}|{settings.app_secret}",
        )
    except Exception:
        return None
    data = debug.get("data") if isinstance(debug, dict) else {}
    granular_scopes = data.get("granular_scopes") if isinstance(data, dict) else []
    candidates: list[str] = []
    for scope in granular_scopes or []:
        if not isinstance(scope, dict) or scope.get("scope") != "whatsapp_business_management":
            continue
        for target_id in scope.get("target_ids") or []:
            target = str(target_id or "").strip()
            if target and target not in candidates:
                candidates.append(target)
    matches: list[str] = []
    for candidate in candidates:
        try:
            phones = await client.list_waba_phone_numbers(candidate)
        except Exception:
            continue
        if any(str(phone.get("id") or "") == str(phone_number_id) for phone in phones if isinstance(phone, dict)):
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


async def _resolve_and_persist_meta_number(
    *,
    db: AsyncSession,
    conexao: Conexao,
    empresa_id: str,
    usuario_id: str | None,
    client: MetaGraphClient,
    identifiers: dict[str, str | None],
    onboarding_mode: str,
) -> dict[str, Any]:
    metadata = dict(conexao.metadata_json or {})
    # A signup novo pode trocar o telefone e não devolver WABA. Nunca reutilize
    # um WABA antigo nesse caso: isso mistura ativos de conexões diferentes.
    has_signup_phone = bool(identifiers.get("meta_phone_number_id"))
    meta_waba_id = identifiers.get("meta_waba_id") or (None if has_signup_phone else str(conexao.waba_id or "").strip()) or None
    meta_phone_number_id = identifiers.get("meta_phone_number_id") or str(conexao.phone_number_id or "").strip() or None
    meta_business_portfolio_id = identifiers.get("meta_business_portfolio_id") or metadata.get("meta_business_portfolio_id") or metadata.get("business_id")

    if meta_phone_number_id:
        _audit_graph_read(
            "graph_read",
            empresa_id=empresa_id,
            usuario_id=usuario_id,
            conexao_id=str(conexao.id),
            stage="get_phone_number",
            path="/{meta_phone_number_id}",
            id_value=meta_phone_number_id,
        )
        phone = await client.get_phone_number(meta_phone_number_id)
        if not meta_waba_id:
            meta_waba_id = await _discover_waba_from_token_targets(
                client=client,
                phone_number_id=meta_phone_number_id,
            )
        waba = {"id": meta_waba_id} if meta_waba_id else None
        if meta_waba_id:
            _audit_graph_read(
                "graph_read",
                empresa_id=empresa_id,
                usuario_id=usuario_id,
                conexao_id=str(conexao.id),
                stage="confirm_waba_phone_numbers",
                path="/{meta_waba_id}/phone_numbers",
                id_value=meta_waba_id,
            )
            phones = await client.list_waba_phone_numbers(meta_waba_id)
            matched = next((item for item in phones if str(item.get("id")) == str(meta_phone_number_id)), None)
            if matched:
                phone = {**matched, **phone}
                waba = {"id": meta_waba_id, "phone_numbers": phones}
            else:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "asset_not_shared",
                        "message": "O número informado pelo Embedded Signup não apareceu nos números acessíveis deste WABA.",
                    },
                )
        metadata = _metadata_from_phone(
            metadata=metadata,
            phone=phone,
            waba=waba,
            meta_business_portfolio_id=str(meta_business_portfolio_id or ""),
            onboarding_mode=onboarding_mode,
        )
        conexao.waba_id = str(meta_waba_id or "")
        conexao.phone_number_id = str(meta_phone_number_id)
        conexao.external_account_id = str(meta_phone_number_id)
        conexao.display_number = str(phone.get("display_phone_number") or "")
        conexao.nome_instancia = str(phone.get("verified_name") or f"Meta {meta_phone_number_id}")
        if meta_waba_id:
            await _ensure_meta_waba_subscription(
                client=client,
                conexao=conexao,
                empresa_id=empresa_id,
                usuario_id=usuario_id,
                settings=meta_settings(),
            )
            conexao.status = "connected"
        else:
            metadata["current_error"] = {
                "code": "waba_not_resolved",
                "message": "O Embedded Signup não retornou a WABA e o token atual não permite resolver a WABA do número (requer business_management). Refaça o onboarding ou conceda a permissão para confirmar a inscrição.",
                "stage": "confirm_waba",
            }
            conexao.status = "attention"
        conexao.metadata_json = metadata
        await db.commit()
        await db.refresh(conexao)
        return {"status": conexao.status, "source": "embedded_signup_phone_number_id"}

    assets = await discover_assets(client)
    metadata["authorized_assets"] = assets
    metadata["last_synced_at"] = utc_now_iso()
    metadata["current_error"] = None
    conexao.metadata_json = metadata
    await db.commit()

    businesses = assets.get("businesses") if isinstance(assets.get("businesses"), list) else []
    phones: list[tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]] = []
    for business in businesses:
        if not isinstance(business, dict):
            continue
        for waba in business.get("wabas") or []:
            if not isinstance(waba, dict):
                continue
            for phone in waba.get("phone_numbers") or []:
                if isinstance(phone, dict):
                    phones.append((business, waba, phone))
    if len(phones) == 1:
        business, waba, phone = phones[0]
        metadata = _metadata_from_phone(
            metadata=metadata,
            phone=phone,
            waba=waba,
            meta_business_portfolio_id=str((business or {}).get("id") or ""),
            onboarding_mode=onboarding_mode,
        )
        conexao.waba_id = str(waba.get("id") or "")
        conexao.phone_number_id = str(phone.get("id") or "")
        conexao.external_account_id = str(phone.get("id") or "")
        conexao.display_number = str(phone.get("display_phone_number") or "")
        conexao.nome_instancia = str(phone.get("verified_name") or f"Meta {phone.get('id')}")
        await _ensure_meta_waba_subscription(
            client=client,
            conexao=conexao,
            empresa_id=empresa_id,
            usuario_id=usuario_id,
            settings=meta_settings(),
        )
        conexao.status = "connected"
        conexao.metadata_json = metadata
        await db.commit()
        await db.refresh(conexao)
        return {"status": "connected", "source": "single_discovered_phone_number"}
    if len(phones) > 1:
        conexao.status = "attention"
        metadata["current_error"] = {
            "code": "number_selection_required",
            "message": "Mais de um número Meta foi encontrado. Selecione o número na interface.",
            "stage": "discover_assets",
        }
        conexao.metadata_json = metadata
        await db.commit()
        return {"status": "number_selection_required", "source": "discover_assets"}
    raise HTTPException(
        status_code=404,
        detail={"code": "asset_not_shared", "message": "Nenhum número WhatsApp acessível foi encontrado para este token."},
    )


def _serializar_whatsapp_operacional(
    conexao: Conexao | None,
    status_atual: str = "disconnected",
    online: bool = False,
    detail: str | None = None,
) -> dict[str, Any]:
    status_normalizado = str(status_atual or "disconnected").strip().lower()
    conectado = bool(online) or status_normalizado in {"open", "connected", "online", "ativo"}
    if conectado:
        status_normalizado = "connected"
    elif status_normalizado in {"connecting", "qrcode", "qr", "pairing"}:
        status_normalizado = "connecting"
    else:
        status_normalizado = "disconnected"

    return {
        "conexao_id": str(conexao.id) if conexao else None,
        "status": status_normalizado,
        "online": conectado,
        "conectado": conectado,
        "instance_name": conexao.nome_instancia if conexao else None,
        "ultima_atualizacao": None,
        "detail": detail,
    }


async def _buscar_conexao_whatsapp_empresa(db: AsyncSession, empresa_id: str) -> Conexao | None:
    empresa = await _buscar_empresa(db, empresa_id)
    result = await db.execute(
        select(Conexao)
        .where(
            Conexao.empresa_id == empresa.id,
            Conexao.tipo == TipoConexao.EVOLUTION,
        )
        .order_by(Conexao.criado_em.desc())
    )
    return result.scalars().first()


async def _status_whatsapp_empresa(
    db: AsyncSession,
    empresa_id: str,
    *,
    persist_status: bool = False,
) -> dict[str, Any]:
    resolved = await resolve_whatsapp_operational_status(db, empresa_id)
    if resolved.get("provider") == "meta_whatsapp":
        return resolved

    conexao = await _buscar_conexao_whatsapp_empresa(db, empresa_id)
    if not conexao:
        return resolved

    try:
        status_result = await consultar_status_conexao(conexao.id)
    except Exception:
        status_result = {"success": False, "detail": "Não foi possível consultar o status agora."}

    if status_result.get("success"):
        status_normalizado = str(status_result.get("status") or "disconnected").lower()
        online = status_normalizado == "open"
        status_db = "ativo" if online else ("connecting" if status_normalizado == "connecting" else "disconnected")
        if persist_status and _normalizar_status_conexao(conexao.status, default="") != _normalizar_status_conexao(status_db, default=""):
            conexao.status = status_db
            await db.commit()
            await db.refresh(conexao)
        return _serializar_whatsapp_operacional(conexao, status_normalizado, online)

    return _serializar_whatsapp_operacional(
        conexao,
        "disconnected",
        False,
        "Não foi possível confirmar o status do WhatsApp neste momento.",
    )


@router.get("/", response_model=list[ConexaoResponse], status_code=status.HTTP_200_OK)
async def listar_conexoes(
    empresa_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    await _buscar_empresa(db, empresa_id)
    empresa_uuid = uuid.UUID(empresa_id)
    result = await db.execute(
        select(Conexao)
        .where(Conexao.empresa_id == empresa_uuid, Conexao.tipo != TipoConexao.INSTAGRAM)
        .order_by(Conexao.criado_em.desc())
    )
    conexoes = result.scalars().all()
    if is_meta_app_reviewer_role(current_user.role):
        conexoes = [item for item in conexoes if item.tipo == TipoConexao.META and item.provider == "meta_official"]
    return [_serialize_conexao(request, empresa_id, conexao, reviewer=is_meta_app_reviewer_role(current_user.role)) for conexao in conexoes]


@router.get("/meta/config", status_code=status.HTTP_200_OK)
async def meta_embedded_signup_config(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    await _buscar_empresa(db, empresa_id)
    _audit_meta("config_requested", empresa_id=empresa_id, usuario_id=str(current_user.id))
    return public_embedded_signup_config()


@router.post("/meta/session", status_code=status.HTTP_201_CREATED)
async def meta_embedded_signup_session(
    empresa_id: str,
    payload: MetaSessionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    await _buscar_empresa(db, empresa_id)
    try:
        onboarding_mode = normalize_onboarding_mode((payload or MetaSessionRequest()).onboarding_mode)
    except MetaSignupStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = create_signup_state(
        empresa_id=empresa_id,
        usuario_id=str(current_user.id),
        onboarding_mode=onboarding_mode,
    )
    config = public_embedded_signup_config()
    _audit_meta("signup_started", empresa_id=empresa_id, usuario_id=str(current_user.id), detail={"onboarding_mode": onboarding_mode})
    return {
        "state": state,
        "expires_in": 600,
        "onboarding_mode": onboarding_mode,
        "config": config,
    }


def _ensure_meta_marketing_admin(user: Usuario, empresa_id: uuid.UUID) -> None:
    if is_root_admin_email(user.email) or is_super_admin_role(user.role):
        return
    if is_admin_empresa_role(user.role) and user.empresa_id == empresa_id:
        return
    raise HTTPException(status_code=403, detail="Acesso administrativo necessário para autorizar leitura de anúncios.")


@router.post("/meta/marketing/session", status_code=status.HTTP_201_CREATED)
async def meta_marketing_oauth_session(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa = await _buscar_empresa(db, empresa_id)
    _ensure_meta_marketing_admin(current_user, empresa.id)
    settings = meta_settings()
    if not (settings.app_id and settings.app_secret and settings.credential_encryption_key and settings.redirect_uri):
        raise HTTPException(status_code=503, detail="Configuração OAuth Meta incompleta para Marketing API.")
    config = await db.scalar(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == empresa.id))
    if not config or not config.ad_account_id or not config.business_id:
        raise HTTPException(status_code=409, detail="Vincule primeiro a conta de anúncios e o Business desta empresa.")
    try:
        state = create_signup_state(
            empresa_id=empresa_id,
            usuario_id=str(current_user.id),
            onboarding_mode="standard_embedded_signup",
            purpose=MARKETING_OAUTH_STATE_PURPOSE,
        )
    except MetaSignupStateError as exc:
        raise HTTPException(status_code=503, detail="Configuração de segurança Meta indisponível.") from exc
    _audit_meta("marketing_ads_oauth_started", empresa_id=empresa_id, usuario_id=str(current_user.id))
    return {
        "state": state,
        "expires_in": 600,
        "app_id": settings.app_id,
        "graph_api_version": settings.marketing_graph_api_version,
        "sdk_url": "https://connect.facebook.net/pt_BR/sdk.js",
        "requested_permissions": list(META_MARKETING_OAUTH_PERMISSIONS),
    }


@router.post("/meta/marketing/complete", status_code=status.HTTP_200_OK)
async def meta_marketing_oauth_complete(
    empresa_id: str,
    payload: MetaMarketingOAuthCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa = await _buscar_empresa(db, empresa_id)
    _ensure_meta_marketing_admin(current_user, empresa.id)
    settings = meta_settings()
    if not (settings.app_id and settings.app_secret and settings.credential_encryption_key and settings.redirect_uri):
        raise HTTPException(status_code=503, detail="Configuração OAuth Meta incompleta para Marketing API.")
    config = await db.scalar(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == empresa.id))
    if not config or not config.ad_account_id or not config.business_id:
        raise HTTPException(status_code=409, detail="Vincule primeiro a conta de anúncios e o Business desta empresa.")
    try:
        try:
            from app.api.main import redis_client
        except Exception:
            redis_client = None
        await validate_signup_state(
            payload.state,
            empresa_id=empresa_id,
            usuario_id=str(current_user.id),
            onboarding_mode="standard_embedded_signup",
            redis_client=redis_client,
            consume=True,
            purpose=MARKETING_OAUTH_STATE_PURPOSE,
        )
    except MetaSignupStateError as exc:
        _audit_meta("marketing_ads_oauth_failed", empresa_id=empresa_id, usuario_id=str(current_user.id), detail={"reason": str(exc)})
        raise HTTPException(status_code=400, detail="Sessão de autorização Meta inválida ou expirada. Inicie novamente.") from exc

    try:
        graph = MetaGraphClient(graph_api_version=settings.marketing_graph_api_version)
        token_payload = await graph.exchange_code(
            code=payload.code,
            app_id=settings.app_id or "",
            app_secret=settings.app_secret or "",
            redirect_uri=settings.redirect_uri,
        )
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("META_OAUTH_TOKEN_MISSING")
        marketing_client = MetaMarketingApiClient(
            access_token,
            graph_version=settings.marketing_graph_api_version,
            timeout_seconds=12.0,
            max_attempts=2,
            read_only=True,
        )
        permission_body = await marketing_client.get_permissions()
    except Exception as exc:
        error = sanitize_meta_error(exc)
        _audit_meta("marketing_ads_oauth_failed", empresa_id=empresa_id, usuario_id=str(current_user.id), detail={"code": error.get("code")})
        raise HTTPException(status_code=502, detail={"message": "A Meta não concluiu a autorização da Marketing API.", "error": error}) from exc

    granted = sorted(granted_permissions(permission_body))
    if not ({"ads_read", "ads_management"} & set(granted)):
        _audit_meta("marketing_ads_oauth_permission_missing", empresa_id=empresa_id, usuario_id=str(current_user.id))
        raise HTTPException(status_code=409, detail={
            "code": "MARKETING_PERMISSION_NOT_GRANTED",
            "message": "A Meta não concedeu ads_read ou ads_management. Revise as permissões do app e tente novamente.",
        })

    try:
        account = await marketing_client.validate_ad_account(str(config.ad_account_id))
        actual_account_id = normalize_ad_account_id(account.get("account_id") or account.get("id"))
        expected_account_id = normalize_ad_account_id(config.ad_account_id)
        if actual_account_id != expected_account_id:
            raise HTTPException(status_code=403, detail="A credencial autorizada não corresponde à conta de anúncios desta empresa.")
        bound, _method = await _business_account_binding(
            marketing_client,
            expected_business_id=str(config.business_id).strip(),
            configured_account_id=str(config.ad_account_id),
            actual_account=account,
        )
        if not bound:
            raise HTTPException(status_code=403, detail="A Meta não confirmou o vínculo Business/conta desta empresa.")
    except HTTPException:
        raise
    except Exception as exc:
        error = sanitize_meta_error(exc)
        _audit_meta("marketing_ads_oauth_account_binding_failed", empresa_id=empresa_id, usuario_id=str(current_user.id), detail={"code": error.get("code")})
        raise HTTPException(status_code=502, detail={"message": "Não foi possível validar o vínculo da conta autorizada.", "error": error}) from exc

    connection = await db.scalar(
        select(Conexao)
        .where(
            Conexao.empresa_id == empresa.id,
            Conexao.tipo == TipoConexao.META,
            Conexao.channel == META_MARKETING_CHANNEL,
            Conexao.provider == META_MARKETING_PROVIDER,
        )
        .order_by(Conexao.atualizado_em.desc(), Conexao.id.desc())
        .limit(1)
        .with_for_update()
    )
    if connection is None:
        connection = Conexao(
            empresa_id=empresa.id,
            tipo=TipoConexao.META,
            channel=META_MARKETING_CHANNEL,
            provider=META_MARKETING_PROVIDER,
            nome_instancia="Meta Marketing API",
            status="ativo",
            credential_source="tenant_marketing_oauth",
            credenciais={},
            metadata_json={},
            external_account_id=expected_account_id,
        )
        db.add(connection)
        await db.flush()
    credentials = dict(connection.credenciais or {})
    credentials.update({
        "encrypted_marketing_access_token": encrypt_meta_secret(access_token),
        "marketing_token_type": str(token_payload.get("token_type") or "bearer")[:40],
        "marketing_token_expires_in": token_payload.get("expires_in"),
        "marketing_granted_permissions": granted,
        "marketing_token_obtained_at": utc_now_iso(),
    })
    connection.credenciais = credentials
    metadata = dict(connection.metadata_json or {})
    metadata.update({
        "marketing_granted_permissions": granted,
        "marketing_business_id": str(config.business_id).strip(),
        "marketing_ad_account_id": expected_account_id,
        "marketing_authorized_at": utc_now_iso(),
        "marketing_permission_check_at": utc_now_iso(),
    })
    connection.metadata_json = metadata
    await db.commit()
    _audit_meta(
        "marketing_ads_oauth_completed",
        empresa_id=empresa_id,
        usuario_id=str(current_user.id),
        conexao_id=str(connection.id),
        detail={"ads_read": "ads_read" in granted, "ads_management": "ads_management" in granted},
    )
    return {
        "status": "authorized",
        "credential_source": "tenant_marketing_oauth",
        "permissions": {"ads_read": "ads_read" in granted, "ads_management": "ads_management" in granted},
        "expires_in": token_payload.get("expires_in"),
    }


@router.get("/meta/status", status_code=status.HTTP_200_OK)
async def meta_connection_status(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa = await _buscar_empresa(db, empresa_id)
    conexao = await _buscar_meta_official_conexao(db, empresa.id)
    return _serialize_meta_connection(conexao, reviewer=is_meta_app_reviewer_role(current_user.role))


@router.post("/meta/complete", status_code=status.HTTP_200_OK)
async def meta_embedded_signup_complete(
    empresa_id: str,
    payload: MetaCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa = await _buscar_empresa(db, empresa_id)
    try:
        onboarding_mode = normalize_onboarding_mode(payload.onboarding_mode)
    except MetaSignupStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        from app.api.main import redis_client
    except Exception:
        redis_client = None
    try:
        await validate_signup_state(
            payload.state,
            empresa_id=empresa_id,
            usuario_id=str(current_user.id),
            onboarding_mode=onboarding_mode,
            redis_client=redis_client,
            consume=True,
        )
    except MetaSignupStateError as exc:
        _audit_meta("signup_failed", empresa_id=empresa_id, usuario_id=str(current_user.id), detail={"reason": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.cancelled or payload.error:
        _audit_meta(
            "signup_cancelled",
            empresa_id=empresa_id,
            usuario_id=str(current_user.id),
            detail={"error": payload.error, "reason": payload.error_reason, "onboarding_mode": onboarding_mode},
        )
        return {"status": "cancelled", "detail": "Fluxo Meta cancelado no navegador."}

    settings = meta_settings()
    if not settings.server_configured and not payload.mock_token:
        _audit_meta("signup_failed", empresa_id=empresa_id, usuario_id=str(current_user.id), detail={"reason": "missing_server_config"})
        raise HTTPException(status_code=503, detail="Configuração global Meta incompleta.")

    token_payload: dict[str, Any]
    if payload.mock_token:
        token_payload = {"access_token": payload.mock_token, "token_type": "mock", "expires_in": None}
    else:
        if not settings.live_actions_enabled:
            _audit_meta("live_action_blocked", empresa_id=empresa_id, usuario_id=str(current_user.id), detail={"action": "exchange_code"})
            raise HTTPException(status_code=409, detail="Ações reais Meta estão bloqueadas por META_LIVE_ACTIONS_ENABLED=false.")
        if not payload.code:
            raise HTTPException(status_code=400, detail="Código temporário ausente no retorno do Embedded Signup.")
        client = MetaGraphClient(graph_api_version=settings.graph_api_version)
        try:
            token_payload = await client.exchange_code(
                code=payload.code,
                app_id=settings.app_id or "",
                app_secret=settings.app_secret or "",
                redirect_uri=settings.redirect_uri,
            )
        except Exception as exc:
            error = sanitize_meta_error(exc)
            _audit_meta("signup_failed", empresa_id=empresa_id, usuario_id=str(current_user.id), detail=error)
            raise HTTPException(status_code=400, detail=error) from exc

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(status_code=400, detail="A Meta não retornou access token utilizável.")

    credentials = encrypted_credentials_from_token(
        access_token,
        token_type=str(token_payload.get("token_type") or ""),
        expires_in=token_payload.get("expires_in"),
    )
    identifiers = extract_signup_identifiers(payload.signup_payload)
    metadata = {
        "connection_mode": CONNECTION_MODE_CLOUD_API,
        "graph_api_version": settings.graph_api_version,
        "signup_payload": sanitize_for_api(payload.signup_payload),
        "authorized_assets": sanitize_for_api(payload.mock_assets or {}),
        "coexistence": detect_coexistence({}),
        "onboarding_mode": onboarding_mode,
        "requested_business_app_onboarding": onboarding_mode == BUSINESS_APP_ONBOARDING,
        "meta_business_portfolio_id": identifiers.get("meta_business_portfolio_id"),
        "business_id": identifiers.get("meta_business_portfolio_id"),
        "meta_waba_id": identifiers.get("meta_waba_id"),
        "meta_phone_number_id": identifiers.get("meta_phone_number_id"),
        "token_obtained_at": utc_now_iso(),
        "connected_at": None,
        "last_validated_at": None,
        "last_synced_at": None,
        "current_error": None,
    }
    conexao = await _buscar_meta_official_conexao(db, empresa.id)
    if not conexao:
        conexao = Conexao(
            empresa_id=empresa.id,
            tipo=TipoConexao.META,
            channel=META_OFFICIAL_CHANNEL,
            provider=META_OFFICIAL_PROVIDER,
            nome_instancia="WhatsApp Oficial Meta",
            status="token_obtained",
            credenciais=credentials,
            metadata_json=metadata,
        )
        db.add(conexao)
        await db.flush()
        _audit_meta("connection_created", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=str(conexao.id))
    else:
        conexao.credenciais = credentials
        conexao.status = "token_obtained"
        conexao.channel = META_OFFICIAL_CHANNEL
        conexao.provider = META_OFFICIAL_PROVIDER
        conexao.metadata_json = {**dict(conexao.metadata_json or {}), **metadata}

    await db.commit()
    await db.refresh(conexao)
    if settings.live_actions_enabled:
        try:
            client = MetaGraphClient(access_token=access_token, graph_api_version=settings.graph_api_version)
            await _resolve_and_persist_meta_number(
                db=db,
                conexao=conexao,
                empresa_id=empresa_id,
                usuario_id=str(current_user.id),
                client=client,
                identifiers=identifiers,
                onboarding_mode=onboarding_mode,
            )
        except HTTPException as exc:
            metadata = dict(conexao.metadata_json or {})
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": "retryable_error", "message": str(exc.detail)}
            metadata["current_error"] = {
                **detail,
                "stage": detail.get("stage") or "discovering_assets",
            }
            conexao.status = "attention" if detail.get("code") == "number_selection_required" else "retryable_error"
            conexao.metadata_json = metadata
            await db.commit()
            await db.refresh(conexao)
        except Exception as exc:
            metadata = dict(conexao.metadata_json or {})
            metadata["current_error"] = _embedded_signup_stage_error("discovering_assets", exc)
            conexao.status = "token_expired" if metadata["current_error"]["code"] == "token_expired" else "retryable_error"
            conexao.metadata_json = metadata
            await db.commit()
            await db.refresh(conexao)
    _audit_meta(
        "signup_completed",
        empresa_id=empresa_id,
        usuario_id=str(current_user.id),
        conexao_id=str(conexao.id),
        detail={"onboarding_mode": onboarding_mode},
    )
    return _serialize_meta_connection(conexao)


@router.get("/meta/assets", status_code=status.HTTP_200_OK)
async def meta_assets(
    empresa_id: str,
    conexao_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    """Return cached assets or discover them with read-only Graph requests."""
    empresa = await _buscar_empresa(db, empresa_id)
    conexao = await _buscar_conexao_empresa(db, empresa_id, conexao_id) if conexao_id else await _buscar_meta_official_conexao(db, empresa.id)
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão Meta Official não encontrada.")
    _ensure_meta_official(conexao)
    settings = meta_settings()
    if not settings.live_actions_enabled:
        assets = _extract_assets(dict(conexao.metadata_json or {}))
        if assets:
            return assets
        raise HTTPException(status_code=409, detail="Descoberta real bloqueada por META_LIVE_ACTIONS_ENABLED=false.")
    try:
        token = decrypt_connection_token(dict(conexao.credenciais or {}))
        client = MetaGraphClient(access_token=token, graph_api_version=settings.graph_api_version)
        # This endpoint is also used by the existing "Listar ativos" UI action.
        # Keep it compatible while making it strictly read-only: selecting a
        # number and persisting the connection remains an explicit POST action.
        assets = await discover_assets(client)
    except Exception as exc:
        error = sanitize_meta_error(exc)
        raise HTTPException(status_code=400, detail=error) from exc
    return assets


@router.post("/meta/select-number", status_code=status.HTTP_200_OK)
async def meta_select_number(
    empresa_id: str,
    payload: MetaSelectNumberRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    conexao = await _buscar_conexao_empresa(db, empresa_id, payload.conexao_id)
    _ensure_meta_official(conexao)
    metadata = dict(conexao.metadata_json or {})
    assets = _extract_assets(metadata)
    business, waba, phone = _find_selected_assets(assets, payload.business_id, payload.waba_id, payload.phone_number_id)
    if assets and not phone:
        raise HTTPException(status_code=404, detail="Número não encontrado entre os ativos autorizados.")
    phone = phone or {"id": payload.phone_number_id}
    waba = waba or {"id": payload.waba_id}
    business = business or ({"id": payload.business_id} if payload.business_id else None)

    coexistence = detect_coexistence(phone, waba)
    metadata.update(
        {
            "business_id": str((business or {}).get("id") or payload.business_id or ""),
            "business_name": (business or {}).get("name"),
            "connection_mode": "coexistence" if coexistence.get("confirmed") else CONNECTION_MODE_CLOUD_API,
            "verified_name": phone.get("verified_name"),
            "quality_rating": phone.get("quality_rating"),
            "phone_status": phone.get("status") or phone.get("code_verification_status"),
            "code_verification_status": phone.get("code_verification_status"),
            "platform_type": phone.get("platform_type"),
            "capabilities": phone.get("capabilities") if isinstance(phone.get("capabilities"), list) else [],
            "coexistence": coexistence,
            "connected_at": metadata.get("connected_at") or utc_now_iso(),
            "last_synced_at": utc_now_iso(),
            "current_error": None,
        }
    )
    conexao.waba_id = str(payload.waba_id)
    conexao.phone_number_id = str(payload.phone_number_id)
    conexao.external_account_id = str(payload.phone_number_id)
    conexao.display_number = str(phone.get("display_phone_number") or "")
    conexao.nome_instancia = str(phone.get("verified_name") or f"Meta {payload.phone_number_id}")
    settings = meta_settings()
    if settings.live_actions_enabled:
        token = decrypt_connection_token(dict(conexao.credenciais or {}))
        client = MetaGraphClient(access_token=token, graph_api_version=settings.graph_api_version)
        await _ensure_meta_waba_subscription(
            client=client,
            conexao=conexao,
            empresa_id=empresa_id,
            usuario_id=str(current_user.id),
            settings=settings,
        )
    conexao.status = "connected"
    conexao.metadata_json = metadata
    await db.commit()
    await db.refresh(conexao)
    _audit_meta("number_selected", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=str(conexao.id), detail={"waba_id": payload.waba_id, "phone_number_id": payload.phone_number_id})
    return _serialize_meta_connection(conexao)


@whatsapp_router.get("/status", status_code=status.HTTP_200_OK)
async def status_whatsapp_tenant(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await _status_whatsapp_empresa(db, empresa_id)


@whatsapp_router.post("/atualizar-status", status_code=status.HTTP_200_OK)
async def atualizar_status_whatsapp_tenant(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await _status_whatsapp_empresa(db, empresa_id, persist_status=True)


@whatsapp_router.post("/conectar", status_code=status.HTTP_200_OK)
async def conectar_whatsapp_tenant(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    await _buscar_empresa(db, empresa_id)
    resultado = await provisionar_whatsapp_empresa(empresa_id, db)
    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Não foi possível iniciar a conexão WhatsApp.",
        )
    conexao = await _buscar_conexao_whatsapp_empresa(db, empresa_id)
    return {
        **_serializar_whatsapp_operacional(
            conexao,
            "connecting" if resultado.get("base64") else "disconnected",
            False,
            resultado.get("detail") or "QR Code solicitado.",
        ),
        "base64": resultado.get("base64"),
        "qr_code_disponivel": bool(resultado.get("base64")),
        "reaproveitada": bool(resultado.get("reaproveitada", False)),
    }


@whatsapp_router.get("/qrcode", status_code=status.HTTP_200_OK)
async def qrcode_whatsapp_tenant(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    conexao = await _buscar_conexao_whatsapp_empresa(db, empresa_id)
    if not conexao:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conexão WhatsApp ainda não iniciada. Clique em Conectar WhatsApp.",
        )

    resultado = await obter_qr_code(conexao.id)
    if not resultado.get("success"):
        status_code = status.HTTP_409_CONFLICT if resultado.get("already_connected") else status.HTTP_400_BAD_REQUEST
        raise HTTPException(
            status_code=status_code,
            detail=resultado.get("detail") or "Não foi possível obter o QR Code.",
        )

    return {
        **_serializar_whatsapp_operacional(
            conexao,
            "connecting",
            False,
            resultado.get("detail") or "QR Code gerado com sucesso.",
        ),
        "base64": resultado.get("base64"),
        "qr_code_disponivel": bool(resultado.get("base64")),
    }


@whatsapp_router.post("/desconectar", status_code=status.HTTP_200_OK)
async def desconectar_whatsapp_tenant(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    await _buscar_empresa(db, empresa_id)
    resultado = await logout_evolution_whatsapp_empresa(empresa_id, db)
    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Não foi possível desconectar o WhatsApp.",
        )
    conexao = await _buscar_conexao_whatsapp_empresa(db, empresa_id)
    return _serializar_whatsapp_operacional(
        conexao,
        "disconnected",
        False,
        resultado.get("detail") or "WhatsApp desconectado.",
    )


@router.post("/provision-whatsapp", status_code=status.HTTP_200_OK)
async def provisionar_whatsapp(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    """
    Cria (ou reutiliza) a instância Evolution para o tenant usando EVOLUTION_API_URL/EVOLUTION_API_TOKEN
    do servidor e devolve o QR Code em Base64.
    """
    await _buscar_empresa(db, empresa_id)
    resultado = await provisionar_whatsapp_empresa(empresa_id, db)
    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Falha ao provisionar WhatsApp.",
        )
    return {
        "conexao_id": resultado.get("conexao_id"),
        "instance_name": resultado.get("instance_name"),
        "base64": resultado.get("base64"),
        "detail": resultado.get("detail"),
        "reaproveitada": resultado.get("reaproveitada", False),
    }


@router.post("/logout-whatsapp", status_code=status.HTTP_200_OK)
async def logout_whatsapp(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    """
    Encerra a sessão WhatsApp (logout) da instância automática do tenant na Evolution,
    sem apagar a instância. Permite reconectar via QR Code logo em seguida.
    """
    await _buscar_empresa(db, empresa_id)
    resultado = await logout_evolution_whatsapp_empresa(empresa_id, db)
    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Não foi possível desconectar o WhatsApp.",
        )
    return {
        "detail": resultado.get("detail"),
        "instance_name": resultado.get("instance_name"),
    }


@router.post("/reset-evolution-whatsapp", status_code=status.HTTP_200_OK)
async def reset_evolution_whatsapp(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    """
    Apaga na Evolution API a instância automática wa_<uuid> e remove a Conexão correspondente na BD.
    """
    await _buscar_empresa(db, empresa_id)
    resultado = await reset_evolution_whatsapp_empresa(empresa_id, db)
    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Não foi possível reiniciar a instância WhatsApp.",
        )
    return {
        "detail": resultado.get("detail"),
        "instance_name": resultado.get("instance_name"),
        "conexoes_removidas": resultado.get("conexoes_removidas", 0),
    }


@router.post("/{conexao_id}/validate", status_code=status.HTTP_200_OK)
async def validar_conexao_meta(
    empresa_id: str,
    conexao_id: str,
    payload: MetaConnectionActionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    conexao = await _buscar_conexao_empresa(db, empresa_id, conexao_id)
    _ensure_meta_official(conexao)
    settings = meta_settings()
    metadata = dict(conexao.metadata_json or {})
    if not settings.live_actions_enabled:
        metadata["last_validated_at"] = utc_now_iso()
        metadata["current_error"] = None
        conexao.metadata_json = metadata
        if conexao.status not in META_CONNECTION_STATUSES:
            conexao.status = "attention"
        await db.commit()
        _audit_meta("live_action_blocked", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id, detail={"action": "validate"})
        return {**_serialize_meta_connection(conexao), "validation": {"status": "blocked", "reason": "META_LIVE_ACTIONS_ENABLED=false"}}

    try:
        token = decrypt_connection_token(dict(conexao.credenciais or {}))
        client = MetaGraphClient(access_token=token, graph_api_version=settings.graph_api_version)
        phone = await client.get_phone_number(str(conexao.phone_number_id or ""))
        await _ensure_meta_waba_subscription(
            client=client,
            conexao=conexao,
            empresa_id=empresa_id,
            usuario_id=str(current_user.id),
            settings=settings,
        )
    except HTTPException as exc:
        error = exc.detail if isinstance(exc.detail, dict) else {"code": "unexpected_error", "message": str(exc.detail)}
        metadata["current_error"] = error
        conexao.status = "attention" if error.get("code") == "waba_not_resolved" else "error"
        conexao.metadata_json = metadata
        await db.commit()
        raise
    except Exception as exc:
        error = sanitize_meta_error(exc)
        metadata["current_error"] = error
        conexao.status = "token_expired" if error["code"] == "token_expired" else "error"
        conexao.metadata_json = metadata
        await db.commit()
        raise HTTPException(status_code=400, detail=error) from exc

    metadata.update(
        {
            "last_validated_at": utc_now_iso(),
            "verified_name": phone.get("verified_name") or metadata.get("verified_name"),
            "quality_rating": phone.get("quality_rating") or metadata.get("quality_rating"),
            "phone_status": phone.get("status") or metadata.get("phone_status"),
            "current_error": None,
        }
    )
    conexao.status = "connected"
    conexao.metadata_json = metadata
    await db.commit()
    _audit_meta("connection_validated", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id)
    return _serialize_meta_connection(conexao)


@router.post("/{conexao_id}/sync", status_code=status.HTTP_200_OK)
async def sincronizar_conexao_meta(
    empresa_id: str,
    conexao_id: str,
    payload: MetaConnectionActionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    conexao = await _buscar_conexao_empresa(db, empresa_id, conexao_id)
    _ensure_meta_official(conexao)
    settings = meta_settings()
    metadata = dict(conexao.metadata_json or {})
    action_payload = payload or MetaConnectionActionRequest()
    if action_payload.mock_assets:
        metadata["authorized_assets"] = sanitize_for_api(action_payload.mock_assets)
        metadata["last_synced_at"] = utc_now_iso()
        metadata["current_error"] = None
        conexao.metadata_json = metadata
        await db.commit()
        _audit_meta("connection_synced", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id, detail={"mode": "mock"})
        return _serialize_meta_connection(conexao)
    if not settings.live_actions_enabled:
        _audit_meta("live_action_blocked", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id, detail={"action": "sync"})
        return {**_serialize_meta_connection(conexao), "sync": {"status": "blocked", "reason": "META_LIVE_ACTIONS_ENABLED=false"}}

    try:
        token = decrypt_connection_token(dict(conexao.credenciais or {}))
        client = MetaGraphClient(access_token=token, graph_api_version=settings.graph_api_version)
        payload_identifiers = extract_signup_identifiers(metadata.get("signup_payload"))
        identifiers = {
            "meta_business_portfolio_id": metadata.get("meta_business_portfolio_id") or metadata.get("business_id") or payload_identifiers.get("meta_business_portfolio_id"),
            "meta_waba_id": metadata.get("meta_waba_id") or conexao.waba_id or payload_identifiers.get("meta_waba_id"),
            "meta_phone_number_id": metadata.get("meta_phone_number_id") or conexao.phone_number_id or payload_identifiers.get("meta_phone_number_id"),
        }
        if identifiers.get("meta_phone_number_id"):
            await _resolve_and_persist_meta_number(
                db=db,
                conexao=conexao,
                empresa_id=empresa_id,
                usuario_id=str(current_user.id),
                client=client,
                identifiers=identifiers,
                onboarding_mode=str(metadata.get("onboarding_mode") or "standard_embedded_signup"),
            )
            _audit_meta("connection_synced", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id, detail={"mode": "direct_phone_number"})
            return _serialize_meta_connection(conexao)
        assets = await discover_assets(client)
    except Exception as exc:
        error = sanitize_meta_error(exc)
        metadata["current_error"] = error
        conexao.status = "token_expired" if error["code"] == "token_expired" else "error"
        conexao.metadata_json = metadata
        await db.commit()
        raise HTTPException(status_code=400, detail=error) from exc

    metadata["authorized_assets"] = assets
    metadata["last_synced_at"] = utc_now_iso()
    metadata["current_error"] = None
    conexao.metadata_json = metadata
    await db.commit()
    _audit_meta("connection_synced", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id)
    return _serialize_meta_connection(conexao)


@router.post("/{conexao_id}/disconnect", status_code=status.HTTP_200_OK)
async def desconectar_conexao_meta(
    empresa_id: str,
    conexao_id: str,
    payload: MetaConnectionActionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    conexao = await _buscar_conexao_empresa(db, empresa_id, conexao_id)
    _ensure_meta_official(conexao)
    settings = meta_settings()
    metadata = dict(conexao.metadata_json or {})
    if settings.live_actions_enabled and payload and payload.confirm:
        _audit_meta("live_action_blocked", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id, detail={"action": "unsubscribe_not_executed_in_phase6"})
        raise HTTPException(status_code=409, detail="Desconexão real não é executada nesta fase.")
    # Disconnecting from AgentOS is a local operation.  Do not require a
    # remote Meta object to exist before removing the local authorization and
    # selected assets; remote subscription cleanup, when introduced, must be
    # best-effort and must not make this local transition fail.
    disconnected_at = utc_now_iso()
    metadata = {"disconnected_at": disconnected_at}
    conexao.credenciais = {}
    conexao.external_account_id = None
    conexao.waba_id = None
    conexao.phone_number_id = None
    conexao.display_number = None
    conexao.nome_instancia = "Meta Official"
    conexao.status = "disconnected"
    conexao.metadata_json = metadata
    await db.commit()
    _audit_meta("connection_disconnected", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id)
    return _serialize_meta_connection(conexao)


@router.post("/{conexao_id}/reconnect", status_code=status.HTTP_200_OK)
async def reconectar_conexao_meta(
    empresa_id: str,
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    conexao = await _buscar_conexao_empresa(db, empresa_id, conexao_id)
    _ensure_meta_official(conexao)
    metadata = dict(conexao.metadata_json or {})
    metadata["current_error"] = None
    metadata["reconnect_requested_at"] = utc_now_iso()
    conexao.status = "pending"
    conexao.metadata_json = metadata
    await db.commit()
    _audit_meta("reconnect_requested", empresa_id=empresa_id, usuario_id=str(current_user.id), conexao_id=conexao_id)
    return _serialize_meta_connection(conexao)


@router.get("/{conexao_id}/diagnostics", status_code=status.HTTP_200_OK)
async def diagnostico_conexao_meta(
    empresa_id: str,
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    conexao = await _buscar_conexao_empresa(db, empresa_id, conexao_id)
    _ensure_meta_official(conexao)
    metadata = dict(conexao.metadata_json or {})
    return {
        "conexao_id": str(conexao.id),
        "status": conexao.status,
        "configured": bool((conexao.credenciais or {}).get("encrypted_access_token")),
        "graph_api_version": metadata.get("graph_api_version"),
        "current_error": metadata.get("current_error"),
        "coexistence": metadata.get("coexistence") or detect_coexistence({}),
        "webhook": {
            "compatible": bool(conexao.phone_number_id or conexao.waba_id),
            "phone_number_id": bool(conexao.phone_number_id),
            "waba_id": bool(conexao.waba_id),
            "signature_validation": bool(meta_settings().app_secret),
        },
        "live_actions_enabled": meta_settings().live_actions_enabled,
    }


@router.post("/", response_model=ConexaoResponse, status_code=status.HTTP_201_CREATED)
async def criar_conexao(
    empresa_id: str,
    payload: ConexaoCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    await _buscar_empresa(db, empresa_id)
    empresa_uuid = uuid.UUID(empresa_id)
    tipo = _normalizar_tipo(payload.tipo)
    nome_instancia, credenciais = _validar_payload(tipo, payload)

    await _testar_conectividade(tipo, credenciais)
    if tipo == TipoConexao.EVOLUTION:
        await _validar_instancia_evolution_existe(credenciais)
        settings_result = await aplicar_settings_padrao_evolution(
            str(credenciais.get("evolution_url") or evolution_base_url()).rstrip("/"),
            evolution_global_api_key() or str(credenciais.get("evolution_apikey") or "").strip(),
            str(credenciais.get("evolution_instance") or "").strip(),
        )
        if not settings_result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=str(sanitize_for_log(settings_result.get("detail") or "Falha ao aplicar settings padrão Evolution.")),
            )

    result_existente = await db.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            Conexao.tipo == tipo,
            Conexao.nome_instancia == nome_instancia,
        )
    )
    existente = result_existente.scalars().first()
    if existente:
        raise HTTPException(
            status_code=409,
            detail="Já existe uma conexão desse tipo com o mesmo nome/identificador para esta empresa."
        )

    conexao = Conexao(
        empresa_id=empresa_uuid,
        tipo=tipo,
        nome_instancia=nome_instancia,
        credenciais=credenciais,
        status=_normalizar_status_conexao(payload.status, default="ativo"),
    )
    db.add(conexao)

    try:
        await db.commit()
        await db.refresh(conexao)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Erro ao criar conexão: {exc}") from exc

    return _serialize_conexao(request, empresa_id, conexao)


@router.delete("/{conexao_id}", status_code=status.HTTP_200_OK)
async def excluir_conexao(
    empresa_id: str,
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    await _buscar_empresa(db, empresa_id)

    try:
        empresa_uuid = uuid.UUID(empresa_id)
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    result = await db.execute(
        select(Conexao).where(
            Conexao.id == conexao_uuid,
            Conexao.empresa_id == empresa_uuid,
        )
    )
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    await db.delete(conexao)
    await db.commit()
    return {"status": "success", "message": "Conexão excluída com sucesso."}


@router.put("/{conexao_id}", response_model=ConexaoResponse, status_code=status.HTTP_200_OK)
async def atualizar_conexao(
    empresa_id: str,
    conexao_id: str,
    payload: ConexaoUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    await _buscar_empresa(db, empresa_id)

    try:
        empresa_uuid = uuid.UUID(empresa_id)
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    result = await db.execute(
        select(Conexao).where(
            Conexao.id == conexao_uuid,
            Conexao.empresa_id == empresa_uuid,
        )
    )
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    tipo = _normalizar_tipo(payload.tipo or conexao.tipo.value)
    credenciais_merge = dict(conexao.credenciais or {})
    credenciais_merge.update(payload.credenciais or {})

    payload_validacao = ConexaoCreate(
        tipo=tipo.value,
        nome_instancia=payload.nome_instancia if payload.nome_instancia is not None else conexao.nome_instancia,
        credenciais=credenciais_merge,
        status=payload.status if payload.status is not None else conexao.status,
    )
    nome_instancia, credenciais = _validar_payload(tipo, payload_validacao)
    await _testar_conectividade(tipo, credenciais)
    if tipo == TipoConexao.EVOLUTION:
        await _validar_instancia_evolution_existe(credenciais)
        settings_result = await aplicar_settings_padrao_evolution(
            str(credenciais.get("evolution_url") or evolution_base_url()).rstrip("/"),
            evolution_global_api_key() or str(credenciais.get("evolution_apikey") or "").strip(),
            str(credenciais.get("evolution_instance") or "").strip(),
        )
        if not settings_result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=str(sanitize_for_log(settings_result.get("detail") or "Falha ao aplicar settings padrão Evolution.")),
            )

    result_existente = await db.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            Conexao.tipo == tipo,
            Conexao.nome_instancia == nome_instancia,
            Conexao.id != conexao_uuid,
        )
    )
    existente = result_existente.scalars().first()
    if existente:
        raise HTTPException(
            status_code=409,
            detail="Já existe outra conexão desse tipo com o mesmo nome/identificador para esta empresa."
        )

    conexao.tipo = tipo
    conexao.nome_instancia = nome_instancia
    conexao.credenciais = credenciais
    conexao.status = _normalizar_status_conexao(payload.status or conexao.status, default="ativo")

    try:
        await db.commit()
        await db.refresh(conexao)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Erro ao atualizar conexão: {exc}") from exc

    return _serialize_conexao(request, empresa_id, conexao)


@status_router.get("/{conexao_id}/status", status_code=status.HTTP_200_OK)
async def status_conexao(
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    try:
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    query = select(Conexao).where(Conexao.id == conexao_uuid)
    if not (is_root_admin_email(current_user.email) or is_super_admin_role(current_user.role)):
        if not is_admin_empresa_role(current_user.role) or current_user.empresa_id is None:
            raise HTTPException(status_code=403, detail="Acesso negado para esta conexão.")
        query = query.where(Conexao.empresa_id == current_user.empresa_id)
    result = await db.execute(query)
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    await _validar_acesso_conexao_usuario(conexao, current_user)

    if conexao.tipo == TipoConexao.INSTAGRAM:
        raise HTTPException(
            status_code=410,
            detail="O status Instagram é fornecido pela API canônica /api/empresas/{empresa_id}/instagram/connections.",
        )

    try:
        if conexao.tipo == TipoConexao.EVOLUTION:
            status_result = await consultar_status_conexao(conexao.id)
            if status_result.get("success"):
                status_normalizado = str(status_result.get("status") or "disconnected").lower()
                online = status_normalizado == "open"
                status_db = "ativo" if online else ("connecting" if status_normalizado == "connecting" else "disconnected")
                return {
                    "conexao_id": str(conexao.id),
                    "status": status_normalizado,
                    "online": online,
                }
            return {
                "conexao_id": str(conexao.id),
                "status": "disconnected",
                "online": False,
                "detail": status_result.get("detail"),
            }

        current_status, online = await _consultar_status_conexao(conexao)
        return {
            "conexao_id": str(conexao.id),
            "status": str(current_status or "").lower(),
            "online": online,
        }
    except Exception as exc:
        return {
            "conexao_id": str(conexao.id),
            "status": "disconnected",
            "online": False,
            "detail": str(exc),
        }


@status_router.get("/status/{conexao_id}", status_code=status.HTTP_200_OK)
async def status_conexao_alias(
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    return await status_conexao(conexao_id=conexao_id, db=db, current_user=current_user)


@status_router.get("/{conexao_id}/qrcode", status_code=status.HTTP_200_OK)
async def obter_qrcode_conexao(
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    try:
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    query = select(Conexao).where(Conexao.id == conexao_uuid)
    if not (is_root_admin_email(current_user.email) or is_super_admin_role(current_user.role)):
        if not is_admin_empresa_role(current_user.role) or current_user.empresa_id is None:
            raise HTTPException(status_code=403, detail="Acesso negado para esta conexão.")
        query = query.where(Conexao.empresa_id == current_user.empresa_id)
    result = await db.execute(query)
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    await _validar_acesso_conexao_usuario(conexao, current_user)

    if conexao.tipo != TipoConexao.EVOLUTION:
        raise HTTPException(status_code=400, detail="QR Code disponível apenas para conexões Evolution.")

    resultado = await obter_qr_code(conexao.id)
    if not resultado.get("success"):
        status_code = 409 if resultado.get("already_connected") else 400
        raise HTTPException(status_code=status_code, detail=resultado.get("detail") or "Falha ao obter QR Code.")

    return {
        "conexao_id": str(conexao.id),
        "base64": resultado.get("base64"),
        "detail": resultado.get("detail"),
    }
