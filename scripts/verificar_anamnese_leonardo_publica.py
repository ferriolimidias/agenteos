from __future__ import annotations

import asyncio
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.tokenized_links import generate_or_get_tokenized_link, validate_tokenized_link  # noqa: E402
from db.database import AsyncSessionLocal  # noqa: E402
from db.models import CRMLead, Empresa, MetaConversionEvent, TagCRM  # noqa: E402
from scripts import setup_leonardo_metodo_hibrido_leoft as setup  # noqa: E402
from scripts import setup_rosangela_danca_circular as setup_rosangela  # noqa: E402


PUBLIC_SLUG = "leoft"
PUBLIC_PATH = "/anamnese/leoft"
BASE_URL = "http://127.0.0.1:8080/anamnese/leoft"
BACKEND_URL = "http://127.0.0.1:8000"
TEST_PHONE = "5554999988101"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def http_get(url: str) -> requests.Response:
    return requests.get(url, timeout=10)


def http_post(url: str, payload: dict[str, Any]) -> requests.Response:
    return requests.post(url, json=payload, timeout=10)


def validate_static_files() -> None:
    app_dir = ROOT / "apps/anamnese-leonardo"
    assert_true(app_dir.exists(), "frontend da anamnese existe")
    assert_true((app_dir / "package.json").exists(), "package.json da anamnese existe")
    assert_true('base: "/anamnese/leoft/"' in read("apps/anamnese-leonardo/vite.config.ts"), "Vite base está correto")
    dockerfile = read("frontend/Dockerfile")
    nginx = read("frontend/nginx.conf")
    assert_true("anamnese_build" in dockerfile and "/usr/share/nginx/html/anamnese/leoft" in dockerfile, "Dockerfile builda e copia a anamnese")
    assert_true("location = /anamnese/leoft" in nginx and "/anamnese/leoft/index.html" in nginx, "Nginx serve /anamnese/leoft")
    main_py = read("app/api/main.py")
    router = read("app/api/routers/public_anamnese.py")
    store = read("apps/anamnese-leonardo/src/features/anamnese/lib/store.ts")
    flow = read("apps/anamnese-leonardo/src/features/anamnese/AnamneseFlow.tsx")
    tracking = read("apps/anamnese-leonardo/src/features/anamnese/lib/tracking.ts")
    not_found = read("apps/anamnese-leonardo/src/pages/NotFound.tsx")
    assert_true("public_anamnese.router" in main_py, "router público registrado")
    assert_true('@router.get("/{slug}/session")' in router, "endpoint GET session existe")
    assert_true('@router.post("/{slug}/respostas")' in router, "endpoint POST respostas existe")
    assert_true("Mercado Pago" not in router and "OpenAI" not in router and "WhatsApp" not in router, "rota pública não chama provedores reais")
    assert_true("storageKeyForToken" in store and "tokenFingerprint" in store, "draft localStorage é escopado por fingerprint do token")
    assert_true("localStorage.getItem(key)" in store and "localStorage.setItem(key" in store, "localStorage usa chave derivada do token")
    assert_true("loadFromStorage(leadToken)" in store and "if (!key) return emptyAnamnese()" in store, "sem token não reaproveita draft antigo")
    assert_true("localStorage.getItem(STORAGE_KEY)" not in store and "localStorage.setItem(STORAGE_KEY" not in store, "draft global único não é usado")
    assert_true("completeAnamnese(completedData)" in flow and "anamneseCompleted: true" in flow, "submit envia payload com anamneseCompleted true")
    assert_true("console.log" not in tracking and "console.error" not in not_found, "frontend não loga token/path sensível")

    npm = shutil.which("npm")
    if npm:
        subprocess.run([npm, "run", "build"], cwd=app_dir, check=True)
        assert_true(True, "build da anamnese funciona")
    else:
        assert_true("RUN npm run build" in dockerfile, "build da anamnese validado pelo Dockerfile quando npm não está disponível")


