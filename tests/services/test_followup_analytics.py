from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import followup_analytics as service


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def execution(*, status="sent", sent_at=None, response_at=None, prior_activity=None, delivered=False,
              eligible=None, mode="conversation_resume", reason=None, lead=None, conversation=None):
    lead = lead or uuid4()
    conversation = conversation or uuid4()
    snapshot = {"mode": mode}
    if eligible is not None:
        snapshot["followup_delivery_decision"] = {"eligible": eligible}
    return SimpleNamespace(
        id=uuid4(), empresa_id=uuid4(), lead_id=lead, conversation_id=conversation,
        followup_id=uuid4(), followup_name="Synthetic follow-up", channel="whatsapp",
        trigger="last_agent_message" if mode == "conversation_resume" else "tag_applied",
        policy_snapshot_json=snapshot, status=status, stop_reason=reason,
        scheduled_at=NOW.replace(tzinfo=None), created_at=NOW.replace(tzinfo=None),
        processing_started_at=NOW.replace(tzinfo=None) if status in {"sent", "failed"} else None,
        sent_at=sent_at, completed_at=None, cancelled_at=None, failed_at=None,
        external_message_id="synthetic-message", provider="synthetic-provider",
        analytics_mode=mode, response_at=response_at, previous_activity_at=prior_activity,
        provider_delivery_confirmed=delivered, verified_conversion_id=None,
    )


def params(**kwargs):
    defaults = {"empresa_id": uuid4(), "start": NOW - timedelta(days=30), "end": NOW,
                "observed_at": NOW, "now": NOW}
    return service.filters(**(defaults | kwargs))


def test_metrics_require_explicit_delivery_eligibility_and_inbound_evidence():
    old_inbound = NOW.replace(tzinfo=None) - timedelta(days=20)
    sent_at = NOW.replace(tzinfo=None) - timedelta(days=9)
    inbound_at = NOW.replace(tzinfo=None) - timedelta(days=8)
    rows = [
        execution(sent_at=sent_at, response_at=inbound_at, prior_activity=old_inbound, delivered=False, eligible=True),
        execution(sent_at=sent_at, response_at=None, prior_activity=None, delivered=False, eligible=False, mode="scheduled_message"),
        execution(status="cancelled", sent_at=None, eligible=None, reason="human_takeover"),
    ]
    p = params()
    result = service._metric_pack(rows, p=p, stage_events={}, stage_orders={}, stage_truncated=False, truncated=False)
    metrics = result["summary"]
    assert metrics["eligible_contacts"]["numerator"] == 1
    assert metrics["eligible_contacts"]["denominator"] == 2
    assert metrics["eligible_contacts"]["state"] == "partial"  # one execution has no persisted decision
    assert metrics["messages_sent"]["numerator"] == 2
    assert metrics["deliveries_confirmed"]["numerator"] == 0  # sent status is not delivery evidence
    assert metrics["responses_observed"]["numerator"] == 1
    assert metrics["reactivations_observed"]["numerator"] == 1
    assert metrics["confirmed_conversions"]["state"] == "unknown"
    assert metrics["cancelled_or_stopped"]["numerator"] == 1
    assert result["by_mode"]["conversation_resume"]["sent"] == 1
    assert result["by_mode"]["scheduled_message"]["sent"] == 1


def test_recent_outbound_activity_prevents_false_reactivation():
    sent_at = NOW.replace(tzinfo=None) - timedelta(days=9)
    prior_activity = sent_at - timedelta(days=2)
    row = execution(sent_at=sent_at, response_at=sent_at + timedelta(days=1), prior_activity=prior_activity)
    metric = service._metric_pack([row], p=params(), stage_events={}, stage_orders={}, stage_truncated=False, truncated=False)["summary"]["reactivations_observed"]
    assert metric["numerator"] == 0
    assert metric["denominator"] == 1


def test_incomplete_response_window_is_partial_not_zero_or_abandonment():
    sent_at = NOW.replace(tzinfo=None) - timedelta(days=1)
    rows = [execution(sent_at=sent_at, response_at=None, prior_activity=None, eligible=None)]
    result = service._metric_pack(rows, p=params(), stage_events={}, stage_orders={}, stage_truncated=False, truncated=False)
    response = result["summary"]["responses_observed"]
    assert response["numerator"] == 0
    assert response["denominator"] == 0
    assert response["state"] == "partial"
    assert response["coverage"]["pending_window"] == 1


def test_missing_conversation_is_excluded_from_observable_rate_denominators():
    sent_at = NOW.replace(tzinfo=None) - timedelta(days=9)
    row = execution(sent_at=sent_at, response_at=None, eligible=True)
    row.conversation_id = None
    summary = service._metric_pack([row], p=params(), stage_events={}, stage_orders={}, stage_truncated=False, truncated=False)["summary"]
    assert summary["responses_observed"]["denominator"] == 0
    assert summary["responses_observed"]["state"] == "partial"
    assert summary["confirmed_conversions"]["denominator"] == 0
    assert summary["confirmed_conversions"]["coverage"]["sent_without_conversation"] == 1


