from __future__ import annotations

from datetime import date, timedelta
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_tenant_access
from app.services.commercial_analytics import attribution_quality, event_signal_status, funnel, performance
from app.services.meta.canonical_conversion_shadow import shadow_status
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
