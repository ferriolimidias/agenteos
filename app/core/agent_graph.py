from __future__ import annotations

import os
import asyncio
import logging
import json
import re
import uuid
import unicodedata
from functools import partial
from typing import TypedDict, List, Optional, Any, Dict
from datetime import datetime, timedelta, date, time as dt_time
from zoneinfo import ZoneInfo
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
from langchain_core.messages import AIMessage, HumanMessage

# Removido LLM global: será instanciado via get_llm
embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")

async def get_llm(empresa_id: str | None = None, modelo_ia: str | None = None) -> Any:
    modelo_ia = str(modelo_ia or "").strip() or None
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
    modelo_final = normalize_model_name(modelo_ia or "gpt-4o-mini")
    try:
        logger.info("[LLM] Instanciando modelo final='%s' (empresa_id=%s)", modelo_final, empresa_id)
        return get_llm_model(modelo_final, api_key=api_key)
    except Exception as e:
        print(f"Erro instanciando modelo {modelo_final}: {e}")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            model_kwargs={"frequency_penalty": 0.4, "presence_penalty": 0.4},
        )

from db.database import AsyncSessionLocal
from db.models import (
    Conhecimento,
    Especialista,
    Empresa,
    CRMLead,
    CRMFunil,
    CRMEtapa,
    FerramentaAPI,
    MensagemHistorico,
    AgendaConfiguracao,
    AgendamentoLocal,
    EmpresaUnidade,
)
from sqlalchemy import select, update, and_
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
from app.core.llm_factory import normalize_model_name
from app.core.tools import (
    tool_atualizar_tags_lead,
    tool_aplicar_tag_dinamica,
    tool_consultar_tags_empresa,
)
from langchain_core.tools import StructuredTool, tool
from langgraph.prebuilt import create_react_agent, ToolNode
import httpx

logger = logging.getLogger(__name__)


def _strip_role_prefix(texto: str) -> str:
    raw = str(texto or "").strip()
    lower_raw = raw.lower()
    for prefixo in ("assistente:", "usuario:", "usuário:", "cliente:"):
        if lower_raw.startswith(prefixo):
            return raw[len(prefixo):].strip()
    return raw


def _mensagens_estado(state: "AgentState") -> list[Any]:
    # Compatibilidade: fluxo legado usa `mensagens`, alguns pontos podem usar `messages`.
    mensagens = state.get("mensagens")
    if isinstance(mensagens, list):
        return mensagens
    messages = state.get("messages")
    if isinstance(messages, list):
        return messages
    return []


def _ultima_mensagem_cliente(state: "AgentState") -> str:
    mensagens = _mensagens_estado(state)
    if not mensagens:
        return ""
    for item in reversed(mensagens):
        texto = str(item or "").strip()
        if not texto:
            continue
        texto_lower = texto.lower()
        if texto_lower.startswith("assistente:"):
            continue
        return _strip_role_prefix(texto)
    return _strip_role_prefix(str(mensagens[-1] if mensagens else ""))


def _ultima_mensagem_assistente(state: "AgentState") -> str:
    mensagens = _mensagens_estado(state)
    if not mensagens:
        return ""
    for item in reversed(mensagens):
        texto = str(item or "").strip()
        if not texto:
            continue
        if texto.lower().startswith("assistente:"):
            return _strip_role_prefix(texto)
    return ""


def _historico_curto_roteador(state: "AgentState", limite: int = 3) -> str:
    mensagens = _mensagens_estado(state)
    if not mensagens:
        return ""
    itens = [str(item or "").strip() for item in mensagens if str(item or "").strip()]
    if not itens:
        return ""
    ultimas = itens[-max(1, int(limite)) :]
    linhas: list[str] = []
    for texto in ultimas:
        if texto.lower().startswith("assistente:"):
            linhas.append(f"IA: {_strip_role_prefix(texto)}")
        else:
            linhas.append(f"Cliente: {_strip_role_prefix(texto)}")
    return "\n".join(linhas).strip()


def _turnos_consolidados_roteador(state: "AgentState", limite_turnos: int = 3) -> str:
    mensagens = _mensagens_estado(state)
    if not mensagens:
        return ""

    turnos: list[dict[str, str]] = []
    for item in mensagens:
        texto = str(item or "").strip()
        if not texto:
            continue
        role = "assistant" if texto.lower().startswith("assistente:") else "user"
        conteudo = _strip_role_prefix(texto)
        if not conteudo:
            continue
        if turnos and turnos[-1]["role"] == role:
            turnos[-1]["content"] = f"{turnos[-1]['content']}\n{conteudo}".strip()
            continue
        turnos.append({"role": role, "content": conteudo})

    if not turnos:
        return ""

    ultimos = turnos[-max(1, int(limite_turnos)) :]
    linhas: list[str] = []
    for turno in ultimos:
        prefixo = "IA" if turno["role"] == "assistant" else "Cliente"
        linhas.append(f"{prefixo}: {turno['content']}")
    return "\n".join(linhas).strip()


def _normalizar_chave_especialista(valor: str) -> str:
    texto_normalizado = unicodedata.normalize("NFKD", str(valor or ""))
    return "".join(ch for ch in texto_normalizado if not unicodedata.combining(ch)).strip().lower()


def _extrair_opcoes_menu(texto_assistente: str) -> dict[str, str]:
    texto = str(texto_assistente or "")
    if not texto.strip():
        return {}
    opcoes: dict[str, str] = {}
    for linha in texto.splitlines():
        trecho = str(linha or "").strip()
        if not trecho:
            continue
        match = re.match(r"^(\d{1,2})\s*[\)\-\.:\u2013]\s*(.+)$", trecho)
        if not match:
            continue
        indice = match.group(1).strip()
        rotulo = match.group(2).strip()
        if indice and rotulo:
            opcoes[indice] = rotulo
    return opcoes


def _is_primeiro_contato(state: "AgentState") -> bool:
    flag_deterministica = state.get("primeiro_contato")
    if isinstance(flag_deterministica, bool):
        return flag_deterministica

    total_hist = state.get("total_msgs_historico")
    if isinstance(total_hist, int):
        return total_hist <= 0

    historico_bd = str(state.get("historico_bd") or "").strip()
    if historico_bd:
        return False

    mensagens = _mensagens_estado(state)
    mensagens_usuario = []
    for item in mensagens:
        texto = str(item or "").strip()
        if not texto:
            continue
        if texto.lower().startswith("assistente:"):
            continue
        mensagens_usuario.append(texto)
    return len(mensagens_usuario) <= 1


def _prepend_resumo_cliente_system_prompt(state: AgentState, prompt: str) -> str:
    """
    Injeta o resumo de longo prazo do cliente e diretrizes obrigatórias de sistema
    no início do prompt.
    """
    resumo = state.get("resumo_cliente", "")

    diretrizes_sistema = """
[DIRETRIZES GLOBAIS DE SISTEMA - OBRIGATÓRIO]
1. USO DE FERRAMENTAS: Você é um agente proativo. Se a sua missão envolve classificar o lead ou se o cliente indicou interesse em um setor/produto específico (ex: Financeiro, Pedagógico, Vendas), você DEVE OBRIGATORIAMENTE chamar a ferramenta de atualizar tags ANTES de formular sua resposta em texto.
2. Não diga "vou adicionar a tag", simplesmente EXECUTE a ferramenta silenciosamente e responda ao cliente normalmente.
3. ORDEM DE TRANSFERÊNCIA OBRIGATÓRIA: quando houver transbordo para humano/setor, siga esta sequência sem exceções:
   (a) aplicar tag de intenção/setor -> (b) enviar a mensagem ao cliente informando a transferência -> (c) executar a transferência para humano.
4. É proibido acionar transferência para humano antes da mensagem de confirmação ao cliente.
"""

    if resumo:
        return f"{diretrizes_sistema}\nRESUMO DO CLIENTE (Memória de longo prazo): {resumo}\n\n{prompt}"

    return f"{diretrizes_sistema}\n\n{prompt}"


def _remover_especialista_do_estado(state: "AgentState", *chaves: str) -> None:
    alvos = {_normalizar_chave_especialista(chave) for chave in chaves if str(chave or "").strip()}
    if not alvos:
        return

    identificados = state.get("especialistas_identificados") or []
    if not isinstance(identificados, list):
        identificados = []
    state["especialistas_identificados"] = [
        item
        for item in identificados
        if _normalizar_chave_especialista(str(item or "")) not in alvos
    ]

    selecionados = state.get("especialistas_selecionados") or []
    if not isinstance(selecionados, list):
        selecionados = []
    filtrados: list[dict[str, Any]] = []
    for item in selecionados:
        if not isinstance(item, dict):
            continue
        item_id = _normalizar_chave_especialista(str(item.get("id") or ""))
        item_nome = _normalizar_chave_especialista(str(item.get("nome") or ""))
        if item_id in alvos or item_nome in alvos:
            continue
        filtrados.append(item)
    state["especialistas_selecionados"] = filtrados


def _marcar_bot_pausado_se_necessario(state: "AgentState", retorno_tool: str) -> None:
    conteudo = str(retorno_tool or "").strip()
    if "SISTEMA_BOT_PAUSADO" in conteudo:
        state["bot_foi_pausado"] = True


def _to_chat_messages(mensagens: list[Any]) -> list[Any]:
    saida: list[Any] = []
    for item in mensagens:
        texto = str(item or "").strip()
        if not texto:
            continue
        if texto.lower().startswith("assistente:"):
            saida.append(AIMessage(content=_strip_role_prefix(texto)))
        else:
            saida.append(HumanMessage(content=_strip_role_prefix(texto)))
    return saida

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


def _dia_semana_curto(dt: datetime) -> str:
    dias_semana_curto = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
    return dias_semana_curto[dt.weekday()]


def _parse_data(data_str: str) -> datetime:
    return datetime.strptime(str(data_str).strip(), "%Y-%m-%d")


def _parse_data_hora(data_hora_str: str) -> datetime:
    raw = str(data_hora_str).strip()
    if "T" in raw:
        raw = raw.replace("Z", "")
        return datetime.fromisoformat(raw)
    return datetime.strptime(raw, "%Y-%m-%d %H:%M")


def _horario_para_datetime(data_base: datetime, horario_hhmm: str) -> datetime:
    h, m = str(horario_hhmm).split(":")
    return data_base.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _normalizar_dias_funcionamento(dias_raw: Any) -> dict[str, dict[str, Any]]:
    dias = {
        "seg": {"aberto": False, "inicio": None, "fim": None},
        "ter": {"aberto": False, "inicio": None, "fim": None},
        "qua": {"aberto": False, "inicio": None, "fim": None},
        "qui": {"aberto": False, "inicio": None, "fim": None},
        "sex": {"aberto": False, "inicio": None, "fim": None},
        "sab": {"aberto": False, "inicio": None, "fim": None},
        "dom": {"aberto": False, "inicio": None, "fim": None},
    }
    if not isinstance(dias_raw, dict):
        return dias

    # Compatibilidade formato legado: {"dias": ["seg", ...]}
    if isinstance(dias_raw.get("dias"), list):
        for dia in dias_raw.get("dias", []):
            d = str(dia).strip().lower()
            if d in dias:
                dias[d] = {"aberto": True, "inicio": "08:00", "fim": "18:00"}
        return dias

    for d in dias.keys():
        item = dias_raw.get(d)
        if not isinstance(item, dict):
            continue
        aberto = bool(item.get("aberto", False))
        if not aberto:
            dias[d] = {"aberto": False, "inicio": None, "fim": None}
            continue
        inicio = item.get("inicio")
        fim = item.get("fim")
        if isinstance(inicio, str) and isinstance(fim, str):
            dias[d] = {"aberto": True, "inicio": inicio, "fim": fim}
    return dias


async def _obter_janela_funcionamento(session, empresa_uuid: uuid.UUID, data_ref: datetime) -> tuple[datetime | None, datetime | None, int]:
    result_cfg = await session.execute(
        select(AgendaConfiguracao).where(AgendaConfiguracao.empresa_id == empresa_uuid)
    )
    cfg = result_cfg.scalars().first()
    duracao = int(getattr(cfg, "duracao_slot_minutos", 30) or 30)
    if not cfg:
        inicio = data_ref.replace(hour=8, minute=0, second=0, microsecond=0)
        fim = data_ref.replace(hour=18, minute=0, second=0, microsecond=0)
        return inicio, fim, duracao

    dias_norm = _normalizar_dias_funcionamento(getattr(cfg, "dias_funcionamento", None))
    dia_cfg = dias_norm.get(_dia_semana_curto(data_ref), {"aberto": False, "inicio": None, "fim": None})
    if not bool(dia_cfg.get("aberto")):
        return None, None, duracao
    inicio_hhmm = str(dia_cfg.get("inicio") or "08:00")
    fim_hhmm = str(dia_cfg.get("fim") or "18:00")
    return _horario_para_datetime(data_ref, inicio_hhmm), _horario_para_datetime(data_ref, fim_hhmm), duracao


