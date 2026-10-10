import asyncio
from sqlalchemy import text
from db.database import engine

async def up():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN nome_agente VARCHAR DEFAULT 'Assistente Virtual';"))
            print("Coluna nome_agente adicionada.")
        except Exception as e:
            print(f"Erro ao adicionar nome_agente: {e}")
            
        try:
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN mensagem_saudacao VARCHAR;"))
            print("Coluna mensagem_saudacao adicionada.")
        except Exception as e:
            print(f"Erro ao adicionar mensagem_saudacao: {e}")

if __name__ == "__main__":
    asyncio.run(up())
