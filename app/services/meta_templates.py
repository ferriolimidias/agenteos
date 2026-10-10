from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.secret_sanitizer import sanitize_for_log
from app.services.channels.types import Provider
from app.services.conversation_window import evaluate_conversation_window
from db.models import Conexao, MetaMessageTemplate, MetaTemplateComponent, MetaTemplateSyncRun, ChannelConversation


GRAPH_API_VERSION = "v25.0"
PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")
NUMERIC_PLACEHOLDER_RE = re.compile(r"^\d+$")


class MetaTemplateProvider(Protocol):
    async def list_templates_page(self, *, waba_id: str, after: str | None = None) -> dict[str, Any]:
        ...


class MetaTemplateUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class PlaceholderSpec:
    name: str
    component_type: str
    component_order: int
    source: str
    expected_type: str = "text"


@dataclass(frozen=True)
class ParsedTemplate:
    placeholders: list[PlaceholderSpec] = field(default_factory=list)
    preview_components: list[dict[str, Any]] = field(default_factory=list)
    requires_media: bool = False
    has_buttons: bool = False
    has_dynamic_url: bool = False
    has_carousel: bool = False
    has_authentication: bool = False
    unknown_components: list[str] = field(default_factory=list)


def normalize_meta_template_category(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    if raw == "MARKETING":
        return "marketing"
    if raw == "UTILITY":
        return "utility"
    if raw == "AUTHENTICATION":
        return "authentication"
    return "unknown"


def normalize_meta_template_status(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    mapping = {
        "APPROVED": "approved",
        "PENDING": "pending",
        "REJECTED": "rejected",
        "PAUSED": "paused",
        "DISABLED": "disabled",
        "ARCHIVED": "archived",
        "DELETED": "deleted",
    }
    return mapping.get(raw, "unknown")


def sanitize_meta_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("token", "secret", "password", "authorization", "api_key", "apikey")):
                sanitized[key] = "<redacted>"
            else:
                sanitized[key] = sanitize_meta_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_meta_payload(item) for item in value]
    if isinstance(value, str):
        return str(sanitize_for_log(value))
    return value


def _component_placeholders(component: dict[str, Any], order: int) -> list[PlaceholderSpec]:
    component_type = str(component.get("type") or "UNKNOWN").upper()
    fmt = str(component.get("format") or "").upper()
    specs: list[PlaceholderSpec] = []
    text = str(component.get("text") or "")
    for match in PLACEHOLDER_RE.finditer(text):
        name = match.group(1)
        specs.append(
            PlaceholderSpec(
                name=name,
                component_type=component_type,
                component_order=order,
                source="text",
                expected_type="text",
            )
        )
    if component_type == "HEADER" and fmt in {"IMAGE", "VIDEO", "DOCUMENT"}:
        specs.append(
            PlaceholderSpec(
                name=f"header_{fmt.lower()}",
                component_type=component_type,
                component_order=order,
                source="media",
                expected_type=fmt.lower(),
            )
        )
    buttons = component.get("buttons") if isinstance(component.get("buttons"), list) else []
    for index, button in enumerate(buttons):
        if not isinstance(button, dict):
            continue
        url = str(button.get("url") or "")
        for match in PLACEHOLDER_RE.finditer(url):
            specs.append(
                PlaceholderSpec(
                    name=f"button_{index}_{match.group(1)}",
                    component_type="BUTTONS",
                    component_order=order,
                    source="button_url",
                    expected_type="text",
                )
            )
    return specs


