from __future__ import annotations

import re
from typing import Iterable


MAX_SEGMENTS = 3


def segment_whatsapp_response(text: str, *, context: dict | None = None) -> list[str]:
    """
    Segmenta a resposta final em blocos visuais para WhatsApp.

    A função não compõe texto novo: ela apenas normaliza espaços e escolhe pontos
    de quebra entre frases já existentes.
    """
    del context
    original = str(text or "").strip()
    if not original:
        return []

    planned_blocks = _split_planned_sequence(original)
    if planned_blocks:
        return planned_blocks

    cleaned = _normalize_spaces(original)
    if not cleaned:
        return []

    if _looks_like_raw_json(cleaned) or _has_structured_list(original):
        return [cleaned]

    sentences = _split_sentences(cleaned)
    if len(sentences) <= 1:
        return [cleaned]

    segments = _segment_sentences(sentences)
    segments = _merge_fragile_segments(segments)
    segments = _limit_segments(segments, MAX_SEGMENTS)
    segments = [segment.strip() for segment in segments if segment.strip()]

    if not segments:
        return [cleaned]
    if _content_changed(cleaned, segments):
        return [cleaned]
    return segments


def _split_planned_sequence(text: str) -> list[str]:
    """
    Preserva sequências planejadas pelo prompt do tenant.

    Quando o LLM devolve blocos rotulados como "Mensagem 1:", "Mensagem 2:"
    etc., cada bloco vira uma mensagem separada e o rótulo técnico não é
    enviado ao WhatsApp.
    """
    matches = list(re.finditer(r"(?im)^\s*\.?\s*Mensagem\s+\d+\s*:\s*$", text))
    if len(matches) < 2:
        return []

    blocks: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        block = re.sub(r"\n{3,}", "\n\n", block)
        if block:
            blocks.append(block)

    if len(blocks) < 2:
        return []
    return blocks


def _normalize_spaces(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n+\s*", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def _looks_like_raw_json(text: str) -> bool:
    stripped = text.strip()
    return (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    )


def _has_structured_list(text: str) -> bool:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    list_lines = 0
    for line in lines:
        if re.match(r"^(\d+[\).\-\s]|[-*•]\s+)", line):
            list_lines += 1
    return list_lines >= 2


def _split_sentences(text: str) -> list[str]:
    protected = _protect_abbreviations(text)
    matches = re.findall(r".+?(?:[.!?]+(?=\s|$)|$)", protected)
    return [_restore_abbreviations(part).strip() for part in matches if part.strip()]


def _protect_abbreviations(text: str) -> str:
    replacements = {
        "R$": "R§",
        "Sr.": "Sr§",
        "Sra.": "Sra§",
        "Dr.": "Dr§",
        "Dra.": "Dra§",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _restore_abbreviations(text: str) -> str:
    replacements = {
        "R§": "R$",
        "Sr§": "Sr.",
        "Sra§": "Sra.",
        "Dr§": "Dr.",
        "Dra§": "Dra.",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _segment_sentences(sentences: list[str]) -> list[str]:
    if _is_greeting_pair(sentences[:2]):
        rest = sentences[2:]
        segments = [_join(sentences[:2])]
        if rest:
            if _is_final_short_question(rest[-1]) and len(rest) > 1:
                segments.append(_join(rest[:-1]))
                segments.append(rest[-1])
            else:
                segments.append(_join(rest))
        return segments

    if _contains_budget_signal(sentences):
        first_group: list[str] = []
        remaining = list(sentences)
        while remaining:
            first_group.append(remaining.pop(0))
            if _has_money_or_measure(first_group[-1]):
                break
        if remaining:
            return [_join(first_group), _join(remaining)]
        return [_join(first_group)]

    if _is_short_intro(sentences[0]) and len(sentences) > 1:
        return [sentences[0], _join(sentences[1:])]

    if _is_final_short_question(sentences[-1]) and len(sentences) > 1:
        return [_join(sentences[:-1]), sentences[-1]]

    midpoint = max(1, len(sentences) // 2)
    return [_join(sentences[:midpoint]), _join(sentences[midpoint:])]


def _merge_fragile_segments(segments: list[str]) -> list[str]:
    stable: list[str] = []
    for segment in segments:
        if not segment.strip():
            continue
        if _is_fragile_segment(segment) and stable:
            stable[-1] = _join([stable[-1], segment])
        else:
            stable.append(segment)
    if len(stable) > 1 and _is_fragile_segment(stable[0]):
        stable[1] = _join([stable[0], stable[1]])
        stable = stable[1:]
    return stable


def _limit_segments(segments: list[str], limit: int) -> list[str]:
    limited = [segment for segment in segments if segment.strip()]
    while len(limited) > limit:
        first_len = len(limited[0])
        last_len = len(limited[-1])
        if first_len <= last_len:
            limited[1] = _join([limited[0], limited[1]])
            limited = limited[1:]
        else:
            limited[-2] = _join([limited[-2], limited[-1]])
            limited.pop()
    return limited


def _join(parts: Iterable[str]) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip()).strip()


def _is_greeting_pair(sentences: list[str]) -> bool:
    if len(sentences) < 2:
        return False
    first = _plain(sentences[0])
    second = _plain(sentences[1])
    return first in {"ola", "oi", "bom dia", "boa tarde", "boa noite"} and (
        second in {"tudo bem", "como vai"} or second.startswith("tudo bem")
    )


def _is_short_intro(sentence: str) -> bool:
    plain = _plain(sentence)
    return len(sentence) <= 45 and (
        plain.startswith("consigo te ajudar")
        or plain.startswith("perfeito")
        or plain.startswith("claro")
        or plain.startswith("entendi")
    )


def _is_final_short_question(sentence: str) -> bool:
    stripped = sentence.strip()
    return stripped.endswith("?") and len(stripped) <= 90


def _contains_budget_signal(sentences: list[str]) -> bool:
    joined = " ".join(sentences).lower()
    return bool(re.search(r"\br\$ ?\d", joined)) or bool(
        re.search(r"\b\d+(?:[,.]\d+)?\s*(?:m|metro|metros)\b", joined, flags=re.IGNORECASE)
    )


def _has_money_or_measure(sentence: str) -> bool:
    return bool(re.search(r"\bR\$ ?\d", sentence, flags=re.IGNORECASE)) or bool(
        re.search(r"\b\d+(?:[,.]\d+)?\s*(?:m|metro|metros)\b", sentence, flags=re.IGNORECASE)
    )


def _is_fragile_segment(segment: str) -> bool:
    plain = _plain(segment)
    if len(segment.strip()) <= 12:
        return True
    fragile_phrases = (
        "vou calcular",
        "vou verificar",
        "vou conferir",
        "ja calculo",
        "já calculo",
        "calculo para voce",
        "calculo para você",
    )
    return any(phrase in plain for phrase in fragile_phrases) and not _has_money_or_measure(segment)


def _plain(text: str) -> str:
    normalized = text.strip().lower().replace("*", "")
    normalized = normalized.rstrip(".!?;:")
    accents = str.maketrans("áàãâéêíóôõúç", "aaaaeeiooouc")
    return normalized.translate(accents)


def _content_changed(cleaned: str, segments: list[str]) -> bool:
    return _semantic_view(cleaned) != _semantic_view(" ".join(segments))


def _semantic_view(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("*", "")).strip()
