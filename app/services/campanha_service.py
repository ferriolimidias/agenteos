import asyncio
from datetime import datetime
import random
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.services.channel_factory import despachar_mensagem
from app.services.operational_identity import ensure_operational_conversation, sanitize_address
from app.services.outbox_service import (
    deterministic_idempotency_key,
    dispatch_outbox_now,
    enqueue_message,
    upsert_campaign_recipient,
)
from db.database import AsyncSessionLocal
from db.models import CampanhaDisparo, CampanhaDisparoStatus, CRMLead, Empresa, MensagemHistorico, TemplateMensagem


def renderizar_template_mensagem(texto_template: str, lead: CRMLead) -> str:
    dados_adicionais = lead.dados_adicionais or {}

    def _resolver_variavel(match: re.Match[str]) -> str:
        chave = match.group(1).strip()
        chave_normalizada = chave.lower()

        mapa_base = {
            "nome": lead.nome_contato or "",
            "telefone": lead.telefone_contato or "",
            "historico_resumo": lead.historico_resumo or "",
        }

        if chave_normalizada in mapa_base:
            return str(mapa_base[chave_normalizada])

        for campo, valor in dados_adicionais.items():
            if str(campo).strip().lower() == chave_normalizada:
                return "" if valor is None else str(valor)

        return ""

    return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", _resolver_variavel, texto_template or "")


def extrair_variaveis_template(texto_template: str) -> list[str]:
    variaveis: list[str] = []
    vistos: set[str] = set()
    for match in re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", texto_template or ""):
        chave = str(match).strip()
        chave_lower = chave.lower()
        if not chave or chave_lower in vistos:
            continue
        vistos.add(chave_lower)
        variaveis.append(chave)
    return variaveis


def lead_possui_alguma_tag(lead: CRMLead, tags_alvo: list[str]) -> bool:
    if not tags_alvo:
        return True
    lead_tags = {str(tag).strip().lower() for tag in (lead.tags or []) if str(tag).strip()}
    return any(tag in lead_tags for tag in tags_alvo)


def criar_lead_mock_preview(tag: str | None = None) -> CRMLead:
    return CRMLead(
        empresa_id=uuid.uuid4(),
        nome_contato="Cliente Exemplo",
        telefone_contato="5511999999999",
        historico_resumo="Cliente demonstrou interesse e aguarda retorno.",
        tags=[tag] if tag else [],
        dados_adicionais={
            "cidade": "Sao Paulo",
            "produto_interesse": "Plano Premium",
        },
    )


async def gerar_preview_campanha(
    session,
    empresa_id: str,
    template_id: str,
    tag: str | None,
) -> dict:
    emp_uuid = uuid.UUID(str(empresa_id))
    template_uuid = uuid.UUID(str(template_id))
    tag_normalizada = str(tag or "").strip().lower()

    result_template = await session.execute(
        select(TemplateMensagem).where(
            TemplateMensagem.id == template_uuid,
            TemplateMensagem.empresa_id == emp_uuid,
        )
    )
    template = result_template.scalars().first()
    if not template:
        raise ValueError("Template não encontrado")

    result_leads = await session.execute(
        select(CRMLead).where(CRMLead.empresa_id == emp_uuid)
    )
    leads = result_leads.scalars().all()
    leads_alvo = [
        lead
        for lead in leads
        if lead_possui_alguma_tag(lead, [tag_normalizada] if tag_normalizada else [])
    ]

    lead = leads_alvo[0] if leads_alvo else criar_lead_mock_preview(tag)
    mensagem = renderizar_template_mensagem(template.texto_template, lead).strip()

    return {
        "preview_texto": mensagem,
        "total_leads": len(leads_alvo),
        "usou_mock": not bool(leads_alvo),
    }


