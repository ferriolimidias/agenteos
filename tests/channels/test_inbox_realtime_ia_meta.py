from pathlib import Path
import unittest


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_meta_webhook_publishes_canonical_inbox_event_after_commit():
    source = _read("app/api/routers/meta_webhook.py")
    commit_index = source.index("await session.commit()")
    broadcast_index = source.index('\"type\": \"inbox.message.created\"')

    assert commit_index < broadcast_index
    assert '\"tipo_evento\": \"nova_mensagem_inbound\"' in source
    assert '\"lead_id\": operational_values[\"lead_id\"]' in source
    assert '\"telefone\": operational_values[\"sender_id\"]' in source
    assert '\"direction\": \"inbound\"' in source
    assert '\"provider\": operational.provider.value' in source
    assert '\"channel\": operational.channel.value' in source
    assert "await handle_debouncer(agent_message, None)" in source
    assert broadcast_index < source.index("await handle_debouncer(agent_message, None)")
    assert '"direction": "inbound"' in source
    assert "agent_message = StandardMessage(" in source
    assert "Capture every ORM-derived value needed after commit" in source


def test_evolution_webhook_identifies_the_persisted_conversation_in_realtime_events():
    source = _read("app/api/routers/webhook.py")
    commit_index = source.index("await session.commit()", source.index('"type": "NOVA_MENSAGEM"') - 2500)
    broadcast_index = source.index('"type": "NOVA_MENSAGEM"')
    assert commit_index < broadcast_index
    assert '"conversation_id": str(nova_msg.channel_conversation_id)' in source


def test_audio_transcription_update_identifies_its_persisted_conversation():
    source = _read("app/api/routers/webhook.py")
    transcription = source.split("async def _tarefa_transcricao_audio_e_opcional_ia", 1)[1]
    event = transcription.split('"type": "ATUALIZACAO_MENSAGEM"', 1)[1].split("if disparar_ia", 1)[0]
    assert '"conversation_id": str(row.channel_conversation_id)' in transcription
    assert '"conversation_id": payload["conversation_id"]' in event


def test_ai_gate_state_is_broadcast_for_inbound_processing():
    source = _read("app/api/utils.py")

    assert "async def _broadcast_ai_state_update" in source
    assert '\"type\": \"inbox.ai_state.updated\"' in source
    assert "await _broadcast_ai_state_update(" in source
    assert "Grafo sem resposta_final" in source
    assert "_extract_agent_block_reason" in source
    assert "ai.debounce.scheduled" in source
    assert "ai.lock.acquired" in source
    assert "ai.agent.starting" in source
    assert "ai.dispatch.started" in source
    assert "processing_state" in source


def test_inbox_frontend_accepts_canonical_events_and_refreshes_without_f5():
    source = _read("frontend/src/pages/tenant/Inbox.jsx")

    assert 'tipoEventoV2 === \"inbox.message.created\"' in source
    assert 'tipoEventoV2 === \"inbox.ai_state.updated\"' in source
    assert "const novaMensagem = data?.mensagem || data?.message" in source
    assert "void fetchLeads();" in source
    assert "scheduleFetchLeads(10000);" in source
    assert "translateAiState" in source
    assert "IA autorizada" in source
    assert "IA processando" in source
    assert "manual_enabled" in source
    assert "photoRequestsRef.current.has(cacheKey)" in source
    assert "enabled: desiredEnabled" in source


def test_manual_ai_state_uses_single_canonical_source_and_explicit_setter():
    service = _read("app/services/rosangela_ai_control.py")
    empresas = _read("app/api/routers/empresas.py")
    inbox = _read("app/api/routers/inbox.py")
    utils = _read("app/api/utils.py")

    assert "class ConversationManualAIState" in service
    assert "def get_conversation_manual_ai_state" in service
    assert 'source="crm_leads.ia_ativa"' in service
    assert "def set_conversation_ai_manual_state" in service
    assert "novo_estado = not" not in empresas
    assert "payload: LeadAIStateUpdate" in empresas
    assert "enabled=bool(payload.enabled)" in empresas
    assert '"type": "inbox.ai_state.updated"' in empresas
    assert "get_conversation_manual_ai_state(lead)" in inbox
    assert '"manual_enabled": manual_state.manual_enabled' in inbox
    assert '"manual_enabled": bool(getattr(lead, "ia_ativa", True))' in utils
    assert '"payload": {' in utils


