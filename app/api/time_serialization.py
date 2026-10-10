from __future__ import annotations

from datetime import datetime, timezone


def serialize_utc_timestamp(value: datetime | None) -> str | None:
    """Serialize legacy UTC-naive datetimes with an explicit API timezone."""
    if value is None:
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")
