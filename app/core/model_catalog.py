"""Canonical model IDs and centrally maintained reasoning capabilities.

OpenAI's ``/v1/models`` endpoint lists model IDs, not per-model reasoning
capabilities.  This policy therefore contains only exact IDs whose support was
confirmed in official OpenAI documentation.  Unknown IDs expose ``default``
only until their capabilities are reviewed and this policy is updated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


MODEL_OPTIONS: Final[tuple[dict[str, str], ...]] = (
    {"label": "GPT-4o", "value": "gpt-4o"},
    {"label": "GPT-4o Mini", "value": "gpt-4o-mini"},
    {"label": "GPT-5.4", "value": "gpt-5.4"},
    {"label": "GPT-5.4 Mini", "value": "gpt-5.4-mini"},
    {"label": "GPT-5.4 Nano", "value": "gpt-5.4-nano"},
    {"label": "GPT-5.6 Luna", "value": "gpt-5.6-luna"},
    {"label": "o1", "value": "o1"},
    {"label": "o3", "value": "o3"},
    {"label": "o4-mini", "value": "o4-mini"},
)

ALLOWED_MODEL_IDS: Final[frozenset[str]] = frozenset(item["value"] for item in MODEL_OPTIONS)
REASONING_EFFORT_VALUES: Final[tuple[str, ...]] = (
    "default", "none", "minimal", "low", "medium", "high", "xhigh", "max",
)
DEFAULT_ONLY: Final[tuple[str, ...]] = ("default",)

REASONING_GUIDE_URL: Final[str] = "https://developers.openai.com/api/docs/guides/reasoning"
DEPLOYMENT_CHECKLIST_URL: Final[str] = "https://developers.openai.com/api/docs/guides/deployment-checklist"
MODEL_POLICY_VERSION: Final[str] = "2026-10-15.1"


@dataclass(frozen=True)
class ReasoningCapability:
    status: str
    levels: tuple[str, ...]
    source_url: str | None
    explanation: str
    default_level: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "levels": list(self.levels),
            "options": ["default", *self.levels],
            "default_level": self.default_level,
            "source_url": self.source_url,
            "policy_version": MODEL_POLICY_VERSION,
            "explanation": self.explanation,
        }


_GPT6_FULL_LEVELS: Final[tuple[str, ...]] = ("none", "low", "medium", "high", "xhigh", "max")
_GPT6_NO_NONE_LEVELS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh", "max")
_GPT56_LEVELS: Final[tuple[str, ...]] = ("none", "low", "medium", "high", "xhigh", "max")
_GPT54_LEVELS: Final[tuple[str, ...]] = ("none", "low", "medium", "high", "xhigh")
_O3_LEVELS: Final[tuple[str, ...]] = ("low", "medium", "high")
_GPT5_LEVELS: Final[tuple[str, ...]] = ("minimal", "low", "medium", "high")

_SUPPORTED_REASONING: Final[dict[str, ReasoningCapability]] = {
    "gpt-6-luna": ReasoningCapability("confirmed", _GPT6_FULL_LEVELS, "https://developers.openai.com/api/docs/models/gpt-6-luna", "Efforts confirmados para GPT-6 Luna.", "medium"),
    "gpt-6-sol": ReasoningCapability("confirmed", _GPT6_FULL_LEVELS, "https://developers.openai.com/api/docs/models/gpt-6-sol", "Efforts confirmados para GPT-6 Sol.", "medium"),
    "gpt-6.1-sol": ReasoningCapability("confirmed", _GPT6_NO_NONE_LEVELS, "https://developers.openai.com/api/docs/models/gpt-6.1-sol", "GPT-6.1 Sol não aceita none nem minimal.", "medium"),
    "gpt-6-astra": ReasoningCapability("confirmed", _GPT6_NO_NONE_LEVELS, "https://developers.openai.com/api/docs/models/gpt-6-astra", "GPT-6 Astra não aceita none.", "medium"),
    "gpt-5.6": ReasoningCapability("confirmed", _GPT56_LEVELS, "https://developers.openai.com/api/docs/guides/reasoning", "O alias GPT-5.6 usa o esforço padrão médio.", "medium"),
    "gpt-5.6-sol": ReasoningCapability("confirmed", _GPT56_LEVELS, "https://developers.openai.com/api/docs/models/gpt-5.6-sol", "Efforts confirmados para GPT-5.6 Sol.", "medium"),
    "gpt-5.6-terra": ReasoningCapability("confirmed", _GPT56_LEVELS, "https://developers.openai.com/api/docs/models/gpt-5.6-terra", "Efforts confirmados para GPT-5.6 Terra.", "medium"),
    "gpt-5.6-luna": ReasoningCapability("confirmed", _GPT56_LEVELS, "https://developers.openai.com/api/docs/models/gpt-5.6-luna", "Efforts confirmados para GPT-5.6 Luna.", "medium"),
    "gpt-5.5": ReasoningCapability("confirmed", ("none", "low", "medium", "high", "xhigh"), "https://developers.openai.com/api/docs/models/gpt-5.5", "Efforts confirmados para GPT-5.5.", "medium"),
    "gpt-5.4": ReasoningCapability("confirmed", _GPT54_LEVELS, "https://developers.openai.com/api/docs/models/gpt-5.4", "GPT-5.4 aceita amostragem somente com effort none.", "none"),
    "gpt-5.4-mini": ReasoningCapability("confirmed", _GPT54_LEVELS, "https://developers.openai.com/api/docs/models/gpt-5.4-mini", "GPT-5.4 Mini aceita amostragem somente com effort none.", "none"),
    "gpt-5.4-nano": ReasoningCapability("confirmed", _GPT54_LEVELS, "https://developers.openai.com/api/docs/models/gpt-5.4-nano", "GPT-5.4 Nano aceita amostragem somente com effort none.", "none"),
    "gpt-5.2": ReasoningCapability("confirmed", _GPT54_LEVELS, "https://developers.openai.com/api/docs/models/gpt-5.2", "GPT-5.2 aceita effort none como padrão.", "none"),
    "gpt-5": ReasoningCapability("confirmed", _GPT5_LEVELS, "https://developers.openai.com/api/docs/models/gpt-5", "GPT-5 suporta minimal, low, medium e high.", "medium"),
    "o3": ReasoningCapability("confirmed", _O3_LEVELS, REASONING_GUIDE_URL, "Os modelos anteriores, como o3, documentam low, medium e high.", "medium"),
}

_NON_REASONING_MODELS: Final[frozenset[str]] = frozenset({"gpt-4o", "gpt-4o-mini"})


def reasoning_effort_capabilities(model_id: str | None) -> dict[str, object]:
    """Return the authoritative, UI-safe effort options for an exact model ID."""
    normalized = str(model_id or "").strip().lower()
    capability = _SUPPORTED_REASONING.get(normalized)
    if capability is not None:
        return capability.payload()
    if normalized in _NON_REASONING_MODELS:
        return {
            "status": "not_supported",
            "levels": [],
            "options": ["default"],
            "default_level": None,
            "source_url": "https://developers.openai.com/api/docs/guides/reasoning-best-practices",
            "policy_version": MODEL_POLICY_VERSION,
            "explanation": "Este modelo não oferece níveis explícitos de raciocínio.",
        }
    return {
        "status": "unknown",
        "levels": [],
        "options": ["default"],
        "default_level": None,
        "source_url": None,
        "policy_version": MODEL_POLICY_VERSION,
        "explanation": "Compatibilidade de raciocínio ainda não confirmada para este ID exato.",
    }


def is_reasoning_effort_compatible(model_id: str | None, effort: str | None) -> bool:
    normalized_effort = str(effort or "default").strip().lower() or "default"
    if normalized_effort not in REASONING_EFFORT_VALUES:
        return False
    capabilities = reasoning_effort_capabilities(model_id)
    return normalized_effort in capabilities["options"]


def model_requires_minimal_chat_parameters(model_id: str | None) -> bool:
    """Whether default Chat Completions must omit sampling parameters.

    This preserves the existing model-profile behavior while accounting for
    models whose documented default effort is reasoning-enabled.
    """
    normalized = str(model_id or "").strip().lower()
    capability = _SUPPORTED_REASONING.get(normalized)
    if capability is not None:
        return capability.default_level not in (None, "none")
    return normalized.startswith(("o1", "o3", "o4"))


def is_allowed_model_id(value: str | None) -> bool:
    return str(value or "").strip() in ALLOWED_MODEL_IDS


def model_options_payload() -> list[dict[str, str]]:
    return [dict(item) for item in MODEL_OPTIONS]
