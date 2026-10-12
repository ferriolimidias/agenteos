import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock3,
  Copy,
  DollarSign,
  Eye,
  FileText,
  FolderOpen,
  GitBranch,
  ListChecks,
  Megaphone,
  PauseCircle,
  PlayCircle,
  Plus,
  RefreshCw,
  Save,
  Search,
  Send,
  Trash2,
  Upload,
  Users,
  X,
} from "lucide-react";

import "../../styles/ux05-automation.css";

import MetaTemplateSelector from "../../components/MetaTemplateSelector";
import api from "../../services/api";
import { ActionButton, EmptyState, InfoBanner, LoadingState, MetricCard, PageHeader, StatusBadge } from "../../components/ui";
import { getActiveEmpresaId } from "../../utils/auth";

const tabs = [
  { id: "overview", label: "Visão geral", path: "/painel/campanhas", icon: BarChart3 },
  { id: "campaigns", label: "Campanhas", path: "/painel/campanhas/nova", icon: Megaphone },
  { id: "templates", label: "Templates", path: "/painel/campanhas/templates", icon: FileText },
  { id: "audiences", label: "Públicos", path: "/painel/campanhas/publicos", icon: Users },
  { id: "results", label: "Resultados", path: "/painel/campanhas/resultados", icon: CheckCircle2 },
  { id: "costs", label: "Custos", path: "/painel/campanhas/resultados/custos", icon: DollarSign },
  { id: "conversions", label: "Conversões", path: "/painel/campanhas/resultados/conversoes", icon: GitBranch },
];

const statusLabels = {
  local_draft: "Rascunho local",
  submitted: "Enviado à Meta",
  pending: "Pendente",
  approved: "Aprovado",
  rejected: "Rejeitado",
  paused: "Pausado",
  disabled: "Desabilitado",
  archived: "Arquivado",
  unknown: "Desconhecido",
};

const campaignStatusLabels = {
  draft: "Rascunho",
  prepared: "Preparada",
  queued: "Na fila",
  running: "Enviando",
  paused: "Pausada",
  completed: "Concluída",
  cancelled: "Cancelada",
  failed: "Erro",
  pendente: "Pendente",
  executando: "Executando",
  concluido: "Concluída",
  erro: "Erro",
};

function campaignStatusTone(status) {
  if (["completed", "concluido", "sent", "delivered", "read"].includes(status)) return "active";
  if (["failed", "erro", "cancelled", "blocked"].includes(status)) return "danger";
  if (["queued", "running", "paused", "pendente", "executando"].includes(status)) return "warning";
  return "neutral";
}

const templateStatusOptions = [
  { value: "", label: "Todos" },
  { value: "approved", label: "Aprovados" },
  { value: "pending", label: "Pendentes" },
  { value: "rejected", label: "Rejeitados" },
];

const categoryLabels = {
  marketing: "Marketing",
  utility: "Utilidade",
  authentication: "Autenticação",
  unknown: "Desconhecida",
};

const templateInitial = {
  internal_name: "",
  technical_name: "",
  conexao_id: "",
  language: "pt_BR",
  category: "UTILITY",
  description: "",
  blocks: {
    header: { kind: "none", text: "" },
    body: { text: "Olá, {{1}}." },
    footer: { text: "" },
    buttons: [],
  },
  variables: [{ position: 1, represents: "Nome do cliente", component: "body", example: "José" }],
};

const campaignInitial = {
  name: "",
  objective: "",
  description: "",
  conexao_id: "",
  template_id: "",
  timezone: "America/Sao_Paulo",
  owner: "",
  include_tags: [],
  saved_audience_id: "",
  variable_values: {},
  variable_bindings: {},
  country: "BR",
  currency: "BRL",
  origin: "manual",
  ai_reply_enabled: true,
};

const imageFileAccept = "image/jpeg,image/png";
const maxImageBytes = 5 * 1024 * 1024;
const templateButtonTypes = [
  { value: "QUICK_REPLY", label: "Resposta rápida" },
  { value: "URL", label: "Abrir link" },
  { value: "PHONE_NUMBER", label: "Ligar" },
];
const campaignVariableBindingTypes = [
  { value: "CONTACT_NAME", label: "Nome do destinatário" },
  { value: "FIXED_VALUE", label: "Valor fixo" },
];

function validateLocalImageFile(file) {
  if (!file) return "Selecione uma imagem.";
  if (!["image/jpeg", "image/png"].includes(file.type)) return "Use imagem JPEG ou PNG.";
  if (file.size > maxImageBytes) return "A imagem deve ter no máximo 5 MB.";
  return "";
}

function templateHeaderLocalMedia(file) {
  if (!file) return null;
  return {
    selected: true,
    filename: file.name || "imagem",
    mime_type: file.type || "",
    size: file.size || 0,
  };
}

function activeTabFromPath(pathname) {
  if (pathname.includes("/templates")) return "templates";
  if (pathname.includes("/publicos")) return "audiences";
  if (pathname.includes("/resultados/custos")) return "costs";
  if (pathname.includes("/resultados/conversoes")) return "conversions";
  if (pathname.includes("/resultados")) return "results";
  if (pathname.includes("/nova")) return "campaigns";
  return "overview";
}

function countValue(bucket, keys) {
  return keys.reduce((total, key) => total + Number(bucket?.[key] || 0), 0);
}

function formatMoney(value, currency = "BRL") {
  if (value === null || value === undefined) return "Sem dados";
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(Number(value || 0));
}

function formatNumber(value) {
  return new Intl.NumberFormat("pt-BR").format(Number(value || 0));
}

function formatPercent(value) {
  return `${new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 }).format(Number(value || 0))}%`;
}

function formatTimelineLabel(value, granularity) {
  if (!value) return "-";
  try {
    const date = new Date(value);
    return new Intl.DateTimeFormat("pt-BR", granularity === "hour" ? { day: "2-digit", month: "2-digit", hour: "2-digit" } : { day: "2-digit", month: "2-digit" }).format(date);
  } catch {
    return "-";
  }
}

