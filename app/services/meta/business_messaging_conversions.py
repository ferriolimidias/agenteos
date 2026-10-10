from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.secret_sanitizer import sanitize_for_log
from app.services.meta.conversion_credentials import (
    CONVERSIONS_PURPOSE,
    EMBEDDED_SIGNUP_TENANT,
    MetaCredentialResolutionError,
    ResolvedMetaCredential,
    SYSTEM_USER_TEST,
)
from app.services.meta.credential_crypto import MetaCredentialCryptoError, decrypt_meta_secret, encrypt_meta_secret
from app.services.meta.settings import meta_settings
from app.services.meta.waba_dataset import (
    _connection_dataset_id,
    _connection_waba_id,
    _empresa_name,
    _official_connection_for_update,
    _set_connection_dataset,
    _sync_traffic_meta_dataset_mirror,
    _traffic_config_for_update,
    is_generic_dataset_name,
    proposed_business_messaging_dataset_name,
)
from db.models import BusinessMessagingConversionMapping, Conexao, TipoConexao, TrafficMetaConfig


BUSINESS_MESSAGING_CONVERSIONS = "business_messaging_conversions"
TENANT_CONVERSION_TOKEN = "tenant_conversion_token"
SYSTEM_USER_CONVERSIONS_FALLBACK = "system_user_conversions_fallback"
CONVERSION_TOKEN_CREDENTIAL_KEY = "encrypted_business_messaging_conversion_access_token"
CONVERSION_TOKEN_METADATA_KEY = "business_messaging_conversion_token"
REQUIRED_CONVERSION_SCOPES = {"whatsapp_business_management", "whatsapp_business_manage_events"}
BUSINESS_MESSAGING_TRIGGER_SOURCE_OPTIONS = [
    {"value": "ai", "label": "Inteligência Artificial"},
    {"value": "manual", "label": "Alteração manual"},
    {"value": "automation", "label": "Automação"},
    {"value": "integration", "label": "Integração"},
    {"value": "system", "label": "Sistema"},
    {"value": "funnel", "label": "Funil"},
    {"value": "followup", "label": "Follow-up"},
    {"value": "campaign", "label": "Campanha"},
]
DEFAULT_BUSINESS_MESSAGING_ALLOWED_SOURCES = ["integration", "manual", "system"]
PUBLIC_MESSAGING_SOURCE_BINDING_API_FOUND = False
PUBLIC_MESSAGING_SOURCE_BINDING_ENDPOINT: str | None = None
FACEBOOK_PAGE_BINDING_REQUIRED_FOR_WHATSAPP_CAPI = False
MESSAGING_SOURCE_MANUAL_ACTION_DESCRIPTION = (
    "Nenhuma API pública oficial separada foi identificada para vincular Página/Fonte Mensagens ao Dataset. "
    "Pelo contrato público de Business Messaging Conversions, o Dataset da WABA fica pronto para eventos após "
    "GET/POST /{WABA_ID}/dataset e validação de acesso; a verificação no Events Manager ocorre após ingestão de eventos."
)


class ConversionGraphTransport(Protocol):
    async def get(self, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None) -> Any: ...
    async def post(self, url: str, *, headers: dict[str, str]) -> Any: ...


@dataclass(frozen=True)
class ConversionValidationResult:
    valid: bool
    capability_ready: bool
    waba_access: bool
    dataset_valid: bool
    scopes: list[str]
    granular_scopes: list[dict[str, Any]]
    token_type: str | None = None
    app_id: str | None = None
    application: str | None = None
    expires_at: int | None = None
    fingerprint: str | None = None
    source: str = TENANT_CONVERSION_TOKEN
    error: str | None = None

    def sanitized(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "capability_ready": self.capability_ready,
            "waba_access": self.waba_access,
            "dataset_valid": self.dataset_valid,
            "scopes": sorted(self.scopes),
            "granular_scopes": _safe_granular_scopes(self.granular_scopes),
            "token_type": self.token_type,
            "app_id": _mask_id(self.app_id),
            "application": self.application,
            "expires_at": self.expires_at,
            "fingerprint": self.fingerprint,
            "source": self.source,
            "error": self.error,
        }


def _mask_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return f"***{raw[-4:]}" if raw else None


