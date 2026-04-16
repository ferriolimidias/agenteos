import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from langchain_core.tools import tool

from db.database import AsyncSessionLocal
from db.models import CRMLead, TagCRM
from app.services.tag_crm_service import processar_disparo_conversao_ads_para_tags, sync_tag_etapa_lead


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

        # 2.1 Regra de exclusividade para tags de etapa de funil.
        # Se houver nova tag de etapa_funil, remove quaisquer outras tags
        # desse tipo já existentes no lead e mantém somente a primeira etapa
        # recebida na entrada atual.
        tags_existentes_ids = [str(item).strip() for item in (lead.tags if isinstance(lead.tags, list) else []) if str(item).strip()]
        tags_etapa_novas = [tag for tag in tags_encontradas if str(getattr(tag, "tipo", "") or "").strip().lower() == "etapa_funil"]
        if tags_etapa_novas:
            result_tags_empresa = await session.execute(select(TagCRM).where(TagCRM.empresa_id == lead.empresa_id))
            tags_empresa = result_tags_empresa.scalars().all()
            tags_empresa_por_id = {str(tag.id): tag for tag in tags_empresa}

            tags_existentes_sem_etapa = []
            for tag_id in tags_existentes_ids:
                tag_obj = tags_empresa_por_id.get(tag_id)
                if tag_obj and str(getattr(tag_obj, "tipo", "") or "").strip().lower() == "etapa_funil":
                    continue
                tags_existentes_sem_etapa.append(tag_id)

            primeira_etapa_nova_id = str(tags_etapa_novas[0].id)
            ids_novos_sem_outras_etapas: list[str] = []
            etapa_incluida = False
            for tag_obj in tags_encontradas:
                tag_id = str(tag_obj.id)
                if str(getattr(tag_obj, "tipo", "") or "").strip().lower() == "etapa_funil":
                    if etapa_incluida or tag_id != primeira_etapa_nova_id:
                        continue
                    etapa_incluida = True
                ids_novos_sem_outras_etapas.append(tag_id)
            ids_finais = tags_existentes_sem_etapa + ids_novos_sem_outras_etapas

        # 3. Atualizar e Salvar
        lead.tags = ids_finais
        flag_modified(lead, "tags")
        await sync_tag_etapa_lead(str(lead.id), session=session, preferencia="tag_to_crm")

        # 4. Recarregar as tags aplicadas do banco para avaliar ações automáticas nativas
        # Importante: usar UUIDs nativos para evitar falha de tipagem no IN com coluna UUID.
        tags_aplicadas: list[TagCRM] = []
        lista_de_ids_aplicados: list[uuid.UUID] = []
        for tag_id in ids_finais:
            try:
                lista_de_ids_aplicados.append(uuid.UUID(str(tag_id)))
            except (ValueError, TypeError):
                continue
        if lista_de_ids_aplicados:
            result_tags_aplicadas = await session.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == lead.empresa_id,
                    TagCRM.id.in_(lista_de_ids_aplicados),
                )
            )
            tags_aplicadas = result_tags_aplicadas.scalars().all()
        print(f"Tags encontradas no banco para pausa: {len(tags_aplicadas)}")

        # 5. Se alguma tag exigir transferência humana, pausa bot com update explícito.
        for tag in tags_aplicadas:
            if not tag.acao_transferir_humano:
                continue

            lead.status_atendimento = "manual"
            lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=24)
            session.add(lead)
            await session.commit()  # OBRIGATÓRIO
            await session.refresh(lead)
            print("--- [DEBUG TOOL] Commit com pausa de bot realizado com sucesso! --- \n")

            mensagem_transferencia = str(getattr(tag, "mensagem_transferencia", "") or "").strip()
            return f"[SISTEMA_BOT_PAUSADO] INSTRUÇÃO CRÍTICA: Pare de responder. Diga EXATAMENTE: {mensagem_transferencia}"

        await session.commit()
        print("--- [DEBUG TOOL] Commit realizado com sucesso! --- \n")

        return f"Tags atualizadas: {', '.join([t.nome for t in tags_encontradas])}"


