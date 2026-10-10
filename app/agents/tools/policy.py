from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.schemas import ToolInvocation
from app.agents.tools.http import is_dangerous_http_url


@dataclass(slots=True)
class ToolPolicy:
    tenant_allowlist: dict[str, set[str]] = field(default_factory=dict)
    specialist_allowlist: dict[str, set[str]] = field(default_factory=dict)
    http_allowlist: set[str] = field(default_factory=set)
    max_risk_by_scope: dict[str, str] = field(default_factory=lambda: {"conversation": "low", "crm": "medium", "agenda": "medium", "handoff": "medium"})

    _risk_rank = {"low": 1, "medium": 2, "high": 3}

    def validate(self, invocation: ToolInvocation) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        tool_name = str(invocation.tool_name or "").strip()
        tenant_id = str(invocation.tenant_id or "").strip()
        specialist_key = str(invocation.specialist_key or "").strip()

        if not tool_name:
            reasons.append("missing_tool_name")
        tenant_tools = self.tenant_allowlist.get(tenant_id)
        if tenant_tools is not None and tool_name not in tenant_tools:
            reasons.append("tenant_tool_not_allowed")
        specialist_tools = self.specialist_allowlist.get(specialist_key)
        if specialist_tools is not None and tool_name not in specialist_tools:
            reasons.append("specialist_tool_not_allowed")

        max_risk = self.max_risk_by_scope.get(invocation.scope, "low")
        if self._risk_rank.get(invocation.risk, 3) > self._risk_rank.get(max_risk, 1):
            reasons.append("risk_exceeds_scope")

        if invocation.tool_type == "http":
            dangerous, url_reasons = is_dangerous_http_url(invocation.url, self.http_allowlist)
            reasons.extend(url_reasons)
            if dangerous:
                reasons.append("dangerous_url")

        return not reasons, reasons
