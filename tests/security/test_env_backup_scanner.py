import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.verificar_secrets_nao_expostos as verifier


class EnvBackupScannerTests(unittest.TestCase):
    def test_env_backup_sensitive_assignment_is_detected_without_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.pre-live").write_text("SECRET_KEY=" + ("A" * 40) + "\n", encoding="utf-8")
            with patch.object(verifier, "ROOT", root):
                findings = verifier.scan_env_backups()

        self.assertEqual(len(findings), 1)
        self.assertIn(".env.pre-live:1:env_backup_sensitive_assignment:SECRET_KEY=<redacted>", findings[0])
        self.assertNotIn("AAAA", findings[0])


if __name__ == "__main__":
    unittest.main()

