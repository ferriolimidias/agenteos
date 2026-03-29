from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from langchain_core.tools import StructuredTool

from db.database import AsyncSessionLocal
from db.models import CRMLead, Especialista
from app.core.agent_graph import (
    MAP_FUNCOES_NATIVAS,
    buscar_conhecimento,
    criar_ferramenta_transferir_atendimento_contextual,
    create_dynamic_tool,
    get_llm,
    listar_destinos_transferencia_para_prompt,
    _create_pydantic_model_from_json_schema,
)

ESPECIALISTA_ID = "26a3c8bd-4e93-4a66-9b83-05076b57ba72"
MENSAGEM_TESTE = "Quais são as opções pedagógicas disponíveis para o aluno?"


async def main() -> None:
    especialista_uuid = uuid.UUID(ESPECIALISTA_ID)

    print("[SETUP] Iniciando conexão com o banco...")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Especialista)
            .where(Especialista.id == especialista_uuid)
            .options(
                selectinload(Especialista.api_connections),
                selectinload(Especialista.ferramentas),
            )
        )
        especialista = result.scalars().first()

        if not especialista:
            print(f"[ERRO] Especialista {ESPECIALISTA_ID} não encontrado.")
            return

        empresa_id = str(especialista.empresa_id)
        print(f"[SETUP] Especialista carregado: {especialista.nome} | empresa_id={empresa_id}")

        tools_disponiveis = []
        nomes_tools = []
        erros_etapa_1 = []
        contexto_adicional = ""

        print("[ETAPA 1] Montando ferramentas (RAG, Transferência, APIs)...")
        try:
            if getattr(especialista, "usar_rag", False):
                try:
                    rag = await buscar_conhecimento(MENSAGEM_TESTE, especialista.empresa_id)
                    dados_rag = str(rag.get("dados", "") or "").strip()
                    if dados_rag:
                        contexto_adicional = f"\n\nDADOS_RAG_BRUTOS:\n{dados_rag}"
                        print(f"[ETAPA 1] RAG carregado com {len(dados_rag)} chars.")
                except Exception as e:
                    erros_etapa_1.append(f"Falha no RAG: {e}")
                    print(f"[ETAPA 1][ERRO] Falha no RAG: {e}")
                    traceback.print_exc()

            for conexao in especialista.api_connections:
                try:
                    tool = create_dynamic_tool(conexao)
                    tools_disponiveis.append(tool)
                    nomes_tools.append(str(getattr(tool, "name", "")).strip())
                except Exception as e:
                    erros_etapa_1.append(f"Falha API Connection '{conexao.nome}': {e}")
                    print(f"[ETAPA 1][ERRO] API Connection '{conexao.nome}': {e}")
                    traceback.print_exc()

            for f_db in especialista.ferramentas:
                try:
                    schema_dict = f_db.schema_parametros if f_db.schema_parametros else {}
                    if isinstance(schema_dict, str):
                        schema_dict = json.loads(schema_dict)

                    args_schema = _create_pydantic_model_from_json_schema(
                        schema_dict,
                        model_name=f"{f_db.nome_ferramenta}ArgsTesteIsolado",
                    )

                    if f_db.nome_ferramenta in MAP_FUNCOES_NATIVAS:
                        tool = StructuredTool(
                            name=f_db.nome_ferramenta,
                            description=f_db.descricao_ia,
                            args_schema=args_schema,
                            coroutine=MAP_FUNCOES_NATIVAS[f_db.nome_ferramenta],
                        )
                        tools_disponiveis.append(tool)
                        nomes_tools.append(str(getattr(tool, "name", "")).strip())
                    elif getattr(f_db, "url", None):
                        headers_str = getattr(f_db, "headers", "{}")
                        payload_str = getattr(f_db, "payload", "{}")

                        def create_http_tool_coroutine(url, method, headers_json, payload_json, nome_tool):
                            async def http_tool_coroutine(**kwargs) -> str:
                                import httpx

                                h_dict = json.loads(headers_json) if headers_json else {}
                                p_dict = json.loads(payload_json) if payload_json else {}

                                final_url = url or ""
                                for k, v in kwargs.items():
                                    final_url = final_url.replace(f"{{{{{k}}}}}", str(v))
                                    final_url = final_url.replace(f"{{{k}}}", str(v))

                                p_str = json.dumps(p_dict)
                                for k, v in kwargs.items():
                                    p_str = p_str.replace(f"{{{{{k}}}}}", str(v))
                                final_payload = json.loads(p_str)

                                async with httpx.AsyncClient() as client:
                                    if method.upper() == "GET":
                                        resp = await client.get(final_url, headers=h_dict, timeout=10.0)
                                    elif method.upper() == "POST":
                                        resp = await client.post(final_url, headers=h_dict, json=final_payload, timeout=10.0)
                                    elif method.upper() == "PUT":
                                        resp = await client.put(final_url, headers=h_dict, json=final_payload, timeout=10.0)
                                    elif method.upper() == "DELETE":
                                        resp = await client.delete(final_url, headers=h_dict, timeout=10.0)
                                    else:
                                        return f"Método HTTP não suportado: {method}"
                                return resp.text

                            return http_tool_coroutine

                        tool = StructuredTool(
                            name=f_db.nome_ferramenta,
                            description=f_db.descricao_ia,
                            args_schema=args_schema,
                            coroutine=create_http_tool_coroutine(
                                f_db.url,
                                f_db.metodo,
                                headers_str,
                                payload_str,
                                f_db.nome_ferramenta,
                            ),
                        )
                        tools_disponiveis.append(tool)
                        nomes_tools.append(str(getattr(tool, "name", "")).strip())
                except Exception as e:
                    erros_etapa_1.append(f"Falha ferramenta nativa '{f_db.nome_ferramenta}': {e}")
                    print(f"[ETAPA 1][ERRO] Ferramenta nativa '{f_db.nome_ferramenta}': {e}")
                    traceback.print_exc()

            destinos = await listar_destinos_transferencia_para_prompt(empresa_id)
            result_lead = await session.execute(
                select(CRMLead)
                .where(CRMLead.empresa_id == especialista.empresa_id)
                .limit(1)
            )
            lead = result_lead.scalars().first()
            if lead and destinos:
                tool_transferencia = criar_ferramenta_transferir_atendimento_contextual(
                    lead_id=str(lead.id),
                    empresa_id=empresa_id,
                    conexao_id=None,
                )
                tools_disponiveis.append(tool_transferencia)
                nomes_tools.append(str(getattr(tool_transferencia, "name", "")).strip())

            nomes_tools = [n for n in nomes_tools if n]
            print(f"[ETAPA 1] Ferramentas carregadas: {nomes_tools}")
            if erros_etapa_1:
                print(f"[ETAPA 1] Erros capturados: {erros_etapa_1}")
        except Exception as e:
            print(f"[ETAPA 1][ERRO FATAL] Falha na montagem das ferramentas: {e}")
            traceback.print_exc()
            return

        llm = await get_llm(empresa_id=empresa_id, modelo_ia=getattr(especialista, "modelo_ia", None))
        prompt_base = (
            "Você é um extrator de dados.\n"
            "Use as ferramentas disponíveis para buscar a informação solicitada.\n"
            "Retorne APENAS dados brutos encontrados, em JSON simples ou tópicos diretos.\n"
            "NÃO redija mensagens para o cliente final.\n"
            f"\nCONTEXTO_TECNICO_ESPECIALISTA:\n{str(getattr(especialista, 'prompt_sistema', '') or '').strip()}\n"
            f"{contexto_adicional}"
        )

        llm_para_invocar = llm
        if tools_disponiveis:
            print("[ETAPA 2] Fazendo bind_tools...")
            try:
                llm_para_invocar = llm.bind_tools(tools_disponiveis)
                print("[ETAPA 2] bind_tools concluído.")
            except Exception as e:
                print(f"[ETAPA 2][ERRO] Falha no bind_tools: {e}")
                traceback.print_exc()
                return
        else:
            print("[ETAPA 2] Sem ferramentas para bind_tools; seguirá sem bind.")

        print("[ETAPA 3] Invocando LLM...")
        try:
            resposta = await llm_para_invocar.ainvoke(
                [("system", prompt_base), ("user", MENSAGEM_TESTE)]
            )
            conteudo = str(getattr(resposta, "content", "") or "").strip()
            print("[ETAPA 3] Resposta recebida com sucesso.")
            print(f"[ETAPA 3] Conteúdo: {conteudo[:1000]}")
            if hasattr(resposta, "tool_calls") and getattr(resposta, "tool_calls"):
                print(f"[ETAPA 3] Tool calls detectadas: {[t.get('name') for t in resposta.tool_calls]}")
        except Exception as e:
            print(f"[ETAPA 3][ERRO] Falha ao invocar LLM: {e}")
            traceback.print_exc()
            return


if __name__ == "__main__":
    asyncio.run(main())
