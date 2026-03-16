import asyncio

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine


ADD_CONEXAO_ID_COLUMN = """
ALTER TABLE mensagens_historico
ADD COLUMN IF NOT EXISTS conexao_id UUID;
"""


ADD_CONEXAO_ID_INDEX = """
CREATE INDEX IF NOT EXISTS idx_mensagens_historico_conexao_id
ON mensagens_historico (conexao_id);
"""


ADD_CONEXAO_ID_FOREIGN_KEY = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_mensagens_historico_conexao_id'
    ) THEN
        ALTER TABLE mensagens_historico
        ADD CONSTRAINT fk_mensagens_historico_conexao_id
        FOREIGN KEY (conexao_id)
        REFERENCES conexoes(id)
        ON DELETE SET NULL;
    END IF;
END
$$;
"""


async def main() -> None:
    print("Iniciando migracao de auditoria por conexao...")

    async with engine.begin() as conn:
        await conn.execute(text(ADD_CONEXAO_ID_COLUMN))
        print("Coluna conexao_id garantida em mensagens_historico.")

        await conn.execute(text(ADD_CONEXAO_ID_FOREIGN_KEY))
        print("Foreign key de mensagens_historico -> conexoes garantida.")

        await conn.execute(text(ADD_CONEXAO_ID_INDEX))
        print("Indice de conexao_id em mensagens_historico garantido.")

    print("Migracao de auditoria concluida.")


if __name__ == "__main__":
    asyncio.run(main())
