import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const pageSource = readFileSync(new URL("./TemplatesWhatsApp.jsx", import.meta.url), "utf8");
const selectorSource = readFileSync(new URL("../../components/MetaTemplateSelector.jsx", import.meta.url), "utf8");

test("templates page uses safe endpoints and reusable selector", () => {
  assert.match(pageSource, /Templates do WhatsApp/);
  assert.match(pageSource, /MetaTemplateSelector/);
  assert.match(pageSource, /\/empresas\/\$\{empresaId\}\/meta\/templates/);
  assert.doesNotMatch(pageSource, /EmbeddedSignup|FB\.login|\/complete\b|completeEmbeddedSignup/i);
  assert.doesNotMatch(pageSource, /send_template|send_text|send_media|\/send/i);
});

test("templates UI does not expose internal flags, secrets or raw payloads", () => {
  for (const source of [pageSource, selectorSource]) {
    assert.doesNotMatch(source, /META_LIVE_ACTIONS_ENABLED/);
    assert.doesNotMatch(source, /App Secret|access_token|authorization/i);
    assert.doesNotMatch(source, /payload bruto|raw_metadata_json|raw_component_json/i);
  }
});

test("selector keeps phone out of the template contract", () => {
  assert.match(selectorSource, /Template oficial/);
  assert.doesNotMatch(selectorSource, /phone|telefone|e164/i);
});
