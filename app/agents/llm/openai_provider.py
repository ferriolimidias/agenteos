from __future__ import annotations

import logging
import time
from typing import Any

from app.core.secret_sanitizer import sanitize_for_log
from app.core.model_catalog import is_reasoning_effort_compatible, model_requires_minimal_chat_parameters
from app.agents.llm.base import AgentLLM

logger = logging.getLogger(__name__)


class OpenAIAgentLLM(AgentLLM):
    def __init__(self, *, api_key: str, model: str, tenant_id: str | None = None, request_profile: str = "legacy", project_id: str | None = None, reasoning_effort: str | None = None) -> None:
        self._api_key = str(api_key or "").strip()
        self._model = str(model or "").strip() or "gpt-4o-mini"
        self._tenant_id = str(tenant_id or "").strip() or None
        self._request_profile = request_profile if request_profile in {"legacy", "chat_minimal"} else "legacy"
        self._project_id = str(project_id or "").strip() or None
        self._reasoning_effort = str(reasoning_effort or "default").strip().lower() or "default"
        if not is_reasoning_effort_compatible(self._model, self._reasoning_effort):
            raise ValueError("Configured reasoning effort is not compatible with the selected OpenAI model")
        self._observe_usage = False
        self.last_usage_observation = None

    def enable_usage_observation(self) -> None:
        """A07 opt-in only; leaves request arguments and return value unchanged."""
        self._observe_usage = True
        self.last_usage_observation = None

    def disable_usage_observation(self) -> None:
        self._observe_usage = False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "OpenAIAgentLLM":
        credentials = config.get("credenciais_canais") if isinstance(config.get("credenciais_canais"), dict) else {}
        api_key = str(
            config.get("_openai_api_key")
            or credentials.get("openai_api_key")
            or credentials.get("api_key_openai")
            or ""
        ).strip()
        project_id = config.get("openai_project_id") or credentials.get("openai_project_id") or credentials.get("project_id")
        return cls(
            api_key=api_key,
            model=str(config.get("modelo_ia") or "gpt-4o-mini"),
            tenant_id=config.get("empresa_id"),
            request_profile=str(config.get("modelo_ia_request_profile") or "legacy"),
            project_id=project_id,
            reasoning_effort=config.get("modelo_ia_reasoning_effort"),
        )

    async def generate(
        self,
        *,
        system_prompt: str,
        messages: list[dict],
        tools_context: dict | None = None,
    ) -> str:
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # pragma: no cover - defensive
            raise RuntimeError("OpenAI SDK unavailable for OpenAIAgentLLM") from exc

        if not self._api_key:
            raise RuntimeError("Tenant OpenAI key missing")

        client_kwargs = {"api_key": self._api_key}
        if self._project_id:
            client_kwargs["project"] = self._project_id
        client = AsyncOpenAI(**client_kwargs)
        payload_messages = [{"role": "system", "content": system_prompt}]
        for item in messages:
            role = str(item.get("role") or "user").strip() or "user"
            content = str(item.get("content") or "")
            payload_messages.append({"role": role, "content": content})
        if tools_context:
            payload_messages.append(
                {
                    "role": "system",
                    "content": f"Contexto estruturado interno: {tools_context}",
                }
            )

        started = time.monotonic()
        logger.info(
            "ai.llm.request_started tenant_id=%s model=%s message_count=%s",
            self._tenant_id,
            self._model,
            len(payload_messages),
        )
        request_kwargs = {
            "model": self._model,
            "messages": payload_messages,
        }
        if self._reasoning_effort != "default":
            request_kwargs["reasoning_effort"] = self._reasoning_effort
        # Sampling parameters are valid only at effort=none for the reasoning
        # models covered by the provider contract. Keep legacy sampling for
        # existing default/non-reasoning tenant configurations.
        sampling_allowed = self._reasoning_effort in {"default", "none"}
        if (
            self._request_profile == "legacy"
            and sampling_allowed
            and (self._reasoning_effort == "none" or not model_requires_minimal_chat_parameters(self._model))
        ):
            request_kwargs["temperature"] = 0.2
        try:
            response = await client.chat.completions.create(
                **request_kwargs,
            )
        except Exception as exc:
            error_body = getattr(exc, "body", None)
            error_data = error_body.get("error", {}) if isinstance(error_body, dict) else {}
            logger.warning(
                "ai.llm.failed tenant_id=%s model=%s duration_ms=%s error_class=%s http_status=%s error_type=%s error_code=%s error_param=%s request_id=%s error=%s",
                self._tenant_id,
                self._model,
                int((time.monotonic() - started) * 1000),
                exc.__class__.__name__,
                getattr(exc, "status_code", None),
                error_data.get("type"),
                error_data.get("code"),
                error_data.get("param"),
                getattr(exc, "request_id", None),
                sanitize_for_log(str(exc))[:240],
            )
            raise
        if self._observe_usage:
            usage = getattr(response, "usage", None)
            def observed_count(name):
                value = getattr(usage, name, None) if usage is not None else None
                return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
            self.last_usage_observation = {
                "input_tokens": observed_count("prompt_tokens"),
                "output_tokens": observed_count("completion_tokens"),
                "cost_usd": None,  # No measured billing amount or versioned tariff here.
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        choice = response.choices[0] if getattr(response, "choices", None) else None
        if not choice:
            logger.warning(
                "ai.llm.failed tenant_id=%s model=%s duration_ms=%s error_class=EmptyChoices",
                self._tenant_id,
                self._model,
                int((time.monotonic() - started) * 1000),
            )
            raise RuntimeError("OpenAI returned no choices")
        message = getattr(choice, "message", None)
        content = getattr(message, "content", "") if message is not None else ""
        text = str(content or "").strip()
        finish_reason = str(getattr(choice, "finish_reason", "") or "")
        if not text:
            refusal = str(getattr(message, "refusal", "") or "") if message is not None else ""
            logger.warning(
                "ai.llm.failed tenant_id=%s model=%s duration_ms=%s error_class=EmptyContent finish_reason=%s refusal_present=%s",
                self._tenant_id,
                self._model,
                int((time.monotonic() - started) * 1000),
                finish_reason,
                bool(refusal),
            )
            raise RuntimeError(f"OpenAI returned empty content; finish_reason={finish_reason or 'unknown'}")
        logger.info(
            "ai.llm.request_completed tenant_id=%s model=%s duration_ms=%s response_chars=%s finish_reason=%s",
            self._tenant_id,
            self._model,
            int((time.monotonic() - started) * 1000),
            len(text),
            finish_reason,
        )
        return text
