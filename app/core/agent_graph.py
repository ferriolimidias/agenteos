from __future__ import annotations

import os
import asyncio
import logging
import json
from typing import TypedDict, List, Optional, Literal, Any
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field, StrictStr
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL_CONVERSATION = os.getenv("LOG_LEVEL_CONVERSATION", "INFO").upper()
try:
    MAX_MSGS_CONTEXT = max(1, int(os.getenv("MAX_MSGS_CONTEXT", "20")))
except (TypeError, ValueError):
    MAX_MSGS_CONTEXT = 20


def _conversation_debug_enabled() -> bool:
    return LOG_LEVEL_CONVERSATION == "DEBUG"


def _conversation_debug_log(message: str, flush: bool = False) -> None:
    if _conversation_debug_enabled():
        print(message, flush=flush)

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# Removido LLM global: será instanciado via get_llm
embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")

async def get_llm(empresa_id: str | None = None, modelo_ia: str | None = None) -> Any:
    api_key = None
    if empresa_id:
        try:
            import uuid
            if isinstance(empresa_id, str):
                empresa_uuid = uuid.UUID(empresa_id)
            else:
                empresa_uuid = empresa_id
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
                empresa = result.scalars().first()
                if empresa:
                    if empresa.credenciais_canais:
                        api_key = empresa.credenciais_canais.get("openai_api_key")
                    if not modelo_ia:
                        modelo_ia = empresa.modelo_ia
        except Exception as e:
            print(f"Erro ao buscar credenciais IA: {e}")
            pass
            
    from app.api.utils import get_llm_model
    try:
        return get_llm_model(modelo_ia or "gpt-4o-mini", api_key=api_key)
    except Exception as e:
        print(f"Erro instanciando modelo {modelo_ia}: {e}")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model="gpt-4o-mini", temperature=0.7)

from db.database import AsyncSessionLocal
from db.models import Conhecimento, Especialista, Empresa, CRMLead, CRMFunil, CRMEtapa, FerramentaAPI, MensagemHistorico
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from app.core.dynamic_tools import create_dynamic_tool, _create_pydantic_model_from_json_schema
from app.services.transferencia_service import (
    executar_transferencia_atendimento,
)
from app.services.ferramentas_service import (
    criar_tool_rag_contextual,
    criar_tool_transferencia_contextual,
)
from app.services.semantic_router import SemanticRouterService
from app.services.tag_crm_service import listar_tags_crm_para_prompt
from app.core.tools import tool_atualizar_tags_lead
from app.services.websocket_manager import manager
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent, ToolNode
import httpx

logger = logging.getLogger(__name__)

async def disparar_webhook_saida(lead_id: str):
    import uuid
    from db.models import WebhookSaida
    try:
        lead_uuid = uuid.UUID(lead_id)
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead)
                .where(CRMLead.id == lead_uuid)
                .options(selectinload(CRMLead.etapa))
            )
            lead = result_lead.scalars().first()
            if not lead:
                return
                
            result_wh = await session.execute(
                select(WebhookSaida).where(
                    WebhookSaida.empresa_id == lead.empresa_id,
                    WebhookSaida.ativo == True
                )
            )
            webhook = result_wh.scalars().first()
            
            if webhook and webhook.url:
                payload = {
                    "evento": "lead_atualizado",
                    "telefone": lead.telefone_contato,
                    "nome": lead.nome_contato,
                    "etapa_crm": lead.etapa.nome if lead.etapa else None
                }
                async with httpx.AsyncClient() as client:
                    await client.post(webhook.url, json=payload, timeout=5.0)
    except Exception as e:
        print(f"Erro ao disparar webhook: {e}")

# Mapeamento de funções Nativas para as Ferramentas do banco (FerramentaAPI)
async def avancar_etapa_crm(lead_id: str, nova_etapa_id: str) -> str:
    import uuid
    try:
        lead_uuid = uuid.UUID(lead_id)
        etapa_uuid = uuid.UUID(nova_etapa_id)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CRMLead).where(CRMLead.id == lead_uuid).values(etapa_id=etapa_uuid)
            )
            await session.commit()
            
        asyncio.create_task(disparar_webhook_saida(lead_id))
        
        return f"Sucesso: o lead de ID {lead_id} foi movido para a etapa {nova_etapa_id}."
    except Exception as e:
        return f"Erro ao atualizar etapa do CRM: {str(e)}"

async def consultar_agenda(data_inicio: str, data_fim: str) -> str:
    return f"Busca realizada de {data_inicio} até {data_fim}. Resposta Mock: A agenda está livre."

async def transferir_para_humano(telefone: str, empresa_id: str) -> str:
    import uuid
    from datetime import datetime, timedelta
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(CRMLead).where(CRMLead.telefone_contato == telefone, CRMLead.empresa_id == empresa_uuid)
            )
            lead = result.scalars().first()
            if lead:
                # Update bot_pausado_ate to +24h
                lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=24)
                
                # Fetch standard funil
                result_funil = await session.execute(
                    select(CRMFunil).where(CRMFunil.empresa_id == empresa_uuid)
                )
                funil = result_funil.scalars().first()
                if funil:
                    # Find etapa 'Aguardando Humano'
                    result_etapa = await session.execute(
                        select(CRMEtapa).where(
                            CRMEtapa.funil_id == funil.id,
                            CRMEtapa.nome == 'Aguardando Humano'
                        )
                    )
                    etapa = result_etapa.scalars().first()
                    if not etapa:
                        nova_etapa = CRMEtapa(funil_id=funil.id, nome="Aguardando Humano", ordem=99)
                        session.add(nova_etapa)
                        await session.flush()
                        lead.etapa_id = nova_etapa.id
                    else:
                        lead.etapa_id = etapa.id
                
                await session.commit()
                
                asyncio.create_task(disparar_webhook_saida(str(lead.id)))
                
                return "Transferência solicitada internamente com sucesso. Avise o cliente de forma amigável que um especialista humano assumirá o atendimento em breve."
            return "Lead não encontrado para realizar a transferência."
    except Exception as e:
        return f"Erro ao transferir para humano: {str(e)}"


class ActionTransferirAtendimentoInput(BaseModel):
    destino_id: str = Field(
        description="UUID do destino de transferência que deve receber o transbordo."
    )
    resumo_conversa: str = Field(
        description="Resumo curto e objetivo do motivo da transferência e do que o cliente precisa."
    )


class ToolAtualizarTagsLeadInput(BaseModel):
    lead_id: str = Field(description="UUID do lead atual que deve receber a classificação por tags.")
    tags: List[str] = Field(description="Lista de tags oficiais que devem ser aplicadas ao lead.")


