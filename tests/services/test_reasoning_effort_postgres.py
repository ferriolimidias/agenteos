"""Migration contract against an explicitly disposable PostgreSQL database."""
from pathlib import Path
import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


DATABASE_URL = os.getenv("REASONING_EFFORT_TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="REASONING_EFFORT_TEST_DATABASE_URL must point to a disposable PostgreSQL database")


def _checked_url() -> str:
    parsed = make_url(DATABASE_URL)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "reasoning_effort_test" or parsed.host not in {"127.0.0.1", "localhost"}:
        pytest.fail("REASONING_EFFORT_TEST_DATABASE_URL must target local disposable database reasoning_effort_test")
    return DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("column_exists", [False, True], ids=["migration-adds-column", "create-all-created-column-first"])
async def test_additive_migration_preserves_existing_rows_and_checks_valid_values(column_exists):
    engine = create_async_engine(_checked_url())
    migration = Path("db/migrations/2026_10_15_002_model_reasoning_effort.sql").read_text(encoding="utf-8")
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            initial_column = ", modelo_ia_reasoning_effort varchar(16)" if column_exists else ""
            await connection.execute(text(
                f"CREATE TEMP TABLE empresas (id integer PRIMARY KEY, modelo_ia text NOT NULL{initial_column}) ON COMMIT DROP"
            ))
            await connection.execute(text("INSERT INTO empresas(id, modelo_ia) VALUES (1, 'gpt-4o-mini'), (2, 'gpt-6-luna')"))
            await connection.execute(text(migration))
            # Idempotence within the isolated transaction also confirms the
            # migration's IF NOT EXISTS behavior on a partially prepared schema.
            await connection.execute(text(migration))
            rows = (await connection.execute(text(
                "SELECT id, modelo_ia_reasoning_effort FROM empresas ORDER BY id"
            ))).all()
            assert rows == [(1, None), (2, None)]
            assert await connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='empresas'::regclass "
                "AND conname='ck_empresas_modelo_ia_reasoning_effort')"
            )) is True
            await connection.execute(text(
                "UPDATE empresas SET modelo_ia_reasoning_effort='high' WHERE id=2"
            ))
            assert await connection.scalar(text(
                "SELECT modelo_ia_reasoning_effort FROM empresas WHERE id=2"
            )) == "high"
            with pytest.raises(Exception):
                async with connection.begin_nested():
                    await connection.execute(text(
                        "UPDATE empresas SET modelo_ia_reasoning_effort='default' WHERE id=1"
                    ))
            assert await connection.scalar(text(
                "SELECT modelo_ia_reasoning_effort FROM empresas WHERE id=1"
            )) is None
        finally:
            await transaction.rollback()
    await engine.dispose()
