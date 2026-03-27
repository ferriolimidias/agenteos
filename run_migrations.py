import asyncio
from sqlalchemy import text
from db.database import engine, Base
import db.models  # noqa: F401

async def run_migrations():
    print("Starting base schema setup...")
    async with engine.begin() as conn:
        try:
            print("Ensuring vector extension...")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            print("Vector extension ready.")
        except Exception as e:
            print(f"Warning: could not ensure vector extension: {e}")

        print("Ensuring base tables with create_all...")
        await conn.run_sync(Base.metadata.create_all)
        print("Base tables ensured.")

    print("Base schema setup complete!")

if __name__ == "__main__":
    asyncio.run(run_migrations())
