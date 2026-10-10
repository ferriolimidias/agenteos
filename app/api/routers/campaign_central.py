from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_tenant_access
from app.services.campaign_audience_service import (
    import_contacts_csv,
    delete_import_batch,
    list_campaign_snapshots,
    list_import_batches,
    list_saved_audiences,
    list_tag_audiences,
    preview_import_csv,
    preview_saved_audience,
    save_audience,
)
from app.services.campaign_funnel_audience_service import (
    list_campaign_funnels,
    list_smart_audiences,
    preview_smart_audience,
    reorder_funnel_stages,
    save_smart_audience,
    set_funnel_stage_active,
    upsert_campaign_funnel,
    upsert_funnel_stage,
)
from app.services.campaign_central_policy import evaluate_message_policy
from app.services.campaign_central_service import (
    campaign_overview,
    create_local_template_draft,
    prepare_campaign_snapshot,
    prepare_remote_template_action,
    submit_template_to_meta,
    validate_campaign_image_upload,
    validate_template_header_image_upload,
    validate_template_payload,
    validate_template_payload_for_meta_create,
)
from app.services.campaign_central_dispatch_service import (
    CampaignCentralError,
    campaign_results,
    campaign_results_analytics,
    cancel_campaign,
    list_central_campaigns,
    pause_campaign,
    resume_campaign,
    start_campaign_from_snapshot,
)
from app.api.routers.meta_templates import _provider_for_connection
from app.services.channels.providers.meta_whatsapp import MetaWhatsAppError
from app.services.meta.image_validation import ImageValidationError
from app.services.meta.settings import meta_settings
from db.database import get_db
from db.models import Conexao, Usuario
from sqlalchemy import select


router = APIRouter(prefix="/api/empresas/{empresa_id}/campaign-central", tags=["Campaign Central"])


def _uuid_or_400(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label} inválido.")


