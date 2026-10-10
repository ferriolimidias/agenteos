"""Legacy manual diagnostic retained outside pytest collection.

This utility belongs to the retired semantic-router architecture. The current
runtime uses the consolidated agent graph and does not expose
``app.services.semantic_router``. It is intentionally not a pytest module.
"""

import asyncio
import os
import sys

# Força o path correto dentro do container
sys.path.append("/app")

from app.services.semantic_router import SemanticRouterService
from db.database import AsyncSessionLocal

async def testar():
    async with AsyncSessionLocal() as db:
        router = SemanticRouterService(db)
        pergunta = "Quero saber sobre o curso de administração"
        empresa_id = "ca87e7a5-b673-4e13-9388-c373c33049ca"
        
        print(f"\n--- Analisando: {pergunta} ---")
        
        # threshold=0.0 para vermos a nota sem filtros
        candidatos = await router.get_matching_specialists_with_similarity(
            query_text=pergunta,
            threshold=0.0,
            top_k=5,
            empresa_id=empresa_id
        )
        
        if not candidatos:
            print("❌ Nenhum especialista encontrado no banco.")
            return

        for especialista, similarity in candidatos:
            status = "✅ PASSARIA" if similarity >= 0.45 else "❌ BLOQUEADO (Abaixo do Limite)"
            print(f"Especialista: {especialista.nome} | Score: {similarity:.4f} | {status}")

if __name__ == "__main__":
    asyncio.run(testar())
