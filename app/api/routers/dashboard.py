from fastapi import APIRouter, HTTPException
import uuid
from sqlalchemy import select, func
from db.database import AsyncSessionLocal
from db.models import CRMLead, MensagemHistorico, CRMEtapa

router = APIRouter(prefix="/api/empresas", tags=["Dashboard"])

@router.get("/{empresa_id}/dashboard/stats")
async def obter_estatisticas(empresa_id: str):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            # Total leads
            result_total_leads = await session.execute(
                select(func.count()).select_from(CRMLead).where(CRMLead.empresa_id == empresa_uuid)
            )
            total_leads = result_total_leads.scalar() or 0

            # Faturamento do funil (valor_conversao) e quantidade de conversões
            result_faturamento = await session.execute(
                select(func.coalesce(func.sum(CRMLead.valor_conversao), 0)).where(CRMLead.empresa_id == empresa_uuid)
            )
            total_faturamento_funil = float(result_faturamento.scalar() or 0)
            result_convertidos = await session.execute(
                select(func.count())
                .select_from(CRMLead)
                .where(CRMLead.empresa_id == empresa_uuid, CRMLead.valor_conversao > 0)
            )
            total_leads_convertidos_funil = int(result_convertidos.scalar() or 0)

            # Leads por etapa e aguardando humano
            result_etapas = await session.execute(
                select(CRMEtapa.nome, func.count(CRMLead.id))
                .select_from(CRMLead)
                .join(CRMEtapa, CRMLead.etapa_id == CRMEtapa.id)
                .where(CRMLead.empresa_id == empresa_uuid)
                .group_by(CRMEtapa.nome)
            )
            
            leads_por_etapa = []
            aguardando_humano = 0
            
            for row in result_etapas.all():
                nome_etapa = row[0]
                contagem = row[1]
                leads_por_etapa.append({"name": nome_etapa, "value": contagem})
                if nome_etapa == "Aguardando Humano":
                    aguardando_humano = contagem

            # Total mensagens (interações)
            # Find all leads for this company first, then count messages
            result_leads_empresa = await session.execute(
                select(CRMLead.id).where(CRMLead.empresa_id == empresa_uuid)
            )
            lead_ids = [row[0] for row in result_leads_empresa.all()]
            
            total_mensagens = 0
            if lead_ids:
                result_total_msgs = await session.execute(
                    select(func.count()).select_from(MensagemHistorico).where(MensagemHistorico.lead_id.in_(lead_ids))
                )
                total_mensagens = result_total_msgs.scalar() or 0

            return {
                "total_leads": total_leads,
                "total_faturamento_funil": total_faturamento_funil,
                "total_leads_convertidos_funil": total_leads_convertidos_funil,
                "leads_por_etapa": leads_por_etapa,
                "aguardando_humano": aguardando_humano,
                "total_mensagens": total_mensagens
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
