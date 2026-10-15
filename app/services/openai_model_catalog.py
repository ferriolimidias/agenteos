"""Tenant-scoped OpenAI model discovery and explicit Chat Completions checks."""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_factory import get_tenant_api_key
from app.core.model_catalog import is_allowed_model_id
from db.models import Empresa, OpenAIModelCatalogSnapshot, OpenAIModelValidation

SPECIALIZED_MARKERS = (
    "embedding", "dall-e", "image", "sora", "whisper", "transcribe", "tts", "audio",
    "realtime", "moderation", "computer-use", "video", "search",
)
KNOWN_CHAT_MODELS = {
    "gpt-4o", "gpt-4o-mini", "gpt-4o-2024-05-13", "gpt-4o-2024-08-06", "gpt-4o-mini-2024-07-18",
    "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.6-luna", "o1", "o1-mini", "o3", "o3-mini", "o4-mini",
}


def credential_fingerprint(api_key: str) -> str:
    # Keys are high-entropy secrets. Only this one-way digest enters the cache.
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


async def _project_context(db: AsyncSession, tenant_id, api_key: str | None = None):
    company = await db.scalar(select(Empresa).where(Empresa.id == tenant_id))
    credentials = getattr(company, "credenciais_canais", None) or {}
    project_id = ""
    if isinstance(credentials, dict):
        project_id = str(credentials.get("openai_project_id") or credentials.get("project_id") or "").strip()
    context_fingerprint = hashlib.sha256(project_id.encode("utf-8")).hexdigest() if project_id else "default"
    scoped_key = api_key if api_key is not None else await get_tenant_api_key(str(tenant_id), db)
    credential_scope = hashlib.sha256(f"{credential_fingerprint(scoped_key)}:{context_fingerprint}".encode()).hexdigest()
    return project_id or None, context_fingerprint, credential_scope


def _ttl_seconds() -> int:
    try:
        return max(60, min(86400, int(os.getenv("OPENAI_MODEL_CATALOG_TTL_SECONDS", "1800"))))
    except (TypeError, ValueError):
        return 1800


def classify_model(model_id: str) -> tuple[str, str, bool]:
    normalized = model_id.strip().lower()
    if any(marker in normalized for marker in SPECIALIZED_MARKERS):
        return "incompatible", "Specialized model; not available for the conversation selector", False
    # The official list endpoint publishes identifiers/owners, not capabilities.
    if normalized in KNOWN_CHAT_MODELS or is_allowed_model_id(normalized):
        return "known_compatible", "Known Chat Completions profile", True
    return "needs_validation", "Verification required with an explicit test", False


def sanitize_model(item: Any, *, validation_status: str | None = None) -> dict[str, Any] | None:
    model_id = str(getattr(item, "id", "") or (item.get("id") if isinstance(item, dict) else "") or "").strip()
    if not model_id or len(model_id) > 255 or any(ord(c) < 32 for c in model_id):
        return None
    created = getattr(item, "created", None)
    owner = getattr(item, "owned_by", None)
    if isinstance(item, dict):
        created = item.get("created", created)
        owner = item.get("owned_by", owner)
    compatibility, explanation, known = classify_model(model_id)
    if validation_status == "compatible":
        compatibility, explanation = "verified_compatible", "Validated with the tenant credential using Chat Completions"
    elif validation_status == "incompatible":
        compatibility, explanation, known = "incompatible", "The explicit provider check did not succeed", False
    return {
        "id": model_id,
        "name": model_id,
        "provider": "openai",
        # /models proves that this credential can list the ID, not that every
        # endpoint or feature is enabled for it.
        "availability": "listed",
        "compatibility": compatibility,
        "compatibility_known": known or validation_status == "compatible",
        "validation_required": compatibility == "needs_validation",
        "compatibility_explanation": explanation,
        "created": int(created) if isinstance(created, int) and not isinstance(created, bool) else None,
        "owned_by": str(owner)[:80] if owner else None,
        "source": "openai_api_v1_models",
        "fetched_at": None,
    }


