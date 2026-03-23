import os
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CRMLead


ADS_CONVERSAO_URL = os.getenv(
    "ADS_CONVERSAO_URL",
    "http://api-ads:8000/webhook/agenteso/conversao",
)


async def notificar_conversao_ads(lead_id: str, tag_nome: str, db: AsyncSession) -> None:
    try:
        lead_uuid = uuid.UUID(str(lead_id))
    except (ValueError, TypeError):
        print(f"[ADS] lead_id inválido para conversão: {lead_id}")
        return

    result = await db.execute(select(CRMLead).where(CRMLead.id == lead_uuid))
    lead = result.scalars().first()
    if not lead:
        print(f"[ADS] Lead não encontrado para conversão: {lead_id}")
        return

    if not (str(lead.gclid or "").strip() or str(lead.fbclid or "").strip()):
        print(f"[ADS] Lead {lead_id} sem gclid/fbclid; conversão ignorada.")
        return

    payload = {
        "telefone": lead.telefone_contato,
        "tag_aplicada": tag_nome,
        "gclid": lead.gclid,
        "fbclid": lead.fbclid,
        "valor_venda": float(lead.valor_conversao or 0.0),
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(ADS_CONVERSAO_URL, json=payload)
            response.raise_for_status()
        print(
            "[ADS] Conversão enviada com sucesso | "
            f"lead_id={lead_id} tag={tag_nome} status={response.status_code} payload={payload}"
        )
    except Exception as exc:
        print(
            "[ADS] Falha ao notificar conversão | "
            f"lead_id={lead_id} tag={tag_nome} url={ADS_CONVERSAO_URL} erro={exc} payload={payload}"
        )
