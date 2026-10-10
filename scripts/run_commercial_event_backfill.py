"""Dry-run by default; idempotently backfill internal commercial facts when --apply is explicit."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.services.commercial_event_backfill import commercial_event_backfill
from db.database import AsyncSessionLocal


async def _run(*, apply: bool, tenant_id: str | None) -> dict[str, object]:
    async with AsyncSessionLocal() as discovery:
        query = """
            SELECT empresa_id FROM crm_tag_events
            UNION SELECT empresa_id FROM attribution_touches
            UNION SELECT empresa_id FROM meta_conversion_events
        """
        params = {}
        if tenant_id:
            query = "SELECT CAST(:tenant_id AS uuid)"
            params["tenant_id"] = tenant_id
        tenant_ids = list((await discovery.execute(text(query), params)).scalars().all())
    totals: Counter[str] = Counter()
    classifications: Counter[str] = Counter()
    by_event_name_status: Counter[str] = Counter()
    for current_tenant in tenant_ids:
        async with AsyncSessionLocal() as session:
            try:
                result = await commercial_event_backfill(session, empresa_id=current_tenant, dry_run=not apply)
                if apply:
                    await session.commit()
                totals["tenants"] += 1
                totals["would_insert"] += int(result.get("would_insert", 0))
                totals["inserted"] += int(result.get("inserted", 0))
                totals["skipped_duplicate"] += int(result.get("skipped_duplicate", 0))
                totals["unresolved"] += int(result.get("unresolved", 0))
                totals["ambiguous"] += int(result.get("ambiguous", 0))
                reconciliation = result.get("reconciliation") or {}
                for name, count in (reconciliation.get("classifications") or {}).items():
                    classifications[str(name)] += int(count)
                for name, count in (reconciliation.get("by_event_name_status") or {}).items():
                    by_event_name_status[str(name)] += int((count or {}).get("count", 0))
            except Exception:
                await session.rollback()
                raise
    return {
        "mode": "apply" if apply else "dry_run",
        "tenant_count": totals["tenants"],
        "would_insert": totals["would_insert"],
        "inserted": totals["inserted"],
        "skipped_duplicate": totals["skipped_duplicate"],
        "unresolved": totals["unresolved"],
        "ambiguous": totals["ambiguous"],
        "legacy_classifications": dict(classifications),
        "legacy_event_name_status_counts": dict(by_event_name_status),
        "meta_sender_invoked": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist idempotent backfill; default only reports a dry-run")
    parser.add_argument("--tenant-id", help="optional tenant UUID scope")
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(apply=args.apply, tenant_id=args.tenant_id))
    except Exception as exc:
        print(json.dumps({"status": "error", "exception_type": type(exc).__name__}, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
