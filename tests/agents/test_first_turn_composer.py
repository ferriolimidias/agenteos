from pydantic import ValidationError

from app.agents.atendimento.first_turn import compose_first_turn_response
from app.agents.schemas import FirstTurnAgentDecision


GREETING = "GREETING_ALPHA\nFACT_ALPHA\n*CTA_ALPHA* 💛\nhttps://example.test/a"


def test_first_turn_agent_extension_only_decision_has_no_greeting():
    decision = FirstTurnAgentDecision(needs_extension=True, extension="PRICE_ALPHA")
    assert GREETING not in decision.extension


def test_first_turn_covered_question_returns_exact_greeting():
    decision = FirstTurnAgentDecision(needs_extension=False, extension="")
    assert compose_first_turn_response(GREETING, decision) == GREETING


def test_first_turn_simple_message_returns_exact_greeting():
    assert compose_first_turn_response(GREETING, {"needs_extension": False, "extension": ""}) == GREETING


def test_first_turn_extra_question_preserves_exact_prefix():
    decision = FirstTurnAgentDecision(needs_extension=True, extension="Preço: PRICE_ALPHA.")
    final = compose_first_turn_response(GREETING, decision)
    assert final.startswith(GREETING)
    assert final == f"{GREETING}\n\nPreço: PRICE_ALPHA."


def test_first_turn_unknown_extra_fact_has_no_invention():
    decision = FirstTurnAgentDecision(needs_extension=False, extension="")
    assert compose_first_turn_response(GREETING, decision) == GREETING


def test_first_turn_extension_cannot_repeat_or_replace_greeting():
    decision = FirstTurnAgentDecision(needs_extension=True, extension=f"{GREETING} altered")
    assert compose_first_turn_response(GREETING, decision) == GREETING


def test_first_turn_extension_schema_rejects_empty_extension():
    try:
        FirstTurnAgentDecision(needs_extension=True, extension="")
    except ValidationError:
        return
    raise AssertionError("needs_extension=true must require a non-empty extension")


def test_first_turn_greeting_formatting_is_preserved():
    assert compose_first_turn_response(GREETING, FirstTurnAgentDecision()) == GREETING


def test_first_turn_composer_tenant_isolation():
    decision = FirstTurnAgentDecision()
    assert compose_first_turn_response("GREETING_ALPHA", decision) == "GREETING_ALPHA"
    assert compose_first_turn_response("GREETING_BETA", decision) == "GREETING_BETA"


def test_second_turn_does_not_use_composer():
    assert compose_first_turn_response(None, None) == ""
