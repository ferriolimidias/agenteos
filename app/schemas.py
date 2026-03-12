from pydantic import BaseModel, UUID4
from typing import Optional, Dict, Any

# --- Schemas para Empresa ---
class EmpresaBase(BaseModel):
    nome_empresa: str
    area_atuacao: Optional[str] = None
    credenciais_canais: Optional[Dict[str, Any]] = {}
    ia_instrucoes_personalizadas: Optional[str] = None
    ia_tom_voz: Optional[str] = None

class EmpresaCreate(EmpresaBase):
    pass

class EmpresaResponse(EmpresaBase):
    id: UUID4

    class Config:
        from_attributes = True

# --- Schemas para Agente ---
class AgenteBase(BaseModel):
    empresa_id: UUID4
    nome: str
    modelo_ia: str
    prompt_sistema: str
    ativo: Optional[bool] = True

class AgenteCreate(AgenteBase):
    pass

class AgenteResponse(AgenteBase):
    id: UUID4

    class Config:
        from_attributes = True

# --- Schemas para Conhecimento ---
class ConhecimentoUpload(BaseModel):
    conteudo: str

# --- Schemas para Especialista ---
class EspecialistaBase(BaseModel):
    empresa_id: UUID4
    nome: str
    prompt_sistema: str
    ativo: Optional[bool] = True

class EspecialistaCreate(EspecialistaBase):
    pass

class EspecialistaResponse(EspecialistaBase):
    id: UUID4

    class Config:
        from_attributes = True

# --- Schemas para APIConnection ---
class APIConnectionBase(BaseModel):
    empresa_id: UUID4
    nome: str
    url: str
    metodo: Optional[str] = "GET"
    headers_json: Optional[Dict[str, Any]] = {}
    params_schema_json: Optional[Dict[str, Any]] = {}

class APIConnectionCreate(APIConnectionBase):
    pass

class APIConnectionResponse(APIConnectionBase):
    id: UUID4

    class Config:
        from_attributes = True

# --- Schema para Vínculo Ferramenta Especialista ---
class EspecialistaToolLink(BaseModel):
    especialista_id: UUID4
    api_connection_id: UUID4
