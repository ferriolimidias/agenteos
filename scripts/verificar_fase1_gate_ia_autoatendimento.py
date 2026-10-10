from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agents.tenant_ai import get_tenant_ai_status, tenant_ai_enabled
from app.api.utils import processar_bloco_mensagens
from app.schemas import StandardMessage


class _FakeResult:
    def __init__(self, first=None, all_items=None, scalar_value=None):
        self._first = first
        self._all = list(all_items or [])
        self._scalar_value = scalar_value

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return list(self._all)

    def scalar(self):
        return self._scalar_value


class _FakeSession:
    def __init__(self, *, company=None, lead=None, conversation=None, empty_history: bool = True):
        self.company = company
        self.lead = lead
        self.conversation = conversation
        self.empty_history = empty_history
        self.execute_calls = 0

    async def execute(self, statement):
        self.execute_calls += 1
        if self.company is not None and self.execute_calls == 1:
            return _FakeResult(first=self.company)
        if self.lead is not None and self.execute_calls == 2:
            return _FakeResult(first=self.lead)
        if self.conversation is not None and self.execute_calls == 3:
            return _FakeResult(first=self.conversation)
        if self.empty_history:
            return _FakeResult(first=None, all_items=[], scalar_value=0)
        return _FakeResult(first=None, all_items=[], scalar_value=0)

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, sessions: list[_FakeSession]):
        self.sessions = list(sessions)
        self.index = 0

    def __call__(self):
        if self.index >= len(self.sessions):
            return _FakeSession()
        session = self.sessions[self.index]
        self.index += 1
        return session


class _FakeRedis:
    def __init__(self):
        self.setex_calls: list[tuple[str, int, str]] = []
        self.deleted: list[str] = []
        self.setnx_calls: list[tuple[str, str]] = []

    async def get(self, key):
        return None

    async def setnx(self, key, value):
        self.setnx_calls.append((key, value))
        return True

    async def expire(self, key, ttl):
        return True

    async def delete(self, key):
        self.deleted.append(key)
        return True

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        return True


