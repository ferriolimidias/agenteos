#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if condition:
        print(f"CHECK {message}=OK")
    else:
        print(f"CHECK {message}=FAIL")
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    required_files = [
        "db/migrations/2026_07_17_phase2_multichannel_persistence.sql",
        "db/migrations/2026_07_17_phase2_multichannel_persistence_rollback.sql",
        "tests/channels/test_multichannel_persistence.py",
    ]
    for rel in required_files:
        require((ROOT / rel).exists(), f"exists {rel}", failures)

    models = (ROOT / "db/models.py").read_text(encoding="utf-8")
    for fragment in (
        "class ChannelContact",
        "class ChannelConversation",
        "channel_conversation_id",
        "conversation_key",
        "waba_id",
        "phone_number_id",
        "instagram_business_account_id",
        "uq_channel_contacts_empresa_id_id",
        "uq_channel_conversations_empresa_id_id",
    ):
        require(fragment in models, f"models contains {fragment}", failures)

    upgrade_sql = (ROOT / "db/migrations/2026_07_17_phase2_multichannel_persistence.sql").read_text(
        encoding="utf-8"
    )
    for fragment in (
        "uq_conexoes_empresa_id_id",
        "uq_crm_leads_empresa_id_id",
        "uq_channel_contacts_empresa_id_id",
        "uq_channel_conversations_empresa_id_id",
        "precheck failed",
        "postcheck failed",
        "mensagens_historico possui lead_id sem crm_leads correspondente",
    ):
        require(fragment in upgrade_sql, f"upgrade sql contains {fragment}", failures)

    main_source = (ROOT / "app/api/main.py").read_text(encoding="utf-8")
    require(
        "ensure_multichannel_persistence_tables" not in main_source,
        "manual migration not wired into FastAPI startup",
        failures,
    )

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.channels.test_multichannel_persistence"],
        cwd=ROOT,
    )
    require(proc.returncode == 0, "unittest multichannel persistence", failures)

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("OK - Fase 2 persistencia multicanal verificada sem APIs reais.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
