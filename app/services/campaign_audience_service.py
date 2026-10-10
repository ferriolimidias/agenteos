from __future__ import annotations

import csv
import base64
import hashlib
import io
import json
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.secret_sanitizer import sanitize_for_log
from app.services.followup_engine import registrar_followup_tag_event
from db.models import CRMLead, ChannelContact, CommunicationConsent, Conexao, TagCRM


MAX_CSV_BYTES = 2 * 1024 * 1024
CSV_INJECTION_RE = re.compile(r"^[=+\-@\t\r]")
RESTRICTIVE_CONSENT_STATUSES = {"opt_out", "opted_out", "blocked", "revoked"}
AUDIENCE_SCHEMA_VERSION = 2
EXCLUSION_PRECEDENCE = "ALWAYS"


def sanitize_filename(name: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or "import.csv")).strip("._")
    return (cleaned or "import.csv")[:120]


def normalize_phone(value: str | None, *, default_country_code: str | None = None) -> tuple[str | None, bool]:
    raw = re.sub(r"\D+", "", str(value or ""))
    if not raw:
        return None, False
    if raw.startswith("00"):
        raw = raw[2:]
    if len(raw) < 8:
        return None, False
    if len(raw) <= 11 and default_country_code:
        raw = re.sub(r"\D+", "", default_country_code) + raw
    if len(raw) < 10 or len(raw) > 15:
        return None, False
    return f"+{raw}", True


def mask_phone(value: str | None) -> str:
    raw = re.sub(r"\D+", "", str(value or ""))
    if len(raw) <= 4:
        return "***"
    return f"+{raw[:2]}***{raw[-4:]}"


def normalize_match_mode(value: Any, default: str = "any") -> str:
    normalized = str(value or default or "any").strip().lower()
    return "all" if normalized == "all" else "any"


def _extract_tag_values(rules: Any) -> list[str]:
    values: list[str] = []
    if not isinstance(rules, list):
        return values
    for rule in rules:
        if isinstance(rule, dict):
            rule_type = str(rule.get("type") or "tag").strip().lower()
            value = rule.get("tag_id") or rule.get("value") or rule.get("id")
            if rule_type == "tag" and str(value or "").strip():
                values.append(str(value).strip())
        elif str(rule or "").strip():
            values.append(str(rule).strip())
    return values


def canonicalize_audience_rules(rules: dict[str, Any] | None) -> dict[str, Any]:
    raw = rules if isinstance(rules, dict) else {}
    include = raw.get("include") if isinstance(raw.get("include"), dict) else {}
    exclude = raw.get("exclude") if isinstance(raw.get("exclude"), dict) else {}

    include_tags = _extract_tag_values(include.get("rules"))
    exclude_tags = _extract_tag_values(exclude.get("rules"))
    if not include_tags:
        include_tags = [str(item).strip() for item in raw.get("include_tags", []) if str(item).strip()]
    if not exclude_tags:
        exclude_tags = [str(item).strip() for item in raw.get("exclude_tags", []) if str(item).strip()]

    include_mode = normalize_match_mode(
        include.get("match") or include.get("mode") or raw.get("include_match") or raw.get("tag_logic") or "any"
    )
    exclude_mode = normalize_match_mode(exclude.get("match") or exclude.get("mode") or raw.get("exclude_match") or "any")

    def tag_rules(values: list[str]) -> list[dict[str, str]]:
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for value in values:
            key = str(value).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append({"type": "tag", "tag_id": key})
        return deduped

    return {
        "schema_version": AUDIENCE_SCHEMA_VERSION,
        "include": {"match": include_mode, "rules": tag_rules(include_tags)},
        "exclude": {"match": exclude_mode, "rules": tag_rules(exclude_tags)},
    }


def _rule_values(canonical: dict[str, Any], section: str) -> set[str]:
    return {str(rule.get("tag_id") or "").strip().lower() for rule in canonical.get(section, {}).get("rules", []) if str(rule.get("tag_id") or "").strip()}


def _lead_tag_set(lead: CRMLead, tag_name_to_id: dict[str, str]) -> set[str]:
    lead_tags_raw = {str(tag).strip() for tag in (lead.tags or []) if str(tag).strip()}
    lead_tags = {item.lower() for item in lead_tags_raw}
    resolved = set(lead_tags)
    resolved.update(tag_name_to_id.get(item) for item in lead_tags if tag_name_to_id.get(item))
    return resolved