async def node_especialista_tags(state: AgentState):
    lead_id = state.get("lead_id")
    empresa_id = state.get("empresa_id")
    ultima_mensagem = state["mensagens"][-1] if state["mensagens"] else ""
    tags_crm_prompt = await listar_tags_crm_para_prompt(empresa_id) if empresa_id else ""

    if not lead_id or not empresa_id or not tags_crm_prompt:
        return state

    llm = await get_llm(empresa_id)

    async def _tool(lead_id: str, tags: List[str]) -> str:
        return await tool_atualizar_tags_lead(lead_id=lead_id, tags=tags)

    tool_tags = StructuredTool(
        name="tool_atualizar_tags_lead",
        description="Aplica tags oficiais do CRM ao lead atual com base nas regras de classificação.",
        args_schema=ToolAtualizarTagsLeadInput,
        coroutine=_tool,
    )

    prompt = f"""Você é um extrator técnico de dados de classificação.
Use a tool 'tool_atualizar_tags_lead' seguindo estritamente estas regras:
{tags_crm_prompt}

Lead atual: {lead_id}

Classifique o lead apenas com tags oficiais coerentes com a conversa.
Retorne APENAS dados crus da operação (resultado da tool, tags aplicadas, erros).
NÃO redija mensagem para o cliente final."""

    llm_with_tools = llm.bind_tools([tool_tags])
    tool_node = ToolNode([tool_tags])
    llm_extracao = llm.with_structured_output(ExtracaoEspecialista)

    from langchain_core.messages import HumanMessage, SystemMessage

    mensagens = [SystemMessage(content=prompt), HumanMessage(content=ultima_mensagem)]
    dados_crus_tags: list[str] = []
    fontes_tags: list[str] = []
    erros_tags: list[str] = []

    for _ in range(4):
        resposta = await llm_with_tools.ainvoke(mensagens)
        mensagens.append(resposta)
        if hasattr(resposta, "tool_calls") and resposta.tool_calls:
            for t in resposta.tool_calls:
                nome_tool = str(t.get("name", "")).strip()
                if nome_tool:
                    fontes_tags.append(nome_tool)
            resultado_toolnode = await tool_node.ainvoke({"messages": [resposta]})
            mensagens.extend(resultado_toolnode["messages"])
            for tool_msg in resultado_toolnode.get("messages", []):
                conteudo = str(getattr(tool_msg, "content", "") or "").strip()
                if conteudo:
                    dados_crus_tags.append(conteudo)
                    if "erro" in conteudo.lower() or "falha" in conteudo.lower():
                        erros_tags.append(conteudo)
        else:
            conteudo_final = str(getattr(resposta, "content", "") or "").strip()
            if conteudo_final:
                dados_crus_tags.append(conteudo_final)
                if "erro" in conteudo_final.lower() or "falha" in conteudo_final.lower():
                    erros_tags.append(conteudo_final)
            break

    dados_crus = "\n".join(dados_crus_tags).strip() or "Sem dados crus retornados na classificação."
    fontes_unicas = sorted(list({f for f in fontes_tags if str(f).strip()}))
    erros_unicos = sorted(list({e for e in erros_tags if str(e).strip()}))
    extracao = await llm_extracao.ainvoke(
        [
            (
                "system",
                "Converta o material bruto em um objeto ExtracaoEspecialista. "
                "Preserve os dados tecnicos, mantenha apenas fatos, sem linguagem ao cliente."
            ),
            (
                "user",
                f"dados_brutos:\n{dados_crus}\n\nfontes_detectadas:\n{fontes_unicas}\n\nerros_detectados:\n{erros_unicos}",
            ),
        ]
    )
    state["respostas_especialistas"].append(
        f"[ESPECIALISTA: tags_crm] {json.dumps(extracao.model_dump(), ensure_ascii=False)}"
    )
    state["intencao"] = [item for item in (state.get("intencao") or []) if item != "tags_crm"]
    return state


def criar_ferramenta_transferir_atendimento_contextual(
    lead_id: str,
    empresa_id: str,
    conexao_id: Optional[str] = None,
) -> StructuredTool:
    async def _tool(destino_id: str, resumo_conversa: str) -> str:
        return await executar_transferencia_atendimento(
            empresa_id=empresa_id,
            lead_id=lead_id,
            destino_id=destino_id,
            resumo_conversa=resumo_conversa,
            conexao_id_atual=conexao_id,
        )

    return StructuredTool(
        name="action_transferir_atendimento",
        description=(
            "Executa o transbordo real do atendimento para um destino humano configurado. "
            "Use quando o cenário corresponder às instruções de ativação do destino e inclua um resumo objetivo da conversa."
        ),
        args_schema=ActionTransferirAtendimentoInput,
        coroutine=_tool,
    )

MAP_FUNCOES_NATIVAS = {
    "avancar_etapa_crm": avancar_etapa_crm,
    "consultar_agenda": consultar_agenda,
    "transferir_para_humano": transferir_para_humano,
}

async def ler_dados_empresa(empresa_uuid) -> tuple:
    if not empresa_uuid:
        return "Empresa Padrão", ""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
        empresa = result.scalars().first()
        if empresa:
            return empresa.nome_empresa, empresa.area_atuacao or ""
        return "Empresa Padrão", ""
from langgraph.prebuilt import create_react_agent

async def buscar_conhecimento(pergunta: str, empresa_uuid):
    print(f"[RAG] Buscando conhecimento para a pergunta: '{pergunta}' na empresa {empresa_uuid}")
    try:
        pergunta_embedding = await embeddings_model.aembed_query(pergunta)

        async with AsyncSessionLocal() as session:
            # Busca vetorial filtrada por empresa e ordenada pela distância de cosseno
            query = select(Conhecimento).where(
                Conhecimento.empresa_id == empresa_uuid
            ).order_by(
                Conhecimento.embedding.cosine_distance(pergunta_embedding)
            ).limit(3)

            resultado = await session.execute(query)
            trechos = resultado.scalars().all()

            if not trechos:
                return {"dados": "", "fontes": [], "erros": []}

            dados = "\n\n".join([f"Contexto {i+1}:\n{t.conteudo}" for i, t in enumerate(trechos)])
            fontes = sorted(
                {
                    str(t.source_name).strip()
                    for t in trechos
                    if getattr(t, "source_name", None) and str(t.source_name).strip()
                }
            )
            return {"dados": dados, "fontes": fontes, "erros": []}
    except Exception as e:
        return {"dados": "", "fontes": [], "erros": [f"Erro ao buscar conhecimento: {str(e)}"]}

# 1. Definir o Estado
class EspecialistaSelecionadoState(TypedDict):
    id: str
    nome: str
    prompt_sistema: str
    usar_rag: bool


class AgentState(TypedDict):
    empresa_id: str
    identificador_origem: str
    canal: str
    conexao_id: Optional[str]
    mensagens: list
    historico_bd: str          # Histórico real do PostgreSQL, formatado
    nome_contato: Optional[str]
    intencao: List[str]
    especialistas_selecionados: List[EspecialistaSelecionadoState]
    super_contexto_especialistas: str
    respostas_especialistas: List[str]
    handoff_requested: bool
    resposta_final: Optional[str]
    status_conversa: Optional[str]
    lead_id: Optional[str]

# 1.5. Modelos de Estruturação
class DecisaoAtendente(BaseModel):
    precisa_roteamento: bool = Field(description="True se a solicitação exigir buscar dados de terceiros (preços, agenda, sistema, técnicos, etc) ou repasse humano. False se for uma interação que você pode responder apenas com o histórico e seu contexto (ex: saudações, small talk).")
    resposta: Optional[str] = Field(description="A mensagem formulada para o cliente caso não precise rotear. Nula caso precisa_roteamento seja True.")
    status_conversa: Literal['ABERTA', 'ENCERRADA'] = Field(description="ENCERRADA se o cliente encerrou a conversa com clareza, senão ABERTA.")

class AnaliseRoteador(BaseModel):
    intencao: List[str] = Field(description="Lista OBRIGATÓRIA com a(s) intenção(ões) principal(is) do usuário. Deve conter o nome exato dos especialistas relevantes. Ex: ['Comercial', 'Suporte'].")
    handoff_requested: bool = Field(description="True se o usuário pediu explicitamente para falar com humano ou atendente.")


class ExtracaoEspecialista(BaseModel):
    model_config = {"extra": "forbid"}
    dados: StrictStr = Field(description="Dados crus extraidos pelo especialista.")
    fontes: List[str] = Field(default_factory=list, description="Ferramentas, APIs ou documentos usados na extracao.")
    erros: List[str] = Field(default_factory=list, description="Falhas tecnicas encontradas durante a extracao.")

