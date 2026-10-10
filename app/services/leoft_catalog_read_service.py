from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas_leoft_catalog import (
    CatalogEntryResponse,
    CatalogMediaResponse,
    CatalogMetaResponse,
    CatalogMetaCounts,
    CatalogSearchFilters,
    CatalogSearchRequest,
    CatalogSearchResponse,
    CatalogSearchResult,
    CatalogStates,
)
from scripts.import_leoft_catalog import normalize as normalize_catalog_text
from db.models import (
    LeoFTCatalogAlias,
    LeoFTCatalogEntry,
    LeoFTCatalogMedia,
    LeoFTCatalogSignature,
    LeoFTCatalogVersion,
)


@dataclass(frozen=True)
class CatalogContext:
    version: str
    source_sha256: str


async def _context(db: AsyncSession) -> CatalogContext:
    row = (await db.execute(
        select(LeoFTCatalogVersion.version, LeoFTCatalogVersion.source_hash)
        .where(LeoFTCatalogVersion.status == "imported")
        .order_by(LeoFTCatalogVersion.activated_at.desc().nullslast(), LeoFTCatalogVersion.created_at.desc())
        .limit(1)
    )).first()
    if row is None:
        raise LookupError("catalog_not_available")
    return CatalogContext(version=row.version, source_sha256=row.source_hash)


def _safe_path(value: str) -> str:
    value = str(value or "")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        raise ValueError("catalog contains unsafe relative path")
    return value


def _media_response(media: LeoFTCatalogMedia, catalog_id: str) -> CatalogMediaResponse:
    return CatalogMediaResponse(
        media_id=media.media_id,
        catalog_id=catalog_id,
        storage_path=_safe_path(media.storage_path),
        thumbnail_path=_safe_path(media.thumbnail_path) if media.thumbnail_path else None,
        sha256=media.sha256,
        media_type=media.media_type,
        format=media.format,
        mime_type=media.mime_type,
        storage_provider=media.storage_provider,
        file_size=media.file_size,
        width=media.width,
        height=media.height,
        duration_ms=media.duration_ms,
        metadata=media.media_metadata,
    )


def _entry_response(entry: LeoFTCatalogEntry, aliases: list[LeoFTCatalogAlias], media: list[LeoFTCatalogMedia], version: str) -> CatalogEntryResponse:
    return CatalogEntryResponse(
        catalog_id=entry.catalog_id,
        canonical_name=entry.canonical_name,
        normalized_name=entry.normalized_name,
        approval_status=entry.approval_status,
        confidence=entry.confidence,
        muscle_group=entry.muscle_group,
        equipment=entry.equipment,
        body_position=entry.body_position,
        variation=entry.variation,
        laterality=entry.laterality,
        grip=entry.grip,
        source=entry.source,
        source_metadata=entry.source_metadata,
        aliases=[a.alias for a in sorted(aliases, key=lambda item: item.normalized_alias)],
        media=[_media_response(m, entry.catalog_id) for m in sorted(media, key=lambda item: item.media_id)],
        catalog_version=version,
    )


async def _load_entry_parts(db: AsyncSession, entry_ids: list[Any]) -> tuple[dict[Any, list[LeoFTCatalogAlias]], dict[Any, list[LeoFTCatalogMedia]]]:
    if not entry_ids:
        return {}, {}
    aliases = (await db.execute(select(LeoFTCatalogAlias).where(LeoFTCatalogAlias.catalog_entry_id.in_(entry_ids)))).scalars().all()
    media = (await db.execute(select(LeoFTCatalogMedia).where(LeoFTCatalogMedia.catalog_entry_id.in_(entry_ids)))).scalars().all()
    alias_map: dict[Any, list[LeoFTCatalogAlias]] = {}
    media_map: dict[Any, list[LeoFTCatalogMedia]] = {}
    for item in aliases:
        alias_map.setdefault(item.catalog_entry_id, []).append(item)
    for item in media:
        media_map.setdefault(item.catalog_entry_id, []).append(item)
    return alias_map, media_map