def _resolver_cfg_dia(
    dias_funcionamento: Optional[Dict[str, Any]],
    dia_obj: date,
) -> Optional[Dict[str, Any]]:
    dias_func = dias_funcionamento if isinstance(dias_funcionamento, dict) else {}
    mapa = {
        0: ("segunda", "seg"),
        1: ("terca", "ter"),
        2: ("quarta", "qua"),
        3: ("quinta", "qui"),
        4: ("sexta", "sex"),
        5: ("sabado", "sab"),
        6: ("domingo", "dom"),
    }
    aliases = mapa.get(dia_obj.weekday(), ())
    for chave in aliases:
        dia_cfg = dias_func.get(chave)
        if isinstance(dia_cfg, dict):
            return dia_cfg
    return None


@tool
async def consultar_horarios_livres(data: str, empresa_id: str) -> str:
    """
    Consulta a disponibilidade de horários livres em uma data específica.
    A 'data' deve estar no formato 'YYYY-MM-DD'.
    O 'empresa_id' deve ser injetado internamente ou passado pelo agente.
    """
    try:
        data_obj = datetime.strptime(data, "%Y-%m-%d").date()
        tz = ZoneInfo("America/Sao_Paulo")

        async with AsyncSessionLocal() as db:
            emp_uuid = uuid.UUID(str(empresa_id))
            result_config = await db.execute(
                select(AgendaConfiguracao).where(AgendaConfiguracao.empresa_id == emp_uuid)
            )
            config = result_config.scalars().first()
            if not config:
                return "Erro: A empresa ainda não configurou os horários de atendimento."

            dia_cfg = _resolver_cfg_dia(config.dias_funcionamento, data_obj)
            if not dia_cfg:
                return "A empresa não tem expediente neste dia da semana."

            ativo = bool(dia_cfg.get("ativo")) if "ativo" in dia_cfg else bool(dia_cfg.get("aberto"))
            if not ativo:
                return "A empresa não tem expediente neste dia da semana."

            str_inicio = str(dia_cfg.get("inicio") or "08:00")
            str_fim = str(dia_cfg.get("fim") or "18:00")
            hora_inicio = dt_time.fromisoformat(str_inicio)
            hora_fim = dt_time.fromisoformat(str_fim)
            duracao_minutos = int(getattr(config, "duracao_slot_minutos", 30) or 30)

            dt_inicio = datetime.combine(data_obj, hora_inicio).replace(tzinfo=tz)
            dt_fim = datetime.combine(data_obj, hora_fim).replace(tzinfo=tz)

            result_agendamentos = await db.execute(
                select(AgendamentoLocal).where(
                    and_(
                        AgendamentoLocal.empresa_id == emp_uuid,
                        AgendamentoLocal.status.in_(["marcado", "confirmado", "agendado"]),
                        AgendamentoLocal.data_hora_inicio >= dt_inicio.replace(tzinfo=None),
                        AgendamentoLocal.data_hora_fim <= dt_fim.replace(tzinfo=None),
                    )
                )
            )
            agendamentos_ocupados = result_agendamentos.scalars().all()

            slots_livres: list[str] = []
            slot_atual = dt_inicio
            agora = datetime.now(tz)
            while slot_atual + timedelta(minutes=duracao_minutos) <= dt_fim:
                slot_fim = slot_atual + timedelta(minutes=duracao_minutos)

                conflito = False
                for ag in agendamentos_ocupados:
                    ag_inicio = ag.data_hora_inicio
                    ag_fim = ag.data_hora_fim
                    if not ag_inicio or not ag_fim:
                        continue
                    ag_inicio_tz = ag_inicio.replace(tzinfo=tz) if ag_inicio.tzinfo is None else ag_inicio
                    ag_fim_tz = ag_fim.replace(tzinfo=tz) if ag_fim.tzinfo is None else ag_fim
                    if (slot_atual < ag_fim_tz) and (slot_fim > ag_inicio_tz):
                        conflito = True
                        break

                if not conflito and slot_atual > agora:
                    slots_livres.append(slot_atual.strftime("%H:%M"))
                slot_atual = slot_fim

            if not slots_livres:
                return f"Desculpe, não há horários livres no dia {data}."
            return f"Horários livres encontrados para o dia {data}: " + ", ".join(slots_livres)
    except Exception as e:
        return f"Erro ao consultar horários: {str(e)}"


@tool
async def agendar_horario(data_hora: str, empresa_id: str, lead_id: str, cliente_nome: str) -> str:
    """
    Realiza um agendamento no sistema.
    'data_hora' deve ser no formato 'YYYY-MM-DD HH:MM'.
    'empresa_id' e 'lead_id' devem ser injetados.
    'cliente_nome' é o nome do cliente.
    """
    try:
        tz = ZoneInfo("America/Sao_Paulo")
        dt_inicio_naive = datetime.strptime(data_hora, "%Y-%m-%d %H:%M")
        dt_inicio = dt_inicio_naive.replace(tzinfo=tz)

        async with AsyncSessionLocal() as db:
            emp_uuid = uuid.UUID(str(empresa_id))
            result_config = await db.execute(
                select(AgendaConfiguracao).where(AgendaConfiguracao.empresa_id == emp_uuid)
            )
            config = result_config.scalars().first()
            duracao_minutos = int(getattr(config, "duracao_slot_minutos", 30) or 30)
            dt_fim = dt_inicio + timedelta(minutes=duracao_minutos)

            result_conflito = await db.execute(
                select(AgendamentoLocal).where(
                    and_(
                        AgendamentoLocal.empresa_id == emp_uuid,
                        AgendamentoLocal.status.in_(["marcado", "confirmado", "agendado"]),
                        AgendamentoLocal.data_hora_inicio < dt_fim.replace(tzinfo=None),
                        AgendamentoLocal.data_hora_fim > dt_inicio.replace(tzinfo=None),
                    )
                )
            )
            if result_conflito.scalars().first():
                return "Falha ao agendar: Este horário acabou de ser ocupado. Por favor, escolha outro."

            novo_agendamento = AgendamentoLocal(
                empresa_id=emp_uuid,
                lead_id=uuid.UUID(str(lead_id)) if lead_id else None,
                data_hora_inicio=dt_inicio_naive,
                data_hora_fim=dt_fim.replace(tzinfo=None),
                status="marcado",
            )
            db.add(novo_agendamento)
            await db.commit()
            await db.refresh(novo_agendamento)
            return (
                f"SUCESSO! Agendamento criado para {cliente_nome} no dia {data_hora}. "
                f"O ID do agendamento é: {novo_agendamento.id}"
            )
    except Exception as e:
        return f"Erro ao criar agendamento: {str(e)}"


@tool
async def cancelar_agendamento(agendamento_id: str) -> str:
    """
    Cancela um agendamento existente com base no seu ID.
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AgendamentoLocal).where(AgendamentoLocal.id == uuid.UUID(str(agendamento_id)))
            )
            agendamento = result.scalars().first()
            if not agendamento:
                return f"Erro: Nenhum agendamento encontrado com o ID {agendamento_id}."
            agendamento.status = "cancelado"
            await db.commit()
            return f"Agendamento {agendamento_id} cancelado com sucesso."
    except Exception as e:
        return f"Erro ao cancelar agendamento: {str(e)}"

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


class ToolAplicarTagDinamicaInput(BaseModel):
    tag_id: str = Field(description="UUID da tag oficial que deve ser aplicada no lead atual.")


class ToolTransferirParaHumanoInput(BaseModel):
    motivo: Optional[str] = Field(
        default=None,
        description="Resumo curto do motivo para pausar o bot e transferir para humano.",
    )


class ToolConsultarTagsEmpresaInput(BaseModel):
    pass


async def node_especialista_tags(state: AgentState):
    lead_id = state.get("lead_id")
    empresa_id = state.get("empresa_id")
    ultima_mensagem = _ultima_mensagem_cliente(state)
    historico_global = str(state.get("historico_bd") or "").strip() or "(sem histórico global)"
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
HISTORICO_GLOBAL_READ_ONLY:
{historico_global}

Classifique o lead apenas com tags oficiais coerentes com a conversa.
Retorne APENAS dados crus da operação (resultado da tool, tags aplicadas, erros).
NÃO redija mensagem para o cliente final."""

    llm_with_tools = llm.bind_tools([tool_tags])
    tool_node = ToolNode([tool_tags])
    llm_extracao = llm.with_structured_output(ExtracaoEspecialista)

    from langchain_core.messages import HumanMessage, SystemMessage

    mensagens = [
        SystemMessage(content=_prepend_resumo_cliente_system_prompt(state, prompt)),
        HumanMessage(content=ultima_mensagem),
    ]
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
                _prepend_resumo_cliente_system_prompt(
                    state,
                    "Converta o material bruto em um objeto ExtracaoEspecialista. "
                    "Preserve os dados tecnicos, mantenha apenas fatos, sem linguagem ao cliente.",
                ),
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
    _remover_especialista_do_estado(state, "tags_crm")
    return state


