from __future__ import annotations

import asyncio
import unicodedata
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import is_ai_blocked
from app.core.llm_factory import get_llm_for_tenant
from app.core.prompt_placeholders import substituir_placeholders_nome_lead_em_texto
from app.services.channel_factory import despachar_mensagem
from db.database import AsyncSessionLocal
from db.models import (
    CRMLead,
    CRMEtapa,
    ConfigFollowUp,
    Empresa,
    Especialista,
    LeadFollowUpLog,
    MensagemHistorico,
    TagCRM,
)

# Nomes de etapa que encerram a cadência (comparação normalizada, sem acento).
_ETAPAS_NOME_FINAL = frozenset(
    {
        "fechamento",
        "ganho",
        "venda_realizada",
    }
)


async def _gerar_texto_followup(
    *,
    session: AsyncSession,
    empresa_id: uuid.UUID,
    lead: CRMLead,
    config: ConfigFollowUp,
) -> str:
    result_especialista = await session.execute(
        select(Especialista).where(
            Especialista.empresa_id == empresa_id,
            Especialista.nome == "especialista_followup",
            Especialista.ativo.is_(True),
        )
    )
    especialista = result_especialista.scalars().first()
    prompt_base = str(getattr(especialista, "prompt_sistema", "") or "").strip()
    if not prompt_base:
        prompt_base = "Você é um especialista em follow-up. Seja breve, educado e contextual."
    nome_lead = str(lead.nome_contato or "").strip()
    prompt_base = substituir_placeholders_nome_lead_em_texto(prompt_base, nome_lead)

    result_historico = await session.execute(
        select(MensagemHistorico)
        .where(MensagemHistorico.lead_id == lead.id)
        .order_by(MensagemHistorico.criado_em.desc())
        .limit(6)
    )
    historico = list(reversed(result_historico.scalars().all()))
    linhas = []
    for msg in historico:
        papel = "Assistente" if bool(msg.from_me) else "Cliente"
        linhas.append(f"{papel}: {str(msg.texto or '').strip()}")
    historico_txt = "\n".join(linhas).strip() or "(sem histórico recente)"

    llm = await get_llm_for_tenant(str(empresa_id), session, str(getattr(especialista, "modelo_llm", "") or getattr(especialista, "modelo_ia", "") or "gpt-4o-mini"))
    resposta = await llm.ainvoke(
        [
            (
                "system",
                f"{prompt_base}\n\n"
                "Você está executando um follow-up automático de cadência. "
                "Gere no máximo 2 frases, tom natural, sem parecer robótico, sem citar regras internas.",
            ),
            (
                "user",
                f"Objetivo do follow-up (definido pelo cliente): {substituir_placeholders_nome_lead_em_texto(str(config.objetivo_prompt or '').strip(), nome_lead)}\n"
                f"Nome do lead: {nome_lead or 'Cliente'}\n"
                f"Histórico recente:\n{historico_txt}",
            ),
        ]
    )
    return str(getattr(resposta, "content", "") or "").strip()


