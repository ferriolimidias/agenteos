import os
import logging
import time
from typing import Any, List, Optional

from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from db.models import Empresa, Especialista

logger = logging.getLogger(__name__)


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

    def _build_fast_llm(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> ChatOpenAI:
        key = api_key or self.api_key or os.getenv("OPENAI_API_KEY")
        try:
            return ChatOpenAI(model=(model_name or "gpt-5.4-nano"), temperature=0, api_key=key)
        except Exception:
            try:
                return ChatOpenAI(model="gpt-5.4-nano", temperature=0, api_key=key)
            except Exception:
                return ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=key)

    def _build_routing_text(especialista: Especialista) -> str:
        # Unificacao: roteamento semantico deve depender apenas da missao do especialista.
        return (especialista.descricao_missao or "").strip()

    @staticmethod
    def _agrupar_turnos(mensagens: list[dict[str, str]]) -> list[dict[str, str]]:
        turnos: list[dict[str, str]] = []
        for mensagem in mensagens:
            role = str(mensagem.get("role") or "").strip().lower()
            content = str(mensagem.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            if turnos and turnos[-1]["role"] == role:
                turnos[-1]["content"] = f"{turnos[-1]['content']}\n{content}".strip()
                continue
            turnos.append({"role": role, "content": content})
        return turnos

    @staticmethod
    def _normalizar_historico_para_mensagens(recent_history_text: str) -> list[dict[str, str]]:
        mensagens: list[dict[str, str]] = []
        for linha_raw in str(recent_history_text or "").splitlines():
            linha = str(linha_raw or "").strip()
            if not linha:
                continue
            lower = linha.lower()
            if lower.startswith("ia:") or lower.startswith("assistente:"):
                content = linha.split(":", 1)[1].strip() if ":" in linha else linha
                mensagens.append({"role": "assistant", "content": content})
            elif lower.startswith("cliente:") or lower.startswith("usuario:") or lower.startswith("usuário:"):
                content = linha.split(":", 1)[1].strip() if ":" in linha else linha
                mensagens.append({"role": "user", "content": content})
            elif mensagens:
                mensagens[-1]["content"] = f"{mensagens[-1]['content']}\n{linha}".strip()
            else:
                mensagens.append({"role": "user", "content": linha})
        return mensagens

    async def _expandir_query_com_llm(
        self,
        mensagem_atual: str,
        recent_history_text: str,
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> str:
        mensagens = self._normalizar_historico_para_mensagens(recent_history_text)
        if not mensagens and str(mensagem_atual or "").strip():
            mensagens = [{"role": "user", "content": str(mensagem_atual).strip()}]

        turnos_consolidados = self._agrupar_turnos(mensagens)
        ultimos_turnos = turnos_consolidados[-3:]

        linhas_contexto: list[str] = []
        for turno in ultimos_turnos:
            role = turno.get("role")
            prefixo = "Cliente" if role == "user" else "IA"
            linhas_contexto.append(f"{prefixo}: {str(turno.get('content') or '').strip()}")
        contexto_turnos = "\n".join(linhas_contexto).strip()

        system_prompt = (
            "Você é um especialista em expansão semântica para busca vetorial.\n"
            "Analise o contexto EXCLUSIVAMENTE dos últimos turnos da conversa acima. "
            f"A resposta final do usuário foi: '{mensagem_atual}'.\n"
            "Entenda a intenção real do usuário (mesmo que ele tenha digitado apenas um número de menu, como '1' ou '2', ou uma frase muito curta).\n"
            "Sua tarefa: GERE exatamente 10 palavras-chave ou termos curtos que sejam ALTAMENTE RELACIONADOS ao departamento, setor ou assunto que o usuário deseja acessar. "
            "Amplie o vocabulário para facilitar a busca no banco de dados.\n"
            "Exemplo: Se o usuário quer o setor financeiro, gere: 'financeiro, pagamentos, boleto, mensalidade, fatura, cobrança, dinheiro, pix, atraso, negociar'.\n"
            "Retorne APENAS os 10 termos separados por vírgula. Absolutamente nenhum outro texto."
        )

        llm_expansao = self._build_fast_llm(api_key=api_key, model_name=model_name)
        resposta = await llm_expansao.ainvoke(
            [
                ("system", system_prompt),
                ("user", f"Últimos turnos consolidados:\n{contexto_turnos}"),
            ]
        )
        termos_expandidos = str(getattr(resposta, "content", "") or "").strip()
        return ",".join([t.strip() for t in termos_expandidos.split(",") if t.strip()])

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

    async def get_top_specialists_contextual(
        self,
        query_text: str,
        empresa_id: Optional[str] = None,
        recent_history_text: Optional[str] = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        normalized_query = str(query_text or "").strip()
        normalized_history_text = str(recent_history_text or "").strip()
        if not normalized_history_text:
            normalized_history_text = f"Cliente: {normalized_query}"

        if not normalized_query:
            return {"termos_expandidos": "", "candidatos": []}

        limite_duvida = 0.45
        api_key_empresa = None
        modelo_roteador_empresa = "gpt-5.4-nano"

        if empresa_id:
            result_empresa = await self.db.execute(select(Empresa).where(Empresa.id == empresa_id))
            empresa = result_empresa.scalars().first()
            if empresa:
                if getattr(empresa, "limite_duvida", None) is not None:
                    limite_duvida = float(empresa.limite_duvida)
                credenciais = getattr(empresa, "credenciais_canais", {}) or {}
                api_key_empresa = credenciais.get("openai_api_key")
                modelo_roteador_empresa = str(getattr(empresa, "modelo_roteador", "") or "").strip() or "gpt-5.4-nano"

        query_vetorial = normalized_query
        try:
            query_vetorial = await self._expandir_query_com_llm(
                mensagem_atual=normalized_query,
                recent_history_text=normalized_history_text,
                api_key=api_key_empresa,
                model_name=modelo_roteador_empresa,
            )
        except Exception as exc:
            logger.warning("[SEMANTIC ROUTER] Falha na etapa de query expansion contextual: %s", exc)
            query_vetorial = normalized_query

        query_vetorial = str(query_vetorial or "").strip() or normalized_query
        termos = [termo.strip() for termo in query_vetorial.split(",") if termo.strip()]
        query_vetorial = ", ".join(termos[:10]) if termos else normalized_query
        print(
            f"[ROTEADOR - QUERY EXPANSION] Original: '{normalized_query}' | "
            f"Termos Expandidos: '{query_vetorial}'"
        )

        try:
            query_embedding = await self.embeddings_model.aembed_query(query_vetorial)
            similarity_expr = (1 - Especialista.embedding.cosine_distance(query_embedding)).label("similarity")
            stmt = (
                select(Especialista, similarity_expr)
                .where(
                    Especialista.ativo.is_(True),
                    Especialista.embedding.isnot(None),
                    similarity_expr >= limite_duvida,
                )
                .order_by(similarity_expr.desc())
                .limit(max(1, int(top_k)))
            )
            if empresa_id:
                stmt = stmt.where(Especialista.empresa_id == empresa_id)

            result = await self.db.execute(stmt)
            rows = result.all()
        except Exception as exc:
            logger.warning("[SEMANTIC ROUTER] Falha na busca vetorial: %s", exc)
            rows = []

        candidatos = []
        for especialista, similarity in rows:
            candidatos.append(
                {
                    "id": str(especialista.id),
                    "nome": str(getattr(especialista, "nome", "") or ""),
                    "descricao_missao": str(getattr(especialista, "descricao_missao", "") or "").strip(),
                    "prompt_sistema": str(getattr(especialista, "prompt_sistema", "") or ""),
                    "usar_rag": bool(getattr(especialista, "usar_rag", False)),
                    "usar_agenda": bool(getattr(especialista, "usar_agenda", False)),
                    "similarity": float(similarity or 0.0),
                }
            )

        return {"termos_expandidos": query_vetorial, "candidatos": candidatos}

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
                "[ROUTER TELEMETRY] Total: %.3fs | Expansion (%.3fs) | QueryVetorial=%s | Pool (%.3fs): %d candidatos | Reranking (%.3fs): %s",
                total_time,
                expansion_time,
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
        modelo_roteador_empresa = "gpt-5.4-nano"

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
                modelo_roteador_empresa = str(getattr(empresa, "modelo_roteador", "") or "").strip() or "gpt-5.4-nano"

        # ETAPA 1: Query Expansion contextual por turnos (LLM rápido)
        t0_expansion = time.perf_counter()
        query_vetorial = normalized_query
        try:
            query_vetorial = await self._expandir_query_com_llm(
                mensagem_atual=normalized_query,
                recent_history_text=normalized_history_text,
                api_key=api_key_empresa,
                model_name=modelo_roteador_empresa,
            )
        except Exception as exc:
            logger.warning("[SEMANTIC ROUTER] Falha na etapa de query expansion contextual: %s", exc)
            query_vetorial = normalized_query
        finally:
            expansion_time = time.perf_counter() - t0_expansion
        query_vetorial = str(query_vetorial or "").strip() or normalized_query
        print(
            f"[ROTEADOR - QUERY EXPANSION] Original: '{normalized_query}' | "
            f"Termos Expandidos: '{query_vetorial}'"
        )
        termos_consulta_log = [query_vetorial]

        # ETAPA 2: Busca semântica vetorial usando EXATAMENTE a query expandida
        top_por_termo = 5
        t0_pool = time.perf_counter()
        try:
            query_embedding = await self.embeddings_model.aembed_query(query_vetorial)
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
            result = await self.db.execute(stmt)
            rows = result.all()
            candidate_pool = {str(esp.id): (esp, float(sim or 0.0)) for esp, sim in rows}
            pool_size = len(candidate_pool)
        except Exception as exc:
            logger.warning("[SEMANTIC ROUTER] Falha na busca vetorial: %s", exc)
            candidate_pool = {}
            pool_size = 0
        finally:
            pool_time = time.perf_counter() - t0_pool

        if not candidate_pool:
            total_time = time.perf_counter() - t0_total
            logger.info(
                "[ROUTER TELEMETRY] Total: %.3fs | Expansion (%.3fs) | QueryVetorial=%s | Pool (%.3fs): %d candidatos | Reranking (%.3fs): %s",
                total_time,
                expansion_time,
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
        candidatos_ordenados = candidatos_ordenados[:top_por_termo]

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
                        "Historico recente da conversa:\n"
                        f"{normalized_history_text}\n\n"
                        f"Mensagem final do usuario: {normalized_query}\n\n"
                        f"Candidatos:\n{payload_candidatos}",
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
                "[ROUTER TELEMETRY] Total: %.3fs | Expansion (%.3fs) | QueryVetorial=%s | Pool (%.3fs): %d candidatos | Reranking (%.3fs): %s",
                total_time,
                expansion_time,
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
            "[ROUTER TELEMETRY] Total: %.3fs | Expansion (%.3fs) | QueryVetorial=%s | Pool (%.3fs): %d candidatos | Reranking (%.3fs): %s",
            total_time,
            expansion_time,
            termos_consulta_log,
            pool_time,
            pool_size,
            reranking_time,
            ids_escolhidos_log,
        )
        return resultado
