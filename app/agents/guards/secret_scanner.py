from __future__ import annotations

import re


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-+/=]{10,}", re.IGNORECASE)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    ("api_key_header", re.compile(r"\b(api[-_]?key|authorization|x-api-key)\s*[:=]\s*\S+", re.IGNORECASE)),
)


def scan_secrets(text: str | None) -> list[str]:
    raw = str(text or "")
    return [code for code, pattern in SECRET_PATTERNS if pattern.search(raw)]
