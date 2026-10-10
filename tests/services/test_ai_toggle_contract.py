from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.rosangela_ai_control import (
    initialize_conversation_ai_from_campaign,
    set_conversation_ai_manual_state,
)


def _lead(*, enabled: bool, status: str = "aberto"):
    return SimpleNamespace(
        id="lead-a",
        empresa_id="tenant-a",
        ia_ativa=enabled,
        status_atendimento=status,
        bot_pausado_ate="stale-pause" if status == "manual" else None,
        dados_adicionais={},
    )


def test_ai_toggle_off_persists_canonical_gate():
    lead = _lead(enabled=True)
    with patch("app.services.rosangela_ai_control.flag_modified"):
        state = set_conversation_ai_manual_state(lead, enabled=False, actor="user-a", reason="inbox_toggle")

    assert lead.ia_ativa is False
    assert lead.status_atendimento == "aberto"
    assert state.manual_enabled is False
    assert lead.dados_adicionais["ai_manual_state"]["enabled"] is False


def test_ai_toggle_on_releases_human_state_without_replaying_messages():
    lead = _lead(enabled=False, status="manual")
    with patch("app.services.rosangela_ai_control.flag_modified"):
        state = set_conversation_ai_manual_state(lead, enabled=True, actor="user-a", reason="inbox_toggle")

    assert lead.ia_ativa is True
    assert lead.status_atendimento == "aberto"
    assert lead.bot_pausado_ate is None
    assert state.manual_enabled is True


def test_ai_toggle_is_idempotent_for_on_and_off():
    lead = _lead(enabled=True)
    with patch("app.services.rosangela_ai_control.flag_modified"):
        set_conversation_ai_manual_state(lead, enabled=True, reason="inbox_toggle")
        set_conversation_ai_manual_state(lead, enabled=False, reason="inbox_toggle")
        set_conversation_ai_manual_state(lead, enabled=False, reason="inbox_toggle")
        set_conversation_ai_manual_state(lead, enabled=True, reason="inbox_toggle")

    assert lead.ia_ativa is True
    assert lead.status_atendimento == "aberto"


def test_ai_toggle_route_keeps_tenant_scoped_lookup_and_response_contract():
    source = Path("app/api/routers/empresas.py").read_text(encoding="utf-8")
    route = source.split('@router.patch("/{empresa_id}/crm/{lead_id}/toggle-ia"', 1)[1]
    assert "CRMLead.id == lead_uuid" in route
    assert "CRMLead.empresa_id == emp_uuid" in route
    assert '"manual_enabled": final_manual_state.manual_enabled' in route
    assert '"status_atendimento": str(lead.status_atendimento or "aberto")' in route


def test_campaign_ai_off_initializes_single_conversation_gate():
    lead = _lead(enabled=True)
    with patch("app.services.rosangela_ai_control.flag_modified"):
        state = initialize_conversation_ai_from_campaign(lead, enabled=False, campaign_id="campaign-a")
    assert lead.ia_ativa is False
    assert state.manual_enabled is False
    assert lead.dados_adicionais["ai_manual_state"]["source"] == "campaign"


def test_campaign_ai_on_does_not_override_manual_off():
    lead = _lead(enabled=True)
    with patch("app.services.rosangela_ai_control.flag_modified"):
        set_conversation_ai_manual_state(lead, enabled=False, reason="inbox_toggle")
        state = initialize_conversation_ai_from_campaign(lead, enabled=True, campaign_id="campaign-a")
    assert lead.ia_ativa is False
    assert state.manual_enabled is False


def test_campaign_ai_on_initializes_new_conversation_even_after_manual_off():
    lead = _lead(enabled=True)
    with patch("app.services.rosangela_ai_control.flag_modified"):
        set_conversation_ai_manual_state(lead, enabled=False, reason="inbox_toggle")
        state = initialize_conversation_ai_from_campaign(
            lead,
            enabled=True,
            campaign_id="campaign-a",
            created_new_conversation=True,
        )
    assert lead.ia_ativa is True
    assert state.manual_enabled is True
    assert lead.dados_adicionais["ai_manual_state"]["source"] == "campaign"
