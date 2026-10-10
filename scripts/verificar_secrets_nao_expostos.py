from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.routers.empresas import _serializar_empresa_segura
from app.core.secret_sanitizer import (
    REDACTED,
    is_sensitive_key,
    sanitize_credenciais_canais,
    sanitize_for_api,
    sanitize_for_log,
    sanitize_string,
)


OPENAI_PREFIX = "sk-" + "proj-"
OPENAI_CLASSIC_PREFIX = "sk-"
META_PREFIX = "EAAT"
BEARER_PREFIX = "Bearer "

ALLOWED_REDACTED_PLACEHOLDERS = {
    REDACTED,
    f"Bearer {REDACTED}",
    "<REDACTED>",
    "REDACTED_FOR_TEST",
    "fake-local-token-ag2",
    "fake-local-token-ag21",
    "fake-local-token-ag4-tests",
    "configured-secret-value",
    "instagram-user-token",
    "marketing-admin-token",
    "received-secret-value",
    "secret-value",
    "synthetic-catalog-secret",
    "synthetic-generation-secret",
    "test-secret-key-with-more-than-thirty-two-chars",
    "test-meta-credential-key-with-32chars",
}

SCANNER_SENSITIVE_KEYS = {
    "meta_app_secret",
    "meta_credential_encryption_key",
    "meta_webhook_verify_token",
    "secret_key",
}

ALLOWED_ENV_FILES = {
    ".env.example",
    "frontend/.env.production",
}


def is_allowed_redacted_placeholder(value: str | None) -> bool:
    """Allow only exact, documented placeholders. Never allow mixed token text."""
    return str(value or "").strip() in ALLOWED_REDACTED_PLACEHOLDERS


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def contains_raw_secret(obj: Any, secret: str) -> bool:
    return secret in json.dumps(obj, ensure_ascii=False, default=str)


def fake_secret(prefix: str, char: str = "A", size: int = 32) -> str:
    return prefix + (char * size)


def _fake_empresa() -> SimpleNamespace:
    openai_key = fake_secret(OPENAI_PREFIX, "O", 48)
    meta_token = fake_secret(META_PREFIX, "M", 32)
    evolution_key = fake_secret("evo_", "E", 32)
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        nome_empresa="Tenant Seguro",
        area_atuacao=None,
        logo_url=None,
        credenciais_canais={
            "openai_api_key": openai_key,
            "api_key_openai": openai_key,
            "evolution": {
                "evolution_apikey": evolution_key,
                "authorization": BEARER_PREFIX + evolution_key,
                "url": "https://evolution.example.invalid",
            },
            "danca_circular": {"link_pagamento": None, "local": None},
        },
        openai_api_key=openai_key,
        ia_personalidade=None,
        ia_regras_negocio=None,
        conexao_disparo_id=None,
        disparo_delay_min=3,
        disparo_delay_max=7,
        limite_certeza=0.65,
        limite_duvida=0.45,
        max_agentes_desempate=3,
        meta_capi_ativo=True,
        meta_pixel_id="123",
        meta_access_token=meta_token,
    )


def _assert_sanitized(obj: Any, secret: str, label: str) -> None:
    assert_true(not contains_raw_secret(obj, secret), f"{label} não expõe valor cru")
    assert_true(REDACTED in json.dumps(obj, ensure_ascii=False, default=str), f"{label} contém marcador redacted")


