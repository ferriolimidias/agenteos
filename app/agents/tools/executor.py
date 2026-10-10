from __future__ import annotations

from app.agents.schemas import ToolInvocation, ToolResult
from app.agents.tools.policy import ToolPolicy


class ToolExecutor:
    def __init__(self, policy: ToolPolicy | None = None) -> None:
        self.policy = policy or ToolPolicy()

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        allowed, reasons = self.policy.validate(invocation)
        if not allowed:
            return ToolResult(
                tool_name=invocation.tool_name,
                allowed=False,
                success=False,
                message="Ferramenta bloqueada pela política de segurança.",
                reason_codes=reasons,
            )
        return ToolResult(
            tool_name=invocation.tool_name,
            allowed=True,
            success=False,
            message="Executor em modo fachada: nenhuma chamada externa foi executada.",
            reason_codes=["stub_executor"],
        )
