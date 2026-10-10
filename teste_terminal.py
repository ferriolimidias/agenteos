import asyncio
import uuid
import os
from sqlalchemy import select

# Imports diretos (sem o prefixo app.)
from db.database import AsyncSessionLocal
from db.models import TagCRM, CRMLead
from core.tools import tool_atualizar_tags_lead, tool_transferir_para_humano

EMPRESA_ID = "ca87e7a5-b673-4e13-9388-c373c33049ca"
LEAD_ID = "f1b1b4ea-eb80-4bb8-a255-e2cf8aeb2472"

async def main():
    print(f"\n=== [SOLUÇÃO DEFINITIVA] TESTE NO TERMINAL ===")
    print(f"Diretório atual: {os.getcwd()}\n")
    
    async with AsyncSessionLocal() as session:
        print("1️⃣ LISTA DE TAGS EXISTENTES:")
        result = await session.execute(select(TagCRM).where(TagCRM.empresa_id == uuid.UUID(EMPRESA_ID)))
        tags_encontradas = result.scalars().all()
        for t in tags_encontradas:
            print(f"   - {t.nome} (ID: {t.id})")

        print("\n2️⃣ TESTE DA FERRAMENTA DE TRANSFERIR:")
        ret_transf = await tool_transferir_para_humano.ainvoke({
            "lead_id": LEAD_ID, "empresa_id": EMPRESA_ID, "motivo": "Teste no Terminal"
        })
        print(f"   Retorno: {ret_transf}")

        print("\n3️⃣ TESTE DA FERRAMENTA DE TAG (Adicionando 'Financeiro'):")
        ret_tag = await tool_atualizar_tags_lead.ainvoke({
            "lead_id": LEAD_ID, "tags": ["Financeiro"]
        })
        print(f"   Retorno: {ret_tag}")

        print("\n4️⃣ VERIFICAÇÃO NO BANCO DE DADOS:")
        lead = (await session.execute(select(CRMLead).where(CRMLead.id == uuid.UUID(LEAD_ID)))).scalars().first()
        if lead:
            print(f"   Status Atendimento: {lead.status_atendimento}")
            print(f"   Tags (UUIDs): {lead.tags}")
        print("\n====================================\n")

if __name__ == "__main__":
    asyncio.run(main())
