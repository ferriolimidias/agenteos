import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

MIGRATIONS = [
    "ALTER TABLE especialistas ADD COLUMN IF NOT EXISTS peso_prioridade INTEGER DEFAULT 1 NOT NULL;",
]


async def main():
    engine = create_async_engine(os.getenv("DATABASE_URL"))
    async with engine.begin() as conn:
        for sql in MIGRATIONS:
            try:
                await conn.execute(text(sql))
                print(f"OK: {sql}")
            except Exception as e:
                print(f"Aviso: falha ao aplicar migração '{sql}': {e}")
    print("\nMigração de peso_prioridade concluída.")


if __name__ == "__main__":
    asyncio.run(main())
