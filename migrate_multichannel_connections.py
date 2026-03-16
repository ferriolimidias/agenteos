import asyncio
import json
import uuid

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine


CREATE_TIPO_CONEXAO_ENUM = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type
        WHERE typname = 'tipo_conexao_enum'
    ) THEN
        CREATE TYPE tipo_conexao_enum AS ENUM ('evolution', 'meta', 'instagram');
    END IF;
END
$$;
"""


CREATE_CONEXOES_TABLE = """
CREATE TABLE IF NOT EXISTS conexoes (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    tipo tipo_conexao_enum NOT NULL,
    nome_instancia VARCHAR NOT NULL,
    credenciais JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR NOT NULL DEFAULT 'ativo',
    criado_em TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


CREATE_CONEXOES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_conexoes_empresa_id ON conexoes (empresa_id);
"""


async def backfill_evolution_conexoes(conn) -> None:
    result = await conn.execute(
        text(
            """
            SELECT id, credenciais_canais
            FROM empresas
            WHERE credenciais_canais IS NOT NULL
              AND credenciais_canais != '{}'::jsonb
            """
        )
    )

    empresas = result.mappings().all()

    for empresa in empresas:
        empresa_id = empresa["id"]
        credenciais = empresa["credenciais_canais"] or {}

        evolution_url = credenciais.get("evolution_url")
        evolution_apikey = credenciais.get("evolution_apikey")
        evolution_instance = credenciais.get("evolution_instance")

        if not any([evolution_url, evolution_apikey, evolution_instance]):
            continue

        nome_instancia = evolution_instance or "evolution-default"

        existing = await conn.execute(
            text(
                """
                SELECT 1
                FROM conexoes
                WHERE empresa_id = :empresa_id
                  AND tipo = 'evolution'
                  AND nome_instancia = :nome_instancia
                LIMIT 1
                """
            ),
            {
                "empresa_id": empresa_id,
                "nome_instancia": nome_instancia,
            },
        )

        if existing.scalar():
            continue

        await conn.execute(
            text(
                """
                INSERT INTO conexoes (
                    id,
                    empresa_id,
                    tipo,
                    nome_instancia,
                    credenciais,
                    status
                ) VALUES (
                    :id,
                    :empresa_id,
                    'evolution',
                    :nome_instancia,
                    CAST(:credenciais AS JSONB),
                    'ativo'
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "empresa_id": empresa_id,
                "nome_instancia": nome_instancia,
                "credenciais": json.dumps(
                    {
                        "evolution_url": evolution_url,
                        "evolution_apikey": evolution_apikey,
                        "evolution_instance": evolution_instance,
                        "openai_api_key": credenciais.get("openai_api_key"),
                    }
                ),
            },
        )

        print(f"Backfill de conexao evolution criado para empresa {empresa_id}.")


async def main() -> None:
    print("Iniciando migracao multicanal...")

    async with engine.begin() as conn:
        await conn.execute(text(CREATE_TIPO_CONEXAO_ENUM))
        print("Enum tipo_conexao_enum garantido.")

        await conn.execute(text(CREATE_CONEXOES_TABLE))
        print("Tabela conexoes garantida.")

        await conn.execute(text(CREATE_CONEXOES_INDEX))
        print("Indice de conexoes garantido.")

        await backfill_evolution_conexoes(conn)

    print("Migracao multicanal concluida.")


if __name__ == "__main__":
    asyncio.run(main())
