import asyncio

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine


CREATE_TAGS_CRM_TABLE = """
CREATE TABLE IF NOT EXISTS tags_crm (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    nome VARCHAR NOT NULL,
    cor VARCHAR NOT NULL DEFAULT '#2563eb',
    instrucao_ia TEXT NULL,
    criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
"""


CREATE_TAGS_CRM_EMPRESA_INDEX = """
CREATE INDEX IF NOT EXISTS idx_tags_crm_empresa_id
ON tags_crm (empresa_id);
"""


CREATE_TAGS_CRM_EMPRESA_NOME_INDEX = """
CREATE INDEX IF NOT EXISTS idx_tags_crm_empresa_nome
ON tags_crm (empresa_id, nome);
"""


async def main() -> None:
    print("Iniciando migracao de tags oficiais do CRM...")

    async with engine.begin() as conn:
        await conn.execute(text(CREATE_TAGS_CRM_TABLE))
        print("Tabela tags_crm garantida.")

        await conn.execute(text(CREATE_TAGS_CRM_EMPRESA_INDEX))
        print("Indice de empresa em tags_crm garantido.")

        await conn.execute(text(CREATE_TAGS_CRM_EMPRESA_NOME_INDEX))
        print("Indice composto empresa/nome em tags_crm garantido.")

    print("Migracao de tags oficiais concluida.")


if __name__ == "__main__":
    asyncio.run(main())
