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
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS gclid VARCHAR NULL;",
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS fbclid VARCHAR NULL;",
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS valor_conversao DOUBLE PRECISION NULL;",
    "ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS ia_ativa BOOLEAN NOT NULL DEFAULT TRUE;",
    "ALTER TABLE conhecimento ADD COLUMN IF NOT EXISTS source_name VARCHAR NULL;",
    "ALTER TABLE conhecimento ADD COLUMN IF NOT EXISTS source_type VARCHAR NULL;",
    "ALTER TABLE conhecimento ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP NULL;",
    "ALTER TABLE conhecimento_rag ADD COLUMN IF NOT EXISTS source_name VARCHAR NULL;",
    "ALTER TABLE conhecimento_rag ADD COLUMN IF NOT EXISTS source_type VARCHAR NULL;",
    "ALTER TABLE conhecimento_rag ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP NULL;",
    "CREATE TABLE IF NOT EXISTS tags_grupos (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE, nome VARCHAR NOT NULL, cor VARCHAR NULL, ordem INTEGER NOT NULL DEFAULT 0);",
    "ALTER TABLE tags_crm ADD COLUMN IF NOT EXISTS grupo_id UUID NULL REFERENCES tags_grupos(id) ON DELETE SET NULL;",
    "ALTER TABLE tags_crm ADD COLUMN IF NOT EXISTS disparar_conversao_ads BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE tags_crm ADD COLUMN IF NOT EXISTS pausa_permanente BOOLEAN NOT NULL DEFAULT FALSE;",
    "ALTER TABLE crm_etapas ADD COLUMN IF NOT EXISTS tipo VARCHAR NULL;",
    "CREATE TABLE IF NOT EXISTS config_followups (id UUID PRIMARY KEY, empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE, nome VARCHAR NOT NULL, tempo_gatilho_minutos INTEGER NOT NULL, objetivo_prompt TEXT NOT NULL, tag_aplicar_final UUID NULL REFERENCES tags_crm(id) ON DELETE SET NULL, ativo BOOLEAN NOT NULL DEFAULT TRUE, criado_em TIMESTAMP NULL DEFAULT NOW());",
    "CREATE TABLE IF NOT EXISTS lead_followup_logs (id UUID PRIMARY KEY, lead_id UUID NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE, config_followup_id UUID NOT NULL REFERENCES config_followups(id) ON DELETE CASCADE, data_envio TIMESTAMP NOT NULL DEFAULT NOW(), status_envio VARCHAR NOT NULL DEFAULT 'enviado', criado_em TIMESTAMP NULL DEFAULT NOW());",
    "CREATE INDEX IF NOT EXISTS idx_config_followups_empresa_id ON config_followups (empresa_id);",
    "CREATE INDEX IF NOT EXISTS idx_lead_followup_logs_lead_id ON lead_followup_logs (lead_id);",
    "CREATE INDEX IF NOT EXISTS idx_lead_followup_logs_config_followup_id ON lead_followup_logs (config_followup_id);",
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
