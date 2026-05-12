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
from openai import AuthenticationError, RateLimitError

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

from langchain_core.messages import AIMessage, HumanMessage

async def get_llm(empresa_id: str | None = None, modelo_ia: str | None = None) -> Any:
    from app.core.llm_factory import get_llm_for_tenant

    if not empresa_id:
        raise ValueError("empresa_id é obrigatório para instanciar LLM em modo BYOK.")

    modelo_ia = str(modelo_ia or "").strip() or "gpt-4o-mini"
    async with AsyncSessionLocal() as session:
        logger.info("[LLM] Instanciando modelo='%s' (empresa_id=%s)", modelo_ia, empresa_id)
        return await get_llm_for_tenant(str(empresa_id), session, modelo_ia)

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
    TagCRM,
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
from app.services.tag_crm_service import listar_tags_crm_para_prompt
from app.core.funnel_router import (
    parse_funil_routing_credenciais,
    resolver_especialista_top1_por_funil,
    normalizar_uuid_str,
)
from app.api.utils import is_ai_blocked
from app.core.default_agents import ESPECIALISTAS_NATIVOS
from app.core.llm_factory import normalize_model_name, handle_openai_runtime_exception, mark_openai_status_ok
from app.core.tools import (
    tool_atualizar_nome_lead,
    tool_atualizar_tags_lead,
    tool_aplicar_tag_dinamica,
    tool_adicionar_tag_lead,
    tool_consultar_tags_empresa,
    tool_listar_etapas_funil,
    tool_atualizar_etapa_lead,
    atualizar_etapa_lead_core,
)
from langchain_core.tools import StructuredTool, tool
from langgraph.prebuilt import ToolNode
import httpx

logger = logging.getLogger(__name__)


async def _ainvoke_with_openai_guard(llm: Any, payload: Any, empresa_id: str | None):
    try:
        response = await llm.ainvoke(payload)
        await mark_openai_status_ok(str(empresa_id or ""))
        return response
    except (AuthenticationError, RateLimitError) as exc:
        await handle_openai_runtime_exception(str(empresa_id or ""), exc)
        raise


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


def _eh_especialista_porteiro(nome: str | None) -> bool:
    """
    Identifica o agente nativo de "porteiro" (saudação) por convenção interna
    do seed em default_agents.py — NÃO é regra de tag de cliente. Existe um
    único papel de porteiro no sistema: especialista_saudacao.
    """
    return _normalizar_chave_especialista(str(nome or "")) == "especialista_saudacao"


def _lead_precisa_saudacao_inicial(state: "AgentState") -> bool:
    """
    Decide universalmente se o especialista de saudação deve compor a fila
    deste turno, baseado APENAS em sinais do CRM/DB (sem hardcode de tags):

      • Primeiro contato (sem histórico de mensagens), OU
      • Nome do contato vazio / placeholder gerado pelo sistema / apenas dígitos.

    Se o lead já passou da triagem inicial (tem nome e já trocou mensagens),
    o porteiro é retirado da lista de especialistas disponíveis.
    """
    if _is_primeiro_contato(state):
        return True

    nome = str(state.get("nome_contato") or "").strip()
    if not nome:
        return True
    if nome.lower() in {"usuário (auto)", "usuario (auto)"}:
        return True
    if re.fullmatch(r"[\d\+\-\(\)\s]+", nome):
        return True
    return False


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


def _prepend_rota_interna_system_prompt(state: AgentState, prompt: str) -> str:
    """
    System prompt para nós de roteamento (Condutor / Maestro): não injeta as
    diretrizes globais de ferramentas do atendente, para não misturar protocolo
    conversacional com saída estruturada de rota.
    """
    resumo = str(state.get("resumo_cliente") or "").strip()
    if resumo:
        return f"CONTEXTO_INTERNO_RESUMO_CLIENTE:\n{resumo}\n\n{prompt}"
    return prompt


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


def _montar_human_message_multimodal(texto: str | None, tipo_mensagem: str | None, media_url: str | None) -> HumanMessage:
    tipo = str(tipo_mensagem or "text").strip().lower()
    media = str(media_url or "").strip()
    texto_base = str(texto or "").strip()

    if tipo == "video":
        legenda = texto_base if texto_base else "(sem legenda)"
        return HumanMessage(content=f"[Vídeo recebido do cliente] Legenda: {legenda}")

    if tipo == "image" and media:
        media_final = media
        mlow = media_final.lower()
        if media_final.startswith("http://") or media_final.startswith("https://"):
            pass
        elif mlow.startswith("data:image/"):
            pass
        elif media_final.startswith("data:"):
            pass
        else:
            media_final = f"data:image/jpeg;base64,{media_final}"
        texto_complementar = texto_base if texto_base else "O utilizador enviou uma imagem anexa."
        conteudo = [
            {"type": "text", "text": texto_complementar},
            {"type": "image_url", "image_url": {"url": media_final}},
        ]
        return HumanMessage(content=conteudo)
    return HumanMessage(content=texto_base)


def _to_chat_messages(mensagens: list[Any]) -> list[Any]:
    saida: list[Any] = []
    for item in mensagens:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("papel") or "").strip().lower()
            texto_dict = str(item.get("text") or item.get("texto") or item.get("content") or "").strip()
            if not texto_dict:
                continue
            if role in {"assistant", "assistente", "ai"}:
                saida.append(AIMessage(content=texto_dict))
            else:
                saida.append(
                    _montar_human_message_multimodal(
                        texto_dict,
                        str(item.get("tipo_mensagem") or "text"),
                        str(item.get("media_url") or ""),
                    )
                )
            continue

        texto = str(item or "").strip()
        if not texto:
            continue
        if texto.lower().startswith("assistente:"):
            saida.append(AIMessage(content=_strip_role_prefix(texto)))
        else:
            saida.append(HumanMessage(content=_strip_role_prefix(texto)))
    return saida


