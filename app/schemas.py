import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator


SENSITIVE_METADATA_KEY_MARKERS = (
    "api_key",
    "apikey",
    "access_token",
    "app_secret",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "password",
    "secret",
    "senha",
    "token",
)


def _metadata_key_is_sensitive(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    return any(marker in normalized for marker in SENSITIVE_METADATA_KEY_MARKERS)


def _contains_sensitive_metadata(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _metadata_key_is_sensitive(str(key)) or _contains_sensitive_metadata(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_metadata(item) for item in value)
    return False


def _sanitize_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        text_key = str(key)
        if _metadata_key_is_sensitive(text_key):
            sanitized[text_key] = "***REDACTED***"
        elif isinstance(item, dict):
            sanitized[text_key] = _sanitize_metadata(item)
        elif isinstance(item, list):
            sanitized[text_key] = [
                _sanitize_metadata(entry) if isinstance(entry, dict) else entry
                for entry in item
            ]
        else:
            sanitized[text_key] = item
    return sanitized


def _reject_sensitive_metadata(value: Any) -> dict[str, Any]:
    metadata = value if isinstance(value, dict) else {}
    if _contains_sensitive_metadata(metadata):
        raise ValueError("metadata_json nao pode conter tokens, chaves ou segredos")
    return metadata


# --- Schemas para Empresa ---
class EmpresaBase(BaseModel):
    nome_empresa: str
    area_atuacao: str | None = None
    logo_url: str | None = None
    credenciais_canais: dict[str, Any] | None = Field(default_factory=dict)
    openai_api_key: str | None = None
    ia_personalidade: str | None = None
    ia_regras_negocio: str | None = None
    disparo_delay_min: int = 3
    disparo_delay_max: int = 7
    limite_certeza: float = 0.65
    limite_duvida: float = 0.45
    max_agentes_desempate: int = 3

    @field_validator("disparo_delay_min", mode="before")
    @classmethod
    def _default_delay_min(cls, value):
        return 3 if value is None else value

    @field_validator("disparo_delay_max", mode="before")
    @classmethod
    def _default_delay_max(cls, value):
        return 7 if value is None else value

class EmpresaCreate(EmpresaBase):
    pass


class EmpresaUpdate(BaseModel):
    nome_empresa: str | None = None
    area_atuacao: str | None = None
    logo_url: str | None = None
    ia_personalidade: str | None = None
    ia_regras_negocio: str | None = None
    conexao_disparo_id: str | None = None
    disparo_delay_min: int | None = None
    disparo_delay_max: int | None = None
    limite_certeza: float | None = None
    limite_duvida: float | None = None
    max_agentes_desempate: int | None = None
    meta_capi_ativo: bool | None = None
    meta_pixel_id: str | None = None
    meta_access_token: str | None = None
    remover_meta_access_token: bool | None = None

class EmpresaResponse(EmpresaBase):
    id: UUID4
    conexao_disparo_id: UUID4 | None = None
    openai_api_key_configurada: bool = False
    meta_capi_ativo: bool | None = None
    meta_pixel_id: str | None = None
    meta_access_token_configurado: bool = False
    meta_access_token_mascarado: str | None = None

    model_config = ConfigDict(from_attributes=True)


class IAConfigResponse(BaseModel):
    ia_personalidade: str | None = None
    ia_regras_negocio: str | None = None
    nome_agente: str | None = None
    mensagem_saudacao: str | None = None
    conversion_monitor_prompt: str | None = None
    modelo_ia: str | None = None
    followup_ativo: bool = False
    limite_certeza: float = 0.65
    limite_duvida: float = 0.45
    max_agentes_desempate: int = 3
    informacoes_adicionais: str | None = None
    coletar_nome: bool = True
    telefone_notificacao: str | None = None
    status_openai: str = "ok"
    openai_configurada: bool = False
    tags_ia_habilitadas: list[str] = []


class IAConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ia_personalidade: str | None = None
    ia_regras_negocio: str | None = None
    nome_agente: str | None = None
    mensagem_saudacao: str | None = None
    conversion_monitor_prompt: str | None = None
    modelo_ia: str | None = None
    followup_ativo: bool | None = None
    limite_certeza: float | None = None
    limite_duvida: float | None = None
    max_agentes_desempate: int | None = None
    informacoes_adicionais: str | None = None
    coletar_nome: bool | None = None
    telefone_notificacao: str | None = None
    tags_ia_habilitadas: list[str] | None = None

    @field_validator("modelo_ia")
    @classmethod
    def validate_modelo_ia(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or len(normalized) > 255 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", normalized):
            raise ValueError("modelo_ia deve ser um ID canônico válido")
        return normalized


class EmpresaSetupRequest(BaseModel):
    nome_empresa: str
    area_atuacao: str | None = None
    logo_url: str | None = None
    ia_personalidade: str | None = None
    ia_regras_negocio: str | None = None
    admin_nome: str
    admin_email: str
    admin_senha: str


class EvolutionCredentials(BaseModel):
    """Legado: manter apenas openai_api_key; URLs/tokens Evolution vêm do servidor."""
    openai_api_key: str | None = None


class StandardMessage(BaseModel):
    empresa_id: str
    lead_id: str | None = None
    canal: str
    identificador_origem: str
    conexao_id: str | None = None
    channel: str | None = None
    provider: str | None = None
    external_message_id: str | None = None
    external_contact_id: str | None = None
    external_thread_id: str | None = None
    channel_conversation_id: str | None = None
    conversation_key: str | None = None
    nome_contato: str | None = None
    texto_mensagem: str
    is_human_agent: bool
    tipo_mensagem: str | None = None
    media_url: str | None = None
    media_type: str | None = None
    media_id: str | None = None
    media_mime_type: str | None = None
    caption: str | None = None
    has_image: bool | None = None
    image_analysis: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = Field(default_factory=dict)

# --- Schemas para Agente ---
class AgenteBase(BaseModel):
    empresa_id: UUID4
    nome: str
    modelo_ia: str
    prompt_sistema: str
    ativo: bool | None = True

class AgenteCreate(AgenteBase):
    pass

class AgenteResponse(AgenteBase):
    id: UUID4

    model_config = ConfigDict(from_attributes=True)

# --- Schemas para Conhecimento ---
class ConhecimentoUpload(BaseModel):
    conteudo: str
    source_name: str | None = None
    source_type: str | None = "texto"

# --- Schemas para APIConnection ---
class APIConnectionBase(BaseModel):
    empresa_id: UUID4
    nome: str
    url: str
    metodo: str | None = "GET"
    headers_json: dict[str, Any] | None = Field(default_factory=dict)
    params_schema_json: dict[str, Any] | None = Field(default_factory=dict)

class APIConnectionCreate(APIConnectionBase):
    pass

class APIConnectionResponse(APIConnectionBase):
    id: UUID4

    model_config = ConfigDict(from_attributes=True)

# --- Schemas para Conexoes de Canais ---
class ConexaoCreate(BaseModel):
    tipo: Literal["evolution", "meta", "instagram"]
    channel: Literal["whatsapp", "instagram"] | None = None
    provider: Literal["evolution_legacy", "meta_whatsapp", "meta_official", "meta_instagram"] | None = None
    nome_instancia: str | None = None
    credenciais: dict[str, Any] = Field(default_factory=dict)
    status: str | None = "ativo"
    is_default: bool = False
    external_account_id: str | None = None
    waba_id: str | None = None
    phone_number_id: str | None = None
    instagram_business_account_id: str | None = None
    page_id: str | None = None
    display_number: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def _metadata_sem_segredos(cls, value):
        return _reject_sensitive_metadata(value)


class ConexaoUpdate(BaseModel):
    tipo: Literal["evolution", "meta", "instagram"] | None = None
    channel: Literal["whatsapp", "instagram"] | None = None
    provider: Literal["evolution_legacy", "meta_whatsapp", "meta_official", "meta_instagram"] | None = None
    nome_instancia: str | None = None
    credenciais: dict[str, Any] | None = None
    status: str | None = None
    is_default: bool | None = None
    external_account_id: str | None = None
    waba_id: str | None = None
    phone_number_id: str | None = None
    instagram_business_account_id: str | None = None
    page_id: str | None = None
    display_number: str | None = None
    metadata_json: dict[str, Any] | None = None

    @field_validator("metadata_json", mode="before")
    @classmethod
    def _metadata_sem_segredos(cls, value):
        if value is None:
            return None
        return _reject_sensitive_metadata(value)


class ConexaoResponse(BaseModel):
    id: UUID4
    empresa_id: UUID4
    tipo: Literal["evolution", "meta", "instagram"]
    channel: str | None = None
    provider: str | None = None
    nome_instancia: str
    status: str
    is_default: bool = False
    external_account_id: str | None = None
    waba_id: str | None = None
    phone_number_id: str | None = None
    instagram_business_account_id: str | None = None
    page_id: str | None = None
    display_number: str | None = None
    campaign_send_mode: Literal["test_only", "production"] = "test_only"
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    credenciais_masked: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str
    webhook_path: str

    @field_validator("metadata_json", mode="before")
    @classmethod
    def _metadata_mascarada(cls, value):
        return _sanitize_metadata(value)

    model_config = ConfigDict(from_attributes=True)


class MensagemHistoricoResponse(BaseModel):
    id: str
    empresa_id: UUID4 | None = None
    texto: str
    tipo_mensagem: str | None = None
    media_url: str | None = None
    conexao_id: UUID4 | None = None
    channel_conversation_id: UUID4 | None = None
    channel: str | None = None
    provider: str | None = None
    external_message_id: str | None = None
    external_conversation_id: str | None = None
    direction: str | None = None
    message_status: str | None = None
    provider_status: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    origem_mensagem: str | None = None
    from_me: bool = False
    criado_em: str | None = None

    @field_validator("metadata_json", mode="before")
    @classmethod
    def _metadata_mascarada(cls, value):
        return _sanitize_metadata(value)


class ChannelContactResponse(BaseModel):
    id: UUID4
    empresa_id: UUID4
    conexao_id: UUID4
    lead_id: UUID4 | None = None
    channel: str
    provider: str
    external_contact_id: str
    normalized_address: str | None = None
    phone_e164: str | None = None
    username: str | None = None
    display_name: str | None = None
    profile_photo_url: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def _metadata_mascarada(cls, value):
        return _sanitize_metadata(value)

    model_config = ConfigDict(from_attributes=True)


class ChannelConversationResponse(BaseModel):
    id: UUID4
    empresa_id: UUID4
    conexao_id: UUID4
    contact_id: UUID4
    lead_id: UUID4 | None = None
    channel: str
    provider: str
    conversation_key: str
    external_conversation_id: str
    external_thread_id: str | None = None
    status: str = "open"
    last_message_at: str | None = None
    last_inbound_at: str | None = None
    last_outbound_at: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def _metadata_mascarada(cls, value):
        return _sanitize_metadata(value)

    model_config = ConfigDict(from_attributes=True)


class ConversaListaResponse(BaseModel):
    id: str | None = None
    conversation_id: str | None = None
    conversation_options: list[dict[str, Any]] = Field(default_factory=list)
    nome_contato: str | None = None
    foto_url: str | None = None
    telefone_contato: str | None = None
    ultima_mensagem: str | None = None
    ultima_mensagem_data: str | None = None
    nao_lidas: int = 0
    status_atendimento: str | None = "aberto"
    bot_pausado: bool = False
    bot_pausado_ate: str | None = None
    ia_ativa: bool = True
    etapa_crm: str | None = None
    tags: list[dict[str, Any]] = Field(default_factory=list)
    historico_resumo: str | None = None
    dados_adicionais: dict[str, Any] = Field(default_factory=dict)


class TagGroupBase(BaseModel):
    nome: str
    cor: str | None = None
    ordem: int = 0


class TagGroupCreate(TagGroupBase):
    pass


class TagGroupUpdate(BaseModel):
    nome: str | None = None
    cor: str | None = None
    ordem: int | None = None


class TagGroupResponse(TagGroupBase):
    id: str


class TagOfficialBase(BaseModel):
    nome: str
    cor: str = "#2563eb"
    instrucao_ia: str | None = None
    grupo_id: str | None = None
    disparar_conversao_ads: bool = False
    acao_fechamento: bool = False


class TagOfficialCreate(TagOfficialBase):
    pass


class TagOfficialUpdate(BaseModel):
    nome: str | None = None
    cor: str | None = None
    instrucao_ia: str | None = None
    grupo_id: str | None = None
    disparar_conversao_ads: bool | None = None
    acao_fechamento: bool | None = None


class TagOfficialResponse(TagOfficialBase):
    id: str
    criado_em: str | None = None



class ConfiguracaoGlobalUpdate(BaseModel):
    nome_sistema: str
    cor_primaria: str
    openai_key_global: str | None = None
    favicon_base64: str | None = None
    logo_base64: str | None = None


class ConfiguracaoGlobalResponse(BaseModel):
    id: int
    nome_sistema: str
    cor_primaria: str
    openai_key_global: str | None = None
    favicon_base64: str | None = None
    logo_base64: str | None = None
    model_config = ConfigDict(from_attributes=True)
