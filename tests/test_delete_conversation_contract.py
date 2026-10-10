from pathlib import Path


ROOT = Path(__file__).parents[1]
ENDPOINT = (ROOT / "app/api/routers/empresas.py").read_text()
INBOX = (ROOT / "frontend/src/pages/tenant/Inbox.jsx").read_text()
DELETE_FUNCTION = ENDPOINT[ENDPOINT.index("async def deletar_lead") :]


def test_delete_endpoint_cleans_followups_before_hard_delete():
    marker = "delete(FollowupExecution).where("
    assert marker in DELETE_FUNCTION
    assert DELETE_FUNCTION.index(marker) < DELETE_FUNCTION.index("await db.delete(lead)")
    assert "FollowupExecution.lead_id == lead.id" in DELETE_FUNCTION
    assert "FollowupExecution.empresa_id == lead.empresa_id" in DELETE_FUNCTION


def test_inbox_delete_contract_is_conversation_scoped():
    assert "api.delete(`/empresas/${empresa_id}/crm/leads/${selectedLead.id}`" in INBOX
    assert "conversation_id: conversationId" in INBOX
    assert "Certeza que deseja excluir esta conversa e todo o seu histórico de mensagens?" in INBOX


def test_delete_error_label_matches_current_endpoint_entity():
    assert 'alert("Erro ao excluir a conversa.")' in INBOX