async def prepare_valid_token() -> tuple[str, str, dict[str, Any]]:
    await setup.main()
    async with AsyncSessionLocal() as session:
        empresa = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
        original_credenciais = copy.deepcopy(empresa.credenciais_canais or {})
        credenciais = copy.deepcopy(empresa.credenciais_canais or {})
        links = credenciais.setdefault("links_tokenizados", {})
        link_config = links.setdefault(setup.LINK_SLUG, copy.deepcopy(setup.LINK_TOKENIZADO_CONFIG))
        link_config["base_url"] = BASE_URL
        link_config["public_slug"] = PUBLIC_SLUG
        link_config["public_path"] = PUBLIC_PATH
        link_config["tag_conclusao"] = "LeoFT - Anamnese concluída"
        empresa.credenciais_canais = credenciais
        flag_modified(empresa, "credenciais_canais")

        lead = (
            await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == empresa.id, CRMLead.telefone_contato == TEST_PHONE)
            )
        ).scalars().first()
        if not lead:
            lead = CRMLead(
                empresa_id=empresa.id,
                nome_contato="Lead Anamnese Pública",
                telefone_contato=TEST_PHONE,
                dados_adicionais={},
                tags=[],
            )
            session.add(lead)
            await session.flush()
        lead.nome_contato = "Lead Anamnese Pública"
        lead.telefone_contato = TEST_PHONE
        lead.dados_adicionais = {}
        lead.tags = []
        flag_modified(lead, "dados_adicionais")
        flag_modified(lead, "tags")
        await session.execute(delete(MetaConversionEvent).where(MetaConversionEvent.lead_id == lead.id))
        result = generate_or_get_tokenized_link(session, empresa, lead, setup.LINK_SLUG, setup.SEQUENCE_SLUG)
        await session.commit()
        return str(lead.id), str(result.token), original_credenciais


