from __future__ import annotations

import uuid
import re
import hmac
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import Usuario, require_super_admin
from app.services.meta.credential_crypto import (
    MetaCredentialCryptoError,
    decrypt_meta_secret,
    encrypt_meta_secret,
    mask_last4,
)
from app.services.meta.graph_client import MetaGraphError
from app.services.meta.marketing_credentials import supports_ads_read
from app.services.meta.system_user_assets import (
    discover_system_user_assets,
    read_crm_dataset_metadata,
    revalidate_asset,
    validate_asset_in_business,
    validate_system_user_token,
)
from db.database import get_db
from db.models import (
    Empresa,
    MetaMarketingTenantAccountGrant,
    MetaPlatformAsset,
    MetaSystemUserAudit,
    MetaSystemUserCredential,
    MetaTenantAssetLink,
    TrafficMetaConfig,
)

router = APIRouter(prefix="/api/admin/meta/system-user", tags=["Meta System User"])


class SystemUserTokenRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    token: str


class TenantAssetSelection(BaseModel):
    asset_id: uuid.UUID
    write_enabled: bool = False


class TenantAssetLinksRequest(BaseModel):
    assets: list[TenantAssetSelection] = Field(max_length=6)


class AssetIdValidation(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    asset_type: Literal["business", "ad_account", "page", "instagram_account", "dataset", "pixel"]
    external_id: str
    business_id: str | None = None


def _permission_rows(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _permission_capabilities(permissions: list[str], *, status: str) -> dict[str, str]:
    if status != "verified":
        return {name: "unknown" for name in (
            "ads_read", "ads_management", "business_management",
            "pages_show_list", "pages_read_engagement", "instagram_basic",
        )}
    granted = set(permissions)
    return {
        "ads_read": "granted" if {"ads_read", "ads_management"} & granted else "missing",
        "ads_management": "granted" if "ads_management" in granted else "missing",
        "business_management": "granted" if "business_management" in granted else "missing",
        "pages_show_list": "granted" if "pages_show_list" in granted else "missing",
        "pages_read_engagement": "granted" if "pages_read_engagement" in granted else "missing",
        "instagram_basic": "granted" if "instagram_basic" in granted else "missing",
    }


def _request_token(payload: SystemUserTokenRequest) -> str:
    token = payload.token.strip()
    if not 20 <= len(token) <= 8192:
        # Never echo an invalid secret in Pydantic's validation response.
        raise HTTPException(status_code=422, detail="O token informado possui formato ou tamanho inválido.")
    return token


def _credential_status(row: MetaSystemUserCredential | None) -> dict[str, Any]:
    if row is None:
        permissions_status = "unknown"
        permissions = []
        return {"configured": False, "validated": False, "token_masked": None,
                "system_user_id": None, "system_user_name": None, "app_id": None,
                "app_name": None,
                "system_user_identity_state": "unknown", "app_binding_state": "unknown",
                "permissions": permissions, "permissions_status": permissions_status,
                "permission_capabilities": _permission_capabilities(permissions, status=permissions_status),
                "permissions_error_code": None,
                "validated_at": None, "expires_at": None, "expiry_status": "unknown",
                "encryption_available": _encryption_available()}
    permissions = _permission_rows(row.granted_permissions)
    return {
        "configured": True,
        "validated": bool(row.validated_at),
        "token_masked": row.token_hint,
        "system_user_id": row.system_user_id,
        "system_user_name": row.system_user_name,
        "app_id": row.app_id,
        "app_name": row.app_name,
        # /me proves a Graph identity, but not token type or Ferrioli BM membership.
        "system_user_identity_state": "unknown",
        "app_binding_state": "verified" if row.app_id else "unknown",
        "permissions": permissions,
        "permissions_status": row.permissions_status,
        "permission_capabilities": _permission_capabilities(permissions, status=row.permissions_status),
        "permissions_error_code": row.permissions_error_code,
        "validated_at": row.validated_at.isoformat() if row.validated_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "expiry_status": row.expiry_status,
        "encryption_available": _encryption_available(),
        "expiry": "verified" if row.expires_at else "unknown",
    }


def _encryption_available() -> bool:
    from app.services.meta.settings import meta_settings
    key = str(meta_settings().credential_encryption_key or "")
    return len(key) >= 32


async def _stored_credential(db: AsyncSession, *, lock: bool = False) -> MetaSystemUserCredential | None:
    query = select(MetaSystemUserCredential).where(MetaSystemUserCredential.id == 1)
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


def _decrypt(row: MetaSystemUserCredential) -> str:
    try:
        return decrypt_meta_secret(row.encrypted_access_token)
    except MetaCredentialCryptoError as exc:
        raise HTTPException(status_code=503, detail="A chave de criptografia Meta está ausente ou inválida.") from exc


async def _audit(db: AsyncSession, actor: Usuario, action: str, *, empresa_id=None, details=None) -> None:
    db.add(MetaSystemUserAudit(
        id=uuid.uuid4(),
        actor_user_id=getattr(actor, "id", None),
        empresa_id=empresa_id,
        action=action,
        details_json=dict(details or {}),
    ))


def _graph_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MetaGraphError):
        # Never return Graph request URLs or raw request data. The error message
        # was sanitized by MetaGraphClient; the stable code is sufficient here.
        return HTTPException(status_code=400, detail={
            "code": exc.code,
            "message": str(exc)[:200],
            "http_status": exc.status_code,
            "meta_error_code": exc.meta_code,
            "error_subcode": exc.error_subcode,
        })
    return HTTPException(status_code=400, detail="Não foi possível validar a credencial Meta.")


@router.get("/credential")
async def get_system_user_credential(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    return _credential_status(await _stored_credential(db))


@router.post("/credential/validate")
async def validate_system_user_credential(
    payload: SystemUserTokenRequest,
    _: Usuario = Depends(require_super_admin),
):
    token = _request_token(payload)
    try:
        result = await validate_system_user_token(token)
    except Exception as exc:
        raise _graph_http_error(exc) from exc
    return {
        "valid": bool(result.get("system_user_id")),
        "system_user_id": result["system_user_id"],
        "system_user_name": result["system_user_name"],
        "system_user_identity_state": result.get("system_user_identity_state", "unknown"),
        "app_binding_state": result.get("app_binding_state", "unknown"),
        "app_id": result["app_id"],
        "app_name": result["app_name"],
        "permissions": result["granted_permissions"],
        "permissions_status": result["permissions_status"],
        "permission_capabilities": _permission_capabilities(
            result["granted_permissions"], status=result["permissions_status"]
        ),
        "validated_at": result["validated_at"].isoformat(),
        "expires_at": result["expires_at"].isoformat() if result["expires_at"] else None,
        "expiry_status": result["expiry_status"],
    }


@router.put("/credential")
async def save_system_user_credential(
    payload: SystemUserTokenRequest,
    db: AsyncSession = Depends(get_db),
    actor: Usuario = Depends(require_super_admin),
):
    token = _request_token(payload)
    if not _encryption_available():
        raise HTTPException(status_code=503, detail="A chave de criptografia Meta não está configurada; a credencial não foi alterada.")
    # Validation is deliberately completed before taking the row lock or
    # changing stored state, so a bad replacement can never destroy the old one.
    try:
        verified = await validate_system_user_token(token)
    except Exception as exc:
        raise _graph_http_error(exc) from exc
    try:
        encrypted = encrypt_meta_secret(token)
    except MetaCredentialCryptoError as exc:
        raise HTTPException(status_code=503, detail="A chave de criptografia Meta está indisponível; a credencial não foi alterada.") from exc
    row = await _stored_credential(db, lock=True)
    was_replacement = False
    if row is None:
        row = MetaSystemUserCredential(id=1, encrypted_access_token=encrypted,
                                       token_hint=mask_last4(token) or "***")
        db.add(row)
    else:
        # Saving the same value is a safe revalidation/update. It must not
        # invalidate every tenant's asset mapping as if the System User had
        # changed. Compare decrypted values in constant time and never return
        # either value to the caller.
        existing_token = _decrypt(row)
        was_replacement = not hmac.compare_digest(existing_token, token)
    row.encrypted_access_token = encrypted
    row.token_hint = mask_last4(token) or "***"
    row.system_user_id = verified["system_user_id"]
    row.system_user_name = verified["system_user_name"]
    row.app_id = verified["app_id"]
    row.app_name = verified["app_name"]
    row.granted_permissions = verified["granted_permissions"]
    row.permissions_status = verified["permissions_status"]
    row.permissions_error_code = verified["permissions_error_code"]
    row.validated_at = verified["validated_at"]
    row.expires_at = verified["expires_at"]
    row.expiry_status = verified["expiry_status"]
    row.updated_at = datetime.now(timezone.utc)
    row.updated_by_user_id = getattr(actor, "id", None)
    if was_replacement:
        # A replacement can have different asset grants. Invalidate previous
        # authorizations until the new credential rediscovers and revalidates
        # those assets; never silently carry old access into a new token.
        await db.execute(update(MetaPlatformAsset).values(access_state="stale_credential"))
        await db.execute(update(MetaTenantAssetLink).values(read_enabled=False, write_enabled=False))
        await db.execute(update(MetaMarketingTenantAccountGrant).values(read_enabled=False, write_enabled=False))
    await _audit(db, actor, "credential_saved", details={
        "system_user_id": row.system_user_id,
        "permission_count": len(row.granted_permissions or []),
        "replacement": was_replacement,
        "existing_asset_links_invalidated": was_replacement,
    })
    await db.commit()
    return {**_credential_status(row), "existing_asset_links_invalidated": was_replacement}


@router.delete("/credential")
async def revoke_system_user_credential(
    db: AsyncSession = Depends(get_db),
    actor: Usuario = Depends(require_super_admin),
):
    row = await _stored_credential(db, lock=True)
    if row is not None:
        # Keep link/asset history for audit but disable its authorization. This
        # also prevents a legacy process token from inheriting central grants.
        await db.delete(row)
        await db.execute(update(MetaPlatformAsset).values(access_state="credential_revoked"))
        await db.execute(update(MetaTenantAssetLink).values(read_enabled=False, write_enabled=False))
        await db.execute(update(MetaMarketingTenantAccountGrant).values(read_enabled=False, write_enabled=False))
        await _audit(db, actor, "credential_revoked", details={"tenant_assets_retained": True, "links_disabled": True})
        await db.commit()
    return {"configured": False, "revoked": True}


@router.post("/assets/discover")
async def discover_meta_assets(
    db: AsyncSession = Depends(get_db),
    actor: Usuario = Depends(require_super_admin),
):
    credential = await _stored_credential(db)
    if credential is None:
        raise HTTPException(status_code=409, detail="Cadastre e valide o Usuário do Sistema antes de atualizar os ativos.")
    token = _decrypt(credential)
    try:
        result = await discover_system_user_assets(token)
    except Exception as exc:
        raise _graph_http_error(exc) from exc
    now = datetime.now(timezone.utc)
    discovered_keys = {(data["asset_type"], data["external_id"]) for data in result["assets"]}
    for data in result["assets"]:
        row = await db.scalar(select(MetaPlatformAsset).where(
            MetaPlatformAsset.asset_type == data["asset_type"],
            MetaPlatformAsset.external_id == data["external_id"],
        ))
        if row is None:
            row = MetaPlatformAsset(id=uuid.uuid4(), asset_type=data["asset_type"], external_id=data["external_id"])
            db.add(row)
        row.name = data["name"]
        row.business_id = data["business_id"]
        row.business_name = data["business_name"]
        row.business_access_json = data.get("business_access", [])
        row.access_state = "verified"
        row.access_path = data["access_path"]
        row.permissions_json = data["permissions"]
        row.metadata_json = data["metadata"]
        row.discovered_at = now
        row.last_verified_at = now
    if result["complete"]:
        # Only a complete discovery can establish that a previously known
        # asset is no longer visible. Preserve the record and mapping for audit,
        # but prevent its use until an operator verifies and relinks it.
        existing_assets = (await db.scalars(select(MetaPlatformAsset))).all()
        for row in existing_assets:
            if (row.asset_type, row.external_id) in discovered_keys:
                continue
            row.access_state = "not_returned_by_complete_discovery"
            linked_rows = (await db.execute(
                select(MetaTenantAssetLink).where(MetaTenantAssetLink.asset_id == row.id)
            )).scalars().all()
            for link in linked_rows:
                link.read_enabled = False
                link.write_enabled = False
                if row.asset_type == "ad_account":
                    grant = await db.scalar(select(MetaMarketingTenantAccountGrant).where(
                        MetaMarketingTenantAccountGrant.empresa_id == link.empresa_id,
                        MetaMarketingTenantAccountGrant.ad_account_id == row.external_id,
                    ))
                    if grant is not None:
                        grant.read_enabled = False
                        grant.write_enabled = False
    await _audit(db, actor, "assets_discovered", details={
        "asset_count": len(result["assets"]),
        "business_count": result["business_count"],
        "partial": bool(result["errors"]),
        "error_count": len(result["errors"]),
    })
    await db.commit()
    return {
        "assets": len(result["assets"]),
        "businesses": result["business_count"],
        "complete": result["complete"],
        "errors": result["errors"],
    }


@router.post("/assets/validate-id")
async def validate_meta_asset_id(
    payload: AssetIdValidation,
    db: AsyncSession = Depends(get_db),
    actor: Usuario = Depends(require_super_admin),
):
    """Allow a bounded ID check when Graph does not enumerate an asset edge."""
    external_id = payload.external_id.strip()
    if payload.asset_type == "ad_account" and external_id.lower().startswith("act_"):
        external_id = external_id[4:]
    if not re.fullmatch(r"[0-9]{1,40}", external_id):
        raise HTTPException(status_code=422, detail="Informe um identificador Meta numérico válido.")
    business_id = str(payload.business_id or "").strip()
    if payload.asset_type != "business" and not re.fullmatch(r"[0-9]{1,40}", business_id):
        raise HTTPException(status_code=422, detail="Selecione o Business ID que dará contexto à validação do ativo.")
    credential = await _stored_credential(db)
    if credential is None:
        raise HTTPException(status_code=409, detail="Cadastre a credencial central antes de validar um ID de ativo.")
    token = _decrypt(credential)
    try:
        if payload.asset_type == "business":
            live = await revalidate_asset(token, "business", external_id)
            data = {
                "asset_type": "business", "external_id": str(live.get("id") or "").strip(),
                "name": str(live.get("name") or "").strip()[:240] or None,
                "business_id": str(live.get("id") or "").strip(),
                "business_name": str(live.get("name") or "").strip()[:240] or None,
                "access_path": "manual_id_validation", "permissions": [], "metadata": {},
            }
        else:
            data = await validate_asset_in_business(
                token, payload.asset_type, external_id, business_id,
            )
    except Exception as exc:
        raise _graph_http_error(exc) from exc
    row = await db.scalar(select(MetaPlatformAsset).where(
        MetaPlatformAsset.asset_type == data["asset_type"],
        MetaPlatformAsset.external_id == data["external_id"],
    ))
    if row is None:
        row = MetaPlatformAsset(
            id=uuid.uuid4(), asset_type=data["asset_type"], external_id=data["external_id"],
        )
        db.add(row)
    now = datetime.now(timezone.utc)
    row.name = data["name"]
    row.business_id = data["business_id"]
    row.business_name = data["business_name"]
    row.business_access_json = data.get("business_access", [])
    row.access_state = "verified"
    row.access_path = data["access_path"]
    row.permissions_json = data["permissions"]
    row.metadata_json = data["metadata"]
    row.discovered_at = now
    row.last_verified_at = now
    await _audit(db, actor, "asset_id_validated", details={
        "asset_type": data["asset_type"], "external_id": data["external_id"],
        "business_id": data["business_id"],
    })
    await db.commit()
    return {
        "validated": True, "asset": {
            "id": str(row.id), "type": row.asset_type, "external_id": row.external_id,
            "name": row.name, "business_id": row.business_id, "business_name": row.business_name,
            "business_access": list(row.business_access_json or []),
            "access_state": row.access_state, "access_path": row.access_path,
            "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        },
    }


@router.get("/assets")
async def list_discovered_meta_assets(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    rows = (await db.scalars(select(MetaPlatformAsset).order_by(MetaPlatformAsset.asset_type, MetaPlatformAsset.name))).all()
    links = (await db.scalars(select(MetaTenantAssetLink))).all()
    link_by_asset: dict[str, list[MetaTenantAssetLink]] = {}
    for link in links:
        link_by_asset.setdefault(str(link.asset_id), []).append(link)
    return {"assets": [{
        "id": str(row.id), "type": row.asset_type, "external_id": row.external_id,
        "name": row.name, "business_id": row.business_id, "business_name": row.business_name,
        "business_access": list(row.business_access_json or []),
        "access_state": row.access_state, "access_path": row.access_path,
        "permissions": _permission_rows(row.permissions_json),
        "metadata": dict(row.metadata_json or {}),
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "linked_empresa_id": str(link_by_asset[str(row.id)][0].empresa_id) if str(row.id) in link_by_asset else None,
        "linked_empresa_ids": sorted({str(link.empresa_id) for link in link_by_asset.get(str(row.id), [])}),
        "linked_read_enabled": link_by_asset[str(row.id)][0].read_enabled if str(row.id) in link_by_asset else None,
        "linked_write_enabled": link_by_asset[str(row.id)][0].write_enabled if str(row.id) in link_by_asset else None,
    } for row in rows]}


@router.get("/companies/{empresa_id}/assets")
async def get_company_meta_assets(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    company_id = _parse_company_id(empresa_id)
    rows = (await db.execute(
        select(MetaPlatformAsset, MetaTenantAssetLink)
        .join(MetaTenantAssetLink, MetaTenantAssetLink.asset_id == MetaPlatformAsset.id)
        .where(MetaTenantAssetLink.empresa_id == company_id)
        .order_by(MetaPlatformAsset.asset_type)
    )).all()
    return {"empresa_id": str(company_id), "assets": [{
        "id": str(asset.id), "type": asset.asset_type, "external_id": asset.external_id,
        "name": asset.name, "business_id": asset.business_id, "business_name": asset.business_name,
        "business_access": list(asset.business_access_json or []),
        "access_state": asset.access_state, "permissions": _permission_rows(asset.permissions_json),
        "read_enabled": link.read_enabled, "write_enabled": link.write_enabled,
        "verified_at": link.verified_at.isoformat(),
    } for asset, link in rows]}


@router.post("/companies/{empresa_id}/datasets/crm/validate")
async def validate_company_crm_dataset_readiness(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    actor: Usuario = Depends(require_super_admin),
):
    """Verify read access to the tenant-linked CRM Dataset without CAPI writes."""
    company_id = _parse_company_id(empresa_id)
    if await db.get(Empresa, company_id) is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    rows = (await db.execute(
        select(MetaPlatformAsset, MetaTenantAssetLink)
        .join(MetaTenantAssetLink, MetaTenantAssetLink.asset_id == MetaPlatformAsset.id)
        .where(
            MetaTenantAssetLink.empresa_id == company_id,
            MetaTenantAssetLink.read_enabled.is_(True),
            MetaPlatformAsset.access_state == "verified",
            MetaPlatformAsset.asset_type.in_(("business", "dataset")),
        )
    )).all()
    datasets = [(asset, link) for asset, link in rows if asset.asset_type == "dataset"]
    businesses = [(asset, link) for asset, link in rows if asset.asset_type == "business"]
    if len(datasets) != 1 or len(businesses) != 1:
        raise HTTPException(status_code=409, detail="Vincule um Dataset e um portfólio empresarial verificados a esta empresa antes da validação.")
    dataset, dataset_link = datasets[0]
    business, business_link = businesses[0]
    if not dataset_link.read_enabled or not business_link.read_enabled:
        raise HTTPException(status_code=409, detail="A leitura dos vínculos Dataset/Business está desativada.")
    access_businesses = {
        str(item.get("business_id") or "").strip()
        for item in (dataset.business_access_json or [])
        if isinstance(item, dict)
    }
    if business.external_id not in access_businesses:
        raise HTTPException(status_code=409, detail="O Dataset não possui evidência de acesso pelo portfólio vinculado a esta empresa.")

    credential = await _stored_credential(db)
    if credential is None or not credential.validated_at:
        raise HTTPException(status_code=409, detail="Cadastre e valide a credencial central antes de consultar o Dataset.")
    token = _decrypt(credential)
    try:
        live = await read_crm_dataset_metadata(token, dataset.external_id)
    except Exception as exc:
        raise _graph_http_error(exc) from exc
    if live["is_crm"] is False:
        crm_state = "not_applicable"
    elif live["is_crm"] is True and live["is_unavailable"] is False:
        crm_state = "observed"
    elif live["is_unavailable"] is True:
        crm_state = "partial"
    else:
        crm_state = "unknown"

    dataset.name = live["name"] or dataset.name
    metadata = dict(dataset.metadata_json or {})
    metadata.update({"is_crm": live["is_crm"], "is_unavailable": live["is_unavailable"]})
    dataset.metadata_json = metadata
    dataset.last_verified_at = datetime.now(timezone.utc)
    await _audit(db, actor, "asset_id_validated", empresa_id=company_id, details={
        "validation_purpose": "crm_dataset_read_only",
        "asset_type": "dataset",
        "asset_id": dataset.external_id,
        "business_id": business.external_id,
        "crm_state": crm_state,
        "capi_delivery_state": "unknown",
        "live_sender_enabled": False,
    })
    await db.commit()
    return {
        "empresa_id": str(company_id),
        "asset_read_state": "observed",
        "crm_dataset_state": crm_state,
        "dataset": {"id": dataset.external_id, "name": live["name"] or dataset.name},
        "is_crm": live["is_crm"],
        "is_unavailable": live["is_unavailable"],
        "system_user_permissions_state": credential.permissions_status,
        "system_user_permissions": _permission_rows(credential.granted_permissions),
        "capi_delivery_state": "unknown",
        "capi_delivery_explanation": "A leitura do Dataset não comprova o contrato de envio nem a aceitação/otimização de eventos. O sender CAPI permanece sob a Issue #33.",
        "live_sender_enabled": False,
        "external_write_performed": False,
    }


@router.delete("/companies/{empresa_id}/assets")
async def unlink_company_meta_assets(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    actor: Usuario = Depends(require_super_admin),
):
    company_id = _parse_company_id(empresa_id)
    links = (await db.execute(
        select(MetaTenantAssetLink, MetaPlatformAsset)
        .join(MetaPlatformAsset, MetaPlatformAsset.id == MetaTenantAssetLink.asset_id)
        .where(MetaTenantAssetLink.empresa_id == company_id)
        .with_for_update()
    )).all()
    config = await db.scalar(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == company_id).with_for_update())
    by_type = {asset.asset_type: asset.external_id for asset, _ in links}
    if config is not None:
        for asset_type, field in (
            ("business", "business_id"), ("ad_account", "ad_account_id"),
            ("page", "page_id"), ("instagram_account", "instagram_account_id"),
        ):
            if getattr(config, field, None) == by_type.get(asset_type):
                setattr(config, field, None)
    old_grant = await db.scalar(select(MetaMarketingTenantAccountGrant).where(MetaMarketingTenantAccountGrant.empresa_id == company_id))
    if old_grant is not None and old_grant.ad_account_id == by_type.get("ad_account"):
        await db.delete(old_grant)
        await db.flush()
    for _, link in links:
        await db.delete(link)
    await _audit(db, actor, "tenant_assets_linked", empresa_id=company_id,
                 details={"asset_count": 0, "unlinked": True, "asset_types": sorted({asset.asset_type for asset, _ in links})})
    await db.commit()
    return {"empresa_id": str(company_id), "assets": [], "unlinked": True}


@router.put("/companies/{empresa_id}/assets")
async def link_company_meta_assets(
    empresa_id: str,
    payload: TenantAssetLinksRequest,
    db: AsyncSession = Depends(get_db),
    actor: Usuario = Depends(require_super_admin),
):
    company_id = _parse_company_id(empresa_id)
    company = await db.get(Empresa, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    if len({item.asset_id for item in payload.assets}) != len(payload.assets):
        raise HTTPException(status_code=422, detail="A seleção contém ativos duplicados.")
    if not payload.assets:
        raise HTTPException(status_code=422, detail="Use a ação explícita de desvincular para remover os ativos da empresa.")
    types = []
    assets: list[MetaPlatformAsset] = []
    for selection in payload.assets:
        asset = await db.get(MetaPlatformAsset, selection.asset_id)
        if asset is None or asset.access_state != "verified":
            raise HTTPException(status_code=409, detail="Um ativo não está confirmado no catálogo. Atualize os ativos antes de vinculá-los.")
        if asset.asset_type in types:
            raise HTTPException(status_code=422, detail=f"Selecione no máximo um ativo do tipo {asset.asset_type} para esta empresa.")
        types.append(asset.asset_type)
        if selection.write_enabled and asset.asset_type != "ad_account":
            raise HTTPException(status_code=422, detail="A permissão de escrita desta entrega aplica-se somente à conta de anúncios.")
        assets.append(asset)
    business_assets = [asset for asset in assets if asset.asset_type == "business"]
    non_business = [asset for asset in assets if asset.asset_type != "business"]
    if non_business and len(business_assets) != 1:
        raise HTTPException(status_code=422, detail="Vincule exatamente um portfólio empresarial antes dos outros ativos.")
    business_id = business_assets[0].external_id if business_assets else None
    page_asset = next((asset for asset in assets if asset.asset_type == "page"), None)
    instagram_asset = next((asset for asset in assets if asset.asset_type == "instagram_account"), None)
    if page_asset and instagram_asset:
        associated_instagram = str((page_asset.metadata_json or {}).get("instagram_account_id") or "").strip()
        if associated_instagram and associated_instagram != instagram_asset.external_id:
            raise HTTPException(status_code=409, detail="A conta Instagram selecionada não corresponde à Página escolhida.")

    credential = await _stored_credential(db)
    if payload.assets and credential is None:
        raise HTTPException(status_code=409, detail="A credencial central precisa estar configurada para validar os ativos.")
    token = _decrypt(credential) if credential else None
    verified_at = datetime.now(timezone.utc)
    for asset in assets:
        other_link = await db.scalar(select(MetaTenantAssetLink).where(
            MetaTenantAssetLink.asset_id == asset.id,
            MetaTenantAssetLink.empresa_id != company_id,
        )) if asset.asset_type != "business" else None
        if other_link is not None:
            raise HTTPException(status_code=409, detail="Este ativo já está vinculado a outra empresa.")
        try:
            live = (
                await revalidate_asset(token or "", asset.asset_type, asset.external_id)
                if asset.asset_type == "business"
                else await validate_asset_in_business(token or "", asset.asset_type, asset.external_id, business_id or "")
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail={"code": getattr(exc, "code", "asset_access_unverified"),
                                                         "message": "A Meta não confirmou o acesso atual a um dos ativos; nenhum vínculo foi salvo."}) from exc
        if live.get("external_id"):
            returned_id = str(live.get("external_id") or "").strip()
        elif asset.asset_type == "ad_account":
            returned_id = str(live.get("account_id") or live.get("id") or "").strip()
        elif asset.asset_type == "dataset":
            returned_id = str(live.get("dataset_id") or live.get("id") or "").strip()
        else:
            returned_id = str(live.get("id") or "").strip()
        if returned_id.startswith("act_"):
            returned_id = returned_id[4:]
        if returned_id and returned_id != asset.external_id:
            raise HTTPException(status_code=409, detail="A Meta retornou um identificador diferente; o vínculo não foi salvo.")
        if asset.asset_type != "business":
            contexts = list(asset.business_access_json or [])
            for context in live.get("business_access") or []:
                if context not in contexts:
                    contexts.append(context)
            asset.business_access_json = contexts
        asset.last_verified_at = verified_at

    granted_permissions = set(_permission_rows(credential.granted_permissions)) if credential else set()
    if any(item.write_enabled for item in payload.assets) and "ads_management" not in granted_permissions:
        raise HTTPException(status_code=403, detail="A Meta não confirmou ads_management; a escrita permanece bloqueada.")
    if any(asset.asset_type == "ad_account" for asset in assets) and not supports_ads_read(granted_permissions):
        raise HTTPException(status_code=403, detail="A Meta não confirmou ads_read ou ads_management para a conta de anúncios.")

    config = await db.scalar(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == company_id).with_for_update())
    if config is None:
        config = TrafficMetaConfig(empresa_id=company_id)
        db.add(config)
    ad_account = next((asset for asset in assets if asset.asset_type == "ad_account"), None)
    if ad_account:
        conflicting_config = await db.scalar(select(TrafficMetaConfig).where(
            TrafficMetaConfig.ad_account_id == ad_account.external_id,
            TrafficMetaConfig.empresa_id != company_id,
        ))
        if conflicting_config is not None:
            raise HTTPException(status_code=409, detail="A conta de anúncios já está configurada para outra empresa.")

    old_grant = await db.scalar(select(MetaMarketingTenantAccountGrant).where(MetaMarketingTenantAccountGrant.empresa_id == company_id))
    if old_grant is not None:
        await db.delete(old_grant)
    await db.execute(delete(MetaTenantAssetLink).where(MetaTenantAssetLink.empresa_id == company_id))
    selections = {item.asset_id: item for item in payload.assets}
    for asset in assets:
        selection = selections[asset.id]
        db.add(MetaTenantAssetLink(
            id=uuid.uuid4(), empresa_id=company_id, asset_id=asset.id,
            asset_type=asset.asset_type,
            read_enabled=True, write_enabled=selection.write_enabled,
            verified_at=verified_at, linked_by_user_id=getattr(actor, "id", None),
        ))
    config.business_id = business_id
    config.ad_account_id = ad_account.external_id if ad_account else None
    config.page_id = next((asset.external_id for asset in assets if asset.asset_type == "page"), None)
    config.instagram_account_id = next((asset.external_id for asset in assets if asset.asset_type == "instagram_account"), None)
    # Do not copy a CRM Ads Dataset into TrafficMetaConfig.dataset_id: that
    # column remains the WhatsApp Business Messaging Dataset mirror.
    if ad_account:
        method = "delegated_client_ad_account" if ad_account.access_path == "business_client" else "business_owner"
        db.add(MetaMarketingTenantAccountGrant(
            id=uuid.uuid4(), empresa_id=company_id, business_id=business_id or "",
            ad_account_id=ad_account.external_id, credential_source="ADMIN_MANAGED_MARKETING_TOKEN",
            read_enabled=True,
            write_enabled=selections[ad_account.id].write_enabled,
            verification_method=method,
            verified_at=verified_at,
        ))
    await _audit(db, actor, "tenant_assets_linked", empresa_id=company_id, details={
        "asset_types": sorted(types), "asset_count": len(assets),
        "ad_account_write_enabled": bool(ad_account and selections[ad_account.id].write_enabled),
    })
    await db.commit()
    return await get_company_meta_assets(str(company_id), db, actor)


def _parse_company_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Identificador de empresa inválido.") from exc
