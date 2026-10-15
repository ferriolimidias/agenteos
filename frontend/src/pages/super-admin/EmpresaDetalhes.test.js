import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const pageSource = readFileSync(new URL("./EmpresaDetalhes.jsx", import.meta.url), "utf8");
const companiesSource = readFileSync(new URL("./Empresas.jsx", import.meta.url), "utf8");
const managerSource = readFileSync(new URL("../../components/EmpresaConexoesManager.jsx", import.meta.url), "utf8");
const paymentManagerSource = readFileSync(new URL("../../components/EmpresaPaymentManager.jsx", import.meta.url), "utf8");

test("super admin uses operational WhatsApp status instead of Evolution-only status", () => {
  assert.match(pageSource, /\/whatsapp\/status/);
  assert.doesNotMatch(pageSource, /\/conexoes\/status\/\$\{conexaoEvolution\.id\}/);
  assert.doesNotMatch(pageSource, /Estado atual da instância Evolution deste tenant/);
  assert.match(pageSource, /API Oficial da Meta/);
});

test("connection manager keeps Evolution labeled as legacy and hides raw status labels", () => {
  assert.match(managerSource, /Evolution - legado/);
  assert.match(managerSource, /API Oficial da Meta/);
  assert.match(managerSource, /formatStatusLabel/);
  assert.doesNotMatch(managerSource, />\{statusInfo\.status \|\| "Verificando"\}</);
});

test("agent configuration loads and saves allowed tag UUIDs separately from conversion settings", () => {
  assert.match(pageSource, /\/ia-config/);
  assert.match(pageSource, /\/crm\/tags\/oficiais/);
  assert.match(pageSource, /tags_ia_habilitadas/);
  assert.match(pageSource, /Tags disponíveis para a IA/);
  assert.match(pageSource, /Essa configuração não altera as conversões vinculadas às tags/);
  assert.match(pageSource, /type="checkbox"/);
  assert.match(pageSource, /value=\{tagId\}/);
  assert.match(pageSource, /toggleTagIa/);
  assert.match(pageSource, /Carregando tags oficiais/);
  assert.match(pageSource, /Não foi possível carregar as tags oficiais deste tenant/);
});

test("company management exposes tenant-scoped Mercado Pago configuration without revealing saved secrets", () => {
  assert.match(pageSource, /id: "pagamentos", label: "Pagamentos"/);
  assert.match(pageSource, /tabAtiva === "pagamentos"/);
  assert.match(pageSource, /EmpresaPaymentManager/);
  assert.match(paymentManagerSource, /\/admin\/empresas\/\$\{empresaId\}\/payments\/mercado-pago/);
  assert.match(paymentManagerSource, /Access Token/);
  assert.match(paymentManagerSource, /Webhook Secret/);
  assert.match(paymentManagerSource, /Deixe vazio para manter a credencial atual/);
  assert.match(paymentManagerSource, /readOnly/);
  assert.match(paymentManagerSource, /Cadastre esta URL nas notificações de Pagamentos do Mercado Pago/);
  assert.doesNotMatch(paymentManagerSource, /masked/);
});

test("main model select uses canonical IDs and friendly labels", () => {
  const modelStart = pageSource.indexOf("Modelo principal");
  const modelBlock = pageSource.slice(modelStart, pageSource.indexOf("</select>", modelStart) + "</select>".length);
  const selectStart = pageSource.indexOf('<select id="company-field-6"', modelStart);
  const selectBlock = pageSource.slice(selectStart, pageSource.indexOf("</select>", selectStart) + "</select>".length);
  assert.match(pageSource, /api\.get\(`\/admin\/empresas\/\$\{empresaId\}\/openai-models`\)/);
  assert.match(pageSource, /api\.get\(`\/admin\/empresas\/\$\{empresaId\}\/openai-models\?refresh=true`\)/);
  assert.match(modelBlock, /<select/);
  assert.match(modelBlock, /value=\{option\.id\}/);
  assert.match(modelBlock, /option\.name \|\| option\.id/);
  assert.match(pageSource, /Modelo atual — não confirmado/);
  assert.match(pageSource, /Verificação necessária/);
  assert.match(pageSource, /Este modelo é compartilhado pelo atendimento e pelo Monitor de Conversão/);
  assert.match(modelBlock, /aria-label="Buscar modelos OpenAI"/);
  // UX03 removed the unreachable duplicate list modal; the canonical select stays in details.
  assert.match(companiesSource, /tab=agente/);
  assert.doesNotMatch(companiesSource, /showConfigIAModal/);
  assert.doesNotMatch(selectBlock, /<input/);
});