# 2. Nós
async def node_crm(state: AgentState):
    print(f"[NODE CRM] Consultando banco para o identificador: {state['identificador_origem']} ({state['canal']})")
    
    origem = state.get("identificador_origem", "")
    empresa_id = state.get("empresa_id")
    mensagens_pendentes = [str(msg or "").strip() for msg in (state.get("mensagens") or []) if str(msg or "").strip()]
    
    # 1. Verifica se já existe um Lead para este telefone nesta empresa
    async with AsyncSessionLocal() as session:
        # Busca lead
        result = await session.execute(
            select(CRMLead)
            .where(CRMLead.telefone_contato == origem, CRMLead.empresa_id == empresa_id)
        )
        lead = result.scalars().first()
        
        if lead:
            nome_recebido = str(state.get("nome_contato") or "").strip()
            nome_atual = str(lead.nome_contato or "").strip()
            if nome_recebido and (not nome_atual or nome_atual == "Usuário (Auto)"):
                lead.nome_contato = nome_recebido
                await session.commit()
            state["nome_contato"] = lead.nome_contato
            state["lead_id"] = str(lead.id)
            print(f"[NODE CRM] Lead existente encontrado. ID: {state['lead_id']}")
        else:
            print(f"[NODE CRM] Lead não encontrado. Iniciando criação automática...")
            
            nome_recebido = str(state.get("nome_contato") or "").strip()
            possivel_nome = nome_recebido or "Usuário (Auto)"
            if "novo" in origem.lower(): 
                possivel_nome = nome_recebido or None

            try:
                # Buscar o funil padrao ou criar se não existir
                result_funil = await session.execute(
                    select(CRMFunil)
                    .where(CRMFunil.empresa_id == empresa_id)
                    .options(selectinload(CRMFunil.etapas))
                )
                funil = result_funil.scalars().first()
                
                etapa_inicial_id = None
                
                if funil and funil.etapas:
                    # Pega a primeira etapa (ordem)
                    etapa_inicial = min(funil.etapas, key=lambda x: x.ordem)
                    etapa_inicial_id = etapa_inicial.id
                elif not funil:
                    # Precisa criar o funil padrao aqui também caso chegue a msg antes do admin abrir o relatorio
                    novo_funil = CRMFunil(empresa_id=empresa_id, nome="Pipeline Padrão")
                    session.add(novo_funil)
                    await session.flush()
                    
                    etapa_padrao = CRMEtapa(funil_id=novo_funil.id, nome="Novo Lead", ordem=1)
                    session.add(etapa_padrao)
                    session.add(CRMEtapa(funil_id=novo_funil.id, nome="Em Atendimento", ordem=2))
                    session.add(CRMEtapa(funil_id=novo_funil.id, nome="Fechado", ordem=3))
                    await session.flush()
                    
                    etapa_inicial_id = etapa_padrao.id
                
                # Criar Lead
                if possivel_nome:
                    import uuid

                    try:
                        conexao_uuid = uuid.UUID(str(state.get("conexao_id"))) if state.get("conexao_id") else None
                    except (ValueError, TypeError):
                        conexao_uuid = None

                    novo_lead = CRMLead(
                        empresa_id=empresa_id,
                        telefone_contato=origem,
                        nome_contato=possivel_nome,
                        etapa_id=etapa_inicial_id,
                        historico_resumo="Lead capturado automaticamente via integração."
                    )
                    session.add(novo_lead)
                    await session.flush()

                    primeira_msg_inbound = None
                    for texto_inbound in mensagens_pendentes:
                        nova_msg = MensagemHistorico(
                            lead_id=novo_lead.id,
                            conexao_id=conexao_uuid,
                            texto=texto_inbound,
                            from_me=False,
                        )
                        session.add(nova_msg)
                        if primeira_msg_inbound is None:
                            primeira_msg_inbound = nova_msg

                    await session.commit()
                    await session.refresh(novo_lead)
                    
                    state["nome_contato"] = novo_lead.nome_contato
                    state["lead_id"] = str(novo_lead.id)
                    print(f"[NODE CRM] Novo Lead criado com sucesso. ID: {state['lead_id']}")

                    if primeira_msg_inbound:
                        mensagem_payload = {
                            "id": str(primeira_msg_inbound.id),
                            "texto": str(primeira_msg_inbound.texto or ""),
                            "from_me": bool(primeira_msg_inbound.from_me),
                            "tipo_mensagem": str(primeira_msg_inbound.tipo_mensagem or "text"),
                            "media_url": str(primeira_msg_inbound.media_url) if primeira_msg_inbound.media_url else None,
                            "criado_em": primeira_msg_inbound.criado_em.isoformat() if primeira_msg_inbound.criado_em else None,
                        }
                        await manager.broadcast_to_empresa(
                            str(empresa_id),
                            {
                                "tipo_evento": "nova_mensagem_inbound",
                                "telefone": origem,
                                "mensagem": mensagem_payload,
                            },
                        )
                else:
                    state["nome_contato"] = None
                    state["lead_id"] = None
                    print(f"[NODE CRM] Não há nome suficiente para criar Lead agora. Roteando para captura.")

            except Exception as e:
                print(f"[NODE CRM] Erro ao criar lead auto: {e}")
                await session.rollback()
                state["nome_contato"] = None
                state["lead_id"] = None
                
    return state

async def node_capturar_nome(state: AgentState):
    print(f"[NODE CAPTURAR NOME] Gerando mensagem amigável para perguntar o nome...")
    
    prompt = "Você é um assistente virtual prestativo. O usuário iniciou a conversa, mas não sabemos o nome dele. Gere uma mensagem curta, simpática e humana pedindo o nome dele."
    ultima_mensagem = state["mensagens"][-1] if state["mensagens"] else ""
    
    llm = await get_llm(state.get("empresa_id"))
    resposta = await llm.ainvoke([("system", prompt), ("user", ultima_mensagem)])
    state["resposta_final"] = resposta.content

    return state

