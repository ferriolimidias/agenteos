from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import attendance_quality as service


NOW = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


def message(*, lead, conversation, at, text, direction=None, from_me=False, channel="whatsapp"):
    return SimpleNamespace(
        id=uuid4(), lead_id=lead, channel_conversation_id=conversation, channel=channel,
        criado_em=at, texto=text, direction=direction, from_me=from_me,
        tipo_mensagem="text", metadata_json={}
    )


def test_metrics_use_observed_denominators_and_do_not_call_silence_abandonment():
    lead_a, lead_b, lead_c = uuid4(), uuid4(), uuid4()
    conv_a, conv_b, conv_c = uuid4(), uuid4(), uuid4()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        message(lead=lead_a, conversation=conv_a, at=start + timedelta(hours=1), text="Posso saber o preço?", direction="inbound"),
        message(lead=lead_a, conversation=conv_a, at=start + timedelta(hours=2), text="R$ 100", direction="outbound", from_me=True),
        message(lead=lead_a, conversation=conv_a, at=start + timedelta(days=2), text="Ainda tenho uma dúvida?", direction="inbound"),
        message(lead=lead_b, conversation=conv_b, at=start + timedelta(days=3), text="Quero saber o valor?", direction="inbound"),
        message(lead=lead_c, conversation=conv_c, at=start + timedelta(days=2), text="Oferta de pagamento em parcelas", direction="outbound", from_me=True),
        message(lead=lead_b, conversation=None, at=start + timedelta(days=4), text="Sem vínculo?", direction=None),
    ]
    p = service.filters(empresa_id=uuid4(), start=start, end=datetime(2026, 1, 6, tzinfo=timezone.utc), observed_at=NOW, now=NOW)
    result = service._quality_metrics(rows, p)
    questions = result["question_without_observed_response"]
    offers = result["price_offer_without_observed_continuity"]
    response = result["response_coverage"]
    assert result["coverage"]["distinct_leads_by_crm_id"] == 3
    assert result["coverage"]["messages_in_period"] == 6
    assert result["coverage"]["distinct_question_leads_by_crm_id"] == 2
    assert questions["denominator"] == 3
    assert questions["numerator"] == 2
    assert questions["state"] == "partial"  # the orphaned message reduces coverage
    assert offers["denominator"] == 2
    assert offers["numerator"] == 1
    assert response["numerator"] == 1
    assert response["denominator"] == 3
    assert result["median_response_seconds"]["denominator"] == 1
    assert result["median_response_seconds"]["mature_inbound_denominator"] == 3
    assert "abandono" in offers["definition"]  # the caveat is explicit, never a causal label


def test_missing_or_mismatched_conversation_makes_coverage_partial():
    lead, conversation = uuid4(), uuid4()
    row = message(lead=lead, conversation=conversation, at=NOW - timedelta(days=2), text="Qual o preço?", direction="inbound")
    row.valid_conversation_id = None
    p = service.filters(empresa_id=uuid4(), start=NOW - timedelta(days=3), end=NOW, observed_at=NOW, now=NOW)
    result = service._quality_metrics([row], p)
    assert result["coverage"]["distinct_leads_by_crm_id"] == 1
    assert result["coverage"]["missing_conversation_messages"] == 1
    assert result["question_without_observed_response"]["state"] == "partial"
    assert result["question_without_observed_response"]["denominator"] == 0


def test_channel_filter_counts_null_channel_rows_as_unknown_and_excludes_other_channels():
    lead_a, lead_b, lead_c = uuid4(), uuid4(), uuid4()
    conv_a, conv_b, conv_c = uuid4(), uuid4(), uuid4()
    start = NOW - timedelta(hours=2)
    rows = [
        message(lead=lead_a, conversation=conv_a, at=start, text="Olá", direction="inbound", channel="whatsapp"),
        message(lead=lead_a, conversation=conv_a, at=start + timedelta(minutes=5), text="Oi", direction="outbound", from_me=True, channel="whatsapp"),
        message(lead=lead_b, conversation=conv_b, at=start + timedelta(minutes=1), text="Canal ausente", direction="inbound", channel=None),
        message(lead=lead_c, conversation=conv_c, at=start + timedelta(minutes=2), text="Instagram", direction="inbound", channel="instagram"),
    ]
    p = service.filters(empresa_id=uuid4(), start=start, end=NOW, observed_at=NOW, response_window_hours=1, now=NOW, channel="whatsapp")
    result = service._quality_metrics(rows, p)
    assert result["coverage"]["messages_in_period"] == 2
    assert result["coverage"]["unknown_channel_messages"] == 1
    assert result["coverage"]["distinct_leads_by_crm_id"] == 1
    assert result["response_coverage"]["state"] == "partial"
    assert service._overall_coverage_state(result["coverage"], truncated=False, objection_state="unknown") == "partial"


