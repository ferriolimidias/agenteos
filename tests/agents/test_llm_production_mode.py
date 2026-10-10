import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.atendimento.agent import AtendimentoAgent, TenantLLMUnavailable
from app.agents.llm.mock import MockAgentLLM
from app.agents.llm.openai_provider import OpenAIAgentLLM


class LLMProductionModeTests(unittest.TestCase):
    def test_configured_tenant_uses_real_provider_without_new_flag(self):
        with patch.dict(os.environ, {}, clear=True):
            agent = AtendimentoAgent()
            provider = agent._resolve_llm_provider(
                {
                    "has_ai": True,
                    "_openai_api_key": "REDACTED_FOR_TEST",
                    "modelo_ia": "gpt-4o-mini",
                    "empresa_id": "tenant-a",
                }
            )

        self.assertIsInstance(provider, OpenAIAgentLLM)

    def test_unconfigured_tenant_does_not_get_mock_in_production(self):
        with patch.dict(os.environ, {}, clear=True):
            agent = AtendimentoAgent()
            with self.assertRaises(TenantLLMUnavailable):
                agent._resolve_llm_provider({"has_ai": False})

    def test_mock_requires_explicit_test_mode(self):
        with patch.dict(os.environ, {"AGENTEOS_LLM_MODE": "mock"}, clear=True):
            agent = AtendimentoAgent()
            provider = agent._resolve_llm_provider({"has_ai": False})

        self.assertIsInstance(provider, MockAgentLLM)

    def test_openai_provider_has_no_enable_real_flag_gate(self):
        source = Path("app/agents/llm/openai_provider.py").read_text(encoding="utf-8")
        self.assertNotIn("AGENTEOS_ENABLE_REAL_OPENAI", source)

    def test_openai_runtime_error_does_not_alert_via_evolution(self):
        source = Path("app/core/llm_factory.py").read_text(encoding="utf-8")
        self.assertNotIn('canal="evolution"', source)
        self.assertNotIn("despachar_mensagem", source)


if __name__ == "__main__":
    unittest.main()
