import asyncio
import os
import sys

from langchain_openai import OpenAIEmbeddings
from sqlalchemy import select

# Adiciona o diretório pai e a pasta 'app' ao sistema de busca do Python
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)
sys.path.append(os.path.join(base_dir, "app"))

# Tenta importar com e sem o prefixo 'app.' para garantir compatibilidade com o Docker
try:
    from app.db.database import AsyncSessionLocal
    from app.db.models import Empresa, Especialista
    from app.core.default_agents import ESPECIALISTAS_NATIVOS
    print("ℹ️  Importado via app.db")
except ImportError:
    try:
        from db.database import AsyncSessionLocal
        from db.models import Empresa, Especialista
        from app.core.default_agents import ESPECIALISTAS_NATIVOS
        print("ℹ️  Importado via db direto")
    except ImportError as e:
        print(f"❌ Erro crítico de importação: {e}")
        print(f"Caminhos verificados: {sys.path}")
        sys.exit(1)

def _build_embedding_text(especialista: Especialista) -> str:
    partes = [
        (especialista.nome or "").strip(),
        (especialista.descricao_missao or "").strip(),
        (especialista.descricao_roteamento or "").strip(),
    ]
    return " ".join(parte for parte in partes if parte)


async def seed_especialistas_nativos() -> None:
    embeddings = OpenAIEmbeddings()

    async with AsyncSessionLocal() as session:
        try:
            result_empresas = await session.execute(select(Empresa))
            empresas = result_empresas.scalars().all()

            if not empresas:
                print("Nenhuma empresa encontrada. Nada para processar.")
                return

            total_criados = 0
            total_atualizados = 0

            for empresa in empresas:
                nomes_nativos = list(ESPECIALISTAS_NATIVOS.keys())
                result_existentes = await session.execute(
                    select(Especialista).where(
                        Especialista.empresa_id == empresa.id,
                        Especialista.nome.in_(nomes_nativos),
                    )
                )
                existentes = {esp.nome: esp for esp in result_existentes.scalars().all()}

                for nome, dados in ESPECIALISTAS_NATIVOS.items():
                    especialista = existentes.get(nome)

                    if especialista is None:
                        especialista = Especialista(
                            empresa_id=empresa.id,
                            nome=nome,
                            descricao_missao=dados["descricao_missao"],
                            descricao_roteamento=dados["descricao_roteamento"],
                            prompt_sistema=dados["prompt_sistema"],
                            ativo=True,
                            fixo_no_roteador=bool(dados.get("fixo_no_roteador", True)),
                        )
                        session.add(especialista)
                        total_criados += 1
                    else:
                        especialista.descricao_missao = dados["descricao_missao"]
                        especialista.descricao_roteamento = dados["descricao_roteamento"]
                        especialista.prompt_sistema = dados["prompt_sistema"]
                        especialista.fixo_no_roteador = bool(dados.get("fixo_no_roteador", True))
                        total_atualizados += 1

                    texto_base = _build_embedding_text(especialista)
                    especialista.embedding = await embeddings.aembed_query(texto_base)

            await session.commit()
            print(
                "Seed concluído com sucesso. "
                f"Empresas processadas: {len(empresas)} | "
                f"Especialistas criados: {total_criados} | "
                f"Especialistas atualizados: {total_atualizados}"
            )
        except Exception:
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(seed_especialistas_nativos())
