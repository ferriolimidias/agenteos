"""Verifica a migration formal LeoFT sem apagar dados nem chamar integrações reais."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import text

from app.core.security import is_bcrypt_hash
from db.database import engine


EXPECTED_COLUMNS = {
    "leoft_training_plans": {
        "id": "uuid",
        "empresa_id": "uuid",
        "lead_id": "uuid",
        "conversation_id": "text",
        "token_reference": "text",
        "payment_reference": "text",
        "payment_status": "text",
        "payment_mode": "text",
        "payment_provider": "text",
        "product_name": "text",
        "status": "text",
        "student_name": "text",
        "student_email": "text",
        "student_whatsapp": "text",
        "plan_json": "jsonb",
        "hermes_payload": "jsonb",
        "hermes_mode": "text",
        "telegram_status": "text",
        "telegram_payload": "jsonb",
        "telegram_sent_at": "timestamp",
        "approved_at": "timestamp",
        "approved_by": "text",
        "approval_notes": "text",
        "rejected_at": "timestamp",
        "rejected_by": "text",
        "rejection_reason": "text",
        "revision_requested_at": "timestamp",
        "revision_requested_by": "text",
        "revision_notes": "text",
        "created_at": "timestamp",
        "updated_at": "timestamp",
    },
    "leoft_student_accesses": {
        "id": "uuid",
        "empresa_id": "uuid",
        "lead_id": "uuid",
        "plan_id": "uuid",
        "student_email": "text",
        "student_whatsapp": "text",
        "login_identifier": "text",
        "password_hash": "text",
        "temporary_password_masked": "text",
        "access_token_hash": "text",
        "access_token_masked": "text",
        "app_url": "text",
        "status": "text",
        "created_at": "timestamp",
        "updated_at": "timestamp",
        "first_login_at": "timestamp",
        "revoked_at": "timestamp",
    },
}

EXPECTED_INDEXES = {
    "leoft_training_plans": {
        "ix_leoft_training_plans_empresa_id",
        "ix_leoft_training_plans_lead_id",
        "ix_leoft_training_plans_status",
        "ix_leoft_training_plans_payment_reference",
        "ix_leoft_training_plans_created_at",
    },
    "leoft_student_accesses": {
        "ix_leoft_student_accesses_empresa_id",
        "ix_leoft_student_accesses_lead_id",
        "ix_leoft_student_accesses_plan_id",
        "ix_leoft_student_accesses_login_identifier",
        "ix_leoft_student_accesses_access_token_hash",
    },
}

EXPECTED_CHECKS = {
    "ck_leoft_training_plans_status",
    "ck_leoft_training_plans_hermes_mode",
    "ck_leoft_training_plans_telegram_status",
    "ck_leoft_student_accesses_status",
}


async def main() -> dict[str, Any]:
    async with engine.connect() as conn:
        result: dict[str, Any] = {
            "resultado": "PASSOU",
            "tables": {},
            "unique_empresa_payment_reference": "unknown",
            "unique_active_access_plan_id": "unknown",
            "checks": {},
            "counts": {},
            "approved_plan_available": False,
            "pending_plan_available": False,
            "safe_access_storage": False,
        }

        for table, expected in EXPECTED_COLUMNS.items():
            exists = await conn.scalar(text("select to_regclass(:name)"), {"name": f"public.{table}"})
            if not exists:
                raise RuntimeError(f"Tabela ausente: {table}")
            columns = await _columns(conn, table)
            missing = [name for name in expected if name not in columns]
            if missing:
                raise RuntimeError(f"{table}: colunas ausentes: {missing}")
            for name, expected_type in expected.items():
                actual = columns[name]
                if expected_type == "timestamp":
                    if actual != "timestamp":
                        raise RuntimeError(f"{table}.{name}: tipo esperado timestamp, encontrado {actual}")
                elif actual != expected_type:
                    raise RuntimeError(f"{table}.{name}: tipo esperado {expected_type}, encontrado {actual}")

            indexes = await _indexes(conn, table)
            missing_indexes = sorted(EXPECTED_INDEXES[table] - indexes)
            if missing_indexes:
                raise RuntimeError(f"{table}: índices ausentes: {missing_indexes}")

            count = int(await conn.scalar(text(f"select count(*) from {table}")) or 0)
            result["counts"][table] = count
            result["tables"][table] = {"columns_ok": True, "indexes_ok": True, "rows": count}

        duplicate_payment = int(await conn.scalar(text("""
            select count(*) from (
              select empresa_id, payment_reference
              from leoft_training_plans
              where payment_reference is not null
              group by empresa_id, payment_reference
              having count(*) > 1
            ) d
        """)) or 0)
        payment_unique_exists = await _index_exists(conn, "ux_leoft_training_plans_empresa_payment_reference")
        result["unique_empresa_payment_reference"] = "created" if payment_unique_exists else "skipped_duplicate" if duplicate_payment else "missing"
        if duplicate_payment == 0 and not payment_unique_exists:
            raise RuntimeError("Índice único parcial empresa_id + payment_reference ausente sem duplicidade que justifique pular.")

        duplicate_active = int(await conn.scalar(text("""
            select count(*) from (
              select plan_id
              from leoft_student_accesses
              where status = 'active'
              group by plan_id
              having count(*) > 1
            ) d
        """)) or 0)
        active_unique_exists = await _index_exists(conn, "ux_leoft_student_accesses_active_plan_id")
        result["unique_active_access_plan_id"] = "created" if active_unique_exists else "skipped_duplicate" if duplicate_active else "missing"
        if duplicate_active == 0 and not active_unique_exists:
            raise RuntimeError("Índice único parcial plan_id ativo ausente sem duplicidade que justifique pular.")

        checks = await _checks(conn)
        for check in EXPECTED_CHECKS:
            result["checks"][check] = check in checks

        approved_result = await conn.execute(text("""
            select a.password_hash, a.temporary_password_masked, a.access_token_hash, a.access_token_masked
            from leoft_training_plans p
            join leoft_student_accesses a on a.plan_id = p.id
            where p.status = 'approved' and a.status = 'active'
            order by p.updated_at desc
            limit 1
        """))
        approved = approved_result.mappings().first()
        if not approved:
            raise RuntimeError("Nenhum plano approved com acesso ativo encontrado.")
        result["approved_plan_available"] = True
        if not is_bcrypt_hash(approved["password_hash"]):
            raise RuntimeError("password_hash do acesso aprovado não é bcrypt.")
        serialized_access = json.dumps(dict(approved), ensure_ascii=False, default=str)
        if "leoft_app_" in serialized_access or "LeoFT-" in serialized_access:
            raise RuntimeError("Access token ou senha crua aparenta estar persistido.")
        if not str(approved["access_token_hash"] or "").strip() or len(str(approved["access_token_hash"])) != 64:
            raise RuntimeError("access_token_hash inválido.")
        result["safe_access_storage"] = True

        pending_exists = await conn.scalar(text("select exists(select 1 from leoft_training_plans where status='pending_approval')"))
        result["pending_plan_available"] = bool(pending_exists)

    print("PASSOU")
    print(json.dumps(result, ensure_ascii=False, default=str))
    return result


async def _columns(conn, table: str) -> dict[str, str]:
    rows = await conn.execute(
        text("""
            select column_name, udt_name
            from information_schema.columns
            where table_schema='public' and table_name=:table
        """),
        {"table": table},
    )
    return {row.column_name: row.udt_name for row in rows}


async def _indexes(conn, table: str) -> set[str]:
    rows = await conn.execute(
        text("select indexname from pg_indexes where schemaname='public' and tablename=:table"),
        {"table": table},
    )
    return {row.indexname for row in rows}


async def _index_exists(conn, index_name: str) -> bool:
    return bool(await conn.scalar(text("select to_regclass(:name)"), {"name": f"public.{index_name}"}))


async def _checks(conn) -> set[str]:
    rows = await conn.execute(
        text("""
            select conname
            from pg_constraint
            where contype='c'
              and conrelid in ('leoft_training_plans'::regclass, 'leoft_student_accesses'::regclass)
        """)
    )
    return {row.conname for row in rows}


if __name__ == "__main__":
    asyncio.run(main())
