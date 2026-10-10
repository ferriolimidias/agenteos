from pathlib import Path


ROOT = Path(".")
BOUNCER = (ROOT / "app/api/utils.py").read_text(encoding="utf-8")
META_WEBHOOK = (ROOT / "app/api/routers/meta_webhook.py").read_text(encoding="utf-8")
ANAMNESE_ROUTER = (ROOT / "app/api/routers/public_anamnese.py").read_text(encoding="utf-8")
MP_WEBHOOK = (ROOT / "app/services/mercado_pago/leoft_webhook.py").read_text(encoding="utf-8")
FUNNEL_EVENTS = (ROOT / "app/services/leoft_funnel_events.py").read_text(encoding="utf-8")
TRACKING = (ROOT / "apps/anamnese-leonardo/src/features/anamnese/lib/tracking.ts").read_text(encoding="utf-8")
FLOW = (ROOT / "apps/anamnese-leonardo/src/features/anamnese/AnamneseFlow.tsx").read_text(encoding="utf-8")
SUMMARY = (ROOT / "apps/anamnese-leonardo/src/features/anamnese/steps/Summary.tsx").read_text(encoding="utf-8")
PLANNER = (ROOT / "app/agents/planner/tool_planner.py").read_text(encoding="utf-8")


def test_bouncer_keeps_lock_and_drains_messages_arriving_during_processing():
    assert 'BOUNCER_DEBOUNCE_SECONDS = int(os.getenv("BOUNCER_DEBOUNCE_SECONDS", "6"))' in BOUNCER
    assert 'BOUNCER_LOCK_TTL_SECONDS = int(os.getenv("BOUNCER_LOCK_TTL_SECONDS", "120"))' in BOUNCER
    assert 'BOUNCER_LOCK_RENEW_INTERVAL_SECONDS = int(os.getenv("BOUNCER_LOCK_RENEW_INTERVAL_SECONDS", "30"))' in BOUNCER
    assert "while True:" in BOUNCER
    assert "ai.bouncer.next_batch" in BOUNCER
    assert "ai.bouncer.lock_renewed" in BOUNCER
    assert "_bouncer_release_lock(redis_client, lock_key, lock_token)" in BOUNCER
    assert "runtime_key_from_standard_message" in BOUNCER


def test_ctwa_leadsubmitted_uses_system_event_from_meta_webhook():
    assert 'event_type="ctwa_lead_created"' in META_WEBHOOK
    assert 'source_event_id=f"ctwa_lead_created:{nova_msg.id}"' in META_WEBHOOK
    assert 'trigger_source="system"' in META_WEBHOOK


def test_checkout_created_is_the_initiatecheckout_operational_trigger():
    assert 'event_type="checkout_created"' in ANAMNESE_ROUTER
    assert 'source_event_id=f"checkout_created:{checkout_result.reference}"' in ANAMNESE_ROUTER
    assert 'event_type="anamnese_completed"' in ANAMNESE_ROUTER
    conversion_block = ANAMNESE_ROUTER.split("await emit_conversion_domain_event", 1)[1]
    assert 'event_type="checkout_created"' in conversion_block
    assert 'event_type="anamnese_completed"' not in conversion_block


def test_mercado_pago_approved_is_the_purchase_operational_trigger():
    assert 'event_type="payment_confirmed"' in MP_WEBHOOK
    assert 'trigger_ref="mercado_pago_approved"' in MP_WEBHOOK
    assert 'trigger_source="integration"' in MP_WEBHOOK
    assert "mensagem" not in MP_WEBHOOK.lower()


def test_internal_funnel_events_do_not_emit_meta_conversions():
    assert "LeoFTIntegrationEvent" in FUNNEL_EVENTS
    assert 'FUNNEL_INTEGRATION = "leoft_funnel"' in FUNNEL_EVENTS
    assert "integration=FUNNEL_INTEGRATION" in FUNNEL_EVENTS
    assert "emit_conversion_domain_event" not in FUNNEL_EVENTS
    assert "enviar_payload_meta" not in FUNNEL_EVENTS


def test_anamnese_browser_tracks_start_and_payment_actions_without_completion_duplication():
    assert 'trackEvent("AnamneseStarted", { leadToken: data.leadToken })' in FLOW
    assert 'if (name === "AnamneseCompleted" || name === "CompleteRegistration") return;' in TRACKING
    assert '"payment_button_clicked"' in TRACKING
    assert 'fetch("/api/public/anamnese/leoft/event"' in TRACKING


def test_final_screen_keeps_anamnese_before_checkout_and_clear_97_total_copy():
    assert "Avaliação concluída" in SUMMARY
    assert "R$ 97 total" in SUMMARY
    assert "pelo período completo." in SUMMARY
    assert "Confirmar meus 3 meses de treino" in SUMMARY
    assert "pagamento é único" in SUMMARY
    assert "diagnóstico" not in SUMMARY.lower()
    assert "score" not in SUMMARY.lower()


def test_agent_policy_forbids_purchase_from_user_text_and_checkout_from_intent():
    assert "Purchase exige confirmacao operacional" in PLANNER
    assert "Intencao verbal, pedido de proximo passo ou aceite da oferta nao sao checkout" in PLANNER
    assert "InitiateCheckout exige evidencia de entrada operacional real no checkout" in PLANNER
