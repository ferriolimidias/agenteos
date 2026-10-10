"""Dry-run (default) or persist tenant-scoped canonical Meta shadow projections."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.services.meta.canonical_conversion_shadow import run_shadow_projection
from db.database import AsyncSessionLocal
from db.models import CanonicalCommercialEvent


async def _run(*, persist: bool, tenant_id: str | None) -> dict[str, object]:
    async with AsyncSessionLocal() as discovery:
        query = select(CanonicalCommercialEvent.empresa_id).distinct()
        if tenant_id:
            from uuid import UUID
            query = query.where(CanonicalCommercialEvent.empresa_id == UUID(tenant_id))
        tenants = list((await discovery.execute(query)).scalars().all())
    results = []
    for current_tenant in tenants:
        async with AsyncSessionLocal() as session:
            try:
                result = await run_shadow_projection(session, empresa_id=current_tenant, persist=persist)
                if persist:
                    await session.commit()
                results.append(result)
            except Exception:
                await session.rollback()
                raise
    totals: dict[str, int] = {}
    for result in results:
        for group in ("projection_status_counts", "comparison_counts", "legacy_reference_counts", "delivery_state_counts", "eligibility_counts", "reason_bucket_counts"):
            for key, value in (result.get(group) or {}).items():
                totals[f"{group}.{key}"] = totals.get(f"{group}.{key}", 0) + int(value)
    return {
        "mode": "persist_shadow" if persist else "dry_run",
        "tenant_count": len(results),
        "commercial_events_total": sum(int(item["tenant_event_count"]) for item in results),
        "legacy_meta_events_total": sum(int(item["legacy_meta_event_count"]) for item in results),
        "counts": totals,
        "tenants": results,
        "meta_graph_write_executed": False,
        "canonical_live_dispatch_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist projections; never sends Meta events")
    parser.add_argument("--tenant-id", help="optional tenant UUID scope")
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(persist=args.apply, tenant_id=args.tenant_id))
    except Exception as exc:
        print(json.dumps({"status": "error", "exception_type": type(exc).__name__}, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
