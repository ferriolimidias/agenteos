from __future__ import annotations

import uuid
import logging
from typing import Any, TypedDict

from app.core.secret_sanitizer import sanitize_for_log

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.planner.tool_planner import ToolPlan


class AgentToolResult(TypedDict):
    name: str
    ok: bool
    data: dict[str, Any]
    missing_fields: list[str]
    actions: list[dict[str, Any]]
    memory_update: dict[str, Any]
    error: str | None


def make_tool_result(
    *,
    name: str,
    ok: bool,
    data: dict[str, Any] | None = None,
    missing_fields: list[str] | None = None,
    actions: list[dict[str, Any]] | None = None,
    memory_update: dict[str, Any] | None = None,
    error: str | None = None,
) -> AgentToolResult:
    payload = dict(data or {})
    payload_actions = actions if actions is not None else payload.get("actions")
    payload_memory = memory_update if memory_update is not None else payload.get("memory_update")
    return {
        "name": name,
        "ok": ok,
        "data": payload,
        "missing_fields": [str(item) for item in (missing_fields or []) if str(item or "").strip()],
        "actions": [dict(item) for item in (payload_actions or []) if isinstance(item, dict)],
        "memory_update": dict(payload_memory or {}) if isinstance(payload_memory, dict) else {},
        "error": error,
    }


logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._order = ("context", "anamnese", "orcamento", "agenda", "handoff", "tags")

    def available_tools(self, tenant_config: dict[str, Any] | None = None) -> list[str]:
        # AI tag permission is resolved from TagCRM.ai_can_apply by the
        # commercial-state planner/executor, not from tenant JSON metadata.
        available = list(self._order)
        if not bool((tenant_config or {}).get("anamnese_available")):
            available = [tool for tool in available if tool != "anamnese"]
        return available

    async def run_tool(
        self,
        tool_name: str,
        *,
        state: ConversationState,
        message: IncomingMessage | None,
        tenant_config: dict[str, Any] | None = None,
        tool_args: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        from app.agents.tools.agenda_tool import run_agenda_tool
        from app.agents.tools.anamnese_tool import run_anamnese_tool
        from app.agents.tools.context_tool import run_context_tool
        from app.agents.tools.handoff_tool import run_handoff_tool
        from app.agents.tools.orcamento_tool import run_orcamento_tool
        from app.agents.tools.tags_tool import run_tags_tool

        if tool_name == "context":
            return await run_context_tool(state=state, message=message, tenant_config=tenant_config)
        if tool_name == "anamnese":
            return await run_anamnese_tool(state=state, message=message, tenant_config=tenant_config)
        if tool_name == "orcamento":
            return await run_orcamento_tool(state=state, message=message, tenant_config=tenant_config)
        if tool_name == "agenda":
            return await run_agenda_tool(state=state, message=message, tenant_config=tenant_config)
        if tool_name == "handoff":
            return await run_handoff_tool(state=state, message=message, tenant_config=tenant_config)
        if tool_name == "tags":
            return await run_tags_tool(
                state=state, message=message, tenant_config=tenant_config, tool_args=tool_args
            )
        return make_tool_result(name=tool_name, ok=False, error="unknown_tool")

    async def run_planned_tools(
        self,
        *,
        state: ConversationState,
        plan: ToolPlan,
        message: IncomingMessage | None,
        tenant_config: dict[str, Any] | None = None,
    ) -> list[AgentToolResult]:
        results: list[AgentToolResult] = []
        selected = [tool for tool in self._dedupe(list(plan.get("selected_tools") or [])) if tool in self._order]
        # Handoff has precedence over commercial tagging in one planner turn.
        if "handoff" in selected:
            selected = [tool for tool in selected if tool != "tags"]
        tool_args = plan.get("tool_args") if isinstance(plan.get("tool_args"), dict) else {}
        for tool_name in selected:
            logger.warning(
                "ai.tool.started tool=%s empresa_id=%s lead_id=%s",
                sanitize_for_log(tool_name),
                sanitize_for_log(str(state.empresa_id or "")),
                sanitize_for_log(str(state.lead_id or "")),
            )
            try:
                result = await self.run_tool(
                    tool_name,
                    state=state,
                    message=message,
                    tenant_config=tenant_config,
                    tool_args=tool_args.get(tool_name) if isinstance(tool_args.get(tool_name), dict) else {},
                )
            except Exception as exc:
                logger.exception(
                    "ai.tool.failed tool=%s empresa_id=%s lead_id=%s error_class=%s",
                    sanitize_for_log(tool_name),
                    sanitize_for_log(str(state.empresa_id or "")),
                    sanitize_for_log(str(state.lead_id or "")),
                    exc.__class__.__name__,
                )
                raise
            results.append(result)
            logger.warning(
                "ai.tool.completed tool=%s empresa_id=%s lead_id=%s ok=%s",
                sanitize_for_log(tool_name),
                sanitize_for_log(str(state.empresa_id or "")),
                sanitize_for_log(str(state.lead_id or "")),
                bool(result.get("ok")),
            )
        return results

    def build_tools_context(
        self,
        tool_results: list[AgentToolResult],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {}
        for result in tool_results:
            data = dict(result.get("data") or {})
            internal_keys = {"memory_update", "actions"}
            context[result["name"]] = {
                key: value
                for key, value in data.items()
                if key not in internal_keys
            }
            if data.get("actions"):
                context[result["name"]]["actions"] = data.get("actions")
            if result.get("missing_fields"):
                context[result["name"]]["missing_fields"] = list(result.get("missing_fields") or [])
        return context

    def _dedupe(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False
