from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.tools.tags_tool import run_tags_tool
from app.agents.tenant_config import build_tenant_agent_config
from app.agents.tools.registry import ToolRegistry


class _Empresa:
    id = "11111111-1111-1111-1111-111111111111"
    nome_empresa = "Tenant de teste"
    credenciais_canais = {
        "tags_ia_habilitadas": [
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        ]
    }


def test_tenant_config_does_not_expose_legacy_tag_permission_source():
    config = build_tenant_agent_config(_Empresa())
    assert "tags_ia_habilitadas" not in config


def test_tag_tool_is_core_and_checks_database_permission():
    assert "tags" in ToolRegistry().available_tools({})


def test_ia_config_update_is_tenant_scoped_and_normalizes_duplicate_uuids():
    source = Path("app/api/routers/empresas.py").read_text(encoding="utf-8")
    start = source.index("async def put_ia_config(")
    end = source.index("@router.put(\"/{empresa_id}\"", start)
    handler = source[start:end]
    assert "Depends(require_ia_config_access)" in handler
    assert "dict.fromkeys" in handler
    assert "TagCRM.empresa_id == empresa.id" in handler
    assert "tags_ia_habilitadas deve conter UUIDs válidos" in handler
    assert "Uma ou mais tags não pertencem ao tenant" in handler


def test_tag_tool_uses_allowed_uuids_and_official_consequence_flow_without_direct_meta_call():
    source = Path("app/agents/tools/tags_tool.py").read_text(encoding="utf-8")
    assert "TagCRM.empresa_id == uuid.UUID(str(state.empresa_id))" in source
    assert "requested not in allowed" in source
    assert "execute_commercial_state_for_lead" in source
    assert "meta" not in source.lower()
    assert "handoff_requires_handoff_tool" in source


@pytest.mark.asyncio
async def test_handoff_has_precedence_over_commercial_tags_in_one_plan():
    registry = ToolRegistry()
    registry.run_tool = AsyncMock(return_value={
        "name": "handoff",
        "ok": True,
        "data": {},
        "missing_fields": [],
        "actions": [],
        "memory_update": {},
        "error": None,
    })
    plan = {
        "selected_tools": ["context", "handoff", "tags"],
        "tool_args": {"tags": {"tag_id": "tag-id"}},
    }
    state = ConversationState(empresa_id="tenant", lead_id="lead")

    await registry.run_planned_tools(state=state, plan=plan, message=None, tenant_config={})

    called_tools = [call.args[0] for call in registry.run_tool.await_args_list]
    assert "handoff" in called_tools
    assert "tags" not in called_tools


@pytest.mark.asyncio
async def test_run_tags_tool_blocks_when_conversation_is_manual():
    state = ConversationState(
        empresa_id="11111111-1111-1111-1111-111111111111",
        lead_id="22222222-2222-2222-2222-222222222222",
        status_atendimento="manual",
    )
    with patch("app.agents.tools.tags_tool._allowed_ids", new=AsyncMock(return_value=set())) as mocked:
        result = await run_tags_tool(
            state=state,
            message=IncomingMessage(text="quero uma vaga"),
            tenant_config={"tags_ia_habilitadas": ["33333333-3333-3333-3333-333333333333"]},
            tool_args={"tag_id": "33333333-3333-3333-3333-333333333333"},
        )

    assert result["ok"] is False
    assert result["error"] == "tags_not_available"
    mocked.assert_awaited_once()
