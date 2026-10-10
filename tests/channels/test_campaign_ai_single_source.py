from pathlib import Path
from unittest.mock import patch

from app.services.rosangela_ai_control import (
    initialize_conversation_ai_from_campaign,
    set_conversation_ai_manual_state,
)
from tests.services.test_ai_toggle_contract import _lead


ROOT = Path(__file__).parents[2]


def test_campaign_ui_off_is_carried_to_snapshot_and_start_without_default_reinterpretation():
    frontend = (ROOT / "frontend/src/pages/tenant/Campanhas.jsx").read_text()
    service = (ROOT / "app/services/campaign_central_service.py").read_text()
    dispatch = (ROOT / "app/services/campaign_central_dispatch_service.py").read_text()
    assert "ai_reply_enabled: true" in frontend
    assert "checked={campaignForm.ai_reply_enabled !== false}" in frontend
    assert 'ai_reply_enabled = bool(payload.get("ai_reply_enabled", True))' in service
    assert "ai_reply_enabled=bool(filters.get(\"ai_reply_enabled\"))" in dispatch
    assert '"ai_reply_enabled": bool(filters.get("ai_reply_enabled"))' in dispatch


def test_campaign_off_initializes_normal_gate_and_campaign_on_preserves_manual_off():
    lead = _lead(enabled=True)
    with patch("app.services.rosangela_ai_control.flag_modified"):
        state = initialize_conversation_ai_from_campaign(lead, enabled=False, campaign_id="c-off")
    assert lead.ia_ativa is False
    assert state.manual_enabled is False

    with patch("app.services.rosangela_ai_control.flag_modified"):
        set_conversation_ai_manual_state(lead, enabled=False, reason="inbox_toggle")
        state = initialize_conversation_ai_from_campaign(lead, enabled=True, campaign_id="c-on")
    assert lead.ia_ativa is False
    assert state.manual_enabled is False


def test_runtime_has_one_ai_gate_and_canonical_history_context():
    webhook = (ROOT / "app/api/routers/meta_webhook.py").read_text()
    inbox = (ROOT / "app/api/routers/inbox.py").read_text()
    utils = (ROOT / "app/api/utils.py").read_text()
    assert "resolve_campaign_reply_policy" not in webhook
    assert "resolve_campaign_reply_policy" not in inbox
    assert "resolve_conversation_ai_state" in webhook
    assert 'lead_id=lead_id_hist' in utils
    assert 'conversation_id=getattr(mensagens[0], "channel_conversation_id", None)' in utils
