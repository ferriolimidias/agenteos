from __future__ import annotations

import re
import sys
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.schemas import ConversationState

FORMATTERS_PATH = ROOT / "app" / "agents" / "atendimento" / "formatters.py"
_spec = importlib.util.spec_from_file_location("verificar_formatacao_whatsapp_agentes_formatters", FORMATTERS_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Não foi possível carregar {FORMATTERS_PATH}")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
format_for_whatsapp = _module.format_for_whatsapp


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _norm(text: str) -> str:
    text = str(text or "").replace("*", "")
    return re.sub(r"\s+", " ", text.lower()).strip()


def _case_formatter_preserves_agenda_wording() -> None:
    state = ConversationState(nome="Jose Paulo | Ferrioli Mídias")
    draft = "Para continuar, me envie nome, endereço ou bairro e melhor dia."
    formatted = format_for_whatsapp(draft, state)
    assert_true("perfeito. para solicitar o agendamento da visita" not in _norm(formatted), formatted)
    normalized = _norm(formatted)
    assert_true("me envie" in normalized, formatted)
    assert_true("endereço ou bairro" in normalized, formatted)
    assert_true("melhor dia" in normalized, formatted)


def _case_formatter_keeps_simple_whatsapp_normalization() -> None:
    state = ConversationState(nome="Jose Paulo | Ferrioli Mídias")
    draft = "Valor total: R$ 200.\n\nObrigado."
    formatted = format_for_whatsapp(draft, state)
    assert_true("*R$ 200*" in formatted, formatted)
    assert_true("Jose Paulo | Ferrioli Mídias" not in formatted, formatted)


def _case_formatter_does_not_inject_content() -> None:
    state = ConversationState(nome="Cliente Teste")
    draft = "Oi, posso ajudar com o que você precisar."
    formatted = format_for_whatsapp(draft, state)
    assert_true(formatted == draft, f"Formatter alterou conteúdo sem necessidade: {formatted!r}")


def main() -> int:
    _case_formatter_preserves_agenda_wording()
    _case_formatter_keeps_simple_whatsapp_normalization()
    _case_formatter_does_not_inject_content()
    print("verificar_formatacao_whatsapp_agentes: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"verificar_formatacao_whatsapp_agentes: FALHOU: {exc}", file=sys.stderr)
        raise SystemExit(1)