async def _list_models_from_openai(api_key: str, project_id: str | None = None) -> list[dict[str, Any]]:
    from openai import AsyncOpenAI

    client_kwargs = {"api_key": api_key, "timeout": 8.0, "max_retries": 0}
    if project_id:
        client_kwargs["project"] = project_id
    client = AsyncOpenAI(**client_kwargs)
    try:
        page = await client.models.list()
        # AsyncPage implements an auto-paging iterator. Consume all pages so a
        # large tenant catalog is not silently truncated to the first response.
        result = []
        async for item in page:
            model = sanitize_model(item)
            if model is not None:
                result.append(model)
            if len(result) >= 10000:
                break
        result.sort(key=lambda model: (model["created"] or 0, model["id"]), reverse=True)
        if not result:
            raise ValueError("invalid_model_catalog")
        return result
    finally:
        await client.close()


async def _snapshot(db: AsyncSession, tenant_id, fingerprint: str, project_context_fingerprint: str):
    return await db.scalar(select(OpenAIModelCatalogSnapshot).where(
        OpenAIModelCatalogSnapshot.empresa_id == tenant_id,
        OpenAIModelCatalogSnapshot.credential_fingerprint == fingerprint,
        OpenAIModelCatalogSnapshot.provider == "openai",
        OpenAIModelCatalogSnapshot.project_context_fingerprint == project_context_fingerprint,
    ))


async def _with_validation_state(db: AsyncSession, tenant_id, fingerprint: str, items: list[dict[str, Any]]):
    rows = (await db.execute(select(OpenAIModelValidation).where(
        OpenAIModelValidation.empresa_id == tenant_id,
        OpenAIModelValidation.credential_fingerprint == fingerprint,
        OpenAIModelValidation.validation_status.in_(("compatible", "incompatible")),
        OpenAIModelValidation.expires_at > datetime.utcnow(),
    ))).scalars().all()
    validation_by_model = {row.model_id: row.validation_status for row in rows}
    result = []
    for item in items:
        copied = dict(item)
        validation_status = validation_by_model.get(copied.get("id"))
        if validation_status == "compatible":
            copied.update({"compatibility": "verified_compatible", "compatibility_known": True, "validation_required": False, "compatibility_explanation": "Validated with the tenant credential using Chat Completions"})
        elif validation_status == "incompatible":
            copied.update({"compatibility": "incompatible", "compatibility_known": True, "validation_required": False, "compatibility_explanation": "The explicit provider check did not succeed"})
        result.append(copied)
    return result


async def list_models(db: AsyncSession, tenant_id, *, refresh: bool = False) -> dict[str, Any]:
    try:
        api_key = await get_tenant_api_key(str(tenant_id), db)
    except ValueError as exc:
        if "não configurou" in str(exc).lower():
            return {"items": [], "credential_status": "not_configured", "updated_at": None, "stale": False, "source": "tenant_openai_credential"}
        raise
    project_id, project_context_fingerprint, fingerprint = await _project_context(db, tenant_id, api_key)
    cached = await _snapshot(db, tenant_id, fingerprint, project_context_fingerprint)
    now = datetime.utcnow()
    ttl = timedelta(seconds=_ttl_seconds())
    if cached and not refresh and cached.fetched_at + ttl > now:
        items = await _with_validation_state(db, tenant_id, fingerprint, cached.models_json)
        return {"items": items, "credential_status": "configured", "updated_at": cached.fetched_at.isoformat(), "stale": False, "source": "cache"}

    try:
        items = await asyncio.wait_for(_list_models_from_openai(api_key, project_id), timeout=9.0)
        for item in items:
            item["fetched_at"] = now.isoformat()
        if cached is None:
            statement = insert(OpenAIModelCatalogSnapshot).values(
                id=uuid.uuid4(), empresa_id=tenant_id, credential_fingerprint=fingerprint,
                provider="openai", project_context_fingerprint=project_context_fingerprint,
                models_json=items, fetched_at=now,
            ).on_conflict_do_update(
                index_elements=["empresa_id", "credential_fingerprint", "provider", "project_context_fingerprint"],
                set_={"models_json": items, "fetched_at": now},
            )
            await db.execute(statement)
        else:
            cached.models_json = items
            cached.fetched_at = now
        await db.flush()
        return {"items": await _with_validation_state(db, tenant_id, fingerprint, items), "credential_status": "configured", "updated_at": now.isoformat(), "stale": False, "source": "openai_api_v1_models"}
    except Exception as exc:
        if cached:
            return {
                "items": await _with_validation_state(db, tenant_id, fingerprint, cached.models_json),
                "credential_status": "configured",
                "updated_at": cached.fetched_at.isoformat(),
                "stale": True,
                "refresh_error": _safe_error_code(exc),
                "source": "last_valid_cache",
            }
        raise CatalogUnavailable(_safe_error_code(exc)) from None