async def _obter_ultima_mensagem_inbound_multimodal(
    empresa_id: Any,
    identificador_origem: str | None,
    fallback_texto: str | None,
) -> HumanMessage:
    texto_fallback = str(fallback_texto or "").strip()
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
        telefone = str(identificador_origem or "").strip()
        if not telefone:
            return HumanMessage(content=texto_fallback)

        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone,
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                return HumanMessage(content=texto_fallback)

            result_msg = await session.execute(
                select(MensagemHistorico)
                .where(
                    MensagemHistorico.lead_id == lead.id,
                    MensagemHistorico.from_me.is_(False),
                )
                .order_by(MensagemHistorico.criado_em.desc())
                .limit(1)
            )
            ultima = result_msg.scalars().first()
            if not ultima:
                return HumanMessage(content=texto_fallback)

            return _montar_human_message_multimodal(
                str(getattr(ultima, "texto", "") or ""),
                str(getattr(ultima, "tipo_mensagem", "text") or "text"),
                str(getattr(ultima, "media_url", "") or ""),
            )
    except Exception as exc:
        logger.debug("[MULTIMODAL] Fallback para texto simples na última mensagem: %s", exc)
        return HumanMessage(content=texto_fallback)

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
    """Compatível com ferramentas antigas: resolve empresa pelo lead e valida etapa no tenant."""
    try:
        lead_uuid = uuid.UUID(str(lead_id).strip())
    except (ValueError, TypeError):
        return "Erro ao atualizar etapa do CRM: lead_id inválido."
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(CRMLead).where(CRMLead.id == lead_uuid))
            lead = result.scalars().first()
            if not lead:
                return "Erro ao atualizar etapa do CRM: lead não encontrado."
            empresa_id_str = str(lead.empresa_id)
        return await atualizar_etapa_lead_core(lead_id=lead_id, empresa_id=empresa_id_str, etapa_id=nova_etapa_id)
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

                from app.services.crm_etapas_service import obter_ou_criar_etapa_por_tipo

                etapa_handoff_id = await obter_ou_criar_etapa_por_tipo(session, empresa_uuid, "handoff")
                if etapa_handoff_id:
                    lead.etapa_id = etapa_handoff_id

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

    mensagem_usuario_atual = await _obter_ultima_mensagem_inbound_multimodal(
        empresa_id=empresa_id,
        identificador_origem=str(state.get("identificador_origem") or ""),
        fallback_texto=ultima_mensagem,
    )
    mensagens = [
        SystemMessage(content=_prepend_resumo_cliente_system_prompt(state, prompt)),
        mensagem_usuario_atual,
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


async def node_handoff(state: AgentState):
    """Micro-agente interno para saída contextual de transbordo."""
    if not state.get("bot_foi_pausado"):
        return state
    if state.get("resposta_final"):
        return state

    contexto_historico = str(state.get("historico_bd") or "")
    contexto_curto = str(state.get("historico_curto") or "")
    contexto = f"{contexto_historico}\n{contexto_curto}".strip()
    contexto_lower = contexto.lower()

    if state.get("ia_bloqueada_entrada"):
        return state

    humano_ja_assumiu = any(
        marcador in contexto_lower
        for marcador in ("atendente:", "humano:", "equipe:", "suporte:")
    )
    if humano_ja_assumiu:
        return state

    prompt_handoff = str(
        ESPECIALISTAS_NATIVOS.get("especialista_handoff_interno", {}).get("prompt_sistema")
        or ""
    ).strip() or "Você é um agente interno de transbordo. Responda com uma frase curta de transferência para humano."

    ultima_msg_cliente = _ultima_mensagem_cliente(state)
    try:
        llm = await get_llm(state.get("empresa_id"))
        resposta = await _ainvoke_with_openai_guard(
            llm,
            [
                ("system", _prepend_resumo_cliente_system_prompt(state, prompt_handoff)),
                (
                    "user",
                    "Gere UMA frase curta de transbordo para humano, em português, sem detalhes técnicos.\n"
                    f"Última mensagem do cliente: {ultima_msg_cliente}\n"
                    f"Histórico recente:\n{contexto[-1800:] if contexto else '(sem histórico)'}",
                ),
            ],
            state.get("empresa_id"),
        )
        texto = str(getattr(resposta, "content", "") or "").strip()
    except Exception:
        texto = ""

    if not texto:
        texto = "Entendido. Vou transferir você para a nossa equipe agora."
    if len(texto) > 260:
        texto = texto[:257].rstrip() + "..."

    state["resposta_final"] = texto
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
    "tool_atualizar_etapa_lead": tool_atualizar_etapa_lead.coroutine,
    "tool_listar_etapas_funil": tool_listar_etapas_funil.coroutine,
    "consultar_agenda": consultar_agenda,
    "transferir_para_humano": transferir_para_humano,  # ADICIONE ESTA LINHA
    "tool_transferir_para_humano": transferir_para_humano,
    "tool_atualizar_nome_lead": tool_atualizar_nome_lead,
    "tool_atualizar_tags_lead": tool_atualizar_tags_lead,
    "tool_aplicar_tag_dinamica": tool_aplicar_tag_dinamica.coroutine,
    "tool_adicionar_tag_lead": tool_adicionar_tag_lead.coroutine,
    "tool_consultar_tags_empresa": tool_consultar_tags_empresa.coroutine,
}


@tool
async def google_search(query: str) -> str:
    """
    Busca informações públicas recentes na web para apoiar respostas contextuais.
    Retorna até 5 resultados em texto curto (título, URL e resumo).
    """
    termo = str(query or "").strip()
    if not termo:
        return "Busca não executada: consulta vazia."
    endpoint = "https://duckduckgo.com/?q=" + httpx.QueryParams({"q": termo}).get("q", termo) + "&format=json&pretty=1"
    api = "https://api.duckduckgo.com/"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=True) as client:
            response = await client.get(
                api,
                params={
                    "q": termo,
                    "format": "json",
                    "no_html": 1,
                    "no_redirect": 1,
                },
            )
        if response.status_code != 200:
            return f"Falha na busca web (status {response.status_code})."
        data = response.json() if response.content else {}
        resultados: list[str] = []
        abstrato = str(data.get("AbstractText") or "").strip()
        abstrato_url = str(data.get("AbstractURL") or "").strip()
        heading = str(data.get("Heading") or "").strip()
        if abstrato:
            resultados.append(f"1) {heading or 'Resultado'} | {abstrato_url or endpoint}\n{abstrato}")
        relacionados = data.get("RelatedTopics") or []
        for item in relacionados:
            if len(resultados) >= 5:
                break
            if isinstance(item, dict) and isinstance(item.get("Topics"), list):
                for sub in item.get("Topics") or []:
                    if len(resultados) >= 5:
                        break
                    txt = str(sub.get("Text") or "").strip()
                    url = str(sub.get("FirstURL") or "").strip()
                    if txt:
                        resultados.append(f"{len(resultados)+1}) {url or endpoint}\n{txt}")
                continue
            txt = str(item.get("Text") or "").strip() if isinstance(item, dict) else ""
            url = str(item.get("FirstURL") or "").strip() if isinstance(item, dict) else ""
            if txt:
                resultados.append(f"{len(resultados)+1}) {url or endpoint}\n{txt}")
        if not resultados:
            return "Busca web executada, mas sem resultados úteis no momento."
        return "Resultados da busca web:\n" + "\n\n".join(resultados[:5])
    except Exception as exc:
        return f"Falha ao executar busca web: {exc}"


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

    from app.services.crm_etapas_service import resolver_etapa_fechamento_id

    async with AsyncSessionLocal() as session:
        etapa_fechamento_id = await resolver_etapa_fechamento_id(session, empresa_uuid)
        if not etapa_fechamento_id:
            return

        await session.execute(
            update(CRMLead)
            .where(CRMLead.id == lead_uuid, CRMLead.empresa_id == empresa_uuid)
            .values(etapa_id=etapa_fechamento_id)
        )
        await session.commit()

