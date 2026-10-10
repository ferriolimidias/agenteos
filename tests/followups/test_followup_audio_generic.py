from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.followup_rules import FollowupRuleSpec
from app.services.media_storage import MediaValidationError, detect_audio_mime, resolve_storage_path, validate_audio_bytes
from app.services.meta_templates import parse_template_components
from app.services.channels.contracts import ChannelConnectionContext, ChannelOutboundMessage
from app.services.channels.providers.meta_whatsapp import MetaWhatsAppProvider
from app.services.channels.types import Channel, Provider
from app.services.outbox_service import deterministic_idempotency_key


def test_legacy_followup_defaults_to_text():
    spec = FollowupRuleSpec.from_payload(SimpleNamespace(model_fields_set=set(), message_content="hello"))
    assert spec.content_type == "text"
    assert spec.media_asset_id is None


def test_audio_and_text_audio_are_tenant_neutral():
    audio = FollowupRuleSpec.from_payload(SimpleNamespace(model_fields_set={"content_type", "media_asset_id"}, content_type="audio", media_asset_id="asset-a"))
    combined = FollowupRuleSpec.from_payload(SimpleNamespace(model_fields_set={"content_type", "media_asset_id", "send_as_voice"}, content_type="text_audio", media_asset_id="asset-b", send_as_voice=True))
    assert audio.content_type == "audio"
    assert combined.content_type == "text_audio"
    assert combined.send_as_voice is True


def test_audio_validation_rejects_mismatch_and_size():
    assert detect_audio_mime(b"OggS" + b"0", "audio/ogg", "voice.ogg") == "audio/ogg"
    with pytest.raises(MediaValidationError):
        validate_audio_bytes(b"not-audio", declared_mime="application/pdf", original_name="x.pdf")
    with pytest.raises(MediaValidationError):
        validate_audio_bytes(b"x" * (16 * 1024 * 1024 + 1), declared_mime="audio/ogg", original_name="x.ogg")


def test_storage_path_is_tenant_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_ROOT", str(tmp_path))
    safe = tmp_path / "tenant-a" / "audio" / "a.ogg"
    safe.parent.mkdir(parents=True)
    safe.write_bytes(b"OggS")
    assert resolve_storage_path("tenant-a/audio/a.ogg", empresa_id="tenant-a") == safe.resolve()
    with pytest.raises(MediaValidationError):
        resolve_storage_path("tenant-b/audio/a.ogg", empresa_id="tenant-a")


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


class FakeTransport:
    def __init__(self):
        self.uploads = []
        self.posts = []

    async def post_multipart(self, url, *, headers, data, files):
        self.uploads.append((url, data, files))
        return FakeResponse({"id": "meta-media-1"})

    async def post(self, url, *, headers, json):
        self.posts.append((url, json))
        return FakeResponse({"messages": [{"id": "wamid-test"}]})

    async def get(self, url, *, headers, params=None):
        return FakeResponse({})


@pytest.mark.asyncio
async def test_meta_audio_upload_and_voice_payload_are_mocked(tmp_path):
    path = tmp_path / "audio.ogg"
    path.write_bytes(b"OggS-test")
    connection = SimpleNamespace(phone_number_id="phone-test", credenciais={"access_token": "token-test"})
    context = ChannelConnectionContext(
        empresa_id="tenant-a", connection_id="connection-a", channel=Channel.WHATSAPP,
        provider=Provider.META_WHATSAPP, status="connected", external_account_id="phone-test",
    )
    transport = FakeTransport()
    provider = MetaWhatsAppProvider(context, connection=connection, transport=transport)
    result = await provider.send_audio(ChannelOutboundMessage(
        empresa_id="tenant-a", connection_id="connection-a", channel=Channel.WHATSAPP,
        provider=Provider.META_WHATSAPP, destination="contact-test", message_type="audio",
        text="caption", media={"path": str(path), "mime_type": "audio/ogg", "filename": "audio.ogg"},
        metadata={"send_as_voice": True},
    ))
    assert result["external_message_id"] == "wamid-test"
    assert transport.uploads and transport.posts
    assert transport.posts[0][1]["audio"] == {"id": "meta-media-1", "voice": True, "caption": "caption"}


def test_idempotency_separates_text_and_audio_parts():
    execution = "execution-test"
    assert deterministic_idempotency_key("followup_outbox", execution, "text") != deterministic_idempotency_key("followup_outbox", execution, "audio")
    assert deterministic_idempotency_key("followup_outbox", execution, "audio") == deterministic_idempotency_key("followup_outbox", execution, "audio")


def test_template_payload_is_not_audio_authorization():
    parsed = parse_template_components([])
    assert parsed.requires_media is False
