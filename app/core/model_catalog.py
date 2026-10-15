"""Canonical model IDs exposed by the tenant agent configuration UI."""

from __future__ import annotations

import re
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
_REASONING_CHAT_MODEL_ID: Final[re.Pattern[str]] = re.compile(r"^o(?:1|3|4)(?:-|$)", re.IGNORECASE)


def model_requires_minimal_chat_parameters(model_id: str | None) -> bool:
    """Avoid optional sampling fields for known reasoning model families.

    OpenAI's model list does not publish parameter capabilities. Current
    reasoning families have model-specific Chat Completions restrictions, so
    the AgentOS sends only the required model/messages fields for these IDs.
    Unknown newly listed IDs already use this minimal profile after explicit
    tenant validation.
    """
    normalized = str(model_id or "").strip().lower()
    return normalized == "gpt-5.6-luna" or bool(_REASONING_CHAT_MODEL_ID.match(normalized))


def is_allowed_model_id(value: str | None) -> bool:
    return str(value or "").strip() in ALLOWED_MODEL_IDS


def model_options_payload() -> list[dict[str, str]]:
    return [dict(item) for item in MODEL_OPTIONS]
