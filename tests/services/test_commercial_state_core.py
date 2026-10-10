import ast
import json
from pathlib import Path

from app.agents.planner.tool_planner import _normalize_plan
from app.agents.schemas import CommercialStateDecision
from app.agents.tools.registry import ToolRegistry


def test_structured_state_is_normalized_to_uuid_values_and_confidence():
    plan = _normalize_plan(
        {
            "selected_tools": ["context", "tags"],
            "commercial_state": {
                "tags_to_apply": ["11111111-1111-1111-1111-111111111111", "not-a-uuid"],
                "target_stage_id": "22222222-2222-2222-2222-222222222222",
                "signals": ["QualifiedLead"],
                "confidence": 1.4,
            },
        },
        ["context", "tags"],
    )
    assert plan["selected_tools"] == ["context"]
    assert plan["commercial_state"]["tags_to_apply"] == ["11111111-1111-1111-1111-111111111111"]
    assert plan["commercial_state"]["confidence"] == 1.0


def test_commercial_signals_reject_free_form_intent_labels():
    plan = _normalize_plan(
        {
            "selected_tools": ["context"],
            "commercial_state": {
                "signals": ["Purchase intent", "InitiateCheckout"],
                "operational_evidence": {"checkout_started": True},
            },
        },
        ["context"],
    )
    assert plan["commercial_state"]["signals"] == ["InitiateCheckout"]


def test_checkout_and_purchase_signals_require_operational_evidence():
    plan = _normalize_plan(
        {
            "selected_tools": ["context"],
            "commercial_state": {
                "signals": ["InitiateCheckout", "Purchase"],
                "operational_evidence": {},
            },
        },
        ["context"],
    )
    assert plan["commercial_state"]["signals"] == []


def test_decision_contract_is_serializable_without_free_form_database_commands():
    decision = CommercialStateDecision(
        tags_to_apply=["11111111-1111-1111-1111-111111111111"],
        target_stage_id="22222222-2222-2222-2222-222222222222",
        signals=["LeadSubmitted"],
        confidence=0.8,
    )
    assert json.loads(json.dumps(decision.to_dict()))["signals"] == ["LeadSubmitted"]


def test_shared_commercial_runtime_contains_no_real_tenant_names():
    paths = [Path("app/agents/planner/tool_planner.py"), Path("app/services/commercial_state_executor.py")]
    blob = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
    assert "rosangela" not in blob
    assert "leonardo" not in blob
    ast.parse(paths[0].read_text(encoding="utf-8"))


def test_tag_capability_permission_is_not_read_from_json():
    assert "tags" in ToolRegistry().available_tools({"tags_ia_habilitadas": []})
