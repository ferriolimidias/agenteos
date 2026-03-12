import asyncio
from dotenv import load_dotenv

# Carrega o .env antes das importações de SQLAlchemy
load_dotenv()

from db.database import engine, Base
# Importar os models para garantir que o SQLAlchemy os conheça ao chamar create_all
from db.models import Empresa, Contato, Agente, FerramentaAPI, ParametrosCadencia, DocumentoBase, VetorConhecimento, Conhecimento, Especialista, APIConnection, WebhookSaida

async def init_models():
    print("Iniciando a criação das tabelas no banco de dados...")
    async with engine.begin() as conn:
        # A extensão 'vector' do pgvector precisará ter sido criada antes, manualmente no banco:
        # await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        
        # Cria fisicamente as tabelas
        await conn.run_sync(Base.metadata.create_all)
        
    print("Tabelas criadas com sucesso!")

if __name__ == "__main__":
    asyncio.run(init_models())