async def buscar_conhecimento(pergunta: str, empresa_uuid):
    print(f"[RAG] Buscando conhecimento para a pergunta: '{pergunta}' na empresa {empresa_uuid}")
    try:
        async with AsyncSessionLocal() as session:
            from app.core.llm_factory import get_embeddings_for_tenant

            embeddings_model = await get_embeddings_for_tenant(str(empresa_uuid), session)
            pergunta_embedding = await embeddings_model.aembed_query(pergunta)
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
    etapa_funil: Optional[str]
    etapas_concluidas: List[str]
    objetivo_atual: Optional[str]
    proxima_acao: Optional[str]
    termos_busca_condutor: Optional[List[str]]
    search_terms_condutor: Optional[List[str]]
    ia_bloqueada_entrada: Optional[bool]

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
            if is_ai_blocked(lead):
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
                state["ia_bloqueada_entrada"] = True
                state["bot_foi_pausado"] = True
                state["handoff_requested"] = True
                state["fluxo_encerrado"] = False
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
            print(f"[NODE CRM] Lead não encontrado. Iniciando criação automática via lead_service...")

            nome_recebido = str(state.get("nome_contato") or "").strip()
            possivel_nome = nome_recebido or "Usuário (Auto)"

            try:
                # Garante que existe um funil padrão antes de tentar criar o lead
                # (a helper get_or_create_lead resolve a etapa, mas o funil padrão
                # precisa existir caso a empresa ainda nunca tenha aberto o CRM).
                result_funil = await session.execute(
                    select(CRMFunil)
                    .where(CRMFunil.empresa_id == empresa_id)
                    .options(selectinload(CRMFunil.etapas))
                )
                funil = result_funil.scalars().first()
                if not funil:
                    novo_funil = CRMFunil(empresa_id=empresa_id, nome="Pipeline Padrão")
                    session.add(novo_funil)
                    await session.flush()
                    session.add(CRMEtapa(funil_id=novo_funil.id, nome="Entrada", tipo="entrada", ordem=1))
                    session.add(CRMEtapa(funil_id=novo_funil.id, nome="Atendimento", tipo="atendimento", ordem=2))
                    session.add(CRMEtapa(funil_id=novo_funil.id, nome="Encerramento", tipo="fechamento", ordem=3))
                    await session.flush()

                # UPSERT centralizado (advisory lock + sanitização de telefone)
                from app.services.lead_service import get_or_create_lead

                import uuid

                try:
                    conexao_uuid = uuid.UUID(str(state.get("conexao_id"))) if state.get("conexao_id") else None
                except (ValueError, TypeError):
                    conexao_uuid = None

                if possivel_nome:
                    novo_lead, foi_criado = await get_or_create_lead(
                        session,
                        empresa_id=empresa_id,
                        telefone=origem,
                        nome_inicial=possivel_nome,
                        historico_resumo_inicial="Lead capturado automaticamente via integração.",
                    )

                    if not foi_criado:
                        # Outro caminho (ex.: webhook.save_history_and_check_pause)
                        # já criou o lead antes da gente. Apenas usamos.
                        print(
                            f"[NODE CRM] Lead encontrado durante UPSERT concorrente. ID: {novo_lead.id}"
                        )

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
                    if foi_criado:
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
    resposta = await _ainvoke_with_openai_guard(
        llm,
        [("system", _prepend_resumo_cliente_system_prompt(state, prompt)), ("user", ultima_mensagem)],
        state.get("empresa_id"),
    )
    state["resposta_final"] = resposta.content

    return state

async def node_encerrar_resposta(state: AgentState):
    """
    Finalização determinística do turno: não sintetiza texto com LLM.
    A mensagem ao cliente vem do especialista (resposta_final) ou de fallbacks curtos.
    """
    print("[NODE ENCERRAR RESPOSTA] Normalizando estado pós-especialista (sem síntese LLM)...")

    respostas_antes = state.get("respostas_especialistas") or []
    if any("SISTEMA_BOT_PAUSADO" in str(r) for r in respostas_antes):
        state["handoff_requested"] = True
        state["bot_foi_pausado"] = True

    if not str(state.get("resposta_final") or "").strip():
        for bloco in reversed(respostas_antes):
            texto = str(bloco or "")
            if "SISTEMA_BOT_PAUSADO" in texto or "AGUARDANDO_HUMANO" in texto:
                state["resposta_final"] = (
                    "Perfeito! Vou te transferir agora para o time responsável. "
                    "Um atendente humano continua com você em instantes."
                )
                break
        if not str(state.get("resposta_final") or "").strip() and state.get("handoff_requested"):
            state["resposta_final"] = (
                "Perfeito! Vou te transferir agora para o time responsável. "
                "Um atendente humano continua com você em instantes."
            )
        if not str(state.get("resposta_final") or "").strip():
            state["resposta_final"] = (
                "No momento não consegui concluir a resposta automática. "
                "Poderia reformular sua mensagem em poucas palavras?"
            )

    status_conv = "HANDOFF" if state.get("handoff_requested") else "ABERTA"
    state["status_conversa"] = status_conv

    pendentes = list(state.get("acoes_sistema_pendentes") or [])
    executadas = list(state.get("acoes_sistema_executadas") or [])
    if status_conv == "HANDOFF" and "transferir_atendimento" not in pendentes and "transferir_atendimento" not in executadas:
        pendentes.append("transferir_atendimento")
    state["acoes_sistema_pendentes"] = pendentes

    state["especialistas_identificados"] = []
    state["respostas_especialistas"] = []
    state["especialistas_selecionados"] = []
    state["fila_agentes"] = []
    state["super_contexto_especialistas"] = ""
    state["roteamento_tentado"] = False
    return state