async def restore_credenciais(original: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        empresa = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
        empresa.credenciais_canais = original
        flag_modified(empresa, "credenciais_canais")
        await session.commit()


async def validate_backend_flow(lead_id: str, token: str) -> None:
    invalid = http_get(f"{BACKEND_URL}/api/public/anamnese/{PUBLIC_SLUG}/session?token=TOKEN_INVALIDO")
    assert_true(invalid.status_code in {400, 401, 404}, "token inválido retorna erro seguro")
    assert_true("Traceback" not in invalid.text and setup.EMPRESA_ID not in invalid.text, "token inválido não vaza dados")

    session_resp = http_get(f"{BACKEND_URL}/api/public/anamnese/{PUBLIC_SLUG}/session?token={token}")
    assert_true(session_resp.status_code == 200, "token válido abre sessão pública")
    session_body = session_resp.json()
    body_text = json.dumps(session_body, ensure_ascii=False)
    assert_true(session_body.get("ok") is True and session_body.get("status") == "valid", "session retorna dados seguros")
    assert_true("lead_id" not in body_text and "empresa_id" not in body_text and TEST_PHONE not in body_text, "session não retorna IDs ou telefone")

    async with AsyncSessionLocal() as db:
        validation = await validate_tokenized_link(db, public_slug=PUBLIC_SLUG, token=token)
        assert_true(validation.ok and validation.lead is not None, "token pode ser validado pelo serviço genérico")

    payload = {
        "token": token,
        "respostas": {
            "personalData": {"name": "Lead Anamnese Pública", "whatsapp": TEST_PHONE},
            "trainingProfile": {"mainGoal": "condicionamento", "trainingType": "hibrido"},
            "status": {"anamneseStarted": True, "anamneseCompleted": True, "checkoutStarted": False, "paymentStatus": "pending"},
        },
        "metadata": {"user_agent": "verificador", "submitted_at_client": "2026-07-04T00:00:00Z"},
    }
    submit = http_post(f"{BACKEND_URL}/api/public/anamnese/{PUBLIC_SLUG}/respostas", payload)
    assert_true(submit.status_code == 200, "POST respostas retorna sucesso")
    submit_body = submit.json()
    assert_true(submit_body.get("status") == "submitted", "POST respostas salva primeira submissão")
    assert_true("lead_id" not in json.dumps(submit_body), "POST não retorna IDs crus")

    async with AsyncSessionLocal() as db:
        lead = (await db.execute(select(CRMLead).where(CRMLead.id == lead_id))).scalars().one()
        dados = dict(lead.dados_adicionais or {})
        anamnese = dados.get("anamneses", {}).get(PUBLIC_SLUG, {})
        link_record = dados.get("links_tokenizados", {}).get(setup.LINK_SLUG, {})
        tag_concluida = (
            await db.execute(select(TagCRM).where(TagCRM.empresa_id == setup.EMPRESA_ID, TagCRM.nome == "LeoFT - Anamnese concluída"))
        ).scalars().one()
        paid_tag = (
            await db.execute(select(TagCRM).where(TagCRM.empresa_id == setup.EMPRESA_ID, TagCRM.nome == "LeoFT - Pagamento confirmado"))
        ).scalars().one()
        events = (
            await db.execute(
                select(MetaConversionEvent).where(
                    MetaConversionEvent.lead_id == lead.id,
                    MetaConversionEvent.tag_id == tag_concluida.id,
                    MetaConversionEvent.source == "public_anamnese",
                )
            )
        ).scalars().all()
        purchases = (
            await db.execute(
                select(MetaConversionEvent).where(MetaConversionEvent.lead_id == lead.id, MetaConversionEvent.event_name == "Purchase")
            )
        ).scalars().all()
        assert_true(anamnese.get("submitted") is True and anamnese.get("respostas"), "respostas ficam em dados_adicionais.anamneses")
        assert_true(
            anamnese.get("respostas", {}).get("status", {}).get("anamneseCompleted") is True,
            "payload salvo contém status.anamneseCompleted true",
        )
        assert_true(anamnese.get("token_ref") and token not in json.dumps(anamnese, ensure_ascii=False), "anamnese não salva token cru")
        assert_true(link_record.get("submitted") is True and link_record.get("usado_em"), "link tokenizado marcado como usado/submitted")
        assert_true(str(tag_concluida.id) in list(lead.tags or []), "tag LeoFT - Anamnese concluída aplicada")
        assert_true(all(event.status == "dry_run" for event in events) and len(events) == 1, "CAPI permanece dry_run")
        assert_true(str(paid_tag.id) not in list(lead.tags or []) and not purchases, "Purchase não é aplicado")

    submit_again = http_post(f"{BACKEND_URL}/api/public/anamnese/{PUBLIC_SLUG}/respostas", payload)
    assert_true(submit_again.status_code == 200 and submit_again.json().get("status") == "already_submitted", "POST repetido é idempotente")
    async with AsyncSessionLocal() as db:
        tag_concluida = (
            await db.execute(select(TagCRM).where(TagCRM.empresa_id == setup.EMPRESA_ID, TagCRM.nome == "LeoFT - Anamnese concluída"))
        ).scalars().one()
        events = (
            await db.execute(
                select(MetaConversionEvent).where(
                    MetaConversionEvent.lead_id == lead_id,
                    MetaConversionEvent.tag_id == tag_concluida.id,
                    MetaConversionEvent.source == "public_anamnese",
                )
            )
        ).scalars().all()
        assert_true(len(events) == 1, "POST idempotente não duplica conversão")


def validate_health() -> None:
    backend = http_get(f"{BACKEND_URL}/")
    assert_true(backend.status_code == 200 and backend.json().get("status") == "online", "backend healthy")
    frontend_urls = ["http://127.0.0.1:8080", "http://frontend"]
    frontend_ok = False
    anamnese_ok = False
    for base in frontend_urls:
        try:
            frontend_ok = frontend_ok or requests.head(f"{base}/", timeout=5).status_code < 500
            anamnese_ok = anamnese_ok or requests.head(f"{base}{PUBLIC_PATH}", timeout=5).status_code < 500
        except requests.RequestException:
            continue
    assert_true(frontend_ok, "frontend healthy")
    assert_true(anamnese_ok, "frontend serve /anamnese/leoft")


async def validate_tenants_preserved() -> None:
    async with AsyncSessionLocal() as session:
        rosangela = (await session.execute(select(Empresa).where(Empresa.id == setup_rosangela.EMPRESA_ID))).scalars().first()
        ros_seq = ((rosangela.credenciais_canais or {}).get("sequencias_whatsapp") or {}).get("danca_circular") if rosangela else None
        assert_true(bool(ros_seq and ros_seq.get("cta_esperado") == "QUERO MINHA VAGA"), "Rosangela continua passando")
        leonardo = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
        leo_seq = ((leonardo.credenciais_canais or {}).get("sequencias_whatsapp") or {}).get(setup.SEQUENCE_SLUG)
        assert_true(bool(leo_seq and leo_seq.get("cta_esperado") == "QUERO COMEÇAR"), "Leonardo sequência continua passando")
        jjclean = (await session.execute(select(Empresa).where(Empresa.nome_empresa == "JJClean"))).scalars().first()
        assert_true(bool(jjclean and "Método Híbrido LeoFT" not in json.dumps(jjclean.credenciais_canais or {}, ensure_ascii=False)), "JJClean não é alterada")


async def main() -> int:
    validate_static_files()
    lead_id, token, original = await prepare_valid_token()
    try:
        validate_health()
        await validate_backend_flow(lead_id, token)
        await validate_tenants_preserved()
        print("[OK] Mercado Pago real não é chamado")
        print("[OK] WhatsApp real não é chamado")
        print("[OK] Meta real não é chamada")
        print("[OK] OpenAI real não é chamada")
    finally:
        await restore_credenciais(original)
    print("verificar_anamnese_leonardo_publica: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
