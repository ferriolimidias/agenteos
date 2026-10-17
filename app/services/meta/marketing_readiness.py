"""Tenant/account scoped Marketing permission and asset readiness checks."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.meta.conversion_credentials import MetaCredentialResolutionError
from app.services.meta.marketing_api import MetaMarketingApiClient, normalize_ad_account_id
from app.services.meta.marketing_credentials import (
    ADMIN_MARKETING_SOURCE,
    META_MARKETING_OAUTH_PERMISSIONS,
    MetaMarketingCredentialResolver,
    SYSTEM_USER_MARKETING_SOURCE,
    supports_ads_read,
)
from app.services.meta.settings import meta_settings
from db.models import MetaAdsInsightDaily, TrafficMetaConfig


def granted_permissions(body: dict[str, Any]) -> set[str]:
    values = body.get("data") if isinstance(body.get("data"), list) else []
    return {
        str(item.get("permission") or "").strip()
        for item in values
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() == "granted"
        and item.get("permission")
    }


def _graph_error_code(exc: Exception) -> str:
    return str(getattr(exc, "code", "") or type(exc).__name__).strip().lower()


def _error_readiness(exc: Exception) -> str:
    code = _graph_error_code(exc)
    if code in {"token_expired", "invalid_auth", "invalid_token", "reauth_required"}:
        return "REAUTH_MARKETING_REQUIRED"
    if code in {"app_review_pending", "advanced_access_required"}:
        return "UNKNOWN"
    if code in {"missing_permission", "permission_denied"}:
        return "PERMISSION_DENIED"
    if code in {"unsupported_field", "invalid_object_id", "permanent_error"}:
        return "UNKNOWN"
    return "ERROR"


def _action_for_status(status: str) -> str | None:
    return {
        "REAUTH_MARKETING_REQUIRED": "Autorize uma credencial de Marketing com leitura de anúncios. A conexão WhatsApp não substitui essa autorização.",
        "BUSINESS_BINDING_REQUIRED": "Comprove o vínculo deste Business com a conta de anúncios antes de habilitar leitura administrada.",
        "ASSET_ACCESS_REQUIRED": "Conceda acesso à conta de anúncios configurada para esta credencial.",
        "PERMISSION_DENIED": "A credencial não tem permissão suficiente para consultar a Marketing API.",
        "CONFIG_INVALID": "Configure uma conta de anúncios válida para esta empresa.",
        "UNKNOWN": "A Meta não confirmou o acesso aos ativos; revise o vínculo no Business Manager e o acesso do aplicativo.",
        "ERROR": "Não foi possível validar a Marketing API; tente novamente mais tarde.",
    }.get(status)


async def _business_account_binding(
    client: MetaMarketingApiClient,
    *,
    expected_business_id: str,
    configured_account_id: str,
    actual_account: dict[str, Any],
) -> tuple[bool, str]:
    account_business = actual_account.get("business") if isinstance(actual_account.get("business"), dict) else {}
    actual_business_id = str(account_business.get("id") or "").strip()
    if actual_business_id and actual_business_id == expected_business_id:
        return True, "business_owner"
    shared = await client.list_business_client_ad_accounts(expected_business_id)
    expected_account = normalize_ad_account_id(configured_account_id)
    linked = any(
        isinstance(row, dict)
        and normalize_ad_account_id(row.get("account_id") or row.get("id")) == expected_account
        for row in shared
    )
    return linked, "delegated_client_ad_account" if linked else "unverified"


async def marketing_ads_readiness(
    session: AsyncSession,
    *,
    empresa_id: Any,
    today: date | None = None,
) -> dict[str, Any]:
    tenant_id = uuid.UUID(str(empresa_id))
    config = await session.scalar(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == tenant_id))
    settings = meta_settings()
    oauth_enabled = bool(
        settings.app_id and settings.app_secret and settings.credential_encryption_key and settings.redirect_uri
    )
    oauth_config = {
        "configured": bool(settings.app_id and settings.app_secret and settings.credential_encryption_key),
        "enabled": oauth_enabled,
        "action_required": None if oauth_enabled else "A autorização da Marketing API aguarda a configuração do retorno OAuth pela administração da plataforma.",
        "app_id": settings.app_id,
        "graph_api_version": settings.marketing_graph_api_version,
        "sdk_url": "https://connect.facebook.net/pt_BR/sdk.js",
        "requested_permissions": list(META_MARKETING_OAUTH_PERMISSIONS),
    }
    last_sync, stored_rows = (await session.execute(select(
        func.max(MetaAdsInsightDaily.fetched_at), func.count(MetaAdsInsightDaily.id),
    ).where(MetaAdsInsightDaily.empresa_id == tenant_id))).one()

    base: dict[str, Any] = {
        "status": "ACTION_REQUIRED",
        "marketing_readiness_status": "UNKNOWN",
        "token_present": False,
        "token_source": None,
        "credential_source": None,
        "token_type": None,
        "permissions": {name: False for name in META_MARKETING_OAUTH_PERMISSIONS},
        "asset_access": {"ad_account": False, "campaigns": False, "adsets": False, "ads": False},
        "insights": {"preflight": "not_run", "rows_returned": None, "date": None},
        "ad_account_configured": bool(config and config.ad_account_id),
        "business_binding": "configured" if config and config.business_id else "not_configured",
        "app_advanced_access_status": "UNKNOWN_REQUIRES_META_DASHBOARD",
        "credential_error": None,
        "last_sync_at": last_sync.isoformat() if last_sync else None,
        "stored_insights_rows": int(stored_rows or 0),
        "graph_pages_read": 0,
        "marketing_authorization": oauth_config,
    }

    if config is None or not config.ad_account_id:
        base["marketing_readiness_status"] = "CONFIG_INVALID"
        base["action_required"] = _action_for_status("CONFIG_INVALID")
        return base
    if not str(config.business_id or "").strip():
        base["marketing_readiness_status"] = "BUSINESS_BINDING_REQUIRED"
        base["action_required"] = _action_for_status("BUSINESS_BINDING_REQUIRED")
        return base

    try:
        credential = await MetaMarketingCredentialResolver().resolve_tenant_for_permission_check(
            session, tenant_id, ad_account_id=str(config.ad_account_id), allow_admin_fallback=True,
        )
    except MetaCredentialResolutionError as exc:
        code = str(exc)
        if code in {"BUSINESS_BINDING_REQUIRED", "ADMIN_MARKETING_BUSINESS_BINDING_REQUIRED"}:
            status = "BUSINESS_BINDING_REQUIRED"
        elif code in {"AD_ACCOUNT_NOT_CONFIGURED", "MARKETING_ACCOUNT_OUT_OF_SCOPE"}:
            status = "CONFIG_INVALID" if code == "AD_ACCOUNT_NOT_CONFIGURED" else "ASSET_ACCESS_REQUIRED"
        elif code in {"ADMIN_MARKETING_ASSET_NOT_LINKED", "ADMIN_MARKETING_ASSET_ACCESS_UNVERIFIED", "ADMIN_MARKETING_ASSET_BUSINESS_MISMATCH"}:
            status = "ASSET_ACCESS_REQUIRED"
        elif code in {"MARKETING_CREDENTIAL_INVALID", "CREDENTIAL_INVALID"}:
            status = "REAUTH_MARKETING_REQUIRED"
        elif code in {"MARKETING_CREDENTIAL_UNAVAILABLE", "TENANT_MARKETING_CREDENTIAL_UNAVAILABLE", "CREDENTIAL_NOT_CONFIGURED"}:
            status = "REAUTH_MARKETING_REQUIRED"
        else:
            status = "UNKNOWN"
        base.update({"marketing_readiness_status": status, "credential_error": code})
        base["action_required"] = _action_for_status(status)
        return base
    except Exception as exc:
        status = _error_readiness(exc)
        base.update({"marketing_readiness_status": status, "credential_error": _graph_error_code(exc)[:80]})
        base["action_required"] = _action_for_status(status)
        return base

    client = MetaMarketingApiClient(
        credential.access_token,
        graph_version=credential.graph_version,
        timeout_seconds=12.0,
        max_attempts=2,
        read_only=credential.read_only,
    )
    base.update({
        "token_present": True,
        "token_source": credential.credential_source,
        "credential_source": credential.credential_source,
        # The credential source is centrally managed, but Graph's /me and
        # permissions edges do not prove that the underlying Meta principal is
        # a System User or belongs to the Ferrioli Business.
        "token_type": "CENTRAL_META_CREDENTIAL" if credential.credential_source == SYSTEM_USER_MARKETING_SOURCE else (
            "ADMIN_MANAGED_MARKETING" if credential.credential_source == ADMIN_MARKETING_SOURCE else "TENANT_MARKETING_OAUTH"
        ),
    })
    try:
        permissions = granted_permissions(await client.get_permissions())
        base["permissions"] = {
            name: name in permissions for name in META_MARKETING_OAUTH_PERMISSIONS
        }
        if not supports_ads_read(permissions):
            base["marketing_readiness_status"] = "PERMISSION_DENIED"
            base["action_required"] = _action_for_status("PERMISSION_DENIED")
            return base

        expected_account = normalize_ad_account_id(config.ad_account_id)
        account = await client.validate_ad_account(str(config.ad_account_id))
        actual_account = normalize_ad_account_id(account.get("account_id") or account.get("id"))
        if actual_account != expected_account:
            base["marketing_readiness_status"] = "ASSET_ACCESS_REQUIRED"
            base["action_required"] = _action_for_status("ASSET_ACCESS_REQUIRED")
            return base
        account_status = account.get("account_status")
        if account_status is not None and str(account_status).strip().lower() not in {"1", "active"}:
            base["marketing_readiness_status"] = "ASSET_ACCESS_REQUIRED"
            base["action_required"] = "A conta de anúncios está desativada, restrita ou sem acesso ativo."
            return base
        if credential.credential_source == SYSTEM_USER_MARKETING_SOURCE:
            # The System User resolver has already required an exact, verified
            # tenant-to-account link and checked that the selected Business is
            # one of the Graph access contexts for this account. Do not issue a
            # second Business edge request that may require an extra scope and
            # contradict that verified grant.
            bound, method = True, "explicit_tenant_asset_link"
        else:
            bound, method = await _business_account_binding(
                client,
                expected_business_id=str(config.business_id).strip(),
                configured_account_id=str(config.ad_account_id),
                actual_account=account,
            )
        if not bound:
            base["business_binding"] = "unverified"
            base["marketing_readiness_status"] = "BUSINESS_BINDING_REQUIRED"
            base["action_required"] = _action_for_status("BUSINESS_BINDING_REQUIRED")
            return base
        base["business_binding"] = method
        base["asset_access"]["ad_account"] = True
    except Exception as exc:
        status = _error_readiness(exc)
        base.update({"marketing_readiness_status": status, "credential_error": _graph_error_code(exc)[:80]})
        base["action_required"] = _action_for_status(status)
        return base

    pages = [0]
    for name, operation in (
        ("campaigns", lambda: client.list_campaigns(config.ad_account_id, page_counter=pages, max_pages=1)),
        ("adsets", lambda: client.list_adsets(config.ad_account_id, page_counter=pages, max_pages=1)),
        ("ads", lambda: client.list_ads(config.ad_account_id, page_counter=pages, max_pages=1)),
    ):
        try:
            await operation()
            base["asset_access"][name] = True
        except Exception as exc:
            status = _error_readiness(exc)
            base.update({"marketing_readiness_status": status, "credential_error": _graph_error_code(exc)[:80]})
            base["action_required"] = _action_for_status(status)
            base["graph_pages_read"] = pages[0]
            return base

    preflight_day = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
    try:
        rows = await client.get_insights(
            config.ad_account_id,
            level="account",
            since=preflight_day.isoformat(),
            until=preflight_day.isoformat(),
            time_increment=1,
            account_scope=True,
            page_counter=pages,
            max_pages=1,
        )
        base["insights"] = {
            "preflight": "pass",
            "rows_returned": len(rows),
            "result": "rows_returned" if rows else "no_rows_returned",
            "date": preflight_day.isoformat(),
        }
    except Exception as exc:
        status = _error_readiness(exc)
        base["insights"] = {"preflight": "error", "rows_returned": None, "date": preflight_day.isoformat()}
        base.update({"marketing_readiness_status": status, "credential_error": _graph_error_code(exc)[:80]})
        base["action_required"] = _action_for_status(status)
        base["graph_pages_read"] = pages[0]
        return base

    state = "READY_ADMIN_MANAGED_READ" if credential.credential_source in {
        ADMIN_MARKETING_SOURCE, SYSTEM_USER_MARKETING_SOURCE,
    } else "READY_TENANT_OAUTH"
    base["marketing_readiness_status"] = state
    base["status"] = "READY"
    base["credential_error"] = None
    base["action_required"] = None
    base["graph_pages_read"] = pages[0]
    return base
