"""Substituição determinística de placeholders em prompts (sem depender do LLM)."""

from __future__ import annotations

import re

# Quando não há nome utilizável: o painel usava `{nome_lead}`; injetamos o marcador
# literal para o modelo deduzir pelo contexto (pedido de produto).
_MARCADOR_IA_DEDUZ_NOME = "[Nome]"

_SEPARADORES_PERFIL_EMPRESA = ("|", ",", "_")

# Segundo token que não é nome de pessoa (razão social / sufixo).
_SUFIXOS_JURIDICOS = frozenset(
    {
        "me",
        "epp",
        "ltda",
        "ss",
        "sa",
        "eireli",
        "cia",
        "cnpj",
        "ltda.",
        "s.a.",
        "s/a",
    }
)


def _apenas_digitos_apos_limpeza_telefone(s: str) -> bool:
    """True se, após remover formatação típica de telefone, só restarem dígitos."""
    compacto = re.sub(r"[\s\-\(\)\+\.]", "", s)
    if not compacto:
        return True
    return compacto.isdigit()


def _parte_esquerda_perfil_empresarial(nome: str) -> str:
    """
    Perfis de WhatsApp costumam usar `|`, `,`, `_` ou traço com espaços para
    separar pessoa de empresa. Fica só o trecho da esquerda.
    Traço sem espaços (ex.: Ana-Maria) não é cortado aqui.
    """
    s = nome.strip()
    for sep in _SEPARADORES_PERFIL_EMPRESA:
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    for pattern in (r"\s+–\s+", r"\s+—\s+", r"\s+-\s+"):
        m = re.search(pattern, s)
        if m:
            s = s[: m.start()].strip()
            break
    return s


def _parece_fragmento_nome_proprio(token: str) -> bool:
    """Letras (incl. acentuadas), apóstrofo ou hífen interno; sem dígitos nem underscore."""
    if not token or any(ch.isdigit() for ch in token):
        return False
    for ch in token:
        if ch.isalpha() or ch in "'-":
            continue
        return False
    return True


def _primeiro_nome_ou_duplo_curto(partes: list[str]) -> str:
    """Primeiro token, ou primeiro + segundo se o segundo parecer nome curto."""
    if not partes:
        return ""
    primeiro = partes[0]
    if len(partes) == 1:
        return primeiro
    segundo = partes[1]
    segundo_l = segundo.lower().rstrip(".")
    if segundo_l in _SUFIXOS_JURIDICOS or len(segundo) > 15:
        return primeiro
    if not _parece_fragmento_nome_proprio(segundo):
        return primeiro
    return f"{primeiro} {segundo}"


def normalizar_nome_lead_para_prompt(nome_lead: str | None) -> str:
    """
    Nome “limpo” para injetar no prompt (primeiro nome ou dois curtos).
    Se vazio ou só telefone/dígitos, devolve o literal `[Nome]` para a IA deduzir.
    """
    bruto = (nome_lead or "").strip()
    if not bruto or _apenas_digitos_apos_limpeza_telefone(bruto):
        return _MARCADOR_IA_DEDUZ_NOME

    segmento = _parte_esquerda_perfil_empresarial(bruto)
    if not segmento or _apenas_digitos_apos_limpeza_telefone(segmento):
        return _MARCADOR_IA_DEDUZ_NOME

    partes = [p for p in re.split(r"\s+", segmento.strip()) if p]
    if not partes:
        return _MARCADOR_IA_DEDUZ_NOME

    limpo = _primeiro_nome_ou_duplo_curto(partes)
    if not limpo or _apenas_digitos_apos_limpeza_telefone(limpo):
        return _MARCADOR_IA_DEDUZ_NOME
    toks_limpo = limpo.split()
    if not toks_limpo or not _parece_fragmento_nome_proprio(toks_limpo[0]):
        return _MARCADOR_IA_DEDUZ_NOME
    if len(toks_limpo) > 1 and not _parece_fragmento_nome_proprio(toks_limpo[1]):
        return _MARCADOR_IA_DEDUZ_NOME
    return limpo


def substituir_placeholders_nome_lead_em_texto(texto: str, nome_lead: str) -> str:
    """
    Injeta o nome do lead normalizado no texto antes do envio ao modelo.
    Painéis usam `{nome_lead}`; `[Nome]` legado recebe o mesmo valor resolvido.
    """
    if texto is None:
        return ""
    valor = normalizar_nome_lead_para_prompt(nome_lead)
    s = str(texto)
    s = s.replace("{nome_lead}", valor)
    s = s.replace("[Nome]", valor)
    return s
