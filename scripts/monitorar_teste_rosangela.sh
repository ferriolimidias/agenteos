#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
OUT_DIR="${ROOT_DIR}/relatorios/codex/monitoramento-rosangela-${STAMP}"
RAW_LOG="${OUT_DIR}/docker-raw.log"
FILTERED_LOG="${OUT_DIR}/docker-filtered.log"

mkdir -p "${OUT_DIR}"

cd "${ROOT_DIR}"

echo "Monitoramento ativo. Aguardando nova mensagem da Rosângela..."

docker compose logs --no-color --timestamps -f backend frontend \
  | tee "${RAW_LOG}" \
  | grep -E 'ai\.route|ai\.graph|ai\.llm|ai\.validation|ai\.response_final|ai\.gate|ai\.processing|ai\.dispatch|META_INBOUND|META_OUTBOUND|ERROR|Traceback' \
  | grep -vE 'GET /|POST /|OPTIONS /|/foto|health|healthcheck' \
  | tee "${FILTERED_LOG}"
