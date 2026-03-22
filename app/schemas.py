from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator

# --- Schemas para Empresa ---
class EmpresaBase(BaseModel):
    nome_empresa: str
    area_atuacao: str | None = None
    credenciais_canais: dict[str, Any] | None = Field(default_factory=dict)
    ia_instrucoes_personalizadas: str | None = None
    ia_tom_voz: str | None = None
    disparo_delay_min: int = 3
    disparo_delay_max: int = 7

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
    ia_instrucoes_personalizadas: str | None = None
    ia_tom_voz: str | None = None
    conexao_disparo_id: str | None = None
    disparo_delay_min: int | None = None
    disparo_delay_max: int | None = None

class EmpresaResponse(EmpresaBase):
    id: UUID4
    conexao_disparo_id: UUID4 | None = None

    model_config = ConfigDict(from_attributes=True)

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

# --- Schemas para Especialista ---
class EspecialistaBase(BaseModel):
    empresa_id: UUID4
    nome: str
    prompt_sistema: str
    ativo: bool | None = True

class EspecialistaCreate(EspecialistaBase):
    pass

class EspecialistaResponse(EspecialistaBase):
    id: UUID4

    model_config = ConfigDict(from_attributes=True)

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
    nome_instancia: str | None = None
    credenciais: dict[str, Any] = Field(default_factory=dict)
    status: str | None = "ativo"


class ConexaoUpdate(BaseModel):
    tipo: Literal["evolution", "meta", "instagram"] | None = None
    nome_instancia: str | None = None
    credenciais: dict[str, Any] | None = None
    status: str | None = None


class ConexaoResponse(BaseModel):
    id: UUID4
    empresa_id: UUID4
    tipo: Literal["evolution", "meta", "instagram"]
    nome_instancia: str
    status: str
    credenciais_masked: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str
    webhook_path: str

    model_config = ConfigDict(from_attributes=True)


class MensagemHistoricoResponse(BaseModel):
    id: str
    texto: str
    tipo_mensagem: str | None = None
    media_url: str | None = None
    conexao_id: UUID4 | None = None
    from_me: bool = False
    criado_em: str | None = None


class ConversaListaResponse(BaseModel):
    id: str | None = None
    nome_contato: str | None = None
    foto_url: str | None = None
    telefone_contato: str | None = None
    ultima_mensagem: str | None = None
    status_atendimento: str | None = "aberto"
    bot_pausado: bool = False
    bot_pausado_ate: str | None = None
    etapa_crm: str | None = None
    tags: list[str] = Field(default_factory=list)
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


class TagOfficialCreate(TagOfficialBase):
    pass


class TagOfficialUpdate(BaseModel):
    nome: str | None = None
    cor: str | None = None
    instrucao_ia: str | None = None
    grupo_id: str | None = None
    disparar_conversao_ads: bool | None = None


class TagOfficialResponse(TagOfficialBase):
    id: str
    criado_em: str | None = None

# --- Schema para Vínculo Ferramenta Especialista ---
class EspecialistaToolLink(BaseModel):
    especialista_id: UUID4
    api_connection_id: UUID4
