import asyncio

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine


CREATE_EMPRESA_UNIDADES_TABLE = """
CREATE TABLE IF NOT EXISTS empresa_unidades (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    nome_unidade VARCHAR NOT NULL,
    endereco_completo TEXT NOT NULL,
    link_google_maps VARCHAR NULL,
    horario_funcionamento VARCHAR NULL,
    is_matriz BOOLEAN NOT NULL DEFAULT FALSE
);
"""


CREATE_EMPRESA_UNIDADES_EMPRESA_INDEX = """
CREATE INDEX IF NOT EXISTS idx_empresa_unidades_empresa_id
ON empresa_unidades (empresa_id);
"""


CREATE_EMPRESA_UNIDADES_MATRIZ_INDEX = """
CREATE INDEX IF NOT EXISTS idx_empresa_unidades_empresa_matriz
ON empresa_unidades (empresa_id, is_matriz);
"""


async def main() -> None:
    print("Iniciando migracao de unidades/filiais por empresa...")

    async with engine.begin() as conn:
        await conn.execute(text(CREATE_EMPRESA_UNIDADES_TABLE))
        print("Tabela empresa_unidades garantida.")

        await conn.execute(text(CREATE_EMPRESA_UNIDADES_EMPRESA_INDEX))
        print("Indice por empresa garantido.")

        await conn.execute(text(CREATE_EMPRESA_UNIDADES_MATRIZ_INDEX))
        print("Indice empresa/matriz garantido.")

    print("Migracao de empresa_unidades concluida.")


if __name__ == "__main__":
    asyncio.run(main())
