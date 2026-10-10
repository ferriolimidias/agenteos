from app.agents.guards.response_guard import ResponseGuard


def test_blocks_openai_key():
    result = ResponseGuard().validate("minha chave é sk-1234567890abcdef")
    assert result.allowed is False
    assert result.sanitized is True


def test_blocks_bearer_token():
    result = ResponseGuard().validate("Authorization: Bearer abcdefghijklmnop")
    assert result.allowed is False


def test_blocks_stack_trace():
    result = ResponseGuard().validate('Traceback (most recent call last):\n  File "x.py", line 1')
    assert result.allowed is False


def test_blocks_system_marker():
    result = ResponseGuard().validate("[SISTEMA_DEBUG] resposta interna")
    assert result.allowed is False


def test_safe_text_is_approved():
    result = ResponseGuard().validate("Claro, posso te ajudar com isso.")
    assert result.allowed is True
    assert result.text == "Claro, posso te ajudar com isso."
