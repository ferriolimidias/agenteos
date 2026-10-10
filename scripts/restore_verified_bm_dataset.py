"""Read-only by default; optionally restore a WABA Dataset proven by Graph GET.

This tool never creates a Dataset and has no Graph write transport. `--apply`
persists only the existing Dataset listed under the tenant's own WABA after
token, scope, business-owner and phone-number checks pass.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.meta.waba_dataset import verify_existing_business_messaging_dataset
from db.database import AsyncSessionLocal


async def _run(tenant_id: str, *, persist: bool) -> dict[str, object]:
    async with AsyncSessionLocal() as session:
        result = await verify_existing_business_messaging_dataset(
            session,
            tenant_id,
            persist=persist,
        )
    return {
        "mode": "persist_verified_local_binding" if persist else "dry_run",
        "result": result,
        "meta_graph_write_executed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="UUID of the tenant to verify; no tenant fallback is used")
    parser.add_argument("--apply", action="store_true", help="persist the Graph-verified local binding")
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args.tenant_id, persist=args.apply))
    except Exception as exc:
        print(json.dumps({"status": "error", "exception_type": type(exc).__name__, "meta_graph_write_executed": False}, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
