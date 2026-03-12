import json
from typing import Any, Dict, Optional, Type
import httpx
from pydantic import BaseModel, Field, create_model
from langchain_core.tools import StructuredTool

from db.models import APIConnection

# Mapping from JSON Schema Draft 7 types to Python types
_JSON_SCHEMA_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _create_pydantic_model_from_json_schema(schema: dict, model_name: str = "DynamicModel") -> Type[BaseModel]:
    """
    Creates a Pydantic v2 BaseModel dynamically from a valid JSON Schema (Draft 7).
    Respects 'properties', 'description', 'type', and the 'required' array strictly.
    Required fields use Ellipsis (...) — Pydantic treats them as mandatory.
    Optional fields default to None.
    """
    if not schema or "properties" not in schema:
        # No parameters needed — return an empty model
        return create_model(model_name)

    required_fields: list = schema.get("required", [])
    fields: Dict[str, Any] = {}

    for field_name, field_info in schema.get("properties", {}).items():
        field_type_str = field_info.get("type", "string")
        py_type = _JSON_SCHEMA_TYPE_MAP.get(field_type_str, Any)
        description = field_info.get("description", f"Parâmetro '{field_name}' da ferramenta.")

        is_required = field_name in required_fields

        if is_required:
            # Mandatory: Ellipsis signals Pydantic that this field has no default
            fields[field_name] = (py_type, Field(..., description=description))
        else:
            # Optional: defaults to None
            fields[field_name] = (Optional[py_type], Field(None, description=description))

    return create_model(model_name, **fields)


def create_dynamic_tool(connection: APIConnection) -> StructuredTool:
    """
    Receives an APIConnection ORM model and returns a LangChain StructuredTool.
    The tool uses httpx.AsyncClient and wraps ALL HTTP logic in a global try/except,
    returning a clean error string to the LLM instead of crashing LangGraph.
    """

    # --- Parse params schema from DB (JSON Schema Draft 7) ---
    schema_dict = connection.params_schema_json
    if isinstance(schema_dict, str) and schema_dict:
        try:
            schema_dict = json.loads(schema_dict)
        except json.JSONDecodeError:
            schema_dict = {}
    if not isinstance(schema_dict, dict):
        schema_dict = {}

    # --- Parse headers from DB ---
    headers_dict = connection.headers_json
    if isinstance(headers_dict, str) and headers_dict:
        try:
            headers_dict = json.loads(headers_dict)
        except json.JSONDecodeError:
            headers_dict = {}
    if not isinstance(headers_dict, dict):
        headers_dict = {}

    # --- Generate dynamic Pydantic model from the JSON Schema ---
    safe_model_name = "".join(c for c in connection.nome if c.isalnum()) or "Tool"
    DynamicArgsSchema = _create_pydantic_model_from_json_schema(
        schema_dict,
        model_name=f"{safe_model_name}Args"
    )

    # Capture tool metadata in closure (avoids late-binding bugs)
    _nome = connection.nome
    _url = connection.url
    _metodo = connection.metodo.upper() if connection.metodo else "GET"
    _headers = headers_dict

    async def tool_func(**kwargs) -> str:
        """Async execution of the external API call. Returns a string result always."""
        print(f"[TOOL EXECUTION] Calling '{_nome}' with params: {kwargs}")
        try:
            # URL template substitution: replace {param} placeholders with actual values
            formatted_url = _url or ""
            for k, v in kwargs.items():
                formatted_url = formatted_url.replace(f"{{{k}}}", str(v))

            async with httpx.AsyncClient(timeout=15.0) as client:
                if _metodo == "GET":
                    # Send non-None params as query string
                    query_params = {k: v for k, v in kwargs.items() if v is not None}
                    response = await client.get(formatted_url, headers=_headers, params=query_params)
                elif _metodo == "POST":
                    response = await client.post(formatted_url, headers=_headers, json=kwargs)
                elif _metodo == "PUT":
                    response = await client.put(formatted_url, headers=_headers, json=kwargs)
                elif _metodo == "DELETE":
                    response = await client.delete(formatted_url, headers=_headers)
                else:
                    return f"Tool Execution Failed: HTTP method '{_metodo}' is not supported. Analise e avise o usuario."

                # Raise for 4xx/5xx so the except block handles it uniformly
                response.raise_for_status()

                resultado = response.text
                print(f"[TOOL RESULT] '{_nome}' returned: {resultado[:300]}")
                return resultado

        except httpx.TimeoutException as e:
            msg = f"Tool Execution Failed: Timeout ao chamar '{_nome}' ({e}). Analise e avise o usuario."
            print(f"[TOOL ERROR] {msg}")
            return msg
        except httpx.HTTPStatusError as e:
            msg = f"Tool Execution Failed: HTTP {e.response.status_code} ao chamar '{_nome}'. Resposta: {e.response.text[:300]}. Analise e avise o usuario."
            print(f"[TOOL ERROR] {msg}")
            return msg
        except Exception as e:
            msg = f"Tool Execution Failed: {type(e).__name__} ao executar '{_nome}': {str(e)}. Analise e avise o usuario."
            print(f"[TOOL ERROR] {msg}")
            return msg

    # --- Build tool name (snake_case, alphanumeric only) ---
    tool_name = connection.nome.replace(" ", "_").lower()
    tool_name = "".join(c for c in tool_name if c.isalnum() or c == "_")
    if not tool_name:
        tool_name = f"tool_{str(connection.id)[:8]}"

    description = (
        connection.descricao
        if getattr(connection, "descricao", None)
        else f"Ferramenta dinâmica para acessar a API: {connection.nome}."
    )

    return StructuredTool(
        name=tool_name,
        description=description,
        args_schema=DynamicArgsSchema,
        coroutine=tool_func,  # async-safe: LangChain uses coroutine for async tools
    )
