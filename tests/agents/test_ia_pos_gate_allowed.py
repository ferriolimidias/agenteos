import asyncio
import unittest
from unittest.mock import AsyncMock

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.graph import run_new_agent_flow
from app.agents.schemas import IncomingMessage


class BrokenPlanner:
    async def plan(self, **kwargs):
        raise RuntimeError("planner failed with secret-like sk-test-value")


class AlwaysBlockingFiscal:
    calls = 0

    def validate(self, **kwargs):
        self.calls += 1
        return [{"code": "blocked_for_test", "severity": "error", "message": "blocked"}]


class RecordingFiscal:
    def __init__(self):
        self.calls = 0

    def validate(self, **kwargs):
        self.calls += 1
        return []


class IAPosGateAllowedTests(unittest.TestCase):
    def _run_agent(self, agent):
        agent._load_tenant_config = AsyncMock(  # type: ignore[attr-defined]
            return_value={
                "empresa_id": "tenant-1",
                "has_ai": True,
                "modelo_ia": "gpt-4o-mini",
                "nome_empresa": "Tenant",
            }
        )
        return asyncio.run(
            run_new_agent_flow(
                {"empresa_id": "tenant-1", "lead_id": "lead-1", "canal": "whatsapp"},
                IncomingMessage(text="Olá", tenant_id="tenant-1"),
                atendimento_agent=agent,
            )
        )

    def test_empty_llm_response_is_structured_not_generic_operational_error(self):
        agent = AtendimentoAgent(llm=MockAgentLLM(responses=[""]), tool_planner=MockToolPlanner())

        output = self._run_agent(agent)

        self.assertEqual(output.response_text, "")
        self.assertEqual(output.metadata["blocked_reason"], "tenant_llm_empty_response")
        self.assertEqual(output.metadata["operational_error"], "OpenAI returned empty response")
        self.assertEqual(output.guard_result.reason_codes, ["tenant_llm_empty_response"])

    def test_tool_planner_error_is_structured_and_sanitized(self):
        agent = AtendimentoAgent(llm=MockAgentLLM(), tool_planner=BrokenPlanner())

        output = self._run_agent(agent)

        self.assertEqual(output.response_text, "")
        self.assertEqual(output.metadata["blocked_reason"], "tenant_tool_planner_error")
        self.assertNotIn("sk-test-value", output.metadata["operational_error"])
        self.assertEqual(output.guard_result.reason_codes, ["tenant_tool_planner_error"])

    def test_general_rosangela_text_uses_general_guard_not_fiscal_validator(self):
        fiscal = RecordingFiscal()
        agent = AtendimentoAgent(
            llm=MockAgentLLM(responses=["Olá! O atendimento da Rosângela funciona com acolhimento inicial e orientação sobre horários."]),
            tool_planner=MockToolPlanner(),
            fiscal=fiscal,
        )

        output = self._run_agent(agent)

        self.assertIn("Rosângela", output.response_text)
        self.assertIsNone(output.metadata["blocked_reason"])
        self.assertEqual(output.metadata["fiscal_findings"], [])
        self.assertEqual(fiscal.calls, 0)

    def test_event_price_question_does_not_enter_unconfigured_fiscal_flow(self):
        fiscal = AlwaysBlockingFiscal()
        agent = AtendimentoAgent(
            llm=MockAgentLLM(responses=["O investimento é de R$ 70. Você gostaria de saber como funciona?"]),
            tool_planner=MockToolPlanner(),
            fiscal=fiscal,
        )

        output = self._run_agent(agent)

        self.assertIn("R$ 70", output.response_text)
        self.assertIsNone(output.metadata["blocked_reason"])
        self.assertEqual(fiscal.calls, 0)

    def test_fiscal_block_preserves_blocked_reason_for_explicit_budget_flow(self):
        fiscal = AlwaysBlockingFiscal()
        agent = AtendimentoAgent(
            llm=MockAgentLLM(responses=["Vou verificar e retorno em breve.", "Vou verificar e retorno em breve."]),
            tool_planner=MockToolPlanner(),
            fiscal=fiscal,
        )
        agent._load_tenant_config = AsyncMock(  # type: ignore[attr-defined]
            return_value={
                "empresa_id": "tenant-1",
                "has_ai": True,
                "modelo_ia": "gpt-4o-mini",
                "nome_empresa": "Tenant",
                "agent_type": "fiscal",
            }
        )

        output = asyncio.run(
            run_new_agent_flow(
                {"empresa_id": "tenant-1", "lead_id": "lead-1", "canal": "whatsapp", "active_intent": "orcamento"},
                IncomingMessage(text="Quanto custa?", tenant_id="tenant-1"),
                atendimento_agent=agent,
            )
        )

        self.assertEqual(output.response_text, "")
        self.assertEqual(output.metadata["blocked_reason"], "fiscal_validation_failed")
        self.assertEqual(output.metadata["operational_error"], "fiscal_validation_failed")
        self.assertEqual(output.guard_result.reason_codes, ["fiscal_validation_failed"])
        self.assertGreaterEqual(fiscal.calls, 1)


if __name__ == "__main__":
    unittest.main()
