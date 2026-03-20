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
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS status_atendimento VARCHAR NOT NULL DEFAULT 'aberto';",
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS foto_url VARCHAR NULL;",
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS foto_atualizada_em TIMESTAMP NULL;",
    "CREATE TABLE IF NOT EXISTS tags_grupos (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE, nome VARCHAR NOT NULL, cor VARCHAR NULL, ordem INTEGER NOT NULL DEFAULT 0);",
    "ALTER TABLE tags_crm ADD COLUMN IF NOT EXISTS grupo_id UUID NULL REFERENCES tags_grupos(id) ON DELETE SET NULL;",
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
