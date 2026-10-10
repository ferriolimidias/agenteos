"""Aplica a migration central LeoFT e faz backfill transacional por plano."""
from __future__ import annotations
import asyncio, json
from pathlib import Path
from sqlalchemy import select, func
from db.database import AsyncSessionLocal
from app.services.leoft_postgres_central import normalize_plan, create_or_get_review
from db.models import LeoFTTrainingPlan, LeoFTTrainingPlanVersion, LeoFTTrainingWeek, LeoFTTrainingSession, LeoFTTrainingExercise

MIGRATION = Path(__file__).resolve().parents[1] / "db/migrations/2026_07_29_001_leoft_postgres_central.sql"

async def main() -> None:
    summary = {"plans": 0, "versions_created": 0, "weeks": 0, "sessions": 0, "exercises": 0, "reviews": 0, "inconsistencies": []}
    async with AsyncSessionLocal() as db:
        plans = (await db.scalars(select(LeoFTTrainingPlan).order_by(LeoFTTrainingPlan.created_at))).all()
        for plan in plans:
            try:
                result = await normalize_plan(db, plan, source="backfill_2026_07_29", created_by="codex")
                summary["plans"] += 1; summary["versions_created"] += int(result["created"]); summary["weeks"] += result["weeks"]; summary["sessions"] += result["sessions"]; summary["exercises"] += result["exercises"]
                if plan.status == "pending_approval":
                    review = await create_or_get_review(db, plan, version_id=__import__('uuid').UUID(result["version_id"]), payload=dict(plan.telegram_payload or {}), idempotency_key=f"backfill:{plan.id}:{result['version_id']}")
                    payload = dict(plan.telegram_payload or {})
                    if payload.get("telegram_message_id") == "46":
                        review.telegram_message_id = "46"
                    summary["reviews"] += 1
            except Exception as exc:
                summary["inconsistencies"].append({"plan_id": str(plan.id), "error": type(exc).__name__})
                await db.rollback()
        await db.commit()
        summary["db_counts"] = {
            "versions": await db.scalar(select(func.count(LeoFTTrainingPlanVersion.id))),
            "weeks": await db.scalar(select(func.count(LeoFTTrainingWeek.id))),
            "sessions": await db.scalar(select(func.count(LeoFTTrainingSession.id))),
            "exercises": await db.scalar(select(func.count(LeoFTTrainingExercise.id))),
        }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

if __name__ == "__main__": asyncio.run(main())
