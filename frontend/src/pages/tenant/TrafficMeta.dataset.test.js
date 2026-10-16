import { readFileSync } from "node:fs";
import assert from "node:assert";

const source = readFileSync(new URL("./TrafficMeta.jsx", import.meta.url), "utf8");

assert.match(source, /function datasetDisplay/);
assert.match(source, /Nome genérico da Meta/);
assert.match(source, /ID: \$\{datasetId\}/);
assert.doesNotMatch(source, /Briefing do criativo/);
assert.match(source, /Selecionar imagens/);
assert.match(source, /multiple onChange=\{selectImages\}/);
assert.match(source, /\/traffic\/meta\/ad-copy\/analyze-image/);
assert.match(source, /analysis_status/);
assert.match(source, /maximo 5 imagens|máximo 5 imagens/);
assert.match(source, /Carregar dados reais/);
assert.match(source, /Dados reais da campanha/);
assert.match(source, /Conjuntos de anúncios/);
assert.match(source, /Salvar ativos Meta/);
assert.match(source, /Credencial de conversões/);
assert.doesNotMatch(source, /Configurar token/);
assert.match(source, /setAdsets\(response\.data\?\.adsets \|\| \[\]\)/);
assert.match(source, /setAds\(response\.data\?\.ads \|\| \[\]\)/);
assert.doesNotMatch(source, /Dataset ID\\s*<input/);
assert.match(source, /MARKETING_RECONNECT_STATUSES = new Set\(\["REAUTH_MARKETING_REQUIRED", "REAUTH_REQUIRED", "TOKEN_INVALID"\]\)/);
assert.match(source, /READY_ADMIN_MANAGED_READ/);
assert.match(source, /Credencial administrativa de Marketing \(somente leitura\)/);
assert.match(source, /MARKETING_RECONNECT_STATUSES\.has\(credentials\?\.marketing_readiness_status\)/);
assert.doesNotMatch(source, /Objetivo comercial|conversion_goal_key/);
assert.match(source, /Uma única jornada: anúncio AgentOS → WhatsApp Oficial → atendimento AgentOS\/JEV/);
assert.match(source, /analytics\/ads/);
assert.match(source, /Resultados comerciais por anúncio/);
assert.match(source, /Aceitação no endpoint Meta não comprova entrega ao cliente nem otimização da campanha/);
// The A12 creative media upload path remains available while the technical goal selector is removed.
assert.match(source, /Selecionar imagens/);
