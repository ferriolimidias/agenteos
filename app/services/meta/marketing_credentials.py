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
    MetaPlatformAsset,
    MetaSystemUserCredential,
    MetaTenantAssetLink,
    TipoConexao,
    TrafficMetaConfig,
)

ADMIN_MARKETING_SOURCE = "admin_managed_marketing_token"
SYSTEM_USER_MARKETING_SOURCE = "admin_managed_system_user_token"
TENANT_MARKETING_SOURCE = "tenant_marketing_oauth"
EMBEDDED_SIGNUP_TENANT = "embedded_signup_tenant"  # legacy display/source compatibility only
META_MARKETING_CHANNEL = "marketing_ads"
META_MARKETING_PROVIDER = "facebook_marketing"
META_MARKETING_OAUTH_PERMISSIONS = (
    "ads_read",
    "ads_management",
    "business_management",
    "pages_show_list",
    "pages_read_engagement",
    "instagram_basic",
)


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
    """Legacy environment credential status; never means a tenant is authorized."""
    del empresa_id
    return {
        "credential_source": ADMIN_MARKETING_SOURCE,
        "legacy_env_token_configured": bool(_admin_token()),
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

        central: MetaSystemUserCredential | None = None
        central_asset_linked = False
        # Keep a malformed/legacy caller from surfacing an AttributeError. The
        # operational routes pass an AsyncSession; without it we simply skip
        # central credential discovery and fail closed at the grant boundary.
        if (
            allow_admin_fallback
            and callable(getattr(session, "scalar", None))
            and callable(getattr(session, "execute", None))
        ):
            central = await session.scalar(
                select(MetaSystemUserCredential).where(MetaSystemUserCredential.id == 1)
            )
            if central is not None:
                central_link_result = await session.execute(
                    select(MetaTenantAssetLink.id)
                    .join(MetaPlatformAsset, MetaPlatformAsset.id == MetaTenantAssetLink.asset_id)
                    .where(
                        MetaTenantAssetLink.empresa_id == empresa_id,
                        MetaPlatformAsset.asset_type == "ad_account",
                        MetaPlatformAsset.external_id == configured_account,
                    )
                )
                central_asset_linked = central_link_result.scalar_one_or_none() is not None
                if central_asset_linked:
                    # Once this exact account is linked to the central System
                    # User credential, that mapping is authoritative. A stale
                    # or disabled link must fail closed, never fall through to
                    # a different token for the same configured account.
                    return await self._authorized_admin_credential(
                        session,
                        capability=required,
                        tenant_id=tenant_id,
                        empresa_id=empresa_id,
                        business_id=business_id,
                        ad_account_id=configured_account,
                    )

        connection = await self._embedded_connection(session, empresa_id)
        tenant_credential = self._tenant_marketing_credential(
            tenant_id, connection, required, business_id, configured_account
        )
        if tenant_credential is not None:
            return tenant_credential
        for legacy_connection in await self._legacy_marketing_connections(session, empresa_id):
            if connection is not None and legacy_connection.id == connection.id:
                continue
            tenant_credential = self._tenant_marketing_credential(
                tenant_id, legacy_connection, required, business_id, configured_account
            )
            if tenant_credential is not None:
                return tenant_credential

        if not allow_admin_fallback:
            raise MetaCredentialResolutionError("TENANT_MARKETING_CREDENTIAL_UNAVAILABLE")
        if central is not None and not central_asset_linked:
            # A configured platform credential does not authorize every
            # tenant. Preserve an existing tenant credential until a Super
            # Admin explicitly links this account; do not fall back to a
            # process-wide environment token while the central DB credential
            # is present but unbound.
            raise MetaCredentialResolutionError("ADMIN_MARKETING_ASSET_NOT_LINKED")
        return await self._authorized_admin_credential(
            session,
            capability=required,
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
        marketing_result = await session.execute(
            select(Conexao)
            .where(
                Conexao.empresa_id == empresa_id,
                Conexao.tipo == TipoConexao.META,
                Conexao.channel == META_MARKETING_CHANNEL,
                Conexao.provider == META_MARKETING_PROVIDER,
            )
            .order_by(Conexao.atualizado_em.desc(), Conexao.id.desc())
            .limit(1)
        )
        marketing_connection = next((
            connection
            for connection in marketing_result.scalars().all()
            if dict(connection.credenciais or {}).get("encrypted_marketing_access_token")
        ), None)
        if marketing_connection is not None:
            return marketing_connection
        legacy_connections = await self._legacy_marketing_connections(session, empresa_id)
        return legacy_connections[0] if legacy_connections else None

    async def _legacy_marketing_connections(self, session: AsyncSession, empresa_id: Any) -> list[Conexao]:
        execute = getattr(session, "execute", None)
        if execute is None:
            return []
        result = await execute(
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
        return [
            connection
            for connection in result.scalars().all()
            if dict(connection.credenciais or {}).get("encrypted_marketing_access_token")
        ]

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
        metadata = dict(getattr(connection, "metadata_json", None) or {})
        stored_business = str(metadata.get("marketing_business_id") or "").strip()
        stored_account = _normalize_ad_account_id(metadata.get("marketing_ad_account_id"))
        # Legacy Marketing tokens without both bindings cannot safely follow a
        # tenant-editable Traffic Meta configuration. Require explicit binding
        # before using them for either reads or writes; read-only admin fallback
        # remains available only through a separately verified tenant grant.
        if not stored_business or not stored_account:
            return None
        if stored_business != business_id:
            return None
        if stored_account != _normalize_ad_account_id(ad_account_id):
            return None
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
        capability: MetaMarketingCapability,
        tenant_id: str,
        empresa_id: Any,
        business_id: str,
        ad_account_id: str,
    ) -> ResolvedMetaMarketingCredential:
        if not callable(getattr(session, "scalar", None)) or not callable(getattr(session, "execute", None)):
            raise MetaCredentialResolutionError("MARKETING_CREDENTIAL_UNAVAILABLE")
        grant = await session.scalar(
            select(MetaMarketingTenantAccountGrant).where(
                MetaMarketingTenantAccountGrant.empresa_id == empresa_id,
                MetaMarketingTenantAccountGrant.business_id == business_id,
                MetaMarketingTenantAccountGrant.ad_account_id == ad_account_id,
                MetaMarketingTenantAccountGrant.credential_source == "ADMIN_MANAGED_MARKETING_TOKEN",
            )
        )
        if grant is None or not grant.verified_at:
            raise MetaCredentialResolutionError("ADMIN_MARKETING_BUSINESS_BINDING_REQUIRED")
        if capability == MetaMarketingCapability.ADS_READ and not grant.read_enabled:
            raise MetaCredentialResolutionError("ADMIN_MARKETING_READ_NOT_ENABLED")
        if capability == MetaMarketingCapability.ADS_MANAGEMENT and not grant.write_enabled:
            raise MetaCredentialResolutionError("ADMIN_MARKETING_WRITE_NOT_ENABLED")

        central = await session.scalar(
            select(MetaSystemUserCredential).where(MetaSystemUserCredential.id == 1)
        )
        if central is not None:
            link_result = await session.execute(
                select(MetaTenantAssetLink, MetaPlatformAsset)
                .join(MetaPlatformAsset, MetaPlatformAsset.id == MetaTenantAssetLink.asset_id)
                .where(
                    MetaTenantAssetLink.empresa_id == empresa_id,
                    MetaPlatformAsset.asset_type == "ad_account",
                    MetaPlatformAsset.external_id == ad_account_id,
                )
            )
            linked = link_result.first()
            if linked is None:
                raise MetaCredentialResolutionError("ADMIN_MARKETING_ASSET_NOT_LINKED")
            link, asset = linked
            if not link.read_enabled or asset.access_state != "verified":
                raise MetaCredentialResolutionError("ADMIN_MARKETING_ASSET_ACCESS_UNVERIFIED")
            authorized_businesses = {
                str(item.get("business_id") or "").strip()
                for item in (asset.business_access_json or [])
                if isinstance(item, dict)
            }
            if business_id not in authorized_businesses:
                raise MetaCredentialResolutionError("ADMIN_MARKETING_ASSET_BUSINESS_MISMATCH")
            if link.write_enabled != bool(grant.write_enabled) and capability == MetaMarketingCapability.ADS_MANAGEMENT:
                raise MetaCredentialResolutionError("ADMIN_MARKETING_WRITE_GRANT_MISMATCH")
            if central.permissions_status != "verified":
                raise MetaCredentialResolutionError("META_PERMISSIONS_NOT_VERIFIED")
            permissions = {str(item).strip() for item in (central.granted_permissions or [])}
            if capability == MetaMarketingCapability.ADS_READ and not supports_ads_read(permissions):
                raise MetaCredentialResolutionError("META_ADS_READ_PERMISSION_MISSING")
            if capability == MetaMarketingCapability.ADS_MANAGEMENT and "ads_management" not in permissions:
                raise MetaCredentialResolutionError("META_ADS_MANAGEMENT_PERMISSION_MISSING")
            if capability == MetaMarketingCapability.BUSINESS_MANAGEMENT:
                business_link_result = await session.execute(
                    select(MetaTenantAssetLink, MetaPlatformAsset)
                    .join(MetaPlatformAsset, MetaPlatformAsset.id == MetaTenantAssetLink.asset_id)
                    .where(
                        MetaTenantAssetLink.empresa_id == empresa_id,
                        MetaTenantAssetLink.read_enabled.is_(True),
                        MetaPlatformAsset.asset_type == "business",
                        MetaPlatformAsset.external_id == business_id,
                        MetaPlatformAsset.access_state == "verified",
                    )
                )
                if business_link_result.first() is None:
                    raise MetaCredentialResolutionError("ADMIN_MARKETING_BUSINESS_ASSET_NOT_LINKED")
                if "business_management" not in permissions:
                    raise MetaCredentialResolutionError("META_BUSINESS_MANAGEMENT_PERMISSION_MISSING")
            try:
                token = decrypt_meta_secret(central.encrypted_access_token)
            except MetaCredentialCryptoError as exc:
                raise MetaCredentialResolutionError("MARKETING_CREDENTIAL_INVALID") from exc
            return ResolvedMetaMarketingCredential(
                SYSTEM_USER_MARKETING_SOURCE,
                token,
                tenant_id,
                capability,
                meta_settings().marketing_graph_api_version,
                business_id=business_id,
                ad_account_id=ad_account_id,
                # The central credential is write-capable only for an explicit
                # ADS_MANAGEMENT request whose tenant grant and deployment
                # gates are also satisfied. Business-level auditing stays GET-only.
                read_only=capability != MetaMarketingCapability.ADS_MANAGEMENT,
            )

        # Transition compatibility for an existing centrally managed token
        # supplied only through the old process environment. It remains
        # read-only and requires the same previously verified tenant grant.
        token = _admin_token()
        if not token or capability != MetaMarketingCapability.ADS_READ:
            raise MetaCredentialResolutionError("MARKETING_CREDENTIAL_UNAVAILABLE")
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
