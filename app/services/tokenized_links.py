from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.services.leoft_security import mask_token
from db.models import CRMLead, Empresa


TOKENIZED_LINKS_KEY = "links_tokenizados"
DEFAULT_TOKEN_PARAM = "token"
DEFAULT_VALIDITY_HOURS = 168


class TokenizedLinkCryptoError(ValueError):
    pass


@dataclass(frozen=True)
class TokenizedLinkResult:
    slug: str
    token: str | None
    url: str | None
    config: dict[str, Any]
    record: dict[str, Any] | None = None
    reused: bool = False
    operational_pending: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class TokenizedLinkValidation:
    ok: bool
    slug: str
    link_slug: str | None = None
    lead: CRMLead | None = None
    empresa: Empresa | None = None
    config: dict[str, Any] | None = None
    record: dict[str, Any] | None = None
    reason: str | None = None


def get_tokenized_link_config(empresa: Empresa | Mapping[str, Any] | None, link_slug: str) -> dict[str, Any] | None:
    slug = str(link_slug or "").strip()
    if not slug:
        return None

    credenciais = _get_value(empresa, "credenciais_canais")
    if not isinstance(credenciais, Mapping):
        return None

    configs = credenciais.get(TOKENIZED_LINKS_KEY)
    if not isinstance(configs, Mapping):
        return None

    raw = configs.get(slug)
    if not isinstance(raw, Mapping):
        return None

    config = dict(raw)
    config.setdefault("slug", slug)
    if not bool(config.get("ativo", True)):
        return None
    return config


async def validate_tokenized_link(
    session: AsyncSession,
    *,
    public_slug: str,
    token: str,
) -> TokenizedLinkValidation:
    slug = str(public_slug or "").strip()
    raw_token = str(token or "").strip()
    if not slug or not raw_token:
        return TokenizedLinkValidation(ok=False, slug=slug, reason="missing_token")

    token_hash = _token_hash(raw_token)
    for link_slug in _candidate_link_slugs(slug):
        result = await session.execute(
            select(CRMLead, Empresa)
            .join(Empresa, CRMLead.empresa_id == Empresa.id)
            .where(
                CRMLead.dados_adicionais.contains(
                    {TOKENIZED_LINKS_KEY: {link_slug: {"token_hash": token_hash}}}
                )
            )
            .limit(2)
        )
        rows = result.all()
        for lead, empresa in rows:
            record = _link_record(lead, link_slug)
            if not record:
                continue
            if str(record.get("token_hash") or "") != token_hash:
                continue
            stored_token = str(record.get("token") or "").strip()
            if stored_token and not secrets.compare_digest(stored_token, raw_token):
                continue
            config = get_tokenized_link_config(empresa, link_slug)
            if not config:
                return TokenizedLinkValidation(
                    ok=False,
                    slug=slug,
                    link_slug=link_slug,
                    lead=lead,
                    empresa=empresa,
                    record=record,
                    reason="missing_or_inactive_config",
                )
            if not _config_matches_public_slug(config, slug, link_slug):
                return TokenizedLinkValidation(
                    ok=False,
                    slug=slug,
                    link_slug=link_slug,
                    lead=lead,
                    empresa=empresa,
                    config=config,
                    record=record,
                    reason="slug_mismatch",
                )
            expires = _parse_iso(str(record.get("expira_em") or ""))
            if expires and expires <= _utc_now():
                return TokenizedLinkValidation(
                    ok=False,
                    slug=slug,
                    link_slug=link_slug,
                    lead=lead,
                    empresa=empresa,
                    config=config,
                    record=record,
                    reason="expired",
                )
            return TokenizedLinkValidation(
                ok=True,
                slug=slug,
                link_slug=link_slug,
                lead=lead,
                empresa=empresa,
                config=config,
                record=record,
            )

    return TokenizedLinkValidation(ok=False, slug=slug, reason="not_found")


