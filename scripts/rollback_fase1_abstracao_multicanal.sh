#!/usr/bin/env bash
set -euo pipefail

MISSION_DIR="relatorios/factorynova/2026-07-17_20-17-agentos-fase1-abstracao-multicanal"
PRECHANGE_DIR="${MISSION_DIR}/prechange"
APPLY="false"

if [[ "${1:-}" == "--apply" ]]; then
  APPLY="true"
elif [[ $# -gt 0 ]]; then
  echo "Uso: $0 [--apply]" >&2
  exit 2
fi

if [[ ! -d "${PRECHANGE_DIR}" ]]; then
  echo "Snapshot prechange nao encontrado: ${PRECHANGE_DIR}" >&2
  exit 1
fi

restore_files=(
  "app/services/channel_factory.py"
)

new_files=(
  "app/services/channels/__init__.py"
  "app/services/channels/contracts.py"
  "app/services/channels/types.py"
  "app/services/channels/registry.py"
  "app/services/channels/resolver.py"
  "app/services/channels/providers/__init__.py"
  "app/services/channels/providers/evolution_legacy.py"
  "tests/channels/__init__.py"
  "tests/channels/test_contracts_registry.py"
  "scripts/verificar_fase1_abstracao_multicanal.py"
  "scripts/rollback_fase1_abstracao_multicanal.sh"
)

echo "Rollback Fase 1 abstracao multicanal"
if [[ "${APPLY}" != "true" ]]; then
  echo "Modo dry-run. Passe --apply para executar."
fi

for file in "${restore_files[@]}"; do
  snapshot="${PRECHANGE_DIR}/${file}"
  if [[ ! -f "${snapshot}" ]]; then
    echo "Snapshot ausente para ${file}: ${snapshot}" >&2
    exit 1
  fi
  echo "Restaurar: ${snapshot} -> ${file}"
  if [[ "${APPLY}" == "true" ]]; then
    cp -a "${snapshot}" "${file}"
  fi
done

for file in "${new_files[@]}"; do
  echo "Remover arquivo criado pela missao, se existir: ${file}"
  if [[ "${APPLY}" == "true" && -f "${file}" ]]; then
    rm -f "${file}"
  fi
done

echo "Diretorios app/services/channels e tests/channels devem ser removidos manualmente apenas se vazios e revisados."
echo "Nenhum git reset, git clean ou alteracao fora da lista foi executado."
