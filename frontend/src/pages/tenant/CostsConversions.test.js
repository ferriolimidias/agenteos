import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const pageSource = readFileSync(new URL("./Campanhas.jsx", import.meta.url), "utf8");

test("costs and conversions do not fabricate confirmed values", () => {
  // Exercise the formatter instead of requiring obsolete explanatory copy.
  const formatterSource = pageSource.match(/function formatMoney\([^]*?\n\}/)?.[0];
  assert.ok(formatterSource, "money formatter must exist");
  const formatMoney = Function(`${formatterSource}; return formatMoney;`)();
  assert.equal(formatMoney(null), "Sem dados");
  assert.equal(formatMoney(undefined), "Sem dados");
  assert.equal(formatMoney(0), new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(0));
  assert.equal(formatMoney(123.45), new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(123.45));

  assert.match(pageSource, /analyticsResults\?\.costs\?\.state === "available" \? <MetricCard label="Custo total" value=\{formatMoney\(analyticsResults\?\.costs\?\.total\)\}/);
  assert.match(pageSource, /activeTab === "costs" && analyticsResults\?\.costs\?\.state !== "available" \? \(\s*<InfoBanner[^>]*>Não disponível: não há fonte auditável de custo/);
  assert.match(pageSource, /activeTab === "conversions" && analyticsResults\?\.conversions\?\.state !== "available" \? \(\s*<InfoBanner[^>]*>Sem eventos de conversão registrados\./);
});

