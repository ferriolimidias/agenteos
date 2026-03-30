import base64
import re
import traceback

import httpx
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from typing import Dict, Any, List
from pydantic import BaseModel
import uuid
from datetime import datetime, timedelta

from db.database import AsyncSessionLocal, get_db
from db.models import CRMLead, MensagemHistorico, CRMEtapa, CRMFunil, Conexao
from sqlalchemy import select, func, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.schemas import ConversaListaResponse
from app.services.evolution_service import _obter_credenciais_conexao_ou_empresa
from app.services.mensageria.dispatcher import dispatch_outbound_message
from app.services.mensageria.schemas import StandardOutgoingMessage
from app.services.websocket_manager import manager

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


def _normalizar_numero_whatsapp(telefone: str | None) -> str:
    valor = str(telefone or "").strip()
    if "@s.whatsapp.net" in valor:
        valor = valor.replace("@s.whatsapp.net", "")
    return re.sub(r"\D", "", valor)


def _formatar_jid_whatsapp(telefone: str | None) -> str:
    numero = _normalizar_numero_whatsapp(telefone)
    return f"{numero}@s.whatsapp.net" if numero else ""


def _extrair_foto_url_resposta(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None

    candidatos = [
        payload.get("profilePictureUrl"),
        payload.get("profilePicUrl"),
        payload.get("pictureUrl"),
        payload.get("photoUrl"),
        payload.get("url"),
    ]

    for chave in ("data", "response", "result"):
        nested = payload.get(chave)
        if isinstance(nested, dict):
            candidatos.extend(
                [
                    nested.get("profilePictureUrl"),
                    nested.get("profilePicUrl"),
                    nested.get("pictureUrl"),
                    nested.get("photoUrl"),
                    nested.get("url"),
                ]
            )

    for candidato in candidatos:
        if isinstance(candidato, str) and candidato.strip():
            return candidato.strip()

    return None


async def _buscar_conexao_evolution_ativa(session: AsyncSession, empresa_uuid: uuid.UUID) -> Conexao | None:
    tipos_aceitos = ["evolution", "EVOLUTION"]
    status_aceitos = ["ativo", "connected", "conectado", "open"]
    result = await session.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            cast(Conexao.tipo, String).in_(tipos_aceitos),
            func.lower(Conexao.status).in_(status_aceitos),
        )
    )
    return result.scalars().first()

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
                    "foto_url": l.foto_url,
                    "telefone_contato": l.telefone_contato,
                    "ultima_mensagem": l.historico_resumo or None,
                    "status_atendimento": str(l.status_atendimento or "aberto"),
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
    print(">>> DEBUG: Rota de histórico acessada com sucesso! <<<")
    if _telefone_eh_simulador(telefone):
        return []

    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        return []

    try:
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
            if not mensagens:
                return []

            print(f"DEBUG: Encontradas {len(mensagens)} mensagens para o lead {lead.id}")

            output = []
            for m in mensagens:
                try:
                    criado_em_value = getattr(m, "criado_em", None)
                    output.append({
                        "id": str(getattr(m, "id", "erro-id")),
                        "texto": str(getattr(m, "texto", "[Sem texto]")),
                        "from_me": bool(getattr(m, "from_me", False)),
                        "tipo_mensagem": str(getattr(m, "tipo_mensagem", "text")),
                        "media_url": str(m.media_url) if getattr(m, "media_url", None) else None,
                        "criado_em": criado_em_value.isoformat() if hasattr(criado_em_value, "isoformat") else str(criado_em_value or ""),
                    })
                except Exception as e:
                    print(f"FALHA NO ITEM: {getattr(m, 'id', 'sem-id')}")
                    print(f"ERRO: {e}")
            return output
    except Exception as e:
        print(f"ERRO GERAL NA ROTA DE HISTÓRICO: {e}")
        return []


@router.get("/{empresa_id}/inbox/{telefone}/foto")
async def obter_foto_lead(empresa_id: str, telefone: str):
    if _telefone_eh_simulador(telefone):
        return {"foto_url": None}

    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except (ValueError, TypeError):
        return {"foto_url": None}

    try:
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

            now = datetime.utcnow()
            # Se a foto foi atualizada nas últimas 24h, retorna do banco (seja a URL ou None)
            if lead.foto_atualizada_em and (now - lead.foto_atualizada_em) < timedelta(hours=24):
                return {"foto_url": lead.foto_url}

            conexao = await _buscar_conexao_evolution_ativa(session, empresa_uuid)
            if not conexao:
                return {"foto_url": lead.foto_url or None}

            credenciais = await _obter_credenciais_conexao_ou_empresa(session, conexao)
            if not credenciais:
                return {"foto_url": lead.foto_url or None}

            evolution_url = str(credenciais.get("evolution_url") or "").strip().rstrip("/")
            evolution_apikey = str(credenciais.get("evolution_apikey") or "").strip()
            instance_name = str(credenciais.get("evolution_instance") or "").strip()
            numero = _normalizar_numero_whatsapp(telefone)
            numero_jid = _formatar_jid_whatsapp(telefone)

            if not all([evolution_url, evolution_apikey, instance_name, numero]):
                return {"foto_url": lead.foto_url or None}

            foto_url = None
            headers = {"apikey": evolution_apikey, "Content-Type": "application/json"}
            endpoint_get = f"{evolution_url}/profile/fetchProfile/{instance_name}"
            endpoint_post = f"{evolution_url}/chat/fetchProfilePictureUrl/{instance_name}"

            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                    response_get = await client.get(
                        endpoint_get,
                        headers=headers,
                        params={"number": numero},
                    )
                    if response_get.status_code in (200, 201):
                        try:
                            foto_url = _extrair_foto_url_resposta(response_get.json())
                        except Exception:
                            foto_url = None

                    if not foto_url:
                        response_post = await client.post(
                            endpoint_post,
                            headers=headers,
                            json={"number": numero_jid or numero},
                        )
                        if response_post.status_code in (200, 201):
                            try:
                                foto_url = _extrair_foto_url_resposta(response_post.json())
                            except Exception:
                                foto_url = None
            except Exception as exc:
                print(f"[Inbox Foto] Falha ao buscar foto na Evolution: {exc}")
                return {"foto_url": lead.foto_url or None}

            # Se achou foto nova, atualiza a URL. Se não achou, mantém a velha (ou None).
            if foto_url:
                lead.foto_url = foto_url

            # SEMPRE atualiza a data de checagem para não tentar de novo nas próximas 24h
            lead.foto_atualizada_em = now
            await session.commit()

            return {"foto_url": lead.foto_url}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Inbox Foto] Erro ao obter foto do lead: {e}")
        return {"foto_url": None}

