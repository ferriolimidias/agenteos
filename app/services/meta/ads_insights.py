"""Tenant-scoped, read-only Meta Ads Insights collection and idempotent storage."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.meta.conversion_credentials import MetaCredentialResolutionError
from app.services.meta.marketing_api import MetaMarketingApiClient, normalize_ad_account_id
from app.services.meta.marketing_credentials import MetaMarketingCapability, MetaMarketingCredentialResolver
from db.models import MetaAdsInsightDaily, TrafficMetaConfig

logger = logging.getLogger(__name__)


def _decimal(row: Mapping[str, Any], key: str) -> Decimal | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _integer(row: Mapping[str, Any], key: str) -> int | None:
    value = _decimal(row, key)
    return int(value) if value is not None and value >= 0 and value == value.to_integral_value() else None


def _safe_array(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {str(key)[:64]: item for key, item in entry.items() if isinstance(item, (str, int, float, bool)) or item is None}
        for entry in value[:500] if isinstance(entry, dict)
    ]


def _metric_values(row: Mapping[str, Any], key: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for entry in _safe_array(row.get(key)):
        action_type = str(entry.get("action_type") or "")[:120]
        raw = _decimal(entry, "value")
        if action_type and raw is not None:
            values[action_type] = str(raw)
    return values


def _messaging_conversations(actions: list[dict[str, Any]]) -> Decimal | None:
    matches = []
    for action in actions:
        action_type = str(action.get("action_type") or "").lower()
        if "messaging_conversation_started" in action_type or "onsite_conversion.messaging_conversation_started" in action_type:
            value = _decimal(action, "value")
            if value is not None:
                matches.append(value)
    return sum(matches, Decimal(0)) if matches else None


async def sync_ads_insights(
    session: AsyncSession,
    *,
    empresa_id: Any,
    start_date: date,
    end_date: date,
    dry_run: bool = True,
) -> dict[str, Any]:
    if end_date < start_date or (end_date - start_date).days > 92:
        raise ValueError("META_INSIGHTS_DATE_RANGE_INVALID")
    tenant_id = uuid.UUID(str(empresa_id))
    config = await session.scalar(select(TrafficMetaConfig).where(TrafficMetaConfig.empresa_id == tenant_id))
    if config is None or not config.ad_account_id:
        return {"status": "unknown", "reason": "tenant_ad_account_unconfigured", "rows_fetched": None, "rows_upserted": 0}
    try:
        credential = await MetaMarketingCredentialResolver().resolve(
            session, tenant_id, MetaMarketingCapability.ADS_READ,
            ad_account_id=str(config.ad_account_id), allow_admin_fallback=True,
        )
    except MetaCredentialResolutionError as exc:
        reason = str(exc)[:80]
        logger.info("event=META_INSIGHTS_SYNC_BLOCKED tenant_scope=tenant reason=%s", reason)
        return {"status": "unknown", "reason": reason, "rows_fetched": None, "rows_upserted": 0,
                "ad_account_id": normalize_ad_account_id(config.ad_account_id)}
    if dry_run:
        return {"status": "estimated", "reason": "tenant_ads_read_available", "rows_fetched": None,
                "rows_upserted": 0, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                "ad_account_id": normalize_ad_account_id(config.ad_account_id)}

    client = MetaMarketingApiClient(
        credential.access_token, graph_version=credential.graph_version,
        timeout_seconds=12.0, max_attempts=2,
        read_only=bool(getattr(credential, "read_only", True)),
    )
    try:
        account = await client.validate_ad_account(str(config.ad_account_id))
        if normalize_ad_account_id(account.get("account_id") or account.get("id")) != normalize_ad_account_id(config.ad_account_id):
            return {"status": "error", "reason": "tenant_ad_account_mismatch", "rows_fetched": None, "rows_upserted": 0}
        expected_business = str(config.business_id or "").strip()
        account_business = account.get("business") if isinstance(account.get("business"), dict) else {}
        actual_business = str(account_business.get("id") or "").strip()
        verification_method = "business_owner" if expected_business and actual_business == expected_business else None
        if expected_business and not verification_method:
            shared_accounts = await client.list_business_client_ad_accounts(expected_business)
            expected_account = normalize_ad_account_id(config.ad_account_id)
            if any(
                isinstance(item, dict)
                and normalize_ad_account_id(item.get("account_id") or item.get("id")) == expected_account
                for item in shared_accounts
            ):
                verification_method = "delegated_client_ad_account"
        if not verification_method:
            return {"status": "blocked", "reason": "business_account_binding_unverified", "rows_fetched": None, "rows_upserted": 0}
        account_currency = str(account.get("currency") or "").upper()[:3] or None
    except Exception as exc:
        logger.warning("event=META_INSIGHTS_SYNC_FAILED tenant_scope=tenant exception_type=%s", type(exc).__name__)
        return {"status": "error", "reason": type(exc).__name__, "rows_fetched": None, "rows_upserted": 0}

    page_counter = [0]
    rows_by_level: dict[str, list[dict[str, Any]]] = {}
    try:
        for level in ("campaign", "adset", "ad"):
            rows_by_level[level] = await client.get_insights(
                str(config.ad_account_id), level=level, since=start_date.isoformat(),
                until=end_date.isoformat(), time_increment=1, account_scope=True,
                page_counter=page_counter,
            )
    except Exception as exc:
        logger.warning("event=META_INSIGHTS_SYNC_FAILED tenant_scope=tenant exception_type=%s", type(exc).__name__)
        return {"status": "error", "reason": type(exc).__name__, "rows_fetched": None, "rows_upserted": 0,
                "graph_pages_read": page_counter[0]}

    api_version = str(credential.graph_version or "v20.0")[:24]
    account_id = normalize_ad_account_id(config.ad_account_id)
    upserted = 0
    rows_fetched = 0
    rows_fetched_by_level = {}
    id_fields = {"campaign": "campaign_id", "adset": "adset_id", "ad": "ad_id"}
    for level, rows in rows_by_level.items():
        rows_fetched_by_level[level] = len(rows)
        rows_fetched += len(rows)
        for row in rows:
            try:
                row_start = date.fromisoformat(str(row.get("date_start") or ""))
                row_stop = date.fromisoformat(str(row.get("date_stop") or ""))
            except ValueError:
                continue
            entity_id = str(row.get(id_fields[level]) or "").strip()
            if not entity_id:
                continue
            actions = _safe_array(row.get("actions"))
            action_values = _safe_array(row.get("action_values"))
            raw = {
                key: value for key, value in row.items()
                if key not in {"actions", "action_values"} and isinstance(value, (str, int, float, bool))
            }
            values = {
                "id": uuid.uuid4(), "empresa_id": tenant_id, "ad_account_id": account_id,
                "account_currency": str(row.get("account_currency") or account_currency or "").upper()[:3] or None,
                "entity_level": level, "entity_id": entity_id,
                "account_name": str(row.get("account_name") or "")[:240] or None,
                "campaign_id": str(row.get("campaign_id") or "")[:100] or None,
                "campaign_name": str(row.get("campaign_name") or "")[:240] or None,
                "adset_id": str(row.get("adset_id") or "")[:100] or None,
                "adset_name": str(row.get("adset_name") or "")[:240] or None,
                "ad_id": str(row.get("ad_id") or "")[:100] or None,
                "ad_name": str(row.get("ad_name") or "")[:240] or None,
                "date_start": row_start, "date_stop": row_stop,
                "attribution_setting": str(row.get("attribution_setting") or "unspecified")[:120],
                "spend": _decimal(row, "spend"), "impressions": _integer(row, "impressions"),
                "reach": _integer(row, "reach"), "frequency": _decimal(row, "frequency"),
                "clicks": _integer(row, "clicks"), "ctr": _decimal(row, "ctr"),
                "cpc": _decimal(row, "cpc"), "cpm": _decimal(row, "cpm"),
                "messaging_conversations": _messaging_conversations(actions),
                "actions_json": actions, "action_values_json": action_values,
                "raw_metrics_json": raw, "graph_api_version": api_version,
                "fetched_at": datetime.now(timezone.utc),
            }
            statement = pg_insert(MetaAdsInsightDaily).values(**values).on_conflict_do_update(
                constraint="uq_meta_ads_insights_daily_key",
                set_={key: values[key] for key in values if key not in {"id", "empresa_id", "ad_account_id", "entity_level", "entity_id", "date_start", "date_stop", "attribution_setting"}},
            )
            await session.execute(statement)
            upserted += 1
    await session.flush()
    return {"status": "complete", "reason": None, "rows_fetched": rows_fetched,
            "rows_fetched_by_level": rows_fetched_by_level, "rows_upserted": upserted,
            "graph_pages_read": page_counter[0], "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(), "ad_account_id": account_id}


async def estimate_default_insights_sync(session: AsyncSession, *, empresa_id: Any, today: date | None = None) -> dict[str, Any]:
    end = (today or date.today()) - timedelta(days=1)
    start = end - timedelta(days=29)
    result = await sync_ads_insights(session, empresa_id=empresa_id, start_date=start, end_date=end, dry_run=True)
    result["window_days"] = 30
    return result
