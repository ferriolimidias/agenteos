from pathlib import Path


ROOT = Path(__file__).parents[2]
UTILS = (ROOT / "app/api/utils.py").read_text()
META = (ROOT / "app/api/routers/meta_webhook.py").read_text()
SCHEMA = (ROOT / "app/schemas.py").read_text()
DELETE = (ROOT / "app/api/routers/empresas.py").read_text()
INBOX = (ROOT / "frontend/src/pages/tenant/Inbox.jsx").read_text()


def test_new_meta_inbound_carries_lead_and_conversation_identity():
    assert 'lead_id=operational_values["lead_id"]' in META
    assert "channel_conversation_id=str(operational_conversation.conversation.id)" in META
    assert '"persisted_message_id": str(nova_msg.id)' in META
    assert "lead_id: str | None = None" in SCHEMA


def test_first_contact_is_conversation_scoped_and_marks_agent_first_turn():
    assert "_MH.channel_conversation_id == conversation_uuid_hist" in UTILS
    assert "_MH.id.not_in(persisted_current_ids)" in UTILS
    assert "_MH.external_message_id.not_in(current_external_ids)" in UTILS
    assert '"first_agent_response": previous_ai_outbound_count == 0' in UTILS
    assert "_send_first_configured_greeting" not in UTILS
    assert "estado_final = await graph.ainvoke" in UTILS


def test_greeting_idempotency_key_is_conversation_scoped():
    assert "scoped_runtime_key = f\"{runtime_key}:conversation:{conversation_scope}\"" in UTILS


def test_inbox_delete_is_conversation_scoped_and_preserves_lead_path():
    assert "conversation_id: str | None = None" in DELETE
    assert "ChannelConversation.lead_id == lead.id" in DELETE
    assert "await db.delete(conversation)" in DELETE
    assert "params: { conversation_id: conversationId }" in INBOX


def test_meta_first_turn_trace_is_observable_without_full_greeting_logging():
    assert '"ai.first_turn.trace"' in UTILS
    assert "greeting_hash_prefix" in UTILS
    assert "final_source" in UTILS
    assert "historical_count" in UTILS
