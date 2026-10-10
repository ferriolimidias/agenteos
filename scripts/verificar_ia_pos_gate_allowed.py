#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FALHA: {message}")


def main() -> None:
    utils = read("app/api/utils.py")
    agent = read("app/agents/atendimento/agent.py")
    openai_provider = read("app/agents/llm/openai_provider.py")
    inbox = read("frontend/src/pages/tenant/Inbox.jsx")
    webhook = read("app/api/routers/meta_webhook.py")
    main = read("app/api/main.py")

    require('prefix="/webhook/meta"' in webhook, "router Meta central ausente")
    require("app.include_router(meta_webhook.router, prefix=\"/api\")" in main, "webhook Meta central nao e montado em /api")
    require("await handle_debouncer(msg, None)" in webhook, "webhook nao chama debouncer")
    require("ai.debounce.scheduled" in utils, "debouncer sem log estruturado")
    require("ai.debounce.executing" in utils, "callback do debouncer sem log")
    require("ai.lock.acquired" in utils, "lock Redis sem observabilidade")
    require("ai.gate.allowed" in utils, "gate allowed sem log runtime")
    require("ai.agent.starting" in utils, "agente sem log de inicio")
    require("ai.response.blocked" in utils, "bloqueio de resposta sem log")
    require("ai.dispatch.started" in utils, "dispatch sem log de inicio")
    require("tenant_llm_empty_response" in agent, "resposta vazia do LLM nao e codificada")
    require("tenant_tool_planner_error" in agent, "erro do planner nao e codificado")
    require('blocked_reason="fiscal_validation_failed"' in agent, "fiscal nao preserva blocked_reason")
    require("ai.llm.request_started" in openai_provider, "OpenAI sem log de inicio")
    require("ai.llm.failed" in openai_provider, "OpenAI sem log de falha")
    require("OpenAI returned empty content" in openai_provider, "resposta vazia OpenAI nao falha explicitamente")
    require("translateAiState" in inbox, "Inbox nao traduz estado da IA")
    require("IA autorizada" in inbox, "estado allowed nao traduzido")
    require("IA processando" in inbox, "indicador de processamento ausente")
    require("EvolutionLegacyProvider" not in utils, "fluxo IA tocado nao deve importar Evolution")
    require("sk-" not in utils, "utils contem possivel segredo")
    require("Authorization" not in utils, "utils nao deve registrar Authorization")

    print("OK: Fase 0A.2 IA pos-gate allowed verificada sem chamadas externas.")


if __name__ == "__main__":
    main()
