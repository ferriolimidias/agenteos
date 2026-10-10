"""
Serviço central de CRMLead.

Responsável por:
- Sanitizar números de telefone para garantir chave estável.
- Fazer UPSERT (get-or-create) seguro de leads, evitando duplicatas mesmo sob
  alta concorrência (advisory lock por (empresa_id, telefone) no Postgres).
- Classificar o nome do contato como pessoa física / pessoa jurídica /
  indeterminado, para que o agente de saudação possa decidir se pede o nome.

A intenção é que TODO ponto do código que precise criar/atualizar um CRMLead
passe por aqui, em vez de duplicar a lógica de busca-e-cria.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from typing import Iterable, Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CRMEtapa, CRMFunil, CRMLead
from app.services.whatsapp_address import normalize_whatsapp_address

logger = logging.getLogger(__name__)


def _constraint_name(exc: Exception) -> str | None:
    """Read constraint names from asyncpg and psycopg SQLAlchemy errors."""
    pending = [exc]
    visited: set[int] = set()
    while pending:
        candidate = pending.pop()
        if candidate is None or id(candidate) in visited:
            continue
        visited.add(id(candidate))
        direct = getattr(candidate, "constraint_name", None)
        if direct:
            return str(direct)
        diagnostic = getattr(candidate, "diag", None)
        value = getattr(diagnostic, "constraint_name", None) if diagnostic is not None else None
        if value:
            return str(value)
        pending.extend((
            getattr(candidate, "orig", None),
            getattr(candidate, "__cause__", None),
            getattr(candidate, "__context__", None),
        ))
    return None


# ─── Sanitização de telefone ──────────────────────────────────────────────────

_TELEFONE_NAO_DIGITO = re.compile(r"\D")
_TELEFONE_SUFIXOS_WHATSAPP = ("@s.whatsapp.net", "@g.us", "@c.us", "@broadcast")


def _whatsapp_identity_candidates(telefone: str | None) -> tuple[str, ...]:
    raw = str(telefone or "").strip().lower()
    digits = sanitize_telefone(raw)
    normalized = normalize_whatsapp_address(telefone, default_country="BR").digits
    if not (
        (digits.startswith("55") and len(digits) in {12, 13})
        or (normalized.startswith("55") and len(normalized) in {12, 13})
        or "@s.whatsapp.net" in raw
    ):
        return ()
    if normalized.startswith("55") and len(normalized) == 12 and normalized[4] in "6789":
        normalized = f"{normalized[:4]}9{normalized[4:]}"
    values = [normalized, digits]
    for value in (normalized, digits):
        if value:
            values.append(f"+{value}")
    if normalized.startswith("55") and len(normalized) == 13 and normalized[4] == "9":
        legacy_mobile = f"{normalized[:4]}{normalized[5:]}"
        values.extend([legacy_mobile, f"+{legacy_mobile}"])
    return tuple(dict.fromkeys(value for value in values if value))


def sanitize_telefone(telefone: str | None) -> str:
    """
    Normaliza qualquer representação de telefone para apenas dígitos.

    Aceita "+55 (11) 99999-9999", "5511999999999@s.whatsapp.net", etc.
    Retorna string vazia quando a entrada não tem dígitos aproveitáveis.
    """
    bruto = str(telefone or "").strip()
    if not bruto:
        return ""
    for sufixo in _TELEFONE_SUFIXOS_WHATSAPP:
        if sufixo in bruto:
            bruto = bruto.replace(sufixo, "")
    return _TELEFONE_NAO_DIGITO.sub("", bruto)


# ─── Classificação PF/PJ do nome do contato ───────────────────────────────────

# Palavras-chave que indicam fortemente Pessoa Jurídica.
# Lista intencionalmente conservadora — preferimos falso-negativo (cair em
# "indeterminado" e perguntar o nome) a falso-positivo (não perguntar para
# alguém que era PJ).
_PJ_KEYWORDS = frozenset(
    {
        "ltda", "me", "mei", "eireli", "sa", "s.a",
        "loja", "lojas", "comercio", "comercial", "industria", "industrial",
        "empresa", "grupo", "holding", "cia", "cia.",
        "oficina", "salao", "salão", "clinica", "clínica", "consultorio", "consultório",
        "escritorio", "escritório", "consultoria", "servicos", "serviços",
        "atelie", "ateliê", "studio", "estudio", "estúdio",
        "transportes", "logistica", "logística", "distribuidora", "importadora", "exportadora",
        "restaurante", "lanchonete", "pizzaria", "padaria", "confeitaria",
        "mercado", "supermercado", "minimercado", "mercadinho",
        "farmacia", "farmácia", "drogaria", "hospital", "policlinica",
        "autopecas", "autopeças", "autocenter", "motors", "motor",
        "tecnica", "técnica", "engenharia", "construtora", "imobiliaria", "imobiliária",
        "escola", "colegio", "colégio", "academia",
        "produtos", "comercio", "atacado", "varejo",
        "agencia", "agência", "studio", "moda", "boutique",
        "barbearia", "petshop", "pet shop",
        "inc", "llc", "gmbh", "corp", "corporation",
    }
)

# Conectores comuns de razão social que reforçam suspeita de PJ.
_PJ_CONNECTORS = frozenset({"do", "da", "de", "e", "&", "dos", "das"})

# Palavras genéricas (não são nome de pessoa real).
_NOMES_GENERICOS = frozenset(
    {
        "usuario", "usuário", "user", "cliente", "contato", "teste",
        "lead", "novo", "anonimo", "anônimo", "unknown",
        "contato meta", "contato whatsapp", "cliente meta", "cliente whatsapp",
        "desconhecido",
    }
)


def _strip_accents_lower(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in base if not unicodedata.combining(ch)).lower()


def classificar_nome_contato(nome_contato: str | None) -> str:
    """
    Heurística leve para decidir se um nome veio de uma pessoa física, de uma
    empresa, ou se é genérico/incompleto demais para confiar.

    Retorna:
        "pessoa_fisica"     — provável nome de PF (ex.: "João Silva").
        "pessoa_juridica"   — contém palavra-chave de PJ (ex.: "Oficina do Zé").
        "indeterminado"    — só dígitos/emoji, vazio, "Usuário (Auto)", etc.

    Importante: a UI continua mostrando o nome original. Esta classificação só
    serve para o agente de saudação decidir se pergunta o nome de quem fala.
    """
    bruto = str(nome_contato or "").strip()
    if not bruto:
        return "indeterminado"

    # Caso degenerado: nome é o próprio telefone.
    if sanitize_telefone(bruto) and not re.search(r"[A-Za-zÀ-ÿ]", bruto):
        return "indeterminado"

    normalizado = _strip_accents_lower(bruto)

    # Placeholders e nomes genéricos do sistema.
    if normalizado in _NOMES_GENERICOS:
        return "indeterminado"
    if normalizado.startswith("usuario") or "usuario (auto)" in normalizado or "[simulador]" in normalizado:
        return "indeterminado"

    tokens = [t for t in re.split(r"\s+", normalizado) if t]
    if not tokens:
        return "indeterminado"

    # Sinal forte de PJ: qualquer termo conhecido aparece nos tokens.
    for token in tokens:
        limpo = token.strip(".,;:")
        if limpo in _PJ_KEYWORDS:
            return "pessoa_juridica"

    # Combinação token-conector-token muito comum em razão social
    # (ex.: "oficina do ze", "casa das tintas"). Já tratado acima pelo
    # primeiro token, mas reforço aqui para casos invertidos.
    if len(tokens) >= 3 and tokens[1] in _PJ_CONNECTORS:
        # "Casa do Pão" → suspeita de PJ.
        for token in (tokens[0], tokens[2]):
            if token in _PJ_KEYWORDS:
                return "pessoa_juridica"

    # Heurística de PF: 1 a 4 tokens, todos alfabéticos, cada um com >=2 chars.
    if 1 <= len(tokens) <= 4 and all(t.isalpha() and len(t) >= 2 for t in tokens):
        return "pessoa_fisica"

    return "indeterminado"


def nome_contato_tem_procedencia_manual(dados_adicionais: dict | None = None) -> bool:
    """
    Indica se o nome foi marcado como edição manual, para impedir que um
    webhook automático o sobrescreva.
    """
    if not isinstance(dados_adicionais, dict):
        return False
    origem = str(dados_adicionais.get("nome_origem") or "").strip().lower()
    if origem == "manual":
        return True
    if bool(dados_adicionais.get("nome_editado_manual")):
        return True
    return False


def nome_contato_eh_fallback(nome_contato: str | None, telefone_sanitizado: str | None = None) -> bool:
    atual = str(nome_contato or "").strip()
    if not atual:
        return True

    normalizado = _strip_accents_lower(atual)
    if normalizado in _NOMES_GENERICOS:
        return True
    if normalizado.startswith("usuario") or "usuario (auto)" in normalizado or "[simulador]" in normalizado:
        return True
    if re.fullmatch(r"contato\s*[•\-]?\s*final\s*\d{3,4}", normalizado):
        return True
    if re.fullmatch(r"[\d\+\-\(\)\s]+", atual):
        return True
    if telefone_sanitizado and re.sub(r"\D", "", atual) == telefone_sanitizado:
        return True
    return False


# ─── UPSERT seguro de Lead ────────────────────────────────────────────────────

def _advisory_lock_key(empresa_id: uuid.UUID, telefone_sanitizado: str) -> int:
    """
    Produz uma chave int64 estável para `pg_advisory_xact_lock` a partir do par
    (empresa_id, telefone). Usamos blake2b com digest=8 bytes para caber em
    bigint do Postgres.
    """
    raw = f"{empresa_id}:{telefone_sanitizado}".encode("utf-8")
    digest = hashlib.blake2b(raw, digest_size=8).digest()
    # Signed: o Postgres aceita até 2^63-1 como chave do advisory lock.
    return int.from_bytes(digest, byteorder="big", signed=True)


async def _buscar_etapa_inicial(session: AsyncSession, empresa_uuid: uuid.UUID) -> uuid.UUID | None:
    result = await session.execute(
        select(CRMEtapa.id)
        .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
        .where(CRMFunil.empresa_id == empresa_uuid)
        .order_by(CRMEtapa.ordem.asc())
    )
    return result.scalars().first()


async def get_or_create_lead(
    session: AsyncSession,
    *,
    empresa_id: str | uuid.UUID,
    telefone: str | None,
    nome_inicial: str | None = None,
    historico_resumo_inicial: str | None = None,
    extras: Optional[dict] = None,
) -> tuple[CRMLead, bool]:
    """
    Garante a existência de um CRMLead único por (empresa_id, telefone).

    Estratégia anti-duplicação (multicamada):
      1. Sanitiza o telefone (apenas dígitos).
      2. Antes da criação, adquire `pg_advisory_xact_lock` baseado em
         hash(empresa_id, telefone) — bloqueia qualquer outra transação que
         tente upsertar o MESMO contato até esta commitar.
      3. Faz SELECT do lead já dentro do lock; se existe, devolve-o e atualiza
         silenciosamente para a forma sanitizada do telefone (cura dados antigos).
      4. Se não existe, cria novo. Se uma corrida ainda assim acontecer (ex.:
         outro processo em outro DB session), captura `IntegrityError` e
         relê o lead criado pelo concorrente.

    Retorna `(lead, criado)` — onde `criado` é True só quando este chamador foi
    quem realmente inseriu o registro.
    """
    if isinstance(empresa_id, str):
        try:
            empresa_uuid = uuid.UUID(empresa_id)
        except ValueError as exc:
            raise ValueError(f"empresa_id inválido: {empresa_id!r}") from exc
    else:
        empresa_uuid = empresa_id

    telefone_sanitizado = sanitize_telefone(telefone)
    if not telefone_sanitizado:
        raise ValueError("telefone vazio/sem dígitos não pode identificar um lead.")
    whatsapp_candidates = _whatsapp_identity_candidates(telefone)
    telefone_canonico = whatsapp_candidates[0] if whatsapp_candidates else telefone_sanitizado

    # Camada 2: trava cooperativa em PG por (empresa, telefone). Liberada no commit.
    lock_key = _advisory_lock_key(empresa_uuid, telefone_canonico)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})

    # Prefer the canonical identity before looking at legacy phone spellings.
    # Otherwise an old lead (for example a mobile number without the current
    # country/mobile prefix) can be selected first and then updated into a
    # different lead's already-owned unique phone value.
    canonical_result = await session.execute(
        select(CRMLead).where(
            CRMLead.empresa_id == empresa_uuid,
            CRMLead.telefone_contato == telefone_canonico,
        )
    )
    canonical_lead = canonical_result.scalars().first()
    if canonical_lead is not None:
        return canonical_lead, False

    # Camada 3: tenta encontrar pelo sanitizado ou pela forma "como veio".
    telefone_bruto = str(telefone or "").strip()
    candidatos_busca: list[str] = []
    if telefone_sanitizado:
        candidatos_busca.append(telefone_sanitizado)
    if telefone_bruto and telefone_bruto not in candidatos_busca:
        candidatos_busca.append(telefone_bruto)
    for candidato in whatsapp_candidates:
        if candidato not in candidatos_busca:
            candidatos_busca.append(candidato)

    result = await session.execute(
        select(CRMLead).where(
            CRMLead.empresa_id == empresa_uuid,
            CRMLead.telefone_contato.in_(candidatos_busca),
        )
    )
    lead_existente = result.scalars().first()
    if lead_existente is not None:
        telefone_persistido = telefone_canonico if whatsapp_candidates else telefone_sanitizado
        if str(lead_existente.telefone_contato or "") != telefone_persistido:
            try:
                # Keep a conflicting legacy normalization inside a savepoint so
                # its recovery cannot rollback the inbound transaction.
                async with session.begin_nested():
                    lead_existente.telefone_contato = telefone_persistido
                    await session.flush()
            except IntegrityError as exc:
                constraint = _constraint_name(exc)
                if constraint != "idx_unique_lead_telefone":
                    raise
                winner_result = await session.execute(
                    select(CRMLead).where(
                        CRMLead.empresa_id == empresa_uuid,
                        CRMLead.telefone_contato == telefone_persistido,
                    )
                )
                winner = winner_result.scalars().first()
                if winner is None:
                    raise
                logger.info(
                    "event=LEAD_PHONE_CANONICAL_CONFLICT_RESOLVED tenant_hash=%s resolution=existing_canonical_lead",
                    hashlib.sha256(str(empresa_uuid).encode("utf-8")).hexdigest()[:12],
                )
                return winner, False
        return lead_existente, False

    # Camada 4: criação dentro do lock.
    etapa_id = await _buscar_etapa_inicial(session, empresa_uuid)

    nome_para_gravar = (nome_inicial or "").strip() or telefone_sanitizado or "Usuário (Auto)"

    novo_lead = CRMLead(
        empresa_id=empresa_uuid,
        nome_contato=nome_para_gravar,
        telefone_contato=telefone_canonico if whatsapp_candidates else telefone_sanitizado,
        etapa_id=etapa_id,
        historico_resumo=(historico_resumo_inicial or None),
    )
    if extras:
        for campo, valor in extras.items():
            if hasattr(novo_lead, campo) and valor is not None:
                setattr(novo_lead, campo, valor)

    try:
        # Keep an insert race inside a savepoint. Rolling back the outer
        # transaction here would release the inbound event's transaction lock.
        async with session.begin_nested():
            session.add(novo_lead)
            await session.flush()
    except IntegrityError as exc:
        # Only the phone uniqueness violation can mean another transaction
        # created the canonical lead. FK/NOT NULL/CHECK and unknown constraints
        # must reach the caller so the webhook does not ACK a failed insert.
        if _constraint_name(exc) != "idx_unique_lead_telefone":
            raise
        telefone_persistido = telefone_canonico if whatsapp_candidates else telefone_sanitizado
        result_retry = await session.execute(
            select(CRMLead).where(
                CRMLead.empresa_id == empresa_uuid,
                CRMLead.telefone_contato == telefone_persistido,
            )
        )
        vencedor = result_retry.scalars().first()
        if vencedor is None:
            logger.error(
                "[LEAD SERVICE] IntegrityError sem vencedor: tenant_hash=%s telefone_hash=%s",
                hashlib.sha256(str(empresa_uuid).encode("utf-8")).hexdigest()[:12],
                hashlib.sha256(telefone_sanitizado.encode("utf-8")).hexdigest()[:12],
            )
            raise
        logger.info(
            "event=LEAD_PHONE_CANONICAL_CONFLICT_RESOLVED tenant_hash=%s resolution=existing_canonical_lead",
            hashlib.sha256(str(empresa_uuid).encode("utf-8")).hexdigest()[:12],
        )
        return vencedor, False

    return novo_lead, True


def precisa_atualizar_nome(nome_atual: str | None, telefone_sanitizado: str, dados_adicionais: dict | None = None) -> bool:
    """
    Decide se o nome atual do lead é fraco o suficiente para ser sobrescrito
    automaticamente quando recebemos um nome melhor (ex.: o nome de perfil do
    WhatsApp).

    Conservador: só atualiza quando o atual está claramente vazio, é um
    placeholder/fallback conhecido, é só o próprio número, ou não foi marcado
    como edição manual.
    """
    if nome_contato_tem_procedencia_manual(dados_adicionais):
        return False
    return nome_contato_eh_fallback(nome_atual, telefone_sanitizado)


def candidatos_telefone_para_busca(telefone: str | None) -> Iterable[str]:
    """
    Lista de strings de telefone que devem ser tentadas ao buscar um lead em
    bases que possam conter dados antigos não-sanitizados.
    """
    sanit = sanitize_telefone(telefone)
    bruto = str(telefone or "").strip()
    out: list[str] = []
    if sanit:
        out.append(sanit)
    if bruto and bruto not in out:
        out.append(bruto)
    return out
