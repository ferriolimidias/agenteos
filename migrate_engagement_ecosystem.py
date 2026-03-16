import asyncio

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine


CREATE_CAMPANHA_STATUS_ENUM = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type
        WHERE typname = 'campanha_disparo_status_enum'
    ) THEN
        CREATE TYPE campanha_disparo_status_enum AS ENUM ('pendente', 'executando', 'concluido', 'erro');
    END IF;
END
$$;
"""


ADD_EMPRESA_CONEXAO_DISPARO_COLUMN = """
ALTER TABLE empresas
ADD COLUMN IF NOT EXISTS conexao_disparo_id UUID;
"""


ADD_EMPRESA_CONEXAO_DISPARO_FK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_empresas_conexao_disparo_id'
    ) THEN
        ALTER TABLE empresas
        ADD CONSTRAINT fk_empresas_conexao_disparo_id
        FOREIGN KEY (conexao_disparo_id)
        REFERENCES conexoes(id)
        ON DELETE SET NULL;
    END IF;
END
$$;
"""


ADD_EMPRESA_CONEXAO_DISPARO_INDEX = """
CREATE INDEX IF NOT EXISTS idx_empresas_conexao_disparo_id
ON empresas (conexao_disparo_id);
"""


ADD_CRM_LEADS_TAGS_COLUMN = """
ALTER TABLE crm_leads
ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb;
"""


NORMALIZE_CRM_LEADS_TAGS = """
UPDATE crm_leads
SET tags = '[]'::jsonb
WHERE tags IS NULL;
"""


ADD_CRM_LEADS_TAGS_GIN_INDEX = """
CREATE INDEX IF NOT EXISTS idx_crm_leads_tags_gin
ON crm_leads
USING GIN (tags);
"""


CREATE_TEMPLATES_MENSAGEM_TABLE = """
CREATE TABLE IF NOT EXISTS templates_mensagem (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    nome VARCHAR NOT NULL,
    texto_template TEXT NOT NULL,
    variaveis_esperadas JSONB NOT NULL DEFAULT '[]'::jsonb,
    criado_em TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


CREATE_TEMPLATES_MENSAGEM_INDEX = """
CREATE INDEX IF NOT EXISTS idx_templates_mensagem_empresa_id
ON templates_mensagem (empresa_id);
"""


CREATE_CAMPANHAS_DISPARO_TABLE = """
CREATE TABLE IF NOT EXISTS campanhas_disparo (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    nome VARCHAR NOT NULL,
    template_id UUID REFERENCES templates_mensagem(id) ON DELETE SET NULL,
    tags_alvo JSONB NOT NULL DEFAULT '[]'::jsonb,
    data_agendamento TIMESTAMP NULL,
    status campanha_disparo_status_enum NOT NULL DEFAULT 'pendente',
    criado_em TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


CREATE_CAMPANHAS_DISPARO_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_campanhas_disparo_empresa_id
ON campanhas_disparo (empresa_id);

CREATE INDEX IF NOT EXISTS idx_campanhas_disparo_template_id
ON campanhas_disparo (template_id);
"""


CREATE_DESTINOS_TRANSFERENCIA_TABLE = """
CREATE TABLE IF NOT EXISTS destinos_transferencia (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    nome_destino VARCHAR NOT NULL,
    contatos_destino JSONB NOT NULL DEFAULT '[]'::jsonb,
    instrucoes_ativacao TEXT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


CREATE_DESTINOS_TRANSFERENCIA_INDEX = """
CREATE INDEX IF NOT EXISTS idx_destinos_transferencia_empresa_id
ON destinos_transferencia (empresa_id);
"""


CREATE_HISTORICO_TRANSFERENCIA_TABLE = """
CREATE TABLE IF NOT EXISTS historico_transferencia (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
    destino_id UUID NULL REFERENCES destinos_transferencia(id) ON DELETE SET NULL,
    motivo_ia TEXT NULL,
    resumo_enviado TEXT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


CREATE_HISTORICO_TRANSFERENCIA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_historico_transferencia_empresa_id
ON historico_transferencia (empresa_id);

CREATE INDEX IF NOT EXISTS idx_historico_transferencia_lead_id
ON historico_transferencia (lead_id);

CREATE INDEX IF NOT EXISTS idx_historico_transferencia_destino_id
ON historico_transferencia (destino_id);
"""


async def main() -> None:
    print("Iniciando migracao do ecossistema de engajamento e transbordo...")

    async with engine.begin() as conn:
        await conn.execute(text(CREATE_CAMPANHA_STATUS_ENUM))
        print("Enum campanha_disparo_status_enum garantido.")

        await conn.execute(text(ADD_EMPRESA_CONEXAO_DISPARO_COLUMN))
        await conn.execute(text(ADD_EMPRESA_CONEXAO_DISPARO_FK))
        await conn.execute(text(ADD_EMPRESA_CONEXAO_DISPARO_INDEX))
        print("Coluna conexao_disparo_id garantida em empresas.")

        await conn.execute(text(ADD_CRM_LEADS_TAGS_COLUMN))
        await conn.execute(text(NORMALIZE_CRM_LEADS_TAGS))
        await conn.execute(text(ADD_CRM_LEADS_TAGS_GIN_INDEX))
        print("Coluna tags garantida em crm_leads.")

        await conn.execute(text(CREATE_TEMPLATES_MENSAGEM_TABLE))
        await conn.execute(text(CREATE_TEMPLATES_MENSAGEM_INDEX))
        print("Tabela templates_mensagem garantida.")

        await conn.execute(text(CREATE_CAMPANHAS_DISPARO_TABLE))
        await conn.execute(text(CREATE_CAMPANHAS_DISPARO_INDEXES))
        print("Tabela campanhas_disparo garantida.")

        await conn.execute(text(CREATE_DESTINOS_TRANSFERENCIA_TABLE))
        await conn.execute(text(CREATE_DESTINOS_TRANSFERENCIA_INDEX))
        print("Tabela destinos_transferencia garantida.")

        await conn.execute(text(CREATE_HISTORICO_TRANSFERENCIA_TABLE))
        await conn.execute(text(CREATE_HISTORICO_TRANSFERENCIA_INDEXES))
        print("Tabela historico_transferencia garantida.")

    print("Migracao do ecossistema concluida.")


if __name__ == "__main__":
    asyncio.run(main())
