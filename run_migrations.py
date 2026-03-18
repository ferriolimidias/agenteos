import asyncio
from sqlalchemy import text
from db.database import engine

async def run_migrations():
    print("Starting migrations...")
    async with engine.begin() as conn:
        try:
            print("Adding columns to ferramentas_api...")
            # We use IF NOT EXISTS workaround or just catch errors if they exist
            # PostgreSQL doesn't have IF NOT EXISTS for ADD COLUMN natively until v14+ in some contexts, but let's try direct ALTER
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN IF NOT EXISTS url VARCHAR;"))
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN IF NOT EXISTS metodo VARCHAR DEFAULT 'GET';"))
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN IF NOT EXISTS headers TEXT;"))
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN IF NOT EXISTS payload TEXT;"))
            await conn.execute(text("ALTER TABLE ferramentas_api ADD COLUMN IF NOT EXISTS schema_parametros JSONB DEFAULT '{}'::jsonb;"))
            print("Columns added to ferramentas_api.")
        except Exception as e:
            print(f"Schema columns might already exist or error: {e}")

        try:
            print("Creating especialista_ferramentas table...")
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS especialista_ferramentas (
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
                CREATE TABLE IF NOT EXISTS especialista_tools (
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
                text("ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS conexao_id UUID;")
            )
            print("Column conexao_id ensured on mensagens_historico.")
        except Exception as e:
            print(f"mensagens_historico migration error: {e}")

    print("Migrations complete!")

if __name__ == "__main__":
    asyncio.run(run_migrations())
