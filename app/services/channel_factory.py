import asyncio
import os
import traceback
import uuid
from typing import Any

import httpx
from app.agents.atendimento.whatsapp_segmenter import segment_whatsapp_response
from app.core.secret_sanitizer import sanitize_for_log
from app.services.channels.contracts import ChannelOutboundMessage
from app.services.channels.providers import EvolutionLegacyProvider, MetaWhatsAppProvider
from app.services.channels.registry import default_registry
from app.services.channels.resolver import ChannelResolutionError, context_from_connection
from app.services.channels.types import Channel, Provider
from db.database import AsyncSessionLocal
from db.models import Conexao, TipoConexao
from sqlalchemy import String, cast, func, select

HUMANIZACAO_DELAY_MIN = 0.5
HUMANIZACAO_DELAY_MAX = 2.0
HUMANIZACAO_CHARS_POR_SEGUNDO = 120.0
HUMANIZACAO_PALAVRAS_POR_SEGUNDO = 30.0
_CHANNEL_REGISTRY_READY = False


class _HttpxMetaTransport:
    async def post(self, url: str, *, headers: dict[str, str], json: dict):
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, json=json)

    async def get(self, url: str, *, headers: dict[str, str], params: dict | None = None):
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url, headers=headers, params=params)

    async def post_multipart(self, url: str, *, headers: dict[str, str], data: dict[str, Any], files: dict[str, Any]):
        timeout = httpx.Timeout(30.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, headers=headers, data=data, files=files)


def _legacy_evolution_dispatch_enabled() -> bool:
    return os.getenv("AGENTEOS_ENABLE_LEGACY_EVOLUTION_DISPATCH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _ensure_channel_registry() -> None:
    global _CHANNEL_REGISTRY_READY
    if _CHANNEL_REGISTRY_READY:
        return
    default_registry.register(
        Channel.WHATSAPP,
        Provider.EVOLUTION_LEGACY,
        lambda context, connection=None: EvolutionLegacyProvider(context, connection),
    )
    default_registry.register(
        Channel.WHATSAPP,
        Provider.META_WHATSAPP,
        lambda context, connection=None: MetaWhatsAppProvider(context, connection),
    )
    _CHANNEL_REGISTRY_READY = True


async def _resolver_conexao_evolution(
    *,
    session,
    conexao_id: str | None,
    empresa_id: str | None,
):
    conexao_explicita_informada = bool(conexao_id)
    if conexao_id:
        if not empresa_id:
            print(
                "[Channel Factory] Conexão explícita rejeitada: empresa_id é obrigatório "
                "para resolver conexao_id com isolamento multi-tenant."
            )
            return None
        try:
            filtros = [Conexao.id == uuid.UUID(str(conexao_id))]
            filtros.append(Conexao.empresa_id == uuid.UUID(str(empresa_id)))
            result = await session.execute(
                select(Conexao).where(*filtros)
            )
            conexao = result.scalars().first()
            if conexao:
                print(
                    f"[Channel Factory] Conexão explícita localizada: id={conexao.id} "
                    f"tipo={conexao.tipo} status={conexao.status} instancia={conexao.nome_instancia}"
                )
            if conexao and conexao.tipo == TipoConexao.EVOLUTION:
                return conexao
            if conexao:
                print(
                    f"[Channel Factory] Conexão explícita ignorada por tipo incompatível: {conexao.tipo}"
                )
        except (ValueError, TypeError):
            print(f"[Channel Factory] conexao_id inválido recebido para fallback: {conexao_id}")

        if conexao_explicita_informada:
            print(
                "[Channel Factory] Conexão explícita não resolvida; fallback por empresa "
                "bloqueado para evitar rerroteamento silencioso."
            )
            return None

    if not empresa_id:
        print("[Channel Factory] Fallback por empresa indisponível: empresa_id ausente.")
        return None

    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        print(f"[Channel Factory] Fallback por empresa falhou: empresa_id inválido '{empresa_id}'.")
        return None

    tipos_aceitos = ["evolution", "EVOLUTION"]
    status_aceitos = ["ativo", "connected", "conectado", "open"]
    result = await session.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            cast(Conexao.tipo, String).in_(tipos_aceitos),
            func.lower(Conexao.status).in_(status_aceitos),
        )
    )
    conexao = result.scalars().first()
    if conexao:
        print(
            f"[Channel Factory] Fallback por empresa selecionou conexão: id={conexao.id} "
            f"status={conexao.status} instancia={conexao.nome_instancia}"
        )
    else:
        print(
            f"[Channel Factory] Nenhuma conexão Evolution ativa encontrada para empresa={empresa_id}."
        )
    return conexao