async def get_meta(db: AsyncSession) -> CatalogMetaResponse:
    context = await _context(db)
    counts_row = (await db.execute(select(
        select(func.count()).select_from(LeoFTCatalogEntry).scalar_subquery().label("entries"),
        select(func.count()).select_from(LeoFTCatalogMedia).scalar_subquery().label("media"),
        select(func.count()).select_from(LeoFTCatalogAlias).scalar_subquery().label("aliases"),
        select(func.count()).select_from(LeoFTCatalogSignature).scalar_subquery().label("signatures"),
    ))).one()
    state_rows = (await db.execute(
        select(LeoFTCatalogEntry.approval_status, func.count())
        .group_by(LeoFTCatalogEntry.approval_status)
    )).all()
    states = {name: 0 for name in ("approved_by_ai", "approved_manually", "pending_review", "unidentified", "rejected", "duplicate")}
    states.update({str(name): count for name, count in state_rows if name in states})
    return CatalogMetaResponse(
        schema_version="1.0",
        catalog_version=context.version,
        source_sha256=context.source_sha256,
        counts=CatalogMetaCounts(**counts_row._mapping),
        states=CatalogStates(**states),
        signatures_available=counts_row.signatures > 0,
    )


async def get_entry(db: AsyncSession, catalog_id: str) -> CatalogEntryResponse | None:
    context = await _context(db)
    entry = (await db.execute(select(LeoFTCatalogEntry).where(LeoFTCatalogEntry.catalog_id == catalog_id))).scalar_one_or_none()
    if entry is None:
        return None
    aliases, media = await _load_entry_parts(db, [entry.id])
    return _entry_response(entry, aliases.get(entry.id, []), media.get(entry.id, []), context.version)


def _filter_conditions(filters: CatalogSearchFilters) -> list[Any]:
    conditions: list[Any] = []
    if filters.approval_status:
        conditions.append(LeoFTCatalogEntry.approval_status.in_(filters.approval_status))
    for field, value in (("muscle_group", filters.muscle_group), ("equipment", filters.equipment), ("body_position", filters.position), ("variation", filters.variation), ("laterality", filters.laterality), ("grip", filters.grip)):
        if value:
            conditions.append(getattr(LeoFTCatalogEntry, field) == value)
    return conditions


