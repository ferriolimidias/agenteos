from __future__ import annotations

import os
import asyncio
import logging
import json
from typing import TypedDict, List, Optional, Literal, Any
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL_CONVERSATION = os.getenv("LOG_LEVEL_CONVERSATION", "INFO").upper()


def _conversation_debug_enabled() -> bool:
    return LOG_LEVEL_CONVERSATION == "DEBUG"


def _conversation_debug_log(message: str, flush: bool = False) -> None:
    if _conversation_debug_enabled():
        print(message, flush=flush)

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# Removido LLM global: será instanciado via get_llm
embeddings_model = OpenAIEmbeddings(model="text-embedding-ada-002")

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
    listar_destinos_transferencia_para_prompt,
)
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
            return "Nenhum contexto adicional encontrado na base de conhecimento."
            
        contexto = "\n\n".join([f"Contexto {i+1}:\n{t.conteudo}" for i, t in enumerate(trechos)])
        return contexto

# 1. Definir o Estado
class AgentState(TypedDict):
    empresa_id: str
    identificador_origem: str
    canal: str
    conexao_id: Optional[str]
    mensagens: list
    historico_bd: str          # Histórico real do PostgreSQL, formatado
    nome_contato: Optional[str]
    intencao: List[str]
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
    dados: str | dict = Field(description="Dados crus extraidos pelo especialista.")
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
    from langchain_core.messages import SystemMessage
    from app.api.utils import get_orchestrator_system_prompt

    # 1. Inicialização de Variáveis Locais (Prevenção de UnboundLocalError)
    empresa_id = state.get("empresa_id")
    lead_id = state.get("lead_id")
    historico_bd = state.get("historico_bd", "")
    respostas_especialistas = state.get("respostas_especialistas", [])
    identificador = state.get("identificador_origem")
    canal = state.get("canal")
    
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

    # Identifica se é o primeiro turno para a trava de saudação
    is_primeira_msg = (not historico_bd or historico_bd.strip() == "(nenhuma interacao anterior registrada)")

    # Pegar prompt base
    system_prompt_base = await get_orchestrator_system_prompt(empresa_id, is_primeira_mensagem=is_primeira_msg)

    # ── FASE DE SÍNTESE (Voltando dos Especialistas) ─────────────────────────────
    if respostas_especialistas:
        print(f"[NODE ATENDENTE] Fase de Síntese: Consolidando {len(respostas_especialistas)} resposta(s)...")

        bloco_xml = _bloco_xml(historico_bd, respostas_especialistas)

        bloco_xml_sintese = _bloco_xml(historico_bd, respostas_especialistas)

        prompt_sintese = f"""{system_prompt_base}

{bloco_xml_sintese}

<instrucao_final>
Você recebeu os seguintes DADOS CRUS dos especialistas do sistema em <respostas_especialistas>, no formato JSON com os campos: "dados", "fontes" e "erros".
Sua tarefa é ler esses dados e formular uma resposta natural, educada e coesa para o cliente, assumindo a persona da empresa.
Não mencione nomes de especialistas, ferramentas, APIs, bancos internos ou roteamento.
Se houver "erros" no JSON de algum especialista, informe ao cliente de forma educada que houve uma limitação técnica ao buscar aquela informação específica.
Baseie a resposta principalmente no campo "dados" de cada JSON, combinado com o histórico da conversa.
Responda em uma única mensagem clara e objetiva.
</instrucao_final>"""

        _conversation_debug_log(f"--- PROMPT FINAL ATENDENTE (SINTESE) ---\n{prompt_sintese}", flush=True)
        mensagens_para_llm = [SystemMessage(content=prompt_sintese)] + state.get("mensagens", [])
        llm = await get_llm(empresa_id)
        resposta = await llm.ainvoke(mensagens_para_llm)

        state["resposta_final"] = resposta.content
        state["respostas_especialistas"] = []
        state["intencao"] = []

        return state

    # ── FASE INICIAL (Recepção e Decisão) ────────────────────────────────────────
    print("[NODE ATENDENTE] Fase Inicial: Avaliando small-talk ou roteamento...")

    bloco_xml = _bloco_xml(historico_bd, [])

    prompt_decisao = f"""{system_prompt_base}

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
Se for small talk puro, use precisa_roteamento=False e forneça uma resposta curta em 'resposta' (máximo 2 frases).
</instrucao_decisao>"""

    _conversation_debug_log(f"--- PROMPT FINAL ATENDENTE (DECISAO) ---\n{prompt_decisao}", flush=True)
    mensagens_para_llm = [SystemMessage(content=prompt_decisao)] + state.get("mensagens", [])

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
        print(f"[NODE ATENDENTE] Resolvido via Small Talk: {resultado.resposta}")
    else:
        print("[NODE ATENDENTE] Optou por acionar Roteador.")
        state["resposta_final"] = None

    return state