def _matches(values: set[str], required: set[str], mode: str, *, empty_matches: bool) -> bool:
    if not required:
        return empty_matches
    return required.issubset(values) if mode == "all" else bool(required.intersection(values))


def _member_payload(lead: CRMLead, *, reason: str, address: str | None = None, detail: str | None = None) -> dict[str, Any]:
    return {
        "lead_id": str(lead.id),
        "name": str(lead.nome_contato or "")[:120],
        "phone_masked": mask_phone(address or lead.telefone_contato),
        "reason": reason,
        "detail": detail,
    }


def _encode_member_cursor(index: int) -> str | None:
    if index <= 0:
        return None
    payload = json.dumps({"index": index}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_member_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = str(cursor) + ("=" * (-len(str(cursor)) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return max(0, int(payload.get("index") or 0))
    except Exception:
        return 0


def _paginate_members(items: list[dict[str, Any]], *, cursor: str | None, limit: int) -> dict[str, Any]:
    safe_limit = min(max(int(limit or 25), 1), 100)
    start = _decode_member_cursor(cursor)
    end = start + safe_limit
    page = items[start:end]
    return {
        "items": page,
        "next_cursor": _encode_member_cursor(end) if end < len(items) else None,
        "has_more": end < len(items),
        "limit": safe_limit,
        "total": len(items),
        "order": "lead_id",
    }


def safe_cell(value: Any) -> str:
    text_value = str(value or "").replace("\x00", "").strip()
    if CSV_INJECTION_RE.match(text_value):
        return "'" + text_value
    return text_value[:500]


def parse_csv_bytes(file_bytes: bytes) -> tuple[list[str], list[dict[str, str]], str]:
    if len(file_bytes) > MAX_CSV_BYTES:
        raise ValueError("Arquivo CSV excede o limite de 2 MB.")
    if file_bytes[:2] == b"MZ" or file_bytes[:4] in {b"\x7fELF", b"PK\x03\x04"}:
        raise ValueError("Arquivo executável ou compactado não é aceito como CSV.")
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            content = file_bytes.decode(encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Não foi possível detectar o encoding do CSV.")
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    headers = [safe_cell(item) for item in (reader.fieldnames or []) if safe_cell(item)]
    if not headers:
        raise ValueError("O CSV precisa ter cabeçalho.")
    rows = [{str(k or "").strip(): safe_cell(v) for k, v in (row or {}).items()} for row in reader]
    return headers, rows, used_encoding


def default_mapping(headers: list[str]) -> dict[str, str]:
    lowered = {header.lower(): header for header in headers}

    def pick(*names: str) -> str:
        for name in names:
            for key, original in lowered.items():
                if name in key:
                    return original
        return ""

    return {
        "name": pick("nome", "name"),
        "phone": pick("telefone", "phone", "celular", "whatsapp"),
        "email": pick("email", "e-mail"),
        "username": pick("username", "usuario", "instagram"),
        "company": pick("empresa", "company"),
        "city": pick("cidade", "city"),
        "product": pick("produto", "product"),
        "notes": pick("observacao", "observação", "notes"),
    }


def resolve_csv_mapping(headers: list[str], requested_mapping: Any = None) -> dict[str, str]:
    """Use an explicit complete mapping, or safely derive the default one.

    The UI may submit an empty mapping when the user skips the preview step.
    Treating that empty object as authoritative made every row lose its identity.
    """
    mapping = requested_mapping if isinstance(requested_mapping, dict) else {}
    if not mapping:
        mapping = default_mapping(headers)
    name_column = str(mapping.get("name") or "").strip()
    phone_column = str(mapping.get("phone") or "").strip()
    if not name_column or not phone_column or name_column not in headers or phone_column not in headers:
        raise ValueError("Não foi possível identificar as colunas nome e telefone no CSV.")
    return {**mapping, "name": name_column, "phone": phone_column}


async def list_tag_audiences(session: AsyncSession, *, empresa_id: uuid.UUID) -> list[dict[str, Any]]:
    tags = (
        await session.execute(select(TagCRM).where(TagCRM.empresa_id == empresa_id).order_by(TagCRM.nome.asc()))
    ).scalars().all()
    leads = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_id))).scalars().all()
    consent_rows = (
        await session.execute(select(CommunicationConsent.lead_id, CommunicationConsent.status).where(CommunicationConsent.empresa_id == empresa_id))
    ).all()
    consent_by_lead: dict[str, set[str]] = {}
    for lead_id, status in consent_rows:
        if lead_id:
            consent_by_lead.setdefault(str(lead_id), set()).add(str(status or "unknown"))
    last_events = (
        await session.execute(
            text(
                """
                select tag_id::text, max(occurred_at) as last_at
                from crm_tag_events
                where empresa_id = :empresa_id and action = 'applied'
                group by tag_id
                """
            ),
            {"empresa_id": empresa_id},
        )
    ).all()
    last_by_tag = {tag_id: last_at for tag_id, last_at in last_events}
    tag_by_name = {str(tag.nome or "").strip().lower(): str(tag.id) for tag in tags if str(tag.nome or "").strip()}
    output = []
    for tag in tags:
        tag_id = str(tag.id)
        members = []
        for lead in leads:
            values = {str(item).strip() for item in (lead.tags or []) if str(item).strip()}
            values_norm = {item.lower() for item in values}
            resolved = set(values)
            resolved.update(tag_by_name.get(item) for item in values_norm if tag_by_name.get(item))
            if tag_id in resolved:
                members.append(lead)
        known_in = 0
        known_out = 0
        blocked = 0
        unknown = 0
        whatsapp_ready = 0
        for lead in members:
            if str(lead.telefone_contato or "").strip():
                whatsapp_ready += 1
            statuses = consent_by_lead.get(str(lead.id), set())
            if "blocked" in statuses:
                blocked += 1
            elif statuses.intersection({"opt_out", "revoked"}):
                known_out += 1
            elif statuses.intersection({"opted_in", "transactional_only"}):
                known_in += 1
            else:
                unknown += 1
        output.append(
            {
                "id": tag_id,
                "tag_id": tag_id,
                "nome": tag.nome,
                "cor": tag.cor or "#2563eb",
                "descricao": tag.instrucao_ia or "",
                "origem": tag.tipo or "comportamento",
                "created_at": tag.criado_em.isoformat() if tag.criado_em else None,
                "last_applied_at": last_by_tag.get(tag_id).isoformat() if last_by_tag.get(tag_id) else None,
                "status": "ativa",
                "total_contacts": len(members),
                "whatsapp_ready": whatsapp_ready,
                "consent_known": known_in + known_out + blocked,
                "consent_unknown": unknown,
                "blocked": blocked,
            }
        )
    return output


async def list_import_batches(session: AsyncSession, *, empresa_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                select b.*, t.nome as tag_nome
                from contact_import_batches b
                join tags_crm t on t.empresa_id = b.empresa_id and t.id = b.tag_id
                where b.empresa_id = :empresa_id
                order by b.created_at desc
                limit 100
                """
            ),
            {"empresa_id": empresa_id},
        )
    ).mappings().all()
    return [dict(row) | {"id": str(row["id"]), "tag_id": str(row["tag_id"])} for row in rows]


async def delete_import_batch(session: AsyncSession, *, empresa_id: uuid.UUID, import_id: uuid.UUID) -> bool:
    """Remove only the import batch and its private error details.

    CRM leads, tags, contacts, consents, conversations and campaign data are
    intentionally outside this delete path.
    """
    result = await session.execute(
        text(
            """
            delete from contact_import_batches
            where id = :import_id and empresa_id = :empresa_id
            returning id
            """
        ),
        {"import_id": import_id, "empresa_id": empresa_id},
    )
    return result.first() is not None


async def preview_import_csv(file_bytes: bytes) -> dict[str, Any]:
    headers, rows, encoding = parse_csv_bytes(file_bytes)
    mapping = default_mapping(headers)
    preview_rows = []
    for row in rows[:5]:
        item = {key: row.get(column, "") for key, column in mapping.items() if column}
        if item.get("phone"):
            item["phone"] = mask_phone(item["phone"])
        preview_rows.append(item)
    return {
        "encoding": encoding,
        "headers": headers,
        "mapping": mapping,
        "sample_rows": preview_rows,
        "total_rows": len(rows),
        "csv_injection_blocked": True,
    }


async def ensure_tag(session: AsyncSession, *, empresa_id: uuid.UUID, tag_id: str | None, tag_name: str | None) -> TagCRM:
    if tag_id:
        tag_uuid = uuid.UUID(str(tag_id))
        tag = (
            await session.execute(select(TagCRM).where(TagCRM.id == tag_uuid, TagCRM.empresa_id == empresa_id))
        ).scalars().first()
        if not tag:
            raise ValueError("Tag não pertence à empresa.")
        return tag
    name = str(tag_name or "").strip()
    if not name:
        raise ValueError("Escolha ou crie uma tag para a importação.")
    existing = (
        await session.execute(select(TagCRM).where(TagCRM.empresa_id == empresa_id, TagCRM.nome.ilike(name)))
    ).scalars().first()
    if existing:
        return existing
    tag = TagCRM(empresa_id=empresa_id, nome=name[:120], cor="#2563eb", tipo="comportamento", instrucao_ia="Origem: csv_import")
    session.add(tag)
    await session.flush()
    return tag


async def record_tag_event(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    lead_id: uuid.UUID,
    tag_id: uuid.UUID,
    action: str,
    source: str,
    source_reference_id: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    idem = hashlib.sha256(f"{empresa_id}|{lead_id}|{tag_id}|{action}|{source}|{source_reference_id}".encode()).hexdigest()
    event_id = uuid.uuid4()
    await session.execute(
        text(
            """
            insert into crm_tag_events
            (id, empresa_id, lead_id, tag_id, action, source, source_reference_id, actor_user_id,
             occurred_at, idempotency_key, metadata_json, created_at)
            values
            (:id, :empresa_id, :lead_id, :tag_id, :action, :source, :source_reference_id, :actor_user_id,
             now(), :idempotency_key, cast(:metadata_json as jsonb), now())
            on conflict (empresa_id, idempotency_key) do nothing
            """
        ),
        {
            "id": event_id,
            "empresa_id": empresa_id,
            "lead_id": lead_id,
            "tag_id": tag_id,
            "action": action,
            "source": source,
            "source_reference_id": source_reference_id,
            "actor_user_id": actor_user_id,
            "idempotency_key": idem,
            "metadata_json": json.dumps(sanitize_for_log(metadata or {})),
        },
    )
    lead = (await session.execute(select(CRMLead).where(CRMLead.id == lead_id, CRMLead.empresa_id == empresa_id))).scalars().first()
    if lead:
        try:
            await registrar_followup_tag_event(
                session,
                empresa_id=empresa_id,
                lead=lead,
                tag_id=tag_id,
                action=action,
                source_event_id=source_reference_id,
                occurred_at=datetime.utcnow(),
            )
        except Exception:
            pass
    return event_id


async def import_contacts_csv(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    payload: dict[str, Any],
    user_id: uuid.UUID | None,
) -> dict[str, Any]:
    headers, rows, encoding = parse_csv_bytes(file_bytes)
    mapping = resolve_csv_mapping(headers, payload.get("mapping"))
    tag = await ensure_tag(session, empresa_id=empresa_id, tag_id=payload.get("tag_id"), tag_name=payload.get("tag_name"))
    source = str(payload.get("source") or "importacao_manual")[:80]
    consent_status = str(payload.get("consent_status") or "unknown")
    if consent_status == "opted_in" and not str(payload.get("consent_source") or "").strip():
        raise ValueError("Opted-in exige origem/evidência do consentimento.")
    batch_id = uuid.uuid4()
    idem = hashlib.sha256(f"{empresa_id}|{sanitize_filename(filename)}|{tag.id}|{len(rows)}|{json.dumps(mapping, sort_keys=True)}".encode()).hexdigest()
    stats = {
        "total_rows": len(rows),
        "inserted_count": 0,
        "updated_count": 0,
        "duplicate_count": 0,
        "invalid_count": 0,
        "conflict_count": 0,
        "blocked_count": 0,
        "opted_out_count": 0,
        "unknown_consent_count": 0,
        "opted_in_count": 0,
    }
    seen_keys: set[str] = set()
    errors: list[dict[str, Any]] = []
    invalid_reasons: dict[str, int] = {}

    def register_invalid(reason: str, row_number: int, row: dict[str, Any]) -> None:
        stats["invalid_count"] += 1
        invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
        errors.append({"row_number": row_number, "reason": reason, "row": row})

    connection = (
        await session.execute(select(Conexao).where(Conexao.empresa_id == empresa_id).order_by(Conexao.criado_em.asc()).limit(1))
    ).scalars().first()
    for idx, row in enumerate(rows, start=2):
        name = safe_cell(row.get(mapping.get("name", ""), ""))
        phone_raw = safe_cell(row.get(mapping.get("phone", ""), ""))
        email = safe_cell(row.get(mapping.get("email", ""), "")).lower()
        username = safe_cell(row.get(mapping.get("username", ""), ""))
        phone, phone_ok = normalize_phone(phone_raw, default_country_code=payload.get("default_country_code"))
        identity = phone or email or username
        if not identity:
            register_invalid("sem_identidade", idx, {"name": name, "phone": mask_phone(phone_raw)})
            continue
        if phone_raw and not phone_ok:
            register_invalid("telefone_invalido", idx, {"name": name, "phone": mask_phone(phone_raw)})
            continue
        if identity in seen_keys:
            stats["duplicate_count"] += 1
            continue
        seen_keys.add(identity)
        query = select(CRMLead).where(CRMLead.empresa_id == empresa_id)
        if phone:
            query = query.where(CRMLead.telefone_contato == phone)
        elif email:
            query = query.where(CRMLead.dados_adicionais["email"].astext == email)
        else:
            query = query.where(CRMLead.dados_adicionais["username"].astext == username)
        lead = (await session.execute(query)).scalars().first()
        dados = dict(lead.dados_adicionais or {}) if lead else {}
        for key in ("email", "username", "company", "city", "product", "notes"):
            column = mapping.get(key)
            value = safe_cell(row.get(column, "")) if column else ""
            if value:
                dados[key] = value
        if lead:
            if name:
                lead.nome_contato = name
            if phone and not lead.telefone_contato:
                lead.telefone_contato = phone
            lead.dados_adicionais = dados
            stats["updated_count"] += 1
        else:
            lead = CRMLead(
                empresa_id=empresa_id,
                nome_contato=name or phone or email or username or "Contato importado",
                telefone_contato=phone,
                tags=[],
                dados_adicionais=dados,
            )
            session.add(lead)
            await session.flush()
            stats["inserted_count"] += 1
        tags = [str(item).strip() for item in (lead.tags or []) if str(item).strip()]
        if str(tag.id) not in tags:
            tags.append(str(tag.id))
            lead.tags = tags
            flag_modified(lead, "tags")
            await record_tag_event(
                session,
                empresa_id=empresa_id,
                lead_id=lead.id,
                tag_id=tag.id,
                action="applied",
                source="csv_import",
                source_reference_id=str(batch_id),
                actor_user_id=user_id,
                metadata={"filename": sanitize_filename(filename)},
            )
        if connection and phone:
            contact = (
                await session.execute(
                    select(ChannelContact).where(
                        ChannelContact.empresa_id == empresa_id,
                        ChannelContact.channel == "whatsapp",
                        ChannelContact.conexao_id == connection.id,
                        ChannelContact.external_contact_id == phone,
                    )
                )
            ).scalars().first()
            if not contact:
                contact = ChannelContact(
                    empresa_id=empresa_id,
                    conexao_id=connection.id,
                    lead_id=lead.id,
                    channel="whatsapp",
                    provider=str(connection.provider or "meta_whatsapp"),
                    external_contact_id=phone,
                    normalized_address=phone,
                    phone_e164=phone,
                    display_name=lead.nome_contato,
                    metadata_json={"origin": "csv_import"},
                )
                session.add(contact)
                await session.flush()
            existing_consent = (
                await session.execute(
                    select(CommunicationConsent).where(
                        CommunicationConsent.empresa_id == empresa_id,
                        CommunicationConsent.contact_id == contact.id,
                        CommunicationConsent.channel == "whatsapp",
                    )
                )
            ).scalars().first()
            restrictive = {"blocked", "opted_out", "revoked"}
            if existing_consent and existing_consent.status in restrictive:
                stats["blocked_count" if existing_consent.status == "blocked" else "opted_out_count"] += 1
            elif not existing_consent:
                consent = CommunicationConsent(
                    empresa_id=empresa_id,
                    lead_id=lead.id,
                    contact_id=contact.id,
                    conexao_id=connection.id,
                    channel="whatsapp",
                    provider=str(connection.provider or "meta_whatsapp"),
                    status=consent_status,
                    source=str(payload.get("consent_source") or "csv_import_unknown"),
                    proof_reference=str(payload.get("consent_note") or "")[:300] or None,
                    consented_at=datetime.utcnow() if consent_status == "opted_in" else None,
                    recorded_by_user_id=user_id,
                )
                session.add(consent)
                stats["opted_in_count" if consent_status == "opted_in" else "unknown_consent_count"] += 1
    await session.execute(
        text(
            """
            insert into contact_import_batches
            (id, empresa_id, filename, source, tag_id, status, total_rows, inserted_count, updated_count,
             duplicate_count, invalid_count, conflict_count, blocked_count, opted_out_count, unknown_consent_count,
             opted_in_count, started_at, completed_at, initiated_by, idempotency_key, mapping_json, error_summary,
             created_at, updated_at)
            values
            (:id, :empresa_id, :filename, :source, :tag_id, 'completed', :total_rows, :inserted_count, :updated_count,
             :duplicate_count, :invalid_count, :conflict_count, :blocked_count, :opted_out_count, :unknown_consent_count,
             :opted_in_count, now(), now(), :initiated_by, :idempotency_key, cast(:mapping_json as jsonb),
             cast(:error_summary as jsonb), now(), now())
            on conflict (empresa_id, idempotency_key) do nothing
            """
        ),
        {
            "id": batch_id,
            "empresa_id": empresa_id,
            "filename": sanitize_filename(filename),
            "source": source,
            "tag_id": tag.id,
            "initiated_by": user_id,
            "idempotency_key": idem,
            "mapping_json": json.dumps(sanitize_for_log(mapping)),
            "error_summary": json.dumps({"errors": errors[:20], "invalid_reasons": invalid_reasons}),
            **stats,
        },
    )
    for error in errors[:100]:
        await session.execute(
            text(
                """
                insert into contact_import_errors
                (id, empresa_id, batch_id, row_number, reason, sanitized_row_json, created_at)
                values (:id, :empresa_id, :batch_id, :row_number, :reason, cast(:row as jsonb), now())
                """
            ),
            {
                "id": uuid.uuid4(),
                "empresa_id": empresa_id,
                "batch_id": batch_id,
                "row_number": error["row_number"],
                "reason": error["reason"],
                "row": json.dumps(sanitize_for_log(error["row"])),
            },
        )
    return {
        "batch_id": str(batch_id),
        "tag_id": str(tag.id),
        "tag_name": tag.nome,
        "encoding": encoding,
        **stats,
        "invalid_reasons": invalid_reasons,
        "external_call": False,
    }


async def resolve_audience_rules(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    rules: dict[str, Any] | None,
    default_country_code: str | None = None,
    member_limit: int = 50,
    member_view: str | None = None,
    member_cursor: str | None = None,
    member_reason: str | None = None,
) -> dict[str, Any]:
    canonical = canonicalize_audience_rules(rules)
    include_values = _rule_values(canonical, "include")
    exclude_values = _rule_values(canonical, "exclude")
    include_mode = canonical["include"]["match"]
    exclude_mode = canonical["exclude"]["match"]

    tags = (
        await session.execute(select(TagCRM).where(TagCRM.empresa_id == empresa_id).order_by(TagCRM.nome.asc()))
    ).scalars().all()
    tag_name_to_id = {str(tag.nome or "").strip().lower(): str(tag.id) for tag in tags if str(tag.nome or "").strip()}
    tag_label_by_key: dict[str, str] = {}
    for tag in tags:
        tag_label_by_key[str(tag.id).lower()] = str(tag.nome or tag.id)
        tag_label_by_key[str(tag.nome or "").strip().lower()] = str(tag.nome or tag.id)

    leads = (
        await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_id).order_by(CRMLead.id.asc()))
    ).scalars().all()
    consent_rows = (
        await session.execute(
            select(CommunicationConsent.lead_id, CommunicationConsent.status).where(CommunicationConsent.empresa_id == empresa_id)
        )
    ).all()
    consent_by_lead: dict[str, set[str]] = {}
    for lead_id, status in consent_rows:
        if lead_id:
            consent_by_lead.setdefault(str(lead_id), set()).add(str(status or "unknown"))

    rule_counts = {
        "include": {tag_label_by_key.get(value, value): 0 for value in include_values},
        "exclude": {tag_label_by_key.get(value, value): 0 for value in exclude_values},
    }
    include_candidates: list[tuple[CRMLead, set[str]]] = []
    raw_include_matches = 0
    for lead in leads:
        lead_tags = _lead_tag_set(lead, tag_name_to_id)
        for value in include_values:
            if value in lead_tags:
                rule_counts["include"][tag_label_by_key.get(value, value)] = rule_counts["include"].get(tag_label_by_key.get(value, value), 0) + 1
        for value in exclude_values:
            if value in lead_tags:
                rule_counts["exclude"][tag_label_by_key.get(value, value)] = rule_counts["exclude"].get(tag_label_by_key.get(value, value), 0) + 1
        include_ok = _matches(lead_tags, include_values, include_mode, empty_matches=True)
        if include_ok:
            raw_include_matches += max(1, len(include_values.intersection(lead_tags)) if include_values else 1)
            include_candidates.append((lead, lead_tags))

    duplicate_lead_count = max(0, raw_include_matches - len(include_candidates))
    excluded_by_rules = 0
    optout_removed = 0
    blocked_removed = 0
    invalid_phone_count = 0
    duplicates_removed = duplicate_lead_count
    final_members_all: list[dict[str, Any]] = []
    excluded_members_all: list[dict[str, Any]] = []
    seen_phones: set[str] = set()
    final_lead_ids: list[str] = []

    for lead, lead_tags in include_candidates:
        exclude_ok = _matches(lead_tags, exclude_values, exclude_mode, empty_matches=False)
        if exclude_ok:
            excluded_by_rules += 1
            matched = sorted(tag_label_by_key.get(value, value) for value in exclude_values.intersection(lead_tags))
            excluded_members_all.append(_member_payload(lead, reason="MATCHED_EXCLUDE", detail=", ".join(matched) or None))
            continue

        address, phone_ok = normalize_phone(str(lead.telefone_contato or ""), default_country_code=default_country_code)
        if not address or not phone_ok:
            invalid_phone_count += 1
            excluded_members_all.append(_member_payload(lead, reason="INVALID_PHONE"))
            continue

        statuses = consent_by_lead.get(str(lead.id), set())
        if statuses.intersection({"blocked"}):
            blocked_removed += 1
            excluded_members_all.append(_member_payload(lead, reason="BLOCKED", address=address))
            continue
        if statuses.intersection(RESTRICTIVE_CONSENT_STATUSES):
            optout_removed += 1
            excluded_members_all.append(_member_payload(lead, reason="OPT_OUT", address=address))
            continue

        if address in seen_phones:
            duplicates_removed += 1
            excluded_members_all.append(_member_payload(lead, reason="DUPLICATE_PHONE", address=address))
            continue
        seen_phones.add(address)
        final_lead_ids.append(str(lead.id))
        final_members_all.append(_member_payload(lead, reason="MATCHED_INCLUDE", address=address))

    final_count = len(final_lead_ids)
    if member_reason:
        excluded_page_source = [item for item in excluded_members_all if item.get("reason") == member_reason]
    else:
        excluded_page_source = excluded_members_all
    view = str(member_view or "both").strip().lower()
    included_page = _paginate_members(final_members_all, cursor=member_cursor if view == "included" else None, limit=member_limit)
    excluded_page = _paginate_members(excluded_page_source, cursor=member_cursor if view == "excluded" else None, limit=member_limit)
    return {
        "schema_version": AUDIENCE_SCHEMA_VERSION,
        "rules": canonical,
        "selected": final_count,
        "excluded": excluded_by_rules + optout_removed + blocked_removed + invalid_phone_count + duplicates_removed,
        "dynamic": True,
        "snapshot_created": False,
        "counts": {
            "contacts_found": len(include_candidates),
            "initial_included": len(include_candidates),
            "excluded_by_rules": excluded_by_rules,
            "optout_removed": optout_removed,
            "blocked_removed": blocked_removed,
            "invalid_phone_count": invalid_phone_count,
            "duplicates_removed": duplicates_removed,
            "duplicate_lead_count": duplicate_lead_count,
            "duplicate_phone_count": max(0, duplicates_removed - duplicate_lead_count),
            "final_audience_count": final_count,
        },
        "rule_counts": rule_counts,
        "members": {
            "included": included_page["items"],
            "excluded": excluded_page["items"],
            "limit": member_limit,
            "pagination": {
                "model": "cursor",
                "stable_order": "lead_id",
                "included": included_page,
                "excluded": excluded_page,
                "view": view,
                "reason_filter": member_reason,
            },
        },
        "reason_codes": [
            "MATCHED_INCLUDE",
            "MATCHED_EXCLUDE",
            "OPT_OUT",
            "BLOCKED",
            "INVALID_PHONE",
            "DUPLICATE_PHONE",
        ],
        "final_lead_ids": final_lead_ids,
        "resolution_order": [
            "resolve_inclusion",
            "dedupe_identity",
            "apply_exclusions",
            "apply_consent_optout",
            "validate_phone",
            "dedupe_normalized_phone",
            "produce_final_audience",
        ],
        "exclusion_precedence": EXCLUSION_PRECEDENCE,
    }


async def preview_saved_audience(
    session: AsyncSession,
    *,
    empresa_id: uuid.UUID,
    rules: dict[str, Any],
    member_view: str | None = None,
    member_cursor: str | None = None,
    member_limit: int = 50,
    member_reason: str | None = None,
) -> dict[str, Any]:
    return await resolve_audience_rules(
        session,
        empresa_id=empresa_id,
        rules=rules,
        member_view=member_view,
        member_cursor=member_cursor,
        member_limit=member_limit,
        member_reason=member_reason,
    )


async def save_audience(session: AsyncSession, *, empresa_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Informe o nome do público.")
    rules = payload.get("rules") if isinstance(payload.get("rules"), dict) else {}
    preview = await preview_saved_audience(session, empresa_id=empresa_id, rules=rules)
    canonical_rules = preview["rules"]
    audience_id = uuid.uuid4()
    idem = hashlib.sha256(f"{empresa_id}|{name}|{json.dumps(rules, sort_keys=True)}".encode()).hexdigest()
    await session.execute(
        text(
            """
            insert into saved_audiences
            (id, empresa_id, name, description, status, rules_json, preview_json, idempotency_key, created_at, updated_at)
            values (:id, :empresa_id, :name, :description, 'active', cast(:rules as jsonb),
                    cast(:preview as jsonb), :idempotency_key, now(), now())
            on conflict (empresa_id, name) do update set
              description = excluded.description,
              rules_json = excluded.rules_json,
              preview_json = excluded.preview_json,
              updated_at = now()
            """
        ),
        {
            "id": audience_id,
            "empresa_id": empresa_id,
            "name": name[:160],
            "description": str(payload.get("description") or "")[:500],
            "rules": json.dumps(sanitize_for_log(canonical_rules)),
            "preview": json.dumps(preview),
            "idempotency_key": idem,
        },
    )
    return {"saved": True, "id": str(audience_id), "preview": preview}


async def list_saved_audiences(session: AsyncSession, *, empresa_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                select id::text, name, description, status, rules_json, preview_json, created_at, updated_at
                from saved_audiences
                where empresa_id = :empresa_id
                order by created_at desc
                """
            ),
            {"empresa_id": empresa_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def list_campaign_snapshots(session: AsyncSession, *, empresa_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                select campaign_id::text, created_at, jsonb_array_length(inclusions_json) as inclusions,
                       jsonb_array_length(exclusions_json) as exclusions, audience_version
                from campaign_preparation_snapshots
                where empresa_id = :empresa_id
                order by created_at desc
                limit 100
                """
            ),
            {"empresa_id": empresa_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]
