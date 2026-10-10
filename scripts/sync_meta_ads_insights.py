"""Tenant-scoped Graph GET and daily Insights snapshot; dry-run unless --apply is explicit."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.services.meta.ads_insights import sync_ads_insights
from db.database import AsyncSessionLocal
from db.models import TrafficMetaConfig


async def _run(*, apply: bool, tenant_id: str | None, start_date: date, end_date: date) -> dict[str, object]:
    async with AsyncSessionLocal() as discovery:
        statement = select(TrafficMetaConfig.empresa_id).where(TrafficMetaConfig.ad_account_id.is_not(None))
        if tenant_id:
            statement = statement.where(TrafficMetaConfig.empresa_id == tenant_id)
        tenant_ids = list((await discovery.execute(statement)).scalars().all())
    totals: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for current_tenant in tenant_ids:
        async with AsyncSessionLocal() as session:
            try:
                result = await sync_ads_insights(
                    session, empresa_id=current_tenant, start_date=start_date,
                    end_date=end_date, dry_run=not apply,
                )
                if apply and result.get("status") == "complete":
                    await session.commit()
                else:
                    await session.rollback()
                statuses[str(result.get("status") or "unknown")] += 1
                reason = str(result.get("reason") or "")
                if reason:
                    errors[reason[:80]] += 1
                totals["rows_fetched"] += int(result.get("rows_fetched") or 0)
                totals["rows_upserted"] += int(result.get("rows_upserted") or 0)
            except Exception as exc:
                await session.rollback()
                errors[type(exc).__name__] += 1
                statuses["error"] += 1
    return {
        "mode": "apply" if apply else "dry_run",
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "tenant_count": len(tenant_ids), "statuses": dict(statuses),
        "rows_fetched": totals["rows_fetched"], "rows_upserted": totals["rows_upserted"],
        "blocked_or_error_reasons": dict(errors), "graph_access": "tenant_credentials_only",
        "meta_write_invoked": False,
    }


def main() -> None:
    end = date.today() - timedelta(days=1)
    default_start = end - timedelta(days=29)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform tenant-scoped Graph GET and persist snapshots")
    parser.add_argument("--tenant-id", help="optional tenant UUID scope")
    parser.add_argument("--start-date", type=date.fromisoformat, default=default_start)
    parser.add_argument("--end-date", type=date.fromisoformat, default=end)
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(
            apply=args.apply, tenant_id=args.tenant_id,
            start_date=args.start_date, end_date=args.end_date,
        ))
    except Exception as exc:
        print(json.dumps({"status": "error", "exception_type": type(exc).__name__}, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
