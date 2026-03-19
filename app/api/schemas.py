from pydantic import BaseModel

class StandardMessage(BaseModel):
    empresa_id: str
    canal: str
    identificador_origem: str
    conexao_id: str | None = None
    nome_contato: str | None = None
    texto_mensagem: str
    is_human_agent: bool