@router.post("/{empresa_id}/inbox/{telefone}/send")
@router.post("/{empresa_id}/inbox/{telefone}/mensagens")
async def enviar_mensagem(
    empresa_id: str,
    telefone: str,
    payload: SendMessagePayload,
    db: AsyncSession = Depends(get_db),
):
    if _telefone_eh_simulador(telefone):
        return {"status": "success"}
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        tipos_aceitos = ["evolution", "EVOLUTION"]
        status_aceitos = ["ativo", "connected", "conectado", "open"]
        result_conexao = await db.execute(
            select(Conexao).where(
                Conexao.empresa_id == empresa_uuid,
                cast(Conexao.tipo, String).in_(tipos_aceitos),
                func.lower(Conexao.status).in_(status_aceitos),
            )
        )
        conexao = result_conexao.scalars().first()
        if not conexao:
            todas_conexoes = await db.execute(
                select(Conexao).where(Conexao.empresa_id == empresa_uuid)
            )
            lista_conexoes = todas_conexoes.scalars().all()
            print(
                f"DEBUG BANCO: Encontradas {len(lista_conexoes)} conexões para a empresa {empresa_id}"
            )
            for c in lista_conexoes:
                print(
                    f" -> ID: {c.id} | Tipo: {c.tipo} | Status: {c.status} | Instância: {c.nome_instancia}"
                )
            raise HTTPException(
                status_code=400,
                detail="Conexão não encontrada para esta empresa",
            )

        result_lead = await db.execute(
            select(CRMLead).where(
                CRMLead.empresa_id == empresa_uuid,
                CRMLead.telefone_contato == telefone
            )
        )
        lead = result_lead.scalars().first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead não encontrado")
        
        outbound_payload = StandardOutgoingMessage(
            identificador_contato=str(telefone or "").strip(),
            canal="whatsapp",
            texto=payload.texto,
            tipo="text",
            media_url=None,
        )
        await dispatch_outbound_message(
            empresa_id=empresa_uuid,
            conexao=conexao,
            payload=outbound_payload,
        )

        # Só persiste no histórico local após envio outbound com sucesso
        nova_msg = MensagemHistorico(
            lead_id=lead.id,
            conexao_id=conexao.id if conexao else None,
            texto=payload.texto,
            tipo_mensagem="text",
            media_url=None,
            from_me=True
        )
        db.add(nova_msg)
        lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=1)
        await db.commit()
        mensagem_payload = {
            "id": str(nova_msg.id),
            "texto": str(nova_msg.texto or ""),
            "from_me": bool(nova_msg.from_me),
            "tipo_mensagem": str(nova_msg.tipo_mensagem or "text"),
            "media_url": str(nova_msg.media_url) if nova_msg.media_url else None,
            "criado_em": nova_msg.criado_em.isoformat() if nova_msg.criado_em else None,
        }
        await manager.broadcast_to_empresa(
            empresa_id,
            {
                "tipo_evento": "nova_mensagem_outbound",
                "telefone": telefone,
                "mensagem": mensagem_payload,
            },
        )
        
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERRO REAL DE ENVIO: {str(e)}")
        traceback.print_exc()
        print(f"[Inbox] Erro ao enviar mensagem manualmente: {e}")
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
            tipos_aceitos = ["evolution", "EVOLUTION"]
            status_aceitos = ["ativo", "connected", "conectado", "open"]
            result_conexao = await session.execute(
                select(Conexao).where(
                    Conexao.empresa_id == empresa_uuid,
                    cast(Conexao.tipo, String).in_(tipos_aceitos),
                    func.lower(Conexao.status).in_(status_aceitos),
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


@router.post("/{empresa_id}/inbox/{telefone}/pausar_bot")
async def pausar_bot(empresa_id: str, telefone: str):
    if _telefone_eh_simulador(telefone):
        return {"status": "success", "message": "Bot pausado"}
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

            lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=24)
            await session.commit()
            return {
                "status": "success",
                "message": "Bot pausado",
                "bot_pausado_ate": lead.bot_pausado_ate.isoformat() if lead.bot_pausado_ate else None,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
