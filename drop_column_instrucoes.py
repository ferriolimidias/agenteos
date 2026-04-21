import asyncio

from sqlalchemy import text

from db.database import engine


async def drop_column_instrucoes() -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "ALTER TABLE empresas DROP COLUMN IF EXISTS ia_instrucoes_personalizadas;"
                )
            )
        print("Coluna 'ia_instrucoes_personalizadas' removida com sucesso da tabela empresas!")
    except Exception as exc:
        print(f"Erro ao remover coluna 'ia_instrucoes_personalizadas': {exc}")


if __name__ == "__main__":
    try:
        asyncio.run(drop_column_instrucoes())
    except Exception as exc:
        print(f"Falha ao executar script de remoção da coluna: {exc}")
