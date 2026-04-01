import os
import logging
import asyncio
import time
from typing import Any, List, Optional

from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from db.models import Empresa, Especialista
from db.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class TermosBusca(BaseModel):
    termos: List[str] = Field(default_factory=list)


class EspecialistasEscolhidos(BaseModel):
    ids: List[str] = Field(default_factory=list)


class SemanticRouterService:
    def __init__(self, db: AsyncSession, api_key: str | None = None):
        self.db = db
        key = api_key or os.getenv("OPENAI_API_KEY")
        self.embeddings_model = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=key,
        )
        self.api_key = key

    def _build_fast_llm(self, api_key: str | None = None) -> ChatOpenAI:
        key = api_key or self.api_key or os.getenv("OPENAI_API_KEY")
        try:
            return ChatOpenAI(model="gpt-5.4-nano", temperature=0, api_key=key)
        except Exception:
            return ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=key)

    @staticmethod
    def _deduplicar_termos(termos: list[str], fallback: str) -> list[str]:
        itens: list[str] = []
        vistos: set[str] = set()
        for termo in termos:
            normalizado = str(termo or "").strip()
            if not normalizado:
                continue
            chave = normalizado.lower()
            if chave in vistos:
                continue
            vistos.add(chave)
            itens.append(normalizado)
        if not itens and str(fallback or "").strip():
            itens = [str(fallback).strip()]
        return itens[:6]

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
                "Sua tarefa é gerar termos de busca otimizados (keywords e frases curtas) para recuperar especialistas "
                "em um banco de dados vetorial. "
                "Retorne apenas 3 ou 4 termos curtos, separados por vírgula, sem explicações adicionais. "
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
        recent_history_text: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        t0_total = time.perf_counter()
        expansion_time = 0.0
        pool_time = 0.0
        reranking_time = 0.0
        termos_busca: list[str] = []
        termos_gerados_log: list[str] = []
        termos_consulta_log: list[str] = []
        pool_size = 0
        ids_escolhidos_log: list[str] = []

        normalized_query = str(query_text or "").strip()
        normalized_history_text = str(recent_history_text or "").strip()
        if not normalized_history_text:
            normalized_history_text = f"Cliente: {normalized_query}"
        if not normalized_query:
            total_time = time.perf_counter() - t0_total
            logger.info(
                "[ROUTER TELEMETRY] Total: %.3fs | Expansion (%.3fs): gerados=%s | QueryVetorial=%s | Pool (%.3fs): %d candidatos | Reranking (%.3fs): %s",
                total_time,
                expansion_time,
                termos_gerados_log,
                termos_consulta_log,
                pool_time,
                pool_size,
                reranking_time,
                ids_escolhidos_log,
            )
            return []

        limite_duvida = 0.45
        max_agentes_desempate = 3
        api_key_empresa = None

        if empresa_id:
            result_empresa = await self.db.execute(select(Empresa).where(Empresa.id == empresa_id))
            empresa = result_empresa.scalars().first()
            if empresa:
                if getattr(empresa, "limite_duvida", None) is not None:
                    limite_duvida = float(empresa.limite_duvida)
                if getattr(empresa, "max_agentes_desempate", None) is not None:
                    max_agentes_desempate = max(1, int(empresa.max_agentes_desempate))
                credenciais = getattr(empresa, "credenciais_canais", {}) or {}
                api_key_empresa = credenciais.get("openai_api_key")

        # ETAPA 1: Query Expansion (LLM rápido + structured output)
        t0_expansion = time.perf_counter()
        try:
            llm_expansao = self._build_fast_llm(api_key_empresa)
            llm_expansao_struct = llm_expansao.with_structured_output(TermosBusca)
            termos_resp = await llm_expansao_struct.ainvoke(
                [
                    (
                        "system",
                        "Sua tarefa é gerar termos de busca otimizados (keywords e frases curtas) para recuperar especialistas "
                        "em um banco de dados vetorial.\n"
                        "Analise o histórico recente e a última mensagem do usuário para extrair necessidades explícitas e implícitas.\n\n"
                        "DIRETRIZES:\n"
                        "1. Se a mensagem do usuário for longa ou contiver uma dúvida clara, extraia os termos principais dessa mensagem.\n"
                        "2. Se a mensagem do usuário for apenas uma confirmação curta (ex: 'Sim', 'Quero', 'Pode mandar', 'Certo'), OLHE PARA A MENSAGEM ANTERIOR DA IA. Extraia os termos cruciais daquilo que a IA ofereceu e o usuário acabou de aceitar.\n"
                        "3. Se o usuário pedir localização ou referências, inclua termos como: endereço, ponto de referência, como chegar.\n"
                        "4. Se o usuário apenas saudar, inclua termos como: saudação, início de conversa.\n"
                        "5. Gere apenas termos-chave essenciais para recuperação vetorial. Não classifique em categorias, não use rótulos de intenção e não explique o resultado.\n"
                        "6. Retorne somente a lista de termos no campo 'termos'.\n\n"
                        "EXEMPLOS PRÁTICOS DE ANÁLISE E SAÍDA:\n\n"
                        "Exemplo 1 (Pergunta Direta):\n"
                        "Histórico:\n"
                        "IA: Como posso te ajudar hoje?\n"
                        "Cliente: Quais os valores e horários de Pedagogia?\n"
                        "Termos de Busca: [\"preço pedagogia\", \"horários aulas pedagogia\"]\n\n"
                        "Exemplo 2 (A Confirmação Curta):\n"
                        "Histórico:\n"
                        "IA: Temos uma condição incrível de Bolsas Parciais. Posso te mostrar?\n"
                        "Cliente: Sim, quero ver.\n"
                        "Termos de Busca: [\"bolsas parciais valores\"]\n\n"
                        "Exemplo 3 (Confirmação + Nova Dúvida):\n"
                        "Histórico:\n"
                        "IA: O curso tem duração de 4 anos. Quer saber sobre a matrícula?\n"
                        "Cliente: Pode ser, e vocês têm EAD?\n"
                        "Termos de Busca: [\"matrícula\", \"curso EAD online\"]",
                    ),
                    (
                        "user",
                        "Histórico recente (ordem cronológica):\n"
                        f"{normalized_history_text}",
                    ),
                ]
            )
            termos_gerados_log = self._deduplicar_termos(
                getattr(termos_resp, "termos", []) or [],
                normalized_query,
            )
            termos_busca = list(termos_gerados_log)
        except Exception as exc:
            logger.warning("[SEMANTIC ROUTER] Falha na etapa de query expansion: %s", exc)
            termos_gerados_log = []
            termos_busca = [normalized_query]
        finally:
            expansion_time = time.perf_counter() - t0_expansion

        if not termos_busca:
            termos_busca = [normalized_query]
        termos_consulta_log = list(termos_busca)

        # ETAPA 2: Busca semântica paralela (Candidate Pool)
        top_por_termo = max(3, min(5, max_agentes_desempate))

        async def _buscar_candidatos_por_termo(termo: str) -> list[tuple[Especialista, float]]:
            texto = str(termo or "").strip()
            if not texto:
                return []
            try:
                query_embedding = await self.embeddings_model.aembed_query(texto)
                similarity_expr = (1 - Especialista.embedding.cosine_distance(query_embedding)).label("similarity")
                stmt = (
                    select(Especialista, similarity_expr)
                    .where(
                        Especialista.ativo.is_(True),
                        Especialista.embedding.isnot(None),
                        similarity_expr >= limite_duvida,
                    )
                    .order_by(similarity_expr.desc())
                    .limit(top_por_termo)
                )
                if empresa_id:
                    stmt = stmt.where(Especialista.empresa_id == empresa_id)

                # AsyncSession não é seguro para queries concorrentes; usa sessão isolada por tarefa.
                async with AsyncSessionLocal() as session_parallel:
                    result = await session_parallel.execute(stmt)
                    rows = result.all()
                return [(esp, float(sim or 0.0)) for esp, sim in rows]
            except Exception as exc:
                logger.warning("[SEMANTIC ROUTER] Falha na busca vetorial para termo '%s': %s", texto, exc)
                return []

        t0_pool = time.perf_counter()
        buscas = await asyncio.gather(*[_buscar_candidatos_por_termo(t) for t in termos_busca], return_exceptions=False)
        pool_time = time.perf_counter() - t0_pool
        candidate_pool: dict[str, tuple[Especialista, float]] = {}
        for resultado_termo in buscas:
            for especialista, score in resultado_termo:
                esp_id = str(especialista.id)
                atual = candidate_pool.get(esp_id)
                if (atual is None) or (score > atual[1]):
                    candidate_pool[esp_id] = (especialista, score)
        pool_size = len(candidate_pool)

        if not candidate_pool:
            total_time = time.perf_counter() - t0_total
            logger.info(
                "[ROUTER TELEMETRY] Total: %.3fs | Expansion (%.3fs): gerados=%s | QueryVetorial=%s | Pool (%.3fs): %d candidatos | Reranking (%.3fs): %s",
                total_time,
                expansion_time,
                termos_gerados_log,
                termos_consulta_log,
                pool_time,
                pool_size,
                reranking_time,
                ids_escolhidos_log,
            )
            return []

        candidatos_ordenados = sorted(
            candidate_pool.values(),
            key=lambda item: item[1],
            reverse=True,
        )
        candidatos_ordenados = candidatos_ordenados[: max(8, top_por_termo)]

        # ETAPA 3: Seleção final (LLM reranking por IDs)
        ids_escolhidos: list[str] = []
        t0_reranking = time.perf_counter()
        try:
            llm_rerank = self._build_fast_llm(api_key_empresa)
            llm_rerank_struct = llm_rerank.with_structured_output(EspecialistasEscolhidos)
            payload_candidatos = [
                {
                    "id": str(especialista.id),
                    "nome": str(especialista.nome),
                    "descricao": str(getattr(especialista, "descricao_missao", "") or "").strip()
                    or str(getattr(especialista, "descricao_roteamento", "") or "").strip()
                    or "(sem descrição)",
                }
                for especialista, _score in candidatos_ordenados
            ]
            rerank_resp = await llm_rerank_struct.ainvoke(
                [
                    (
                        "system",
                        "Analise a mensagem do usuário e a lista de especialistas candidatos. "
                        "Devolva APENAS os IDs dos especialistas que são estritamente necessários "
                        "para responder à pergunta. Você pode escolher mais de um se houver múltiplos assuntos.",
                    ),
                    (
                        "user",
                        f"Mensagem original: {normalized_query}\n\nCandidatos:\n{payload_candidatos}",
                    ),
                ]
            )
            ids_escolhidos = [str(i).strip() for i in (getattr(rerank_resp, "ids", []) or []) if str(i).strip()]
        except Exception as exc:
            logger.warning("[SEMANTIC ROUTER] Falha no reranking por LLM: %s", exc)
            ids_escolhidos = []
        finally:
            reranking_time = time.perf_counter() - t0_reranking

        if not ids_escolhidos:
            ids_escolhidos = [str(especialista.id) for especialista, _ in candidatos_ordenados[:top_por_termo]]
        ids_escolhidos_log = list(ids_escolhidos)

        mapa_por_id = {str(especialista.id): especialista for especialista, _ in candidatos_ordenados}
        selecionados_ordenados: list[Especialista] = []
        for esp_id in ids_escolhidos:
            especialista = mapa_por_id.get(str(esp_id))
            if especialista:
                selecionados_ordenados.append(especialista)

        if not selecionados_ordenados:
            total_time = time.perf_counter() - t0_total
            logger.info(
                "[ROUTER TELEMETRY] Total: %.3fs | Expansion (%.3fs): gerados=%s | QueryVetorial=%s | Pool (%.3fs): %d candidatos | Reranking (%.3fs): %s",
                total_time,
                expansion_time,
                termos_gerados_log,
                termos_consulta_log,
                pool_time,
                pool_size,
                reranking_time,
                ids_escolhidos_log,
            )
            return []

        resultado = [
            {
                "id": str(especialista.id),
                "nome": str(especialista.nome),
                "prompt_sistema": str(getattr(especialista, "prompt_sistema", "") or ""),
                "usar_rag": bool(getattr(especialista, "usar_rag", False)),
                "usar_agenda": bool(getattr(especialista, "usar_agenda", False)),
            }
            for especialista in selecionados_ordenados
        ]
        total_time = time.perf_counter() - t0_total
        logger.info(
            "[ROUTER TELEMETRY] Total: %.3fs | Expansion (%.3fs): gerados=%s | QueryVetorial=%s | Pool (%.3fs): %d candidatos | Reranking (%.3fs): %s",
            total_time,
            expansion_time,
            termos_gerados_log,
            termos_consulta_log,
            pool_time,
            pool_size,
            reranking_time,
            ids_escolhidos_log,
        )
        return resultado
