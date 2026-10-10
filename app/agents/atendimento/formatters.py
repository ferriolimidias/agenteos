from __future__ import annotations

import re

from app.agents.schemas import ConversationState


def first_person_name(name: str | None) -> str:
    cleaned = re.split(r"\s*[|/@-]\s*", str(name or "").strip(), maxsplit=1)[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    first = cleaned.split(" ")[0].strip(" ,.;:")
    if len(first) < 2 or any(char.isdigit() for char in first):
        return ""
    return first


def format_for_whatsapp(text: str, state: ConversationState) -> str:
    formatted = str(text or "").strip()
    if not formatted:
        return ""
    formatted = _remove_commercial_full_name(formatted, state)
    formatted = _normalize_paragraphs(formatted)
    formatted = _normalize_simple_labels(formatted)
    formatted = _bold_money(formatted)
    formatted = _bold_measurements(formatted)
    formatted = _normalize_whatsapp_emphasis(formatted)
    return formatted.strip()


def _remove_commercial_full_name(text: str, state: ConversationState) -> str:
    raw_name = str(state.nome or "").strip()
    if not raw_name or "|" not in raw_name:
        return text
    first = first_person_name(raw_name)
    if first:
        text = text.replace(raw_name, first)
    else:
        text = text.replace(f", {raw_name}", "").replace(raw_name, "")
    return text


def _normalize_paragraphs(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(
        r"(\*R\$ ?\d+(?:[,.]\d+)?\*|R\$ ?\d+(?:[,.]\d+)?\.)\s+(Esse é um orçamento)",
        r"\1\n\n\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(local\.)\s+(Quer que eu te ajude)",
        r"\1\n\n\2",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _normalize_simple_labels(text: str) -> str:
    for label in ("Nome", "Endereço ou bairro", "Melhor dia", "Melhor turno"):
        text = re.sub(rf"(?<!\*)\b{re.escape(label)}\b(?!\*)", f"*{label}*", text, flags=re.IGNORECASE)
    return text


def _bold_money(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        prefix = text[max(0, match.start() - 16):match.start()].lower()
        if "total prévio:" in prefix or "total previo:" in prefix:
            return match.group(1)
        return f"*{match.group(1)}*"

    return re.sub(r"(?<!\*)\b(R\$ ?\d+(?:[,.]\d+)?)\b(?!\*)", repl, text)


def _bold_measurements(text: str) -> str:
    return re.sub(
        r"(?<![\w*])(\d+(?:[,.]\d+)?\s*(?:m|metro|metros))\b(?!\*)",
        r"*\1*",
        text,
        flags=re.IGNORECASE,
    )


def _normalize_whatsapp_emphasis(text: str) -> str:
    """Keep emphasis pairs compatible with WhatsApp without changing content."""
    text = re.sub(r"\*{2,}([^*\n]+)\*{1,}", r"*\1*", text)
    lines = []
    for line in text.splitlines():
        lines.append(line if line.count("*") % 2 == 0 else line.replace("*", ""))
    return "\n".join(lines)
