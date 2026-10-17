from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from db.database import engine


REQUIRED_COLUMNS: dict[str, set[str]] = {
    "legacy_conversion_delivery_evidence": {"empresa_id", "commercial_event_id", "legacy_event_id", "external_event_id_hash", "dataset_id_hash", "waba_id_hash", "request_outcome", "delivery_uncertainty", "request_correlation_id"},
    "conversion_delivery_identities": {"empresa_id", "commercial_event_id", "identity_key", "external_event_id_hash", "owner", "ownership_mode", "delivery_state"},
    "conexoes": {
        "channel",
        "provider",
        "external_account_id",
        "waba_id",
        "phone_number_id",
        "instagram_business_account_id",
        "page_id", "credential_source",
        "display_number",
        "metadata_json",
    },
    "channel_contacts": {"empresa_id", "conexao_id", "channel", "provider", "external_contact_id"},
    "channel_conversations": {"empresa_id", "conexao_id", "channel", "provider", "conversation_key"},
    "instagram_comment_automations": {"empresa_id", "name", "active", "public_reply_mode", "fallback_reply"},
    "instagram_comment_executions": {"empresa_id", "automation_id", "instagram_account_id", "comment_id", "status"},
    "traffic_meta_configs": {"empresa_id", "business_id", "ad_account_id"},
    "meta_marketing_tenant_account_grants": {
        "empresa_id", "business_id", "ad_account_id", "credential_source", "read_enabled",
        "write_enabled", "verification_method", "verified_at",
    },
    "meta_system_user_credentials": {
        "id", "encrypted_access_token", "token_hint", "system_user_id", "system_user_name",
        "app_id", "app_name", "granted_permissions", "permissions_status",
        "permissions_error_code", "validated_at", "expires_at", "expiry_status",
        "updated_at", "updated_by_user_id",
    },
    "meta_platform_assets": {
        "id", "asset_type", "external_id", "business_id", "business_access_json", "access_state",
        "permissions_json", "metadata_json", "last_verified_at",
    },
    "meta_tenant_asset_links": {
        "id", "empresa_id", "asset_id", "asset_type", "read_enabled", "write_enabled",
        "verified_at", "linked_by_user_id",
    },
    "meta_system_user_audit": {"id", "actor_user_id", "empresa_id", "action", "details_json", "created_at"},
    "commercial_meta_event_policies": {"empresa_id", "event_name", "mode"},
    "commercial_meta_conversion_projections": {
        "empresa_id", "commercial_event_id", "canonical_event_id", "event_name", "mode",
        "projection_status", "comparison_classification", "legacy_reference_classification",
        "delivery_state", "comparison_json", "safe_summary_json", "last_projected_at",
    },
    "canonical_conversion_outbox": {
        "empresa_id", "commercial_event_id", "projection_id", "event_id", "idempotency_key",
        "payload_snapshot", "status", "dispatch_mode", "historical_no_send", "lease_owner",
        "lease_expires_at", "attempt_count", "provider_response_metadata",
    },
    "canonical_conversion_delivery_attempts": {
        "empresa_id", "outbox_id", "attempt_number", "attempt_kind", "started_at", "status",
        "provider_ack_status", "request_correlation_id", "response_metadata",
    },
}

