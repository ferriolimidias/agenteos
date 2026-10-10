#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:?usage: validate_frontend_protected_baseline.sh IMAGE [EXPECTED_IMAGE_ID]}"
EXPECTED_ID="${2:-}"
IMAGE_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}')"

if [[ -n "$EXPECTED_ID" && "$IMAGE_ID" != "$EXPECTED_ID" ]]; then
  echo "FRONTEND_BASELINE_FAILED image_id_mismatch expected=$EXPECTED_ID actual=$IMAGE_ID"
  exit 1
fi

docker run --rm "$IMAGE" sh -c 'for f in /usr/share/nginx/html/assets/Inbox-*.js; do [ -f "$f" ] && cat "$f" && break; done' |
  node --input-type=module -e '
import fs from "node:fs";
const source = fs.readFileSync(0, "utf8");
const start = source.lastIndexOf("chat-conversation-header");
const end = source.indexOf("min-h-0 flex-1 overflow-y-auto", start);
if (start < 0 || end <= start) process.exit(2);
const header = source.slice(start, end);
for (const marker of ["Concluir atendimento", "Reabrir atendimento", "Transferir", "Excluir lead"]) {
  if (!header.includes(marker)) { console.error(`FRONTEND_BASELINE_FAILED required_header_bundle_marker=${marker}`); process.exit(1); }
}
if (!header.includes("Tags")) { console.error("FRONTEND_BASELINE_FAILED required_header_bundle_marker=Tags"); process.exit(1); }
if (!source.includes("IA autorizada") && !source.includes("IA desligada nesta conversa") && !source.includes("Verificando IA")) { console.error("FRONTEND_BASELINE_FAILED required_header_bundle_marker=IA"); process.exit(1); }
console.log("FRONTEND_INBOX_BUNDLE_BASELINE_PASS");
'

docker run --rm "$IMAGE" sh -c 'for f in /usr/share/nginx/html/assets/EmpresaDetalhes-*.js; do [ -f "$f" ] && cat "$f" && break; done' |
  node --input-type=module -e '
import fs from "node:fs";
const source = fs.readFileSync(0, "utf8");
if (!source.includes("conversion_monitor_prompt") || !source.includes("Monitor de conversão")) process.exit(1);
console.log("FRONTEND_MONITOR_BUNDLE_BASELINE_PASS");
'

TMP_DIR="$(mktemp -d)"
CONTAINER_ID="$(docker create "$IMAGE")"
trap 'docker rm "$CONTAINER_ID" >/dev/null 2>&1 || true; rm -rf "$TMP_DIR"' EXIT
docker cp "$CONTAINER_ID:/usr/share/nginx/html/assets" "$TMP_DIR/assets" >/dev/null
node - "$TMP_DIR/assets" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const acorn = require("acorn");
const dir = process.argv[2];
const files = fs.readdirSync(dir).filter((file) => file.endsWith(".js"));
for (const file of files) {
  const source = fs.readFileSync(path.join(dir, file), "utf8");
  try { acorn.parse(source, { ecmaVersion: "latest", sourceType: "module" }); }
  catch (error) {
    console.error(`FRONTEND_BASELINE_FAILED invalid_js=${file} error=${error.message}`);
    process.exit(1);
  }
}
console.log("FRONTEND_JS_RUNTIME_BASELINE_PASS");
NODE

echo "FRONTEND_ARTIFACT_BASELINE_PASS image=$IMAGE image_id=$IMAGE_ID"
