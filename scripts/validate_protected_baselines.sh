#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASELINES="${PROTECTED_BASELINES_FILE:-$ROOT/protected_baselines/baselines.json}"

exec python3 "$ROOT/scripts/protected_baselines.py" \
  --workspace-root "$ROOT" \
  --baselines "$BASELINES" \
  "$@"
