from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    service = read("app/services/rosangela_ai_control.py")
    empresas = read("app/api/routers/empresas.py")
    inbox_api = read("app/api/routers/inbox.py")
    utils = read("app/api/utils.py")
    inbox_ui = read("frontend/src/pages/tenant/Inbox.jsx")
    monitor = read("scripts/monitorar_teste_rosangela.sh")

    check("class ConversationManualAIState" in service, "estado manual canonico ausente")
    check("def get_conversation_manual_ai_state" in service, "leitura canonica ausente")
    check("def set_conversation_ai_manual_state" in service, "setter canonico ausente")
    check("novo_estado = not" not in empresas, "endpoint ainda alterna estado cegamente")
    check("payload: LeadAIStateUpdate" in empresas, "endpoint nao recebe estado desejado")
    check("enabled=bool(payload.enabled)" in empresas, "setter nao usa payload explicito")
    check('"type": "inbox.ai_state.updated"' in empresas, "endpoint nao publica evento canonico")
    check("get_conversation_manual_ai_state(lead)" in inbox_api, "ai-state nao usa fonte canonica")
    check('"manual_enabled": manual_state.manual_enabled' in inbox_api, "ai-state nao expõe manual_enabled canonico")
    check('"manual_enabled": bool(getattr(lead, "ia_ativa", True))' in utils, "broadcast nao inclui manual_enabled")
    check("ai.manual_state.read" in inbox_api, "log ai.manual_state.read ausente")
    check("ai.manual_state.changed" in empresas, "log ai.manual_state.changed ausente")
    check("ai.processing.started" in utils, "log ai.processing.started ausente")
    check("ai.processing.completed" in utils, "log ai.processing.completed ausente")
    check("ai.processing.failed" in utils, "log ai.processing.failed ausente")
    check("setAiEffectiveState(translateAiState(data.ai_state))" in inbox_ui, "frontend nao consome evento ai-state")
    check("enabled: desiredEnabled" in inbox_ui, "toggle nao envia estado desejado")
    check("photoCacheRef.current.has(cacheKey)" in inbox_ui, "cache de foto ausente")
    check("photoRequestsRef.current.has(cacheKey)" in inbox_ui, "dedupe de foto ausente")
    check("photoAbortControllersRef" in inbox_ui, "abort de foto ausente")
    check("IA desligada nesta conversa" in inbox_ui, "texto traduzido ausente")
    check("/foto" in monitor and "healthcheck" in monitor, "monitor nao exclui /foto")
    check("META_INBOUND" in monitor and "META_OUTBOUND" in monitor and "ai\\.processing" in monitor, "monitor nao usa filtro especifico")

    print("verificar_fix_ai_manual_disabled: OK")


if __name__ == "__main__":
    main()