def _token_fingerprint(token: str) -> str:
    digest = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest().upper()
    return f"********{digest[-4:]}"


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_granular_scopes(scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for item in scopes or []:
        if not isinstance(item, dict):
            continue
        safe.append(
            {
                "scope": str(item.get("scope") or ""),
                "target_ids": [_mask_id(value) for value in item.get("target_ids") or []],
            }
        )
    return safe


def _metadata(connection: Conexao) -> dict[str, Any]:
    return dict(getattr(connection, "metadata_json", None) or {})


def _credentials(connection: Conexao) -> dict[str, Any]:
    return dict(getattr(connection, "credenciais", None) or {})


async def _graph_get(
    token: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    transport: ConversionGraphTransport | None = None,
    graph_version: str | None = None,
) -> tuple[int, dict[str, Any]]:
    version = graph_version or meta_settings().graph_api_version
    url = f"https://graph.facebook.com/{version}/{str(path).strip().lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"} if str(token or "").strip() else {}
    if transport is not None:
        response = await transport.get(url, headers=headers, params=params)
    else:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers, params=params)
    status = int(getattr(response, "status_code", 0) or 0)
    try:
        body = response.json()
    except Exception:
        body = {}
    return status, sanitize_for_log(body if isinstance(body, dict) else {})


async def _debug_token(token: str, *, transport: ConversionGraphTransport | None = None) -> dict[str, Any]:
    settings = meta_settings()
    if not settings.app_id or not settings.app_secret:
        return {"available": False, "reason": "app_credentials_missing"}
    app_access = f"{settings.app_id}|{settings.app_secret}"
    status, body = await _graph_get(
        "",
        "debug_token",
        params={"input_token": token, "access_token": app_access},
        transport=transport,
        graph_version=settings.graph_api_version,
    )
    if status >= 400:
        return {"available": False, "status": status, "error": body.get("error") if isinstance(body, dict) else None}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return {"available": True, "data": data}


def _scope_set(debug: dict[str, Any]) -> set[str]:
    data = debug.get("data") if isinstance(debug.get("data"), dict) else {}
    scopes = set(str(item) for item in data.get("scopes") or [] if item)
    for item in data.get("granular_scopes") or []:
        if isinstance(item, dict) and item.get("scope"):
            scopes.add(str(item.get("scope")))
    return scopes


def _dataset_list_contains(dataset_body: dict[str, Any], dataset_id: str) -> bool:
    expected = str(dataset_id or "").strip()
    if not expected:
        return False
    data = dataset_body.get("data") if isinstance(dataset_body.get("data"), list) else []
    return any(str(item.get("id") or "").strip() == expected for item in data if isinstance(item, dict))


async def _validate_waba_dataset_edge(
    token: str,
    waba_id: str,
    dataset_id: str,
    *,
    transport: ConversionGraphTransport | None = None,
) -> tuple[bool, int, dict[str, Any]]:
    status, body = await _graph_get(
        token,
        f"{waba_id}/dataset",
        params={"fields": "id,name"},
        transport=transport,
    )
    return bool(200 <= status < 300 and _dataset_list_contains(body, dataset_id)), status, body


async def official_whatsapp_connection(session: AsyncSession, empresa_id: Any, *, for_update: bool = False) -> Conexao:
    if for_update:
        return await _official_connection_for_update(session, empresa_id)
    result = await session.execute(
        select(Conexao)
        .where(Conexao.empresa_id == empresa_id, Conexao.tipo == TipoConexao.META, Conexao.provider == "meta_official")
        .order_by(Conexao.criado_em.asc(), Conexao.id.asc())
    )
    connection = result.scalars().first()
    if not connection:
        raise MetaCredentialResolutionError("OFFICIAL_WHATSAPP_CONNECTION_MISSING")
    return connection


