from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.llm.openai_provider import OpenAIAgentLLM
from app.agents.tenant_config import build_tenant_agent_config
from app.core.model_catalog import (
    ALLOWED_MODEL_IDS,
    MODEL_OPTIONS,
    is_reasoning_effort_compatible,
    reasoning_effort_capabilities,
)
from app.schemas import IAConfigUpdateRequest
from db.models import Empresa


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


def test_reasoning_capabilities_are_model_specific_and_fail_closed_for_unknown_ids():
    assert reasoning_effort_capabilities("gpt-6-luna")["options"] == ["default", "none", "low", "medium", "high", "xhigh", "max"]
    assert reasoning_effort_capabilities("gpt-6-sol")["options"] == ["default", "none", "low", "medium", "high", "xhigh", "max"]
    for model in ("gpt-6.1-sol", "gpt-6-astra"):
        assert reasoning_effort_capabilities(model)["options"] == ["default", "low", "medium", "high", "xhigh", "max"]
        assert not is_reasoning_effort_compatible(model, "none")
    assert reasoning_effort_capabilities("gpt-5")["options"] == ["default", "minimal", "low", "medium", "high"]
    assert reasoning_effort_capabilities("gpt-5.4-mini")["options"] == ["default", "none", "low", "medium", "high", "xhigh"]
    assert reasoning_effort_capabilities("gpt-4o")["status"] == "not_supported"
    assert reasoning_effort_capabilities("gpt-future-2030")["options"] == ["default"]
    assert not is_reasoning_effort_compatible("gpt-future-2030", "low")


def test_optional_effort_request_preserves_omission_semantics():
    absent = IAConfigUpdateRequest(modelo_ia="gpt-6-luna")
    explicit_default = IAConfigUpdateRequest(modelo_ia_reasoning_effort="default")
    explicit_null = IAConfigUpdateRequest(modelo_ia_reasoning_effort=None)
    assert "modelo_ia_reasoning_effort" not in absent.model_fields_set
    assert "modelo_ia_reasoning_effort" in explicit_default.model_fields_set
    assert "modelo_ia_reasoning_effort" in explicit_null.model_fields_set


def test_empresa_model_declares_reasoning_effort_constraint_for_create_all():
    constraint_names = {constraint.name for constraint in Empresa.__table__.constraints}
    assert "ck_empresas_modelo_ia_reasoning_effort" in constraint_names


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
    empresa_a = SimpleNamespace(modelo_ia="gpt-5.4-mini", modelo_ia_reasoning_effort="high", **base)
    empresa_b = SimpleNamespace(modelo_ia="gpt-5.6-luna", modelo_ia_reasoning_effort=None, **base)
    assert build_tenant_agent_config(empresa_a)["modelo_ia"] == "gpt-5.4-mini"
    assert build_tenant_agent_config(empresa_a)["modelo_ia_reasoning_effort"] == "high"
    assert build_tenant_agent_config(empresa_b)["modelo_ia"] == "gpt-5.6-luna"
    assert build_tenant_agent_config(empresa_b)["modelo_ia_reasoning_effort"] == "default"


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
        for model in ("gpt-5.6-luna", "o1", "o1-mini", "o3", "o3-mini", "o4-mini", "gpt-5.4-mini"):
            await OpenAIAgentLLM.from_config(
                {"modelo_ia": model, "_openai_api_key": "REDACTED_FOR_TEST", "empresa_id": "tenant-test"}
            ).generate(system_prompt="system", messages=[{"role": "user", "content": "hello"}])

    assert all("temperature" not in request for request in captured[:6])
    assert captured[6]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_reasoning_effort_is_forwarded_only_when_explicit_and_sampling_is_omitted():
    captured = []

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    with patch("openai.AsyncOpenAI", FakeOpenAI):
        for model, effort in (
            ("gpt-6-luna", "default"),
            ("gpt-6-luna", "none"),
            ("gpt-6-luna", "low"),
            ("gpt-6-luna", "medium"),
            ("gpt-6-sol", "low"),
            ("gpt-6-sol", "high"),
            ("gpt-6-sol", "max"),
            ("gpt-6.1-sol", "low"),
        ):
            await OpenAIAgentLLM.from_config({
                "modelo_ia": model,
                "modelo_ia_reasoning_effort": effort,
                "_openai_api_key": "REDACTED_FOR_TEST",
                "empresa_id": "tenant-test",
            }).generate(system_prompt="system", messages=[{"role": "user", "content": "hello"}], tools_context={"synthetic": True})

    assert "reasoning_effort" not in captured[0]
    assert "temperature" not in captured[0]  # GPT-6 defaults to reasoning; sampling is incompatible.
    assert [request.get("reasoning_effort") for request in captured[1:]] == ["none", "low", "medium", "low", "high", "max", "low"]
    assert captured[1]["temperature"] == 0.2
    assert all("temperature" not in request for request in captured[2:])
    assert all("tools" not in request for request in captured)


