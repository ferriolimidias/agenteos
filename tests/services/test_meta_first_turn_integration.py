import asyncio
import json

from starlette.requests import Request

from app.api.routers import meta_webhook
from app.services.meta_webhook import MetaWebhookProcessResult


def test_meta_official_entrypoint_passes_event_to_persist_and_debouncer(monkeypatch):
    captured = {}

    async def fake_process(raw_body, signature, app_secret, **kwargs):
        captured["raw_body"] = raw_body
        captured["handler"] = kwargs["handle_event"]
        return MetaWebhookProcessResult(received=1, accepted=1, duplicates=0, unresolved=0)

    monkeypatch.setattr(meta_webhook, "process_meta_webhook", fake_process)
    payload = {"object": "whatsapp_business_account", "entry": []}

    async def receive():
        return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/webhook/meta",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
            "root_path": "",
        },
        receive,
    )
    asyncio.run(meta_webhook._receive(request))
    assert captured["raw_body"] == json.dumps(payload).encode()
    assert callable(captured["handler"])


def test_meta_first_turn_contract_excludes_persisted_current_message_from_history_count():
    from pathlib import Path

    source = (Path(__file__).parents[2] / "app/api/utils.py").read_text()
    assert '"persisted_message_id"' in source
    assert "_MH.id.not_in(persisted_current_ids)" in source
    assert '"first_agent_response": first_agent_response_from_origins(origins_found)' in source
    assert '"previous_ai_outbound_count"' in source
    from app.api.utils import first_agent_response_from_origins

    assert first_agent_response_from_origins(["cliente"]) is True
    assert first_agent_response_from_origins(["ia"]) is False
