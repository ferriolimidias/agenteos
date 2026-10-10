import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("./GestaoTags.jsx", import.meta.url), "utf8");

test("tenant tag management loads and saves IA allowlist UUIDs independently from conversions", () => {
  assert.match(source, /\/ia-config/);
  assert.match(source, /\/crm\/tags\/oficiais/);
  assert.match(source, /tags_ia_habilitadas/);
  assert.match(source, /Tags disponíveis para a IA/);
  assert.match(source, /Essa configuração não altera as conversões vinculadas às tags/);
  assert.match(source, /type="checkbox" value=\{tagId\}/);
  assert.match(source, /toggleTagIa/);
  assert.match(source, /Salvar tags disponíveis para a IA/);
  assert.match(source, /Não foi possível salvar as tags disponíveis para a IA/);
});
