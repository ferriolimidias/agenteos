"""
Helpers multi-tenant para etapas de CRM: sempre via `crm_funis` + `crm_etapas` + `empresa_id`.
Evita acoplar automações a nomes fixos de coluna — preferir o campo `tipo` quando existir.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.models import CRMFunil, CRMEtapa


async def listar_etapas_empresa_formatadas(session, empresa_id: uuid.UUID) -> tuple[str, list[dict[str, Any]]]:
    """
    Retorna (texto_formatado_para_prompt, lista_serializavel).
    Inclui todas as etapas de todos os funis da empresa, ordenadas por funil e ordem.
    """
    result = await session.execute(
        select(CRMEtapa, CRMFunil.nome)
        .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
        .where(CRMFunil.empresa_id == empresa_id)
        .order_by(CRMFunil.nome.asc(), CRMEtapa.ordem.asc(), CRMEtapa.nome.asc())
    )
    linhas: list[str] = []
    serial: list[dict[str, Any]] = []
    for etapa, funil_nome in result.all():
        fid = str(etapa.id)
        nome_funil = str(funil_nome or "").strip() or "(funil)"
        nome_etapa = str(getattr(etapa, "nome", "") or "").strip() or "(sem nome)"
        tipo = str(getattr(etapa, "tipo", "") or "").strip() or None
        ordem = int(getattr(etapa, "ordem", 0) or 0)
        trecho_tipo = f" | tipo={tipo}" if tipo else ""
        linhas.append(f"- UUID={fid} | funil={nome_funil} | ordem={ordem} | nome={nome_etapa}{trecho_tipo}")
        serial.append(
            {
                "etapa_id": fid,
                "nome": nome_etapa,
                "ordem": ordem,
                "tipo": tipo,
                "funil_nome": nome_funil,
            }
        )
    texto = "\n".join(linhas) if linhas else "(nenhuma etapa cadastrada para esta empresa)"
    return texto, serial


async def obter_ou_criar_etapa_por_tipo(
    session,
    empresa_uuid: uuid.UUID,
    tipo: str,
    *,
    nome_fallback: str | None = None,
) -> uuid.UUID | None:
    """
    Localiza uma etapa com `tipo` (ex.: 'handoff', 'fechamento') em qualquer funil da empresa.
    Se não existir, cria funil padrão (se necessário) e uma etapa com esse tipo.
    """
    tipo_norm = str(tipo or "").strip().lower()
    if not tipo_norm:
        return None

    result = await session.execute(
        select(CRMEtapa)
        .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
        .where(
            CRMFunil.empresa_id == empresa_uuid,
            CRMEtapa.tipo.isnot(None),
        )
        .options(selectinload(CRMEtapa.funil))
    )
    for etapa in result.scalars().all():
        if str(getattr(etapa, "tipo", "") or "").strip().lower() == tipo_norm:
            return etapa.id

    result_funil = await session.execute(
        select(CRMFunil)
        .where(CRMFunil.empresa_id == empresa_uuid)
        .options(selectinload(CRMFunil.etapas))
        .order_by(CRMFunil.nome.asc())
    )
    funil = result_funil.scalars().first()
    if not funil:
        funil = CRMFunil(empresa_id=empresa_uuid, nome="Pipeline")
        session.add(funil)
        await session.flush()

    nome_etapa = (nome_fallback or "").strip() or f"Etapa ({tipo_norm})"
    maior_ordem = 0
    if getattr(funil, "etapas", None):
        for e in funil.etapas:
            try:
                maior_ordem = max(maior_ordem, int(getattr(e, "ordem", 0) or 0))
            except (TypeError, ValueError):
                continue
    nova = CRMEtapa(
        funil_id=funil.id,
        nome=nome_etapa,
        ordem=maior_ordem + 1,
        tipo=tipo_norm,
    )
    session.add(nova)
    await session.flush()
    return nova.id


async def resolver_etapa_fechamento_id(session, empresa_uuid: uuid.UUID) -> uuid.UUID | None:
    """
    Etapa de encerramento do funil: prioriza `tipo` contendo 'fechamento' ou igual a 'fechado'.
    Se não houver, cria etapa com tipo 'fechamento'.
    """
    result = await session.execute(
        select(CRMEtapa)
        .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
        .where(CRMFunil.empresa_id == empresa_uuid, CRMEtapa.tipo.isnot(None))
        .order_by(CRMEtapa.ordem.asc())
    )
    for etapa in result.scalars().all():
        t = str(getattr(etapa, "tipo", "") or "").strip().lower()
        if "fechamento" in t or t in {"fechado", "fechamento"}:
            return etapa.id
    return await obter_ou_criar_etapa_por_tipo(session, empresa_uuid, "fechamento")


async def obter_nome_etapa_lead(session, empresa_uuid: uuid.UUID, lead_etapa_id: uuid.UUID | None) -> str:
    if not lead_etapa_id:
        return "(sem etapa definida no CRM)"
    row = await session.execute(
        select(CRMEtapa.nome, CRMFunil.nome)
        .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
        .where(CRMEtapa.id == lead_etapa_id, CRMFunil.empresa_id == empresa_uuid)
    )
    first = row.first()
    if not first:
        return "(etapa não encontrada para esta empresa)"
    nome_etapa, nome_funil = first[0], first[1]
    return f"{str(nome_etapa or '').strip()} (funil: {str(nome_funil or '').strip() or '—'})"