def generate_or_get_tokenized_link(
    db: Any,
    empresa: Empresa | Mapping[str, Any],
    lead: CRMLead,
    link_slug: str,
    sequence_slug: str | None = None,
    conversation_id: str | None = None,
) -> TokenizedLinkResult:
    slug = str(link_slug or "").strip()
    config = get_tokenized_link_config(empresa, slug)
    if not slug or not config:
        return TokenizedLinkResult(
            slug=slug,
            token=None,
            url=None,
            config={},
            operational_pending=True,
            reason="missing_or_inactive_config",
        )

    base_url = str(config.get("base_url") or "").strip()
    if not base_url:
        _mark_pending(lead, slug, sequence_slug, "missing_base_url")
        return TokenizedLinkResult(
            slug=slug,
            token=None,
            url=None,
            config=config,
            operational_pending=True,
            reason="missing_base_url",
        )

    dados = _lead_data(lead)
    links = _links_map(dados)
    current = dict(links.get(slug) or {}) if isinstance(links.get(slug), Mapping) else {}
    now = _utc_now()

    recovered_token = _recover_reusable_token(current, now)
    if recovered_token:
        parametro = str(config.get("parametro_token") or DEFAULT_TOKEN_PARAM).strip() or DEFAULT_TOKEN_PARAM
        url = _append_query_param(base_url, parametro, recovered_token)
        return TokenizedLinkResult(
            slug=slug,
            token=recovered_token,
            url=url,
            config=config,
            record=current,
            reused=True,
            operational_pending=False,
        )

    token = _new_token()
    parametro = str(config.get("parametro_token") or DEFAULT_TOKEN_PARAM).strip() or DEFAULT_TOKEN_PARAM
    validade_horas = _validity_hours(config)
    expira_em = now + timedelta(hours=validade_horas)
    url = _append_query_param(base_url, parametro, token)

    stable_anamnese_id = str(current.get("anamnese_id") or "").strip() or f"{slug}:{lead.id}"
    record = {
        "anamnese_id": stable_anamnese_id,
        "token_hash": _token_hash(token),
        "token_ciphertext": _encrypt_token(token),
        "token_masked": mask_token(token),
        "url": mask_tokenized_url_for_logs(url),
        "slug": slug,
        "criado_em": _iso(now),
        "expira_em": _iso(expira_em),
        "usado_em": None,
        "enviado_em": None,
        "empresa_id": str(_get_value(empresa, "id") or _get_value(empresa, "empresa_id") or ""),
        "lead_id": str(getattr(lead, "id", "") or ""),
        "conversation_id": str(conversation_id or current.get("conversation_id") or "").strip() or None,
        "sequencia_slug": str(sequence_slug or "") or None,
        "parametro_token": parametro,
        "produto_id": str(config.get("produto_id") or config.get("product_id") or "").strip() or None,
        "produto_nome": str(config.get("oferta_nome") or config.get("nome") or "").strip() or None,
    }
    links[slug] = record
    dados[TOKENIZED_LINKS_KEY] = links
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")
    if db is not None:
        db.add(lead)

    return TokenizedLinkResult(
        slug=slug,
        token=token,
        url=url,
        config=config,
        record=record,
        reused=False,
        operational_pending=False,
    )


def tokenized_link_token_hash(token: str) -> str:
    return _token_hash(str(token or ""))


def mark_tokenized_link_sent(
    db: Any,
    lead: CRMLead,
    link_slug: str,
    sequence_slug: str | None = None,
) -> None:
    slug = str(link_slug or "").strip()
    if not slug:
        return

    dados = _lead_data(lead)
    links = _links_map(dados)
    current = dict(links.get(slug) or {}) if isinstance(links.get(slug), Mapping) else {}
    if not current:
        return

    current["enviado_em"] = _iso(_utc_now())
    if sequence_slug:
        current["sequencia_slug"] = str(sequence_slug)
    links[slug] = current
    dados[TOKENIZED_LINKS_KEY] = links
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")
    if db is not None:
        db.add(lead)


def render_tokenized_link_message(template: str, lead: CRMLead | Mapping[str, Any] | None, link: TokenizedLinkResult) -> str:
    text = str(template or "").replace("\r\n", "\n").replace("\r", "\n")
    name = _lead_name(lead)
    url = str(link.url or "")
    return (
        text.replace("[Nome]", name)
        .replace("{nome}", name)
        .replace("[LINK]", url)
        .replace("{link}", url)
        .strip()
    )


