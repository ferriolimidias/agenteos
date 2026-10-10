"""Single server-side Meta credential resolver for conversions and Instagram.

Administrative tokens are read only from the process environment, which is
loaded from `.secrets/meta-system-user.env`; they are never persisted or
serialized. WhatsApp message transport does not use this resolver.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.meta.credential_crypto import MetaCredentialCryptoError, decrypt_meta_secret
from app.services.meta.settings import meta_settings
from db.models import Conexao, TipoConexao

CONVERSIONS_PURPOSE = "conversions"
INSTAGRAM_PURPOSE = "instagram"
# Compatibility alias for the existing conversion foundation only.
BUSINESS_MESSAGING_PURPOSE = "business_messaging"
SYSTEM_USER_TEST = "system_user_test"
EMBEDDED_SIGNUP_TENANT = "embedded_signup_tenant"
TENANT_CONNECTION = "tenant_connection"


class MetaCredentialResolutionError(ValueError):
    """Safe, stable credential error codes suitable for API responses/logs."""


@dataclass(frozen=True)
class ResolvedMetaCredential:
    credential_source: str
    access_token: str
    tenant_id: str
    waba_id: str = ""
    dataset_id: str = ""
    graph_version: str = ""
    connection_id: str | None = None
    business_id: str | None = None
    instagram_account_id: str | None = None
    page_id: str | None = None
    purpose: str = CONVERSIONS_PURPOSE

    def sanitized_metadata(self) -> dict[str, Any]:
        return {"credential_source": self.credential_source, "purpose": self.purpose, "tenant_id": self.tenant_id, "waba_id": _mask_id(self.waba_id), "dataset_id": _mask_id(self.dataset_id), "instagram_account_id": _mask_id(self.instagram_account_id), "page_id": _mask_id(self.page_id), "graph_version": self.graph_version, "connection_id": self.connection_id, "business_id": _mask_id(self.business_id)}


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "false").strip().lower() in {"1", "true", "yes", "on"}


def _allowed_tenants() -> set[str]:
    return {item.strip() for item in str(os.getenv("META_SYSTEM_USER_TEST_TENANT_IDS") or "").split(",") if item.strip()}


def _mask_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return f"***{raw[-4:]}" if raw else None


def system_user_status(empresa_id: Any) -> dict[str, Any]:
    """Public-safe state for UI; deliberately omits the token itself."""
    return {
        "credential_source": SYSTEM_USER_TEST,
        "credential_configured": bool(str(os.getenv("META_SYSTEM_USER_ACCESS_TOKEN") or "").strip()),
        "tenant_allowlisted": True,
        "tenant_allowlist_deprecated": True,
        "enabled": _enabled("META_SYSTEM_USER_TEST_ENABLED"),
    }


class MetaCredentialResolver:
    async def resolve_administrative(self, purpose: str = INSTAGRAM_PURPOSE) -> ResolvedMetaCredential:
        """Resolve the central asset credential for Super Admin discovery.

        This path intentionally has no tenant or connection lookup. The
        credential remains process-local and is never serialized to callers.
        Tenant assignment is a separate, scoped operation.
        """
        normalized_purpose = str(purpose or "").strip()
        if normalized_purpose not in {CONVERSIONS_PURPOSE, INSTAGRAM_PURPOSE}:
            raise MetaCredentialResolutionError("CREDENTIAL_SOURCE_INVALID")
        if not _enabled("META_SYSTEM_USER_TEST_ENABLED"):
            raise MetaCredentialResolutionError("CREDENTIAL_NOT_CONFIGURED")
        token = str(os.getenv("META_SYSTEM_USER_ACCESS_TOKEN") or "").strip()
        if not token:
            raise MetaCredentialResolutionError("CREDENTIAL_NOT_CONFIGURED")
        return ResolvedMetaCredential(
            SYSTEM_USER_TEST,
            token,
            "__administrative__",
            graph_version=meta_settings().graph_api_version,
            purpose=normalized_purpose,
        )

    async def resolve_conversion_setup(self, session: AsyncSession, empresa_id: Any) -> ResolvedMetaCredential:
        """Resolve the central test credential and linked WABA before a dataset exists.

        This is intentionally setup-only. Event delivery still uses ``resolve``
        and therefore requires a dataset persisted on the tenant connection.
        """
        tenant_id = str(empresa_id)
        if not _enabled("META_SYSTEM_USER_TEST_ENABLED"):
            raise MetaCredentialResolutionError("CREDENTIAL_NOT_CONFIGURED")
        token = str(os.getenv("META_SYSTEM_USER_ACCESS_TOKEN") or "").strip()
        if not token:
            raise MetaCredentialResolutionError("CREDENTIAL_NOT_CONFIGURED")
        connection = await self._connection(session, empresa_id, CONVERSIONS_PURPOSE)
        metadata = dict(getattr(connection, "metadata_json", None) or {})
        waba_id = str(getattr(connection, "waba_id", "") or metadata.get("meta_waba_id") or "").strip()
        if not waba_id:
            raise MetaCredentialResolutionError("BLOCKED_WABA_NOT_LINKED")
        return ResolvedMetaCredential(
            SYSTEM_USER_TEST, token, tenant_id, waba_id, "",
            meta_settings().graph_api_version, str(connection.id),
            str(metadata.get("meta_business_portfolio_id") or "").strip() or None,
            purpose=CONVERSIONS_PURPOSE,
        )

    async def resolve(
        self,
        session: AsyncSession,
        empresa_id: Any,
        purpose: str,
        requested_source: str | None = None,
        mode: str = "dry_run",
        connection_id: str | None = None,
    ) -> ResolvedMetaCredential:
        normalized_purpose = CONVERSIONS_PURPOSE if purpose == BUSINESS_MESSAGING_PURPOSE else str(purpose or "").strip()
        if normalized_purpose not in {CONVERSIONS_PURPOSE, INSTAGRAM_PURPOSE}:
            raise MetaCredentialResolutionError("CREDENTIAL_SOURCE_INVALID")
        source = str(requested_source or (EMBEDDED_SIGNUP_TENANT if normalized_purpose == CONVERSIONS_PURPOSE else TENANT_CONNECTION)).strip()
        tenant_id = str(empresa_id)
        if source == SYSTEM_USER_TEST:
            return await self._system_user(session, empresa_id, tenant_id, normalized_purpose, connection_id=connection_id)
        if normalized_purpose == CONVERSIONS_PURPOSE and source == EMBEDDED_SIGNUP_TENANT:
            connection = await self._connection(session, empresa_id, normalized_purpose, connection_id=connection_id)
            return self._embedded(tenant_id, connection, normalized_purpose)
        if normalized_purpose == INSTAGRAM_PURPOSE and source == TENANT_CONNECTION:
            connection = await self._connection(session, empresa_id, normalized_purpose, connection_id=connection_id)
            return self._tenant_instagram(tenant_id, connection)
        raise MetaCredentialResolutionError("CREDENTIAL_SOURCE_INVALID")

    async def _connection(
        self,
        session: AsyncSession,
        empresa_id: Any,
        purpose: str = CONVERSIONS_PURPOSE,
        *,
        connection_id: str | None = None,
    ) -> Conexao:
        if purpose == INSTAGRAM_PURPOSE:
            query = select(Conexao).where(Conexao.empresa_id == empresa_id, Conexao.tipo == TipoConexao.INSTAGRAM, Conexao.channel == "instagram", Conexao.provider == "meta_instagram")
            if connection_id:
                query = query.where(Conexao.id == connection_id)
            connections = list((await session.execute(query.order_by(Conexao.criado_em.asc(), Conexao.id.asc()))).scalars().all())
            if len(connections) > 1 and not connection_id:
                raise MetaCredentialResolutionError("CONNECTION_ID_REQUIRED")
            connection = connections[0] if connections else None
            if not connection:
                raise MetaCredentialResolutionError("ASSET_ACCESS_DENIED")
            return connection
        query = select(Conexao).where(Conexao.empresa_id == empresa_id, Conexao.tipo == TipoConexao.META)
        connection = next((item for item in (await session.execute(query)).scalars().all() if str(getattr(item, "provider", "") or "") == "meta_official"), None)
        if connection is None:
            raise MetaCredentialResolutionError("ASSET_ACCESS_DENIED")
        return connection

    async def _system_user(self, session: AsyncSession, empresa_id: Any, tenant_id: str, purpose: str, connection_id: str | None = None) -> ResolvedMetaCredential:
        if not _enabled("META_SYSTEM_USER_TEST_ENABLED"):
            raise MetaCredentialResolutionError("CREDENTIAL_NOT_CONFIGURED")
        token = str(os.getenv("META_SYSTEM_USER_ACCESS_TOKEN") or "").strip()
        if not token:
            raise MetaCredentialResolutionError("CREDENTIAL_NOT_CONFIGURED")
        connection = await self._connection(session, empresa_id, purpose, connection_id=connection_id)
        if purpose == INSTAGRAM_PURPOSE:
            account_id = str(connection.instagram_business_account_id or "").strip()
            page_id = str(connection.page_id or "").strip()
            return ResolvedMetaCredential(
                SYSTEM_USER_TEST,
                token,
                tenant_id,
                graph_version=meta_settings().graph_api_version,
                connection_id=str(connection.id),
                instagram_account_id=account_id or None,
                page_id=page_id or None,
                purpose=purpose,
            )
        waba_id, dataset_id, business_id = self._conversion_assets(connection)
        return ResolvedMetaCredential(SYSTEM_USER_TEST, token, tenant_id, waba_id, dataset_id, meta_settings().graph_api_version, str(connection.id), business_id, purpose=purpose)

    @staticmethod
    def _conversion_assets(connection: Conexao) -> tuple[str, str, str | None]:
        metadata = dict(getattr(connection, "metadata_json", None) or {})
        waba_id = str(getattr(connection, "waba_id", "") or metadata.get("meta_waba_id") or "").strip()
        dataset_id = str(metadata.get("dataset_id") or metadata.get("meta_dataset_id") or "").strip()
        if not waba_id or not dataset_id:
            raise MetaCredentialResolutionError("ASSET_ACCESS_DENIED")
        return waba_id, dataset_id, str(metadata.get("meta_business_portfolio_id") or "").strip() or None

    def _embedded(self, tenant_id: str, connection: Conexao, purpose: str) -> ResolvedMetaCredential:
        waba_id, dataset_id, business_id = self._conversion_assets(connection)
        try:
            token = decrypt_meta_secret(str((getattr(connection, "credenciais", None) or {}).get("encrypted_access_token") or ""))
        except MetaCredentialCryptoError as exc:
            raise MetaCredentialResolutionError("CREDENTIAL_NOT_CONFIGURED") from exc
        return ResolvedMetaCredential(EMBEDDED_SIGNUP_TENANT, token, tenant_id, waba_id, dataset_id, meta_settings().graph_api_version, str(connection.id), business_id, purpose=purpose)

    def _tenant_instagram(self, tenant_id: str, connection: Conexao) -> ResolvedMetaCredential:
        try:
            token = decrypt_meta_secret(str((getattr(connection, "credenciais", None) or {}).get("encrypted_access_token") or ""))
        except MetaCredentialCryptoError as exc:
            raise MetaCredentialResolutionError("CREDENTIAL_NOT_CONFIGURED") from exc
        return ResolvedMetaCredential(TENANT_CONNECTION, token, tenant_id, graph_version=meta_settings().graph_api_version, connection_id=str(connection.id), instagram_account_id=str(connection.instagram_business_account_id or "") or None, page_id=str(connection.page_id or "") or None, purpose=INSTAGRAM_PURPOSE)
