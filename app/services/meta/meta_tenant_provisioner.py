"""Tenant-scoped Meta asset discovery and conversion infrastructure setup.

This module is deliberately conservative: identity assets are discovered and
validated, the existing WABA Dataset is reused, and no mappings, events, or
campaigns are created as a side effect. Dataset creation is opt-in because a
new event source is an external Meta asset and must never be created by a
readiness check accidentally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conexao, Empresa, TipoConexao, TrafficMetaConfig
from app.services.meta.business_messaging_conversions import (
    _validate_waba_dataset_edge,
    resolve_meta_conversion_credential,
)
from app.services.meta.conversion_credentials import MetaCredentialResolutionError
from app.services.meta.graph_client import MetaGraphClient, MetaGraphError
from app.services.meta.marketing_api import MetaMarketingApiClient
from app.services.meta.marketing_credentials import MetaMarketingCapability, MetaMarketingCredentialResolver
from app.services.meta.settings import meta_settings
from app.services.meta.waba_dataset import ensure_business_messaging_dataset


PROVISIONING_STATES = (
    "NOT_CONNECTED",
    "DISCOVERING",
    "ASSETS_READY",
    "EVENT_SOURCE_READY",
    "CONVERSIONS_READY",
    "CAPI_READY",
    "ADS_READY",
    "READY",
)


@dataclass(frozen=True)
class MetaTenantProvisioningResult:
    empresa_id: str
    state: str
    assets: dict[str, Any]
    checks: dict[str, Any]
    custom_conversions: list[dict[str, Any]]
    created_dataset: bool = False
    errors: tuple[str, ...] = ()

    def sanitized(self) -> dict[str, Any]:
        return {
            "empresa_id": self.empresa_id,
            "state": self.state,
            "assets": self.assets,
            "checks": self.checks,
            "custom_conversions": self.custom_conversions,
            "created_dataset": self.created_dataset,
            "errors": list(self.errors),
        }


async def audit_business_meta_surfaces(
    session: AsyncSession,
    empresa_id: Any,
    *,
    business_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Audit Business-level surfaces with the least-privilege admin capability.

    The tenant WhatsApp token is intentionally not upgraded or reused for
    Business administration. Callers receive sanitized API errors so a missing
    ``business_management`` grant is distinguishable from an empty edge.
    """
    credential = await MetaMarketingCredentialResolver().resolve(
        session,
        empresa_id,
        MetaMarketingCapability.BUSINESS_MANAGEMENT,
    )
    client = MetaMarketingApiClient(
        credential.access_token,
        graph_version=credential.graph_version,
    )
    ids = [str(item).strip() for item in (business_ids or []) if str(item).strip()]
    result: dict[str, Any] = {"credential": credential.sanitized_metadata(), "businesses": {}}
    for business_id in ids:
        surfaces: dict[str, Any] = {}
        for key, loader in (
            ("ads_dataset", client.list_business_ads_datasets),
            ("offsite_signal_containers", client.list_business_offsite_signal_containers),
            ("event_source_groups", client.list_business_event_source_groups),
        ):
            try:
                surfaces[key] = {"status": "ok", "data": await loader(business_id)}
            except Exception as exc:
                surfaces[key] = {"status": "error", "error": str(exc)[:240]}
        result["businesses"][business_id] = surfaces
    return result


def _asset(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw or None


def _metadata(connection: Conexao) -> dict[str, Any]:
    return dict(getattr(connection, "metadata_json", None) or {})


async def _tenant_rows(session: AsyncSession, empresa_id: Any) -> tuple[Conexao | None, TrafficMetaConfig | None, str]:
    connection = (
        await session.execute(
            select(Conexao)
            .where(
                Conexao.empresa_id == empresa_id,
                Conexao.tipo == TipoConexao.META,
                Conexao.provider == "meta_official",
            )
            .order_by(Conexao.criado_em.asc(), Conexao.id.asc())
        )
    ).scalars().first()
    config = (
        await session.execute(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == empresa_id))
    ).scalars().first()
    name = str((await session.execute(select(Empresa.nome_empresa).where(Empresa.id == empresa_id))).scalar_one_or_none() or "Empresa")
    return connection, config, name.strip() or "Empresa"


