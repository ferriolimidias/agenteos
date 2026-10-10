import hashlib
import hmac
import json
import os
import time
import unittest
from uuid import uuid4

from starlette.requests import Request
from sqlalchemy import delete, select

from app.api.routers.leoft_internal import import_catalog_from_hermes
from app.services.leoft_internal_auth import require_hermes_catalog_hmac
from db.database import AsyncSessionLocal
from db.database import engine
from db.models import LeoFTIntegrationEvent
from scripts.import_leoft_catalog import validate_catalog_document, import_catalog_document


SECRET = "catalog-endpoint-test-secret"


def request_for(payload: dict, *, timestamp: int | None = None, signature: str | None = None, event: str = "leoft.catalog.import", key: str = "catalog-test-idempotency") -> Request:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    stamp = str(timestamp or int(time.time()))
    signed = signature or hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    headers = [
        (b"x-hermes-event", event.encode()),
        (b"x-hermes-timestamp", stamp.encode()),
        (b"x-hermes-signature", signed.encode()),
        (b"x-hermes-idempotency-key", key.encode()),
        (b"content-type", b"application/json"),
    ]
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/api/internal/hermes/leoft/catalog/import", "headers": headers, "query_string": b""}, receive)


def catalog_payload(*, version: str = "endpoint-test-v1", source_hash: str | None = None) -> dict:
    return {
        "event_type": "leoft.catalog.import",
        "schema_version": "1.0",
        "catalog_version": version,
        "source_sha256": source_hash or "b" * 64,
        "generated_at": "2026-07-31T00:00:00Z",
        "source": "hermes",
        "expected_counts": {"entries": 1, "media": 1, "aliases": 1, "signatures": 1, "states": {"approved_by_ai": 1, "approved_manually": 0, "pending_review": 0, "unidentified": 0, "rejected": 0, "duplicate": 0}},
        "entries": [{
            "catalog_id": "endpoint-test-entry",
            "canonical_name": "Rosca de teste",
            "approval_status": "approved_by_ai",
            "equipment": "cable",
            "aliases": ["teste rosca"],
            "media": [{"media_id": "endpoint-test-media", "storage_path": "catalog/endpoint-test.mp4", "sha256": "c" * 64}],
            "signatures": [{"signature_key": "endpoint-test-signature", "signature_version": "v1", "signature_payload": {"movement": "curl"}}],
        }],
    }


class LeoFTCatalogImportEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ["HERMES_LEOFT_HMAC_SECRET"] = SECRET
        await engine.dispose()

    async def test_hmac_valid_invalid_expired_and_event_direction(self):
        payload = catalog_payload()
        valid = await require_hermes_catalog_hmac(request_for(payload))
        self.assertEqual(valid[1], hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        with self.assertRaises(Exception):
            await require_hermes_catalog_hmac(request_for(payload, signature="invalid"))
        with self.assertRaises(Exception):
            await require_hermes_catalog_hmac(request_for(payload, timestamp=int(time.time()) - 9999))
        with self.assertRaises(Exception):
            await require_hermes_catalog_hmac(request_for(payload, event="leoft.payment_approved"))

    async def test_dry_run_records_only_sanitized_integration_event(self):
        key = "catalog-dry-run-" + uuid4().hex
        payload = catalog_payload()
        raw = request_for(payload, key=key)
        async with AsyncSessionLocal() as db:
            auth = await require_hermes_catalog_hmac(raw)
            response = await import_catalog_from_hermes(raw, dry_run=True, auth=auth, db=db)
            self.assertTrue(response["dry_run"])
            self.assertEqual(response["counts"]["entries"], 1)
            self.assertIsNotNone(await db.scalar(select(LeoFTIntegrationEvent.id).where(LeoFTIntegrationEvent.idempotency_key == key)))
            self.assertEqual(await db.scalar(select(LeoFTIntegrationEvent.request_payload_sanitized).where(LeoFTIntegrationEvent.idempotency_key == key)), {
                "event_type": "leoft.catalog.import", "catalog_version": payload["catalog_version"], "source_sha256": payload["source_sha256"], "counts": response["counts"], "dry_run": True,
            })
            duplicate = await import_catalog_from_hermes(raw, dry_run=True, auth=auth, db=db)
            self.assertTrue(duplicate["idempotent"])
            changed = catalog_payload()
            changed["generated_at"] = "2026-07-31T00:00:01Z"
            changed_request = request_for(changed, key=key)
            changed_auth = await require_hermes_catalog_hmac(changed_request)
            with self.assertRaises(Exception):
                await import_catalog_from_hermes(changed_request, dry_run=True, auth=changed_auth, db=db)
            await db.execute(delete(LeoFTIntegrationEvent).where(LeoFTIntegrationEvent.idempotency_key == key))
            await db.commit()

    async def test_transactional_import_rolls_back_without_real_fixture(self):
        payload = catalog_payload(version="endpoint-rollback-" + uuid4().hex)
        async with AsyncSessionLocal() as db:
            transaction = await db.begin()
            try:
                result = await import_catalog_document(payload, payload["source_sha256"], db)
                self.assertFalse(result["idempotent"])
                self.assertEqual(await db.scalar(select(LeoFTIntegrationEvent.id).where(LeoFTIntegrationEvent.idempotency_key == "never-written")), None)
            finally:
                await transaction.rollback()
        async with AsyncSessionLocal() as verify:
            from sqlalchemy import text
            self.assertEqual(await verify.scalar(text("select count(*) from leoft_catalog_entries where catalog_id='endpoint-test-entry'")), 0)

    def test_schema_and_security_rejections(self):
        invalid = catalog_payload()
        invalid["schema_version"] = "9.0"
        with self.assertRaises(ValueError):
            validate_catalog_document(invalid, enforce_envelope=True)
        invalid = catalog_payload()
        invalid["entries"][0]["media"][0]["storage_path"] = "../escape.mp4"
        with self.assertRaises(ValueError):
            validate_catalog_document(invalid, enforce_envelope=True)
        invalid = catalog_payload()
        invalid["source_sha256"] = "not-a-hash"
        with self.assertRaises(ValueError):
            validate_catalog_document(invalid, enforce_envelope=True)


if __name__ == "__main__":
    unittest.main()
