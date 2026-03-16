from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4

# --- Schemas para Empresa ---
class EmpresaBase(BaseModel):
    nome_empresa: str
    area_atuacao: str | None = None
    credenciais_canais: dict[str, Any] | None = Field(default_factory=dict)
    ia_instrucoes_personalizadas: str | None = None
    ia_tom_voz: str | None = None

class EmpresaCreate(EmpresaBase):
    pass

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

# --- Schema para Vínculo Ferramenta Especialista ---
class EspecialistaToolLink(BaseModel):
    especialista_id: UUID4
    api_connection_id: UUID4
