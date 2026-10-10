import pytest

from app.api.routers.instagram_simple import _graph_error
from app.services.instagram_simple import InstagramGraphError


def test_meta_error_190_is_returned_sanitized():
    error = _graph_error(InstagramGraphError("Meta", status_code=400, code=190, meta_message="OAuth exception"))
    assert error.status_code == 422
    assert error.detail["meta_code"] == 190
    assert "190" in error.detail["message"]
    assert "token" in error.detail["message"].lower()
    assert "OAuth exception" not in error.detail["message"]


def test_n8n_transport_contract_is_documented_in_service_source():
    from pathlib import Path
    source = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
    assert 'params={"access_token": self.access_token, "fields": "id,username"}' in source
    identify_section = source.split("async def identify", 1)[1].split("async def media", 1)[0]
    assert '"me/permissions"' not in identify_section
    assert '"GET",\n            "me"' not in identify_section
    assert "graph.facebook.com" not in identify_section


def test_signed_comment_processing_does_not_require_remote_owner_or_sender_match():
    from pathlib import Path
    source = Path("app/services/instagram_simple.py").read_text(encoding="utf-8")
    process = source.split("async def process_comment", 1)[1].split("async def process_direct_message", 1)[0]
    assert "comment_payload_has_matching_fields" in process
    assert "Optional Instagram comment Graph lookup failed" in process
    assert "remote_owner" not in process
    assert "remote_from.get(\"id\") != sender_id" not in process
