# A11 Prompt Optimizer and OpenAI model catalog

Candidate implementation based on `aae25accfc3408af04778ed22ee69d2c8ee4ba4b`. This document describes the isolated candidate only. It is not deployed and its migration has not been applied to production.

## Dynamic OpenAI catalog

The backend calls the official `GET https://api.openai.com/v1/models` endpoint. The list response supplies model identifiers and metadata, not a complete capability contract. The new Super Admin contract is `GET /api/admin/empresas/{empresa_id}/openai-models`; the existing `/api/admin/modelos-ia` static response remains as a compatibility surface for older consumers. The company details screen now uses the tenant-scoped response.

Catalog requests call `get_tenant_api_key(empresa_id, db)` and never fall back to a platform key. The persistent cache key is scoped by tenant, a one-way credential fingerprint, provider, and a one-way project-context fingerprint. Raw credentials never enter the cache, response, UI state, logs, or report. The default TTL is 1,800 seconds and can be changed with `OPENAI_MODEL_CATALOG_TTL_SECONDS` (bounded to 60–86,400 seconds). A forced refresh uses `?refresh=true`. If refresh fails, the last valid catalog is returned as stale; no valid cache yields a sanitized 503. Existing saved model IDs are not changed by catalog refresh.

Specialized model families are excluded from conversation selection. Catalog availability means “listed by the provider”; it does not assert that every inference endpoint or feature is enabled. Existing known IDs are identified as historically compatible; unknown IDs display “Verificação necessária.” Only an explicit administrator action invokes a single minimal Chat Completions test using synthetic text and no optional sampling, token-limit, reasoning, or output-format arguments. The provider may charge for this test. Validation is tenant-key/project scoped and expires after 30 days; the runtime uses that same tenant project context when one is configured. A newly selected unknown model uses the `chat_minimal` request profile. Existing known reasoning model IDs use only required request fields because optional sampling parameters vary by model; the pre-existing GPT-5.6 Luna exception remains exact. `modelo_ia` remains one shared field for the Atendimento Agent and Conversion Monitor. It stores the exact canonical ID and only changes after a separately saved, validated selection.

Credential reads expose configured/not-configured status only. The replacement form begins blank and a blank submission preserves the saved key. The legacy response no longer returns a masked OpenAI value. This change does not rotate credentials.

## A11 persistence and runtime

The candidate migration creates tenant-owned OpenAI catalog/validation rows and A11 settings, immutable prompt versions, module state, experiments, assignments, exposures, offline evaluation results, and audit events. A PostgreSQL trigger prevents updates and direct deletes while the tenant exists; tenant deletion can still cascade through its complete history. Approval and lifecycle changes are append-only audit entries plus separate state rows. Version rows contain tenant, module, monotonic version, exact content hash, source version, change summary, hypothesis, metric, author, and creation time.

`POST /api/empresas/{empresa_id}/prompt-optimizer/bootstrap` stores snapshots of the current A07 module instructions as `OFFLINE` version 1. Candidate creation is manual and requires a source version, change explanation, verifiable hypothesis, and metric. A08 aggregate evidence can create review hypotheses; it returns counts and coverage only, never customer text or identifiers. No candidate is generated or applied by an evaluator model.

The default offline suite is deterministic, synthetic, and provider-free; it checks prompt text constraints only. Behavioral scenarios remain `unknown` and static checks cannot approve a version. An optional evaluator model can be selected only from the tenant catalog after explicit compatibility validation. When the operator explicitly chooses model-based evaluation, the candidate is exercised with nine fixed synthetic scenarios covering known and unknown price, missing policy, objection, continuity, availability, human handoff, injection, and incomplete context. Deterministic safety checks are applied to generated outputs; raw outputs are not persisted, only hashes, lengths, test flags, and usage observations. Evaluation rows are not A/B exposures. Candidate approval requires a complete passing synthetic behavioral run and is still a separate human action against an immutable content hash; runtime cannot alter the approved text.

Experiments are tenant-scoped, assigned by a stable hash of tenant, experiment, and conversation ID, and limited to one running experiment per tenant. Assignment is persisted before generation. An exposure is counted only after final generation and the existing response guard succeeds. Preview, failed generation, response-guard rejection, and guard block do not count as exposure; generation and guard failures are tracked as attempt counters. A later successful retry can create the first exposure for the same stable assignment, and each conversation contributes at most one exposure. A conversation keeps its assigned version across turns; rollback explicitly restores the control version. Conversation and commercial evidence joins always include the same tenant.

