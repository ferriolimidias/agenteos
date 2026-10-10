from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request
from sqlalchemy import select

from app.schemas_leoft_catalog import CatalogAssociationRequest, CatalogSearchFilters, CatalogSearchRequest
from app.services.leoft_catalog_read_service import get_entry, get_media, get_meta, search_catalog, validate_association
from app.services.leoft_internal_auth import require_hermes_catalog_read_hmac
from db.database import AsyncSessionLocal, engine
from db.models import LeoFTCatalogEntry, LeoFTCatalogMedia


SECRET = "catalog-read-test-secret"


def request_for(body: bytes = b"", *, method: str = "GET", event: str = "leoft.catalog.read.meta", timestamp: int | None = None, signature: str | None = None, key: str = "read-test-key") -> Request:
    stamp = str(timestamp or int(time.time()))
    signed = signature or hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = [
        (b"x-hermes-event", event.encode()),
        (b"x-hermes-timestamp", stamp.encode()),
        (b"x-hermes-signature", signed.encode()),
        (b"x-hermes-idempotency-key", key.encode()),
        (b"content-type", b"application/json"),
    ]

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": method, "path": "/api/internal/hermes/leoft/catalog/v1/meta", "headers": headers, "query_string": b""}, receive)


class LeoFTCatalogReadApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ["HERMES_LEOFT_HMAC_SECRET"] = SECRET
        await engine.dispose()

    async def asyncTearDown(self):
        await engine.dispose()

    async def test_read_hmac_uses_exact_body_and_allowed_events(self):
        self.assertEqual((await require_hermes_catalog_read_hmac(request_for()))[0], "read-test-key")
        payload = b'{"query":"squat"}'
        result = await require_hermes_catalog_read_hmac(request_for(payload, method="POST", event="leoft.catalog.read.search"))
        self.assertEqual(result[1], hashlib.sha256(payload).hexdigest())
        with self.assertRaises(Exception):
            await require_hermes_catalog_read_hmac(request_for(b" "))
        with self.assertRaises(Exception):
            await require_hermes_catalog_read_hmac(request_for(event="leoft.catalog.import"))
        with self.assertRaises(Exception):
            await require_hermes_catalog_read_hmac(request_for(signature="0" * 64))
        with self.assertRaises(Exception):
            await require_hermes_catalog_read_hmac(request_for(timestamp=int(time.time()) - 9999))

    async def test_meta_entry_media_association_and_zero_signatures_are_read_only(self):
        async with AsyncSessionLocal() as db:
            meta = await get_meta(db)
            self.assertEqual(meta.counts.model_dump(), {"entries": 1074, "media": 1074, "aliases": 2542, "signatures": 0})
            self.assertFalse(meta.signatures_available)
            self.assertEqual(meta.states.model_dump(), {"approved_by_ai": 20, "approved_manually": 0, "pending_review": 412, "unidentified": 642, "rejected": 0, "duplicate": 0})
            first = (await db.execute(select(LeoFTCatalogEntry).order_by(LeoFTCatalogEntry.catalog_id).limit(1))).scalar_one()
            media = (await db.execute(select(LeoFTCatalogMedia).where(LeoFTCatalogMedia.catalog_entry_id == first.id))).scalar_one()
            entry = await get_entry(db, first.catalog_id)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.catalog_id, first.catalog_id)
            self.assertTrue(all(not path.startswith("/") for path in [m.storage_path for m in entry.media]))
            self.assertIsNone(await get_entry(db, "does-not-exist"))
            media_response = await get_media(db, media.media_id)
            self.assertEqual(media_response.media_id, media.media_id)
            self.assertEqual(media_response.approval_status, first.approval_status)
            self.assertIsNone(await get_media(db, "does-not-exist"))
            valid, entry_exists, media_exists, _ = await validate_association(db, first.catalog_id, media.media_id)
            self.assertTrue((valid, entry_exists, media_exists) == (True, True, True))
            valid, entry_exists, media_exists, _ = await validate_association(db, first.catalog_id, "does-not-exist")
            self.assertEqual((valid, entry_exists, media_exists), (False, True, False))

    async def test_search_filters_pagination_and_stable_order(self):
        async with AsyncSessionLocal() as db:
            first = (await db.execute(select(LeoFTCatalogEntry).order_by(LeoFTCatalogEntry.catalog_id).limit(1))).scalar_one()
            request = CatalogSearchRequest(query=first.canonical_name, limit=2, offset=0)
            result = await search_catalog(db, request)
            again = await search_catalog(db, request)
            self.assertEqual([r.entry.catalog_id for r in result.results], [r.entry.catalog_id for r in again.results])
            self.assertEqual(result.results[0].match_type, "canonical_name_exact")
            self.assertLessEqual(len(result.results), 2)
            page = await search_catalog(db, CatalogSearchRequest(query=first.canonical_name, limit=1, offset=1))
            self.assertEqual(page.offset, 1)
            filtered = await search_catalog(db, CatalogSearchRequest(query=first.canonical_name, filters={"approval_status": [first.approval_status]}))
            self.assertTrue(all(item.entry.approval_status == first.approval_status for item in filtered.results))
            with self.assertRaises(Exception):
                CatalogSearchRequest(query=" ")
            with self.assertRaises(Exception):
                CatalogSearchRequest(query="x", limit=51)
            with self.assertRaises(Exception):
                CatalogSearchRequest(query="x", extra_field="blocked")

    async def test_complete_candidates_ignores_text_but_preserves_structural_filters_and_pagination(self):
        async with AsyncSessionLocal() as db:
            first = (await db.execute(select(LeoFTCatalogEntry).order_by(LeoFTCatalogEntry.catalog_id).limit(1))).scalar_one()
            complete = CatalogSearchRequest(query="Remada baixa na polia", limit=50, retrieval_mode="complete_candidates")
            result = await search_catalog(db, complete)
            changed_query = await search_catalog(db, complete.model_copy(update={"query": "texto sem correspondencia"}))
            self.assertEqual(result.total, 1074)
            self.assertEqual(len(result.results), 50)
            self.assertEqual([item.entry.catalog_id for item in result.results], [item.entry.catalog_id for item in changed_query.results])
            self.assertTrue(all(item.match_type is None and item.match_score is None for item in result.results))
            self.assertEqual(result.results[0].entry.catalog_id, first.catalog_id)
            self.assertTrue(result.results[0].entry.aliases)
            pages = []
            for offset in range(0, result.total, 50):
                page = await search_catalog(db, complete.model_copy(update={"offset": offset}))
                self.assertLessEqual(len(page.results), 50)
                pages.extend(item.entry.catalog_id for item in page.results)
            self.assertEqual(len(pages), 1074)
            self.assertEqual(len(set(pages)), 1074)
            self.assertEqual(pages, sorted(pages))
            filtered = await search_catalog(db, complete.model_copy(update={"filters": CatalogSearchFilters(approval_status=["approved_by_ai"])}))
            self.assertEqual(filtered.total, 20)
            self.assertTrue(all(item.entry.approval_status == "approved_by_ai" for item in filtered.results))
            with self.assertRaises(Exception):
                CatalogSearchRequest(query="x", retrieval_mode="invalid")

    async def test_remada_partial_alias_candidates_are_available_in_complete_mode(self):
        expected_ids = {
            "exercise_cross-over-polia-baixa-1_5ef3393e",
            "exercise_crucifixo-invertido-polia-baixa-1_498f2124",
            "exercise_crucifixo-polia-baixa-1_d9778f10",
            "exercise_obliquo-polia-baixa-1_d7a8aa95",
            "exercise_remada-baixa-no-pulley-pegada-aberta-supinada-1_23505ed3",
            "exercise_remada-baixa-unilateral-no-cross-1_d8858801",
            "exercise_remada-baixa-unilateral-pegada-neutra-1_c23b25a8",
            "exercise_abs-polia-alta-1_348e0ec1",
            "exercise_abs-polia-alta-2-1_95a881df",
            "exercise_aducao-na-polia-1_3facf6f3",
        }
        async with AsyncSessionLocal() as db:
            current = await search_catalog(db, CatalogSearchRequest(query="Remada baixa na polia", limit=50))
            complete_request = CatalogSearchRequest(query="Remada baixa na polia", limit=50, retrieval_mode="complete_candidates")
            complete = await search_catalog(db, complete_request)
            current_ids = {item.entry.catalog_id for item in current.results}
            complete_ids = set()
            for offset in range(0, complete.total, complete_request.limit):
                page = await search_catalog(db, complete_request.model_copy(update={"offset": offset}))
                complete_ids.update(item.entry.catalog_id for item in page.results)
            self.assertEqual(current_ids, {"exercise_treinos-276_fe0305f7"})
            self.assertTrue(expected_ids.isdisjoint(current_ids))
            self.assertTrue(expected_ids.issubset(complete_ids))


if __name__ == "__main__":
    unittest.main()
