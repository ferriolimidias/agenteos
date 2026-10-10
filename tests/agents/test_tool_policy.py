from app.agents.schemas import ToolInvocation
from app.agents.tools.policy import ToolPolicy


def validate_url(url: str):
    invocation = ToolInvocation(
        tool_name="consulta_http",
        tenant_id="tenant-1",
        specialist_key="rag",
        tool_type="http",
        scope="conversation",
        risk="low",
        url=url,
    )
    return ToolPolicy().validate(invocation)


def test_blocks_localhost():
    allowed, reasons = validate_url("http://localhost:8000")
    assert allowed is False
    assert "dangerous_url" in reasons


def test_blocks_loopback_ip():
    allowed, reasons = validate_url("http://127.0.0.1:8000")
    assert allowed is False
    assert "dangerous_url" in reasons


def test_blocks_metadata_endpoint():
    allowed, reasons = validate_url("http://169.254.169.254/latest/meta-data")
    assert allowed is False
    assert "dangerous_url" in reasons


def test_blocks_private_ip():
    allowed, reasons = validate_url("http://192.168.0.1")
    assert allowed is False
    assert "dangerous_url" in reasons


def test_allows_public_https_without_execution():
    allowed, reasons = validate_url("https://example.com/resource")
    assert allowed is True
    assert reasons == []
