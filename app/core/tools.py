import uuid

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import CRMLead, TagCRM


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

            result_tags = await session.execute(
                select(TagCRM).where(TagCRM.empresa_id == lead.empresa_id)
            )
            tags_oficiais = result_tags.scalars().all()
            mapa_oficiais = {str(tag.nome).strip().lower(): str(tag.nome).strip() for tag in tags_oficiais if str(tag.nome).strip()}

            tags_aplicadas: list[str] = []
            for tag in tags_normalizadas:
                oficial = mapa_oficiais.get(tag.lower())
                if oficial:
                    tags_aplicadas.append(oficial)

            if not tags_aplicadas:
                return "Erro ao atualizar tags do lead: nenhuma das tags informadas existe nas tags oficiais da empresa."

            atuais = _normalizar_tags(lead.tags if isinstance(lead.tags, list) else [])
            mapa_finais = {tag.lower(): tag for tag in atuais}
            for tag in tags_aplicadas:
                mapa_finais[tag.lower()] = tag

            lead.tags = list(mapa_finais.values())
            await session.commit()
            return f"Sucesso: tags oficiais aplicadas ao lead: {lead.tags}"
    except Exception as e:
        return f"Erro ao atualizar tags do lead: {str(e)}"
