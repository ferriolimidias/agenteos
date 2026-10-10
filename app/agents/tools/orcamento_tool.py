from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any

from app.agents.schemas import ConversationState, IncomingMessage
from app.agents.tools.registry import AgentToolResult, make_tool_result


async def run_orcamento_tool(
    *,
    state: ConversationState,
    message: IncomingMessage | None,
    tenant_config: dict[str, Any] | None = None,
) -> AgentToolResult:
    config_orcamento = _extract_orcamento_config(tenant_config)
    if config_orcamento:
        structured = _calculate_from_tenant_config(state=state, message=message, config_orcamento=config_orcamento)
        missing_fields = list(structured.get("missing_fields") or [])
        return make_tool_result(
            name="orcamento",
            ok=not missing_fields and bool((structured.get("estimate") or {}).get("amount")),
            data=structured,
            missing_fields=missing_fields,
            error=None,
        )

    slots = dict(state.slots or {})
    structured = {
        "summary": "Configuracao estruturada de orcamento indisponivel.",
        "service_type": slots.get("service_type"),
        "slots": slots,
        "estimate": {"amount": None, "currency": "BRL", "items": [], "slots": slots, "next_action": "collect_missing_fields"},
        "missing_fields": ["tipo_de_servico"] if not slots.get("service_type") else ["measure"],
        "requires_human_confirmation": True,
        "source": "structured_tool_no_config",
        "memory_update": {
            "active_flow": "orcamento",
            "active_intent": "orcamento",
            "pending_slot": "service_type" if not slots.get("service_type") else "measure",
            "slots": slots,
        },
        "actions": [],
    }
    return make_tool_result(
        name="orcamento",
        ok=False,
        data=structured,
        missing_fields=list(structured["missing_fields"]),
        error=None,
    )