def test_channel_filter_marks_unknown_lookahead_messages_partial():
    lead = uuid4()
    conversation = uuid4()
    start = NOW - timedelta(hours=2)
    rows = [
        message(lead=lead, conversation=conversation, at=start, text="Tenho uma pergunta?", direction="inbound", channel="whatsapp"),
        message(lead=uuid4(), conversation=uuid4(), at=NOW + timedelta(minutes=30), text="Resposta de canal ausente", direction="outbound", from_me=True, channel=None),
    ]
    observed = NOW + timedelta(hours=2)
    p = service.filters(empresa_id=uuid4(), start=start, end=NOW, observed_at=observed,
                        response_window_hours=1, continuity_window_days=1, now=observed, channel="whatsapp")
    result = service._quality_metrics(rows, p)
    assert result["coverage"]["messages_in_period"] == 1
    assert result["coverage"]["unknown_channel_messages"] == 1
    assert result["response_coverage"]["state"] == "partial"
    assert result["question_without_observed_response"]["state"] == "partial"


@pytest.mark.asyncio
async def test_selected_channel_query_includes_null_channel_rows_for_coverage():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    p = service.filters(empresa_id=uuid4(), now=NOW, channel="whatsapp")
    await service._message_rows(session, p)
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "mensagens_historico.channel IS NULL" in sql
    assert "mensagens_historico.channel =" in sql


@pytest.mark.asyncio
async def test_message_query_is_tenant_scoped_and_validates_lead_conversation_join():
    tenant = uuid4()
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    p = service.filters(empresa_id=tenant, now=NOW)
    await service._message_rows(session, p)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    params = statement.compile(dialect=postgresql.dialect()).params
    assert "mensagens_historico.empresa_id" in sql
    assert "channel_conversations.empresa_id" in sql
    assert "channel_conversations.lead_id = mensagens_historico.lead_id" in sql
    assert tenant in params.values()
    assert "texto" not in statement.selected_columns.keys()
    assert "metadata_json" not in statement.selected_columns.keys()


@pytest.mark.asyncio
async def test_message_query_binds_utc_naive_for_legacy_timestamp_column():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    p = service.filters(
        empresa_id=uuid4(),
        start="2026-01-01T01:00:00+01:00",
        end="2026-01-02T01:00:00+01:00",
        observed_at="2026-01-10T13:00:00+01:00",
        now=NOW,
    )

    await service._message_rows(session, p)

    statement = session.execute.await_args.args[0]
    timestamp_values = [
        value for value in statement.compile(dialect=postgresql.dialect()).params.values()
        if isinstance(value, datetime)
    ]
    assert timestamp_values
    assert all(value.tzinfo is None for value in timestamp_values)
    assert datetime(2026, 1, 1) in timestamp_values
    assert datetime(2026, 1, 9) in timestamp_values


@pytest.mark.asyncio
async def test_objection_query_binds_utc_naive_for_legacy_timestamp_column():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    p = service.filters(
        empresa_id=uuid4(),
        start="2026-01-01T01:00:00+01:00",
        end="2026-01-02T01:00:00+01:00",
        observed_at="2026-01-10T13:00:00+01:00",
        now=NOW,
    )

    await service._objection_metrics(session, p)

    statement = session.execute.await_args.args[0]
    timestamp_values = [
        value for value in statement.compile(dialect=postgresql.dialect()).params.values()
        if isinstance(value, datetime)
    ]
    assert timestamp_values
    assert all(value.tzinfo is None for value in timestamp_values)
    assert datetime(2026, 1, 1) in timestamp_values
    assert datetime(2026, 1, 2) in timestamp_values


@pytest.mark.parametrize("kwargs", [
    {"channel": "email"}, {"ad_id": "1 OR 1=1"}, {"offer_signal": "invented"},
    {"response_window_hours": 0}, {"continuity_window_days": 31},
])
def test_invalid_quality_filters_are_rejected_before_database(kwargs):
    with pytest.raises((ValueError, TypeError)):
        service.filters(empresa_id=uuid4(), now=NOW, **kwargs)


def test_feature_flag_is_visibility_only(monkeypatch):
    monkeypatch.setenv("ATTENDANCE_QUALITY_ANALYTICS_ENABLED", "off")
    assert service.enabled() is False
    monkeypatch.setenv("ATTENDANCE_QUALITY_ANALYTICS_ENABLED", "true")
    assert service.enabled() is True


