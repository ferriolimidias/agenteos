from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.routers.auth import require_tenant_access
from app.services.meta_templates import (
    MetaTemplateUnavailable,
    get_template_for_tenant,
    render_template_preview,
    sync_meta_templates,
    validate_template_for_campaign,
    validate_template_for_followup,
)
from app.services.channels.contracts import ChannelConnectionContext
from app.services.channels.providers.meta_whatsapp import MetaWhatsAppProvider
from app.services.channels.types import Channel, Provider
from db.database import get_db
from db.models import Conexao, MetaMessageTemplate, MetaTemplateSyncRun, Usuario


router = APIRouter(prefix="/api/empresas/{empresa_id}/meta/templates", tags=["Meta Templates"])


class _MetaTemplateHttpTransport:
    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> Any:
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, json=json)

    async def get(self, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None) -> Any:
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url, headers=headers, params=params)

    async def delete(self, url: str, *, headers: dict[str, str]) -> Any:
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.delete(url, headers=headers)

    async def post_multipart(self, url: str, *, headers: dict[str, str], data: dict[str, Any], files: dict[str, Any]) -> Any:
        timeout = httpx.Timeout(30.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, data=data, files=files)

    async def post_bytes(self, url: str, *, headers: dict[str, str], content: bytes) -> Any:
        timeout = httpx.Timeout(30.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, content=content)


def _provider_for_connection(connection: Conexao) -> MetaWhatsAppProvider:
    context = ChannelConnectionContext(
        empresa_id=str(connection.empresa_id),
        connection_id=str(connection.id),
        channel=Channel.WHATSAPP,
        provider=Provider.META_WHATSAPP,
        status=str(connection.status or "connected"),
        external_account_id=str(connection.phone_number_id or ""),
        metadata={"waba_id": connection.waba_id, "nome_instancia": connection.nome_instancia},
    )
    return MetaWhatsAppProvider(context, connection=connection, transport=_MetaTemplateHttpTransport())


def _uuid_or_400(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label} inválido.")


def _serialize_template(template: MetaMessageTemplate, *, include_components: bool = False) -> dict[str, Any]:
    payload = {
        "id": str(template.id),
        "empresa_id": str(template.empresa_id),
        "conexao_id": str(template.conexao_id),
        "waba_id": template.waba_id,
        "remote_template_id": template.remote_template_id,
        "name": template.name,
        "language": template.language,
        "raw_category": template.raw_category,
        "normalized_category": template.normalized_category,
        "raw_status": template.raw_status,
        "normalized_status": template.normalized_status,
        "quality_rating": template.quality_rating,
        "rejection_reason": template.rejection_reason,
        "parameter_format": template.parameter_format,
        "last_synced_at": template.last_synced_at.isoformat() if template.last_synced_at else None,
        "first_seen_at": template.first_seen_at.isoformat() if template.first_seen_at else None,
        "last_seen_at": template.last_seen_at.isoformat() if template.last_seen_at else None,
        "stale_at": template.stale_at.isoformat() if template.stale_at else None,
        "archived_at": template.archived_at.isoformat() if template.archived_at else None,
        "graph_api_version": template.graph_api_version,
    }
    if include_components:
        components = sorted(template.components, key=lambda item: item.component_order)
        payload["components"] = [
            {
                "id": str(component.id),
                "component_type": component.component_type,
                "component_order": component.component_order,
                "format": component.format,
                "text": component.text,
                "buttons": component.buttons_json,
                "example": component.example_json,
            }
            for component in components
        ]
        payload["preview"] = render_template_preview([component.raw_component_json for component in components])
    return payload


def _serialize_sync_run(run: MetaTemplateSyncRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": str(run.id),
        "empresa_id": str(run.empresa_id),
        "conexao_id": str(run.conexao_id),
        "waba_id": run.waba_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "pages": run.pages,
        "found_count": run.found_count,
        "created_count": run.created_count,
        "updated_count": run.updated_count,
        "unchanged_count": run.unchanged_count,
        "stale_count": run.stale_count,
        "error_code": run.error_code,
        "error_detail": run.error_detail,
        "graph_api_version": run.graph_api_version,
        "source": run.source,
    }