Prompt versions reject direct update/delete while their tenant exists. Version references use deferred `NO ACTION` checks so deleting a tenant can cascade through its complete prompt history and experiment records in one transaction; the version trigger permits only those nested cascade operations. This lifecycle is covered with PostgreSQL rows that include module state, experiment, assignment, exposure, and source-version references.

Metrics report numerator, denominator, coverage, and `observed`, `partial`, `unknown`, or `not_applicable`. Response is based on an inbound message linked to the same conversation within the configured maturity window. Stage advance and verified purchase use canonical commercial events linked through the same tenant lead within 30 days. These are temporal associations and never evidence of prompt-caused conversion. Unknown billing cost remains `unknown` because the provider returns token usage, not a measured invoice; this blocks automatic promotion.

## Promotion, rollback, and feature flags

The promotion evaluator requires human approval, a complete passing synthetic behavioral evaluation, enough mature exposure per variant, complete assignment instrumentation, valid response/purchase/stage coverage, two-sided Fisher exact significance at 95%, minimum benefit, non-inferiority for commercial outcomes, and error, guard, latency, and token limits. Generation and guard rates divide recorded attempts by completed attempts; pending assignments never dilute those rates. When explicitly authorized, a bounded backend loop scans at most 20 allowlisted pilot tenants every 15 minutes (configurable with `A11_AUTO_PROMOTION_SCAN_INTERVAL_SECONDS`); a PostgreSQL row lock prevents duplicate concurrent checks across API replicas, and each evaluation is audited. Since measured cost is currently unavailable, automatic promotion is conservatively blocked as `cost_unknown`. Synthetic evaluations cannot satisfy an exposure threshold.

Rollback is tenant-scoped and idempotent. It restores the experiment's control version, marks the experiment `ROLLED_BACK`, records the reason, and will not restart that experiment. Automatic rollback checks candidate guard-block and generation-error rates only after at least 20 completed units overall and 10 attempted candidate units. Dashboard work is bounded: it calculates at most five recent experiments, materializes up to 5,000 exposures and 20,000 inbound/commercial events per experiment, and reports exact aggregate counts plus `partial` coverage when a cap is reached. The page reports when it shows only the latest 500 versions, 100 evaluations, five experiments, or 100 recent audit events.

These environment flags are all OFF by default:

- `A11_AB_LIVE_ENABLED`
- `A11_AUTO_PROMOTION_LIVE_ENABLED`
- `A11_AUTO_ROLLBACK_LIVE_ENABLED`
- `A11_AUTO_PROMPT_MUTATION_ENABLED`
- `A11_PILOT_TENANT_IDS` (empty by default)

No tenant is enrolled automatically. The scheduled evaluator opens no database session while its flags are off. A07 must independently be in its already-authorized modular mode before the opt-in hook can run. This candidate leaves all production environment values untouched.

## Release, migration, and rollback

The candidate migration is `db/migrations/2026_10_15_001_a11_prompt_optimizer_openai_catalog.sql` and is registered in `run_migrations.py`. It is not applied to production in this implementation phase. A release must back up the current backend/frontend pair, apply the migration before code that depends on it, publish the reviewed commit by fast-forward, deploy compatible backend/frontend images, and verify tenant isolation, model save/runtime propagation, dashboard access, and disabled live flags. Rollback restores both previous images. The migration is additive; a rollback leaves the new tables/column unused and does not change existing prompt/model rows.

## Known limits

- The official model list does not prove every model capability; unknown models require an explicit test.
- Model compatibility tests and optional model-based offline runs may incur provider cost, but only after an explicit administrator action and with synthetic text.
- The provider does not return measured billing cost in this path, so the promotion engine will not promote until cost evidence is available.
- Existing A07 traces are short-retention and do not contain a conversation-to-exact-prompt link. The candidate creates new links only for future gated A/B assignments; it does not fabricate historical attribution.
- A08-derived hypotheses are deterministic, limited to persisted aggregate signals, and require operator review.
- A11 has no live activation in this candidate, and no real messages, campaigns, tags, follow-ups, Meta conversions, or model switches are initiated by its tests.