REQUIRED_CONSTRAINTS = {
    "fk_legacy_delivery_evidence_fact", "fk_legacy_delivery_evidence_legacy",
    "uq_legacy_delivery_evidence_request_event", "ck_legacy_delivery_evidence_outcome",
    "ck_legacy_delivery_evidence_exact", "fk_conversion_shared_identity_fact",
    "uq_conversion_shared_identity", "ck_conversion_shared_identity_shadow",
    "uq_conexoes_empresa_id_id",
    "uq_channel_contacts_empresa_id_id",
    "uq_channel_conversations_empresa_id_id",
    "uq_ig_comment_execution_idempotency",
    "uq_traffic_meta_configs_empresa_ad_account",
    "uq_meta_marketing_grant_empresa_account",
    "fk_meta_marketing_grant_tenant_account",
    "ck_meta_marketing_grant_credential_source",
    "ck_meta_marketing_grant_verification_method",
    "ck_meta_system_user_credential_singleton",
    "uq_meta_platform_asset_type_external_id",
    "uq_meta_platform_asset_id_type",
    "ck_meta_platform_asset_type",
    "uq_meta_tenant_asset_link_company_asset",
    "fk_meta_tenant_asset_link_asset_type",
    "ck_meta_system_user_audit_action",
    "uq_commercial_meta_policy_tenant_event",
    "ck_commercial_meta_policy_mode",
    "uq_commercial_meta_projection_event_version",
    "uq_commercial_meta_projection_canonical_id",
    "fk_commercial_meta_projection_fact",
    "fk_commercial_meta_projection_exact_legacy",
    "fk_commercial_meta_projection_mapping",
    "ck_commercial_meta_projection_mode",
    "ck_commercial_meta_projection_status",
    "ck_commercial_meta_projection_comparison",
    "ck_commercial_meta_projection_legacy_class",
    "ck_commercial_meta_projection_delivery_state",
    "ck_commercial_meta_projection_json_objects",
    "ck_commercial_meta_projection_canonical_id_length",
    "uq_commercial_meta_projection_empresa_id_id",
    "uq_canonical_conversion_outbox_empresa_id",
    "uq_canonical_conversion_outbox_idempotency",
    "uq_canonical_conversion_outbox_event",
    "fk_conversion_outbox_fact",
    "fk_conversion_outbox_projection",
    "ck_conversion_outbox_status",
    "ck_conversion_outbox_provider",
    "ck_conversion_outbox_destination",
    "ck_conversion_outbox_mode",
    "ck_conversion_outbox_live_stays_blocked",
    "ck_conversion_outbox_history_no_send",
    "ck_conversion_outbox_attempt_count",
    "ck_conversion_outbox_payload_object",
    "uq_conversion_attempt_number",
    "fk_conversion_attempt_outbox",
    "ck_conversion_attempt_number",
    "ck_conversion_attempt_kind",
    "ck_conversion_attempt_status",
    "ck_conversion_attempt_ack",
    "ck_conversion_shadow_never_acked",
    "ck_conversion_attempt_response_object",
    "trg_conversion_outbox_history_no_send",
}

REQUIRED_INDEXES = {"uq_meta_tenant_asset_link_exclusive_asset"}


class SchemaCompatibilityError(RuntimeError):
    pass


def find_missing_schema_items(existing_columns: dict[str, set[str]], constraints: set[str]) -> list[str]:
    missing: list[str] = []
    for table, required in REQUIRED_COLUMNS.items():
        existing = existing_columns.get(table, set())
        if not existing:
            missing.append(f"table:{table}")
            continue
        for column in sorted(required - existing):
            missing.append(f"column:{table}.{column}")

    for constraint in sorted(REQUIRED_CONSTRAINTS - constraints):
        missing.append(f"constraint:{constraint}")
    return missing


async def _existing_columns(conn, table: str) -> set[str]:
    rows = await conn.execute(
        text(
            """
            select column_name
            from information_schema.columns
            where table_schema = 'public' and table_name = :table
            """
        ),
        {"table": table},
    )
    return {str(row[0]) for row in rows}


async def _existing_constraints(conn) -> set[str]:
    rows = await conn.execute(
        text(
            """
            select conname AS object_name
            from pg_constraint
            where connamespace = 'public'::regnamespace
            UNION
            select tgname AS object_name
            from pg_trigger
            where tgrelid = to_regclass('public.canonical_conversion_outbox')
              and NOT tgisinternal
            """
        )
    )
    return {str(row[0]) for row in rows}


async def _existing_indexes(conn) -> set[str]:
    rows = await conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
    ))
    return {str(row[0]) for row in rows}


async def check_multichannel_schema(engine_: AsyncEngine = engine) -> dict[str, list[str]]:
    existing_columns: dict[str, set[str]] = {}
    async with engine_.connect() as conn:
        for table in REQUIRED_COLUMNS:
            existing_columns[table] = await _existing_columns(conn, table)
        constraints = await _existing_constraints(conn)
        indexes = await _existing_indexes(conn)

    missing = find_missing_schema_items(existing_columns, constraints)
    missing.extend(f"index:{name}" for name in sorted(REQUIRED_INDEXES - indexes))
    if missing:
        raise SchemaCompatibilityError(
            "Schema multicanal incompatível. Execute migrations na ordem: "
            "2026_07_17_phase2_multichannel_persistence.sql, Fase A, Fase B e 2026_07_23_instagram_comment_direct_mvp.sql antes de ativar esta imagem. "
            f"Itens pendentes: {', '.join(missing)}"
        )
    return {"missing": []}


async def main() -> int:
    try:
        await check_multichannel_schema(engine)
    except SchemaCompatibilityError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("SCHEMA_COMPATIBILITY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
