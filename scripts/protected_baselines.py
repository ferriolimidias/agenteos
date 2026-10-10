#!/usr/bin/env python3
"""Validate protected AgentOS production baselines.

The validator intentionally avoids production data. It checks versioned contracts
against the workspace and, when provided, against a candidate image or runtime
tree. Candidate image validation imports the FastAPI app inside the image so a
route present only in the workspace still blocks deployment.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROUTE_MARKER = "__PROTECTED_BASELINE_ROUTES__="


@dataclass(frozen=True)
class Finding:
    module: str
    contract: str
    source: str
    detail: str = ""
    path: str = ""
    route: str = ""
    method: str = ""


def _normalize_path(value: str) -> str:
    if not value:
        return "/"
    value = value.replace("//", "/")
    if not value.startswith("/"):
        value = "/" + value
    if len(value) > 1 and value.endswith("/"):
        value = value[:-1]
    return value


def _join_paths(*parts: str) -> str:
    joined = "/".join(str(part or "").strip("/") for part in parts if str(part or "").strip("/"))
    return _normalize_path(joined)


def _literal_string(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                pieces.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                pieces.append("{param}")
        return "".join(pieces)
    return ""


def _call_keyword(call: ast.Call, name: str) -> str:
    for keyword in call.keywords:
        if keyword.arg == name:
            return _literal_string(keyword.value)
    return ""


def _expr_name(expr: ast.AST) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        parts = [expr.attr]
        value = expr.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


def _call_name(call: ast.Call) -> str:
    return _expr_name(call.func)


def _parse_router_vars(router_path: Path) -> dict[str, str]:
    try:
        tree = ast.parse(router_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    routers: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if not _call_name(node.value).endswith("APIRouter"):
            continue
        prefix = _call_keyword(node.value, "prefix")
        for target in node.targets:
            if isinstance(target, ast.Name):
                routers[target.id] = prefix
    return routers


def _parse_module_routes(router_path: Path) -> dict[str, set[str]]:
    routers = _parse_router_vars(router_path)
    if not routers:
        return {}
    try:
        tree = ast.parse(router_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    routes: dict[str, set[str]] = {}
    methods = {"get", "post", "put", "delete", "patch"}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.lower()
            if method not in methods:
                continue
            owner = decorator.func.value
            if not isinstance(owner, ast.Name) or owner.id not in routers:
                continue
            subpath = _literal_string(decorator.args[0]) if decorator.args else ""
            full_path = _join_paths(routers[owner.id], subpath)
            routes.setdefault(full_path, set()).add(method.upper())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        inner = node.func
        if not isinstance(inner, ast.Call):
            continue
        if not isinstance(inner.func, ast.Attribute):
            continue
        method = inner.func.attr.lower()
        if method not in methods:
            continue
        owner = inner.func.value
        if not isinstance(owner, ast.Name) or owner.id not in routers:
            continue
        subpath = _literal_string(inner.args[0]) if inner.args else ""
        full_path = _join_paths(routers[owner.id], subpath)
        routes.setdefault(full_path, set()).add(method.upper())
    return routes


def _imported_router_modules(main_path: Path) -> dict[str, str]:
    try:
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    modules: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "app.api.routers":
            for alias in node.names:
                modules[alias.asname or alias.name] = alias.name
    return modules


def _include_router_calls(main_path: Path) -> list[tuple[str, str, str]]:
    try:
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    includes: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) != "app.include_router":
            continue
        if not node.args:
            continue
        route_expr = _expr_name(node.args[0])
        if not route_expr:
            continue
        parts = route_expr.split(".")
        if len(parts) < 2:
            continue
        include_prefix = _call_keyword(node, "prefix")
        includes.append((parts[0], parts[1], include_prefix))
    return includes


def extract_routes_static(root: Path) -> dict[str, set[str]]:
    main_path = root / "app" / "api" / "main.py"
    imported_modules = _imported_router_modules(main_path)
    route_map: dict[str, set[str]] = {}
    for module_alias, router_var, include_prefix in _include_router_calls(main_path):
        module_name = imported_modules.get(module_alias, module_alias)
        router_path = root / "app" / "api" / "routers" / f"{module_name}.py"
        module_routes = _parse_module_routes(router_path)
        router_prefixes = _parse_router_vars(router_path)
        expected_prefix = router_prefixes.get(router_var, "")
        for path, methods in module_routes.items():
            if expected_prefix and not path.startswith(_normalize_path(expected_prefix)):
                continue
            full_path = _join_paths(include_prefix, path)
            route_map.setdefault(full_path, set()).update(methods)
    return route_map


ROUTE_EXTRACTOR = r"""
import json
import os
import sys

