import asyncio

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine


ADD_DISPARO_DELAY_MIN_COLUMN = """
ALTER TABLE empresas
ADD COLUMN IF NOT EXISTS disparo_delay_min INTEGER NOT NULL DEFAULT 3;
"""


ADD_DISPARO_DELAY_MAX_COLUMN = """
ALTER TABLE empresas
ADD COLUMN IF NOT EXISTS disparo_delay_max INTEGER NOT NULL DEFAULT 7;
"""


async def main() -> None:
    print("Iniciando migracao de delay dinâmico para disparos...")

    async with engine.begin() as conn:
        await conn.execute(text(ADD_DISPARO_DELAY_MIN_COLUMN))
        print("Coluna disparo_delay_min garantida em empresas.")

        await conn.execute(text(ADD_DISPARO_DELAY_MAX_COLUMN))
        print("Coluna disparo_delay_max garantida em empresas.")

    print("Migracao de delay dinâmico concluida.")


if __name__ == "__main__":
    asyncio.run(main())
