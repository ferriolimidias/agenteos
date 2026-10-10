#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    failures = []
    provider = ROOT / "app/services/channels/providers/meta_whatsapp.py"
    text = provider.read_text(encoding="utf-8")
    checks = {
        "provider exists": provider.exists(),
        "central version": 'GRAPH_API_VERSION = "v25.0"' in text,
        "transport injected": "transport: MetaWhatsAppTransport | None = None" in text,
        "no implicit http client": "httpx" not in text and "requests" not in text,
        "sanitized error": "secret-token" not in text and "corpo completo" in text,
        "retry bounded": "max_attempts" in text and "max_attempts > 3" in text,
    }
    for label, ok in checks.items():
        print(f"CHECK {label}={'OK' if ok else 'FAIL'}")
        if not ok:
            failures.append(label)
    proc = subprocess.run([sys.executable, "-m", "unittest", "tests.channels.test_meta_whatsapp_provider"], cwd=ROOT)
    if proc.returncode:
        failures.append("provider tests")
    print("FAIL" if failures else "OK - provider Meta WhatsApp validado sem HTTP real.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
