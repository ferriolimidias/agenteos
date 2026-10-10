"""Tenant and account scoped credentials for read-only Meta Marketing calls."""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.meta.conversion_credentials import MetaCredentialResolutionError
from app.services.meta.credential_crypto import MetaCredentialCryptoError, decrypt_meta_secret
from app.services.meta.embedded_signup import META_OFFICIAL_CHANNEL, META_OFFICIAL_PROVIDER
from app.services.meta.settings import meta_settings
from db.models import (
    Conexao,
    MetaMarketingTenantAccountGrant,
    TipoConexao,
    TrafficMetaConfig,
)

ADMIN_MARKETING_SOURCE = "admin_managed_marketing_token"
TENANT_MARKETING_SOURCE = "tenant_marketing_oauth"
EMBEDDED_SIGNUP_TENANT = "embedded_signup_tenant"  # legacy display/source compatibility only


class MetaMarketingCapability(str, enum.Enum):
    ADS_READ = "ads_read"
    ADS_MANAGEMENT = "ads_management"
    BUSINESS_MANAGEMENT = "business_management"


@dataclass(frozen=True)
class ResolvedMetaMarketingCredential:
    credential_source: str
    access_token: str
    empresa_id: str
    capability: MetaMarketingCapability
    graph_version: str
    connection_id: str | None = None
    business_id: str | None = None
    ad_account_id: str | None = None
    read_only: bool = True

    def sanitized_metadata(self) -> dict[str, Any]:
        return {
            "credential_source": self.credential_source,
            "empresa_id": self.empresa_id,
            "capability": self.capability.value,
            "graph_version": self.graph_version,
            "connection_id": self.connection_id,
            "business_id": _mask_id(self.business_id),
            "ad_account_id": _mask_id(self.ad_account_id),
            "read_only": self.read_only,
            "token_present": bool(self.access_token),
        }


def normalize_capability(value: str | MetaMarketingCapability) -> MetaMarketingCapability:
    try:
        return value if isinstance(value, MetaMarketingCapability) else MetaMarketingCapability(str(value or "").strip())
    except ValueError as exc:
        raise MetaCredentialResolutionError("CAPABILITY_INVALID") from exc


def _normalize_ad_account_id(value: Any) -> str:
    raw = str(value or "").strip()
    return raw[4:] if raw.lower().startswith("act_") else raw


def _mask_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return f"***{raw[-4:]}" if raw else None


def _admin_token() -> str:
    return str(os.getenv("META_MARKETING_ADMIN_ACCESS_TOKEN") or "").strip()


def marketing_admin_status(empresa_id: Any) -> dict[str, Any]:
    """Report availability without treating the process token as authorization."""
    del empresa_id
    return {
        "credential_source": ADMIN_MARKETING_SOURCE,
        "admin_token_configured": bool(_admin_token()),
        "tenant_allowlist_required": True,
        "tenant_account_binding_required": True,
        "read_only": True,
        "enabled": False,
    }


def _connection_metadata(connection: Conexao) -> dict[str, Any]:
    metadata = dict(getattr(connection, "metadata_json", None) or {})
    credentials = dict(getattr(connection, "credenciais", None) or {})
    return {**metadata, **credentials}


def _marketing_permissions(connection: Conexao) -> set[str]:
    values = _connection_metadata(connection)
    result: set[str] = set()
    for key in ("marketing_granted_permissions", "marketing_granted_scopes"):
        raw = values.get(key)
        if isinstance(raw, str):
            result.update(x.strip() for x in raw.replace(" ", ",").split(",") if x.strip())
        elif isinstance(raw, (list, tuple, set)):
            result.update(str(x).strip() for x in raw if str(x).strip())
    return result


def supports_ads_read(granted: set[str] | list[str] | tuple[str, ...]) -> bool:
    """Meta permits these two Marketing capabilities to read Ads assets."""
    permissions = {str(item).strip() for item in granted}
    return bool({MetaMarketingCapability.ADS_READ.value, MetaMarketingCapability.ADS_MANAGEMENT.value} & permissions)