def test_sanitizer_direct() -> None:
    openai_proj = fake_secret(OPENAI_PREFIX, "P", 40)
    openai_classic = fake_secret(OPENAI_CLASSIC_PREFIX, "C", 40)
    meta = fake_secret(META_PREFIX, "T", 32)
    bearer_value = fake_secret("", "B", 32)
    bearer_base64 = "abcd/efgh+ijkl==mnopqrstuv123456"
    evolution = fake_secret("evo_", "K", 32)
    generic_api_key = fake_secret("", "X", 32)

    samples = [
        ({"apikey": evolution}, evolution, "dict apikey"),
        ({"X-API-Key": generic_api_key}, generic_api_key, "dict X-API-Key"),
        ({"x_api_key": generic_api_key}, generic_api_key, "dict x_api_key"),
        ({"headers": {"X-API-Key": generic_api_key}}, generic_api_key, "nested headers X-API-Key"),
        ({"access_token": evolution}, evolution, "dict access_token"),
        ({"openai_api_key": openai_proj}, openai_proj, "dict openai_api_key"),
        ({"meta_access_token": meta}, meta, "dict meta_access_token"),
        ({"Authorization": BEARER_PREFIX + bearer_value}, bearer_value, "dict Authorization Bearer"),
        ({"Authorization": BEARER_PREFIX + bearer_base64}, bearer_base64, "dict Authorization Bearer base64"),
        ({"nested": [{"token": evolution}, {"headers": {"Authorization": BEARER_PREFIX + bearer_value}}]}, evolution, "nested dict/list"),
    ]
    for source, secret, label in samples:
        _assert_sanitized(sanitize_for_log(source), secret, label)

    json_samples = [
        (json.dumps({"apikey": evolution}), evolution, "string JSON apikey"),
        (str({"apikey": evolution}), evolution, "string repr apikey"),
        (json.dumps({"X-API-Key": generic_api_key}, separators=(",", ":")), generic_api_key, "string JSON X-API-Key"),
        (json.dumps({"customer_api_key": generic_api_key}, separators=(",", ":")), generic_api_key, "string JSON customer_api_key"),
        (json.dumps({"tenant_access_token": evolution}, separators=(",", ":")), evolution, "string JSON tenant_access_token"),
        (json.dumps({"provider_token": evolution}, separators=(",", ":")), evolution, "string JSON provider_token"),
        (json.dumps({"webhook_secret": evolution}, separators=(",", ":")), evolution, "string JSON webhook_secret"),
        (json.dumps({"access_token": evolution}), evolution, "string JSON access_token"),
        (json.dumps({"authorization": BEARER_PREFIX + bearer_value}), bearer_value, "string JSON authorization"),
        (json.dumps({"Authorization": BEARER_PREFIX + bearer_base64}), bearer_base64, "string JSON Authorization Bearer base64"),
    ]
    for source, secret, label in json_samples:
        _assert_sanitized(sanitize_string(source), secret, label)

    string_samples = [
        (f"openai={openai_proj}", openai_proj, "string sk-proj"),
        (f"openai={openai_classic}", openai_classic, "string sk"),
        (f"meta={meta}", meta, "string EAAT"),
        (f"Authorization: {BEARER_PREFIX}{bearer_value}", bearer_value, "string Bearer"),
        (f"{BEARER_PREFIX}{bearer_base64}", bearer_base64, "string Bearer base64"),
        (f"Authorization: {BEARER_PREFIX}{bearer_base64}", bearer_base64, "string Authorization Bearer base64"),
        (f"X-API-Key: {generic_api_key}", generic_api_key, "header X-API-Key"),
        (f"customer_api_key: {generic_api_key}", generic_api_key, "header customer_api_key"),
        (f"https://example.invalid/path?apikey={evolution}&x=1", evolution, "query apikey"),
        (f"https://example.invalid/path?a=1&x_api_key={generic_api_key}&b=2", generic_api_key, "query x_api_key"),
        (f"customer_api_key={generic_api_key}", generic_api_key, "query customer_api_key"),
        (f"x-api-key={generic_api_key}", generic_api_key, "query x-api-key"),
        (f"tenant_access_token={evolution}", evolution, "query tenant_access_token"),
        (f"provider_token={evolution}", evolution, "query provider_token"),
        (f"webhook_secret={evolution}", evolution, "query webhook_secret"),
        (f"https://example.invalid/path?access_token={evolution}&x=1", evolution, "query access_token"),
    ]
    for source, secret, label in string_samples:
        _assert_sanitized(sanitize_string(source), secret, label)

    anamnese_token = fake_secret("anam_", "N", 32)
    anamnese_url_query = f"https://dominio.invalid/anamnese/leoft?token={anamnese_token}"
    anamnese_url_amp = f"https://dominio.invalid/anamnese/leoft?origem=whatsapp&token={anamnese_token}"
    evolution_payload_text = f"[EvolutionProvider] Payload -> {{'number': '5554999999999', 'text': '{anamnese_url_query}\\n\\nVocê responde a anamnese.'}}"
    evolution_payload_dict = {"number": "5554999999999", "text": anamnese_url_query}

    for source, label in [
        (anamnese_url_query, "URL anamnese com ?token"),
        (anamnese_url_amp, "URL anamnese com &token"),
        (evolution_payload_text, "payload Evolution texto tokenizado"),
    ]:
        safe = sanitize_string(source)
        assert_true("token=***REDACTED***" in safe, f"{label} usa token redacted")
        assert_true(anamnese_token not in safe, f"{label} não expõe token cru")

    safe_payload = sanitize_for_log(evolution_payload_dict)
    assert_true(safe_payload.get("text", "").endswith("token=***REDACTED***"), "dict payload Evolution mascara token na chave text")
    assert_true(anamnese_token not in json.dumps(safe_payload, ensure_ascii=False), "dict payload Evolution não expõe token cru")