async def validate_conversion_token(
    session: AsyncSession,
    empresa_id: Any,
    token: str,
    *,
    transport: ConversionGraphTransport | None = None,
) -> ConversionValidationResult:
    clean_token = str(token or "").strip()
    if not clean_token:
        return ConversionValidationResult(False, False, False, False, [], [], error="TOKEN_EMPTY")
    connection = await official_whatsapp_connection(session, empresa_id)
    waba_id = _connection_waba_id(connection)
    if not waba_id:
        return ConversionValidationResult(False, False, False, False, [], [], fingerprint=_token_fingerprint(clean_token), error="WABA_MISSING")

    debug = await _debug_token(clean_token, transport=transport)
    scopes = sorted(_scope_set(debug))
    data = debug.get("data") if isinstance(debug.get("data"), dict) else {}
    debug_valid = bool(data.get("is_valid", True)) if debug.get("available") else True
    capability_ready = bool(REQUIRED_CONVERSION_SCOPES.issubset(set(scopes))) if debug.get("available") else True

    waba_status, _waba_body = await _graph_get(clean_token, waba_id, params={"fields": "id,name,owner_business_info"}, transport=transport)
    waba_access = 200 <= waba_status < 300
    dataset_id = _connection_dataset_id(connection)
    dataset_valid = False
    if dataset_id:
        dataset_valid, _dataset_status, _dataset_body = await _validate_waba_dataset_edge(
            clean_token,
            waba_id,
            dataset_id,
            transport=transport,
        )

    valid = bool(debug_valid and capability_ready and waba_access)
    return ConversionValidationResult(
        valid=valid,
        capability_ready=capability_ready,
        waba_access=waba_access,
        dataset_valid=dataset_valid,
        scopes=scopes,
        granular_scopes=list(data.get("granular_scopes") or []) if isinstance(data.get("granular_scopes"), list) else [],
        token_type=str(data.get("type") or data.get("token_type") or "") or None,
        app_id=str(data.get("app_id") or "") or None,
        application=str(data.get("application") or "") or None,
        expires_at=data.get("expires_at") if isinstance(data.get("expires_at"), int) else None,
        fingerprint=_token_fingerprint(clean_token),
        error=None if valid else "CONVERSION_CAPABILITY_OR_WABA_ACCESS_MISSING",
    )


async def save_conversion_token(
    session: AsyncSession,
    empresa_id: Any,
    token: str,
    *,
    transport: ConversionGraphTransport | None = None,
) -> ConversionValidationResult:
    validation = await validate_conversion_token(session, empresa_id, token, transport=transport)
    if not validation.valid:
        return validation
    connection = await official_whatsapp_connection(session, empresa_id, for_update=True)
    creds = _credentials(connection)
    creds[CONVERSION_TOKEN_CREDENTIAL_KEY] = encrypt_meta_secret(str(token).strip())
    connection.credenciais = creds
    flag_modified(connection, "credenciais")

    metadata = _metadata(connection)
    metadata[CONVERSION_TOKEN_METADATA_KEY] = {
        "configured": True,
        "valid": True,
        "capability_ready": validation.capability_ready,
        "waba_access": validation.waba_access,
        "dataset_valid": validation.dataset_valid,
        "fingerprint": validation.fingerprint,
        "origin": "conversions_api",
        "last_validated_at": _now_iso(),
        "updated_at": _now_iso(),
        "scopes": sorted(validation.scopes),
    }
    connection.metadata_json = metadata
    flag_modified(connection, "metadata_json")
    await session.commit()
    return validation


async def conversion_token_status(session: AsyncSession, empresa_id: Any) -> dict[str, Any]:
    try:
        connection = await official_whatsapp_connection(session, empresa_id)
    except MetaCredentialResolutionError:
        return {"configured": False, "valid": False, "capability_ready": False, "source": None}
    metadata = _metadata(connection)
    token_meta = dict(metadata.get(CONVERSION_TOKEN_METADATA_KEY) or {})
    creds = _credentials(connection)
    configured = bool(creds.get(CONVERSION_TOKEN_CREDENTIAL_KEY))
    if not configured:
        try:
            credential = await resolve_meta_conversion_credential(session, empresa_id, require_dataset=False)
            debug = await _debug_token(credential.access_token)
            scopes = sorted(_scope_set(debug))
            data = debug.get("data") if isinstance(debug.get("data"), dict) else {}
            debug_valid = bool(data.get("is_valid", True)) if debug.get("available") else True
            capability_ready = bool(REQUIRED_CONVERSION_SCOPES.issubset(set(scopes))) if debug.get("available") else True
            dataset_valid = False
            if credential.dataset_id:
                dataset_valid, _dataset_status, _dataset_body = await _validate_waba_dataset_edge(
                    credential.access_token,
                    credential.waba_id,
                    credential.dataset_id,
                )
            return {
                "configured": True,
                "valid": bool(debug_valid and capability_ready and credential.waba_id),
                "capability_ready": capability_ready,
                "waba_access": bool(credential.waba_id),
                "dataset_valid": dataset_valid,
                "fingerprint": _token_fingerprint(credential.access_token),
                "origin": credential.credential_source,
                "last_validated_at": None,
                "source": credential.credential_source,
            }
        except Exception:
            pass
    return {
        "configured": configured,
        "valid": bool(token_meta.get("valid")) if configured else False,
        "capability_ready": bool(token_meta.get("capability_ready")) if configured else False,
        "waba_access": bool(token_meta.get("waba_access")) if configured else False,
        "dataset_valid": bool(token_meta.get("dataset_valid")) if configured else False,
        "fingerprint": token_meta.get("fingerprint") if configured else None,
        "origin": token_meta.get("origin") if configured else None,
        "last_validated_at": token_meta.get("last_validated_at") if configured else None,
        "source": TENANT_CONVERSION_TOKEN if configured else None,
    }