async def _sync_etapa_funil_state_from_crm(state: AgentState) -> None:
    """
    Sincroniza `etapa_funil` e metadados de CRM a partir do banco (sem LLM).
    Invocado no Maestro no início do ciclo de roteamento (antes do Top-1).
    """
    empresa_id = str(state.get("empresa_id") or "").strip()
    lead_id = str(state.get("lead_id") or "").strip()
    if not empresa_id:
        return

    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        return

    async with AsyncSessionLocal() as session:
        result_empresa = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
        empresa = result_empresa.scalars().first()
        if not empresa:
            return

        lead_obj: CRMLead | None = None
        etapa_atual_por_tag: str | None = None

        if lead_id:
            try:
                lead_uuid = uuid.UUID(lead_id)
            except (ValueError, TypeError):
                lead_uuid = None
            if lead_uuid:
                result_lead = await session.execute(
                    select(CRMLead).where(
                        CRMLead.id == lead_uuid,
                        CRMLead.empresa_id == empresa_uuid,
                    )
                )
                lead_obj = result_lead.scalars().first()
                if lead_obj:
                    tags_ids = [
                        str(item).strip()
                        for item in (lead_obj.tags if isinstance(lead_obj.tags, list) else [])
                        if str(item).strip()
                    ]
                    tags_uuid = []
                    for item in tags_ids:
                        try:
                            tags_uuid.append(uuid.UUID(item))
                        except (ValueError, TypeError):
                            continue
                    if tags_uuid:
                        result_tags = await session.execute(
                            select(TagCRM).where(
                                TagCRM.empresa_id == empresa_uuid,
                                TagCRM.id.in_(tags_uuid),
                            )
                        )
                        for tag in result_tags.scalars().all():
                            nome_tag = str(getattr(tag, "nome", "") or "").strip()
                            tipo_tag = str(getattr(tag, "tipo", "") or "").strip().lower()
                            if not nome_tag:
                                continue
                            if tipo_tag == "etapa_funil":
                                if etapa_atual_por_tag is None:
                                    etapa_atual_por_tag = nome_tag

        etapa_lead_nome: str | None = None
        if lead_obj is not None and getattr(lead_obj, "etapa_id", None):
            result_etapa = await session.execute(
                select(CRMEtapa.nome).where(CRMEtapa.id == lead_obj.etapa_id)
            )
            row = result_etapa.first()
            if row:
                etapa_lead_nome = str(row[0] or "").strip() or None

        etapa_oficial_nome = etapa_lead_nome or etapa_atual_por_tag

        state["termos_busca_condutor"] = []
        state["search_terms_condutor"] = []
        state["objetivo_atual"] = None
        state["proxima_acao"] = None

        if etapa_oficial_nome:
            etapa_anterior = str(state.get("etapa_funil") or "").strip() or None
            if etapa_anterior and etapa_anterior != etapa_oficial_nome:
                concluidas = list(state.get("etapas_concluidas") or [])
                if etapa_anterior not in concluidas:
                    concluidas.append(etapa_anterior)
                    state["etapas_concluidas"] = concluidas
            state["etapa_funil"] = etapa_oficial_nome


