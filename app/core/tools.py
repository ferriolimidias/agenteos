import uuid

from sqlalchemy import select, func
from langchain_core.tools import tool

from db.database import AsyncSessionLocal
from db.models import CRMLead, TagCRM
from app.services.tag_crm_service import processar_disparo_conversao_ads_para_tags


def _normalizar_tags(tags: list[str] | None) -> list[str]:
    output: list[str] = []
    vistos: set[str] = set()
    for tag in tags or []:
        limpa = str(tag or "").strip()
        if not limpa:
            continue
        chave = limpa.lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        output.append(limpa)
    return output


async def tool_atualizar_tags_lead(lead_id: str, tags: list[str]) -> str:
    try:
        lead_uuid = uuid.UUID(str(lead_id))
    except (ValueError, TypeError):
        return "Erro ao atualizar tags do lead: lead_id inválido."

    tags_normalizadas = _normalizar_tags(tags)
    if not tags_normalizadas:
        return "Erro ao atualizar tags do lead: informe ao menos uma tag válida."

    try:
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(CRMLead.id == lead_uuid)
            )
            lead = result_lead.scalars().first()
            if not lead:
                return "Erro ao atualizar tags do lead: lead não encontrado."

            nomes_tags_lower = [tag.lower() for tag in tags_normalizadas]
            result_tags = await session.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == lead.empresa_id,
                    func.lower(TagCRM.nome).in_(nomes_tags_lower),
                )
            )
            tags_oficiais_encontradas = result_tags.scalars().all()

            # Mapeamento por nome normalizado para preservar a ordem da entrada da IA.
            mapa_tags_por_nome = {
                str(tag.nome).strip().lower(): tag
                for tag in tags_oficiais_encontradas
                if str(tag.nome).strip()
            }

            tags_ids_aplicadas: list[str] = []
            tags_nomes_aplicadas: list[str] = []
            for nome_tag in nomes_tags_lower:
                tag_oficial = mapa_tags_por_nome.get(nome_tag)
                if not tag_oficial:
                    continue
                tag_id_str = str(tag_oficial.id)
                if tag_id_str in tags_ids_aplicadas:
                    continue
                tags_ids_aplicadas.append(tag_id_str)
                tags_nomes_aplicadas.append(str(tag_oficial.nome).strip())

            if not tags_ids_aplicadas:
                return "Erro ao atualizar tags do lead: nenhuma das tags informadas existe nas tags oficiais da empresa."

            # Atualiza o lead apenas com UUIDs oficiais encontrados.
            lead.tags = tags_ids_aplicadas

            # Notifica os Ads usando o nome oficial
            await processar_disparo_conversao_ads_para_tags(
                session=session,
                lead=lead,
                tags_aplicadas=tags_nomes_aplicadas,
            )
            await session.commit()
            return f"Sucesso: tags oficiais aplicadas ao lead: {tags_nomes_aplicadas}"
    except Exception as e:
        return f"Erro ao atualizar tags do lead: {str(e)}"


@tool
async def tool_aplicar_tag_dinamica(lead_id: str, empresa_id: str, nome_da_tag: str) -> str:
    """
    Use esta ferramenta para aplicar uma tag ao contato. Passe o nome exato da tag.
    """
    try:
        lead_uuid = uuid.UUID(str(lead_id))
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        return "Falha ao aplicar tag dinâmica: lead_id ou empresa_id inválido."

    nome_limpo = str(nome_da_tag or "").strip()
    if not nome_limpo:
        return "Falha ao aplicar tag dinâmica: nome_da_tag inválido."

    try:
        async with AsyncSessionLocal() as session:
            result_tag = await session.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == empresa_uuid,
                    TagCRM.nome == nome_limpo,
                )
            )
            tag = result_tag.scalars().first()
            if not tag:
                return "Falha ao aplicar tag dinâmica: tag não encontrada."

            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.id == lead_uuid,
                    CRMLead.empresa_id == empresa_uuid,
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                return "Falha ao aplicar tag dinâmica: lead não encontrado."

            tags_atuais = lead.tags if isinstance(lead.tags, list) else []
            tags_finais = [str(item).strip() for item in tags_atuais if str(item).strip()]
            tag_id_str = str(tag.id)
            if tag_id_str not in tags_finais:
                tags_finais.append(tag_id_str)
                lead.tags = tags_finais
                await session.commit()

            return "Sucesso: tag aplicada ao lead."
    except Exception as e:
        return f"Falha ao aplicar tag dinâmica: {str(e)}"


@tool
async def tool_transferir_para_humano(lead_id: str, empresa_id: str, motivo: str = None) -> str:
    """
    Use esta ferramenta QUANDO PRECISAR TRANSFERIR o atendimento para um humano, pausando o bot. 
    Sempre avise o cliente que está transferindo ANTES ou JUNTO com a chamada desta ferramenta.
    """
    # Apenas retorna a flag. A pausa real no banco será feita na borda do sistema (webhook)
    # após a IA ter a chance de se despedir do cliente.
    return "SISTEMA_BOT_PAUSADO"