def _extract_orcamento_config(tenant_config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(tenant_config, dict):
        return {}
    credenciais = tenant_config.get("credenciais_canais")
    if not isinstance(credenciais, dict):
        return {}
    config = credenciais.get("orcamento")
    return dict(config) if isinstance(config, dict) and config.get("enabled") else {}


def _calculate_from_tenant_config(
    *,
    state: ConversationState,
    message: IncomingMessage | None,
    config_orcamento: dict[str, Any],
) -> dict[str, Any]:
    text = normalize_text((message.text if message else None) or state.mensagem_atual or "")
    slots = dict(state.slots or {})
    services = config_orcamento.get("services") if isinstance(config_orcamento.get("services"), dict) else {}
    currency = str(config_orcamento.get("currency") or "BRL").strip() or "BRL"
    results: list[dict[str, Any]] = []
    missing_fields: list[str] = []
    matched_service_type: str | None = None

    for service_key, service in services.items():
        if not isinstance(service, dict):
            continue
        service_match = _service_matches_text(service_key, service, text)
        if not service_match and slots.get("service_type") != service_key:
            continue
        matched_service_type = str(service_key)
        item = _build_item(service_key, service, text, slots, currency)
        if item.get("selected"):
            results.append(item)
            continue
        missing_fields.extend(item.get("missing_fields") or [])

    if not results and slots.get("service_type") and slots.get("service_type") in services:
        service_key = str(slots.get("service_type"))
        service = services.get(service_key)
        if isinstance(service, dict):
            item = _build_item(service_key, service, text, slots, currency)
            if item.get("selected"):
                results.append(item)
            else:
                missing_fields.extend(item.get("missing_fields") or [])

    if not results and not matched_service_type:
        missing_fields.append("tipo_de_servico")

    total = sum(Decimal(str(item.get("price") or 0)) for item in results if item.get("price") is not None)
    public_items = [
        {
            "service_key": item.get("service_key"),
            "label": item.get("label"),
            "quantity": item.get("quantity"),
            "measure": item.get("measure"),
            "price": float(item.get("price")) if item.get("price") is not None else None,
            "requires_confirmation": bool(item.get("requires_confirmation")),
        }
        for item in results
    ]
    service_type = results[0]["service_key"] if results else matched_service_type or slots.get("service_type")
    summary = f"Orçamento prévio calculado para {len(results)} item(ns)."
    if len(results) == 1:
        summary = f"Orçamento prévio calculado para {results[0].get('label') or service_type}."
    return {
        "summary": summary,
        "service_type": service_type,
        "slots": slots,
        "estimate": {
            "service_type": service_type,
            "amount": float(total) if results else None,
            "currency": currency,
            "items": public_items,
            "slots": slots,
            "preliminary": True,
            "next_action": "offer_scheduling" if results else "collect_missing_fields",
        },
        "missing_fields": [item for item in _dedupe(missing_fields) if item],
        "requires_human_confirmation": any(item.get("requires_confirmation") for item in results),
        "source": "tenant_orcamento_config",
        "memory_update": {
            "active_flow": "orcamento",
            "active_intent": "orcamento",
            "pending_slot": None if results else ([item for item in _dedupe(missing_fields) if item] or ["service_type"])[0],
            "slots": _merge_slots({**slots, **({"service_type": service_type} if service_type else {})}, results),
            "last_budget_estimate": {
                "amount": float(total) if results else None,
                "currency": currency,
                "items": public_items,
                "total": float(total) if results else None,
                "preliminary": True,
                "next_action": "offer_scheduling" if results else "collect_missing_fields",
            }
            if results
            else None,
            "last_agent_question": None if results else "dados do item para orçamento",
        },
        "actions": [
            {
                "action_type": "registrar_orcamento_previo",
                "scope": "crm",
                "risk": "low",
                "requires_confirmation": False,
            }
        ]
        if results
        else [
            {
                "action_type": "coletar_dados_orcamento",
                "scope": "conversation",
                "risk": "low",
                "requires_confirmation": False,
            }
        ],
    }


def _service_matches_text(service_key: str, service: dict[str, Any], text: str) -> bool:
    terms = {normalize_text(str(service_key))}
    for field in ("label", "response_label", "response_label_plural", "missing_field"):
        value = service.get(field)
        if value:
            terms.add(normalize_text(str(value)))
    for alias in service.get("aliases") or []:
        terms.add(normalize_text(str(alias)))
    variants = service.get("variants") if isinstance(service.get("variants"), dict) else {}
    for variant_key, variant in variants.items():
        terms.add(normalize_text(str(variant_key)))
        if isinstance(variant, dict):
            if variant.get("label"):
                terms.add(normalize_text(str(variant.get("label"))))
            for alias in variant.get("aliases") or []:
                terms.add(normalize_text(str(alias)))
    return any(term and term in text for term in terms)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _build_item(
    service_key: str,
    service: dict[str, Any],
    text: str,
    slots: dict[str, Any],
    currency: str,
) -> dict[str, Any]:
    pricing_type = str(service.get("pricing_type") or "unit").strip()
    label = str(service.get("label") or service_key).strip()
    if pricing_type == "tier_by_measure":
        measure = _extract_measure(text, slots)
        tiers = service.get("tiers") if isinstance(service.get("tiers"), list) else []
        if measure is None:
            return {
                "service_key": service_key,
                "label": label,
                "selected": False,
                "missing_fields": [str(service.get("missing_field") or "tamanho_do_item")],
            }
        tier = _select_tier(measure, tiers)
        if tier is None:
            return {
                "service_key": service_key,
                "label": label,
                "selected": False,
                "missing_fields": [str(service.get("missing_field") or "tamanho_do_item")],
                "requires_confirmation": True,
            }
        price = float(tier.get("price") or 0)
        return {
            "service_key": service_key,
            "label": label,
            "measure": measure,
            "quantity": 1,
            "price": price,
            "selected": True,
            "currency": currency,
            "requires_confirmation": bool(tier.get("requires_confirmation")),
        }

    if pricing_type == "variant":
        variants = service.get("variants") if isinstance(service.get("variants"), dict) else {}
        variant_key, variant = _select_variant(text, service, variants)
        if not variant or not isinstance(variant, dict):
            return {
                "service_key": service_key,
                "label": label,
                "selected": False,
                "missing_fields": [str(service.get("missing_field") or "tipo_do_item")],
            }
        price = float(variant.get("price") or 0)
        return {
            "service_key": service_key,
            "label": str(variant.get("label") or label).strip(),
            "variant": variant_key,
            "quantity": 1,
            "price": price,
            "selected": True,
            "currency": currency,
            "requires_confirmation": bool(variant.get("requires_confirmation")),
        }

    quantity = _extract_quantity(text, slots)
    if quantity is None:
        quantity = 1
    unit_price = _select_unit_price(service, quantity)
    if unit_price is None:
        return {
            "service_key": service_key,
            "label": label,
            "selected": False,
            "missing_fields": [str(service.get("missing_field") or "quantidade")],
        }
    price = float(unit_price) * float(quantity)
    return {
        "service_key": service_key,
        "label": label,
        "quantity": quantity,
        "price": price,
        "selected": True,
        "currency": currency,
        "requires_confirmation": bool(service.get("requires_confirmation")),
    }


def _extract_measure(text: str, slots: dict[str, Any]) -> float | None:
    if isinstance(slots.get("measure"), (int, float)):
        return float(slots.get("measure"))
    if isinstance(slots.get("measure"), str):
        match = re.search(r"\d+(?:[,.]\d+)?", slots.get("measure"))
        if match:
            return float(match.group(0).replace(",", "."))
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:m|metro|metros)\b", text)
    if match:
        return float(match.group(1).replace(",", "."))
    match = re.fullmatch(r"(?:mais ou menos\s+)?(\d+(?:[,.]\d+)?)(?:\s+mais ou menos)?", text)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def _extract_quantity(text: str, slots: dict[str, Any]) -> int | None:
    for key in ("quantity", "quantidade", "qtd"):
        value = slots.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    match = re.search(r"\b(\d+)\s*(?:unidades?|cadeiras?)\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\b", text)
    if match:
        return int(match.group(1))
    return None


