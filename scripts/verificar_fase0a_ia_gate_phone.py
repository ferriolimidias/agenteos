from __future__ import annotations

from pathlib import Path
import inspect
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.rosangela_ai_control import evaluate_conversation_ai_gate
from app.services.whatsapp_address import compare_whatsapp_addresses, normalize_whatsapp_address


def main() -> int:
    normalized = normalize_whatsapp_address("(54) 99999-0411", default_country="BR")
    assert normalized.digits == "5554999990411"
    assert compare_whatsapp_addresses("5554999990411", "555499990411", default_country="BR").match
    assert not compare_whatsapp_addresses("5554999990411", "555599990411", default_country="BR").match
    source = inspect.getsource(evaluate_conversation_ai_gate)
    assert "ensure_ai_window" not in source
    assert "test_mode" not in source
    assert "ad_entry_policy" not in source
    print("verificar_fase0a_ia_gate_phone: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