async def node_roteador_maestro(state: AgentState):
    print("[NODE ROTEADOR] Roteamento determinístico por funil (Top-1, sem vetor/LLM)...")
    import uuid

    # Se já existem contribuições no ciclo, o roteador atua apenas como controlador da fila.
    respostas_no_ciclo = state.get("respostas_especialistas") or []
    if isinstance(respostas_no_ciclo, list) and respostas_no_ciclo:
        return state

    await _sync_etapa_funil_state_from_crm(state)

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

    # ── Decisão universal: o porteiro entra apenas se o lead ainda está em
    # estado inicial (sem histórico OU sem nome real). É o ÚNICO ponto onde
    # ele é injetado na fila — em qualquer outro turno ele será filtrado dos
    # candidatos do roteador semântico abaixo.
    saudacao_inicial_necessaria = _lead_precisa_saudacao_inicial(state)
    if saudacao_inicial_necessaria:
        print(
            "[NODE ROTEADOR] Lead em estado inicial (sem histórico ou sem nome). "
            "Convocando porteiro de saudação."
        )
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
                    if especialistas_ativos:
                        especialista_escolhido = next(
                            (esp for esp in especialistas_ativos if _eh_especialista_porteiro(getattr(esp, "nome", ""))),
                            None,
                        )
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

    ultima_ia = _ultima_mensagem_assistente(state).lower()
    is_short_confirm = msg_lower in ["sim", "quero", "pode", "por favor", "pode transferir", "sim por favor"]

    if is_short_confirm and any(m in ultima_ia for m in ["transferir", "humano", "atendente", "especialista"]):
        is_handoff = True

    state["handoff_requested"] = is_handoff

    if is_handoff and is_short_confirm:
        state["especialistas_identificados"] = []
        state["especialistas_selecionados"] = []
        state["fila_agentes"] = []
        state["especialista_corrente"] = None
        state["respostas_especialistas"] = []
        state["resposta_final"] = (
            "Perfeito! Vou te transferir agora para o time responsável. "
            "Um atendente humano continua com você em instantes."
        )
        return state

    async with AsyncSessionLocal() as session:
        result_emp = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
        empresa_row = result_emp.scalars().first()
        cred = getattr(empresa_row, "credenciais_canais", None) or {}
        regras, default_esp_id, match_order = parse_funil_routing_credenciais(cred if isinstance(cred, dict) else {})

        etapa_uuid_lead = None
        tags_lead_list: list[Any] = []
        lead_pk = str(state.get("lead_id") or "").strip()
        if lead_pk:
            try:
                lead_uuid = uuid.UUID(lead_pk)
            except (ValueError, TypeError):
                lead_uuid = None
            if lead_uuid:
                result_lead = await session.execute(
                    select(CRMLead).where(
                        CRMLead.id == lead_uuid,
                        CRMLead.empresa_id == empresa_uuid,
                    )
                )
                lead_obj = result_lead.scalars().first()
                if lead_obj:
                    etapa_uuid_lead = getattr(lead_obj, "etapa_id", None)
                    raw_tags = lead_obj.tags
                    tags_lead_list = raw_tags if isinstance(raw_tags, list) else []

        result_esp = await session.execute(
            select(Especialista).where(
                Especialista.empresa_id == empresa_uuid,
                Especialista.ativo.is_(True),
            )
        )
        especialistas_db = list(result_esp.scalars().all())
        ids_ativos_list: list[str] = []
        for e in especialistas_db:
            nid = normalizar_uuid_str(getattr(e, "id", None))
            if nid:
                ids_ativos_list.append(nid)
        ids_ativos: frozenset[str] = frozenset(ids_ativos_list)
        fallback_pool: list[tuple[str, int]] = []
        for e in especialistas_db:
            nome = str(getattr(e, "nome", "") or "")
            if _normalizar_chave_especialista(nome) == "especialista_handoff_interno":
                continue
            if _eh_especialista_porteiro(nome):
                continue
            eid = normalizar_uuid_str(getattr(e, "id", None))
            if eid:
                fallback_pool.append((eid, int(getattr(e, "peso_prioridade", 1) or 1)))

        escolhido = resolver_especialista_top1_por_funil(
            etapa_uuid_lead,
            tags_lead_list,
            regras,
            default_esp_id,
            match_order,
            ids_ativos,
            fallback_pool,
        )

        especialistas_match: list[dict[str, Any]] = []
        if escolhido:
            esp_row = next(
                (row for row in especialistas_db if normalizar_uuid_str(getattr(row, "id", None)) == escolhido),
                None,
            )
            if esp_row:
                especialistas_match = [
                    {
                        "id": str(esp_row.id),
                        "nome": str(esp_row.nome or ""),
                        "modelo_ia": str(
                            getattr(esp_row, "modelo_llm", "") or getattr(esp_row, "modelo_ia", "") or ""
                        ).strip(),
                        "peso_prioridade": int(getattr(esp_row, "peso_prioridade", 1) or 1),
                        "descricao_missao": str(getattr(esp_row, "descricao_missao", "") or "").strip(),
                        "prompt_sistema": str(esp_row.prompt_sistema or ""),
                        "usar_rag": bool(getattr(esp_row, "usar_rag", False)),
                        "usar_agenda": bool(getattr(esp_row, "usar_agenda", False)),
                    }
                ]
                logger.info("[MAESTRO] Roteamento determinístico Top-1: especialista_id=%s nome=%s", escolhido, esp_row.nome)
            else:
                logger.warning(
                    "[MAESTRO] UUID resolvido (%s) não encontrado entre especialistas ativos; ignorando.",
                    escolhido,
                )
        else:
            logger.warning(
                "[MAESTRO] Nenhum especialista resolvido (configure credenciais_canais.funnel_routing). empresa_id=%s",
                empresa_uuid,
            )

        especialistas_identificados: list[str] = []
        if especialistas_match and especialistas_match[0].get("id"):
            especialistas_identificados = [str(especialistas_match[0]["id"])]

        respostas_existentes = state.get("respostas_especialistas") or []
        if not isinstance(respostas_existentes, list):
            respostas_existentes = []

        state["especialistas_selecionados"] = especialistas_match
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
        resposta = await _ainvoke_with_openai_guard(
            llm,
            [("system", _prepend_resumo_cliente_system_prompt(state, prompt_sistema)), ("user", ultima_mensagem)],
            empresa_id,
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
        resposta = await _ainvoke_with_openai_guard(
            llm,
            [("system", _prepend_resumo_cliente_system_prompt(state, prompt_sistema)), ("user", ultima_mensagem)],
            empresa_id,
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

    from app.services.lead_service import classificar_nome_contato

    empresa_id = state.get("empresa_id")
    ultima_mensagem = _ultima_mensagem_cliente(state)
    historico_global = str(state.get("historico_bd") or "").strip() or "(sem histórico global)"

    saudacao_base_empresa = ""
    prompt_painel = ""
    nome_sistema = ""
    lead_id_state = str(state.get("lead_id") or "").strip()
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

                # Recupera o nome JÁ NORMALIZADO/CAPTURADO pelo sistema. Damos
                # prioridade à BD (fonte de verdade do CRM) e caímos no state
                # apenas se o lead ainda não tiver sido criado.
                if lead_id_state:
                    try:
                        lead_uuid = uuid.UUID(lead_id_state)
                        res_lead = await session.execute(
                            select(CRMLead).where(CRMLead.id == lead_uuid)
                        )
                        lead_atual = res_lead.scalars().first()
                        if lead_atual:
                            nome_sistema = str(lead_atual.nome_contato or "").strip()
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            logger.error("[NODE ESPECIALISTA SAUDACAO] Falha ao carregar empresa %s: %s", empresa_id, e)

    if not nome_sistema:
        nome_sistema = str(state.get("nome_contato") or "").strip()

    tipo_nome = classificar_nome_contato(nome_sistema)
    print(
        f"[NODE ESPECIALISTA SAUDACAO] nome_sistema='{nome_sistema}' "
        f"tipo_nome='{tipo_nome}'"
    )

    hora_atual = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%H:%M")

    # Validação do momento da conversa
    primeiro_contato = _is_primeiro_contato(state)

    if primeiro_contato:
        # Injeção explícita do texto real (evita rótulos tipo VARIAVEL que o modelo ecoa literalmente).
        if saudacao_base_empresa:
            bloco_conteudo_saudacao = (
                "Conteúdo da Saudação Oficial (texto exato cadastrado no painel da empresa; JSON string único abaixo):\n"
                f"{json.dumps(saudacao_base_empresa, ensure_ascii=False)}\n\n"
            )
            regra_mensagem_base = (
                bloco_conteudo_saudacao
                + "REGRA CRÍTICA 1 (SAUDAÇÃO): Comece sua mensagem ao cliente reproduzindo literalmente o texto "
                "definido acima em «Conteúdo da Saudação Oficial» (o valor JSON entre aspas). "
                "Não parafraseie, não resuma e não prefixe com frases como 'a mensagem oficial é'. "
                "Se houver menu numérico, mantenha a estrutura exata.\n"
            )
        else:
            regra_mensagem_base = (
                "Conteúdo da Saudação Oficial: (vazio — não há texto cadastrado no painel para esta empresa.)\n\n"
                "REGRA CRÍTICA 1 (SAUDAÇÃO): Como não há saudação oficial cadastrada, abra com uma saudação curta e cordial "
                "adequada ao tom da empresa; em seguida siga as demais regras deste prompt.\n"
            )
    else:
        regra_mensagem_base = (
            "REGRA CRÍTICA 1 (CONVERSA EM ANDAMENTO): O cliente enviou uma saudação ou mensagem curta no MEIO de uma conversa em andamento.\n"
            "NÃO reenvie a mensagem de boas-vindas padrão nem o menu inicial. Responda de forma natural, curta e empática, baseando-se no contexto das mensagens anteriores.\n"
        )

    # ── Regras hierárquicas de identidade (PF vs PJ vs indeterminado) ────────
    if tipo_nome == "pessoa_fisica":
        regra_identidade = (
            "REGRA CRÍTICA 2 (IDENTIDADE — PESSOA FÍSICA):\n"
            f"O sistema já identificou que o nome do contato é \"{nome_sistema}\" e é PROVAVELMENTE um nome de pessoa real.\n"
            "Você NÃO deve perguntar o nome. Cumprimente o cliente pelo primeiro nome de forma natural.\n"
            "Em seguida faça UMA pergunta curta para descobrir o que ele precisa, para o roteador encaminhar ao especialista certo.\n"
            "Se a empresa tiver alguma tag de triagem cadastrada em `tool_consultar_tags_empresa` cuja `instrucao_ia` indique uso ao concluir a triagem inicial, você pode aplicá-la com `tool_aplicar_tag_dinamica` (consultando o tag_id real; jamais invente IDs).\n"
        )
    elif tipo_nome == "pessoa_juridica":
        regra_identidade = (
            "REGRA CRÍTICA 2 (IDENTIDADE — NOME PARECE EMPRESA):\n"
            f"O sistema identificou que o contato está cadastrado como \"{nome_sistema}\", mas isso parece ser o nome de uma EMPRESA, não de uma pessoa.\n"
            "Pergunte de forma cordial: \"Com quem eu falo?\" (ou variação natural).\n"
            "Assim que o cliente confirmar o nome dele, chame OBRIGATORIAMENTE a ferramenta `tool_atualizar_nome_lead` para atualizar o CRM com o nome da pessoa física.\n"
            "Não conclua a triagem antes de confirmar a identidade.\n"
        )
    else:
        regra_identidade = (
            "REGRA CRÍTICA 2 (IDENTIDADE — INDETERMINADO):\n"
            f"O sistema não conseguiu identificar com clareza o nome do contato (valor atual: \"{nome_sistema or '(vazio)'}\").\n"
            "Pergunte de forma educada e curta: \"Como prefere ser chamado(a)?\" ou \"Com quem eu falo?\".\n"
            "Quando o cliente responder com o nome, chame OBRIGATORIAMENTE a ferramenta `tool_atualizar_nome_lead` com o nome confirmado.\n"
        )

    prompt_saudacao = (
        f"{prompt_painel}\n\n"
        "--- DADOS DE SISTEMA OBRIGATÓRIOS (USE-OS PARA RESPONDER O CLIENTE) ---\n"
        f"Agora são {hora_atual}.\n"
        f"NOME_IDENTIFICADO_PELO_SISTEMA: \"{nome_sistema or '(vazio)'}\"\n"
        f"TIPO_NOME_DETECTADO: {tipo_nome}\n"
        f"{regra_mensagem_base}"
        f"{regra_identidade}"
        f"Considere este histórico global apenas para leitura contextual: {historico_global}.\n"
        "-----------------------------------------------------------------------\n"
    )

    try:
        llm = await get_llm(empresa_id)
        resposta = await _ainvoke_with_openai_guard(
            llm,
            [("system", _prepend_resumo_cliente_system_prompt(state, prompt_saudacao)), ("user", ultima_mensagem)],
            empresa_id,
        )
        resposta_texto = str(getattr(resposta, "content", "") or "").strip()
    except Exception as e:
        logger.exception("[NODE ESPECIALISTA SAUDACAO] Falha ao invocar LLM: %s", e)
        resposta_texto = saudacao_base_empresa or "Olá! Seja bem-vindo(a)." if primeiro_contato else "Olá! Como posso ajudar?"

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

        intencoes = intencoes[:1]

        print(f"[NODE ESPECIALISTA DINAMICO] Top-1 acionado: {intencoes} (contato: {state.get('nome_contato')}).")
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

                mission = str(
                    (especialista_db.prompt_sistema if especialista_db else prompt_especialista_meta) or ""
                ).strip()
                prompt_base = (
                    "Você responde diretamente ao cliente neste canal.\n"
                    "Use as ferramentas disponíveis de forma silenciosa: não descreva raciocínio, cadeias de pensamento "
                    "nem passos internos (nada de 'vou consultar', 'deixe-me verificar', etc.).\n"
                    "Não inclua JSON de diagnóstico, dumps técnicos nem tags tipo <thinking> na mensagem final.\n"
                    f"HISTORICO_CURTO (contexto):\n{historico_curto}\n\n"
                    "--- Missão e políticas do especialista ---\n"
                    f"{mission or '(configure o prompt do especialista no painel)'}\n"
                )

                prompt_especialista_final = mission
                if "[GOOGLE_SEARCH_ENABLED=true]" in prompt_especialista_final:
                    nome_search_tool = str(getattr(google_search, "name", "google_search")).strip()
                    if nome_search_tool not in nomes_tools_registradas:
                        tools_disponiveis.append(google_search)
                        nomes_tools_registradas.add(nome_search_tool)
                    descricoes_tools.append(
                        "- google_search: consulta informações públicas recentes na web (internet)."
                    )
                    prompt_base += (
                        "\nFerramenta habilitada: google_search.\n"
                        "Quando faltar informação atualizada no contexto interno, você PODE usar google_search.\n"
                        "Sempre valide coerência e cite que a resposta veio de busca web quando aplicável.\n"
                    )

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
                            elif chave_nativa == "tool_adicionar_tag_lead":
                                async def _tool_adicionar_tag_lead_contextual(
                                    tag_id: str = "",
                                    tag_nome: str = "",
                                    _lead_id: str | None = str(lead_id) if lead_id else None,
                                    _empresa_id: str | None = str(state.get("empresa_id") or "").strip() or None,
                                    _coroutine_native=coroutine_native,
                                ) -> str:
                                    if not _lead_id or not _empresa_id:
                                        return "Falha ao adicionar tag: contexto de lead/empresa ausente."
                                    return await _coroutine_native(
                                        lead_id=_lead_id,
                                        empresa_id=_empresa_id,
                                        tag_id=str(tag_id or "").strip(),
                                        tag_nome=str(tag_nome or "").strip(),
                                    )

                                coroutine_native = _tool_adicionar_tag_lead_contextual
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
                            elif chave_nativa == "tool_atualizar_nome_lead":
                                async def _tool_atualizar_nome_lead_contextual(
                                    novo_nome: str,
                                    lead_id: Optional[str] = None,
                                    _lead_id: str | None = str(lead_id) if lead_id else None,
                                    _coroutine_native=coroutine_native,
                                ) -> str:
                                    lead_id_final = str(lead_id or _lead_id or "").strip()
                                    if not lead_id_final:
                                        return "Falha ao atualizar nome: contexto de lead ausente."
                                    return await _coroutine_native(
                                        lead_id=lead_id_final,
                                        novo_nome=novo_nome,
                                    )

                                coroutine_native = _tool_atualizar_nome_lead_contextual
                            elif chave_nativa == "tool_consultar_tags_empresa":
                                async def _tool_consultar_tags_empresa_contextual(
                                    _empresa_id: str | None = str(state.get("empresa_id") or "").strip() or None,
                                    _coroutine_native=coroutine_native,
                                ) -> str:
                                    if not _empresa_id:
                                        return "Falha ao consultar tags: empresa não identificada."
                                    return await _coroutine_native(empresa_id=_empresa_id)

                                coroutine_native = _tool_consultar_tags_empresa_contextual
                            elif chave_nativa == "tool_listar_etapas_funil":
                                async def _tool_listar_etapas_funil_contextual(
                                    _empresa_id: str | None = str(state.get("empresa_id") or "").strip() or None,
                                    _coroutine_native=coroutine_native,
                                ) -> str:
                                    if not _empresa_id:
                                        return "Falha ao listar etapas: empresa não identificada."
                                    return await _coroutine_native(empresa_id=_empresa_id)

                                coroutine_native = _tool_listar_etapas_funil_contextual
                            elif chave_nativa == "tool_atualizar_etapa_lead":
                                async def _tool_atualizar_etapa_lead_contextual(
                                    etapa_id: str,
                                    _lead_id: str | None = str(lead_id) if lead_id else None,
                                    _empresa_id: str | None = str(state.get("empresa_id") or "").strip() or None,
                                    _coroutine_native=coroutine_native,
                                ) -> str:
                                    if not _lead_id or not _empresa_id:
                                        return "Erro: contexto de lead/empresa ausente para mover etapa."
                                    return await _coroutine_native(
                                        lead_id=_lead_id,
                                        empresa_id=_empresa_id,
                                        etapa_id=str(etapa_id or "").strip(),
                                    )

                                coroutine_native = _tool_atualizar_etapa_lead_contextual
                            elif chave_nativa in ("transferir_para_humano", "tool_transferir_para_humano"):
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

            texto_etapas_funil = ""
            nome_etapa_lead_prompt = ""
            if empresa_uuid and lead_id:
                try:
                    lid = uuid.UUID(str(lead_id).strip())
                    from app.services.crm_etapas_service import listar_etapas_empresa_formatadas, obter_nome_etapa_lead

                    async with AsyncSessionLocal() as s_funil:
                        lr = await s_funil.execute(
                            select(CRMLead.etapa_id).where(CRMLead.id == lid, CRMLead.empresa_id == empresa_uuid)
                        )
                        etapa_id_lead = lr.scalar_one_or_none()
                        nome_etapa_lead_prompt = await obter_nome_etapa_lead(s_funil, empresa_uuid, etapa_id_lead)
                        texto_etapas_funil, _ = await listar_etapas_empresa_formatadas(s_funil, empresa_uuid)
                except Exception as exc_funil:
                    logger.debug("[FUNIL PROMPT] Falha ao montar contexto de etapas: %s", exc_funil)

            system_message_adicional = f"\n{contexto_empresa}"
            if texto_etapas_funil:
                system_message_adicional += (
                    "\n\n[CRM — FUNIL DESTA EMPRESA]\n"
                    f"O lead está atualmente na etapa: {nome_etapa_lead_prompt}.\n"
                    "As etapas disponíveis no funil desta empresa (UUID oficial de cada coluna) são:\n"
                    f"{texto_etapas_funil}\n"
                    "Para avançar o lead, use a ferramenta de atualização de etapa com um etapa_id copiado desta lista. "
                    "Se precisar reconsultar, use a ferramenta de listar etapas."
                )
            contexto_funil_partes = []
            etapa_funil = str(state.get("etapa_funil") or "").strip()
            objetivo_atual = str(state.get("objetivo_atual") or "").strip()
            proxima_acao = str(state.get("proxima_acao") or "").strip()
            if etapa_funil:
                contexto_funil_partes.append(f"Etapa atual: {etapa_funil}")
            if objetivo_atual:
                contexto_funil_partes.append(f"Objetivo: {objetivo_atual}")
            if proxima_acao:
                contexto_funil_partes.append(f"Próxima ação: {proxima_acao}")
            if contexto_funil_partes:
                system_message_adicional += (
                    "\n\n[CONTEXTO DO FUNIL]\n"
                    + "\n".join(contexto_funil_partes)
                )
            if descricoes_tools:
                system_message_adicional += "\n\nFerramentas disponíveis:\n" + "\n".join(descricoes_tools)
                system_message_adicional += "\nUse-as quando necessário para obter dados técnicos."
            if state.get("handoff_requested", False):
                system_message_adicional += (
                    "\n\nO usuário pediu atendimento humano; priorize tool de transferência quando aplicável."
                )
            prompt_completo = prompt_base + system_message_adicional

            modelo_esp = ""
            if especialista_db:
                modelo_esp = str(
                    getattr(especialista_db, "modelo_llm", "")
                    or getattr(especialista_db, "modelo_ia", "")
                    or ""
                ).strip()
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
                mensagem_usuario_atual = await _obter_ultima_mensagem_inbound_multimodal(
                    empresa_id=empresa_id,
                    identificador_origem=str(state.get("identificador_origem") or ""),
                    fallback_texto=ultima_mensagem,
                )
                mensagens = [
                    system_msg,
                    mensagem_usuario_atual,
                ]
                for _ in range(5):
                    logger.info(
                        "[NODE ESPECIALISTA DINAMICO][ETAPA 3] Invocando LLM para '%s'",
                        nome_especialista_resultado,
                    )
                    resposta = await _ainvoke_with_openai_guard(llm_para_invocar, mensagens, state.get("empresa_id"))
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
                                    state["resposta_final"] = str(msg_transferencia or "").strip()
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
            texto_ao_cliente = str(resposta_parcial or "").strip()
            if texto_ao_cliente and not texto_ao_cliente.lower().startswith("falha na etapa"):
                state["resposta_final"] = texto_ao_cliente

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
                    "tool_adicionar_tag_lead",
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

        state["super_contexto_especialistas"] = ""
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
    if state.get("ia_bloqueada_entrada"):
        return "node_handoff"
    if state.get("fluxo_encerrado"):
        return END
    if state.get("nome_contato") is None:
        return "capturar_nome"
    return "node_roteador_maestro"

# 3. Desenhar o Grafo
workflow = StateGraph(AgentState)

workflow.add_node("node_crm", node_crm)
workflow.add_node("node_capturar_nome", node_capturar_nome)
workflow.add_node("node_encerrar_resposta", node_encerrar_resposta)
workflow.add_node("node_roteador_maestro", node_roteador_maestro)
workflow.add_node("node_acao_sistema", node_acao_sistema)
workflow.add_node("node_handoff", node_handoff)
workflow.add_node("node_especialista_dinamico", node_especialista_dinamico)

workflow.set_entry_point("node_crm")

workflow.add_conditional_edges(
    "node_crm",
    router_crm,
    {
        END: END,
        "capturar_nome": "node_capturar_nome",
        "node_handoff": "node_handoff",
        "node_roteador_maestro": "node_roteador_maestro",
    },
)

workflow.add_edge("node_capturar_nome", END)

def router_encerrar_resposta(state: AgentState):
    if state.get("acoes_sistema_pendentes"):
        return "node_acao_sistema"
    return END


workflow.add_conditional_edges(
    "node_encerrar_resposta",
    router_encerrar_resposta,
    {
        END: END,
        "node_acao_sistema": "node_acao_sistema",
    },
)

def router_maestro(state: AgentState):
    # Curto-circuito para parar a fila se houve transferência
    respostas = state.get("respostas_especialistas", [])
    if any("SISTEMA_BOT_PAUSADO" in str(r) or "AGUARDANDO_HUMANO" in str(r) for r in respostas):
        state["fila_agentes"] = []
        return "node_encerrar_resposta"

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
            state["resposta_final"] = (
                "Perfeito! Vou te transferir agora para o time responsável. "
                "Um atendente humano continua com você em instantes."
            )
        state["especialista_corrente"] = None
        return "node_acao_sistema"
    if not fila_agentes:
        state["fila_agentes"] = []
        state["especialista_corrente"] = None
        return "node_encerrar_resposta"

    agente_atual = str(fila_agentes.pop(0) or "").strip()
    state["fila_agentes"] = fila_agentes
    print("--- [CONTROLE DE FILA] ---")
    print(f"Agente atual sendo despachado: {agente_atual}")
    print(f"Agentes restantes na fila: {state['fila_agentes']}")
    print("--------------------------")
    if not agente_atual:
        state["especialista_corrente"] = None
        return "node_encerrar_resposta"

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

    # Todo especialista (incl. saudação, legados por nome) passa pelo nó dinâmico.
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
        "node_encerrar_resposta": "node_encerrar_resposta",
        "node_acao_sistema": "node_acao_sistema",
        "node_especialista_dinamico": "node_especialista_dinamico",
    },
)


