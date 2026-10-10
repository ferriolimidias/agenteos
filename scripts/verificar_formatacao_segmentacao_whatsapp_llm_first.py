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
SEGMENTER_PATH = ROOT / "app" / "agents" / "atendimento" / "whatsapp_segmenter.py"


def _load_function(path: Path, module_name: str, function_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Não foi possível carregar {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function_name)


format_for_whatsapp = _load_function(FORMATTERS_PATH, "verificar_formatters_segmentacao", "format_for_whatsapp")
segment_whatsapp_response = _load_function(SEGMENTER_PATH, "verificar_whatsapp_segmenter", "segment_whatsapp_response")


FORBIDDEN = (
    "Intent" + "Router",
    "Tool" + "Registry",
    "Conversation" + "State",
    "sk" + "-",
    "Bearer",
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _semantic(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("*", "")).strip()


def _format_and_segment(text: str) -> list[str]:
    formatted = format_for_whatsapp(text, ConversationState(nome="Cliente Teste"))
    segments = segment_whatsapp_response(formatted)
    assert_true(len(segments) <= 3, f"Segmentou acima do limite: {segments!r}")
    assert_true(all(segment.strip() for segment in segments), f"Segmento vazio: {segments!r}")
    assert_true(_semantic(formatted) == _semantic(" ".join(segments)), f"Conteúdo alterado: {segments!r}")
    joined = "\n".join(segments)
    for forbidden in FORBIDDEN:
        assert_true(forbidden not in joined, f"Termo técnico vazou: {forbidden}")
    assert_true(not joined.strip().startswith("{"), f"JSON bruto retornado: {joined!r}")
    return segments


def _case_saudacao_inicial() -> None:
    text = (
        "Olá! Tudo bem? Eu sou o assistente da JJClean. Posso te ajudar com orçamento "
        "e agendamento para higienização de sofás, colchões e cadeiras. O que você precisa?"
    )
    segments = _format_and_segment(text)
    assert_true(len(segments) == 3, f"Saudação deveria ter 3 segmentos: {segments!r}")
    assert_true(segments[0] == "Olá! Tudo bem?", segments[0])
    assert_true("O que você precisa?" in segments[-1], segments[-1])


def _case_orcamento() -> None:
    text = (
        "Perfeito. Para um sofá de aproximadamente 1,80 m, a estimativa fica em R$ 180. "
        "Esse valor é uma prévia e pode ser confirmado pela equipe. Quer seguir para o agendamento?"
    )
    segments = _format_and_segment(text)
    joined = " ".join(segments)
    assert_true("R$ 180" in joined and "1,80 m" in joined, joined)
    assert_true("*R$ 180*" in joined and "*1,80 m*" in joined, joined)
    assert_true(not any(segment.strip() in {"R$ 180", "*R$ 180*", "1,80 m", "*1,80 m*"} for segment in segments), segments)
    assert_true("R$" not in segments[-1] or "180" in segments[-1], segments)


def _case_campo_faltante() -> None:
    text = (
        "Consigo te ajudar com isso. Para calcular a estimativa do sofá, me diga por favor "
        "a medida aproximada em metros."
    )
    segments = _format_and_segment(text)
    assert_true(len(segments) == 2, f"Campo faltante deveria separar introdução: {segments!r}")
    assert_true(segments[0] == "Consigo te ajudar com isso.", segments[0])
    joined = " ".join(segments).lower()
    assert_true("r$" not in joined, joined)
    assert_true("telefone" not in joined and "endereço" not in joined, joined)


def _case_sem_split_incoerente() -> None:
    text = "Vou calcular para você.\n\nCom essa medida, a estimativa fica em R$ 180."
    segments = _format_and_segment(text)
    assert_true(
        all(segment.strip().lower() != "vou calcular para você." for segment in segments),
        f"Segmento intermediário sem conclusão: {segments!r}",
    )
    assert_true(any("R$ 180" in segment for segment in segments), segments)


def _case_limite() -> None:
    text = (
        "Olá! Tudo bem? Eu sou o assistente da JJClean. Posso te ajudar com orçamento. "
        "Também consigo apoiar o agendamento. Para começar, me diga qual item você quer higienizar. "
        "Se tiver a medida aproximada, pode mandar junto. Assim eu sigo com mais precisão."
    )
    segments = _format_and_segment(text)
    assert_true(len(segments) <= 3, f"Resposta longa excedeu limite: {segments!r}")


def _case_seguranca() -> None:
    text = "Claro, posso te ajudar com isso. Me diga qual estofado você quer higienizar."
    segments = _format_and_segment(text)
    joined = "\n".join(segments)
    for forbidden in FORBIDDEN:
        assert_true(forbidden not in joined, f"Segmentador inseriu termo técnico: {forbidden}")


def main() -> int:
    _case_saudacao_inicial()
    _case_orcamento()
    _case_campo_faltante()
    _case_sem_split_incoerente()
    _case_limite()
    _case_seguranca()
    print("verificar_formatacao_segmentacao_whatsapp_llm_first: OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"verificar_formatacao_segmentacao_whatsapp_llm_first: FALHOU: {exc}", file=sys.stderr)
        raise SystemExit(1)
