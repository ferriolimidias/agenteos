from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secret_sanitizer import sanitize_for_log
from app.services.campaign_audience_service import canonicalize_audience_rules, preview_saved_audience, save_audience


def _uuid(value: Any, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} inválido.") from exc


def _stage_rules_or_empty(rules: Any) -> dict[str, Any]:
    return canonicalize_audience_rules(rules if isinstance(rules, dict) else {})


def _merge_stage_rules(stages: list[dict[str, Any]], *, section: str) -> dict[str, Any]:
    values: list[dict[str, str]] = []
    seen: set[str] = set()
    for stage in stages:
        rules = _stage_rules_or_empty(stage.get("rules"))
        for rule in rules.get("include", {}).get("rules", []):
            tag_id = str(rule.get("tag_id") or "").strip()
            if not tag_id or tag_id in seen:
                continue
            seen.add(tag_id)
            values.append({"type": "tag", "tag_id": tag_id})
    return {"match": "any", "rules": values}


def build_not_advanced_rules(stage: dict[str, Any], later_stages: list[dict[str, Any]]) -> dict[str, Any]:
    return canonicalize_audience_rules(
        {
            "include": _merge_stage_rules([stage], section="include"),
            "exclude": _merge_stage_rules(later_stages, section="exclude"),
        }
    )


def _stage_has_rules(stage: dict[str, Any]) -> bool:
    rules = _stage_rules_or_empty(stage.get("rules"))
    return bool(rules.get("include", {}).get("rules"))


def build_smart_audiences(funnel: dict[str, Any]) -> list[dict[str, Any]]:
    stages = [stage for stage in funnel.get("stages", []) if stage.get("active") and _stage_has_rules(stage)]
    suggestions: list[dict[str, Any]] = []
    for index, stage in enumerate(stages):
        later = [item for item in stages[index + 1 :] if _stage_has_rules(item)]
        if later:
            name = f"{stage['name']}, mas não avançou"
            description = (
                f"Inclui {stage['name']} e exclui "
                + ", ".join(item["name"] for item in later)
            )
            rules = build_not_advanced_rules(stage, later)
            suggestion_type = "not_advanced"
        else:
            name = f"Todos em {stage['name']}"
            description = f"Inclui {stage['name']}."
            rules = canonicalize_audience_rules({"include": _merge_stage_rules([stage], section="include")})
            suggestion_type = "last_stage"
        suggestions.append(
            {
                "id": f"{funnel['id']}:{stage['id']}:{suggestion_type}",
                "source": "funnel_suggestion",
                "type": suggestion_type,
                "funnel_id": funnel["id"],
                "funnel_name": funnel["name"],
                "stage_id": stage["id"],
                "stage_name": stage["name"],
                "name": name,
                "description": description,
                "include_labels": [stage["name"]],
                "exclude_labels": [item["name"] for item in later],
                "rules": rules,
                "saved_behavior": "dynamic_rules_snapshot_only_when_campaign_prepares",
            }
        )
    return suggestions


