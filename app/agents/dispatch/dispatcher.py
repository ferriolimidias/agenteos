from __future__ import annotations

from app.agents.dispatch.schemas import DispatchRequest, DispatchResult


class Dispatcher:
    """Camada de envio em dry-run. Não chama Evolution nem outros provedores."""

    async def dispatch(self, request: DispatchRequest) -> DispatchResult:
        return DispatchResult(dispatched=False, reason="dry_run_no_external_send")
