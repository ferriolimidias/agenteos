---
name: safe-production-change
description: Use for audits, fixes, implementations, migrations, reviews, and deploys in existing or production systems. Requires evidence-based diagnosis, isolated diffs, backups, focused review, safe migrations, controlled deploy, post-deploy validation, real-event validation, rollback planning, and final reporting. Do not use for disposable prototypes or tasks that do not change code, data, configuration, or runtime.
---

# Safe Production Change

Use this workflow for changes in systems that already exist or may be serving real users.
Start read-only, prove the failure, isolate the task, preserve rollback options, and never
declare success without evidence.

If `references/` contains a project profile relevant to the current repository, read it before
acting. Project profiles contain only project-specific constraints; keep this file generic.
Use `assets/final-report-template.md` for the required final report fields.

## Allowed States

Use exactly one of these states in reports and closeout:

- `AUDITED`
- `BLOCKED`
- `IMPLEMENTED_NOT_DEPLOYED`
- `REVIEW_FAILED`
- `READY_FOR_MIGRATION`
- `READY_FOR_DEPLOY`
- `DEPLOYED_NOT_REAL_VALIDATED`
- `REAL_VALIDATED`
- `ROLLED_BACK`

## Workflow

1. Confirm the project.
   - Verify `pwd`, Git root, branch, current commit, and runtime.
   - Confirm the requested task belongs to the current project.
   - Stop with `BLOCKED` if paths, reports, artifacts, or worktrees belong to another project.

2. Diagnose from evidence.
   - Begin in read-only mode.
   - Inspect logs, database state, code, runtime state, and existing reports as applicable.
   - Identify the first failed step in the observed flow.
   - Separate facts, hypotheses, and unproven points.
   - Do not implement a fix based only on a hypothesis.

3. Define one objective.
   - Record one primary objective.
   - State what is in scope and out of scope.
   - List resources that must be preserved.
   - Do not mix old, stale, or unrelated fixes into the task.

4. Capture initial state.
   - Record branch, commit, worktree status, images, containers, versions, and health.
   - Identify dirty changes that existed before the task.
   - Record data and artifact locations needed for rollback.

5. Back up affected assets.
   - Back up every file that will be changed.
   - If a database is involved, preserve schema and affected data before mutation.
   - Preserve previous images or deploy artifacts.
   - Exclude tokens, secrets, credentials, and private data from reports.

6. Isolate the task.
   - Identify exact files and hunks that belong to the task.
   - Compare intended changes with the initial backup.
   - Do not publish a whole worktree when unrelated changes are mixed in.
   - Use an isolated patch, branch, or worktree when needed.
   - Return `BLOCKED` if the task cannot be separated safely.

7. Implement minimally.
   - Change only what the proved cause requires.
   - Preserve backward compatibility.
   - Do not silently delete or convert old data.
   - Validate behavior in the backend, not only through the interface.
   - Add sanitized observability when necessary to prove behavior.

8. Test deterministically.
   - Run focused tests for the change.
   - Test contracts between frontend, backend, and database when the change crosses layers.
   - Test compatibility with existing data.
   - Run regressions for protected modules.
   - Do not trigger real external actions during technical tests.

9. Perform focused review.
   - Review only the task diff.
   - Compare the diff against the backup.
   - Classify findings as task-introduced, preexisting, or unrelated.
   - Do not fix unrelated findings in this task.
   - Block deploy with `REVIEW_FAILED` if any related P0 or P1 finding remains.

10. Handle migrations safely.
    - Confirm current table and schema before writing or running migration.
    - Align migration, ORM models, schemas, and services.
    - Require idempotency.
    - Prefer additive changes.
    - Preserve old records.
    - Record affected row counts before and after.
    - Define rollback before execution.
    - Execute the migration before deploying code that depends on it.

11. Deploy in a controlled way.
    - Publish only task artifacts.
    - Do not recreate services unnecessarily.
    - Preserve databases, queues, caches, integrations, and previous images.
    - Avoid destructive global commands.
    - Stop and execute rollback if health fails.

12. Validate after deploy.
    - Validate service health.
    - Validate essential endpoints.
    - Validate schema and persistence.
    - Validate the new functionality.
    - Validate old functionality in protected modules.
    - Run protected regressions.
    - Confirm no real external event was sent accidentally.

13. Run a real test only after technical validation.
    - Use a new real event.
    - Never reprocess old events.
    - Execute only one real test at a time.
    - Trace logs and database state before proposing another change.

14. Report the result.
    - Always create a report using `assets/final-report-template.md`.
    - Never declare success without evidence.
    - Include the proved root cause, changed files, unrelated changes excluded, tests,
      migration status, deploy status, health, rollback status, and exact next action.