async def node_atendente(state: AgentState):
    print(f"[NODE ATENDENTE] Iniciando processamento...")
    import uuid
    from datetime import datetime
    from langchain_core.messages import SystemMessage

    # 1. Inicialização de Variáveis Locais (Prevenção de UnboundLocalError)
    empresa_id = state.get("empresa_id")
    lead_id = state.get("lead_id")
    historico_bd = state.get("historico_bd", "")
    respostas_especialistas = state.get("respostas_especialistas", [])
    especialistas_selecionados = state.get("especialistas_selecionados", []) or []
    super_contexto_especialistas = str(state.get("super_contexto_especialistas", "") or "").strip()
    mensagens_estado = state.get("mensagens") or []
    mensagens_recentes = mensagens_estado[-MAX_MSGS_CONTEXT:]
    identificador = state.get("identificador_origem")
    canal = state.get("canal")

    # Carrega configuração da empresa para garantir injeção de contexto no prompt.
    empresa = None
    saudacao_configurada = ""
    ia_instrucoes_personalizadas = ""
    ia_identidade = ""
    ia_regras_negocio = ""
    ia_estrategia_vendas = ""
    ia_formatacao_whatsapp = ""
    ia_tom_voz = ""
    if empresa_id:
        try:
            empresa_uuid = uuid.UUID(str(empresa_id))
            async with AsyncSessionLocal() as session:
                result_empresa = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
                empresa = result_empresa.scalars().first()
        except Exception as e:
            logger.error("[NODE ATENDENTE] Falha ao carregar empresa %s: %s", empresa_id, e)
            empresa = None
    else:
        logger.error("[NODE ATENDENTE] Estado sem empresa_id; não foi possível carregar configurações da empresa.")

    if empresa:
        saudacao_configurada = str(getattr(empresa, "mensagem_saudacao", "") or "").strip()
        ia_instrucoes_personalizadas = str(getattr(empresa, "ia_instrucoes_personalizadas", "") or "").strip()
        ia_identidade = str(getattr(empresa, "ia_identidade", "") or "").strip()
        ia_regras_negocio = str(getattr(empresa, "ia_regras_negocio", "") or "").strip()
        ia_estrategia_vendas = str(getattr(empresa, "ia_estrategia_vendas", "") or "").strip()
        ia_formatacao_whatsapp = str(getattr(empresa, "ia_formatacao_whatsapp", "") or "").strip()
        ia_tom_voz = str(getattr(empresa, "ia_tom_voz", "") or "").strip()

    if not empresa or not any([
        saudacao_configurada,
        ia_instrucoes_personalizadas,
        ia_identidade,
        ia_regras_negocio,
        ia_estrategia_vendas,
        ia_formatacao_whatsapp,
        ia_tom_voz,
    ]):
        logger.error(
            "[NODE ATENDENTE] Dados da empresa vazios ou incompletos para empresa_id=%s "
            "(saudacao=%s, instrucoes=%s, identidade=%s, regras=%s, estrategia=%s, formatacao=%s, tom_voz=%s)",
            empresa_id,
            bool(saudacao_configurada),
            bool(ia_instrucoes_personalizadas),
            bool(ia_identidade),
            bool(ia_regras_negocio),
            bool(ia_estrategia_vendas),
            bool(ia_formatacao_whatsapp),
            bool(ia_tom_voz),
        )
    
    # Helper para montar bloco de contexto XML
    def _bloco_xml(historico: str, respostas: list[str]) -> str:
        hist_content = historico.strip() if historico and historico.strip() \
            else "(nenhuma interacao anterior registrada)"
        esp_content = "\n".join(respostas).strip() if respostas \
            else "(nenhuma resposta de especialista neste turno)"
        return (
            f"<historico_conversa>\n{hist_content}\n</historico_conversa>\n\n"
            f"<respostas_especialistas>\n{esp_content}\n</respostas_especialistas>"
        )

    def _montar_system_prompt_modular(incluir_especialistas: bool = False) -> str:
        blocos: list[str] = []

        nome_agente = str(getattr(empresa, "nome_agente", "") or "").strip() or "Assistente Virtual"
        nome_empresa_prompt = str(getattr(empresa, "nome_empresa", "") or "").strip() or "Empresa"
        blocos.append(f"Você é {nome_agente}, assistente virtual da {nome_empresa_prompt}.")

        agora = datetime.now()
        dias_semana = [
            "segunda-feira",
            "terça-feira",
            "quarta-feira",
            "quinta-feira",
            "sexta-feira",
            "sábado",
            "domingo",
        ]
        dia_da_semana = dias_semana[agora.weekday()]
        data_formatada = agora.strftime("%d/%m/%Y")
        hora_formatada = agora.strftime("%H:%M")
        blocos.append(f"Contexto temporal: Hoje é {dia_da_semana}, {data_formatada} às {hora_formatada}.")

        identidade_prompt = ia_identidade or ia_instrucoes_personalizadas
        regras_prompt = ia_regras_negocio or ia_instrucoes_personalizadas
        estrategia_prompt = ia_estrategia_vendas or ia_instrucoes_personalizadas

        diretrizes_base = regras_prompt or "(não configuradas)"
        blocos.append(
            "Diretrizes de Atendimento: "
            f"{diretrizes_base}. "
            f"Tom de voz: {ia_tom_voz or '(não configurado)'}."
        )

        if identidade_prompt:
            blocos.append(f"Diretriz de Identidade da Marca: {identidade_prompt}")
        if estrategia_prompt:
            blocos.append(f"Estratégia Comercial/Vendas: {estrategia_prompt}")

        formatacao_base = (
            "DIRETRIZES DE ESTRUTURAÇÃO VISUAL E RENDERIZAÇÃO (WHATSAPP):\n"
            "- Separação de Balões: Sempre que mudar de um bloco de assunto para outro, use obrigatoriamente duas "
            "quebras de linha (\\n\\n). O sistema usa isso para separar as mensagens.\n"
            "- Respiro Visual: Nunca gere um parágrafo com mais de 3 linhas corridas.\n"
            "- Listas: Utilize o caractere '•' para itens de listas em vez de travessões.\n"
            "- Ênfase Visual: Use *negrito* apenas para destacar valores monetários e nomes de serviços cruciais.\n"
            "- Emojis: Aplique emojis no início de tópicos novos para facilitar a escaneabilidade."
        )
        blocos.append(formatacao_base)

        ja_respondeu = "Assistente:" in str(historico_bd or "")
        is_primeira_interacao = not ja_respondeu
        if is_primeira_interacao and saudacao_configurada:
            blocos.append(
                "Utilize a seguinte mensagem como base para sua saudação: "
                f"'{saudacao_configurada}'. "
                "REGRA DE CORDIALIDADE: Adapte o início da mensagem de forma natural para espelhar a cordialidade do usuário. "
                "Se ele disser 'Bom dia', 'Boa tarde' ou 'Boa noite', retribua a saudação adequadamente antes de entregar "
                "o conteúdo da mensagem base. Mantenha a essência, o tom de voz e a pergunta final da saudação "
                "configurada intactos."
            )

        if incluir_especialistas and respostas_especialistas:
            respostas_texto = "\n".join([str(r) for r in respostas_especialistas if str(r).strip()])
            blocos.append(
                "Baseie sua resposta RIGOROSAMENTE nas seguintes informações técnicas resolvidas pelos especialistas: "
                f"{respostas_texto}. "
                "Sintetize todas as informações em uma única resposta fluida e natural, sem dizer que consultou especialistas."
            )

        return "\n\n".join(blocos).strip()

    # Identifica primeiro contato pelo histórico persistido no banco.
    ja_respondeu = "Assistente:" in str(historico_bd or "")
    is_primeiro_contato = not ja_respondeu
    is_primeira_msg = is_primeiro_contato

    instrucao_primeiro_contato = ""
    if is_primeiro_contato:
        instrucao_primeiro_contato = (
            'REGRA DE PRIMEIRO CONTATO: Você DEVE iniciar sua resposta obrigatoriamente usando o estilo e as informações '
            'contidas em <saudacao_configurada>. Não invente saudações genéricas como "Como posso ajudar?" '
            "se houver uma saudação personalizada disponível."
        )

    # ── FASE DE SÍNTESE (Voltando dos Especialistas) ─────────────────────────────
    if respostas_especialistas:
        print(f"[NODE ATENDENTE] Fase de Síntese: Consolidando {len(respostas_especialistas)} resposta(s)...")

        bloco_xml = _bloco_xml(historico_bd, respostas_especialistas)

        bloco_xml_sintese = _bloco_xml(historico_bd, respostas_especialistas)

        prompt_sintese = f"""{_montar_system_prompt_modular(incluir_especialistas=True)}

{bloco_xml_sintese}

<instrucao_final>
Você recebeu os seguintes DADOS CRUS dos especialistas do sistema em <respostas_especialistas>, no formato JSON com os campos: "dados", "fontes" e "erros".
Sua tarefa é ler esses dados e formular uma resposta natural, educada e coesa para o cliente, assumindo a persona da empresa.
Você também recebeu um super-contexto técnico consolidado dos especialistas selecionados para esta pergunta.
IMPORTANTE: podem existir múltiplos especialistas cobrindo assuntos diferentes (ex.: curso, preço, agenda, suporte).
Você DEVE mesclar tudo em uma única resposta integrada e fluida, sem segmentar por especialista e sem parecer múltiplas vozes.
Quando houver pontos complementares, conecte-os com transições naturais para o cliente perceber uma única resposta contínua.
Não mencione nomes de especialistas, ferramentas, APIs, bancos internos ou roteamento.
Se houver "erros" no JSON de algum especialista, informe ao cliente de forma educada que houve uma limitação técnica ao buscar aquela informação específica.
Baseie a resposta principalmente no campo "dados" de cada JSON, combinado com o histórico da conversa.
Considere o seguinte super-contexto como fonte adicional para cobrir todas as partes da pergunta do usuário:
<super_contexto_especialistas>
{super_contexto_especialistas or '(sem super-contexto consolidado)'}
</super_contexto_especialistas>
Especialistas selecionados neste turno: {[esp.get('nome') for esp in especialistas_selecionados] if especialistas_selecionados else ['(nenhum)']}
Responda em uma única mensagem clara e objetiva.
Você DEVE aplicar rigorosamente o tom definido em <ia_tom_voz> em toda a resposta final.
{instrucao_primeiro_contato}
REGRA DE PRIMEIRO CONTATO: Se existir a tag <saudacao_obrigatoria> no seu contexto, significa que esta é a primeira resposta que o cliente vai receber. Nesse caso, você DEVE iniciar sua resposta incorporando a essência e a simpatia dessa saudação oficial, e logo em seguida entregar os dados que o especialista extraiu de forma natural.
</instrucao_final>"""

        _conversation_debug_log(f"--- PROMPT FINAL ATENDENTE (SINTESE) ---\n{prompt_sintese}", flush=True)
        mensagens_para_llm = [SystemMessage(content=prompt_sintese)] + mensagens_recentes
        llm = await get_llm(empresa_id)
        resposta = await llm.ainvoke(mensagens_para_llm)

        state["resposta_final"] = resposta.content
        state["respostas_especialistas"] = []
        state["intencao"] = []
        state["especialistas_selecionados"] = []
        state["super_contexto_especialistas"] = ""

        return state

    # ── FASE INICIAL (Recepção e Decisão) ────────────────────────────────────────
    print("[NODE ATENDENTE] Fase Inicial: Avaliando small-talk ou roteamento...")

    bloco_xml = _bloco_xml(historico_bd, [])

    prompt_decisao = f"""{_montar_system_prompt_modular(incluir_especialistas=False)}

{bloco_xml}

<instrucao_decisao>
Avalie a última mensagem do cliente considerando TODO o <historico_conversa>.
Regra principal: em caso de dúvida, classifique como precisa_roteamento=True.
Somente use precisa_roteamento=False quando for small talk puro (cumprimento, agradecimento, despedida ou conversa social sem pedido operacional).
Defina precisa_roteamento=True obrigatoriamente para qualquer solicitação que envolva:
- consulta a sistemas, APIs, bancos, ferramentas, integrações ou dados externos;
- suporte técnico, diagnóstico, status, disponibilidade, preços, prazos, agendamento ou segunda via;
- cálculos, verificações, validações, comparações, busca de endereço/CEP, tracking, protocolo ou pedidos com campos estruturados;
- intenção desconhecida, ambígua, incompleta, ou qualquer pedido que você não possa resolver somente com conversa.
Quando precisa_roteamento=True, deixe 'resposta' como nula.
É proibido responder com limitação do tipo "não consigo consultar" na fase de decisão; nesses casos, roteie.
Se for small talk puro, use precisa_roteamento=False e forneça sua resposta em 'resposta'. ATENÇÃO VITAL: Se existir a tag <saudacao_obrigatoria> no seu contexto, você é PROIBIDO de inventar uma saudação genérica. Você DEVE usar o texto da <saudacao_obrigatoria> como a BASE exata da sua resposta. Adapte-a levemente ao contexto se o cliente tiver feito algum comentário extra, mas garanta que a essência, as perguntas e a identidade da saudação original sejam entregues ao cliente.
Você DEVE aplicar rigorosamente o tom definido em <ia_tom_voz> nas saudações e em qualquer resposta ao cliente.
{instrucao_primeiro_contato}
</instrucao_decisao>"""

    _conversation_debug_log(f"--- PROMPT FINAL ATENDENTE (DECISAO) ---\n{prompt_decisao}", flush=True)
    mensagens_para_llm = [SystemMessage(content=prompt_decisao)] + mensagens_recentes

    llm = await get_llm(empresa_id)
    llm_json = llm.with_structured_output(DecisaoAtendente)
    resultado = await llm_json.ainvoke(mensagens_para_llm)

    state["status_conversa"] = resultado.status_conversa

    ultima_mensagem = (state.get("mensagens") or [""])[-1]
    texto_ultima = str(ultima_mensagem or "").strip().lower()

    # Guarda de segurança para reduzir falso "small talk":
    # se houver qualquer sinal de consulta operacional, ferramenta, cálculo,
    # CEP ou intenção não social clara, força roteamento para o supervisor.
    import re

    def _eh_small_talk_puro(texto: str) -> bool:
        if not texto:
            return False
        if "?" in texto:
            return False
        if re.search(r"\d{2,}", texto):
            return False
        tokens = re.findall(r"[a-zA-ZÀ-ÿ0-9_]+", texto.lower())
        if not tokens:
            return False
        small_talk_vocab = {
            "oi", "ola", "olá", "bom", "boa", "dia", "tarde", "noite",
            "tudo", "bem", "obrigado", "obrigada", "valeu", "blz", "beleza",
            "show", "perfeito", "entendi", "ok", "okay", "flw", "falou",
            "tchau", "ate", "até", "mais", "vlw", "obg"
        }
        return len(tokens) <= 8 and all(t in small_talk_vocab for t in tokens)

    marcadores_roteamento = [
        "cep", "preco", "preço", "valor", "agenda", "agendar", "horario", "horário",
        "disponibilidade", "suporte", "erro", "problema", "consultar", "consulta",
        "buscar", "calcular", "calculo", "cálculo", "status", "rastreio",
        "codigo", "código", "protocolo", "sistema", "api", "ferramenta", "integracao", "integração"
    ]
    resposta_modelo = (resultado.resposta or "").lower()
    respondeu_limitacao = any(
        trecho in resposta_modelo
        for trecho in ["não consigo", "nao consigo", "não posso", "nao posso", "não tenho acesso", "nao tenho acesso"]
    )
    tem_padrao_cep = re.search(r"\b\d{5}-?\d{3}\b", texto_ultima) is not None
    precisa_forcar_roteamento = (
        (not _eh_small_talk_puro(texto_ultima))
        and (
            # Se não for small talk puro, tratar por padrão como rota para especialistas.
            # Mantém no atendente apenas cumprimentos/agradecimentos/despedidas.
            bool(texto_ultima)
            or
            any(m in texto_ultima for m in marcadores_roteamento)
            or tem_padrao_cep
            or respondeu_limitacao
        )
    )

    if not resultado.precisa_roteamento and precisa_forcar_roteamento:
        print("[NODE ATENDENTE] Guarda de segurança acionada: forçando roteamento para o Supervisor.")
        resultado.precisa_roteamento = True
        resultado.resposta = None

    if not resultado.precisa_roteamento and resultado.resposta:
        state["resposta_final"] = resultado.resposta
        state["intencao"] = []
        state["respostas_especialistas"] = []
        state["especialistas_selecionados"] = []
        state["super_contexto_especialistas"] = ""
        print(f"[NODE ATENDENTE] Resolvido via Small Talk: {resultado.resposta}")
    else:
        print("[NODE ATENDENTE] Optou por acionar Roteador.")
        state["resposta_final"] = None
        state["respostas_especialistas"] = []
        state["especialistas_selecionados"] = []
        state["super_contexto_especialistas"] = ""

    return state