async def audit_meta_tenant(session: AsyncSession, empresa_id: Any) -> MetaTenantProvisioningResult:
    """Read and validate all assets already assigned to one tenant."""
    connection, config, _ = await _tenant_rows(session, empresa_id)
    if connection is None:
        return MetaTenantProvisioningResult(str(empresa_id), "NOT_CONNECTED", {}, {}, [], errors=("OFFICIAL_META_CONNECTION_MISSING",))

    metadata = _metadata(connection)
    assets: dict[str, Any] = {
        "meta_connection_id": str(connection.id),
        "business_id": _asset(getattr(config, "business_id", None) or metadata.get("meta_business_portfolio_id") or metadata.get("business_id")),
        "ad_account_id": _asset(getattr(config, "ad_account_id", None) or metadata.get("ad_account_id") or metadata.get("meta_ad_account_id")),
        "page_id": _asset(getattr(config, "page_id", None) or getattr(connection, "page_id", None) or metadata.get("page_id")),
        "waba_id": _asset(getattr(connection, "waba_id", None) or metadata.get("meta_waba_id")),
        "phone_number_id": _asset(getattr(connection, "phone_number_id", None) or metadata.get("meta_phone_number_id")),
        "canonical_event_source_id": _asset(metadata.get("dataset_id") or metadata.get("meta_dataset_id")),
    }
    missing = tuple(f"{key.upper()}_MISSING" for key, value in assets.items() if key != "meta_connection_id" and not value)
    if missing:
        return MetaTenantProvisioningResult(str(empresa_id), "DISCOVERING", assets, {"assets": False}, [], errors=missing)

    try:
        credential = await resolve_meta_conversion_credential(session, empresa_id, require_dataset=True)
        graph = MetaGraphClient(access_token=credential.access_token, graph_api_version=meta_settings().graph_api_version)
        marketing = MetaMarketingApiClient(access_token=credential.access_token, graph_version=meta_settings().marketing_graph_api_version)
        waba = await graph._request("GET", assets["waba_id"], params={"fields": "id,name,owner_business_info{id,name}"})
        phone = await graph.get_phone_number(assets["phone_number_id"])
        account = await marketing.validate_ad_account(assets["ad_account_id"])
        pages = await marketing.list_pages()
        page = next((item for item in pages if str(item.get("id")) == assets["page_id"]), None)
        dataset_valid, dataset_status, dataset_body = await _validate_waba_dataset_edge(
            credential.access_token,
            assets["waba_id"],
            assets["canonical_event_source_id"],
        )
        custom_conversions = await marketing.list_custom_conversions(assets["ad_account_id"])
        assets.update({
            "business_from_ad_account": _asset((account.get("business") or {}).get("id")),
            "waba_owner_business_id": _asset((waba.get("owner_business_info") or {}).get("id")),
            "phone_display_number": phone.get("display_phone_number"),
            "dataset_edge_status": dataset_status,
        })
        checks = {
            "official_connection": True,
            "waba": str(waba.get("id")) == assets["waba_id"],
            "phone": str(phone.get("id")) == assets["phone_number_id"],
            "ad_account": str(account.get("account_id") or account.get("id", "")).replace("act_", "") == str(assets["ad_account_id"]),
            "page": bool(page),
            "dataset_waba_edge": dataset_valid,
            "dataset_edge_response": dataset_body if isinstance(dataset_body, dict) else {},
            "custom_conversions_readable": True,
        }
        all_ready = all(bool(value) for key, value in checks.items() if key not in {"dataset_edge_response"})
        state = "READY" if all_ready else ("CONVERSIONS_READY" if dataset_valid else "ASSETS_READY")
        return MetaTenantProvisioningResult(str(empresa_id), state, assets, checks, custom_conversions)
    except MetaCredentialResolutionError as exc:
        return MetaTenantProvisioningResult(str(empresa_id), "DISCOVERING", assets, {}, [], errors=(str(exc),))
    except MetaGraphError as exc:
        return MetaTenantProvisioningResult(str(empresa_id), "DISCOVERING", assets, {}, [], errors=(exc.code,))


async def provision_meta_conversion_infrastructure(
    session: AsyncSession,
    empresa_id: Any,
    *,
    create_dataset: bool = False,
) -> MetaTenantProvisioningResult:
    """Idempotently prepare a tenant; mappings and live delivery stay manual."""
    current = await audit_meta_tenant(session, empresa_id)
    if current.state != "NOT_CONNECTED" and current.assets.get("canonical_event_source_id"):
        return current
    if not create_dataset:
        return current
    try:
        await ensure_business_messaging_dataset(session, empresa_id)
    except (ValueError, MetaCredentialResolutionError, MetaGraphError) as exc:
        return MetaTenantProvisioningResult(
            current.empresa_id,
            current.state,
            current.assets,
            current.checks,
            current.custom_conversions,
            errors=tuple(current.errors) + (str(exc),),
        )
    refreshed = await audit_meta_tenant(session, empresa_id)
    return MetaTenantProvisioningResult(
        refreshed.empresa_id,
        refreshed.state,
        refreshed.assets,
        refreshed.checks,
        refreshed.custom_conversions,
        created_dataset=True,
        errors=refreshed.errors,
    )