async def node_roteador_maestro(state: AgentState):
    print(f"[NODE ROTEADOR] Mapeando intenções para Especialistas...")
    
    import uuid
    from langchain_core.messages import SystemMessage
    empresa_id = state.get("empresa_id")
    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        empresa_uuid = None

    nome_empresa, area_atuacao = await ler_dados_empresa(empresa_uuid)
    contexto_empresa = f"Você mapeia requisições para a empresa {nome_empresa}"
    if area_atuacao:
        contexto_empresa += f", área: {area_atuacao}."
    else:
        contexto_empresa += "."

    especialistas_str = "- 'geral': Atendimento geral."
    if empresa_uuid:
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Especialista).where(
                    Especialista.ativo == True,
                    Especialista.empresa_id == empresa_uuid
                ).options(selectinload(Especialista.ferramentas))
            )
            especialistas_ativos = result.scalars().all()
            if especialistas_ativos:
                linhas_esp = []
                for e in especialistas_ativos:
                    desc = e.descricao_missao if e.descricao_missao else f"{e.prompt_sistema[:150]}..."
                    if getattr(e, 'ferramentas', None):
                        fd = [f.nome_ferramenta for f in e.ferramentas]
                        if fd:
                            desc += f" (Ferramentas: {', '.join(fd)})."
                    linhas_esp.append(f"- '{e.nome}': {desc}")
                especialistas_str = "\n".join(linhas_esp)

            tags_prompt_roteador = await listar_tags_crm_para_prompt(empresa_uuid)
            if tags_prompt_roteador:
                especialistas_str += (
                    "\n- 'tags_crm': Classifica o lead com tags oficiais do CRM quando a mensagem indicar perfil, interesse, urgência, objeções, origem, qualificação ou qualquer regra abaixo.\n"
                    f"{tags_prompt_roteador}"
                )

    prompt = f"""[ROTEADOR MECÂNICO]
Sua função é APENAS rotear.
{contexto_empresa}

ESPECIALISTAS DISPONÍVEIS:
{especialistas_str}

Com base na última mensagem do cliente, retorne um ARRAY/LISTA contendo os nomes exatos dos especialistas que devem atuar. 
Se a requisição tiver múltiplos assuntos, retorne múltiplos nomes (Ex: ['Comercial', 'Suporte']).
Se a mensagem ativar alguma regra de tag oficial, inclua obrigatoriamente 'tags_crm' na lista.
Se nenhum encaixar perfeitamente, retorne ['geral']."""
    
    mensagens_para_llm = [SystemMessage(content=prompt)] + state.get("mensagens", [])

    # ── Busca explícita do modelo_roteador — independente do modelo_ia do Atendente ──
    modelo_roteador = None
    if empresa_uuid:
        try:
            from db.database import AsyncSessionLocal
            async with AsyncSessionLocal() as _sess_rot:
                _res_emp = await _sess_rot.execute(
                    select(Empresa).where(Empresa.id == empresa_uuid)
                )
                _emp = _res_emp.scalars().first()
                if _emp:
                    modelo_roteador = getattr(_emp, "modelo_roteador", None)
        except Exception as _e_rot:
            print(f"[NODE ROTEADOR] Aviso: falha ao buscar modelo_roteador: {_e_rot}")
    # ─────────────────────────────────────────────────────────────────────────────────

    print(f"[NODE ROTEADOR] Usando modelo: '{modelo_roteador or 'default'}'")
    llm = await get_llm(state.get("empresa_id"), modelo_ia=modelo_roteador)
    llm_json = llm.with_structured_output(AnaliseRoteador)
    resultado = await llm_json.ainvoke(mensagens_para_llm)

    print(f"[LLM ROTEADOR] Intenções: '{resultado.intencao}' | Handoff: {resultado.handoff_requested}")
    state["intencao"] = resultado.intencao
    state["respostas_especialistas"] = []
    state["handoff_requested"] = resultado.handoff_requested
    return state

