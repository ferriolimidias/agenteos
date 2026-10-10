"""Supervise background workers independently from startup database mode.

Worker execution state is ephemeral telemetry. PostgreSQL remains the durable
source of truth for queued work and claim/idempotency state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Uvicorn configures this logger with the operational stderr handler. The
# default root logger in the production image filters application INFO logs.
logger = logging.getLogger("uvicorn.error")

WorkerCycle = Callable[[], Awaitable[dict[str, Any]]]
WorkerReadiness = Callable[[], Awaitable[None]]

_REQUIRED_TABLES = {
    "followup": (
        "followup_executions",
        "config_followups",
        "crm_leads",
        "channel_contacts",
        "channel_conversations",
        "conexoes",
    ),
    "campaign": (
        "message_outbox",
        "campaign_recipients",
        "campanhas_disparo",
        "conexoes",
    ),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def worker_interval(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default))
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        logger.warning("WORKER_CONFIG_INVALID key=%s fallback_seconds=%s", name, default)
        return max(minimum, int(default))


async def check_worker_database_readiness(engine: AsyncEngine, worker_name: str) -> None:
    """Verify PostgreSQL and the worker's core schema without writing or migrating."""
    tables = _REQUIRED_TABLES.get(worker_name)
    if not tables:
        raise ValueError("unknown_worker")
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        for table in tables:
            # Table names are a fixed internal allowlist, never request data.
            await connection.execute(text(f"SELECT 1 FROM {table} LIMIT 0"))