def parse_template_components(components: list[dict[str, Any]] | None) -> ParsedTemplate:
    placeholders: list[PlaceholderSpec] = []
    preview_components: list[dict[str, Any]] = []
    unknown_components: list[str] = []
    requires_media = False
    has_buttons = False
    has_dynamic_url = False
    has_carousel = False
    has_authentication = False

    for order, component in enumerate(components or []):
        if not isinstance(component, dict):
            continue
        ctype = str(component.get("type") or "UNKNOWN").upper()
        fmt = str(component.get("format") or "").upper()
        if ctype not in {"HEADER", "BODY", "FOOTER", "BUTTONS", "CAROUSEL"}:
            unknown_components.append(ctype)
        if ctype == "HEADER" and fmt in {"IMAGE", "VIDEO", "DOCUMENT"}:
            requires_media = True
        if ctype == "BUTTONS":
            has_buttons = True
            for button in component.get("buttons") or []:
                if isinstance(button, dict) and PLACEHOLDER_RE.search(str(button.get("url") or "")):
                    has_dynamic_url = True
                if isinstance(button, dict) and str(button.get("type") or "").upper() in {"OTP", "COPY_CODE"}:
                    has_authentication = True
        if ctype == "CAROUSEL":
            has_carousel = True
        placeholders.extend(_component_placeholders(component, order))
        preview_components.append(
            {
                "type": ctype,
                "format": fmt or None,
                "text": component.get("text"),
                "buttons": component.get("buttons") if isinstance(component.get("buttons"), list) else [],
                "example": component.get("example") if isinstance(component.get("example"), dict) else {},
                "unknown": ctype in unknown_components,
            }
        )

    return ParsedTemplate(
        placeholders=placeholders,
        preview_components=preview_components,
        requires_media=requires_media,
        has_buttons=has_buttons,
        has_dynamic_url=has_dynamic_url,
        has_carousel=has_carousel,
        has_authentication=has_authentication,
        unknown_components=unknown_components,
    )


