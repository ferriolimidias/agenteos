import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("./TenantAgentes.jsx", import.meta.url), "utf8");

test("agent page does not expose the removed atendimento test feature", () => {
  assert.doesNotMatch(source, /Testar atendimento|Número autorizado|Ativar teste|Encerrar teste/);
  assert.doesNotMatch(source, /ia-test-mode|testMode|allowlisted_phone|max_ai_replies/);
  assert.match(source, /Atendimento em produção/);
});

test("agent page keeps the real production configuration surface", () => {
  assert.match(source, /Capacidades do agente/);
  assert.match(source, /Solicitar alteração/);
  assert.doesNotMatch(source, /access_token|app_secret|authorization code/i);
});
