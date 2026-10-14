from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_tenant_access
from app.services.commercial_analytics import attribution_quality, event_signal_status, funnel, performance
from app.services.meta.canonical_conversion_shadow import shadow_status
from app.services.meta.sender_operations import enabled as sender_operations_enabled, sender_operations, sender_attempt_detail
from db.database import get_db
from db.models import Usuario

router = APIRouter(
    prefix="/api/empresas/{empresa_id}/analytics",
    tags=["Commercial Analytics"],
    dependencies=[Depends(require_tenant_access)],
)


def _window(start_date: date | None, end_date: date | None) -> tuple[date, date]:
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=29))
    if start > end or (end - start).days > 366:
        raise HTTPException(status_code=400, detail="Intervalo de analytics inválido.")
    return start, end


async def _performance(
    level: str,
    empresa_id: uuid.UUID,
    start_date: date | None,
    end_date: date | None,
    attribution_model: str,
    campaign_id: str | None,
    adset_id: str | None,
    ad_id: str | None,
    db: AsyncSession,
):
    start, end = _window(start_date, end_date)
    try:
        return await performance(
            db, empresa_id=empresa_id, start_date=start, end_date=end,
            attribution_model=attribution_model, level=level,
            campaign_id=campaign_id, adset_id=adset_id, ad_id=ad_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Filtro de analytics inválido.") from exc


@router.get("/performance")
async def commercial_performance(
    empresa_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    attribution_model: str = Query(default="last_touch", pattern="^(first_touch|last_touch)$"),
    campaign_id: str | None = Query(default=None, max_length=100),
    adset_id: str | None = Query(default=None, max_length=100),
    ad_id: str | None = Query(default=None, max_length=100),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    return await _performance("campaign", empresa_id, start_date, end_date, attribution_model, campaign_id, adset_id, ad_id, db)


@router.get("/campaigns")
async def campaign_performance(
    empresa_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    attribution_model: str = Query(default="last_touch", pattern="^(first_touch|last_touch)$"),
    campaign_id: str | None = Query(default=None, max_length=100),
    adset_id: str | None = Query(default=None, max_length=100),
    ad_id: str | None = Query(default=None, max_length=100),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    return await _performance("campaign", empresa_id, start_date, end_date, attribution_model, campaign_id, adset_id, ad_id, db)


@router.get("/adsets")
async def adset_performance(
    empresa_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    attribution_model: str = Query(default="last_touch", pattern="^(first_touch|last_touch)$"),
    campaign_id: str | None = Query(default=None, max_length=100),
    adset_id: str | None = Query(default=None, max_length=100),
    ad_id: str | None = Query(default=None, max_length=100),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    return await _performance("adset", empresa_id, start_date, end_date, attribution_model, campaign_id, adset_id, ad_id, db)


@router.get("/ads")
async def ad_performance(
    empresa_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    attribution_model: str = Query(default="last_touch", pattern="^(first_touch|last_touch)$"),
    campaign_id: str | None = Query(default=None, max_length=100),
    adset_id: str | None = Query(default=None, max_length=100),
    ad_id: str | None = Query(default=None, max_length=100),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    return await _performance("ad", empresa_id, start_date, end_date, attribution_model, campaign_id, adset_id, ad_id, db)


@router.get("/funnel")
async def commercial_funnel(
    empresa_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    attribution_model: str = Query(default="last_touch", pattern="^(first_touch|last_touch)$"),
    campaign_id: str | None = Query(default=None, max_length=100),
    adset_id: str | None = Query(default=None, max_length=100),
    ad_id: str | None = Query(default=None, max_length=100),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    start, end = _window(start_date, end_date)
    return await funnel(
        db, empresa_id=empresa_id, start_date=start, end_date=end, attribution_model=attribution_model,
        campaign_id=campaign_id, adset_id=adset_id, ad_id=ad_id,
    )


@router.get("/attribution-quality")
async def commercial_attribution_quality(
    empresa_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    start, end = _window(start_date, end_date)
    return await attribution_quality(db, empresa_id=empresa_id, start_date=start, end_date=end)


@router.get("/event-signals")
async def commercial_event_signals(
    empresa_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    start, end = _window(start_date, end_date)
    return await event_signal_status(db, empresa_id=empresa_id, start_date=start, end_date=end)


@router.get("/meta-conversion-shadow")
async def canonical_meta_conversion_shadow_status(
    empresa_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    return await shadow_status(db, empresa_id=empresa_id)


def _sender_operations_gate():
    if not sender_operations_enabled():
        raise HTTPException(status_code=404, detail="Visualização indisponível.")


@router.get("/meta-sender-operations")
async def meta_sender_operations(
    empresa_id: uuid.UUID,
    outcome: str | None = Query(default=None, pattern="^(accepted|rejected|unknown|not_sent|prepared|blocked)$"),
    transport: str | None = Query(default=None, pattern="^(business_messaging|capi)$"),
    event_name: str | None = Query(default=None, max_length=80),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    _sender_operations_gate()
    start = start_at.replace(tzinfo=timezone.utc) if start_at and start_at.tzinfo is None else start_at
    end = end_at.replace(tzinfo=timezone.utc) if end_at and end_at.tzinfo is None else end_at
    try:
        return await sender_operations(db, empresa_id=empresa_id, outcome=outcome, transport=transport,
                                       event_name=event_name, start_at=start, end_at=end, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Filtro operacional inválido.") from exc


@router.get("/meta-sender-operations/{attempt_id}")
async def meta_sender_attempt_detail(
    empresa_id: uuid.UUID,
    attempt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    _sender_operations_gate()
    result = await sender_attempt_detail(db, empresa_id=empresa_id, attempt_id=attempt_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Tentativa não encontrada.")
    return result


# A05 is read-only and additive; A01 and legacy analytics stay unchanged.
from app.services.commercial_funnel_dashboard import (
    enabled as commercial_funnel_dashboard_enabled,
    dashboard as commercial_funnel_dashboard,
    drilldown as commercial_funnel_leads,
    export_csv as commercial_funnel_export,
)
from app.services.attendance_quality import (
    dashboard as attendance_quality_dashboard,
    drilldown as attendance_quality_drilldown,
    enabled as attendance_quality_enabled,
)


def _commercial_funnel_filters(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    observed_at: datetime | None = Query(default=None),
    inactivity_hours: int = Query(default=168, ge=1, le=8760),
    return_window_days: int = Query(default=7, ge=1, le=90),
    stage: str | None = Query(default=None, min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_:-]+$'),
    origin: str | None = Query(default=None, pattern=r'^(whatsapp|instagram|web|sms|telegram|unknown|ctwa_referral|tracked_link_click|manual_touch|unknown_source)$'),
    campaign_id: str | None = Query(default=None, pattern=r'^[0-9]{1,40}$'),
    ad_id: str | None = Query(default=None, pattern=r'^[0-9]{1,40}$'),
    status: str | None = Query(default=None, pattern=r'^(active|inactive|converted|lost|indeterminate)$'),
):
    return dict(start=start,end=end,observed_at=observed_at,inactivity_hours=inactivity_hours,
                return_window_days=return_window_days,stage=stage,origin=origin,campaign_id=campaign_id,ad_id=ad_id,status=status)


async def _commercial_funnel_read(service, db, empresa_id, filters):
    if not commercial_funnel_dashboard_enabled():
        raise HTTPException(status_code=404, detail='Dashboard comercial indisponível.')
    try:
        return await service(db, empresa_id=empresa_id, **filters)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail='Etapa ou escopo comercial indisponível.') from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail='Filtro comercial inválido.') from exc


@router.get('/commercial-funnel')
async def commercial_funnel_dashboard_get(
    empresa_id: uuid.UUID,
    filters: dict = Depends(_commercial_funnel_filters),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    return await _commercial_funnel_read(commercial_funnel_dashboard,db,empresa_id,filters)


@router.get('/commercial-funnel/leads')
async def commercial_funnel_leads_get(
    empresa_id: uuid.UUID,
    filters: dict = Depends(_commercial_funnel_filters),
    bottleneck: str | None = Query(default=None,min_length=1,max_length=110,pattern=r'^inactive:[a-zA-Z0-9_:-]+$'),
    limit: int = Query(default=25,ge=1,le=100),
    offset: int = Query(default=0,ge=0,le=100000),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    return await _commercial_funnel_read(commercial_funnel_leads,db,empresa_id,
                                        filters|dict(bottleneck=bottleneck,limit=limit,offset=offset))


@router.get('/commercial-funnel/export')
async def commercial_funnel_export_get(
    empresa_id: uuid.UUID,
    filters: dict = Depends(_commercial_funnel_filters),
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    content = await _commercial_funnel_read(commercial_funnel_export,db,empresa_id,filters)
    return Response(content=content,media_type='text/csv; charset=utf-8',headers={
        'Content-Disposition':'attachment; filename="commercial-funnel-aggregated.csv"',
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def _attendance_quality_filters(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    observed_at: datetime | None = Query(default=None),
    response_window_hours: int = Query(default=24, ge=1, le=168),
    continuity_window_days: int = Query(default=7, ge=1, le=30),
    channel: str | None = Query(default=None, pattern=r'^(whatsapp|instagram|web|sms|telegram)$'),
    stage: str | None = Query(default=None, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_:-]+$'),
    ad_id: str | None = Query(default=None, min_length=1, max_length=40, pattern=r'^[0-9]+$'),
    offer_signal: str | None = Query(default=None, pattern=r'^(price|offer)$'),
    composition_version: str | None = Query(default=None, min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_.:-]+$'),
):
    return dict(start=start, end=end, observed_at=observed_at,
                response_window_hours=response_window_hours,
                continuity_window_days=continuity_window_days, channel=channel,
                stage=stage, ad_id=ad_id, offer_signal=offer_signal,
                composition_version=composition_version)


async def _attendance_quality_read(service, db, empresa_id, filters):
    if not attendance_quality_enabled():
        raise HTTPException(status_code=404, detail='Diagnóstico de qualidade indisponível.')
    try:
        return await service(db, empresa_id=empresa_id, **filters)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail='Diagnóstico de qualidade indisponível.') from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail='Filtro de qualidade inválido.') from exc


@router.get('/attendance-quality')
async def attendance_quality_get(
    empresa_id: uuid.UUID,
    filters: dict = Depends(_attendance_quality_filters),
    response: Response = None,
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    result = await _attendance_quality_read(attendance_quality_dashboard, db, empresa_id, filters)
    if response is not None:
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
    return result


@router.get('/attendance-quality/evidence')
async def attendance_quality_evidence_get(
    empresa_id: uuid.UUID,
    category: str = Query(..., pattern=r'^(question_without_observed_response|price_offer_without_observed_continuity)$'),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
    filters: dict = Depends(_attendance_quality_filters),
    response: Response = None,
    db: AsyncSession = Depends(get_db),
    _current_user: Usuario = Depends(require_tenant_access),
):
    if not attendance_quality_enabled():
        raise HTTPException(status_code=404, detail='Diagnóstico de qualidade indisponível.')
    try:
        result = await attendance_quality_drilldown(db, empresa_id=empresa_id, category=category,
                                                    limit=limit, offset=offset, **filters)
        if response is not None:
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail='Diagnóstico de qualidade indisponível.') from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail='Filtro de qualidade inválido.') from exc
