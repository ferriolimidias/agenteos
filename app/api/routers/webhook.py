from fastapi import APIRouter, BackgroundTasks, Request
from typing import Dict, Any
import uuid
import os
import base64
import re
from datetime import datetime, timedelta

from app.api.schemas import StandardMessage
from app.api.utils import handle_debouncer
from app.services.websocket_manager import manager
from db.database import AsyncSessionLocal
from db.models import Empresa, CRMLead, MensagemHistorico, Conexao, TipoConexao
from sqlalchemy import select

router = APIRouter(prefix="/webhook", tags=["Webhook"])


def _mask_phone(telefone: str) -> str:
    digits = "".join(ch for ch in (telefone or "") if ch.isdigit())
    if len(digits) <= 8:
        return digits
    return f"{digits[:5]}{'*' * max(len(digits) - 9, 4)}{digits[-4:]}"


def _normalizar_telefone_remote_jid(remote_jid: str | None) -> str:
    bruto = str(remote_jid or "")
    sem_sufixo = bruto.replace("@s.whatsapp.net", "").replace("@g.us", "")
    return re.sub(r"\D", "", sem_sufixo)


def extrair_conteudo_mensagem(data_json: dict) -> str:
    message = (data_json or {}).get("message", {}) or {}
    texto = (
        message.get("conversation")
        or (message.get("extendedTextMessage", {}) or {}).get("text")
        or (message.get("imageMessage", {}) or {}).get("caption")
        or (message.get("videoMessage", {}) or {}).get("caption")
    )
    if texto:
        return str(texto)

    if message.get("audioMessage"):
        return "[Áudio]"
    if message.get("imageMessage"):
        return "[Imagem]"
    if message.get("videoMessage") or message.get("documentMessage"):
        return "[Arquivo]"
    if message.get("stickerMessage"):
        return "[Sticker]"
    return "[Mensagem não suportada]"


def _extrair_tipo_mensagem(data_json: dict) -> str:
    message = (data_json or {}).get("message", {}) or {}
    if message.get("imageMessage"):
        return "image"
    if message.get("audioMessage"):
        return "audio"
    if message.get("documentMessage") or message.get("videoMessage"):
        return "document"
    return "text"

async def get_conexao_id_por_tipo(
    session,
    empresa_uuid: uuid.UUID,
    tipo: TipoConexao,
    nome_instancia: str | None = None,
) -> str | None:
    result = await session.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            Conexao.tipo == tipo,
            Conexao.status == "ativo",
        )
    )
    conexoes = result.scalars().all()

    if not conexoes:
        return None

    if nome_instancia:
        for conexao in conexoes:
            credenciais = conexao.credenciais or {}
            evolution_instance = credenciais.get("evolution_instance")
            if evolution_instance == nome_instancia or conexao.nome_instancia == nome_instancia:
                return str(conexao.id)
        return None

    return str(conexoes[0].id) if conexoes else None


async def save_history_and_check_pause(
    empresa_id: str,
    telefone: str,
    texto: str,
    from_me: bool,
    conexao_id: str | None = None,
    nome_contato: str | None = None,
    tipo_mensagem: str = "text",
    media_url: str | None = None,
) -> bool:
    should_process = True
    async with AsyncSessionLocal() as session:
        empresa_uuid = uuid.UUID(empresa_id)
        try:
            conexao_uuid = uuid.UUID(conexao_id) if conexao_id else None
        except (ValueError, TypeError):
            conexao_uuid = None
        result = await session.execute(
            select(CRMLead).where(CRMLead.empresa_id == empresa_uuid, CRMLead.telefone_contato == telefone)
        )
        lead = result.scalars().first()
        
        if lead:
            nome_contato_limpo = str(nome_contato or "").strip()
            nome_atual = str(lead.nome_contato or "").strip()
            if (
                not from_me
                and nome_contato_limpo
                and (not nome_atual or nome_atual == "Usuário (Auto)")
            ):
                lead.nome_contato = nome_contato_limpo

            # Salvar no histórico
            nova_msg = MensagemHistorico(
                lead_id=lead.id,
                conexao_id=conexao_uuid,
                texto=texto,
                tipo_mensagem=tipo_mensagem,
                media_url=media_url,
                from_me=from_me
            )
            session.add(nova_msg)
            
            now = datetime.utcnow()
            
            if from_me:
                # Humano respondeu, pausar bot por +1h
                lead.bot_pausado_ate = now + timedelta(hours=1)
                should_process = False
            else:
                if lead.bot_pausado_ate and lead.bot_pausado_ate > now:
                    should_process = False
                    
            await session.commit()
            mensagem_payload = {
                "id": str(nova_msg.id),
                "texto": str(nova_msg.texto or ""),
                "from_me": bool(nova_msg.from_me),
                "tipo_mensagem": str(nova_msg.tipo_mensagem or "text"),
                "media_url": str(nova_msg.media_url) if nova_msg.media_url else None,
                "criado_em": nova_msg.criado_em.isoformat() if nova_msg.criado_em else None,
            }
            tipo_evento = "nova_mensagem_outbound" if from_me else "nova_mensagem_inbound"
            await manager.broadcast_to_empresa(
                empresa_id,
                {
                    "tipo_evento": tipo_evento,
                    "telefone": telefone,
                    "mensagem": mensagem_payload,
                },
            )
        else:
            # Caso o lead ainda não exista, processar normalmente
            if from_me:
                should_process = False
                
    return should_process