def mask_tokenized_url_for_logs(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in {"token", "lead_token", "access_token"} or "token" in key.lower():
                query.append((key, "***"))
            else:
                query.append((key, value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return raw.split("?", 1)[0] + "?token=***" if "?" in raw else raw


def _mark_pending(lead: CRMLead, slug: str, sequence_slug: str | None, reason: str) -> None:
    dados = _lead_data(lead)
    pending = dict(dados.get("links_tokenizados_pendencias") or {}) if isinstance(dados.get("links_tokenizados_pendencias"), Mapping) else {}
    pending[slug] = {
        "status": "pendente_operacional",
        "motivo": reason,
        "sequencia_slug": str(sequence_slug or "") or None,
        "registrado_em": _iso(_utc_now()),
    }
    dados["links_tokenizados_pendencias"] = pending
    lead.dados_adicionais = dados
    flag_modified(lead, "dados_adicionais")


def _recover_reusable_token(record: Mapping[str, Any], now: datetime) -> str | None:
    expires = _parse_iso(str(record.get("expira_em") or ""))
    if not expires or expires <= now:
        return None
    token_hash = str(record.get("token_hash") or "").strip()
    ciphertext = str(record.get("token_ciphertext") or "").strip()
    if not token_hash or not ciphertext:
        return None
    try:
        token = _decrypt_token(ciphertext)
    except TokenizedLinkCryptoError:
        return None
    return token if secrets.compare_digest(_token_hash(token), token_hash) else None


def _token_fernet() -> Fernet:
    raw_key = str(
        os.getenv("TOKENIZED_LINK_ENCRYPTION_KEY")
        or os.getenv("SECRET_KEY")
        or ""
    ).strip()
    if len(raw_key) < 32:
        raise TokenizedLinkCryptoError("Chave de criptografia de link tokenizado ausente ou curta.")
    derived = hashlib.sha256(f"agentos:tokenized-link:v1:{raw_key}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _encrypt_token(token: str) -> str:
    raw = str(token or "").strip()
    if not raw:
        raise TokenizedLinkCryptoError("Token de link vazio.")
    return _token_fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def _decrypt_token(ciphertext: str) -> str:
    try:
        return _token_fernet().decrypt(str(ciphertext or "").encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise TokenizedLinkCryptoError("Token de link criptografado invalido.") from exc


def _append_query_param(base_url: str, key: str, value: str) -> str:
    parts = urlsplit(base_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != key]
    query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _validity_hours(config: Mapping[str, Any]) -> int:
    try:
        hours = int(config.get("validade_horas") or DEFAULT_VALIDITY_HOURS)
    except (TypeError, ValueError):
        hours = DEFAULT_VALIDITY_HOURS
    return max(1, min(hours, 24 * 365))


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _lead_data(lead: CRMLead) -> dict[str, Any]:
    return dict(lead.dados_adicionais or {}) if isinstance(lead.dados_adicionais, dict) else {}


def _links_map(dados: dict[str, Any]) -> dict[str, Any]:
    return dict(dados.get(TOKENIZED_LINKS_KEY) or {}) if isinstance(dados.get(TOKENIZED_LINKS_KEY), Mapping) else {}


def _link_record(lead: CRMLead, link_slug: str) -> dict[str, Any] | None:
    links = _links_map(_lead_data(lead))
    record = links.get(str(link_slug or "").strip())
    return dict(record) if isinstance(record, Mapping) else None


def _candidate_link_slugs(public_slug: str) -> list[str]:
    cleaned = str(public_slug or "").strip()
    candidates = [cleaned]
    if cleaned and not cleaned.startswith("anamnese_"):
        candidates.append(f"anamnese_{cleaned}")
    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        if item and item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def _config_matches_public_slug(config: Mapping[str, Any], public_slug: str, link_slug: str) -> bool:
    slug = str(public_slug or "").strip()
    link = str(link_slug or "").strip()
    explicit_values = {
        str(config.get("public_slug") or "").strip(),
        str(config.get("slug_publico") or "").strip(),
        str(config.get("rota_publica_slug") or "").strip(),
    }
    if slug in explicit_values:
        return True
    if link == slug:
        return True
    if link == f"anamnese_{slug}" and str(config.get("finalidade") or "").strip() == "anamnese":
        return True
    return False


def _lead_name(lead: CRMLead | Mapping[str, Any] | None) -> str:
    name = _get_value(lead, "nome_contato")
    if not str(name or "").strip():
        name = _get_value(lead, "nome")
    cleaned = " ".join(str(name or "").split()).strip()
    return cleaned[:80] or "tudo certo"


def _get_value(source: Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _utc_now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1]
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