async def node_roteador_maestro(state: AgentState):
    print("[NODE ROTEADOR] Roteamento semântico via embeddings...")
    import uuid

    empresa_id = state.get("empresa_id")
    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        empresa_uuid = None

    ultima_mensagem = str(state["mensagens"][-1] if state.get("mensagens") else "").strip()
    if not ultima_mensagem:
        state["intencao"] = []
        state["especialistas_selecionados"] = []
        state["super_contexto_especialistas"] = ""
        state["respostas_especialistas"] = []
        state["handoff_requested"] = False
        return state

    handoff_markers = (
        "humano",
        "atendente",
        "pessoa",
        "suporte humano",
        "falar com",
        "transferir",
    )
    state["handoff_requested"] = any(marker in ultima_mensagem.lower() for marker in handoff_markers)

    async with AsyncSessionLocal() as session:
        router_service = SemanticRouterService(session)
        especialistas_match = await router_service.route_multi_specialists(
            query_text=ultima_mensagem,
            empresa_id=str(empresa_uuid) if empresa_uuid else None,
        )

    if len(especialistas_match) > 1:
        def _score_similaridade(item: dict) -> float:
            for chave in ("score", "similaridade", "similarity", "similarity_score"):
                valor = item.get(chave)
                if isinstance(valor, (int, float)):
                    return float(valor)
                try:
                    return float(valor)
                except (TypeError, ValueError):
                    continue
            return float("-inf")

        # Mantém todos os especialistas acima do threshold e apenas ordena por relevância.
        especialistas_match = sorted(
            especialistas_match,
            key=_score_similaridade,
            reverse=True,
        )

    ids_especialistas = [esp.get("id") for esp in especialistas_match]
    nomes_especialistas = [esp.get("nome") for esp in especialistas_match]
    print(
        f"[NODE ROTEADOR] Matches: {len(ids_especialistas)} "
        f"| IDs={ids_especialistas} | Nomes={nomes_especialistas}"
    )

    # Sem matches: volta para o Atendente Principal (bate-papo comum).
    state["especialistas_selecionados"] = especialistas_match
    state["respostas_especialistas"] = []
    state["super_contexto_especialistas"] = ""
    state["intencao"] = []
    return state

