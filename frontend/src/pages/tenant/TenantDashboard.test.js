import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const dashboardSource = readFileSync(new URL("./TenantDashboard.jsx", import.meta.url), "utf8");

test("dashboard shows WhatsApp channel instead of legacy robot status", () => {
  assert.match(dashboardSource, /Canal WhatsApp/);
  assert.match(dashboardSource, /Agente de atendimento/);
  assert.match(dashboardSource, /Configuração do atendimento automático/);
  assert.match(dashboardSource, /A equipe Ferrioli pode ajudar a revisar/);
  assert.doesNotMatch(dashboardSource, /telefoneNotificacao|telefone para alertas de crédito|Creditos OpenAI|chave rejeitada/i);
  assert.doesNotMatch(dashboardSource, /Status do robô/);
});

test("dashboard translates provider and connection states for users", () => {
  assert.match(dashboardSource, /API Oficial da Meta/);
  assert.match(dashboardSource, /Evolution - legado/);
  assert.match(dashboardSource, /Não configurado/);
  assert.match(dashboardSource, /Conectado/);
  assert.doesNotMatch(dashboardSource, /Offline.*DISCONNECTED/);
});
