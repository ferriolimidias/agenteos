import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.rosangela_ai_control import resolve_conversation_ai_state


class _FakeDB:
    async def commit(self):
        return None


def _fixture(*, ai_enabled=True, human_status=None, attribution=None, old=False):
    empresa = SimpleNamespace(id=uuid.uuid4(), credenciais_canais={})
    lead = SimpleNamespace(
        id=uuid.uuid4(), empresa_id=empresa.id, ia_ativa=ai_enabled,
        status_atendimento=human_status, bot_pausado_ate=None,
        dados_adicionais={"meta_attribution": attribution} if attribution else {},
    )
    created = datetime.utcnow() - timedelta(days=3 if old else 0)
    conversation = SimpleNamespace(id=uuid.uuid4(), empresa_id=empresa.id, lead_id=lead.id, metadata_json={}, criado_em=created)
    return empresa, lead, conversation, created


class ReactiveInboundAIGateTests(unittest.IsolatedAsyncioTestCase):
    async def _state(self, **kwargs):
        empresa, lead, conversation, now = _fixture(**kwargs)
        state = await resolve_conversation_ai_state(
            _FakeDB(), empresa=empresa, lead=lead, conversation=conversation,
            connection_id="meta-1", provider="meta_whatsapp", phone="5511999999999",
            trigger="customer_inbound_message", now=datetime.utcnow(), persist=False,
        )
        return state, lead

    async def test_agent_works_without_test_session(self):
        state, _ = await self._state()
        self.assertEqual((state.state, state.reason), ("active", "allowed"))

    async def test_old_conversation_new_inbound_has_no_internal_window_block(self):
        state, _ = await self._state(old=True)
        self.assertEqual((state.state, state.reason), ("active", "allowed"))

    async def test_organic_inbound_with_ai_authorized_is_allowed(self):
        state, _ = await self._state()
        self.assertEqual(state.reason, "allowed")

    async def test_ad_inbound_with_ai_authorized_is_allowed_and_keeps_metadata(self):
        attribution = {"last_touch": {"ctwa_clid": "ctwa-test", "source_id": "ad-1"}}
        state, lead = await self._state(attribution=attribution)
        self.assertEqual(state.reason, "allowed")
        self.assertEqual(lead.dados_adicionais["meta_attribution"]["last_touch"]["ctwa_clid"], "ctwa-test")

    async def test_ai_disabled_still_blocks(self):
        state, _ = await self._state(ai_enabled=False)
        self.assertEqual((state.state, state.reason), ("blocked", "ai_manual_disabled"))

    async def test_human_lock_still_blocks(self):
        state, _ = await self._state(human_status="manual")
        self.assertEqual((state.state, state.reason), ("blocked", "human_takeover"))


if __name__ == "__main__":
    unittest.main()
