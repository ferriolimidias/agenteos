import asyncio
from sqlalchemy import text
from db.database import engine

async def run_migrations():
    print("Starting migrations...")
    async with engine.begin() as conn:
        try:
            print("Adding columns to ferramentas_api...")
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN url VARCHAR;"))
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN metodo VARCHAR DEFAULT 'GET';"))
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN headers TEXT;"))
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN payload TEXT;"))
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN schema_parametros JSONB DEFAULT '{}'::jsonb;"))
            print("Columns added to ferramentas_api.")
        except Exception as e:
            print(f"Schema columns might already exist or error: {e}")

        try:
            print("Creating especialista_ferramentas table...")
            await conn.execute(text("""
                CREATE TABLE especialista_ferramentas (
                    especialista_id UUID NOT NULL REFERENCES especialistas(id) ON DELETE CASCADE,
                    ferramenta_id UUID NOT NULL REFERENCES ferramentas_api(id) ON DELETE CASCADE,
                    PRIMARY KEY (especialista_id, ferramenta_id)
                );
            """))
            print("Created especialista_ferramentas.")
        except Exception as e:
            print(f"Join table error: {e}")
            
        try:
            print("Creating especialista_tools table...")
            await conn.execute(text("""
                CREATE TABLE especialista_tools (
                    especialista_id UUID NOT NULL REFERENCES especialistas(id) ON DELETE CASCADE,
                    api_connection_id UUID NOT NULL REFERENCES api_connections(id) ON DELETE CASCADE,
                    PRIMARY KEY (especialista_id, api_connection_id)
                );
            """))
            print("Created especialista_tools.")
        except Exception as e:
            print(f"Join table error: {e}")

        try:
            print("Adding conexao_id to mensagens_historico...")
            await conn.execute(
                text("ALTER TABLE mensagens_historico ADD COLUMN conexao_id UUID;")
            )
            print("Column conexao_id ensured on mensagens_historico.")
        except Exception as e:
            print(f"mensagens_historico migration error: {e}")

        try:
            print("Applying semantic routing migrations on especialistas...")
            await conn.execute(text("CREATE EXTENSION vector;"))
            await conn.execute(
                text("ALTER TABLE especialistas ADD COLUMN descricao_roteamento TEXT;")
            )
            await conn.execute(
                text("ALTER TABLE especialistas ADD COLUMN embedding vector(1536);")
            )
            print("Semantic routing columns ensured on especialistas.")
        except Exception as e:
            print(f"semantic routing migration error: {e}")

        try:
            print("Adding favicon_base64 to configuracoes_globais...")
            await conn.execute(
                text("ALTER TABLE configuracoes_globais ADD COLUMN favicon_base64 TEXT;")
            )
            print("Column favicon_base64 ensured on configuracoes_globais.")
        except Exception as e:
            print(f"configuracoes_globais migration error: {e}")

        try:
            print("Adding logo_base64 to configuracoes_globais...")
            await conn.execute(
                text("ALTER TABLE configuracoes_globais ADD COLUMN logo_base64 TEXT;")
            )
            print("Column logo_base64 ensured on configuracoes_globais.")
        except Exception as e:
            print(f"configuracoes_globais logo migration error: {e}")

        print("Adding logo_url to empresas...")
        await conn.execute(text("ALTER TABLE empresas ADD COLUMN logo_url VARCHAR;"))
        print("Adding favicon_url to empresas...")
        await conn.execute(text("ALTER TABLE empresas ADD COLUMN favicon_url VARCHAR;"))
        print("Columns logo_url and favicon_url added on empresas.")

    print("Migrations complete!")

if __name__ == "__main__":
    asyncio.run(run_migrations())
