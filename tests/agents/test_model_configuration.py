from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.llm.openai_provider import OpenAIAgentLLM
from app.agents.tenant_config import build_tenant_agent_config
from app.core.model_catalog import ALLOWED_MODEL_IDS, MODEL_OPTIONS
from app.schemas import IAConfigUpdateRequest


def test_model_catalog_contains_canonical_luna_and_preserves_gpt54_mini():
    options = {item["value"]: item["label"] for item in MODEL_OPTIONS}
    assert options["gpt-5.4-mini"] == "GPT-5.4 Mini"
    assert options["gpt-5.6-luna"] == "GPT-5.6 Luna"


def test_luna_model_is_accepted_and_label_is_not_a_model_id():
    assert IAConfigUpdateRequest(modelo_ia="gpt-5.6-luna").modelo_ia == "gpt-5.6-luna"
    with pytest.raises(ValueError):
        IAConfigUpdateRequest(modelo_ia="gpt-5.6 Luna")


def test_new_canonical_model_id_is_accepted_for_tenant_scoped_validation():
    assert IAConfigUpdateRequest(modelo_ia="gpt-future-2027-preview").modelo_ia == "gpt-future-2027-preview"
    with pytest.raises(ValueError):
        IAConfigUpdateRequest(modelo_ia="GPT Future Label")


def test_gpt54_mini_remains_accepted():
    assert "gpt-5.4-mini" in ALLOWED_MODEL_IDS
    assert IAConfigUpdateRequest(modelo_ia="gpt-5.4-mini").modelo_ia == "gpt-5.4-mini"


def test_tenant_model_switch_is_dynamic_without_persistence():
    base = dict(
        id="tenant-test",
        nome_empresa="Teste",
        nome_agente="Agente",
        mensagem_saudacao=None,
        ia_personalidade=None,
        ia_regras_negocio=None,
        atendente_prompt=None,
        credenciais_canais={},
    )
    empresa_a = SimpleNamespace(modelo_ia="gpt-5.4-mini", **base)
    empresa_b = SimpleNamespace(modelo_ia="gpt-5.6-luna", **base)
    assert build_tenant_agent_config(empresa_a)["modelo_ia"] == "gpt-5.4-mini"
    assert build_tenant_agent_config(empresa_b)["modelo_ia"] == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_luna_runtime_model_reaches_chat_completions():
    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    with patch("openai.AsyncOpenAI", FakeOpenAI):
        await OpenAIAgentLLM.from_config(
            {
                "modelo_ia": "gpt-5.6-luna",
                "_openai_api_key": "REDACTED_FOR_TEST",
                "empresa_id": "tenant-test",
            }
        ).generate(system_prompt="system", messages=[{"role": "user", "content": "hello"}])

    assert captured["model"] == "gpt-5.6-luna"
    assert "temperature" not in captured


@pytest.mark.asyncio
async def test_luna_request_omits_unsupported_temperature_while_other_models_keep_default_tuning():
    captured = []

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    with patch("openai.AsyncOpenAI", FakeOpenAI):
        for model in ("gpt-5.6-luna", "gpt-5.4-mini"):
            await OpenAIAgentLLM.from_config(
                {"modelo_ia": model, "_openai_api_key": "REDACTED_FOR_TEST", "empresa_id": "tenant-test"}
            ).generate(system_prompt="system", messages=[{"role": "user", "content": "hello"}])

    assert "temperature" not in captured[0]
    assert captured[1]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_explicitly_verified_dynamic_model_uses_minimal_provider_parameters():
    captured = {}
    client_kwargs = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            client_kwargs.update(kwargs)
            self.chat = SimpleNamespace(completions=FakeCompletions())

    with patch("openai.AsyncOpenAI", FakeOpenAI):
        await OpenAIAgentLLM.from_config({
            "modelo_ia": "gpt-new-preview",
            "modelo_ia_request_profile": "chat_minimal",
            "_openai_api_key": "REDACTED_FOR_TEST",
            "empresa_id": "tenant-test",
            "credenciais_canais": {"openai_project_id": "proj-synthetic"},
        }).generate(system_prompt="system", messages=[{"role": "user", "content": "hello"}])

    assert captured["model"] == "gpt-new-preview"
    assert "temperature" not in captured
    assert set(captured) == {"model", "messages"}
    assert client_kwargs["project"] == "proj-synthetic"
