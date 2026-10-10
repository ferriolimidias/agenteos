import asyncio
import unittest
from unittest.mock import AsyncMock

from app.agents.atendimento.agent import AtendimentoAgent
from app.agents.llm.mock import MockAgentLLM
from app.agents.planner.tool_planner import MockToolPlanner
from app.agents.graph import NewAgentGraph
from app.services.meta_webhook import normalize_meta_payload


class CtwaAgentRecoveryTests(unittest.TestCase):
    def test_graph_first_turn_keeps_agent_dispatch_decision_valid(self):
        agent = AtendimentoAgent(
            llm=MockAgentLLM(responses=['{"needs_extension": false, "extension": ""}']),
            tool_planner=MockToolPlanner(),
        )
        agent._load_tenant_config = AsyncMock(return_value={
            "empresa_id": "tenant-ctwa",
            "has_ai": True,
            "mensagem_saudacao": "GREETING_ALPHA",
        })  # type: ignore[attr-defined]

        output = asyncio.run(NewAgentGraph(agent).ainvoke({
            "empresa_id": "tenant-ctwa",
            "lead_id": "lead-ctwa",
            "canal": "simulador",
            "identificador_origem": "contact-ctwa",
            "first_agent_response": True,
            "mensagens": ["Usuario: Pode me passar mais informações?"],
        }))

        self.assertEqual(output["first_turn_decision"], {"needs_extension": False, "extension": ""})
        self.assertEqual(output["pendencias"], [])
        self.assertEqual(output["status_conversa"], "IA_ATIVA")

    def test_text_referral_is_customer_message_and_not_technical_event(self):
        events = normalize_meta_payload({
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "waba",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "phone"},
                        "messages": [{
                            "from": "customer",
                            "id": "wamid.ctwa",
                            "timestamp": "1777137556",
                            "type": "text",
                            "text": {"body": "Pode me passar mais informações?"},
                            "referral": {
                                "ctwa_clid": "clid",
                                "source_id": "ad",
                                "source_type": "ad",
                            },
                        }],
                    },
                }],
            }],
        })

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_kind, "message")
        self.assertEqual(events[0].message_type, "text")
        self.assertEqual(events[0].referral, {
            "ctwa_clid": "clid",
            "source_id": "ad",
            "source_type": "ad",
        })


if __name__ == "__main__":
    unittest.main()