@router.post("/{empresa_id}/meta")
async def webhook_meta(empresa_id: str, payload: Dict[Any, Any], background_tasks: BackgroundTasks):
    identificador = payload.get("from", "5511999999999")
    texto = payload.get("text", {}).get("body", "Mensagem de teste (Meta)")
    
    should_process = await save_history_and_check_pause(empresa_id, identificador, texto, False)
    
    if should_process:
        msg = StandardMessage(
            empresa_id=empresa_id, 
            canal="meta",
            identificador_origem=identificador,
            texto_mensagem=texto,
            is_human_agent=False
        )
        background_tasks.add_task(handle_debouncer, msg)
    
    return {"status": "received"}


@router.post("/{empresa_id}/evolution")
async def webhook_evolution(empresa_id: str, payload: Dict[Any, Any], background_tasks: BackgroundTasks):
    print(f"\n[WEBHOOK EVOLUTION] Recebido para empresa: {empresa_id}")
    try:
        event = payload.get("event")
        if event != "messages.upsert":
            return {"status": "ignored", "reason": f"Event {event} ignored"}

        data = payload.get("data", {}) or {}
        key = data.get("key", {}) or {}
        message = data.get("message", {}) or {}
        push_name = str(data.get("pushName") or payload.get("pushName") or "").strip() or None
        instance_name = payload.get("instance") or payload.get("instanceName") or data.get("instance")
        print(f"[WEBHOOK] Mensagem recebida da Instância: {instance_name or 'desconhecida'} | Empresa: {empresa_id}")

        fromMe = bool(key.get("fromMe", False))
        remote_jid = key.get("remoteJid", "")
        telefone = _normalizar_telefone_remote_jid(remote_jid)
        if not telefone:
            print(f"DEBUG: Formato de mensagem desconhecido (remoteJid inválido: {remote_jid})")
            telefone = "0000000000"

        print(
            f"[WEBHOOK] Mensagem recebida da Instância: {instance_name or 'desconhecida'} | "
            f"Empresa: {empresa_id} | Telefone: {_mask_phone(telefone)}"
        )
        empresa_uuid = uuid.UUID(empresa_id)

        async with AsyncSessionLocal() as session:
            conexao_id = await get_conexao_id_por_tipo(
                session,
                empresa_uuid,
                TipoConexao.EVOLUTION,
                nome_instancia=instance_name,
            )
            if instance_name and not conexao_id:
                print(f"[WEBHOOK EVOLUTION] Nenhuma conexão ativa encontrada para instance='{instance_name}' na empresa {empresa_id}")

        texto = extrair_conteudo_mensagem(data)
        tipo_mensagem = _extrair_tipo_mensagem(data)
        if texto == "[Mensagem não suportada]":
            print("DEBUG: Formato de mensagem desconhecido")

        if tipo_mensagem == "audio":
             async def transcribe_audio():
                 try:
                     from app.services.evolution_service import get_base64_media
                     from openai import AsyncOpenAI
                     
                     async with AsyncSessionLocal() as session:
                         base64_data = await get_base64_media(empresa_uuid, message, session, conexao_id=conexao_id)
                         
                         openai_key = None
                         result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
                         empresa = result.scalars().first()
                         if empresa and empresa.credenciais_canais:
                             openai_key = empresa.credenciais_canais.get("openai_api_key")
                             
                 except Exception as e:
                     print(f"[WEBHOOK EVOLUTION] Erro ao transcrever áudio: {e}")
                     texto_transcrito = "[Áudio recebido, mas falha na transcrição]"
                     should_proc = await save_history_and_check_pause(
                        empresa_id,
                        telefone,
                        texto_transcrito,
                        fromMe,
                        conexao_id,
                        nome_contato=push_name,
                     )
                     if should_proc:
                         msg = StandardMessage(
                             empresa_id=empresa_id,
                             canal="evolution",
                             identificador_origem=telefone,
                             conexao_id=conexao_id,
                             nome_contato=push_name,
                             texto_mensagem=texto_transcrito,
                             is_human_agent=False,
                         )
                         await handle_debouncer(msg)
                     return
                     
                 if not base64_data:
                     print("[WEBHOOK EVOLUTION] Não foi possível baixar áudio da Evolution.")
                     await save_history_and_check_pause(
                        empresa_id,
                        telefone,
                        "[Áudio não pôde ser baixado]",
                        fromMe,
                        conexao_id,
                        nome_contato=push_name,
                        tipo_mensagem="audio",
                        media_url=None,
                     )
                     texto_transcrito = "[Áudio não pôde ser baixado]"
                 else:
                     await save_history_and_check_pause(
                        empresa_id,
                        telefone,
                        "[Áudio]",
                        fromMe,
                        conexao_id,
                        nome_contato=push_name,
                        tipo_mensagem="audio",
                        media_url=base64_data,
                     )
                     try:
                         if "," in base64_data:
                             base64_data = base64_data.split(",")[1]
                         audio_bytes = base64.b64decode(base64_data)
                         
                         temp_audio_path = f"/tmp/audio_{uuid.uuid4()}.ogg"
                         with open(temp_audio_path, "wb") as f:
                             f.write(audio_bytes)
                             
                         client = AsyncOpenAI(api_key=openai_key) if openai_key else AsyncOpenAI()
                         with open(temp_audio_path, "rb") as audio_file:
                             transcript = await client.audio.transcriptions.create(
                                 model="whisper-1", 
                                 file=audio_file,
                                 response_format="text"
                             )
                             
                         os.remove(temp_audio_path)
                         texto_transcrito = f"[Áudio Transcrito]: {transcript}"
                     except Exception as e:
                         print(f"[WEBHOOK EVOLUTION] Erro no Whisper: {e}")
                         texto_transcrito = "[Erro na Transcrição Whisper]"

                 should_proc = await save_history_and_check_pause(
                    empresa_id,
                    telefone,
                    texto_transcrito,
                    fromMe,
                    conexao_id,
                    nome_contato=push_name,
                 )
                 if should_proc:
                     msg = StandardMessage(
                         empresa_id=empresa_id,
                         canal="evolution",
                         identificador_origem=telefone,
                         conexao_id=conexao_id,
                         nome_contato=push_name,
                         texto_mensagem=texto_transcrito,
                         is_human_agent=False,
                     )
                     await handle_debouncer(msg)
                 
             background_tasks.add_task(transcribe_audio)
             return {"status": "received", "message": "Transcription in background"}

        media_base64 = None
        if tipo_mensagem in {"image", "document"}:
            try:
                from app.services.evolution_service import get_base64_media

                async with AsyncSessionLocal() as session:
                    media_base64 = await get_base64_media(empresa_uuid, message, session, conexao_id=conexao_id)
            except Exception as e:
                print(f"[WEBHOOK EVOLUTION] Erro ao baixar midia ({tipo_mensagem}): {e}")

        should_process = await save_history_and_check_pause(
            empresa_id,
            telefone,
            texto or "[Mensagem não suportada]",
            fromMe,
            conexao_id,
            nome_contato=push_name,
            tipo_mensagem=tipo_mensagem,
            media_url=media_base64,
        )
        
        if should_process:
            msg = StandardMessage(
                empresa_id=empresa_id,
                canal="evolution",
                identificador_origem=telefone,
                conexao_id=conexao_id,
                nome_contato=push_name,
                texto_mensagem=texto or "[Mensagem não suportada]",
                is_human_agent=False
            )
            background_tasks.add_task(handle_debouncer, msg)
        
        return {"status": "received", "message": "Processed"}

    except Exception as e:
        print(f"DEBUG: Formato de mensagem desconhecido: {e}")
        return {"status": "received", "message": "Formato desconhecido tratado"}