async def node_especialista_dinamico(state: AgentState):
    try:
        especialistas_selecionados = state.get("especialistas_selecionados", []) or []
        if not isinstance(especialistas_selecionados, list):
            especialistas_selecionados = []

        if not especialistas_selecionados:
            intencoes_legadas = state.get("intencao", [])
            if not isinstance(intencoes_legadas, list):
                intencoes_legadas = [intencoes_legadas] if intencoes_legadas else []
            especialistas_selecionados = [
                {"id": str(item), "nome": str(item), "prompt_sistema": "", "usar_rag": False}
                for item in intencoes_legadas
                if str(item or "").strip()
            ]

        intencoes = [
            str(item.get("id") or item.get("nome") or "").strip()
            for item in especialistas_selecionados
            if isinstance(item, dict)
        ]
        metadados_por_id = {
            str(item.get("id") or item.get("nome") or "").strip(): item
            for item in especialistas_selecionados
            if isinstance(item, dict) and str(item.get("id") or item.get("nome") or "").strip()
        }
        if not intencoes:
            return state

        print(f"[NODE ESPECIALISTA DINAMICO] Especialistas acionados: {intencoes} para {state['nome_contato']}.")
        ultima_mensagem = state["mensagens"][-1] if state["mensagens"] else ""

        import uuid
        empresa_id = state.get("empresa_id")
        try:
            empresa_uuid = uuid.UUID(empresa_id)
        except (ValueError, TypeError):
            empresa_uuid = None

        nome_empresa, area_atuacao = await ler_dados_empresa(empresa_uuid)
        contexto_empresa = f"Empresa: {nome_empresa}"
        if area_atuacao:
            contexto_empresa += f" | Área de atuação: {area_atuacao}.\n"
        else:
            contexto_empresa += ".\n"

        respostas_existentes = state.get("respostas_especialistas") or []
        if not isinstance(respostas_existentes, list):
            respostas_existentes = []
        state["respostas_especialistas"] = list(respostas_existentes)
        blocos_super_contexto: list[str] = []
        lead_id = state.get("lead_id")
        conexao_id = state.get("conexao_id")

        def _tool_name_safe(raw_name: str) -> str:
            nome = str(raw_name or "").strip().replace(" ", "_").lower()
            nome = "".join(ch for ch in nome if ch.isalnum() or ch == "_")
            return nome or "tool_sem_nome"

        for intencao in intencoes:
            meta_especialista = metadados_por_id.get(str(intencao), {})
            nome_especialista_meta = str(meta_especialista.get("nome") or intencao)
            prompt_especialista_meta = str(meta_especialista.get("prompt_sistema") or "").strip()
            usar_rag_meta = bool(meta_especialista.get("usar_rag", False))
            dados_crus_partes: list[str] = []
            fontes_usadas: list[str] = []
            erros_extracao: list[str] = []
            tools_disponiveis: list[Any] = []
            descricoes_tools: list[str] = []
            nomes_tools_registradas: set[str] = set()

            especialista_db = None
            nome_especialista_resultado = nome_especialista_meta
            especialista_id_uuid = None
            try:
                especialista_id_uuid = uuid.UUID(str(intencao))
            except (ValueError, TypeError):
                especialista_id_uuid = None

            prompt_base = (
                "Você é um extrator de dados.\n"
                "Use as ferramentas disponíveis para buscar a informação solicitada.\n"
                "Retorne APENAS dados brutos encontrados, em JSON simples ou tópicos diretos.\n"
                "NÃO redija mensagens para o cliente final.\n"
            )

            # ETAPA 1: montar tools sem I/O de banco no construtor das ferramentas.
            try:
                if empresa_uuid:
                    async with AsyncSessionLocal() as session:
                        filtros = [
                            Especialista.ativo == True,
                            Especialista.empresa_id == empresa_uuid,
                        ]
                        if especialista_id_uuid:
                            filtros.append(Especialista.id == especialista_id_uuid)
                        else:
                            filtros.append(Especialista.nome == intencao)

                        result = await session.execute(
                            select(Especialista)
                            .where(*filtros)
                            .options(
                                selectinload(Especialista.api_connections),
                                selectinload(Especialista.ferramentas),
                            )
                        )
                        especialista_db = result.scalars().first()

                if especialista_db:
                    nome_especialista_resultado = str(especialista_db.nome)
                    prompt_base += f"\nCONTEXTO_TECNICO_ESPECIALISTA:\n{especialista_db.prompt_sistema}\n"
                elif prompt_especialista_meta:
                    prompt_base += f"\nCONTEXTO_TECNICO_ESPECIALISTA:\n{prompt_especialista_meta}\n"

                usar_rag_final = bool(
                    usar_rag_meta or (especialista_db and getattr(especialista_db, "usar_rag", False))
                )
                if usar_rag_final and empresa_id:
                    rag_tool = criar_tool_rag_contextual(empresa_id=str(empresa_id))
                    tools_disponiveis.append(rag_tool)
                    nomes_tools_registradas.add(rag_tool.name)
                    descricoes_tools.append(
                        "- action_buscar_conhecimento_rag: consulta a base de conhecimento interna (RAG)."
                    )

                if lead_id and empresa_id and "action_transferir_atendimento" not in nomes_tools_registradas:
                    transferencia_tool = criar_tool_transferencia_contextual(
                        empresa_id=str(empresa_id),
                        lead_id=str(lead_id),
                        conexao_id=str(conexao_id) if conexao_id else None,
                    )
                    tools_disponiveis.append(transferencia_tool)
                    nomes_tools_registradas.add(transferencia_tool.name)
                    descricoes_tools.append(
                        "- action_transferir_atendimento: transfere o atendimento para um destino humano."
                    )

                for conexao in (especialista_db.api_connections if especialista_db else []):
                    try:
                        nova_tool = create_dynamic_tool(conexao)
                        tools_disponiveis.append(nova_tool)
                        nomes_tools_registradas.add(str(nova_tool.name))
                        desc = nova_tool.description if nova_tool.description else "Ferramenta sem descrição."
                        descricoes_tools.append(f"- {conexao.nome}: {desc}")
                    except Exception as e:
                        logger.exception(
                            "[NODE ESPECIALISTA DINAMICO][ETAPA 1] Falha ao instanciar API Connection '%s': %s",
                            conexao.nome,
                            e,
                        )
                        erros_extracao.append(f"Falha ao instanciar ferramenta {conexao.nome}: {e}")

                for f_db in (especialista_db.ferramentas if especialista_db else []):
                    try:
                        schema_dict = f_db.schema_parametros if f_db.schema_parametros else {}
                        if isinstance(schema_dict, str):
                            schema_dict = json.loads(schema_dict)

                        args_schema = _create_pydantic_model_from_json_schema(
                            schema_dict,
                            model_name=f"{_tool_name_safe(f_db.nome_ferramenta)}Args",
                        )
                        tool_name = _tool_name_safe(f_db.nome_ferramenta)

                        if f_db.nome_ferramenta in MAP_FUNCOES_NATIVAS:
                            nova_tool = StructuredTool(
                                name=tool_name,
                                description=f_db.descricao_ia,
                                args_schema=args_schema,
                                coroutine=MAP_FUNCOES_NATIVAS[f_db.nome_ferramenta],
                            )
                            tools_disponiveis.append(nova_tool)
                            nomes_tools_registradas.add(nova_tool.name)
                            descricoes_tools.append(f"- {tool_name}: {f_db.descricao_ia}")
                        elif getattr(f_db, "url", None):
                            headers_str = getattr(f_db, "headers", "{}")
                            payload_str = getattr(f_db, "payload", "{}")

                            def create_http_tool_coroutine(url, method, headers_json, payload_json, nome_tool):
                                async def http_tool_coroutine(**kwargs) -> str:
                                    import httpx

                                    try:
                                        h_dict = json.loads(headers_json) if headers_json else {}
                                    except Exception:
                                        h_dict = {}
                                    try:
                                        p_dict = json.loads(payload_json) if payload_json else {}
                                    except Exception:
                                        p_dict = {}

                                    try:
                                        final_url = url or ""
                                        for k, v in kwargs.items():
                                            final_url = final_url.replace(f"{{{{{k}}}}}", str(v))
                                            final_url = final_url.replace(f"{{{k}}}", str(v))

                                        if "{" in final_url or "}" in final_url:
                                            return f"Falha ao executar ferramenta {nome_tool}: URL template não preenchido."

                                        p_str = json.dumps(p_dict)
                                        for k, v in kwargs.items():
                                            p_str = p_str.replace(f"{{{{{k}}}}}", str(v))
                                        final_payload = json.loads(p_str)

                                        async with httpx.AsyncClient() as client:
                                            if str(method).upper() == "GET":
                                                resp = await client.get(final_url, headers=h_dict, timeout=10.0)
                                            elif str(method).upper() == "POST":
                                                resp = await client.post(final_url, headers=h_dict, json=final_payload, timeout=10.0)
                                            elif str(method).upper() == "PUT":
                                                resp = await client.put(final_url, headers=h_dict, json=final_payload, timeout=10.0)
                                            elif str(method).upper() == "DELETE":
                                                resp = await client.delete(final_url, headers=h_dict, timeout=10.0)
                                            else:
                                                return f"Método HTTP {method} não suportado."

                                        if resp.status_code >= 400:
                                            return f"Erro na requisição: {resp.status_code} - {resp.text}"
                                        return resp.text
                                    except Exception as e:
                                        logger.exception("[TOOL ERROR] Falha em '%s': %s", nome_tool, e)
                                        return f"Falha ao executar ferramenta {nome_tool}: {e}"

                                return http_tool_coroutine

                            nova_tool = StructuredTool(
                                name=tool_name,
                                description=f_db.descricao_ia,
                                args_schema=args_schema,
                                coroutine=create_http_tool_coroutine(
                                    f_db.url,
                                    f_db.metodo,
                                    headers_str,
                                    payload_str,
                                    tool_name,
                                ),
                            )
                            tools_disponiveis.append(nova_tool)
                            nomes_tools_registradas.add(nova_tool.name)
                            descricoes_tools.append(f"- {tool_name}: {f_db.descricao_ia}")
                    except Exception as e:
                        logger.exception(
                            "[NODE ESPECIALISTA DINAMICO][ETAPA 1] Falha ao instanciar ferramenta '%s': %s",
                            getattr(f_db, "nome_ferramenta", "desconhecida"),
                            e,
                        )
                        erros_extracao.append(f"Falha ao instanciar ferramenta nativa: {e}")

                nomes_tools = [
                    str(getattr(t, "name", "")).strip()
                    for t in tools_disponiveis
                    if str(getattr(t, "name", "")).strip()
                ]
                logger.info(
                    "[NODE ESPECIALISTA DINAMICO][ETAPA 1] Ferramentas montadas para '%s': %s",
                    nome_especialista_resultado,
                    nomes_tools,
                )
            except Exception as e:
                logger.exception(
                    "[NODE ESPECIALISTA DINAMICO][ETAPA 1] Falha na montagem de ferramentas para '%s': %s",
                    nome_especialista_resultado,
                    e,
                )
                erros_extracao.append(f"Falha na ETAPA 1 (ferramentas): {e}")

            system_message_adicional = f"\n{contexto_empresa}"
            if descricoes_tools:
                system_message_adicional += "\n\nFerramentas disponíveis:\n" + "\n".join(descricoes_tools)
                system_message_adicional += "\nUse-as quando necessário para obter dados técnicos."
            if state.get("handoff_requested", False):
                system_message_adicional += (
                    "\n\nO usuário pediu atendimento humano; priorize tool de transferência quando aplicável."
                )
            prompt_completo = prompt_base + system_message_adicional

            modelo_esp = especialista_db.modelo_ia if especialista_db and hasattr(especialista_db, "modelo_ia") else None
            llm = await get_llm(state.get("empresa_id"), modelo_ia=modelo_esp)
            resposta_parcial = ""

            llm_para_invocar = llm
            tool_node = None
            if tools_disponiveis:
                try:
                    logger.info(
                        "[NODE ESPECIALISTA DINAMICO][ETAPA 2] bind_tools para '%s' com %d tools",
                        nome_especialista_resultado,
                        len(tools_disponiveis),
                    )
                    llm_para_invocar = llm.bind_tools(tools_disponiveis)
                    tool_node = ToolNode(tools_disponiveis)
                except Exception as e:
                    logger.exception(
                        "[NODE ESPECIALISTA DINAMICO][ETAPA 2] Falha no bind_tools para '%s': %s",
                        nome_especialista_resultado,
                        e,
                    )
                    erros_extracao.append(f"Falha na ETAPA 2 (bind_tools): {e}")
                    llm_para_invocar = llm
                    tool_node = None

            # ETAPA 3: invoke
            try:
                from langchain_core.messages import HumanMessage, SystemMessage

                mensagens = [SystemMessage(content=prompt_completo), HumanMessage(content=ultima_mensagem)]
                for _ in range(5):
                    logger.info(
                        "[NODE ESPECIALISTA DINAMICO][ETAPA 3] Invocando LLM para '%s'",
                        nome_especialista_resultado,
                    )
                    resposta = await llm_para_invocar.ainvoke(mensagens)
                    mensagens.append(resposta)

                    if tool_node and hasattr(resposta, "tool_calls") and resposta.tool_calls:
                        nomes = [str(t.get("name", "")).strip() for t in resposta.tool_calls if str(t.get("name", "")).strip()]
                        fontes_usadas.extend(nomes)
                        resultado_toolnode = await tool_node.ainvoke({"messages": [resposta]})
                        mensagens.extend(resultado_toolnode.get("messages", []))
                        for tool_msg in resultado_toolnode.get("messages", []):
                            conteudo_tool = str(getattr(tool_msg, "content", "") or "").strip()
                            if conteudo_tool:
                                dados_crus_partes.append(conteudo_tool)
                                if "erro" in conteudo_tool.lower() or "falha" in conteudo_tool.lower():
                                    erros_extracao.append(conteudo_tool)
                        continue

                    resposta_parcial = str(getattr(resposta, "content", "") or "").strip()
                    break
                else:
                    resposta_parcial = "Não foi possível concluir após múltiplas tentativas."
                    erros_extracao.append(resposta_parcial)
            except Exception as e:
                logger.exception(
                    "[NODE ESPECIALISTA DINAMICO][ETAPA 3] Falha no invoke para '%s': %s",
                    nome_especialista_resultado,
                    e,
                )
                resposta_parcial = f"Falha na ETAPA 3 (invoke): {e}"
                erros_extracao.append(resposta_parcial)

            if resposta_parcial:
                dados_crus_partes.append(resposta_parcial)

            fontes_unicas = sorted({f for f in fontes_usadas if str(f).strip()})
            erros_unicos = sorted({e for e in erros_extracao if str(e).strip()})
            extracao = {
                "dados": str(resposta_parcial or ""),
                "fontes": list(fontes_unicas),
                "erros": list(erros_unicos),
            }
            state["respostas_especialistas"].append(
                f"[ESPECIALISTA: {nome_especialista_resultado}] {json.dumps(extracao, ensure_ascii=False)}"
            )

            prompt_para_super_contexto = (
                str(getattr(especialista_db, "prompt_sistema", "") or "").strip()
                if especialista_db
                else prompt_especialista_meta
            )
            blocos_super_contexto.append(
                "\n".join(
                    [
                        f"[ESPECIALISTA: {nome_especialista_resultado}]",
                        f"PROMPT_SISTEMA:\n{prompt_para_super_contexto or '(sem prompt técnico)'}",
                    ]
                )
            )

        super_contexto_existente = str(state.get("super_contexto_especialistas") or "").strip()
        super_contexto_novo = "\n\n".join(blocos_super_contexto).strip()
        state["super_contexto_especialistas"] = (
            f"{super_contexto_existente}\n\n{super_contexto_novo}".strip()
            if super_contexto_existente and super_contexto_novo
            else (super_contexto_novo or super_contexto_existente)
        )
        return state
    except Exception as e:
        logger.exception("[NODE ESPECIALISTA DINAMICO] Erro crítico no nó: %s", e)
        return {
            **state,
            "erros_extracao": [f"Erro crítico no nó: {str(e)}"],
            "dados": "Erro interno.",
        }