os.environ.setdefault("STARTUP_DATABASE_MODE", "skip")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/agent_os")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "protected-baseline-local-secret")
os.environ.setdefault("SECRET_KEY", "protected-baseline-local-secret")
os.environ.setdefault("EVOLUTION_API_URL", "http://127.0.0.1:8080")
os.environ.setdefault("EVOLUTION_API_TOKEN", "protected-baseline-token")

from app.api.main import app

routes = {}
for route in app.routes:
    path = getattr(route, "path", "")
    methods = sorted(m for m in getattr(route, "methods", set()) if m not in {"HEAD", "OPTIONS"})
    if path:
        routes[path] = sorted(set(routes.get(path, [])) | set(methods))
print("__PROTECTED_BASELINE_ROUTES__=" + json.dumps(routes, sort_keys=True))
"""


def _parse_route_extractor_output(stdout: str) -> dict[str, set[str]]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(ROUTE_MARKER):
            raw = json.loads(line[len(ROUTE_MARKER):])
            return {path: set(methods) for path, methods in raw.items()}
    raise RuntimeError("route extractor marker not found")


def extract_routes_from_image(image: str, timeout: int = 60) -> dict[str, set[str]]:
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "python",
        image,
        "-c",
        ROUTE_EXTRACTOR,
    ]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "candidate image route extraction failed").strip())
    return _parse_route_extractor_output(proc.stdout)


def load_route_map_file(path: Path) -> dict[str, set[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["path"]: set(item.get("methods", [])) for item in data.get("routes", [])}


def load_baselines(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_routes(modules: list[dict[str, Any]], route_map: dict[str, set[str]], source: str) -> list[Finding]:
    findings: list[Finding] = []
    for module in modules:
        module_name = module["name"]
        for route in module.get("required_routes", []):
            route_path = route["path"]
            actual_methods = route_map.get(route_path)
            if actual_methods is None:
                findings.append(Finding(module_name, "required_route_missing", source, route=route_path))
                continue
            for method in route.get("methods", []):
                if method.upper() not in actual_methods:
                    findings.append(Finding(module_name, "required_method_missing", source, route=route_path, method=method.upper()))
    return findings


def _read_file(root: Path, relative: str) -> str | None:
    target = root / relative
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8", errors="ignore")


def check_workspace_files(modules: list[dict[str, Any]], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for module in modules:
        module_name = module["name"]
        for relative in module.get("required_files", []):
            if not (root / relative).is_file():
                findings.append(Finding(module_name, "required_file_missing", "workspace", path=relative))
        for item in module.get("required_text", []):
            content = _read_file(root, item["path"])
            if content is None:
                findings.append(Finding(module_name, "required_text_file_missing", "workspace", path=item["path"], detail=item.get("contract", "")))
            elif item["contains"] not in content:
                findings.append(Finding(module_name, "required_text_missing", "workspace", path=item["path"], detail=item.get("contract", "")))
        for item in module.get("forbidden_regex", []):
            content = _read_file(root, item["path"])
            if content is not None and re.search(item["regex"], content):
                findings.append(Finding(module_name, "forbidden_regex_present", "workspace", path=item["path"], detail=item.get("contract", "")))
    return findings


def check_tenant_contracts(modules: list[dict[str, Any]], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    instagram_source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (root / "app").glob("**/*instagram*.py")
        if path.is_file()
    ) if (root / "app").exists() else ""
    tenant_expectations = {
        "connection_list_is_scoped_by_empresa_id": "Conexao.empresa_id == empresa",
        "connection_create_is_scoped_by_empresa_id": "empresa_id=empresa",
        "connection_update_is_scoped_by_empresa_id_and_connection_id": "Conexao.id == connection_id",
        "connection_delete_is_scoped_by_empresa_id_and_connection_id": "Conexao.id == connection_id",
        "company_without_connection_returns_empty_success": "\"connections\"",
        "company_a_connection_never_appears_for_company_b": "Conexao.empresa_id == empresa",
        "automation_lookup_is_scoped_by_empresa_id": "InstagramCommentAutomation.empresa_id == empresa",
        "automation_lookup_is_scoped_by_connection_id": "InstagramCommentAutomation.connection_id == connection_id",
        "automation_mutation_is_scoped_by_empresa_id": "InstagramCommentAutomation.empresa_id == empresa",
    }
    for module in modules:
        for contract in module.get("tenant_contracts", []):
            needle = tenant_expectations.get(contract)
            if needle and needle not in instagram_source:
                findings.append(Finding(module["name"], "tenant_contract_missing", "workspace", detail=contract))

    sql_pattern = re.compile(r"\b(UPDATE|DELETE\s+FROM)\s+conexoes\b(?:(?!;).){0,240};", re.IGNORECASE | re.DOTALL)
    for path in root.glob("app/**/*.py"):
        content = path.read_text(encoding="utf-8", errors="ignore")
        for match in sql_pattern.finditer(content):
            if "empresa_id" not in match.group(0):
                findings.append(
                    Finding(
                        "instagram_connections",
                        "tenant_scope_missing_on_connection_write",
                        "workspace",
                        path=str(path.relative_to(root)),
                    )
                )
    return findings


def format_failure(finding: Finding) -> str:
    lines = ["PROTECTED_BASELINE_FAILED"]
    lines.append(f"module={finding.module}")
    lines.append(f"contract={finding.contract}")
    if finding.source:
        lines.append(f"source={finding.source}")
    if finding.route:
        lines.append(f"route={finding.route}")
    if finding.method:
        lines.append(f"method={finding.method}")
    if finding.path:
        lines.append(f"path={finding.path}")
    if finding.detail:
        lines.append(f"detail={finding.detail}")
    lines.append("deployment_allowed=false")
    return "\n".join(lines)


def format_success(modules: Iterable[str], candidate_checked: bool) -> str:
    return "\n".join(
        [
            "PROTECTED_BASELINES_PASS",
            "deployment_allowed=true",
            "workspace_checked=true",
            f"candidate_image_checked={str(candidate_checked).lower()}",
            "protected_modules=" + ",".join(modules),
        ]
    )


def validate(args: argparse.Namespace) -> tuple[int, str]:
    root = Path(args.workspace_root).resolve()
    baseline = load_baselines(Path(args.baselines).resolve())
    modules = baseline.get("modules", [])
    module_names = [module["name"] for module in modules]

    if args.workspace_route_map:
        workspace_routes = load_route_map_file(Path(args.workspace_route_map))
    else:
        workspace_routes = extract_routes_static(root)

    findings: list[Finding] = []
    findings.extend(check_routes(modules, workspace_routes, "workspace"))
    findings.extend(check_workspace_files(modules, root))
    findings.extend(check_tenant_contracts(modules, root))

    candidate_checked = False
    candidate_routes: dict[str, set[str]] | None = None
    if args.candidate_route_map:
        candidate_checked = True
        candidate_routes = load_route_map_file(Path(args.candidate_route_map))
    elif args.candidate_root:
        candidate_checked = True
        candidate_routes = extract_routes_static(Path(args.candidate_root).resolve())
    elif args.candidate_image:
        candidate_checked = True
        try:
            candidate_routes = extract_routes_from_image(args.candidate_image, timeout=args.image_timeout)
        except Exception as exc:
            findings.append(Finding("candidate_image", "candidate_route_extraction_failed", "candidate", detail=str(exc)[:500]))

    if candidate_routes is not None:
        findings.extend(check_routes(modules, candidate_routes, "candidate"))

    if args.require_candidate and not candidate_checked:
        findings.append(Finding("candidate_image", "candidate_validation_required", "candidate"))

    if findings:
        return 1, format_failure(findings[0])
    return 0, format_success(module_names, candidate_checked)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate protected production baselines.")
    parser.add_argument("--workspace-root", default=os.getcwd())
    parser.add_argument("--baselines", default=str(Path(os.getcwd()) / "protected_baselines" / "baselines.json"))
    parser.add_argument("--candidate-image", default=os.getenv("CANDIDATE_IMAGE", ""))
    parser.add_argument("--candidate-root", default="")
    parser.add_argument("--candidate-route-map", default="")
    parser.add_argument("--workspace-route-map", default="")
    parser.add_argument("--require-candidate", action="store_true")
    parser.add_argument("--image-timeout", type=int, default=60)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    code, output = validate(args)
    print(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