async def node_acao_sistema(state: AgentState):
    """
    Nó dedicado a ações internas de sistema.
    Não deve gerar texto conversacional para o cliente final.
    """
    pendentes = list(state.get("acoes_sistema_pendentes") or [])
    executadas = list(state.get("acoes_sistema_executadas") or [])
    status_acoes = list(state.get("acoes_sistema_status") or [])

    if not pendentes:
        state["acoes_sistema_pendentes"] = pendentes
        state["acoes_sistema_executadas"] = executadas
        state["acoes_sistema_status"] = status_acoes
        return state

    empresa_id = str(state.get("empresa_id") or "").strip()
    lead_id = str(state.get("lead_id") or "").strip()
    conexao_id = state.get("conexao_id")
    ultima_mensagem = _ultima_mensagem_cliente(state)
    prioridade_acoes = {
        "aplicar_tags": 1,
        "transferir_atendimento": 2,
        "fechar_conversa": 3,
    }
    pendentes_ordenadas = sorted(
        pendentes,
        key=lambda acao: prioridade_acoes.get(str(acao or "").strip().lower(), 99),
    )

    for acao in pendentes_ordenadas:
        acao_nome = str(acao or "").strip().lower()
        if not acao_nome:
            continue
        if acao_nome in executadas:
            continue

        if acao_nome == "aplicar_tags":
            try:
                if not lead_id or not empresa_id:
                    status_acoes.append("aplicar_tags: ignorada (lead/empresa ausentes)")
                    executadas.append(acao_nome)
                    continue

                tags_crm_prompt = await listar_tags_crm_para_prompt(empresa_id)
                if not tags_crm_prompt:
                    status_acoes.append("aplicar_tags: ignorada (regras de tag ausentes)")
                    executadas.append(acao_nome)
                    continue

                llm = await get_llm(empresa_id)

                async def _tool(lead_id: str, tags: List[str]) -> str:
                    return await tool_atualizar_tags_lead(lead_id=lead_id, tags=tags)

                tool_tags = StructuredTool(
                    name="tool_atualizar_tags_lead",
                    description="Aplica tags oficiais do CRM ao lead atual com base nas regras de classificação.",
                    args_schema=ToolAtualizarTagsLeadInput,
                    coroutine=_tool,
                )
                llm_with_tools = llm.bind_tools([tool_tags])
                tool_node = ToolNode([tool_tags])

                prompt_tags = f"""Você é um executor de ações internas de sistema.
Use a tool 'tool_atualizar_tags_lead' seguindo estritamente estas regras:
{tags_crm_prompt}

Lead atual: {lead_id}
Mensagem atual do cliente: {ultima_mensagem}
Objetivo: classificar e aplicar tags oficiais no CRM.
Retorne apenas o resultado técnico da ação."""

                msgs = [("system", _prepend_resumo_cliente_system_prompt(state, prompt_tags)), ("user", ultima_mensagem)]
                resultado_tool = ""
                for _ in range(4):
                    resposta = await llm_with_tools.ainvoke(msgs)
                    msgs.append(resposta)
                    if hasattr(resposta, "tool_calls") and resposta.tool_calls:
                        resultado_toolnode = await tool_node.ainvoke({"messages": [resposta]})
                        msgs.extend(resultado_toolnode.get("messages", []))
                        for tool_msg in resultado_toolnode.get("messages", []):
                            conteudo = str(getattr(tool_msg, "content", "") or "").strip()
                            if conteudo:
                                resultado_tool = conteudo
                                _marcar_bot_pausado_se_necessario(state, conteudo)
                        continue
                    resultado_tool = str(getattr(resposta, "content", "") or "").strip()
                    break

                status_acoes.append(f"aplicar_tags: ok ({resultado_tool or 'sem retorno'})")
            except Exception as e:
                status_acoes.append(f"aplicar_tags: erro ({str(e)})")
            finally:
                executadas.append(acao_nome)
                _remover_especialista_do_estado(state, "tags_crm")

        elif acao_nome == "transferir_atendimento":
            try:
                if not (empresa_id and lead_id):
                    status_acoes.append("transferir_atendimento: erro (lead/empresa ausentes)")
                    executadas.append(acao_nome)
                    continue

                transferencia_tool = criar_tool_transferencia_contextual(
                    empresa_id=empresa_id,
                    lead_id=lead_id,
                    conexao_id=str(conexao_id) if conexao_id else None,
                )
                llm = await get_llm(empresa_id)
                llm_with_tools = llm.bind_tools([transferencia_tool])
                tool_node = ToolNode([transferencia_tool])
                historico_global = str(state.get("historico_bd") or "").strip()
                prompt_transferencia = (
                    "Você é um executor técnico de sistema. "
                    "Sua tarefa é avaliar pedido de atendimento humano e, quando aplicável, "
                    "executar a tool action_transferir_atendimento. "
                    "Não responda ao cliente; apenas execute a ação interna.\n"
                    f"HISTORICO_GLOBAL_READ_ONLY:\n{historico_global}\n"
                )
                mensagens = [("system", _prepend_resumo_cliente_system_prompt(state, prompt_transferencia)), ("user", ultima_mensagem)]
                retorno_transferencia = ""
                for _ in range(4):
                    resposta = await llm_with_tools.ainvoke(mensagens)
                    mensagens.append(resposta)
                    if hasattr(resposta, "tool_calls") and resposta.tool_calls:
                        resultado_toolnode = await tool_node.ainvoke({"messages": [resposta]})
                        mensagens.extend(resultado_toolnode.get("messages", []))
                        for tool_msg in resultado_toolnode.get("messages", []):
                            conteudo = str(getattr(tool_msg, "content", "") or "").strip()
                            if conteudo:
                                retorno_transferencia = conteudo
                                _marcar_bot_pausado_se_necessario(state, conteudo)
                        break
                    retorno_transferencia = str(getattr(resposta, "content", "") or "").strip()

                if not retorno_transferencia:
                    # fallback seguro sem depender de destino estruturado
                    retorno_transferencia = await transferir_para_humano(
                        telefone=str(state.get("identificador_origem") or ""),
                        empresa_id=empresa_id,
                    )
                _marcar_bot_pausado_se_necessario(state, retorno_transferencia)
                status_acoes.append(f"transferir_atendimento: ok ({retorno_transferencia})")
            except Exception as e:
                status_acoes.append(f"transferir_atendimento: erro ({str(e)})")
            finally:
                executadas.append(acao_nome)

        elif acao_nome == "fechar_conversa":
            try:
                await mover_lead_para_fechado(empresa_id, lead_id)
                status_acoes.append("fechar_conversa: ok")
            except Exception as e:
                status_acoes.append(f"fechar_conversa: erro ({str(e)})")
            finally:
                executadas.append(acao_nome)
        else:
            status_acoes.append(f"{acao_nome}: ignorada (ação desconhecida)")
            executadas.append(acao_nome)

    state["acoes_sistema_pendentes"] = []
    state["acoes_sistema_executadas"] = sorted(list({a for a in executadas if str(a).strip()}))
    state["acoes_sistema_status"] = status_acoes
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
    "transferir_para_humano": transferir_para_humano,  # ADICIONE ESTA LINHA
    "tool_atualizar_tags_lead": tool_atualizar_tags_lead,
    "tool_aplicar_tag_dinamica": tool_aplicar_tag_dinamica.coroutine,
    "tool_consultar_tags_empresa": tool_consultar_tags_empresa.coroutine,
}


class ConsultarHorariosLivresInput(BaseModel):
    data: str = Field(description="Data no formato YYYY-MM-DD.")


class AgendarHorarioInput(BaseModel):
    data_hora: str = Field(description="Data e hora do agendamento no formato YYYY-MM-DD HH:MM ou ISO.")
    cliente_nome: str = Field(description="Nome do cliente para vincular o compromisso.")


class CancelarAgendamentoInput(BaseModel):
    agendamento_id: str = Field(description="UUID do agendamento que deve ser cancelado.")


def criar_ferramentas_agendamento_contextual(empresa_id: str, lead_id: Optional[str]) -> list[StructuredTool]:
    consulta_parcial = partial(consultar_horarios_livres.coroutine, empresa_id=empresa_id)
    agendar_parcial = partial(agendar_horario.coroutine, empresa_id=empresa_id, lead_id=(lead_id or ""))

    async def _consultar(data: str) -> str:
        return await consulta_parcial(data=data)

    async def _agendar(data_hora: str, cliente_nome: str) -> str:
        return await agendar_parcial(data_hora=data_hora, cliente_nome=cliente_nome)

    async def _cancelar(agendamento_id: str) -> str:
        return await cancelar_agendamento.coroutine(agendamento_id=agendamento_id)

    return [
        StructuredTool(
            name="consultar_horarios_livres",
            description=(
                "Consulta a disponibilidade de horários livres em uma data específica "
                "na agenda da empresa."
            ),
            args_schema=ConsultarHorariosLivresInput,
            coroutine=_consultar,
        ),
        StructuredTool(
            name="agendar_horario",
            description="Realiza um agendamento para o lead atual na agenda da empresa.",
            args_schema=AgendarHorarioInput,
            coroutine=_agendar,
        ),
        StructuredTool(
            name="cancelar_agendamento",
            description="Cancela um agendamento existente com base no seu ID.",
            args_schema=CancelarAgendamentoInput,
            coroutine=_cancelar,
        ),
    ]

async def ler_dados_empresa(empresa_uuid) -> tuple:
    if not empresa_uuid:
        return "Empresa Padrão", ""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
        empresa = result.scalars().first()
        if empresa:
            return empresa.nome_empresa, empresa.area_atuacao or ""
        return "Empresa Padrão", ""


async def get_unidades_formatadas(empresa_id: str, db) -> str:
    empresa_uuid = uuid.UUID(str(empresa_id))
    result = await db.execute(
        select(EmpresaUnidade)
        .where(EmpresaUnidade.empresa_id == empresa_uuid)
        .order_by(EmpresaUnidade.is_matriz.desc(), EmpresaUnidade.nome_unidade.asc())
    )
    unidades = result.scalars().all()
    if not unidades:
        return "(nenhuma unidade cadastrada)"

    linhas: list[str] = []
    for idx, unidade in enumerate(unidades, start=1):
        tipo = "Matriz" if bool(getattr(unidade, "is_matriz", False)) else "Filial"
        nome = str(getattr(unidade, "nome_unidade", "") or f"Unidade {idx}")
        endereco = str(getattr(unidade, "endereco_completo", "") or "não informado")
        link_maps = str(getattr(unidade, "link_google_maps", "") or "").strip()
        horario = str(getattr(unidade, "horario_funcionamento", "") or "").strip()
        bloco = [
            f"{idx}. {nome} ({tipo})",
            f"- Endereço: {endereco}",
        ]
        if horario:
            bloco.append(f"- Horário: {horario}")
        if link_maps:
            bloco.append(f"- Google Maps: {link_maps}")
        linhas.append("\n".join(bloco))
    return "\n\n".join(linhas)


def _normalize_stage_name(value: str | None) -> str:
    texto = str(value or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in texto if not unicodedata.combining(ch))


async def mover_lead_para_fechado(empresa_id: str | None, lead_id: str | None) -> None:
    if not empresa_id or not lead_id:
        return
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
        lead_uuid = uuid.UUID(str(lead_id))
    except (ValueError, TypeError):
        return

    async with AsyncSessionLocal() as session:
        result_etapas = await session.execute(
            select(CRMEtapa.id, CRMEtapa.nome)
            .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
            .where(CRMFunil.empresa_id == empresa_uuid)
            .order_by(CRMEtapa.ordem.asc())
        )
        etapa_fechamento_id = None
        for etapa_id, etapa_nome in result_etapas.all():
            nome_norm = _normalize_stage_name(etapa_nome)
            if "fechado" in nome_norm or "concluido" in nome_norm:
                etapa_fechamento_id = etapa_id
                break
        if not etapa_fechamento_id:
            return

        await session.execute(
            update(CRMLead)
            .where(CRMLead.id == lead_uuid, CRMLead.empresa_id == empresa_uuid)
            .values(etapa_id=etapa_fechamento_id)
        )
        await session.commit()
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
    mensagens: list  # Memória global única da conversa (cliente + assistente), somente a orquestradora atualiza.
    historico_bd: str          # Histórico real do PostgreSQL, formatado
    resumo_cliente: str
    historico_curto: str       # Histórico resumido (últimas 5 mensagens) para roteamento/especialistas
    nome_contato: Optional[str]
    especialistas_identificados: List[str]
    especialistas_selecionados: List[EspecialistaSelecionadoState]
    fila_agentes: List[str]
    super_contexto_especialistas: str
    respostas_especialistas: List[str]
    acoes_sistema_pendentes: List[str]
    acoes_sistema_executadas: List[str]
    acoes_sistema_status: List[str]
    handoff_requested: bool
    resposta_final: Optional[str]
    status_conversa: Optional[str]
    lead_id: Optional[str]
    roteamento_tentado: Optional[bool]
    saudacao_pendente: Optional[bool]
    saudacao_processada: Optional[bool]
    especialista_respondeu_no_ciclo: Optional[bool]
    bot_foi_pausado: Optional[bool]
    fluxo_encerrado: Optional[bool]
    empresa: Optional[Any]
    total_msgs_historico: Optional[int]
    primeiro_contato: Optional[bool]
    especialista_corrente: Optional[EspecialistaSelecionadoState]

class AnaliseRoteador(BaseModel):
    termos_busca: List[str] = Field(description="Lista de termos de busca otimizados para roteamento semântico de especialistas no banco vetorial.")
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
            if lead.bot_pausado_ate and lead.bot_pausado_ate > datetime.utcnow():
                conexao_id_limpo = str(state.get("conexao_id") or "").strip()
                try:
                    conexao_uuid = uuid.UUID(conexao_id_limpo) if conexao_id_limpo else None
                except (ValueError, TypeError):
                    conexao_uuid = None

                for texto_inbound in mensagens_pendentes:
                    session.add(
                        MensagemHistorico(
                            lead_id=lead.id,
                            conexao_id=conexao_uuid,
                            texto=texto_inbound,
                            from_me=False,
                        )
                    )
                await session.commit()
                state["lead_id"] = str(lead.id)
                state["nome_contato"] = lead.nome_contato
                state["resposta_final"] = None
                state["fluxo_encerrado"] = True
                print(
                    "[NODE CRM] Bot pausado para este lead até "
                    f"{lead.bot_pausado_ate}. Mensagem registrada no histórico e fluxo encerrado."
                )
                return state
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
    ultima_mensagem = _ultima_mensagem_cliente(state)
    
    llm = await get_llm(state.get("empresa_id"))
    resposta = await llm.ainvoke(
        [("system", _prepend_resumo_cliente_system_prompt(state, prompt)), ("user", ultima_mensagem)]
    )
    state["resposta_final"] = resposta.content

    return state