function ResultsLineChart({ timeline }) {
  const rows = Array.isArray(timeline?.series) ? timeline.series : [];
  const keys = [
    ["meta_accepted", "Aceitas", "#2563eb"],
    ["delivered", "Entregues", "#059669"],
    ["read", "Lidas", "#7c3aed"],
    ["failed", "Falhas", "#dc2626"],
  ];
  const width = 720;
  const height = 240;
  const padding = 34;
  const maxValue = Math.max(1, ...rows.flatMap((row) => keys.map(([key]) => Number(row[key] || 0))));
  const xFor = (index) => padding + (rows.length <= 1 ? 0 : (index * (width - padding * 2)) / (rows.length - 1));
  const yFor = (value) => height - padding - (Number(value || 0) * (height - padding * 2)) / maxValue;
  const pathFor = (key) => rows.map((row, index) => `${index === 0 ? "M" : "L"} ${xFor(index)} ${yFor(row[key])}`).join(" ");
  if (!rows.length) {
    return <EmptyState title="Sem série temporal" description="Não há timestamps reais suficientes para montar o gráfico." icon={BarChart3} />;
  }
  return (
    <div className="overflow-x-auto">
      <svg role="img" aria-label="Desempenho das campanhas ao longo do tempo" viewBox={`0 0 ${width} ${height}`} className="min-w-[640px] rounded-lg border border-slate-200 bg-white">
        <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="#cbd5e1" />
        <line x1={padding} y1={padding} x2={padding} y2={height - padding} stroke="#cbd5e1" />
        {keys.map(([key, label, color]) => (
          <path key={key} d={pathFor(key)} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <title>{label}</title>
          </path>
        ))}
        {rows.map((row, index) => (
          <text key={row.bucket || index} x={xFor(index)} y={height - 10} textAnchor="middle" className="fill-slate-500 text-[10px]">
            {index === 0 || index === rows.length - 1 || rows.length <= 8 ? formatTimelineLabel(row.bucket, timeline?.granularity) : ""}
          </text>
        ))}
      </svg>
      <div className="mt-3 flex flex-wrap gap-3 text-xs font-semibold text-slate-600">
        {keys.map(([key, label, color]) => <span key={key} className="inline-flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />{label}</span>)}
      </div>
    </div>
  );
}

function ResultsFunnel({ funnel, failed }) {
  const rows = Array.isArray(funnel) ? funnel : [];
  if (!rows.length) return <EmptyState title="Sem funil" description="Nenhum destinatário no escopo selecionado." icon={ListChecks} />;
  return (
    <div className="space-y-2">
      {rows.map((item) => (
        <div key={item.key} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
          <span className="font-semibold text-slate-800">{item.label}</span>
          <span className="text-right"><strong className="text-slate-950">{formatNumber(item.value)}</strong>{item.previous_rate === null || item.previous_rate === undefined ? null : <span className="ml-2 text-xs text-slate-500">{formatPercent(item.previous_rate)}</span>}</span>
        </div>
      ))}
      <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">Falhas: <strong>{formatNumber(failed)}</strong></div>
    </div>
  );
}

function PreviewMessage({ form }) {
  const body = form.blocks.body.text || "";
  const header = form.blocks.header.kind === "text" ? form.blocks.header.text || "" : "";
  const renderText = (value) => form.variables.reduce((text, variable) => {
    const sample = variable.example || `[${variable.represents || `Variável ${variable.position}`}]`;
    return text.replaceAll(`{{${variable.position}}}`, sample);
  }, value || "");
  const rendered = form.variables.reduce((text, variable) => {
    const sample = variable.example || `[${variable.represents || `Variável ${variable.position}`}]`;
    return text.replaceAll(`{{${variable.position}}}`, sample);
  }, body);
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <p className="text-xs font-semibold uppercase text-slate-500">Preview</p>
      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4 text-sm leading-6 text-slate-800 whitespace-pre-wrap break-words">
        {form.blocks.header.kind === "image" ? (
          <div className="mb-3 rounded border border-dashed border-slate-300 bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-500">Cabeçalho com imagem</div>
        ) : null}
        {header ? <p className="mb-2 font-semibold text-slate-900">{renderText(header)}</p> : null}
        {rendered || "Preencha o corpo para visualizar."}
        {form.blocks.buttons?.length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {form.blocks.buttons.map((button, index) => (
              <span key={`${button.type}-${index}`} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700">
                {button.text || `Botão ${index + 1}`}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function replacePlaceholders(text, mapping) {
  return String(text || "").replace(/\{\{\s*(\d+)\s*\}\}/g, (match, value) => {
    const next = mapping.get(Number(value));
    return next ? `{{${next}}}` : "";
  });
}

function templateBadgeTone(status) {
  if (status === "approved") return "active";
  if (["rejected", "disabled", "deleted"].includes(status)) return "danger";
  return "warning";
}

function formatTemplateDate(value) {
  if (!value) return "-";
  try {
    return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
  } catch {
    return "-";
  }
}

function normalizeAudienceRules(rules = {}) {
  const includeRules = Array.isArray(rules.include?.rules)
    ? rules.include.rules
    : (Array.isArray(rules.include_tags) ? rules.include_tags.map((tagId) => ({ type: "tag", tag_id: tagId })) : []);
  const excludeRules = Array.isArray(rules.exclude?.rules)
    ? rules.exclude.rules
    : (Array.isArray(rules.exclude_tags) ? rules.exclude_tags.map((tagId) => ({ type: "tag", tag_id: tagId })) : []);
  return {
    include: {
      match: rules.include?.match === "all" || rules.tag_logic === "all" ? "all" : "any",
      rules: includeRules.filter((rule) => rule?.tag_id),
    },
    exclude: {
      match: rules.exclude?.match === "all" || rules.exclude_match === "all" ? "all" : "any",
      rules: excludeRules.filter((rule) => rule?.tag_id),
    },
  };
}

function audienceRuleHas(rules, section, tagId) {
  return normalizeAudienceRules(rules)[section].rules.some((rule) => String(rule.tag_id) === String(tagId));
}

function updateAudienceTagRule(currentRules, section, tagId, enabled) {
  const next = normalizeAudienceRules(currentRules);
  const rules = next[section].rules.filter((rule) => String(rule.tag_id) !== String(tagId));
  if (enabled) rules.push({ type: "tag", tag_id: tagId });
  next[section] = { ...next[section], rules };
  return next;
}

function audienceCount(preview, key, fallback = 0) {
  return Number(preview?.counts?.[key] ?? preview?.[key] ?? fallback);
}

function templateComponents(template) {
  return Array.isArray(template?.components) ? template.components : [];
}

function templateComponentByType(template, type) {
  return templateComponents(template).find((component) => String(component.component_type || "").toUpperCase() === type);
}

function templateBodyText(template) {
  return templateComponentByType(template, "BODY")?.text || "";
}

function templateVariables(template) {
  const seen = new Set();
  const variables = [];
  for (const component of templateComponents(template)) {
    const text = String(component.text || "");
    for (const match of text.matchAll(/\{\{\s*(\d+)\s*\}\}/g)) {
      const position = match[1];
      const identity = templateVariableIdentity(component.component_type, position);
      if (seen.has(identity)) continue;
      seen.add(identity);
      const examples = component.example?.body_text || component.example?.header_text || [];
      const firstRow = Array.isArray(examples?.[0]) ? examples[0] : examples;
      variables.push({
        position,
        component: component.component_type,
        example: Array.isArray(firstRow) ? firstRow[Number(position) - 1] : "",
      });
    }
  }
  return variables;
}

function extractNumericPlaceholders(text) {
  return [...new Set([...String(text || "").matchAll(/\{\{\s*(\d+)\s*\}\}/g)].map((match) => Number(match[1])).filter(Boolean))].sort((a, b) => a - b);
}

function templateVariableIdentity(component, position, buttonIndex = null) {
  const normalizedComponent = String(component || "body").toLowerCase();
  const normalizedPosition = Number(position);
  if (normalizedComponent === "button") return `button:${Number(buttonIndex)}:${normalizedPosition}`;
  return `${normalizedComponent}:${normalizedPosition}`;
}

function syncTemplateVariables(form) {
  const refs = [];
  if (form.blocks.header.kind === "text") {
    extractNumericPlaceholders(form.blocks.header.text).forEach((position) => refs.push({ position, component: "header" }));
  }
  extractNumericPlaceholders(form.blocks.body.text).forEach((position) => refs.push({ position, component: "body" }));
  const existing = new Map((form.variables || []).map((item) => [templateVariableIdentity(item.component, item.position), item]));
  return refs.map((ref) => {
    const key = templateVariableIdentity(ref.component, ref.position);
    const old = existing.get(key) || {};
    return {
      position: ref.position,
      component: ref.component,
      represents: old.represents || "",
      example: old.example || "",
    };
  });
}

function serializeTemplateForm(form, options = {}) {
  const payload = { ...form, variables: syncTemplateVariables(form) };
  const localMedia = templateHeaderLocalMedia(options.headerImageFile);
  if (payload.blocks?.header?.kind === "image" && localMedia) {
    payload.blocks = {
      ...payload.blocks,
      header: {
        ...payload.blocks.header,
        local_media: localMedia,
      },
    };
  }
  return payload;
}

function variableComplete(variable) {
  return Boolean(String(variable?.represents || "").trim() && String(variable?.example || "").trim());
}

function templateVariableStats(form) {
  const variables = syncTemplateVariables(form);
  const missingRepresents = variables.filter((variable) => !String(variable.represents || "").trim()).length;
  const missingExample = variables.filter((variable) => !String(variable.example || "").trim()).length;
  return {
    total: variables.length,
    complete: variables.filter(variableComplete).length,
    missingRepresents,
    missingExample,
  };
}

function hasDoubleAsterisk(text) {
  return /\*\*[^*\n][\s\S]*?[^*\n]\*\*/.test(String(text || ""));
}

function templateRuntimeVariables(template) {
  const variables = [];
  const seen = new Set();
  for (const component of templateComponents(template)) {
    const type = String(component.component_type || component.type || "").toUpperCase();
    const text = String(component.text || "");
    for (const position of extractNumericPlaceholders(text)) {
      const key = `${type.toLowerCase()}_${position}`;
      if (seen.has(key)) continue;
      seen.add(key);
      variables.push({ key, position, component: type, label: `{{${position}}} - ${type === "HEADER" ? "Cabeçalho" : "Mensagem"}` });
    }
    const buttons = Array.isArray(component.buttons) ? component.buttons : [];
    buttons.forEach((button, index) => {
      for (const position of extractNumericPlaceholders(button?.url)) {
        const key = `button_${index}_${position}`;
        if (seen.has(key)) continue;
        seen.add(key);
        variables.push({ key, position, component: "BUTTON", label: `{{${position}}} - Botão ${index + 1}` });
      }
    });
  }
  const required = selectedTemplateRequiredKeys(template);
  for (const key of required) {
    if (key === "header_image") continue;
    const normalizedKey = /^\d+$/.test(String(key)) ? `body_${Number(key)}` : String(key);
    if (seen.has(normalizedKey)) continue;
    seen.add(normalizedKey);
    const isBodyPosition = /^body_\d+$/.test(normalizedKey);
    variables.push({
      key: normalizedKey,
      position: isBodyPosition ? normalizedKey.slice(5) : key,
      component: isBodyPosition ? "BODY" : "",
      label: isBodyPosition ? `{{${normalizedKey.slice(5)}}} - Mensagem` : key,
    });
  }
  return variables;
}

function selectedTemplateRequiredKeys(template) {
  const required = template?.preview?.validation?.required;
  return Array.isArray(required) ? required.filter((item) => item !== "header_image") : [];
}

function TemplateReadOnlyDetails({ template, onClose }) {
  if (!template) return null;
  const header = templateComponentByType(template, "HEADER");
  const body = templateComponentByType(template, "BODY");
  const footer = templateComponentByType(template, "FOOTER");
  const buttons = templateComponentByType(template, "BUTTONS");
  const variables = templateVariables(template);

  return (
    <div className="fm-automation-overlay fixed inset-0 z-50 flex items-stretch justify-end bg-slate-950/40 p-0 sm:p-4" role="dialog" aria-modal="true" aria-label="Detalhes do template">
      <div className="fm-template-details flex h-full w-full max-w-3xl flex-col overflow-hidden bg-white shadow-xl sm:rounded-xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 p-5">
          <div>
            <p className="text-xs font-semibold uppercase text-slate-500">Template somente leitura</p>
            <h2 className="mt-1 break-words text-xl font-semibold text-slate-950">{template.name}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-600">
              <span>{categoryLabels[template.normalized_category] || template.raw_category || "Categoria desconhecida"}</span>
              <span>{template.language}</span>
              <StatusBadge tone={templateBadgeTone(template.normalized_status)}>{statusLabels[template.normalized_status] || template.raw_status || "Desconhecido"}</StatusBadge>
            </div>
          </div>
          <button type="button" onClick={onClose} className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50" aria-label="Fechar detalhes do template" title="Fechar">
            <X size={18} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
            <div className="space-y-5">
              {header ? (
                <section>
                  <h3 className="text-sm font-semibold text-slate-900">Cabeçalho</h3>
                  <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-800 whitespace-pre-wrap break-words">{header.text || header.format || "Sem texto sincronizado."}</div>
                </section>
              ) : null}

              <section>
                <h3 className="text-sm font-semibold text-slate-900">Mensagem</h3>
                <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-800 whitespace-pre-wrap break-words">{body?.text || "Sem corpo sincronizado."}</div>
              </section>

              {footer ? (
                <section>
                  <h3 className="text-sm font-semibold text-slate-900">Rodapé</h3>
                  <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-800 whitespace-pre-wrap break-words">{footer.text || "Sem texto sincronizado."}</div>
                </section>
              ) : null}

              {buttons?.buttons?.length ? (
                <section>
                  <h3 className="text-sm font-semibold text-slate-900">Botões</h3>
                  <div className="mt-2 space-y-2">
                    {buttons.buttons.map((button, index) => (
                      <div key={`${button.type || "button"}-${index}`} className="rounded-lg border border-slate-200 p-3 text-sm text-slate-700">
                        <p className="font-semibold">{button.text || `Botão ${index + 1}`}</p>
                        <p className="mt-1 text-xs text-slate-500">{button.type || "Tipo não informado"}{button.url ? ` · ${button.url}` : ""}{button.phone_number ? ` · ${button.phone_number}` : ""}</p>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}

              <section>
                <h3 className="text-sm font-semibold text-slate-900">Variáveis</h3>
                {variables.length ? (
                  <div className="mt-2 space-y-2">
                    {variables.map((variable) => (
                      <div key={`${variable.component}-${variable.position}`} className="rounded-lg border border-slate-200 p-3 text-sm">
                        <p className="font-semibold text-slate-900">{"{{"}{variable.position}{"}}"}</p>
                        <p className="mt-1 text-slate-500">Componente: {variable.component || "-"}</p>
                        {variable.example ? <p className="mt-1 text-slate-600">Exemplo: {variable.example}</p> : null}
                      </div>
                    ))}
                  </div>
                ) : <p className="mt-2 text-sm text-slate-500">Nenhuma variável sincronizada.</p>}
              </section>
            </div>

            <aside className="space-y-4">
              <section>
                <h3 className="text-sm font-semibold text-slate-900">Preview</h3>
                <div className="mt-2 rounded-xl border border-emerald-100 bg-emerald-50 p-3">
                  <div className="rounded-lg bg-white p-3 text-sm leading-6 text-slate-800 shadow-sm whitespace-pre-wrap break-words">{templateBodyText(template) || "Sem mensagem para preview."}</div>
                </div>
              </section>

              {template.rejection_reason ? (
                <section className="rounded-lg border border-red-200 bg-red-50 p-3">
                  <h3 className="text-sm font-semibold text-red-800">Motivo da rejeição</h3>
                  <p className="mt-2 text-sm text-red-700">{template.rejection_reason}</p>
                </section>
              ) : null}

              <details className="rounded-lg border border-slate-200 p-3">
                <summary className="cursor-pointer text-sm font-semibold text-slate-900">Detalhes</summary>
                <dl className="mt-3 space-y-2 text-xs text-slate-600">
                  <div><dt className="font-semibold text-slate-800">Meta Template ID</dt><dd className="break-all">{template.remote_template_id || "-"}</dd></div>
                  <div><dt className="font-semibold text-slate-800">Nome técnico</dt><dd className="break-all">{template.name}</dd></div>
                  <div><dt className="font-semibold text-slate-800">Última sincronização</dt><dd>{formatTemplateDate(template.last_synced_at)}</dd></div>
                  <div><dt className="font-semibold text-slate-800">Criado no AgentOS</dt><dd>{formatTemplateDate(template.first_seen_at || template.created_at)}</dd></div>
                  <div><dt className="font-semibold text-slate-800">Conexão</dt><dd className="break-all">{template.conexao_id || "-"}</dd></div>
                  <div><dt className="font-semibold text-slate-800">WABA</dt><dd className="break-all">{template.waba_id || "-"}</dd></div>
                </dl>
              </details>
            </aside>
          </div>
        </div>

        <div className="border-t border-slate-200 p-4">
          <ActionButton type="button" variant="secondary" onClick={onClose}>Fechar</ActionButton>
        </div>
      </div>
    </div>
  );
}

export default function Campanhas() {
  const empresaId = getActiveEmpresaId();
  const location = useLocation();
  const navigate = useNavigate();
  const activeTab = activeTabFromPath(location.pathname);

  const [overview, setOverview] = useState(null);
  const [analyticsResults, setAnalyticsResults] = useState(null);
  const [resultsError, setResultsError] = useState("");
  const [resultsFilters, setResultsFilters] = useState({ campaign_id: "", period_start: "", period_end: "", conexao_id: "" });
  const [templates, setTemplates] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [connections, setConnections] = useState([]);
  const [tags, setTags] = useState([]);
  const [tagAudiences, setTagAudiences] = useState([]);
  const [importBatches, setImportBatches] = useState([]);
  const [savedAudiences, setSavedAudiences] = useState([]);
  const [snapshots, setSnapshots] = useState([]);
  const [funnels, setFunnels] = useState([]);
  const [smartAudiences, setSmartAudiences] = useState([]);
  const [audienceTab, setAudienceTab] = useState("tags");
  const [importFile, setImportFile] = useState(null);
  const [importPreview, setImportPreview] = useState(null);
  const [deletingImportId, setDeletingImportId] = useState("");
  const [importPayload, setImportPayload] = useState({ tag_id: "", tag_name: "", source: "importacao_manual", consent_status: "unknown", default_country_code: "55", mapping: {} });
  const [savedAudienceForm, setSavedAudienceForm] = useState({
    name: "",
    description: "",
    rules: {
      include: { match: "any", rules: [] },
      exclude: { match: "any", rules: [] },
    },
  });
  const [savedAudiencePreview, setSavedAudiencePreview] = useState(null);
  const [smartAudiencePreview, setSmartAudiencePreview] = useState(null);
  const [funnelForm, setFunnelForm] = useState({ name: "", description: "" });
  const [stageForm, setStageForm] = useState({
    funnel_id: "",
    name: "",
    position: 10,
    rules: {
      include: { match: "any", rules: [] },
      exclude: { match: "any", rules: [] },
    },
  });
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [templateForm, setTemplateForm] = useState(templateInitial);
  const [campaignForm, setCampaignForm] = useState(campaignInitial);
  const [templateSearch, setTemplateSearch] = useState("");
  const [templateStatusFilter, setTemplateStatusFilter] = useState("");
  const [selectedTemplateDetails, setSelectedTemplateDetails] = useState(null);
  const [templateHeaderImageFile, setTemplateHeaderImageFile] = useState(null);
  const [templateHeaderImagePreview, setTemplateHeaderImagePreview] = useState("");
  const [campaignImageFile, setCampaignImageFile] = useState(null);
  const [campaignImagePreview, setCampaignImagePreview] = useState("");
  const [templateValidation, setTemplateValidation] = useState(null);
  const [campaignPreparation, setCampaignPreparation] = useState(null);
  const [saving, setSaving] = useState(false);
  const [campaignActionId, setCampaignActionId] = useState("");
  const headerRef = useRef(null);
  const bodyRef = useRef(null);
  const [activeTextField, setActiveTextField] = useState("body");

  const officialConnections = useMemo(
    () => connections.filter((item) => String(item.provider || item.tipo || "").toLowerCase().includes("meta")),
    [connections]
  );
  const selectedCampaignConnection = useMemo(
    () => officialConnections.find((item) => String(item.id) === String(campaignForm.conexao_id)) || null,
    [officialConnections, campaignForm.conexao_id]
  );
  const campaignSendMode = selectedCampaignConnection?.campaign_send_mode === "production" ? "production" : "test_only";
  const isProductionCampaignMode = campaignSendMode === "production";
  const approvedTemplates = useMemo(() => templates.filter((item) => item.normalized_status === "approved"), [templates]);
  const visibleTemplates = useMemo(() => {
    const search = templateSearch.trim().toLowerCase();
    return templates.filter((template) => {
      const matchesConnection = templateForm.conexao_id ? String(template.conexao_id) === String(templateForm.conexao_id) : true;
      const matchesStatus = templateStatusFilter ? template.normalized_status === templateStatusFilter : true;
      const matchesSearch = search ? String(template.name || "").toLowerCase().includes(search) : true;
      return matchesConnection && matchesStatus && matchesSearch;
    });
  }, [templates, templateForm.conexao_id, templateSearch, templateStatusFilter]);
  const selectedTemplate = useMemo(
    () => templates.find((item) => String(item.id) === String(campaignForm.template_id)) || null,
    [templates, campaignForm.template_id]
  );
  const selectedTemplateRequiresImage = useMemo(
    () => templateComponents(selectedTemplate).some((component) => String(component.component_type || "").toUpperCase() === "HEADER" && String(component.format || "").toUpperCase() === "IMAGE"),
    [selectedTemplate]
  );
  const selectedTemplateVariables = useMemo(() => templateRuntimeVariables(selectedTemplate), [selectedTemplate]);

  const loadData = useCallback(async () => {
    if (!empresaId) return;
    setLoading(true);
    try {
      const resultsParams = Object.fromEntries(Object.entries(resultsFilters).filter(([, value]) => String(value || "").trim()));
      const [overviewRes, resultsRes, templatesRes, campaignsRes, connectionsRes, tagsRes, tagAudiencesRes, importsRes, savedRes, snapshotsRes, funnelsRes, smartRes] = await Promise.all([
        api.get(`/empresas/${empresaId}/campaign-central/overview`),
        api.get(`/empresas/${empresaId}/campaign-central/results`, { params: resultsParams }),
        api.get(`/empresas/${empresaId}/meta/templates`),
        api.get(`/empresas/${empresaId}/campaign-central/campaigns`),
        api.get(`/empresas/${empresaId}/conexoes/`),
        api.get(`/empresas/${empresaId}/crm/tags/oficiais`),
        api.get(`/empresas/${empresaId}/campaign-central/audiences/tags`),
        api.get(`/empresas/${empresaId}/campaign-central/audiences/imports`),
        api.get(`/empresas/${empresaId}/campaign-central/audiences/saved`),
        api.get(`/empresas/${empresaId}/campaign-central/audiences/snapshots`),
        api.get(`/empresas/${empresaId}/campaign-central/audiences/funnels`),
        api.get(`/empresas/${empresaId}/campaign-central/audiences/smart`),
      ]);
      setOverview(overviewRes.data || {});
      setAnalyticsResults(resultsRes.data || {});
      setResultsError("");
      setTemplates(Array.isArray(templatesRes.data) ? templatesRes.data : []);
      setCampaigns(Array.isArray(campaignsRes.data) ? campaignsRes.data : []);
      setConnections(Array.isArray(connectionsRes.data) ? connectionsRes.data : []);
      setTags(Array.isArray(tagsRes.data) ? tagsRes.data : []);
      setTagAudiences(Array.isArray(tagAudiencesRes.data) ? tagAudiencesRes.data : []);
      setImportBatches(Array.isArray(importsRes.data) ? importsRes.data : []);
      setSavedAudiences(Array.isArray(savedRes.data) ? savedRes.data : []);
      setSnapshots(Array.isArray(snapshotsRes.data) ? snapshotsRes.data : []);
      setFunnels(Array.isArray(funnelsRes.data) ? funnelsRes.data : []);
      setSmartAudiences(Array.isArray(smartRes.data) ? smartRes.data : []);
      setMessage("");
    } catch (error) {
      setMessage(error?.response?.data?.detail || "Não foi possível carregar a Central de Campanhas.");
      setResultsError("Não foi possível carregar os resultados.");
    } finally {
      setLoading(false);
    }
  }, [empresaId, resultsFilters]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (!templateForm.conexao_id && officialConnections.length === 1) {
      setTemplateForm((prev) => ({ ...prev, conexao_id: officialConnections[0].id }));
    }
  }, [officialConnections, templateForm.conexao_id]);

  useEffect(() => {
    if (!campaignForm.conexao_id && officialConnections.length === 1) {
      setCampaignForm((prev) => ({ ...prev, conexao_id: officialConnections[0].id }));
    }
  }, [officialConnections, campaignForm.conexao_id]);

  useEffect(() => {
    if (templateForm.blocks.header.kind !== "image" && templateHeaderImageFile) {
      clearTemplateHeaderImage();
    }
  }, [templateForm.blocks.header.kind, templateHeaderImageFile]);

  useEffect(() => {
    if (!selectedTemplateRequiresImage && campaignImageFile) {
      clearCampaignImage();
    }
  }, [selectedTemplateRequiresImage, campaignImageFile]);

  const validateTemplate = async () => {
    if (!empresaId) return;
    const payload = serializeTemplateForm(templateForm, { headerImageFile: templateHeaderImageFile });
    const res = await api.post(`/empresas/${empresaId}/campaign-central/templates/validate`, payload);
    setTemplateValidation(res.data);
  };

  const createDraft = async () => {
    if (!empresaId) return;
    setSaving(true);
    try {
      const payload = serializeTemplateForm(templateForm, { headerImageFile: templateHeaderImageFile });
      await api.post(`/empresas/${empresaId}/campaign-central/templates/drafts`, payload);
      setMessage("Rascunho local criado. Nenhuma chamada foi enviada à Meta.");
      await loadData();
    } catch (error) {
      setTemplateValidation(error?.response?.data?.detail?.validation || error?.response?.data?.detail);
      setMessage("Revise os campos do template antes de salvar.");
    } finally {
      setSaving(false);
    }
  };

  const campaignAction = async (campaignId, action, payload = undefined) => {
    if (!empresaId || !campaignId) return;
    const label = action === "start" ? "iniciar" : action === "pause" ? "pausar" : action === "resume" ? "retomar" : "cancelar";
    if (["start", "cancel"].includes(action) && !window.confirm(`Confirmar ${label} campanha?`)) return;
    setCampaignActionId(`${campaignId}:${action}`);
    try {
      await api.post(`/empresas/${empresaId}/campaign-central/campaigns/${campaignId}/${action}`, payload);
      setMessage(`Campanha ${label} executado com sucesso.`);
      await loadData();
    } catch (error) {
      setMessage(error?.response?.data?.detail || `Não foi possível ${label} a campanha.`);
    } finally {
      setCampaignActionId("");
    }
  };

  const startPreparedCampaign = async () => {
    const campaignId = campaignPreparation?.campaign_id;
    if (!campaignId) return;
    const recipientCount = Number(campaignPreparation?.snapshot?.selected ?? 0);
    const snapshotId = campaignPreparation?.snapshot_id || campaignPreparation?.snapshot?.id;
    if (!snapshotId || recipientCount <= 0) {
      setMessage("Snapshot preparado inválido. Prepare a campanha novamente.");
      return;
    }
    const confirmed = window.confirm(`Confirmar envio para ${recipientCount} contatos`);
    if (!confirmed) return;
    await campaignAction(campaignId, "start", {
      snapshot_id: snapshotId,
      confirmed_recipient_count: recipientCount,
    });
    setCampaignPreparation(null);
  };

  const submitTemplateToMeta = async () => {
    if (!empresaId) return;
    setSaving(true);
    try {
      let payload = serializeTemplateForm(templateForm);
      if (templateForm.blocks.header.kind === "image") {
        const imageError = validateLocalImageFile(templateHeaderImageFile);
        if (imageError) {
          setMessage(imageError);
          setSaving(false);
          return;
        }
        const form = new FormData();
        form.append("conexao_id", templateForm.conexao_id);
        form.append("file", templateHeaderImageFile);
        const upload = await api.post(`/empresas/${empresaId}/campaign-central/templates/header-image-handle`, form, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        payload = {
          ...payload,
          blocks: {
            ...payload.blocks,
            header: {
              ...payload.blocks.header,
              example: { header_handle: [upload.data?.header_handle] },
              image_example: {
                filename: upload.data?.filename,
                mime_type: upload.data?.mime_type,
                byte_size: upload.data?.byte_size,
              },
            },
          },
        };
      }
      const res = await api.post(`/empresas/${empresaId}/campaign-central/templates/remote-actions/submit_template`, payload);
      const status = res.data?.status || res.data?.raw_status || "pending";
      setMessage(`Template enviado para análise da Meta. Status atual: ${status}.`);
      setTemplateValidation(res.data?.validation || null);
      await loadData();
    } catch (error) {
      const detail = error?.response?.data?.detail;
      setTemplateValidation(detail?.validation || detail || null);
      setMessage(detail?.errors?.[0]?.message || detail?.message || detail?.meta_error?.error_user_msg || (typeof detail === "string" ? detail : "Não foi possível enviar o template para a Meta."));
    } finally {
      setSaving(false);
    }
  };

  const selectTemplateHeaderImage = (event) => {
    const file = event.target.files?.[0] || null;
    event.target.value = "";
    const error = validateLocalImageFile(file);
    if (error) {
      setMessage(error);
      setTemplateHeaderImageFile(null);
      setTemplateHeaderImagePreview("");
      return;
    }
    setTemplateHeaderImageFile(file);
    setTemplateHeaderImagePreview(URL.createObjectURL(file));
    setMessage("");
  };

  const clearTemplateHeaderImage = () => {
    setTemplateHeaderImageFile(null);
    setTemplateHeaderImagePreview("");
  };

  const selectCampaignImage = (event) => {
    const file = event.target.files?.[0] || null;
    event.target.value = "";
    const error = validateLocalImageFile(file);
    if (error) {
      setMessage(error);
      setCampaignImageFile(null);
      setCampaignImagePreview("");
      return;
    }
    setCampaignImageFile(file);
    setCampaignImagePreview(URL.createObjectURL(file));
    setMessage("");
  };

  const clearCampaignImage = () => {
    setCampaignImageFile(null);
    setCampaignImagePreview("");
  };

  const syncTemplates = async () => {
    if (!empresaId || !templateForm.conexao_id) return;
    setSaving(true);
    try {
      await api.post(`/empresas/${empresaId}/meta/templates/sync`, null, { params: { conexao_id: templateForm.conexao_id } });
      await loadData();
      setMessage("Templates sincronizados com a Meta.");
    } catch (error) {
      setMessage(error?.response?.data?.detail || "Não foi possível sincronizar templates.");
    } finally {
      setSaving(false);
    }
  };

  const updateTemplateText = (field, value) => {
    setTemplateForm((prev) => {
      const next = {
        ...prev,
        blocks: {
          ...prev.blocks,
          [field]: { ...prev.blocks[field], text: value },
        },
      };
      return { ...next, variables: syncTemplateVariables(next) };
    });
  };

  const updateTemplateButton = (index, patch) => {
    setTemplateForm((prev) => {
      const buttons = [...(prev.blocks.buttons || [])];
      buttons[index] = { ...buttons[index], ...patch };
      return {
        ...prev,
        blocks: {
          ...prev.blocks,
          buttons,
        },
      };
    });
  };

  const addTemplateButton = () => {
    setTemplateForm((prev) => ({
      ...prev,
      blocks: {
        ...prev.blocks,
        buttons: [...(prev.blocks.buttons || []), { type: "QUICK_REPLY", text: "" }],
      },
    }));
  };

  const removeTemplateButton = (index) => {
    setTemplateForm((prev) => ({
      ...prev,
      blocks: {
        ...prev.blocks,
        buttons: (prev.blocks.buttons || []).filter((_, itemIndex) => itemIndex !== index),
      },
    }));
  };

  const addVariableAtCursor = () => {
    const field = activeTextField === "header" && templateForm.blocks.header.kind === "text" ? "header" : "body";
    const ref = field === "header" ? headerRef : bodyRef;
    const node = ref.current;
    const text = field === "header" ? templateForm.blocks.header.text || "" : templateForm.blocks.body.text || "";
    const nextPosition = Math.max(0, ...templateForm.variables.map((item) => Number(item.position) || 0)) + 1;
    const placeholder = `{{${nextPosition}}}`;
    const start = node?.selectionStart ?? text.length;
    const end = node?.selectionEnd ?? start;
    const nextText = `${text.slice(0, start)}${placeholder}${text.slice(end)}`;
    setTemplateForm((prev) => {
      const next = {
        ...prev,
        blocks: {
          ...prev.blocks,
          [field]: { ...prev.blocks[field], text: nextText },
        },
        variables: [...prev.variables, { position: nextPosition, represents: "", component: field, example: "" }],
      };
      return { ...next, variables: syncTemplateVariables(next) };
    });
    requestAnimationFrame(() => {
      ref.current?.focus();
      ref.current?.setSelectionRange(start + placeholder.length, start + placeholder.length);
    });
  };

  const deleteVariable = (position) => {
    const used = [templateForm.blocks.header.text, templateForm.blocks.body.text].some((text) => new RegExp(`\\{\\{\\s*${position}\\s*\\}\\}`).test(text || ""));
    if (used && !window.confirm(`A variável {{${position}}} está sendo usada no texto. Deseja removê-la também do texto?`)) return;
    const remaining = templateForm.variables.filter((item) => Number(item.position) !== Number(position));
    const mapping = new Map();
    remaining.forEach((item, index) => mapping.set(Number(item.position), index + 1));
    setTemplateForm((prev) => ({
      ...prev,
      blocks: {
        ...prev.blocks,
        header: { ...prev.blocks.header, text: replacePlaceholders(prev.blocks.header.text, mapping) },
        body: { ...prev.blocks.body, text: replacePlaceholders(prev.blocks.body.text, mapping) },
      },
      variables: remaining.map((item, index) => ({ ...item, position: index + 1 })),
    }));
  };

  const previewImport = async () => {
    if (!empresaId || !importFile) return;
    const form = new FormData();
    form.append("file", importFile);
    const res = await api.post(`/empresas/${empresaId}/campaign-central/audiences/imports/preview`, form);
    setImportPreview(res.data);
    setImportPayload((prev) => ({ ...prev, mapping: res.data?.mapping || {} }));
  };

  const confirmImport = async () => {
    if (!empresaId || !importFile) return;
    const form = new FormData();
    form.append("file", importFile);
    form.append("payload_json", JSON.stringify(importPayload));
    const res = await api.post(`/empresas/${empresaId}/campaign-central/audiences/imports`, form);
    setMessage(`Importação concluída: ${res.data.inserted_count} novos, ${res.data.updated_count} atualizados. Nenhum envio foi executado.`);
    setImportFile(null);
    setImportPreview(null);
    await loadData();
  };

  const deleteImport = async (batch) => {
    if (!empresaId || !batch?.id || deletingImportId) return;
    if (!window.confirm("Excluir esta lista importada?\n\nEsta ação remove a lista importada do painel. Os contatos do CRM e as conversas não serão excluídos.")) return;
    setDeletingImportId(batch.id);
    setMessage("");
    try {
      await api.delete(`/empresas/${empresaId}/campaign-central/audiences/imports/${batch.id}`);
      setImportBatches((current) => current.filter((item) => item.id !== batch.id));
      setMessage("Lista excluída.");
    } catch (error) {
      setMessage(error?.response?.data?.detail || "Não foi possível excluir a lista importada.");
    } finally {
      setDeletingImportId("");
    }
  };

  const previewSavedAudience = async (memberView = null, memberCursor = null) => {
    if (!empresaId) return;
    const res = await api.post(`/empresas/${empresaId}/campaign-central/audiences/saved/preview`, {
      ...savedAudienceForm,
      member_view: memberView,
      member_cursor: memberCursor,
      member_limit: 5,
    });
    setSavedAudiencePreview(res.data);
  };

  const saveAudience = async () => {
    if (!empresaId) return;
    const res = await api.post(`/empresas/${empresaId}/campaign-central/audiences/saved`, savedAudienceForm);
    setSavedAudiencePreview(res.data.preview);
    setMessage("Público salvo como lista dinâmica. Nenhum snapshot foi criado.");
    await loadData();
  };

  const saveFunnel = async () => {
    if (!empresaId || !funnelForm.name.trim()) return;
    await api.post(`/empresas/${empresaId}/campaign-central/audiences/funnels`, funnelForm);
    setFunnelForm({ name: "", description: "" });
    setMessage("Funil salvo para segmentação.");
    await loadData();
  };

  const saveFunnelStage = async () => {
    if (!empresaId || !stageForm.funnel_id || !stageForm.name.trim()) return;
    await api.post(`/empresas/${empresaId}/campaign-central/audiences/funnels/${stageForm.funnel_id}/stages`, {
      name: stageForm.name,
      position: Number(stageForm.position || 10),
      rules: stageForm.rules,
      active: true,
    });
    setStageForm({
      funnel_id: stageForm.funnel_id,
      name: "",
      position: Number(stageForm.position || 10) + 10,
      rules: { include: { match: "any", rules: [] }, exclude: { match: "any", rules: [] } },
    });
    setMessage("Etapa salva sem alterar leads.");
    await loadData();
  };

  const moveFunnelStage = async (funnel, stage, direction) => {
    const stages = [...(funnel.stages || [])].sort((a, b) => Number(a.position || 0) - Number(b.position || 0));
    const index = stages.findIndex((item) => item.id === stage.id);
    const swapIndex = index + direction;
    if (index < 0 || swapIndex < 0 || swapIndex >= stages.length) return;
    const payload = stages.map((item) => ({ id: item.id, position: Number(item.position || 0) }));
    const current = payload[index].position;
    payload[index].position = payload[swapIndex].position;
    payload[swapIndex].position = current;
    await api.post(`/empresas/${empresaId}/campaign-central/audiences/funnels/${funnel.id}/stages/reorder`, { stages: payload });
    await loadData();
  };

  const toggleFunnelStage = async (funnelId, stageId, active) => {
    await api.post(`/empresas/${empresaId}/campaign-central/audiences/funnels/${funnelId}/stages/${stageId}/active`, { active });
    await loadData();
  };

  const previewSmartAudience = async (suggestion, memberView = null, memberCursor = null) => {
    if (!empresaId) return;
    const res = await api.post(`/empresas/${empresaId}/campaign-central/audiences/smart/preview`, {
      funnel_id: suggestion.funnel_id,
      stage_id: suggestion.stage_id,
      type: suggestion.type,
      member_view: memberView,
      member_cursor: memberCursor,
      member_limit: 5,
    });
    setSmartAudiencePreview(res.data);
  };

  const saveSmartAudience = async (suggestion) => {
    if (!empresaId) return;
    await api.post(`/empresas/${empresaId}/campaign-central/audiences/smart/save`, {
      funnel_id: suggestion.funnel_id,
      stage_id: suggestion.stage_id,
      type: suggestion.type,
      name: suggestion.name,
      description: suggestion.description,
    });
    setMessage("Sugestão salva como público dinâmico.");
    await loadData();
  };

  const campaignVariableBindingsPayload = () => {
    const current = campaignForm.variable_bindings || {};
    return selectedTemplateVariables.reduce((acc, variable) => {
      acc[variable.key] = current[variable.key] || {
        type: variable.component === "BODY" && Number(variable.position) === 1 ? "CONTACT_NAME" : "FIXED_VALUE",
        value: campaignForm.variable_values?.[variable.key] || "",
      };
      return acc;
    }, {});
  };

  const prepareCampaign = async () => {
    if (!empresaId) return;
    setSaving(true);
    try {
      const payload = {
        ...campaignForm,
        audience: {
          include_tags: campaignForm.include_tags,
          saved_audience_id: campaignForm.saved_audience_id,
        },
        variable_values: campaignForm.variable_values,
        variable_bindings: campaignVariableBindingsPayload(),
      };
      if (!payload.include_tags?.length && !payload.saved_audience_id) {
        setCampaignPreparation(null);
        setMessage("Selecione um público antes de preparar a campanha.");
        setSaving(false);
        return;
      }
      let res;
      if (selectedTemplateRequiresImage) {
        const imageError = validateLocalImageFile(campaignImageFile);
        if (imageError) {
          setMessage(imageError);
          setSaving(false);
          return;
        }
        const form = new FormData();
        form.append("payload_json", JSON.stringify(payload));
        form.append("image", campaignImageFile);
        res = await api.post(`/empresas/${empresaId}/campaign-central/campaigns/prepare-with-media`, form, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      } else {
        res = await api.post(`/empresas/${empresaId}/campaign-central/campaigns/prepare`, payload);
      }
      setCampaignPreparation(res.data);
      setMessage(`${isProductionCampaignMode ? "Campanha preparada" : "Campanha preparada em modo de teste"}. Nenhum envio externo foi executado.`);
    } catch (error) {
      setCampaignPreparation(error?.response?.data?.detail || null);
      setMessage("A campanha não foi preparada. Revise conexão, template aprovado e público.");
    } finally {
      setSaving(false);
    }
  };

  if (!empresaId) return <div className="p-6 text-sm text-slate-600">Empresa não identificada.</div>;
  if (loading) return <LoadingState label="Carregando Central de Campanhas..." />;

  const campaignCounts = overview?.campaigns || {};
  const recipientCounts = overview?.recipients || {};
  const templateCounts = overview?.templates || {};
  const resultMetrics = analyticsResults?.metrics || {};
  const resultRates = analyticsResults?.rates || {};
  const selectedResultsCampaign = campaigns.find((campaign) => String(campaign.id) === String(resultsFilters.campaign_id));
  const resultsScopeLabel = analyticsResults?.scope?.label || (selectedResultsCampaign?.name ? selectedResultsCampaign.name : "Todas as campanhas");
  const selectedSavedAudience = savedAudiences.find((audience) => String(audience.id) === String(campaignForm.saved_audience_id));
  const campaignAudienceSelected = Boolean(campaignForm.include_tags.length || campaignForm.saved_audience_id);
  const variableStats = templateVariableStats(templateForm);
  const doubleAsteriskWarning = hasDoubleAsterisk(`${templateForm.blocks.header.text || ""}\n${templateForm.blocks.body.text || ""}\n${templateForm.blocks.footer.text || ""}`);

  return (
    <div className="fm-automation fm-campaigns space-y-6">
      <PageHeader
        title="Central de Campanhas"
        description="Campanhas, templates, públicos, custos e conversões em uma operação única. Follow-ups continuam em área própria."
        icon={Megaphone}
        eyebrow="Operação"
        actions={
          <>
            <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={loadData}>Atualizar</ActionButton>
            <ActionButton type="button" icon={Plus} onClick={() => navigate("/painel/campanhas/nova")}>Nova campanha</ActionButton>
            <ActionButton type="button" variant="secondary" icon={FileText} onClick={() => navigate("/painel/campanhas/templates/novo")}>Criar template</ActionButton>
          </>
        }
      />

      <nav className="fm-automation-tabs" aria-label="Áreas da Central de Campanhas">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const active = activeTab === tab.id;
          return (
            <button key={tab.id} type="button" onClick={() => navigate(tab.path)} aria-current={active ? "page" : undefined} className={`inline-flex shrink-0 items-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold ${active ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"}`}>
              <Icon size={15} /> {tab.label}
            </button>
          );
        })}
      </nav>

      {message ? <InfoBanner tone={message.includes("não") || message.includes("Revise") ? "warning" : "success"}>{message}</InfoBanner> : null}

      {activeTab === "overview" ? (
        <div className="space-y-5">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Preparadas" value={countValue(campaignCounts, ["draft", "prepared", "pendente"])} helper="Snapshots prontos" icon={FileText} />
            <MetricCard label="Na fila" value={countValue(campaignCounts, ["queued"])} helper="Aguardando worker" icon={Clock3} />
            <MetricCard label="Em envio" value={countValue(campaignCounts, ["running", "executando"])} helper="Worker ativo" icon={Megaphone} tone="warning" />
            <MetricCard label="Pausadas/falhas" value={countValue(campaignCounts, ["paused", "failed", "erro", "falha"])} helper="Requer ação humana" icon={AlertTriangle} tone="danger" />
            <MetricCard label="Templates pendentes" value={countValue(templateCounts, ["pending", "submitted"])} icon={FileText} />
            <MetricCard label="Templates aprovados" value={countValue(templateCounts, ["approved"])} icon={CheckCircle2} tone="active" />
            <MetricCard label="Destinatários preparados" value={Object.values(recipientCounts).reduce((a, b) => a + Number(b || 0), 0)} icon={Users} />
            <MetricCard label="Custo confirmado" value={formatMoney(overview?.costs?.confirmed)} helper="Sem valor quando não há webhook/tarifa" icon={DollarSign} />
          </div>
          {campaigns.length || templates.length ? null : (
            <EmptyState title="Nenhuma operação preparada" description="Crie um template local ou prepare uma campanha em safe mode para começar." icon={FolderOpen} />
          )}
          <section className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-slate-950">Campanhas recentes</h2>
              <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={loadData}>Atualizar</ActionButton>
            </div>
            <div className="fm-table-scroll mt-4 overflow-x-auto" role="region" aria-label="Campanhas recentes" tabIndex={0}>
              {campaigns.length ? (
                <table className="fm-table min-w-full divide-y divide-slate-200 text-sm">
                  <thead>
                    <tr className="text-left text-xs font-semibold uppercase text-slate-500">
                      <th className="px-3 py-2">Campanha</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Resultados</th>
                      <th className="px-3 py-2">Ações</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {campaigns.map((campaign) => {
                      const status = String(campaign.status || "draft");
                      const results = campaign.results || {};
                      const busy = campaignActionId.startsWith(`${campaign.id}:`);
                      return (
                        <tr key={campaign.id}>
                          <td className="px-3 py-3">
                            <p className="font-semibold text-slate-900">{campaign.nome || campaign.id}</p>
                            <p className="text-xs text-slate-500">{campaign.created_at ? formatTemplateDate(campaign.created_at) : "-"}</p>
                          </td>
                          <td className="px-3 py-3">
                            <StatusBadge tone={campaignStatusTone(status)}>{campaignStatusLabels[status] || status}</StatusBadge>
                          </td>
                          <td className="px-3 py-3 text-xs text-slate-600">
                            <span>Total {results.total || 0}</span>
                            <span className="ml-2">Fila {results.queued || 0}</span>
                            <span className="ml-2">Enviados {results.sent || 0}</span>
                            <span className="ml-2">Falhas {Number(results.failed || 0) + Number(results.blocked || 0)}</span>
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex flex-wrap gap-2">
                              {["queued", "running"].includes(status) ? (
                                <ActionButton type="button" variant="secondary" icon={PauseCircle} onClick={() => campaignAction(campaign.id, "pause")} disabled={busy}>Pausar</ActionButton>
                              ) : null}
                              {status === "paused" ? (
                                <ActionButton type="button" variant="secondary" icon={PlayCircle} onClick={() => campaignAction(campaign.id, "resume")} disabled={busy}>Retomar</ActionButton>
                              ) : null}
                              {!["completed", "cancelled", "failed"].includes(status) ? (
                                <ActionButton type="button" variant="secondary" icon={X} onClick={() => campaignAction(campaign.id, "cancel")} disabled={busy}>Cancelar</ActionButton>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <EmptyState title="Nenhuma campanha iniciada" description="Snapshots preparados aparecem após iniciar a campanha." icon={Megaphone} />
              )}
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "templates" ? (
        <div className="space-y-5">
          <section className="fm-automation-panel fm-template-editor space-y-4 rounded-xl border border-slate-200 bg-white p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">Editor visual de template</h2>
                <p className="mt-1 text-sm text-slate-500">Crie templates na WABA conectada da empresa, acompanhe a análise da Meta e use quando estiverem aprovados.</p>
              </div>
              <StatusBadge tone="active">Meta Official</StatusBadge>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <label className="fm-automation-field"><span>Conexão WhatsApp oficial</span><select value={templateForm.conexao_id} onChange={(e) => setTemplateForm({ ...templateForm, conexao_id: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                <option value="">Conexão WhatsApp oficial</option>
                {officialConnections.map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.nome_instancia || connection.display_number || connection.phone_number_id || "WhatsApp Meta"}
                  </option>
                ))}
              </select></label>
              <label className="fm-automation-field"><span>Nome interno</span><input value={templateForm.internal_name} onChange={(e) => setTemplateForm({ ...templateForm, internal_name: e.target.value })} placeholder="Nome interno" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
              <label className="fm-automation-field"><span>Nome técnico</span><input value={templateForm.technical_name} onChange={(e) => setTemplateForm({ ...templateForm, technical_name: e.target.value.toLowerCase() })} placeholder="nome_tecnico" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
              <label className="fm-automation-field"><span>Idioma</span><input value={templateForm.language} onChange={(e) => setTemplateForm({ ...templateForm, language: e.target.value })} placeholder="pt_BR" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
              <label className="fm-automation-field"><span>Categoria</span><select value={templateForm.category} onChange={(e) => setTemplateForm({ ...templateForm, category: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                <option value="MARKETING">Marketing</option>
                <option value="UTILITY">Utilidade</option>
                <option value="AUTHENTICATION">Autenticação</option>
              </select></label>
              <label className="fm-automation-field"><span>Tipo de cabeçalho</span><select value={templateForm.blocks.header.kind} onChange={(e) => setTemplateForm({ ...templateForm, blocks: { ...templateForm.blocks, header: { kind: e.target.value, text: "" } } })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                <option value="none">Sem cabeçalho</option>
                <option value="text">Cabeçalho em texto</option>
                <option value="image">Imagem</option>
              </select></label>
              <label className="fm-automation-field"><span>Descrição interna</span><input value={templateForm.description} onChange={(e) => setTemplateForm({ ...templateForm, description: e.target.value })} placeholder="Descrição interna" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
            </div>

            {templateForm.blocks.header.kind === "text" ? (
              <label className="fm-automation-field"><span>Texto do cabeçalho</span><input ref={headerRef} value={templateForm.blocks.header.text} onFocus={() => setActiveTextField("header")} onChange={(e) => updateTemplateText("header", e.target.value)} placeholder="Texto do cabeçalho" className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
            ) : null}

            {templateForm.blocks.header.kind === "image" ? (
              <div className="rounded-lg border border-slate-200 p-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">Imagem de exemplo</h3>
                    <p className="mt-1 text-sm text-slate-500">JPEG ou PNG, até 5 MB. A imagem é usada somente na análise do template pela Meta.</p>
                  </div>
                  <label className="inline-flex cursor-pointer items-center justify-center rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-semibold text-slate-700 shadow-sm hover:bg-slate-50">
                    Selecionar imagem
                    <input type="file" accept={imageFileAccept} onChange={selectTemplateHeaderImage} className="sr-only" />
                  </label>
                </div>
                {templateHeaderImageFile ? (
                  <div className="mt-4 flex flex-col gap-3 md:flex-row md:items-center">
                    {templateHeaderImagePreview ? <img src={templateHeaderImagePreview} alt="Preview da imagem de exemplo" className="h-28 w-40 rounded-lg border border-slate-200 object-cover" /> : null}
                    <div className="min-w-0 text-sm text-slate-600">
                      <p className="break-words font-medium text-slate-900">{templateHeaderImageFile.name}</p>
                      <p>{Math.round(templateHeaderImageFile.size / 1024)} KB</p>
                      <button type="button" onClick={clearTemplateHeaderImage} className="mt-2 text-sm font-semibold text-red-700 hover:text-red-800">Remover imagem</button>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            <label className="fm-automation-field"><span>Mensagem</span><textarea ref={bodyRef} value={templateForm.blocks.body.text} onFocus={() => setActiveTextField("body")} onChange={(e) => updateTemplateText("body", e.target.value)} rows={5} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
            <label className="fm-automation-field"><span>Rodapé opcional</span><input value={templateForm.blocks.footer.text} onChange={(e) => setTemplateForm({ ...templateForm, blocks: { ...templateForm.blocks, footer: { text: e.target.value } } })} placeholder="Rodapé opcional" className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>

            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900">Variáveis</h3>
                <ActionButton type="button" variant="secondary" icon={Plus} onClick={addVariableAtCursor}>Adicionar variável</ActionButton>
              </div>
              {templateForm.variables.map((variable, index) => (
                    <div key={templateVariableIdentity(variable.component, variable.position)} className="grid gap-2 rounded-lg border border-slate-200 p-3 md:grid-cols-[90px_1fr_1fr_auto]">
                  <span className="flex flex-col justify-center rounded border border-slate-200 px-2 text-sm font-semibold text-slate-700">
                    {"{{"}{variable.position}{"}}"}
                    <small className="font-normal uppercase text-slate-400">{variable.component || "body"}</small>
                  </span>
                  <label className="fm-automation-field"><span>Representa</span><input value={variable.represents || ""} onChange={(e) => {
                    const variables = [...templateForm.variables];
                    variables[index] = { ...variable, represents: e.target.value };
                    setTemplateForm({ ...templateForm, variables });
                  }} placeholder="Representa" maxLength={120} className="rounded border border-slate-300 px-2 py-2 text-sm" /></label>
                  <label className="fm-automation-field"><span>Exemplo para Meta</span><input value={variable.example || ""} onChange={(e) => {
                    const variables = [...templateForm.variables];
                    variables[index] = { ...variable, example: e.target.value };
                    setTemplateForm({ ...templateForm, variables });
                  }} placeholder="Exemplo para Meta" maxLength={160} className="rounded border border-slate-300 px-2 py-2 text-sm" /></label>
                  <button type="button" onClick={() => deleteVariable(variable.position)} aria-label={`Excluir variável ${variable.position}`} title="Excluir variável" className="inline-flex items-center justify-center rounded border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-red-500">
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
              {variableStats.total ? (
                <InfoBanner tone={variableStats.complete === variableStats.total ? "success" : "warning"}>
                  {variableStats.complete === variableStats.total
                    ? `${variableStats.total} variáveis completas.`
                    : `${variableStats.complete}/${variableStats.total} variáveis completas. Faltam ${variableStats.missingRepresents} descrição(ões) e ${variableStats.missingExample} exemplo(s).`}
                </InfoBanner>
              ) : (
                <InfoBanner>Nenhuma variável detectada no texto.</InfoBanner>
              )}
              {doubleAsteriskWarning ? (
                <InfoBanner tone="warning">Para negrito no WhatsApp, prefira *texto* em vez de **texto**.</InfoBanner>
              ) : null}
            </div>

            <div className="space-y-3 rounded-lg border border-slate-200 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">Botões</h3>
                  <p className="mt-1 text-xs text-slate-500">Suporte a resposta rápida, link e telefone conforme contrato Meta Official.</p>
                </div>
                <ActionButton type="button" variant="secondary" icon={Plus} onClick={addTemplateButton}>Adicionar botão</ActionButton>
              </div>
              {(templateForm.blocks.buttons || []).map((button, index) => (
                <div key={`button-${index}`} className="grid gap-2 rounded-lg border border-slate-200 p-3 md:grid-cols-[160px_1fr_1fr_auto]">
                  <label className="fm-automation-field"><span>Tipo do botão</span><select value={button.type || "QUICK_REPLY"} onChange={(event) => updateTemplateButton(index, { type: event.target.value, url: "", phone_number: "", example: "" })} className="rounded border border-slate-300 px-2 py-2 text-sm">
                    {templateButtonTypes.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select></label>
                  <label className="fm-automation-field"><span>Texto do botão</span><input value={button.text || ""} onChange={(event) => updateTemplateButton(index, { text: event.target.value })} placeholder="Texto do botão" maxLength={25} className="rounded border border-slate-300 px-2 py-2 text-sm" /></label>
                  {button.type === "URL" ? (
                    <div className="grid gap-2">
                      <label className="fm-automation-field"><span>URL do botão</span><input value={button.url || ""} onChange={(event) => updateTemplateButton(index, { url: event.target.value })} placeholder="https://..." className="rounded border border-slate-300 px-2 py-2 text-sm" /></label>
                      {extractNumericPlaceholders(button.url).length ? (
                        <label className="fm-automation-field"><span>Exemplo da parte dinâmica</span><input value={button.example || ""} onChange={(event) => updateTemplateButton(index, { example: event.target.value })} placeholder="Exemplo da parte dinâmica" className="rounded border border-slate-300 px-2 py-2 text-sm" /></label>
                      ) : null}
                    </div>
                  ) : button.type === "PHONE_NUMBER" ? (
                    <label className="fm-automation-field"><span>Telefone do botão</span><input value={button.phone_number || ""} onChange={(event) => updateTemplateButton(index, { phone_number: event.target.value })} placeholder="+5551999999999" className="rounded border border-slate-300 px-2 py-2 text-sm" /></label>
                  ) : (
                    <span className="flex items-center text-xs text-slate-500">Sem valor adicional.</span>
                  )}
                  <button type="button" onClick={() => removeTemplateButton(index)} aria-label={`Remover botão ${index + 1}`} title="Remover botão" className="inline-flex items-center justify-center rounded border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-red-500">
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
              {!templateForm.blocks.buttons?.length ? <p className="text-sm text-slate-500">Nenhum botão configurado.</p> : null}
            </div>

            <PreviewMessage form={templateForm} />
            {templateValidation?.errors?.length ? (
              <InfoBanner tone="warning">{templateValidation.errors.map((item) => item.message).join(" ")}</InfoBanner>
            ) : templateValidation?.valid ? <InfoBanner tone="success">Template válido para rascunho local.</InfoBanner> : null}
            <div className="flex flex-wrap gap-2">
              <ActionButton type="button" variant="secondary" icon={Search} onClick={validateTemplate}>Validar</ActionButton>
              <ActionButton type="button" icon={Save} onClick={createDraft} disabled={saving}>Salvar rascunho local</ActionButton>
              <ActionButton type="button" variant="secondary" icon={Copy} onClick={() => setTemplateForm({ ...templateForm, technical_name: `${templateForm.technical_name}_copia` })}>Duplicar local</ActionButton>
              <ActionButton type="button" variant="secondary" icon={Send} onClick={submitTemplateToMeta} disabled={saving || !templateForm.conexao_id}>Enviar para análise da Meta</ActionButton>
            </div>
          </section>

          <section className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-5">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">Templates da conta</h2>
                <p className="mt-1 text-sm text-slate-500">Visualize os templates sincronizados da conexão selecionada. Templates existentes são somente leitura.</p>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <label className="relative block">
                  <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                  <input
                    aria-label="Busca por nome do template"
                    value={templateSearch}
                    onChange={(event) => setTemplateSearch(event.target.value)}
                    placeholder="Busca por nome"
                    className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm sm:w-56"
                  />
                </label>
                <label className="fm-automation-field"><span>Status do template</span><select value={templateStatusFilter} onChange={(event) => setTemplateStatusFilter(event.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                  {templateStatusOptions.map((option) => <option key={option.value || "all"} value={option.value}>{option.label}</option>)}
                </select></label>
                <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={syncTemplates} disabled={saving || !templateForm.conexao_id}>Sincronizar com a Meta</ActionButton>
              </div>
            </div>

            <div className="mt-4 overflow-hidden rounded-lg border border-slate-200">
              {visibleTemplates.length ? (
                <div className="fm-table-scroll overflow-x-auto" role="region" aria-label="Templates da conta" tabIndex={0}>
                  <table className="fm-table min-w-full divide-y divide-slate-200 text-sm">
                    <thead className="bg-slate-50 text-left text-xs font-semibold uppercase text-slate-500">
                      <tr>
                        <th className="px-4 py-3">Nome</th>
                        <th className="px-4 py-3">Categoria</th>
                        <th className="px-4 py-3">Idioma</th>
                        <th className="px-4 py-3">Status</th>
                        <th className="px-4 py-3">Última sync</th>
                        <th className="px-4 py-3 text-right">Ação</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {visibleTemplates.map((template) => (
                        <tr key={template.id} className="hover:bg-slate-50">
                          <td className="max-w-xs px-4 py-3">
                            <p className="break-words font-medium text-slate-900">{template.name}</p>
                            {template.remote_template_id ? <p className="mt-1 break-all text-xs text-slate-500">Meta ID: {template.remote_template_id}</p> : null}
                          </td>
                          <td className="px-4 py-3 text-slate-600">{categoryLabels[template.normalized_category] || template.raw_category || "Desconhecida"}</td>
                          <td className="px-4 py-3 text-slate-600">{template.language || "-"}</td>
                          <td className="px-4 py-3"><StatusBadge tone={templateBadgeTone(template.normalized_status)}>{statusLabels[template.normalized_status] || template.raw_status || "Desconhecido"}</StatusBadge></td>
                          <td className="px-4 py-3 text-slate-600">{formatTemplateDate(template.last_synced_at)}</td>
                          <td className="px-4 py-3 text-right">
                            <ActionButton type="button" variant="secondary" icon={Eye} onClick={() => setSelectedTemplateDetails(template)}>Visualizar</ActionButton>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="p-8 text-center text-sm text-slate-500">
                  Nenhum template encontrado para os filtros atuais.
                </div>
              )}
            </div>
          </section>

          <TemplateReadOnlyDetails template={selectedTemplateDetails} onClose={() => setSelectedTemplateDetails(null)} />
        </div>
      ) : null}

      {activeTab === "campaigns" ? (
        <section className="fm-campaign-builder grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="fm-automation-panel space-y-5 rounded-xl border border-slate-200 bg-white p-5">
            <h2 className="text-lg font-semibold text-slate-950">Construtor de campanha</h2>
            <div className="grid gap-4 md:grid-cols-2">
              <label className="fm-automation-field"><span>Nome da campanha</span><input value={campaignForm.name} onChange={(e) => setCampaignForm({ ...campaignForm, name: e.target.value })} placeholder="Nome da campanha" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
              <label className="fm-automation-field"><span>Objetivo</span><input value={campaignForm.objective} onChange={(e) => setCampaignForm({ ...campaignForm, objective: e.target.value })} placeholder="Objetivo" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
              <label className="fm-automation-field"><span>Conexão oficial</span><select value={campaignForm.conexao_id} onChange={(e) => setCampaignForm({ ...campaignForm, conexao_id: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                <option value="">Conexão oficial</option>
                {officialConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.nome_instancia || "WhatsApp Oficial"}</option>)}
              </select></label>
              <label className="fm-automation-field"><span>Fuso horário</span><input value={campaignForm.timezone} onChange={(e) => setCampaignForm({ ...campaignForm, timezone: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
            </div>
            <label className="fm-automation-field"><span>Descrição interna</span><textarea value={campaignForm.description} onChange={(e) => setCampaignForm({ ...campaignForm, description: e.target.value })} rows={3} placeholder="Descrição interna" className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
            <label className="flex items-start gap-3 rounded-lg border border-slate-200 p-4">
              <input
                type="checkbox"
                checked={campaignForm.ai_reply_enabled !== false}
                onChange={(event) => setCampaignForm({ ...campaignForm, ai_reply_enabled: event.target.checked })}
                className="mt-1 h-4 w-4 rounded border-slate-300"
              />
              <span>
                <span className="block text-sm font-semibold text-slate-900">Atendimento automático da IA</span>
                <span className="mt-1 block text-xs text-slate-500">Permite que a IA responda quando alguém responder a esta campanha.</span>
              </span>
            </label>
            <div className="rounded-lg border border-slate-200 p-4">
              <h3 className="text-sm font-semibold text-slate-900">Público</h3>
              <div className="mt-3 flex flex-wrap gap-2">
                {tags.map((tag) => {
                  const selected = campaignForm.include_tags.includes(tag.id);
                  return (
                    <button aria-pressed={selected} key={tag.id} type="button" onClick={() => setCampaignForm({ ...campaignForm, include_tags: selected ? campaignForm.include_tags.filter((item) => item !== tag.id) : [...campaignForm.include_tags, tag.id] })} className={`rounded-full border px-3 py-1 text-xs font-semibold ${selected ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700"}`}>
                      {tag.nome}
                    </button>
                  );
                })}
                {!tags.length ? <p className="text-sm text-slate-500">Sem tags disponíveis para seleção.</p> : null}
              </div>
              <label className="fm-automation-field"><span>Público salvo</span><select value={campaignForm.saved_audience_id} onChange={(e) => setCampaignForm({ ...campaignForm, saved_audience_id: e.target.value })} className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
                <option value="">Ou selecione um público salvo</option>
                {savedAudiences.map((audience) => <option key={audience.id} value={audience.id}>{audience.name}</option>)}
              </select></label>
            </div>
            <div className="rounded-lg border border-slate-200 p-4">
              <h3 className="mb-3 text-sm font-semibold text-slate-900">Template aprovado</h3>
              <MetaTemplateSelector templates={approvedTemplates} value={campaignForm.template_id} onChange={(value) => setCampaignForm({ ...campaignForm, template_id: value, variable_values: {}, variable_bindings: {} })} disabled={!approvedTemplates.length} />
              {!approvedTemplates.length ? <p className="mt-2 text-sm text-slate-500">Campanhas oficiais em massa só usam templates aprovados da empresa/conexão.</p> : null}
              {selectedTemplateVariables.length ? (
                <div className="mt-3 space-y-2">
                  <h4 className="text-sm font-semibold text-slate-900">Variáveis do template</h4>
                  {selectedTemplateVariables.map((variable) => {
                    const binding = campaignForm.variable_bindings?.[variable.key] || { type: variable.component === "BODY" && variable.position === 1 ? "CONTACT_NAME" : "FIXED_VALUE", value: "" };
                    return (
                    <div key={variable.key} className="grid gap-2 rounded-lg border border-slate-200 p-3 text-sm md:grid-cols-[180px_180px_1fr]">
                      <span className="font-semibold text-slate-700">{variable.label}</span>
                      <label className="fm-automation-field"><span>Origem do valor</span><select
                        value={binding.type || "FIXED_VALUE"}
                        onChange={(event) => setCampaignForm({
                          ...campaignForm,
                          variable_bindings: {
                            ...(campaignForm.variable_bindings || {}),
                            [variable.key]: { ...binding, type: event.target.value },
                          },
                        })}
                        className="rounded border border-slate-300 px-2 py-1"
                      >
                        {campaignVariableBindingTypes.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select></label>
                      {binding.type === "CONTACT_NAME" ? (
                        <span className="flex items-center text-xs text-slate-500">Resolvido pelo lead/contato no start.</span>
                      ) : (
                        <label className="fm-automation-field"><span>Valor fixo para esta campanha</span><input
                          value={binding.value || ""}
                          onChange={(event) => setCampaignForm({
                            ...campaignForm,
                            variable_bindings: {
                              ...(campaignForm.variable_bindings || {}),
                              [variable.key]: { ...binding, type: "FIXED_VALUE", value: event.target.value },
                            },
                            variable_values: {
                              ...(campaignForm.variable_values || {}),
                              [variable.key]: event.target.value,
                            },
                          })}
                          placeholder="Valor fixo para esta campanha"
                          className="rounded border border-slate-300 px-2 py-1"
                        /></label>
                      )}
                    </div>
                    );
                  })}
                </div>
              ) : null}
              {selectedTemplateRequiresImage ? (
                <div className="mt-4 rounded-lg border border-slate-200 p-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div>
                      <h4 className="text-sm font-semibold text-slate-900">Imagem da campanha</h4>
                      <p className="mt-1 text-sm text-slate-500">Obrigatória para este template. JPEG ou PNG, até 5 MB. Será enviada uma vez para obter o MEDIA_ID da campanha.</p>
                    </div>
                    <label className="inline-flex cursor-pointer items-center justify-center rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-semibold text-slate-700 shadow-sm hover:bg-slate-50">
                      Selecionar imagem
                      <input type="file" accept={imageFileAccept} onChange={selectCampaignImage} className="sr-only" />
                    </label>
                  </div>
                  {campaignImageFile ? (
                    <div className="mt-4 flex flex-col gap-3 md:flex-row md:items-center">
                      {campaignImagePreview ? <img src={campaignImagePreview} alt="Preview da imagem da campanha" className="h-28 w-40 rounded-lg border border-slate-200 object-cover" /> : null}
                      <div className="min-w-0 text-sm text-slate-600">
                        <p className="break-words font-medium text-slate-900">{campaignImageFile.name}</p>
                        <p>{Math.round(campaignImageFile.size / 1024)} KB</p>
                        <button type="button" onClick={clearCampaignImage} className="mt-2 text-sm font-semibold text-red-700 hover:text-red-800">Remover imagem</button>
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div className="space-y-3">
              <InfoBanner>
                {isProductionCampaignMode
                  ? "Produção habilitada. A campanha usará o público selecionado e seguirá as validações de opt-out, telefone, template aprovado e fila de envio."
                  : "Modo de teste ativo. Apenas destinatários autorizados na allowlist podem receber esta campanha."}
              </InfoBanner>
              <p className="text-sm text-slate-500">Campanhas oficiais em massa usam templates aprovados pela Meta; preparar cria apenas um snapshot e não inicia o envio.</p>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <span>Modo de envio:</span>
                <StatusBadge tone={isProductionCampaignMode ? "active" : "warning"}>{isProductionCampaignMode ? "Produção" : "Teste"}</StatusBadge>
              </div>
              <ActionButton type="button" icon={Save} onClick={prepareCampaign} disabled={saving || !campaignAudienceSelected}>{isProductionCampaignMode ? "Preparar campanha" : "Preparar em modo de teste"}</ActionButton>
            </div>
          </div>
          <aside className="space-y-4">
            <div className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-slate-900">Revisão</h3>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between gap-3"><dt className="text-slate-500">Público</dt><dd>{campaignForm.include_tags.length ? `${campaignForm.include_tags.length} filtro(s)` : (selectedSavedAudience?.name || "Não definido")}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-slate-500">Template</dt><dd>{campaignForm.template_id ? "Selecionado" : "Pendente"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-slate-500">Agendamento</dt><dd>Não ativado</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-slate-500">Atendimento automático</dt><dd>{campaignForm.ai_reply_enabled !== false ? "Ativado" : "Desativado"}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-slate-500">Custo</dt><dd>Estimado por política</dd></div>
              </dl>
            </div>
            {campaignPreparation ? (
              <div className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4">
                <h3 className="text-sm font-semibold text-slate-900">Snapshot</h3>
                <p className="mt-2 text-sm text-slate-600">Selecionados: {campaignPreparation.snapshot?.selected ?? 0}</p>
                <p className="text-sm font-semibold text-slate-900">Envio autorizado para: {campaignPreparation.snapshot?.selected ?? 0} contatos</p>
                <p className="text-sm text-slate-600">Excluídos: {campaignPreparation.snapshot?.excluded ?? 0}</p>
                {campaignPreparation.sender ? <p className="text-sm text-slate-600">Remetente: {campaignPreparation.sender.display_phone || "WhatsApp oficial"}</p> : null}
                {campaignPreparation.template ? <p className="text-sm text-slate-600">Template: {campaignPreparation.template.name} ({campaignPreparation.template.language})</p> : null}
                {campaignPreparation.payload_preview?.media?.media_id ? <p className="text-sm text-slate-600">Imagem: MEDIA_ID preparado para a campanha</p> : null}
                {campaignPreparation.payload_preview?.media?.filename ? <p className="text-sm text-slate-600">Arquivo: {campaignPreparation.payload_preview.media.filename}</p> : null}
                <p className="mt-2 text-xs text-slate-500">Preflight: {isProductionCampaignMode ? "produção" : "teste"}</p>
                <p className="text-xs text-slate-500">Payload: {campaignPreparation.payload_preview ? "pronto sem envio" : "pendente"}</p>
                <div className="mt-4">
                  <ActionButton
                    type="button"
                    icon={Send}
                    onClick={startPreparedCampaign}
                    disabled={!campaignPreparation.campaign_id || campaignActionId === `${campaignPreparation.campaign_id}:start`}
                  >
                    Confirmar envio para {campaignPreparation.snapshot?.selected ?? 0} contatos
                  </ActionButton>
                </div>
                <p className="mt-2 text-xs text-slate-500">O início materializa destinatários, cria outbox e aplica as políticas operacionais do backend.</p>
              </div>
            ) : null}
          </aside>
        </section>
      ) : null}

      {activeTab === "audiences" ? (
        <section className="space-y-4">
          <div className="fm-automation-tabs fm-automation-subtabs" role="group" aria-label="Tipos de público">
            {[
              { id: "tags", label: "Listas por tags", icon: <Users size={15} /> },
              { id: "imports", label: "Listas importadas", icon: <Upload size={15} /> },
              { id: "saved", label: "Públicos salvos", icon: <ListChecks size={15} /> },
              { id: "smart", label: "Públicos sugeridos", icon: <GitBranch size={15} /> },
              { id: "funnels", label: "Configuração de funil", icon: <BarChart3 size={15} /> },
              { id: "snapshots", label: "Snapshots de campanhas", icon: <FolderOpen size={15} /> },
            ].map((tab) => {
              return (
                <button key={tab.id} type="button" onClick={() => setAudienceTab(tab.id)} aria-pressed={audienceTab === tab.id} className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold ${audienceTab === tab.id ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700"}`}>
                  {tab.icon} {tab.label}
                </button>
              );
            })}
          </div>

          {audienceTab === "tags" ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {tagAudiences.map((tag) => (
                <div key={tag.id} className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-slate-950">{tag.nome}</p>
                      <p className="mt-1 text-xs text-slate-500">{tag.origem || "comportamento"}</p>
                    </div>
                    <span className="h-4 w-4 rounded-full border" style={{ backgroundColor: tag.cor }} />
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
                    <span>Contatos: <b>{tag.total_contacts}</b></span>
                    <span>WhatsApp apto: <b>{tag.whatsapp_ready}</b></span>
                    <span>Consent. conhecido: <b>{tag.consent_known}</b></span>
                    <span>Desconhecido: <b>{tag.consent_unknown}</b></span>
                  </div>
                  <p className="mt-3 text-xs text-slate-500">Última aplicação: {tag.last_applied_at || "sem evento confiável"}</p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <ActionButton type="button" variant="secondary" onClick={() => setCampaignForm({ ...campaignForm, include_tags: [tag.id] })}>Criar campanha</ActionButton>
                    <ActionButton type="button" variant="secondary" onClick={() => { setSavedAudienceForm({ ...savedAudienceForm, rules: updateAudienceTagRule(savedAudienceForm.rules, "include", tag.id, true) }); setAudienceTab("saved"); }}>Combinar</ActionButton>
                  </div>
                </div>
              ))}
              {!tagAudiences.length ? <EmptyState title="Nenhuma tag cadastrada" description="Tags do CRM aparecem automaticamente aqui como listas dinâmicas." icon={Users} /> : null}
            </div>
          ) : null}

          {audienceTab === "imports" ? (
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="text-lg font-semibold text-slate-950">Importar CSV</h2>
                <p className="mt-1 text-sm text-slate-500">Importa contatos antigos sem criar conversas, janelas ou opt-in fictício.</p>
                <label className="fm-automation-field"><span>Arquivo CSV</span><input type="file" accept=".csv,text/csv" onChange={(e) => setImportFile(e.target.files?.[0] || null)} className="mt-4 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <label className="fm-automation-field"><span>Tag existente</span><select value={importPayload.tag_id} onChange={(e) => setImportPayload({ ...importPayload, tag_id: e.target.value, tag_name: "" })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                    <option value="">Escolher tag existente</option>
                    {tags.map((tag) => <option key={tag.id} value={tag.id}>{tag.nome}</option>)}
                  </select></label>
                  <label className="fm-automation-field"><span>Nova tag</span><input value={importPayload.tag_name} onChange={(e) => setImportPayload({ ...importPayload, tag_name: e.target.value, tag_id: "" })} placeholder="Ou criar nova tag" className="rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
                  <label className="fm-automation-field"><span>Origem da lista</span><select value={importPayload.source} onChange={(e) => setImportPayload({ ...importPayload, source: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                    <option value="base_anterior">Base anterior da empresa</option>
                    <option value="clientes_antigos">Clientes antigos</option>
                    <option value="leads_antigos">Leads antigos</option>
                    <option value="evento">Evento</option>
                    <option value="formulario">Formulário</option>
                    <option value="planilha">Planilha</option>
                    <option value="importacao_manual">Importação manual</option>
                    <option value="outro">Outro</option>
                  </select></label>
                  <label className="fm-automation-field"><span>Consentimento</span><select value={importPayload.consent_status} onChange={(e) => setImportPayload({ ...importPayload, consent_status: e.target.value })} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">
                    <option value="unknown">Consentimento desconhecido</option>
                    <option value="opted_in">Opted-in com evidência</option>
                    <option value="opted_out">Opted-out</option>
                    <option value="blocked">Bloqueado</option>
                    <option value="transactional_only">Somente transacional</option>
                  </select></label>
                </div>
                {importPayload.consent_status === "opted_in" ? <label className="fm-automation-field"><span>Origem/evidência do consentimento</span><input value={importPayload.consent_source || ""} onChange={(e) => setImportPayload({ ...importPayload, consent_source: e.target.value })} placeholder="Origem/evidência do consentimento" className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label> : null}
                <div className="mt-4 flex gap-2">
                  <ActionButton type="button" variant="secondary" icon={Search} onClick={previewImport} disabled={!importFile}>Prévia</ActionButton>
                  <ActionButton type="button" icon={Upload} onClick={confirmImport} disabled={!importFile || (!importPayload.tag_id && !importPayload.tag_name)}>Importar sem enviar</ActionButton>
                </div>
                {importPreview ? (
                  <div className="mt-4 rounded-lg border border-slate-200 p-3 text-sm">
                    <p>Total de linhas: <b>{importPreview.total_rows}</b></p>
                    <p>Encoding: {importPreview.encoding}</p>
                    <pre className="mt-2 overflow-auto rounded bg-slate-50 p-2 text-xs">{JSON.stringify(importPreview.sample_rows, null, 2)}</pre>
                  </div>
                ) : null}
              </div>
              <aside className="space-y-3">
                {importBatches.map((batch) => (
                  <div key={batch.id} className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4 text-sm">
                    <div className="flex items-start justify-between gap-3">
                      <p className="font-semibold text-slate-900">{batch.filename}</p>
                      <button type="button" onClick={() => deleteImport(batch)} disabled={Boolean(deletingImportId)} title="Excluir lista" aria-label={`Excluir lista ${batch.filename}`} className="rounded-md p-1 text-slate-400 transition hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50">
                        {deletingImportId === batch.id ? <RefreshCw size={15} className="animate-spin" /> : <Trash2 size={15} />}
                      </button>
                    </div>
                    <p className="text-slate-500">Tag: {batch.tag_nome}</p>
                    <p className="text-slate-500">Novos {batch.inserted_count} · Atualizados {batch.updated_count} · Inválidos {batch.invalid_count}</p>
                  </div>
                ))}
                {!importBatches.length ? <InfoBanner>Nenhum lote CSV importado na Central.</InfoBanner> : null}
              </aside>
            </div>
          ) : null}

          {audienceTab === "saved" ? (
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="text-lg font-semibold text-slate-950">Construtor de público salvo</h2>
                <label className="fm-automation-field"><span>Nome do público</span><input value={savedAudienceForm.name} onChange={(e) => setSavedAudienceForm({ ...savedAudienceForm, name: e.target.value })} placeholder="Nome do público" className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
                <label className="fm-automation-field"><span>Descrição</span><textarea value={savedAudienceForm.description} onChange={(e) => setSavedAudienceForm({ ...savedAudienceForm, description: e.target.value })} placeholder="Descrição" rows={2} className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>

                {["include", "exclude"].map((section) => {
                  const normalized = normalizeAudienceRules(savedAudienceForm.rules);
                  const title = section === "include" ? "INCLUIR" : "EXCLUIR";
                  const helper = section === "include" ? "Pessoas que devem entrar no público." : "Pessoas removidas mesmo se também baterem nas inclusões.";
                  return (
                    <div key={section} className="mt-4 rounded-lg border border-slate-200 p-4">
                      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <div>
                          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
                          <p className="mt-1 text-xs text-slate-500">{helper}</p>
                        </div>
                        <select
                          aria-label="Combinação das regras"
                          value={normalized[section].match}
                          onChange={(e) => setSavedAudienceForm({ ...savedAudienceForm, rules: { ...normalized, [section]: { ...normalized[section], match: e.target.value } } })}
                          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        >
                          <option value="any">Qualquer uma</option>
                          <option value="all">Todas</option>
                        </select>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {tags.map((tag) => {
                          const selected = audienceRuleHas(normalized, section, tag.id);
                          const alsoExcluded = section === "include" && audienceRuleHas(normalized, "exclude", tag.id);
                          return (
                            <button aria-pressed={selected}
                              key={`${section}-${tag.id}`}
                              type="button"
                              onClick={() => setSavedAudienceForm({ ...savedAudienceForm, rules: updateAudienceTagRule(normalized, section, tag.id, !selected) })}
                              className={`rounded-full border px-3 py-1 text-xs font-semibold ${selected ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700"}`}
                            >
                              {tag.nome}{alsoExcluded ? " · também excluída" : ""}
                            </button>
                          );
                        })}
                        {!tags.length ? <p className="text-sm text-slate-500">Sem tags disponíveis.</p> : null}
                      </div>
                    </div>
                  );
                })}

                {normalizeAudienceRules(savedAudienceForm.rules).include.rules.some((rule) => audienceRuleHas(savedAudienceForm.rules, "exclude", rule.tag_id)) ? (
                  <InfoBanner tone="warning">Esta regra também está sendo excluída e pode resultar em público vazio.</InfoBanner>
                ) : null}

                <div className="mt-4 flex gap-2">
                  <ActionButton type="button" variant="secondary" onClick={() => previewSavedAudience()}>Prévia</ActionButton>
                  <ActionButton type="button" icon={Save} onClick={saveAudience}>Salvar público</ActionButton>
                </div>
                {savedAudiencePreview ? (
                  <div className="mt-4 rounded-lg border border-slate-200 p-4 text-sm">
                    <h3 className="font-semibold text-slate-900">Resumo do público</h3>
                    <dl className="mt-3 space-y-1">
                      <div className="flex justify-between gap-3"><dt>Encontrados inicialmente</dt><dd>{audienceCount(savedAudiencePreview, "initial_included")}</dd></div>
                      <div className="flex justify-between gap-3"><dt>Excluídos pelas regras</dt><dd>-{audienceCount(savedAudiencePreview, "excluded_by_rules")}</dd></div>
                      <div className="flex justify-between gap-3"><dt>Opt-out/bloqueados</dt><dd>-{audienceCount(savedAudiencePreview, "optout_removed") + audienceCount(savedAudiencePreview, "blocked_removed")}</dd></div>
                      <div className="flex justify-between gap-3"><dt>Telefones inválidos</dt><dd>-{audienceCount(savedAudiencePreview, "invalid_phone_count")}</dd></div>
                      <div className="flex justify-between gap-3"><dt>Duplicados</dt><dd>-{audienceCount(savedAudiencePreview, "duplicates_removed")}</dd></div>
                      <div className="mt-2 flex justify-between gap-3 border-t border-slate-200 pt-2 font-semibold"><dt>Destinatários finais</dt><dd>{audienceCount(savedAudiencePreview, "final_audience_count", savedAudiencePreview.selected)}</dd></div>
                    </dl>
                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                      <div>
                        <p className="text-xs font-semibold uppercase text-slate-500">Incluídos</p>
                        {(savedAudiencePreview.members?.included || []).slice(0, 5).map((member) => <p key={member.lead_id} className="mt-1 truncate text-xs text-slate-600">{member.name || member.lead_id} · {member.reason}</p>)}
                        {savedAudiencePreview.members?.pagination?.included?.has_more ? (
                          <button type="button" onClick={() => previewSavedAudience("included", savedAudiencePreview.members.pagination.included.next_cursor)} className="mt-2 text-xs font-semibold text-slate-900">Próxima página</button>
                        ) : null}
                      </div>
                      <div>
                        <p className="text-xs font-semibold uppercase text-slate-500">Removidos</p>
                        {(savedAudiencePreview.members?.excluded || []).slice(0, 5).map((member) => <p key={`${member.lead_id}-${member.reason}`} className="mt-1 truncate text-xs text-slate-600">{member.name || member.lead_id} · {member.reason}</p>)}
                        {savedAudiencePreview.members?.pagination?.excluded?.has_more ? (
                          <button type="button" onClick={() => previewSavedAudience("excluded", savedAudiencePreview.members.pagination.excluded.next_cursor)} className="mt-2 text-xs font-semibold text-slate-900">Próxima página</button>
                        ) : null}
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
              <aside className="space-y-3">
                {savedAudiences.map((audience) => (
                  <button
                    key={audience.id}
                    type="button"
                    onClick={() => {
                      setSavedAudienceForm({ name: audience.name || "", description: audience.description || "", rules: normalizeAudienceRules(audience.rules_json || {}) });
                      setSavedAudiencePreview(audience.preview_json || null);
                    }}
                    className="w-full rounded-xl border border-slate-200 bg-white p-4 text-left text-sm hover:bg-slate-50"
                  >
                    <p className="font-semibold">{audience.name}</p>
                    <p className="text-slate-500">{audience.description}</p>
                    <p className="text-slate-500">Destinatários finais: {audience.preview_json?.counts?.final_audience_count ?? audience.preview_json?.selected ?? 0}</p>
                  </button>
                ))}
              </aside>
            </div>
          ) : null}

          {audienceTab === "smart" ? (
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="space-y-3">
                {smartAudiences.map((suggestion) => (
                  <div key={suggestion.id} className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4">
                    <p className="text-sm font-semibold text-slate-950">{suggestion.name}</p>
                    <p className="mt-1 text-xs text-slate-500">{suggestion.funnel_name}</p>
                    <div className="mt-3 grid gap-2 text-xs md:grid-cols-2">
                      <div><span className="font-semibold text-slate-700">Inclui:</span> {(suggestion.include_labels || []).join(", ") || "-"}</div>
                      <div><span className="font-semibold text-slate-700">Exclui:</span> {(suggestion.exclude_labels || []).join(", ") || "-"}</div>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <ActionButton type="button" variant="secondary" onClick={() => previewSmartAudience(suggestion)}>Prévia</ActionButton>
                      <ActionButton type="button" icon={Save} onClick={() => saveSmartAudience(suggestion)}>Salvar público</ActionButton>
                    </div>
                  </div>
                ))}
                {!smartAudiences.length ? <EmptyState title="Nenhum público sugerido" description="Configure etapas com regras para gerar sugestões automaticamente." icon={GitBranch} /> : null}
              </div>
              <aside className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4 text-sm">
                <h3 className="font-semibold text-slate-900">Prévia da sugestão</h3>
                {smartAudiencePreview?.preview ? (
                  <>
                    <dl className="mt-3 space-y-1">
                      <div className="flex justify-between gap-3"><dt>Encontrados inicialmente</dt><dd>{audienceCount(smartAudiencePreview.preview, "initial_included")}</dd></div>
                      <div className="flex justify-between gap-3"><dt>Excluídos pelas regras</dt><dd>-{audienceCount(smartAudiencePreview.preview, "excluded_by_rules")}</dd></div>
                      <div className="flex justify-between gap-3"><dt>Opt-out/bloqueados</dt><dd>-{audienceCount(smartAudiencePreview.preview, "optout_removed") + audienceCount(smartAudiencePreview.preview, "blocked_removed")}</dd></div>
                      <div className="flex justify-between gap-3"><dt>Telefones inválidos</dt><dd>-{audienceCount(smartAudiencePreview.preview, "invalid_phone_count")}</dd></div>
                      <div className="flex justify-between gap-3"><dt>Duplicados</dt><dd>-{audienceCount(smartAudiencePreview.preview, "duplicates_removed")}</dd></div>
                      <div className="mt-2 flex justify-between gap-3 border-t border-slate-200 pt-2 font-semibold"><dt>Destinatários finais</dt><dd>{audienceCount(smartAudiencePreview.preview, "final_audience_count")}</dd></div>
                    </dl>
                    <div className="mt-4 grid gap-3">
                      <div>
                        <p className="text-xs font-semibold uppercase text-slate-500">Incluídos</p>
                        {(smartAudiencePreview.preview.members?.included || []).map((member) => <p key={member.lead_id} className="mt-1 truncate text-xs text-slate-600">{member.name || member.lead_id} · {member.reason}</p>)}
                        {smartAudiencePreview.preview.members?.pagination?.included?.has_more ? (
                          <button type="button" onClick={() => previewSmartAudience(smartAudiencePreview, "included", smartAudiencePreview.preview.members.pagination.included.next_cursor)} className="mt-2 text-xs font-semibold text-slate-900">Próxima página de incluídos</button>
                        ) : null}
                      </div>
                      <div>
                        <p className="text-xs font-semibold uppercase text-slate-500">Removidos</p>
                        {(smartAudiencePreview.preview.members?.excluded || []).map((member) => <p key={`${member.lead_id}-${member.reason}`} className="mt-1 truncate text-xs text-slate-600">{member.name || member.lead_id} · {member.reason}</p>)}
                        {smartAudiencePreview.preview.members?.pagination?.excluded?.has_more ? (
                          <button type="button" onClick={() => previewSmartAudience(smartAudiencePreview, "excluded", smartAudiencePreview.preview.members.pagination.excluded.next_cursor)} className="mt-2 text-xs font-semibold text-slate-900">Próxima página de removidos</button>
                        ) : null}
                      </div>
                    </div>
                  </>
                ) : <p className="mt-2 text-slate-500">Abra a prévia de uma sugestão.</p>}
              </aside>
            </div>
          ) : null}

          {audienceTab === "funnels" ? (
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
              <div className="space-y-4">
                {funnels.map((funnel) => (
                  <div key={funnel.id} className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-semibold text-slate-950">{funnel.name}</p>
                        <p className="mt-1 text-xs text-slate-500">Ordenação: {funnel.order_source}</p>
                      </div>
                      <StatusBadge tone={funnel.active ? "active" : "warning"}>{funnel.active ? "Ativo" : "Inativo"}</StatusBadge>
                    </div>
                    <div className="mt-4 space-y-2">
                      {(funnel.stages || []).map((stage, index) => (
                        <div key={stage.id} className="rounded-lg border border-slate-200 p-3 text-sm">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div>
                              <p className="font-semibold text-slate-900">{stage.position}. {stage.name}</p>
                              <p className="text-xs text-slate-500">Regras: {stage.rules?.include?.rules?.length || 0} tag(s)</p>
                            </div>
                            <div className="flex gap-2">
                              <button type="button" onClick={() => moveFunnelStage(funnel, stage, -1)} disabled={index === 0} className="rounded border border-slate-200 px-2 py-1 text-xs disabled:opacity-40">Subir</button>
                              <button type="button" onClick={() => moveFunnelStage(funnel, stage, 1)} disabled={index === (funnel.stages || []).length - 1} className="rounded border border-slate-200 px-2 py-1 text-xs disabled:opacity-40">Descer</button>
                              <button type="button" onClick={() => toggleFunnelStage(funnel.id, stage.id, !stage.active)} className="rounded border border-slate-200 px-2 py-1 text-xs">{stage.active ? "Desativar" : "Ativar"}</button>
                            </div>
                          </div>
                        </div>
                      ))}
                      {!funnel.stages?.length ? <p className="text-sm text-slate-500">Sem etapas configuradas.</p> : null}
                    </div>
                  </div>
                ))}
                {!funnels.length ? <EmptyState title="Nenhum funil" description="Crie um funil para gerar públicos sugeridos." icon={BarChart3} /> : null}
              </div>
              <aside className="space-y-4">
                <div className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4">
                  <h3 className="font-semibold text-slate-900">Criar funil</h3>
                  <label className="fm-automation-field"><span>Nome do funil</span><input value={funnelForm.name} onChange={(e) => setFunnelForm({ ...funnelForm, name: e.target.value })} placeholder="Nome do funil" className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
                  <label className="fm-automation-field"><span>Descrição</span><textarea value={funnelForm.description} onChange={(e) => setFunnelForm({ ...funnelForm, description: e.target.value })} placeholder="Descrição" rows={2} className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
                  <ActionButton type="button" className="mt-3" icon={Save} onClick={saveFunnel}>Salvar funil</ActionButton>
                </div>
                <div className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4">
                  <h3 className="font-semibold text-slate-900">Adicionar etapa</h3>
                  <label className="fm-automation-field"><span>Funil</span><select value={stageForm.funnel_id} onChange={(e) => setStageForm({ ...stageForm, funnel_id: e.target.value })} className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
                    <option value="">Escolher funil</option>
                    {funnels.map((funnel) => <option key={funnel.id} value={funnel.id}>{funnel.name}</option>)}
                  </select></label>
                  <label className="fm-automation-field"><span>Nome da etapa</span><input value={stageForm.name} onChange={(e) => setStageForm({ ...stageForm, name: e.target.value })} placeholder="Nome da etapa" className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
                  <label className="fm-automation-field"><span>Posição</span><input type="number" value={stageForm.position} onChange={(e) => setStageForm({ ...stageForm, position: e.target.value })} placeholder="Posição" className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" /></label>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {tags.map((tag) => {
                      const selected = audienceRuleHas(stageForm.rules, "include", tag.id);
                      return (
                        <button aria-pressed={selected} key={`stage-${tag.id}`} type="button" onClick={() => setStageForm({ ...stageForm, rules: updateAudienceTagRule(stageForm.rules, "include", tag.id, !selected) })} className={`rounded-full border px-3 py-1 text-xs font-semibold ${selected ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-700"}`}>
                          {tag.nome}
                        </button>
                      );
                    })}
                  </div>
                  <ActionButton type="button" className="mt-3" icon={Plus} onClick={saveFunnelStage}>Salvar etapa</ActionButton>
                </div>
              </aside>
            </div>
          ) : null}

          {audienceTab === "snapshots" ? (
            <div className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-5">
              <h2 className="text-lg font-semibold text-slate-950">Snapshots de campanhas</h2>
              <p className="mt-1 text-sm text-slate-500">Destinatários congelados para campanhas já preparadas.</p>
              <div className="mt-4 space-y-2">
                {snapshots.map((snapshot) => <div key={`${snapshot.campaign_id}-${snapshot.created_at}`} className="rounded-lg border border-slate-200 p-3 text-sm"><p>Campanha {snapshot.campaign_id}</p><p className="text-slate-500">Incluídos {snapshot.inclusions}; excluídos {snapshot.exclusions}; versão {snapshot.audience_version}</p></div>)}
                {!snapshots.length ? <EmptyState title="Nenhum snapshot" description="Snapshots aparecem somente após preparação de campanha." icon={FolderOpen} /> : null}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {["results", "costs", "conversions"].includes(activeTab) ? (
        <section className="space-y-5">
          <div className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-4">
            <div className="fm-campaign-result-filters flex flex-wrap items-end gap-3">
              <label className="min-w-[220px] flex-1 text-sm font-semibold text-slate-700">
                Campanha
                <select value={resultsFilters.campaign_id} onChange={(e) => setResultsFilters((prev) => ({ ...prev, campaign_id: e.target.value }))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todas as campanhas</option>
                  {campaigns.map((campaign) => <option key={campaign.id} value={campaign.id}>{campaign.name || campaign.nome || campaign.id}</option>)}
                </select>
              </label>
              <label className="min-w-[180px] text-sm font-semibold text-slate-700">
                Período inicial
                <input type="datetime-local" value={resultsFilters.period_start} onChange={(e) => setResultsFilters((prev) => ({ ...prev, period_start: e.target.value }))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </label>
              <label className="min-w-[180px] text-sm font-semibold text-slate-700">
                Período final
                <input type="datetime-local" value={resultsFilters.period_end} onChange={(e) => setResultsFilters((prev) => ({ ...prev, period_end: e.target.value }))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
              </label>
              <label className="min-w-[220px] flex-1 text-sm font-semibold text-slate-700">
                Conexão
                <select value={resultsFilters.conexao_id} onChange={(e) => setResultsFilters((prev) => ({ ...prev, conexao_id: e.target.value }))} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm">
                  <option value="">Todas as conexões</option>
                  {officialConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.nome_instancia || connection.nome || connection.id}</option>)}
                </select>
              </label>
            </div>
            <p className="mt-3 text-sm font-semibold text-slate-900">Resultados de: {resultsScopeLabel}</p>
          </div>

          {resultsError ? <InfoBanner tone="warning">{resultsError}</InfoBanner> : null}

          <div className="fm-campaign-result-metrics grid gap-4 md:grid-cols-2 xl:grid-cols-6">
            <MetricCard label="Público" value={formatNumber(resultMetrics.audience_total)} icon={Users} />
            <MetricCard label="Aceitas pela Meta" value={formatNumber(resultMetrics.meta_accepted)} icon={Send} />
            <MetricCard label="Entregues" value={formatNumber(resultMetrics.delivered)} icon={CheckCircle2} tone="active" />
            <MetricCard label="Lidas" value={formatNumber(resultMetrics.read)} icon={Eye} />
            <MetricCard label="Falhas" value={formatNumber(resultMetrics.failed)} icon={AlertTriangle} tone="danger" />
            <MetricCard label="Respostas" value={formatNumber(resultMetrics.replies)} icon={GitBranch} />
            <MetricCard label="Taxa de entrega" value={formatPercent(resultRates.delivery_rate)} helper="Entregues / aceitas" icon={CheckCircle2} />
            <MetricCard label="Taxa de leitura" value={formatPercent(resultRates.read_rate)} helper="Lidas / entregues" icon={Eye} />
            <MetricCard label="Taxa de resposta" value={formatPercent(resultRates.reply_rate)} helper="Respostas / entregues" icon={GitBranch} />
            {analyticsResults?.costs?.state === "available" ? <MetricCard label="Custo total" value={formatMoney(analyticsResults?.costs?.total)} icon={DollarSign} /> : null}
          </div>

          {activeTab === "costs" && analyticsResults?.costs?.state !== "available" ? (
            <InfoBanner tone="warning">Não disponível: não há fonte auditável de custo, conversas gratuitas/tarifadas ou pricing billable para este escopo.</InfoBanner>
          ) : null}
          {activeTab === "conversions" && analyticsResults?.conversions?.state !== "available" ? (
            <InfoBanner tone="warning">Sem eventos de conversão registrados.</InfoBanner>
          ) : null}

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
            <section className="fm-automation-panel rounded-xl border border-slate-200 bg-white p-5">
              <h2 className="text-lg font-semibold text-slate-950">Desempenho ao longo do tempo</h2>
              <div className="mt-4">
                <ResultsLineChart timeline={analyticsResults?.timeline} />
              </div>
            </section>
            <section className="fm-automation-panel rounded-xl border border-slate-200 bg-slate-50 p-5">
              <h2 className="text-lg font-semibold text-slate-950">Funil</h2>
              <div className="mt-4">
                <ResultsFunnel funnel={analyticsResults?.funnel} failed={resultMetrics.failed} />
              </div>
            </section>
          </div>
        </section>
      ) : null}
    </div>
  );
}