@pytest.mark.asyncio
async def test_objection_recurrence_uses_explicit_tag_and_distinct_leads(monkeypatch):
    tag_id, lead = uuid4(), uuid4()
    conversation_a, conversation_b = uuid4(), uuid4()
    fake = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [
        (tag_id, "Objeção preço", lead, conversation_a, "whatsapp", uuid4(), "Qualificação", uuid4(), NOW),
        (tag_id, "Objeção preço", lead, conversation_b, "whatsapp", uuid4(), "Qualificação", uuid4(), NOW),
    ])))
    p = service.filters(empresa_id=uuid4(), now=NOW)
    result = await service._objection_metrics(fake, p)
    assert result["items"][0]["distinct_leads"] == 1
    assert result["items"][0]["events"] == 2
    assert result["items"][0]["label"] == "Preço"


@pytest.mark.asyncio
async def test_objection_query_counts_applied_tags_only_and_is_tenant_scoped():
    tenant = uuid4()
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    p = service.filters(empresa_id=tenant, now=NOW)
    await service._objection_metrics(session, p)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    params = statement.compile(dialect=postgresql.dialect()).params
    assert "crm_tag_events.action =" in sql
    assert "crm_tag_events.empresa_id =" in sql
    assert tenant in params.values()


@pytest.mark.asyncio
async def test_prompt_traces_separate_real_and_synthetic_sources(monkeypatch):
    class FakeLedger:
        async def runs(self):
            return [
                {"created_at": NOW.isoformat(), "composition_version": "a07-v1", "mode": "LEGACY", "source": "real", "status": "completed", "metric_scope": "final_generation_only", "measurement_source": "provider_observed", "metrics": {"input_tokens": 120, "output_tokens": 23, "latency_ms": 120, "cost_usd": .01}},
                {"created_at": NOW.isoformat(), "composition_version": "a07-v1", "mode": "LEGACY", "source": "synthetic", "status": "completed", "metric_scope": "local_composition", "measurement_source": "local_synthetic", "metrics": {"input_tokens": 20, "output_tokens": 8, "latency_ms": 8, "cost_usd": .0}},
                {"created_at": NOW.isoformat(), "composition_version": "a07-v1", "mode": "LEGACY", "source": "real", "status": "completed", "metric_scope": "final_generation_only", "measurement_source": "provider_observed", "metrics": {"latency_ms": 130}},
            ]

    class FakeClient:
        async def aclose(self):
            return None

    monkeypatch.setattr(service, "new_client", FakeClient)
    monkeypatch.setattr(service, "A07Ledger", lambda _client, _tenant: FakeLedger())
    p = service.filters(empresa_id=uuid4(), start=NOW - timedelta(days=1), end=NOW + timedelta(hours=1), observed_at=NOW, now=NOW)
    result = await service._prompt_metrics(p)
    assert len(result["items"]) == 2
    real = next(item for item in result["items"] if item["source"] == "real")
    synthetic = next(item for item in result["items"] if item["source"] == "synthetic")
    assert real["observed_cost_usd"] == .01
    assert real["input_tokens_observed"] == 120
    assert real["output_tokens_observed"] == 23
    assert synthetic["observed_cost_usd"] is None
    assert synthetic["input_tokens_observed"] is None
    assert synthetic["cost_state"] == "not_applicable"
    assert real["median_latency_ms"] == 125
    assert real["provider_usage_runs"] == 2
    assert real["input_token_samples"] == 1
    assert real["output_token_samples"] == 1
    assert real["token_state"] == "partial"
    assert real["cost_samples"] == 1
    assert real["cost_state"] == "partial"
    assert synthetic["median_latency_ms"] == 8


@pytest.mark.asyncio
async def test_channel_filtered_objections_report_missing_conversation_associations():
    tag_id, lead = uuid4(), uuid4()
    fake = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [
        (tag_id, "Objeção preço", lead, None, None, uuid4(), "Qualificação", uuid4(), NOW),
    ])))
    p = service.filters(empresa_id=uuid4(), channel="whatsapp", now=NOW)
    result = await service._objection_metrics(fake, p)
    assert result["state"] == "partial"
    assert result["coverage"]["unknown_channel_association_events"] == 1
    assert result["coverage"]["tag_events"] == 0
    assert result["items"] == []


def test_overall_coverage_is_partial_for_incomplete_windows_and_unknown_links():
    coverage = {
        "messages_in_period": 3,
        "missing_conversation_messages": 0,
        "unknown_direction_messages": 0,
        "unknown_channel_messages": 0,
        "response_pending_window": 0,
        "price_offer_pending_window": 0,
    }
    assert service._overall_coverage_state(coverage, truncated=False, objection_state="observed") == "observed"
    coverage["response_pending_window"] = 1
    assert service._overall_coverage_state(coverage, truncated=False, objection_state="observed") == "partial"
    coverage["response_pending_window"] = 0
    assert service._overall_coverage_state(coverage, truncated=False, objection_state="partial") == "partial"
    coverage["messages_in_period"] = 0
    assert service._overall_coverage_state(coverage, truncated=False, objection_state="unknown") == "unknown"
