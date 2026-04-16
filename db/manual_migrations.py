from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


MIGRATIONS_EMPRESAS_PROMPTS = (
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS condutor_prompt TEXT;",
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS condutor_ativo BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE empresas ADD COLUMN IF NOT EXISTS atendente_prompt TEXT;",
)


async def ensure_empresas_prompt_columns(engine: AsyncEngine) -> None:
    """
    Garante compatibilidade da tabela empresas para campos novos do modelo.
    É idempotente e pode ser executada em todo startup.
    """
    async with engine.begin() as conn:
        for statement in MIGRATIONS_EMPRESAS_PROMPTS:
            await conn.execute(text(statement))
