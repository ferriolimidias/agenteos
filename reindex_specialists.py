import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import select

from app.services.semantic_router import SemanticRouterService
from db.database import AsyncSessionLocal
from db.models import Especialista


load_dotenv()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_fallback_mission_text(especialista: Especialista) -> str:
    funcao = (
        (especialista.descricao_missao or "").strip()
        or (especialista.prompt_sistema or "").strip()
        or "atendimento especializado"
    )
    return f"Especialista {especialista.nome}. Funcao: {funcao}"


async def reindex_specialists() -> None:
    force_reindex_all = _env_flag("FORCE_REINDEX_ALL", default=False)
    print(
        "[REINDEX] Iniciando reindexacao de especialistas "
        f"(force_all={force_reindex_all})..."
    )

    async with AsyncSessionLocal() as session:
        router_service = SemanticRouterService(session)

        if force_reindex_all:
            query = select(Especialista).order_by(Especialista.nome.asc())
        else:
            query = (
                select(Especialista)
                .where(Especialista.embedding.is_(None))
                .order_by(Especialista.nome.asc())
            )

        result = await session.execute(query)
        especialistas = result.scalars().all()

        if not especialistas:
            print("[REINDEX] Nenhum especialista para atualizar.")
            return

        total = len(especialistas)
        atualizados = 0
        falhas = 0

        for idx, especialista in enumerate(especialistas, start=1):
            try:
                async with session.begin_nested():
                    if not (especialista.descricao_missao or "").strip():
                        especialista.descricao_missao = _build_fallback_mission_text(especialista)

                    embedding = await router_service.generate_embedding_for_specialist(especialista)
                    if not embedding:
                        raise ValueError("nao foi possivel gerar embedding para o especialista")

                    especialista.embedding = embedding
                    await session.flush()

                atualizados += 1
                print(
                    f"[REINDEX] ({idx}/{total}) Especialista '{especialista.nome}' "
                    "atualizado com sucesso."
                )
            except Exception as exc:
                falhas += 1
                print(
                    f"[REINDEX] ({idx}/{total}) Falha ao atualizar especialista "
                    f"'{especialista.nome}': {exc}"
                )

        if atualizados > 0:
            await session.commit()
        else:
            await session.rollback()

        print(
            "[REINDEX] Finalizado. "
            f"Total={total} | Atualizados={atualizados} | Falhas={falhas}"
        )


if __name__ == "__main__":
    asyncio.run(reindex_specialists())
