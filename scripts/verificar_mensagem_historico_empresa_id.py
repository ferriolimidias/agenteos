from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEARCH_DIRS = ("app", "db", "scripts", "tests")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def iter_python_files() -> list[Path]:
    files: list[Path] = []
    for directory in SEARCH_DIRS:
        root = ROOT / directory
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def is_mensagem_historico_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Name) and func.id == "MensagemHistorico"


def function_name(parents: list[ast.AST]) -> str:
    for parent in reversed(parents):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
    return "<module>"


class CallVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.parents: list[ast.AST] = []
        self.calls: list[dict[str, object]] = []

    def visit(self, node: ast.AST) -> None:  # noqa: D401
        self.parents.append(node)
        try:
            super().visit(node)
        finally:
            self.parents.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if is_mensagem_historico_call(node):
            keyword_names = {keyword.arg for keyword in node.keywords if keyword.arg}
            self.calls.append(
                {
                    "path": str(self.path.relative_to(ROOT)),
                    "line": node.lineno,
                    "function": function_name(self.parents[:-1]),
                    "has_empresa_id": "empresa_id" in keyword_names,
                    "keywords": sorted(keyword_names),
                }
            )
        self.generic_visit(node)


def collect_calls() -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    for path in iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise AssertionError(f"arquivo Python invalido: {path}: {exc}") from exc
        visitor = CallVisitor(path)
        visitor.visit(tree)
        calls.extend(visitor.calls)
    return calls


def main() -> int:
    calls = collect_calls()
    missing = [call for call in calls if not call["has_empresa_id"]]

    for call in calls:
        print(
            f"{call['path']}:{call['line']} function={call['function']} "
            f"empresa_id={'sim' if call['has_empresa_id'] else 'nao'}"
        )

    required_functions = {
        "_salvar_historico_saida_ia",
        "enviar_mensagem",
        "enviar_midia",
        "save_history_and_check_pause",
        "_persist_event",
        "_materialize_campaign_history",
        "_dispatch_followup_execution",
        "_persist_private_reply_outbound",
        "process_direct_message",
    }
    observed_functions = {str(call["function"]) for call in calls}
    assert_true(
        required_functions.issubset(observed_functions),
        f"caminhos canonicos de persistencia cobertos (total={len(calls)})",
    )
    assert_true(not missing, "todos os construtores executaveis passam empresa_id")

    utils = (ROOT / "app/api/utils.py").read_text(encoding="utf-8")
    inbox = (ROOT / "app/api/routers/inbox.py").read_text(encoding="utf-8")
    assert_true("message_history.tenant_mismatch" in utils, "helpers registram tenant mismatch")
    assert_true("Conexao.empresa_id == empresa_uuid" in utils, "helpers validam conexao contra tenant")
    assert_true("CRMLead.empresa_id == empresa_uuid" in inbox, "Inbox busca lead tenant-safe")
    assert_true("Conexao.empresa_id == empresa_uuid" in inbox, "Inbox busca conexao tenant-safe")
    assert_true("external_message_id=event.external_event_id" in (ROOT / "app/api/routers/meta_webhook.py").read_text(encoding="utf-8"), "Meta inbound preserva external_message_id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
