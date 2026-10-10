from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.leoft_generation.enums import GenerationJobStatus
from app.leoft_generation.errors import ResourceNotFoundError
from app.leoft_generation.schemas import E2EAllowlist, Environment, GenerationPolicy
from app.leoft_generation.services import LeoFTGenerationService
from app.services.leoft_generation_dispatch_client import (
    GenerationDispatchConfig,
    GenerationDispatchError,
    GenerationStartPayload,
    HermesGenerationDispatchClient,
)

CALLBACK_BASE_URL = "https://agenteos.example/api/internal/hermes/leoft"


class FakeSession:
    def __init__(self, version: SimpleNamespace | None) -> None:
        self.version = version

    async def scalar(self, _stmt):
        return self.version


class FakeRepository:
    def __init__(
        self,
        *,
        job: SimpleNamespace,
        snapshot: SimpleNamespace | None,
        version: SimpleNamespace | None,
    ) -> None:
        self.company_id = job.company_id
        self.job = job
        self.snapshot = snapshot
        self.session = FakeSession(version)
        self.tasks = []
        self.flush_count = 0

    async def get_job(self, job_id, *, lock: bool = False):
        assert lock is True
        return self.job if job_id == self.job.generation_job_id else None

    async def get_snapshot(self, snapshot_id):
        if self.snapshot is None:
            return None
        return self.snapshot if snapshot_id == self.snapshot.snapshot_id else None

    async def list_tasks(self, job_id, *, lock: bool = False):
        assert lock is True
        return self.tasks if job_id == self.job.generation_job_id else []

    async def flush(self):
        self.flush_count += 1


class FakeClient:
    def __init__(
        self, result: dict | None = None, error: Exception | None = None
    ) -> None:
        self.result = result or {"request_id": "hermes-request-1"}
        self.error = error
        self.payloads: list[GenerationStartPayload] = []

    async def start_job(self, payload: GenerationStartPayload) -> dict:
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return self.result


def _service(*, status: str = "pending", snapshot=True, version=True):
    company_id = uuid4()
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    snapshot_id = uuid4()
    job = SimpleNamespace(
        generation_job_id=uuid4(),
        company_id=company_id,
        student_id=uuid4(),
        anamnesis_snapshot_id=snapshot_id,
        plan_id=uuid4(),
        target_version_id=uuid4() if version else None,
        status=status,
        current_stage="created",
        correlation_id=uuid4(),
        idempotency_key="a" * 64,
        requested_duration_weeks=4,
        sessions_per_week=3,
        row_version=0,
        started_at=None,
        updated_at=now,
        created_at=now,
        hermes_request_id=None,
        dispatch_attempt_count=0,
        last_dispatch_attempt_at=None,
        last_redispatch_reason=None,
        last_error_code=None,
        last_error_summary=None,
    )
    repo = FakeRepository(
        job=job,
        snapshot=(
            SimpleNamespace(snapshot_id=snapshot_id, source_hash="b" * 64)
            if snapshot
            else None
        ),
        version=(
            SimpleNamespace(
                id=job.target_version_id,
                plan_id=job.plan_id,
                schema_version="leoft.training-plan.v2",
                status="building",
                blueprint={},
            )
            if version
            else None
        ),
    )
    policy = GenerationPolicy(
        environment=Environment.E2E,
        e2e_allowlist=E2EAllowlist(
            company_ids={company_id},
            student_ids={job.student_id},
            contact_ids={job.student_id},
            product_ids={"leoft_metodo_hibrido"},
        ),
    )
    return LeoFTGenerationService(repo, policy=policy, clock=lambda: now), repo, job


class LeoFTGenerationDispatchActivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_job_dispatches_once_and_persists_acceptance(self):
        service, repo, job = _service()
        client = FakeClient(result={"generation_job_id": str(job.generation_job_id)})

        result = await service.dispatch_generation_job_to_hermes(
            job_id=job.generation_job_id,
            callback_base_url=CALLBACK_BASE_URL,
            client=client,
        )

        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.response_status, 202)
        self.assertEqual(job.status, GenerationJobStatus.DISPATCHING.value)
        self.assertEqual(job.current_stage, "dispatch_accepted")
        self.assertIsNotNone(job.started_at)
        self.assertEqual(job.dispatch_attempt_count, 1)
        self.assertEqual(job.last_dispatch_attempt_at, job.started_at)
        self.assertEqual(job.row_version, 1)
        self.assertEqual(repo.flush_count, 1)
        self.assertEqual(len(client.payloads), 1)
        payload = client.payloads[0]
        self.assertEqual(payload.generation_job_id, job.generation_job_id)
        self.assertEqual(payload.company_id, job.company_id)
        self.assertEqual(payload.target_version_id, job.target_version_id)
        self.assertEqual(payload.correlation_id, job.correlation_id)
        self.assertEqual(payload.idempotency_key, job.idempotency_key)
        self.assertEqual(payload.snapshot_hash, "b" * 64)

        repeated = await service.dispatch_generation_job_to_hermes(
            job_id=job.generation_job_id,
            callback_base_url=CALLBACK_BASE_URL,
            client=client,
        )
        self.assertEqual(repeated.code, "generation_dispatch_already_accepted")
        self.assertEqual(len(client.payloads), 1)

    async def test_non_pending_job_does_not_dispatch(self):
        for status in ["planning", "generating", "cancelled"]:
            service, _repo, job = _service(status=status)
            client = FakeClient()

            result = await service.dispatch_generation_job_to_hermes(
                job_id=job.generation_job_id,
                callback_base_url=CALLBACK_BASE_URL,
                client=client,
            )

            self.assertEqual(result.status, "skipped")
            self.assertEqual(result.code, "generation_dispatch_job_not_pending")
            self.assertEqual(client.payloads, [])

    async def test_missing_snapshot_or_target_version_does_not_dispatch(self):
        service, _repo, job = _service(snapshot=False)
        client = FakeClient()
        with self.assertRaises(ResourceNotFoundError):
            await service.dispatch_generation_job_to_hermes(
                job_id=job.generation_job_id,
                callback_base_url=CALLBACK_BASE_URL,
                client=client,
            )
        self.assertEqual(client.payloads, [])

        service, _repo, job = _service(version=False)
        with self.assertRaises(ResourceNotFoundError):
            await service.dispatch_generation_job_to_hermes(
                job_id=job.generation_job_id,
                callback_base_url=CALLBACK_BASE_URL,
                client=client,
            )

    async def test_dispatch_error_classes_are_persisted_without_duplication(self):
        cases = [
            (401, "generation_dispatch_unauthorized", False, "blocked"),
            (403, "generation_dispatch_forbidden", False, "blocked"),
            (409, "generation_dispatch_idempotency_conflict", False, "blocked"),
            (422, "generation_dispatch_contract_error", False, "blocked"),
            (429, "generation_dispatch_rate_limited", True, "retryable_error"),
            (500, "generation_dispatch_upstream_error", True, "retryable_error"),
            (None, "generation_dispatch_network_error", True, "retryable_error"),
            (None, "generation_dispatch_invalid_response", False, "blocked"),
        ]
        for status_code, code, retryable, result_status in cases:
            service, repo, job = _service()
            client = FakeClient(
                error=GenerationDispatchError(
                    code,
                    retryable=retryable,
                    status_code=status_code,
                )
            )

            result = await service.dispatch_generation_job_to_hermes(
                job_id=job.generation_job_id,
                callback_base_url=CALLBACK_BASE_URL,
                client=client,
            )

            self.assertEqual(result.status, result_status)
            self.assertIs(result.retryable, retryable)
            self.assertEqual(result.response_status, status_code)
            self.assertEqual(job.status, GenerationJobStatus.PENDING.value)
            self.assertEqual(job.last_error_code, code)
            self.assertEqual(job.row_version, 1)
            self.assertEqual(repo.flush_count, 1)
            self.assertEqual(len(client.payloads), 1)

    async def test_blocking_error_prevents_automatic_retry_until_operator_action(self):
        for code in (
            "generation_dispatch_unauthorized",
            "generation_dispatch_invalid_response",
            "generation_dispatch_http_error",
        ):
            service, _repo, job = _service()
            job.last_error_code = code
            client = FakeClient()

            result = await service.dispatch_generation_job_to_hermes(
                job_id=job.generation_job_id,
                callback_base_url=CALLBACK_BASE_URL,
                client=client,
            )

            self.assertEqual(result.status, "blocked")
            self.assertEqual(result.code, code)
            self.assertEqual(client.payloads, [])

    async def test_client_maps_http_responses_without_reserializing_payload(self):
        payload = GenerationStartPayload(
            generation_job_id=uuid4(),
            company_id=uuid4(),
            target_version_id=uuid4(),
            schema_version="leoft.training-plan.v2",
            correlation_id=uuid4(),
            callback_base_url=CALLBACK_BASE_URL,
            requested_duration_weeks=4,
            sessions_per_week=3,
            snapshot_hash="c" * 64,
            idempotency_key="d" * 64,
        )
        config = GenerationDispatchConfig(
            base_url="https://app.leoft.com.br",
            secret="synthetic",
            key_id="synthetic-key",
            maximum_attempts=1,
        )

        captured_body = None

        def accepted_transport(request, _timeout):
            nonlocal captured_body
            captured_body = request.data
            return 202, b'{"request_id":"accepted"}'

        response = await HermesGenerationDispatchClient(
            config=config,
            transport=accepted_transport,
        ).start_job(payload)
        self.assertEqual(response["request_id"], "accepted")
        self.assertIsInstance(captured_body, bytes)

        cases = [
            (401, "generation_dispatch_unauthorized", False),
            (403, "generation_dispatch_forbidden", False),
            (409, "generation_dispatch_idempotency_conflict", False),
            (422, "generation_dispatch_contract_error", False),
            (429, "generation_dispatch_rate_limited", True),
            (500, "generation_dispatch_upstream_error", True),
        ]
        for status_code, code, retryable in cases:
            def transport(_request, _timeout, status_code=status_code):
                return status_code, b'{"ok":false}'

            with self.assertRaises(GenerationDispatchError) as raised:
                await HermesGenerationDispatchClient(
                    config=config,
                    transport=transport,
                ).start_job(payload)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(raised.exception.status_code, status_code)
            self.assertIs(raised.exception.retryable, retryable)

    async def test_recent_dispatching_job_without_blueprint_does_not_redispatch(self):
        service, _repo, job = _service(status=GenerationJobStatus.DISPATCHING.value)
        job.current_stage = "dispatch_accepted"
        job.started_at = service.clock() - timedelta(minutes=2)
        client = FakeClient()

        result = await service.redispatch_generation_job_to_hermes(
            job_id=job.generation_job_id,
            reason="operator resume after stale dispatch",
            callback_base_url=CALLBACK_BASE_URL,
            client=client,
            stale_after=timedelta(minutes=10),
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.code, "generation_redispatch_not_stale")
        self.assertEqual(client.payloads, [])

    async def test_stale_dispatching_job_without_blueprint_redispatches_same_job(self):
        service, repo, job = _service(status=GenerationJobStatus.DISPATCHING.value)
        job.current_stage = "dispatch_accepted"
        job.started_at = service.clock() - timedelta(minutes=20)
        client = FakeClient(result={"request_id": "redispatch-1"})

        result = await service.redispatch_generation_job_to_hermes(
            job_id=job.generation_job_id,
            reason="operator resume after stale dispatch",
            callback_base_url=CALLBACK_BASE_URL,
            client=client,
            stale_after=timedelta(minutes=10),
        )

        self.assertEqual(result.status, "accepted")
        self.assertTrue(result.redispatched)
        self.assertEqual(result.response_status, 202)
        self.assertEqual(job.status, GenerationJobStatus.DISPATCHING.value)
        self.assertEqual(job.current_stage, "dispatch_accepted")
        self.assertEqual(job.hermes_request_id, "redispatch-1")
        self.assertEqual(job.dispatch_attempt_count, 1)
        self.assertEqual(job.row_version, 2)
        self.assertEqual(repo.flush_count, 2)
        self.assertEqual(len(client.payloads), 1)
        payload = client.payloads[0]
        self.assertEqual(payload.generation_job_id, job.generation_job_id)
        self.assertEqual(payload.correlation_id, job.correlation_id)
        self.assertEqual(payload.idempotency_key, job.idempotency_key)
        self.assertEqual(payload.snapshot_hash, "b" * 64)
        self.assertEqual(payload.target_version_id, job.target_version_id)

    async def test_redispatch_skips_non_eligible_states_and_existing_blueprint(self):
        for status in (
            GenerationJobStatus.PLANNING.value,
            GenerationJobStatus.GENERATING.value,
            GenerationJobStatus.AWAITING_REVIEW.value,
            GenerationJobStatus.COMPLETED.value
            if hasattr(GenerationJobStatus, "COMPLETED")
            else GenerationJobStatus.PUBLISHED.value,
            GenerationJobStatus.FAILED.value,
        ):
            service, _repo, job = _service(status=status)
            job.current_stage = status
            client = FakeClient()
            result = await service.redispatch_generation_job_to_hermes(
                job_id=job.generation_job_id,
                reason="operator resume after stale dispatch",
                callback_base_url=CALLBACK_BASE_URL,
                client=client,
            )
            self.assertEqual(result.status, "skipped")
            self.assertEqual(client.payloads, [])

        service, _repo, job = _service(status=GenerationJobStatus.DISPATCHING.value)
        job.current_stage = "dispatch_accepted"
        job.started_at = service.clock() - timedelta(minutes=20)
        service.repository.session.version.blueprint = {"sessions": []}
        client = FakeClient()
        result = await service.redispatch_generation_job_to_hermes(
            job_id=job.generation_job_id,
            reason="operator resume after stale dispatch",
            callback_base_url=CALLBACK_BASE_URL,
            client=client,
            stale_after=timedelta(minutes=10),
        )
        self.assertEqual(result.code, "generation_redispatch_blueprint_present")
        self.assertEqual(client.payloads, [])

    async def test_redispatch_error_semantics_match_dispatch_policy(self):
        cases = [
            (401, "generation_dispatch_unauthorized", False, "blocked"),
            (403, "generation_dispatch_forbidden", False, "blocked"),
            (409, "generation_dispatch_idempotency_conflict", False, "blocked"),
            (422, "generation_dispatch_contract_error", False, "blocked"),
            (429, "generation_dispatch_rate_limited", True, "retryable_error"),
            (500, "generation_dispatch_upstream_error", True, "retryable_error"),
            (None, "generation_dispatch_network_error", True, "retryable_error"),
        ]
        for status_code, code, retryable, result_status in cases:
            service, _repo, job = _service(status=GenerationJobStatus.DISPATCHING.value)
            job.current_stage = "dispatch_accepted"
            job.started_at = service.clock() - timedelta(minutes=20)
            client = FakeClient(
                error=GenerationDispatchError(
                    code,
                    retryable=retryable,
                    status_code=status_code,
                )
            )

            result = await service.redispatch_generation_job_to_hermes(
                job_id=job.generation_job_id,
                reason="operator resume after stale dispatch",
                callback_base_url=CALLBACK_BASE_URL,
                client=client,
                stale_after=timedelta(minutes=10),
            )

            self.assertEqual(result.status, result_status)
            self.assertIs(result.retryable, retryable)
            self.assertEqual(result.code, code)
            self.assertEqual(result.response_status, status_code)
            self.assertEqual(job.status, GenerationJobStatus.DISPATCHING.value)
            self.assertEqual(job.current_stage, "dispatch_accepted")
            self.assertEqual(job.dispatch_attempt_count, 1)
            self.assertEqual(job.last_error_code, code)

    async def test_redispatch_cooldown_uses_persisted_attempt_timestamp(self):
        service, _repo, job = _service(status=GenerationJobStatus.DISPATCHING.value)
        job.current_stage = "dispatch_accepted"
        job.started_at = service.clock() - timedelta(minutes=30)
        job.last_dispatch_attempt_at = service.clock() - timedelta(minutes=2)
        client = FakeClient()

        result = await service.redispatch_generation_job_to_hermes(
            job_id=job.generation_job_id,
            reason="operator resume after stale dispatch",
            callback_base_url=CALLBACK_BASE_URL,
            client=client,
            stale_after=timedelta(minutes=10),
            cooldown=timedelta(minutes=5),
        )

        self.assertEqual(result.code, "generation_redispatch_not_stale")
        self.assertEqual(client.payloads, [])
