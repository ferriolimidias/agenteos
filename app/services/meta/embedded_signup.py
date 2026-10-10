from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.secret_sanitizer import sanitize_for_api, sanitize_for_log
from app.services.meta.credential_crypto import decrypt_meta_secret, encrypt_meta_secret, mask_last4
from app.services.meta.graph_client import MetaGraphClient, MetaGraphError
from app.services.meta.settings import MetaAppSettings, meta_settings


META_OFFICIAL_PROVIDER = "meta_official"
META_OFFICIAL_CHANNEL = "whatsapp"
CONNECTION_MODE_CLOUD_API = "cloud_api"
CONNECTION_MODE_COEXISTENCE = "coexistence"


def mask_meta_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) <= 4:
        return "***"
    return f"***{raw[-4:]}"


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def public_embedded_signup_config(settings: MetaAppSettings | None = None) -> dict[str, Any]:
    settings = settings or meta_settings()
    return {
        "configured": settings.public_configured,
        "app_id": settings.app_id if settings.public_configured else None,
        "config_id": settings.embedded_signup_config_id if settings.public_configured else None,
        "graph_api_version": settings.graph_api_version,
        "sdk_url": "https://connect.facebook.net/pt_BR/sdk.js",
        "login_options": {
            "config_id": settings.embedded_signup_config_id if settings.public_configured else None,
            "response_type": "code",
            "override_default_response_type": True,
        },
        "missing": [
            name
            for name, value in {
                "META_APP_ID": settings.app_id,
                "META_EMBEDDED_SIGNUP_CONFIG_ID": settings.embedded_signup_config_id,
            }.items()
            if not value
        ],
        "live_actions_enabled": settings.live_actions_enabled,
    }


def sanitize_meta_error(exc: Exception) -> dict[str, str]:
    if isinstance(exc, MetaGraphError):
        return {
            "code": exc.code,
            "message": str(sanitize_for_log(str(exc))),
            "http_status": str(exc.status_code or ""),
            "graph_path": str(exc.graph_path or ""),
            "meta_error_code": str(exc.meta_code or ""),
            "error_subcode": str(exc.error_subcode or ""),
            "fbtrace_id": str(exc.fbtrace_id or ""),
        }
    return {"code": "unexpected_error", "message": str(sanitize_for_log(str(exc)))[:240]}


def classify_meta_error_for_user(error: dict[str, Any] | None) -> dict[str, Any] | None:
    if not error:
        return None
    code = str(error.get("code") or "unknown_graph_error")
    message = "Seu cadastro foi concluído na Meta, mas o AgentOS não conseguiu consultar os dados do número. A autorização foi preservada para nova tentativa."
    if code in {"token_expired", "invalid_auth", "invalid_token", "reauth_required"}:
        message = "A autorização da Meta não está mais válida. Será necessário reconectar depois da correção."
    elif code in {"missing_permission", "asset_not_shared"}:
        message = "A Meta autorizou o fluxo, mas o ativo não está disponível para este aplicativo ou faltam permissões."
    return {**error, "user_message": message}


def _digits_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or not raw.isdigit() or len(raw) < 5:
        return None
    return raw


def extract_signup_identifiers(signup_payload: dict[str, Any] | None) -> dict[str, str | None]:
    payload = signup_payload if isinstance(signup_payload, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        "meta_business_portfolio_id": _digits_id(data.get("business_id")),
        "meta_waba_id": _digits_id(data.get("waba_id")),
        "meta_phone_number_id": _digits_id(data.get("phone_number_id")),
    }


def detect_coexistence(phone: dict[str, Any], waba: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**(waba or {}), **(phone or {})}
    capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), list) else []
    capability_text = " ".join(str(item).lower() for item in capabilities)
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    requested = bool(metadata.get("coexistence_requested") or source.get("coexistence_requested"))
    available = "coexist" in capability_text or bool(source.get("coexistence_available"))
    confirmed = bool(source.get("coexistence_confirmed") or source.get("coexistence_active"))
    state = "ativo" if confirmed else ("disponivel" if available else ("pendente" if requested else "nao_verificado"))
    return {
        "requested": requested,
        "available": available,
        "confirmed": confirmed,
        "active": confirmed,
        "state": state,
        "source": "meta_capabilities" if capabilities else "not_returned_by_meta",
    }


def normalize_assets(businesses: list[dict[str, Any]], wabas_by_business: dict[str, list[dict[str, Any]]], phones_by_waba: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    normalized_businesses = []
    for business in businesses:
        business_id = str(business.get("id") or "")
        wabas = []
        for waba in wabas_by_business.get(business_id, []):
            waba_id = str(waba.get("id") or "")
            phones = phones_by_waba.get(waba_id, [])
            wabas.append(
                {
                    **sanitize_for_api(waba),
                    "phone_numbers": [sanitize_for_api(phone) for phone in phones],
                }
            )
        normalized_businesses.append({**sanitize_for_api(business), "wabas": wabas})
    return {"businesses": normalized_businesses}


async def discover_assets(client: MetaGraphClient) -> dict[str, Any]:
    businesses = await client.list_assigned_businesses()
    wabas_by_business: dict[str, list[dict[str, Any]]] = {}
    phones_by_waba: dict[str, list[dict[str, Any]]] = {}
    for business in businesses:
        business_id = str(business.get("id") or "")
        if not business_id:
            continue
        wabas = await client.list_owned_wabas(business_id)
        wabas_by_business[business_id] = wabas
        for waba in wabas:
            waba_id = str(waba.get("id") or "")
            if waba_id:
                phones_by_waba[waba_id] = await client.list_waba_phone_numbers(waba_id)
    return normalize_assets(businesses, wabas_by_business, phones_by_waba)


def encrypted_credentials_from_token(access_token: str, *, token_type: str | None = None, expires_in: Any = None) -> dict[str, Any]:
    return {
        "encrypted_access_token": encrypt_meta_secret(access_token),
        "token_configured": True,
        "token_last4": mask_last4(access_token),
        "token_type": token_type,
        "expires_in": expires_in,
    }


def decrypt_connection_token(credentials: dict[str, Any]) -> str:
    encrypted = str((credentials or {}).get("encrypted_access_token") or "").strip()
    return decrypt_meta_secret(encrypted)