class _GraphStub:
    def __init__(self, *, response=None, error: Exception | None = None):
        self.response = response or {}
        self.error = error
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _valid_company(**overrides):
    base = {
        "openai_api_key": None,
        "credenciais_canais": None,
        "status_openai": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _valid_lead():
    return SimpleNamespace(
        id="22222222-2222-2222-2222-222222222222",
        empresa_id="11111111-1111-1111-1111-111111111111",
        telefone_contato="5511999999999",
        ia_ativa=True,
        status_atendimento="aberto",
        bot_pausado_ate=None,
        dados_adicionais={},
    )


def _valid_conversation():
    return SimpleNamespace(
        id="33333333-3333-3333-3333-333333333333",
        empresa_id="11111111-1111-1111-1111-111111111111",
        lead_id="22222222-2222-2222-2222-222222222222",
        conexao_id=None,
        metadata_json={},
    )


def test_helper_rules():
    _assert(get_tenant_ai_status(None)["reason"] == "empresa_missing", "empresa_missing falhou")

    sem_chave = _valid_company()
    status = get_tenant_ai_status(sem_chave)
    _assert(status["enabled"] is False, "Sem chave deveria bloquear")
    _assert(status["reason"] == "missing_openai_key", "Reason missing_openai_key esperado")
    _assert(tenant_ai_enabled(sem_chave) is False, "tenant_ai_enabled deveria ser False")
    _assert("openai_api_key" not in status, "Status não deve expor chave")

    chave_direta = _valid_company(openai_api_key="sk-test")
    status = get_tenant_ai_status(chave_direta)
    _assert(status["enabled"] is True, "Chave direta deveria liberar")
    _assert(status["reason"] == "openai_key_present", "Reason openai_key_present esperado")

    chave_legada = _valid_company(credenciais_canais={"openai_api_key": "sk-test"})
    status = get_tenant_ai_status(chave_legada)
    _assert(status["enabled"] is True, "Chave legada deveria liberar")

    chave_alternativa = _valid_company(credenciais_canais={"api_key_openai": "sk-test"})
    status = get_tenant_ai_status(chave_alternativa)
    _assert(status["enabled"] is True, "api_key_openai deveria liberar")

    bloqueado = _valid_company(openai_api_key="sk-test", status_openai="chave_invalida")
    status = get_tenant_ai_status(bloqueado)
    _assert(status["enabled"] is False, "status_openai bloqueado deveria impedir")
    _assert(status["reason"] == "openai_status_blocked", "Reason openai_status_blocked esperado")
    _assert("sk-test" not in str(status), "Status não pode conter chave")


async def _run_processar_blocked_case() -> None:
    company = _valid_company()
    fake_redis = _FakeRedis()
    fake_graph = _GraphStub(response={"resposta_final": "nao deveria ser usado"})
    fake_dispatch = AsyncMock()

    sessions = _SessionFactory([
        _FakeSession(company=None),
        _FakeSession(company=company),
    ])

    message = StandardMessage(
        empresa_id="11111111-1111-1111-1111-111111111111",
        canal="evolution",
        identificador_origem="5511999999999",
        texto_mensagem="oi",
        is_human_agent=False,
    )

    with ExitStack() as stack:
        stack.enter_context(patch("db.database.AsyncSessionLocal", sessions))
        stack.enter_context(patch("app.api.main.redis_client", fake_redis))
        stack.enter_context(patch("app.agents.graph.graph", fake_graph))
        stack.enter_context(patch("app.services.channel_factory.despachar_mensagem", fake_dispatch))
        await processar_bloco_mensagens([message], background_tasks=None)

    _assert(fake_graph.calls == 0, "Gate sem IA não deveria chamar graph.ainvoke")
    _assert(fake_dispatch.await_count == 0, "Gate sem IA não deveria despachar mensagem")
    _assert(len(fake_redis.setex_calls) == 0, "Gate sem IA não deveria gerar fallback/redis outbound")


async def _run_processar_graph_error_case() -> None:
    company = _valid_company(openai_api_key="sk-test")
    lead = _valid_lead()
    conversation = _valid_conversation()
    fake_redis = _FakeRedis()
    fake_graph = _GraphStub(error=RuntimeError("boom"))
    fake_dispatch = AsyncMock()

    sessions = _SessionFactory([
        _FakeSession(company=None),
        _FakeSession(company=company, lead=lead, conversation=conversation),
    ])

    message = StandardMessage(
        empresa_id="11111111-1111-1111-1111-111111111111",
        canal="evolution",
        identificador_origem="5511999999999",
        texto_mensagem="quanto custa?",
        is_human_agent=False,
    )

    with ExitStack() as stack:
        stack.enter_context(patch("db.database.AsyncSessionLocal", sessions))
        stack.enter_context(patch("app.api.main.redis_client", fake_redis))
        stack.enter_context(patch("app.agents.graph.graph", fake_graph))
        stack.enter_context(patch("app.services.channel_factory.despachar_mensagem", fake_dispatch))
        await processar_bloco_mensagens([message], background_tasks=None)

    _assert(fake_graph.calls == 1, "Com IA fake o grafo deveria ser chamado")
    _assert(fake_dispatch.await_count == 0, "Falha no grafo não deve despachar fallback")
    _assert(len(fake_redis.setex_calls) == 0, "Falha no grafo não deve gerar fallback outbound")


async def _run_processar_enabled_smoke_case() -> None:
    company = _valid_company(openai_api_key="sk-test")
    lead = _valid_lead()
    conversation = _valid_conversation()
    fake_redis = _FakeRedis()
    fake_graph = _GraphStub(response={
        "resposta_final": "",
        "resumos_ferramentas": [],
        "status_conversa": None,
        "conexao_id": None,
    })
    fake_dispatch = AsyncMock()

    sessions = _SessionFactory([
        _FakeSession(company=None),
        _FakeSession(company=company, lead=lead, conversation=conversation),
    ])

    message = StandardMessage(
        empresa_id="11111111-1111-1111-1111-111111111111",
        canal="evolution",
        identificador_origem="5511999999999",
        texto_mensagem="oi",
        is_human_agent=False,
    )

    with ExitStack() as stack:
        stack.enter_context(patch("db.database.AsyncSessionLocal", sessions))
        stack.enter_context(patch("app.api.main.redis_client", fake_redis))
        stack.enter_context(patch("app.agents.graph.graph", fake_graph))
        stack.enter_context(patch("app.services.channel_factory.despachar_mensagem", fake_dispatch))
        await processar_bloco_mensagens([message], background_tasks=None)

    _assert(fake_graph.calls == 1, "IA fake deveria liberar o grafo")
    _assert(fake_dispatch.await_count == 0, "Resposta vazia não deve despachar")


def main() -> int:
    test_helper_rules()
    asyncio.run(_run_processar_blocked_case())
    asyncio.run(_run_processar_graph_error_case())
    asyncio.run(_run_processar_enabled_smoke_case())
    print("OK - Fase 1 gate IA autoatendimento validado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
