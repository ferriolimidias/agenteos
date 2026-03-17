import asyncio
import random
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.services.evolution_service import enviar_mensagem_whatsapp_por_credenciais
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


async def processar_campanha_disparo(campanha_id: str) -> None:
    try:
        campanha_uuid = uuid.UUID(str(campanha_id))
    except (ValueError, TypeError):
        return

    async with AsyncSessionLocal() as session:
        try:
            result_campanha = await session.execute(
                select(CampanhaDisparo)
                .where(CampanhaDisparo.id == campanha_uuid)
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
                select(Conexao).where(Conexao.id == empresa.conexao_disparo_id)
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
            for index, lead in enumerate(leads_alvo):
                mensagem = renderizar_template_mensagem(campanha.template.texto_template, lead).strip()
                if not mensagem:
                    continue

                enviado = await enviar_mensagem_whatsapp_por_credenciais(
                    lead.telefone_contato,
                    mensagem,
                    conexao.credenciais,
                )

                if enviado:
                    session.add(
                        MensagemHistorico(
                            lead_id=lead.id,
                            conexao_id=conexao.id,
                            texto=mensagem,
                            from_me=True,
                        )
                    )
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