@pytest.mark.asyncio
async def test_provider_propagates_http_400_without_retrying_or_switching_models():
    captured = []

    class ProviderBadRequest(Exception):
        status_code = 400
        body = {"error": {"code": "unsupported_parameter", "param": "reasoning_effort"}}
        request_id = "req-synthetic"

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            raise ProviderBadRequest("synthetic bad request")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    with patch("openai.AsyncOpenAI", FakeOpenAI):
        llm = OpenAIAgentLLM.from_config({
            "modelo_ia": "gpt-6-luna",
            "modelo_ia_reasoning_effort": "high",
            "_openai_api_key": "REDACTED_FOR_TEST",
        })
        with pytest.raises(ProviderBadRequest):
            await llm.generate(system_prompt="system", messages=[{"role": "user", "content": "synthetic"}])

    assert len(captured) == 1
    assert captured[0]["model"] == "gpt-6-luna"
    assert captured[0]["reasoning_effort"] == "high"
    assert "temperature" not in captured[0]


@pytest.mark.parametrize("model,effort", [("gpt-6.1-sol", "none"), ("gpt-6-astra", "none"), ("gpt-4o", "low"), ("gpt-future-2030", "medium")])
def test_provider_refuses_unconfirmed_or_incompatible_effort_without_model_fallback(model, effort):
    with pytest.raises(ValueError, match="not compatible"):
        OpenAIAgentLLM.from_config({
            "modelo_ia": model,
            "modelo_ia_reasoning_effort": effort,
            "_openai_api_key": "REDACTED_FOR_TEST",
        })


def _empresa_ia_config(**overrides):
    values = {
        "id": "tenant-test",
        "modelo_ia": "gpt-6-luna",
        "modelo_ia_reasoning_effort": None,
        "modelo_ia_request_profile": "legacy",
        "ia_personalidade": None,
        "ia_regras_negocio": None,
        "nome_agente": "Agente",
        "mensagem_saudacao": None,
        "conversion_monitor_prompt": None,
        "followup_ativo": False,
        "limite_certeza": 0.65,
        "limite_duvida": 0.45,
        "max_agentes_desempate": 3,
        "informacoes_adicionais": None,
        "coletar_nome": True,
        "telefone_notificacao": None,
        "status_openai": "ok",
        "credenciais_canais": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_ia_config_rejects_incompatible_pair_before_mutating_any_fields():
    from fastapi import HTTPException
    from app.api.routers.empresas import put_ia_config

    company = _empresa_ia_config(nome_agente="Antes")
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: company))
    db = SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock())
    request = IAConfigUpdateRequest(
        nome_agente="Depois",
        modelo_ia="gpt-6.1-sol",
        modelo_ia_reasoning_effort="none",
    )

    with pytest.raises(HTTPException) as error:
        await put_ia_config("tenant-test", request, db)

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "reasoning_effort_incompatible"
    assert db.execute.await_args.args[0]._for_update_arg is not None
    assert company.nome_agente == "Antes"
    assert company.modelo_ia == "gpt-6-luna"
    assert company.modelo_ia_reasoning_effort is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ia_config_saves_model_and_effort_together_and_default_as_null():
    from app.api.routers import empresas as empresas_router

    company = _empresa_ia_config()
    company_result = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: company))
    empty_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[company_result, empty_result]),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    request = IAConfigUpdateRequest(modelo_ia="gpt-6.1-sol", modelo_ia_reasoning_effort="low")

    with patch("app.services.openai_model_catalog.model_is_validated", new=AsyncMock(return_value=True)):
        response = await empresas_router.put_ia_config("tenant-test", request, db)

    assert company.modelo_ia == "gpt-6.1-sol"
    assert company.modelo_ia_reasoning_effort == "low"
    assert company.modelo_ia_request_profile == "chat_minimal"
    assert response["modelo_ia_reasoning_effort"] == "low"
    assert response["reasoning_effort_capabilities"]["options"] == ["default", "low", "medium", "high", "xhigh", "max"]
    assert db.execute.await_args_list[0].args[0]._for_update_arg is not None
    db.commit.assert_awaited_once()


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
