from __future__ import annotations

import re

from app.agents.tools.http import is_dangerous_http_url


URL_PATTERN = re.compile(r"https?://[^\s<>)\"']+", re.IGNORECASE)


def scan_unsafe_links(text: str | None) -> list[str]:
    reasons: list[str] = []
    for url in URL_PATTERN.findall(str(text or "")):
        dangerous, url_reasons = is_dangerous_http_url(url)
        if dangerous:
            reasons.extend([f"url:{reason}" for reason in url_reasons])
    return reasons
