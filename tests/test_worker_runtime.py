from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

from app.core.startup_database_mode import StartupDatabaseMode
from app.services.worker_runtime import WorkerRuntimeRegistry, check_worker_database_readiness, env_flag


async def _wait_for_state(registry, worker_name, predicate, timeout=1):
    async def wait_until_true():
        while not predicate(registry.local_snapshot(worker_name)):
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_true(), timeout=timeout)


class WorkerRuntimeRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_start_event_uses_uvicorn_operational_log_channel(self):
        registry = WorkerRuntimeRegistry(instance_id="log-instance")

        async def readiness():
            return None

        async def run_cycle():
            return {"processed": 0}

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            task = registry.start_worker(
                name="followup",
                enabled=True,
                interval_seconds=30,
                readiness_check=readiness,
                run_cycle=run_cycle,
            )
        await registry.stop_workers({"followup": task})
        self.assertTrue(any("WORKER_START" in entry for entry in captured.output))

    async def test_readiness_checks_postgres_schema_with_select_only(self):
        class FakeConnection:
            def __init__(self):
                self.sql = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def execute(self, statement):
                self.sql.append(str(statement))

        class FakeEngine:
            def __init__(self):
                self.connection = FakeConnection()

            def connect(self):
                return self.connection

        engine = FakeEngine()
        await check_worker_database_readiness(engine, "campaign")
        self.assertTrue(engine.connection.sql)
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT") for statement in engine.connection.sql))
        self.assertIn("message_outbox", " ".join(engine.connection.sql))

    async def test_database_failure_degrades_then_recovers_without_migration(self):
        registry = WorkerRuntimeRegistry(
            instance_id="test-instance",
            heartbeat_interval_seconds=1,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
        )
        readiness_calls = 0
        cycle_ran = asyncio.Event()

        async def readiness():
            nonlocal readiness_calls
            readiness_calls += 1
            if readiness_calls == 1:
                raise ConnectionError("database unavailable")

        async def run_cycle():
            cycle_ran.set()
            return {"processed": 0}

        with patch.dict(os.environ, {"STARTUP_DATABASE_MODE": "skip"}, clear=False):
            task = registry.start_worker(
                name="followup",
                enabled=True,
                interval_seconds=30,
                readiness_check=readiness,
                run_cycle=run_cycle,
            )
            await asyncio.wait_for(cycle_ran.wait(), timeout=1)
            await _wait_for_state(
                registry,
                "followup",
                lambda state: state["cycle_count"] >= 1 and state["last_cycle_completed"] is not None,
            )
            state = registry.local_snapshot("followup")
            self.assertEqual(StartupDatabaseMode.resolve(), StartupDatabaseMode.SKIP)
            self.assertEqual(state["status"], "RUNNING")
            self.assertEqual(state["last_error_type"], "ConnectionError")
            self.assertGreaterEqual(readiness_calls, 2)
            self.assertEqual(state["cycle_count"], 1)
            await registry.stop_workers({"followup": task})

        self.assertEqual(registry.local_snapshot("followup")["status"], "STOPPED")

    async def test_cycle_exception_is_retried_and_worker_recovers(self):
        registry = WorkerRuntimeRegistry(
            instance_id="cycle-instance",
            heartbeat_interval_seconds=1,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
        )
        calls = 0
        recovered = asyncio.Event()

        async def readiness():
            return None

        async def run_cycle():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary failure")
            recovered.set()
            return {"processed": 2}

        task = registry.start_worker(
            name="campaign",
            enabled=True,
            interval_seconds=30,
            readiness_check=readiness,
            run_cycle=run_cycle,
        )
        await asyncio.wait_for(recovered.wait(), timeout=1)
        await _wait_for_state(
            registry,
            "campaign",
            lambda state: state["cycle_count"] >= 2 and state["processed_count"] >= 2,
        )
        state = registry.local_snapshot("campaign")
        self.assertEqual(state["status"], "RUNNING")
        self.assertEqual(state["last_error_type"], "RuntimeError")
        self.assertEqual(state["cycle_count"], 2)
        self.assertEqual(state["processed_count"], 2)
        await registry.stop_workers({"campaign": task})

    async def test_campaign_attempt_count_is_not_reported_as_job_success(self):
        registry = WorkerRuntimeRegistry(
            instance_id="outcome-instance",
            heartbeat_interval_seconds=1,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
        )

        async def readiness():
            return None

        async def run_cycle():
            return {"processed": 1, "sent": 0, "retried": 1}

        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            task = registry.start_worker(
                name="campaign",
                enabled=True,
                interval_seconds=30,
                readiness_check=readiness,
                run_cycle=run_cycle,
            )
            await _wait_for_state(
                registry,
                "campaign",
                lambda state: state["cycle_count"] >= 1,
            )
            await registry.stop_workers({"campaign": task})

        self.assertFalse(any("WORKER_JOB_SUCCESS" in entry for entry in captured.output))

    async def test_disabled_worker_stays_stopped(self):
        registry = WorkerRuntimeRegistry(instance_id="disabled-instance")
        cycle_ran = False

        async def readiness():
            return None

        async def run_cycle():
            nonlocal cycle_ran
            cycle_ran = True
            return {}

        task = registry.start_worker(
            name="followup",
            enabled=False,
            interval_seconds=1,
            readiness_check=readiness,
            run_cycle=run_cycle,
        )
        await asyncio.sleep(0)
        self.assertIsNone(task)
        self.assertFalse(cycle_ran)
        self.assertEqual(registry.local_snapshot("followup")["status"], "STOPPED")

    async def test_graceful_shutdown_cancels_active_cycle_without_counting_it_complete(self):
        registry = WorkerRuntimeRegistry(instance_id="shutdown-instance", heartbeat_interval_seconds=1)
        cycle_started = asyncio.Event()
        cycle_cancelled = asyncio.Event()

        async def readiness():
            return None

        async def run_cycle():
            cycle_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cycle_cancelled.set()
                raise

        task = registry.start_worker(
            name="campaign",
            enabled=True,
            interval_seconds=30,
            readiness_check=readiness,
            run_cycle=run_cycle,
        )
        await asyncio.wait_for(cycle_started.wait(), timeout=1)
        await registry.stop_workers({"campaign": task})
        state = registry.local_snapshot("campaign")
        self.assertTrue(cycle_cancelled.is_set())
        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(state["cycle_count"], 0)
        self.assertIsNone(state["last_cycle_completed"])

    async def test_redis_heartbeat_failure_does_not_block_postgres_claim_loop(self):
        registry = WorkerRuntimeRegistry(
            instance_id="redis-failure-instance",
            heartbeat_interval_seconds=1,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
        )
        cycle_ran = asyncio.Event()

        class UnavailableRedis:
            async def set(self, *_args, **_kwargs):
                raise ConnectionError("redis unavailable")

        async def readiness():
            return None

        async def run_cycle():
            cycle_ran.set()
            return {"processed": 0}

        task = registry.start_worker(
            name="campaign",
            enabled=True,
            interval_seconds=30,
            readiness_check=readiness,
            run_cycle=run_cycle,
            redis_client=UnavailableRedis(),
        )
        await asyncio.wait_for(cycle_ran.wait(), timeout=1)
        state = registry.local_snapshot("campaign")
        self.assertEqual(state["status"], "RUNNING")
        self.assertFalse(state["heartbeat_persisted"])
        self.assertEqual(state["dependency_status"]["postgres"], "healthy")
        await registry.stop_workers({"campaign": task})

    async def test_heartbeat_contains_only_runtime_metadata_and_uses_ttl(self):
        registry = WorkerRuntimeRegistry(
            instance_id="heartbeat-instance",
            heartbeat_interval_seconds=1,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
        )
        cycle_ran = asyncio.Event()

        class FakeRedis:
            def __init__(self):
                self.values = {}
                self.ttls = {}

            async def set(self, key, value, ex):
                self.values[key] = value
                self.ttls[key] = ex

        fake_redis = FakeRedis()

        async def readiness():
            return None

        async def run_cycle():
            cycle_ran.set()
            return {"processed": 1, "sent": 1}

        task = registry.start_worker(
            name="followup",
            enabled=True,
            interval_seconds=30,
            readiness_check=readiness,
            run_cycle=run_cycle,
            redis_client=fake_redis,
        )
        await asyncio.wait_for(cycle_ran.wait(), timeout=1)
        await registry.stop_workers({"followup": task}, fake_redis)
        key = "worker:runtime:followup:heartbeat-instance"
        state = json.loads(fake_redis.values[key])
        self.assertEqual(state["worker_name"], "followup")
        self.assertEqual(state["instance_id"], "heartbeat-instance")
        self.assertTrue(state["heartbeat_persisted"])
        self.assertGreater(fake_redis.ttls[key], 0)
        self.assertNotIn("message", state)
        self.assertNotIn("phone", state)


