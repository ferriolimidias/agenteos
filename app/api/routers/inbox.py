import base64

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import Dict, Any, List
from pydantic import BaseModel
import uuid
from datetime import datetime, timedelta

from db.database import AsyncSessionLocal
from db.models import CRMLead, MensagemHistorico, Empresa, CRMEtapa, CRMFunil, Conexao, TipoConexao
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.schemas import ConversaListaResponse

router = APIRouter(prefix="/api/empresas", tags=["Inbox Live Chat"])

SIMULADOR_LEAD_ID = "ID_TESTE_SIMULADOR"


def _telefone_eh_simulador(telefone: str | None) -> bool:
    return str(telefone or "") == SIMULADOR_LEAD_ID

class SendMessagePayload(BaseModel):
    texto: str


def _inferir_tipo_mensagem(content_type: str | None) -> str:
    ct = str(content_type or "").lower()
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("audio/"):
        return "audio"
    return "document"

@router.get("/{empresa_id}/inbox", response_model=List[ConversaListaResponse])
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
                    "ultima_mensagem": l.historico_resumo or None,
                    "bot_pausado": bot_pausado,
                    "bot_pausado_ate": l.bot_pausado_ate.isoformat() if l.bot_pausado_ate else None,
                    "etapa_crm": l.etapa.nome if l.etapa else None,
                    "tags": l.tags or [],
                    "historico_resumo": l.historico_resumo or "",
                    "dados_adicionais": l.dados_adicionais or {},
                })
            
            return leads_retorno
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{empresa_id}/inbox/{telefone}")
async def listar_historico_lead(empresa_id: str, telefone: str):
    if _telefone_eh_simulador(telefone):
        return []
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
            mensagens = result_msgs.scalars().all()

            output = []
            for m in mensagens:
                try:
                    output.append({
                        "id": str(m.id),
                        "texto": str(m.texto or ""),
                        "from_me": bool(m.from_me),
                        "tipo_mensagem": str(m.tipo_mensagem or "text"),
                        "media_url": str(m.media_url) if m.media_url else None,
                        "criado_em": m.criado_em.isoformat() if m.criado_em else None,
                    })
                except Exception as e:
                    print(f"FALHA NO ITEM: {m.id}")
                    print(f"ERRO: {e}")
            return output
    except Exception as e:
        print(f"ERRO NA SERIALIZAÇÃO: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{empresa_id}/inbox/{telefone}/send")
async def enviar_mensagem(empresa_id: str, telefone: str, payload: SendMessagePayload):
    if _telefone_eh_simulador(telefone):
        return {"status": "success"}
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
                tipo_mensagem="text",
                media_url=None,
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


@router.post("/{empresa_id}/inbox/{telefone}/send_media")
async def enviar_midia(
    empresa_id: str,
    telefone: str,
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    if _telefone_eh_simulador(telefone):
        return {"status": "success", "tipo_mensagem": "document"}

    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID da empresa inválido")

    if not file:
        raise HTTPException(status_code=400, detail="Arquivo não informado")

    tipo_mensagem = _inferir_tipo_mensagem(file.content_type)
    mimetype = file.content_type or "application/octet-stream"
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    media_b64 = base64.b64encode(file_bytes).decode("utf-8")

    try:
        async with AsyncSessionLocal() as session:
            result_conexao = await session.execute(
                select(Conexao).where(
                    Conexao.empresa_id == empresa_uuid,
                    Conexao.tipo == TipoConexao.EVOLUTION,
                    Conexao.status == "ativo",
                )
            )
            conexao = result_conexao.scalars().first()
            if not conexao:
                raise HTTPException(status_code=400, detail="Nenhuma conexão Evolution ativa encontrada")

            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa_uuid,
                    CRMLead.telefone_contato == telefone
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                raise HTTPException(status_code=404, detail="Lead não encontrado")

            from app.services.evolution_service import enviar_midia_base64
            enviado = await enviar_midia_base64(
                conexao=conexao,
                numero=telefone,
                base64_data=media_b64,
                tipo=tipo_mensagem,
                mimetype=mimetype,
                caption=caption or "",
            )
            if not enviado:
                raise HTTPException(status_code=502, detail="Falha ao enviar mídia para a Evolution API")

            texto = (caption or "").strip()
            if not texto:
                if tipo_mensagem == "image":
                    texto = "Imagem enviada"
                elif tipo_mensagem == "audio":
                    texto = "Áudio enviado"
                else:
                    texto = f"Documento enviado: {file.filename or 'arquivo'}"

            nova_msg = MensagemHistorico(
                lead_id=lead.id,
                conexao_id=conexao.id,
                texto=texto,
                tipo_mensagem=tipo_mensagem,
                media_url=media_b64,
                from_me=True,
            )
            session.add(nova_msg)

            lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=1)
            await session.commit()

            return {"status": "success", "tipo_mensagem": tipo_mensagem}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{empresa_id}/inbox/{telefone}/reativar_bot")
async def reativar_bot(empresa_id: str, telefone: str):
    if _telefone_eh_simulador(telefone):
        return {"status": "success", "message": "Bot reativado"}
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
