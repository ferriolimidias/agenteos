#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(source: str, needle: str, label: str) -> None:
    if needle not in source:
        raise AssertionError(f"Ausente: {label}")


def forbid(source: str, needle: str, label: str) -> None:
    if needle in source:
        raise AssertionError(f"Proibido encontrado: {label}")


def main() -> None:
    webhook_router = read("app/api/routers/meta_webhook.py")
    webhook_service = read("app/services/meta_webhook.py")
    graph_client = read("app/services/meta/graph_client.py")
    ai_control = read("app/services/rosangela_ai_control.py")
    inbox = read("app/api/routers/inbox.py")
    channel_factory = read("app/services/channel_factory.py")

    require(webhook_router, 'prefix="/webhook/meta"', "callback oficial /api/webhook/meta")
    require(webhook_router, 'alias="hub.verify_token"', "challenge GET")
    require(webhook_router, 'request.headers.get("X-Hub-Signature-256")', "assinatura HMAC")
    require(webhook_router, "await session.commit()", "persistencia antes do gate")
    require(webhook_router, "await handle_debouncer(msg, None)", "gate IA apos persistencia")

    require(webhook_service, "verify_meta_signature", "verificacao de assinatura")
    require(webhook_service, "hmac.compare_digest", "comparacao constante")
    require(webhook_service, "metadata.get(\"phone_number_id\")", "resolucao por phone_number_id")
    require(webhook_service, "Conexao.phone_number_id == event.external_account_id", "tenant-safe por phone_number_id")
    require(webhook_service, "provider_aliases.extend", "alias meta_official/meta_whatsapp")

    require(graph_client, "list_waba_subscribed_apps", "leitura subscribed_apps")
    require(graph_client, "subscribe_waba", "inscricao WABA")
    require(graph_client, "webhook_configuration", "leitura webhook do numero")

    require(ai_control, "default_country_code", "normalizacao com DDI padrao")
    require(ai_control, "resolve_conversation_ai_state", "gate canonico de IA")
    forbid(ai_control, "test_mode_expired", "bloqueio legado de teste")
    forbid(ai_control, "ai_window_expired", "janela interna artificial")

    require(inbox, "ChannelConversation", "Inbox multicanal")
    require(inbox, "conversation.provider", "provider exibido")
    forbid(inbox.split("@router.get(\"/{empresa_id}/inbox/conversas\")", 1)[1].split("@router.get", 1)[0], "evolution", "filtro Evolution na listagem multicanal")

    require(channel_factory, "_meta_dispatch_allowed", "gate outbound Meta")
    require(channel_factory, "MetaWhatsAppProvider", "provider Meta")
    require(channel_factory, "transporte legado bloqueado sem gate explicito", "Evolution bloqueada por padrao")

    print("verificar_meta_inbound_ia_test: OK")


if __name__ == "__main__":
    main()