def test_truncated_execution_population_marks_mode_and_stage_metrics_partial():
    sent_at = NOW.replace(tzinfo=None) - timedelta(days=9)
    row = execution(sent_at=sent_at, response_at=None, eligible=True)
    facts = {(row.lead_id, row.conversation_id): [
        (sent_at - timedelta(days=1), "qualification_confirmed", 1),
        (sent_at + timedelta(days=1), "selection_confirmed", 2),
    ]}
    result = service._metric_pack([row], p=params(), stage_events=facts,
                                  stage_orders={"qualification_confirmed": 1, "selection_confirmed": 2},
                                  stage_truncated=False, truncated=True)
    assert result["by_mode"]["conversation_resume"]["response_rate"]["state"] == "partial"
    assert result["summary"]["stage_advances_observed"]["state"] == "partial"
    assert result["summary"]["stage_advances_observed"]["coverage"]["execution_rows_truncated"] is True
    assert result["summary"]["confirmed_conversions"]["state"] == "partial"
    assert result["summary"]["confirmed_conversions"]["coverage"]["execution_rows_truncated"] is True


def test_stage_advance_requires_persisted_higher_order_stage_event():
    sent_at = NOW.replace(tzinfo=None) - timedelta(days=5)
    lead, conversation = uuid4(), uuid4()
    row = execution(sent_at=sent_at, lead=lead, conversation=conversation, eligible=True)
    facts = {(lead, conversation): [
        (sent_at - timedelta(days=1), "qualification_confirmed", 3),
        (sent_at + timedelta(days=1), "selection_confirmed", 4),
    ]}
    result = service._metric_pack(rows=[row], p=params(), stage_events=facts,
                                  stage_orders={"qualification_confirmed": 3, "selection_confirmed": 4},
                                  stage_truncated=False, truncated=False)
    metric = result["summary"]["stage_advances_observed"]
    assert metric["numerator"] == 1
    assert metric["denominator"] == 1
    assert "Associação temporal" in metric["definition"]


def test_verified_purchase_event_is_counted_once_even_when_temporally_after_two_sends():
    event_id = uuid4()
    first = execution(sent_at=NOW.replace(tzinfo=None) - timedelta(days=9), eligible=True)
    second = execution(sent_at=NOW.replace(tzinfo=None) - timedelta(days=8), eligible=True)
    first.verified_conversion_id = event_id
    second.verified_conversion_id = event_id
    result = service._metric_pack(rows=[first, second], p=params(), stage_events={}, stage_orders={}, stage_truncated=False, truncated=False)
    metric = result["summary"]["confirmed_conversions"]
    assert metric["numerator"] == 1
    assert metric["denominator"] == 2


@pytest.mark.parametrize("kwargs", [
    {"mode": "invented"}, {"channel": "email"}, {"status": "unknown"},
    {"stop_reason": "private-error-detail"}, {"response_window_days": 0},
    {"reactivation_days": 91},
])
def test_filter_validation_rejects_unbounded_or_unallowlisted_values(kwargs):
    with pytest.raises(ValueError):
        params(**kwargs)


@pytest.mark.asyncio
async def test_query_is_tenant_scoped_and_uses_recorded_conversation_status():
    tenant = uuid4()
    p = params(empresa_id=tenant, mode="scheduled_message", channel="whatsapp")
    statement = service._base_query(p)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "followup_executions.empresa_id" in sql
    assert "message_status_events.empresa_id" in sql
    assert "mensagens_historico.channel_conversation_id = followup_executions.conversation_id" in sql
    assert "commercial_events.revenue_verified IS true" in sql
    assert tenant in compiled.params.values()
    assert all("texto" not in selected for selected in statement.selected_columns.keys())
    assert "followup_executions.policy_snapshot_json ->>" in sql
    assert "conversation_resume" in compiled.params.values()
    assert "CASE WHEN" in sql
    assert any("followup_delivery_decision" in str(value) for value in compiled.params.values())


@pytest.mark.asyncio
async def test_drilldown_returns_sanitized_records_without_message_content(monkeypatch):
    row = execution(status="cancelled", reason="private arbitrary exception with phone 555123", eligible=False)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [row])))
    monkeypatch.setattr(service, "_stage_events", AsyncMock(return_value=({}, {}, False)))
    result = await service.drilldown(session, empresa_id=uuid4(), kind="stopped", now=NOW,
                                     start=NOW - timedelta(days=30), end=NOW, observed_at=NOW)
    assert result["total"] == 1
    item = result["items"][0]
    assert item["stop_reason"] == "other_recorded_reason"
    assert "555123" not in str(item)
    assert "texto" not in item
    assert item["eligibility"] is False


@pytest.mark.asyncio
async def test_dashboard_reports_legacy_rows_separately(monkeypatch):
    row = execution(sent_at=NOW.replace(tzinfo=None) - timedelta(days=8), eligible=True)
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(all=lambda: [row]),
        SimpleNamespace(first=lambda: ({"stages": []},)),
        SimpleNamespace(one=lambda: (2, 2)),
    ]))
    result = await service.dashboard(session, empresa_id=uuid4(), start=NOW - timedelta(days=30), end=NOW, observed_at=NOW, now=NOW)
    assert result["summary"]["messages_sent"]["numerator"] == 1
    assert result["legacy_send_logs"]["send_log_rows"] == 2
    assert result["coverage"]["legacy_logs"]["included_in_modern_execution_counts"] is False
