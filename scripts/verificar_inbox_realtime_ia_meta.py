#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FALHA: {message}")


def main() -> None:
    meta_webhook = read("app/api/routers/meta_webhook.py")
    utils = read("app/api/utils.py")
    inbox = read("frontend/src/pages/tenant/Inbox.jsx")
    rosangela_control = read("app/services/rosangela_ai_control.py")
    phone_helper = read("app/services/whatsapp_address.py")

    require('router = APIRouter(prefix="/webhook/meta"' in meta_webhook, "webhook central Meta foi alterado")
    require("verify_meta_signature" not in meta_webhook, "router deve preservar validação no serviço existente")
    require('\"type\": \"inbox.message.created\"' in meta_webhook, "evento canônico de mensagem não é publicado")
    require('\"tipo_evento\": \"nova_mensagem_inbound\"' in meta_webhook, "compatibilidade de evento legado ausente")
    require(
        meta_webhook.index("await session.commit()") < meta_webhook.index('\"type\": \"inbox.message.created\"'),
        "publicação de mensagem deve ocorrer depois do commit",
    )
    require(
        meta_webhook.index('\"type\": \"inbox.message.created\"') < meta_webhook.index("await handle_debouncer(msg, None)"),
        "Inbox deve ser notificada antes do processamento IA/debounce",
    )

    require('\"type\": \"inbox.ai_state.updated\"' in utils, "evento de ai-state não é publicado")
    require("_extract_agent_block_reason" in utils, "reason code de grafo vazio não é extraído")
    require("tenant_llm_provider_error" in utils, "erro de provider LLM não é tratado explicitamente")
    require("br_mobile_ninth_digit_equivalent" in phone_helper, "equivalência brasileira 12/13 não está implementada")
    require("compare_whatsapp_addresses(" in rosangela_control, "gate não usa comparador canônico de telefone")
    require('"match_method": comparison.result' in rosangela_control, "gate não expõe match_method da tentativa")

    require('tipoEventoV2 === "inbox.message.created"' in inbox, "frontend não aceita evento canônico de mensagem")
    require('tipoEventoV2 === "inbox.ai_state.updated"' in inbox, "frontend não aceita evento canônico de ai-state")
    require("const novaMensagem = data?.mensagem || data?.message" in inbox, "frontend não aceita payload canônico message")
    require("new WebSocket(wsUrl)" in inbox, "Inbox não usa WebSocket")
    require("void fetchLeads();" in inbox, "Inbox não sincroniza lista ao receber evento")
    require("Evolution" not in meta_webhook, "webhook Meta não deve depender de Evolution neste fluxo")

    print("OK: verificação inbox realtime + IA Meta concluída")


if __name__ == "__main__":
    main()
