from __future__ import annotations

import re

from app.agents.guards.link_policy import scan_unsafe_links
from app.agents.guards.secret_scanner import scan_secrets
from app.agents.schemas import GuardResult


SAFE_FALLBACK = "Não consegui enviar essa resposta com segurança. Pode reformular sua pergunta?"


class ResponseGuard:
    def validate(self, text: str | None) -> GuardResult:
        raw = str(text or "").strip()
        reasons: list[str] = []
        if not raw:
            reasons.append("empty_response")

        reasons.extend(scan_secrets(raw))
        reasons.extend(scan_unsafe_links(raw))

        if re.search(r"Traceback \(most recent call last\)|File \".+\", line \d+|Stack trace", raw, re.IGNORECASE):
            reasons.append("stack_trace")
        if re.search(r"\[SISTEMA_[A-Z0-9_]+\]", raw):
            reasons.append("system_marker")
        if self._looks_like_internal_json(raw):
            reasons.append("internal_json")
        technical_terms = (
            "tool_call",
            "specialist_key",
            "Intent" + "Decision",
            "Specialist" + "Result",
            "Tool" + "Invocation",
            "Tool" + "Registry",
            "Response" + "Guard",
        )
        if any(re.search(rf"\b{re.escape(term)}\b", raw) for term in technical_terms):
            reasons.append("technical_marker")

        if reasons:
            return GuardResult(allowed=False, text=SAFE_FALLBACK, reason_codes=sorted(set(reasons)), sanitized=True)
        return GuardResult(allowed=True, text=raw, reason_codes=[], sanitized=False)

    def _looks_like_internal_json(self, text: str) -> bool:
        stripped = text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return False
        markers = ("specialist", "tool", "token", "api_key", "traceback", "internal", "intent")
        lowered = stripped.lower()
        return any(marker in lowered for marker in markers)
