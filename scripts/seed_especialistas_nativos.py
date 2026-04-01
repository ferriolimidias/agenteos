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
    print("ℹ️  Importado via app.db")
except ImportError:
    try:
        from db.database import AsyncSessionLocal
        from db.models import Empresa, Especialista
        print("ℹ️  Importado via db direto")
    except ImportError as e:
        print(f"❌ Erro crítico de importação: {e}")
        print(f"Caminhos verificados: {sys.path}")
        sys.exit(1)


ESPECIALISTAS_NATIVOS = {
    "especialista_saudacao": {
        "descricao_missao": (
            "Atender mensagens de abertura de conversa e cumprimentos iniciais."
        ),
        "descricao_roteamento": (
            "oi, olá, bom dia, boa tarde, boa noite, tudo bem, "
            "quero falar com atendente, inicio de conversa, saudação, iniciar atendimento"
        ),
        "prompt_sistema": (
            "Você é o especialista de saudação. Receba o cliente com cordialidade, "
            "identifique a intenção inicial e conduza para o próximo passo do atendimento."
        ),
    },
    "especialista_localizacao": {
        "descricao_missao": (
            "Fornecer o endereço completo, ponto de referência e enviar o link do Google Maps (mapa) para ajudar o cliente a chegar à unidade."
        ),
        "descricao_roteamento": (
            "endereço, onde fica, mapa, link do maps, google maps, matriz, filial, ponto de referência, "
            "como chegar, me manda o mapa, referências do local, fica perto de onde, rua, avenida, bairro, cidade, "
            "localização novamente, endereço de novo, gps, rota, manda a localização"
        ),
        "prompt_sistema": (
            "Você é o especialista de localização. REGRAS OBRIGATÓRIAS: 1. NUNCA peça permissão para enviar o endereço ou o link, "
            "envie imediatamente. 2. Use o PONTO DE REFERÊNCIA cadastrado como guia principal. 3. Se houver um link de mapa disponível "
            "no contexto, forneça-o. 4. NUNCA invente ou gere links falsos (como '/0'). Se não houver link no contexto, envie apenas o "
            "endereço em texto."
        ),
    },
    "especialista_funcionamento": {
        "descricao_missao": (
            "Responder perguntas sobre dias e horários de atendimento."
        ),
        "descricao_roteamento": (
            "horário de atendimento, que horas abre, que horas fecha, dias de funcionamento, "
            "vocês abrem de sábado, abrem feriado, estão abertos hoje, expediente"
        ),
        "prompt_sistema": (
            "Você é o especialista de funcionamento. Informe horários, dias úteis e "
            "regras de abertura com clareza."
        ),
    },
}


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
                        )
                        session.add(especialista)
                        total_criados += 1
                    else:
                        especialista.descricao_roteamento = dados["descricao_roteamento"]
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