@tool
async def tool_aplicar_tag_dinamica(lead_id: str, empresa_id: str, tag_id: str) -> str:
    """
    Use esta ferramenta para aplicar uma tag ao contato usando o ID oficial da etiqueta.
    """
    try:
        lead_uuid = uuid.UUID(str(lead_id))
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        return "Falha ao aplicar tag dinâmica: lead_id ou empresa_id inválido."

    tag_id_limpo = str(tag_id or "").strip()
    if not tag_id_limpo:
        return "Erro: ID de etiqueta inválido."

    try:
        tag_uuid = uuid.UUID(tag_id_limpo)
    except (ValueError, TypeError):
        return "Erro: ID de etiqueta inválido."

    try:
        async with AsyncSessionLocal() as session:
            # 1) Valida se o ID da etiqueta existe na empresa.
            result_tag = await session.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == empresa_uuid,
                    TagCRM.id == tag_uuid,
                )
            )
            tag = result_tag.scalars().first()
            if not tag:
                return "Erro: ID de etiqueta inválido."

            # 2) Busca o lead.
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
            tag_id_oficial = str(tag.id)
            if tag_id_oficial not in tags_finais:
                tags_finais.append(tag_id_oficial)
                lead.tags = tags_finais
                flag_modified(lead, "tags")
                await sync_tag_etapa_lead(str(lead.id), session=session, preferencia="tag_to_crm")
                
                # --- NOVA LÓGICA DE PAUSA NATIVA ---
                if getattr(tag, "acao_transferir_humano", False):
                    lead.status_atendimento = "manual"
                    lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=24)
                    session.add(lead)
                    await session.commit()
                    await session.refresh(lead)
                    
                    mensagem_transferencia = str(getattr(tag, "mensagem_transferencia", "") or "").strip()
                    return f"[SISTEMA_BOT_PAUSADO] INSTRUÇÃO CRÍTICA: Pare de responder. Diga EXATAMENTE: {mensagem_transferencia}"
                # -----------------------------------
                
                await session.commit()
                return f"Sucesso: etiqueta '{tag.nome}' aplicada ao lead."
            # Se a tag JÁ estava aplicada, mas exige transferência (evita que a IA ignore se repetir a tag)
            await sync_tag_etapa_lead(str(lead.id), session=session, preferencia="tag_to_crm")
            if getattr(tag, "acao_transferir_humano", False):
                lead.status_atendimento = "manual"
                lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=24)
                session.add(lead)
                await session.commit()
                await session.refresh(lead)
                mensagem_transferencia = str(getattr(tag, "mensagem_transferencia", "") or "").strip()
                return f"[SISTEMA_BOT_PAUSADO] INSTRUÇÃO CRÍTICA: Pare de responder. Diga EXATAMENTE: {mensagem_transferencia}"
                
            return f"Informação: O lead já possui a etiqueta '{tag.nome}'."
    except Exception as e:
        return f"Falha ao aplicar tag dinâmica: {str(e)}"


@tool
async def tool_transferir_para_humano(lead_id: str, empresa_id: str, motivo: str = None) -> str:
    """
    Use esta ferramenta QUANDO PRECISAR TRANSFERIR o atendimento para um humano, pausando o bot. 
    Sempre avise o cliente que está transferindo ANTES ou JUNTO com a chamada desta ferramenta.
    """
    try:
        lead_uuid = uuid.UUID(str(lead_id))
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        return "SISTEMA_BOT_PAUSADO"

    try:
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.id == lead_uuid,
                    CRMLead.empresa_id == empresa_uuid,
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                return "SISTEMA_BOT_PAUSADO"

            # 1) Coloca o atendimento em modo manual.
            lead.status_atendimento = "manual"

            # 2) Pausa o bot por 24h.
            lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=24)

            # 3) Aplica a tag oficial "Atendimento Humano" usando o ID real.
            result_tag = await session.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == empresa_uuid,
                    TagCRM.nome.ilike("Atendimento Humano"),
                )
            )
            tag_humano = result_tag.scalars().first()
            if tag_humano:
                tags_atuais = lead.tags if isinstance(lead.tags, list) else []
                tags_ids = [str(item).strip() for item in tags_atuais if str(item).strip()]
                tag_id = str(tag_humano.id)
                if tag_id not in tags_ids:
                    tags_ids.append(tag_id)
                    lead.tags = tags_ids
                    flag_modified(lead, "tags")

            await session.commit()
            return "SISTEMA_BOT_PAUSADO"
    except Exception:
        return "SISTEMA_BOT_PAUSADO"


@tool
async def tool_consultar_tags_empresa(empresa_id: str) -> str:
    """
    Use esta ferramenta para ler TODAS as tags oficiais criadas no painel da empresa.
    Retorna os nomes exatos das tags e as suas instruções/descrições (se houver).
    """
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        return "Falha ao consultar tags: empresa_id inválido."

    try:
        async with AsyncSessionLocal() as session:
            result_tags = await session.execute(
                select(TagCRM).where(TagCRM.empresa_id == empresa_uuid)
            )
            tags = result_tags.scalars().all()
            if not tags:
                return "Nenhuma tag encontrada no sistema para esta empresa."

            etiquetas = []
            for t in tags:
                etiquetas.append(
                    {
                        "nome": str(getattr(t, "nome", "") or ""),
                        "id": str(getattr(t, "id", "") or ""),
                    }
                )

            return f"Etiquetas disponíveis: {etiquetas}"
    except Exception as e:
        return f"Falha ao consultar tags: {str(e)}"
