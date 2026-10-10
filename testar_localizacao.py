import asyncio
import json
from langchain_core.messages import HumanMessage
from app.core.agent_graph import node_especialista_localizacao


async def testar_agente():
    print("--- INICIANDO TESTE DO ESPECIALISTA DE LOCALIZAÇÃO ---")

    # Simulando o estado exato que o LangGraph passa para ele
    estado_mock = {
        "empresa_id": "ca87e7a5-b673-4e13-9388-c373c33049ca",
        "mensagens": [HumanMessage(content="Onde fica a escola? Me passa o link do mapa.")],
        "respostas_especialistas": [],
        "intencao": ["localizacao"]
    }

    try:
        # Chamando o nó do especialista diretamente
        novo_estado = await node_especialista_localizacao(estado_mock)

        print("\n✅ PROCESSAMENTO CONCLUÍDO!")
        print("-" * 50)
        print("JSON GERADO PELO ESPECIALISTA PARA A ORQUESTRADORA:")
        if novo_estado.get("respostas_especialistas"):
            print(novo_estado["respostas_especialistas"][-1])
        else:
            print("Nenhuma resposta gerada.")
        print("-" * 50)

    except Exception as e:
        print(f"❌ ERRO DURANTE O TESTE: {e}")


if __name__ == "__main__":
    asyncio.run(testar_agente())
