import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

MIGRATIONS = [
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_identidade TEXT;",
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_regras_negocio TEXT;",
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_estrategia_vendas TEXT;",
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_formatacao_whatsapp TEXT;",
]


async def main():
    engine = create_async_engine(os.getenv("DATABASE_URL"))
    async with engine.begin() as conn:
        for sql in MIGRATIONS:
            try:
                await conn.execute(text(sql))
                print(f"OK: {sql}")
            except Exception as e:
                print(f"Aviso (pode já existir): {e}")
    print("\nMigração concluída.")


if __name__ == "__main__":
    asyncio.run(main())
