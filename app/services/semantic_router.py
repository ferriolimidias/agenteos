import os
from typing import List, Optional

from langchain_openai import OpenAIEmbeddings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Especialista


class SemanticRouterService:
    def __init__(self, db: AsyncSession, api_key: str | None = None):
        self.db = db
        key = api_key or os.getenv("OPENAI_API_KEY")
        self.embeddings_model = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=key,
        )

    @staticmethod
    def _build_routing_text(especialista: Especialista) -> str:
        return (
            (especialista.descricao_roteamento or "").strip()
            or (especialista.descricao_missao or "").strip()
            or (especialista.prompt_sistema or "").strip()
        )

    async def generate_embedding_for_specialist(self, especialista: Especialista) -> list[float] | None:
        routing_text = self._build_routing_text(especialista)
        if not routing_text:
            return None
        return await self.embeddings_model.aembed_query(routing_text)

    async def refresh_specialist_embedding(self, especialista: Especialista) -> list[float] | None:
        embedding = await self.generate_embedding_for_specialist(especialista)
        especialista.embedding = embedding
        return embedding

    async def get_matching_specialists(
        self,
        query_text: str,
        threshold: float = 0.75,
        top_k: int = 3,
        empresa_id: Optional[str] = None,
    ) -> List[Especialista]:
        normalized_query = (query_text or "").strip()
        if not normalized_query:
            return []

        query_embedding = await self.embeddings_model.aembed_query(normalized_query)
        similarity_expr = (1 - Especialista.embedding.cosine_distance(query_embedding)).label("similarity")

        stmt = (
            select(Especialista, similarity_expr)
            .where(
                Especialista.ativo.is_(True),
                Especialista.embedding.isnot(None),
                similarity_expr >= threshold,
            )
            .order_by(similarity_expr.desc())
            .limit(top_k)
        )

        if empresa_id:
            stmt = stmt.where(Especialista.empresa_id == empresa_id)

        result = await self.db.execute(stmt)
        rows = result.all()
        return [row[0] for row in rows]