async def node_atendente(state: AgentState):
    print(f"[NODE ATENDENTE] Iniciando processamento...")
    import uuid
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
    lead_id = state.get("lead_id")
    ja_respondeu = "Assistente:" in str(historico_bd or "")
    # TODO: Implementar reset de sessão por tempo (ex: 12h).
    is_primeira_interacao = not ja_respondeu

    # Carrega configuração da empresa para garantir injeção de contexto no prompt.
    empresa = None
    saudacao_configurada = ""
    ia_instrucoes_personalizadas = ""
    ia_personalidade = ""
    ia_regras_negocio = ""
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
        ia_personalidade = str(getattr(empresa, "ia_personalidade", "") or "").strip()
        ia_regras_negocio = str(getattr(empresa, "ia_regras_negocio", "") or "").strip()

    if not empresa or not any([
        saudacao_configurada,
        ia_instrucoes_personalizadas,
        ia_personalidade,
        ia_regras_negocio,
    ]):
        logger.error(
            "[NODE ATENDENTE] Dados da empresa vazios ou incompletos para empresa_id=%s "
            "(saudacao=%s, instrucoes=%s, personalidade=%s, regras=%s)",
            empresa_id,
            bool(saudacao_configurada),
            bool(ia_instrucoes_personalizadas),
            bool(ia_personalidade),
            bool(ia_regras_negocio),
        )
    
    # Helper para montar bloco de contexto XML
    def _resposta_indica_conclusao(texto: str) -> bool:
        t = str(texto or "").lower()
        sinais = [
            "venda conclu",
            "pedido conclu",
            "compra conclu",
            "atendimento finalizado",
            "atendimento conclu",
            "agendamento confirmado",
            "agendamento conclu",
        ]
        return any(s in t for s in sinais)

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

        personalidade_prompt = ia_personalidade or "(não configurada)"
        regras_prompt = ia_regras_negocio or "(não configuradas)"

        blocos.append(
            "Diretriz de roteamento de sistema: Se o cliente pedir para falar com atendente humano, "
            "ou se a conversa chegar ao fim natural (fechamento), acione o [Especialista de Sistema / Ferramenta de Roteamento] "
            "e avise o cliente amigavelmente que a ação está sendo tomada."
        )
        blocos.append(
            "DIRETRIZ DE OBJETIVIDADE: NUNCA peça permissão para enviar uma informação ou oferta "
            "(ex: Posso te mostrar?). Se você tem a informação (como preços, cursos ou bolsas), "
            "ENVIE IMEDIATAMENTE. Se faltar contexto para buscar, faça a pergunta de forma direta. "
            "Proibido usar excesso de confirmações como Perfeito, Que ótimo, seguidas."
        )

        formatacao_base = (
            "DIRETRIZ DE FORMATAÇÃO (CRÍTICO): Você está se comunicando exclusivamente pelo WhatsApp. "
            "NUNCA use formatação Markdown padrão.\n"
            "- Proibido usar ** para negrito. Use apenas um asterisco: *texto*.\n"
            "- Proibido usar # ou ## para títulos.\n"
            "- Para itálico, use _texto_."
        )
        blocos.append(formatacao_base)

        blocos.append(f"Identidade da IA: Você é {nome_agente}, assistente virtual da {nome_empresa_prompt}.")

        if incluir_especialistas and respostas_especialistas:
            respostas_texto = "\n".join([str(r) for r in respostas_especialistas if str(r).strip()])
            blocos.append(
                "[INFORMAÇÕES DE CONSULTA OBTIDAS PELO ESPECIALISTA]\n"
                f"{respostas_texto}\n"
                "[FIM DAS INFORMAÇÕES DE CONSULTA]\n\n"
                "REGRA: As informações acima são apenas para sua consulta. "
                "Você DEVE manter sua Persona e Diretrizes Primárias. "
                "Use os dados acima para formular sua resposta com suas próprias palavras, "
                "mantendo o tom de voz amigável e conversacional."
            )

        blocos.append(
            "AS INSTRUÇÕES A SEGUIR SÃO SOBERANAS E OBRIGATÓRIAS. ELAS SOBREPÕEM-SE A QUALQUER REGRA ANTERIOR. "
            "SE HOUVER UM MENU OU SCRIPT ABAIXO, VOCÊ DEVE SEGUI-LO À RISCA SEM ALTERAR A ESTRUTURA, "
            "NOMENCLATURA OU QUANTIDADE DE OPÇÕES."
        )
        blocos.append(
            "[SOBERANIA] Diretrizes de Atendimento e Regras de Negócio:\n"
            f"- Diretrizes de Atendimento e Estratégia de Vendas: {regras_prompt}.\n"
            f"- Identidade e Tom de Voz da IA: {personalidade_prompt}."
        )

        return "\n\n".join(blocos).strip()

    # ── FASE DE SÍNTESE (Voltando dos Especialistas) ─────────────────────────────
    if respostas_especialistas:
        print(f"[NODE ATENDENTE] Fase de Síntese: Consolidando {len(respostas_especialistas)} resposta(s)...")

        bloco_xml_sintese = _bloco_xml(historico_bd, respostas_especialistas)

        prompt_sintese = f"""{_montar_system_prompt_modular(incluir_especialistas=True)}

{bloco_xml_sintese}

<instrucao_final>
Você recebeu os DADOS CRUS dos especialistas em <respostas_especialistas> (formato JSON com "dados", "fontes", "erros").
Sua tarefa é ANALISAR CUIDADOSAMENTE essas respostas, cruzar com o histórico recente e formular a resposta final ao cliente assumindo a persona da empresa.

REGRAS DE ANÁLISE E SÍNTESE:
1. DESPEDIDA DE TRANSFERÊNCIA: Se algum especialista retornar "SISTEMA_BOT_PAUSADO", "AGUARDANDO_HUMANO" ou indicar que a transferência foi feita, você DEVE encerrar sua resposta avisando educadamente que um atendente humano assumirá a conversa em instantes. Não faça mais perguntas se a transferência ocorreu.
2. COESÃO ABSOLUTA: Mescle as informações de múltiplos especialistas (ex: endereço + curso + horário) em um texto fluido e contínuo. Não repita saudações a cada frase. Pareça uma única mente brilhante respondendo a tudo.
3. CAMUFLAGEM TÉCNICA: NUNCA mencione que você consultou "especialistas", "JSON", "ferramentas", "APIs" ou "banco de dados". Entregue a informação como se você mesma soubesse.
4. INTEGRIDADE DE LINKS: Se os especialistas trouxerem URLs (ex: Google Maps), cole-as EXATAMENTE como chegaram. Nunca abrevie ou invente links.
5. TRATAMENTO DE FALHAS: Se algum especialista disser que não achou a informação, seja empática. Diga que não localizou aquele detalhe no momento, forneça os dados que você conseguiu e sugira ajuda da equipe.

Super-contexto das regras de negócio dos especialistas envolvidos:
<super_contexto_especialistas>
{super_contexto_especialistas or '(sem super-contexto consolidado)'}
</super_contexto_especialistas>

Responda em uma única mensagem clara.
Siga RIGOROSAMENTE a "Identidade e Tom de Voz da IA" e a "DIRETRIZ DE FORMATAÇÃO (CRÍTICO)".
ATENÇÃO MÁXIMA: Respeite ABSOLUTAMENTE a seção [SOBERANIA]
</instrucao_final>"""

        _conversation_debug_log(f"--- PROMPT FINAL ATENDENTE (SINTESE) ---\n{prompt_sintese}", flush=True)
        mensagens_para_llm = [
            SystemMessage(content=_prepend_resumo_cliente_system_prompt(state, prompt_sintese))
        ] + _to_chat_messages(mensagens_recentes)
        llm = await get_llm(empresa_id)
        resposta = await llm.ainvoke(mensagens_para_llm)

        state["resposta_final"] = resposta.content
        status_conv = "ABERTA"
        if state.get("handoff_requested"):
            status_conv = "HANDOFF"
        elif _resposta_indica_conclusao(str(getattr(resposta, "content", "") or "")):
            status_conv = "FINALIZADA"
        state["status_conversa"] = status_conv
        pendentes = list(state.get("acoes_sistema_pendentes") or [])
        executadas = list(state.get("acoes_sistema_executadas") or [])
        if status_conv == "HANDOFF" and "transferir_atendimento" not in pendentes and "transferir_atendimento" not in executadas:
            pendentes.append("transferir_atendimento")
        if status_conv == "FINALIZADA" and "fechar_conversa" not in pendentes and "fechar_conversa" not in executadas:
            pendentes.append("fechar_conversa")
        state["acoes_sistema_pendentes"] = pendentes
        state["respostas_especialistas"] = []
        state["especialistas_identificados"] = []
        state["especialistas_selecionados"] = []
        state["fila_agentes"] = []
        state["super_contexto_especialistas"] = ""
        state["roteamento_tentado"] = False

        return state

    # ── FASE INICIAL (Fluxo único via roteamento + resposta principal) ───────────
    roteamento_tentado = bool(state.get("roteamento_tentado"))
    if not roteamento_tentado:
        print("[NODE ATENDENTE] Primeira passagem: encaminhando para roteamento sem responder ainda.")
        state["roteamento_tentado"] = True
        state["saudacao_processada"] = False
        state["saudacao_pendente"] = False
        state["resposta_final"] = None
        state["respostas_especialistas"] = []
        state["super_contexto_especialistas"] = ""
        return state

    print("[NODE ATENDENTE] Sem respostas de especialistas; gerando resposta final no LLM principal.")
    bloco_xml_direto = _bloco_xml(historico_bd, [])
    prompt_resposta_direta = f"""{_montar_system_prompt_modular(incluir_especialistas=False)}

{bloco_xml_direto}

<instrucao_final>
Você deve responder diretamente à solicitação do cliente com base no seu conhecimento, no histórico recente e nas diretrizes da empresa.

REGRAS DE RESPOSTA DIRETA:
1. TOM E PERSONA: Assuma completamente a identidade da empresa. Seja cordial, resolutiva e empática.
2. CONCISÃO E CLAREZA: Vá direto ao ponto. Use listas em bullet points se necessário, e emojis com moderação para manter a leveza.
3. LIMITES DE CONHECIMENTO: Se o cliente perguntar algo que exija ação no sistema interno (ex: transferências, agendamentos complexos ou financeiro) e você não tiver uma ferramenta direta para isso, informe educadamente que você acionará o departamento responsável.
4. NUNCA mencione que você é uma IA, um LLM, ou que está lendo um "prompt".

Responda em uma única mensagem clara.
Siga RIGOROSAMENTE a "Identidade e Tom de Voz da IA" e a "DIRETRIZ DE FORMATAÇÃO (CRÍTICO)".
ATENÇÃO MÁXIMA: Respeite ABSOLUTAMENTE a seção [SOBERANIA]
</instrucao_final>"""

    _conversation_debug_log(f"--- PROMPT FINAL ATENDENTE (RESPOSTA DIRETA) ---\n{prompt_resposta_direta}", flush=True)
    mensagens_para_llm = [
        SystemMessage(content=_prepend_resumo_cliente_system_prompt(state, prompt_resposta_direta))
    ] + _to_chat_messages(mensagens_recentes)
    llm = await get_llm(empresa_id)
    resposta = await llm.ainvoke(mensagens_para_llm)

    state["resposta_final"] = resposta.content
    status_conv = "ABERTA"
    if state.get("handoff_requested"):
        status_conv = "HANDOFF"
    elif _resposta_indica_conclusao(str(getattr(resposta, "content", "") or "")):
        status_conv = "FINALIZADA"
    state["status_conversa"] = status_conv
    pendentes = list(state.get("acoes_sistema_pendentes") or [])
    executadas = list(state.get("acoes_sistema_executadas") or [])
    if status_conv == "HANDOFF" and "transferir_atendimento" not in pendentes and "transferir_atendimento" not in executadas:
        pendentes.append("transferir_atendimento")
    if status_conv == "FINALIZADA" and "fechar_conversa" not in pendentes and "fechar_conversa" not in executadas:
        pendentes.append("fechar_conversa")
    state["acoes_sistema_pendentes"] = pendentes
    state["especialistas_identificados"] = []
    state["respostas_especialistas"] = []
    state["especialistas_selecionados"] = []
    state["fila_agentes"] = []
    state["super_contexto_especialistas"] = ""
    state["roteamento_tentado"] = False
    return state

