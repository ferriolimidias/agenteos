from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_campaign_ai_reply_contract_is_persisted_and_frozen():
    service = (ROOT / "app/services/campaign_central_service.py").read_text()
    dispatch = (ROOT / "app/services/campaign_central_dispatch_service.py").read_text()
    model = (ROOT / "db/models.py").read_text()
    assert "ai_reply_enabled = Column(Boolean" in model
    assert '"ai_reply_enabled": ai_reply_enabled' in service
    assert 'filters.get("ai_reply_enabled")' in dispatch


def test_campaign_ai_reply_gate_runs_after_inbound_broadcast_and_before_debouncer():
    source = (ROOT / "app/api/routers/meta_webhook.py").read_text()
    persist_block = source.split("async def _persist_event", 1)[1].split("async def _verify", 1)[0]
    assert "await manager.broadcast_to_empresa" in source
    assert "resolve_conversation_ai_state" in source
    assert "if ai_gate.state == \"active\"" in source
    assert persist_block.index("await manager.broadcast_to_empresa") < persist_block.index("resolve_conversation_ai_state") < persist_block.index("await handle_debouncer")


def test_inbox_ai_state_keeps_campaign_disabled_label():
    source = (ROOT / "app/api/routers/inbox.py").read_text()
    block = source.split("label, description = _ai_state_message", 1)[1].split("async def ", 1)[0]
    assert '"campaign_ai_disabled"' in source
    assert '"IA desativada pela campanha"' in source
    assert block.index('elif state.reason == "campaign_ai_disabled"') < block.index("return {")


def test_campaign_outbound_realtime_uses_persisted_history_after_commit():
    source = (ROOT / "app/services/campaign_central_dispatch_service.py").read_text()
    assert "await session.commit()" in source
    assert "broadcast_persisted_campaign_history" in source
    assert '"type": "inbox.message.created"' in source
    assert 'MensagemHistorico.origem_mensagem == "campaign"' in source


def test_meta_inbound_uses_canonical_conversation_lead_before_ai_policy():
    source = (ROOT / "app/api/routers/meta_webhook.py").read_text()
    persist_block = source.split("async def _persist_event", 1)[1].split("async def _verify", 1)[0]
    assert "conversation_lead_id = getattr(operational_conversation.conversation, \"lead_id\", None)" in source
    assert "message_lead = canonical_lead" in source
    assert '"lead_id": str(message_lead.id)' in source
    assert "lead=side_lead" in source
    assert persist_block.index("message_lead = canonical_lead") < persist_block.index("nova_msg = MensagemHistorico")
    nova_msg_index = persist_block.index("nova_msg = MensagemHistorico")
    assert nova_msg_index < persist_block.index("await session.commit()", nova_msg_index)
    commit_index = persist_block.index("await session.commit()", nova_msg_index)
    assert commit_index < persist_block.index("resolve_conversation_ai_state", commit_index)
    assert persist_block.index("resolve_conversation_ai_state", commit_index) < persist_block.index("await handle_debouncer", commit_index)


def test_multichannel_inbox_uses_meta_profile_for_campaign_display_name():
    source = (ROOT / "app/api/routers/inbox.py").read_text()
    assert "select(ChannelConversation, CRMLead, ChannelContact)" in source
    assert 'name_source in {"manual", "meta_profile"}' in source
    assert '"lead_nome": display_name or None' in source
    assert 'conversation_metadata.get("campaign_created_new_conversation") is True' in source


def test_campaign_is_the_initializer_and_normal_conversation_gate_is_runtime_authority():
    dispatch = (ROOT / "app/services/campaign_central_dispatch_service.py").read_text()
    webhook = (ROOT / "app/api/routers/meta_webhook.py").read_text()
    inbox = (ROOT / "app/api/routers/inbox.py").read_text()
    assert "initialize_conversation_ai_from_campaign" in dispatch
    assert 'enabled=bool(campaign.ai_reply_enabled)' in dispatch
    assert "created_new_conversation=bool(" in dispatch
    assert "operational.conversation.metadata_json" in dispatch
    assert "operational_conversation.conversation.metadata_json" not in dispatch
    assert "resolve_campaign_reply_policy" not in webhook
    assert "resolve_campaign_reply_policy" not in inbox


def test_ai_history_uses_message_canonical_ids_before_phone_fallback():
    source = (ROOT / "app/api/utils.py").read_text()
    assert "lead_id: str | None = None" in source
    assert "conversation_id: str | None = None" in source
    assert 'lead_id=lead_id_hist' in source
    assert 'conversation_id=getattr(mensagens[0], "channel_conversation_id", None)' in source
