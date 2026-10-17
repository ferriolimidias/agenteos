# AGENTS.md

Production omnichannel platform (multi-tenant WhatsApp/Meta/Instagram). Treat as a live system: read the mandatory workflow before touching code.

## Required workflow (read first)

This repo ships its own change protocol. Load the skill before any audit/fix/implement/migrate/review/deploy:

```
.agents/skills/safe-production-change/SKILL.md
```

Project constraints (`.agents/skills/safe-production-change/references/agentos-profile.md`):
- Never run `docker compose down`; never recreate Postgres, Redis, or Evolution.
- Preserve WhatsApp, Meta webhooks, connections, tokens, Embedded Signup.
- Apply migrations before the backend code that depends on them.
- Before deploy, run `./scripts/validate_protected_baselines.sh` (and `--candidate-image <img> --require-candidate` for a candidate image); a `PROTECTED_BASELINE_FAILED` blocks deploy. Protected contracts live in `protected_baselines/baselines.json`.
- Final reports go in `reports/`.

## Product architecture decisions (mandatory for UX, Admin, Meta, and legacy cleanup)

The owner-approved target **before deleting legacy code** is documented in:
- `docs/architecture/README.md` (decision index)
- `docs/architecture/ARQUITETURA-UX-ADMIN-TENANT-20261017.md` (client/Admin navigation and permissions)
- `docs/architecture/PLANO-SANEAMENTO-E-LEGADOS-20261017.md` (classification, sequence and removal gates)
- Issues #34 (UX first, legacy cleanup after) and #33 (Meta credentials/conversions).

Mandatory: client home is **Inbox**; keep dashboard under Results and keep deep-link aliases; client menu has no prompts, A11, JEV SHADOW or technical tokens. Prompt/model/experiment mutation and Meta token management belong to Super Admin **with API-side RBAC**. Meta Official WhatsApp provider, Instagram API credentials, Meta Marketing/System User token, and CRM Dataset/CAPI are separate contracts. Evolution remains active. Never delete migrations, historical delivery evidence, or LeoFT exercise media just for a cleanup. Docs are plans, NOT evidence of deployed code.

## Stack & layout

- Backend: FastAPI + SQLAlchemy (async) + Pydantic v2, Python 3.11. Entrypoint `app.api.main:app`.
- Frontend: React/Vite (`frontend/`), plus a second app `apps/anamnese-leonardo/` built into the same nginx image.
- Infra: Postgres (pgvector), Redis, Evolution API (WhatsApp gateway), Meta Graph/Marketing/CAPI. Orchestrated by `docker-compose.yml`.
- DB: engine/settings in `db/database.py`; models in `db/models.py` and `db/leoft_models.py`; migrations in `db/migrations/`.
- Multi-tenant: everything is scoped by `empresa_id`. Preserve tenant isolation invariants.

## Commands

Backend tests (pytest + pytest-asyncio, marker mode; NOT in `requirements.txt`, use a dev venv):
```
pytest                                          # run from repo root
pytest tests/services/test_bouncer_hardening.py # single test file
```
Most tests are isolated/mocked. Real-Postgres tests auto-skip unless an explicit URL env is set (e.g. `LEOFT_GENERATION_TEST_DATABASE_URL`). No committed pytest/pyproject config exists.

Backend run (local): `uvicorn app.api.main:app` (docker-compose sets `STARTUP_DATABASE_MODE=skip`).

DB init/migrations (idempotent, run in `init`/`migrate` startup only):
```
python init_db.py          # create_all + vector extension
python run_migrations.py   # applies a HARDCODED list of SQL files (update the list when adding migrations)
```
`scripts/check_schema_compatibility.py` runs read-only at startup and fails the app on schema mismatch. Restart backend without DB actions: `scripts/restart_backend_without_db_actions.sh` (root, from `/root/agenteos`).

Frontend (Node 22 per `.nvmrc`; host Node may be older, build via Docker):
```
cd frontend && npm run dev / build / lint
docker run --rm -v "$PWD":/app -w /app node:22-alpine sh -lc "npm ci && npm run build"
```
Frontend tests use `node --test` (`npm run test:meta`, `test:templates`, etc.), not Jest.

## Startup database mode

`STARTUP_DATABASE_MODE` accepts exactly `init` | `migrate` | `skip` (source of truth: `app/core/startup_database_mode.py`). Absent defaults to `init`. The mode controls startup database actions only. `FOLLOWUP_WORKER_ENABLED` and `CAMPAIGN_WORKER_ENABLED` independently control their workers; enabled workers start in `skip` after read-only Postgres/schema readiness checks and retry in degraded state if unavailable. Workers never run migrations. Some older `docs/startup-database-mode*.md` text may describe the former `normal` value; trust the code and current worker runtime contract.

## Live-action gating (do not bypass)

Real/live effects are gated by env flags that default OFF. Any "real" action requires an explicit flag + tenant scope, never assume it is enabled:
- `AGENTEOS_LLM_MODE` (empty = real provider; `mock` for CI/isolated)
- `META_LIVE_ACTIONS_ENABLED`, `META_MARKETING_WRITE_ENABLED` (+ `*_CREATE_ENABLED`), `META_SYSTEM_USER_TEST_ENABLED`, `META_BUSINESS_MESSAGING_TEST_ENABLED`
- `LEOFT_TELEGRAM_REAL_ENABLED`, `HERMES_*` (remote LeoFT), `MERCADO_PAGO_LEOFT_*`

Secrets live in `.env` and `.secrets/` (both gitignored). Never commit tokens/keys; `tests/security/` and `scripts/verificar_secrets_nao_expostos.py` enforce this.

## Repo hygiene gotchas

- The working tree is intentionally dirty and full of untracked scratch dirs: `.snapshot-*`, `.tmp-*`, `.preserve-*`, `.candidate-*`, `.worktrees/`, `backups/`, `reports/`, `exports/`, `imports/`, `relatorios/`, and many root-level `RELATORIO_*.md` / `*.py` migration scripts. None of these are the app; `.gitignore` does not cover them. Don't treat them as source of truth and don't bulk-delete them.
- `git status` may show pre-existing modified files not related to your task — capture that baseline before changing anything.
