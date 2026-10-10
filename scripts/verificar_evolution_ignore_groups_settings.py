from __future__ import annotations

import asyncio
import ast
import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.evolution_service import (
    aplicar_settings_padrao_evolution,
    endpoint_find_settings_evolution,
    endpoint_settings_evolution,
    payload_settings_padrao_evolution,
)
from scripts import aplicar_ignore_groups_evolution_existentes as backfill


class FakeResponse:
    status_code = 200
    text = '{"ok":true}'
    content = b'{"ok":true}'

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload or {"ok": True}

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get(self, endpoint: str, headers: dict):
        self.calls.append({"method": "GET", "endpoint": endpoint, "headers": headers})
        return FakeResponse(
            {
                "rejectCall": False,
                "msgCall": "",
                "groupsIgnore": False,
                "alwaysOnline": False,
                "readMessages": False,
                "readStatus": False,
                "syncFullHistory": False,
            }
        )

    async def post(self, endpoint: str, headers: dict, json: dict):
        self.calls.append({"method": "POST", "endpoint": endpoint, "headers": headers, "json": json})
        return FakeResponse()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"OK - {message}")


async def check_helper_request() -> None:
    fake = FakeClient()
    secret = "secret-key-123456"
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        result = await aplicar_settings_padrao_evolution(
            "https://evolution.local/",
            secret,
            "wa_test",
            client=fake,
        )
    output = capture.getvalue()
    assert_true(result["success"] is True, "helper retorna sucesso com HTTP fake")
    assert_true(len(fake.calls) == 2, "helper consulta e aplica settings com HTTP fake")
    find_call = fake.calls[0]
    call = fake.calls[1]
    assert_true(
        find_call["endpoint"] == "https://evolution.local/settings/find/wa_test",
        "helper consulta endpoint /settings/find/{instance}",
    )
    assert_true(
        call["endpoint"] == "https://evolution.local/settings/set/wa_test",
        "helper monta endpoint /settings/set/{instance}",
    )
    assert_true(call["json"]["groupsIgnore"] is True, "helper envia groupsIgnore=true")
    assert_true(
        all(key in call["json"] for key in ("rejectCall", "alwaysOnline", "readMessages", "readStatus", "syncFullHistory")),
        "helper envia campos obrigatórios da Evolution atual",
    )
    assert_true(secret not in output, "API key não aparece nos logs do helper")
    assert_true("***" in output, "helper mascara API key nos logs")


async def check_backfill_modes() -> None:
    class FakeConexao:
        id = "conexao-1"
        empresa_id = "empresa-1"
        nome_instancia = "wa_existing"
        status = "ativo"
        credenciais = {
            "evolution_url": "https://evolution.local",
            "evolution_apikey": "local-key",
            "evolution_instance": "wa_existing",
        }
        empresa = SimpleNamespace(nome_empresa="Empresa Teste")

    original_list = backfill.listar_conexoes_evolution
    original_apply = backfill.aplicar_settings_padrao_evolution
    original_global_key = backfill.evolution_global_api_key
    calls: list[tuple] = []

    async def fake_list():
        return [FakeConexao()]

    async def fake_apply(base_url: str, api_key: str, instance_name: str):
        calls.append((base_url, api_key, instance_name))
        return {"success": True}

    try:
        backfill.listar_conexoes_evolution = fake_list
        backfill.aplicar_settings_padrao_evolution = fake_apply
        backfill.evolution_global_api_key = lambda: ""
        dry_rc = await backfill.run("dry-run")
        assert_true(dry_rc == 0, "script --dry-run finaliza com sucesso")
        assert_true(calls == [], "script --dry-run não chama HTTP/helper")
        apply_rc = await backfill.run("apply")
        assert_true(apply_rc == 0, "script --apply finaliza com sucesso com fake HTTP")
        assert_true(
            calls == [("https://evolution.local", "local-key", "wa_existing")],
            "script --apply chama helper com credenciais da conexão",
        )
    finally:
        backfill.listar_conexoes_evolution = original_list
        backfill.aplicar_settings_padrao_evolution = original_apply
        backfill.evolution_global_api_key = original_global_key


def check_source_calls() -> None:
    service = (ROOT / "app/services/evolution_service.py").read_text(encoding="utf-8")
    router = (ROOT / "app/api/routers/conexoes.py").read_text(encoding="utf-8")
    assert_true("aplicar_settings_padrao_evolution" in service, "helper central existe no evolution_service")
    assert_true(service.count("aplicar_settings_padrao_evolution(") >= 4, "fluxo de criação/reuso chama o helper")
    assert_true("settings/set" in service, "endpoint settings/set fica centralizado no serviço Evolution")
    assert_true("aplicar_settings_padrao_evolution" in router, "criação/atualização administrativa chama o helper")


def check_no_runtime_llm_first_changes() -> None:
    forbidden = {
        "app/agents/atendimento/agent.py",
        "app/agents/planner/tool_planner.py",
        "app/agents/fiscal/agent.py",
        "app/agents/dispatch/dispatcher.py",
        "app/services/channel_factory.py",
        "app/api/utils.py",
        "app/api/routers/inbox.py",
    }
    if not shutil.which("git"):
        touched_by_this_feature = []
        markers = ("groupsIgnore", "groups_ignore", "settings/set", "aplicar_settings_padrao_evolution")
        for relpath in forbidden:
            path = ROOT / relpath
            if path.exists() and any(marker in path.read_text(encoding="utf-8", errors="ignore") for marker in markers):
                touched_by_this_feature.append(relpath)
        assert_true(
            not touched_by_this_feature,
            f"runtime LLM-first/dispatcher/inbox não contém lógica de ignore groups: {touched_by_this_feature}",
        )
        return

    changed = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    names = set(changed.stdout.splitlines())
    touched = sorted(names & forbidden)
    assert_true(not touched, f"não houve alteração em runtime LLM-first/dispatcher/inbox: {touched}")


def check_no_forbidden_evolution_operations_in_backfill() -> None:
    source = (ROOT / "scripts/aplicar_ignore_groups_evolution_existentes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_fragments = ["instance/delete", "instance/logout", "message/send"]
    assert_true(
        all(fragment not in source for fragment in forbidden_fragments),
        "script de backfill não contém delete/logout/envio",
    )
    uses_delete = any(isinstance(node, ast.Attribute) and node.attr == "delete" for node in ast.walk(tree))
    assert_true(not uses_delete, "script de backfill não usa HTTP DELETE")


async def main() -> int:
    assert_true(
        endpoint_find_settings_evolution("https://evolution.local/", "wa_test")
        == "https://evolution.local/settings/find/wa_test",
        "função de endpoint find remove barra duplicada",
    )
    assert_true(
        endpoint_settings_evolution("https://evolution.local/", "wa_test")
        == "https://evolution.local/settings/set/wa_test",
        "função de endpoint remove barra duplicada",
    )
    assert_true(payload_settings_padrao_evolution()["groupsIgnore"] is True, "payload usa padrão camelCase do projeto")
    assert_true("groups_ignore" not in payload_settings_padrao_evolution(), "payload não usa snake_case")
    await check_helper_request()
    await check_backfill_modes()
    check_source_calls()
    check_no_runtime_llm_first_changes()
    check_no_forbidden_evolution_operations_in_backfill()
    print("\nverificar_evolution_ignore_groups_settings: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
