from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verificar_secrets_nao_expostos.py"
TMP_FILE = ROOT / "tests" / "_tmp_secret_verifier_probe.py"
TMP_REPORT = ROOT / "relatorios" / "codex" / "_tmp_secret_verifier_report.md"


def _run_verifier() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


class SecretVerifierSubprocessTests(unittest.TestCase):
    def tearDown(self) -> None:
        TMP_FILE.unlink(missing_ok=True)
        TMP_REPORT.unlink(missing_ok=True)

    def test_placeholders_only_return_zero(self):
        TMP_FILE.write_text(
            "\n".join(
                [
                    'SAFE_JSON = {"access_token": "***REDACTED***"}',
                    'SAFE_ANGLE = {"authorization": "<REDACTED>"}',
                    'SAFE_TEST = {"api_key": "REDACTED_FOR_TEST"}',
                    'SAFE_BEARER = {"Authorization": "Bearer ***REDACTED***"}',
                    'SAFE_LOCAL = {"access_token": "fake-local-token-ag2"}',
                ]
            ),
            encoding="utf-8",
        )
        result = _run_verifier()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_plausible_secrets_return_one_without_printing_secret(self):
        meta_token = "EAAT" + ("M" * 32)
        bearer_token = "bearer" + ("B" * 30)
        app_secret = "appsecret" + ("7" * 32)
        fernet_key = ("F" * 43) + "="
        TMP_FILE.write_text(
            "\n".join(
                [
                    f'META_TOKEN = "{meta_token}"',
                    f'HEADERS = {{"Authorization": "Bearer {bearer_token}"}}',
                    f"META_APP_SECRET={app_secret}",
                    f'META_CREDENTIAL_ENCRYPTION_KEY="{fernet_key}"',
                    'BROKEN = {"'
                    + "access"
                    + "_token"
                    + '": "'
                    + "REDACTED_"
                    + meta_token
                    + '"}',
                ]
            ),
            encoding="utf-8",
        )
        result = _run_verifier()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("[FAIL]", result.stdout)
        self.assertNotIn(meta_token, result.stdout)
        self.assertNotIn(bearer_token, result.stdout)
        self.assertNotIn(app_secret, result.stdout)
        self.assertNotIn(fernet_key, result.stdout)

    def test_reports_directory_is_scanned(self):
        meta_token = "EAAT" + ("R" * 32)
        TMP_REPORT.parent.mkdir(parents=True, exist_ok=True)
        TMP_REPORT.write_text(f"access_token={meta_token}\n", encoding="utf-8")
        result = _run_verifier()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("_tmp_secret_verifier_report.md", result.stdout)
        self.assertNotIn(meta_token, result.stdout)


if __name__ == "__main__":
    unittest.main()