async def _encrypted_token_credential(session: AsyncSession, empresa_id: Any, connection: Conexao) -> ResolvedMetaCredential | None:
    encrypted = str(_credentials(connection).get(CONVERSION_TOKEN_CREDENTIAL_KEY) or "").strip()
    if not encrypted:
        return None
    try:
        token = decrypt_meta_secret(encrypted)
    except MetaCredentialCryptoError as exc:
        raise MetaCredentialResolutionError("CONVERSION_TOKEN_DECRYPT_FAILED") from exc
    waba_id = _connection_waba_id(connection)
    dataset_id = _connection_dataset_id(connection)
    if not waba_id:
        raise MetaCredentialResolutionError("BLOCKED_WABA_NOT_LINKED")
    return ResolvedMetaCredential(
        TENANT_CONVERSION_TOKEN,
        token,
        str(empresa_id),
        waba_id,
        dataset_id,
        meta_settings().graph_api_version,
        str(connection.id),
        str(_metadata(connection).get("meta_business_portfolio_id") or "").strip() or None,
        purpose=CONVERSIONS_PURPOSE,
    )


async def resolve_meta_conversion_credential(
    session: AsyncSession,
    empresa_id: Any,
    *,
    require_dataset: bool = True,
) -> ResolvedMetaCredential:
    connection = await official_whatsapp_connection(session, empresa_id)
    waba_id = _connection_waba_id(connection)
    dataset_id = _connection_dataset_id(connection)
    if not waba_id:
        raise MetaCredentialResolutionError("BLOCKED_WABA_NOT_LINKED")
    if require_dataset and not dataset_id:
        raise MetaCredentialResolutionError("BLOCKED_DATASET_NOT_LINKED")

    credential = await _encrypted_token_credential(session, empresa_id, connection)
    if credential:
        return credential

    try:
        encrypted = str(_credentials(connection).get("encrypted_access_token") or "").strip()
        if encrypted:
            token = decrypt_meta_secret(encrypted)
            return ResolvedMetaCredential(
                EMBEDDED_SIGNUP_TENANT,
                token,
                str(empresa_id),
                waba_id,
                dataset_id,
                meta_settings().graph_api_version,
                str(connection.id),
                str(_metadata(connection).get("meta_business_portfolio_id") or "").strip() or None,
                purpose=CONVERSIONS_PURPOSE,
            )
    except MetaCredentialCryptoError:
        pass

    if str(os.getenv("META_SYSTEM_USER_TEST_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"}:
        token = str(os.getenv("META_SYSTEM_USER_ACCESS_TOKEN") or "").strip()
        if token:
            return ResolvedMetaCredential(
                SYSTEM_USER_CONVERSIONS_FALLBACK,
                token,
                str(empresa_id),
                waba_id,
                dataset_id,
                meta_settings().graph_api_version,
                str(connection.id),
                str(_metadata(connection).get("meta_business_portfolio_id") or "").strip() or None,
                purpose=CONVERSIONS_PURPOSE,
            )
    raise MetaCredentialResolutionError("CONVERSION_CREDENTIAL_NOT_CONFIGURED")


async def conversions_status(session: AsyncSession, empresa_id: Any) -> dict[str, Any]:
    try:
        connection = await official_whatsapp_connection(session, empresa_id)
    except MetaCredentialResolutionError:
        connection = None
    config = (await session.execute(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == empresa_id))).scalars().first()
    empresa_name = await _empresa_name(session, empresa_id)
    display_name = proposed_business_messaging_dataset_name(empresa_id=empresa_id, empresa_name=empresa_name)
    metadata = _metadata(connection) if connection else {}
    dataset_id = _connection_dataset_id(connection) if connection else ""
    dataset_meta_name = str(metadata.get("meta_dataset_name") or "unknown Event Data").strip() if dataset_id else None
    engine_mappings = list(
        (
            await session.execute(
                select(BusinessMessagingConversionMapping).where(BusinessMessagingConversionMapping.empresa_id == empresa_id)
            )
        ).scalars().all()
    )
    mapping_count = len(engine_mappings)
    funnel = [
        "ViewContent",
        "LeadSubmitted",
        "QualifiedLead",
        "AddToCart",
        "InitiateCheckout",
        "OrderCreated",
        "CartAbandoned",
        "Purchase",
    ]
    engine_by_event = {name: [item for item in engine_mappings if item.event_name == name and item.ativo] for name in funnel}
    allowed_source_values = {item["value"] for item in BUSINESS_MESSAGING_TRIGGER_SOURCE_OPTIONS}

    def _mapping_allowed_sources(mapping: BusinessMessagingConversionMapping | None) -> list[str]:
        metadata = getattr(mapping, "metadata_json", None) or {}
        raw = metadata.get("allowed_sources") if isinstance(metadata, dict) else None
        if not isinstance(raw, list):
            return list(DEFAULT_BUSINESS_MESSAGING_ALLOWED_SOURCES)
        values = sorted({str(item or "").strip().lower() for item in raw}.intersection(allowed_source_values))
        return values or list(DEFAULT_BUSINESS_MESSAGING_ALLOWED_SOURCES)

    def _mapping_has_test_event_code(mapping: BusinessMessagingConversionMapping | None) -> bool:
        metadata = getattr(mapping, "metadata_json", None) or {}
        return bool(str(metadata.get("test_event_code") or "").strip()) if isinstance(metadata, dict) else False

    token = await conversion_token_status(session, empresa_id)
    dataset_configured = bool(dataset_id)
    token_dataset_valid = bool(token["dataset_valid"])
    dataset_valid = bool(dataset_configured and (token_dataset_valid or not token["configured"]))
    messaging_source_configured = bool(dataset_valid)
    manual_action_required = bool(dataset_configured and not messaging_source_configured)
    return {
        "whatsapp_connected": bool(connection),
        "waba_detected": bool(_connection_waba_id(connection)) if connection else False,
        "conversion_token_configured": bool(token["configured"]),
        "conversion_token_valid": bool(token["valid"]),
        "conversion_capability_ready": bool(token["capability_ready"]),
        "conversion_token": token,
        "dataset_configured": dataset_configured,
        "dataset_valid": dataset_valid,
        "dataset_meta_id_masked": _mask_id(dataset_id),
        "dataset_agentos_display_name": display_name,
        "dataset_meta_name": dataset_meta_name,
        "dataset_meta_name_is_generic": is_generic_dataset_name(dataset_meta_name),
        "messaging_source": {
            "configured": messaging_source_configured,
            "automatic": bool(dataset_valid),
            "public_binding_api_found": PUBLIC_MESSAGING_SOURCE_BINDING_API_FOUND,
            "official_endpoint": PUBLIC_MESSAGING_SOURCE_BINDING_ENDPOINT,
            "facebook_page_binding_required": FACEBOOK_PAGE_BINDING_REQUIRED_FOR_WHATSAPP_CAPI,
            "facebook_page_binding_automatic": False,
            "manual_action_required": manual_action_required,
            "manual_action_description": MESSAGING_SOURCE_MANUAL_ACTION_DESCRIPTION if manual_action_required else None,
        },
        "mapping_count": mapping_count,
        "conversion_engine": {
            "trigger_model_present": True,
            "supported_triggers": ["tag_applied", "crm_stage_changed", "order_created", "payment_confirmed", "system_event"],
            "source_policy_supported": True,
            "source_options": BUSINESS_MESSAGING_TRIGGER_SOURCE_OPTIONS,
            "default_allowed_sources": DEFAULT_BUSINESS_MESSAGING_ALLOWED_SOURCES,
            "funnel": [
                {
                    "event_name": name,
                    "configured": bool(engine_by_event[name]),
                    "mapping_id": str(engine_by_event[name][0].id) if engine_by_event[name] else None,
                    "allowed_sources": _mapping_allowed_sources(engine_by_event[name][0] if engine_by_event[name] else None),
                    "test_event_code_configured": _mapping_has_test_event_code(engine_by_event[name][0] if engine_by_event[name] else None),
                }
                for name in funnel
            ],
            "mapping_count": len(engine_mappings),
        },
        "conversion_ready": bool(connection and token["valid"] and token["capability_ready"] and dataset_configured and token_dataset_valid and messaging_source_configured),
        "traffic_meta_dataset_role": "mirror/reference",
        "traffic_meta_dataset_id_masked": _mask_id(getattr(config, "dataset_id", None)) if config else None,
    }