def _safe_error_code(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code in (400, 401, 403, 404, 429):
        return f"provider_http_{status_code}"
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in exc.__class__.__name__.lower():
        return "provider_timeout"
    if isinstance(exc, ValueError):
        return "invalid_provider_response"
    return "provider_unavailable"


class CatalogUnavailable(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def validate_chat_model(db: AsyncSession, tenant_id, model_id: str) -> dict[str, Any]:
    normalized = model_id.strip()
    if not normalized or len(normalized) > 255 or any(ord(c) < 32 for c in normalized):
        raise ValueError("invalid_model_id")
    compatibility, _, _ = classify_model(normalized)
    if compatibility == "incompatible":
        raise ValueError("specialized_model_not_supported")
    api_key = await get_tenant_api_key(str(tenant_id), db)
    project_id, project_context_fingerprint, fingerprint = await _project_context(db, tenant_id, api_key)
    cached = await _snapshot(db, tenant_id, fingerprint, project_context_fingerprint)
    allowed = {item.get("id") for item in (cached.models_json if cached else []) if isinstance(item, dict)}
    if normalized not in allowed:
        raise ValueError("model_not_in_tenant_catalog")
    from openai import AsyncOpenAI

    client_kwargs = {"api_key": api_key, "timeout": 12.0, "max_retries": 0}
    if project_id:
        client_kwargs["project"] = project_id
    client = AsyncOpenAI(**client_kwargs)
    try:
        # Explicit admin action only. No temperature, token-limit, response-format,
        # or reasoning parameters are sent, maximizing endpoint compatibility.
        response = await asyncio.wait_for(client.chat.completions.create(
            model=normalized,
            messages=[{"role": "user", "content": "Reply with OK."}],
        ), timeout=13.0)
        if not getattr(response, "choices", None):
            raise ValueError("empty_provider_response")
    except Exception as exc:
        code = _safe_error_code(exc)
        if code in {"provider_http_400", "provider_http_404"}:
            await _save_validation(db, tenant_id, fingerprint, normalized, "incompatible", datetime.utcnow())
        raise CatalogUnavailable(code) from None
    finally:
        await client.close()
    now = datetime.utcnow()
    await _save_validation(db, tenant_id, fingerprint, normalized, "compatible", now)
    return {"model_id": normalized, "compatibility": "verified_compatible", "endpoint": "chat.completions", "validated_at": now.isoformat(), "expires_at": (now + timedelta(days=30)).isoformat(), "possible_cost": True}


async def _save_validation(db, tenant_id, fingerprint, model_id, state, now):
    row = await db.scalar(select(OpenAIModelValidation).where(
        OpenAIModelValidation.empresa_id == tenant_id,
        OpenAIModelValidation.credential_fingerprint == fingerprint,
        OpenAIModelValidation.model_id == model_id,
    ))
    if row is None:
        row = OpenAIModelValidation(empresa_id=tenant_id, credential_fingerprint=fingerprint, model_id=model_id, validation_status=state, validated_at=now, expires_at=now + timedelta(days=30))
        db.add(row)
    else:
        row.validation_status = state
        row.validated_at = now
        row.expires_at = now + timedelta(days=30)
    await db.flush()


async def model_is_validated(db: AsyncSession, tenant_id, model_id: str) -> bool:
    try:
        key = await get_tenant_api_key(str(tenant_id), db)
    except ValueError:
        return False
    project_id, project_context_fingerprint, fingerprint = await _project_context(db, tenant_id, key)
    snapshot = await _snapshot(db, tenant_id, fingerprint, project_context_fingerprint)
    if not snapshot or not any(isinstance(item, dict) and item.get("id") == model_id for item in snapshot.models_json or []):
        return False
    row = await db.scalar(select(OpenAIModelValidation).where(
        OpenAIModelValidation.empresa_id == tenant_id,
        OpenAIModelValidation.credential_fingerprint == fingerprint,
        OpenAIModelValidation.model_id == model_id,
        OpenAIModelValidation.validation_status == "compatible",
        OpenAIModelValidation.expires_at > datetime.utcnow(),
    ))
    if row:
        return True
    # Historical IDs accepted by the existing product contract remain selectable
    # only when the tenant's current /models catalog contains them.
    compatibility, _, known = classify_model(model_id)
    if compatibility == "incompatible" or not known:
        return False
    return compatibility in {"known_compatible", "verified_compatible"}
