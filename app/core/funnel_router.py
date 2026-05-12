"""
Roteamento determinístico de funil: etapa CRM e/ou tags do lead → exatamente um especialista.

A configuração oficial fica em `Empresa.credenciais_canais["funnel_routing"]` (JSONB), sem LLM
nem busca vetorial. Estrutura esperada:

{
  "match_order": "tags_first" | "etapa_first",
  "rules": [
    {
      "especialista_id": "<uuid>",
      "tag_ids": ["<uuid>", ...],
      "etapa_ids": ["<uuid>", ...],
      "ordem": 100
    }
  ],
  "default_especialista_id": "<uuid> | null"
}

Regras com mais peso em `ordem` vencem empates. Uma regra só aplica se, para cada eixo
definido (tags / etapas), houver interseção com o lead; eixo omitido ou lista vazia = não restringe.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


def normalizar_uuid_str(valor: Any) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        return str(uuid.UUID(texto)).lower()
    except (ValueError, TypeError):
        return None


def normalizar_conjunto_uuid(valores: Iterable[Any] | None) -> frozenset[str]:
    saida: set[str] = set()
    for item in valores or ():
        u = normalizar_uuid_str(item)
        if u:
            saida.add(u)
    return frozenset(saida)


@dataclass(frozen=True, slots=True)
class RegraFunil:
    especialista_id: str
    tag_ids: frozenset[str]
    etapa_ids: frozenset[str]
    ordem: int


def _parse_regras_de_credenciais(block: Mapping[str, Any] | None) -> list[RegraFunil]:
    if not isinstance(block, dict):
        return []
    raw_rules = block.get("rules")
    if not isinstance(raw_rules, list):
        return []
    regras: list[RegraFunil] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        esp = normalizar_uuid_str(item.get("especialista_id"))
        if not esp:
            continue
        try:
            ordem = int(item.get("ordem", 0) or 0)
        except (TypeError, ValueError):
            ordem = 0
        tag_ids = normalizar_conjunto_uuid(item.get("tag_ids"))
        etapa_ids = normalizar_conjunto_uuid(item.get("etapa_ids"))
        regras.append(RegraFunil(especialista_id=esp, tag_ids=tag_ids, etapa_ids=etapa_ids, ordem=ordem))
    return regras


def parse_funil_routing_credenciais(credenciais: Mapping[str, Any] | None) -> tuple[list[RegraFunil], str | None, str]:
    """
    Retorna (regras, default_especialista_id, match_order).
    """
    cred = credenciais if isinstance(credenciais, dict) else {}
    block = cred.get("funnel_routing")
    if not isinstance(block, dict):
        return [], None, "tags_first"
    regras = _parse_regras_de_credenciais(block)
    default_id = normalizar_uuid_str(block.get("default_especialista_id"))
    ordem_raw = str(block.get("match_order") or "tags_first").strip().lower()
    if ordem_raw not in {"tags_first", "etapa_first"}:
        ordem_raw = "tags_first"
    return regras, default_id, ordem_raw


def _regra_casa(
    regra: RegraFunil,
    etapa_id: str | None,
    tags_lead: frozenset[str],
) -> bool:
    if regra.tag_ids:
        if not (regra.tag_ids & tags_lead):
            return False
    if regra.etapa_ids:
        if not etapa_id or etapa_id not in regra.etapa_ids:
            return False
    return True


def _pontuacao_regra(regra: RegraFunil, etapa_id: str | None, tags_lead: frozenset[str], prioridade_eixo: str) -> tuple[int, int, int, int]:
    """
    Tupla para ordenação determinística (maior = melhor).
    """
    tag_hit = 1 if (regra.tag_ids and (regra.tag_ids & tags_lead)) else 0
    etapa_hit = 1 if (regra.etapa_ids and etapa_id and etapa_id in regra.etapa_ids) else 0
    if prioridade_eixo == "etapa_first":
        eixo_bonus = etapa_hit * 1000 + tag_hit * 100
    else:
        eixo_bonus = tag_hit * 1000 + etapa_hit * 100
    especificidade = len(regra.tag_ids) + len(regra.etapa_ids)
    return (regra.ordem, eixo_bonus, especificidade, tag_hit + etapa_hit)


def resolver_especialista_top1_por_funil(
    etapa_id: Any,
    lead_tags_cru: list[Any] | None,
    regras: Sequence[RegraFunil],
    default_especialista_id: str | None,
    match_order: str,
    ids_especialistas_ativos: frozenset[str],
    fallback_por_peso: Sequence[tuple[str, int]],
) -> str | None:
    """
    Mapeia etapa_id (UUID do lead) e tags (UUIDs em lead.tags) para um único especialista_id.

    - `fallback_por_peso`: sequência (especialista_id, peso_prioridade) já filtrada (ex.: sem porteiro).
    """
    etapa_norm = normalizar_uuid_str(etapa_id)
    tags_lead = normalizar_conjunto_uuid(lead_tags_cru)

    candidatos: list[RegraFunil] = [r for r in regras if _regra_casa(r, etapa_norm, tags_lead)]
    if candidatos:
        candidatos.sort(
            key=lambda r: _pontuacao_regra(r, etapa_norm, tags_lead, match_order),
            reverse=True,
        )
        escolhido = candidatos[0].especialista_id
        if escolhido in ids_especialistas_ativos:
            return escolhido

    if default_especialista_id and default_especialista_id in ids_especialistas_ativos:
        return default_especialista_id

    if not fallback_por_peso:
        return None
    ordenado = sorted(fallback_por_peso, key=lambda item: int(item[1] or 0), reverse=True)
    return ordenado[0][0] if ordenado else None
