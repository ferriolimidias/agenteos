import asyncio

from sqlalchemy import text

from db.database import engine


async def main() -> None:
    sql = (
        "ALTER TABLE tags_crm "
        "ADD COLUMN acao_transferir_humano BOOLEAN DEFAULT FALSE, "
        "ADD COLUMN mensagem_transferencia TEXT;"
    )
    async with engine.begin() as conn:
        await conn.execute(text(sql))
    print("Colunas 'acao_transferir_humano' e 'mensagem_transferencia' adicionadas com sucesso.")


if __name__ == "__main__":
    asyncio.run(main())
