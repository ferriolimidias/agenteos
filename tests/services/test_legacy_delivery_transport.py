"""Both legacy HTTP boundaries are exercised exclusively with mocked providers."""
import copy
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services import ads_integration_service as sender
from app.services.meta import legacy_delivery_evidence as evidence
from app.services.meta.conversion_credentials import ResolvedMetaCredential


class LegacyDeliveryTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant = uuid.uuid4()
        self.credential = ResolvedMetaCredential("tenant_conversion_token", "mock-token", str(self.tenant),
            "mock-waba", "mock-dataset", "v25.0")
        self.empresa = SimpleNamespace(id=self.tenant,meta_pixel_id="mock-dataset",meta_access_token="mock-token")
        self.payload = {"data":[{"event_name":"LeadSubmitted","event_id":"a"*64,
            "event_time":1791540000,"action_source":"business_messaging",
            "user_data":{"ctwa_clid":"mock-ctwa","whatsapp_business_account_id":"mock-waba"}}]}

    def mock_client(self, post):
        client = MagicMock()
        client.post = post
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)
        return manager

    async def invoke(self, path):
        if path == "enviar_payload_meta_capi":
            return await sender.enviar_payload_meta_capi(self.empresa,self.payload)
        return await sender.enviar_payload_meta_business_messaging(self.credential,self.payload)

    async def test_both_transports_persist_before_post_and_preserve_request(self):
        for path in ("enviar_payload_meta_business_messaging","enviar_payload_meta_capi"):
            for http_status, body in ((200,{"events_received":1}),(400,{"error":{"code":100}})):
                with self.subTest(path=path,http_status=http_status):
                    order = []
                    before = copy.deepcopy(self.payload)
                    attempt = uuid.uuid4()
                    async def prepare(**kwargs):
                        order.append("prepare")
                        self.assertEqual(kwargs["transport_path"],"ads_integration_service."+path)
                        self.assertEqual(str(kwargs["empresa_id"]),str(self.tenant))
                        self.assertEqual(kwargs["dataset_id"],"mock-dataset")
                        self.assertEqual(kwargs["external_event_id"],"a"*64)
                        self.assertEqual(kwargs["waba_id"],"mock-waba" if path.endswith("business_messaging") else None)
                        return attempt
                    async def mark(item):
                        order.append("attempted")
                        self.assertEqual(item,attempt)
                        return True
                    async def post(url,**kwargs):
                        order.append("post")
                        expected_version = "v25.0" if path.endswith("business_messaging") else sender.GRAPH_CAPI_VERSION
                        self.assertEqual(url,f"https://graph.facebook.com/{expected_version}/mock-dataset/events")
                        self.assertIs(kwargs["json"],self.payload)
                        self.assertEqual(kwargs["json"],before)
                        if path.endswith("business_messaging"):
                            self.assertEqual(kwargs["headers"],{"Authorization":"Bearer mock-token"})
                        else:
                            self.assertEqual(kwargs["params"],{"access_token":"mock-token"})
                        return SimpleNamespace(status_code=http_status,json=lambda:body)
                    async def finish(item,**kwargs):
                        order.append("finish")
                        self.assertEqual(item,attempt)
                        self.assertEqual(kwargs["http_status"],http_status)
                        self.assertEqual(kwargs["provider_response"],body)
                        self.assertIsNone(kwargs["error_phase"])
                        return True
                    with patch.object(evidence,"start_delivery_attempt",prepare),patch.object(evidence,"mark_delivery_attempted",mark), \
                         patch.object(evidence,"finish_delivery_attempt",finish),patch.object(sender.httpx,"AsyncClient",return_value=self.mock_client(post)):
                        result = await self.invoke(path)
                    self.assertEqual(order,["prepare","attempted","post","finish"])
                    self.assertEqual(result,(http_status,body))
                    self.assertEqual(self.payload,before)

    async def test_telemetry_failure_never_blocks_either_original_transport(self):
        for path in ("enviar_payload_meta_business_messaging","enviar_payload_meta_capi"):
            with self.subTest(path=path):
                post = AsyncMock(return_value=SimpleNamespace(status_code=200,json=lambda:{"events_received":1}))
                with patch.object(evidence,"start_delivery_attempt",AsyncMock(side_effect=RuntimeError("mock DB unavailable"))), \
                     patch.object(evidence,"finish_delivery_attempt",AsyncMock()) as finish, \
                     patch.object(sender.httpx,"AsyncClient",return_value=self.mock_client(post)):
                    result = await self.invoke(path)
                self.assertEqual(result,(200,{"events_received":1}))
                post.assert_awaited_once()
                finish.assert_not_awaited()

    async def test_marker_and_finish_failures_do_not_change_provider_response(self):
        for path in ("enviar_payload_meta_business_messaging","enviar_payload_meta_capi"):
            with self.subTest(path=path):
                attempt = uuid.uuid4()
                post = AsyncMock(return_value=SimpleNamespace(status_code=200,json=lambda:{"events_received":1}))
                with patch.object(evidence,"start_delivery_attempt",AsyncMock(return_value=attempt)), \
                     patch.object(evidence,"mark_delivery_attempted",AsyncMock(side_effect=RuntimeError("mock marker failure"))), \
                     patch.object(evidence,"finish_delivery_attempt",AsyncMock(side_effect=RuntimeError("mock finish failure"))), \
                     patch.object(sender.httpx,"AsyncClient",return_value=self.mock_client(post)):
                    result = await self.invoke(path)
                self.assertEqual(result,(200,{"events_received":1}))
                post.assert_awaited_once()

    async def test_timeout_after_request_records_unknown_phase_for_both_transports(self):
        for path in ("enviar_payload_meta_business_messaging","enviar_payload_meta_capi"):
            with self.subTest(path=path):
                attempt = uuid.uuid4()
                post = AsyncMock(side_effect=httpx.ReadTimeout("mock timeout"))
                with patch.object(evidence,"start_delivery_attempt",AsyncMock(return_value=attempt)), \
                     patch.object(evidence,"mark_delivery_attempted",AsyncMock(return_value=True)), \
                     patch.object(evidence,"finish_delivery_attempt",AsyncMock(return_value=True)) as finish, \
                     patch.object(sender.httpx,"AsyncClient",return_value=self.mock_client(post)):
                    with self.assertRaises(httpx.ReadTimeout):
                        await self.invoke(path)
                finish.assert_awaited_once_with(attempt,http_status=None,provider_response=None,error_phase="after_request")
                post.assert_awaited_once()

    async def test_client_open_failure_is_not_sent_for_both_transports(self):
        for path in ("enviar_payload_meta_business_messaging","enviar_payload_meta_capi"):
            with self.subTest(path=path):
                attempt = uuid.uuid4()
                post = AsyncMock()
                manager = self.mock_client(post)
                manager.__aenter__ = AsyncMock(side_effect=RuntimeError("mock before request"))
                with patch.object(evidence,"start_delivery_attempt",AsyncMock(return_value=attempt)), \
                     patch.object(evidence,"mark_delivery_attempted",AsyncMock()) as mark, \
                     patch.object(evidence,"finish_delivery_attempt",AsyncMock(return_value=True)) as finish, \
                     patch.object(sender.httpx,"AsyncClient",return_value=manager):
                    with self.assertRaises(RuntimeError):
                        await self.invoke(path)
                mark.assert_not_awaited()
                post.assert_not_awaited()
                finish.assert_awaited_once_with(attempt,http_status=None,provider_response=None,error_phase="before_request")

    async def test_actual_retry_wrapper_preserves_id_destination_payload_and_records_each_request(self):
        seen = []
        prepared = []
        async def prepare(**kwargs):
            prepared.append(kwargs)
            return uuid.uuid4()
        async def post(url,**kwargs):
            seen.append((url,copy.deepcopy(kwargs["json"])))
            if len(seen)==1:
                raise httpx.ReadTimeout("mock first request uncertain")
            return SimpleNamespace(status_code=200,json=lambda:{"events_received":1})
        with patch.object(evidence,"start_delivery_attempt",prepare), \
             patch.object(evidence,"mark_delivery_attempted",AsyncMock(return_value=True)), \
             patch.object(evidence,"finish_delivery_attempt",AsyncMock(return_value=True)) as finish, \
             patch.object(sender.httpx,"AsyncClient",return_value=self.mock_client(post)), \
             patch.object(sender.asyncio,"sleep",AsyncMock()):
            result = await sender.enviar_payload_meta_business_messaging_with_retry(self.credential,self.payload,
                event_id="a"*64,event_name="LeadSubmitted")
        self.assertEqual(result[0],200)
        self.assertEqual(result[2],2)
        self.assertEqual(seen[0],seen[1])
        self.assertEqual([item["external_event_id"] for item in prepared],["a"*64,"a"*64])
        self.assertNotEqual(prepared[0]["request_correlation_id"],prepared[1]["request_correlation_id"])
        self.assertEqual(finish.await_args_list[0].kwargs["error_phase"],"after_request")
        self.assertEqual(finish.await_args_list[1].kwargs["http_status"],200)

    async def test_wrapper_mock_receives_same_event_id_across_retry(self):
        seen = []
        async def transport(credential,payload):
            seen.append(payload["data"][0]["event_id"])
            return (500,{"error":{"is_transient":True}}) if len(seen)==1 else (200,{"events_received":1})
        with patch.object(sender,"enviar_payload_meta_business_messaging",transport),patch.object(sender.asyncio,"sleep",AsyncMock()):
            result = await sender.enviar_payload_meta_business_messaging_with_retry(self.credential,self.payload,
                event_id="a"*64,event_name="LeadSubmitted")
        self.assertEqual(result[2],2)
        self.assertEqual(seen,["a"*64,"a"*64])

    async def test_retained_website_lead_is_observed_without_canonical_eligibility(self):
        self.payload["data"][0]["event_name"] = "Lead"
        self.assertIn("Lead",evidence.TELEMETRY_EVENT_NAMES)
        self.assertNotIn("Lead",evidence.EVENT_NAMES)
        attempt = uuid.uuid4()
        post = AsyncMock(return_value=SimpleNamespace(status_code=200,json=lambda:{"events_received":1}))
        with patch.object(evidence,"start_delivery_attempt",AsyncMock(return_value=attempt)) as prepare, \
             patch.object(evidence,"mark_delivery_attempted",AsyncMock(return_value=True)), \
             patch.object(evidence,"finish_delivery_attempt",AsyncMock(return_value=True)), \
             patch.object(sender.httpx,"AsyncClient",return_value=self.mock_client(post)):
            result = await sender.enviar_payload_meta_capi(self.empresa,self.payload)
        self.assertEqual(result[0],200)
        self.assertEqual(prepare.await_args.kwargs["event_name"],"Lead")

    async def test_partial_batch_acceptance_does_not_fabricate_per_event_acceptance(self):
        for path in ("enviar_payload_meta_business_messaging","enviar_payload_meta_capi"):
            with self.subTest(path=path):
                self.payload["data"] = [dict(self.payload["data"][0],event_id="a"*64),dict(self.payload["data"][0],event_id="b"*64)]
                returned = {"events_received":1}
                post = AsyncMock(return_value=SimpleNamespace(status_code=200,json=lambda:returned))
                with patch.object(evidence,"start_delivery_attempt",AsyncMock(side_effect=[uuid.uuid4(),uuid.uuid4()])), \
                     patch.object(evidence,"mark_delivery_attempted",AsyncMock(return_value=True)), \
                     patch.object(evidence,"finish_delivery_attempt",AsyncMock(return_value=True)) as finish, \
                     patch.object(sender.httpx,"AsyncClient",return_value=self.mock_client(post)):
                    result = await self.invoke(path)
                self.assertEqual(result,(200,returned))
                self.assertEqual(finish.await_count,2)
                for call in finish.await_args_list:
                    self.assertNotIn("events_received",call.kwargs["provider_response"])
                    state, uncertain = evidence.delivery_outcome(attempted=True,http_status=200,
                        provider_response=call.kwargs["provider_response"])
                    self.assertEqual(state,"DELIVERY_UNKNOWN")
                    self.assertTrue(uncertain)

    async def test_malformed_data_does_not_create_retry_after_original_success(self):
        for path in ("enviar_payload_meta_business_messaging","enviar_payload_meta_capi"):
            with self.subTest(path=path):
                self.payload = {"data":None}
                returned = {"events_received":1}
                post = AsyncMock(return_value=SimpleNamespace(status_code=200,json=lambda:returned))
                with patch.object(evidence,"start_delivery_attempt",AsyncMock()) as prepare, \
                     patch.object(evidence,"mark_delivery_attempted",AsyncMock()) as mark, \
                     patch.object(evidence,"finish_delivery_attempt",AsyncMock()) as finish, \
                     patch.object(sender.httpx,"AsyncClient",return_value=self.mock_client(post)):
                    result = await self.invoke(path)
                self.assertEqual(result,(200,returned))
                post.assert_awaited_once()
                self.assertEqual(post.await_args.kwargs["json"],{"data":None})
                prepare.assert_not_awaited()
                mark.assert_not_awaited()
                finish.assert_not_awaited()
