import asyncio
from sqlalchemy import select, update
from langchain_openai import OpenAIEmbeddings
from db.database import AsyncSessionLocal
from db.models import Especialista


async def main():
    print("--- INICIANDO REINDEXAÇÃO VETORIAL ---")
    # Instancia o gerador de embeddings padrão do projeto (OpenAI)
    embeddings = OpenAIEmbeddings()

    async with AsyncSessionLocal() as session:
        # 1. Busca o especialista no banco
        result = await session.execute(
            select(Especialista).where(Especialista.nome == 'especialista_localizacao')
        )
        esp = result.scalars().first()

        if esp:
            # 2. Concatena os textos para formar a "identidade matemática" dele
            texto_base = f"{esp.nome} {esp.descricao_missao} {esp.descricao_roteamento}"
            print(f"Texto base para vetorização: {texto_base}")

            # 3. Chama a API da OpenAI para gerar a lista de números (Vetor)
            print("\nCalculando embeddings na OpenAI...")
            vetor = await embeddings.aembed_query(texto_base)

            # 4. Salva o novo vetor na coluna 'embedding' do PostgreSQL
            await session.execute(
                update(Especialista)
                .where(Especialista.id == esp.id)
                .values(embedding=vetor)
            )
            await session.commit()
            print("✅ Embeddings gerados e salvos com sucesso! O Roteador agora vai encontrar as palavras novas.")
        else:
            print("❌ Especialista não encontrado no banco.")


if __name__ == "__main__":
    asyncio.run(main())
