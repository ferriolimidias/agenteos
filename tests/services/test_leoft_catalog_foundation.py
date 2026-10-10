import json
import tempfile
import unittest
from pathlib import Path

from scripts.import_leoft_catalog import adapt_source_catalog, load_catalog


class LeoFTCatalogFoundationContractTests(unittest.TestCase):
    def test_source_records_are_adapted_in_memory_and_aliases_are_canonicalized(self):
        source = {
            "catalog_version": "source-1",
            "generated_at": "2026-08-01T00:00:00Z",
            "records": [{
                "catalog_id": "source-entry-1",
                "canonical_name_ptbr": "Agachamento",
                "approval_status": "pending_review",
                "confidence": 0.5,
                "primary_muscle_group": "legs",
                "aliases": ["Squat", " squat ", "Agachamento"],
                "media": [{"media_id": "source-media-1", "relative_path": "media/a.mp4", "mime_type": "video/mp4", "format": "mp4", "sha256": "a" * 64, "duration_seconds": 1.5}],
                "signatures": [{"signature_key": "source-signature-1", "signature_version": "v1", "signature_payload": {"source": True}}],
            }, {
                "catalog_id": "source-entry-2",
                "canonical_name_ptbr": "Outro",
                "approval_status": "pending_review",
                "aliases": ["Squat"],
                "media": [],
                "signatures": [{"signature_key": "source-signature-2", "signature_version": "v1", "signature_payload": {}}],
            }],
        }
        adapted, counts = adapt_source_catalog(source, "b" * 64)
        self.assertEqual(len(adapted["entries"]), 2)
        self.assertEqual(counts["raw_aliases"], 4)
        self.assertEqual(counts["aliases"], 3)
        self.assertEqual(adapted["entries"][0]["media"][0]["storage_path"], "media/a.mp4")
        self.assertEqual(adapted["entries"][0]["media"][0]["duration_ms"], 1500)
        self.assertEqual(adapted["entries"][1]["aliases"], ["Squat"])
        self.assertEqual(counts["signatures"], 2)

    def test_source_record_without_signature_is_blocked(self):
        source = {"catalog_version": "source-1", "records": [{
            "catalog_id": "source-entry-1", "canonical_name_ptbr": "Agachamento",
            "approval_status": "pending_review", "aliases": [],
            "media": [{"media_id": "source-media-1", "relative_path": "media/a.mp4", "sha256": "a" * 64}],
        }]}
        with self.assertRaisesRegex(ValueError, "no catalog signature"):
            adapt_source_catalog(source, "b" * 64)

    def test_catalog_validation_counts_and_sha256(self):
        media_hash = "a" * 64
        document = {
            "version": "fixture-1",
            "entries": [{
                "catalog_id": "ex-1",
                "canonical_name": "Agachamento",
                "approval_status": "approved_by_ai",
                "aliases": ["squat"],
                "media": [{"media_id": "media-1", "storage_path": "media/ex-1.mp4", "sha256": media_hash}],
                "signatures": [{"signature_key": "sig-1", "signature_version": "v1", "signature_payload": {"x": 1}}],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            loaded, source_hash, counts = load_catalog(path)
        self.assertEqual(loaded["version"], "fixture-1")
        self.assertEqual(len(source_hash), 64)
        self.assertEqual(counts, {
            "entries": 1, "media": 1, "aliases": 1, "signatures": 1,
            "states": {"approved_by_ai": 1, "approved_manually": 0, "pending_review": 0, "unidentified": 0, "rejected": 0, "duplicate": 0},
        })

    def test_canonical_counts_reject_negative_missing_and_divergent_states(self):
        from scripts.import_leoft_catalog import validate_catalog_document

        base = {
            "event_type": "leoft.catalog.import", "schema_version": "1.0", "catalog_version": "counts-v1",
            "source_sha256": "a" * 64, "generated_at": "2026-07-31T00:00:00Z", "source": "hermes",
            "entries": [{"catalog_id": "ex-1", "canonical_name": "Agachamento", "approval_status": "approved_by_ai", "aliases": [], "media": [], "signatures": []}],
        }
        states = {"approved_by_ai": 1, "approved_manually": 0, "pending_review": 0, "unidentified": 0, "rejected": 0, "duplicate": 0}
        base["expected_counts"] = {"entries": 1, "media": 0, "aliases": 0, "signatures": 0, "states": states}
        with self.assertRaisesRegex(ValueError, "one or more signatures"):
            validate_catalog_document(base, enforce_envelope=True)
        self.assertEqual(validate_catalog_document(base, enforce_envelope=True, allow_missing_signatures=True)["states"], states)

        partial = json.loads(json.dumps(base))
        partial["entries"].append({
            "catalog_id": "ex-2", "canonical_name": "Outro", "approval_status": "approved_by_ai",
            "media": [], "aliases": [], "signatures": [{
                "signature_key": "sig-2", "signature_version": "v1", "signature_payload": {},
            }],
        })
        partial["expected_counts"].update({"entries": 2, "signatures": 1})
        partial["expected_counts"]["states"]["approved_by_ai"] = 2
        with self.assertRaisesRegex(ValueError, "signature count must be zero or equal to entries"):
            validate_catalog_document(partial, enforce_envelope=True, allow_missing_signatures=True)

        negative = json.loads(json.dumps(base))
        negative["expected_counts"]["entries"] = -1
        with self.assertRaisesRegex(ValueError, "non-negative"):
            validate_catalog_document(negative, enforce_envelope=True, allow_missing_signatures=True)

        missing_state = json.loads(json.dumps(base))
        del missing_state["expected_counts"]["states"]["duplicate"]
        with self.assertRaisesRegex(ValueError, "required"):
            validate_catalog_document(missing_state, enforce_envelope=True, allow_missing_signatures=True)

        divergent = json.loads(json.dumps(base))
        divergent["expected_counts"]["states"]["pending_review"] = 1
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            validate_catalog_document(divergent, enforce_envelope=True, allow_missing_signatures=True)

    def test_invalid_media_hash_is_rejected(self):
        document = {"version": "fixture-1", "entries": [{
            "catalog_id": "ex-1", "canonical_name": "Agachamento", "approval_status": "pending_review",
            "media": [{"media_id": "media-1", "storage_path": "media/ex-1.mp4", "sha256": "invalid"}],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_catalog(path)


if __name__ == "__main__":
    unittest.main()