def _datetime_or_400(value: str | None, label: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{label} inválido.") from exc


@router.get("/overview")
async def get_overview(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await campaign_overview(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"))


@router.post("/policy/evaluate")
async def evaluate_policy_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    _: Usuario = Depends(require_tenant_access),
):
    _uuid_or_400(empresa_id, "empresa_id")
    return evaluate_message_policy(payload or {})


@router.post("/templates/validate")
async def validate_template_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    _: Usuario = Depends(require_tenant_access),
):
    _uuid_or_400(empresa_id, "empresa_id")
    return validate_template_payload_for_meta_create(payload or {}, require_remote_header_handle=False)


@router.post("/templates/drafts", status_code=status.HTTP_201_CREATED)
async def create_template_draft_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    result = await create_local_template_draft(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"), payload=payload or {})
    if not result.get("created"):
        raise HTTPException(status_code=422, detail=result)
    await db.commit()
    return result


@router.post("/templates/remote-actions/{action}")
async def remote_template_action_endpoint(
    empresa_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    payload = payload or {}
    if action in {"submit_template", "create_template"}:
        try:
            conexao_uuid = _uuid_or_400(str(payload.get("conexao_id") or ""), "conexao_id")
            connection = (
                await db.execute(select(Conexao).where(Conexao.empresa_id == empresa_uuid, Conexao.id == conexao_uuid))
            ).scalars().first()
            if not connection:
                raise HTTPException(status_code=404, detail="Conexão não encontrada para esta empresa.")
            result = await submit_template_to_meta(
                db,
                empresa_id=empresa_uuid,
                payload=payload,
                provider=_provider_for_connection(connection),
                initiated_by=getattr(user, "id", None),
            )
            if not result.get("created"):
                raise HTTPException(status_code=422, detail=result)
            await db.commit()
            return result
        except HTTPException:
            await db.rollback()
            raise
        except MetaWhatsAppError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail={
                    "message": str(exc),
                    "meta_error": exc.meta_error,
                    "http_status": exc.http_status,
                },
            ) from exc
        except Exception as exc:
            await db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = prepare_remote_template_action(action, payload)
    if not result.get("allowed"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/templates/header-image-handle")
async def upload_template_header_image_endpoint(
    empresa_id: str,
    conexao_id: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    conexao_uuid = _uuid_or_400(conexao_id, "conexao_id")
    connection = (
        await db.execute(select(Conexao).where(Conexao.empresa_id == empresa_uuid, Conexao.id == conexao_uuid))
    ).scalars().first()
    if not connection:
        raise HTTPException(status_code=404, detail="Conexão não encontrada para esta empresa.")
    content = await file.read()
    try:
        image = validate_template_header_image_upload(content, declared_mime=file.content_type, original_name=file.filename)
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = meta_settings()
    if not settings.app_id:
        raise HTTPException(status_code=503, detail="Meta App ID não configurado para upload resumable.")
    result = await _provider_for_connection(connection).create_template_header_handle(
        app_id=settings.app_id,
        file_name=image["filename"],
        mime_type=image["mime_type"],
        content=content,
    )
    return {
        "header_handle": result["header_handle"],
        "mime_type": image["mime_type"],
        "filename": image["filename"],
        "byte_size": image["byte_size"],
        "width": image["width"],
        "height": image["height"],
    }


@router.post("/campaigns/prepare", status_code=status.HTTP_201_CREATED)
async def prepare_campaign_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    result = await prepare_campaign_snapshot(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"), payload=payload or {})
    if not result.get("prepared"):
        raise HTTPException(status_code=422, detail=result)
    await db.commit()
    return result


@router.post("/campaigns/prepare-with-media", status_code=status.HTTP_201_CREATED)
async def prepare_campaign_with_media_endpoint(
    empresa_id: str,
    payload_json: str = Form(...),
    image: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        import json

        payload = json.loads(payload_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Payload da campanha inválido.") from exc
    campaign_image = None
    if image is not None:
        content = await image.read()
        try:
            campaign_image = validate_campaign_image_upload(content, declared_mime=image.content_type, original_name=image.filename)
        except ImageValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        campaign_image["content"] = content
    result = await prepare_campaign_snapshot(
        db,
        empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
        payload=payload or {},
        provider_factory=_provider_for_connection,
        campaign_image=campaign_image,
    )
    if not result.get("prepared"):
        raise HTTPException(status_code=422, detail=result)
    await db.commit()
    return result


@router.get("/campaigns")
async def list_campaigns_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await list_central_campaigns(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"))


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign_endpoint(
    empresa_id: str,
    campaign_id: str,
    payload: dict[str, Any] | None = None,
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    try:
        result = await start_campaign_from_snapshot(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            campaign_id=_uuid_or_400(campaign_id, "campaign_id"),
            snapshot_id=(payload or {}).get("snapshot_id"),
            confirmed_recipient_count=(payload or {}).get("confirmed_recipient_count"),
            started_by=getattr(user, "id", None),
        )
        await db.commit()
        return result
    except CampaignCentralError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign_endpoint(
    empresa_id: str,
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await pause_campaign(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            campaign_id=_uuid_or_400(campaign_id, "campaign_id"),
        )
        await db.commit()
        return result
    except CampaignCentralError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/campaigns/{campaign_id}/resume")
async def resume_campaign_endpoint(
    empresa_id: str,
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await resume_campaign(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            campaign_id=_uuid_or_400(campaign_id, "campaign_id"),
        )
        await db.commit()
        return result
    except CampaignCentralError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign_endpoint(
    empresa_id: str,
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await cancel_campaign(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            campaign_id=_uuid_or_400(campaign_id, "campaign_id"),
        )
        await db.commit()
        return result
    except CampaignCentralError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/campaigns/{campaign_id}/results")
async def campaign_results_endpoint(
    empresa_id: str,
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await campaign_results(
        db,
        empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
        campaign_id=_uuid_or_400(campaign_id, "campaign_id"),
    )


@router.get("/results")
async def results_analytics_endpoint(
    empresa_id: str,
    campaign_id: str | None = Query(default=None),
    period_start: str | None = Query(default=None),
    period_end: str | None = Query(default=None),
    conexao_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await campaign_results_analytics(
        db,
        empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
        campaign_id=_uuid_or_400(campaign_id, "campaign_id") if campaign_id else None,
        conexao_id=_uuid_or_400(conexao_id, "conexao_id") if conexao_id else None,
        period_start=_datetime_or_400(period_start, "period_start"),
        period_end=_datetime_or_400(period_end, "period_end"),
    )


@router.get("/audiences/tags")
async def list_tag_audiences_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await list_tag_audiences(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"))


@router.get("/audiences/imports")
async def list_import_batches_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await list_import_batches(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"))


@router.delete("/audiences/imports/{import_id}")
async def delete_import_batch_endpoint(
    empresa_id: str,
    import_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa_uuid = _uuid_or_400(empresa_id, "empresa_id")
    import_uuid = _uuid_or_400(import_id, "import_id")
    deleted = await delete_import_batch(db, empresa_id=empresa_uuid, import_id=import_uuid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Lista importada não encontrada.")
    await db.commit()
    return {"deleted": True, "import_id": str(import_uuid)}


@router.post("/audiences/imports/preview")
async def preview_import_endpoint(
    empresa_id: str,
    file: UploadFile = File(...),
    _: Usuario = Depends(require_tenant_access),
):
    _uuid_or_400(empresa_id, "empresa_id")
    if not str(file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Envie um arquivo CSV.")
    try:
        return await preview_import_csv(await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audiences/imports", status_code=status.HTTP_201_CREATED)
async def import_contacts_endpoint(
    empresa_id: str,
    file: UploadFile = File(...),
    payload_json: str = Form("{}"),
    db: AsyncSession = Depends(get_db),
    user: Usuario = Depends(require_tenant_access),
):
    if not str(file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Envie um arquivo CSV.")
    try:
        import json

        payload = json.loads(payload_json or "{}")
        result = await import_contacts_csv(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            file_bytes=await file.read(),
            filename=str(file.filename or "import.csv"),
            payload=payload if isinstance(payload, dict) else {},
            user_id=getattr(user, "id", None),
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/audiences/saved")
async def list_saved_audiences_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await list_saved_audiences(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"))


@router.post("/audiences/saved/preview")
async def preview_saved_audience_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    payload = payload if isinstance(payload, dict) else {}
    return await preview_saved_audience(
        db,
        empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
        rules=payload.get("rules") if isinstance(payload.get("rules"), dict) else {},
        member_view=payload.get("member_view"),
        member_cursor=payload.get("member_cursor"),
        member_limit=int(payload.get("member_limit") or 50),
        member_reason=payload.get("member_reason"),
    )


@router.post("/audiences/saved", status_code=status.HTTP_201_CREATED)
async def save_audience_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await save_audience(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"), payload=payload or {})
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/audiences/funnels")
async def list_campaign_funnels_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await list_campaign_funnels(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"))


@router.post("/audiences/funnels")
async def upsert_campaign_funnel_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await upsert_campaign_funnel(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"), payload=payload or {})
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audiences/funnels/{funnel_id}/stages")
async def upsert_funnel_stage_endpoint(
    empresa_id: str,
    funnel_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await upsert_funnel_stage(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            funnel_id=_uuid_or_400(funnel_id, "funnel_id"),
            payload=payload or {},
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audiences/funnels/{funnel_id}/stages/reorder")
async def reorder_funnel_stages_endpoint(
    empresa_id: str,
    funnel_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await reorder_funnel_stages(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            funnel_id=_uuid_or_400(funnel_id, "funnel_id"),
            stages=payload.get("stages") if isinstance(payload.get("stages"), list) else [],
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audiences/funnels/{funnel_id}/stages/{stage_id}/active")
async def set_funnel_stage_active_endpoint(
    empresa_id: str,
    funnel_id: str,
    stage_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await set_funnel_stage_active(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            funnel_id=_uuid_or_400(funnel_id, "funnel_id"),
            stage_id=_uuid_or_400(stage_id, "stage_id"),
            active=bool((payload or {}).get("active", True)),
        )
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/audiences/smart")
async def list_smart_audiences_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await list_smart_audiences(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"))


@router.post("/audiences/smart/preview")
async def preview_smart_audience_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    payload = payload if isinstance(payload, dict) else {}
    try:
        return await preview_smart_audience(
            db,
            empresa_id=_uuid_or_400(empresa_id, "empresa_id"),
            funnel_id=_uuid_or_400(payload.get("funnel_id"), "funnel_id"),
            stage_id=_uuid_or_400(payload.get("stage_id"), "stage_id"),
            suggestion_type=str(payload.get("type") or "not_advanced"),
            member_view=payload.get("member_view"),
            member_cursor=payload.get("member_cursor"),
            member_limit=int(payload.get("member_limit") or 25),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/audiences/smart/save")
async def save_smart_audience_endpoint(
    empresa_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        result = await save_smart_audience(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"), payload=payload or {})
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/audiences/snapshots")
async def list_snapshots_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    return await list_campaign_snapshots(db, empresa_id=_uuid_or_400(empresa_id, "empresa_id"))
