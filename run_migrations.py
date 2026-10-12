import asyncio
import hashlib
from pathlib import Path

from sqlalchemy import text

import db.models  # noqa: F401
from app.core.startup_database_mode import StartupDatabaseMode
from db.database import Base, engine
from db.manual_migrations import _split_sql_statements


async def _legacy_cleanup_superseded(conn, migration: Path) -> bool:
    """Recognize the retired-column cleanup only after its successor's schema.

    A migrated database no longer has condutor_prompt. Its historical data
    transformation cannot be replayed; do not recreate the retired column or
    rewrite the historical SQL. A partially retired schema fails closed.
    """
    if migration.name != "2026_08_12_commercial_legacy_data_cleanup.sql":
        return False
    columns = set((await conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='empresas'"
    ))).scalars())
    if "condutor_prompt" in columns:
        return False
    legacy_columns = {"condutor_ativo", "modelo_roteador"}
    legacy_tables = (await conn.execute(text(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
        "AND table_name IN ('tag_meta_conversion_configs','especialista_ferramentas',"
        "'especialista_tools','especialistas')"
    ))).scalar_one()
    retired_tag_column = (await conn.execute(text(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name='meta_conversion_events' AND column_name='tag_config_id'"
    ))).scalar_one()
    if (legacy_columns & columns or legacy_tables or retired_tag_column
            or not {"ia_regras_negocio", "credenciais_canais"}.issubset(columns)):
        raise RuntimeError("Cannot identify completed legacy cleanup: partially retired schema")
    return True


async def _apply_registered_migration(conn, migration: Path) -> None:
    source = migration.read_text(encoding="utf-8")
    checksum = hashlib.sha256(source.encode("utf-8")).hexdigest()
    existing = (await conn.execute(text(
        "SELECT checksum FROM agentos_schema_migrations WHERE filename=:filename"
    ), {"filename": migration.name})).scalar_one_or_none()
    if existing is not None:
        if existing != checksum:
            raise RuntimeError(f"Previously recorded migration changed: {migration.name}")
        print(f"Migration already recorded: {migration.name}")
        return
    superseded = await _legacy_cleanup_superseded(conn, migration)
    if not superseded:
        for statement in _split_sql_statements(source):
            await conn.execute(text(statement))
    status = "superseded_by_schema" if superseded else "applied"
    await conn.execute(text(
        "INSERT INTO agentos_schema_migrations(filename,checksum,status) "
        "VALUES (:filename,:checksum,:status)"
    ), {"filename": migration.name, "checksum": checksum, "status": status})
    print(f"Migration {'superseded by verified schema' if superseded else 'applied'}: {migration.name}")


async def run_migrations():
    StartupDatabaseMode.resolve().require_database_actions("run_migrations.py")
    print("Starting base schema setup...")
    async with engine.begin() as conn:
        # Serialize runners; history and SQL changes commit together or roll back.
        await conn.execute(text("SELECT pg_advisory_xact_lock(1093677104)"))
        try:
            print("Ensuring vector extension...")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            print("Vector extension ready.")
        except Exception as e:
            print(f"Warning: could not ensure vector extension: {e}")

        print("Ensuring base tables with create_all...")
        await conn.run_sync(Base.metadata.create_all)
        print("Base tables ensured.")
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS agentos_schema_migrations ("
            "filename TEXT PRIMARY KEY, checksum TEXT NOT NULL, "
            "status TEXT NOT NULL CHECK (status IN ('applied','superseded_by_schema')), "
            "recorded_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        ))
        migrations = (
            Path(__file__).resolve().parent / "db/migrations/2026_07_29_001_leoft_postgres_central.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_07_29_002_leoft_student_progress.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_07_31_001_leoft_catalog_foundation.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_08_12_commercial_state_core.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_08_12_commercial_mapping_contract.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_08_12_commercial_legacy_data_cleanup.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_08_12_remove_legacy_commercial_schema.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_08_13_traffic_meta_full_campaign.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_08_17_empresa_payment_providers.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_08_28_001_campaign_ai_reply_enabled.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_10_02_001_leoft_canonical_product_catalog.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_10_08_001_attribution_ledger.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_10_08_002_commercial_intelligence.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_10_09_001_meta_marketing_tenant_account_grants.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_10_09_002_meta_conversion_shadow.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_10_09_003_canonical_conversion_outbox.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_10_09_004_legacy_conversion_delivery_evidence.sql",
            Path(__file__).resolve().parent / "db/migrations/2026_10_12_001_jev_tenant_config.sql",
        )
        for migration in migrations:
            await _apply_registered_migration(conn, migration)

    print("Base schema setup complete!")

if __name__ == "__main__":
    asyncio.run(run_migrations())
