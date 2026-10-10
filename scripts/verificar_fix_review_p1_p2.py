#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FALHA: {message}")


def main() -> None:
    campanha = read("app/services/campanha_service.py")
    followup = read("app/services/followup_service.py")
    meta_webhook = read("app/api/routers/meta_webhook.py")
    outbox = read("app/services/outbox_service.py")

    require("dispatch_outbox_now" in campanha, "campanha nao usa dispatch_outbox_now")
    require("processar_followups_pendentes" in followup, "followup nao delega ao engine")
    require("followup_engine" in followup, "followup nao referencia o engine único")
    require('mode": "direct_dispatch"' in campanha, "campanha nao registra dispatch direto")
    require('recipient.status = "sent"' in campanha, "campanha nao marca recipient enviado")
    require("Compatibilidade fina" in followup, "followup nao ficou como shim explicito")
    require("async def dispatch_outbox_now" in outbox, "helper de dispatch imediato ausente")
    require('elif event.channel.value == "instagram"' in meta_webhook, "instagram nao tem ramo proprio")
    require("CRMLead(" in meta_webhook, "instagram nao cria projeção de lead")
    require('if lead and event.channel.value == "whatsapp"' in meta_webhook, "debouncer nao ficou restrito ao WhatsApp")

    print("OK: review P1/P2 verificado sem envio real.")


if __name__ == "__main__":
    main()