def _processed_from_result(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    for key in ("processed", "enviados"):
        try:
            return max(0, int(result.get(key) or 0))
        except (TypeError, ValueError):
            return 0
    return 0


class WorkerRuntimeRegistry:
    """Tracks worker lifecycle locally and publishes short-lived Redis heartbeats."""

    def __init__(
        self,
        *,
        instance_id: str | None = None,
        heartbeat_interval_seconds: int = 15,
        retry_base_seconds: float = 5,
        retry_max_seconds: float = 60,
    ) -> None:
        self.instance_id = instance_id or str(uuid.uuid4())
        self.heartbeat_interval_seconds = max(1, int(heartbeat_interval_seconds))
        self.retry_base_seconds = max(0.01, float(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, float(retry_max_seconds))
        self._states: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start_worker(
        self,
        *,
        name: str,
        enabled: bool,
        interval_seconds: int,
        readiness_check: WorkerReadiness,
        run_cycle: WorkerCycle,
        redis_client: Any = None,
    ) -> asyncio.Task[None] | None:
        interval = max(1, int(interval_seconds))
        previous = self._tasks.get(name)
        if previous is not None and not previous.done():
            logger.warning("WORKER_START_IGNORED worker=%s instance_id=%s reason=already_running", name, self.instance_id)
            return previous
        now = utc_now_iso()
        state = {
            "worker_name": name,
            "instance_id": self.instance_id,
            "enabled": bool(enabled),
            "status": "STARTING" if enabled else "STOPPED",
            "running": False,
            "started_at": now if enabled else None,
            "last_heartbeat": now,
            "last_cycle_started": None,
            "last_cycle_completed": None,
            "last_error_at": None,
            "last_error_type": None,
            "cycle_count": 0,
            "processed_count": 0,
            "active_cycle": False,
            "heartbeat_ttl_seconds": max(120, interval * 3),
            "heartbeat_persisted": False,
            "dependency_status": {"postgres": "starting", "redis": "optional"},
        }
        self._states[name] = state
        if not enabled:
            logger.info("WORKER_STOP worker=%s instance_id=%s reason=disabled", name, self.instance_id)
            return None
        logger.info(
            "WORKER_START worker=%s instance_id=%s enabled=true interval_seconds=%s",
            name,
            self.instance_id,
            interval,
        )
        task = asyncio.create_task(
            self._run_worker(
                name=name,
                interval_seconds=interval,
                readiness_check=readiness_check,
                run_cycle=run_cycle,
                redis_client=redis_client,
            ),
            name=f"worker:{name}:{self.instance_id}",
        )
        self._tasks[name] = task
        return task

    async def stop_workers(self, tasks: dict[str, asyncio.Task[None] | None], redis_client: Any = None) -> None:
        active = [(name, task) for name, task in tasks.items() if task is not None]
        for name, _ in active:
            state = self._states.get(name)
            if state is not None:
                state["status"] = "STOPPING"
                state["running"] = False
                await self._publish(name, redis_client)
        for _, task in active:
            if not task.done():
                task.cancel()
        if active:
            await asyncio.gather(*(task for _, task in active), return_exceptions=True)
        for name, _ in active:
            state = self._states.get(name)
            if state is not None:
                state["status"] = "STOPPED"
                state["running"] = False
                state["active_cycle"] = False
                await self._publish(name, redis_client)
                logger.info("WORKER_STOP worker=%s instance_id=%s reason=lifespan_shutdown", name, self.instance_id)

    def local_snapshot(self, name: str) -> dict[str, Any]:
        state = self._states.get(name)
        return dict(state) if state else {
            "worker_name": name,
            "instance_id": self.instance_id,
            "enabled": False,
            "status": "STOPPED",
            "running": False,
            "started_at": None,
            "last_heartbeat": None,
            "last_cycle_started": None,
            "last_cycle_completed": None,
            "last_error_at": None,
            "last_error_type": None,
            "cycle_count": 0,
            "processed_count": 0,
            "active_cycle": False,
            "heartbeat_persisted": False,
            "dependency_status": {"postgres": "unknown", "redis": "optional"},
        }

    async def snapshots(self, redis_client: Any = None) -> dict[str, dict[str, Any]]:
        names = ("followup", "campaign")
        result = {name: {"local": self.local_snapshot(name), "instances": []} for name in names}
        for name in names:
            result[name]["instances"].append(self.local_snapshot(name))
        if redis_client is None:
            return result
        try:
            async for raw_key in redis_client.scan_iter(match="worker:runtime:*"):
                key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
                try:
                    raw_state = await redis_client.get(key)
                    if not raw_state:
                        continue
                    if isinstance(raw_state, bytes):
                        raw_state = raw_state.decode()
                    state = json.loads(raw_state)
                    name = str(state.get("worker_name") or "")
                    if name not in result:
                        continue
                    instance = str(state.get("instance_id") or "")
                    if instance == self.instance_id:
                        result[name]["instances"][0] = self.local_snapshot(name)
                    else:
                        result[name]["instances"].append(state)
                except (TypeError, ValueError, json.JSONDecodeError):
                    logger.warning("WORKER_HEARTBEAT_READ_FAILED key=invalid_payload")
        except Exception as exc:
            logger.warning("WORKER_HEARTBEAT_READ_FAILED exception_type=%s", type(exc).__name__)
        for name in names:
            local = result[name]["local"]
            for state in result[name]["instances"]:
                fresh = self._heartbeat_is_fresh(state)
                state["running"] = bool(state.get("enabled") and state.get("status") == "RUNNING" and fresh)
                if state.get("enabled") and state.get("status") == "RUNNING" and not fresh:
                    state["status"] = "DEGRADED"
                    state["degraded_reason"] = "heartbeat_expired"
            local["running"] = bool(
                local.get("enabled")
                and local.get("status") == "RUNNING"
                and self._heartbeat_is_fresh(local)
            )
            if local.get("enabled") and local.get("status") == "RUNNING" and not local["running"]:
                local["status"] = "DEGRADED"
                local["degraded_reason"] = "heartbeat_expired"
        return result

    @staticmethod
    def _heartbeat_is_fresh(state: dict[str, Any]) -> bool:
        raw = state.get("last_heartbeat")
        if not raw:
            return False
        try:
            heartbeat = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            ttl = max(1, int(state.get("heartbeat_ttl_seconds") or 120))
            return (datetime.now(timezone.utc) - heartbeat).total_seconds() <= ttl
        except (TypeError, ValueError):
            return False

    async def _run_worker(
        self,
        *,
        name: str,
        interval_seconds: int,
        readiness_check: WorkerReadiness,
        run_cycle: WorkerCycle,
        redis_client: Any,
    ) -> None:
        retry_seconds = self.retry_base_seconds
        state = self._states[name]
        needs_readiness_check = True
        try:
            await self._publish(name, redis_client)
            while True:
                if needs_readiness_check:
                    try:
                        await asyncio.wait_for(readiness_check(), timeout=15)
                        state["dependency_status"] = {"postgres": "healthy", "redis": "optional"}
                        needs_readiness_check = False
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        state["status"] = "DEGRADED"
                        state["running"] = False
                        state["dependency_status"] = {"postgres": "degraded", "redis": "optional"}
                        state["last_error_at"] = utc_now_iso()
                        state["last_error_type"] = type(exc).__name__
                        logger.error(
                            "WORKER_DEGRADED worker=%s instance_id=%s stage=readiness exception_type=%s retry_seconds=%s",
                            name,
                            self.instance_id,
                            type(exc).__name__,
                            retry_seconds,
                        )
                        await self._publish(name, redis_client)
                        await asyncio.sleep(retry_seconds)
                        retry_seconds = min(retry_seconds * 2, self.retry_max_seconds)
                        continue

                state["status"] = "RUNNING"
                state["running"] = True
                state["active_cycle"] = True
                state["last_cycle_started"] = utc_now_iso()
                logger.debug("WORKER_CYCLE_START worker=%s instance_id=%s cycle=%s", name, self.instance_id, state["cycle_count"] + 1)
                cycle_task = asyncio.create_task(run_cycle(), name=f"worker-cycle:{name}:{self.instance_id}")
                cycle_ended = False
                try:
                    while not cycle_task.done():
                        done, _ = await asyncio.wait(
                            {cycle_task},
                            timeout=self.heartbeat_interval_seconds,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if not done:
                            await self._publish(name, redis_client)
                    cycle_result = await cycle_task
                    cycle_ended = True
                    processed = _processed_from_result(cycle_result)
                    state["status"] = "RUNNING"
                    state["running"] = True
                    state["processed_count"] += processed
                    retry_seconds = self.retry_base_seconds
                    logger.debug("WORKER_CYCLE_COMPLETE worker=%s instance_id=%s processed=%s cycle=%s", name, self.instance_id, processed, state["cycle_count"] + 1)
                except asyncio.CancelledError:
                    if not cycle_task.done():
                        cycle_task.cancel()
                        await asyncio.gather(cycle_task, return_exceptions=True)
                    raise
                except Exception as exc:
                    cycle_ended = True
                    state["status"] = "DEGRADED"
                    state["running"] = False
                    state["dependency_status"] = {"postgres": "unknown", "redis": "optional"}
                    needs_readiness_check = True
                    state["last_error_at"] = utc_now_iso()
                    state["last_error_type"] = type(exc).__name__
                    logger.error(
                        "WORKER_CYCLE_FAILED worker=%s instance_id=%s stage=cycle exception_type=%s retry_seconds=%s",
                        name,
                        self.instance_id,
                        type(exc).__name__,
                        retry_seconds,
                    )
                finally:
                    state["active_cycle"] = False
                    if cycle_ended:
                        state["last_cycle_completed"] = utc_now_iso()
                        state["cycle_count"] += 1
                    await self._publish(name, redis_client)
                await asyncio.sleep(interval_seconds if state["status"] == "RUNNING" else retry_seconds)
                if state["status"] != "RUNNING":
                    retry_seconds = min(retry_seconds * 2, self.retry_max_seconds)
        except asyncio.CancelledError:
            state["status"] = "STOPPING"
            state["running"] = False
            logger.info("WORKER_STOPPING worker=%s instance_id=%s", name, self.instance_id)
            raise
        finally:
            state["status"] = "STOPPED"
            state["running"] = False
            state["active_cycle"] = False
            state["last_heartbeat"] = utc_now_iso()
            await self._publish(name, redis_client)

    async def _publish(self, name: str, redis_client: Any) -> None:
        state = self._states.get(name)
        if state is None:
            return
        state["last_heartbeat"] = utc_now_iso()
        key = f"worker:runtime:{name}:{self.instance_id}"
        if redis_client is None:
            state["heartbeat_persisted"] = False
            logger.debug("WORKER_HEARTBEAT worker=%s instance_id=%s status=%s redis_persisted=false", name, self.instance_id, state["status"])
            return
        try:
            state["heartbeat_persisted"] = True
            value = json.dumps(state, separators=(",", ":"), sort_keys=True)
            await redis_client.set(key, value, ex=int(state["heartbeat_ttl_seconds"]))
            logger.debug("WORKER_HEARTBEAT worker=%s instance_id=%s status=%s redis_persisted=true", name, self.instance_id, state["status"])
        except Exception as exc:
            state["heartbeat_persisted"] = False
            logger.warning("WORKER_HEARTBEAT_WRITE_FAILED worker=%s instance_id=%s exception_type=%s", name, self.instance_id, type(exc).__name__)


worker_runtime = WorkerRuntimeRegistry()
