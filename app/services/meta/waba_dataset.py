"""Tenant-scoped WhatsApp Business Account Dataset API helper.

The Meta Dataset API intentionally uses one idempotent POST to the WABA
``/{WABA_ID}/dataset`` edge.  It never probes an unsupported dataset edge and
never falls back to a phone number or business portfolio identifier.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
import uuid
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.secret_sanitizer import sanitize_for_log
from app.services.meta.conversion_credentials import CONVERSIONS_PURPOSE, ResolvedMetaCredential
from db.models import Conexao, Empresa, TipoConexao, TrafficMetaConfig


class GraphTransport(Protocol):
    async def get(self, url: str, *, headers: dict[str, str], params: dict[str, str]) -> Any: ...
    async def post(self, url: str, *, headers: dict[str, str]) -> Any: ...


class DatasetApiError(ValueError):
    def __init__(self, *, status_code: int | None, error_type: str | None, code: int | None, subcode: int | None, message: str, fbtrace_id: str | None) -> None:
        self.status_code = status_code
        self.error_type = error_type
        self.meta_code = code
        self.subcode = subcode
        self.fbtrace_id = fbtrace_id
        super().__init__(message)

    @classmethod
    def from_response(cls, response: Any) -> "DatasetApiError":
        try:
            body = response.json()
        except Exception:
            body = {}
        error = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
        return cls(
            status_code=int(getattr(response, "status_code", 0) or 0) or None,
            error_type=str(error.get("type") or "") or None,
            code=error.get("code"),
            subcode=error.get("error_subcode"),
            message=str(sanitize_for_log(str(error.get("message") or "Meta Dataset API error")))[:240],
            fbtrace_id=str(error.get("fbtrace_id") or "")[:80] or None,
        )


DATASET_GENERIC_NAMES = {"", "unknown event data"}
DATASET_NAME_SUFFIX = "EVENT DATA"
DATASET_NAME_MAX_LENGTH = 80


def event_source_company_slug(name: str | None, *, max_length: int = 36) -> str:
    normalized = unicodedata.normalize("NFKD", str(name or "EMPRESA"))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text.upper()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug) or "EMPRESA"
    return slug[:max_length].strip("-") or "EMPRESA"


def proposed_business_messaging_dataset_name(*, empresa_id: Any, empresa_name: str | None) -> str:
    short_id = re.sub(r"[^A-Fa-f0-9]", "", str(empresa_id or ""))[:8].upper() or "UNKNOWN"
    prefix = "AOS | "
    middle = f" | {short_id} | {DATASET_NAME_SUFFIX}"
    available_slug_length = max(8, DATASET_NAME_MAX_LENGTH - len(prefix) - len(middle))
    slug = event_source_company_slug(empresa_name, max_length=available_slug_length)
    return f"{prefix}{slug}{middle}"[:DATASET_NAME_MAX_LENGTH]


def is_generic_dataset_name(name: str | None) -> bool:
    return str(name or "").strip().lower() in DATASET_GENERIC_NAMES


@dataclass(frozen=True)
class DatasetResolution:
    dataset_id: str
    status_code: int
    fbtrace_id: str | None = None


@dataclass(frozen=True)
class BusinessMessagingDatasetResolution:
    empresa_id: str
    connection_id: str
    waba_id: str
    dataset_id: str
    display_name: str
    created: bool
    status_code: int | None = None
    fbtrace_id: str | None = None
    traffic_meta_mirrored: bool = False


class WabaDatasetService:
    def __init__(self, credential: ResolvedMetaCredential, *, transport: GraphTransport | None = None) -> None:
        if credential.purpose != CONVERSIONS_PURPOSE:
            raise ValueError("dataset_requires_conversions_credential")
        if not str(credential.waba_id or "").strip() or not str(credential.access_token or "").strip():
            raise ValueError("dataset_waba_or_credential_missing")
        self.credential = credential
        self.transport = transport

    @property
    def _base_url(self) -> str:
        return f"https://graph.facebook.com/{self.credential.graph_version}/{self.credential.waba_id}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.credential.access_token}"}

    @staticmethod
    def _body(response: Any) -> dict[str, Any]:
        try:
            body = response.json()
        except Exception:
            body = {}
        return body if isinstance(body, dict) else {}

    async def validate_waba(self) -> int:
        """One read-only WABA validation, deliberately independent from POST."""
        response = await self._get("", params={"fields": "id,name"})
        if not 200 <= int(response.status_code) < 300:
            raise DatasetApiError.from_response(response)
        return int(response.status_code)

    async def get_or_create_dataset(self) -> DatasetResolution:
        """Use the documented idempotent WABA POST; no GET dataset precondition."""
        response = await self._post("/dataset")
        if not 200 <= int(response.status_code) < 300:
            raise DatasetApiError.from_response(response)
        body = self._body(response)
        dataset_id = str(body.get("id") or "").strip()
        if not dataset_id:
            raise DatasetApiError(
                status_code=int(response.status_code), error_type="invalid_response", code=None,
                subcode=None, message="Meta Dataset API response missing dataset ID", fbtrace_id=str(body.get("fbtrace_id") or "")[:80] or None,
            )
        return DatasetResolution(dataset_id=dataset_id, status_code=int(response.status_code), fbtrace_id=str(body.get("fbtrace_id") or "")[:80] or None)

    async def _get(self, suffix: str, *, params: dict[str, str]) -> Any:
        url = f"{self._base_url}{suffix}"
        if self.transport is not None:
            return await self.transport.get(url, headers=self._headers, params=params)
        async with httpx.AsyncClient(timeout=20.0) as client:
            return await client.get(url, headers=self._headers, params=params)

    async def _post(self, suffix: str) -> Any:
        url = f"{self._base_url}{suffix}"
        if self.transport is not None:
            return await self.transport.post(url, headers=self._headers)
        async with httpx.AsyncClient(timeout=20.0) as client:
            return await client.post(url, headers=self._headers)


def _connection_waba_id(connection: Conexao) -> str:
    metadata = dict(getattr(connection, "metadata_json", None) or {})
    return str(getattr(connection, "waba_id", "") or metadata.get("meta_waba_id") or "").strip()


def _connection_dataset_id(connection: Conexao) -> str:
    metadata = dict(getattr(connection, "metadata_json", None) or {})
    return str(metadata.get("dataset_id") or metadata.get("meta_dataset_id") or "").strip()


def _set_connection_dataset(connection: Conexao, dataset_id: str, display_name: str) -> bool:
    metadata = dict(getattr(connection, "metadata_json", None) or {})
    changed = False
    if str(metadata.get("dataset_id") or "").strip() != dataset_id:
        metadata["dataset_id"] = dataset_id
        changed = True
    if display_name and str(metadata.get("dataset_name") or "").strip() != display_name:
        metadata["dataset_name"] = display_name
        changed = True
    if changed:
        connection.metadata_json = metadata
        flag_modified(connection, "metadata_json")
    return changed


def _sync_traffic_meta_dataset_mirror(
    config: TrafficMetaConfig | None,
    connection: Conexao,
    dataset_id: str,
) -> bool:
    """Keep the UI/config mirror aligned to the tenant's official connection.

    The Traffic Meta row is never a credential or WABA authority. Its binding
    fields are copied from the already tenant-scoped official connection.
    """
    if not config:
        return False
    values = {
        "dataset_id": dataset_id,
        "waba_id": _connection_waba_id(connection),
        "phone_number_id": str(getattr(connection, "phone_number_id", "") or "").strip() or None,
        "whatsapp_connection_id": connection.id,
    }
    changed = False
    for key, value in values.items():
        if getattr(config, key, None) != value:
            setattr(config, key, value)
            changed = True
    return changed


async def _official_connection_for_update(session: AsyncSession, empresa_id: Any) -> Conexao:
    result = await session.execute(
        select(Conexao)
        .where(
            Conexao.empresa_id == empresa_id,
            Conexao.tipo == TipoConexao.META,
            Conexao.provider == "meta_official",
        )
        .order_by(Conexao.criado_em.asc(), Conexao.id.asc())
        .with_for_update()
    )
    connection = result.scalars().first()
    if not connection:
        raise ValueError("official_whatsapp_connection_missing")
    return connection


async def _traffic_config_for_update(session: AsyncSession, empresa_id: Any) -> TrafficMetaConfig | None:
    result = await session.execute(
        select(TrafficMetaConfig)
        .where(TrafficMetaConfig.empresa_id == empresa_id)
        .with_for_update()
    )
    return result.scalars().first()


async def _empresa_name(session: AsyncSession, empresa_id: Any) -> str:
    result = await session.execute(select(Empresa.nome_empresa).where(Empresa.id == empresa_id))
    return str(result.scalar_one_or_none() or "Empresa").strip() or "Empresa"


async def ensure_business_messaging_dataset(
    session: AsyncSession,
    empresa_id: Any,
    *,
    transport: GraphTransport | None = None,
) -> BusinessMessagingDatasetResolution:
    """Provision the tenant WABA Dataset only when the source of truth is empty.

    Source of truth: ``Conexao.metadata_json.dataset_id``.
    Mirror: ``TrafficMetaConfig.dataset_id``.  Divergence is corrected from the
    connection metadata, never the opposite direction.
    """
    connection = await _official_connection_for_update(session, empresa_id)
    config = await _traffic_config_for_update(session, empresa_id)
    name = await _empresa_name(session, empresa_id)
    display_name = proposed_business_messaging_dataset_name(empresa_id=empresa_id, empresa_name=name)
    waba_id = _connection_waba_id(connection)
    if not waba_id:
        raise ValueError("official_waba_missing")

    existing_dataset_id = _connection_dataset_id(connection)
    if existing_dataset_id:
        changed = _set_connection_dataset(connection, existing_dataset_id, display_name)
        mirrored = _sync_traffic_meta_dataset_mirror(config, connection, existing_dataset_id)
        if changed or mirrored:
            await session.commit()
        return BusinessMessagingDatasetResolution(
            empresa_id=str(empresa_id),
            connection_id=str(connection.id),
            waba_id=waba_id,
            dataset_id=existing_dataset_id,
            display_name=display_name,
            created=False,
            traffic_meta_mirrored=bool(config and str(getattr(config, "dataset_id", "") or "").strip() == existing_dataset_id),
        )

    from app.services.meta.business_messaging_conversions import resolve_meta_conversion_credential

    credential = await resolve_meta_conversion_credential(session, empresa_id, require_dataset=False)
    if str(credential.waba_id or "").strip() != waba_id:
        raise ValueError("dataset_waba_mapping_changed")
    created = await WabaDatasetService(credential, transport=transport).get_or_create_dataset()
    dataset_id = str(created.dataset_id or "").strip()
    if not dataset_id:
        raise ValueError("dataset_id_missing")

    _set_connection_dataset(connection, dataset_id, display_name)
    _sync_traffic_meta_dataset_mirror(config, connection, dataset_id)
    await session.commit()
    return BusinessMessagingDatasetResolution(
        empresa_id=str(empresa_id),
        connection_id=str(connection.id),
        waba_id=waba_id,
        dataset_id=dataset_id,
        display_name=display_name,
        created=True,
        status_code=created.status_code,
        fbtrace_id=created.fbtrace_id,
        traffic_meta_mirrored=bool(config and str(getattr(config, "dataset_id", "") or "").strip() == dataset_id),
    )


async def persist_dataset_for_tenant(
    session: Any,
    empresa_id: Any,
    *,
    expected_waba_id: str,
    dataset_id: str,
    dataset_name: str | None = None,
    graph_verified_waba_id: str | None = None,
) -> None:
    """Persist a Dataset only after its membership was confirmed by Graph GET.

    `graph_verified_waba_id` is supplied only by the read-only verification
    flow below. The source of truth remains the connection metadata; the
    Traffic Meta row is synchronized from that connection in the same commit.
    """
    clean_waba_id = str(expected_waba_id or "").strip()
    clean_dataset_id = str(dataset_id or "").strip()
    if not clean_waba_id or not clean_dataset_id or str(graph_verified_waba_id or "").strip() != clean_waba_id:
        raise ValueError("dataset_graph_verification_required")
    connection = await _official_connection_for_update(session, empresa_id)
    if _connection_waba_id(connection) != clean_waba_id:
        raise ValueError("dataset_waba_mapping_changed")
    config = await _traffic_config_for_update(session, empresa_id)
    metadata = dict(connection.metadata_json or {})
    existing_dataset = _connection_dataset_id(connection)
    if existing_dataset and existing_dataset != clean_dataset_id:
        raise ValueError("dataset_mapping_conflict")
    mirrored_dataset = str(getattr(config, "dataset_id", "") or "").strip() if config else ""
    if not existing_dataset and mirrored_dataset and mirrored_dataset != clean_dataset_id:
        raise ValueError("dataset_candidate_changed")
    metadata["dataset_id"] = clean_dataset_id
    if str(dataset_name or "").strip():
        metadata["dataset_name"] = str(dataset_name).strip()
    connection.metadata_json = metadata
    flag_modified(connection, "metadata_json")
    _sync_traffic_meta_dataset_mirror(config, connection, clean_dataset_id)
    await session.commit()


def _mask_meta_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return f"***{raw[-4:]}" if raw else None


async def verify_existing_business_messaging_dataset(
    session: AsyncSession,
    empresa_id: Any,
    *,
    transport: GraphTransport | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    """Verify the current tenant WABA, phone, credential and Dataset using GETs.

    Recovery may use a Dataset value from this same tenant's Traffic Meta
    mirror only as a candidate. It is never trusted until the current official
    WABA's Dataset edge returns that exact ID. No Graph write is reachable.
    """
    from app.services.meta.business_messaging_conversions import (
        REQUIRED_CONVERSION_SCOPES,
        _debug_token,
        _graph_get,
        _scope_set,
        official_whatsapp_connection,
        resolve_meta_conversion_credential,
    )
    from app.services.meta.conversion_credentials import (
        CONVERSIONS_PURPOSE,
        MetaCredentialResolutionError,
    )

    try:
        tenant_uuid = uuid.UUID(str(empresa_id))
    except (TypeError, ValueError, AttributeError):
        return {"ready": False, "reason": "TENANT_ID_INVALID", "persisted": False, "graph_write_executed": False}
    tenant_id = str(tenant_uuid)
    try:
        connection = await official_whatsapp_connection(session, tenant_uuid)
    except MetaCredentialResolutionError as exc:
        return {"ready": False, "reason": str(exc), "persisted": False, "graph_write_executed": False}

    config = await session.scalar(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == tenant_uuid))
    metadata = dict(connection.metadata_json or {})
    waba_id = _connection_waba_id(connection)
    phone_id = str(connection.phone_number_id or metadata.get("meta_phone_number_id") or "").strip()
    business_id = str(metadata.get("meta_business_portfolio_id") or metadata.get("business_id") or "").strip()
    connection_dataset = _connection_dataset_id(connection)
    mirror_dataset = str(getattr(config, "dataset_id", "") or "").strip() if config else ""
    mirror_waba = str(getattr(config, "waba_id", "") or "").strip() if config else ""

    if not waba_id:
        return {"ready": False, "reason": "BLOCKED_WABA_NOT_LINKED", "persisted": False, "graph_write_executed": False}
    if not phone_id:
        return {"ready": False, "reason": "PHONE_NUMBER_ID_NOT_LINKED", "persisted": False, "graph_write_executed": False}

    try:
        credential = await resolve_meta_conversion_credential(session, tenant_uuid, require_dataset=False)
    except MetaCredentialResolutionError as exc:
        return {"ready": False, "reason": str(exc), "persisted": False, "graph_write_executed": False}
    if (
        credential.tenant_id != tenant_id
        or credential.purpose != CONVERSIONS_PURPOSE
        or credential.connection_id != str(connection.id)
        or credential.waba_id != waba_id
    ):
        return {"ready": False, "reason": "CREDENTIAL_TENANT_OR_WABA_MISMATCH", "persisted": False, "graph_write_executed": False}

    debug = await _debug_token(credential.access_token, transport=transport)
    debug_data = debug.get("data") if isinstance(debug.get("data"), dict) else {}
    scopes = _scope_set(debug)
    token_valid = bool(debug.get("available") and debug_data.get("is_valid") is True)
    scopes_valid = REQUIRED_CONVERSION_SCOPES.issubset(scopes)
    if not token_valid:
        return {
            "ready": False, "reason": "CREDENTIAL_INVALID_OR_UNVERIFIABLE", "persisted": False,
            "credential_source": credential.credential_source,
            "required_scopes_present": scopes_valid,
            "graph_write_executed": False,
        }
    if not scopes_valid:
        return {
            "ready": False, "reason": "BUSINESS_MESSAGING_SCOPES_MISSING", "persisted": False,
            "credential_source": credential.credential_source,
            "required_scopes_present": False,
            "graph_write_executed": False,
        }
    if not business_id or (credential.business_id and credential.business_id != business_id):
        return {
            "ready": False, "reason": "BUSINESS_IDENTITY_NOT_TENANT_BOUND", "persisted": False,
            "credential_source": credential.credential_source,
            "required_scopes_present": True,
            "graph_write_executed": False,
        }

    waba_status, waba_body = await _graph_get(
        credential.access_token,
        waba_id,
        params={"fields": "id,name,owner_business_info"},
        transport=transport,
        graph_version=credential.graph_version,
    )
    owner = waba_body.get("owner_business_info") if isinstance(waba_body.get("owner_business_info"), dict) else {}
    waba_valid = 200 <= waba_status < 300 and str(waba_body.get("id") or "") == waba_id
    owner_valid = str(owner.get("id") or "") == business_id
    if not waba_valid or not owner_valid:
        return {
            "ready": False, "reason": "WABA_BUSINESS_BINDING_UNVERIFIED", "persisted": False,
            "credential_source": credential.credential_source, "required_scopes_present": True,
            "waba_http_status": waba_status, "waba_verified": waba_valid,
            "owner_business_verified": owner_valid, "graph_write_executed": False,
        }

    phone_status, phone_body = await _graph_get(
        credential.access_token,
        f"{waba_id}/phone_numbers",
        params={"fields": "id", "limit": "100"},
        transport=transport,
        graph_version=credential.graph_version,
    )
    phone_rows = phone_body.get("data") if isinstance(phone_body.get("data"), list) else []
    phone_valid = 200 <= phone_status < 300 and any(
        isinstance(item, dict) and str(item.get("id") or "") == phone_id for item in phone_rows
    )
    if not phone_valid:
        return {
            "ready": False, "reason": "PHONE_NUMBER_NOT_BOUND_TO_WABA", "persisted": False,
            "credential_source": credential.credential_source, "required_scopes_present": True,
            "waba_verified": True, "owner_business_verified": True,
            "phone_http_status": phone_status, "phone_verified": False,
            "graph_write_executed": False,
        }

    dataset_status, dataset_body = await _graph_get(
        credential.access_token,
        f"{waba_id}/dataset",
        params={"fields": "id,name", "limit": "100"},
        transport=transport,
        graph_version=credential.graph_version,
    )
    dataset_rows = dataset_body.get("data") if isinstance(dataset_body.get("data"), list) else []
    if not 200 <= dataset_status < 300:
        return {
            "ready": False, "reason": "WABA_DATASET_EDGE_UNREADABLE", "persisted": False,
            "credential_source": credential.credential_source, "required_scopes_present": True,
            "waba_verified": True, "owner_business_verified": True, "phone_verified": True,
            "dataset_http_status": dataset_status, "graph_write_executed": False,
        }

    local_candidates = [value for value in (connection_dataset, mirror_dataset) if value]
    selected_id = connection_dataset or mirror_dataset
    candidate_source = "connection_metadata" if connection_dataset else ("traffic_meta_mirror" if mirror_dataset else None)
    if not selected_id:
        unique_ids = sorted({str(item.get("id") or "").strip() for item in dataset_rows if isinstance(item, dict) and item.get("id")})
        if len(unique_ids) == 1:
            selected_id = unique_ids[0]
            candidate_source = "unique_current_waba_dataset_edge"
    elif selected_id not in local_candidates:
        selected_id = ""
    selected_row = next((item for item in dataset_rows if isinstance(item, dict) and str(item.get("id") or "") == selected_id), None)
    if not selected_id or selected_row is None:
        return {
            "ready": False,
            "reason": "DATASET_NOT_VERIFIED_FOR_CURRENT_WABA" if selected_id else "DATASET_CANDIDATE_AMBIGUOUS_OR_MISSING",
            "persisted": False, "credential_source": credential.credential_source,
            "required_scopes_present": True, "waba_verified": True,
            "owner_business_verified": True, "phone_verified": True,
            "dataset_http_status": dataset_status, "dataset_rows": len(dataset_rows),
            "candidate_dataset_suffix": _mask_meta_id(connection_dataset or mirror_dataset),
            "mirror_waba_mismatch": bool(mirror_waba and mirror_waba != waba_id),
            "graph_write_executed": False,
        }

    dataset_name = str(selected_row.get("name") or "").strip()
    persisted = False
    if persist:
        await persist_dataset_for_tenant(
            session,
            tenant_uuid,
            expected_waba_id=waba_id,
            dataset_id=selected_id,
            dataset_name=dataset_name,
            graph_verified_waba_id=waba_id,
        )
        persisted = True
    return {
        "ready": True,
        "reason": None,
        "tenant_suffix": tenant_id[-4:],
        "connection_suffix": str(connection.id)[-4:],
        "credential_source": credential.credential_source,
        "credential_valid": token_valid,
        "required_scopes_present": scopes_valid,
        "business_suffix": _mask_meta_id(business_id),
        "waba_suffix": _mask_meta_id(waba_id),
        "owner_business_verified": owner_valid,
        "phone_suffix": _mask_meta_id(phone_id),
        "phone_verified": phone_valid,
        "dataset_suffix": _mask_meta_id(selected_id),
        "dataset_name": dataset_name,
        "dataset_verified_by_waba_edge": True,
        "candidate_source": candidate_source,
        "mirror_waba_mismatch": bool(mirror_waba and mirror_waba != waba_id),
        "persisted": persisted,
        "graph_write_executed": False,
    }
