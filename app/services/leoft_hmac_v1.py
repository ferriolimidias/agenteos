"""Shared hardened HMAC v1 signing input for AgentOS and Hermes."""

from __future__ import annotations


def build_hmac_v1_signing_input(
    *,
    method: str,
    path: str,
    event: str,
    timestamp: str,
    idempotency_key: str,
    key_id: str,
    body: bytes,
) -> bytes:
    """Bind routing/auth headers while preserving the body bytes exactly."""
    header_lines = (method.upper(), path, event, timestamp, idempotency_key, key_id)
    if any("\n" in value or "\r" in value for value in header_lines):
        raise ValueError("invalid_hmac_header_value")
    return b"\n".join(value.encode("utf-8") for value in header_lines) + b"\n" + body
