import asyncio
from sqlalchemy import text
from db.database import AsyncSessionLocal, engine

async def run_alter_table():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_instrucoes_personalizadas TEXT"))
            await conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS ia_tom_voz VARCHAR"))
            print("Columns added successfully!")
        except Exception as e:
            print(f"Error executing ALTER TABLE: {e}")

if __name__ == "__main__":
    asyncio.run(run_alter_table())
