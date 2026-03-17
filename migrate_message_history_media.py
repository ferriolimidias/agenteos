import asyncio

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine


ADD_TIPO_MENSAGEM_COLUMN = """
ALTER TABLE mensagens_historico
ADD COLUMN IF NOT EXISTS tipo_mensagem VARCHAR NOT NULL DEFAULT 'text';
"""


ADD_MEDIA_URL_COLUMN = """
ALTER TABLE mensagens_historico
ADD COLUMN IF NOT EXISTS media_url TEXT NULL;
"""


ADD_TIPO_MENSAGEM_INDEX = """
CREATE INDEX IF NOT EXISTS idx_mensagens_historico_tipo_mensagem
ON mensagens_historico (tipo_mensagem);
"""


async def main() -> None:
    print("Iniciando migracao de midias em mensagens_historico...")

    async with engine.begin() as conn:
        await conn.execute(text(ADD_TIPO_MENSAGEM_COLUMN))
        print("Coluna tipo_mensagem garantida.")

        await conn.execute(text(ADD_MEDIA_URL_COLUMN))
        print("Coluna media_url garantida.")

        await conn.execute(text(ADD_TIPO_MENSAGEM_INDEX))
        print("Indice de tipo_mensagem garantido.")

    print("Migracao de midias concluida.")


if __name__ == "__main__":
    asyncio.run(main())