async def setup_business_messaging_conversions(
    session: AsyncSession,
    empresa_id: Any,
    *,
    transport: ConversionGraphTransport | None = None,
) -> dict[str, Any]:
    """Orchestrate every public, tenant-scoped Business Messaging setup step.

    This deliberately does not call private Events Manager endpoints. If Meta
    exposes a public messaging-source binding API in the future, that step can
    be added here without changing the tenant contract.
    """
    dataset_result = await ensure_business_messaging_dataset_for_conversions(session, empresa_id, transport=transport)
    return {
        "dataset": dataset_result,
        "dataset_creation_automatic": True,
        "dataset_waba_binding_automatic": True,
        "public_messaging_source_binding_api_found": PUBLIC_MESSAGING_SOURCE_BINDING_API_FOUND,
        "official_endpoint": PUBLIC_MESSAGING_SOURCE_BINDING_ENDPOINT,
        "messaging_source_binding_automatic": False,
        "facebook_page_binding_required": FACEBOOK_PAGE_BINDING_REQUIRED_FOR_WHATSAPP_CAPI,
        "facebook_page_binding_automatic": False,
        "manual_action_required": False,
        "manual_action_description": None,
    }


async def ensure_business_messaging_dataset_for_conversions(
    session: AsyncSession,
    empresa_id: Any,
    *,
    transport: ConversionGraphTransport | None = None,
) -> dict[str, Any]:
    connection = await _official_connection_for_update(session, empresa_id)
    config: TrafficMetaConfig | None = await _traffic_config_for_update(session, empresa_id)
    empresa_name = await _empresa_name(session, empresa_id)
    display_name = proposed_business_messaging_dataset_name(empresa_id=empresa_id, empresa_name=empresa_name)
    waba_id = _connection_waba_id(connection)
    if not waba_id:
        raise MetaCredentialResolutionError("BLOCKED_WABA_NOT_LINKED")

    existing = _connection_dataset_id(connection)
    if existing:
        credential = await resolve_meta_conversion_credential(session, empresa_id, require_dataset=True)
        valid, _status, _body = await _validate_waba_dataset_edge(
            credential.access_token,
            waba_id,
            existing,
            transport=transport,
        )
        if not valid:
            raise MetaCredentialResolutionError("DATASET_VALIDATION_FAILED")
        _set_connection_dataset(connection, existing, display_name)
        _sync_traffic_meta_dataset_mirror(config, connection, existing)
        metadata = _metadata(connection)
        token_meta = dict(metadata.get(CONVERSION_TOKEN_METADATA_KEY) or {})
        if token_meta.get("configured"):
            token_meta["dataset_valid"] = True
            token_meta["last_validated_at"] = _now_iso()
            metadata[CONVERSION_TOKEN_METADATA_KEY] = token_meta
            connection.metadata_json = metadata
            flag_modified(connection, "metadata_json")
        await session.commit()
        return {"already_configured": True, "created": False, "dataset_id_masked": _mask_id(existing), "dataset_agentos_display_name": display_name}

    credential = await resolve_meta_conversion_credential(session, empresa_id, require_dataset=False)
    if str(credential.waba_id or "").strip() != waba_id:
        raise MetaCredentialResolutionError("DATASET_WABA_MAPPING_CHANGED")

    from app.services.meta.waba_dataset import WabaDatasetService

    created = await WabaDatasetService(credential, transport=transport).get_or_create_dataset()
    dataset_id = str(created.dataset_id or "").strip()
    _set_connection_dataset(connection, dataset_id, display_name)
    _sync_traffic_meta_dataset_mirror(config, connection, dataset_id)
    await session.commit()
    return {
        "already_configured": False,
        "created": True,
        "status_code": created.status_code,
        "fbtrace_id": created.fbtrace_id,
        "dataset_id_masked": _mask_id(dataset_id),
        "dataset_agentos_display_name": display_name,
    }
