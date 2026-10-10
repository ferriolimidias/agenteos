from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.secret_sanitizer import sanitize_for_log
from app.services.campaign_audience_service import canonicalize_audience_rules, normalize_phone, resolve_audience_rules
from app.services.campaign_central_policy import PolicyInput, evaluate_message_policy, estimate_cost
from app.services.media_storage import sanitize_original_name
from app.services.meta.image_validation import validate_image_bytes
from app.services.meta_templates import (
    parse_template_components,
    render_template_preview,
    validate_template_parameters,
)


TECHNICAL_NAME_RE = re.compile(r"^[a-z0-9_]{1,512}$")
TEMPLATE_PARAMETER_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")
TEMPLATE_ANY_PARAMETER_RE = re.compile(r"\{\{([^{}]*)\}\}")
STATIC_WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+")
SUPPORTED_TEMPLATE_STATUSES = {
    "local_draft",
    "submitted",
    "pending",
    "approved",
    "rejected",
    "paused",
    "disabled",
    "archived",
    "unknown",
}
MAX_TEMPLATE_IMAGE_BYTES = 5 * 1024 * 1024
SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png"}
SUPPORTED_TEMPLATE_BUTTON_TYPES = {"QUICK_REPLY", "URL", "PHONE_NUMBER"}
SUPPORTED_CAMPAIGN_VARIABLE_BINDINGS = {"FIXED_VALUE", "CONTACT_NAME"}
CAMPAIGN_TERMINAL_STATUSES = {"completed", "cancelled", "failed", "finished", "done"}


