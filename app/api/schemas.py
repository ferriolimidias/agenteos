from pydantic import BaseModel

class StandardMessage(BaseModel):
    empresa_id: str
    canal: str
    identificador_origem: str
    texto_mensagem: str
    is_human_agent: bool