def _select_tier(measure: float, tiers: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [tier for tier in tiers if isinstance(tier, dict) and tier.get("price") is not None]
    if not usable:
        return None
    usable.sort(key=lambda tier: float(tier.get("max") or tier.get("limit") or 0))
    for tier in usable:
        limit = tier.get("max") if tier.get("max") is not None else tier.get("limit")
        if limit is None:
            continue
        if float(measure) <= float(limit):
            return tier
    return None


def _select_variant(text: str, service: dict[str, Any], variants: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    for variant_key, variant in variants.items():
        if not isinstance(variant, dict):
            continue
        terms = {normalize_text(str(variant_key))}
        if variant.get("label"):
            terms.add(normalize_text(str(variant.get("label"))))
        for alias in variant.get("aliases") or []:
            terms.add(normalize_text(str(alias)))
        if any(term and term in text for term in terms):
            return str(variant_key), variant
    default_variant = service.get("default_variant")
    if isinstance(default_variant, str) and default_variant in variants and isinstance(variants[default_variant], dict):
        return default_variant, variants[default_variant]
    return None, None


def _select_unit_price(service: dict[str, Any], quantity: int) -> float | None:
    if service.get("unit_price") is not None:
        return float(service.get("unit_price"))
    tiers = service.get("tiers") if isinstance(service.get("tiers"), list) else []
    if tiers:
        tier = _select_tier(float(quantity), tiers)
        if tier and tier.get("price") is not None:
            return float(tier.get("price"))
    return None


def _merge_slots(slots: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(slots or {})
    for item in items:
        if item.get("service_key"):
            merged["service_type"] = item.get("service_key")
        if item.get("measure") is not None:
            merged["measure"] = item.get("measure")
        if item.get("quantity") is not None:
            merged["quantity"] = item.get("quantity")
        if item.get("variant") is not None:
            merged["variant"] = item.get("variant")
    return merged


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
