import asyncio

from sqlalchemy import text

from db.database import engine


async def main() -> None:
    sql = "ALTER TABLE especialistas ADD COLUMN fixo_no_roteador BOOLEAN DEFAULT FALSE;"
    async with engine.begin() as conn:
        await conn.execute(text(sql))
    print("Coluna 'fixo_no_roteador' adicionada com sucesso.")


if __name__ == "__main__":
    asyncio.run(main())