async def node_especialista_dinamico(state: AgentState):
    intencoes = state.get("intencao", ["geral"])
    if not isinstance(intencoes, list):
        intencoes = [intencoes] if intencoes else ["geral"]
        
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

    state["respostas_especialistas"] = []
    lead_id = state.get("lead_id")
    conexao_id = state.get("conexao_id")
    destinos_transferencia_prompt = await listar_destinos_transferencia_para_prompt(empresa_id)
    
    for intencao in intencoes:
        contexto_adicional = ""
        dados_crus_partes: list[str] = []
        fontes_usadas: list[str] = []
        erros_extracao: list[str] = []
        
        if intencao == "duvida" and empresa_uuid:
            contexto_rag = await buscar_conhecimento(ultima_mensagem, empresa_uuid)
            contexto_adicional = f"\n\nDADOS_RAG_BRUTOS:\n{contexto_rag}"
            dados_crus_partes.append(f"[RAG]: {contexto_rag}")
            fontes_usadas.append("RAG")
            
        prompt_base = (
            "Você é um extrator de dados.\n"
            "Use as ferramentas disponíveis para buscar a informação solicitada.\n"
            "Retorne APENAS dados brutos encontrados, em JSON simples ou tópicos diretos.\n"
            "NÃO redija mensagens para o cliente final.\n"
            f"{contexto_adicional}"
        )
        tools_disponiveis = []
        descricoes_tools = []
        nomes_tools_registradas = set()
        
        especialista_db = None
        if empresa_uuid:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Especialista)
                    .where(
                        Especialista.nome == intencao, 
                        Especialista.ativo == True,
                        Especialista.empresa_id == empresa_uuid
                    )
                    .options(
                        selectinload(Especialista.api_connections),
                        selectinload(Especialista.ferramentas)
                    )
                )
                especialista_db = result.scalars().first()
                
                if especialista_db:
                    # RAG Check
                    if getattr(especialista_db, 'usar_rag', False) or intencao == "duvida":
                        contexto_rag = await buscar_conhecimento(ultima_mensagem, empresa_uuid)
                        contexto_adicional = f"\n\nDADOS_RAG_BRUTOS:\n{contexto_rag}"
                        dados_crus_partes.append(f"[RAG]: {contexto_rag}")
                        fontes_usadas.append("RAG")

                    prompt_base = (
                        "Você é um extrator de dados.\n"
                        "Use as ferramentas disponíveis para buscar a informação solicitada.\n"
                        "Retorne APENAS dados brutos encontrados, em JSON simples ou tópicos diretos.\n"
                        "NÃO redija mensagens para o cliente final.\n"
                        f"\nCONTEXTO_TECNICO_ESPECIALISTA:\n{especialista_db.prompt_sistema}\n"
                        f"{contexto_adicional}"
                    )
                    
                    # Carrega ferramentas associadas a este especialista (API Connections)
                    for conexao in especialista_db.api_connections:
                        try:
                            nova_tool = create_dynamic_tool(conexao)
                            tools_disponiveis.append(nova_tool)
                            nomes_tools_registradas.add(nova_tool.name)
                            desc = nova_tool.description if nova_tool.description else "Ferramenta sem descrição."
                            descricoes_tools.append(f"- {conexao.nome}: {desc}")
                        except Exception as e:
                            print(f"Erro ao instanciar ferramenta {conexao.nome}: {e}")

                # Busca dinâmica de Ferramentas vinculadas a este Especialista
                ferramentas_nativas = especialista_db.ferramentas if especialista_db else []
                for f_db in ferramentas_nativas:
                    try:
                        schema_dict = f_db.schema_parametros if f_db.schema_parametros else {}
                        if isinstance(schema_dict, str):
                            import json
                            schema_dict = json.loads(schema_dict)
                            
                        ArgsSchema = _create_pydantic_model_from_json_schema(
                            schema_dict, 
                            model_name=f"{f_db.nome_ferramenta}Args"
                        )

                        if f_db.nome_ferramenta in MAP_FUNCOES_NATIVAS:
                            # Hardcoded native function
                            func_python = MAP_FUNCOES_NATIVAS[f_db.nome_ferramenta]
                            nova_tool = StructuredTool(
                                name=f_db.nome_ferramenta,
                                description=f_db.descricao_ia,
                                args_schema=ArgsSchema,
                                coroutine=func_python
                            )
                            tools_disponiveis.append(nova_tool)
                            nomes_tools_registradas.add(nova_tool.name)
                            descricoes_tools.append(f"- {f_db.nome_ferramenta}: {f_db.descricao_ia}")
                        elif getattr(f_db, 'url', None):
                            # Dynamic HTTP Builder Tool
                            headers_str = getattr(f_db, 'headers', '{}')
                            payload_str = getattr(f_db, 'payload', '{}')
                            
                            def create_http_tool_coroutine(url, method, headers_json, payload_json, nome_tool):
                                async def http_tool_coroutine(**kwargs) -> str:
                                    import json
                                    import httpx
                                    
                                    logger.info("[TOOL EXECUTION] Chamando %s com parametros: %s", nome_tool, kwargs)
                                    
                                    # Parse JSONs safely
                                    try:
                                        h_dict = json.loads(headers_json) if headers_json else {}
                                    except:
                                        h_dict = {}
                                        
                                    try:
                                        p_dict = json.loads(payload_json) if payload_json else {}
                                    except:
                                        p_dict = {}

                                    try:
                                        # Substituir {{variaveis}} no URL
                                        final_url = url or ""
                                        for k, v in kwargs.items():
                                            final_url = final_url.replace(f"{{{{{k}}}}}", str(v))
                                            # Suporte legado para single brackets caso o cliente o use
                                            final_url = final_url.replace(f"{{{k}}}", str(v))

                                        if "{" in final_url or "}" in final_url:
                                            resultado = (
                                                f"Falha ao executar ferramenta {nome_tool}: URL template não preenchido. "
                                                f"URL atual: {final_url}. Parametros recebidos: {kwargs}"
                                            )
                                            logger.warning("[TOOL ERROR] %s", resultado)
                                            return resultado
                                            
                                        # Substituir {{variaveis}} no Payload (convertendo para string, substituindo e voltando para dict)
                                        p_str = json.dumps(p_dict)
                                        for k, v in kwargs.items():
                                            p_str = p_str.replace(f"{{{{{k}}}}}", str(v))
                                        final_payload = json.loads(p_str)

                                        path_placeholders = {
                                            k for k in kwargs.keys()
                                            if f"{{{k}}}" in (url or "") or f"{{{{{k}}}}}" in (url or "")
                                        }
                                        query_params = {k: v for k, v in kwargs.items() if k not in path_placeholders}

                                        logger.info(
                                            "[TOOL HTTP REQUEST] tool=%s method=%s url=%s query_params=%s",
                                            nome_tool,
                                            method.upper(),
                                            final_url,
                                            query_params if method.upper() in ["GET", "DELETE"] else {},
                                        )

                                        async with httpx.AsyncClient() as client:
                                            if method.upper() == "GET":
                                                resp = await client.get(final_url, headers=h_dict, params=query_params, timeout=10.0)
                                            elif method.upper() == "POST":
                                                resp = await client.post(final_url, headers=h_dict, json=final_payload, timeout=10.0)
                                            elif method.upper() == "PUT":
                                                resp = await client.put(final_url, headers=h_dict, json=final_payload, timeout=10.0)
                                            elif method.upper() == "DELETE":
                                                resp = await client.delete(final_url, headers=h_dict, params=query_params, timeout=10.0)
                                            else:
                                                resultado = f"Método HTTP {method} não suportado."
                                                logger.warning("[TOOL ERROR] %s", resultado)
                                                return resultado

                                            logger.info(
                                                "[TOOL HTTP RESPONSE] tool=%s method=%s url=%s status_code=%s",
                                                nome_tool,
                                                method.upper(),
                                                final_url,
                                                resp.status_code,
                                            )
                                                
                                            if resp.status_code >= 400:
                                                resultado = f"Erro na requisição: {resp.status_code} - {resp.text}"
                                                logger.warning("[TOOL ERROR] %s", resultado)
                                                return resultado

                                            resp.raise_for_status()
                                            
                                            try:
                                                resp_json = resp.json()
                                                if isinstance(resp_json, dict) and resp_json.get("erro") in [True, "true", "True"]:
                                                    resultado = f"Erro na API: {resp.text}"
                                                    logger.warning("[TOOL ERROR] %s", resultado)
                                                    return resultado
                                            except Exception:
                                                pass

                                            resultado = resp.text
                                            logger.info("[TOOL RESULT] Ferramenta %s retornou %d chars", nome_tool, len(resultado))
                                            return resultado
                                    except Exception as e:
                                        resultado = f"Falha ao executar ferramenta {nome_tool}: {str(e)}"
                                        logger.exception("[TOOL ERROR] %s", resultado)
                                        return resultado
                                return http_tool_coroutine

                            nova_tool = StructuredTool(
                                name=f_db.nome_ferramenta,
                                description=f_db.descricao_ia,
                                args_schema=ArgsSchema,
                                coroutine=create_http_tool_coroutine(f_db.url, f_db.metodo, headers_str, payload_str, f_db.nome_ferramenta)
                            )
                            tools_disponiveis.append(nova_tool)
                            nomes_tools_registradas.add(nova_tool.name)
                            descricoes_tools.append(f"- {f_db.nome_ferramenta}: {f_db.descricao_ia}")

                    except Exception as e:
                        print(f"Erro ao instanciar Ferramenta Nativa {f_db.nome_ferramenta}: {e}")
                        erros_extracao.append(f"Falha ao instanciar ferramenta nativa {f_db.nome_ferramenta}: {e}")

        if lead_id and empresa_id and destinos_transferencia_prompt and "action_transferir_atendimento" not in nomes_tools_registradas:
            tool_transferencia = criar_ferramenta_transferir_atendimento_contextual(
                lead_id=lead_id,
                empresa_id=empresa_id,
                conexao_id=conexao_id,
            )
            tools_disponiveis.append(tool_transferencia)
            descricoes_tools.append(
                "- action_transferir_atendimento: transfere o atendimento para um destino humano configurado, registra auditoria e dispara o aviso interno."
            )

        system_message_adicional = f"\n{contexto_empresa}"
        
        if descricoes_tools:
            system_message_adicional += "\n\nVocê tem acesso às seguintes ferramentas:\n"
            system_message_adicional += "\n".join(descricoes_tools)
            system_message_adicional += "\nUse-as quando necessário para obter os dados brutos."

        if destinos_transferencia_prompt:
            system_message_adicional += (
                "\n\nVocê tem os seguintes destinos de transferência disponíveis:\n"
                f"{destinos_transferencia_prompt}\n"
                "Quando o cenário bater com as instruções de ativação acima, use a ferramenta 'action_transferir_atendimento'. "
                "Retorne apenas o resultado cru da execução da ferramenta."
            )

        if state.get("handoff_requested", False):
            system_message_adicional += (
                "\n\nInformação importante: O usuário pediu atendimento humano. "
                "Se houver um destino compatível, priorize usar 'action_transferir_atendimento' e retorne só o resultado bruto."
            )
            
        prompt_completo = prompt_base + system_message_adicional

        modelo_esp = especialista_db.modelo_ia if especialista_db and hasattr(especialista_db, 'modelo_ia') else None
        llm = await get_llm(state.get("empresa_id"), modelo_ia=modelo_esp)
        llm_extracao = llm.with_structured_output(ExtracaoEspecialista)
        print(f"[NODE ESPECIALISTA DINAMICO] Avaliando especialista '{intencao}' com modelo '{modelo_esp or 'default'}'...")
        if tools_disponiveis:
            print(f"  Fazendo bind dinâmico via llm.bind_tools para {len(tools_disponiveis)} ferramenta(s)")
            llm_with_tools = llm.bind_tools(tools_disponiveis)
            tool_node = ToolNode(tools_disponiveis)
            
            from langchain_core.messages import SystemMessage, HumanMessage
            mensagens = [SystemMessage(content=prompt_completo), HumanMessage(content=ultima_mensagem)]
            
            for _ in range(5):
                resposta = await llm_with_tools.ainvoke(mensagens)
                mensagens.append(resposta)
                
                if hasattr(resposta, "tool_calls") and len(resposta.tool_calls) > 0:
                    nomes = [t['name'] for t in resposta.tool_calls]
                    print(f"  Ferramentas acionadas pelo fluxo: {nomes}")
                    fontes_usadas.extend([str(n).strip() for n in nomes if str(n).strip()])
                    try:
                        # Executa o ToolNode Langgraph Customizado em loop local
                        resultado_toolnode = await tool_node.ainvoke({"messages": [resposta]})
                        mensagens.extend(resultado_toolnode["messages"])
                        for tool_msg in resultado_toolnode.get("messages", []):
                            conteudo_tool = str(getattr(tool_msg, "content", "") or "").strip()
                            if conteudo_tool:
                                dados_crus_partes.append(conteudo_tool)
                                if "erro" in conteudo_tool.lower() or "falha" in conteudo_tool.lower():
                                    erros_extracao.append(conteudo_tool)
                    except Exception as e:
                        print(f"  Erro no nó de execução ToolNode: {e}")
                        resposta_parcial = f"Erro no sistema de execução: {e}"
                        erros_extracao.append(str(resposta_parcial))
                        break
                else:
                    resposta_parcial = resposta.content
                    if "erro" in str(resposta_parcial or "").lower() or "falha" in str(resposta_parcial or "").lower():
                        erros_extracao.append(str(resposta_parcial))
                    break
            else:
                resposta_parcial = "Tentei utilizar as ferramentas várias vezes mas não consegui concluir. Houve limite de tentativas."
                erros_extracao.append(resposta_parcial)
        else:
            print("  Resposta direta via LLM (sem ferramentas)")
            resposta = await llm.ainvoke([("system", prompt_completo), ("user", ultima_mensagem)])
            resposta_parcial = resposta.content
            fontes_usadas.append("LLM_SEM_TOOL")
            if "erro" in str(resposta_parcial or "").lower() or "falha" in str(resposta_parcial or "").lower():
                erros_extracao.append(str(resposta_parcial))

        conteudo_final = str(resposta_parcial or "").strip()
        if conteudo_final:
            dados_crus_partes.append(conteudo_final)
        dados_crus = "\n".join([p for p in dados_crus_partes if str(p).strip()]).strip()
        if not dados_crus:
            dados_crus = "Sem dados crus retornados."

        fontes_unicas = sorted(list({f for f in fontes_usadas if str(f).strip()}))
        erros_unicos = sorted(list({e for e in erros_extracao if str(e).strip()}))
        extracao = await llm_extracao.ainvoke(
            [
                (
                    "system",
                    "Estruture o material recebido no schema ExtracaoEspecialista. "
                    "Nao crie conversa com cliente. Mantenha dados tecnicos e objetivos."
                ),
                (
                    "user",
                    f"dados_brutos:\n{dados_crus}\n\nfontes_detectadas:\n{fontes_unicas}\n\nerros_detectados:\n{erros_unicos}",
                ),
            ]
        )

        state["respostas_especialistas"].append(
            f"[ESPECIALISTA: {intencao}] {json.dumps(extracao.model_dump(), ensure_ascii=False)}"
        )
        
    return state

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
    if "tags_crm" in intencoes:
        return "node_especialista_tags"
    return "node_especialista_dinamico"


workflow.add_conditional_edges(
    "node_roteador_maestro",
    router_maestro,
    {
        "node_especialista_tags": "node_especialista_tags",
        "node_especialista_dinamico": "node_especialista_dinamico",
    }
)

def router_pos_tags(state: AgentState):
    intencoes = state.get("intencao") or []
    if intencoes:
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
