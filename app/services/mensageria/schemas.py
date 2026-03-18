from typing import Any, Literal

from pydantic import BaseModel, Field


MensagemTipo = Literal["text", "image", "audio", "document"]
CanalTipo = Literal["whatsapp", "instagram", "web", "telegram", "meta"]


class StandardIncomingMessage(BaseModel):
    identificador_contato: str
    canal: CanalTipo | str
    nome_contato: str | None = None
    texto: str = ""
    tipo: MensagemTipo = "text"
    media_url: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class StandardOutgoingMessage(BaseModel):
    identificador_contato: str
    canal: CanalTipo | str
    texto: str | None = None
    tipo: MensagemTipo = "text"
    media_url: str | None = None
