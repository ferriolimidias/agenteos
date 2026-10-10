"""Aplica a migration formal idempotente das estruturas LeoFT."""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text

from db.manual_migrations import _split_sql_statements
from db.database import engine


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "2026_07_22_001_formaliza_leoft_training_plans_student_accesses.sql"
)


async def main() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    async with engine.begin() as connection:
        for statement in _split_sql_statements(sql):
            if statement.strip().lower() in {"begin", "commit"}:
                continue
            await connection.execute(text(statement))
    print(f"leoft_training_plans: ready (formal migration applied: {MIGRATION_PATH.name})")


if __name__ == "__main__":
    asyncio.run(main())
