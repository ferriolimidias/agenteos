from __future__ import annotations

from app.agents.schemas import FirstTurnAgentDecision


def compose_first_turn_response(
    configured_greeting: str | None,
    decision: FirstTurnAgentDecision | dict | None,
) -> str | None:
    """Compose one first-turn outbound without allowing the agent to alter greeting."""
    greeting = str(configured_greeting or "")
    if not greeting:
        return ""
    if isinstance(decision, dict):
        try:
            decision = FirstTurnAgentDecision.model_validate(decision)
        except Exception:
            return None
    if not isinstance(decision, FirstTurnAgentDecision):
        return None
    extension = str(decision.extension or "").strip()
    if not decision.needs_extension or not extension:
        return greeting
    if greeting in extension or extension.startswith(greeting):
        return greeting
    return f"{greeting}\n\n{extension}"
