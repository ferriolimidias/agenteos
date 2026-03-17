from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, List
from pydantic import BaseModel
import uuid
from datetime import datetime, timedelta

from db.database import AsyncSessionLocal
from db.models import CRMLead, MensagemHistorico, Empresa, CRMEtapa, CRMFunil, Conexao, TipoConexao
from sqlalchemy import select
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/api/empresas", tags=["Inbox Live Chat"])

class SendMessagePayload(BaseModel):
    texto: str

@router.get("/{empresa_id}/inbox")
async def listar_inbox(empresa_id: str):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(CRMLead)
                .where(CRMLead.empresa_id == empresa_uuid)
                .options(selectinload(CRMLead.etapa))
                .order_by(CRMLead.criado_em.desc())
            )
            leads = result.scalars().all()
            
            leads_retorno = []
            now = datetime.utcnow()
            for l in leads:
                bot_pausado = False
                if l.bot_pausado_ate and l.bot_pausado_ate > now:
                    bot_pausado = True
                
                leads_retorno.append({
                    "id": str(l.id),
                    "nome_contato": l.nome_contato,
                    "telefone_contato": l.telefone_contato,
                    "bot_pausado": bot_pausado,
                    "bot_pausado_ate": l.bot_pausado_ate.isoformat() if l.bot_pausado_ate else None,
                    "etapa_crm": l.etapa.nome if l.etapa else None,
                    "tags": l.tags or [],
                    "historico_resumo": l.historico_resumo,
                    "dados_adicionais": l.dados_adicionais or {},
                })
            
            return leads_retorno
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{empresa_id}/inbox/{telefone}")
async def listar_historico_lead(empresa_id: str, telefone: str):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                return []
                
            result_msgs = await session.execute(
                select(MensagemHistorico)
                .where(MensagemHistorico.lead_id == lead.id)
                .order_by(MensagemHistorico.criado_em.asc())
            )
            msgs = result_msgs.scalars().all()
            
            retorno = []
            for m in msgs:
                retorno.append({
                    "id": str(m.id),
                    "texto": m.texto,
                    "from_me": m.from_me,
                    "criado_em": m.criado_em.isoformat()
                })
            return retorno
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{empresa_id}/inbox/{telefone}/send")
async def enviar_mensagem(empresa_id: str, telefone: str, payload: SendMessagePayload):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result_conexao = await session.execute(
                select(Conexao).where(
                    Conexao.empresa_id == empresa_uuid,
                    Conexao.tipo == TipoConexao.EVOLUTION,
                    Conexao.status == "ativo"
                )
            )
            conexao = result_conexao.scalars().first()

            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead não encontrado")
            
            nova_msg = MensagemHistorico(
                lead_id=lead.id,
                conexao_id=conexao.id if conexao else None,
                texto=payload.texto,
                from_me=True
            )
            session.add(nova_msg)
            
            lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=1)
            
            await session.commit()
            
            from app.services.evolution_service import enviar_mensagem_whatsapp
            await enviar_mensagem_whatsapp(empresa_uuid, telefone, payload.texto, session)
            
            return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{empresa_id}/inbox/{telefone}/reativar_bot")
async def reativar_bot(empresa_id: str, telefone: str):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead não encontrado")
            
            lead.bot_pausado_ate = None
            
            result_funil = await session.execute(
                select(CRMFunil).where(CRMFunil.empresa_id == empresa_uuid)
            )
            funil = result_funil.scalars().first()
            if funil:
                result_etapa = await session.execute(
                    select(CRMEtapa).where(
                        CRMEtapa.funil_id == funil.id,
                        CRMEtapa.nome == 'Em Atendimento'
                    )
                )
                etapa = result_etapa.scalars().first()
                if not etapa:
                    nova_etapa = CRMEtapa(funil_id=funil.id, nome="Em Atendimento", ordem=2)
                    session.add(nova_etapa)
                    await session.flush()
                    lead.etapa_id = nova_etapa.id
                else:
                    lead.etapa_id = etapa.id
            
            await session.commit()
            return {"status": "success", "message": "Bot reativado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
