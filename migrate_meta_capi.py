import asyncio

from sqlalchemy import text

from db.database import engine


SQL_ALTERS = [
    "ALTER TABLE empresas ADD COLUMN meta_capi_ativo BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE empresas ADD COLUMN meta_pixel_id VARCHAR;",
    "ALTER TABLE empresas ADD COLUMN meta_access_token VARCHAR;",
]


async def run_migration() -> None:
    async with engine.begin() as conn:
        for sql in SQL_ALTERS:
            try:
                await conn.execute(text(sql))
                print(f"[OK] Executado: {sql}")
            except Exception as exc:
                msg = str(exc).lower()
                if "already exists" in msg or "duplicate column" in msg:
                    print(f"[SKIP] Coluna já existe: {sql}")
                    continue
                raise


if __name__ == "__main__":
    asyncio.run(run_migration())