async def _resolver_conexao_meta(
    *,
    session,
    conexao_id: str | None,
    empresa_id: str | None,
):
    if not conexao_id or not empresa_id:
        print("[Channel Factory -> Meta] conexão e empresa são obrigatórias para envio oficial.")
        return None
    try:
        result = await session.execute(
            select(Conexao).where(
                Conexao.id == uuid.UUID(str(conexao_id)),
                Conexao.empresa_id == uuid.UUID(str(empresa_id)),
            )
        )
    except (ValueError, TypeError):
        print("[Channel Factory -> Meta] conexao_id ou empresa_id inválido.")
        return None
    conexao = result.scalars().first()
    if not conexao:
        print("[Channel Factory -> Meta] conexão não encontrada para esta empresa.")
        return None
    if str(getattr(conexao, "empresa_id", "")) != str(empresa_id):
        print("[Channel Factory -> Meta] conexão rejeitada por escopo de empresa.")
        return None
    try:
        context_from_connection(conexao, channel=Channel.WHATSAPP, provider=Provider.META_WHATSAPP)
    except ChannelResolutionError as exc:
        print(f"[Channel Factory -> Meta] conexão incompatível: {exc}")
        return None
    return conexao


async def _meta_dispatch_allowed(
    *,
    session,
    conexao: Conexao,
    empresa_id: str | None,
    destination: str,
) -> tuple[bool, str]:
    from db.models import Empresa

    if not empresa_id:
        return False, "empresa_missing"
    try:
        result = await session.execute(select(Empresa).where(Empresa.id == uuid.UUID(str(empresa_id))))
    except (ValueError, TypeError):
        return False, "empresa_invalid"
    empresa = result.scalars().first()
    if not empresa:
        return False, "empresa_not_found"
    # Current inbound authorization is decided by the AI gate.  The legacy
    # test-mode allowlist is never a second outbound authorization gate.
    return True, "ai_gate_authorized"


async def _resolver_conexao_whatsapp_explicita(
    *,
    session,
    conexao_id: str | None,
    empresa_id: str | None,
):
    if not conexao_id or not empresa_id:
        print("[Channel Factory -> WhatsApp] conexão explícita e empresa são obrigatórias.")
        return None
    try:
        result = await session.execute(
            select(Conexao).where(
                Conexao.id == uuid.UUID(str(conexao_id)),
                Conexao.empresa_id == uuid.UUID(str(empresa_id)),
            )
        )
    except (ValueError, TypeError):
        print("[Channel Factory -> WhatsApp] conexao_id ou empresa_id inválido.")
        return None
    conexao = result.scalars().first()
    if not conexao:
        print("[Channel Factory -> WhatsApp] conexão não encontrada para esta empresa.")
        return None
    return conexao


