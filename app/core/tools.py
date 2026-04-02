import uuid

from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
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


@tool
async def tool_atualizar_tags_lead(lead_id: str, tags: list[str]):
    """Use esta ferramenta para atualizar ou adicionar múltiplas tags oficiais ao lead de uma vez. Passe uma lista com os nomes das tags."""
    print(f"\n--- [DEBUG TOOL] Iniciando atualização de tags para Lead: {lead_id} ---")
    print(f"Tags recebidas da IA: {tags}")

    async with AsyncSessionLocal() as session:
        # 1. Buscar o Lead
        result = await session.execute(select(CRMLead).where(CRMLead.id == uuid.UUID(lead_id)))
        lead = result.scalars().first()
        if not lead:
            print(f"ERRO: Lead {lead_id} não encontrado.")
            return "Lead não encontrado."

        # 2. Buscar IDs das tags oficiais
        tags_lower = [t.lower() for t in tags]
        print(f"Buscando no banco tags (lower): {tags_lower} para empresa: {lead.empresa_id}")

        query = select(TagCRM).where(
            TagCRM.empresa_id == lead.empresa_id,
            func.lower(TagCRM.nome).in_(tags_lower)
        )
        result_tags = await session.execute(query)
        tags_encontradas = result_tags.scalars().all()

        print(f"Tags encontradas no banco: {[t.nome for t in tags_encontradas]}")

        ids_finais = [str(t.id) for t in tags_encontradas]
        print(f"IDs que serão gravados: {ids_finais}")

        # 3. Atualizar e Salvar
        lead.tags = ids_finais
        flag_modified(lead, "tags")
        await session.commit()
        print("--- [DEBUG TOOL] Commit realizado com sucesso! --- \n")

        return f"Tags atualizadas: {', '.join([t.nome for t in tags_encontradas])}"


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