def test_api_serialization() -> None:
    empresa = _fake_empresa()
    serialized = _serializar_empresa_segura(empresa)  # type: ignore[arg-type]
    raw_openai = empresa.openai_api_key
    raw_meta = empresa.meta_access_token
    raw_evolution = empresa.credenciais_canais["evolution"]["evolution_apikey"]

    assert_true(not contains_raw_secret(serialized, raw_openai), "API de empresa não retorna openai_api_key crua")
    assert_true(not contains_raw_secret(serialized, raw_meta), "API de empresa não retorna meta_access_token cru")
    assert_true(not contains_raw_secret(serialized, raw_evolution), "API de empresa não retorna Evolution apikey crua")
    assert_true(serialized.get("openai_api_key") is None, "EmpresaResponse não contém openai_api_key em claro")
    assert_true(serialized.get("openai_api_key_configurada") is True, "status OpenAI usa flag booleana")
    assert_true(bool(serialized.get("openai_api_key_mascarada")), "status OpenAI usa máscara")
    assert_true(serialized.get("meta_access_token_configurado") is True, "status Meta usa flag booleana")
    assert_true(bool(serialized.get("meta_access_token_mascarado")), "status Meta usa máscara")

    safe_creds = sanitize_credenciais_canais(empresa.credenciais_canais)
    assert_true(not contains_raw_secret(safe_creds, raw_openai), "credenciais_canais não expõe OpenAI crua")
    assert_true(not contains_raw_secret(safe_creds, raw_evolution), "credenciais_canais não expõe Evolution crua")
    assert_true(REDACTED in json.dumps(safe_creds, ensure_ascii=False), "credenciais_canais usa redacted")


FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_sk_proj", re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}\b")),
    ("openai_sk", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("meta_eaat", re.compile(r"\bEAAT[A-Za-z0-9_\-]{20,}\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._/\+=\-]{20,}\b", re.IGNORECASE)),
]


def _is_plausible_secret_value(value: str) -> bool:
    text = str(value or "").strip()
    if not text or is_allowed_redacted_placeholder(text):
        return False
    if re.fullmatch(r"(?i)redacted[_-].+", text):
        return True
    if REDACTED.lower() in text.lower() or "<redacted>" in text.lower():
        return len(text) > len(REDACTED)
    if re.fullmatch(r"EAAT[A-Za-z0-9_\-]{20,}", text):
        return True
    if re.fullmatch(r"sk-proj-[A-Za-z0-9_\-]{20,}", text):
        return True
    if re.fullmatch(r"sk-[A-Za-z0-9_\-]{20,}", text):
        return True
    if re.fullmatch(r"Bearer\s+[A-Za-z0-9._/\+=\-]{20,}", text, re.IGNORECASE):
        return True
    if re.fullmatch(r"[A-Za-z0-9_-]{43}=", text):
        return True
    return len(text) >= 20 and not re.search(r"\s", text)


def _is_scannable_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(key or "").strip().lower()).strip("_")
    return (is_sensitive_key(key) or normalized in SCANNER_SENSITIVE_KEYS) and normalized != "key"


def _looks_like_code_expression_reference(line: str, value: str, end_index: int) -> bool:
    """Ignore variable/function references; keep scanning literal-looking secrets."""
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", text):
        return False
    if re.fullmatch(r"EAAT[A-Za-z0-9_\-]{20,}", text):
        return False
    remainder = line[end_index:].lstrip()
    return remainder.startswith((".", "(", ",", ";", ")", "}"))


