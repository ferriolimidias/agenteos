import json
import logging
from typing import Any, Dict, Optional, Type
import httpx
from pydantic import BaseModel, Field, create_model
from langchain_core.tools import StructuredTool

from db.models import APIConnection

logger = logging.getLogger(__name__)

# Mapping from JSON Schema Draft 7 types to Python types
_JSON_SCHEMA_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _normalize_params_schema(raw_schema: Any) -> dict:
    """
    Normaliza o schema vindo do banco para um JSON Schema de objeto.

    Aceita:
    - JSON Schema completo (mantém, garantindo type=object quando aplicável)
    - Formato simplificado do painel, ex: {"cep": "string"}
    - Formato simplificado com metadados por campo,
      ex: {"cep": {"type":"string","description":"CEP","required": true}}
    """
    if not isinstance(raw_schema, dict) or not raw_schema:
        return {"type": "object", "properties": {}, "required": []}

    schema_keywords = {
        "type", "properties", "required", "description", "additionalProperties",
        "items", "oneOf", "anyOf", "allOf", "$schema", "$id", "title"
    }

    # Já parece JSON Schema (com properties ou keywords canônicas)
    if "properties" in raw_schema or any(k in raw_schema for k in schema_keywords):
        normalized = dict(raw_schema)
        if "properties" in normalized and "type" not in normalized:
            normalized["type"] = "object"
        if normalized.get("type") == "object":
            normalized.setdefault("properties", {})
            normalized.setdefault("required", [])
        return normalized

    # Formato simplificado: {"campo": "string"} ou {"campo": {"type":"string"}}
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    valid_type_names = set(_JSON_SCHEMA_TYPE_MAP.keys())

    for field_name, field_definition in raw_schema.items():
        field_schema: dict[str, Any]

        if isinstance(field_definition, str):
            field_type = field_definition if field_definition in valid_type_names else "string"
            field_schema = {
                "type": field_type,
                "description": f"Parâmetro '{field_name}' da ferramenta.",
            }
            required.append(field_name)
        elif isinstance(field_definition, dict):
            # Ex.: {"type":"string","description":"...","required":true}
            raw_type = field_definition.get("type", "string")
            field_type = raw_type if raw_type in valid_type_names else "string"
            field_schema = dict(field_definition)
            field_schema["type"] = field_type
            field_schema.setdefault("description", f"Parâmetro '{field_name}' da ferramenta.")

            # No formato simplificado com metadados, se required não vier explícito, assume obrigatório.
            is_required = field_definition.get("required")
            if is_required is None or bool(is_required):
                required.append(field_name)
        else:
            field_schema = {
                "type": "string",
                "description": f"Parâmetro '{field_name}' da ferramenta.",
            }
            required.append(field_name)

        properties[field_name] = field_schema

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _create_pydantic_model_from_json_schema(schema: dict, model_name: str = "DynamicModel") -> Type[BaseModel]:
    """
    Creates a Pydantic v2 BaseModel dynamically from a valid JSON Schema (Draft 7).
    Respects 'properties', 'description', 'type', and the 'required' array strictly.
    Required fields use Ellipsis (...) — Pydantic treats them as mandatory.
    Optional fields default to None.
    """
    schema = _normalize_params_schema(schema)

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

    # --- Parse params schema from DB ---
    schema_dict = connection.params_schema_json
    if isinstance(schema_dict, str) and schema_dict:
        try:
            schema_dict = json.loads(schema_dict)
        except json.JSONDecodeError:
            schema_dict = {}
    if not isinstance(schema_dict, dict):
        schema_dict = {}
    schema_dict = _normalize_params_schema(schema_dict)

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
        logger.info("[TOOL EXECUTION] Calling '%s' with params: %s", _nome, kwargs)
        try:
            # URL template substitution (supports {param} and {{param}})
            formatted_url = (_url or "")
            for k, v in kwargs.items():
                formatted_url = formatted_url.replace(f"{{{k}}}", str(v))
                formatted_url = formatted_url.replace(f"{{{{{k}}}}}", str(v))

            # Detect unresolved placeholders to avoid calls with invalid URL templates.
            if "{" in formatted_url or "}" in formatted_url:
                msg = (
                    f"Tool Execution Failed: URL template não preenchido para '{_nome}'. "
                    f"URL atual: {formatted_url}. Parâmetros recebidos: {kwargs}"
                )
                logger.warning("[TOOL ERROR] %s", msg)
                return msg

            # Avoid duplicating path vars in querystring when URL already interpolated.
            query_params = {k: v for k, v in kwargs.items() if v is not None and f"{{{k}}}" not in (_url or "") and f"{{{{{k}}}}}" not in (_url or "")}

            logger.info(
                "[TOOL HTTP REQUEST] tool=%s method=%s url=%s query_params=%s",
                _nome,
                _metodo,
                formatted_url,
                query_params if _metodo == "GET" else {},
            )

            async with httpx.AsyncClient(timeout=15.0) as client:
                if _metodo == "GET":
                    response = await client.get(formatted_url, headers=_headers, params=query_params)
                elif _metodo == "POST":
                    response = await client.post(formatted_url, headers=_headers, json=kwargs)
                elif _metodo == "PUT":
                    response = await client.put(formatted_url, headers=_headers, json=kwargs)
                elif _metodo == "DELETE":
                    response = await client.delete(formatted_url, headers=_headers)
                else:
                    return f"Tool Execution Failed: HTTP method '{_metodo}' is not supported. Analise e avise o usuario."

                logger.info(
                    "[TOOL HTTP RESPONSE] tool=%s method=%s url=%s status_code=%s",
                    _nome,
                    _metodo,
                    formatted_url,
                    response.status_code,
                )

                # Raise for 4xx/5xx so the except block handles it uniformly
                response.raise_for_status()

                resultado = response.text
                logger.info("[TOOL RESULT] '%s' returned payload with %d chars", _nome, len(resultado))
                return resultado

        except httpx.TimeoutException as e:
            msg = f"Tool Execution Failed: Timeout ao chamar '{_nome}' ({e}). Analise e avise o usuario."
            logger.warning("[TOOL ERROR] %s", msg)
            return msg
        except httpx.HTTPStatusError as e:
            response_text = e.response.text if e.response is not None else str(e)
            msg = (
                f"Tool Execution Failed: HTTP {e.response.status_code} ao chamar '{_nome}'. "
                f"Resposta: {response_text}"
            )
            logger.warning("[TOOL ERROR] %s", msg)
            return msg
        except Exception as e:
            msg = f"Tool Execution Failed: {type(e).__name__} ao executar '{_nome}': {str(e)}. Analise e avise o usuario."
            logger.exception("[TOOL ERROR] %s", msg)
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