def router_pos_especialista(state: AgentState):
    respostas = state.get("respostas_especialistas", [])
    if any("SISTEMA_BOT_PAUSADO" in str(r) or "AGUARDANDO_HUMANO" in str(r) for r in respostas):
        state["fila_agentes"] = []
        return "node_encerrar_resposta"
    if state.get("resposta_final"):
        state["fila_agentes"] = []
        return "node_encerrar_resposta"
    return "node_roteador_maestro"


workflow.add_conditional_edges(
    "node_especialista_dinamico",
    router_pos_especialista,
    {
        "node_encerrar_resposta": "node_encerrar_resposta",
        "node_roteador_maestro": "node_roteador_maestro",
    },
)

def router_pos_acao_sistema(state: AgentState):
    if state.get("bot_foi_pausado"):
        return "node_handoff"
    if state.get("resposta_final"):
        return "node_encerrar_resposta"
    return "node_roteador_maestro"


workflow.add_conditional_edges(
    "node_acao_sistema",
    router_pos_acao_sistema,
    {
        END: END,
        "node_encerrar_resposta": "node_encerrar_resposta",
        "node_handoff": "node_handoff",
        "node_roteador_maestro": "node_roteador_maestro",
    }
)
workflow.add_edge("node_handoff", END)
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


async def _get_followup_prompt_base(empresa_id: str) -> tuple[str, str]:
    """Busca o nome da empresa e o prompt personalizado do Especialista de Follow-up."""
    import uuid as _uuid
    from db.database import AsyncSessionLocal as _ASL
    from db.models import Empresa as _Empresa, Especialista as _Especialista
    from sqlalchemy import select as _sel

    nome_empresa = ""
    prompt_personalizado = ""
    try:
        async with _ASL() as sess:
            emp_uuid = _uuid.UUID(empresa_id)
            # Buscar Empresa
            res_emp = await sess.execute(_sel(_Empresa).where(_Empresa.id == emp_uuid))
            emp = res_emp.scalars().first()
            if emp:
                nome_empresa = emp.nome_empresa or ""
            
            # Buscar Prompt do Especialista
            res_esp = await sess.execute(_sel(_Especialista).where(
                _Especialista.empresa_id == emp_uuid,
                _Especialista.nome == "especialista_followup"
            ))
            esp = res_esp.scalars().first()
            if esp:
                prompt_personalizado = esp.prompt_sistema
    except Exception as e:
        print(f"[FOLLOW-UP] Erro ao buscar dados no banco: {e}", flush=True)

    if not prompt_personalizado:
        nome_empresa_prompt = (nome_empresa or "").strip() or "sua empresa"
        prompt_personalizado = f"Você é um assistente da {nome_empresa_prompt}. Seja educado e prestativo."

    return nome_empresa, prompt_personalizado