def _sensitive_key_findings(line: str) -> list[str]:
    findings: list[str] = []

    for match in re.finditer(r'"([^"]+)"\s*:\s*"([^"]*)"', line, re.IGNORECASE):
        key, value = match.group(1), match.group(2)
        if _is_scannable_sensitive_key(key) and _is_plausible_secret_value(value):
            findings.append(f"json_sensitive_key:{key}")

    for match in re.finditer(
        r"(?<![A-Za-z0-9_-])([A-Za-z0-9][A-Za-z0-9_-]*)(=)([A-Za-z0-9_/*<>\+=\-]{8,})",
        line,
        re.IGNORECASE,
    ):
        key, value = match.group(1), match.group(3)
        if _is_scannable_sensitive_key(key) and _is_plausible_secret_value(value):
            findings.append(f"query_sensitive_key:{key}")

    for match in re.finditer(
        r"(?<![A-Za-z0-9_-])([A-Za-z0-9][A-Za-z0-9_-]*)(=)[\"']([^\"']{8,})[\"']",
        line,
        re.IGNORECASE,
    ):
        key, value = match.group(1), match.group(3)
        if _is_scannable_sensitive_key(key) and _is_plausible_secret_value(value):
            findings.append(f"quoted_assignment_sensitive_key:{key}")

    for match in re.finditer(
        r"(?im)(^|[ \t])([A-Za-z0-9][A-Za-z0-9_-]*)(\s*:\s*)((?:Bearer\s+)?[A-Za-z0-9_/*<>\+=\-]{8,})",
        line,
    ):
        key, value = match.group(2), match.group(4)
        if _looks_like_code_expression_reference(line, value, match.end(4)):
            continue
        if _is_scannable_sensitive_key(key) and _is_plausible_secret_value(value):
            findings.append(f"header_sensitive_key:{key}")

    return findings


def _matching_forbidden_patterns(line: str) -> list[str]:
    findings = [kind for kind, pattern in FORBIDDEN_PATTERNS if pattern.search(line)]
    findings.extend(_sensitive_key_findings(line))
    return findings


def _line_has_forbidden_secret(line: str) -> bool:
    return bool(_matching_forbidden_patterns(line))


def _safe_snippet(line: str) -> str:
    snippet = sanitize_string(line)
    for kind, pattern in FORBIDDEN_PATTERNS:
        snippet = pattern.sub(f"<{kind}:REDACTED>", snippet)
    snippet = re.sub(
        r"(?<![A-Za-z0-9_-])([A-Za-z0-9][A-Za-z0-9_-]*)(=)[\"']([^\"']{8,})[\"']",
        lambda match: f"{match.group(1)}={REDACTED!r}" if _is_scannable_sensitive_key(match.group(1)) else match.group(0),
        snippet,
        flags=re.IGNORECASE,
    )
    snippet = re.sub(
        r"(?<![A-Za-z0-9_-])([A-Za-z0-9][A-Za-z0-9_-]*)(=)([A-Za-z0-9_/*<>\+=\-]{8,})",
        lambda match: f"{match.group(1)}={REDACTED}" if _is_scannable_sensitive_key(match.group(1)) else match.group(0),
        snippet,
        flags=re.IGNORECASE,
    )
    return snippet[:180]