async def node_roteador_maestro(state: AgentState):
    print("[NODE ROTEADOR] Roteamento semântico via embeddings...")
    import uuid

    # Se já existem contribuições no ciclo, o roteador atua apenas como controlador da fila.
    respostas_no_ciclo = state.get("respostas_especialistas") or []
    if isinstance(respostas_no_ciclo, list) and respostas_no_ciclo:
        return state

    empresa_id = state.get("empresa_id")
    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        empresa_uuid = None

    ultima_mensagem = _ultima_mensagem_cliente(state)
    if not ultima_mensagem:
        state["especialistas_identificados"] = []
        state["especialistas_selecionados"] = []
        state["fila_agentes"] = []
        state["especialista_corrente"] = None
        state["super_contexto_especialistas"] = ""
        state["respostas_especialistas"] = []
        state["handoff_requested"] = False
        state["saudacao_pendente"] = False
        return state

    if _is_primeiro_contato(state):
        print("[NODE ROTEADOR] Primeiro contato detectado: pulando embeddings e forçando especialista de saudação.")
        especialista_escolhido = None
        if empresa_uuid:
            try:
                async with AsyncSessionLocal() as session:
                    result_especialistas = await session.execute(
                        select(Especialista).where(
                            Especialista.empresa_id == empresa_uuid,
                            Especialista.ativo.is_(True),
                        )
                    )
                    especialistas_ativos = list(result_especialistas.scalars().all())

                    def _prioridade_saudacao(esp: Especialista) -> int:
                        nome_norm = _normalizar_chave_especialista(getattr(esp, "nome", "") or "")
                        if nome_norm == "especialista_saudacao":
                            return 0
                        if "triagem" in nome_norm:
                            return 1
                        if "saudacao" in nome_norm:
                            return 2
                        return 99

                    if especialistas_ativos:
                        especialista_escolhido = min(especialistas_ativos, key=_prioridade_saudacao)
            except Exception as exc:
                logger.warning("[NODE ROTEADOR] Falha ao buscar especialista inicial de saudação: %s", exc)

        especialista_saudacao = {
            "id": str(getattr(especialista_escolhido, "id", "") or "especialista_saudacao"),
            "nome": "especialista_saudacao",
            "prompt_sistema": str(getattr(especialista_escolhido, "prompt_sistema", "") or ""),
            "usar_rag": bool(getattr(especialista_escolhido, "usar_rag", False)),
        }

        nome_escolhido = str(getattr(especialista_escolhido, "nome", "") or "").strip() or "fallback:especialista_saudacao"
        print(f"[NODE ROTEADOR] Saudação inicial selecionada: {nome_escolhido}")

        respostas_existentes = state.get("respostas_especialistas") or []
        if not isinstance(respostas_existentes, list):
            respostas_existentes = []

        state["saudacao_pendente"] = True
        state["saudacao_processada"] = False
        state["handoff_requested"] = False
        state["especialistas_selecionados"] = [especialista_saudacao]
        state["especialistas_identificados"] = ["especialista_saudacao"]
        state["fila_agentes"] = ["especialista_saudacao"]
        state["especialista_corrente"] = None
        state["super_contexto_especialistas"] = ""
        state["respostas_especialistas"] = respostas_existentes
        return state

    state["saudacao_pendente"] = False

    handoff_markers = (
        "humano", "atendente", "pessoa", "suporte humano", "falar com", "transferir",
    )
    msg_lower = ultima_mensagem.lower()
    is_handoff = any(marker in msg_lower for marker in handoff_markers)

    # Detecção de Confirmação de Transferência (Evitar Efeito Manada)
    ultima_ia = _ultima_mensagem_assistente(state).lower()
    is_short_confirm = msg_lower in ["sim", "quero", "pode", "por favor", "pode transferir", "sim por favor"]

    if is_short_confirm and any(m in ultima_ia for m in ["transferir", "humano", "atendente", "especialista"]):
        is_handoff = True

    state["handoff_requested"] = is_handoff

    # Se o usuário apenas confirmou a transferência, corta o roteamento dos especialistas
    if is_handoff and is_short_confirm:
        state["especialistas_identificados"] = []
        state["especialistas_selecionados"] = []
        state["fila_agentes"] = []
        state["especialista_corrente"] = None
        state["respostas_especialistas"] = []
        return state

    historico_curto_estado = str(state.get("historico_curto") or "").strip()
    historico_curto_dinamico = _historico_curto_roteador(state, limite=6)
    if historico_curto_estado and historico_curto_dinamico:
        historico_curto_roteador = f"{historico_curto_estado}\n{historico_curto_dinamico}".strip()
    else:
        historico_curto_roteador = historico_curto_estado or historico_curto_dinamico
    historico_turnos_maestro = _turnos_consolidados_roteador(state, limite_turnos=3)
    ultima_ia_contexto = _ultima_mensagem_assistente(state)

    def _parse_ids_json(raw: str) -> list[str]:
        texto = str(raw or "").strip()
        if not texto:
            return []

        tentativas = [texto, texto.replace("'", '"')]
        trecho_lista = re.search(r"\[[\s\S]*\]", texto)
        if trecho_lista:
            trecho = trecho_lista.group(0).strip()
            tentativas.extend([trecho, trecho.replace("'", '"')])

        for candidato in tentativas:
            try:
                data = json.loads(candidato)
                if isinstance(data, list):
                    return [str(item).strip() for item in data if str(item).strip()]
            except Exception:
                continue
        return []

    async with AsyncSessionLocal() as session:
        router_service = SemanticRouterService(session)
        roteamento = await router_service.get_top_specialists_contextual(
            query_text=ultima_mensagem,
            empresa_id=str(empresa_uuid) if empresa_uuid else None,
            recent_history_text=historico_curto_roteador,
            top_k=5,
        )
        termos_expandidos = str(roteamento.get("termos_expandidos") or "").strip()
        candidatos = roteamento.get("candidatos") or []
        if not isinstance(candidatos, list):
            candidatos = []

        nomes_candidatos = [str(item.get("nome") or "").strip() for item in candidatos if isinstance(item, dict)]
        resposta_menu_numerica = bool(re.fullmatch(r"\d{1,2}", msg_lower))
        menu_detectado_na_ultima_ia = bool(_extrair_opcoes_menu(ultima_ia_contexto))
        continuidade_menu_ativa = resposta_menu_numerica and menu_detectado_na_ultima_ia

        def _score_ajustado_candidato(item: dict) -> float:
            score_base = float(item.get("similarity") or 0.0)
            peso = int(item.get("peso_prioridade", 1) or 1)
            # O peso atua como um bônus agressivo no score (cada ponto dá 15% de bônus)
            score_ponderado = score_base + (peso * 0.15)
            nome_norm = _normalizar_chave_especialista(str(item.get("nome") or ""))
            if continuidade_menu_ativa and "triagem_microline" in nome_norm:
                return min(1.0, score_ponderado + 0.12)
            if continuidade_menu_ativa and "triagem" in nome_norm:
                return min(1.0, score_ponderado + 0.08)
            return score_ponderado

        candidatos_payload = [
            {
                "id": str(item.get("id") or "").strip(),
                "nome": str(item.get("nome") or "").strip(),
                "peso_prioridade": int(item.get("peso_prioridade", 1) or 1),
                "modelo_ia": str(item.get("modelo_ia") or "").strip(),
                "missao": str(item.get("descricao_missao") or "").strip(),
                "similaridade": float(item.get("similarity") or 0.0),
                "score_ajustado": _score_ajustado_candidato(item),
            }
            for item in candidatos
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]

        ids_selecionados_maestro: list[str] = []
        if candidatos_payload:
            modelo_maestro = next(
                (
                    str(item.get("modelo_ia") or "").strip()
                    for item in candidatos_payload
                    if str(item.get("modelo_ia") or "").strip()
                ),
                "gpt-5.4",
            )
            llm_maestro = await get_llm(
                state.get("empresa_id"),
                modelo_ia=modelo_maestro,
            )
            prompt_decisao = (
                "Você é o Maestro do AgenteOS. "
                f"Analise o histórico e a intenção expandida: '{termos_expandidos}'.\n"
                "Sua missão é selecionar, dentre os candidatos abaixo, quais devem ser acionados para responder ao usuário.\n"
                "REGRAS CRÍTICAS:\n"
                "- Você DEVE dar preferência absoluta aos especialistas com maior 'peso_prioridade' (ex: 2, 3), incluindo-os sempre que o contexto for de vendas ou serviços.\n"
                "- Se a última mensagem da IA continha menu e o usuário respondeu com número (ex: '3'), NÃO escolha apenas o agente de Triagem. VOCÊ DEVE incluir os especialistas comerciais que tratam daquela opção (Vendas, Cursos, etc).\n"
                "- Junte especialistas na fila! Se houver um de Triagem (peso 1) e um de Vendas (peso 3), devolva os IDs de AMBOS.\n"
                "- É OBRIGATÓRIO considerar a ÚLTIMA MENSAGEM DA IA para resolver respostas curtas.\n"
                "- Retorne APENAS um array JSON com os IDs dos selecionados. Ex: ['id1', 'id2']."
            )
            entrada_decisao = (
                "HISTORICO_CURTO_ULTIMAS_5_MENSAGENS:\n"
                f"{historico_curto_roteador or '(sem histórico)'}\n\n"
                "ULTIMOS_3_TURNOS_CONSOLIDADOS:\n"
                f"{historico_turnos_maestro or '(sem histórico)'}\n\n"
                "ULTIMA_MENSAGEM_DA_IA:\n"
                f"{ultima_ia_contexto or '(sem mensagem anterior da IA)'}\n\n"
                f"TERMOS_EXPANDIDOS: {termos_expandidos or ultima_mensagem}\n\n"
                f"CANDIDATOS_TOP5 (Nome + Missão):\n{json.dumps(candidatos_payload, ensure_ascii=False)}"
            )

            try:
                resposta_maestro = await llm_maestro.ainvoke(
                    [("system", _prepend_resumo_cliente_system_prompt(state, prompt_decisao)), ("user", entrada_decisao)]
                )
                ids_selecionados_maestro = _parse_ids_json(getattr(resposta_maestro, "content", ""))
            except Exception as exc:
                logger.warning("[MAESTRO] Falha na decisão contextual por LLM: %s", exc)
                ids_selecionados_maestro = []

        candidatos_por_id = {
            str(item.get("id") or "").strip(): item
            for item in candidatos
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        ids_validos = [esp_id for esp_id in ids_selecionados_maestro if esp_id in candidatos_por_id]
        candidatos_rankeados = sorted(
            [item for item in candidatos if isinstance(item, dict) and str(item.get("id") or "").strip()],
            key=_score_ajustado_candidato,
            reverse=True,
        )

        margem_tolerancia = 0.08
        ids_por_margem: list[str] = []
        if candidatos_rankeados:
            melhor_score = _score_ajustado_candidato(candidatos_rankeados[0])
            if melhor_score > 0:
                limite = melhor_score * (1 - margem_tolerancia)
                ids_por_margem = [
                    str(item.get("id") or "").strip()
                    for item in candidatos_rankeados
                    if _score_ajustado_candidato(item) >= limite
                ]

        ids_base = ids_validos or [str(item.get("id") or "").strip() for item in candidatos_rankeados[:1]]
        ids_combinados: list[str] = []
        for esp_id in [*ids_base, *ids_por_margem]:
            if esp_id and esp_id in candidatos_por_id and esp_id not in ids_combinados:
                ids_combinados.append(esp_id)

        # Removemos os bloqueios restritivos de menu para permitir que a fila
        # atue livremente baseada nos pesos e no LLM.
        especialistas_match = [candidatos_por_id[esp_id] for esp_id in ids_combinados if esp_id in candidatos_por_id]
        
        # Força a ordenação final da fila pelo MAIOR PESO para que sejam despachados primeiro
        especialistas_match.sort(
            key=lambda x: (
                int(x.get("peso_prioridade", 1) or 1),
                _score_ajustado_candidato(x)
            ),
            reverse=True
        )

        # Fallback determinístico: resposta numérica para menu da mensagem anterior da IA.
        ultima_msg_usuario = str(ultima_mensagem or "").strip().lower()
        opcoes_menu = _extrair_opcoes_menu(ultima_ia_contexto)
        if not especialistas_match and opcoes_menu and re.fullmatch(r"\d{1,2}", ultima_msg_usuario):
            rotulo_opcao = str(opcoes_menu.get(ultima_msg_usuario, "")).strip()
            if rotulo_opcao:
                alvo_norm = _normalizar_chave_especialista(rotulo_opcao)
                termos_alvo = [tok for tok in re.split(r"\W+", alvo_norm) if tok]

                def _score_candidato(item: dict) -> int:
                    nome = _normalizar_chave_especialista(str(item.get("nome") or ""))
                    missao = _normalizar_chave_especialista(str(item.get("descricao_missao") or ""))
                    base = f"{nome} {missao}".strip()
                    score = 0
                    if alvo_norm and alvo_norm in base:
                        score += 10
                    for termo in termos_alvo:
                        if termo and termo in base:
                            score += 2
                    return score

                candidatos_ordenados = sorted(
                    [item for item in candidatos if isinstance(item, dict)],
                    key=_score_candidato,
                    reverse=True,
                )
                if candidatos_ordenados and _score_candidato(candidatos_ordenados[0]) > 0:
                    especialistas_match = [candidatos_ordenados[0]]
                    logger.info(
                        "[MAESTRO] Fallback de menu aplicado. resposta='%s' opcao='%s' especialista='%s'",
                        ultima_msg_usuario,
                        rotulo_opcao,
                        str(candidatos_ordenados[0].get("nome") or ""),
                    )

        # Fallback de memória conversacional: evita seleção vazia após pergunta prévia da IA.
        resposta_curta = bool(re.fullmatch(r"\d{1,2}|sim|não|nao|ok|blz|beleza", ultima_msg_usuario))
        if not especialistas_match and candidatos and ultima_ia_contexto and resposta_curta:
            primeiro_candidato = candidatos_rankeados[0] if candidatos_rankeados else None
            if primeiro_candidato:
                especialistas_match = [primeiro_candidato]
                logger.info(
                    "[MAESTRO] Fallback contextual aplicado. resposta='%s' ultimo_bot_presente=True especialista='%s'",
                    ultima_msg_usuario,
                    str(primeiro_candidato.get("nome") or ""),
                )
        nomes_selecionados = [str(item.get("nome") or "").strip() for item in especialistas_match if isinstance(item, dict)]
        print(
            f"[MAESTRO - DECISÃO CONTEXTUAL] Candidatos: {nomes_candidatos} | "
            f"Selecionados por Memória: {nomes_selecionados}"
        )

    ids_especialistas = [esp.get("id") for esp in especialistas_match]
    nomes_especialistas = [esp.get("nome") for esp in especialistas_match]
    especialistas_identificados: list[str] = []
    vistos: set[str] = set()
    for esp in especialistas_match:
        if not isinstance(esp, dict):
            continue
        valor = str(esp.get("id") or "").strip()
        if not valor:
            continue
        normalizado = _normalizar_chave_especialista(valor)
        if normalizado in vistos:
            continue
        vistos.add(normalizado)
        especialistas_identificados.append(valor)
    print(
        f"[NODE ROTEADOR] Matches: {len(ids_especialistas)} "
        f"| IDs={ids_especialistas} | Nomes={nomes_especialistas} "
        f"| Identificados={especialistas_identificados}"
    )

    # Mantém especialistas selecionados para a próxima etapa do fluxo.
    state["especialistas_selecionados"] = especialistas_match
    respostas_existentes = state.get("respostas_especialistas") or []
    if not isinstance(respostas_existentes, list):
        respostas_existentes = []
    state["respostas_especialistas"] = respostas_existentes
    state["super_contexto_especialistas"] = ""
    state["especialistas_identificados"] = especialistas_identificados
    state["fila_agentes"] = list(especialistas_identificados)
    state["especialista_corrente"] = None
    return state

async def node_especialista_funcionamento(state: AgentState):
    print("[NODE ESPECIALISTA FUNCIONAMENTO] Processando dúvida de horários...")
    import uuid

    empresa_id = state.get("empresa_id")
    ultima_mensagem = _ultima_mensagem_cliente(state)
    historico_curto = str(state.get("historico_curto") or "").strip() or "(sem histórico curto)"

    dias_funcionamento_raw = None
    excecoes_raw = []
    horario_inicio_legacy = None
    horario_fim_legacy = None
    prompt_painel = ""

    if empresa_id:
        try:
            empresa_uuid = uuid.UUID(str(empresa_id))
            async with AsyncSessionLocal() as session:
                result_agenda = await session.execute(
                    select(AgendaConfiguracao).where(AgendaConfiguracao.empresa_id == empresa_uuid)
                )
                agenda_config = result_agenda.scalars().first()
                if agenda_config:
                    dias_funcionamento_raw = agenda_config.dias_funcionamento
                    excecoes_raw = getattr(agenda_config, "excecoes", [])
                    h_inicio = getattr(agenda_config, "horario_inicio", None)
                    h_fim = getattr(agenda_config, "horario_fim", None)
                    horario_inicio_legacy = h_inicio.strftime("%H:%M") if h_inicio else None
                    horario_fim_legacy = h_fim.strftime("%H:%M") if h_fim else None
                result_esp = await session.execute(
                    select(Especialista).where(
                        Especialista.nome == "especialista_funcionamento",
                        Especialista.empresa_id == empresa_uuid
                    )
                )
                esp_db = result_esp.scalars().first()
                if esp_db and esp_db.prompt_sistema:
                    prompt_painel = esp_db.prompt_sistema
        except Exception as e:
            logger.error("[NODE ESPECIALISTA FUNCIONAMENTO] Falha ao carregar AgendaConfiguracao: %s", e)

    respostas_existentes = state.get("respostas_especialistas") or []
    if not isinstance(respostas_existentes, list):
        respostas_existentes = []

    agora_br = datetime.now(ZoneInfo("America/Sao_Paulo"))
    data_hoje_iso = agora_br.strftime("%Y-%m-%d")
    dias_semana_extenso = [
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ]
    dias_semana_curto = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
    dia_semana = dias_semana_extenso[agora_br.weekday()]
    dia_semana_curto = dias_semana_curto[agora_br.weekday()]
    data_hora_atual = agora_br.strftime("%d/%m/%Y às %H:%M")
    hora_agora = agora_br.strftime("%H:%M")

    # Prioridade para exceções (feriados/datas especiais) na data atual.
    if isinstance(excecoes_raw, list):
        excecao_hoje = next(
            (
                item for item in excecoes_raw
                if isinstance(item, dict) and str(item.get("data", "")).strip() == data_hoje_iso
            ),
            None
        )
        if excecao_hoje:
            titulo = str(excecao_hoje.get("titulo") or "data especial").strip()
            aberto_excecao = bool(excecao_hoje.get("aberto", False))
            if not aberto_excecao:
                resposta_excecao = f"Hoje é feriado ({titulo}) e a empresa está fechada."
            else:
                inicio_excecao = str(excecao_hoje.get("inicio") or "não informado")
                fim_excecao = str(excecao_hoje.get("fim") or "não informado")
                abertos_agora_excecao = bool(
                    inicio_excecao != "não informado"
                    and fim_excecao != "não informado"
                    and inicio_excecao <= hora_agora <= fim_excecao
                )
                status_excecao = "Abertos" if abertos_agora_excecao else "Fechados"
                resposta_excecao = (
                    f"Hoje é {titulo} e temos um horário especial: das {inicio_excecao} às {fim_excecao}. "
                    f"No momento estamos {status_excecao}."
                )

            extracao = {
                "dados": resposta_excecao,
                "fontes": ["especialista_nativo"],
                "erros": [],
            }
            respostas_existentes.append(
                f"[ESPECIALISTA: especialista_funcionamento] {json.dumps(extracao, ensure_ascii=False)}"
            )
            state["respostas_especialistas"] = respostas_existentes
            _remover_especialista_do_estado(state, "funcionamento", "especialista_funcionamento")
            state["especialista_respondeu_no_ciclo"] = True
            return state

    if not isinstance(dias_funcionamento_raw, dict):
        resposta_texto = "Os horários de funcionamento não estão configurados no momento."
        extracao = {
            "dados": resposta_texto,
            "fontes": ["especialista_nativo"],
            "erros": [],
        }
        respostas_existentes.append(
            f"[ESPECIALISTA: especialista_funcionamento] {json.dumps(extracao, ensure_ascii=False)}"
        )
        state["respostas_especialistas"] = respostas_existentes
        _remover_especialista_do_estado(state, "funcionamento", "especialista_funcionamento")
        state["especialista_respondeu_no_ciclo"] = True
        return state

    dias_normalizados: dict[str, dict[str, Any]] = {
        "seg": {"aberto": False, "inicio": None, "fim": None},
        "ter": {"aberto": False, "inicio": None, "fim": None},
        "qua": {"aberto": False, "inicio": None, "fim": None},
        "qui": {"aberto": False, "inicio": None, "fim": None},
        "sex": {"aberto": False, "inicio": None, "fim": None},
        "sab": {"aberto": False, "inicio": None, "fim": None},
        "dom": {"aberto": False, "inicio": None, "fim": None},
    }

    # Compatibilidade com formato novo (mapa por dia) e legado ({"dias": [...]})
    if "dias" in dias_funcionamento_raw and isinstance(dias_funcionamento_raw.get("dias"), list):
        inicio_legado = str(horario_inicio_legacy or "08:00")
        fim_legado = str(horario_fim_legacy or "18:00")
        for dia_legado in dias_funcionamento_raw.get("dias", []):
            dia_key = str(dia_legado).strip().lower()
            if dia_key in dias_normalizados:
                dias_normalizados[dia_key] = {"aberto": True, "inicio": inicio_legado, "fim": fim_legado}
    else:
        for dia_key in dias_normalizados.keys():
            item = dias_funcionamento_raw.get(dia_key)
            if not isinstance(item, dict):
                continue
            aberto = bool(item.get("aberto", False))
            if not aberto:
                dias_normalizados[dia_key] = {"aberto": False, "inicio": None, "fim": None}
                continue
            inicio = item.get("inicio")
            fim = item.get("fim")
            if isinstance(inicio, str) and isinstance(fim, str):
                dias_normalizados[dia_key] = {"aberto": True, "inicio": inicio, "fim": fim}

    config_hoje = dias_normalizados.get(dia_semana_curto, {"aberto": False, "inicio": None, "fim": None})
    aberto_hoje = bool(config_hoje.get("aberto"))
    inicio_hoje = config_hoje.get("inicio")
    fim_hoje = config_hoje.get("fim")

    if not aberto_hoje:
        frase_hoje = f"Hoje, {dia_semana}, não abrimos."
        status_atual = "Fechados"
    else:
        inicio_hoje = str(inicio_hoje or "não informado")
        fim_hoje = str(fim_hoje or "não informado")
        abertos_agora = bool(inicio_hoje != "não informado" and fim_hoje != "não informado" and inicio_hoje <= hora_agora <= fim_hoje)
        status_atual = "Abertos" if abertos_agora else "Fechados"
        frase_hoje = f"Hoje, {dia_semana}, funcionamos das {inicio_hoje} às {fim_hoje}. No momento estamos {status_atual}."

    prompt_sistema = (
        f"{prompt_painel}\n\n"
        "--- DADOS DE SISTEMA OBRIGATÓRIOS (USE-OS PARA RESPONDER E COMANDAR A ATENDENTE) ---\n"
        f"Hoje é {dia_semana}, {data_hora_atual}.\n"
        f"Configuração de hoje ({dia_semana_curto}): {json.dumps(config_hoje, ensure_ascii=False)}.\n"
        f"HISTORICO_CURTO_READ_ONLY: {historico_curto}.\n"
        f"Frase-base sugerida pelos horários do banco: '{frase_hoje}'.\n"
        "------------------------------------------------------------------------------------\n"
    )

    try:
        llm = await get_llm(empresa_id)
        resposta = await llm.ainvoke(
            [("system", _prepend_resumo_cliente_system_prompt(state, prompt_sistema)), ("user", ultima_mensagem)]
        )
        resposta_texto = str(getattr(resposta, "content", "") or "").strip()
    except Exception as e:
        logger.exception("[NODE ESPECIALISTA FUNCIONAMENTO] Falha ao invocar LLM: %s", e)
        resposta_texto = "No momento não consegui consultar os horários de funcionamento."

    extracao = {
        "dados": resposta_texto,
        "fontes": ["especialista_nativo"],
        "erros": [],
    }
    respostas_existentes.append(
        f"[ESPECIALISTA: especialista_funcionamento] {json.dumps(extracao, ensure_ascii=False)}"
    )
    state["respostas_especialistas"] = respostas_existentes
    _remover_especialista_do_estado(state, "funcionamento", "especialista_funcionamento")
    state["especialista_respondeu_no_ciclo"] = True
    return state


async def node_especialista_localizacao(state: AgentState):
    print("[NODE ESPECIALISTA LOCALIZACAO] Processando dúvida de endereços/unidades...")
    empresa_id = state.get("empresa_id")
    ultima_mensagem = _ultima_mensagem_cliente(state)
    historico_curto = str(state.get("historico_curto") or "").strip() or "(sem histórico curto)"

    respostas_existentes = state.get("respostas_especialistas") or []
    if not isinstance(respostas_existentes, list):
        respostas_existentes = []

    if not empresa_id:
        resposta_texto = "Não foi possível identificar a empresa para consultar unidades."
        extracao = {
            "dados": resposta_texto,
            "fontes": ["especialista_nativo"],
            "erros": [],
        }
        respostas_existentes.append(
            f"[ESPECIALISTA: especialista_localizacao] {json.dumps(extracao, ensure_ascii=False)}"
        )
        state["respostas_especialistas"] = respostas_existentes
        _remover_especialista_do_estado(state, "localizacao", "especialista_localizacao")
        state["especialista_respondeu_no_ciclo"] = True
        return state

    prompt_painel = ""
    try:
        async with AsyncSessionLocal() as session:
            dados_unidades_formatados = await get_unidades_formatadas(empresa_id, session)
            prompt_painel = ""
            import uuid
            empresa_uuid = uuid.UUID(str(empresa_id))
            result_esp = await session.execute(
                select(Especialista).where(
                    Especialista.nome == "especialista_localizacao",
                    Especialista.empresa_id == empresa_uuid
                )
            )
            esp_db = result_esp.scalars().first()
            if esp_db and esp_db.prompt_sistema:
                prompt_painel = esp_db.prompt_sistema
    except Exception as e:
        logger.exception("[NODE ESPECIALISTA LOCALIZACAO] Falha ao buscar unidades: %s", e)
        dados_unidades_formatados = "(erro ao consultar unidades)"

    prompt_sistema = (
        f"{prompt_painel}\n\n"
        "--- DADOS DE SISTEMA OBRIGATÓRIOS (USE-OS PARA RESPONDER E COMANDAR A ATENDENTE) ---\n"
        f"HISTORICO_CURTO_READ_ONLY:\n{historico_curto}\n"
        "[UNIDADES DA EMPRESA CADASTRADAS NO BANCO]\n"
        f"{dados_unidades_formatados}\n"
        "[FIM DAS UNIDADES]\n"
        "------------------------------------------------------------------------------------\n"
    )

    try:
        llm = await get_llm(empresa_id)
        resposta = await llm.ainvoke(
            [("system", _prepend_resumo_cliente_system_prompt(state, prompt_sistema)), ("user", ultima_mensagem)]
        )
        resposta_texto = str(getattr(resposta, "content", "") or "").strip()
    except Exception as e:
        logger.exception("[NODE ESPECIALISTA LOCALIZACAO] Falha ao invocar LLM: %s", e)
        resposta_texto = "No momento não consegui consultar os endereços das unidades."

    extracao = {
        "dados": resposta_texto,
        "fontes": ["especialista_nativo"],
        "erros": [],
    }
    respostas_existentes.append(
        f"[ESPECIALISTA: especialista_localizacao] {json.dumps(extracao, ensure_ascii=False)}"
    )
    state["respostas_especialistas"] = respostas_existentes
    _remover_especialista_do_estado(state, "localizacao", "especialista_localizacao")
    state["especialista_respondeu_no_ciclo"] = True
    return state


async def node_especialista_saudacao(state: AgentState):
    print("[NODE ESPECIALISTA SAUDACAO] Gerando saudação inicial dedicada...")
    import uuid

    empresa_id = state.get("empresa_id")
    ultima_mensagem = _ultima_mensagem_cliente(state)
    historico_global = str(state.get("historico_bd") or "").strip() or "(sem histórico global)"

    saudacao_base_empresa = ""
    prompt_painel = ""
    if empresa_id:
        try:
            empresa_uuid = uuid.UUID(str(empresa_id))
            async with AsyncSessionLocal() as session:
                result_empresa = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
                empresa = result_empresa.scalars().first()
                if empresa:
                    saudacao_base_empresa = str(getattr(empresa, "mensagem_saudacao", "") or "").strip()
                result_esp = await session.execute(
                    select(Especialista).where(
                        Especialista.nome == "especialista_saudacao",
                        Especialista.empresa_id == empresa_uuid
                    )
                )
                esp_db = result_esp.scalars().first()
                if esp_db and esp_db.prompt_sistema:
                    prompt_painel = esp_db.prompt_sistema
        except Exception as e:
            logger.error("[NODE ESPECIALISTA SAUDACAO] Falha ao carregar empresa %s: %s", empresa_id, e)

    hora_atual = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%H:%M")
    prompt_saudacao = (
        f"{prompt_painel}\n\n"
        "--- DADOS DE SISTEMA OBRIGATÓRIOS (USE-OS PARA RESPONDER O CLIENTE) ---\n"
        f"Agora são {hora_atual}.\n"
        "MENSAGEM BASE DA EMPRESA OBRIGATÓRIA (COPIE-A LITERALMENTE):\n"
        f"{saudacao_base_empresa}\n\n"
        "REGRA CRÍTICA: Você DEVE incluir a MENSAGEM BASE DA EMPRESA na sua resposta final exatamente como ela está escrita. Se ela contiver um menu numérico, não o resuma e não altere a estrutura.\n"
        f"Considere este histórico global apenas para leitura contextual: {historico_global}.\n"
        "-----------------------------------------------------------------------\n"
    )

    try:
        llm = await get_llm(empresa_id)
        resposta = await llm.ainvoke(
            [("system", _prepend_resumo_cliente_system_prompt(state, prompt_saudacao)), ("user", ultima_mensagem)]
        )
        resposta_texto = str(getattr(resposta, "content", "") or "").strip()
    except Exception as e:
        logger.exception("[NODE ESPECIALISTA SAUDACAO] Falha ao invocar LLM: %s", e)
        resposta_texto = saudacao_base_empresa or "Olá! Seja bem-vindo(a)."

    respostas_existentes = state.get("respostas_especialistas") or []
    if not isinstance(respostas_existentes, list):
        respostas_existentes = []
    extracao = {
        "dados": resposta_texto,
        "fontes": ["especialista_nativo"],
        "erros": [],
    }
    respostas_existentes.append(
        f"[ESPECIALISTA: especialista_saudacao] {json.dumps(extracao, ensure_ascii=False)}"
    )
    state["respostas_especialistas"] = respostas_existentes
    _remover_especialista_do_estado(state, "saudacao", "especialista_saudacao")
    state["saudacao_processada"] = True
    state["saudacao_pendente"] = False
    state["especialista_respondeu_no_ciclo"] = True
    return state

async def node_especialista_dinamico(state: AgentState):
    try:
        especialista_corrente = state.get("especialista_corrente")
        if isinstance(especialista_corrente, dict) and especialista_corrente:
            especialistas_selecionados = [especialista_corrente]
        else:
            especialistas_selecionados = state.get("especialistas_selecionados", []) or []
        if not isinstance(especialistas_selecionados, list):
            especialistas_selecionados = []

        if not especialistas_selecionados:
            especialistas_refs = state.get("especialistas_identificados", [])
            if not isinstance(especialistas_refs, list):
                especialistas_refs = [especialistas_refs] if especialistas_refs else []
            especialistas_selecionados = [
                {"id": str(item), "nome": str(item), "prompt_sistema": "", "usar_rag": False, "usar_agenda": False}
                for item in especialistas_refs
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
        ultima_mensagem = _ultima_mensagem_cliente(state)
        historico_curto = str(state.get("historico_curto") or "").strip() or "(sem histórico curto)"

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
            usar_agenda_meta = bool(meta_especialista.get("usar_agenda", False))
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
                "REGRA DE SEGURANÇA: NUNCA invente ou crie links de Google Maps ou URLs falsas (como '/0'). "
                "Se o usuário pedir o mapa e você não tiver essa informação exata, diga que não tem o link no momento.\n"
                "Você recebe o histórico curto APENAS para leitura e contexto. "
                "NÃO altere memória e não assuma persona de atendimento final.\n"
                "ORDEM OBRIGATÓRIA DE FERRAMENTAS PARA TRANSFERÊNCIA:\n"
                "1) aplique tags de intenção/setor primeiro;\n"
                "2) registre dados da resposta de confirmação de transferência;\n"
                "3) só então acione a transferência para humano.\n"
                "Nunca acione transferência antes das tags de intenção.\n"
                f"HISTORICO_CURTO_READ_ONLY:\n{historico_curto}\n"
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

                usar_agenda_final = bool(
                    (especialista_db and getattr(especialista_db, "usar_agenda", False)) or usar_agenda_meta
                )
                if usar_agenda_final and empresa_id:
                    tool_consulta_partial = partial(
                        consultar_horarios_livres.coroutine,
                        empresa_id=str(empresa_id),
                    )
                    tool_agendar_partial = partial(
                        agendar_horario.coroutine,
                        empresa_id=str(empresa_id),
                        lead_id=str(lead_id) if lead_id else "",
                    )

                    async def _tool_consultar_horarios_livres(data: str) -> str:
                        return await tool_consulta_partial(data=data)

                    async def _tool_agendar_horario(data_hora: str, cliente_nome: str) -> str:
                        return await tool_agendar_partial(data_hora=data_hora, cliente_nome=cliente_nome)

                    async def _tool_cancelar_agendamento(agendamento_id: str) -> str:
                        return await cancelar_agendamento.coroutine(agendamento_id=agendamento_id)

                    ferramentas_agenda = [
                        StructuredTool(
                            name="consultar_horarios_livres",
                            description=(
                                "Consulta a disponibilidade de horários livres em uma data específica "
                                "na agenda da empresa."
                            ),
                            args_schema=ConsultarHorariosLivresInput,
                            coroutine=_tool_consultar_horarios_livres,
                        ),
                        StructuredTool(
                            name="agendar_horario",
                            description="Realiza um agendamento para o lead atual na agenda da empresa.",
                            args_schema=AgendarHorarioInput,
                            coroutine=_tool_agendar_horario,
                        ),
                        StructuredTool(
                            name="cancelar_agendamento",
                            description="Cancela um agendamento existente com base no seu ID.",
                            args_schema=CancelarAgendamentoInput,
                            coroutine=_tool_cancelar_agendamento,
                        ),
                    ]
                    for tool_agenda in ferramentas_agenda:
                        nome_tool_agenda = str(getattr(tool_agenda, "name", "")).strip()
                        if not nome_tool_agenda or nome_tool_agenda in nomes_tools_registradas:
                            continue
                        tools_disponiveis.append(tool_agenda)
                        nomes_tools_registradas.add(nome_tool_agenda)
                    descricoes_tools.extend(
                        [
                            "- consultar_horarios_livres: consulta horários livres por data.",
                            "- agendar_horario: cria um compromisso na agenda local.",
                            "- cancelar_agendamento: cancela compromisso existente.",
                        ]
                    )
                    prompt_base += (
                        "\nVocê é um Especialista em Agendamentos. Sua prioridade é consultar disponibilidade "
                        "e converter a conversa em um compromisso marcado, utilizando as ferramentas de agenda fornecidas.\n"
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

                ferramentas_nativas = list(especialista_db.ferramentas if especialista_db else [])
                for f_db in ferramentas_nativas:
                    try:
                        schema_dict = f_db.schema_parametros if f_db.schema_parametros else {}
                        if isinstance(schema_dict, str):
                            schema_dict = json.loads(schema_dict)

                        args_schema = _create_pydantic_model_from_json_schema(
                            schema_dict,
                            model_name=f"{_tool_name_safe(f_db.nome_ferramenta)}Args",
                        )
                        nome_tool_original = str(getattr(f_db, "nome_ferramenta", "") or "").strip()
                        nome_tool_normalizado = _tool_name_safe(nome_tool_original)
                        chave_nativa = None
                        if nome_tool_original in MAP_FUNCOES_NATIVAS:
                            chave_nativa = nome_tool_original
                        elif nome_tool_normalizado in MAP_FUNCOES_NATIVAS:
                            chave_nativa = nome_tool_normalizado

                        tool_name = _tool_name_safe(chave_nativa or nome_tool_original)

                        if chave_nativa:
                            fn_nativa = MAP_FUNCOES_NATIVAS[chave_nativa]
                            if hasattr(fn_nativa, "coroutine") and callable(getattr(fn_nativa, "coroutine")):
                                coroutine_native = fn_nativa.coroutine
                            else:
                                coroutine_native = fn_nativa

                            # Ferramentas nativas que exigem contexto do lead/empresa
                            # recebem wrappers contextuais para o LLM enviar apenas
                            # os parâmetros de negócio.
                            if chave_nativa == "tool_aplicar_tag_dinamica":
                                async def _tool_aplicar_tag_dinamica_contextual(
                                    tag_id: str,
                                    _lead_id: str | None = str(lead_id) if lead_id else None,
                                    _empresa_id: str | None = str(state.get("empresa_id") or "").strip() or None,
                                    _coroutine_native=coroutine_native,
                                ) -> str:
                                    if not _lead_id or not _empresa_id:
                                        return "Falha ao aplicar tag dinâmica: contexto de lead/empresa ausente."
                                    return await _coroutine_native(
                                        lead_id=_lead_id,
                                        empresa_id=_empresa_id,
                                        tag_id=tag_id,
                                    )

                                coroutine_native = _tool_aplicar_tag_dinamica_contextual
                            elif chave_nativa == "tool_atualizar_tags_lead":
                                async def _tool_atualizar_tags_lead_contextual(
                                    tags: List[str],
                                    lead_id: Optional[str] = None,
                                    _lead_id: str | None = str(lead_id) if lead_id else None,
                                    _coroutine_native=coroutine_native,
                                ) -> str:
                                    lead_id_final = str(lead_id or _lead_id or "").strip()
                                    if not lead_id_final:
                                        return "Erro ao atualizar tags do lead: contexto de lead ausente."
                                    return await _coroutine_native(
                                        lead_id=lead_id_final,
                                        tags=tags,
                                    )

                                coroutine_native = _tool_atualizar_tags_lead_contextual
                            elif chave_nativa == "tool_consultar_tags_empresa":
                                async def _tool_consultar_tags_empresa_contextual(
                                    _empresa_id: str | None = str(state.get("empresa_id") or "").strip() or None,
                                    _coroutine_native=coroutine_native,
                                ) -> str:
                                    if not _empresa_id:
                                        return "Falha ao consultar tags: empresa não identificada."
                                    return await _coroutine_native(empresa_id=_empresa_id)

                                coroutine_native = _tool_consultar_tags_empresa_contextual
                            elif chave_nativa == "transferir_para_humano":
                                async def _tool_transferir_para_humano_contextual(
                                    *args,
                                    _telefone: str | None = str(state.get("identificador_origem") or "").strip() or None,
                                    _empresa_id: str | None = str(state.get("empresa_id") or "").strip() or None,
                                    _coroutine_native=coroutine_native,
                                    **kwargs
                                ) -> str:
                                    if not _telefone or not _empresa_id:
                                        return "Erro ao transferir: contexto ausente (telefone ou empresa_id não encontrados no state)."
                                    
                                    # Executa a função async original que faz o update real no banco de dados
                                    res = await _coroutine_native(telefone=_telefone, empresa_id=_empresa_id)
                                    
                                    # O gatilho "[SISTEMA_BOT_PAUSADO]" avisa a síntese do LangGraph para travar a fila
                                    return str(res) + " [SISTEMA_BOT_PAUSADO]"

                                coroutine_native = _tool_transferir_para_humano_contextual

                            nova_tool = StructuredTool(
                                name=tool_name,
                                description=f_db.descricao_ia,
                                args_schema=args_schema,
                                coroutine=coroutine_native,
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

            modelo_esp = ""
            if especialista_db and hasattr(especialista_db, "modelo_ia"):
                modelo_esp = str(getattr(especialista_db, "modelo_ia", "") or "").strip()
            if not modelo_esp:
                modelo_esp = str(meta_especialista.get("modelo_ia") or "").strip()
            modelo_esp = normalize_model_name(modelo_esp or "gpt-5.4")
            logger.info(
                "[NODE ESPECIALISTA DINAMICO] Especialista '%s' usando modelo='%s'",
                nome_especialista_resultado,
                modelo_esp,
            )
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
                from types import SimpleNamespace

                especialista = SimpleNamespace(prompt_sistema=prompt_completo)
                system_msg = SystemMessage(content=_prepend_resumo_cliente_system_prompt(state, especialista.prompt_sistema))
                mensagens = [
                    system_msg,
                    HumanMessage(content=ultima_mensagem),
                ]
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
                                _marcar_bot_pausado_se_necessario(state, conteudo_tool)
                                if "[SISTEMA_BOT_PAUSADO]" in str(conteudo_tool):
                                    # Força a parada imediata da fila de agentes e joga a mensagem direto pro usuário
                                    msg_transferencia = (
                                        str(conteudo_tool).split("EXATAMENTE: ")[-1]
                                        if "EXATAMENTE: " in str(conteudo_tool)
                                        else "Atendimento transferido para humano."
                                    )
                                    if not isinstance(state.get("mensagens"), list):
                                        state["mensagens"] = []
                                    state["mensagens"].append(AIMessage(content=msg_transferencia))
                                    state["acao_imediata"] = "RESPONDER_E_PARAR"
                                    state["fila_agentes"] = []
                                    state["agentes_na_fila"] = []
                                    return state
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

            nome_especialista_norm = _normalizar_chave_especialista(nome_especialista_resultado)
            fontes_norm = {_normalizar_chave_especialista(fonte) for fonte in fontes_unicas}
            usou_transferencia = any(
                chave in fontes_norm
                for chave in {
                    "tool_transferir_para_humano",
                    "action_transferir_atendimento",
                    "transferir_para_humano",
                }
            )
            usou_tags_intencao = any(
                chave in fontes_norm
                for chave in {
                    "tool_aplicar_tag_dinamica",
                    "tool_atualizar_tags_lead",
                }
            )

            # Isolamento de fila: se a triagem apenas classificou (tags) sem transferir,
            # encerra a fila do turno atual e aguarda a próxima resposta do usuário.
            if "triagem" in nome_especialista_norm and usou_tags_intencao and not usou_transferencia:
                logger.info(
                    "[QUEUE ISOLATION] Triagem classificou sem transferência; pausando fila atual."
                )
                state["fila_agentes"] = []
                state["especialistas_identificados"] = []
                state["especialistas_selecionados"] = []
                pendentes = [
                    str(acao).strip()
                    for acao in (state.get("acoes_sistema_pendentes") or [])
                    if str(acao).strip().lower() != "transferir_atendimento"
                ]
                state["acoes_sistema_pendentes"] = pendentes

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
        state["especialista_corrente"] = None
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
    if state.get("fluxo_encerrado"):
        return END
    if state.get("nome_contato") is None:
        return "capturar_nome"
    return "node_atendente"

# 3. Desenhar o Grafo
workflow = StateGraph(AgentState)

workflow.add_node("node_crm", node_crm)
workflow.add_node("node_capturar_nome", node_capturar_nome)
workflow.add_node("node_atendente", node_atendente)
workflow.add_node("node_roteador_maestro", node_roteador_maestro)
workflow.add_node("especialista_funcionamento", node_especialista_funcionamento)
workflow.add_node("especialista_localizacao", node_especialista_localizacao)
workflow.add_node("node_especialista_saudacao", node_especialista_saudacao)
workflow.add_node("node_acao_sistema", node_acao_sistema)
workflow.add_node("node_especialista_dinamico", node_especialista_dinamico)

workflow.set_entry_point("node_crm")

workflow.add_conditional_edges(
    "node_crm",
    router_crm,
    {
        END: END,
        "capturar_nome": "node_capturar_nome",
        "node_atendente": "node_atendente"
    }
)

workflow.add_edge("node_capturar_nome", END)

def router_atendente(state: AgentState):
    if state.get("resposta_final"):
        if state.get("acoes_sistema_pendentes"):
            return "node_acao_sistema"
        return END
    return "node_roteador_maestro"

workflow.add_conditional_edges(
    "node_atendente",
    router_atendente,
    {
        END: END,
        "node_roteador_maestro": "node_roteador_maestro",
        "node_acao_sistema": "node_acao_sistema",
    }
)

def router_maestro(state: AgentState):
    # Curto-circuito para parar a fila se houve transferência
    respostas = state.get("respostas_especialistas", [])
    if any("SISTEMA_BOT_PAUSADO" in str(r) or "AGUARDANDO_HUMANO" in str(r) for r in respostas):
        state["fila_agentes"] = []
        return "node_atendente"

    especialistas_identificados = state.get("especialistas_identificados") or []
    if not isinstance(especialistas_identificados, list):
        especialistas_identificados = [especialistas_identificados] if especialistas_identificados else []
    especialistas_selecionados = state.get("especialistas_selecionados") or []
    if not isinstance(especialistas_selecionados, list):
        especialistas_selecionados = []
    fila_agentes = state.get("fila_agentes") or []
    if not isinstance(fila_agentes, list):
        fila_agentes = []

    if not especialistas_identificados and especialistas_selecionados:
        sintetizados: list[str] = []
        vistos: set[str] = set()
        for item in especialistas_selecionados:
            if not isinstance(item, dict):
                continue
            for chave in (item.get("id"), item.get("nome")):
                valor = str(chave or "").strip()
                if not valor:
                    continue
                normalizado = _normalizar_chave_especialista(valor)
                if normalizado in vistos:
                    continue
                vistos.add(normalizado)
                sintetizados.append(valor)
        especialistas_identificados = sintetizados
        state["especialistas_identificados"] = sintetizados

    especialistas_norm = {_normalizar_chave_especialista(str(item or "")) for item in especialistas_identificados}
    pendentes = list(state.get("acoes_sistema_pendentes") or [])
    executadas = set(state.get("acoes_sistema_executadas") or [])

    if state.get("handoff_requested") and "transferir_atendimento" not in executadas and "transferir_atendimento" not in pendentes:
        pendentes.append("transferir_atendimento")
    if "tags_crm" in especialistas_norm and "aplicar_tags" not in executadas and "aplicar_tags" not in pendentes:
        pendentes.append("aplicar_tags")
    state["acoes_sistema_pendentes"] = pendentes

    if pendentes:
        somente_transferencia = all(
            str(acao or "").strip().lower() == "transferir_atendimento"
            for acao in pendentes
        )
        # Garante que a mensagem final de transferência seja gerada antes de pausar o bot.
        if somente_transferencia and not state.get("resposta_final"):
            return "node_atendente"
        state["especialista_corrente"] = None
        return "node_acao_sistema"
    if not fila_agentes:
        state["fila_agentes"] = []
        state["especialista_corrente"] = None
        return "node_atendente"

    agente_atual = str(fila_agentes.pop(0) or "").strip()
    state["fila_agentes"] = fila_agentes
    print("--- [CONTROLE DE FILA] ---")
    print(f"Agente atual sendo despachado: {agente_atual}")
    print(f"Agentes restantes na fila: {state['fila_agentes']}")
    print("--------------------------")
    if not agente_atual:
        state["especialista_corrente"] = None
        return "node_atendente"

    atual_norm = _normalizar_chave_especialista(agente_atual)
    metadado_atual = None
    for item in especialistas_selecionados:
        if not isinstance(item, dict):
            continue
        item_id = _normalizar_chave_especialista(str(item.get("id") or ""))
        item_nome = _normalizar_chave_especialista(str(item.get("nome") or ""))
        if atual_norm and atual_norm in {item_id, item_nome}:
            metadado_atual = item
            break

    nome_ref = _normalizar_chave_especialista(
        str((metadado_atual or {}).get("nome") or agente_atual)
    )

    if nome_ref == "especialista_saudacao":
        state["especialista_corrente"] = None
        return "node_especialista_saudacao"
    if nome_ref == "especialista_funcionamento":
        state["especialista_corrente"] = None
        return "especialista_funcionamento"
    if nome_ref == "especialista_localizacao":
        state["especialista_corrente"] = None
        return "especialista_localizacao"

    # Processa apenas o especialista atual neste passo da fila.
    if isinstance(metadado_atual, dict):
        state["especialista_corrente"] = metadado_atual
    else:
        state["especialista_corrente"] = {
            "id": agente_atual,
            "nome": agente_atual,
            "prompt_sistema": "",
            "usar_rag": False,
        }
    return "node_especialista_dinamico"


workflow.add_conditional_edges(
    "node_roteador_maestro",
    router_maestro,
    {
        "node_atendente": "node_atendente",
        "especialista_funcionamento": "especialista_funcionamento",
        "especialista_localizacao": "especialista_localizacao",
        "node_especialista_saudacao": "node_especialista_saudacao",
        "node_acao_sistema": "node_acao_sistema",
        "node_especialista_dinamico": "node_especialista_dinamico",
    }
)

workflow.add_conditional_edges("especialista_funcionamento", router_maestro)
workflow.add_conditional_edges("especialista_localizacao", router_maestro)
workflow.add_conditional_edges("node_especialista_saudacao", router_maestro)
workflow.add_conditional_edges("node_especialista_dinamico", router_maestro)

def router_pos_acao_sistema(state: AgentState):
    if state.get("bot_foi_pausado") and not state.get("resposta_final"):
        return "node_atendente"
    if state.get("bot_foi_pausado"):
        return END
    if state.get("resposta_final"):
        return "node_atendente"
    return "node_roteador_maestro"


workflow.add_conditional_edges(
    "node_acao_sistema",
    router_pos_acao_sistema,
    {
        END: END,
        "node_atendente": "node_atendente",
        "node_roteador_maestro": "node_roteador_maestro",
    }
)
# Compilar sem checkpointer persistente:
# a memória global da conversa vem exclusivamente do histórico consolidado no estado.
graph = workflow.compile()

async def _buscar_historico_lead_para_followup(canal: str, identificador_origem: str, empresa_id: str, limite: int = 15) -> tuple[str, str | None]:
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
