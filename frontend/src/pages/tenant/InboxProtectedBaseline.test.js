import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const inbox = fs.readFileSync(new URL("./Inbox.jsx", import.meta.url), "utf8");
const monitor = fs.readFileSync(new URL("../super-admin/EmpresaDetalhes.jsx", import.meta.url), "utf8");
const header = inbox.match(/<header className="chat-conversation-header[^>]*>[\s\S]*?<\/header>/)?.[0] || "";

test("Inbox header canonical baseline requires all operational controls", () => {
  assert.match(header, /ContactAvatar/);
  assert.match(header, /selectedLead\.nome_contato/);
  assert.match(header, /Concluir atendimento|Reabrir atendimento/);
  assert.match(header, /\bTags\b/);
  assert.match(header, /onClick=\{handleToggleBot\}|chat-header-ai/);
  assert.match(header, /title="Transferir"/);
  assert.match(header, /title="Excluir lead"/);
  assert.match(inbox, /handleToggleStatus/);
  assert.match(inbox, /handleToggleBot/);
  assert.match(inbox, /handleLeadTagsChange/);
  assert.match(inbox, /handleTransferirManual/);
  assert.match(inbox, /handleExcluirLead/);
});

test("Inbox core UI and Conversion Monitor contracts remain present", () => {
  assert.match(inbox, /MessageList/);
  assert.match(inbox, /<textarea/);
  assert.match(inbox, /\/inbox/);
  assert.match(inbox, /new WebSocket/);
  assert.match(inbox, /applyRealtimeReaction/);
  assert.match(monitor, /conversion_monitor_prompt/);
  assert.match(monitor, /Monitor de conversão/);
});
