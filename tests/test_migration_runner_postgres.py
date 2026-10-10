"""Migration state contracts. Requires an explicitly scoped disposable PostgreSQL."""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from db.manual_migrations import _split_sql_statements
from run_migrations import _apply_registered_migration, _legacy_cleanup_superseded


@pytest.fixture
def isolated_url():
    value = os.getenv("A00_MIGRATION_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("explicit disposable A00_MIGRATION_TEST_DATABASE_URL required")
    url = make_url(value)
    if url.host != "a00-pg" or url.database != "a00_runtime_test":
        pytest.fail("refusing migration-state tests outside isolated A00 database")
    return value


@pytest.mark.asyncio
async def test_postgresql_executes_comments_quotes_and_dollar_bodies(isolated_url):
    engine = create_async_engine(isolated_url)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            sql = r'''-- line; comment
                CREATE TEMP TABLE "split;""table" (value text);
                /* block; /* nested; */ done */
                INSERT INTO "split;""table" VALUES ('a;b'), (E'it\'s;c'), ($tag$x;y$tag$);
                DO $body$ BEGIN
                    INSERT INTO "split;""table" VALUES ('do;body');
                END $body$;
                SELECT value FROM "split;""table" ORDER BY value'''
            statements = _split_sql_statements(sql)
            assert len(statements) == 4
            for statement in statements:
                result = await conn.execute(text(statement))
            assert result.scalars().all() == ["a;b", "do;body", "it's;c", "x;y"]
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_recorded_migration_is_skipped_and_modified_file_is_rejected(isolated_url, tmp_path, capsys):
    engine = create_async_engine(isolated_url)
    migration = tmp_path / ("a00_synthetic_" + uuid4().hex + ".sql")
    migration.write_text("CREATE TEMP TABLE a00_counter(value int); INSERT INTO a00_counter VALUES (1);")
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await _apply_registered_migration(conn, migration)
            await _apply_registered_migration(conn, migration)
            assert (await conn.execute(text("SELECT count(*) FROM a00_counter"))).scalar_one() == 1
            assert "Migration already recorded" in capsys.readouterr().out
            migration.write_text("SELECT 2;")
            with pytest.raises(RuntimeError, match="Previously recorded migration changed"):
                await _apply_registered_migration(conn, migration)
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_sql_failure_cannot_commit_a_migration_record(isolated_url, tmp_path):
    engine = create_async_engine(isolated_url)
    name = "a00_failure_" + uuid4().hex + ".sql"
    migration = tmp_path / name
    migration.write_text("SELECT 1; SELECT * FROM a00_nonexistent_table;")
    with pytest.raises(Exception):
        async with engine.begin() as conn:
            await _apply_registered_migration(conn, migration)
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM agentos_schema_migrations WHERE filename=:name"), {"name": name})).scalar_one()
        assert count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_completed_legacy_schema_is_recognized_but_partial_retirement_fails(isolated_url):
    engine = create_async_engine(isolated_url)
    migration = Path("db/migrations/2026_08_12_commercial_legacy_data_cleanup.sql")
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            assert await _legacy_cleanup_superseded(conn, migration)
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN condutor_ativo BOOLEAN"))
            with pytest.raises(RuntimeError, match="partially retired schema"):
                await _legacy_cleanup_superseded(conn, migration)
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN condutor_prompt TEXT"))
            assert not await _legacy_cleanup_superseded(conn, migration)
        finally:
            await transaction.rollback()
    await engine.dispose()
