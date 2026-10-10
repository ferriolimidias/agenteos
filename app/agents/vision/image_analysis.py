from __future__ import annotations

import asyncio
import base64
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from sqlalchemy import select


DEFAULT_UNAVAILABLE_FALLBACK = (
    "Recebi a imagem. Para te orientar melhor, me diga também alguns detalhes importantes."
)


@dataclass(slots=True)
class ImageAnalysisRequest:
    tenant_id: str | None
    media_url: str | None = None
    media_id: str | None = None
    media_mime_type: str | None = None
    caption: str | None = None
    prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImageAnalysisResult:
    available: bool = False
    image_detected: bool = False
    summary: str = ""
    objects: list[str] = field(default_factory=list)
    scene: str = ""
    condition: str = ""
    text_detected: str = ""
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    needs_human_confirmation: bool = True
    status: str = "unavailable"
    provider: str = "none"
    fallback_message: str = DEFAULT_UNAVAILABLE_FALLBACK

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImageAnalysisProvider(Protocol):
    provider_name: str

    async def analyze(self, request: ImageAnalysisRequest, config: dict[str, Any]) -> ImageAnalysisResult:
        ...


class MockImageAnalysisProvider:
    provider_name = "mock"

    def __init__(self, result: ImageAnalysisResult | None = None) -> None:
        self.result = result

    async def analyze(self, request: ImageAnalysisRequest, config: dict[str, Any]) -> ImageAnalysisResult:
        if self.result:
            return self.result
        return ImageAnalysisResult(
            available=True,
            image_detected=True,
            summary="A imagem parece mostrar um item fotografado para avaliação visual.",
            objects=["item"],
            scene="imagem enviada pelo cliente",
            condition="não confirmado",
            text_detected="",
            confidence=0.72,
            warnings=["A análise visual é aproximada e precisa de confirmação humana."],
            needs_human_confirmation=True,
            status="available",
            provider=self.provider_name,
            fallback_message=str(config.get("fallback_message") or DEFAULT_UNAVAILABLE_FALLBACK),
        )


class OpenAIVisionProvider:
    provider_name = "openai"

    async def analyze(self, request: ImageAnalysisRequest, config: dict[str, Any]) -> ImageAnalysisResult:
        api_key = str(config.get("api_key") or "").strip()
        model = str(config.get("vision_model") or "gpt-4o-mini").strip() or "gpt-4o-mini"
        if not api_key:
            return unavailable("missing_api_key", self.provider_name, config)
        if not request.media_url:
            return unavailable("missing_media", self.provider_name, config)
        if not self._media_accessible(request.media_url):
            return unavailable("media_not_accessible", self.provider_name, config)

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key)
            prompt = request.prompt or (
                "Analise a imagem de forma objetiva para apoiar atendimento ao cliente. "
                "Responda apenas JSON com summary, objects, scene, condition, text_detected, confidence, warnings."
            )
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": request.media_url}},
                        ],
                    }
                ],
                temperature=0,
                max_tokens=450,
            )
            raw = str(response.choices[0].message.content or "").strip()
            parsed = self._parse_json(raw)
            return ImageAnalysisResult(
                available=True,
                image_detected=True,
                summary=str(parsed.get("summary") or raw[:300]).strip(),
                objects=[str(item) for item in parsed.get("objects", []) if str(item).strip()],
                scene=str(parsed.get("scene") or "").strip(),
                condition=str(parsed.get("condition") or "").strip(),
                text_detected=str(parsed.get("text_detected") or "").strip(),
                confidence=float(parsed.get("confidence") or 0.6),
                warnings=[str(item) for item in parsed.get("warnings", []) if str(item).strip()]
                or ["A análise visual é aproximada e precisa de confirmação humana."],
                needs_human_confirmation=True,
                status="available",
                provider=self.provider_name,
                fallback_message=str(config.get("fallback_message") or DEFAULT_UNAVAILABLE_FALLBACK),
            )
        except Exception:
            return unavailable("provider_error", self.provider_name, config)

    def _media_accessible(self, media_url: str) -> bool:
        raw = str(media_url or "").strip()
        if raw.startswith("data:image/"):
            try:
                base64.b64decode(raw.split(",", 1)[1] if "," in raw else raw, validate=False)
                return True
            except Exception:
                return False
        return raw.startswith("http://") or raw.startswith("https://")

    def _parse_json(self, raw: str) -> dict[str, Any]:
        try:
            return json.loads(raw)
        except Exception:
            return {}


def unavailable(reason: str, provider: str = "none", config: dict[str, Any] | None = None) -> ImageAnalysisResult:
    cfg = config or {}
    return ImageAnalysisResult(
        available=False,
        image_detected=False,
        summary="",
        confidence=0.0,
        warnings=[reason],
        needs_human_confirmation=True,
        status="unavailable",
        provider=provider,
        fallback_message=str(cfg.get("fallback_message") or DEFAULT_UNAVAILABLE_FALLBACK),
    )


class ImageAnalysisService:
    def __init__(
        self,
        provider: ImageAnalysisProvider | None = None,
        config_override: dict[str, Any] | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.provider = provider
        self.config_override = config_override
        self.timeout_seconds = timeout_seconds

    async def analyze(self, request: ImageAnalysisRequest) -> ImageAnalysisResult:
        config = await self._load_config(request.tenant_id)
        if self.config_override:
            config.update(self.config_override)
        if not bool(config.get("enabled")):
            return unavailable("disabled", "none", config)
        if not request.media_url:
            return unavailable("missing_media", "none", config)
        provider = self.provider or self._provider_from_config(config)
        try:
            return await asyncio.wait_for(provider.analyze(request, config), timeout=self.timeout_seconds)
        except TimeoutError:
            return unavailable("timeout", getattr(provider, "provider_name", "unknown"), config)
        except Exception:
            return unavailable("service_error", getattr(provider, "provider_name", "unknown"), config)

    async def _load_config(self, tenant_id: str | None) -> dict[str, Any]:
        default = {
            "enabled": False,
            "vision_provider": "openai",
            "vision_model": "gpt-4o-mini",
            "max_image_size_mb": 8,
            "require_human_confirmation": True,
            "fallback_message": DEFAULT_UNAVAILABLE_FALLBACK,
        }
        if not tenant_id:
            return default
        try:
            empresa_uuid = uuid.UUID(str(tenant_id))
        except (TypeError, ValueError):
            return default
        try:
            from db.database import AsyncSessionLocal
            from db.models import Empresa

            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
                empresa = result.scalars().first()
                if not empresa:
                    return default
                credenciais = empresa.credenciais_canais or {}
                cfg = credenciais.get("image_analysis") if isinstance(credenciais, dict) else None
                if isinstance(cfg, dict):
                    default.update(cfg)
                api_key = (
                    str(credenciais.get("openai_api_key") or "").strip()
                    if isinstance(credenciais, dict)
                    else ""
                ) or str(getattr(empresa, "openai_api_key", "") or "").strip()
                if api_key:
                    default["api_key"] = api_key
        except Exception:
            return default
        return default

    def _provider_from_config(self, config: dict[str, Any]) -> ImageAnalysisProvider:
        provider = str(config.get("vision_provider") or "openai").strip().lower()
        if provider == "mock":
            return MockImageAnalysisProvider()
        return OpenAIVisionProvider()
