import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv
import os

load_dotenv()

MIGRATIONS = [
    # Existente (mantida por compatibilidade)
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS bot_pausado_ate TIMESTAMP NULL;",
    # Novas colunas
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS coletar_nome BOOLEAN DEFAULT TRUE;",
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS dados_adicionais JSONB DEFAULT '{}';",
    "ALTER TABLE crm_etapas ADD COLUMN IF NOT EXISTS tipo VARCHAR NULL;",
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