async def despachar_mensagem(
    canal: str,
    identificador_origem: str,
    texto: str,
    conexao_id: str | None = None,
    empresa_id: str | None = None,
    split_messages: bool = False,
    segment_whatsapp: bool = False,
    message_type: str = "text",
    template_payload: dict[str, Any] | None = None,
    media: dict[str, Any] | None = None,
    send_as_voice: bool = False,
    return_details: bool = False,
):
    """
    Função responsável por aplicar o delay 'humano' e enviar a mensagem para o canal final.
    """
    message_type_norm = str(message_type or "text").strip().lower() or "text"
    if not texto and message_type_norm not in {"audio"}:
        return False

    texto = texto.strip()
    if not texto and message_type_norm not in {"audio"}:
        return False

    if segment_whatsapp and canal.lower() in {"evolution", "whatsapp"}:
        partes = segment_whatsapp_response(texto)
    elif split_messages:
        partes = [bloco.strip() for bloco in texto.split("\n\n") if bloco.strip()]
    else:
        partes = [texto]

    if not partes and message_type_norm == "audio":
        partes = [""]
    if not partes:
        return False

    template_payload_norm = dict(template_payload or {}) if isinstance(template_payload, dict) else {}
        
    sucesso_geral = True
    external_message_ids: list[str] = []

    for parte in partes:
        # Humanização enxuta: delay curto com teto absoluto de 2s.
        total_chars = len(parte)
        total_palavras = len([p for p in parte.split() if p.strip()])
        delay_chars = total_chars / HUMANIZACAO_CHARS_POR_SEGUNDO
        delay_palavras = total_palavras / HUMANIZACAO_PALAVRAS_POR_SEGUNDO
        delay_base = max(delay_chars, delay_palavras)
        delay = min(HUMANIZACAO_DELAY_MAX, max(HUMANIZACAO_DELAY_MIN, delay_base))
        print(f"[Humanização] Delay de {delay:.1f}s calculado para o trecho: '{parte[:40]}...'")
        await asyncio.sleep(delay)
        
        # Dispatch por canal (Match/Case)
        match canal.lower():
            case "whatsapp":
                _ensure_channel_registry()
                try:
                    async with AsyncSessionLocal() as session:
                        conexao = await _resolver_conexao_whatsapp_explicita(
                            session=session,
                            conexao_id=conexao_id,
                            empresa_id=empresa_id,
                        )
                        if not conexao:
                            sucesso_geral = False
                            continue
                        provider_raw = str(getattr(conexao, "provider", "") or "").strip().lower()
                        tipo_raw = str(getattr(getattr(conexao, "tipo", ""), "value", getattr(conexao, "tipo", ""))).lower()
                        if provider_raw in {"meta", "meta_whatsapp", "meta_official", "whatsapp_meta_official"} or tipo_raw == "meta":
                            context = context_from_connection(
                                conexao,
                                channel=Channel.WHATSAPP,
                                provider=Provider.META_WHATSAPP,
                            )
                            allowed, reason = await _meta_dispatch_allowed(
                                session=session,
                                conexao=conexao,
                                empresa_id=empresa_id,
                                destination=str(identificador_origem or "").strip(),
                            )
                            if not allowed:
                                print(f"[Channel Factory -> WhatsApp/Meta] not_dispatched: dispatch não autorizado ({reason}).")
                                sucesso_geral = False
                                continue
                            provider = MetaWhatsAppProvider(context, connection=conexao, transport=_HttpxMetaTransport())
                            outbound_payload = ChannelOutboundMessage(
                                empresa_id=str(conexao.empresa_id),
                                connection_id=str(conexao.id),
                                channel=Channel.WHATSAPP,
                                provider=Provider.META_WHATSAPP,
                                destination=str(identificador_origem or "").strip(),
                                message_type=message_type_norm,
                                text=parte,
                                media=media,
                                metadata={"send_as_voice": bool(send_as_voice), **({"template_payload": template_payload_norm} if template_payload_norm else {})},
                            )
                            if message_type_norm == "template":
                                if not template_payload_norm:
                                    print("[Channel Factory -> WhatsApp/Meta] template_payload ausente para envio de template.")
                                    sucesso_geral = False
                                    continue
                                provider_result = await provider.send_template(outbound_payload, template_payload=template_payload_norm)
                            else:
                                if message_type_norm == "audio":
                                    provider_result = await provider.send_audio(outbound_payload)
                                else:
                                    provider_result = await provider.send_text(outbound_payload)
                            external_id = str((provider_result or {}).get("external_message_id") or "").strip()
                            if external_id:
                                external_message_ids.append(external_id)
                        elif provider_raw in {"evolution", "evolution_legacy"} or "evolution" in tipo_raw:
                            if not _legacy_evolution_dispatch_enabled():
                                print(
                                    "[Channel Factory -> WhatsApp/Evolution] not_dispatched: "
                                    "transporte legado bloqueado sem gate explicito."
                                )
                                sucesso_geral = False
                                continue
                            context = context_from_connection(
                                conexao,
                                channel=Channel.WHATSAPP,
                                provider=Provider.EVOLUTION_LEGACY,
                            )
                            provider = default_registry.resolve(context, connection=conexao)
                            outbound_payload = ChannelOutboundMessage(
                                empresa_id=str(conexao.empresa_id),
                                connection_id=str(conexao.id),
                                channel=Channel.WHATSAPP,
                                provider=Provider.EVOLUTION_LEGACY,
                                destination=str(identificador_origem or "").strip(),
                                message_type="text",
                                text=parte,
                            )
                            await provider.send_text(outbound_payload)
                        else:
                            print(
                                f"[Channel Factory -> WhatsApp] provider desconhecido para conexao_id={conexao_id}."
                            )
                            sucesso_geral = False
                except Exception as e:
                    if isinstance(e, ChannelResolutionError):
                        print(f"[Channel Factory -> WhatsApp] Erro de resolução de canal: {e}")
                    print(f"[Channel Factory -> WhatsApp] not_dispatched: {sanitize_for_log(str(e))}")
                    sucesso_geral = False

            case "meta" | "meta_whatsapp":
                _ensure_channel_registry()
                print(
                    f"[Channel Factory -> Meta] Avaliando gate de teste controlado: "
                    f"empresa_id={empresa_id} conexao_id={conexao_id}"
                )
                try:
                    async with AsyncSessionLocal() as session:
                        conexao = await _resolver_conexao_meta(
                            session=session,
                            conexao_id=conexao_id,
                            empresa_id=empresa_id,
                        )
                        if not conexao:
                            sucesso_geral = False
                            continue
                        context = context_from_connection(
                            conexao,
                            channel=Channel.WHATSAPP,
                            provider=Provider.META_WHATSAPP,
                        )
                        allowed, reason = await _meta_dispatch_allowed(
                            session=session,
                            conexao=conexao,
                            empresa_id=empresa_id,
                            destination=str(identificador_origem or "").strip(),
                        )
                        if not allowed:
                            print(f"[Channel Factory -> Meta] not_dispatched: dispatch não autorizado ({reason}).")
                            sucesso_geral = False
                            continue
                        provider = MetaWhatsAppProvider(context, connection=conexao, transport=_HttpxMetaTransport())
                        outbound_payload = ChannelOutboundMessage(
                            empresa_id=str(conexao.empresa_id),
                            connection_id=str(conexao.id),
                            channel=Channel.WHATSAPP,
                            provider=Provider.META_WHATSAPP,
                            destination=str(identificador_origem or "").strip(),
                            message_type=message_type_norm,
                            text=parte,
                            media=media,
                            metadata={"send_as_voice": bool(send_as_voice), **({"template_payload": template_payload_norm} if template_payload_norm else {})},
                        )
                        if message_type_norm == "template":
                            if not template_payload_norm:
                                print("[Channel Factory -> Meta] template_payload ausente para envio de template.")
                                sucesso_geral = False
                                continue
                            provider_result = await provider.send_template(outbound_payload, template_payload=template_payload_norm)
                        else:
                            provider_result = await provider.send_audio(outbound_payload) if message_type_norm == "audio" else await provider.send_text(outbound_payload)
                        external_id = str((provider_result or {}).get("external_message_id") or "").strip()
                        if external_id:
                            external_message_ids.append(external_id)
                except Exception as e:
                    if isinstance(e, ChannelResolutionError):
                        print(f"[Channel Factory -> Meta] Erro de resolução de canal: {e}")
                    print(f"[Channel Factory -> Meta] not_dispatched: {sanitize_for_log(str(e))}")
                    sucesso_geral = False

            case "evolution":
                if not _legacy_evolution_dispatch_enabled():
                    print(
                        "[Channel Factory -> Evolution] not_dispatched: "
                        "transporte legado bloqueado sem gate explicito."
                    )
                    sucesso_geral = False
                    continue
                _ensure_channel_registry()
                print(
                    f"[Channel Factory] Despachando mensagem Evolution: "
                    f"canal={canal} empresa_id={empresa_id} conexao_id={conexao_id} "
                    f"identificador_raw='{identificador_origem}'"
                )
                try:
                    async with AsyncSessionLocal() as session:
                        conexao = await _resolver_conexao_evolution(
                            session=session,
                            conexao_id=conexao_id,
                            empresa_id=empresa_id,
                        )

                        if not conexao:
                            print(
                                f"[Channel Factory -> Evolution] Nenhuma conexão Evolution válida encontrada "
                                f"para empresa={empresa_id} conexao_id={conexao_id}."
                            )
                            sucesso_geral = False
                            continue

                        context = context_from_connection(
                            conexao,
                            channel=Channel.WHATSAPP,
                            provider=Provider.EVOLUTION_LEGACY,
                        )
                        provider = default_registry.resolve(context, connection=conexao)
                        outbound_payload = ChannelOutboundMessage(
                            empresa_id=str(conexao.empresa_id),
                            connection_id=str(conexao.id),
                            channel=Channel.WHATSAPP,
                            provider=Provider.EVOLUTION_LEGACY,
                            destination=str(identificador_origem or "").strip(),
                            message_type=message_type_norm,
                            text=parte,
                            metadata={"template_payload": template_payload_norm} if template_payload_norm else {},
                        )
                        if message_type_norm == "template":
                            print(
                                "[Channel Factory -> Evolution] template nao suportado pelo provider legado."
                            )
                            sucesso_geral = False
                            continue
                        if message_type_norm == "audio":
                            resposta_evolution = await provider.send_audio(outbound_payload)
                        else:
                            resposta_evolution = await provider.send_text(outbound_payload)
                        try:
                            from app.services.outbound_origin_tracker import (
                                extrair_message_id_resposta_evolution,
                                marcar_outbound_backend,
                            )

                            await marcar_outbound_backend(
                                str(empresa_id or conexao.empresa_id),
                                identificador_origem,
                                message_id=extrair_message_id_resposta_evolution(resposta_evolution),
                                texto=parte,
                            )
                        except Exception as mark_exc:
                            print(
                                f"[Channel Factory] Falha ao marcar eco outbound do backend: {mark_exc}"
                            )
                except Exception as e:
                    if isinstance(e, ChannelResolutionError):
                        print(f"[Channel Factory -> Evolution] Erro de resolução de canal: {e}")
                    print(f"[Channel Factory -> Evolution] Erro ao despachar via conexão {conexao_id}: {sanitize_for_log(str(e))}")
                    traceback.print_exc()
                    sucesso_geral = False
                
            case "chatwoot":
                print(f"[Channel Factory -> Chatwoot] Disparando para {identificador_origem}: {parte}")
                # Mock HTTP:
                # async with httpx.AsyncClient() as client:
                #     await client.post(f"mock_url_chatwoot/conversations/{identificador_origem}/messages", json={"content": parte})
                
            case "telegram":
                print(f"[Channel Factory -> Telegram] Disparando para {identificador_origem}: {parte}")
                
            case "simulador":
                print(f"[Channel Factory] Despachando via Conexão ID: {conexao_id} para o canal: {canal}")
                print(f"[Channel Factory -> Simulador] Gravando resposta no Redis para sessão: {identificador_origem}")
                from app.api.main import redis_client
                from app.services.simulator_session import simulator_response_key
                # No simulador, o identificador_origem é o sessao_id
                # Mas aqui concatenamos se houver múltiplas partes? 
                # O ideal é que o simulador leia mensagens uma a uma ou as concatene.
                # Como o polling do simulador deleta a chave, vamos usar um append ou timeout curto.
                # Para simplificar e manter compatibilidade com o polling atual que deleta:
                await redis_client.setex(simulator_response_key(str(empresa_id or ""), identificador_origem), 300, parte)
                
            case _:
                print(f"[Channel Factory] Despachando via Conexão ID: {conexao_id} para o canal: {canal}")
                print(f"[Channel Factory -> {canal}] Canal desconhecido; not_dispatched para {identificador_origem}.")
                sucesso_geral = False

    if return_details:
        return {"sent": sucesso_geral, "external_message_ids": external_message_ids}
    return sucesso_geral