class WorkerFlagContractTests(unittest.TestCase):
    def test_startup_mode_matrix_uses_only_each_workers_flag(self):
        scenarios = (
            ("skip", "true", "true", True, True),
            ("migrate", "true", "true", True, True),
            ("skip", "false", "false", False, False),
            ("init", "true", "false", True, False),
            ("skip", "false", "true", False, True),
        )
        for mode, followup_value, campaign_value, followup_expected, campaign_expected in scenarios:
            with self.subTest(mode=mode, followup=followup_value, campaign=campaign_value), patch.dict(
                os.environ,
                {
                    "STARTUP_DATABASE_MODE": mode,
                    "FOLLOWUP_WORKER_ENABLED": followup_value,
                    "CAMPAIGN_WORKER_ENABLED": campaign_value,
                },
                clear=False,
            ):
                StartupDatabaseMode.resolve()
                self.assertEqual(env_flag("FOLLOWUP_WORKER_ENABLED", "0"), followup_expected)
                self.assertEqual(env_flag("CAMPAIGN_WORKER_ENABLED", "1"), campaign_expected)


class WorkerStatusApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_api_returns_sanitized_worker_and_backlog_state(self):
        from unittest.mock import AsyncMock

        from app.api import main
        from app.api.routers import orquestrador

        now = "2026-10-08T10:00:00+00:00"
        followup = {
            "worker_name": "followup", "instance_id": "runtime-test", "enabled": True,
            "status": "RUNNING", "running": True, "last_heartbeat": now,
            "last_cycle_started": now, "last_cycle_completed": now,
            "last_error_at": None, "last_error_type": None, "cycle_count": 3,
            "processed_count": 0, "active_cycle": False, "heartbeat_persisted": True,
        }
        campaign = {**followup, "worker_name": "campaign"}
        runtime = {
            "followup": {"local": followup, "instances": [followup]},
            "campaign": {"local": campaign, "instances": [campaign]},
        }

        class FakeResult:
            def __init__(self, rows):
                self.rows = rows

            def all(self):
                return self.rows

        class FakeSession:
            def __init__(self):
                self.results = [
                    FakeResult([("scheduled", 3), ("retry", 1)]),
                    FakeResult([("queued", 2), ("locked", 1)]),
                ]

            async def execute(self, _statement):
                return self.results.pop(0)

            async def rollback(self):
                return None

        with patch("app.api.main.redis_client", None), \
             patch.object(orquestrador.worker_runtime, "snapshots", new=AsyncMock(return_value=runtime)):
            payload = await orquestrador.worker_status(db=FakeSession())

        self.assertTrue(payload["database"]["healthy"])
        self.assertFalse(payload["redis"]["healthy"])
        self.assertEqual(payload["workers"]["followup"]["pending"], 3)
        self.assertEqual(payload["workers"]["followup"]["retry"], 1)
        self.assertIsNone(payload["workers"]["followup"]["processing"])
        self.assertEqual(payload["workers"]["campaign"]["processing"], 1)
        self.assertNotIn("phone", payload["workers"]["followup"])

    def test_worker_status_route_remains_super_admin_only(self):
        from app.api.routers.auth import require_super_admin
        from app.api.routers.orquestrador import status_router

        self.assertTrue(any(dependency.dependency is require_super_admin for dependency in status_router.dependencies))


if __name__ == "__main__":
    unittest.main()