def test_manual_meta_outbound_flow_preserves_provider_and_skips_fiscal_validator():
    inbox = _read("app/api/routers/inbox.py")
    channel_factory = _read("app/services/channel_factory.py")

    manual_block = inbox.split('async def enviar_mensagem(', 1)[1].split('@router.post("/{empresa_id}/inbox/{telefone}/send_media")', 1)[0]
    assert 'provider == "meta_whatsapp"' in manual_block
    assert "despachar_mensagem(" in manual_block
    assert 'canal="whatsapp"' in manual_block
    assert 'provider="meta_whatsapp" if provider == "meta_whatsapp" else provider' in manual_block
    assert "AgenteFiscal" not in manual_block
    assert "fiscal_validation_failed" not in manual_block
    assert "dispatch_outbound_message" in manual_block
    assert "MetaWhatsAppProvider" in channel_factory


def test_technical_ai_failure_does_not_disable_manual_ai_toggle():
    utils = _read("app/api/utils.py")
    exception_block = utils.split('"ai.processing.failed"', 1)[1].split("finally:", 1)[0]
    assert "_pausar_lead_para_humano" not in exception_block
    assert "lead_autoatendimento.ia_ativa = False" not in exception_block
    assert "O atendimento automatico continua configurado." in exception_block

    assert 'technical_failure_reasons = technical_failure_reasons | {"fiscal_validation_failed"}' in utils
    assert "Falha operacional registrada para reprocessamento, sem handoff." in utils
    assert "lead_autoatendimento.ia_ativa = False" not in utils.split('technical_failure_reasons = technical_failure_reasons |', 1)[1].split('finally:', 1)[0]
    assert "if block_reason == \"fiscal_validation_failed\":" in utils


def test_tags_officials_support_operacional_and_no_infinite_retry_source():
    empresas = _read("app/api/routers/empresas.py")
    inbox = _read("frontend/src/pages/tenant/Inbox.jsx")

    assert 'TagCRMTipo = Literal["etapa_funil", "comportamento", "conversao", "operacional"]' in empresas
    tags_loader = inbox.split("const fetchGruposETags = useCallback", 1)[1].split("useEffect", 1)[0]
    assert "setTimeout" not in tags_loader
    assert "setInterval" not in tags_loader
    assert ".retry" not in tags_loader


class InboxRealtimeIAMetaUnittest(unittest.TestCase):
    def test_meta_webhook_publishes_canonical_inbox_event_after_commit_unittest(self):
        test_meta_webhook_publishes_canonical_inbox_event_after_commit()

    def test_evolution_webhook_identifies_the_persisted_conversation_unittest(self):
        test_evolution_webhook_identifies_the_persisted_conversation_in_realtime_events()

    def test_audio_transcription_update_identifies_its_persisted_conversation_unittest(self):
        test_audio_transcription_update_identifies_its_persisted_conversation()

    def test_ai_gate_state_is_broadcast_for_inbound_processing_unittest(self):
        test_ai_gate_state_is_broadcast_for_inbound_processing()

    def test_inbox_frontend_accepts_canonical_events_and_refreshes_without_f5_unittest(self):
        test_inbox_frontend_accepts_canonical_events_and_refreshes_without_f5()

    def test_manual_ai_state_uses_single_canonical_source_and_explicit_setter_unittest(self):
        test_manual_ai_state_uses_single_canonical_source_and_explicit_setter()

    def test_manual_meta_outbound_flow_preserves_provider_and_skips_fiscal_validator_unittest(self):
        test_manual_meta_outbound_flow_preserves_provider_and_skips_fiscal_validator()

    def test_technical_ai_failure_does_not_disable_manual_ai_toggle_unittest(self):
        test_technical_ai_failure_does_not_disable_manual_ai_toggle()

    def test_tags_officials_support_operacional_and_no_infinite_retry_source_unittest(self):
        test_tags_officials_support_operacional_and_no_infinite_retry_source()