# Função condicional de roteamento
def router_crm(state: AgentState):
    if state.get("nome_contato") is None:
        return "capturar_nome"
    return "node_atendente"

# 3. Desenhar o Grafo
workflow = StateGraph(AgentState)

workflow.add_node("node_crm", node_crm)
workflow.add_node("node_capturar_nome", node_capturar_nome)
workflow.add_node("node_atendente", node_atendente)
workflow.add_node("node_roteador_maestro", node_roteador_maestro)
workflow.add_node("node_especialista_tags", node_especialista_tags)
workflow.add_node("node_especialista_dinamico", node_especialista_dinamico)

workflow.set_entry_point("node_crm")

workflow.add_conditional_edges(
    "node_crm",
    router_crm,
    {
        "capturar_nome": "node_capturar_nome",
        "node_atendente": "node_atendente"
    }
)

workflow.add_edge("node_capturar_nome", END)

def router_atendente(state: AgentState):
    if state.get("resposta_final"):
        return END
    return "node_roteador_maestro"

workflow.add_conditional_edges(
    "node_atendente",
    router_atendente,
    {
        END: END,
        "node_roteador_maestro": "node_roteador_maestro"
    }
)

def router_maestro(state: AgentState):
    intencoes = state.get("intencao") or []
    especialistas_selecionados = state.get("especialistas_selecionados") or []
    if not intencoes and not especialistas_selecionados:
        return "node_atendente"
    if "tags_crm" in intencoes:
        return "node_especialista_tags"
    if especialistas_selecionados:
        return "node_especialista_dinamico"
    return "node_atendente"


workflow.add_conditional_edges(
    "node_roteador_maestro",
    router_maestro,
    {
        "node_atendente": "node_atendente",
        "node_especialista_tags": "node_especialista_tags",
        "node_especialista_dinamico": "node_especialista_dinamico",
    }
)

def router_pos_tags(state: AgentState):
    especialistas_selecionados = state.get("especialistas_selecionados") or []
    if especialistas_selecionados:
        return "node_especialista_dinamico"
    return "node_atendente"