async def list_campaign_funnels(session: AsyncSession, *, empresa_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                select
                    f.id::text as funnel_id,
                    f.nome as funnel_name,
                    coalesce(fs.description, '') as funnel_description,
                    coalesce(fs.status, 'active') as funnel_status,
                    e.id::text as stage_id,
                    e.nome as stage_name,
                    e.ordem as stage_position,
                    coalesce(sr.rules_json, '{}'::jsonb) as stage_rules,
                    coalesce(sr.status, 'active') as stage_status
                from crm_funis f
                left join campaign_audience_funnel_settings fs
                    on fs.empresa_id = f.empresa_id and fs.funnel_id = f.id
                left join crm_etapas e
                    on e.funil_id = f.id
                left join campaign_funnel_stage_rules sr
                    on sr.empresa_id = f.empresa_id and sr.stage_id = e.id
                where f.empresa_id = :empresa_id
                order by f.nome asc, e.ordem asc nulls last, e.id asc
                """
            ),
            {"empresa_id": empresa_id},
        )
    ).mappings().all()
    funnels: dict[str, dict[str, Any]] = {}
    for row in rows:
        funnel = funnels.setdefault(
            row["funnel_id"],
            {
                "id": row["funnel_id"],
                "name": row["funnel_name"],
                "description": row["funnel_description"],
                "active": str(row["funnel_status"] or "active") == "active",
                "order_source": "crm_etapas.ordem",
                "model_source": "CRMFunil/CRMEtapa + campaign_funnel_stage_rules",
                "stages": [],
            },
        )
        if row["stage_id"]:
            funnel["stages"].append(
                {
                    "id": row["stage_id"],
                    "name": row["stage_name"],
                    "position": int(row["stage_position"] or 0),
                    "active": str(row["stage_status"] or "active") == "active",
                    "rules": _stage_rules_or_empty(row["stage_rules"]),
                }
            )
    return list(funnels.values())


async def get_campaign_funnel(session: AsyncSession, *, empresa_id: uuid.UUID, funnel_id: uuid.UUID) -> dict[str, Any]:
    for funnel in await list_campaign_funnels(session, empresa_id=empresa_id):
        if str(funnel["id"]) == str(funnel_id):
            return funnel
    raise ValueError("Funil não encontrado para esta empresa.")


async def upsert_campaign_funnel(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Informe o nome do funil.")
    funnel_id = payload.get("id")
    if funnel_id:
        funnel_uuid = _uuid(funnel_id, "funnel_id")
        existing = (
            await session.execute(
                text("select id from crm_funis where id = :id and empresa_id = :empresa_id"),
                {"id": funnel_uuid, "empresa_id": empresa_id},
            )
        ).first()
        if not existing:
            raise ValueError("Funil não encontrado para esta empresa.")
        await session.execute(
            text("update crm_funis set nome = :name where id = :id and empresa_id = :empresa_id"),
            {"id": funnel_uuid, "empresa_id": empresa_id, "name": name[:160]},
        )
    else:
        funnel_uuid = uuid.uuid4()
        await session.execute(
            text("insert into crm_funis (id, empresa_id, nome) values (:id, :empresa_id, :name)"),
            {"id": funnel_uuid, "empresa_id": empresa_id, "name": name[:160]},
        )
    await session.execute(
        text(
            """
            insert into campaign_audience_funnel_settings
            (id, empresa_id, funnel_id, description, status, created_at, updated_at)
            values (:id, :empresa_id, :funnel_id, :description, :status, now(), now())
            on conflict (empresa_id, funnel_id) do update set
              description = excluded.description,
              status = excluded.status,
              updated_at = now()
            """
        ),
        {
            "id": uuid.uuid4(),
            "empresa_id": empresa_id,
            "funnel_id": funnel_uuid,
            "description": str(payload.get("description") or "")[:500],
            "status": "active" if payload.get("active", True) else "inactive",
        },
    )
    return await get_campaign_funnel(session, empresa_id=empresa_id, funnel_id=funnel_uuid)


async def upsert_funnel_stage(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    funnel_id: uuid.UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    await get_campaign_funnel(session, empresa_id=empresa_id, funnel_id=funnel_id)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Informe o nome da etapa.")
    position = int(payload.get("position") or payload.get("order") or 0)
    if position <= 0:
        position = 10
    stage_id = payload.get("id")
    if stage_id:
        stage_uuid = _uuid(stage_id, "stage_id")
        existing = (
            await session.execute(
                text(
                    """
                    select e.id
                    from crm_etapas e
                    join crm_funis f on f.id = e.funil_id
                    where e.id = :stage_id and e.funil_id = :funnel_id and f.empresa_id = :empresa_id
                    """
                ),
                {"stage_id": stage_uuid, "funnel_id": funnel_id, "empresa_id": empresa_id},
            )
        ).first()
        if not existing:
            raise ValueError("Etapa não encontrada para esta empresa.")
        await session.execute(
            text("update crm_etapas set nome = :name, ordem = :position where id = :stage_id and funil_id = :funnel_id"),
            {"name": name[:160], "position": position, "stage_id": stage_uuid, "funnel_id": funnel_id},
        )
    else:
        stage_uuid = uuid.uuid4()
        await session.execute(
            text("insert into crm_etapas (id, funil_id, nome, tipo, ordem) values (:id, :funnel_id, :name, 'campaign_audience', :position)"),
            {"id": stage_uuid, "funnel_id": funnel_id, "name": name[:160], "position": position},
        )
    rules = _stage_rules_or_empty(payload.get("rules"))
    await session.execute(
        text(
            """
            insert into campaign_funnel_stage_rules
            (id, empresa_id, funnel_id, stage_id, rules_json, status, created_at, updated_at)
            values (:id, :empresa_id, :funnel_id, :stage_id, cast(:rules as jsonb), :status, now(), now())
            on conflict (empresa_id, stage_id) do update set
              funnel_id = excluded.funnel_id,
              rules_json = excluded.rules_json,
              status = excluded.status,
              updated_at = now()
            """
        ),
        {
            "id": uuid.uuid4(),
            "empresa_id": empresa_id,
            "funnel_id": funnel_id,
            "stage_id": stage_uuid,
            "rules": json.dumps(sanitize_for_log(rules)),
            "status": "active" if payload.get("active", True) else "inactive",
        },
    )
    return await get_campaign_funnel(session, empresa_id=empresa_id, funnel_id=funnel_id)


async def reorder_funnel_stages(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    funnel_id: uuid.UUID,
    stages: list[dict[str, Any]],
) -> dict[str, Any]:
    await get_campaign_funnel(session, empresa_id=empresa_id, funnel_id=funnel_id)
    for item in stages:
        stage_id = _uuid(item.get("id") or item.get("stage_id"), "stage_id")
        position = int(item.get("position") or item.get("order") or 0)
        if position <= 0:
            raise ValueError("Posição de etapa inválida.")
        result = await session.execute(
            text(
                """
                update crm_etapas e
                set ordem = :position
                from crm_funis f
                where e.funil_id = f.id
                  and e.id = :stage_id
                  and e.funil_id = :funnel_id
                  and f.empresa_id = :empresa_id
                returning e.id
                """
            ),
            {"position": position, "stage_id": stage_id, "funnel_id": funnel_id, "empresa_id": empresa_id},
        )
        if not result.first():
            raise ValueError("Etapa não encontrada para esta empresa.")
    return await get_campaign_funnel(session, empresa_id=empresa_id, funnel_id=funnel_id)


async def set_funnel_stage_active(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    funnel_id: uuid.UUID,
    stage_id: uuid.UUID,
    active: bool,
) -> dict[str, Any]:
    funnel = await get_campaign_funnel(session, empresa_id=empresa_id, funnel_id=funnel_id)
    if not any(str(stage["id"]) == str(stage_id) for stage in funnel["stages"]):
        raise ValueError("Etapa não encontrada para esta empresa.")
    await session.execute(
        text(
            """
            insert into campaign_funnel_stage_rules
            (id, empresa_id, funnel_id, stage_id, rules_json, status, created_at, updated_at)
            values (:id, :empresa_id, :funnel_id, :stage_id, '{}'::jsonb, :status, now(), now())
            on conflict (empresa_id, stage_id) do update set
              status = excluded.status,
              updated_at = now()
            """
        ),
        {
            "id": uuid.uuid4(),
            "empresa_id": empresa_id,
            "funnel_id": funnel_id,
            "stage_id": stage_id,
            "status": "active" if active else "inactive",
        },
    )
    return await get_campaign_funnel(session, empresa_id=empresa_id, funnel_id=funnel_id)


async def list_smart_audiences(session: AsyncSession, *, empresa_id: uuid.UUID) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for funnel in await list_campaign_funnels(session, empresa_id=empresa_id):
        if funnel.get("active"):
            suggestions.extend(build_smart_audiences(funnel))
    return suggestions


async def preview_smart_audience(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    funnel_id: uuid.UUID,
    stage_id: uuid.UUID,
    suggestion_type: str = "not_advanced",
    member_view: str | None = None,
    member_cursor: str | None = None,
    member_limit: int = 25,
) -> dict[str, Any]:
    suggestions = [
        item
        for item in build_smart_audiences(await get_campaign_funnel(session, empresa_id=empresa_id, funnel_id=funnel_id))
        if str(item["stage_id"]) == str(stage_id) and item["type"] == suggestion_type
    ]
    if not suggestions:
        raise ValueError("Sugestão não disponível para esta etapa.")
    preview = await preview_saved_audience(
        session,
        empresa_id=empresa_id,
        rules=suggestions[0]["rules"],
        member_view=member_view,
        member_cursor=member_cursor,
        member_limit=member_limit,
    )
    return {**suggestions[0], "preview": preview}


async def save_smart_audience(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    funnel_id = _uuid(payload.get("funnel_id"), "funnel_id")
    stage_id = _uuid(payload.get("stage_id"), "stage_id")
    suggestion_type = str(payload.get("type") or "not_advanced")
    suggestion = await preview_smart_audience(
        session,
        empresa_id=empresa_id,
        funnel_id=funnel_id,
        stage_id=stage_id,
        suggestion_type=suggestion_type,
    )
    name = str(payload.get("name") or suggestion["name"]).strip()
    description = str(payload.get("description") or suggestion["description"]).strip()
    return await save_audience(
        session,
        empresa_id=empresa_id,
        payload={
            "name": name,
            "description": description,
            "rules": {
                **suggestion["rules"],
                "source": "funnel_suggestion",
                "funnel_id": str(funnel_id),
                "stage_id": str(stage_id),
                "suggestion_type": suggestion_type,
            },
        },
    )
