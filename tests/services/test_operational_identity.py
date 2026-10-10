import unittest

from app.services.operational_identity import canonical_operational_address, deterministic_conversation_key
from app.services.channels.types import Channel


class OperationalIdentityTests(unittest.TestCase):
    def test_whatsapp_phone_forms_share_canonical_address(self):
        old_form = canonical_operational_address("555484390411", channel=Channel.WHATSAPP)
        e164_form = canonical_operational_address("+5554984390411", channel=Channel.WHATSAPP)
        self.assertEqual(old_form, e164_form)
        self.assertEqual(
            deterministic_conversation_key(
                channel=Channel.WHATSAPP,
                provider="meta_whatsapp",
                external_contact_id=old_form,
            ),
            deterministic_conversation_key(
                channel=Channel.WHATSAPP,
                provider="meta_whatsapp",
                external_contact_id=e164_form,
            ),
        )

    def test_connection_and_tenant_remain_part_of_identity(self):
        key = deterministic_conversation_key(
            channel=Channel.WHATSAPP,
            provider="meta_whatsapp",
            external_contact_id="5554984390411",
        )
        self.assertEqual(key, "whatsapp:meta_whatsapp:5554984390411")

    def test_brazilian_whatsapp_formats_share_canonical_identity(self):
        values = {
            canonical_operational_address("+55 54 98439-0411", channel=Channel.WHATSAPP),
            canonical_operational_address("55 54 98439-0411", channel=Channel.WHATSAPP),
            canonical_operational_address("(54) 98439-0411", channel=Channel.WHATSAPP),
            canonical_operational_address("5554984390411", channel=Channel.WHATSAPP),
        }
        self.assertEqual(values, {"5554984390411"})

    def test_non_brazilian_whatsapp_number_is_not_rewritten(self):
        self.assertEqual(
            canonical_operational_address("+14155552671", channel=Channel.WHATSAPP),
            "14155552671",
        )


if __name__ == "__main__":
    unittest.main()
