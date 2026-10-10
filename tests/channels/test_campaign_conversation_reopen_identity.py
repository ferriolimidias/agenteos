from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_campaign_reuses_canonical_conversation_and_reopens_closed_threads():
    source = (ROOT / "app/services/operational_identity.py").read_text()
    assert "canonical_operational_address" in source
    assert "ChannelConversation.atualizado_em.desc().nullslast()" in source
    assert 'conversation.status = "open"' in source
    assert '"closed", "concluido", "concluida", "completed", "archived"' in source


def test_campaign_does_not_supply_an_operational_name():
    source = (ROOT / "app/services/campaign_central_dispatch_service.py").read_text()
    assert '"source": "campaign_history_materialization"' in source
    assert "display_name" not in source


def test_meta_profile_does_not_override_existing_trusted_name_source():
    source = (ROOT / "app/services/operational_identity.py").read_text()
    assert 'existing_name_source in {"manual", "meta_profile"}' in source
    assert 'incoming_name_source == "meta_profile"' in source
    assert 'incoming_metadata.pop("trusted_display_name_source", None)' in source


def test_inbox_uses_persisted_history_conversation_identity_for_legacy_links():
    source = (ROOT / "app/api/routers/inbox.py").read_text()
    assert "latest_history_conversation_id" in source
    assert "func.coalesce(latest_conversation_id, latest_history_conversation_id)" in source


def test_conversation_delete_is_tenant_scoped_and_history_linked():
    source = (ROOT / "app/api/routers/empresas.py").read_text()
    assert "ChannelConversation.empresa_id == emp_uuid" in source
    assert "MensagemHistorico.channel_conversation_id == ChannelConversation.id" in source