@router.get("")
async def list_meta_templates(
    empresa_id: str,
    conexao_id: str | None = Query(default=None),
    waba_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    language: str | None = Query(default=None),
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    query = select(MetaMessageTemplate).where(MetaMessageTemplate.empresa_id == empresa_uuid)
    if conexao_id:
        query = query.where(MetaMessageTemplate.conexao_id == _uuid_or_400(conexao_id, "conexao_id"))
    if waba_id:
        query = query.where(MetaMessageTemplate.waba_id == str(waba_id).strip())
    if status_filter:
        query = query.where(MetaMessageTemplate.normalized_status == str(status_filter).strip().lower())
    if category:
        query = query.where(MetaMessageTemplate.normalized_category == str(category).strip().lower())
    if language:
        query = query.where(MetaMessageTemplate.language == str(language).strip())
    if search:
        query = query.where(MetaMessageTemplate.name.ilike(f"%{str(search).strip()}%"))
    result = await db.execute(
        query.options(selectinload(MetaMessageTemplate.components))
        .order_by(MetaMessageTemplate.name.asc(), MetaMessageTemplate.language.asc())
        .limit(500)
    )
    return [_serialize_template(template, include_components=True) for template in result.scalars().all()]


@router.get("/sync/latest")
async def latest_meta_template_sync(
    empresa_id: str,
    conexao_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    query = select(MetaTemplateSyncRun).where(MetaTemplateSyncRun.empresa_id == empresa_uuid)
    if conexao_id:
        query = query.where(MetaTemplateSyncRun.conexao_id == _uuid_or_400(conexao_id, "conexao_id"))
    result = await db.execute(query.order_by(MetaTemplateSyncRun.started_at.desc()).limit(1))
    return {"last_sync": _serialize_sync_run(result.scalars().first())}


@router.post("/sync")
async def request_meta_template_sync(
    empresa_id: str,
    conexao_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    conexao_uuid = _uuid_or_400(conexao_id, "conexao_id")
    result = await db.execute(select(Conexao).where(Conexao.empresa_id == empresa_uuid, Conexao.id == conexao_uuid))
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Conexão não encontrada para esta empresa.")
    connection = (
        await db.execute(select(Conexao).where(Conexao.empresa_id == empresa_uuid, Conexao.id == conexao_uuid))
    ).scalars().first()
    try:
        run = await sync_meta_templates(
            db,
            empresa_id=empresa_uuid,
            conexao_id=conexao_uuid,
            provider=_provider_for_connection(connection),
            initiated_by=current_user.id,
            source="manual_ui",
        )
    except MetaTemplateUnavailable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sincronização real de templates ainda não está disponível nesta etapa. Conclua a conexão oficial antes de sincronizar.",
        )
    await db.commit()
    return {"sync": _serialize_sync_run(run)}


@router.get("/{template_id}")
async def get_meta_template_detail(
    empresa_id: str,
    template_id: str,
    conexao_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        template = await get_template_for_tenant(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            conexao_id=_uuid_or_400(conexao_id, "conexao_id"),
            template_id=_uuid_or_400(template_id, "template_id"),
        )
    except MetaTemplateUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_template(template, include_components=True)


@router.post("/{template_id}/preview")
async def preview_meta_template(
    empresa_id: str,
    template_id: str,
    payload: dict[str, Any] | None = None,
    conexao_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    template = await get_template_for_tenant(
        db,
        empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
        conexao_id=_uuid_or_400(conexao_id, "conexao_id"),
        template_id=_uuid_or_400(template_id, "template_id"),
    )
    components = [component.raw_component_json for component in template.components]
    return render_template_preview(components, (payload or {}).get("parameters") if isinstance(payload, dict) else {})


@router.post("/{template_id}/validate/campaign")
async def validate_campaign_template_endpoint(
    empresa_id: str,
    template_id: str,
    payload: dict[str, Any] | None = None,
    conexao_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        return await validate_template_for_campaign(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            conexao_id=_uuid_or_400(conexao_id, "conexao_id"),
            template_id=_uuid_or_400(template_id, "template_id"),
            parameters=(payload or {}).get("parameters") if isinstance(payload, dict) else {},
        )
    except MetaTemplateUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{template_id}/validate/followup")
async def validate_followup_template_endpoint(
    empresa_id: str,
    template_id: str,
    payload: dict[str, Any] | None = None,
    conexao_id: str = Query(...),
    conversation_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        return await validate_template_for_followup(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            conexao_id=_uuid_or_400(conexao_id, "conexao_id"),
            template_id=_uuid_or_400(template_id, "template_id"),
            parameters=(payload or {}).get("parameters") if isinstance(payload, dict) else {},
            conversation_id=_uuid_or_400(conversation_id, "conversation_id"),
        )
    except MetaTemplateUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc))
