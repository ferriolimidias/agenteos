"""A11 A/B hook: opt-in assignment, valid exposure only after the final guard."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from sqlalchemy import select

from app.services import a11_prompt_optimizer as optimizer
from app.services.a07_composer import MODULES
from db.models import A11PromptAssignment, A11PromptExperiment, A11PromptModuleState, A11PromptVersion

logger = logging.getLogger(__name__)


def _automatic_promotion_scan_interval() -> int:
    try:
        return max(300, min(86400, int(os.getenv("A11_AUTO_PROMOTION_SCAN_INTERVAL_SECONDS", "900"))))
    except (TypeError, ValueError):
        return 900


def _pilot_tenant_ids() -> list[uuid.UUID]:
    ids = []
    for value in os.getenv("A11_PILOT_TENANT_IDS", "").split(","):
        try:
            if value.strip():
                ids.append(uuid.UUID(value.strip()))
        except (ValueError, TypeError):
            continue
    return list(dict.fromkeys(ids))[:20]


async def process_automatic_promotions_once() -> dict:
    """Evaluate explicitly authorized live pilots with bounded, tenant-scoped scans."""
    tenant_ids = _pilot_tenant_ids()
    if not tenant_ids or not os.getenv("A11_AUTO_PROMOTION_LIVE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
        return {"checked": 0, "promoted": 0, "state": "OFF"}
    if os.getenv("A11_AUTO_PROMPT_MUTATION_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return {"checked": 0, "promoted": 0, "state": "OFF"}

    from db.database import AsyncSessionLocal

    checked = promoted = 0
    cutoff = datetime.utcnow() - timedelta(seconds=_automatic_promotion_scan_interval())
    for tenant_id in tenant_ids:
        flags = optimizer.live_status(tenant_id)
        if not flags["automatic_promotion_live"]:
            continue
        try:
            async with AsyncSessionLocal() as db:
                experiment = await db.scalar(select(A11PromptExperiment).where(
                    A11PromptExperiment.empresa_id == tenant_id,
                    A11PromptExperiment.status == "RUNNING",
                    (A11PromptExperiment.last_auto_evaluated_at.is_(None) | (A11PromptExperiment.last_auto_evaluated_at <= cutoff)),
                ).order_by(A11PromptExperiment.created_at.asc()).with_for_update(skip_locked=True).limit(1))
                if experiment is None:
                    continue
                # Row lock makes the bounded check single-owner across API
                # replicas. Persist the check time with the decision below.
                experiment.last_auto_evaluated_at = datetime.utcnow()
                result = await optimizer.promotion_decision(
                    db, tenant_id, experiment, actor_id="system:a11-auto-promotion",
                )
                db.add(optimizer._audit(
                    tenant_id,
                    actor_id="system:a11-auto-promotion",
                    action="automatic_promotion_evaluated",
                    object_type="experiment",
                    object_id=experiment.id,
                    details={
                        "reason": str(result.get("reason") or "unknown")[:120],
                        "eligible": bool(result.get("eligible")),
                        "promoted": bool(result.get("promoted")),
                        "p_value": result.get("p_value"),
                    },
                ))
                await db.commit()
                checked += 1
                promoted += int(bool(result.get("promoted")))
        except Exception as exc:
            logger.warning("a11.automatic_promotion_check_failed error_class=%s", type(exc).__name__)
    return {"checked": checked, "promoted": promoted, "state": "enabled"}


async def automatic_promotion_loop() -> None:
    """Default-OFF supervisor for scheduled, opt-in promotion decisions."""
    while True:
        try:
            result = await process_automatic_promotions_once()
            if result["checked"]:
                logger.info("a11.automatic_promotion_scan checked=%s promoted=%s", result["checked"], result["promoted"])
            await asyncio.sleep(_automatic_promotion_scan_interval())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("a11.automatic_promotion_scan_failed error_class=%s", type(exc).__name__)
            await asyncio.sleep(300)


async def prepare_experiment(state, composition: dict):
    tenant_text = str(getattr(state, "empresa_id", "") or "")
    flags = optimizer.live_status(tenant_text)
    if composition.get("mode") != "MODULAR" or not (flags["ab_live"] or flags["automatic_promotion_live"]):
        return None
    metadata = getattr(state, "metadata", None) or {}
    conversation_text = metadata.get("conversation_id") if isinstance(metadata, dict) else None
    try:
        tenant_id = uuid.UUID(tenant_text)
        conversation_id = uuid.UUID(str(conversation_text))
    except (ValueError, TypeError):
        return None
    selected = {str(item.get("id")) for item in composition.get("selected_modules", []) if isinstance(item, dict)}
    candidates = []
    for module_key in selected:
        base_instruction = MODULES.get(module_key, (None, None))[1]
        if not base_instruction or f"Módulo {module_key}@1.0.0: {base_instruction}" not in composition.get("prompt", ""):
            continue
        candidates.append((module_key, base_instruction))
    if not candidates:
        return None
    try:
        from db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            module_keys = [item[0] for item in candidates]
            assignment = None
            experiment = None
            selected_version_id = None
            selected_module_key = None
            mode = None
            if flags["ab_live"]:
                experiment = await db.scalar(select(A11PromptExperiment).where(
                    A11PromptExperiment.empresa_id == tenant_id,
                    A11PromptExperiment.module_key.in_(module_keys),
                    A11PromptExperiment.status == "RUNNING",
                    A11PromptExperiment.starts_at <= datetime.utcnow(),
                ).order_by(A11PromptExperiment.created_at.desc()).limit(1))
                if experiment and not (experiment.ends_at and experiment.ends_at <= datetime.utcnow()):
                    already_assigned = await db.scalar(select(A11PromptAssignment).where(
                        A11PromptAssignment.empresa_id == tenant_id,
                        A11PromptAssignment.experiment_id == experiment.id,
                        A11PromptAssignment.conversation_id == conversation_id,
                    ))
                    if already_assigned:
                        assignment = already_assigned
                        selected_version_id = assignment.prompt_version_id
                        mode = "existing_experiment_assignment"
                    else:
                        assigned = await optimizer.prepare_assignment(db, tenant_id, experiment, conversation_id)
                        if assigned:
                            assignment = await db.scalar(select(A11PromptAssignment).where(A11PromptAssignment.id == assigned["assignment_id"], A11PromptAssignment.empresa_id == tenant_id))
                            selected_version_id = assigned["version_id"]
                            mode = "new_experiment_assignment"
                    selected_module_key = experiment.module_key if selected_version_id else None
            if selected_version_id is None:
                # A paused experiment stops assigning new conversations, while
                # a conversation already assigned to it keeps its stable arm.
                paused = await db.scalar(select(A11PromptExperiment).where(
                    A11PromptExperiment.empresa_id == tenant_id,
                    A11PromptExperiment.module_key.in_(module_keys),
                    A11PromptExperiment.status == "PAUSED",
                ).order_by(A11PromptExperiment.created_at.desc()).limit(1))
                if paused:
                    assignment = await db.scalar(select(A11PromptAssignment).where(
                        A11PromptAssignment.empresa_id == tenant_id,
                        A11PromptAssignment.experiment_id == paused.id,
                        A11PromptAssignment.conversation_id == conversation_id,
                    ))
                    if assignment:
                        experiment = paused
                        selected_module_key = paused.module_key
                        selected_version_id = assignment.prompt_version_id
                        mode = "paused_experiment_assignment"
            if selected_version_id is None:
                # A rollback restores the control arm for previously assigned
                # conversations; completed experiments are not allowed to
                # keep applying a stale candidate to later turns.
                rolled_back = await db.execute(select(A11PromptAssignment, A11PromptExperiment)
                    .join(A11PromptExperiment, A11PromptExperiment.id == A11PromptAssignment.experiment_id)
                    .where(A11PromptAssignment.empresa_id == tenant_id, A11PromptAssignment.conversation_id == conversation_id,
                           A11PromptExperiment.empresa_id == tenant_id, A11PromptExperiment.module_key.in_(module_keys),
                           A11PromptExperiment.status == "ROLLED_BACK")
                    .order_by(A11PromptExperiment.ends_at.desc()).limit(1))
                rolled_back_pair = rolled_back.first()
                if rolled_back_pair:
                    _, experiment = rolled_back_pair
                    selected_module_key = experiment.module_key
                    selected_version_id = experiment.control_version_id
                    mode = "rollback_control"
            if selected_version_id is None and flags["automatic_promotion_live"]:
                state = await db.scalar(select(A11PromptModuleState).where(A11PromptModuleState.empresa_id == tenant_id, A11PromptModuleState.module_key.in_(module_keys)).limit(1))
                if state and state.active_version_id:
                    selected_version_id = state.active_version_id
                    selected_module_key = state.module_key
                    experiment = None
                    assignment = None
                    mode = "promoted_version"
            if selected_version_id is None:
                return None
            module_key = selected_module_key or (experiment.module_key if experiment else next((key for key in module_keys if key in MODULES), None))
            version = await db.scalar(select(A11PromptVersion).where(
                A11PromptVersion.id == selected_version_id,
                A11PromptVersion.empresa_id == tenant_id,
                A11PromptVersion.module_key == module_key,
            ))
            if not version:
                await db.rollback()
                return None
            _, base_instruction = next(item for item in candidates if item[0] == module_key)
            marker = f"Módulo {module_key}@1.0.0: {base_instruction}"
            variant_instruction = f"Módulo {module_key}@a11:{version.version_number}: {version.content}"
            original_prompt = composition.get("prompt", "")
            variant_prompt = original_prompt.replace(marker, variant_instruction, 1)
            if variant_prompt == original_prompt:
                await db.rollback()
                return None
            attribution = {
                "mode": mode,
                "module_key": module_key,
                "experiment_id": str(experiment.id) if experiment else None,
                "assignment_id": str(assignment.id) if assignment else None,
                "variant": assignment.variant if assignment else "promoted",
                "version_id": str(version.id),
                "version_number": version.version_number,
                "content_hash": version.content_hash,
            }
            await db.commit()
            # Do not expose the candidate prompt before the tenant-scoped
            # assignment/version transaction commits. On any DB failure the
            # legacy A07 composition is therefore left intact.
            composition["prompt"] = variant_prompt
            composition["a11_optimizer"] = attribution
            return attribution
    except Exception:
        return None


async def complete_experiment(state, composition: dict, *, guard_allowed, generation_error=None, latency_ms=None, input_tokens=None, output_tokens=None):
    attribution = (composition or {}).get("a11_optimizer")
    if not attribution or not attribution.get("assignment_id"):
        return
    try:
        tenant_id = uuid.UUID(str(state.empresa_id))
        assignment_id = uuid.UUID(attribution["assignment_id"])
        from db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            assignment = await db.scalar(select(A11PromptAssignment).where(
                A11PromptAssignment.id == assignment_id,
                A11PromptAssignment.empresa_id == tenant_id,
            ))
            experiment = await db.scalar(select(A11PromptExperiment).where(
                A11PromptExperiment.id == assignment.experiment_id,
                A11PromptExperiment.empresa_id == tenant_id,
            )) if assignment else None
            await optimizer.record_assignment_result(
                db, tenant_id, assignment_id,
                guard_allowed=bool(guard_allowed), generation_error=generation_error,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            if experiment:
                await optimizer.check_automatic_rollback(db, tenant_id, experiment)
            await db.commit()
    except Exception:
        # A11 instrumentation must never change the existing response path.
        return
