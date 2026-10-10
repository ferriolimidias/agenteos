import asyncio
from contextvars import ContextVar
import hashlib
import logging
import os
import secrets
import uuid
from typing import List
from fastapi import BackgroundTasks
from app.schemas import StandardMessage
from app.agents.tenant_ai import get_tenant_ai_status
from app.core.secret_sanitizer import sanitize_for_log
from app.services.rosangela_ai_control import resolve_conversation_ai_state
from app.services.operational_identity import canonical_operational_address
from app.services.websocket_manager import manager
from app.api.time_serialization import serialize_utc_timestamp

LOG_LEVEL_CONVERSATION = os.getenv("LOG_LEVEL_CONVERSATION", "INFO").upper()
logger = logging.getLogger(__name__)
_PROCESSING_LIFECYCLE: ContextVar["_ProcessingLifecycle | None"] = ContextVar(
    "agentos_processing_lifecycle", default=None
)
_BOUNCER_LOCK_CONTEXT: ContextVar["_BouncerLockHeartbeat | None"] = ContextVar(
    "agentos_bouncer_lock_context", default=None
)
BOUNCER_DEBOUNCE_SECONDS = int(os.getenv("BOUNCER_DEBOUNCE_SECONDS", "6"))
BOUNCER_LOCK_TTL_SECONDS = int(os.getenv("BOUNCER_LOCK_TTL_SECONDS", "120"))
BOUNCER_LOCK_RENEW_INTERVAL_SECONDS = int(os.getenv("BOUNCER_LOCK_RENEW_INTERVAL_SECONDS", "30"))
BOUNCER_INFLIGHT_MARKER_TTL_SECONDS = BOUNCER_LOCK_TTL_SECONDS + BOUNCER_LOCK_RENEW_INTERVAL_SECONDS
BOUNCER_RELEASE_LOCK_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""
BOUNCER_RENEW_LOCK_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("EXPIRE", KEYS[1], tonumber(ARGV[2]))
end
return 0
"""


def _conversation_debug_enabled() -> bool:
    return LOG_LEVEL_CONVERSATION == "DEBUG"


def _conversation_debug_log(message: str) -> None:
    if _conversation_debug_enabled():
        print(message)


def get_llm_model(model_name: str, api_key: str = None):
    """
    Wrapper para fábrica central de LLM.
    """
    from app.core.llm_factory import get_llm_model as _core_get_llm_model, normalize_model_name

    normalized = normalize_model_name(model_name)
    print(f"[get_llm_model] Solicitado modelo: '{model_name}' (normalizado: '{normalized}')")
    return _core_get_llm_model(normalized, api_key=api_key)

import json
from datetime import datetime, timedelta

def is_ai_blocked(lead: object, now: datetime | None = None) -> bool:
    """
    Fonte única de verdade para bloqueio de IA por lead.

    Precedência:
    1) Pausa permanente/manual por lead.ia_ativa == False
    2) Pausa temporária por lead.bot_pausado_ate > now
    """
    if lead is None:
        return False

    if not bool(getattr(lead, "ia_ativa", True)):
        return True

    referencia = now or datetime.utcnow()
    pausado_ate = getattr(lead, "bot_pausado_ate", None)
    return bool(pausado_ate and pausado_ate > referencia)


def _manual_ai_payload(lead: object | None) -> dict:
    if lead is None:
        return {"manual_enabled": False, "source": "crm_leads.ia_ativa"}
    return {
        "manual_enabled": bool(getattr(lead, "ia_ativa", True)),
        "source": "crm_leads.ia_ativa",
        "status_atendimento": str(getattr(lead, "status_atendimento", "") or "aberto"),
        "bot_pausado_ate": (
            getattr(lead, "bot_pausado_ate", None).isoformat()
            if getattr(lead, "bot_pausado_ate", None)
            else None
        ),
    }


async def _broadcast_ai_state_update(
    *,
    empresa_id: str,
    lead: object | None,
    ai_gate: object,
    override: dict | None = None,
) -> None:
    if lead is None:
        return
    try:
        state_payload = ai_gate.as_dict() if hasattr(ai_gate, "as_dict") else {}
        state_payload = {**state_payload, **_manual_ai_payload(lead)}
        if isinstance(override, dict):
            state_payload = {**state_payload, **override}
        await manager.broadcast_to_empresa(
            str(empresa_id),
            {
                "type": "inbox.ai_state.updated",
                "tipo_evento": "ai_state_updated",
                "empresa_id": str(empresa_id),
                "lead_id": str(getattr(lead, "id", "") or ""),
                "telefone": str(getattr(lead, "telefone_contato", "") or ""),
                "payload": {
                    "lead_id": str(getattr(lead, "id", "") or ""),
                    "ia_ativa": bool(getattr(lead, "ia_ativa", True)),
                    "status_atendimento": str(getattr(lead, "status_atendimento", "") or "aberto"),
                    "bot_pausado_ate": state_payload.get("bot_pausado_ate"),
                },
                "ai_state": state_payload,
            },
        )
        lifecycle = _PROCESSING_LIFECYCLE.get()
        processing_state = str((override or {}).get("processing_state") or "").strip()
        if lifecycle is not None and processing_state:
            lifecycle.observe(
                empresa_id=str(empresa_id),
                lead=lead,
                ai_gate=ai_gate,
                processing_state=processing_state,
            )
        if processing_state in _ProcessingLifecycle._TERMINAL_STATES:
            logger.warning(
                "ai.processing.terminal empresa_id=%s lead_id=%s state=%s reason=%s",
                sanitize_for_log(str(empresa_id or "")),
                sanitize_for_log(str(getattr(lead, "id", "") or "")),
                processing_state,
                sanitize_for_log(str((override or {}).get("reason_code") or "completed")),
            )
    except Exception as exc:
        logger.warning(
            "[AUTOATENDIMENTO] Falha ao publicar ai-state websocket. empresa_id=%s lead_id=%s erro=%s",
            empresa_id,
            getattr(lead, "id", None),
            sanitize_for_log(str(exc)),
        )


class _ProcessingLifecycle:
    _TERMINAL_STATES = {
        "completed",
        "idle",
        "error",
        "failed_start",
        "failed_model",
        "guard_blocked",
        "cancelled",
    }

    def __init__(self) -> None:
        self.empresa_id: str | None = None
        self.lead: object | None = None
        self.ai_gate: object | None = None
        self.last_state: str | None = None
        self.terminal_emitted = False
        self.error_class: str | None = None

    def observe(
        self,
        *,
        empresa_id: str,
        lead: object | None,
        ai_gate: object,
        processing_state: str,
    ) -> None:
        self.empresa_id = empresa_id
        self.lead = lead
        self.ai_gate = ai_gate
        self.last_state = processing_state
        if processing_state in self._TERMINAL_STATES:
            self.terminal_emitted = True

    async def ensure_terminal(self) -> None:
        if self.terminal_emitted or self.last_state not in {"queued", "processing"}:
            return
        terminal_state = "failed_start" if self.last_state == "processing" else "idle"
        reason_code = "processing_incomplete" if terminal_state == "failed_start" else "processing_not_started"
        await _broadcast_ai_state_update(
            empresa_id=str(self.empresa_id or ""),
            lead=self.lead,
            ai_gate=self.ai_gate,
            override={
                "processing_state": terminal_state,
                "reason_code": reason_code,
                "reason": reason_code,
                "reason_label": "Processamento encerrado",
                "processing_label": "Processamento encerrado",
                "processing_description": "A IA encerrou esta tentativa sem deixar processamento pendente.",
            },
        )


def _ai_log(event: str, **fields) -> None:
    safe_fields = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key in {"runtime_key", "phone", "telefone", "identificador_origem"}:
            digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
            safe_fields[key] = f"sha256:{digest}"
            continue
        safe_fields[key] = sanitize_for_log(str(value))[:300]
    log_method = logger.warning if event == "ai.first_turn.trace" else logger.info
    log_method(
        "%s %s",
        event,
        " ".join(f"{key}={value}" for key, value in sorted(safe_fields.items())),
    )


def _bouncer_last_message_key(runtime_key: str) -> str:
    return f"last_msg:{runtime_key}"


async def _bouncer_latest_message_version(redis_client, runtime_key: str) -> str | None:
    value = await redis_client.get(_bouncer_last_message_key(runtime_key))
    return str(value) if value is not None else None


def _bouncer_version_is_newer(latest: str | None, snapshot: str | None) -> bool:
    if not latest or not snapshot:
        return False
    try:
        return float(latest) > float(snapshot)
    except (TypeError, ValueError):
        return latest != snapshot


async def _bouncer_response_superseded(
    redis_client,
    runtime_key: str,
    snapshot: str | None,
    *,
    empresa_id: str | None = None,
    connection_id: str | None = None,
    provider: str | None = None,
) -> bool:
    latest = await _bouncer_latest_message_version(redis_client, runtime_key)
    if not _bouncer_version_is_newer(latest, snapshot):
        return False
    _ai_log(
        "ai.bouncer.superseded",
        empresa_id=empresa_id,
        connection_id=connection_id,
        provider=provider,
        runtime_key=runtime_key,
        batch_version=snapshot,
        latest_version=latest,
    )
    return True


async def _bouncer_owner_still_current(
    *,
    runtime_key: str,
    empresa_id: str | None = None,
    connection_id: str | None = None,
    provider: str | None = None,
) -> bool:
    heartbeat = _BOUNCER_LOCK_CONTEXT.get()
    if heartbeat is None:
        return True
    if await heartbeat.renew_once():
        return True
    _ai_log(
        "ai.bouncer.lock_lost",
        empresa_id=empresa_id,
        connection_id=connection_id,
        provider=provider,
        runtime_key=runtime_key,
    )
    return False


async def _bouncer_dispatch_still_allowed(
    *,
    mensagens: List[StandardMessage],
    inbound_route,
    lead_id_hist: str | None,
) -> tuple[bool, str | None]:
    try:
        from db.database import AsyncSessionLocal
        from db.models import CRMLead, ChannelConversation, Empresa
        from sqlalchemy import select

        empresa_uuid = uuid.UUID(str(mensagens[0].empresa_id))
        async with AsyncSessionLocal() as session:
            empresa = (await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))).scalars().first()
            lead = None
            if lead_id_hist:
                try:
                    lead_uuid = uuid.UUID(str(lead_id_hist))
                    lead = (
                        await session.execute(
                            select(CRMLead).where(CRMLead.id == lead_uuid, CRMLead.empresa_id == empresa_uuid)
                        )
                    ).scalars().first()
                except (ValueError, TypeError):
                    lead = None
            if lead is None:
                lead = (
                    await session.execute(
                        select(CRMLead).where(
                            CRMLead.empresa_id == empresa_uuid,
                            CRMLead.telefone_contato == str(mensagens[0].identificador_origem),
                        )
                    )
                ).scalars().first()

            conversation = None
            conversation_id_raw = getattr(mensagens[0], "channel_conversation_id", None)
            if conversation_id_raw:
                try:
                    conversation_uuid = uuid.UUID(str(conversation_id_raw))
                    conversation = (
                        await session.execute(
                            select(ChannelConversation).where(
                                ChannelConversation.id == conversation_uuid,
                                ChannelConversation.empresa_id == empresa_uuid,
                            )
                        )
                    ).scalars().first()
                except (ValueError, TypeError):
                    conversation = None

            ai_gate = await resolve_conversation_ai_state(
                session,
                empresa=empresa,
                lead=lead,
                conversation=conversation,
                connection_id=str(mensagens[0].conexao_id or ""),
                provider=(inbound_route.provider.value if inbound_route else getattr(mensagens[0], "provider", None) or mensagens[0].canal),
                phone=str(mensagens[0].identificador_origem),
            )
            if ai_gate.state != "active":
                return False, str(ai_gate.reason or "ai_gate_blocked")
            if is_ai_blocked(lead):
                return False, "human_or_manual_block"
            return True, None
    except Exception as exc:
        logger.error(
            "[AUTOATENDIMENTO] Falha ao revalidar gate antes do dispatch. empresa_id=%s erro=%s",
            getattr(mensagens[0], "empresa_id", None),
            sanitize_for_log(str(exc)),
        )
        return False, "gate_revalidation_error"


async def _bouncer_release_lock(redis_client, lock_key: str, owner_token: str) -> bool:
    return bool(await redis_client.eval(BOUNCER_RELEASE_LOCK_LUA, 1, lock_key, owner_token))


async def _bouncer_renew_lock(redis_client, lock_key: str, owner_token: str, ttl_seconds: int) -> bool:
    return bool(await redis_client.eval(BOUNCER_RENEW_LOCK_LUA, 1, lock_key, owner_token, int(ttl_seconds)))


class _BouncerLockHeartbeat:
    def __init__(
        self,
        redis_client,
        *,
        lock_key: str,
        owner_token: str,
        ttl_seconds: int,
        renew_interval_seconds: int,
        runtime_key: str,
        empresa_id: str | None,
        connection_id: str | None,
        provider: str | None,
    ) -> None:
        self.redis_client = redis_client
        self.lock_key = lock_key
        self.owner_token = owner_token
        self.ttl_seconds = ttl_seconds
        self.renew_interval_seconds = renew_interval_seconds
        self.runtime_key = runtime_key
        self.empresa_id = empresa_id
        self.connection_id = connection_id
        self.provider = provider
        self._task: asyncio.Task | None = None
        self.lost = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def renew_once(self) -> bool:
        renewed = await _bouncer_renew_lock(
            self.redis_client,
            self.lock_key,
            self.owner_token,
            self.ttl_seconds,
        )
        if renewed:
            _ai_log(
                "ai.bouncer.lock_renewed",
                empresa_id=self.empresa_id,
                connection_id=self.connection_id,
                provider=self.provider,
                runtime_key=self.runtime_key,
                ttl_seconds=self.ttl_seconds,
            )
        else:
            self.lost = True
        return renewed

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.renew_interval_seconds)
            if not await self.renew_once():
                return

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


def _extract_agent_operational_error(estado_final: dict | None) -> str | None:
    if not isinstance(estado_final, dict):
        return None
    metadata = estado_final.get("agent_metadata")
    if isinstance(metadata, dict):
        operational_error = str(metadata.get("operational_error") or "").strip()
        if operational_error:
            return operational_error
    return None


def _extract_agent_error_class(estado_final: dict | None) -> str | None:
    reason = _extract_agent_block_reason(estado_final)
    if reason in {"tenant_llm_provider_error", "tenant_llm_unavailable", "tenant_llm_empty_response"}:
        return "llm_error"
    if reason == "tenant_tool_planner_error":
        return "tool_planner_error"
    if reason == "fiscal_validation_failed":
        return "fiscal_validation_failed"
    if reason in {"response_guard_blocked", "technical_leak", "raw_json_leak"}:
        return "guard_error"
    if reason:
        return reason
    return None


def _extract_agent_block_reason(estado_final: dict | None) -> str | None:
    if not isinstance(estado_final, dict):
        return None
    metadata = estado_final.get("agent_metadata")
    if isinstance(metadata, dict):
        blocked = str(metadata.get("blocked_reason") or "").strip()
        if blocked:
            return blocked
        guard = metadata.get("guard_result")
        if isinstance(guard, dict):
            reasons = guard.get("reason_codes")
            if isinstance(reasons, list) and reasons:
                return str(reasons[0])
        operational_error = str(metadata.get("operational_error") or "").strip()
        if operational_error:
            known_operational_codes = {
                "tenant_llm_provider_error",
                "tenant_llm_unavailable",
                "tenant_llm_empty_response",
                "tenant_tool_planner_error",
                "fiscal_validation_failed",
            }
            return operational_error if operational_error in known_operational_codes else "agent_operational_error"
    guard_result = estado_final.get("guard_result")
    if isinstance(guard_result, dict):
        reasons = guard_result.get("reason_codes")
        if isinstance(reasons, list) and reasons:
            return str(reasons[0])
    pendencias = estado_final.get("pendencias")
    if isinstance(pendencias, list) and pendencias:
        return str(pendencias[0])
    return None


async def get_available_models() -> list[dict[str, str]]:
    """
    Lista modelos disponíveis filtrando apenas os de Chat das principais APIs.
    Realiza cache da lista no Redis por 24 horas para evitar lentidão.
    """
    from app.api.main import redis_client
    cache_key = "available_ai_models:v2"
    
    # Tenta buscar do cache primeiro
    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
        
    from app.core.model_catalog import model_options_payload

    modelos = model_options_payload()
    
    # Salva no cache por 24 horas (86400 segundos)
    await redis_client.setex(cache_key, 86400, json.dumps(modelos))
    
    return modelos


async def _salvar_historico_saida_ia(
    *,
    empresa_id: str,
    telefone: str,
    resposta: str,
    conexao_id: str | None,
    external_message_id: str | None = None,
    lead_id: str | None = None,
    conversation_id: str | None = None,
) -> dict | None:
    """
    Persiste a mensagem outbound da IA em uma sessão isolada/limpa.
    Evita reaproveitar transações potencialmente sujas do fluxo principal.
    """
    from db.database import AsyncSessionLocal
    from db.models import ChannelConversation, CRMLead, Conexao, MensagemHistorico
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        empresa_uuid = uuid.UUID(empresa_id)
        conexao_id_limpo = str(conexao_id or "").strip()
        try:
            conexao_uuid = uuid.UUID(conexao_id_limpo) if conexao_id_limpo else None
        except (ValueError, TypeError):
            print(
                "[ENGINE] conexao_id inválido ao salvar histórico da IA; "
                f"valor recebido='{conexao_id_limpo}'. Salvando com conexao_id=None."
            )
            conexao_uuid = None

        lead = None
        if lead_id:
            try:
                lead_uuid = uuid.UUID(str(lead_id))
            except (TypeError, ValueError):
                lead_uuid = None
            if lead_uuid:
                lead = (
                    await session.execute(
                        select(CRMLead).where(CRMLead.empresa_id == empresa_uuid, CRMLead.id == lead_uuid)
                    )
                ).scalars().first()
        if lead is None and conversation_id:
            try:
                conversation_uuid = uuid.UUID(str(conversation_id))
            except (TypeError, ValueError):
                conversation_uuid = None
            if conversation_uuid:
                conversation = (
                    await session.execute(
                        select(ChannelConversation).where(
                            ChannelConversation.empresa_id == empresa_uuid,
                            ChannelConversation.id == conversation_uuid,
                        )
                    )
                ).scalars().first()
                if conversation and conversation.lead_id:
                    lead = (
                        await session.execute(
                            select(CRMLead).where(
                                CRMLead.empresa_id == empresa_uuid,
                                CRMLead.id == conversation.lead_id,
                            )
                        )
                    ).scalars().first()
        if lead is None:
            result = await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_uuid))
            canonical_phone = canonical_operational_address(telefone, channel="whatsapp")
            lead = next(
                (
                    item for item in result.scalars().all()
                    if canonical_operational_address(item.telefone_contato, channel="whatsapp") == canonical_phone
                ),
                None,
            )
        if not lead:
            print(
                "[ENGINE] Lead não encontrado ao salvar histórico outbound da IA. "
                f"empresa_id={empresa_id} telefone='{telefone}'"
            )
            return None

        if str(getattr(lead, "empresa_id", "")) != str(empresa_uuid):
            logger.error(
                "message_history.tenant_mismatch empresa_id=%s lead_id=%s source=%s",
                sanitize_for_log(str(empresa_uuid)),
                sanitize_for_log(str(getattr(lead, "id", ""))),
                "_salvar_historico_saida_ia",
            )
            return None

        if conexao_uuid is not None:
            result_conexao = await session.execute(
                select(Conexao.id).where(
                    Conexao.id == conexao_uuid,
                    Conexao.empresa_id == empresa_uuid,
                )
            )
            if result_conexao.scalar_one_or_none() is None:
                logger.error(
                    "message_history.tenant_mismatch empresa_id=%s lead_id=%s conexao_id=%s source=%s",
                    sanitize_for_log(str(empresa_uuid)),
                    sanitize_for_log(str(getattr(lead, "id", ""))),
                    sanitize_for_log(str(conexao_uuid)),
                    "_salvar_historico_saida_ia",
                )
                return None

        conversation_query = select(ChannelConversation).where(
            ChannelConversation.empresa_id == empresa_uuid,
            ChannelConversation.lead_id == lead.id,
        )
        if conversation_id:
            try:
                conversation_uuid = uuid.UUID(str(conversation_id))
            except (TypeError, ValueError):
                conversation_uuid = None
            if conversation_uuid:
                conversation_query = conversation_query.where(ChannelConversation.id == conversation_uuid)
        if conexao_uuid is not None:
            conversation_query = conversation_query.where(ChannelConversation.conexao_id == conexao_uuid)
        conversation_query = conversation_query.order_by(
            ChannelConversation.last_message_at.desc().nullslast(),
            ChannelConversation.atualizado_em.desc(),
        ).limit(1)
        conversation_result = await session.execute(conversation_query)
        conversation = conversation_result.scalars().first()

        nova_msg = MensagemHistorico(
            empresa_id=empresa_uuid,
            lead_id=lead.id,
            conexao_id=conexao_uuid,
            channel_conversation_id=getattr(conversation, "id", None),
            channel="whatsapp",
            direction="outbound",
            message_status="sent",
            provider="meta_whatsapp" if external_message_id else None,
            provider_status="accepted" if external_message_id else None,
            external_message_id=str(external_message_id or "").strip() or None,
            origem_mensagem="ia",
            texto=resposta,
            from_me=True,
        )

        try:
            session.add(nova_msg)
            await session.commit()
            await session.refresh(nova_msg)
        except Exception as e:
            print(f"ERRO CRÍTICO NO BANCO AO SALVAR HISTÓRICO DA IA: {sanitize_for_log(str(e))}")
            await session.rollback()
            return None

        return {
            "id": str(nova_msg.id),
            "texto": str(nova_msg.texto or ""),
            "from_me": bool(nova_msg.from_me),
            "tipo_mensagem": str(nova_msg.tipo_mensagem or "text"),
            "origem_mensagem": nova_msg.origem_mensagem,
            "media_url": str(nova_msg.media_url) if nova_msg.media_url else None,
            "criado_em": serialize_utc_timestamp(nova_msg.criado_em),
            "conversation_id": str(nova_msg.channel_conversation_id) if nova_msg.channel_conversation_id else None,
        }


async def _pausar_lead_para_humano(
    *,
    empresa_id: str,
    lead_id: str | None,
    telefone: str,
    reason: str,
) -> None:
    from db.database import AsyncSessionLocal
    from db.models import CRMLead
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (TypeError, ValueError):
        return
    async with AsyncSessionLocal() as session:
        lead = None
        if lead_id:
            try:
                result = await session.execute(
                    select(CRMLead).where(
                        CRMLead.id == uuid.UUID(str(lead_id)),
                        CRMLead.empresa_id == empresa_uuid,
                    )
                )
                lead = result.scalars().first()
            except (TypeError, ValueError):
                lead = None
        if lead is None:
            result = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == str(telefone or ""),
                )
            )
            lead = result.scalars().first()
        if not lead:
            return
        lead.ia_ativa = False
        lead.status_atendimento = "AGUARDANDO_HUMANO"
        dados = dict(getattr(lead, "dados_adicionais", None) or {})
        dados["autoatendimento_pausado"] = {
            "reason": reason,
            "paused_at": datetime.utcnow().isoformat(),
        }
        lead.dados_adicionais = dados
        flag_modified(lead, "dados_adicionais")
        await session.commit()


def formatar_historico_mensagens(mensagens: list, limite: int = None) -> str:
    """
    Formata uma lista de objetos MensagemHistorico numa string padronizada.
    """
    msgs = mensagens[-limite:] if limite else mensagens
    linhas = [f"[{'Atena' if m.from_me else 'Cliente'}]: {m.texto}" for m in msgs]
    return "\n".join(linhas) if linhas else "(sem histórico)"


def _formatar_historico_curto_estado(mensagens_estado: list[str], limite: int = 5) -> str:
    if not mensagens_estado:
        return ""
    itens_validos = [str(item or "").strip() for item in mensagens_estado if str(item or "").strip()]
    if not itens_validos:
        return ""
    ultimos = itens_validos[-max(1, int(limite)):]
    linhas = []
    for texto in ultimos:
        texto_lower = texto.lower()
        if texto_lower.startswith("assistente:"):
            linhas.append(f"IA: {texto.split(':', 1)[1].strip() if ':' in texto else texto}")
        elif texto_lower.startswith("usuario:") or texto_lower.startswith("usuário:"):
            linhas.append(f"Cliente: {texto.split(':', 1)[1].strip() if ':' in texto else texto}")
        else:
            linhas.append(f"Cliente: {texto}")
    return "\n".join(linhas).strip()


def first_agent_response_from_origins(origins: list[str] | tuple[str, ...] | None) -> bool:
    """Only an AgentOS AI outbound consumes the first AI response turn."""
    return not any(str(origin or "").strip().lower() == "ia" for origin in (origins or []))

async def atualizar_resumo_lead_bg(lead_id: str, empresa_id: str) -> None:
    """
    Consolida memória de longo prazo do lead sem bloquear o atendimento.
    Lê resumo atual + últimas 10 mensagens, gera um novo parágrafo e persiste em CRMLead.historico_resumo.
    """
    from db.database import AsyncSessionLocal
    from db.models import CRMLead, Empresa, MensagemHistorico
    from sqlalchemy import select
    from app.api.main import redis_client

    try:
        lead_uuid = uuid.UUID(str(lead_id))
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        print(f"[MEMORIA_BG] IDs inválidos para atualização: lead_id={lead_id} empresa_id={empresa_id}")
        return

    lock_key = f"lock:sumarizacao:{lead_id}"
    adquiriu_lock = await redis_client.set(lock_key, "1", ex=300, nx=True)  # Cooldown de 5 minutos
    if not adquiriu_lock:
        logger.info(f"[Sumarização] Cooldown ativo para lead {lead_id}. Ignorando.")
        return

    try:
        async with AsyncSessionLocal() as session:
            res_lead = await session.execute(
                select(CRMLead).where(CRMLead.id == lead_uuid, CRMLead.empresa_id == empresa_uuid)
            )
            lead = res_lead.scalars().first()
            if not lead:
                print(f"[MEMORIA_BG] Lead não encontrado para consolidar memória: {lead_id}")
                return

            resumo_atual = str(lead.historico_resumo or "").strip()

            res_msgs = await session.execute(
                select(MensagemHistorico)
                .where(MensagemHistorico.lead_id == lead.id)
                .order_by(MensagemHistorico.criado_em.desc())
                .limit(10)
            )
            ultimas_msgs = list(reversed(res_msgs.scalars().all()))
            linhas_historico = []
            for msg in ultimas_msgs:
                texto = str(msg.texto or "").strip()
                if not texto:
                    continue
                papel = "Assistente" if msg.from_me else "Cliente"
                linhas_historico.append(f"{papel}: {texto}")

            historico_recente = "\n".join(linhas_historico) or "(sem mensagens recentes)"

            api_key_empresa = None
            res_empresa = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
            empresa = res_empresa.scalars().first()
            if empresa and empresa.credenciais_canais:
                api_key_empresa = empresa.credenciais_canais.get("openai_api_key")

            llm = get_llm_model("gpt-5.4-nano", api_key=api_key_empresa)
            prompt_sistema = (
                "Você consolida memória de CRM para IA de atendimento. "
                "Sua saída deve ser APENAS 1 parágrafo curto em português (máx. 1200 caracteres), "
                "com fatos estáveis e vitais do cliente: nome, contexto, necessidades, objeções, "
                "preferências, estágio da conversa, urgência, cidade/unidade, orçamento e próximos passos. "
                "Não invente dados. Não use markdown."
            )
            prompt_usuario = (
                f"Resumo atual:\n{resumo_atual or '(vazio)'}\n\n"
                f"Últimas 10 mensagens:\n{historico_recente}\n\n"
                "Gere o NOVO resumo consolidado, substituindo o anterior."
            )

            resposta = await llm.ainvoke([("system", prompt_sistema), ("user", prompt_usuario)])
            novo_resumo = str(getattr(resposta, "content", "") or "").strip()
            if not novo_resumo:
                print(f"[MEMORIA_BG] LLM retornou resumo vazio para lead={lead_id}")
                return

            lead.historico_resumo = novo_resumo
            await session.commit()
            print(
                f"[MEMORIA_BG] Resumo atualizado para lead={lead_id} "
                f"(chars={len(novo_resumo)})."
            )
    except Exception as e:
        print(f"[MEMORIA_BG] Erro ao consolidar resumo do lead={lead_id}: {sanitize_for_log(str(e))}")


async def _registrar_falha_operacional_grafo(
    *,
    empresa_id: str,
    lead_id: str | None,
    canal: str | None,
    identificador_origem: str | None,
) -> bool:
    """
    Marca pendencia interna quando o grafo falha, sem produzir resposta ao cliente.
    O payload salvo evita stack trace, texto da conversa, credenciais e mensagens de erro.
    """
    from db.database import AsyncSessionLocal
    from db.models import CRMLead
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (TypeError, ValueError):
        logger.warning(
            "[AUTOATENDIMENTO] Falha operacional do grafo sem empresa_id válido. empresa_id=%s lead_id=%s",
            empresa_id,
            lead_id,
        )
        return False

    lead_uuid = None
    if lead_id:
        try:
            lead_uuid = uuid.UUID(str(lead_id))
        except (TypeError, ValueError):
            lead_uuid = None

    try:
        async with AsyncSessionLocal() as session:
            if lead_uuid:
                result = await session.execute(
                    select(CRMLead).where(CRMLead.id == lead_uuid, CRMLead.empresa_id == empresa_uuid)
                )
            else:
                telefone = str(identificador_origem or "").strip()
                if not telefone:
                    logger.warning(
                        "[AUTOATENDIMENTO] Falha operacional do grafo sem lead identificável. empresa_id=%s canal=%s",
                        empresa_id,
                        canal,
                    )
                    return False
                result = await session.execute(
                    select(CRMLead).where(
                        CRMLead.empresa_id == empresa_uuid,
                        CRMLead.telefone_contato == telefone,
                    )
                )

            lead = result.scalars().first()
            if not lead:
                logger.warning(
                    "[AUTOATENDIMENTO] Falha operacional do grafo sem lead encontrado. empresa_id=%s lead_id=%s canal=%s",
                    empresa_id,
                    lead_id,
                    canal,
                )
                return False

            dados = dict(lead.dados_adicionais or {}) if isinstance(lead.dados_adicionais, dict) else {}
            erro_anterior = dados.get("autoatendimento_erro")
            contador = 0
            if isinstance(erro_anterior, dict):
                try:
                    contador = int(erro_anterior.get("contador") or 0)
                except (TypeError, ValueError):
                    contador = 0

            dados["autoatendimento_erro"] = {
                "codigo": "GRAPH_AINVOKE_EXCEPTION",
                "status": "pendente_reprocessamento",
                "origem": "graph.ainvoke",
                "canal": str(canal or ""),
                "ocorrido_em": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "requer_revisao_manual": False,
                "contador": contador + 1,
            }
            lead.dados_adicionais = dados
            flag_modified(lead, "dados_adicionais")
            await session.commit()
            logger.warning(
                "[AUTOATENDIMENTO] Falha operacional registrada para reprocessamento, sem handoff. empresa_id=%s lead_id=%s canal=%s",
                empresa_id,
                str(lead.id),
                canal,
            )
            return True
    except Exception as exc:
        logger.error(
            "[AUTOATENDIMENTO] Falha ao registrar pendência operacional do grafo. empresa_id=%s lead_id=%s erro=%s",
            empresa_id,
            lead_id,
            sanitize_for_log(str(exc)),
        )
        return False


async def _processar_bloco_mensagens_impl(
    mensagens: List[StandardMessage], background_tasks: BackgroundTasks | None = None
):
    """
    Processa o bloco de mensagens quando o timer do debouncer termina.
    Invoca a arquitetura nova de agentes em `app/agents`.
    """
    if not mensagens:
        return
        
    print(f"\n--- INICIANDO PROCESSAMENTO IA ---")
    print(f"Canal: {mensagens[0].canal} | Origem: ***MASKED*** | Empresa: {mensagens[0].empresa_id}")
    print(f"Total de mensagens no bloco original: {len(mensagens)}")
    
    # 1. Block de Mensagem Vazia: Filtra e remove mensagens vazias
    mensagens_validas = []
    for msg in mensagens:
        texto_limpo = msg.texto_mensagem.strip()
        if texto_limpo:
            msg.texto_mensagem = texto_limpo
            mensagens_validas.append(msg)
            
    if not mensagens_validas:
        print("[ENGINE] Bloco de mensagens resultou vazio após limpeza. Abortando agentes.")
        return

    inbound_route = None
    from app.services.channels.routing import message_requires_canonical_route, route_from_standard_message

    try:
        inbound_route = route_from_standard_message(mensagens_validas[0])
    except Exception as route_exc:
        if message_requires_canonical_route(mensagens_validas[0]):
            logger.warning(
                "[ROUTING] Mensagem inbound bloqueada por rota canonica invalida. empresa_id=%s canal=%s erro=%s",
                getattr(mensagens_validas[0], "empresa_id", None),
                getattr(mensagens_validas[0], "canal", None),
                sanitize_for_log(str(route_exc)),
            )
            return

    from app.api.main import redis_client
    from app.services.channels.routing import runtime_key_from_standard_message

    runtime_key = runtime_key_from_standard_message(mensagens_validas[0])
    _ai_log(
        "ai.processing.started",
        empresa_id=getattr(mensagens_validas[0], "empresa_id", None),
        connection_id=getattr(mensagens_validas[0], "conexao_id", None),
        provider=inbound_route.provider.value if inbound_route else getattr(mensagens_validas[0], "provider", None),
        channel=inbound_route.channel.value if inbound_route else getattr(mensagens_validas[0], "canal", None),
        runtime_key=runtime_key,
        message_count=len(mensagens_validas),
    )

    if _conversation_debug_enabled():
        for i, msg in enumerate(mensagens_validas, 1):
            remetente = "Humano" if msg.is_human_agent else "Usuário/Lead"
            print(f" [{i}] {remetente}: {msg.texto_mensagem}")
        
    textos = [msg.texto_mensagem for msg in mensagens_validas]
    media_messages = [
        {
            "tipo_mensagem": str(getattr(msg, "tipo_mensagem", "") or "").strip(),
            "media_type": str(getattr(msg, "media_type", "") or getattr(msg, "tipo_mensagem", "") or "").strip(),
            "media_url": getattr(msg, "media_url", None),
            "media_id": getattr(msg, "media_id", None),
            "media_mime_type": getattr(msg, "media_mime_type", None),
            "caption": getattr(msg, "caption", None),
            "has_image": bool(getattr(msg, "has_image", False)),
            "image_analysis": getattr(msg, "image_analysis", None),
            "metadata": getattr(msg, "metadata", None) or {},
        }
        for msg in mensagens_validas
        if str(getattr(msg, "tipo_mensagem", "") or "").strip() and str(getattr(msg, "tipo_mensagem", "") or "").strip() != "text"
    ]
    
    # ── PONTO 1: Ingestão do histórico real do PostgreSQL ──────────────────────
    historico_bd_formatado = ""
    historico_curto: list[str] = []
    resumo_cliente = ""
    mensagens_globais = list(textos)
    lead_id_hist = getattr(mensagens[0], "lead_id", None)
    total_msgs_historico = 0
    previous_outbound_count = 0
    previous_ai_outbound_count = 0
    origins_found: list[str] = []
    current_persisted_id_count = 0
    current_external_id_count = 0
    try:
        import uuid as _uuid
        from db.database import AsyncSessionLocal as _ASL
        from db.models import CRMLead as _CRMLead, MensagemHistorico as _MH
        from sqlalchemy import select as _select, func as _func

        empresa_id_hist = mensagens[0].empresa_id
        telefone_hist   = str(mensagens[0].identificador_origem)
        empresa_uuid_hist = _uuid.UUID(empresa_id_hist)

        async with _ASL() as _sess:
            try:
                lead_uuid_hist = _uuid.UUID(str(lead_id_hist)) if lead_id_hist else None
            except (ValueError, TypeError):
                lead_uuid_hist = None
            lead_filters = [_CRMLead.empresa_id == empresa_uuid_hist]
            if lead_uuid_hist:
                lead_filters.append(_CRMLead.id == lead_uuid_hist)
            else:
                lead_filters.append(_CRMLead.telefone_contato == telefone_hist)
            _res_lead = await _sess.execute(_select(_CRMLead).where(*lead_filters))
            _lead_hist = _res_lead.scalars().first()

            if _lead_hist:
                lead_id_hist = str(_lead_hist.id)
                resumo_cliente = str(_lead_hist.historico_resumo or "").strip()
                persisted_current_ids = []
                current_external_ids = []
                for current_message in mensagens_validas:
                    raw_id = (getattr(current_message, "metadata", None) or {}).get("persisted_message_id")
                    external_id = str(getattr(current_message, "external_message_id", None) or "").strip()
                    if external_id:
                        current_external_ids.append(external_id)
                    try:
                        if raw_id:
                            persisted_current_ids.append(_uuid.UUID(str(raw_id)))
                    except (ValueError, TypeError):
                        continue
                current_persisted_id_count = len(persisted_current_ids)
                current_external_id_count = len(current_external_ids)
                conversation_id_hist = getattr(mensagens[0], "channel_conversation_id", None)
                try:
                    conversation_uuid_hist = _uuid.UUID(str(conversation_id_hist)) if conversation_id_hist else None
                except (ValueError, TypeError):
                    conversation_uuid_hist = None
                total_filters = [
                    _MH.channel_conversation_id == conversation_uuid_hist
                    if conversation_uuid_hist
                    else _MH.lead_id == _lead_hist.id
                ]
                if persisted_current_ids:
                    total_filters.append(_MH.id.not_in(persisted_current_ids))
                if current_external_ids:
                    total_filters.append(
                        _MH.external_message_id.is_(None)
                        | _MH.external_message_id.not_in(current_external_ids)
                    )
                _res_total = await _sess.execute(
                    _select(_func.count(_MH.id)).where(*total_filters)
                )
                total_msgs_historico = int(_res_total.scalar() or 0)
                _res_previous_outbound = await _sess.execute(
                    _select(_MH.direction, _MH.origem_mensagem)
                    .where(
                        *total_filters,
                        _MH.direction == "outbound",
                    )
                )
                previous_origins = []
                for _direction, _origin in _res_previous_outbound.all():
                    previous_outbound_count += 1
                    normalized_origin = str(_origin or "unknown").strip().lower() or "unknown"
                    previous_origins.append(normalized_origin)
                    if normalized_origin == "ia":
                        previous_ai_outbound_count += 1
                origins_found = sorted(set(previous_origins))
                # Busca as últimas 15 mensagens, da mais antiga para a mais nova
                _res_hist = await _sess.execute(
                    _select(_MH)
                    .where(
                        _MH.channel_conversation_id == conversation_uuid_hist
                        if conversation_uuid_hist
                        else _MH.lead_id == _lead_hist.id
                    )
                    .order_by(_MH.criado_em.desc())
                    .limit(15)
                )
                _msgs_hist = list(reversed(_res_hist.scalars().all()))

                if _msgs_hist:
                    linhas_estado = []
                    historico_curto_estado: list[str] = []
                    for _m in _msgs_hist:
                        texto_limpo_hist = str(_m.texto or "").strip()
                        if not texto_limpo_hist:
                            continue

                        papel_estado = "Assistente" if _m.from_me else "Usuario"
                        entrada_estado = f"{papel_estado}: {texto_limpo_hist}"
                        linhas_estado.append(entrada_estado)
                        historico_curto_estado.append(entrada_estado)

                    historico_longo = formatar_historico_mensagens(_msgs_hist, limite=15)
                    historico_bd_formatado = historico_longo
                    if linhas_estado:
                        mensagens_globais = list(linhas_estado)
                        historico_curto = historico_curto_estado[-5:]
                    print(f"[ENGINE] Histórico PostgreSQL carregado: {len(_msgs_hist)} mensagem(ns).")
                    _conversation_debug_log(f"[ENGINE][DEBUG] Histórico formatado:\n{historico_bd_formatado}")
                else:
                    print("[ENGINE] Lead encontrado, sem histórico anterior no banco.")
            else:
                print("[ENGINE] Lead não encontrado ainda — histórico vazio.")
    except Exception as _e_hist:
        print(f"[ENGINE] Aviso: falha ao carregar histórico do Postgres: {_e_hist}")
    # ────────────────────────────────────────────────────────────────────────────

    # Garante que a última mensagem do usuário do bloco atual esteja no estado,
    # preservando também as últimas respostas da IA vindas do histórico do banco.
    for texto_inbound in textos:
        entrada = f"Usuario: {texto_inbound}".strip()
        if not entrada:
            continue
        ultimo = str(mensagens_globais[-1] if mensagens_globais else "").strip().lower()
        if ultimo == entrada.lower():
            continue
        mensagens_globais.append(entrada)

    historico_curto_log = _formatar_historico_curto_estado(mensagens_globais, limite=5)
    if historico_curto_log:
        _conversation_debug_log(f"[ENGINE][DEBUG] Histórico curto:\n{historico_curto_log}")
    historico_curto = mensagens_globais[-5:] if mensagens_globais else historico_curto

    estado_inicial = {
        "empresa_id": mensagens[0].empresa_id,
        "identificador_origem": mensagens[0].identificador_origem,
        "canal": mensagens[0].canal,
        "conexao_id": mensagens[0].conexao_id,
        "channel": inbound_route.channel.value if inbound_route else None,
        "provider": inbound_route.provider.value if inbound_route else None,
        "connection_id": inbound_route.connection_id if inbound_route else mensagens[0].conexao_id,
        "conversation_id": str(getattr(mensagens[0], "channel_conversation_id", None) or "").strip() or None,
        "message_route": inbound_route.as_state() if inbound_route else None,
        "mensagens": mensagens_globais,
        "historico_bd": historico_bd_formatado,
        "resumo_cliente": resumo_cliente,
        "historico_curto": historico_curto,
        "nome_contato": getattr(mensagens[0], "nome_contato", None),
        "lead_id": lead_id_hist,
        "intencao": [],
        "ferramentas_selecionadas": [],
        "resumos_ferramentas": [],
        "acoes_sistema_pendentes": [],
        "acoes_sistema_executadas": [],
        "acoes_sistema_status": [],
        "handoff_requested": False,
        "resposta_final": None,
        "status_conversa": None,
        "total_msgs_historico": total_msgs_historico,
        "primeiro_contato": total_msgs_historico == 0,
        "first_agent_response": first_agent_response_from_origins(origins_found),
        "previous_outbound_count": previous_outbound_count,
        "previous_ai_outbound_count": previous_ai_outbound_count,
        "origins_found": origins_found,
        "etapa_funil": None,
        "etapas_concluidas": [],
        "objetivo_atual": None,
        "proxima_acao": None,
        "resposta_final_prefixo": None,
        "media_messages": media_messages,
        "has_image": any(item["tipo_mensagem"] == "image" or item["media_type"] == "image" or item["has_image"] for item in media_messages),
        "media_type": next((item["media_type"] for item in media_messages if item.get("media_type")), None),
        "media_url": next((item["media_url"] for item in media_messages if item.get("media_url")), None),
        "media_id": next((item["media_id"] for item in media_messages if item.get("media_id")), None),
        "media_mime_type": next((item["media_mime_type"] for item in media_messages if item.get("media_mime_type")), None),
        "caption": next((item["caption"] for item in media_messages if item.get("caption")), None),
        "image_analysis_available": any(
            bool((item.get("metadata") or {}).get("image_analysis_available")) for item in media_messages
        ),
        "image_analysis": next((item["image_analysis"] for item in media_messages if isinstance(item.get("image_analysis"), dict)), None),
    }

    empresa_autoatendimento = None
    lead_autoatendimento = None
    try:
        from db.database import AsyncSessionLocal
        from db.models import CRMLead, ChannelConversation, Empresa
        from sqlalchemy import select

        empresa_uuid = uuid.UUID(str(mensagens[0].empresa_id))
        async with AsyncSessionLocal() as session:
            result_empresa = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
            empresa_autoatendimento = result_empresa.scalars().first()
            if lead_id_hist:
                try:
                    lead_uuid_auto = uuid.UUID(str(lead_id_hist))
                except (ValueError, TypeError):
                    lead_uuid_auto = None
                if lead_uuid_auto:
                    result_lead_auto = await session.execute(
                        select(CRMLead).where(CRMLead.id == lead_uuid_auto, CRMLead.empresa_id == empresa_uuid)
                    )
                    lead_autoatendimento = result_lead_auto.scalars().first()
            if lead_autoatendimento is None:
                result_lead_auto = await session.execute(
                    select(CRMLead).where(
                        CRMLead.empresa_id == empresa_uuid,
                        CRMLead.telefone_contato == str(mensagens[0].identificador_origem),
                    )
                )
                lead_autoatendimento = result_lead_auto.scalars().first()
            conversation_auto = None
            conversation_id_raw = getattr(mensagens[0], "channel_conversation_id", None)
            if conversation_id_raw:
                try:
                    conversation_uuid = uuid.UUID(str(conversation_id_raw))
                    result_conversation = await session.execute(
                        select(ChannelConversation).where(
                            ChannelConversation.id == conversation_uuid,
                            ChannelConversation.empresa_id == empresa_uuid,
                        )
                    )
                    conversation_auto = result_conversation.scalars().first()
                except (ValueError, TypeError):
                    conversation_auto = None
            if conversation_auto is None and lead_autoatendimento is not None:
                filtros_conversa = [
                    ChannelConversation.empresa_id == empresa_uuid,
                    ChannelConversation.lead_id == lead_autoatendimento.id,
                ]
                if mensagens[0].conexao_id:
                    filtros_conversa.append(ChannelConversation.conexao_id == uuid.UUID(str(mensagens[0].conexao_id)))
                result_conversation = await session.execute(
                    select(ChannelConversation)
                    .where(*filtros_conversa)
                    .order_by(ChannelConversation.atualizado_em.desc())
                    .limit(1)
                )
                conversation_auto = result_conversation.scalars().first()
            ai_gate = await resolve_conversation_ai_state(
                session,
                empresa=empresa_autoatendimento,
                lead=lead_autoatendimento,
                conversation=conversation_auto,
                connection_id=str(mensagens[0].conexao_id or ""),
                provider=(inbound_route.provider.value if inbound_route else getattr(mensagens[0], "provider", None) or mensagens[0].canal),
                phone=str(mensagens[0].identificador_origem),
                trigger=(
                    "customer_inbound_message"
                    if any(
                        str((message.metadata or {}).get("trigger") or "").strip().lower()
                        == "customer_inbound_message"
                        for message in mensagens_validas
                    )
                    else None
                ),
            )
            if ai_gate.state != "active":
                await session.commit()
                await _broadcast_ai_state_update(
                    empresa_id=mensagens[0].empresa_id,
                    lead=lead_autoatendimento,
                    ai_gate=ai_gate,
                )
                _ai_log(
                    "ai.gate.blocked",
                    empresa_id=mensagens[0].empresa_id,
                    lead_id=lead_id_hist,
                    connection_id=mensagens[0].conexao_id,
                    provider=ai_gate.provider,
                    reason_code=ai_gate.reason,
                    state=ai_gate.state,
                )
                logger.info(
                    "[AUTOATENDIMENTO] IA bloqueada pelo gate controlado. empresa_id=%s lead_id=%s state=%s reason=%s",
                    mensagens[0].empresa_id,
                    lead_id_hist,
                    ai_gate.state,
                    ai_gate.reason,
                )
                return
            await _broadcast_ai_state_update(
                empresa_id=mensagens[0].empresa_id,
                lead=lead_autoatendimento,
                ai_gate=ai_gate,
                override={
                    "processing_state": "queued",
                    "processing_label": "IA autorizada",
                    "processing_description": "A IA foi autorizada e entrou na fila de processamento.",
                },
            )
            _ai_log(
                "ai.gate.allowed",
                empresa_id=mensagens[0].empresa_id,
                lead_id=lead_id_hist,
                connection_id=mensagens[0].conexao_id,
                provider=ai_gate.provider,
                reason_code=ai_gate.reason,
                match_method=ai_gate.phone_match_state,
            )
    except Exception as exc:
        logger.error(
            "[AUTOATENDIMENTO] Falha ao carregar empresa para gate. empresa_id=%s erro=%s",
            getattr(mensagens[0], "empresa_id", None),
            sanitize_for_log(str(exc)),
        )
        return

    if is_ai_blocked(lead_autoatendimento):
        _ai_log(
            "ai.processing.blocked_after_gate",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            reason_code="human_or_manual_block",
        )
        logger.info(
            "[AUTOATENDIMENTO] Lead pausado/manual; sequência e IA bloqueadas. empresa_id=%s lead_id=%s",
            mensagens[0].empresa_id,
            lead_id_hist,
        )
        return

    status_openai_auto = str(getattr(empresa_autoatendimento, "status_openai", "") or "").strip().lower()
    if status_openai_auto in {"desativado", "inativo"}:
        logger.info(
            "[AUTOATENDIMENTO] Autoatendimento desativado; sequência e IA bloqueadas. empresa_id=%s lead_id=%s status=%s",
            mensagens[0].empresa_id,
            lead_id_hist,
            status_openai_auto,
        )
        return

    ai_status = get_tenant_ai_status(empresa_autoatendimento)
    if not ai_status.get("enabled"):
        _ai_log(
            "ai.processing.blocked_after_gate",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            reason_code=ai_status.get("reason"),
        )
        logger.info(
            "[AUTOATENDIMENTO] IA não configurada; resposta automática bloqueada. empresa_id=%s lead_id=%s reason=%s",
            mensagens[0].empresa_id,
            lead_id_hist,
            ai_status.get("reason"),
        )
        return

    if lead_id_hist and total_msgs_historico > 0:
        if total_msgs_historico % 10 == 0:
            print(
                "[MEMORIA_BG] Agendando consolidação de resumo "
                f"(total_msgs={total_msgs_historico}, bloco={len(mensagens_validas)})."
            )
            if background_tasks is not None:
                background_tasks.add_task(atualizar_resumo_lead_bg, lead_id_hist, mensagens[0].empresa_id)
            else:
                asyncio.create_task(atualizar_resumo_lead_bg(lead_id_hist, mensagens[0].empresa_id))
    
    timestamp_inicio = await _bouncer_latest_message_version(redis_client, runtime_key)
    in_flight_key = f"inflight:{runtime_key}"
    inflight_token = f"observed:{secrets.token_urlsafe(16)}"
    await redis_client.set(in_flight_key, inflight_token, ex=BOUNCER_INFLIGHT_MARKER_TTL_SECONDS)
    _ai_log(
        "ai.bouncer.inflight_observed",
        empresa_id=mensagens[0].empresa_id,
        lead_id=lead_id_hist,
        connection_id=mensagens[0].conexao_id,
        provider=inbound_route.provider.value if inbound_route else None,
        ttl_seconds=BOUNCER_INFLIGHT_MARKER_TTL_SECONDS,
    )
    _ai_log(
        "ai.lock.acquired",
        empresa_id=mensagens[0].empresa_id,
        lead_id=lead_id_hist,
        connection_id=mensagens[0].conexao_id,
        provider=inbound_route.provider.value if inbound_route else None,
        ttl_seconds=BOUNCER_INFLIGHT_MARKER_TTL_SECONDS,
    )
    
    try:
        await _broadcast_ai_state_update(
            empresa_id=mensagens[0].empresa_id,
            lead=lead_autoatendimento,
            ai_gate=ai_gate,
            override={
                "processing_state": "processing",
                "processing_label": "IA processando",
                "processing_description": "A IA esta preparando uma resposta.",
            },
        )
        print("\n[Agentes] Invocando novo grafo...")
        _ai_log(
            "ai.agent.starting",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=mensagens[0].conexao_id,
            provider=inbound_route.provider.value if inbound_route else None,
        )
        _ai_log(
            "ai.processing.started",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=mensagens[0].conexao_id,
            provider=inbound_route.provider.value if inbound_route else None,
        )
        thread_id = runtime_key
        config = {"configurable": {"thread_id": thread_id}}
        from app.agents.graph import graph

        estado_final = await graph.ainvoke(estado_inicial, config=config)
        _ai_log(
            "ai.agent.completed",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=mensagens[0].conexao_id,
            provider=inbound_route.provider.value if inbound_route else None,
            has_response=bool(estado_final.get("resposta_final")),
            reason_code=_extract_agent_block_reason(estado_final),
            error_class=_extract_agent_error_class(estado_final),
        )
        
        _conversation_debug_log("\n--- ESTADO FINAL DA IA ---")
        _conversation_debug_log(str(estado_final))
        _conversation_debug_log("--------------------------------------\n")
        
        if not await _bouncer_owner_still_current(
            runtime_key=runtime_key,
            empresa_id=mensagens[0].empresa_id,
            connection_id=mensagens[0].conexao_id,
            provider=inbound_route.provider.value if inbound_route else None,
        ):
            print("\n[ENGINE] Descartando resposta gerada - lock do Bouncer nao pertence mais a esta execucao.")
            return

        if await _bouncer_response_superseded(
            redis_client,
            runtime_key,
            timestamp_inicio,
            empresa_id=mensagens[0].empresa_id,
            connection_id=mensagens[0].conexao_id,
            provider=inbound_route.provider.value if inbound_route else None,
        ):
            print("\n[ENGINE] Descartando resposta gerada - nova mensagem chegou durante a geracao.")
            return
    except Exception as e:
        _ai_log(
            "ai.processing.failed",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=mensagens[0].conexao_id,
            provider=inbound_route.provider.value if inbound_route else None,
            error_class=e.__class__.__name__,
        )
        logger.error(
            "[AUTOATENDIMENTO] Erro no grafo; resposta automática não enviada. empresa_id=%s lead_id=%s erro=%s",
            mensagens[0].empresa_id,
            lead_id_hist,
            sanitize_for_log(str(e)),
        )
        await _registrar_falha_operacional_grafo(
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            canal=mensagens[0].canal,
            identificador_origem=mensagens[0].identificador_origem,
        )
        await _broadcast_ai_state_update(
            empresa_id=mensagens[0].empresa_id,
            lead=lead_autoatendimento,
            ai_gate=ai_gate,
            override={
                "processing_state": "failed_model",
                "reason_code": "llm_error",
                "reason": "llm_error",
                "reason_label": "Falha ao gerar resposta",
                "processing_label": "Falha ao gerar resposta",
                "processing_description": "A IA nao conseguiu concluir esta mensagem. O atendimento automatico continua configurado.",
            },
        )
        return
    finally:
        if await redis_client.get(in_flight_key) == inflight_token:
            await redis_client.delete(in_flight_key)
        _ai_log(
            "ai.bouncer.inflight_cleared",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=mensagens[0].conexao_id,
            provider=inbound_route.provider.value if inbound_route else None,
        )
        _ai_log(
            "ai.lock.released",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=mensagens[0].conexao_id,
            provider=inbound_route.provider.value if inbound_route else None,
        )
        
    print("--------------------------------------\n")
    
    from app.services.channel_factory import despachar_mensagem
    
    conexao_id_dispatch = estado_final.get("conexao_id") or estado_final.get("connection_id") or mensagens[0].conexao_id
    canal_dispatch = mensagens[0].canal
    if inbound_route is not None:
        try:
            from app.services.channels.routing import dispatch_canal_for_route, resolve_outbound_route

            outbound_route = resolve_outbound_route(inbound_route, estado_final)
            conexao_id_dispatch = outbound_route.connection_id
            canal_dispatch = dispatch_canal_for_route(outbound_route)
        except Exception as route_exc:
            print(
                "[ROUTING] Resposta bloqueada: tentativa de rerroteamento inconsistente. "
                f"empresa_id={mensagens[0].empresa_id} canal={mensagens[0].canal} "
                f"conexao_id={mensagens[0].conexao_id} erro={sanitize_for_log(str(route_exc))}"
            )
            return
    if estado_inicial.get("first_agent_response"):
        from app.agents.atendimento.first_turn import compose_first_turn_response

        resposta = compose_first_turn_response(
            getattr(empresa_autoatendimento, "mensagem_saudacao", None),
            estado_final.get("first_turn_decision"),
        )
        _ai_log(
            "ai.first_turn.composed",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            has_extension=bool((estado_final.get("first_turn_decision") or {}).get("needs_extension")),
            greeting_chars=len(str(getattr(empresa_autoatendimento, "mensagem_saudacao", None) or "")),
        )
    else:
        resposta = estado_final.get("resposta_final")
    _ai_log(
        "ai.first_turn.trace",
        empresa_id=mensagens[0].empresa_id,
        lead_id=lead_id_hist,
        conversation_id=estado_inicial.get("conversation_id"),
        first_agent_response=bool(estado_inicial.get("first_agent_response")),
        greeting_present=bool(getattr(empresa_autoatendimento, "mensagem_saudacao", None)),
        greeting_hash_prefix=(
            hashlib.sha256(str(getattr(empresa_autoatendimento, "mensagem_saudacao", "")).encode("utf-8")).hexdigest()[:12]
            if getattr(empresa_autoatendimento, "mensagem_saudacao", None)
            else None
        ),
        composer_called=bool(estado_inicial.get("first_agent_response")),
        historical_count=total_msgs_historico,
        previous_outbound_count=previous_outbound_count,
        previous_ai_outbound_count=previous_ai_outbound_count,
        origins_found=origins_found,
        current_persisted_id_count=current_persisted_id_count,
        current_external_id_count=current_external_id_count,
        final_source=("first_turn_composer" if estado_inicial.get("first_agent_response") else "normal_agent_llm"),
    )
    if resposta:
        from app.agents.guards.response_guard import ResponseGuard

        guard_result = ResponseGuard().validate(str(resposta))
        if not guard_result.allowed:
            _ai_log(
                "ai.guard.blocked",
                empresa_id=mensagens[0].empresa_id,
                lead_id=lead_id_hist,
                connection_id=conexao_id_dispatch,
                provider=inbound_route.provider.value if inbound_route else None,
                reason_code="response_guard_blocked",
            )
            print("[ENGINE] ResponseGuard bloqueou resposta; encaminhando para humano sem enviar fallback automático.")
            await _pausar_lead_para_humano(
                empresa_id=mensagens[0].empresa_id,
                lead_id=lead_id_hist,
                telefone=str(mensagens[0].identificador_origem),
                reason="response_guard_blocked",
            )
            if lead_autoatendimento is not None:
                lead_autoatendimento.ia_ativa = False
                lead_autoatendimento.status_atendimento = "AGUARDANDO_HUMANO"
            await _broadcast_ai_state_update(
                empresa_id=mensagens[0].empresa_id,
                lead=lead_autoatendimento,
                ai_gate=ai_gate,
                override={
                    "processing_state": "guard_blocked",
                    "reason_code": "response_guard_blocked",
                    "reason": "response_guard_blocked",
                    "reason_label": "Resposta bloqueada",
                    "processing_label": "Resposta bloqueada",
                    "processing_description": "A resposta automatica foi bloqueada pelos guardas e nao foi enviada.",
                },
            )
            return
        resposta = guard_result.text
        _ai_log(
            "ai.guard.allowed",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=conexao_id_dispatch,
            provider=inbound_route.provider.value if inbound_route else None,
            response_chars=len(str(resposta or "")),
        )
    handoff_pendente = bool(estado_final.get("status_conversa") == "HANDOFF")
    if not resposta and handoff_pendente:
        resposta = (
            "Perfeito! Vou te transferir agora para o time responsável. "
            "Um atendente humano continua com você em instantes."
        )
    if resposta:
        if not await _bouncer_owner_still_current(
            runtime_key=runtime_key,
            empresa_id=mensagens[0].empresa_id,
            connection_id=conexao_id_dispatch,
            provider=inbound_route.provider.value if inbound_route else None,
        ):
            print("\n[ENGINE] Descartando dispatch - lock do Bouncer nao pertence mais a esta execucao.")
            return

        if await _bouncer_response_superseded(
            redis_client,
            runtime_key,
            timestamp_inicio,
            empresa_id=mensagens[0].empresa_id,
            connection_id=conexao_id_dispatch,
            provider=inbound_route.provider.value if inbound_route else None,
        ):
            print("\n[ENGINE] Descartando dispatch - nova mensagem chegou antes do envio.")
            return

        dispatch_allowed, dispatch_block_reason = await _bouncer_dispatch_still_allowed(
            mensagens=mensagens,
            inbound_route=inbound_route,
            lead_id_hist=lead_id_hist,
        )
        if not dispatch_allowed:
            event_name = (
                "ai.bouncer.cancelled_ai_off"
                if dispatch_block_reason == "ai_manual_disabled"
                else "ai.bouncer.cancelled_handoff"
            )
            _ai_log(
                event_name,
                empresa_id=mensagens[0].empresa_id,
                lead_id=lead_id_hist,
                connection_id=conexao_id_dispatch,
                provider=inbound_route.provider.value if inbound_route else None,
                reason_code=dispatch_block_reason,
                runtime_key=runtime_key,
            )
            print("\n[ENGINE] Dispatch automático bloqueado por revalidação operacional.")
            return

        print("\n[Channel Factory] Despachando a resposta final...")
        _ai_log(
            "ai.bouncer.dispatch_started",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=conexao_id_dispatch,
            provider=inbound_route.provider.value if inbound_route else None,
            channel=canal_dispatch,
            runtime_key=runtime_key,
        )
        _ai_log(
            "ai.dispatch.started",
            empresa_id=mensagens[0].empresa_id,
            lead_id=lead_id_hist,
            connection_id=conexao_id_dispatch,
            provider=inbound_route.provider.value if inbound_route else None,
            channel=canal_dispatch,
        )
        logger.warning(
            "ai.outbound.started empresa_id=%s lead_id=%s channel=%s",
            sanitize_for_log(str(mensagens[0].empresa_id or "")),
            sanitize_for_log(str(lead_id_hist or "")),
            sanitize_for_log(str(canal_dispatch or "")),
        )
        
        if mensagens[0].canal == "simulador":
            # Para o simulador, escrever a resposta no Redis para o long-poll do frontend
            from app.services.simulator_session import simulator_response_key

            sim_key = simulator_response_key(
                str(mensagens[0].empresa_id or ""),
                mensagens[0].identificador_origem,
            )
            await redis_client.setex(sim_key, 120, resposta)  # TTL de 2 minutos
            print(f"[SIMULADOR] Resposta gravada no Redis: {sim_key}")
            
            # Salvar histórico mock no banco
            try:
                textos_juntos = "\n".join(textos)
                await save_simulator_history(
                    mensagens[0].empresa_id,
                    mensagens[0].identificador_origem,
                    textos_juntos,
                    resposta,
                    mensagens[0].conexao_id,
                )
            except Exception as e:
                print(f"[SIMULADOR] Aviso: falha ao salvar histórico simulador: {sanitize_for_log(str(e))}")
        else:
            try:
                dispatch_result = await despachar_mensagem(
                    canal=canal_dispatch,
                    identificador_origem=mensagens[0].identificador_origem,
                    texto=resposta,
                    conexao_id=conexao_id_dispatch,
                    empresa_id=mensagens[0].empresa_id,
                    segment_whatsapp=True,
                    return_details=True,
                )
            except Exception as e:
                _ai_log(
                    "ai.dispatch.failed",
                    empresa_id=mensagens[0].empresa_id,
                    lead_id=lead_id_hist,
                    connection_id=conexao_id_dispatch,
                    provider=inbound_route.provider.value if inbound_route else None,
                    channel=canal_dispatch,
                    error_class=e.__class__.__name__,
                )
                print(
                    f"[ENGINE] Falha no envio outbound da IA. "
                    f"empresa_id={mensagens[0].empresa_id} canal={canal_dispatch} "
                    f"identificador='{mensagens[0].identificador_origem}' conexao_id={conexao_id_dispatch}"
                )
                print(f"Erro real no outbound: {sanitize_for_log(str(e))}")
                logger.warning(
                    "ai.outbound.failed empresa_id=%s lead_id=%s channel=%s error_class=%s",
                    sanitize_for_log(str(mensagens[0].empresa_id or "")),
                    sanitize_for_log(str(lead_id_hist or "")),
                    sanitize_for_log(str(canal_dispatch or "")),
                    e.__class__.__name__,
                )
                return

            enviado = dispatch_result.get("sent") if isinstance(dispatch_result, dict) else bool(dispatch_result)
            external_message_id = None
            if isinstance(dispatch_result, dict):
                ids = dispatch_result.get("external_message_ids") or []
                external_message_id = ids[0] if ids else None
            if not enviado:
                _ai_log(
                    "ai.dispatch.failed",
                    empresa_id=mensagens[0].empresa_id,
                    lead_id=lead_id_hist,
                    connection_id=conexao_id_dispatch,
                    provider=inbound_route.provider.value if inbound_route else None,
                    channel=canal_dispatch,
                    reason_code="dispatcher_returned_false",
                )
                print(
                    f"[ENGINE] Falha no envio outbound da IA. "
                    f"empresa_id={mensagens[0].empresa_id} canal={canal_dispatch} "
                    f"identificador='{mensagens[0].identificador_origem}' conexao_id={conexao_id_dispatch}"
                )
                logger.warning(
                    "ai.outbound.failed empresa_id=%s lead_id=%s channel=%s reason=dispatcher_returned_false",
                    sanitize_for_log(str(mensagens[0].empresa_id or "")),
                    sanitize_for_log(str(lead_id_hist or "")),
                    sanitize_for_log(str(canal_dispatch or "")),
                )
                return

            try:
                # 3. Trava de Transacao do Banco (O Check Final)
                # Re-verificar timestamp ANTES de commitar no banco
                if await _bouncer_response_superseded(
                    redis_client,
                    runtime_key,
                    timestamp_inicio,
                    empresa_id=mensagens[0].empresa_id,
                    connection_id=conexao_id_dispatch,
                    provider=inbound_route.provider.value if inbound_route else None,
                ):
                    print("\n[ENGINE] ATENÇÃO: Descartando commit no banco - nova mensagem chegou no último segundo.")
                    return

                telefone = str(mensagens[0].identificador_origem)
                mensagem_payload = await _salvar_historico_saida_ia(
                    empresa_id=mensagens[0].empresa_id,
                    telefone=telefone,
                    resposta=resposta,
                    conexao_id=conexao_id_dispatch or mensagens[0].conexao_id,
                    external_message_id=external_message_id,
                    lead_id=lead_id_hist,
                    conversation_id=getattr(mensagens[0], "channel_conversation_id", None),
                )
                if mensagem_payload:
                    logger.warning(
                        "[AI_BROADCAST_CALL_STARTED] empresa_id=%s event_type=nova_mensagem_outbound",
                        mensagens[0].empresa_id,
                    )
                    await manager.broadcast_to_empresa(
                        mensagens[0].empresa_id,
                        {
                            "type": "inbox.message.created",
                            "tipo_evento": "nova_mensagem_outbound",
                            "empresa_id": str(mensagens[0].empresa_id),
                            "lead_id": str(lead_id_hist or ""),
                            "conversation_id": mensagem_payload.get("conversation_id"),
                            "telefone": telefone,
                            "direction": "outbound",
                            "created_at": mensagem_payload.get("criado_em"),
                            "last_message_preview": str(mensagem_payload.get("texto") or "")[:240],
                            "last_message_at": mensagem_payload.get("criado_em"),
                            "mensagem": mensagem_payload,
                        },
                    )
                    logger.warning(
                        "[AI_BROADCAST_CALL_FINISHED] empresa_id=%s event_type=nova_mensagem_outbound",
                        mensagens[0].empresa_id,
                    )
                    _ai_log(
                        "ai.dispatch.completed",
                        empresa_id=mensagens[0].empresa_id,
                        lead_id=lead_id_hist,
                        connection_id=conexao_id_dispatch,
                        provider=inbound_route.provider.value if inbound_route else None,
                        channel=canal_dispatch,
                    )
                    logger.warning(
                        "ai.outbound.finished empresa_id=%s lead_id=%s channel=%s",
                        sanitize_for_log(str(mensagens[0].empresa_id or "")),
                        sanitize_for_log(str(lead_id_hist or "")),
                        sanitize_for_log(str(canal_dispatch or "")),
                    )
                    _ai_log(
                        "ai.processing.completed",
                        empresa_id=mensagens[0].empresa_id,
                        lead_id=lead_id_hist,
                        connection_id=conexao_id_dispatch,
                        provider=inbound_route.provider.value if inbound_route else None,
                        channel=canal_dispatch,
                    )
                    await _broadcast_ai_state_update(
                        empresa_id=mensagens[0].empresa_id,
                        lead=lead_autoatendimento,
                        ai_gate=ai_gate,
                        override={
                            "processing_state": "completed",
                            "processing_label": "Resposta enviada",
                            "processing_description": "A resposta automatica foi processada pelo provider configurado.",
                        },
                    )
                    if estado_final.get("status_conversa") == "HANDOFF":
                        try:
                            from sqlalchemy import update
                            from db.database import AsyncSessionLocal
                            from db.models import CRMLead
                            from datetime import datetime, timedelta

                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(CRMLead)
                                    .where(
                                        CRMLead.telefone_contato == str(mensagens[0].identificador_origem),
                                        CRMLead.empresa_id == uuid.UUID(mensagens[0].empresa_id)
                                    )
                                    .values(
                                        bot_pausado_ate=datetime.utcnow() + timedelta(hours=24),
                                        status_atendimento="AGUARDANDO_HUMANO"
                                    )
                                )
                                await session.commit()
                                print(f"[ENGINE] Bot efetivamente pausado por 24h para {mensagens[0].identificador_origem}.")
                        except Exception as ep:
                            print(f"Erro ao pausar bot no webhook: {sanitize_for_log(str(ep))}")
            except Exception as e:
                print(f"Erro ao salvar histórico do Grafo (Webhook): {sanitize_for_log(str(e))}")
            
    else:
        block_reason = _extract_agent_block_reason(estado_final)
        if block_reason:
            operational_error = _extract_agent_operational_error(estado_final)
            error_class = _extract_agent_error_class(estado_final)
            _ai_log(
                "ai.response.blocked",
                empresa_id=mensagens[0].empresa_id,
                lead_id=lead_id_hist,
                connection_id=conexao_id_dispatch,
                provider=inbound_route.provider.value if inbound_route else None,
                reason_code=block_reason,
                error_class=error_class,
                operational_error=operational_error,
            )
            _ai_log(
                "ai.processing.failed",
                empresa_id=mensagens[0].empresa_id,
                lead_id=lead_id_hist,
                connection_id=conexao_id_dispatch,
                provider=inbound_route.provider.value if inbound_route else None,
                reason_code=block_reason,
                error_class=error_class,
            )
            logger.warning(
                "[AUTOATENDIMENTO] Grafo sem resposta_final. empresa_id=%s lead_id=%s reason=%s",
                mensagens[0].empresa_id,
                lead_id_hist,
                sanitize_for_log(block_reason),
            )
            technical_failure_reasons = {
                "tenant_llm_provider_error",
                "tenant_llm_unavailable",
                "tenant_llm_empty_response",
                "tenant_tool_planner_error",
                "agent_operational_error",
                "anamnese_tool_error",
            }
            technical_failure_reasons = technical_failure_reasons | {"fiscal_validation_failed"}
            if block_reason in technical_failure_reasons:
                await _registrar_falha_operacional_grafo(
                    empresa_id=mensagens[0].empresa_id,
                    lead_id=lead_id_hist,
                    canal=mensagens[0].canal,
                    identificador_origem=mensagens[0].identificador_origem,
                )
                failed_label = "Falha ao consultar modelo"
                failed_state = "failed_model"
                if block_reason == "fiscal_validation_failed":
                    failed_label = "Resposta fiscal precisa de revisão"
                    failed_state = "guard_blocked"
                elif block_reason == "tenant_tool_planner_error":
                    failed_label = "Falha ao iniciar IA"
                    failed_state = "failed_start"
                elif block_reason in technical_failure_reasons:
                    failed_label = "Falha ao gerar resposta"
                    failed_state = "failed_start"
                await _broadcast_ai_state_update(
                    empresa_id=mensagens[0].empresa_id,
                    lead=lead_autoatendimento,
                    ai_gate=ai_gate,
                    override={
                        "processing_state": failed_state,
                        "reason_code": block_reason,
                        "reason": block_reason,
                        "reason_label": failed_label,
                        "processing_label": failed_label,
                        "processing_description": (
                            "A resposta nao passou pelas validacoes necessarias e nao foi enviada."
                            if block_reason == "fiscal_validation_failed"
                            else "A IA nao conseguiu concluir esta mensagem. O atendimento automatico continua configurado."
                        ),
                    },
                )
        print("\n[Aviso] Nenhuma 'resposta_final' gerada pelo grafo.")

    # ── Motor de Follow-up legado (Nível 1 / Nível 2) — DEPRECADO ─────────────
    # Substituído pelo módulo de Cadências (tabela `config_followups`) executado
    # pelo worker `app/services/followup_service.py`. Este bloco agora só limpa
    # chaves Redis residuais de execuções anteriores para não deixar lixo.
    # ──────────────────────────────────────────────────────────────────────────
    try:
        canal_orig = mensagens[0].canal
        id_orig = mensagens[0].identificador_origem
        await redis_client.delete(f"followup_ativo:{canal_orig}:{id_orig}")
        await redis_client.delete(f"followup_nivel:{canal_orig}:{id_orig}")
    except Exception as _e_cleanup_legacy:
        print(f"[Follow-up Legacy Cleanup] Falha ao limpar chaves Redis residuais: {sanitize_for_log(str(_e_cleanup_legacy))}")
    # ──────────────────────────────────────────────────────────────────────────




async def processar_bloco_mensagens(
    mensagens: List[StandardMessage], background_tasks: BackgroundTasks | None = None
):
    lifecycle = _ProcessingLifecycle()
    token = _PROCESSING_LIFECYCLE.set(lifecycle)
    try:
        return await _processar_bloco_mensagens_impl(mensagens, background_tasks=background_tasks)
    except Exception as exc:
        lifecycle.error_class = exc.__class__.__name__
        raise
    finally:
        try:
            await lifecycle.ensure_terminal()
        finally:
            _PROCESSING_LIFECYCLE.reset(token)


async def handle_debouncer(msg: StandardMessage, background_tasks: BackgroundTasks | None = None):
    """
    Lógica principal do Debouncer baseada em Redis Sliding Window com Atomic Rename.

    O lock cobre também mensagens que chegam durante o processamento. Sem esse
    loop, uma mensagem recebida enquanto o lote anterior já foi renomeado podia
    ficar em `queue:*` e virar uma segunda resposta depois, ou ser abandonada
    por `lock_denied`.
    """
    from app.api.main import redis_client
    import time
    
    from app.services.channels.routing import runtime_key_from_standard_message

    lead_id = runtime_key_from_standard_message(msg)
    _ai_log(
        "ai.bouncer.scheduled",
        empresa_id=getattr(msg, "empresa_id", None),
        connection_id=getattr(msg, "conexao_id", None),
        provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
        channel=getattr(msg, "canal", None),
        runtime_key=lead_id,
    )
    _ai_log(
        "ai.debounce.scheduled",
        empresa_id=getattr(msg, "empresa_id", None),
        connection_id=getattr(msg, "conexao_id", None),
        provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
        channel=getattr(msg, "canal", None),
        runtime_key=lead_id,
    )
    
    debounce_seconds = BOUNCER_DEBOUNCE_SECONDS
    lock_ttl_seconds = BOUNCER_LOCK_TTL_SECONDS

    # 1. Chegada
    await redis_client.rpush(f"queue:{lead_id}", msg.model_dump_json())
    agora = time.time()
    await redis_client.set(_bouncer_last_message_key(lead_id), agora)
    
    # 2. Espera (Debounce)
    await asyncio.sleep(debounce_seconds)
    
    # 3. Verificação de Sobrevivência
    ultimo_registro = await _bouncer_latest_message_version(redis_client, lead_id)
    if ultimo_registro and float(ultimo_registro) > agora:
        _ai_log(
            "ai.bouncer.replaced",
            empresa_id=getattr(msg, "empresa_id", None),
            connection_id=getattr(msg, "conexao_id", None),
            provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
            runtime_key=lead_id,
        )
        _ai_log(
            "ai.debounce.replaced",
            empresa_id=getattr(msg, "empresa_id", None),
            connection_id=getattr(msg, "conexao_id", None),
            provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
            runtime_key=lead_id,
        )
        return
        
    # 4. Lock de Execução (Single Winner)
    lock_key = f"lock:{lead_id}"
    lock_token = secrets.token_urlsafe(32)
    lock = await redis_client.set(lock_key, lock_token, nx=True, ex=lock_ttl_seconds)
    if not lock:
        _ai_log(
            "ai.bouncer.lock_denied",
            empresa_id=getattr(msg, "empresa_id", None),
            connection_id=getattr(msg, "conexao_id", None),
            provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
            runtime_key=lead_id,
        )
        _ai_log(
            "ai.debounce.lock_denied",
            empresa_id=getattr(msg, "empresa_id", None),
            connection_id=getattr(msg, "conexao_id", None),
            provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
            runtime_key=lead_id,
        )
        return

    _ai_log(
        "ai.bouncer.lock_acquired",
        empresa_id=getattr(msg, "empresa_id", None),
        connection_id=getattr(msg, "conexao_id", None),
        provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
        runtime_key=lead_id,
        ttl_seconds=lock_ttl_seconds,
        renew_interval_seconds=BOUNCER_LOCK_RENEW_INTERVAL_SECONDS,
    )
    heartbeat = _BouncerLockHeartbeat(
        redis_client,
        lock_key=lock_key,
        owner_token=lock_token,
        ttl_seconds=lock_ttl_seconds,
        renew_interval_seconds=BOUNCER_LOCK_RENEW_INTERVAL_SECONDS,
        runtime_key=lead_id,
        empresa_id=getattr(msg, "empresa_id", None),
        connection_id=getattr(msg, "conexao_id", None),
        provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
    )
    heartbeat.start()
    try:
        while True:
            if not await heartbeat.renew_once():
                _ai_log(
                    "ai.bouncer.lock_lost",
                    empresa_id=getattr(msg, "empresa_id", None),
                    connection_id=getattr(msg, "conexao_id", None),
                    provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
                    runtime_key=lead_id,
                )
                return
            quiet_started = time.time()
            ultimo_registro = await _bouncer_latest_message_version(redis_client, lead_id)
            if ultimo_registro and float(ultimo_registro) > quiet_started - debounce_seconds:
                await asyncio.sleep(debounce_seconds)
                continue

            queue_len = await redis_client.llen(f"queue:{lead_id}")
            if not queue_len:
                return

            # 5. Atomic Rename (Pulo do Gato)
            batch_key = f"processando:{lead_id}:{time.time()}"
            try:
                await redis_client.rename(f"queue:{lead_id}", batch_key)
            except Exception:
                _ai_log(
                    "ai.debounce.failed",
                    empresa_id=getattr(msg, "empresa_id", None),
                    connection_id=getattr(msg, "conexao_id", None),
                    provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
                    runtime_key=lead_id,
                    error_class="QueueRenameFailed",
                )
                return

            # 6. Processamento e Limpeza
            try:
                mensagens_json = await redis_client.lrange(batch_key, 0, -1)
                mensagens = [StandardMessage.model_validate_json(m) for m in mensagens_json]
                if mensagens:
                    _ai_log(
                        "ai.bouncer.executing",
                        empresa_id=getattr(msg, "empresa_id", None),
                        connection_id=getattr(msg, "conexao_id", None),
                        provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
                        runtime_key=lead_id,
                        message_count=len(mensagens),
                    )
                    _ai_log(
                        "ai.debounce.executing",
                        empresa_id=getattr(msg, "empresa_id", None),
                        connection_id=getattr(msg, "conexao_id", None),
                        provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
                        runtime_key=lead_id,
                        message_count=len(mensagens),
                    )
                    lock_context_token = _BOUNCER_LOCK_CONTEXT.set(heartbeat)
                    try:
                        await processar_bloco_mensagens(mensagens, background_tasks=background_tasks)
                    finally:
                        _BOUNCER_LOCK_CONTEXT.reset(lock_context_token)
                    _ai_log(
                        "ai.debounce.completed",
                        empresa_id=getattr(msg, "empresa_id", None),
                        connection_id=getattr(msg, "conexao_id", None),
                        provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
                        runtime_key=lead_id,
                        message_count=len(mensagens),
                    )
            finally:
                await redis_client.delete(batch_key)

            # Se uma nova mensagem chegou enquanto a IA processava, o mesmo
            # lock aguarda nova janela de quietude e processa o próximo lote.
            if not await redis_client.llen(f"queue:{lead_id}"):
                return
            _ai_log(
                "ai.bouncer.next_batch",
                empresa_id=getattr(msg, "empresa_id", None),
                connection_id=getattr(msg, "conexao_id", None),
                provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
                runtime_key=lead_id,
            )
            _ai_log(
                "ai.debounce.next_batch_detected",
                empresa_id=getattr(msg, "empresa_id", None),
                connection_id=getattr(msg, "conexao_id", None),
                provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
                runtime_key=lead_id,
            )
            await asyncio.sleep(debounce_seconds)
    except Exception as exc:
        _ai_log(
            "ai.debounce.failed",
            empresa_id=getattr(msg, "empresa_id", None),
            connection_id=getattr(msg, "conexao_id", None),
            provider=getattr(msg, "provider", None) or getattr(msg, "canal", None),
            runtime_key=lead_id,
            error_class=exc.__class__.__name__,
        )
        raise
    finally:
        await heartbeat.stop()
        await _bouncer_release_lock(redis_client, lock_key, lock_token)
