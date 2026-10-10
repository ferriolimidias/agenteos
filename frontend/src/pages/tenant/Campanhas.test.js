import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const pageSource = readFileSync(new URL("./Campanhas.jsx", import.meta.url), "utf8");
const appSource = readFileSync(new URL("../../App.jsx", import.meta.url), "utf8");
const layoutSource = readFileSync(new URL("../../layouts/TenantLayout.jsx", import.meta.url), "utf8");

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} must exist`);
  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(start, index + 1);
  }
  assert.fail(`${name} has no complete function body`);
}

function loadVariableHelpers() {
  const identity = extractFunction(pageSource, "templateVariableIdentity");
  const placeholders = extractFunction(pageSource, "extractNumericPlaceholders");
  const components = extractFunction(pageSource, "templateComponents");
  const variables = extractFunction(pageSource, "templateVariables");
  const sync = extractFunction(pageSource, "syncTemplateVariables");
  const required = extractFunction(pageSource, "selectedTemplateRequiredKeys");
  const runtime = extractFunction(pageSource, "templateRuntimeVariables");
  return Function(`${identity}\n${placeholders}\n${components}\n${variables}\n${sync}\n${required}\n${runtime}\nreturn { templateVariableIdentity, templateVariables, syncTemplateVariables, templateRuntimeVariables };`)();
}

test("campaign central owns templates navigation and old route redirects", () => {
  assert.match(pageSource, /Central de Campanhas/);
  assert.match(pageSource, /Templates/);
  assert.match(appSource, /templates-whatsapp" element={<Navigate to="\/painel\/campanhas\/templates"/);
  assert.doesNotMatch(layoutSource, /Templates WhatsApp/);
});

test("campaign central keeps prepare safe and starts only through the new central endpoint", () => {
  assert.match(pageSource, /safe mode/i);
  assert.match(pageSource, /Confirmar envio para/);
  assert.match(pageSource, /campaign-central\/campaigns\/\$\{campaignId\}\/\$\{action\}/);
  assert.match(pageSource, /políticas operacionais do backend/);
  assert.match(pageSource, /Enviar para análise da Meta/);
  assert.match(pageSource, /variáveis completas/);
  assert.doesNotMatch(pageSource, /send_text|send_media|send_template|Evolution/i);
});

test("campaign prepare requires audience and start confirms authoritative snapshot count", () => {
  assert.match(pageSource, /campaignAudienceSelected/);
  assert.match(pageSource, /disabled=\{saving \|\| !campaignAudienceSelected\}/);
  assert.match(pageSource, /Selecione um público antes de preparar a campanha/);
  assert.match(pageSource, /campaignPreparation\?\.snapshot\?\.selected/);
  assert.match(pageSource, /Confirmar envio para/);
  assert.match(pageSource, /snapshot_id: snapshotId/);
  assert.match(pageSource, /confirmed_recipient_count: recipientCount/);
  assert.match(pageSource, /window\.confirm/);
});

test("campaign central renders connection-scoped production and test modes", () => {
  assert.match(pageSource, /campaign_send_mode/);
  assert.match(pageSource, /Produção habilitada/);
  assert.match(pageSource, /Modo de teste ativo/);
  assert.match(pageSource, /Preparar campanha/);
  assert.match(pageSource, /Preparar em modo de teste/);
  assert.match(pageSource, /Campanhas oficiais em massa usam templates aprovados pela Meta/);
});

test("campaign central exposes required operational areas", () => {
  for (const label of ["Visão geral", "Campanhas", "Templates", "Públicos", "Resultados", "Custos", "Conversões"]) {
    assert.match(pageSource, new RegExp(label));
  }
  for (const route of ["campanhas/templates", "campanhas/publicos", "campanhas/resultados/custos", "campanhas/resultados/conversoes"]) {
    assert.match(appSource, new RegExp(route));
  }
});

test("audiences expose tag lists imports saved audiences and snapshots", () => {
  for (const label of ["Listas por tags", "Listas importadas", "Públicos salvos", "Públicos sugeridos", "Configuração de funil", "Snapshots de campanhas"]) {
    assert.match(pageSource, new RegExp(label));
  }
  assert.match(pageSource, new RegExp("campaign-central/audiences/tags"));
  assert.match(pageSource, new RegExp("campaign-central/audiences/imports"));
  assert.match(pageSource, new RegExp("campaign-central/audiences/saved"));
  assert.match(pageSource, new RegExp("campaign-central/audiences/funnels"));
  assert.match(pageSource, new RegExp("campaign-central/audiences/smart"));
});

test("saved audience builder exposes include exclude match modes preview counts and reopen", () => {
  for (const label of [
    "INCLUIR",
    "EXCLUIR",
    "Qualquer uma",
    "Todas",
    "Resumo do público",
    "Encontrados inicialmente",
    "Excluídos pelas regras",
    "Opt-out/bloqueados",
    "Telefones inválidos",
    "Duplicados",
    "Destinatários finais",
  ]) {
    assert.match(pageSource, new RegExp(label));
  }
  assert.match(pageSource, /normalizeAudienceRules/);
  assert.match(pageSource, /updateAudienceTagRule/);
  assert.match(pageSource, /audienceRuleHas/);
  assert.match(pageSource, /setSavedAudienceForm\(\{ name: audience\.name/);
});

test("audiences expose smart suggestions funnel configuration and paginated preview", () => {
  for (const label of [
    "Públicos sugeridos",
    "Configuração de funil",
    "Criar funil",
    "Adicionar etapa",
    "Inclui:",
    "Exclui:",
    "Salvar público",
    "Próxima página",
    "Subir",
    "Descer",
    "Desativar",
  ]) {
    assert.match(pageSource, new RegExp(label));
  }
  assert.match(pageSource, /previewSmartAudience/);
  assert.match(pageSource, /saveSmartAudience/);
  assert.match(pageSource, /saveFunnelStage/);
  assert.match(pageSource, /member_cursor/);
  assert.match(pageSource, /member_limit: 5/);
  assert.doesNotMatch(pageSource, /campaign-central\/campaigns\/start/);
});

test("template variable editor inserts deletes and renumbers numeric placeholders", () => {
  assert.match(pageSource, /addVariableAtCursor/);
  assert.match(pageSource, /deleteVariable/);
  assert.match(pageSource, /replacePlaceholders/);
  assert.match(pageSource, /syncTemplateVariables/);
  assert.match(pageSource, /serializeTemplateForm/);
  assert.match(pageSource, /variableComplete/);
  assert.match(pageSource, /templateVariableStats/);
  assert.match(pageSource, /Representa/);
  assert.match(pageSource, /Exemplo para Meta/);
  assert.doesNotMatch(pageSource, /suggested_source|Fallback|Nome amigável/);
});

test("template variables keep header and body positions as distinct logical fields", () => {
  const { templateVariableIdentity, templateVariables, syncTemplateVariables } = loadVariableHelpers();
  assert.equal(templateVariableIdentity("header", 1), "header:1");
  assert.equal(templateVariableIdentity("body", 1), "body:1");
  assert.deepEqual(
    templateVariables({ components: [{ component_type: "HEADER", text: "{{1}}" }, { component_type: "BODY", text: "Olá {{1}}" }] }).map(({ component, position }) => `${component.toLowerCase()}:${position}`),
    ["header:1", "body:1"],
  );
  assert.deepEqual(
    syncTemplateVariables({
      blocks: { header: { kind: "text", text: "{{1}}" }, body: { text: "Olá {{1}}" } },
      variables: [],
    }).map(({ component, position }) => `${component}:${position}`),
    ["header:1", "body:1"],
  );
});

test("body with four placeholders produces exactly four canonical variables", () => {
  const { syncTemplateVariables } = loadVariableHelpers();
  const variables = syncTemplateVariables({
    blocks: { header: { kind: "none", text: "" }, body: { text: "Olá {{1}}\nEste é {{2}}\nData {{3}}\nHorário {{4}}" } },
    variables: [],
  });
  assert.deepEqual(variables.map(({ component, position }) => `${component}:${position}`), ["body:1", "body:2", "body:3", "body:4"]);
});

test("campaign render shows exactly four controls when body metadata repeats the four placeholders", () => {
  const { templateRuntimeVariables } = loadVariableHelpers();
  const renderedFields = templateRuntimeVariables({
    components: [{ component_type: "BODY", text: "Olá {{1}} {{2}} {{3}} {{4}}" }],
    preview: { validation: { required: ["1", "2", "3", "4"] } },
  });
  assert.equal(renderedFields.length, 4);
  assert.deepEqual(renderedFields.map(({ key, label }) => [key, label]), [
    ["body_1", "{{1}} - Mensagem"],
    ["body_2", "{{2}} - Mensagem"],
    ["body_3", "{{3}} - Mensagem"],
    ["body_4", "{{4}} - Mensagem"],
  ]);
});

test("campaign render keeps header/body positions distinct without duplicate required fields", () => {
  const { templateRuntimeVariables } = loadVariableHelpers();
  const renderedFields = templateRuntimeVariables({
    components: [
      { component_type: "HEADER", text: "{{1}}" },
      { component_type: "BODY", text: "Olá {{1}}" },
    ],
    preview: { validation: { required: ["header_1", "1"] } },
  });
  assert.equal(renderedFields.length, 2);
  assert.deepEqual(renderedFields.map(({ key }) => key), ["header_1", "body_1"]);
});

test("campaign render collapses duplicate body metadata sources into four fields", () => {
  const { templateRuntimeVariables } = loadVariableHelpers();
  const renderedFields = templateRuntimeVariables({
    components: [{ component_type: "BODY", text: "{{1}} {{2}} {{3}} {{4}}" }],
    variables: [1, 2, 3, 4],
    preview: { validation: { required: ["1", "2", "3", "4"] } },
  });
  assert.equal(renderedFields.filter(({ component }) => component === "BODY").length, 4);
  assert.equal(renderedFields.length, 4);
});

test("header and body same position plus body second position produce three variables", () => {
  const { syncTemplateVariables } = loadVariableHelpers();
  const variables = syncTemplateVariables({
    blocks: { header: { kind: "text", text: "{{1}}" }, body: { text: "Olá {{1}} e {{2}}" } },
    variables: [],
  });
  assert.deepEqual(variables.map(({ component, position }) => `${component}:${position}`), ["header:1", "body:1", "body:2"]);
});

test("same component duplicate sources collapse to one variable", () => {
  const { templateVariableIdentity, syncTemplateVariables } = loadVariableHelpers();
  const variables = syncTemplateVariables({
    blocks: { header: { kind: "none", text: "" }, body: { text: "{{1}} and {{1}}" } },
    variables: [{ component: "BODY", position: 1, represents: "nome", example: "Ana" }],
  });
  assert.equal(variables.length, 1);
  assert.equal(templateVariableIdentity("BODY", 1), "body:1");
});

test("contact name and fixed value bindings retain existing API keys", () => {
  assert.match(pageSource, /acc\[variable\.key\]/);
  assert.match(pageSource, /"CONTACT_NAME"/);
  assert.match(pageSource, /"FIXED_VALUE"/);
  assert.match(pageSource, /variable_bindings/);
  assert.match(pageSource, /variable_values/);
  assert.match(pageSource, /type\.toLowerCase\(\).*position/);
});

test("removed placeholder is removed from synchronized state", () => {
  const { syncTemplateVariables } = loadVariableHelpers();
  const variables = syncTemplateVariables({
    blocks: { header: { kind: "none", text: "" }, body: { text: "Olá" } },
    variables: [{ component: "body", position: 1, represents: "nome", example: "Ana" }],
  });
  assert.deepEqual(variables, []);
});

test("button identity cannot collide with body or header", () => {
  const { templateVariableIdentity } = loadVariableHelpers();
  assert.equal(templateVariableIdentity("button", 1, 0), "button:0:1");
  assert.notEqual(templateVariableIdentity("button", 1, 0), templateVariableIdentity("body", 1));
  assert.notEqual(templateVariableIdentity("button", 1, 0), templateVariableIdentity("header", 1));
  assert.match(pageSource, /button_\$\{index\}_\$\{position\}/);
});

test("editor uses canonical identities as React keys", () => {
  assert.match(pageSource, /key=\{templateVariableIdentity\(variable\.component, variable\.position\)\}/);
  const { templateVariableIdentity } = loadVariableHelpers();
  assert.equal(new Set([templateVariableIdentity("header", 1), templateVariableIdentity("body", 1)]).size, 2);
});

test("imported list cards expose guarded delete action and preserve existing campaign controls", () => {
  assert.match(pageSource, /Excluir lista/);
  assert.match(pageSource, /Excluir esta lista importada\?/);
  assert.match(pageSource, /Os contatos do CRM e as conversas não serão excluídos/);
  assert.match(pageSource, /campaign-central\/audiences\/imports\/\$\{batch\.id\}/);
  assert.match(pageSource, /setImportBatches\(\(current\) => current\.filter/);
  assert.match(pageSource, /disabled=\{Boolean\(deletingImportId\)\}/);
});

test("template preview preserves newlines and warns about double markdown asterisks", () => {
  assert.match(pageSource, /whitespace-pre-wrap/);
  assert.match(pageSource, /break-words/);
  assert.match(pageSource, /hasDoubleAsterisk/);
  assert.match(pageSource, /prefira \*texto\* em vez de \*\*texto\*\*/);
});

test("template actions serialize the same variable contract before validate draft and submit", () => {
  assert.match(pageSource, /const payload = serializeTemplateForm\(templateForm, \{ headerImageFile: templateHeaderImageFile \}\);[\s\S]*templates\/validate/);
  assert.match(pageSource, /const payload = serializeTemplateForm\(templateForm, \{ headerImageFile: templateHeaderImageFile \}\);[\s\S]*templates\/drafts/);
  assert.match(pageSource, /let payload = serializeTemplateForm\(templateForm\);[\s\S]*remote-actions\/submit_template/);
});

test("variable completeness banner reports totals instead of stale generic message", () => {
  assert.match(pageSource, /variáveis completas/);
  assert.match(pageSource, /missingRepresents/);
  assert.match(pageSource, /missingExample/);
  assert.doesNotMatch(pageSource, /Templates com variáveis precisam de exemplos para serem enviados à análise da Meta\.<\/InfoBanner>/);
});

test("template editor exposes supported button controls and preview", () => {
  for (const label of ["Botões", "Resposta rápida", "Abrir link", "Ligar", "Adicionar botão", "Texto do botão"]) {
    assert.match(pageSource, new RegExp(label));
  }
  assert.match(pageSource, /templateButtonTypes/);
  assert.match(pageSource, /updateTemplateButton/);
  assert.match(pageSource, /addTemplateButton/);
  assert.match(pageSource, /removeTemplateButton/);
  assert.match(pageSource, /button\.url/);
  assert.match(pageSource, /button\.phone_number/);
});

test("campaign template variables use explicit bindings and no template example at runtime", () => {
  for (const label of ["Variáveis do template", "Nome do destinatário", "Valor fixo", "Valor fixo para esta campanha"]) {
    assert.match(pageSource, new RegExp(label));
  }
  assert.match(pageSource, /variable_bindings/);
  assert.match(pageSource, /campaignVariableBindingsPayload/);
  assert.match(pageSource, /templateRuntimeVariables/);
  assert.match(pageSource, /CONTACT_NAME/);
  assert.match(pageSource, /FIXED_VALUE/);
  assert.doesNotMatch(pageSource, /example.*campaignForm\.variable_values/i);
});

test("template account list renders search status filter and readonly details", () => {
  assert.match(pageSource, /Templates da conta/);
  assert.match(pageSource, /templateSearch/);
  assert.match(pageSource, /templateStatusFilter/);
  assert.match(pageSource, /Busca por nome/);
  assert.match(pageSource, /Aprovados/);
  assert.match(pageSource, /Pendentes/);
  assert.match(pageSource, /Rejeitados/);
  assert.match(pageSource, /TemplateReadOnlyDetails/);
  assert.match(pageSource, /Visualizar/);
  assert.match(pageSource, /Fechar detalhes do template/);
});

test("template readonly details show body variables preview and no edit action", () => {
  assert.match(pageSource, /Mensagem/);
  assert.match(pageSource, /Variáveis/);
  assert.match(pageSource, /Preview/);
  assert.match(pageSource, /Motivo da rejeição/);
  assert.match(pageSource, /Meta Template ID/);
  assert.match(pageSource, /templateVariables/);
  assert.match(pageSource, /templateBodyText/);
  assert.match(pageSource, /Template somente leitura/);
  assert.doesNotMatch(pageSource, /Editar template|Salvar alterações remotas|PUT|PATCH/);
});

test("template list keeps tenant api and waba connection filter", () => {
  assert.match(pageSource, /\/empresas\/\$\{empresaId\}\/meta\/templates/);
  assert.match(pageSource, /String\(template\.conexao_id\) === String\(templateForm\.conexao_id\)/);
  assert.match(pageSource, /Sincronizar com a Meta/);
  assert.match(pageSource, /meta\/templates\/sync/);
});

test("image header template flow exposes upload preview and header handle endpoint", () => {
  assert.match(pageSource, /Imagem de exemplo/);
  assert.match(pageSource, /templateHeaderImageFile/);
  assert.match(pageSource, /templateHeaderImagePreview/);
  assert.match(pageSource, /campaign-central\/templates\/header-image-handle/);
  assert.match(pageSource, /header_handle/);
  assert.match(pageSource, /imageFileAccept/);
  assert.doesNotMatch(pageSource, /Vídeo|Documento/);
});

test("image header validate uses local metadata and does not upload to Meta", () => {
  const validateBlock = pageSource.slice(pageSource.indexOf("const validateTemplate"), pageSource.indexOf("const createDraft"));
  assert.match(pageSource, /function templateHeaderLocalMedia\(file\)/);
  assert.match(pageSource, /selected: true/);
  assert.match(pageSource, /filename: file\.name/);
  assert.match(pageSource, /mime_type: file\.type/);
  assert.match(pageSource, /size: file\.size/);
  assert.match(validateBlock, /serializeTemplateForm\(templateForm, \{ headerImageFile: templateHeaderImageFile \}\)/);
  assert.match(validateBlock, /templates\/validate/);
  assert.doesNotMatch(validateBlock, /templates\/header-image-handle|FormData|upload/);
  assert.doesNotMatch(pageSource, /obter o header_handle|header_handle usado somente na criação do template/);
});

test("campaign image flow derives requirement from selected template and uses safe prepare endpoint", () => {
  assert.match(pageSource, /selectedTemplateRequiresImage/);
  assert.match(pageSource, /Imagem da campanha/);
  assert.match(pageSource, /campaignImageFile/);
  assert.match(pageSource, /campaignImagePreview/);
  assert.match(pageSource, /campaigns\/prepare-with-media/);
  assert.match(pageSource, /MEDIA_ID preparado para a campanha/);
  assert.doesNotMatch(pageSource, /\/messages|send_template|send_media/);
});

test("campaign central lists real campaigns from central endpoint and exposes lifecycle controls", () => {
  assert.match(pageSource, /campaign-central\/campaigns/);
  assert.doesNotMatch(pageSource, /\/empresas\/\$\{empresaId\}\/campanhas/);
  for (const label of ["Na fila", "Enviando", "Pausada", "Concluída", "Cancelada", "Pausar", "Retomar", "Cancelar"]) {
    assert.match(pageSource, new RegExp(label));
  }
});
