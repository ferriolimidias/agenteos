import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const pageSource = readFileSync(new URL("./TenantFollowUps.jsx", import.meta.url), "utf8");

test("followups remain manually configured and separate from campaign central", () => {
  assert.match(pageSource, /Retomar conversa parada/);
  assert.match(pageSource, /Envio programado \/ convite/);
  assert.match(pageSource, /Não enviar quando/);
  assert.doesNotMatch(pageSource, /\/campanhas\/nova|Central de Campanhas/);
});

test("followup editor exposes the simplified anchors and cancellation tags", () => {
  for (const text of [
    "Última mensagem do agente",
    "Primeira mensagem da pessoa",
    "Entrada pelo anúncio",
    "Aplicação de uma tag",
    "Criação do contato",
    "Data e horário fixos",
    "Template aprovado opcional",
  ]) {
    assert.match(pageSource, new RegExp(text));
  }
  assert.match(pageSource, /MetaTemplateSelector/);
  assert.match(pageSource, /cancel_tag_ids/);
  assert.match(pageSource, /campaign_or_event_id/);
  assert.match(pageSource, /needs_review/);
  assert.doesNotMatch(pageSource, /Atraso manual|Janela do anúncio|Condições para executar|Condições para cancelar|Tag posterior ao envio confirmado/);
  assert.doesNotMatch(pageSource, /Evolution/);
});

test("followup editor exposes generic text, audio and combined content", () => {
  assert.match(pageSource, /content_type/);
  assert.match(pageSource, /text_audio/);
  assert.match(pageSource, /followups\/media/);
  assert.match(pageSource, /send_as_voice/);
  assert.match(pageSource, /multipart\/form-data/);
});

test("followup editor exposes message modes and send window controls", () => {
  for (const text of [
    "Gerar com IA",
    "Mensagem fixa",
    "Template do WhatsApp",
    "Instrução para a IA",
    "Horário de envio",
    "Restringir horário",
    "America/Sao_Paulo",
    "Limitar follow-up à Free Entry Point",
  ]) {
    assert.match(pageSource, new RegExp(text));
  }
  assert.match(pageSource, /message_mode/);
  assert.match(pageSource, /send_window_enabled/);
  assert.match(pageSource, /send_window_start/);
  assert.match(pageSource, /send_window_end/);
  assert.match(pageSource, /timezone/);
});