def test_scanner_patterns() -> None:
    meta = fake_secret(META_PREFIX, "S", 32)
    long_query_token = fake_secret("", "Q", 32)
    generic_api_key = fake_secret("", "A", 32)
    bearer_base64 = "abcd/efgh+ijkl==mnopqrstuv123456"

    redacted_json = json.dumps({"apikey": REDACTED}, ensure_ascii=False)
    angle_redacted_json = json.dumps({"access_token": "<REDACTED>"}, ensure_ascii=False)
    official_placeholder_json = json.dumps({"access_token": "REDACTED_FOR_TEST"}, ensure_ascii=False)
    fake_local_placeholder_json = json.dumps({"access_token": "fake-local-token-ag2"}, ensure_ascii=False)
    synthetic_placeholder_json = json.dumps({"secret": "synthetic-generation-secret"}, ensure_ascii=False)
    instagram_placeholder_json = json.dumps({"access_token": "instagram-user-token"}, ensure_ascii=False)
    mixed_json_line = redacted_json + ' "' + "access_token" + '":"' + meta + '"'
    mixed_query_line = f"token={REDACTED}&access_token={long_query_token}"
    redacted_only_line = json.dumps({"apikey": REDACTED}, ensure_ascii=False)
    customer_key = "customer" + "_api_key"
    redacted_customer_line = json.dumps({customer_key: REDACTED}, ensure_ascii=False)

    assert_true(_line_has_forbidden_secret(mixed_json_line), "scanner detecta segredo real na mesma linha com redacted JSON")
    assert_true(_line_has_forbidden_secret(mixed_query_line), "scanner detecta query secret real na mesma linha com redacted")
    assert_true(not _line_has_forbidden_secret(redacted_only_line), "scanner permite linha somente com valor redacted")
    assert_true(not _line_has_forbidden_secret(angle_redacted_json), "scanner permite placeholder <REDACTED> exato")
    assert_true(not _line_has_forbidden_secret(official_placeholder_json), "scanner permite REDACTED_FOR_TEST exato")
    assert_true(not _line_has_forbidden_secret(fake_local_placeholder_json), "scanner permite placeholder local documentado exato")
    assert_true(not _line_has_forbidden_secret(synthetic_placeholder_json), "scanner permite secret sintético documentado exato")
    assert_true(not _line_has_forbidden_secret(instagram_placeholder_json), "scanner permite token sintético documentado exato")
    assert_true(_line_has_forbidden_secret(json.dumps({"X-API-Key": generic_api_key}, separators=(",", ":"))), "scanner detecta JSON X-API-Key cru")
    assert_true(_line_has_forbidden_secret(json.dumps({customer_key: generic_api_key}, separators=(",", ":"))), "scanner detecta JSON customer_api_key cru")
    assert_true(_line_has_forbidden_secret(f"x_api_key={generic_api_key}"), "scanner detecta query x_api_key cru")
    assert_true(_line_has_forbidden_secret(f"{customer_key}={generic_api_key}"), "scanner detecta query customer_api_key cru")
    assert_true(_line_has_forbidden_secret(f"X-API-Key: {generic_api_key}"), "scanner detecta header X-API-Key cru")
    assert_true(_line_has_forbidden_secret(f"{customer_key}: {generic_api_key}"), "scanner detecta header customer_api_key cru")
    assert_true(_line_has_forbidden_secret(redacted_json + " " + json.dumps({customer_key: generic_api_key}, separators=(",", ":"))), "scanner detecta customer_api_key real na mesma linha com redacted")
    assert_true(not _line_has_forbidden_secret(redacted_customer_line), "scanner permite customer_api_key redacted")
    assert_true(_line_has_forbidden_secret(f"{BEARER_PREFIX}{bearer_base64}"), "scanner detecta Bearer base64 com + / =")
    assert_true(_line_has_forbidden_secret(f"META_APP_SECRET={generic_api_key}"), "scanner detecta META_APP_SECRET plausivel")
    assert_true(_line_has_forbidden_secret(f'META_CREDENTIAL_ENCRYPTION_KEY="{generic_api_key}"'), "scanner detecta chave de criptografia plausivel")
    assert_true(_line_has_forbidden_secret(json.dumps({"access_token": "REDACTED_" + generic_api_key})), "scanner bloqueia redacted misturado com token")
    assert_true(_line_has_forbidden_secret(json.dumps({"access_token": REDACTED + generic_api_key})), "scanner bloqueia placeholder seguido de conteudo sensivel")


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for folder in (
        ROOT / "app",
        ROOT / "scripts",
        ROOT / "tests",
        ROOT / "frontend/src",
        ROOT / "frontend/dist",
        ROOT / "relatorios/codex",
    ):
        if folder.exists():
            files.extend(
                path
                for path in folder.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
            )
    files.extend(path for path in ROOT.glob("RELATORIO*.md") if path.is_file())
    return sorted(set(files))


def scan_files() -> list[str]:
    findings: list[str] = []
    for path in _iter_scan_files():
        if ".env" in path.name.lower():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line_no, line in enumerate(lines, 1):
            for kind in _matching_forbidden_patterns(line):
                findings.append(f"{path.relative_to(ROOT)}:{line_no}:{kind}:{_safe_snippet(line)}")
    return findings


def scan_env_backups() -> list[str]:
    findings: list[str] = []
    for path in sorted(ROOT.rglob(".env*")):
        if ".git" in path.parts or not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel == ".env" or rel in ALLOWED_ENV_FILES or rel.endswith(".example"):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line_no, line in enumerate(lines, 1):
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key = line.split("=", 1)[0].strip()
            value = line.split("=", 1)[1].strip().strip("'\"")
            if _is_scannable_sensitive_key(key) and _is_plausible_secret_value(value):
                findings.append(f"{rel}:{line_no}:env_backup_sensitive_assignment:{key}=<redacted>")
    return findings


def main() -> int:
    test_sanitizer_direct()
    test_api_serialization()
    test_scanner_patterns()

    findings = scan_files()
    findings.extend(scan_env_backups())
    if findings:
        print("[FAIL] Possíveis secrets completos encontrados sem imprimir valores:")
        for finding in findings[:50]:
            print(f"- {finding}")
        if len(findings) > 50:
            print(f"- ... {len(findings) - 50} achado(s) adicional(is)")
        return 1

    assert_true(True, "scanner global não encontrou OpenAI/Meta/Bearer/Evolution cru")
    assert_true(True, ".env não é lido nem impresso")
    assert_true(True, "teste não chama OpenAI real")
    print("verificar_secrets_nao_expostos: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
