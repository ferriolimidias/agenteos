from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api import utils as api_utils
from app.api.utils import processar_bloco_mensagens
from app.schemas import StandardMessage


class _FakeResult:
    def __init__(self, first=None, all_items=None, scalar_value=None):
        self._first = first
        self._all_items = list(all_items or [])
        self._scalar_value = scalar_value

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return list(self._all_items)

    def scalar(self):
        return self._scalar_value


class _FakeSession:
    def __init__(self, results: list[_FakeResult]):
        self.results = list(results)
        self.execute_calls = 0
        self.commits = 0

    async def execute(self, statement):
        self.execute_calls += 1
        if self.results:
            return self.results.pop(0)
        return _FakeResult()

    async def commit(self):
        self.commits += 1

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
            return _FakeSession([])
        session = self.sessions[self.index]
        self.index += 1
        return session


class _FakeRedis:
    def __init__(self):
        self.setex_calls: list[tuple[str, int, str]] = []
        self.deleted: list[str] = []

    async def get(self, key):
        return None

    async def setnx(self, key, value):
        return True

    async def expire(self, key, ttl):
        return True

    async def delete(self, key):
        self.deleted.append(key)
        return True

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        return True


class _GraphRaises:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        raise RuntimeError("boom-controlado")


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _run_graph_failure_case() -> None:
    empresa_id = "11111111-1111-1111-1111-111111111111"
    lead_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    lead = SimpleNamespace(
        id=lead_id,
        empresa_id=uuid.UUID(empresa_id),
        telefone_contato="5511999999999",
        historico_resumo="",
        dados_adicionais={},
        ia_ativa=True,
        status_atendimento="aberto",
    )
    company = SimpleNamespace(
        id=uuid.UUID(empresa_id),
        openai_api_key="sk-test",
        credenciais_canais={},
        status_openai="ok",
    )
    fake_redis = _FakeRedis()
    fake_graph = _GraphRaises()
    fake_dispatch = AsyncMock()
    fake_save_outbound = AsyncMock()
    fake_ai_status = {"enabled": True, "reason": "openai_key_present"}
    log_capture = _LogCapture()

    history_session = _FakeSession([
        _FakeResult(first=lead),
        _FakeResult(scalar_value=0),
        _FakeResult(all_items=[]),
    ])
    company_session = _FakeSession([_FakeResult(first=company)])
    operational_session = _FakeSession([_FakeResult(first=lead)])
    sessions = _SessionFactory([history_session, company_session, operational_session])

    message = StandardMessage(
        empresa_id=empresa_id,
        canal="evolution",
        identificador_origem="5511999999999",
        texto_mensagem="quanto custa?",
        is_human_agent=False,
    )

    logger = logging.getLogger(api_utils.__name__)
    logger.addHandler(log_capture)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch("db.database.AsyncSessionLocal", sessions))
            stack.enter_context(patch("app.api.main.redis_client", fake_redis))
            stack.enter_context(patch("app.agents.graph.graph", fake_graph))
            stack.enter_context(patch("app.services.channel_factory.despachar_mensagem", fake_dispatch))
            stack.enter_context(patch("app.api.utils._salvar_historico_saida_ia", fake_save_outbound))
            stack.enter_context(patch("app.api.utils.get_tenant_ai_status", lambda empresa: fake_ai_status))
            stack.enter_context(
                patch(
                    "app.api.utils.resolve_conversation_ai_state",
                    AsyncMock(
                        return_value=SimpleNamespace(
                            state="active",
                            reason="allowed",
                            provider="evolution_legacy",
                            phone_match_state="normalized_match",
                            as_dict=lambda: {
                                "state": "active",
                                "reason": "allowed",
                                "reason_code": "allowed",
                                "reason_label": "IA autorizada",
                                "manual_ai_enabled": True,
                                "test_mode_state": "not_evaluated",
                                "phone_match_state": "normalized_match",
                                "ai_window_state": "active",
                                "human_state": "none",
                                "tenant_ai_state": "enabled",
                                "connection_state": "available",
                                "provider_state": "evolution_legacy",
                                "first_inbound_at": None,
                                "ai_window_started_at": None,
                                "ai_window_expires_at": None,
                                "remaining_seconds": None,
                                "connection_id": str(lead_id),
                                "provider": "evolution_legacy",
                            },
                        )
                    ),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.configured_whatsapp_sequences.maybe_dispatch_configured_sequence",
                    AsyncMock(return_value=SimpleNamespace(handled=False, messages=[])),
                )
            )
            stack.enter_context(patch("sqlalchemy.orm.attributes.flag_modified", lambda *args, **kwargs: None))
            await processar_bloco_mensagens([message], background_tasks=None)
    finally:
        logger.setLevel(previous_level)
        logger.removeHandler(log_capture)

    _assert(fake_graph.calls == 1, "graph.ainvoke deveria ser chamado uma vez")
    _assert(fake_dispatch.await_count == 0, "Falha do grafo nao deve chamar despachar_mensagem")
    _assert(fake_save_outbound.await_count == 0, "Falha do grafo nao deve gravar outbound conversacional")
    _assert(len(fake_redis.setex_calls) == 0, "Falha do grafo nao deve gravar resposta/fallback no Redis")
    _assert(operational_session.commits == 1, "Pendencia operacional deveria ser commitada")
    _assert(lead.ia_ativa is False, "Falha do grafo deveria pausar IA do lead")
    _assert(lead.status_atendimento == "manual", "Falha do grafo deveria marcar atendimento manual")

    erro_operacional = lead.dados_adicionais.get("autoatendimento_erro")
    _assert(isinstance(erro_operacional, dict), "Erro operacional não foi salvo em dados_adicionais")
    _assert(erro_operacional.get("codigo") == "GRAPH_AINVOKE_EXCEPTION", erro_operacional)
    _assert(erro_operacional.get("status") == "pendente_revisao_manual", erro_operacional)
    _assert(erro_operacional.get("requer_revisao_manual") is True, erro_operacional)
    _assert("boom-controlado" not in str(erro_operacional), "Mensagem da excecao nao deve ir para o lead")
    _assert("sk-test" not in str(erro_operacional), "Chave nao deve ir para o lead")

    rendered_logs = "\n".join(record.getMessage() for record in log_capture.records)
    _assert("Erro no grafo; resposta automática não enviada" in rendered_logs, "Log de excecao do grafo ausente")
    _assert("Lead marcado para revisão manual" in rendered_logs, "Log operacional interno ausente")


async def main() -> int:
    await _run_graph_failure_case()
    print("verificar_falha_grafo_sem_fallback_conversacional: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"verificar_falha_grafo_sem_fallback_conversacional: FALHOU: {exc}", file=sys.stderr)
        raise SystemExit(1)
