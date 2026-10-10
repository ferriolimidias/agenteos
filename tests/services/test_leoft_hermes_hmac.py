from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.leoft_hermes_hmac import HermesHMACConfigError, build_hermes_hmac_headers


class LeoFTHermesHMACTest(unittest.TestCase):
    def test_hmac_headers_include_idempotency_from_checkout_reference(self):
        env = dict(os.environ)
        env["HERMES_LEOFT_HMAC_SECRET"] = "fake-local-secret"
        env.pop("HERMES_LEOFT_HMAC_IDEMPOTENCY_HEADER", None)
        with patch.dict(os.environ, env, clear=True):
            headers = build_hermes_hmac_headers(
                {
                    "checkout_reference": "leoft:checkout:123",
                    "payment_id": "pay_123",
                }
            )

        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["X-AgenteOS-Event"], "leoft.payment_approved")
        self.assertEqual(headers["X-AgenteOS-Idempotency-Key"], "leoft:checkout:123")

    def test_hmac_headers_fallback_to_payment_id_for_idempotency(self):
        with patch.dict(
            os.environ,
            {
                "HERMES_LEOFT_HMAC_SECRET": "fake-local-secret",
                "HERMES_LEOFT_HMAC_IDEMPOTENCY_HEADER": "X-Custom-Idempotency",
            },
            clear=True,
        ):
            headers = build_hermes_hmac_headers({"payment_id": "pay_123"})

        self.assertEqual(headers["X-Custom-Idempotency"], "pay_123")

    def test_hmac_headers_require_stable_idempotency_key(self):
        with patch.dict(os.environ, {"HERMES_LEOFT_HMAC_SECRET": "fake-local-secret"}, clear=True):
            with self.assertRaisesRegex(HermesHMACConfigError, "idempotency_key, checkout_reference ou payment_id"):
                build_hermes_hmac_headers({})


if __name__ == "__main__":
    unittest.main()
