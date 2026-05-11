"""
Cria índice único parcial em crm_leads(empresa_id, telefone_contato)
quando telefone_contato IS NOT NULL.

Pré-requisito: executar migrate_merge_duplicate_crm_leads.py para eliminar
duplicatas; caso contrário o CREATE INDEX falha.

Uso:
  python migrate_crm_leads_unique_telefone.py
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.database import engine

CREATE_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_lead_telefone
ON crm_leads (empresa_id, telefone_contato)
WHERE telefone_contato IS NOT NULL;
"""


async def main() -> None:
    print("Aplicando idx_unique_lead_telefone em crm_leads ...")
    async with engine.begin() as conn:
        await conn.execute(text(CREATE_UNIQUE_INDEX))
    print("Concluído.")


if __name__ == "__main__":
    asyncio.run(main())