def validate_template_parameters(parsed: ParsedTemplate, parameters: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(parameters or {})
    required = [spec.name for spec in parsed.placeholders]
    missing = [name for name in required if name not in params or params.get(name) in (None, "")]
    extra = sorted([name for name in params if name not in set(required)])
    valid = not missing and not extra
    return {
        "valid": valid,
        "missing": missing,
        "extra": extra,
        "required": required,
        "requires_media": parsed.requires_media,
        "has_buttons": parsed.has_buttons,
        "has_dynamic_url": parsed.has_dynamic_url,
        "has_carousel": parsed.has_carousel,
        "has_authentication": parsed.has_authentication,
    }


def render_template_preview(components: list[dict[str, Any]] | None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    params = dict(parameters or {})
    parsed = parse_template_components(components)
    rendered: list[dict[str, Any]] = []
    for component in parsed.preview_components:
        text = str(component.get("text") or "")
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            return str(params.get(key, "{{" + key + "}}"))
        rendered.append({**component, "text": PLACEHOLDER_RE.sub(replace, text)})
    return {"components": rendered, "validation": validate_template_parameters(parsed, params)}


def _sync_key(*parts: str) -> str:
    basis = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


async def resolve_meta_template_connection(session: AsyncSession, *, empresa_id: uuid.UUID, conexao_id: uuid.UUID) -> Conexao:
    result = await session.execute(
        select(Conexao).where(
            Conexao.id == conexao_id,
            Conexao.empresa_id == empresa_id,
        )
    )
    conexao = result.scalars().first()
    if not conexao:
        raise MetaTemplateUnavailable("Conexao nao encontrada para esta empresa.")
    provider = str(getattr(conexao, "provider", "") or "").lower()
    tipo = str(getattr(conexao, "tipo", "") or "").lower()
    if provider not in {"meta_whatsapp", "meta_official", "meta", "whatsapp_meta_official"} and "meta" not in tipo:
        raise MetaTemplateUnavailable("Templates oficiais exigem uma conexao WhatsApp oficial.")
    if not str(getattr(conexao, "waba_id", "") or "").strip():
        raise MetaTemplateUnavailable("Conexao oficial sem WABA associado.")
    return conexao


async def upsert_meta_template(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    conexao_id: uuid.UUID,
    waba_id: str,
    item: dict[str, Any],
    graph_api_version: str = GRAPH_API_VERSION,
) -> tuple[MetaMessageTemplate, bool, bool]:
    now = datetime.utcnow()
    remote_id = str(item.get("id") or "").strip() or None
    name = str(item.get("name") or "").strip()
    language = str(item.get("language") or "").strip()
    if not name or not language:
        raise ValueError("Template Meta sem nome ou idioma.")
    conditions = [
        MetaMessageTemplate.empresa_id == empresa_id,
        MetaMessageTemplate.conexao_id == conexao_id,
    ]
    if remote_id:
        conditions.append(MetaMessageTemplate.remote_template_id == remote_id)
    else:
        conditions.extend(
            [
                MetaMessageTemplate.waba_id == waba_id,
                MetaMessageTemplate.name == name,
                MetaMessageTemplate.language == language,
            ]
        )
    result = await session.execute(select(MetaMessageTemplate).where(and_(*conditions)).options(selectinload(MetaMessageTemplate.components)))
    template = result.scalars().first()
    created = template is None
    if template is None:
        template = MetaMessageTemplate(
            empresa_id=empresa_id,
            conexao_id=conexao_id,
            waba_id=waba_id,
            remote_template_id=remote_id,
            name=name,
            language=language,
            first_seen_at=now,
        )
        session.add(template)
        await session.flush()
    before = {
        "raw_status": template.raw_status,
        "raw_category": template.raw_category,
        "quality_rating": template.quality_rating,
        "raw_metadata_json": template.raw_metadata_json,
    }
    template.remote_template_id = remote_id or template.remote_template_id
    template.raw_category = str(item.get("category") or "") or None
    template.normalized_category = normalize_meta_template_category(template.raw_category)
    template.raw_status = str(item.get("status") or "") or None
    template.normalized_status = normalize_meta_template_status(template.raw_status)
    template.quality_rating = str(item.get("quality_score") or item.get("quality_rating") or "") or None
    template.rejection_reason = str(sanitize_for_log(item.get("rejected_reason") or item.get("rejection_reason") or ""))[:500] or None
    template.parameter_format = str(item.get("parameter_format") or "") or None
    template.last_synced_at = now
    template.last_seen_at = now
    template.stale_at = None
    template.graph_api_version = graph_api_version
    template.raw_metadata_json = sanitize_meta_payload(item)
    template.updated_at = now
    await session.execute(
        MetaTemplateComponent.__table__.delete().where(
            MetaTemplateComponent.empresa_id == empresa_id,
            MetaTemplateComponent.template_id == template.id,
        )
    )
    for order, component in enumerate(item.get("components") if isinstance(item.get("components"), list) else []):
        session.add(
            MetaTemplateComponent(
                empresa_id=empresa_id,
                template_id=template.id,
                component_type=str(component.get("type") or "UNKNOWN").upper(),
                component_order=order,
                format=str(component.get("format") or "") or None,
                text=component.get("text") if isinstance(component.get("text"), str) else None,
                buttons_json=sanitize_meta_payload(component.get("buttons") if isinstance(component.get("buttons"), list) else []),
                example_json=sanitize_meta_payload(component.get("example") if isinstance(component.get("example"), dict) else {}),
                media_reference_json=sanitize_meta_payload({k: component.get(k) for k in ("example", "format") if k in component}),
                raw_component_json=sanitize_meta_payload(component),
            )
        )
    changed = created or before != {
        "raw_status": template.raw_status,
        "raw_category": template.raw_category,
        "quality_rating": template.quality_rating,
        "raw_metadata_json": template.raw_metadata_json,
    }
    return template, created, changed


async def sync_meta_templates(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    conexao_id: uuid.UUID,
    provider: MetaTemplateProvider | None,
    initiated_by: uuid.UUID | None = None,
    source: str = "manual",
    graph_api_version: str = GRAPH_API_VERSION,
    idempotency_key: str | None = None,
) -> MetaTemplateSyncRun:
    conexao = await resolve_meta_template_connection(session, empresa_id=empresa_id, conexao_id=conexao_id)
    if provider is None:
        raise MetaTemplateUnavailable("Sincronizacao real indisponivel neste ambiente.")
    waba_id = str(conexao.waba_id).strip()
    run = MetaTemplateSyncRun(
        empresa_id=empresa_id,
        conexao_id=conexao_id,
        waba_id=waba_id,
        source=source,
        initiated_by=initiated_by,
        graph_api_version=graph_api_version,
        idempotency_key=idempotency_key
        or _sync_key(str(empresa_id), str(conexao_id), waba_id, source, str(uuid.uuid4())),
    )
    session.add(run)
    await session.flush()
    lock_key = int(hashlib.sha256(f"{empresa_id}:{conexao_id}:{waba_id}".encode()).hexdigest()[:15], 16)
    locked = (await session.execute(select(func.pg_try_advisory_xact_lock(lock_key)))).scalar()  # type: ignore[name-defined]
    if not locked:
        run.status = "blocked"
        run.error_code = "sync_in_progress"
        run.error_detail = "Ja existe uma sincronizacao em andamento para esta conexao."
        run.completed_at = datetime.utcnow()
        return run
    seen: set[uuid.UUID] = set()
    after: str | None = None
    try:
        while True:
            page = await provider.list_templates_page(waba_id=waba_id, after=after)
            data = page.get("data") if isinstance(page.get("data"), list) else []
            run.pages += 1
            for item in data:
                template, created, changed = await upsert_meta_template(
                    session,
                    empresa_id=empresa_id,
                    conexao_id=conexao_id,
                    waba_id=waba_id,
                    item=item,
                    graph_api_version=graph_api_version,
                )
                seen.add(template.id)
                run.found_count += 1
                if created:
                    run.created_count += 1
                elif changed:
                    run.updated_count += 1
                else:
                    run.unchanged_count += 1
            cursors = page.get("paging", {}).get("cursors") if isinstance(page.get("paging"), dict) else {}
            after = cursors.get("after") if isinstance(cursors, dict) else None
            run.final_cursor = str(sanitize_for_log(after or "")) or None
            if not after:
                break
        if seen:
            stale_result = await session.execute(
                select(MetaMessageTemplate).where(
                    MetaMessageTemplate.empresa_id == empresa_id,
                    MetaMessageTemplate.conexao_id == conexao_id,
                    MetaMessageTemplate.waba_id == waba_id,
                    MetaMessageTemplate.id.notin_(seen),
                    MetaMessageTemplate.archived_at.is_(None),
                )
            )
            now = datetime.utcnow()
            for stale in stale_result.scalars().all():
                stale.stale_at = stale.stale_at or now
                run.stale_count += 1
        run.status = "completed"
        run.completed_at = datetime.utcnow()
    except Exception as exc:
        run.status = "partial_failed" if run.found_count else "failed"
        run.error_code = exc.__class__.__name__[:80]
        run.error_detail = str(sanitize_for_log(str(exc)))[:500]
        run.completed_at = datetime.utcnow()
    return run


async def get_template_for_tenant(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    conexao_id: uuid.UUID,
    template_id: uuid.UUID,
) -> MetaMessageTemplate:
    result = await session.execute(
        select(MetaMessageTemplate)
        .where(
            MetaMessageTemplate.id == template_id,
            MetaMessageTemplate.empresa_id == empresa_id,
            MetaMessageTemplate.conexao_id == conexao_id,
        )
        .options(selectinload(MetaMessageTemplate.components))
    )
    template = result.scalars().first()
    if not template:
        raise MetaTemplateUnavailable("Template nao encontrado para esta empresa/conexao.")
    return template


async def validate_template_for_campaign(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    conexao_id: uuid.UUID,
    template_id: uuid.UUID,
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    template = await get_template_for_tenant(session, empresa_id=empresa_id, conexao_id=conexao_id, template_id=template_id)
    components = [component.raw_component_json for component in template.components]
    parsed = parse_template_components(components)
    validation = validate_template_parameters(parsed, parameters)
    usable = template.normalized_status == "approved" and not template.stale_at and not template.archived_at and validation["valid"]
    return {
        "valid": usable,
        "template_id": str(template.id),
        "status": template.normalized_status,
        "category": template.normalized_category,
        "language": template.language,
        "waba_id": template.waba_id,
        "validation": validation,
        "preview": render_template_preview(components, parameters),
    }


async def validate_template_for_followup(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    conexao_id: uuid.UUID,
    template_id: uuid.UUID,
    parameters: dict[str, Any] | None,
    conversation_id: uuid.UUID,
) -> dict[str, Any]:
    result = await session.execute(
        select(ChannelConversation).where(
            ChannelConversation.empresa_id == empresa_id,
            ChannelConversation.id == conversation_id,
            ChannelConversation.conexao_id == conexao_id,
        )
    )
    conversation = result.scalars().first()
    if not conversation:
        raise MetaTemplateUnavailable("Conversa operacional nao encontrada para esta conexao.")
    template_result = await validate_template_for_campaign(
        session,
        empresa_id=empresa_id,
        conexao_id=conexao_id,
        template_id=template_id,
        parameters=parameters,
    )
    window = evaluate_conversation_window(conversation, provider=Provider.META_WHATSAPP, channel="whatsapp")
    template_result["conversation_window"] = {
        "state": window.state,
        "reason": window.reason,
        "reference_at": window.reference_at.isoformat() if window.reference_at else None,
    }
    template_result["template_required_decision"] = "not_decided"
    return template_result
