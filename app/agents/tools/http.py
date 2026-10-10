from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


BLOCKED_HOSTS = {
    "localhost",
    "host.docker.internal",
    "gateway.docker.internal",
}


def is_dangerous_http_url(url: str | None, allowlist: set[str] | None = None) -> tuple[bool, list[str]]:
    allowlist = allowlist or set()
    reasons: list[str] = []
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").strip().lower()

    if not parsed.scheme or not host:
        return True, ["invalid_url"]
    if parsed.scheme != "https":
        reasons.append("non_https")
    if host in allowlist:
        return False, reasons
    if host in BLOCKED_HOSTS or host.endswith(".internal"):
        reasons.append("blocked_internal_host")

    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None

    if ip:
        if ip.is_loopback:
            reasons.append("loopback_ip")
        if ip.is_private:
            reasons.append("private_ip")
        if ip.is_link_local:
            reasons.append("link_local_ip")
        if ip.is_unspecified:
            reasons.append("unspecified_ip")
        if str(ip) == "169.254.169.254":
            reasons.append("metadata_endpoint")

    dangerous = any(reason != "non_https" for reason in reasons)
    return dangerous, reasons
