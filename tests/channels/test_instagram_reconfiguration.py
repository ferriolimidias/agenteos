import unittest

from app.services.instagram_simple import validation_summary


class InstagramReconfigurationContractTests(unittest.TestCase):
    def test_simple_contract_has_no_manual_validation_receipt(self):
        import app.services.instagram_simple as service

        self.assertFalse(hasattr(service, "create_validation_receipt"))
        self.assertFalse(hasattr(service, "verify_validation_receipt"))


    def test_sanitized_validation_summary_has_no_secret(self):
        summary = validation_summary(
            supplied_id="28090584240546860",
            identified={
                "instagram_professional_account_id": "28090584240546860",
                "resolved_instagram_graph_user_id": "17841458184539865",
                "token_is_valid": True,
                "permissions": [],
                "permissions_verified": False,
            },
            result="valid",
        )
        serialized = str(summary)
        self.assertEqual(summary["account_object_type"], "Instagram ID")
        self.assertIsNone(summary["relationship_verified"])
        self.assertNotIn("token-for-test-only", serialized)
        self.assertNotIn("access_token", serialized)


if __name__ == "__main__":
    unittest.main()
