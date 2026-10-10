from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.api.time_serialization import serialize_utc_timestamp


def _display(value: str, zone: str = "America/Sao_Paulo") -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(ZoneInfo(zone)).strftime("%H:%M")


def test_brazil_timezone_display():
    assert _display("2026-08-24T12:00:00Z") == "09:00"


def test_utc_to_sao_paulo_display():
    assert _display("2026-08-24T12:00:00Z", "America/Sao_Paulo") == "09:00"


def test_offset_aware_timestamp():
    assert _display("2026-08-24T09:00:00-03:00") == "09:00"


def test_no_double_timezone_conversion():
    value = datetime(2026, 8, 24, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert serialize_utc_timestamp(value) == "2026-08-24T12:00:00Z"
    assert _display(serialize_utc_timestamp(value)) == "09:00"


def test_timezone_date_boundary():
    assert _display("2026-08-25T02:00:00Z") == "23:00"
    parsed = datetime.fromisoformat("2026-08-25T02:00:00+00:00").astimezone(ZoneInfo("America/Sao_Paulo"))
    assert parsed.date().isoformat() == "2026-08-24"


def test_inbox_api_serializes_explicit_utc_and_does_not_format_raw_strings():
    source = Path("app/api/routers/inbox.py").read_text()
    assert "serialize_utc_timestamp" in source
    assert '"criado_em": msg.criado_em.isoformat()' not in source
    assert 'split("T")' not in source
    assert serialize_utc_timestamp(datetime(2026, 8, 24, 11, 39, 44, 709829)) == "2026-08-24T11:39:44.709829Z"