class MarketingCredentialResolver:
    async def resolve(
        self,
        session: AsyncSession,
        empresa_id: Any,
        capability: str | MetaMarketingCapability,
        *,
        ad_account_id: str | None = None,
        allow_admin_fallback: bool = False,
    ) -> ResolvedMetaMarketingCredential:
        required = normalize_capability(capability)
        tenant_id = str(empresa_id)
        config = await self._tenant_config(session, empresa_id)
        configured_account = _normalize_ad_account_id(config.ad_account_id if config else None)
        requested_account = _normalize_ad_account_id(ad_account_id) if ad_account_id else configured_account
        if config is None or not configured_account:
            raise MetaCredentialResolutionError("AD_ACCOUNT_NOT_CONFIGURED")
        if requested_account != configured_account:
            raise MetaCredentialResolutionError("MARKETING_ACCOUNT_OUT_OF_SCOPE")
        business_id = str(config.business_id or "").strip()
        if not business_id:
            raise MetaCredentialResolutionError("BUSINESS_BINDING_REQUIRED")

        connection = await self._embedded_connection(session, empresa_id)
        tenant_credential = self._tenant_marketing_credential(tenant_id, connection, required, business_id, configured_account)
        if tenant_credential is not None:
            return tenant_credential

        if required != MetaMarketingCapability.ADS_READ or not allow_admin_fallback:
            raise MetaCredentialResolutionError("TENANT_MARKETING_CREDENTIAL_UNAVAILABLE")
        return await self._authorized_admin_credential(
            session,
            tenant_id=tenant_id,
            empresa_id=empresa_id,
            business_id=business_id,
            ad_account_id=configured_account,
        )

    async def resolve_tenant_for_permission_check(
        self,
        session: AsyncSession,
        empresa_id: Any,
        *,
        ad_account_id: str | None = None,
        allow_admin_fallback: bool = True,
    ) -> ResolvedMetaMarketingCredential:
        """Resolve the credential actually authorized for this configured account."""
        return await self.resolve(
            session,
            empresa_id,
            MetaMarketingCapability.ADS_READ,
            ad_account_id=ad_account_id,
            allow_admin_fallback=allow_admin_fallback,
        )

    async def _tenant_config(self, session: AsyncSession, empresa_id: Any) -> TrafficMetaConfig | None:
        return await session.scalar(
            select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == empresa_id)
        )

    async def _embedded_connection(self, session: AsyncSession, empresa_id: Any) -> Conexao | None:
        result = await session.execute(
            select(Conexao)
            .where(
                Conexao.empresa_id == empresa_id,
                Conexao.tipo == TipoConexao.META,
                Conexao.channel == META_OFFICIAL_CHANNEL,
                Conexao.provider == META_OFFICIAL_PROVIDER,
            )
            .order_by(Conexao.criado_em.desc(), Conexao.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    def _tenant_marketing_credential(
        self,
        tenant_id: str,
        connection: Conexao | None,
        capability: MetaMarketingCapability,
        business_id: str,
        ad_account_id: str,
    ) -> ResolvedMetaMarketingCredential | None:
        if connection is None:
            return None
        credentials = dict(getattr(connection, "credenciais", None) or {})
        # Never use encrypted_access_token: on this connection it belongs to
        # Meta Official WhatsApp, not to Marketing API.
        encrypted = credentials.get("encrypted_marketing_access_token")
        if not encrypted:
            return None
        grants = _marketing_permissions(connection)
        if capability == MetaMarketingCapability.ADS_READ and not supports_ads_read(grants):
            return None
        if capability != MetaMarketingCapability.ADS_READ and capability.value not in grants:
            return None
        try:
            token = decrypt_meta_secret(str(encrypted))
        except MetaCredentialCryptoError as exc:
            raise MetaCredentialResolutionError("MARKETING_CREDENTIAL_INVALID") from exc
        if not token:
            raise MetaCredentialResolutionError("MARKETING_CREDENTIAL_UNAVAILABLE")
        return ResolvedMetaMarketingCredential(
            TENANT_MARKETING_SOURCE,
            token,
            tenant_id,
            capability,
            meta_settings().marketing_graph_api_version,
            connection_id=str(connection.id),
            business_id=business_id,
            ad_account_id=ad_account_id,
            read_only=capability == MetaMarketingCapability.ADS_READ,
        )

    async def _authorized_admin_credential(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        empresa_id: Any,
        business_id: str,
        ad_account_id: str,
    ) -> ResolvedMetaMarketingCredential:
        token = _admin_token()
        if not token:
            raise MetaCredentialResolutionError("MARKETING_CREDENTIAL_UNAVAILABLE")
        grant = await session.scalar(
            select(MetaMarketingTenantAccountGrant).where(
                MetaMarketingTenantAccountGrant.empresa_id == empresa_id,
                MetaMarketingTenantAccountGrant.business_id == business_id,
                MetaMarketingTenantAccountGrant.ad_account_id == ad_account_id,
                MetaMarketingTenantAccountGrant.credential_source == "ADMIN_MANAGED_MARKETING_TOKEN",
                MetaMarketingTenantAccountGrant.read_enabled.is_(True),
            )
        )
        if grant is None or not grant.verified_at:
            raise MetaCredentialResolutionError("ADMIN_MARKETING_BUSINESS_BINDING_REQUIRED")
        return ResolvedMetaMarketingCredential(
            ADMIN_MARKETING_SOURCE,
            token,
            tenant_id,
            MetaMarketingCapability.ADS_READ,
            meta_settings().marketing_graph_api_version,
            business_id=business_id,
            ad_account_id=ad_account_id,
            read_only=True,
        )


# Backwards-compatible import alias while call sites converge on the shared name.
MetaMarketingCredentialResolver = MarketingCredentialResolver
