# Protected Production Baselines

Protected baselines declare the minimum contracts required for AgentOS modules
that are already homologated. They are intentionally small: required routes,
essential files, schema/migration markers, frontend/backend contracts, and
tenant-scope invariants.

Run before deploy from the repository root:

```bash
./scripts/validate_protected_baselines.sh
```

Run against a candidate backend image before publishing it:

```bash
./scripts/validate_protected_baselines.sh --candidate-image agenteos-backend:candidate --require-candidate
```

The guard exits non-zero and prints `PROTECTED_BASELINE_FAILED` when any
protected contract is missing. A deploy pipeline must treat that as a hard
block.

To protect a new module, add one module entry to `baselines.json` with only the
contracts needed to prove the module is still present and compatible. Do not add
customer IDs, tokens, secrets, or private production payloads.
