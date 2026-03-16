import asyncio
from dotenv import load_dotenv
from sqlalchemy import text

# Carrega o .env antes das importações de SQLAlchemy
load_dotenv()

from db.database import engine, Base
import db.models  # noqa: F401

async def init_models():
    print("Iniciando a criação das tabelas no banco de dados...")
    async with engine.begin() as conn:
        # Garante a extensão necessária antes de criar colunas Vector().
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        
        # Cria fisicamente as tabelas
        await conn.run_sync(Base.metadata.create_all)
        
    print("Tabelas criadas com sucesso!")

if __name__ == "__main__":
    asyncio.run(init_models())