async def gerar_followup_contextual(canal: str, identificador_origem: str, empresa_id: str) -> str:
    """
    Gera um Nudge (Nível 1) — apenas as últimas 2 mensagens + nome da empresa.
    """
    print("[FOLLOW-UP CONTEXTUAL] Iniciando Nível 1...", flush=True)

    # Busca apenas as últimas 2 mensagens
    historico, _ = await _buscar_historico_lead_para_followup(canal, identificador_origem, empresa_id, limite=2)

    nome_empresa, prompt_base = await _get_followup_prompt_base(empresa_id)
    print(f"[FOLLOW-UP CONTEXTUAL] Empresa: '{nome_empresa}' | Histórico disponível: {bool(historico)}", flush=True)

    fim_conversa = historico if historico else "(sem histórico registrado)"

    prompt = f"""{prompt_base}

INSTRUÇÃO ATUAL (REENGANJAMENTO - NÍVEL 1): 
O cliente parou de responder há algum tempo. Gere UMA frase curta e educada de retomada de conversa baseada no histórico abaixo.
Seja sutil e não tente vender nada. (Exemplo de tom: "Ficou alguma dúvida sobre o que conversamos?") Máximo 15 palavras.

Fim da conversa:
{fim_conversa}"""

    _conversation_debug_log(f"--- PROMPT FINAL FOLLOW-UP (NIVEL 1) ---\n{prompt}\n" + "-"*40, flush=True)
    llm = await get_llm(empresa_id)
    resposta = await _ainvoke_with_openai_guard(llm, prompt, empresa_id)
    _conversation_debug_log(f"[FOLLOW-UP RESULT] Resposta da IA: {resposta.content}", flush=True)
    return resposta.content


