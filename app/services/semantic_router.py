import os
import logging
from typing import Any, List, Optional

from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Empresa, Especialista

logger = logging.getLogger(__name__)


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
        # Unificacao: roteamento semantico deve depender apenas da missao do especialista.
        return (especialista.descricao_missao or "").strip()

    async def generate_embedding_for_specialist(self, especialista: Especialista) -> list[float] | None:
        routing_text = self._build_routing_text(especialista)
        if not routing_text:
            logger.warning(
                "[SEMANTIC ROUTER] Especialista '%s' sem descricao_missao; embedding nao gerado.",
                getattr(especialista, "nome", "desconhecido"),
            )
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

    async def get_matching_specialists_with_similarity(
        self,
        query_text: str,
        threshold: float = 0.45,
        top_k: int = 3,
        empresa_id: Optional[str] = None,
    ) -> list[tuple[Especialista, float]]:
        async def _buscar_por_texto(texto_busca: str) -> list[tuple[Especialista, float]]:
            query_embedding = await self.embeddings_model.aembed_query(texto_busca)
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
            parsed_rows: list[tuple[Especialista, float]] = []
            for row in rows:
                especialista, similarity = row
                parsed_rows.append((especialista, float(similarity or 0.0)))
            return parsed_rows

        async def _expandir_consulta(texto_original: str) -> list[str]:
            prompt = (
                f"O usuário perguntou: '{texto_original}'. "
                "Extraia e retorne apenas 3 ou 4 variações de termos técnicos ou palavras-chave que representem "
                "a intenção dessa pergunta para uma busca semântica em um banco de especialistas. "
                "Retorne apenas os termos separados por vírgula."
            )
            try:
                llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
                resposta = await llm.ainvoke(prompt)
                conteudo = str(getattr(resposta, "content", "") or "").strip()
                if not conteudo:
                    return []
                return [termo.strip() for termo in conteudo.split(",") if termo.strip()]
            except Exception as exc:
                logger.warning("[SEMANTIC ROUTER] Falha no query expansion: %s", exc)
                return []

        normalized_query = (query_text or "").strip()
        if not normalized_query:
            return []

        # 1) Busca semântica padrão
        primeira_busca = await _buscar_por_texto(normalized_query)
        if primeira_busca:
            return primeira_busca

        # 2) Fallback: Query Expansion via LLM e nova busca semântica
        logger.info(
            "[SEMANTIC ROUTER] Threshold não atingido para: '%s'. Iniciando Expansão de Consulta.",
            normalized_query,
        )
        termos_expandidos = await _expandir_consulta(normalized_query)
        logger.info("[SEMANTIC ROUTER] Termos sugeridos pelo LLM: %s.", termos_expandidos)
        if not termos_expandidos:
            return []

        consulta_expandida = ", ".join(termos_expandidos)
        logger.info("[SEMANTIC ROUTER] Query expansion aplicado: %s", consulta_expandida)
        segunda_busca = await _buscar_por_texto(consulta_expandida)
        logger.info(
            "[SEMANTIC ROUTER] Resultados da segunda busca: %s encontrados.",
            len(segunda_busca),
        )
        return segunda_busca

    @staticmethod
    def _normalizar_resposta_nomes(resposta: str) -> list[str]:
        raw = str(resposta or "").strip()
        if not raw:
            return []
        if raw.upper() == "NONE":
            return []
        return [nome.strip() for nome in raw.split(",") if nome.strip()]

    async def _resolver_duvidas_com_llm(
        self,
        pergunta: str,
        candidatos_duvida: list[tuple[Especialista, float]],
        api_key: str | None = None,
    ) -> set[str]:
        if not candidatos_duvida:
            return set()

        linhas_missoes = []
        for especialista, _similaridade in candidatos_duvida:
            missao = str(getattr(especialista, "descricao_missao", "") or "").strip()
            linhas_missoes.append(f"- {especialista.nome}: {missao or '(sem missão cadastrada)'}")

        prompt = (
            f"O usuário fez a pergunta [{pergunta}]. "
            "Avalie as missões abaixo e retorne uma lista separada por vírgula com os NOMES dos especialistas "
            "necessários para responder a todas as partes da pergunta. "
            "Se nenhum for útil, retorne NONE.\n\n"
            f"Missões:\n{chr(10).join(linhas_missoes)}"
        )

        try:
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key or os.getenv("OPENAI_API_KEY"))
            resposta = await llm.ainvoke(prompt)
            nomes = self._normalizar_resposta_nomes(getattr(resposta, "content", ""))
            return {nome.lower() for nome in nomes}
        except Exception as exc:
            logger.warning("[SEMANTIC ROUTER] Falha no desempate por LLM: %s", exc)
            return set()

    async def route_multi_specialists(
        self,
        query_text: str,
        empresa_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query_text or "").strip()
        if not normalized_query:
            return []

        limite_certeza = 0.65
        limite_duvida = 0.45
        max_agentes_desempate = 3
        api_key_empresa = None

        if empresa_id:
            result_empresa = await self.db.execute(select(Empresa).where(Empresa.id == empresa_id))
            empresa = result_empresa.scalars().first()
            if empresa:
                if getattr(empresa, "limite_certeza", None) is not None:
                    limite_certeza = float(empresa.limite_certeza)
                if getattr(empresa, "limite_duvida", None) is not None:
                    limite_duvida = float(empresa.limite_duvida)
                if getattr(empresa, "max_agentes_desempate", None) is not None:
                    max_agentes_desempate = max(1, int(empresa.max_agentes_desempate))
                credenciais = getattr(empresa, "credenciais_canais", {}) or {}
                api_key_empresa = credenciais.get("openai_api_key")

        if limite_duvida > limite_certeza:
            limite_duvida = limite_certeza

        candidatos = await self.get_matching_specialists_with_similarity(
            query_text=normalized_query,
            threshold=limite_duvida,
            top_k=max_agentes_desempate,
            empresa_id=empresa_id,
        )
        if not candidatos:
            return []

        automaticos: list[tuple[Especialista, float]] = []
        duvida: list[tuple[Especialista, float]] = []
        for especialista, similarity in candidatos:
            if similarity >= limite_certeza:
                automaticos.append((especialista, similarity))
            else:
                duvida.append((especialista, similarity))

        nomes_escolhidos_llm = await self._resolver_duvidas_com_llm(
            pergunta=normalized_query,
            candidatos_duvida=duvida,
            api_key=api_key_empresa,
        )

        selecionados_ordenados: list[Especialista] = []
        ids_adicionados: set[str] = set()

        def _adicionar(especialista: Especialista) -> None:
            esp_id = str(especialista.id)
            if esp_id in ids_adicionados:
                return
            ids_adicionados.add(esp_id)
            selecionados_ordenados.append(especialista)

        for especialista, _similaridade in automaticos:
            _adicionar(especialista)

        for especialista, _similaridade in duvida:
            if especialista.nome.lower() in nomes_escolhidos_llm:
                _adicionar(especialista)

        return [
            {
                "id": str(especialista.id),
                "nome": str(especialista.nome),
                "prompt_sistema": str(getattr(especialista, "prompt_sistema", "") or ""),
                "usar_rag": bool(getattr(especialista, "usar_rag", False)),
            }
            for especialista in selecionados_ordenados
        ]