workflow.add_conditional_edges(
    "node_especialista_tags",
    router_pos_tags,
    {
        "node_especialista_dinamico": "node_especialista_dinamico",
        "node_atendente": "node_atendente",
    }
)
workflow.add_edge("node_especialista_dinamico", "node_atendente")

from langgraph.checkpoint.memory import MemorySaver
memory = MemorySaver()

# Compilar com Checkpointer
graph = workflow.compile(checkpointer=memory)

async def _buscar_historico_lead_para_followup(canal: str, identificador_origem: str, empresa_id: str, limite: int = 5) -> tuple[str, str | None]:
    """Busca as últimas N mensagens do lead no Postgres e as formata para injeção no prompt."""
    try:
        import uuid as _uuid
        from db.database import AsyncSessionLocal as _ASL
        from db.models import CRMLead as _CRMLead, MensagemHistorico as _MH
        from sqlalchemy import select as _select

        empresa_uuid = _uuid.UUID(empresa_id)
        async with _ASL() as sess:
            res_lead = await sess.execute(
                _select(_CRMLead).where(
                    _CRMLead.empresa_id == empresa_uuid,
                    _CRMLead.telefone_contato == str(identificador_origem)
                )
            )
            lead = res_lead.scalars().first()
            if not lead:
                return "", None

            res_hist = await sess.execute(
                _select(_MH)
                .where(_MH.lead_id == lead.id)
                .order_by(_MH.criado_em.desc())
                .limit(limite)
            )
            msgs = list(reversed(res_hist.scalars().all()))
            if not msgs:
                return "", str(lead.id)

            linhas = []
            for m in msgs:
                papel = "Assistente" if m.from_me else "Cliente"
                linhas.append(f"{papel}: {m.texto}")
            return "\n".join(linhas), str(lead.id)
    except Exception as e:
        print(f"[FOLLOW-UP] Aviso: falha ao buscar histórico do Postgres: {e}")
        return "", None


async def gerar_followup_contextual(canal: str, identificador_origem: str, empresa_id: str) -> str:
    """
    Gera um Nudge (Nível 1) — apenas as últimas 2 mensagens + nome da empresa.
    """
    print("[FOLLOW-UP CONTEXTUAL] Iniciando Nível 1...", flush=True)

    # Busca apenas as últimas 2 mensagens
    historico, _ = await _buscar_historico_lead_para_followup(canal, identificador_origem, empresa_id, limite=2)

    import uuid as _uuid
    from db.database import AsyncSessionLocal as _ASL
    from db.models import Empresa as _Empresa
    from sqlalchemy import select as _sel

    nome_empresa = ""
    try:
        async with _ASL() as sess:
            res_emp = await sess.execute(_sel(_Empresa).where(_Empresa.id == _uuid.UUID(empresa_id)))
            emp = res_emp.scalars().first()
            if emp:
                nome_empresa = emp.nome_empresa or ""
    except Exception as e:
        print(f"[FOLLOW-UP] Erro ao buscar empresa: {e}", flush=True)

    print(f"[FOLLOW-UP CONTEXTUAL] Empresa: '{nome_empresa}' | Histórico disponível: {bool(historico)}", flush=True)

    fim_conversa = historico if historico else "(sem histórico registrado)"

    nome_empresa_prompt = (nome_empresa or "").strip() or "sua empresa"
    prompt = f"""Você é um assistente da {nome_empresa_prompt}. Sua única tarefa é enviar UMA frase curta e educada de acompanhamento, baseada apenas no fim da conversa. Seja sutil e não tente vender nada.

Fim da conversa:
{fim_conversa}

Exemplo de tom: "Ficou alguma dúvida sobre o que conversamos?"

Responda APENAS com o texto da frase. Máximo 15 palavras."""

    _conversation_debug_log(f"--- PROMPT FINAL FOLLOW-UP (NIVEL 1) ---\n{prompt}\n" + "-"*40, flush=True)
    llm = await get_llm(empresa_id)
    resposta = await llm.ainvoke(prompt)
    _conversation_debug_log(f"[FOLLOW-UP RESULT] Resposta da IA: {resposta.content}", flush=True)
    return resposta.content


async def gerar_followup_encerramento(canal: str, identificador_origem: str, empresa_id: str) -> str:
    """
    Gera uma Despedida (Nível 2) — mensagem curta de encerramento com nome da empresa.
    """
    print("[FOLLOW-UP ENCERRAMENTO] Iniciando Nível 2...", flush=True)

    historico, lead_id = await _buscar_historico_lead_para_followup(canal, identificador_origem, empresa_id, limite=3)

    import uuid as _uuid
    from db.database import AsyncSessionLocal as _ASL
    from db.models import Empresa as _Empresa
    from sqlalchemy import select as _sel

    nome_empresa = ""
    try:
        async with _ASL() as sess:
            res_emp = await sess.execute(_sel(_Empresa).where(_Empresa.id == _uuid.UUID(empresa_id)))
            emp = res_emp.scalars().first()
            if emp:
                nome_empresa = emp.nome_empresa or ""
    except Exception as e:
        print(f"[FOLLOW-UP ENCERRAMENTO] Erro ao buscar empresa: {e}", flush=True)

    print(f"[FOLLOW-UP ENCERRAMENTO] Gerando prompt para empresa '{nome_empresa}'...", flush=True)

    fim_conversa = historico if historico else "(sem histórico registrado)"

    nome_empresa_prompt = (nome_empresa or "").strip() or "sua empresa"
    prompt = f"""Você é um assistente da {nome_empresa_prompt}. O cliente não respondeu ao acompanhamento anterior.

Fim da conversa:
{fim_conversa}

Escreva UMA mensagem curta informando que:
1. A conversa será arquivada.
2. O consultor responsável foi notificado e pode dar continuidade se necessário.
3. O cliente é sempre bem-vindo a retomar quando quiser.

Seja gentil e breve. Máximo 2 frases. Responda APENAS com o texto da mensagem."""

    _conversation_debug_log(f"--- PROMPT FINAL FOLLOW-UP (NIVEL 2 ENCERRAMENTO) ---\n{prompt}\n" + "-"*40, flush=True)
    llm = await get_llm(empresa_id)
    resposta = await llm.ainvoke(prompt)
    texto_encerramento = resposta.content
    _conversation_debug_log(f"[FOLLOW-UP ENCERRAMENTO RESULT] Resposta da IA: {texto_encerramento}", flush=True)

    # ── UPDATE REAL NO CRM ────────────────────────────────────────────────────
    if lead_id:
        try:
            from db.database import AsyncSessionLocal as _ASL2
            from db.models import CRMLead as _CRMLead, CRMFunil as _CRMFunil, CRMEtapa as _CRMEtapa
            from sqlalchemy import select as _sel2, update as _upd

            empresa_uuid = _uuid.UUID(empresa_id)
            lead_uuid = _uuid.UUID(lead_id)

            async with _ASL2() as sess:
                _res_funil = await sess.execute(
                    _sel2(_CRMFunil).where(_CRMFunil.empresa_id == empresa_uuid)
                )
                funil = _res_funil.scalars().first()

                etapa_encerramento_id = None
                if funil:
                    _res_etapa = await sess.execute(
                        _sel2(_CRMEtapa).where(
                            _CRMEtapa.funil_id == funil.id,
                            _CRMEtapa.nome.in_(["Perdido", "Esfriou", "Perdidos", "Inativo", "Sem Retorno"])
                        )
                    )
                    etapa_enc = _res_etapa.scalars().first()
                    if etapa_enc:
                        etapa_encerramento_id = etapa_enc.id
                        print(f"[CRM UPDATE] Etapa de encerramento encontrada: '{etapa_enc.nome}'", flush=True)

                valores_update = {
                    "historico_resumo": (
                        f"[Encerrado por inatividade — follow-up automático]\n"
                        f"Última interação resumida:\n{historico[:500] if historico else 'Sem histórico registrado.'}"
                    )
                }
                if etapa_encerramento_id:
                    valores_update["etapa_id"] = etapa_encerramento_id

                await sess.execute(
                    _upd(_CRMLead)
                    .where(_CRMLead.id == lead_uuid)
                    .values(**valores_update)
                )
                await sess.commit()
                print(f"[CRM UPDATE] Lead {lead_id} atualizado com sucesso no banco.", flush=True)

        except Exception as e:
            print(f"[FOLLOW-UP ENCERRAMENTO] Aviso: falha ao atualizar CRM: {e}", flush=True)
    # ─────────────────────────────────────────────────────────────────────────

    return texto_encerramento
