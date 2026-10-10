import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const crmSource = readFileSync(new URL("./Crm.jsx", import.meta.url), "utf8");

test("crm remains kanban based while supporting operational lead details", () => {
  assert.match(crmSource, /drag|Drag|etapa|Kanban|kanban/i);
  assert.match(crmSource, /tags/i);
  assert.doesNotMatch(crmSource, /send_template|send_text|send_media/i);
});

