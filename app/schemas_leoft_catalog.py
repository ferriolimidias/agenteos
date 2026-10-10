from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CatalogMediaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    catalog_id: str
    storage_path: str
    thumbnail_path: str | None = None
    sha256: str
    media_type: str | None = None
    format: str | None = None
    mime_type: str | None = None
    storage_provider: str | None = None
    file_size: int | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] | None = None
    approval_status: str | None = None
    catalog_version: str | None = None


class CatalogEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_id: str
    canonical_name: str
    normalized_name: str
    approval_status: str
    confidence: Decimal | float | None = None
    muscle_group: str | None = None
    equipment: str | None = None
    body_position: str | None = None
    variation: str | None = None
    laterality: str | None = None
    grip: str | None = None
    source: str | None = None
    source_metadata: dict[str, Any] | None = None
    aliases: list[str] = Field(default_factory=list)
    media: list[CatalogMediaResponse] = Field(default_factory=list)
    catalog_version: str


class CatalogMetaCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: int
    media: int
    aliases: int
    signatures: int


class CatalogStates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_by_ai: int = 0
    approved_manually: int = 0
    pending_review: int = 0
    unidentified: int = 0
    rejected: int = 0
    duplicate: int = 0


class CatalogMetaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    catalog_version: str
    source_sha256: str
    counts: CatalogMetaCounts
    states: CatalogStates
    signatures_available: bool


class CatalogSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_status: list[str] = Field(default_factory=list)
    muscle_group: str | None = None
    equipment: str | None = None
    position: str | None = None
    variation: str | None = None
    laterality: str | None = None
    grip: str | None = None


class CatalogSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)
    filters: CatalogSearchFilters = Field(default_factory=CatalogSearchFilters)
    retrieval_mode: Literal["ranked_search", "complete_candidates"] = "ranked_search"

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("query must not be blank")
        return value


class CatalogSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_type: str | None = None
    match_score: float | None = None
    entry: CatalogEntryResponse
    relevant_aliases: list[str] = Field(default_factory=list)


class CatalogSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_version: str
    query: str
    limit: int
    offset: int
    total: int
    results: list[CatalogSearchResult]


class CatalogAssociationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_id: str = Field(min_length=1)
    media_id: str = Field(min_length=1)


class CatalogAssociationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    catalog_id: str
    media_id: str
    catalog_version: str
