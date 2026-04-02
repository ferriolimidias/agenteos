import asyncio
import uuid
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import TagCRM, CRMLead
from core.tools import tool_atualizar_tags_lead, tool_transferir_para_humano

EMPRESA_ID = "ca87e7a5-b673-4e13-9388-c373c33049ca"
LEAD_ID = "87f28df9-fb06-429c-a1bf-f3673bab5390"

async def main():
    print("\n=== INICIANDO TESTE NO TERMINAL ===\n")
    async with AsyncSessionLocal() as session:
        # 1. Busca das tags
        print("1️⃣ LISTA DE TAGS EXISTENTES:")
        result = await session.execute(select(TagCRM).where(TagCRM.empresa_id == uuid.UUID(EMPRESA_ID)))
        tags_encontradas = result.scalars().all()
        if not tags_encontradas:
            print("   Nenhuma tag encontrada no banco para esta empresa.")
        for t in tags_encontradas:
            print(f"   - {t.nome} (ID: {t.id})")

        # 2. Teste da Transferência
        print("\n2️⃣ TESTE DA FERRAMENTA DE TRANSFERIR:")
        ret_transf = await tool_transferir_para_humano.ainvoke({
            "lead_id": LEAD_ID, 
            "empresa_id": EMPRESA_ID, 
            "motivo": "Teste no Terminal"
        })
        print(f"   Retorno da Tool: {ret_transf}")

        # 3. Teste da Tag
        print("\n3️⃣ TESTE DA FERRAMENTA DE TAG (Adicionando 'Financeiro'):")
        ret_tag = await tool_atualizar_tags_lead.ainvoke({
            "lead_id": LEAD_ID, 
            "tags": ["Financeiro"]
        })
        print(f"   Retorno da Tool: {ret_tag}")

        # 4. Verificação real no Banco de Dados
        print("\n4️⃣ O QUE FOI SALVO NO BANCO DE DADOS:")
        result_lead = await session.execute(select(CRMLead).where(CRMLead.id == uuid.UUID(LEAD_ID)))
        lead = result_lead.scalars().first()
        if lead:
            print(f"   Status do Atendimento: {lead.status_atendimento}")
            print(f"   Tags no DB (Devem ser IDs UUID): {lead.tags}")
        else:
            print("   Lead não encontrado no banco.")
        print("\n====================================\n")

if __name__ == "__main__":
    asyncio.run(main())