def _normalizar_chave_crm(texto: str | None) -> str:
    s = str(texto or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("-", "_").replace(" ", "_")
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def _tag_nome_indica_venda_fechada(nome: str | None) -> bool:
    chave = _normalizar_chave_crm(nome)
    return chave == "venda_fechada" or chave.endswith("_venda_fechada")


def _etapa_indica_final(nome: str | None, tipo: str | None) -> bool:
    nome_k = _normalizar_chave_crm(nome)
    tipo_k = _normalizar_chave_crm(tipo)
    if nome_k in _ETAPAS_NOME_FINAL:
        return True
    if tipo_k and ("fechamento" in tipo_k or tipo_k in {"fechado", "fechamento", "fechada"}):
        return True
    if nome_k and any(
        fragmento in nome_k for fragmento in ("fechado", "concluido", "ganho", "venda_realizada")
    ):
        return True
    return False


async def _lead_tem_tag_venda_fechada(
    session: AsyncSession,
    lead: CRMLead,
    empresa_id: uuid.UUID,
) -> bool:
    tags_raw = lead.tags if isinstance(lead.tags, list) else []
    tag_uuids: list[uuid.UUID] = []

    for raw in tags_raw:
        texto = str(raw or "").strip()
        if not texto:
            continue
        if _tag_nome_indica_venda_fechada(texto):
            return True
        try:
            tag_uuids.append(uuid.UUID(texto))
        except (ValueError, TypeError):
            continue

    if not tag_uuids:
        return False

    result = await session.execute(
        select(TagCRM.nome, TagCRM.acao_fechamento).where(
            TagCRM.empresa_id == empresa_id,
            TagCRM.id.in_(tag_uuids),
        )
    )
    for nome, acao_fechamento in result.all():
        if bool(acao_fechamento) or _tag_nome_indica_venda_fechada(str(nome or "")):
            return True
    return False


async def _lead_em_etapa_final(session: AsyncSession, lead: CRMLead) -> bool:
    if not lead.etapa_id:
        return False
    result = await session.execute(
        select(CRMEtapa.nome, CRMEtapa.tipo).where(CRMEtapa.id == lead.etapa_id)
    )
    row = result.first()
    if not row:
        return False
    nome, tipo = row
    return _etapa_indica_final(str(nome or ""), str(tipo or "") if tipo is not None else None)


async def _motivo_bloqueio_pos_venda_ou_etapa_final(
    session: AsyncSession,
    lead: CRMLead,
    empresa_id: uuid.UUID,
) -> str | None:
    """
    Trava pós-venda: link de pagamento + tag/etapa de fechamento → sem follow-up.
    Avaliada antes do relógio de gatilho.
    """
    if await _lead_tem_tag_venda_fechada(session, lead, empresa_id):
        return "venda_fechada_ou_etapa_final"
    if await _lead_em_etapa_final(session, lead):
        return "venda_fechada_ou_etapa_final"
    return None


async def _buscar_ultima_mensagem_historico(
    session: AsyncSession,
    lead_id: uuid.UUID,
) -> MensagemHistorico | None:
    result = await session.execute(
        select(MensagemHistorico)
        .where(MensagemHistorico.lead_id == lead_id)
        .order_by(MensagemHistorico.criado_em.desc())
        .limit(1)
    )
    return result.scalars().first()


def _motivo_inelegibilidade_followup(
    ultima_msg: MensagemHistorico | None,
    *,
    gatilho_minutos: int,
    agora: datetime,
) -> str | None:
    """
    Regras de disparo (cadência inteligente):
    1. A última mensagem do histórico deve ser outbound (`from_me=True`) — bot ou humano
       no painel; o lead ficou no vácuo.
    2. Se a última for do lead (`from_me=False`), a conversa está ativa → não dispara.
    3. O relógio do gatilho conta a partir do `criado_em` dessa última outbound (não da
       criação do lead nem de mensagens antigas do cliente).
    """
    if not ultima_msg or not getattr(ultima_msg, "criado_em", None):
        return "sem_historico"

    if not bool(getattr(ultima_msg, "from_me", False)):
        return "lead_respondeu_ou_conversa_ativa"

    limite = agora - timedelta(minutes=gatilho_minutos)
    if ultima_msg.criado_em > limite:
        return "aguardando_gatilho"

    return None


async def processar_followups_pendentes() -> dict[str, int]:
    """
    Varre follow-ups ativos por empresa e dispara para leads elegíveis:
    - sem tag/etapa de venda fechada (ex.: `venda_fechada`, etapa Fechamento/Ganho)
    - última mensagem no histórico é outbound (`from_me=True`) e o gatilho já passou
    - sem log prévio para aquele passo da cadência
    - IA não bloqueada para o lead
    """
    from app.api.main import redis_client

    lock_key = "lock:followup_worker"
    lock_ttl_seconds = 45
    lock_token = str(uuid.uuid4())
    lock_acquired = False
    heartbeat_task = None

    if redis_client is not None:
        try:
            lock_acquired = bool(
                await redis_client.set(lock_key, lock_token, ex=lock_ttl_seconds, nx=True)
            )
        except Exception:
            lock_acquired = True
    else:
        lock_acquired = True

    if not lock_acquired:
        return {"enviados": 0, "ignorados": 0, "erros": 0}

    async def _heartbeat_renovar_lock() -> None:
        while True:
            try:
                await asyncio.sleep(15)
                if redis_client is None:
                    continue
                valor_lock = await redis_client.get(lock_key)
                if valor_lock != lock_token:
                    return
                await redis_client.expire(lock_key, lock_ttl_seconds)
            except asyncio.CancelledError:
                return
            except Exception:
                return

    if redis_client is not None:
        heartbeat_task = asyncio.create_task(_heartbeat_renovar_lock())

    enviados = 0
    ignorados = 0
    erros = 0

    try:
        now = datetime.utcnow()
        # Cache por execução: evita reler a mesma Empresa N vezes quando ela tem
        # várias cadências ativas. Chave = empresa_id (UUID); valor = bool da flag.
        empresa_followup_cache: dict[uuid.UUID, bool] = {}

        async def _empresa_tem_followup_ativo(
            session: AsyncSession, empresa_id: uuid.UUID
        ) -> bool:
            if empresa_id in empresa_followup_cache:
                return empresa_followup_cache[empresa_id]
            result_emp = await session.execute(
                select(Empresa.followup_ativo).where(Empresa.id == empresa_id)
            )
            ativo = bool(result_emp.scalar() or False)
            empresa_followup_cache[empresa_id] = ativo
            return ativo

        async with AsyncSessionLocal() as session:
            result_configs = await session.execute(
                select(ConfigFollowUp).where(ConfigFollowUp.ativo.is_(True))
            )
            configs = result_configs.scalars().all()

            for cfg in configs:
                try:
                    empresa_id = cfg.empresa_id

                    # Chave-mestra global: se a empresa desligou follow-up no
                    # painel ("Ativar Reengajamento"), pulamos TODAS as cadências
                    # dela mesmo que `ConfigFollowUp.ativo` esteja true.
                    if not await _empresa_tem_followup_ativo(session, empresa_id):
                        ignorados += 1
                        continue

                    gatilho = int(getattr(cfg, "tempo_gatilho_minutos", 0) or 0)
                    if gatilho <= 0:
                        ignorados += 1
                        continue

                    result_leads = await session.execute(
                        select(CRMLead).where(CRMLead.empresa_id == empresa_id)
                    )
                    leads = result_leads.scalars().all()

                    for lead in leads:
                        try:
                            if not str(getattr(lead, "telefone_contato", "") or "").strip():
                                ignorados += 1
                                continue
                            if is_ai_blocked(lead, now=now):
                                ignorados += 1
                                continue

                            result_log_existente = await session.execute(
                                select(LeadFollowUpLog).where(
                                    LeadFollowUpLog.lead_id == lead.id,
                                    LeadFollowUpLog.config_followup_id == cfg.id,
                                )
                            )
                            if result_log_existente.scalars().first():
                                ignorados += 1
                                continue

                            motivo_pos_venda = await _motivo_bloqueio_pos_venda_ou_etapa_final(
                                session, lead, empresa_id
                            )
                            if motivo_pos_venda:
                                ignorados += 1
                                continue

                            ultima_msg = await _buscar_ultima_mensagem_historico(session, lead.id)
                            motivo_skip = _motivo_inelegibilidade_followup(
                                ultima_msg,
                                gatilho_minutos=gatilho,
                                agora=now,
                            )
                            if motivo_skip:
                                ignorados += 1
                                continue

                            texto = await _gerar_texto_followup(
                                session=session,
                                empresa_id=empresa_id,
                                lead=lead,
                                config=cfg,
                            )
                            if not texto:
                                ignorados += 1
                                continue

                            ok = await despachar_mensagem(
                                canal="evolution",
                                identificador_origem=str(lead.telefone_contato),
                                texto=texto,
                                empresa_id=str(empresa_id),
                            )
                            if not ok:
                                erros += 1
                                continue

                            session.add(
                                MensagemHistorico(
                                    lead_id=lead.id,
                                    conexao_id=None,
                                    texto=texto,
                                    from_me=True,
                                    tipo_mensagem="text",
                                )
                            )
                            session.add(
                                LeadFollowUpLog(
                                    lead_id=lead.id,
                                    config_followup_id=cfg.id,
                                    data_envio=now,
                                    status_envio="enviado",
                                )
                            )

                            if getattr(cfg, "tag_aplicar_final", None):
                                tag_id = str(cfg.tag_aplicar_final)
                                tags_atuais = lead.tags if isinstance(lead.tags, list) else []
                                tags_final = [str(t).strip() for t in tags_atuais if str(t).strip()]
                                if tag_id not in tags_final:
                                    result_tag = await session.execute(
                                        select(TagCRM).where(
                                            and_(
                                                TagCRM.id == uuid.UUID(tag_id),
                                                TagCRM.empresa_id == empresa_id,
                                            )
                                        )
                                    )
                                    if result_tag.scalars().first():
                                        tags_final.append(tag_id)
                                        lead.tags = tags_final

                            enviados += 1
                        except Exception:
                            erros += 1
                            continue
                except Exception:
                    erros += 1
                    continue

            await session.commit()

        if redis_client is not None:
            try:
                hoje = datetime.utcnow().strftime("%Y-%m-%d")
                key_leads_hoje = f"metrics:followup:leads_processados:{hoje}"
                key_erros_recentes = "metrics:followup:erros_recentes"
                key_ultima_execucao = "metrics:followup:ultima_execucao"

                if enviados > 0:
                    await redis_client.incrby(key_leads_hoje, int(enviados))
                    await redis_client.expire(key_leads_hoje, 60 * 60 * 24 * 3)
                await redis_client.set(key_erros_recentes, int(erros), ex=60 * 60 * 24)
                await redis_client.set(key_ultima_execucao, datetime.utcnow().isoformat(), ex=60 * 60 * 24 * 3)
            except Exception:
                pass

        return {"enviados": enviados, "ignorados": ignorados, "erros": erros}
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if redis_client is not None:
            try:
                valor_lock = await redis_client.get(lock_key)
                if valor_lock == lock_token:
                    await redis_client.delete(lock_key)
            except Exception:
                pass

