from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas_leoft_catalog import (
    CatalogAssociationRequest,
    CatalogAssociationResponse,
    CatalogEntryResponse,
    CatalogMediaResponse,
    CatalogMetaResponse,
    CatalogSearchRequest,
    CatalogSearchResponse,
)
from app.services.leoft_catalog_read_service import (
    get_entry,
    get_media,
    get_meta,
    search_catalog,
    validate_association,
)
from app.services.leoft_internal_auth import require_hermes_catalog_read_hmac
from db.database import get_db

router = APIRouter(prefix="/catalog/v1", tags=["LeoFT Catalog Read v1"])


@router.get("/meta", response_model=CatalogMetaResponse, dependencies=[Depends(require_hermes_catalog_read_hmac)])
async def catalog_meta(db: AsyncSession = Depends(get_db)) -> CatalogMetaResponse:
    try:
        return await get_meta(db)
    except LookupError as exc:
        raise HTTPException(503, "catalog_not_available") from exc


@router.get("/entries/{catalog_id}", response_model=CatalogEntryResponse, dependencies=[Depends(require_hermes_catalog_read_hmac)])
async def catalog_entry(catalog_id: str, db: AsyncSession = Depends(get_db)) -> CatalogEntryResponse:
    try:
        result = await get_entry(db, catalog_id)
    except LookupError as exc:
        raise HTTPException(503, "catalog_not_available") from exc
    if result is None:
        raise HTTPException(404, "catalog_entry_not_found")
    return result


@router.post("/search", response_model=CatalogSearchResponse, dependencies=[Depends(require_hermes_catalog_read_hmac)])
async def catalog_search(body: CatalogSearchRequest, db: AsyncSession = Depends(get_db)) -> CatalogSearchResponse:
    try:
        return await search_catalog(db, body)
    except LookupError as exc:
        raise HTTPException(503, "catalog_not_available") from exc


@router.get("/media/{media_id}", response_model=CatalogMediaResponse, dependencies=[Depends(require_hermes_catalog_read_hmac)])
async def catalog_media(media_id: str, db: AsyncSession = Depends(get_db)) -> CatalogMediaResponse:
    try:
        result = await get_media(db, media_id)
    except LookupError as exc:
        raise HTTPException(503, "catalog_not_available") from exc
    if result is None:
        raise HTTPException(404, "catalog_media_not_found")
    return result


@router.post("/associations/validate", response_model=CatalogAssociationResponse, dependencies=[Depends(require_hermes_catalog_read_hmac)])
async def catalog_association(body: CatalogAssociationRequest, db: AsyncSession = Depends(get_db)) -> CatalogAssociationResponse:
    try:
        valid, entry_exists, media_exists, version = await validate_association(db, body.catalog_id, body.media_id)
    except LookupError as exc:
        raise HTTPException(503, "catalog_not_available") from exc
    if not entry_exists:
        raise HTTPException(404, "catalog_entry_not_found")
    if not media_exists:
        raise HTTPException(404, "catalog_media_not_found")
    return CatalogAssociationResponse(valid=valid, catalog_id=body.catalog_id, media_id=body.media_id, catalog_version=version)