async def search_catalog(db: AsyncSession, request: CatalogSearchRequest) -> CatalogSearchResponse:
    context = await _context(db)
    filters = _filter_conditions(request.filters)
    if request.retrieval_mode == "complete_candidates":
        total = (await db.execute(
            select(func.count()).select_from(LeoFTCatalogEntry).where(*filters)
        )).scalar_one()
        entries = (await db.execute(
            select(LeoFTCatalogEntry)
            .where(*filters)
            .order_by(LeoFTCatalogEntry.catalog_id.collate("C").asc())
            .offset(request.offset)
            .limit(request.limit)
        )).scalars().all()
        alias_map, media_map = await _load_entry_parts(db, [entry.id for entry in entries])
        results = []
        for entry in entries:
            entry_aliases = alias_map.get(entry.id, [])
            results.append(CatalogSearchResult(
                entry=_entry_response(entry, entry_aliases, media_map.get(entry.id, []), context.version),
                relevant_aliases=[a.alias for a in sorted(entry_aliases, key=lambda item: item.normalized_alias)],
            ))
        return CatalogSearchResponse(
            catalog_version=context.version,
            query=request.query,
            limit=request.limit,
            offset=request.offset,
            total=total,
            results=results,
        )

    query = normalize_catalog_text(request.query)
    alias = LeoFTCatalogAlias
    e = LeoFTCatalogEntry
    alias_exact = exists(select(1).where(and_(alias.catalog_entry_id == e.id, alias.normalized_alias == query)))
    alias_prefix = exists(select(1).where(and_(alias.catalog_entry_id == e.id, alias.normalized_alias.like(f"{query}%"))))
    text_match = or_(e.search_document.op("@@")(func.plainto_tsquery("simple", query)), e.canonical_name.ilike(f"%{query}%"), exists(select(1).where(and_(alias.catalog_entry_id == e.id, alias.alias.ilike(f"%{query}%")))))
    attribute_match = or_(*[getattr(e, field).ilike(f"%{query}%") for field in ("muscle_group", "equipment", "body_position", "variation", "laterality", "grip")])
    category = case(
        (e.catalog_id == request.query.strip(), 1),
        (e.normalized_name == query, 2),
        (alias_exact, 3),
        (e.normalized_name.like(f"{query}%"), 4),
        (alias_prefix, 5),
        (text_match, 6),
        (attribute_match, 7),
        else_=99,
    ).label("match_category")
    score = case((e.catalog_id == request.query.strip(), 100.0), (e.normalized_name == query, 95.0), (alias_exact, 90.0), else_=literal(50.0)).label("match_score")
    predicate = or_(e.catalog_id == request.query.strip(), e.normalized_name.like(f"{query}%"), alias_exact, alias_prefix, text_match, attribute_match)
    stmt = select(e, category, score).where(and_(predicate, *filters)).order_by(category.asc(), score.desc(), e.normalized_name.asc(), e.catalog_id.asc())
    rows = (await db.execute(stmt)).all()
    total = len(rows)
    selected = rows[request.offset:request.offset + request.limit]
    entries = [row[0] for row in selected]
    alias_map, media_map = await _load_entry_parts(db, [entry.id for entry in entries])
    results = []
    for entry, match_category, match_score in selected:
        entry_aliases = alias_map.get(entry.id, [])
        relevant = [a.alias for a in entry_aliases if query in a.normalized_alias or query in entry.normalized_name]
        results.append(CatalogSearchResult(match_type=("catalog_id_exact", "canonical_name_exact", "alias_exact", "canonical_name_prefix", "alias_prefix", "textual", "attribute")[match_category - 1] if 1 <= match_category <= 7 else "textual", match_score=float(match_score), entry=_entry_response(entry, entry_aliases, media_map.get(entry.id, []), context.version), relevant_aliases=sorted(set(relevant), key=normalize_catalog_text)))
    return CatalogSearchResponse(catalog_version=context.version, query=request.query, limit=request.limit, offset=request.offset, total=total, results=results)


async def get_media(db: AsyncSession, media_id: str) -> CatalogMediaResponse | None:
    context = await _context(db)
    row = (await db.execute(select(LeoFTCatalogMedia, LeoFTCatalogEntry).join(LeoFTCatalogEntry, LeoFTCatalogEntry.id == LeoFTCatalogMedia.catalog_entry_id).where(LeoFTCatalogMedia.media_id == media_id))).first()
    if row is None:
        return None
    media, entry = row
    return _media_response(media, entry.catalog_id).model_copy(update={"approval_status": entry.approval_status, "catalog_version": context.version})


async def validate_association(db: AsyncSession, catalog_id: str, media_id: str) -> tuple[bool, bool, bool, str]:
    context = await _context(db)
    entry_exists = (await db.execute(select(LeoFTCatalogEntry.id).where(LeoFTCatalogEntry.catalog_id == catalog_id))).scalar_one_or_none() is not None
    media_row = (await db.execute(select(LeoFTCatalogMedia.catalog_entry_id).where(LeoFTCatalogMedia.media_id == media_id))).scalar_one_or_none()
    if not entry_exists or media_row is None:
        return False, entry_exists, media_row is not None, context.version
    linked_entry = (await db.execute(select(LeoFTCatalogEntry.catalog_id).where(LeoFTCatalogEntry.id == media_row))).scalar_one()
    return linked_entry == catalog_id, True, True, context.version
