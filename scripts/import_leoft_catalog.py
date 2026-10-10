"""Validate and import the official LeoFT catalog into PostgreSQL."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COUNT_KEYS = ("entries", "media", "aliases", "signatures")
STATE_KEYS = ("approved_by_ai", "approved_manually", "pending_review", "unidentified", "rejected", "duplicate")
ALLOWED_APPROVAL_STATUSES = set(STATE_KEYS)
SOURCE_SIGNATURE_KEYS = (
    "signatures",
    "signature",
    "media_signature",
    "content_signature",
    "fingerprint",
    "phash",
    "perceptual_hash",
)


def empty_counts() -> dict[str, Any]:
    return {key: 0 for key in COUNT_KEYS} | {"states": {key: 0 for key in STATE_KEYS}}


def normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def required_id(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not ID_RE.fullmatch(result):
        raise ValueError(f"{label} must be a non-empty stable identifier")
    return result


def sha256(value: Any, label: str) -> str:
    result = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(result):
        raise ValueError(f"{label} must be a 64-character SHA-256 hex digest")
    return result


def _canonicalize_aliases(values: Any, label: str) -> tuple[list[Any], int]:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be an array")
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        alias_value = value.get("alias") if isinstance(value, dict) else value
        normalized = normalize(alias_value)
        if not normalized:
            raise ValueError(f"{label} contains an empty alias")
        if normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return result, len(values)


def _source_signatures(record: dict[str, Any], index: int, *, allow_missing: bool) -> list[dict[str, Any]]:
    for key in SOURCE_SIGNATURE_KEYS:
        value = record.get(key)
        if value in (None, "", [], {}):
            continue
        candidates = value if isinstance(value, list) else [value]
        if not all(isinstance(item, dict) for item in candidates):
            raise ValueError(f"records[{index}].{key} must contain signature objects")
        return [dict(item) for item in candidates]
    if allow_missing:
        return []
    raise ValueError(f"records[{index}] has no catalog signature; no official deterministic generator is available")


def _adapt_source_record(record: Any, index: int, *, allow_missing_signatures: bool) -> tuple[dict[str, Any], int]:
    if not isinstance(record, dict):
        raise ValueError(f"records[{index}] must be an object")
    catalog_id = required_id(record.get("catalog_id"), f"records[{index}].catalog_id")
    canonical_name = str(record.get("canonical_name_ptbr") or "").strip()
    if not canonical_name:
        raise ValueError(f"records[{index}].canonical_name_ptbr is required")
    aliases, raw_alias_count = _canonicalize_aliases(record.get("aliases", []), f"records[{index}].aliases")
    media_items = record.get("media")
    if not isinstance(media_items, list):
        raise ValueError(f"records[{index}].media must be an array")
    media: list[dict[str, Any]] = []
    for media_index, item in enumerate(media_items):
        if not isinstance(item, dict):
            raise ValueError(f"records[{index}].media[{media_index}] must be an object")
        storage_path = item.get("relative_path")
        if "storage_path" in item:
            storage_path = item["storage_path"]
        media.append({
            "media_id": item.get("media_id"),
            "media_type": str(item["mime_type"]).split("/", 1)[0] if item.get("mime_type") else None,
            "format": item.get("format"),
            "mime_type": item.get("mime_type"),
            "storage_path": storage_path,
            "thumbnail_path": item.get("thumbnail_path"),
            "sha256": item.get("sha256"),
            "file_size": item.get("file_size"),
            "width": item.get("width"),
            "height": item.get("height"),
            "duration_ms": round(float(item["duration_seconds"]) * 1000) if item.get("duration_seconds") is not None else None,
            "metadata": {"catalog_id": item.get("catalog_id"), "is_primary": item.get("is_primary")},
        })
    signatures = _source_signatures(record, index, allow_missing=allow_missing_signatures)
    entry = {
        "catalog_id": catalog_id,
        "canonical_name": canonical_name,
        "normalized_name": normalize(canonical_name),
        "approval_status": record.get("approval_status"),
        "confidence": record.get("confidence"),
        "muscle_group": record.get("primary_muscle_group"),
        "equipment": record.get("equipment"),
        "body_position": record.get("position"),
        "variation": record.get("variation"),
        "laterality": record.get("laterality"),
        "grip": record.get("grip"),
        "aliases": aliases,
        "media": media,
        "signatures": signatures,
        "source_metadata": {
            "canonical_name_en": record.get("canonical_name_en"),
            "movement_pattern": record.get("movement_pattern"),
            "secondary_muscle_groups": record.get("secondary_muscle_groups", []),
            "eligible_for_automatic_reuse": record.get("eligible_for_automatic_reuse"),
            "approval_method": record.get("approval_method"),
            "evidence": record.get("evidence", []),
        },
    }
    return entry, raw_alias_count


def adapt_source_catalog(
    document: Any,
    source_hash: str,
    *,
    allow_missing_signatures: bool = False,
) -> tuple[dict[str, Any], dict[str, int]]:
    if not isinstance(document, dict) or not isinstance(document.get("records"), list):
        raise ValueError("source catalog must be an object with a records array")
    entries: list[dict[str, Any]] = []
    raw_aliases = 0
    for index, record in enumerate(document["records"]):
        entry, raw_count = _adapt_source_record(record, index, allow_missing_signatures=allow_missing_signatures)
        entries.append(entry)
        raw_aliases += raw_count
    version = required_id(document.get("catalog_version"), "catalog_version")
    canonical = {
        "event_type": "leoft.catalog.import",
        "schema_version": "1.0",
        "catalog_version": version,
        "version": version,
        "source_sha256": source_hash,
        "generated_at": document.get("generated_at"),
        "source": "hermes",
        "entries": entries,
    }
    counters = validate_catalog_document(
        canonical,
        source_hash=source_hash,
        enforce_envelope=False,
        allow_missing_signatures=allow_missing_signatures,
    )
    canonical["expected_counts"] = {**counters, "aliases": counters["aliases"], "states": counters["states"].copy()}
    canonical["raw_counts"] = {"aliases": raw_aliases}
    return canonical, {**counters, "raw_aliases": raw_aliases}


def validate_catalog_document(
    document: Any,
    *,
    source_hash: str | None = None,
    enforce_envelope: bool = False,
    allow_missing_signatures: bool = False,
) -> dict[str, int]:
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        raise ValueError("catalog must be an object with an entries array")
    if enforce_envelope and str(document.get("event_type") or "") != "leoft.catalog.import":
        raise ValueError("event_type must be leoft.catalog.import")
    schema_version = str(document.get("schema_version") or document.get("schemaVersion") or "1.0")
    if enforce_envelope and schema_version != "1.0":
        raise ValueError("schema_version 1.0 is required")
    version = required_id(document.get("catalog_version") or document.get("version"), "catalog_version")
    document["version"] = version
    declared_source_hash = str(document.get("source_sha256") or source_hash or "").lower()
    if not SHA256_RE.fullmatch(declared_source_hash):
        raise ValueError("source_sha256 must be a 64-character SHA-256 hex digest")
    if source_hash is not None and declared_source_hash != source_hash.lower():
        raise ValueError("source_sha256 does not match the received file")
    if enforce_envelope and str(document.get("source") or "") != "hermes":
        raise ValueError("source hermes is required")
    if enforce_envelope and not str(document.get("generated_at") or "").strip():
        raise ValueError("generated_at is required")

    seen_catalog_ids: set[str] = set()
    seen_media_ids: set[str] = set()
    counters = empty_counts()
    for index, entry in enumerate(document["entries"]):
        if not isinstance(entry, dict):
            raise ValueError(f"entries[{index}] must be an object")
        catalog_id = required_id(entry.get("catalog_id"), f"entries[{index}].catalog_id")
        if catalog_id in seen_catalog_ids:
            raise ValueError(f"duplicate catalog_id: {catalog_id}")
        seen_catalog_ids.add(catalog_id)
        if not str(entry.get("canonical_name") or "").strip():
            raise ValueError(f"entries[{index}].canonical_name is required")
        status = str(entry.get("approval_status") or "").strip()
        if status not in ALLOWED_APPROVAL_STATUSES:
            raise ValueError(f"entries[{index}].approval_status is invalid")
        counters["states"][status] += 1
        aliases = entry.get("aliases", [])
        media = entry.get("media", [])
        signatures = entry.get("signatures", [])
        if not all(isinstance(items, list) for items in (aliases, media, signatures)):
            raise ValueError(f"entries[{index}] aliases/media/signatures must be arrays")
        seen_aliases: set[str] = set()
        for alias in aliases:
            alias_value = alias.get("alias") if isinstance(alias, dict) else alias
            normalized_alias = normalize(alias_value)
            if not normalized_alias:
                raise ValueError(f"entries[{index}] contains an empty alias")
            if normalized_alias in seen_aliases:
                raise ValueError(f"duplicate alias in catalog entry: {catalog_id}")
            seen_aliases.add(normalized_alias)
        for media_index, item in enumerate(media):
            if not isinstance(item, dict):
                raise ValueError(f"entries[{index}].media[{media_index}] must be an object")
            media_id = required_id(item.get("media_id"), f"entries[{index}].media[{media_index}].media_id")
            if media_id in seen_media_ids:
                raise ValueError(f"duplicate media_id: {media_id}")
            seen_media_ids.add(media_id)
            storage_path = str(item.get("storage_path") or "").strip()
            if not storage_path or storage_path.startswith(("/", "\\")) or ".." in storage_path.replace("\\", "/").split("/"):
                raise ValueError(f"entries[{index}].media[{media_index}].storage_path must be relative")
            sha256(item.get("sha256"), f"entries[{index}].media[{media_index}].sha256")
        seen_signature_versions: set[str] = set()
        if not signatures and not allow_missing_signatures:
            raise ValueError(f"entries[{index}] must contain one or more signatures")
        for signature_index, item in enumerate(signatures):
            if not isinstance(item, dict):
                raise ValueError(f"entries[{index}].signatures[{signature_index}] must be an object")
            required_id(item.get("signature_key"), f"entries[{index}].signatures[{signature_index}].signature_key")
            signature_version = required_id(item.get("signature_version"), f"entries[{index}].signatures[{signature_index}].signature_version")
            if signature_version in seen_signature_versions:
                raise ValueError(f"duplicate signature version in catalog entry: {catalog_id}")
            seen_signature_versions.add(signature_version)
            if not isinstance(item.get("signature_payload"), dict):
                raise ValueError(f"entries[{index}].signatures[{signature_index}].signature_payload must be an object")
        counters["entries"] += 1
        counters["media"] += len(media)
        counters["aliases"] += len(aliases)
        counters["signatures"] += len(signatures)
    expected = document.get("expected_counts")
    if enforce_envelope and not isinstance(expected, dict):
        raise ValueError("expected_counts is required")
    if expected is not None:
        if not isinstance(expected, dict):
            raise ValueError("expected_counts must be an object")
        for key in COUNT_KEYS:
            if key in expected:
                value = expected[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"expected_counts.{key} must be a non-negative integer")
                if value != counters[key]:
                    raise ValueError(f"count mismatch for {key}")
        expected_states = expected.get("states")
        if enforce_envelope and not isinstance(expected_states, dict):
            raise ValueError("expected_counts.states is required")
        if expected_states is not None:
            if not isinstance(expected_states, dict):
                raise ValueError("expected_counts.states must be an object")
            for key in STATE_KEYS:
                if key not in expected_states:
                    if enforce_envelope:
                        raise ValueError(f"expected_counts.states.{key} is required")
                    continue
                value = expected_states[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"expected_counts.states.{key} must be a non-negative integer")
                if value != counters["states"][key]:
                    raise ValueError(f"count mismatch for states.{key}")
            unexpected = set(expected_states) - set(STATE_KEYS)
            if unexpected:
                raise ValueError("expected_counts.states contains unknown keys")
    if sum(counters["states"].values()) != counters["entries"]:
        raise ValueError("state counts must sum to entries")
    if allow_missing_signatures and counters["signatures"] not in (0, counters["entries"]):
        raise ValueError("signature count must be zero or equal to entries")
    return counters


def load_catalog(
    path: Path,
    *,
    allow_missing_signatures: bool = False,
) -> tuple[dict[str, Any], str, dict[str, int]]:
    raw = path.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 JSON: {exc}") from exc
    if "records" in document and "entries" not in document:
        document, counters = adapt_source_catalog(
            document,
            source_hash,
            allow_missing_signatures=allow_missing_signatures,
        )
        return document, source_hash, counters
    document.setdefault("catalog_version", document.get("version"))
    document["source_sha256"] = source_hash
    counters = validate_catalog_document(
        document,
        source_hash=source_hash,
        enforce_envelope=False,
        allow_missing_signatures=allow_missing_signatures,
    )
    return document, source_hash, counters


async def import_catalog_document(
    document: dict[str, Any],
    source_sha256: str,
    conn: Any,
    *,
    source_filename: str = "hermes_catalog_payload",
    allow_missing_signatures: bool = False,
) -> dict[str, Any]:
    from sqlalchemy import text

    counters = validate_catalog_document(
        document,
        source_hash=source_sha256,
        enforce_envelope=True,
        allow_missing_signatures=allow_missing_signatures,
    )
    idempotency_key = f"catalog:{document['version']}:{source_sha256}"
    existing = await conn.execute(text("SELECT id, status FROM leoft_catalog_import_runs WHERE idempotency_key = :key"), {"key": idempotency_key})
    existing_row = existing.first()
    result: dict[str, Any] = {"version": document["version"], "source_sha256": source_sha256, "counts": counters, "dry_run": False}
    if existing_row:
        return {**result, "idempotent": True, "import_run_id": str(existing_row.id), "status": existing_row.status}

    now = datetime.utcnow()
    version_result = await conn.execute(text("""
        INSERT INTO leoft_catalog_versions
          (version, source_hash, record_count, media_count, alias_count, signature_count, status, metadata)
        VALUES (:version, :source_hash, :entries, :media, :aliases, :signatures, 'importing', CAST(:metadata AS jsonb))
        ON CONFLICT (version) DO UPDATE SET source_hash = leoft_catalog_versions.source_hash
        RETURNING id, source_hash
    """), {"version": document["version"], "source_hash": source_sha256, **counters, "entries": counters["entries"], "metadata": json.dumps(document.get("metadata", {}))})
    version_row = version_result.first()
    if version_row.source_hash != source_sha256:
        raise ValueError("catalog version already exists with a different source hash")
    run_id = (await conn.execute(text("""
        INSERT INTO leoft_catalog_import_runs
          (idempotency_key, catalog_version_id, source_filename, source_sha256, status, counters, started_at)
        VALUES (:key, :version_id, :filename, :source_hash, 'running', CAST(:counters AS jsonb), :started_at)
        RETURNING id
    """), {"key": idempotency_key, "version_id": version_row.id, "filename": source_filename, "source_hash": source_sha256, "counters": json.dumps(counters), "started_at": now})).scalar_one()

    for entry in document["entries"]:
        params = {"catalog_id": entry["catalog_id"], "canonical_name": str(entry["canonical_name"]).strip(), "normalized_name": normalize(entry["canonical_name"]), "approval_status": entry["approval_status"], "confidence": entry.get("confidence"), "source": entry.get("source", "hermes"), "source_metadata": json.dumps(entry.get("source_metadata", {})), "muscle_group": entry.get("muscle_group"), "equipment": entry.get("equipment"), "body_position": entry.get("body_position"), "variation": entry.get("variation"), "laterality": entry.get("laterality"), "grip": entry.get("grip")}
        entry_id = (await conn.execute(text("""
            INSERT INTO leoft_catalog_entries (catalog_id, canonical_name, normalized_name, muscle_group, equipment, body_position, variation, laterality, grip, approval_status, confidence, source, source_metadata)
            VALUES (:catalog_id, :canonical_name, :normalized_name, :muscle_group, :equipment, :body_position, :variation, :laterality, :grip, :approval_status, :confidence, :source, CAST(:source_metadata AS jsonb))
            ON CONFLICT (catalog_id) DO UPDATE SET canonical_name=EXCLUDED.canonical_name, normalized_name=EXCLUDED.normalized_name, muscle_group=EXCLUDED.muscle_group, equipment=EXCLUDED.equipment, body_position=EXCLUDED.body_position, variation=EXCLUDED.variation, laterality=EXCLUDED.laterality, grip=EXCLUDED.grip, approval_status=EXCLUDED.approval_status, confidence=EXCLUDED.confidence, source=EXCLUDED.source, source_metadata=EXCLUDED.source_metadata
            RETURNING id
        """), params)).scalar_one()
        for alias in entry.get("aliases", []):
            value = alias.get("alias") if isinstance(alias, dict) else alias
            await conn.execute(text("INSERT INTO leoft_catalog_aliases (catalog_entry_id, alias, normalized_alias) VALUES (:entry_id, :alias, :normalized) ON CONFLICT (catalog_entry_id, normalized_alias) DO UPDATE SET alias=EXCLUDED.alias"), {"entry_id": entry_id, "alias": str(value).strip(), "normalized": normalize(value)})
        for media in entry.get("media", []):
            await conn.execute(text("""INSERT INTO leoft_catalog_media (media_id, catalog_entry_id, media_type, format, mime_type, storage_provider, storage_path, thumbnail_path, sha256, file_size, width, height, duration_ms, metadata) VALUES (:media_id, :entry_id, :media_type, :format, :mime_type, :storage_provider, :storage_path, :thumbnail_path, :sha256, :file_size, :width, :height, :duration_ms, CAST(:metadata AS jsonb)) ON CONFLICT (media_id) DO UPDATE SET catalog_entry_id=EXCLUDED.catalog_entry_id, media_type=EXCLUDED.media_type, format=EXCLUDED.format, mime_type=EXCLUDED.mime_type, storage_provider=EXCLUDED.storage_provider, storage_path=EXCLUDED.storage_path, thumbnail_path=EXCLUDED.thumbnail_path, sha256=EXCLUDED.sha256, file_size=EXCLUDED.file_size, width=EXCLUDED.width, height=EXCLUDED.height, duration_ms=EXCLUDED.duration_ms, metadata=EXCLUDED.metadata"""), {"media_id": media["media_id"], "entry_id": entry_id, **{key: media.get(key) for key in ("media_type", "format", "mime_type", "storage_provider", "storage_path", "thumbnail_path", "sha256", "file_size", "width", "height", "duration_ms")}, "metadata": json.dumps(media.get("metadata", {}))})
        for signature in entry.get("signatures", []):
            await conn.execute(text("""INSERT INTO leoft_catalog_signatures (catalog_entry_id, signature_key, signature_version, normalized_name, muscle_group, equipment, body_position, variation, laterality, grip, signature_payload) VALUES (:entry_id, :signature_key, :signature_version, :normalized_name, :muscle_group, :equipment, :body_position, :variation, :laterality, :grip, CAST(:payload AS jsonb)) ON CONFLICT (catalog_entry_id, signature_version) DO UPDATE SET signature_key=EXCLUDED.signature_key, normalized_name=EXCLUDED.normalized_name, muscle_group=EXCLUDED.muscle_group, equipment=EXCLUDED.equipment, body_position=EXCLUDED.body_position, variation=EXCLUDED.variation, laterality=EXCLUDED.laterality, grip=EXCLUDED.grip, signature_payload=EXCLUDED.signature_payload"""), {"entry_id": entry_id, "signature_key": signature["signature_key"], "signature_version": signature["signature_version"], "normalized_name": signature.get("normalized_name", params["normalized_name"]), "muscle_group": signature.get("muscle_group", entry.get("muscle_group")), "equipment": signature.get("equipment", entry.get("equipment")), "body_position": signature.get("body_position", entry.get("body_position")), "variation": signature.get("variation", entry.get("variation")), "laterality": signature.get("laterality", entry.get("laterality")), "grip": signature.get("grip", entry.get("grip")), "payload": json.dumps(signature["signature_payload"])})
    await conn.execute(text("UPDATE leoft_catalog_versions SET status='imported', activated_at=now() WHERE id=:id"), {"id": version_row.id})
    await conn.execute(text("UPDATE leoft_catalog_import_runs SET status='succeeded', finished_at=now() WHERE id=:id"), {"id": run_id})
    return {**result, "idempotent": False, "status": "succeeded", "import_run_id": str(run_id)}


async def _project_catalog_import(document: dict[str, Any], conn: Any) -> dict[str, Any]:
    from sqlalchemy import bindparam, text

    idempotency_key = f"catalog:{document['version']}:{document['source_sha256']}"
    existing_run = (await conn.execute(
        text("SELECT 1 FROM leoft_catalog_import_runs WHERE idempotency_key = :key"),
        {"key": idempotency_key},
    )).first()
    if existing_run:
        return {
            "inserts": {key: 0 for key in ("entries", "media", "aliases", "signatures")},
            "updates": {key: 0 for key in ("entries", "media", "aliases", "signatures")},
            "ignored": 0,
            "conflicts": 0,
            "idempotent": True,
        }

    ids = [entry["catalog_id"] for entry in document["entries"]]
    media_ids = [media["media_id"] for entry in document["entries"] for media in entry.get("media", [])]
    entry_count = (await conn.execute(
        text("SELECT count(*) FROM leoft_catalog_entries WHERE catalog_id IN :ids").bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )).scalar_one()
    media_count = (await conn.execute(
        text("SELECT count(*) FROM leoft_catalog_media WHERE media_id IN :ids").bindparams(bindparam("ids", expanding=True)),
        {"ids": media_ids},
    )).scalar_one() if media_ids else 0
    alias_count = (await conn.execute(text("""
        SELECT count(*) FROM leoft_catalog_aliases a
        JOIN leoft_catalog_entries e ON e.id = a.catalog_entry_id
        WHERE e.catalog_id IN :ids
    """).bindparams(bindparam("ids", expanding=True)), {"ids": ids})).scalar_one()
    signature_count = (await conn.execute(text("""
        SELECT count(*) FROM leoft_catalog_signatures s
        JOIN leoft_catalog_entries e ON e.id = s.catalog_entry_id
        WHERE e.catalog_id IN :ids
    """).bindparams(bindparam("ids", expanding=True)), {"ids": ids})).scalar_one()
    counts = {
        "entries": entry_count,
        "media": media_count,
        "aliases": alias_count,
        "signatures": signature_count,
    }
    totals = {
        "entries": len(ids),
        "media": len(media_ids),
        "aliases": sum(len(entry.get("aliases", [])) for entry in document["entries"]),
        "signatures": sum(len(entry.get("signatures", [])) for entry in document["entries"]),
    }
    return {
        "inserts": {key: totals[key] - counts[key] for key in totals},
        "updates": counts,
        "ignored": 0,
        "conflicts": 0,
    }


async def import_catalog(
    path: Path,
    *,
    dry_run: bool,
    allow_missing_signatures: bool = False,
) -> dict[str, Any]:
    document, source_hash, counters = load_catalog(
        path,
        allow_missing_signatures=allow_missing_signatures,
    )
    result: dict[str, Any] = {
        "version": document["version"],
        "source_sha256": source_hash,
        "counts": counters,
        "dry_run": dry_run,
        "warnings": (["signatures absent in explicit initial load; matching by signature remains pending"]
                     if allow_missing_signatures and counters["signatures"] == 0 else []),
    }
    from sqlalchemy import text
    from db.database import engine

    if dry_run:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                projection = await _project_catalog_import(document, conn)
                result["idempotent"] = projection.pop("idempotent", False)
                result["projections"] = projection
                return result
            finally:
                await transaction.rollback()

    idempotency_key = f"catalog:{document['version']}:{source_hash}"
    now = datetime.utcnow()
    async with engine.begin() as conn:
        existing = await conn.execute(text("SELECT id, status FROM leoft_catalog_import_runs WHERE idempotency_key = :key"), {"key": idempotency_key})
        existing_row = existing.first()
        if existing_row:
            return {**result, "idempotent": True, "import_run_id": str(existing_row.id), "status": existing_row.status}

        version_result = await conn.execute(text("""
            INSERT INTO leoft_catalog_versions
              (version, source_hash, record_count, media_count, alias_count, signature_count, status, metadata)
              VALUES (:version, :source_hash, :entries, :media, :aliases, :signatures, 'importing', CAST(:metadata AS jsonb))
            ON CONFLICT (version) DO UPDATE SET
              source_hash = leoft_catalog_versions.source_hash
            RETURNING id, source_hash
            """), {"version": document["version"], "source_hash": source_hash, **counters, "entries": counters["entries"], "metadata": json.dumps(document.get("metadata", {}))})
        version_row = version_result.first()
        if version_row.source_hash != source_hash:
            raise ValueError("catalog version already exists with a different source hash")
        version_id = version_row.id
        run_result = await conn.execute(text("""
            INSERT INTO leoft_catalog_import_runs
              (idempotency_key, catalog_version_id, source_filename, source_sha256, status, counters, started_at)
            VALUES (:key, :version_id, :filename, :source_hash, 'running', CAST(:counters AS jsonb), :started_at)
            RETURNING id
        """), {"key": idempotency_key, "version_id": version_id, "filename": path.name, "source_hash": source_hash, "counters": json.dumps(counters), "started_at": now})
        run_id = run_result.scalar_one()

        for entry in document["entries"]:
            entry_params = {"catalog_id": required_id(entry["catalog_id"], "catalog_id"), "canonical_name": str(entry["canonical_name"]).strip(), "normalized_name": normalize(entry["canonical_name"]), "approval_status": str(entry["approval_status"]).strip(), "confidence": entry.get("confidence"), "source": entry.get("source"), "source_metadata": json.dumps(entry.get("source_metadata", {})), "muscle_group": entry.get("muscle_group"), "equipment": entry.get("equipment"), "body_position": entry.get("body_position"), "variation": entry.get("variation"), "laterality": entry.get("laterality"), "grip": entry.get("grip")}
            entry_id = (await conn.execute(text("""
                INSERT INTO leoft_catalog_entries (catalog_id, canonical_name, normalized_name, muscle_group, equipment, body_position, variation, laterality, grip, approval_status, confidence, source, source_metadata)
                VALUES (:catalog_id, :canonical_name, :normalized_name, :muscle_group, :equipment, :body_position, :variation, :laterality, :grip, :approval_status, :confidence, :source, CAST(:source_metadata AS jsonb))
                ON CONFLICT (catalog_id) DO UPDATE SET canonical_name = EXCLUDED.canonical_name, normalized_name = EXCLUDED.normalized_name, muscle_group = EXCLUDED.muscle_group, equipment = EXCLUDED.equipment, body_position = EXCLUDED.body_position, variation = EXCLUDED.variation, laterality = EXCLUDED.laterality, grip = EXCLUDED.grip, approval_status = EXCLUDED.approval_status, confidence = EXCLUDED.confidence, source = EXCLUDED.source, source_metadata = EXCLUDED.source_metadata
                RETURNING id
            """), entry_params)).scalar_one()
            for alias in entry.get("aliases", []):
                value = alias.get("alias") if isinstance(alias, dict) else alias
                await conn.execute(text("""INSERT INTO leoft_catalog_aliases (catalog_entry_id, alias, normalized_alias) VALUES (:entry_id, :alias, :normalized) ON CONFLICT (catalog_entry_id, normalized_alias) DO UPDATE SET alias = EXCLUDED.alias"""), {"entry_id": entry_id, "alias": str(value).strip(), "normalized": normalize(value)})
            for media in entry.get("media", []):
                await conn.execute(text("""INSERT INTO leoft_catalog_media (media_id, catalog_entry_id, media_type, format, mime_type, storage_provider, storage_path, thumbnail_path, sha256, file_size, width, height, duration_ms, metadata) VALUES (:media_id, :entry_id, :media_type, :format, :mime_type, :storage_provider, :storage_path, :thumbnail_path, :sha256, :file_size, :width, :height, :duration_ms, CAST(:metadata AS jsonb)) ON CONFLICT (media_id) DO UPDATE SET catalog_entry_id = EXCLUDED.catalog_entry_id, media_type = EXCLUDED.media_type, format = EXCLUDED.format, mime_type = EXCLUDED.mime_type, storage_provider = EXCLUDED.storage_provider, storage_path = EXCLUDED.storage_path, thumbnail_path = EXCLUDED.thumbnail_path, sha256 = EXCLUDED.sha256, file_size = EXCLUDED.file_size, width = EXCLUDED.width, height = EXCLUDED.height, duration_ms = EXCLUDED.duration_ms, metadata = EXCLUDED.metadata"""), {"media_id": required_id(media["media_id"], "media_id"), "entry_id": entry_id, **{key: media.get(key) for key in ("media_type", "format", "mime_type", "storage_provider", "storage_path", "thumbnail_path", "sha256", "file_size", "width", "height", "duration_ms")}, "metadata": json.dumps(media.get("metadata", {}))})
            for signature in entry.get("signatures", []):
                await conn.execute(text("""INSERT INTO leoft_catalog_signatures (catalog_entry_id, signature_key, signature_version, normalized_name, muscle_group, equipment, body_position, variation, laterality, grip, signature_payload) VALUES (:entry_id, :signature_key, :signature_version, :normalized_name, :muscle_group, :equipment, :body_position, :variation, :laterality, :grip, CAST(:payload AS jsonb)) ON CONFLICT (catalog_entry_id, signature_version) DO UPDATE SET signature_key = EXCLUDED.signature_key, normalized_name = EXCLUDED.normalized_name, muscle_group = EXCLUDED.muscle_group, equipment = EXCLUDED.equipment, body_position = EXCLUDED.body_position, variation = EXCLUDED.variation, laterality = EXCLUDED.laterality, grip = EXCLUDED.grip, signature_payload = EXCLUDED.signature_payload"""), {"entry_id": entry_id, "signature_key": required_id(signature["signature_key"], "signature_key"), "signature_version": required_id(signature["signature_version"], "signature_version"), "normalized_name": signature.get("normalized_name", entry_params["normalized_name"]), "muscle_group": signature.get("muscle_group", entry.get("muscle_group")), "equipment": signature.get("equipment", entry.get("equipment")), "body_position": signature.get("body_position", entry.get("body_position")), "variation": signature.get("variation", entry.get("variation")), "laterality": signature.get("laterality", entry.get("laterality")), "grip": signature.get("grip", entry.get("grip")), "payload": json.dumps(signature["signature_payload"])})
        await conn.execute(text("UPDATE leoft_catalog_versions SET status = 'imported' WHERE id = :id"), {"id": version_id})
        await conn.execute(text("UPDATE leoft_catalog_import_runs SET status = 'succeeded', finished_at = now() WHERE id = :id"), {"id": run_id})
        result.update({"idempotent": False, "status": "succeeded", "import_run_id": str(run_id)})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate and count without PostgreSQL writes")
    mode.add_argument("--apply", action="store_true", help="commit the import transaction")
    parser.add_argument(
        "--allow-missing-signatures",
        action="store_true",
        help="explicit local initial load mode; allow zero signatures only",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(import_catalog(
        args.catalog,
        dry_run=not args.apply,
        allow_missing_signatures=args.allow_missing_signatures,
    )), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
