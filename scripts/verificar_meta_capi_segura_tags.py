from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api.routers.empresas import (
    MetaCAPITestRequest,
    _serializar_empresa_segura,
    atualizar_empresa,
    teste_meta_capi,
)
from app.schemas import EmpresaUpdate
from app.services import ads_integration_service as capi
from db.database import AsyncSessionLocal, engine
from db.manual_migrations import ensure_meta_capi_tables
from db.models import CRMLead, Empresa, MetaConversionEvent, TagCRM, TagMetaConversionConfig


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def record(results: list[Check], name: str, ok: bool, detail: str = "") -> None:
    results.append(Check(name=name, ok=ok, detail=detail))
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {name}{': ' + detail if detail else ''}")


def contains_raw_secret(obj: Any, secret: str) -> bool:
    return secret in json.dumps(obj, ensure_ascii=False, default=str)


async def main() -> int:
    await ensure_meta_capi_tables(engine)
    results: list[Check] = []
    raw_token = "EAAT" + "OKEN_SUPER_SECRETO_123456789"
    chamadas_http: list[dict[str, Any]] = []

    async def fake_send(empresa: Empresa, payload: dict[str, Any]) -> tuple[int, Any]:
        chamadas_http.append({"empresa_id": str(empresa.id), "payload": payload})
        await asyncio.sleep(0.1)
        return 200, {"events_received": 1, "fbtrace_id": "MOCK_TRACE"}

    original_send = capi.enviar_payload_meta_capi
    capi.enviar_payload_meta_capi = fake_send

    empresa_a_id = uuid.uuid4()
    empresa_b_id = uuid.uuid4()
    try:
        async with AsyncSessionLocal() as session:
            empresa_a = Empresa(
                id=empresa_a_id,
                nome_empresa="Teste CAPI Seguro A",
                meta_capi_ativo=True,
                meta_pixel_id="111111111",
                meta_access_token=raw_token,
            )
            empresa_b = Empresa(
                id=empresa_b_id,
                nome_empresa="Teste CAPI Seguro B",
                meta_capi_ativo=True,
                meta_pixel_id="222222222",
                meta_access_token="TOKEN_EMPRESA_B",
            )
            session.add_all([empresa_a, empresa_b])
            await session.flush()

            lead_a = CRMLead(
                empresa_id=empresa_a.id,
                nome_contato="Lead CAPI A",
                telefone_contato="+55 (11) 99999-0000",
                dados_adicionais={
                    "email": "lead@example.com",
                    "fbp": "fb.1.123.abc",
                    "client_ip_address": "203.0.113.10",
                    "client_user_agent": "Mozilla Test",
                    "event_source_url": "https://example.com/origem",
                    "utm_campaign": "campanha_teste",
                },
                valor_conversao=123.45,
            )
            lead_b = CRMLead(
                empresa_id=empresa_b.id,
                nome_contato="Lead CAPI B",
                telefone_contato="+55 (21) 98888-0000",
            )
            session.add_all([lead_a, lead_b])
            await session.flush()

            tag_sem_config = TagCRM(
                empresa_id=empresa_a.id,
                nome="Conversao sem config",
                disparar_conversao_ads=True,
            )
            tag_dry = TagCRM(
                empresa_id=empresa_a.id,
                nome="Contato terapia",
                disparar_conversao_ads=True,
            )
            tag_test = TagCRM(
                empresa_id=empresa_a.id,
                nome="Lead teste evento",
                disparar_conversao_ads=True,
            )
            tag_live = TagCRM(
                empresa_id=empresa_a.id,
                nome="Lead live",
                disparar_conversao_ads=True,
            )
            tag_live_concorrente = TagCRM(
                empresa_id=empresa_a.id,
                nome="Lead live concorrente",
                disparar_conversao_ads=True,
            )
            tag_b = TagCRM(
                empresa_id=empresa_b.id,
                nome="Lead empresa B",
                disparar_conversao_ads=True,
            )
            session.add_all([tag_sem_config, tag_dry, tag_test, tag_live, tag_live_concorrente, tag_b])
            await session.flush()

            cfg_dry = TagMetaConversionConfig(
                empresa_id=empresa_a.id,
                tag_id=tag_dry.id,
                ativo=True,
                event_name="Contact",
                send_mode="dry_run",
                currency="BRL",
            )
            cfg_test = TagMetaConversionConfig(
                empresa_id=empresa_a.id,
                tag_id=tag_test.id,
                ativo=True,
                event_name="Lead",
                send_mode="test_event",
                test_event_code="TEST123",
            )
            cfg_live = TagMetaConversionConfig(
                empresa_id=empresa_a.id,
                tag_id=tag_live.id,
                ativo=True,
                event_name="Lead",
                send_mode="live",
            )
            cfg_live_concorrente = TagMetaConversionConfig(
                empresa_id=empresa_a.id,
                tag_id=tag_live_concorrente.id,
                ativo=True,
                event_name="Lead",
                send_mode="live",
            )
            cfg_b = TagMetaConversionConfig(
                empresa_id=empresa_b.id,
                tag_id=tag_b.id,
                ativo=True,
                event_name="Lead",
                send_mode="dry_run",
            )
            session.add_all([cfg_dry, cfg_test, cfg_live, cfg_live_concorrente, cfg_b])
            await session.commit()

        async with AsyncSessionLocal() as session:
            empresa_a = (await session.execute(select(Empresa).where(Empresa.id == empresa_a_id))).scalars().one()
            serialized = _serializar_empresa_segura(empresa_a)
            record(results, "token seguro sem valor cru", not contains_raw_secret(serialized, raw_token), json.dumps(serialized, default=str))
            record(
                results,
                "token retorna flags e mascara",
                serialized.get("meta_access_token_configurado") is True
                and serialized.get("meta_access_token_mascarado")
                and "meta_access_token" not in serialized,
            )

            await atualizar_empresa(
                str(empresa_a.id),
                EmpresaUpdate(meta_access_token=""),
                session,
                None,
            )
            await session.refresh(empresa_a)
            record(results, "update token vazio preserva", empresa_a.meta_access_token == raw_token)

            await atualizar_empresa(
                str(empresa_a.id),
                EmpresaUpdate(meta_access_token="NOVO_TOKEN_SEGURO"),
                session,
                None,
            )
            await session.refresh(empresa_a)
            record(results, "update novo token substitui", empresa_a.meta_access_token == "NOVO_TOKEN_SEGURO")

            await atualizar_empresa(
                str(empresa_a.id),
                EmpresaUpdate(remover_meta_access_token=True),
                session,
                None,
            )
            await session.refresh(empresa_a)
            record(results, "remover token exige flag explicita", empresa_a.meta_access_token is None)
            empresa_a.meta_access_token = raw_token
            await session.commit()

        async with AsyncSessionLocal() as session:
            empresa_a = (await session.execute(select(Empresa).where(Empresa.id == empresa_a_id))).scalars().one()
            lead_a = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_a_id))).scalars().first()
            tag_dry = (await session.execute(select(TagCRM).where(TagCRM.empresa_id == empresa_a_id, TagCRM.nome == "Contato terapia"))).scalars().one()
            cfg_dry = (await session.execute(select(TagMetaConversionConfig).where(TagMetaConversionConfig.tag_id == tag_dry.id))).scalars().one()
            payload = await capi.montar_payload_meta_capi(
                lead_a,
                tag_dry,
                empresa_a,
                cfg_dry,
                event_name="Contact",
                event_id=capi.gerar_event_id_estavel(empresa_a.id, lead_a.id, tag_dry.id, "Contact"),
            )
            preview = capi.payload_preview_seguro(payload)
            raw_phone_digits = "5511999990000"
            record(results, "dry-run monta payload sem HTTP", len(chamadas_http) == 0 and payload["data"][0]["event_name"] == "Contact")
            record(results, "preview sem token/telefone cru", not contains_raw_secret(preview, raw_token) and not contains_raw_secret(preview, raw_phone_digits))

            route_resp = await teste_meta_capi(str(empresa_a.id), MetaCAPITestRequest(telefone="+5511999990000"), session, None)
            record(results, "rota teste dry-run por padrao", route_resp.get("status") == "dry_run" and len(chamadas_http) == 0)
            try:
                await teste_meta_capi(str(empresa_a.id), MetaCAPITestRequest(confirmar_envio_real=True), session, None)
                real_sem_codigo_bloqueado = False
            except HTTPException as exc:
                real_sem_codigo_bloqueado = exc.status_code == 400
            record(results, "envio real exige confirmacao e test_event_code", real_sem_codigo_bloqueado)

        async with AsyncSessionLocal() as session:
            empresa_a = (await session.execute(select(Empresa).where(Empresa.id == empresa_a_id))).scalars().one()
            lead_a = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_a_id))).scalars().first()
            tag_sem_config = (await session.execute(select(TagCRM).where(TagCRM.nome == "Conversao sem config"))).scalars().one()
            res_sem = await capi.processar_meta_capi_por_tag(session, lead_a, tag_sem_config, empresa_a, source="agent_tool")
            await session.commit()
            record(results, "tag sem config explicita nao envia real", res_sem.get("status") == "skipped" and len(chamadas_http) == 0)
            skipped_sem_config = (
                await session.execute(
                    select(MetaConversionEvent).where(
                        MetaConversionEvent.empresa_id == empresa_a_id,
                        MetaConversionEvent.tag_id == tag_sem_config.id,
                        MetaConversionEvent.status == "skipped",
                    )
                )
            ).scalars().first()
            record(
                results,
                "skipped_nao_reserva_live_key",
                skipped_sem_config is not None and getattr(skipped_sem_config, "reservation_key", None) is None,
            )
            cfg_sem_live = TagMetaConversionConfig(
                empresa_id=empresa_a.id,
                tag_id=tag_sem_config.id,
                ativo=True,
                event_name="Lead",
                send_mode="live",
            )
            session.add(cfg_sem_live)
            await session.commit()
            chamadas_http.clear()
            res_sem_live = await capi.processar_meta_capi_por_tag(session, lead_a, tag_sem_config, empresa_a, source="agent_tool", send_real=True)
            await session.commit()
            record(results, "skipped_nao_bloqueia_live", res_sem_live.get("status") == "sent" and len(chamadas_http) == 1)
            chamadas_http.clear()

            tag_dry = (await session.execute(select(TagCRM).where(TagCRM.nome == "Contato terapia"))).scalars().one()
            res_dry = await capi.processar_meta_capi_por_tag(session, lead_a, tag_dry, empresa_a, source="tag")
            await session.commit()
            record(results, "tag com config dry-run cria evento dry_run", res_dry.get("status") == "dry_run" and len(chamadas_http) == 0)
            res_dry_repetido = await capi.processar_meta_capi_por_tag(session, lead_a, tag_dry, empresa_a, source="tag")
            await session.commit()
            dry_events = (
                await session.execute(
                    select(MetaConversionEvent).where(
                        MetaConversionEvent.empresa_id == empresa_a_id,
                        MetaConversionEvent.tag_id == tag_dry.id,
                        MetaConversionEvent.status == "dry_run",
                    )
                )
            ).scalars().all()
            stable_dry_key = capi.gerar_event_id_estavel(empresa_a.id, lead_a.id, tag_dry.id, "Contact")
            record(
                results,
                "dry_run_repetido_nao_quebra",
                res_dry_repetido.get("status") == "dry_run"
                and len(dry_events) >= 2
                and all(getattr(event, "reservation_key", None) is None for event in dry_events)
                and len(chamadas_http) == 0,
            )
            record(
                results,
                "dry_run_nao_reserva_live_key",
                all(not str(getattr(event, "event_id", "")).startswith(stable_dry_key) for event in dry_events)
                and all(getattr(event, "reservation_key", None) is None for event in dry_events),
            )
            cfg_dry = (await session.execute(select(TagMetaConversionConfig).where(TagMetaConversionConfig.tag_id == tag_dry.id))).scalars().one()
            cfg_dry.send_mode = "live"
            await session.commit()
            chamadas_http.clear()
            res_dry_live = await capi.processar_meta_capi_por_tag(session, lead_a, tag_dry, empresa_a, source="tag", send_real=True)
            await session.commit()
            dry_live_event = (
                await session.execute(select(MetaConversionEvent).where(MetaConversionEvent.reservation_key == stable_dry_key))
            ).scalars().first()
            record(
                results,
                "dry_run_nao_bloqueia_live",
                res_dry_live.get("status") == "sent"
                and len(chamadas_http) == 1
                and dry_live_event is not None
                and dry_live_event.event_id == stable_dry_key,
            )
            chamadas_http.clear()

            tag_test = (await session.execute(select(TagCRM).where(TagCRM.nome == "Lead teste evento"))).scalars().one()
            res_test = await capi.processar_meta_capi_por_tag(session, lead_a, tag_test, empresa_a, source="tag")
            await session.commit()
            record(results, "tag test_event monta sem Meta real", res_test.get("status") == "dry_run" and len(chamadas_http) == 0)
            test_events = (
                await session.execute(
                    select(MetaConversionEvent).where(
                        MetaConversionEvent.empresa_id == empresa_a_id,
                        MetaConversionEvent.tag_id == tag_test.id,
                        MetaConversionEvent.status == "dry_run",
                    )
                )
            ).scalars().all()
            stable_test_key = capi.gerar_event_id_estavel(empresa_a.id, lead_a.id, tag_test.id, "Lead")
            cfg_test = (await session.execute(select(TagMetaConversionConfig).where(TagMetaConversionConfig.tag_id == tag_test.id))).scalars().one()
            cfg_test.send_mode = "live"
            await session.commit()
            chamadas_http.clear()
            res_test_live = await capi.processar_meta_capi_por_tag(session, lead_a, tag_test, empresa_a, source="tag", send_real=True)
            await session.commit()
            record(
                results,
                "test_event_nao_bloqueia_live",
                test_events
                and all(getattr(event, "reservation_key", None) is None for event in test_events)
                and res_test_live.get("status") == "sent"
                and len(chamadas_http) == 1,
            )
            audit_test_events = (
                await session.execute(
                    select(MetaConversionEvent).where(
                        MetaConversionEvent.tag_id == tag_test.id,
                        MetaConversionEvent.reservation_key.is_(None),
                    )
                )
            ).scalars().all()
            record(
                results,
                "audit_event_id_nao_bloqueia_live",
                any(str(event.event_id).startswith(f"dry_run:{stable_test_key}:") for event in audit_test_events),
            )
            chamadas_http.clear()

            tag_live = (await session.execute(select(TagCRM).where(TagCRM.nome == "Lead live"))).scalars().one()
            stable_id = capi.gerar_event_id_estavel(empresa_a.id, lead_a.id, tag_live.id, "Lead")
            lead_a_id = lead_a.id
            tag_live_id = tag_live.id
            await capi.registrar_evento_meta_capi(
                session,
                empresa=empresa_a,
                lead=lead_a,
                tag=tag_live,
                config=(await session.execute(select(TagMetaConversionConfig).where(TagMetaConversionConfig.tag_id == tag_live.id))).scalars().one(),
                event_name="Lead",
                event_id=stable_id,
                reservation_key=stable_id,
                status="sent",
                payload={"data": []},
                source="tag",
                attempts=1,
            )
            await session.commit()
            res_live_dup = await capi.processar_meta_capi_por_tag(session, lead_a, tag_live, empresa_a, source="tag", send_real=True)
            await session.commit()
            record(results, "idempotencia evita segundo sent", res_live_dup.get("reason") == "duplicate_sent" and len(chamadas_http) == 0)
            record(results, "event_id estavel", stable_id == capi.gerar_event_id_estavel(empresa_a_id, lead_a_id, tag_live_id, "Lead"))

            user_data = payload["data"][0]["user_data"]
            record(results, "payload telefone/email hasheados", bool(user_data.get("ph")) and bool(user_data.get("em")))
            record(results, "payload sem PII crua", not contains_raw_secret(payload, "+55") and not contains_raw_secret(payload, "lead@example.com"))
            record(results, "payload event_id/action_source", payload["data"][0].get("event_id") and payload["data"][0].get("action_source") == "chat")

        async with AsyncSessionLocal() as session:
            lead_a = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_a_id))).scalars().first()
            tag_live_concorrente = (
                await session.execute(select(TagCRM).where(TagCRM.nome == "Lead live concorrente"))
            ).scalars().one()
            stable_concorrente_id = capi.gerar_event_id_estavel(empresa_a_id, lead_a.id, tag_live_concorrente.id, "Lead")

        chamadas_http.clear()

        async def executar_live_concorrente() -> dict[str, Any]:
            async with AsyncSessionLocal() as session:
                empresa = (await session.execute(select(Empresa).where(Empresa.id == empresa_a_id))).scalars().one()
                lead = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_a_id))).scalars().first()
                tag = (
                    await session.execute(select(TagCRM).where(TagCRM.nome == "Lead live concorrente"))
                ).scalars().one()
                return await capi.processar_meta_capi_por_tag(session, lead, tag, empresa, source="tag", send_real=True)

        respostas_concorrentes = await asyncio.gather(executar_live_concorrente(), executar_live_concorrente())
        async with AsyncSessionLocal() as session:
            eventos_concorrentes = (
                await session.execute(select(MetaConversionEvent).where(MetaConversionEvent.reservation_key == stable_concorrente_id))
            ).scalars().all()
            evento_concorrente = eventos_concorrentes[0] if eventos_concorrentes else None
            preview_concorrente = getattr(evento_concorrente, "payload_preview", {}) if evento_concorrente else {}
            reasons = {res.get("reason") for res in respostas_concorrentes if res.get("status") == "skipped"}
            statuses = {res.get("status") for res in respostas_concorrentes}
            record(
                results,
                "live_concorrente_nao_duplica_http",
                len(chamadas_http) == 1
                and len(eventos_concorrentes) == 1
                and getattr(evento_concorrente, "status", None) == "sent"
                and getattr(evento_concorrente, "event_id", None) == stable_concorrente_id
                and "skipped" in statuses
                and bool(reasons & {"pending_exists", "duplicate_sent", "duplicate_event_reserved"})
                and not contains_raw_secret(preview_concorrente, raw_token),
                f"http={len(chamadas_http)} eventos={len(eventos_concorrentes)} respostas={respostas_concorrentes}",
            )
            record(
                results,
                "event_id_live_estavel",
                len(chamadas_http) == 1 and chamadas_http[0]["payload"]["data"][0].get("event_id") == stable_concorrente_id,
            )
            chamadas_http.clear()

        async with AsyncSessionLocal() as session:
            empresa_b = (await session.execute(select(Empresa).where(Empresa.id == empresa_b_id))).scalars().one()
            lead_b = (await session.execute(select(CRMLead).where(CRMLead.empresa_id == empresa_b_id))).scalars().one()
            tag_b = (await session.execute(select(TagCRM).where(TagCRM.empresa_id == empresa_b_id))).scalars().first()
            res_b = await capi.processar_meta_capi_por_tag(session, lead_b, tag_b, empresa_b, source="tag")
            await session.commit()
            evento_b = (
                await session.execute(
                    select(MetaConversionEvent).where(
                        MetaConversionEvent.empresa_id == empresa_b_id,
                        MetaConversionEvent.tag_id == tag_b.id,
                    )
                )
            ).scalars().first()
            record(results, "multi-tenant usa empresa correta", res_b.get("status") == "dry_run" and evento_b and evento_b.empresa_id == empresa_b_id)

        record(results, "agente aplica tag sem chamada Meta direta", len(chamadas_http) == 0, "tools apenas agendam backend CAPI")

    finally:
        capi.enviar_payload_meta_capi = original_send
        async with AsyncSessionLocal() as session:
            await session.execute(delete(Empresa).where(Empresa.id.in_([empresa_a_id, empresa_b_id])))
            await session.commit()

    failed = [item for item in results if not item.ok]
    print(f"\nResumo: {len(results) - len(failed)}/{len(results)} checks OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