@router.post("/{empresa_id}/telegram")
async def webhook_telegram(empresa_id: str, payload: Dict[Any, Any], background_tasks: BackgroundTasks):
    identificador = str(payload.get("message", {}).get("chat", {}).get("id", "123456789"))
    texto = payload.get("message", {}).get("text", "Mensagem de teste (Telegram)")
    
    should_process = await save_history_and_check_pause(empresa_id, identificador, texto, False)
    
    if should_process:
        msg = StandardMessage(empresa_id=empresa_id, canal="telegram", identificador_origem=identificador, texto_mensagem=texto, is_human_agent=False)
        background_tasks.add_task(handle_debouncer, msg)
    return {"status": "received"}


@router.post("/{empresa_id}/chatwoot")
async def webhook_chatwoot(empresa_id: str, payload: Dict[Any, Any], background_tasks: BackgroundTasks):
    sender_type = payload.get("sender", {}).get("type", "contact")
    fromMe = (sender_type != "contact")
    identificador = str(payload.get("conversation", {}).get("contact_inbox", {}).get("source_id", "cw-abc-123"))
    texto = payload.get("content", "Mensagem de teste (Chatwoot)")
    
    should_process = await save_history_and_check_pause(empresa_id, identificador, texto, fromMe)
    
    if should_process:
        msg = StandardMessage(empresa_id=empresa_id, canal="chatwoot", identificador_origem=identificador, texto_mensagem=texto, is_human_agent=(sender_type == "user"))
        background_tasks.add_task(handle_debouncer, msg)
    return {"status": "received"}