async def gerar_followup_encerramento(canal: str, identificador_origem: str, empresa_id: str) -> str:
    """
    Gera uma Despedida (Nível 2) — mensagem curta de encerramento com nome da empresa.
    """
    print("[FOLLOW-UP ENCERRAMENTO] Iniciando Nível 2...", flush=True)

    historico, lead_id = await _buscar_historico_lead_para_followup(canal, identificador_origem, empresa_id, limite=3)
    import uuid as _uuid

    nome_empresa, prompt_base = await _get_followup_prompt_base(empresa_id)
    print(f"[FOLLOW-UP ENCERRAMENTO] Gerando prompt para empresa '{nome_empresa}'...", flush=True)

    fim_conversa = historico if historico else "(sem histórico registrado)"

    prompt = f"""{prompt_base}

INSTRUÇÃO ATUAL (ENCERRAMENTO - NÍVEL 2): 
O cliente não respondeu à tentativa de retomada. Escreva UMA mensagem curta (máximo 2 frases) informando que o atendimento será pausado/arquivado por inatividade, mas que o consultor foi notificado e o cliente pode voltar a chamar quando quiser.

Fim da conversa:
{fim_conversa}"""

    _conversation_debug_log(f"--- PROMPT FINAL FOLLOW-UP (NIVEL 2 ENCERRAMENTO) ---\n{prompt}\n" + "-"*40, flush=True)
    llm = await get_llm(empresa_id)
    resposta = await _ainvoke_with_openai_guard(llm, prompt, empresa_id)
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