async def processar_campanha_disparo(campanha_id: str, empresa_id: str | None = None) -> None:
    try:
        campanha_uuid = uuid.UUID(str(campanha_id))
        empresa_uuid = uuid.UUID(str(empresa_id)) if empresa_id else None
    except (ValueError, TypeError):
        return

    async with AsyncSessionLocal() as session:
        try:
            filtros_campanha = [CampanhaDisparo.id == campanha_uuid]
            if empresa_uuid:
                filtros_campanha.append(CampanhaDisparo.empresa_id == empresa_uuid)
            result_campanha = await session.execute(
                select(CampanhaDisparo)
                .where(*filtros_campanha)
                .options(selectinload(CampanhaDisparo.template))
            )
            campanha = result_campanha.scalars().first()
            if not campanha:
                return

            campanha.status = CampanhaDisparoStatus.EXECUTANDO
            await session.commit()

            result_empresa = await session.execute(select(Empresa).where(Empresa.id == campanha.empresa_id))
            empresa = result_empresa.scalars().first()
            if not empresa:
                campanha.status = CampanhaDisparoStatus.ERRO
                await session.commit()
                return

            if not campanha.template_id or not campanha.template:
                campanha.status = CampanhaDisparoStatus.ERRO
                await session.commit()
                return

            if not empresa.conexao_disparo_id:
                campanha.status = CampanhaDisparoStatus.ERRO
                await session.commit()
                return

            from db.models import Conexao

            result_conexao = await session.execute(
                select(Conexao).where(
                    Conexao.id == empresa.conexao_disparo_id,
                    Conexao.empresa_id == campanha.empresa_id,
                )
            )
            conexao = result_conexao.scalars().first()
            if not conexao or not conexao.credenciais:
                campanha.status = CampanhaDisparoStatus.ERRO
                await session.commit()
                return

            result_leads = await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == campanha.empresa_id)
            )
            leads = result_leads.scalars().all()

            tags_alvo = [str(tag).strip().lower() for tag in (campanha.tags_alvo or []) if str(tag).strip()]
            leads_alvo = [
                lead
                for lead in leads
                if lead_possui_alguma_tag(lead, tags_alvo) and lead.telefone_contato
            ]

            falhou = False
            enfileirados = 0
            for index, lead in enumerate(leads_alvo):
                mensagem = renderizar_template_mensagem(campanha.template.texto_template, lead).strip()
                if not mensagem:
                    continue

                operational = await ensure_operational_conversation(
                    session,
                    empresa_id=campanha.empresa_id,
                    conexao_id=conexao.id,
                    external_contact_id=str(lead.telefone_contato),
                    lead=lead,
                    direction=None,
                    metadata={"origin": "campaign_foundation"},
                )
                idem = deterministic_idempotency_key(
                    "campaign",
                    campanha.empresa_id,
                    campanha.id,
                    lead.id,
                    campanha.template_id,
                    conexao.id,
                    sanitize_address(lead.telefone_contato),
                )
                recipient = await upsert_campaign_recipient(
                    session,
                    empresa_id=campanha.empresa_id,
                    campanha_id=campanha.id,
                    conexao_id=conexao.id,
                    provider=operational.provider.value,
                    channel=operational.channel.value,
                    lead_id=lead.id,
                    contact_id=operational.contact.id,
                    conversation_id=operational.conversation.id,
                    recipient_address=sanitize_address(lead.telefone_contato),
                    template_id=campanha.template_id,
                    parameters={"rendered_text_sha256": deterministic_idempotency_key(mensagem)},
                    scheduled_at=campanha.data_agendamento,
                    idempotency_key=idem,
                )
                await session.flush()
                if recipient.status != "blocked":
                    outbox = await enqueue_message(
                        session,
                        empresa_id=campanha.empresa_id,
                        conexao_id=conexao.id,
                        provider=operational.provider.value,
                        channel=operational.channel.value,
                        operation_type="campaign_prepare_send",
                        payload={
                            "mode": "direct_dispatch",
                            "recipient_id": str(recipient.id),
                            "message_sha256": deterministic_idempotency_key(mensagem),
                        },
                        idempotency_key=deterministic_idempotency_key("outbox", idem),
                        campaign_recipient_id=recipient.id,
                        available_at=campanha.data_agendamento,
                    )
                    await session.flush()
                    if str(outbox.status or "").lower() in {"completed", "failed"}:
                        enviado = str(outbox.status or "").lower() == "completed"
                    else:
                        enviado = await dispatch_outbox_now(
                            session,
                            outbox=outbox,
                            identificador_origem=str(lead.telefone_contato or ""),
                            texto=mensagem,
                            dispatcher=despachar_mensagem,
                        )
                    if enviado:
                        recipient.status = "sent"
                        recipient.sent_at = recipient.sent_at or datetime.utcnow()
                        recipient.error_code = None
                        recipient.error_detail = None
                        enfileirados += 1
                    else:
                        recipient.status = "failed"
                        recipient.failed_at = recipient.failed_at or datetime.utcnow()
                        recipient.error_code = recipient.error_code or "dispatch_failed"
                        recipient.error_detail = recipient.error_detail or "dispatch returned false"
                        falhou = True
                else:
                    falhou = True

                if index < len(leads_alvo) - 1:
                    delay_min = empresa.disparo_delay_min if empresa.disparo_delay_min is not None else 3
                    delay_max = empresa.disparo_delay_max if empresa.disparo_delay_max is not None else 7
                    if delay_min > delay_max:
                        delay_min, delay_max = delay_max, delay_min
                    await asyncio.sleep(random.uniform(delay_min, delay_max))

            campanha.status = CampanhaDisparoStatus.ERRO if falhou else CampanhaDisparoStatus.CONCLUIDO
            await session.commit()
        except Exception:
            await session.rollback()
            try:
                result_campanha = await session.execute(
                    select(CampanhaDisparo).where(CampanhaDisparo.id == campanha_uuid)
                )
                campanha = result_campanha.scalars().first()
                if campanha:
                    campanha.status = CampanhaDisparoStatus.ERRO
                    await session.commit()
            except Exception:
                await session.rollback()
