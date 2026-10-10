import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.leoft_checkout_service import create_or_get_checkout


def test_checkout_reference_is_idempotent_without_second_provider_call():
    async def run():
        anamnese = {}
        response = SimpleNamespace(
            status_code=201,
            content=b'{"id":"pref-1","init_point":"https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=pref-1"}',
            json=lambda: {"id": "pref-1", "init_point": "https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=pref-1"},
        )
        client = AsyncMock()
        client.post.return_value = response
        context = patch("app.services.leoft_checkout_service.httpx.AsyncClient")
        with context as factory:
            factory.return_value.__aenter__.return_value = client
            with patch.dict("os.environ", {"MERCADO_PAGO_LEOFT_ACCESS_TOKEN": "test"}):
                first = await create_or_get_checkout(
                    anamnese=anamnese,
                    empresa_id="empresa",
                    lead_id="lead",
                    anamnese_id="anamnese",
                    config={"checkout_provider": "mercado_pago", "checkout_amount": "97"},
                )
                anamnese["checkout"] = {"reference": first.reference, "url": first.url, "provider": first.provider}
                second = await create_or_get_checkout(
                    anamnese=anamnese,
                    empresa_id="empresa",
                    lead_id="lead",
                    anamnese_id="anamnese",
                    config={"checkout_provider": "mercado_pago", "checkout_amount": "97"},
                )
        assert first.created is True
        assert second.reused is True
        assert first.reference == second.reference
        assert first.url == second.url
        assert client.post.await_count == 1
        sent_payload = client.post.await_args.kwargs["json"]
        assert "payer" not in sent_payload
        excluded_payment_types = [
            item["id"] for item in sent_payload["payment_methods"]["excluded_payment_types"]
        ]
        assert excluded_payment_types == ["ticket"]
        assert "credit_card" not in excluded_payment_types
        assert "debit_card" not in excluded_payment_types
        assert "prepaid_card" not in excluded_payment_types
        assert "bank_transfer" not in excluded_payment_types
        assert "digital_currency" not in excluded_payment_types

    asyncio.run(run())


def test_checkout_preserves_payment_method_properties_while_excluding_only_ticket():
    async def run():
        response = SimpleNamespace(
            status_code=201,
            content=b'{"id":"pref-1","init_point":"https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=pref-1"}',
            json=lambda: {"id": "pref-1", "init_point": "https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=pref-1"},
        )
        client = AsyncMock()
        client.post.return_value = response
        with patch("app.services.leoft_checkout_service.httpx.AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            with patch.dict("os.environ", {"MERCADO_PAGO_LEOFT_ACCESS_TOKEN": "test"}):
                await create_or_get_checkout(
                    anamnese={},
                    empresa_id="empresa",
                    lead_id="lead",
                    anamnese_id="anamnese",
                    config={
                        "checkout_provider": "mercado_pago",
                        "checkout_amount": "97",
                        "payment_methods": {"installments": 6},
                    },
                )
        sent_payment_methods = client.post.await_args.kwargs["json"]["payment_methods"]
        excluded_payment_types = [
            item["id"] for item in sent_payment_methods["excluded_payment_types"]
        ]
        assert sent_payment_methods["installments"] == 6
        assert excluded_payment_types == ["ticket"]

    asyncio.run(run())
