from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator

# --- Schemas para Empresa ---
class EmpresaBase(BaseModel):
    nome_empresa: str
    area_atuacao: str | None = None
    logo_url: str | None = None
    credenciais_canais: dict[str, Any] | None = Field(default_factory=dict)
    ia_instrucoes_personalizadas: str | None = None
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
    ia_instrucoes_personalizadas: str | None = None
    ia_personalidade: str | None = None
    ia_regras_negocio: str | None = None
    conexao_disparo_id: str | None = None
    disparo_delay_min: int | None = None
    disparo_delay_max: int | None = None
    limite_certeza: float | None = None
    limite_duvida: float | None = None
    max_agentes_desempate: int | None = None

class EmpresaResponse(EmpresaBase):
    id: UUID4
    conexao_disparo_id: UUID4 | None = None

    model_config = ConfigDict(from_attributes=True)


class IAConfigResponse(BaseModel):
    ia_instrucoes_personalizadas: str | None = None
    ia_personalidade: str | None = None
    ia_regras_negocio: str | None = None
    nome_agente: str | None = None
    mensagem_saudacao: str | None = None
    modelo_ia: str | None = None
    modelo_roteador: str | None = None
    followup_ativo: bool = False
    followup_espera_nivel_1_minutos: int = 20
    followup_espera_nivel_2_minutos: int = 10
    limite_certeza: float = 0.65
    limite_duvida: float = 0.45
    max_agentes_desempate: int = 3
    informacoes_adicionais: str | None = None
    coletar_nome: bool = True


class IAConfigUpdateRequest(BaseModel):
    ia_instrucoes_personalizadas: str | None = None
    ia_personalidade: str | None = None
    ia_regras_negocio: str | None = None
    nome_agente: str | None = None
    mensagem_saudacao: str | None = None
    modelo_ia: str | None = None
    modelo_roteador: str | None = None
    followup_ativo: bool | None = None
    followup_espera_nivel_1_minutos: int | None = None
    followup_espera_nivel_2_minutos: int | None = None
    limite_certeza: float | None = None
    limite_duvida: float | None = None
    max_agentes_desempate: int | None = None
    informacoes_adicionais: str | None = None
    coletar_nome: bool | None = None


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
    evolution_url: str
    evolution_apikey: str
    evolution_instance: str
    openai_api_key: str | None = None


class StandardMessage(BaseModel):
    empresa_id: str
    canal: str
    identificador_origem: str
    conexao_id: str | None = None
    nome_contato: str | None = None
    texto_mensagem: str
    is_human_agent: bool

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
    descricao_missao: str | None = None
    prompt_sistema: str
    usar_rag: bool = False
    usar_agenda: bool = False
    ativo: bool = True

class EspecialistaCreate(EspecialistaBase):
    pass

class EspecialistaUpdate(BaseModel):
    nome: str | None = None
    descricao_missao: str | None = None
    prompt_sistema: str | None = None
    usar_rag: bool | None = None
    usar_agenda: bool | None = None
    ativo: bool | None = None

class EspecialistaResponse(EspecialistaBase):
    id: UUID4
    usar_agenda: bool = False

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

# --- Schema para Vínculo Ferramenta Especialista ---
class EspecialistaToolLink(BaseModel):
    especialista_id: UUID4
    api_connection_id: UUID4


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
