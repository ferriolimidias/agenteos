from __future__ import annotations

import json
import uuid

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.services.transferencia_service import executar_transferencia_atendimento


class ToolRagInput(BaseModel):
    pergunta: str = Field(description="Pergunta do cliente para consulta de conhecimento/RAG.")


class ToolTransferenciaInput(BaseModel):
    destino_id: str = Field(description="UUID do destino de transferência configurado.")
    resumo_conversa: str = Field(description="Resumo curto e objetivo do motivo da transferência.")


def criar_tool_rag_contextual(
    *,
    empresa_id: str,
    nome_ferramenta: str = "action_buscar_conhecimento_rag",
) -> StructuredTool:
    """
    Cria a tool de RAG sem executar I/O na inicialização.
    A consulta ao banco ocorre somente quando a IA invoca a ferramenta.
    """

    async def _tool(pergunta: str) -> str:
        try:
            from app.core.agent_graph import buscar_conhecimento

            empresa_uuid = uuid.UUID(str(empresa_id))
            resultado = await buscar_conhecimento(pergunta, empresa_uuid)
            payload = {
                "dados": str(resultado.get("dados", "") or "").strip(),
                "fontes": [str(f).strip() for f in (resultado.get("fontes") or []) if str(f).strip()],
                "erros": [str(e).strip() for e in (resultado.get("erros") or []) if str(e).strip()],
            }
            return json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            return f"Erro ao consultar RAG: {exc}"

    return StructuredTool(
        name=nome_ferramenta,
        description=(
            "Consulta a base de conhecimento interna (RAG) da empresa e retorna dados técnicos brutos."
        ),
        args_schema=ToolRagInput,
        coroutine=_tool,
    )


def criar_tool_transferencia_contextual(
    *,
    empresa_id: str,
    lead_id: str,
    conexao_id: str | None = None,
    nome_ferramenta: str = "action_transferir_atendimento",
) -> StructuredTool:
    """
    Cria a tool de transferência sem executar I/O na inicialização.
    O acesso ao banco ocorre somente quando a IA invoca a ferramenta.
    """

    async def _tool(destino_id: str, resumo_conversa: str) -> str:
        return await executar_transferencia_atendimento(
            empresa_id=empresa_id,
            lead_id=lead_id,
            destino_id=destino_id,
            resumo_conversa=resumo_conversa,
            conexao_id_atual=conexao_id,
        )

    return StructuredTool(
        name=nome_ferramenta,
        description=(
            "Executa transbordo real para atendimento humano com base no destino de transferência configurado."
        ),
        args_schema=ToolTransferenciaInput,
        coroutine=_tool,
    )
