# Worker runtime contract

`STARTUP_DATABASE_MODE` controls only startup database actions. `init` may run
the legacy startup initializer and custom migrations; `migrate` applies
migrations in the container entrypoint; `skip` performs no startup schema writes.
None of these modes enables or disables background workers.

`FOLLOWUP_WORKER_ENABLED` and `CAMPAIGN_WORKER_ENABLED` control their own
workers. An enabled worker checks PostgreSQL and its core tables with read-only
queries before processing. It never runs a migration. A readiness failure marks
that worker `DEGRADED` and retries with bounded backoff without taking down the
API.

PostgreSQL remains the durable job store. Campaign outbox rows are claimed with
`FOR UPDATE SKIP LOCKED`; follow-up rows retain their existing transactional
`FOR UPDATE SKIP LOCKED` claim. Redis heartbeat records use expiring keys under
`worker:runtime:<name>:<instance_id>` and are operational telemetry only. Redis
is not the durable job store or the proof that a job completed. Redis is
optional for claims; campaign claims use PostgreSQL, while follow-up uses its
existing Redis lock as an optimization around the database row lock.

The super-admin endpoint `/api/admin/workers/status` retains the legacy
follow-up metrics and adds worker lifecycle, heartbeat, cycle, dependency, and
queue status. Follow-up processing is represented by transaction row locks and
therefore does not have a durable per-row `processing` count. Campaign
processing is visible through the outbox `locked` status.

Worker shutdown cancels the owned tasks and awaits their completion. A worker
cycle exception is reported as `DEGRADED`, then retried; it does not terminate
the worker task or the API process.
