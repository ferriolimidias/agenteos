from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import is_ai_blocked
from app.core.llm_factory import get_llm_for_tenant
from app.services.channel_factory import despachar_mensagem
from db.database import AsyncSessionLocal
from db.models import CRMLead, ConfigFollowUp, Especialista, LeadFollowUpLog, MensagemHistorico, TagCRM


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
                f"Objetivo do follow-up (definido pelo cliente): {str(config.objetivo_prompt or '').strip()}\n"
                f"Nome do lead: {str(lead.nome_contato or '').strip() or 'Cliente'}\n"
                f"Histórico recente:\n{historico_txt}",
            ),
        ]
    )
    return str(getattr(resposta, "content", "") or "").strip()


async def processar_followups_pendentes() -> dict[str, int]:
    """
    Varre follow-ups ativos por empresa e dispara para leads elegíveis:
    - sem interação recente no intervalo da cadência
    - sem log prévio para aquele passo
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
        async with AsyncSessionLocal() as session:
            result_configs = await session.execute(
                select(ConfigFollowUp).where(ConfigFollowUp.ativo.is_(True))
            )
            configs = result_configs.scalars().all()

            for cfg in configs:
                try:
                    empresa_id = cfg.empresa_id
                    gatilho = int(getattr(cfg, "tempo_gatilho_minutos", 0) or 0)
                    if gatilho <= 0:
                        ignorados += 1
                        continue

                    limite = now - timedelta(minutes=gatilho)
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

                            result_ultima_msg = await session.execute(
                                select(MensagemHistorico)
                                .where(MensagemHistorico.lead_id == lead.id)
                                .order_by(MensagemHistorico.criado_em.desc())
                                .limit(1)
                            )
                            ultima_msg = result_ultima_msg.scalars().first()
                            if not ultima_msg or not getattr(ultima_msg, "criado_em", None):
                                ignorados += 1
                                continue
                            if ultima_msg.criado_em > limite:
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

