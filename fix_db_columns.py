import asyncio
from sqlalchemy import text
from db.database import engine


async def fix():
    print("Iniciando correção forçada de colunas...")
    async with engine.begin() as conn:
        # Tabela configuracoes_globais
        try:
            await conn.execute(text("ALTER TABLE configuracoes_globais ADD COLUMN IF NOT EXISTS favicon_base64 TEXT;"))
            await conn.execute(text("ALTER TABLE configuracoes_globais ADD COLUMN IF NOT EXISTS logo_base64 TEXT;"))
            print("Sucesso: Colunas adicionadas em configuracoes_globais.")
        except Exception as e:
            print(f"Erro em configuracoes_globais: {e}")

        # Tabela empresas (para o logo white-label)
        try:
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS logo_url TEXT;"))
            print("Sucesso: Coluna logo_url adicionada em empresas.")
        except Exception as e:
            print(f"Erro em empresas: {e}")

    print("Processo finalizado.")


if __name__ == "__main__":
    asyncio.run(fix())