def normalize_meta_template_category(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    if raw == "MARKETING":
        return "marketing"
    if raw == "UTILITY":
        return "utility"
    if raw == "AUTHENTICATION":
        return "authentication"
    return "unknown"


def deterministic_idempotency_key(*parts: Any) -> str:
    material = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"), default=str)


def _campaign_status_is_terminal(value: Any) -> bool:
    return str(value or "").strip().lower() in CAMPAIGN_TERMINAL_STATUSES


def _audience_has_explicit_inclusion(rules: dict[str, Any]) -> bool:
    include = rules.get("include") if isinstance(rules.get("include"), dict) else {}
    return any(
        str(rule.get("tag_id") or "").strip()
        for rule in include.get("rules", [])
        if isinstance(rule, dict)
    )


@dataclass(frozen=True)
class TemplateVariable:
    position: int
    represents: str
    component: str = "body"
    example: str | None = None


def extract_numeric_variables(text_value: str) -> list[int]:
    return sorted({int(match) for match in TEMPLATE_PARAMETER_RE.findall(text_value or "")})


def template_static_word_count(text_value: str) -> int:
    return len(STATIC_WORD_RE.findall(TEMPLATE_PARAMETER_RE.sub(" ", text_value or "")))


def preflight_meta_template_payload(components: list[dict[str, Any]], variables: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    facts = {
        "body_starts_with_parameter": False,
        "body_ends_with_parameter": False,
        "body_parameter_count": 0,
        "body_static_word_count": 0,
        "body_parameter_density_status": "OK",
    }
    variable_examples = {
        int(item.get("position")): str(item.get("example") or "").strip()
        for item in variables or []
        if str(item.get("position") or "").isdigit()
    }
    for component in components or []:
        ctype = str(component.get("type") or "").upper()
        if ctype != "BODY":
            continue
        text = str(component.get("text") or "")
        stripped = text.strip()
        positions = [int(match.group(1)) for match in TEMPLATE_PARAMETER_RE.finditer(text)]
        facts["body_parameter_count"] = len(positions)
        facts["body_static_word_count"] = template_static_word_count(text)
        for match in TEMPLATE_ANY_PARAMETER_RE.finditer(text):
            raw = str(match.group(1) or "")
            if not re.fullmatch(r"\s*\d+\s*", raw):
                errors.append({"field": "body", "code": "BODY_PARAMETER_INVALID", "message": "Use variáveis no formato {{1}}, {{2}}, {{3}}."})
                break
        if stripped:
            first = TEMPLATE_PARAMETER_RE.match(stripped)
            if first and first.start() == 0:
                facts["body_starts_with_parameter"] = True
                errors.append({"field": "body", "code": "BODY_STARTS_WITH_PARAMETER", "message": "O texto do template não pode começar com uma variável. Adicione texto fixo antes dela."})
            matches = list(TEMPLATE_PARAMETER_RE.finditer(stripped))
            if matches and matches[-1].end() == len(stripped):
                facts["body_ends_with_parameter"] = True
                last_position = matches[-1].group(1)
                errors.append({"field": "body", "code": "BODY_ENDS_WITH_PARAMETER", "message": f"O texto do template não pode terminar com uma variável. Adicione texto fixo depois de {{{{{last_position}}}}}."})
        if re.search(r"\}\}\s*\{\{", text):
            warnings.append({"field": "body", "code": "BODY_ADJACENT_PARAMETERS_REVIEW_RISK", "message": "Variáveis consecutivas sem texto fixo podem aumentar o risco de rejeição; considere adicionar contexto entre elas."})
        expected_sequence = list(range(1, len(positions) + 1))
        if positions and positions != expected_sequence:
            errors.append({"field": "variables", "code": "BODY_PARAMETERS_NOT_SEQUENTIAL", "message": "Use variáveis sequenciais no corpo: {{1}}, {{2}}, {{3}}."})
        missing_examples = [position for position in positions if not variable_examples.get(position)]
        if missing_examples:
            errors.append({"field": "variables", "code": "BODY_PARAMETER_EXAMPLES_MISMATCH", "message": "Informe exemplos para todas as variáveis do corpo antes de enviar para a Meta."})
        if len(positions) >= 7:
            facts["body_parameter_density_status"] = "WARNING_HEURISTIC"
            warnings.append({
                "field": "body",
                "code": "PARAMETER_DENSITY_REVIEW_RISK",
                "message": "A Meta informa que excesso de variáveis em relação ao texto pode causar rejeição; revise se há texto fixo suficiente entre os parâmetros.",
            })
        break
    return {"valid": not errors, "errors": errors, "warnings": warnings, "facts": facts}


def normalize_template_variables(variables: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in variables or []:
        if not isinstance(item, dict):
            continue
        try:
            position = int(item.get("position"))
        except (TypeError, ValueError):
            continue
        if position <= 0 or position in seen:
            continue
        seen.add(position)
        represents = str(item.get("represents") or item.get("friendly_name") or item.get("label") or item.get("name") or item.get("description") or "").strip()[:120]
        normalized.append(
            {
                "position": position,
                "represents": represents,
                "component": str(item.get("component") or "body").strip().lower()[:40],
                "example": str(item.get("example") or item.get("sample") or item.get("value") or "").strip()[:160],
            }
        )
    return sorted(normalized, key=lambda item: item["position"])


def normalize_template_buttons(buttons: list[Any], *, strict: bool = True) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    quick_reply_count = 0
    cta_count = 0
    for item in buttons or []:
        if not isinstance(item, dict):
            continue
        btype = str(item.get("type") or "").strip().upper()
        if btype not in SUPPORTED_TEMPLATE_BUTTON_TYPES:
            continue
        text_value = str(item.get("text") or item.get("title") or "").strip()[:25]
        if strict and not text_value:
            continue
        button: dict[str, Any] = {"type": btype, "text": text_value}
        if btype == "QUICK_REPLY":
            quick_reply_count += 1
        elif btype == "URL":
            cta_count += 1
            url = str(item.get("url") or "").strip()[:2000]
            if strict and not url:
                continue
            button["url"] = url
            if item.get("example"):
                button["example"] = [str(item.get("example"))[:2000]]
        elif btype == "PHONE_NUMBER":
            cta_count += 1
            phone = str(item.get("phone_number") or item.get("phone") or "").strip()[:32]
            if strict and not phone:
                continue
            button["phone_number"] = phone
        normalized.append(button)
    if quick_reply_count and cta_count:
        # Meta does not allow mixing quick replies with CTA buttons in the same
        # BUTTONS component. Keep only the first homogeneous group.
        first_type = normalized[0]["type"] if normalized else ""
        normalized = [button for button in normalized if (button["type"] == "QUICK_REPLY") == (first_type == "QUICK_REPLY")]
    return normalized[:10]


def validate_technical_name(name: str) -> tuple[bool, str | None]:
    value = str(name or "").strip()
    if not value:
        return False, "Informe o nome técnico."
    if not TECHNICAL_NAME_RE.match(value):
        return False, "Use apenas letras minúsculas, números e underscores."
    return True, None


def validate_template_header_image_upload(content: bytes, *, declared_mime: str | None, original_name: str | None) -> dict[str, Any]:
    mime_type = str(declared_mime or "").split(";", 1)[0].strip().lower()
    width, height, detected = validate_image_bytes(content, mime_type, max_bytes=MAX_TEMPLATE_IMAGE_BYTES)
    filename = sanitize_original_name(original_name)
    suffix = Path(filename).suffix.lower()
    if mime_type == "image/jpeg" and suffix not in {".jpg", ".jpeg"}:
        filename = f"{Path(filename).stem or 'template-header'}.jpg"
    if mime_type == "image/png" and suffix != ".png":
        filename = f"{Path(filename).stem or 'template-header'}.png"
    return {
        "filename": filename,
        "mime_type": mime_type,
        "byte_size": len(content),
        "width": width,
        "height": height,
        "detected": detected,
    }


def validate_template_header_image_local_media(value: Any) -> tuple[bool, str | None]:
    if not isinstance(value, dict) or not value.get("selected"):
        return False, "Selecione uma imagem de exemplo antes de enviar para análise da Meta."
    mime_type = str(value.get("mime_type") or "").split(";", 1)[0].strip().lower()
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return False, "Use imagem JPEG ou PNG."
    try:
        size = int(value.get("size") or value.get("byte_size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return False, "A imagem de exemplo está vazia."
    if size > MAX_TEMPLATE_IMAGE_BYTES:
        return False, "A imagem deve ter no máximo 5 MB."
    return True, None


def validate_campaign_image_upload(content: bytes, *, declared_mime: str | None, original_name: str | None) -> dict[str, Any]:
    image = validate_template_header_image_upload(content, declared_mime=declared_mime, original_name=original_name)
    return {**image, "purpose": "campaign_header_image"}


def template_requires_header_image(components: list[dict[str, Any]] | None) -> bool:
    for component in components or []:
        if str(component.get("type") or "").upper() == "HEADER" and str(component.get("format") or "").upper() == "IMAGE":
            return True
    return False


def build_template_components(payload: dict[str, Any], *, strict_buttons: bool = True) -> list[dict[str, Any]]:
    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), dict) else {}
    header = blocks.get("header") if isinstance(blocks.get("header"), dict) else {}
    body = blocks.get("body") if isinstance(blocks.get("body"), dict) else {}
    footer = blocks.get("footer") if isinstance(blocks.get("footer"), dict) else {}
    buttons = normalize_template_buttons(blocks.get("buttons") if isinstance(blocks.get("buttons"), list) else [], strict=strict_buttons)
    unknown = blocks.get("unknown_components") if isinstance(blocks.get("unknown_components"), list) else []

    components: list[dict[str, Any]] = []
    header_kind = str(header.get("kind") or "none").lower()
    if header_kind != "none":
        component = {"type": "HEADER", "format": header_kind.upper()}
        if header_kind == "text":
            component["text"] = str(header.get("text") or "")
        if header.get("example"):
            component["example"] = header.get("example")
        components.append(component)
    body_text = str(body.get("text") or "").strip()
    if body_text:
        component = {"type": "BODY", "text": body_text}
        if body.get("example"):
            component["example"] = body.get("example")
        components.append(component)
    if str(footer.get("text") or "").strip():
        components.append({"type": "FOOTER", "text": str(footer.get("text")).strip()})
    if buttons:
        components.append({"type": "BUTTONS", "buttons": buttons})
    for item in unknown:
        if isinstance(item, dict):
            components.append(item)
    return components


def validate_template_payload(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    ok, error = validate_technical_name(str(payload.get("technical_name") or payload.get("name") or ""))
    if not ok:
        errors.append({"field": "technical_name", "message": error or "Nome técnico inválido."})
    if normalize_meta_template_category(payload.get("category")) == "unknown":
        errors.append({"field": "category", "message": "Selecione marketing, utility ou authentication."})
    if not str(payload.get("language") or "").strip():
        errors.append({"field": "language", "message": "Informe o idioma."})
    components = build_template_components(payload, strict_buttons=False)
    if not any(component.get("type") == "BODY" and str(component.get("text") or "").strip() for component in components):
        errors.append({"field": "body", "message": "O corpo do template é obrigatório."})
    variable_positions = extract_numeric_variables("\n".join(str(component.get("text") or "") for component in components))
    variables = normalize_template_variables(payload.get("variables") if isinstance(payload.get("variables"), list) else [])
    variable_map = {int(item.get("position")): item for item in variables}
    expected_sequence = list(range(1, len(variable_positions) + 1))
    if variable_positions and variable_positions != expected_sequence:
        errors.append({"field": "variables", "message": "Use variáveis numéricas contínuas: {{1}}, {{2}}, {{3}}."})
    for position in variable_positions:
        item = variable_map.get(position) or {}
        if not str(item.get("represents") or "").strip():
            errors.append({"field": f"variable_{position}", "message": f"Informe o que {{%d}} representa." % position})
    for item in variables:
        if int(item["position"]) not in variable_positions:
            errors.append({"field": f"variable_{item['position']}", "message": f"{{{{{item['position']}}}}} está na lista, mas não aparece no texto."})
    button_component = next((component for component in components if component.get("type") == "BUTTONS"), None)
    if button_component:
        buttons = button_component.get("buttons") if isinstance(button_component.get("buttons"), list) else []
        quick_reply = [button for button in buttons if button.get("type") == "QUICK_REPLY"]
        cta = [button for button in buttons if button.get("type") in {"URL", "PHONE_NUMBER"}]
        if quick_reply and cta:
            errors.append({"field": "buttons", "message": "Não misture resposta rápida com botões de link/telefone no mesmo template."})
        if len(quick_reply) > 10:
            errors.append({"field": "buttons", "message": "Use no máximo 10 botões de resposta rápida."})
        if len(cta) > 2:
            errors.append({"field": "buttons", "message": "Use no máximo 2 botões de link/telefone."})
        for index, button in enumerate(buttons):
            btype = str(button.get("type") or "").upper()
            if btype not in SUPPORTED_TEMPLATE_BUTTON_TYPES:
                errors.append({"field": f"button_{index}", "message": "Tipo de botão não suportado."})
            if not str(button.get("text") or "").strip():
                errors.append({"field": f"button_{index}_text", "message": "Informe o texto do botão."})
            if btype == "URL" and not str(button.get("url") or "").strip().startswith(("http://", "https://")):
                errors.append({"field": f"button_{index}_url", "message": "Informe uma URL http(s) válida."})
            if btype == "PHONE_NUMBER" and not str(button.get("phone_number") or "").strip().startswith("+"):
                errors.append({"field": f"button_{index}_phone", "message": "Informe telefone em formato internacional, começando com +."})
    return {"valid": not errors, "errors": errors, "components": components, "variable_positions": variable_positions, "variables": variables}


def validate_template_draft_payload(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    ok, error = validate_technical_name(str(payload.get("technical_name") or payload.get("name") or ""))
    if not ok:
        errors.append({"field": "technical_name", "message": error or "Nome técnico inválido."})
    if normalize_meta_template_category(payload.get("category")) == "unknown":
        errors.append({"field": "category", "message": "Selecione marketing, utility ou authentication."})
    if not str(payload.get("language") or "").strip():
        errors.append({"field": "language", "message": "Informe o idioma."})
    components = build_template_components(payload, strict_buttons=False)
    if not any(component.get("type") == "BODY" and str(component.get("text") or "").strip() for component in components):
        errors.append({"field": "body", "message": "O corpo do template é obrigatório."})
    variable_positions = extract_numeric_variables("\n".join(str(component.get("text") or "") for component in components))
    variables = normalize_template_variables(payload.get("variables") if isinstance(payload.get("variables"), list) else [])
    expected_sequence = list(range(1, len(variable_positions) + 1))
    if variable_positions and variable_positions != expected_sequence:
        errors.append({"field": "variables", "message": "Use variáveis numéricas contínuas: {{1}}, {{2}}, {{3}}."})
    return {"valid": not errors, "errors": errors, "components": components, "variable_positions": variable_positions, "variables": variables}


def template_parameter_key(spec: Any) -> str:
    if getattr(spec, "source", "") == "button_url":
        return str(spec.name)
    component = str(getattr(spec, "component_type", "BODY") or "BODY").lower()
    name = str(getattr(spec, "name", "") or "").strip()
    return f"{component}_{name}" if name.isdigit() else name


def _parameter_value(parameters: dict[str, Any], spec: Any) -> str:
    key = template_parameter_key(spec)
    value = parameters.get(key)
    if value in (None, ""):
        value = parameters.get(str(getattr(spec, "name", "") or ""))
    return str(value or "").strip()


def build_meta_template_create_payload(payload: dict[str, Any], validation: dict[str, Any] | None = None) -> dict[str, Any]:
    validation = validation or validate_template_payload(payload)
    if not validation.get("valid"):
        raise ValueError("Template invalido para criacao na Meta.")
    components = [dict(component) for component in validation.get("components") or []]
    variable_examples = {
        str(item.get("position")): str(item.get("example") or "").strip()
        for item in validation.get("variables") or []
        if str(item.get("example") or "").strip()
    }
    for component in components:
        text = str(component.get("text") or "")
        positions = [str(position) for position in extract_numeric_variables(text)]
        if not positions:
            continue
        ctype = str(component.get("type") or "").upper()
        examples = [variable_examples.get(position) for position in positions]
        if any(not example for example in examples):
            continue
        if ctype == "BODY":
            component["example"] = {"body_text": [examples]}
        elif ctype == "HEADER" and str(component.get("format") or "").upper() == "TEXT":
            component["example"] = {"header_text": examples}
    return {
        "name": str(payload.get("technical_name") or payload.get("name") or "").strip(),
        "language": str(payload.get("language") or "").strip(),
        "category": str(payload.get("category") or "").strip().upper(),
        "components": components,
    }


def build_meta_template_send_payload(*, template_name: str, language: str, parsed_template: Any, parameters: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    button_components: list[dict[str, Any]] = []
    for spec in parsed_template.placeholders:
        value = _parameter_value(parameters, spec)
        if not value:
            continue
        if spec.source == "button_url":
            parts = str(spec.name or "").split("_", 2)
            index = parts[1] if len(parts) >= 3 and parts[1].isdigit() else "0"
            button_components.append(
                {"type": "button", "sub_type": "url", "index": index, "parameters": [{"type": "text", "text": value}]}
            )
            continue
        ctype = str(spec.component_type or "BODY").lower()
        if ctype == "header" and spec.expected_type == "image":
            grouped.setdefault(ctype, []).append({"type": "image", "image": {"id": value}})
        else:
            grouped.setdefault(ctype, []).append({"type": "text", "text": value})
    payload_components = [
        {"type": ctype, "parameters": values}
        for ctype, values in grouped.items()
        if values
    ] + button_components
    template_payload: dict[str, Any] = {
        "name": str(template_name or "").strip(),
        "language": {"code": str(language or "").strip()},
    }
    if payload_components:
        template_payload["components"] = payload_components
    return template_payload


def render_campaign_history_content(
    components: list[dict[str, Any]] | None,
    parsed_template: Any,
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    """Render the recipient-specific content that is persisted in Chat history."""
    rendered = render_template_preview(components, template_preview_parameters(parsed_template, parameters or {}))
    rendered_components = rendered.get("components") if isinstance(rendered, dict) else []
    rendered_components = rendered_components if isinstance(rendered_components, list) else []
    body = "\n\n".join(
        str(item.get("text") or "").strip()
        for item in rendered_components
        if isinstance(item, dict)
        and str(item.get("type") or "").upper() == "BODY"
        and str(item.get("text") or "").strip()
    )
    return {"body": body, "components": rendered_components}


def normalize_campaign_variable_bindings(bindings: Any) -> dict[str, dict[str, Any]]:
    raw = bindings if isinstance(bindings, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, item in raw.items():
        if isinstance(item, dict):
            btype = str(item.get("type") or item.get("source") or "FIXED_VALUE").strip().upper()
            value = str(item.get("value") or "").strip()
            fallback = str(item.get("fallback") or "").strip()
        else:
            btype = "FIXED_VALUE"
            value = str(item or "").strip()
            fallback = ""
        if btype not in SUPPORTED_CAMPAIGN_VARIABLE_BINDINGS:
            btype = "FIXED_VALUE"
        normalized[str(key)] = {"type": btype, "value": value[:500], "fallback": fallback[:160]}
    return normalized


def template_required_parameter_keys(parsed_template: Any) -> list[str]:
    return [template_parameter_key(spec) for spec in parsed_template.placeholders]


def template_preview_parameters(parsed_template: Any, parameters: dict[str, Any]) -> dict[str, Any]:
    preview = dict(parameters or {})
    for spec in parsed_template.placeholders:
        key = template_parameter_key(spec)
        if key in preview:
            preview.setdefault(str(getattr(spec, "name", "") or ""), preview[key])
        else:
            raw_name = str(getattr(spec, "name", "") or "")
            if raw_name in preview:
                preview.setdefault(key, preview[raw_name])
    return preview


def _lead_display_name(lead: Any) -> str:
    for attr in ("nome_contato", "nome", "name"):
        value = str(getattr(lead, attr, "") or "").strip()
        if value:
            return value[:160]
    return ""


def resolve_campaign_template_parameters_for_lead(*, parsed_template: Any, bindings: dict[str, dict[str, Any]], lead: Any, media: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    values: dict[str, Any] = {}
    missing: list[str] = []
    for spec in parsed_template.placeholders:
        key = template_parameter_key(spec)
        if getattr(spec, "expected_type", "text") in {"image", "video", "document"}:
            media_id = str((media or {}).get("media_id") or "").strip()
            if media_id:
                values[key] = media_id
            else:
                missing.append(key)
            continue
        binding = bindings.get(key) or bindings.get(str(getattr(spec, "name", "") or "")) or {}
        btype = str(binding.get("type") or "FIXED_VALUE").upper()
        value = ""
        if btype == "CONTACT_NAME":
            value = _lead_display_name(lead)
        elif btype == "FIXED_VALUE":
            value = str(binding.get("value") or "").strip()
        if not value and str(binding.get("fallback") or "").strip():
            value = str(binding.get("fallback") or "").strip()
        if value:
            values[key] = value[:1000]
        else:
            missing.append(key)
    return values, missing


def validate_template_payload_for_meta_create(payload: dict[str, Any], *, require_remote_header_handle: bool = True) -> dict[str, Any]:
    validation = validate_template_payload(payload)
    errors = list(validation.get("errors") or [])
    preflight = preflight_meta_template_payload(validation.get("components") or [], validation.get("variables") or [])
    errors.extend(preflight.get("errors") or [])
    if str(payload.get("language") or "").strip() != "pt_BR":
        # The backend accepts other Meta languages, but pt_BR is explicitly covered by this MVP flow.
        pass
    variables = validation.get("variables") or []
    example_by_position = {
        int(item.get("position")): str(item.get("example") or "").strip()
        for item in variables
        if str(item.get("position") or "").isdigit()
    }
    for position in validation.get("variable_positions") or []:
        if not example_by_position.get(int(position)):
            errors.append({"field": f"variable_{position}_example", "message": f"Informe um exemplo para {{{{{position}}}}} antes de enviar para a Meta."})
    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), dict) else {}
    header_block = blocks.get("header") if isinstance(blocks.get("header"), dict) else {}
    header_image_local_ready = False
    for component in validation.get("components") or []:
        if str(component.get("type") or "").upper() == "HEADER" and str(component.get("format") or "").upper() == "IMAGE":
            example = component.get("example") if isinstance(component.get("example"), dict) else {}
            handles = example.get("header_handle") if isinstance(example.get("header_handle"), list) else []
            if not handles or not str(handles[0] or "").strip():
                local_ready, local_error = validate_template_header_image_local_media(header_block.get("local_media"))
                header_image_local_ready = local_ready
                if require_remote_header_handle:
                    errors.append({"field": "header_image", "message": "Selecione uma imagem de exemplo antes de enviar para análise da Meta."})
                elif not local_ready:
                    errors.append({"field": "header_image", "message": local_error or "Selecione uma imagem de exemplo antes de enviar para análise da Meta."})
            else:
                header_image_local_ready = True
    meta_payload = None
    if not errors and require_remote_header_handle:
        meta_payload = build_meta_template_create_payload(payload, {**validation, "valid": True})
    return {
        **validation,
        "valid": not errors,
        "errors": errors,
        "warnings": list(preflight.get("warnings") or []),
        "preflight": preflight,
        "meta_payload": meta_payload,
        "readiness": {
            "local_template_ready": not errors,
            "meta_submission_payload_ready": bool(meta_payload),
            "header_image_local_ready": header_image_local_ready,
        },
    }


async def create_local_template_draft(session: Any, *, empresa_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select, text

    from db.models import Conexao

    validation = validate_template_draft_payload(payload)
    if not validation["valid"]:
        return {"created": False, "validation": validation}
    conexao_id = payload.get("conexao_id")
    if not conexao_id:
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Selecione a conexão WhatsApp oficial da empresa."}]}
    try:
        conexao_uuid = uuid.UUID(str(conexao_id))
    except (TypeError, ValueError):
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Conexão inválida."}]}
    connection = (
        await session.execute(select(Conexao).where(Conexao.id == conexao_uuid, Conexao.empresa_id == empresa_id))
    ).scalars().first()
    if not connection:
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Conexão não pertence à empresa."}]}
    provider = str(getattr(connection, "provider", "") or "").strip().lower()
    tipo = str(getattr(getattr(connection, "tipo", ""), "value", getattr(connection, "tipo", "")) or "").strip().lower()
    if provider not in {"meta", "meta_official", "meta_whatsapp", "whatsapp_meta_official"} and tipo != "meta":
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Templates oficiais exigem conexão WhatsApp Meta Official."}]}
    waba_id = str(getattr(connection, "waba_id", "") or "").strip()
    if not waba_id:
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Conexão oficial sem WABA."}]}
    template_id = uuid.uuid4()
    result = await session.execute(
        text(
            """
            insert into campaign_template_drafts
            (id, empresa_id, conexao_id, waba_id, internal_name, technical_name, language, category, description,
             status, components_json, variables_json, remote_action_state, created_at, updated_at)
            values
             (:id, :empresa_id, :conexao_id, :waba_id, :internal_name, :technical_name, :language, :category, :description,
             'local_draft', cast(:components as jsonb), cast(:variables as jsonb), 'local_only', now(), now())
            on conflict on constraint uq_campaign_template_drafts_name
            do update set
                internal_name = excluded.internal_name,
                category = excluded.category,
                description = excluded.description,
                components_json = excluded.components_json,
                variables_json = excluded.variables_json,
                remote_action_state = 'local_only',
                updated_at = now()
            where campaign_template_drafts.remote_template_id is null
            returning id
            """
        ),
        {
            "id": template_id,
            "empresa_id": empresa_id,
            "conexao_id": conexao_uuid,
            "waba_id": waba_id,
            "internal_name": str(payload.get("internal_name") or payload.get("technical_name") or "").strip(),
            "technical_name": str(payload.get("technical_name") or "").strip(),
            "language": str(payload.get("language") or "").strip(),
            "category": normalize_meta_template_category(payload.get("category")),
            "description": str(payload.get("description") or ""),
            "components": json.dumps(validation["components"]),
            "variables": json.dumps(validation["variables"]),
        },
    )
    row = result.mappings().first()
    if not row:
        return {
            "created": False,
            "validation": validation,
            "errors": [{"field": "technical_name", "message": "Já existe um template remoto/local submetido com este nome e idioma."}],
        }
    return {"created": True, "id": str(row["id"]), "validation": validation, "remote_action_state": "local_only"}


async def submit_template_to_meta(
    session: Any,
    *,
    empresa_id: uuid.UUID,
    payload: dict[str, Any],
    provider: Any,
    initiated_by: uuid.UUID | None = None,
) -> dict[str, Any]:
    from sqlalchemy import select, text

    from app.services.meta_templates import upsert_meta_template
    from db.models import Conexao

    validation = validate_template_payload_for_meta_create(payload)
    if not validation["valid"]:
        return {"created": False, "validation": validation}

    conexao_id = payload.get("conexao_id")
    if not conexao_id:
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Selecione a conexão WhatsApp oficial da empresa."}]}
    try:
        conexao_uuid = uuid.UUID(str(conexao_id))
    except (TypeError, ValueError):
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Conexão inválida."}]}
    connection = (
        await session.execute(select(Conexao).where(Conexao.id == conexao_uuid, Conexao.empresa_id == empresa_id))
    ).scalars().first()
    if not connection:
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Conexão não pertence à empresa."}]}
    provider_name = str(getattr(connection, "provider", "") or "").strip().lower()
    tipo = str(getattr(getattr(connection, "tipo", ""), "value", getattr(connection, "tipo", "")) or "").strip().lower()
    if provider_name not in {"meta", "meta_official", "meta_whatsapp", "whatsapp_meta_official"} and tipo != "meta":
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Templates oficiais exigem conexão WhatsApp Meta Official."}]}
    waba_id = str(getattr(connection, "waba_id", "") or "").strip()
    if not waba_id:
        return {"created": False, "validation": validation, "errors": [{"field": "conexao_id", "message": "Conexão oficial sem WABA."}]}

    technical_name = str(payload.get("technical_name") or payload.get("name") or "").strip()
    language = str(payload.get("language") or "").strip()
    existing = (
        await session.execute(
            text(
                """
                select id::text, normalized_status
                from meta_message_templates
                where empresa_id = :empresa_id
                  and conexao_id = :conexao_id
                  and waba_id = :waba_id
                  and name = :name
                  and language = :language
                limit 1
                """
            ),
            {
                "empresa_id": empresa_id,
                "conexao_id": conexao_uuid,
                "waba_id": waba_id,
                "name": technical_name,
                "language": language,
            },
        )
    ).mappings().first()
    if existing:
        return {
            "created": False,
            "validation": validation,
            "errors": [{"field": "technical_name", "message": "Já existe um template Meta com este nome e idioma nesta conexão."}],
            "existing_template_id": existing["id"],
            "existing_status": existing["normalized_status"],
        }

    meta_payload = validation["meta_payload"]
    response = await provider.create_template(waba_id=waba_id, template_payload=meta_payload)
    body = response.get("body") if isinstance(response, dict) else {}
    remote_id = str((body or {}).get("id") or (body or {}).get("message_template_id") or "").strip() or None
    raw_status = str((body or {}).get("status") or "PENDING").strip().upper()
    raw_category = str((body or {}).get("category") or meta_payload["category"]).strip().upper()
    meta_item = {
        "id": remote_id,
        "name": meta_payload["name"],
        "language": meta_payload["language"],
        "category": raw_category,
        "status": raw_status,
        "components": meta_payload["components"],
        "create_response": body,
    }
    template, _, _ = await upsert_meta_template(
        session,
        empresa_id=empresa_id,
        conexao_id=conexao_uuid,
        waba_id=waba_id,
        item=meta_item,
    )
    normalized_status = str(getattr(template, "normalized_status", "") or "unknown")
    draft = (
        await session.execute(
            text(
                """
                select id
                from campaign_template_drafts
                where empresa_id = :empresa_id
                  and conexao_id = :conexao_id
                  and waba_id = :waba_id
                  and technical_name = :technical_name
                  and language = :language
                limit 1
                """
            ),
            {
                "empresa_id": empresa_id,
                "conexao_id": conexao_uuid,
                "waba_id": waba_id,
                "technical_name": technical_name,
                "language": language,
            },
        )
    ).mappings().first()
    if draft:
        await session.execute(
            text(
                """
                update campaign_template_drafts
                   set status = :status,
                       remote_template_id = :remote_template_id,
                       remote_action_state = 'submitted_real',
                       submitted_at = now(),
                       updated_at = now()
                 where id = :id
                """
            ),
            {
                "id": draft["id"],
                "status": normalized_status if normalized_status in {"pending", "approved", "rejected", "paused", "disabled", "archived", "unknown"} else "submitted",
                "remote_template_id": remote_id,
            },
        )
    await session.execute(
        text(
            """
            insert into template_remote_action_audits
            (id, empresa_id, template_draft_id, meta_template_id, action, status, requested_by,
             sanitized_payload_json, external_call_allowed, idempotency_key, created_at)
            values
            (:id, :empresa_id, :template_draft_id, :meta_template_id, 'create_template', :status, :requested_by,
             cast(:payload as jsonb), true, :idempotency_key, now())
            """
        ),
        {
            "id": uuid.uuid4(),
            "empresa_id": empresa_id,
            "template_draft_id": draft["id"] if draft else None,
            "meta_template_id": template.id,
            "status": normalized_status,
            "requested_by": initiated_by,
            "payload": json.dumps(sanitize_for_log({"request": meta_payload, "response": body})),
            "idempotency_key": deterministic_idempotency_key("meta_template_create", empresa_id, conexao_uuid, waba_id, technical_name, language, uuid.uuid4()),
        },
    )
    return {
        "created": True,
        "http_status": response.get("http_status"),
        "meta_template_id": str(template.id),
        "remote_template_id": remote_id,
        "name": template.name,
        "language": template.language,
        "category": template.raw_category,
        "status": template.normalized_status,
        "raw_status": template.raw_status,
        "validation": validation,
        "template": {
            "id": str(template.id),
            "remote_template_id": remote_id,
            "name": template.name,
            "language": template.language,
            "normalized_status": template.normalized_status,
            "normalized_category": template.normalized_category,
        },
    }


def prepare_remote_template_action(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    allowed = {
        "create_template",
        "edit_template",
        "archive_template",
        "duplicate_template_local",
        "submit_template",
        "sync_template_status",
        "list_templates",
        "get_template",
    }
    if action == "delete_template":
        return {"allowed": False, "mocked": True, "reason": "delete_template_not_confirmed_safe"}
    return {
        "allowed": action in allowed,
        "mocked": True,
        "action": action,
        "audit_required": True,
        "external_call": False,
        "sanitized_payload": sanitize_for_log(payload or {}),
    }


async def campaign_overview(session: Any, *, empresa_id: uuid.UUID) -> dict[str, Any]:
    from sqlalchemy import select, text

    from db.models import CampaignRecipient, CampanhaDisparo, MetaMessageTemplate

    campaign_rows = (
        await session.execute(
            select(CampanhaDisparo.central_status, text("count(*)"))
            .where(CampanhaDisparo.empresa_id == empresa_id)
            .group_by(CampanhaDisparo.central_status)
        )
    ).all()
    recipient_rows = (
        await session.execute(
            select(CampaignRecipient.status, text("count(*)")).where(CampaignRecipient.empresa_id == empresa_id).group_by(CampaignRecipient.status)
        )
    ).all()
    template_rows = (
        await session.execute(
            select(MetaMessageTemplate.normalized_status, text("count(*)")).where(MetaMessageTemplate.empresa_id == empresa_id).group_by(MetaMessageTemplate.normalized_status)
        )
    ).all()
    return {
        "campaigns": {str(status.value if hasattr(status, "value") else status): int(count) for status, count in campaign_rows},
        "recipients": {str(status): int(count) for status, count in recipient_rows},
        "templates": {str(status): int(count) for status, count in template_rows},
        "costs": {"estimated": None, "derived": None, "confirmed": None, "state": "no_data"},
        "conversions": {"total": 0, "state": "no_data"},
        "alerts": [],
    }


async def prepare_campaign_snapshot(
    session: Any,
    *,
    empresa_id: uuid.UUID,
    payload: dict[str, Any],
    provider_factory: Any | None = None,
    campaign_image: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from sqlalchemy import select, text
    from sqlalchemy.orm import selectinload

    from db.models import CRMLead, Conexao, MetaMessageTemplate

    campanha_id = uuid.uuid4()
    now = datetime.utcnow()
    name = str(payload.get("name") or "").strip()
    ai_reply_enabled = bool(payload.get("ai_reply_enabled", True))
    if not name:
        return {"prepared": False, "errors": [{"field": "name", "message": "Informe o nome da campanha."}]}
    try:
        conexao_id = uuid.UUID(str(payload.get("conexao_id"))) if payload.get("conexao_id") else None
        template_id = uuid.UUID(str(payload.get("template_id"))) if payload.get("template_id") else None
    except (TypeError, ValueError):
        return {"prepared": False, "errors": [{"field": "template", "message": "Conexão ou template inválido."}]}
    if not conexao_id or not template_id:
        return {"prepared": False, "errors": [{"field": "template", "message": "Conexão e template oficial são obrigatórios."}]}
    connection = (
        await session.execute(select(Conexao).where(Conexao.id == conexao_id, Conexao.empresa_id == empresa_id))
    ).scalars().first()
    template = (
        await session.execute(
            select(MetaMessageTemplate)
            .where(MetaMessageTemplate.id == template_id, MetaMessageTemplate.empresa_id == empresa_id, MetaMessageTemplate.conexao_id == conexao_id)
            .options(selectinload(MetaMessageTemplate.components))
        )
    ).scalars().first()
    if not connection or not template:
        return {"prepared": False, "errors": [{"field": "scope", "message": "Conexão/template não pertencem à empresa."}]}
    provider = str(getattr(connection, "provider", "") or "").strip().lower()
    tipo = str(getattr(getattr(connection, "tipo", ""), "value", getattr(connection, "tipo", "")) or "").strip().lower()
    if provider not in {"meta", "meta_official", "meta_whatsapp", "whatsapp_meta_official"} and tipo != "meta":
        return {"prepared": False, "errors": [{"field": "conexao_id", "message": "Campanha oficial exige conexão WhatsApp Meta Official."}]}
    if str(getattr(connection, "status", "") or "").strip().lower() not in {"ativo", "connected", "conectado", "open", "online"}:
        return {"prepared": False, "errors": [{"field": "conexao_id", "message": "Conexão WhatsApp oficial inativa."}]}
    connection_waba_id = str(getattr(connection, "waba_id", "") or "").strip()
    connection_phone_number_id = str(getattr(connection, "phone_number_id", "") or "").strip()
    credentials = dict(getattr(connection, "credenciais", None) or {})
    token_present = bool(
        str(credentials.get("access_token") or "").strip()
        or str(credentials.get("system_user_token") or "").strip()
        or str(credentials.get("encrypted_access_token") or "").strip()
        or bool(credentials.get("token_configured"))
    )
    if not connection_waba_id:
        return {"prepared": False, "errors": [{"field": "conexao_id", "message": "Conexão oficial sem WABA."}]}
    if not connection_phone_number_id:
        return {"prepared": False, "errors": [{"field": "conexao_id", "message": "Conexão oficial sem phone_number_id."}]}
    if not token_present:
        return {"prepared": False, "errors": [{"field": "conexao_id", "message": "Conexão oficial sem token configurado."}]}
    if str(template.waba_id or "").strip() != connection_waba_id:
        return {"prepared": False, "errors": [{"field": "template", "message": "Template não pertence à WABA da conexão selecionada."}]}
    if template.normalized_status != "approved":
        return {"prepared": False, "errors": [{"field": "template", "message": "Campanhas oficiais exigem template aprovado."}]}

    audience = payload.get("audience") if isinstance(payload.get("audience"), dict) else {}
    audience_rules = audience.get("rules") if isinstance(audience.get("rules"), dict) else audience
    if payload.get("include_tags"):
        audience_rules = {**(audience_rules if isinstance(audience_rules, dict) else {}), "include_tags": payload.get("include_tags")}
    if payload.get("tag_logic"):
        audience_rules = {**(audience_rules if isinstance(audience_rules, dict) else {}), "tag_logic": payload.get("tag_logic")}
    saved_audience_id = str(payload.get("saved_audience_id") or audience.get("saved_audience_id") or "").strip()
    if saved_audience_id:
        try:
            saved_audience_uuid = uuid.UUID(saved_audience_id)
        except (TypeError, ValueError):
            return {"prepared": False, "errors": [{"field": "audience", "message": "Público salvo inválido."}]}
        saved = (
            await session.execute(
                text(
                    """
                    select rules_json
                    from saved_audiences
                    where empresa_id = :empresa_id and id = :id and status = 'active'
                    """
                ),
                {"empresa_id": empresa_id, "id": saved_audience_uuid},
            )
        ).mappings().first()
        if not saved:
            return {"prepared": False, "errors": [{"field": "audience", "message": "Público salvo não pertence à empresa ou está inativo."}]}
        audience_rules = saved.get("rules_json") if isinstance(saved.get("rules_json"), dict) else {}
    canonical_audience_rules = canonicalize_audience_rules(audience_rules if isinstance(audience_rules, dict) else {})
    if not _audience_has_explicit_inclusion(canonical_audience_rules):
        return {
            "prepared": False,
            "errors": [
                {
                    "field": "audience",
                    "message": "Selecione ao menos um público explícito antes de preparar a campanha.",
                }
            ],
        }
    audience_resolution = await resolve_audience_rules(
        session,
        empresa_id=empresa_id,
        rules=canonical_audience_rules,
        default_country_code=payload.get("default_country_code"),
        member_limit=100,
    )
    selected_lead_ids = [uuid.UUID(item) for item in audience_resolution.get("final_lead_ids", [])]
    selected: list[CRMLead] = []
    if selected_lead_ids:
        selected = (
            await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_id, CRMLead.id.in_(selected_lead_ids)))
        ).scalars().all()
    excluded = [
        {"lead_id": str(item.get("lead_id")), "reason": str(item.get("reason")), "detail": str(item.get("detail") or "")}
        for item in audience_resolution.get("members", {}).get("excluded", [])
        if item.get("lead_id")
    ]

    components = [component.raw_component_json for component in getattr(template, "components", [])]
    header_image_required = template_requires_header_image(components)
    campaign_media: dict[str, Any] | None = None
    if header_image_required:
        if not campaign_image:
            return {"prepared": False, "errors": [{"field": "campaign_image", "message": "Este template exige uma imagem da campanha."}]}
        if provider_factory is None:
            return {"prepared": False, "errors": [{"field": "campaign_image", "message": "Upload de imagem indisponível neste ambiente."}]}
        upload = await provider_factory(connection).upload_media_bytes(
            mime_type=str(campaign_image.get("mime_type") or ""),
            content=campaign_image.get("content") or b"",
            file_name=str(campaign_image.get("filename") or "campaign-image"),
        )
        campaign_media = {
            "purpose": "campaign_header_image",
            "media_id": upload["media_id"],
            "phone_number_id": upload["phone_number_id"],
            "phone_number_id_source": "conexoes.phone_number_id",
            "mime_type": campaign_image.get("mime_type"),
            "filename": campaign_image.get("filename"),
            "byte_size": campaign_image.get("byte_size"),
            "uploaded_at": now.isoformat(),
            "upload_http_status": upload.get("http_status"),
        }
    parsed_template = parse_template_components(components)
    variable_bindings = normalize_campaign_variable_bindings(
        payload.get("variable_bindings") if isinstance(payload.get("variable_bindings"), dict) else payload.get("variable_values")
    )
    legacy_parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
    preflight_missing = 0
    preflight_resolved = 0
    preview_parameters: dict[str, Any] = {}
    for lead in selected:
        resolved, missing = resolve_campaign_template_parameters_for_lead(
            parsed_template=parsed_template,
            bindings=variable_bindings,
            lead=lead,
            media=campaign_media,
        )
        if missing:
            preflight_missing += 1
        else:
            preflight_resolved += 1
            if not preview_parameters:
                preview_parameters = resolved
    parameters = {**legacy_parameters, **preview_parameters}
    if campaign_media:
        parameters = {**parameters, "header_image": campaign_media["media_id"]}
    required_parameter_keys = template_required_parameter_keys(parsed_template)
    missing_binding_keys: list[str] = []
    for spec in parsed_template.placeholders:
        key = template_parameter_key(spec)
        if getattr(spec, "expected_type", "text") in {"image", "video", "document"}:
            continue
        legacy_key = key.split("_", 1)[-1]
        if key not in variable_bindings and key not in legacy_parameters and legacy_key not in variable_bindings and legacy_key not in legacy_parameters:
            missing_binding_keys.append(key)
    parameter_validation = {
        "valid": not missing_binding_keys and (not selected or preflight_resolved > 0),
        "missing": missing_binding_keys,
        "required": required_parameter_keys,
        "recipient_missing_count": preflight_missing,
        "recipient_resolved_count": preflight_resolved,
        "requires_media": parsed_template.requires_media,
        "has_buttons": parsed_template.has_buttons,
        "has_dynamic_url": parsed_template.has_dynamic_url,
    }
    if not parameter_validation["valid"]:
        return {
            "prepared": False,
            "errors": [{"field": "variables", "message": "Preencha todos os parâmetros obrigatórios do template."}],
            "template_validation": parameter_validation,
        }

    template_payload = build_meta_template_send_payload(
        template_name=template.name,
        language=template.language,
        parsed_template=parsed_template,
        parameters=parameters,
    )
    preview = render_template_preview(components, template_preview_parameters(parsed_template, parameters))
    inclusions = []
    payload_previews = []
    for lead in selected:
        address, _ = normalize_phone(
            str(lead.telefone_contato or ""),
            default_country_code=payload.get("default_country_code"),
        )
        if not address:
            continue
        item = {
            "lead_id": str(lead.id),
            "address_hash": hashlib.sha256(address.encode()).hexdigest(),
            "template_name": template.name,
            "template_language": template.language,
            "phone_number_id_source": "conexoes.phone_number_id",
        }
        inclusions.append(item)
        if len(payload_previews) < 3:
            payload_previews.append(
                {
                    "to_hash": hashlib.sha256(address.encode()).hexdigest(),
                    "endpoint": f"/{connection_phone_number_id}/messages",
                    "phone_number_id_source": "conexoes.phone_number_id",
                    "template": template_payload,
                }
            )

    snapshot_identity = {
        "schema": 2,
        "name": name,
        "connection_id": str(conexao_id),
        "template": {
            "id": str(template_id),
            "name": template.name,
            "language": template.language,
        },
        "audience": canonical_audience_rules,
        "ai_reply_enabled": ai_reply_enabled,
        "default_country_code": payload.get("default_country_code"),
        "send_mode": str(credentials.get("campaign_send_mode") or "").strip().lower(),
        "media": sanitize_for_log(campaign_media or {}),
        "variable_bindings": sanitize_for_log(variable_bindings),
    }
    snapshot_base_key = deterministic_idempotency_key("campaign_snapshot_v2", empresa_id, _stable_json(snapshot_identity))
    legacy_snapshot_key = deterministic_idempotency_key("campaign_snapshot", empresa_id, name, template_id, conexao_id, canonical_audience_rules)
    terminal_campaign_count = int(
        (
            await session.execute(
                text(
                    """
                    select count(*)
                    from campaign_preparation_snapshots s
                    join campanhas_disparo c
                      on c.empresa_id = s.empresa_id and c.id = s.campaign_id
                    where s.empresa_id = :empresa_id
                      and s.idempotency_key = any(:idempotency_keys)
                      and lower(coalesce(c.central_status, '')) = any(:terminal_statuses)
                    """
                ),
                {
                    "empresa_id": empresa_id,
                    "idempotency_keys": [snapshot_base_key, legacy_snapshot_key],
                    "terminal_statuses": sorted(CAMPAIGN_TERMINAL_STATUSES),
                },
            )
        ).scalar_one()
    )
    snapshot_idempotency_key = (
        snapshot_base_key
        if terminal_campaign_count == 0
        else deterministic_idempotency_key("campaign_snapshot_v2", snapshot_base_key, "terminal_generation", terminal_campaign_count)
    )
    existing_snapshot = (
        await session.execute(
            text("""
                select s.id, s.campaign_id, s.inclusions_json, s.exclusions_json, c.central_status
                from campaign_preparation_snapshots s
                left join campanhas_disparo c
                  on c.empresa_id = s.empresa_id and c.id = s.campaign_id
                where s.empresa_id = :empresa_id and s.idempotency_key = :idempotency_key
                order by s.created_at desc
                limit 1
            """),
            {"empresa_id": empresa_id, "idempotency_key": snapshot_idempotency_key},
        )
    ).mappings().first()
    if existing_snapshot and not _campaign_status_is_terminal(existing_snapshot.get("central_status")):
        return {
            "prepared": True,
            "idempotent": True,
            "campaign_id": str(existing_snapshot["campaign_id"]),
            "snapshot_id": str(existing_snapshot["id"]),
            "snapshot": {
                "id": str(existing_snapshot["id"]),
                "selected": len(existing_snapshot.get("inclusions_json") or []),
                "excluded": len(existing_snapshot.get("exclusions_json") or []),
                "exclusions": existing_snapshot.get("exclusions_json") or [],
                "counts": {},
            },
            "outbox_mode": "safe_mode_only",
            "external_call": False,
            "status": "draft",
        }

    snapshot_id = uuid.uuid4()
    await session.execute(
        text(
            """
            insert into campaign_preparation_snapshots
            (id, empresa_id, campaign_id, filters_json, inclusions_json, exclusions_json, audience_version,
             idempotency_key, created_at)
            values (:id, :empresa_id, :campaign_id, cast(:filters as jsonb), cast(:inclusions as jsonb),
                    cast(:exclusions as jsonb), 1, :idempotency_key, now())
            """
        ),
        {
            "id": snapshot_id,
            "empresa_id": empresa_id,
            "campaign_id": campanha_id,
            "filters": json.dumps(
                {
                    "audience": canonical_audience_rules,
                    "audience_resolution": sanitize_for_log(
                        {
                            "counts": audience_resolution.get("counts", {}),
                            "rule_counts": audience_resolution.get("rule_counts", {}),
                            "reason_codes": audience_resolution.get("reason_codes", []),
                        }
                    ),
                    "name": name,
                    "ai_reply_enabled": ai_reply_enabled,
                    "default_country_code": payload.get("default_country_code"),
                    "sender": {
                        "conexao_id": str(connection.id),
                        "provider": "meta_whatsapp",
                        "waba_id": connection_waba_id,
                        "phone_number_id": connection_phone_number_id,
                        "display_phone": str(getattr(connection, "display_number", "") or ""),
                    },
                    "template": {
                        "id": str(template.id),
                        "name": template.name,
                        "language": template.language,
                        "waba_id": template.waba_id,
                        "status": template.normalized_status,
                        "requires_header_image": header_image_required,
                    },
                    "media": sanitize_for_log(campaign_media or {}),
                    "parameters": sanitize_for_log(parameters),
                    "variable_bindings": sanitize_for_log(variable_bindings),
                    "required_parameter_keys": required_parameter_keys,
                    "preflight": {
                        "variables_resolved": preflight_resolved,
                        "variables_missing": preflight_missing,
                    },
                    "safe_mode": True,
                    "external_call": False,
                }
            ),
            "inclusions": json.dumps(inclusions),
            "exclusions": json.dumps(excluded),
            "idempotency_key": snapshot_idempotency_key,
        },
    )
    policy = evaluate_message_policy(
        PolicyInput(
            provider="meta_whatsapp",
            channel="whatsapp",
            origin=payload.get("origin"),
            country=payload.get("country"),
            category=template.normalized_category,
            execution_at=now,
        )
    )
    return {
        "prepared": True,
        "campaign_id": str(campanha_id),
        "snapshot_id": str(snapshot_id),
        "snapshot": {
            "id": str(snapshot_id),
            "selected": len(selected),
            "excluded": audience_resolution.get("excluded", len(excluded)),
            "exclusions": excluded,
            "counts": audience_resolution.get("counts", {}),
        },
        "outbox_mode": "safe_mode_only",
        "external_call": False,
        "status": "draft",
        "sender": {
            "provider": "meta_whatsapp",
            "conexao_id": str(connection.id),
            "waba_id": connection_waba_id,
            "phone_number_id": connection_phone_number_id,
            "display_phone": str(getattr(connection, "display_number", "") or ""),
            "access_token_present": token_present,
        },
        "template": {
            "id": str(template.id),
            "name": template.name,
            "language": template.language,
            "waba_id": template.waba_id,
            "status": template.normalized_status,
            "validation": parameter_validation,
            "preview": preview,
            "requires_header_image": header_image_required,
            "variables": {
                "bindings": sanitize_for_log(variable_bindings),
                "required": required_parameter_keys,
                "resolved_recipients": preflight_resolved,
                "missing_recipients": preflight_missing,
            },
        },
        "payload_preview": {
            "endpoint": f"/{connection_phone_number_id}/messages",
            "phone_number_id_source": "conexoes.phone_number_id",
            "template": template_payload,
            "media": sanitize_for_log(campaign_media or {}),
            "sample": payload_previews,
            "real_sends": 0,
        },
        "policy": policy,
        "cost": estimate_cost(rate=None, currency=payload.get("currency"), free=policy["cost_estimation_state"] == "estimated_free"),
    }
