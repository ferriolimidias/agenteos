#!/usr/bin/env python3
"""Emite uma fotografia somente-leitura e sanitizada do schema PostgreSQL.

O resultado é propositalmente composto apenas por metadados.  Nenhuma linha de
tabela, URL ou configuração de conexão é consultada ou impressa.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

APP_ROOT = Path("/app")
if APP_ROOT.is_dir() and str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sqlalchemy import text

from db.database import engine


META_FOUNDATION_MIGRATION = "2026_07_23_meta_conversions_unified_foundation.sql"
META_FOUNDATION_COLUMNS = {
    "tag_meta_conversion_configs": {"conversion_context", "credential_source"},
    "meta_conversion_events": {
        "conversion_context",
        "action_source",
        "messaging_channel",
        "credential_source",
        "waba_id_masked",
        "dataset_id_masked",
        "ctwa_present",
    },
}
def _canonical_rows(kind: str, rows) -> list[str]:
    return [f"{kind}|{'|'.join('' if value is None else str(value) for value in row)}" for row in rows]


async def collect_schema_snapshot() -> dict[str, str | int | bool]:
    async with engine.connect() as connection:
        tables = (await connection.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """))).scalars().all()
        columns = (await connection.execute(text("""
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """))).all()
        constraints = (await connection.execute(text("""
            SELECT conrelid::regclass::text, conname, contype, pg_get_constraintdef(oid, true)
            FROM pg_constraint
            WHERE connamespace = 'public'::regnamespace
            ORDER BY conrelid::regclass::text, conname, contype
        """))).all()
        indexes = (await connection.execute(text("""
            SELECT schemaname, tablename, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
            ORDER BY tablename, indexname
        """))).all()
        extensions = (await connection.execute(text("""
            SELECT extname, extversion
            FROM pg_extension
            ORDER BY extname
        """))).all()
        sequences = (await connection.execute(text("""
            SELECT sequence_schema, sequence_name, data_type, start_value, increment
            FROM information_schema.sequences
            WHERE sequence_schema = 'public'
            ORDER BY sequence_name
        """))).all()
        enum_types = (await connection.execute(text("""
            SELECT n.nspname, t.typname, e.enumsortorder, e.enumlabel
            FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE n.nspname = 'public'
            ORDER BY t.typname, e.enumsortorder
        """))).all()
        alembic_table = (await connection.execute(text(
            "SELECT to_regclass('public.alembic_version') IS NOT NULL"
        ))).scalar_one()
        alembic_version = "absent"
        if alembic_table:
            alembic_version = str((await connection.execute(text(
                "SELECT version_num FROM public.alembic_version ORDER BY version_num"
            ))).scalar_one_or_none() or "empty")

    canonical = "\n".join([
        *(f"table|{table}" for table in tables),
        *_canonical_rows("column", columns),
        *_canonical_rows("constraint", constraints),
        *_canonical_rows("index", indexes),
        *_canonical_rows("extension", extensions),
        *_canonical_rows("sequence", sequences),
        *_canonical_rows("enum", enum_types),
        f"alembic_version|{alembic_version}",
    ])
    present = {(str(table), str(column)) for table, column, *_ in columns}
    expected = {(table, column) for table, names in META_FOUNDATION_COLUMNS.items() for column in names}
    found = len(expected & present)
    meta_applied = found == len(expected)
    meta_state = "complete" if meta_applied else "missing" if found == 0 else "partial"
    return {
        "schema_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "table_count": len(tables),
        "column_count": len(columns),
        "constraint_count": len(constraints),
        "index_count": len(indexes),
        "extension_count": len(extensions),
        "sequence_count": len(sequences),
        "enum_value_count": len(enum_types),
        "alembic_version": alembic_version,
        "migration_tracking": "alembic" if alembic_table else "absent",
        "applied_migration_count": 0 if not alembic_table else len(alembic_version.split(",")),
        "migration_meta_etapa2_file": META_FOUNDATION_MIGRATION,
        "migration_meta_etapa2_applied": str(meta_applied).lower(),
        "migration_meta_etapa2_state": meta_state,
        "migration_meta_etapa2_columns_present": found,
        "migration_meta_etapa2_columns_expected": len(expected),
    }


async def main() -> None:
    snapshot = await collect_schema_snapshot()
    for key in sorted(snapshot):
        print(f"{key}={snapshot[key]}")


if __name__ == "__main__":
    asyncio.run(main())
