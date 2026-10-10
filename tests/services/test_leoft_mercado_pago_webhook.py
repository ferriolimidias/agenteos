import hashlib
import hmac
import json

from app.services.mercado_pago.leoft_webhook import extract_payment_id, validate_signature


def _signed(body: bytes, secret: str, request_id: str = "req-1") -> str:
    data = json.loads(body)
    payment_id = data["data"]["id"]
    ts = "1700000000"
    manifest = f"id:{payment_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={digest}"


def test_valid_signature_and_payment_id():
    body = json.dumps({"type": "payment", "data": {"id": "pay-1"}}).encode()
    assert validate_signature(raw_body=body, x_signature=_signed(body, "secret"), x_request_id="req-1", secret="secret")
    assert extract_payment_id(json.loads(body)) == "pay-1"


def test_invalid_signature_rejected():
    body = b'{"type":"payment","data":{"id":"pay-1"}}'
    assert not validate_signature(raw_body=body, x_signature="ts=1,v1=bad", x_request_id="req-1", secret="secret")
