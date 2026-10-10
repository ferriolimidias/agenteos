from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def assert_contains(path: str, needle: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle not in text:
        raise AssertionError(f"{path} nao contem {needle!r}")


def assert_not_contains(path: str, needle: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle in text:
        raise AssertionError(f"{path} contem texto proibido {needle!r}")


def main() -> None:
    assert_contains("app/services/meta/embedded_signup.py", "extract_signup_identifiers")
    assert_contains("app/services/meta/embedded_signup.py", "classify_meta_error_for_user")
    assert_contains("app/services/meta/graph_client.py", "invalid_object_id")
    assert_contains("app/api/routers/conexoes.py", "_resolve_and_persist_meta_number")
    assert_contains("app/api/routers/conexoes.py", "meta_phone_number_id")
    assert_contains("app/api/routers/conexoes.py", "await client.get_phone_number")
    assert_contains("frontend/src/utils/metaEmbeddedSignup.js", "createMetaSignupCompletionAggregator")
    assert_contains("frontend/src/pages/tenant/Conexoes.jsx", "waitForCompletion")
    assert_contains("scripts/recuperar_meta_unsupported_get.py", "display_number_configured")
    assert_not_contains("frontend/src/pages/tenant/Conexoes.jsx", "Unsupported get request")
    print("OK meta_unsupported_get verifier")


if __name__ == "__main__":
    main()
