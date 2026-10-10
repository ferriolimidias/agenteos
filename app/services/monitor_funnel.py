"""Generic, tenant-scoped funnel compiler and tag synchronizer.

The persisted representation deliberately lives in the existing tenant JSON
configuration.  Tags keep their stable identity; the managed marker in
``instrucao_ia`` is backwards compatible with the current schema.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from db.models import BusinessMessagingConversionMapping, Empresa, TagCRM

MARKER = "[AGENTEOS_MONITOR_FUNNEL]"
CATALOG = (
    ("content_engaged", "ViewContent", "Conteúdo engajado", True, "conversational"),
    ("lead_established", "LeadSubmitted", "Lead estabelecido", True, "conversational"),
    ("qualification_confirmed", "QualifiedLead", "Qualificação confirmada", True, "conversational"),
    ("selection_confirmed", "AddToCart", "Seleção confirmada", True, "conversational"),
    ("checkout_intent", "InitiateCheckout", "Checkout iniciado", False, "checkout"),
    ("order_commitment", "OrderCreated", "Pedido criado", False, "order"),
    ("abandonment", "CartAbandoned", "Carrinho abandonado", False, "deterministic"),
    ("purchase_confirmed", "Purchase", "Compra confirmada", False, "payment"),
)


def _spec(prompt: str, context: str = "") -> dict[str, Any]:
    source = (prompt or "").strip() or context.strip()
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    version = f"monitor-funnel-v1-{digest[:12]}"
    stages = []
    for order, (key, event, name, ai, policy) in enumerate(CATALOG, 1):
        stages.append({
            "key": key, "order": order, "display_name": name,
            "description": f"Etapa comercial genérica: {name.lower()}.",
            "detection_criteria": _criteria(key), "tag_spec": {"name": name},
            "meta_event": event, "source_policy": policy,
            "required_evidence": "evidência local do contato e mensagem identificável" if ai else "fonte operacional determinística",
            "monitor_allowed": ai,
        })
    return {"version": version, "prompt_hash": digest, "generated_at": datetime.now(timezone.utc).isoformat(), "stages": stages}


def _criteria(key: str) -> str:
    return {
        "content_engaged": "interação real com oferta, produto, serviço ou conteúdo específico",
        "lead_established": "lead comercial identificável e acionável",
        "qualification_confirmed": "critérios de qualificação definidos pelo tenant comprovadamente atendidos",
        "selection_confirmed": "escolha concreta de produto, serviço, plano, opção ou objeto",
        "checkout_intent": "checkout real criado ou iniciado por integração",
        "order_commitment": "pedido, reserva ou ordem realmente criada",
        "abandonment": "processo iniciado e abandonado conforme regra temporal",
        "purchase_confirmed": "pagamento confirmado pelo provider financeiro",
    }[key]


def compile_monitor_funnel_spec(empresa: Empresa) -> dict[str, Any]:
    return _spec(getattr(empresa, "conversion_monitor_prompt", None) or "", getattr(empresa, "ia_regras_negocio", None) or "")


def parse_managed_instruction(value: str | None) -> dict[str, Any] | None:
    text = str(value or "")
    if MARKER not in text:
        return None
    try:
        return json.loads(text.split(MARKER, 1)[1].strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _instruction(stage: dict[str, Any], version: str, old: str | None = None) -> str:
    payload = {"managed_by_monitor_funnel": True, "funnel_stage_key": stage["key"],
               "funnel_version": version, "meta_event_name": stage["meta_event"],
               "detection_criteria": stage["detection_criteria"], "display_order": stage["order"],
               "active": True, "monitor_allowed": bool(stage.get("monitor_allowed"))}
    base = str(old or "").split(MARKER, 1)[0].strip() or stage["detection_criteria"]
    return f"{base}\n{MARKER}{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"


async def sync_monitor_funnel_tags(session, empresa_id: Any) -> dict[str, Any]:
    tenant_id = uuid.UUID(str(empresa_id))
    empresa = (await session.execute(select(Empresa).where(Empresa.id == tenant_id))).scalars().first()
    if not empresa:
        raise ValueError("tenant_not_found")
    spec = compile_monitor_funnel_spec(empresa)
    tags = list((await session.execute(select(TagCRM).where(TagCRM.empresa_id == tenant_id))).scalars().all())
    managed = {str(t.id): parse_managed_instruction(t.instrucao_ia) for t in tags}
    by_key = {meta.get("funnel_stage_key"): tag for tag, meta in ((t, managed[str(t.id)]) for t in tags) if meta and meta.get("funnel_stage_key")}
    created = updated = preserved = 0
    mapping = []
    events = {stage["meta_event"] for stage in spec["stages"]}
    old_mappings = (await session.execute(select(BusinessMessagingConversionMapping).where(BusinessMessagingConversionMapping.empresa_id == tenant_id))).scalars().all()
    live = any(bool(m.ativo) and str(m.send_mode or "").lower() == "live" for m in old_mappings) or bool((getattr(empresa, "credenciais_canais", {}) or {}).get("meta_capi_ativo"))
    for stage in spec["stages"]:
        tag = by_key.get(stage["key"])
        if not tag:
            aliases = {"LeadSubmitted": ("informações", "lead"), "QualifiedLead": ("interesse", "qualif"), "AddToCart": ("vaga", "seleção"), "Purchase": ("paga", "compra")}.get(stage["meta_event"], ())
            tag = next((t for t in tags if t not in by_key.values() and any(a in (t.nome or "").lower() for a in aliases)), None)
        if tag:
            preserved += 1
            updated += 1
        else:
            tag = TagCRM(id=uuid.uuid4(), empresa_id=tenant_id, nome=stage["display_name"], tipo="conversao", ordem=stage["order"], ativa_no_funil=True, ai_can_apply=bool(stage["monitor_allowed"]), disparar_conversao_ads=True)
            session.add(tag); tags.append(tag); created += 1
        tag.nome = stage["display_name"]
        tag.tipo = "conversao"; tag.ordem = stage["order"]; tag.ativa_no_funil = True
        tag.ai_can_apply = bool(stage["monitor_allowed"])
        tag.disparar_conversao_ads = True
        tag.instrucao_ia = _instruction(stage, spec["version"], tag.instrucao_ia)
        await session.flush()
        current = next((m for m in old_mappings if m.trigger_type == "tag_applied" and m.trigger_ref == str(tag.id)), None)
        if not current:
            current = BusinessMessagingConversionMapping(empresa_id=tenant_id, trigger_type="tag_applied", trigger_ref=str(tag.id))
            session.add(current)
        current.event_name = stage["meta_event"]; current.ativo = True; current.send_mode = "live" if live else "dry_run"
        current.metadata_json = {"allowed_sources": ["monitor", "manual", "ai", "automation", "system", "integration"], "canonical": True, "commercial_fact": stage["key"]}
        mapping.append({"order": stage["order"], "tag": stage["display_name"], "event": stage["meta_event"], "criteria": stage["detection_criteria"], "tag_id": str(tag.id)[:8] + "…"})
    cred = dict(getattr(empresa, "credenciais_canais", None) or {})
    cred["monitor_funnel_spec"] = spec
    empresa.credenciais_canais = cred; flag_modified(empresa, "credenciais_canais")
    # Existing direct system/stage routes are retired; their records remain for audit/history.
    for item in old_mappings:
        if item.event_name in events and item.trigger_type != "tag_applied":
            item.ativo = False
    await session.commit()
    return {"spec": spec, "mapping": mapping, "created": created, "updated": updated, "preserved": preserved, "deprecated": 0}


async def preview_monitor_funnel(session, empresa_id: Any) -> dict[str, Any]:
    tenant_id = uuid.UUID(str(empresa_id))
    empresa = (await session.execute(select(Empresa).where(Empresa.id == tenant_id))).scalars().first()
    if not empresa:
        raise ValueError("tenant_not_found")
    spec = compile_monitor_funnel_spec(empresa)
    tags = (await session.execute(select(TagCRM).where(TagCRM.empresa_id == tenant_id))).scalars().all()
    keys = {meta.get("funnel_stage_key") for tag in tags if (meta := parse_managed_instruction(tag.instrucao_ia))}
    return {"spec": spec, "stage_count": len(spec["stages"]), "new_stage_count": len([s for s in spec["stages"] if s["key"] not in keys]), "stale": True}


async def managed_tag_for_fact(session, empresa_id: Any, fact_type: str) -> TagCRM | None:
    tags = (await session.execute(select(TagCRM).where(TagCRM.empresa_id == uuid.UUID(str(empresa_id))))).scalars().all()
    return next((tag for tag in tags if (parse_managed_instruction(tag.instrucao_ia) or {}).get("funnel_stage_key") == fact_type), None)
