import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, "Instagram.jsx"), "utf8");
const globalSettingsSource = readFileSync(resolve(here, "../super-admin/ConfiguracoesGlobais.jsx"), "utf8");

assert.equal((source.match(/export default function Instagram/g) || []).length, 1);

// Jornada simples: salvar ID manual + token sem teste prévio separado.
assert.match(source, /instagram_id: text\(professionalAccountId\)/);
assert.match(source, /instagram_user_access_token: text\(accessToken\)/);
assert.match(source, /disabled=\{connectionBusy \|\| !text\(accessToken\) \|\| !text\(professionalAccountId\)\}/);
assert.match(source, /if \(!text\(professionalAccountId\) \|\| !text\(accessToken\)\)/);
assert.match(source, /setAccessToken\(""\)/);
assert.match(source, /Salvar conexão/);
assert.doesNotMatch(source, /Testar conexão/);
assert.doesNotMatch(source, /teste o novo token antes de salvar/);
assert.doesNotMatch(source, /Teste a conexão antes de salvar/);
assert.doesNotMatch(source, /instagram\/connection\/test/);
assert.doesNotMatch(source, /validation_receipt: identification\.validation_receipt/);
assert.doesNotMatch(source, /!identification\?\.validation_receipt/);
assert.doesNotMatch(source, /const \[identification, setIdentification\]/);

// Múltiplas conexões e seleção explícita por operação.
assert.match(source, /instagram\/connections/);
assert.match(source, /data\?\.connections/);
assert.match(source, /selectedConnectionId/);
assert.match(source, /aria-pressed=\{selected\}/);
assert.match(source, /instagram\/media/);
assert.match(source, /instagram\/automations/);
assert.match(source, /instagram\/history/);
assert.ok((source.match(/connection_id:/g) || []).length >= 4);
assert.match(source, /private_reply_config/);
assert.match(source, /privateReplyMessageType/);
assert.match(source, /button_template/);
assert.match(source, /web_url/);
assert.match(source, /postback/);
assert.match(source, /isAllowedButtonUrl/);
assert.match(source, /Adicionar botão/);
assert.match(source, /Prévia/);
assert.match(source, /normalizeItems\(mediaResponse\.data, "media"\)/);
assert.match(source, /normalizeItems\(automationsResponse\.data, "automations"\)/);
assert.match(source, /normalizeItems\(historyResponse\.data, "history"\)/);
assert.match(source, /data\?\.paging\?\.after \|\| data\?\.paging\?\.next_cursor/);
assert.match(source, /item\.comment_text \|\| item\.comment/);

// Publicações: carga inicial e botão Atualizar devem usar o mesmo endpoint real quando a conexão está pronta.
assert.match(source, /void loadResources\(selectedConnectionId, usableConnection\)/);
assert.match(source, /onClick=\{\(\) => loadResources\(selectedConnectionId, usableConnection, \{ preserveMediaOnInvalid: true \}\)\}/);
assert.match(source, /api\.get\(`\/empresas\/\$\{empresaId\}\/instagram\/media`, \{ params: \{ connection_id: id, limit: 25 \}/);
assert.doesNotMatch(source, /onClick=\{\(\) => loadResources\(selectedConnectionId\)\}/);

// Publicações: erro ou contrato inesperado no refresh não pode trocar lista válida por [].
assert.match(source, /function hasItemsPayload\(data, key\)/);
assert.match(source, /if \(hasItemsPayload\(mediaResponse\.data, "media"\)\) \{/);
assert.match(source, /preserveMediaOnInvalid: true/);
assert.match(source, /A resposta de publicações veio em formato inesperado\. A lista atual foi preservada\./);

// Publicações: somente uma lista vazia válida vinda de media/items/array pode substituir os posts atuais.
assert.match(source, /key && Array\.isArray\(data\?\.\[key\]\)/);
assert.match(source, /setMedia\(normalizeItems\(mediaResponse\.data, "media"\)\)/);
assert.match(source, /Promise\.resolve\(\{ data: \{ media: \[\], paging: \{\} \} \}\)/);

// Publicações: resposta antiga não deve sobrescrever refresh mais novo.
assert.match(source, /const resourcesRequestRef = useRef\(0\)/);
assert.match(source, /if \(resourcesRequestRef\.current !== requestId\) return/);

// Identificação exibida ao usuário.
for (const label of ["Instagram ID", "Usuário", "Permissões", "Validade", "Última validação", "Status desconhecido"]) {
  assert.match(source, new RegExp(label));
}

// O fluxo removido não pode reaparecer em texto, estado ou endpoint desta página.
for (const forbidden of [
  ["oa", "uth"].join(""),
  ["authorized", "profiles"].join("-"),
  ["available", "profiles"].join("-"),
  ["accounts", "discover"].join("/"),
  ["accounts", "select"].join("/"),
  ["system", "user"].join("_"),
  ["graph", "instagram"].join("."),
  ["catálogo", "global"].join(" "),
  ["perfil", "autorizado"].join(" "),
  ["seleção", "global"].join(" "),
  ["facebook", " page"].join(""),
]) {
  assert.equal(source.toLowerCase().includes(forbidden), false, `legacy Instagram flow found: ${forbidden}`);
}

// Verificações focadas de acessibilidade e contenção visual.
assert.match(source, /role=\{notice\.type === "danger" \? "alert" : "status"\}/);
assert.match(source, /aria-live="polite"/);
assert.match(source, /aria-label="Fechar aviso"/);
assert.match(source, /aria-label="Fechar editor de automação"/);
assert.match(source, /autoComplete="new-password"/);
assert.match(source, /className="min-w-0 space-y-6"/);
assert.match(source, /break-words/);
assert.match(source, /break-all/);
assert.match(source, /translate="no"/);

// Global settings keep no cross-tenant Instagram secrets; company-scoped credentials live under the company admin routes.
for (const forbidden of [
  "Configuração global do webhook Instagram",
  "instagram_app_secret",
  "instagram_verify_token",
  "Instagram App Secret",
  "Instagram Verify Token",
  "instagram_graph_api_version",
  "api/webhooks/meta/instagram",
]) assert.equal(globalSettingsSource.toLowerCase().includes(forbidden.toLowerCase()), false, `super-admin Instagram UI found: ${forbidden}`);

assert.match(source, /Reconfigurar/);
assert.match(source, /Salvar reconfiguração/);
assert.match(source, /connection\/\$\{editingConnectionId\}\/reconfigure/);
assert.match(source, /setAccessToken\(""\)/);
assert.match(source, /Pendente de reconfiguração/);
assert.match(source, /Indisponível até concluir a validação/);
assert.match(source, /normalizeApiError/);
assert.match(source, /connectionMode/);
assert.match(source, /Cancelar/);
assert.doesNotMatch(source.match(/const payload = \{[\s\S]*?\n\s{6}\};/)?.[0] || "", /validation_receipt/);
assert.doesNotMatch(source, /Facebook Page ID/);
assert.doesNotMatch(source, /Business Account ID/);
assert.match(source, /clearConnectionEditor/);
assert.match(source, /if \(editingConnectionId === id\) clearConnectionEditor\(\)/);
assert.match(source, /connectionMode === "reconfigure" && editingConnectionId/);

console.log("Instagram manual ID/token transport contract checks passed");
