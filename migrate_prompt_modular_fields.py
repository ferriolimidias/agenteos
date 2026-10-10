import asyncio

from sqlalchemy import text

from db.database import engine


async def run_alter_table() -> None:
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_identidade TEXT"))
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_regras_negocio TEXT"))
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_estrategia_vendas TEXT"))
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_formatacao_whatsapp TEXT"))
            print("Campos do Prompt Modular adicionados com sucesso em empresas.")
        except Exception as e:
            print(f"Erro ao executar ALTER TABLE para Prompt Modular: {e}")


if __name__ == "__main__":
    asyncio.run(run_alter_table())
