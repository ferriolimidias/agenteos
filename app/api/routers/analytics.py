from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
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
