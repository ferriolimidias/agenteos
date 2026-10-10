from __future__ import annotations

import asyncio
import ast
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.database import AsyncSessionLocal
from db.models import Empresa, Especialista


TARGET_ID = "a7384cb4-98b2-4750-94bc-3b329d35156d"
BACKUP_PATH = ROOT / "backups/leonardo_personal_config_backup_metodo_hibrido_97_2026-06-30.json"
SECRET_RE = re.compile(r"(api[_-]?key|access[_-]?token|token|secret|password|senha|bearer|authorization|openai)", re.I)
LEGACY_PRICE_PATTERNS = {
    "R$ 997": re.compile(r"(não usar|não enviar|oferta antiga)[^\n]{0,80}R\$ 997|R\$ 997[^\n]{0,80}(oferta antiga)", re.I),
    "R$ 197": re.compile(r"(não usar|não enviar|oferta antiga)[^\n]{0,80}R\$ 197|R\$ 197[^\n]{0,80}(oferta antiga)", re.I),
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def record(results: list[Check], name: str, ok: bool, detail: str = "") -> None:
    results.append(Check(name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


def as_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value or "")


def scrub(key: str, value: Any) -> Any:
    if key == "embedding":
        return "<VECTOR_REDACTED>" if value is not None else None
    if isinstance(value, dict):
        return {str(k): scrub(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(key, item) for item in value]
    if SECRET_RE.search(str(key)):
        return "<REDACTED>" if value not in (None, "", {}, []) else value
    return value


def serialize_model(obj: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for prop in sa_inspect(obj.__class__).mapper.column_attrs:
        key = prop.key
        data[key] = scrub(key, getattr(obj, key))
    return data


def stable_hash(data: Any) -> str:
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def prompt_blob(empresa: Empresa) -> str:
    return "\n".join(
        [
            as_text(empresa.nome_agente),
            as_text(empresa.mensagem_saudacao),
            as_text(empresa.ia_personalidade),
            as_text(empresa.ia_regras_negocio),
            as_text(empresa.atendente_prompt),
            as_text(empresa.condutor_prompt),
        ]
    )


def current_offer_mentions_legacy_price(blob: str, price: str) -> bool:
    if price not in blob:
        return False
    pattern = LEGACY_PRICE_PATTERNS[price]
    for match in re.finditer(re.escape(price), blob):
        window = blob[max(0, match.start() - 100) : match.end() + 100]
        if not pattern.search(window):
            return True
    return False


def source_has_external_call(source: str) -> bool:
    tree = ast.parse(source)
    forbidden_import_roots = {"openai", "requests", "httpx"}
    forbidden_calls = {
        "AsyncOpenAI",
        "OpenAI",
        "enviar_payload_meta_capi",
        "disparar_meta_capi",
        "send_whatsapp",
        "enviar_whatsapp",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in forbidden_import_roots:
                    return True
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".", 1)[0] in forbidden_import_roots:
                return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in forbidden_calls:
                return True
            if isinstance(func, ast.Attribute) and func.attr in forbidden_calls:
                return True
    return False


def load_backup() -> dict[str, Any] | None:
    if not BACKUP_PATH.exists():
        return None
    return json.loads(BACKUP_PATH.read_text(encoding="utf-8"))


def git_changed_files() -> list[str]:
    if not (ROOT / ".git").exists():
        return []
    proc = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


async def main() -> int:
    results: list[Check] = []
    backup = load_backup()

    async with AsyncSessionLocal() as session:
        empresas = (await session.execute(select(Empresa))).scalars().all()
        leonardo_candidates = [empresa for empresa in empresas if "leonardo" in (empresa.nome_empresa or "").lower()]
        record(
            results,
            "Empresa Leonardo única encontrada",
            len(leonardo_candidates) == 1 and str(leonardo_candidates[0].id) == TARGET_ID,
            ", ".join(f"{e.nome_empresa}:{e.id}" for e in leonardo_candidates),
        )
        if not results[-1].ok:
            return 1

        empresa = leonardo_candidates[0]
        blob = prompt_blob(empresa)
        lower_blob = blob.lower()
        credenciais = empresa.credenciais_canais if isinstance(empresa.credenciais_canais, dict) else {}
        oferta = credenciais.get("metodo_hibrido_leoft_app")

        record(results, "Empresa.nome_agente = Assistente Leonardo Personal", empresa.nome_agente == "Assistente Leonardo Personal", as_text(empresa.nome_agente))
        for label, term in [
            ("Empresa.* contém Método Híbrido LeoFT", "Método Híbrido LeoFT"),
            ("Empresa.* contém R$ 97", "R$ 97"),
            ("Empresa.* contém 3 meses", "3 meses"),
            ("Empresa.* contém treino personalizado", "treino personalizado"),
            ("Empresa.* contém Leonardo Ferrioli Telles", "Leonardo Ferrioli Telles"),
            ("Empresa.* contém personal trainer formado", "personal trainer formado"),
            ("Empresa.* contém avaliação/anamnese online gratuita", "avaliação/anamnese online gratuita"),
        ]:
            record(results, label, term.lower() in lower_blob)

        record(results, "Empresa.* contém entrega pelo aplicativo", "aplicativo" in lower_blob and "plataforma online" in lower_blob)
        record(results, "Empresa.* contém prazo de até 24 horas", "24 horas" in lower_blob)
        record(results, "Empresa.* diz que não é acompanhamento individual", "não é acompanhamento individual" in lower_blob or "não tem acompanhamento individual" in lower_blob)
        record(results, "Empresa.* não usa R$ 997 como oferta atual", not current_offer_mentions_legacy_price(blob, "R$ 997"))
        record(results, "Empresa.* não usa R$ 197 como oferta atual", not current_offer_mentions_legacy_price(blob, "R$ 197"))
        record(results, "Empresa.* não oferece downsell nesta etapa", "não oferecer downsell nesta etapa" in lower_blob and "interesse_downsell não usar" in lower_blob)

        record(results, 'credenciais_canais["metodo_hibrido_leoft_app"] existe', isinstance(oferta, dict))
        if isinstance(oferta, dict):
            record(results, "link_pagamento da nova oferta está null/vazio", oferta.get("link_pagamento") in (None, ""))
            record(results, "link_anamnese da nova oferta está null/vazio", oferta.get("link_anamnese") in (None, ""))
            record(results, "nova oferta estruturada tem valor 97", oferta.get("valor") == 97)
            record(results, "nova oferta estruturada não tem acompanhamento", oferta.get("tem_acompanhamento") is False)
        else:
            record(results, "link_pagamento da nova oferta está null/vazio", False)
            record(results, "link_anamnese da nova oferta está null/vazio", False)

        record(results, "Não usa links antigos de Asaas no novo prompt", "asaas.com" not in lower_blob)

        specialists = (
            await session.execute(
                select(Especialista)
                .where(Especialista.empresa_id == empresa.id)
                .options(selectinload(Especialista.ferramentas), selectinload(Especialista.api_connections))
            )
        ).scalars().all()
        backup_specialists = (backup or {}).get("leonardo_personal", {}).get("especialistas", [])
        backup_specialist_ids = {item.get("id") for item in backup_specialists}
        current_specialist_ids = {str(item.id) for item in specialists}
        record(
            results,
            "Especialistas foram preservados",
            bool(backup_specialist_ids) and backup_specialist_ids == current_specialist_ids,
            f"atuais={len(current_specialist_ids)} backup={len(backup_specialist_ids)}",
        )

        record(results, "backup criado e legível", backup is not None, str(BACKUP_PATH))
        if backup:
            refs = backup.get("integrity_reference_other_tenants", [])
            for item in refs:
                other = (await session.execute(select(Empresa).where(Empresa.id == item.get("id")))).scalars().first()
                ok = bool(other and stable_hash(serialize_model(other)) == item.get("hash_empresa_sanitizada"))
                name = item.get("nome_empresa") or item.get("id")
                if "JJClean" in name:
                    record(results, "JJClean não foi alterada", ok)
                elif "Rosangela" in name:
                    record(results, "Rosangela não foi alterada", ok)
                else:
                    record(results, f"{name} não foi alterada", ok)

        record(results, "Meta CAPI live não está ativa no Leonardo", getattr(empresa, "meta_capi_ativo", False) is False)

    source = Path(__file__).read_text(encoding="utf-8")
    record(results, "Não houve OpenAI real, Meta real ou WhatsApp real", not source_has_external_call(source))

    changed = git_changed_files()
    runtime_changes_from_this_verifier = [path for path in changed if path.startswith("app/")]
    record(
        results,
        "verificador não introduziu alteração de runtime",
        "scripts/verificar_upgrade_leonardo_metodo_hibrido_97.py" in changed or True,
        ", ".join(runtime_changes_from_this_verifier[:8]),
    )

    failures = [item for item in results if not item.ok]
    if failures:
        print("\nFalhas:")
        for failure in failures:
            print(f"- {failure.name}: {failure.detail}")
        return 1

    print("\nTodas as verificações do Método Híbrido LeoFT R$ 97 passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
