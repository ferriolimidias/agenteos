import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Clock3,
  AudioLines,
  MessageSquare,
  Plus,
  Pencil,
  RefreshCw,
  Send,
  ShieldCheck,
  TimerReset,
  Trash2,
  X,
} from "lucide-react";

import "../../styles/ux05-automation.css";

import MetaTemplateSelector from "../../components/MetaTemplateSelector";
import api from "../../services/api";
import { ActionButton, EmptyState, InfoBanner, LoadingState, PageHeader, SectionCard, StatusBadge } from "../../components/ui";
import { getActiveEmpresaId } from "../../utils/auth";

const FOLLOWUP_MODE_CONVERSATION_RESUME = "conversation_resume";
const FOLLOWUP_MODE_SCHEDULED_MESSAGE = "scheduled_message";
const MESSAGE_MODE_AI_CONTEXTUAL = "ai_contextual";
const MESSAGE_MODE_FIXED_TEXT = "fixed_text";
const MESSAGE_MODE_META_TEMPLATE = "meta_template";

const FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE = "last_agent_message";
const FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE = "first_inbound_message";
const FOLLOWUP_ANCHOR_AD_ENTRY = "ad_entry";
const FOLLOWUP_ANCHOR_TAG_APPLIED = "tag_applied";
const FOLLOWUP_ANCHOR_CONTACT_CREATED = "contact_created";
const FOLLOWUP_ANCHOR_FIXED_DATETIME = "fixed_datetime";

const MODE_OPTIONS = [
  {
    value: FOLLOWUP_MODE_CONVERSATION_RESUME,
    title: "Retomar conversa parada",
    description: "Envia somente quando a pessoa não respondeu e a conversa permaneceu parada.",
  },
  {
    value: FOLLOWUP_MODE_SCHEDULED_MESSAGE,
    title: "Envio programado / convite",
    description: "Envia após o tempo definido, mesmo que a conversa tenha continuado.",
  },
];

const ANCHOR_OPTIONS = {
  [FOLLOWUP_MODE_CONVERSATION_RESUME]: [
    {
      value: FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE,
      label: "Última mensagem do agente",
    },
  ],
  [FOLLOWUP_MODE_SCHEDULED_MESSAGE]: [
    {
      value: FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE,
      label: "Primeira mensagem da pessoa",
    },
    {
      value: FOLLOWUP_ANCHOR_AD_ENTRY,
      label: "Entrada pelo anúncio",
    },
    {
      value: FOLLOWUP_ANCHOR_TAG_APPLIED,
      label: "Aplicação de uma tag",
    },
    {
      value: FOLLOWUP_ANCHOR_CONTACT_CREATED,
      label: "Criação do contato",
    },
    {
      value: FOLLOWUP_ANCHOR_FIXED_DATETIME,
      label: "Data e horário fixos",
    },
  ],
};

const INITIAL_FORM = {
  nome: "",
  ativo: true,
  mode: FOLLOWUP_MODE_CONVERSATION_RESUME,
  delay_value: 3,
  delay_unit: "hours",
  anchor_type: FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE,
  message_content: "",
  message_mode: MESSAGE_MODE_FIXED_TEXT,
  template_id: "",
  cancel_on_inbound: true,
  cancel_on_human: true,
  cancel_on_opt_out: true,
  cancel_on_event_end: true,
  cancel_tag_ids: [],
  activation_at: "",
  campaign_or_event_id: "",
  needs_review: false,
  content_type: "text",
  media_asset_id: "",
  media_asset: null,
  send_as_voice: false,
  audio_caption: "",
  send_window_enabled: false,
  send_window_start: "09:00",
  send_window_end: "21:00",
  timezone: "America/Sao_Paulo",
  limit_to_free_entry_window: false,
};

function ModeCard({ active, title, description, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`fm-automation-choice rounded-xl border p-4 text-left transition-colors ${
        active
          ? "border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500"
          : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          <p className="mt-1 text-sm leading-6 text-slate-500">{description}</p>
        </div>
        <span
          className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold ${
            active ? "border-indigo-500 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-500"
          }`}
        >
          {active ? "✓" : "•"}
        </span>
      </div>
    </button>
  );
}

function fieldLabelFromUnit(unit) {
  if (unit === "hours") return "horas";
  if (unit === "days") return "dias";
  return "minutos";
}

function anchorLabel(anchorType) {
  for (const option of Object.values(ANCHOR_OPTIONS).flat()) {
    if (option.value === anchorType) {
      return option.label;
    }
  }
  return "Âncora não definida";
}

function modeLabel(mode) {
  return MODE_OPTIONS.find((option) => option.value === mode)?.title || mode || "Follow-up";
}

function formatDateTime(value) {
  if (!value) return "Sem registro";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Sem registro";
  return parsed.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function countValue(value) {
  return Number(value || 0).toLocaleString("pt-BR");
}

function sanitizeTags(values) {
  const seen = new Set();
  const output = [];
  for (const value of values || []) {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) {
      continue;
    }
    seen.add(text);
    output.push(text);
  }
  return output;
}

function normalizeFormFromItem(item) {
  return {
    ...INITIAL_FORM,
    nome: item?.nome || "",
    ativo: Boolean(item?.ativo),
    mode: item?.mode || FOLLOWUP_MODE_CONVERSATION_RESUME,
    delay_value: Number(item?.delay_value ?? INITIAL_FORM.delay_value),
    delay_unit: item?.delay_unit || INITIAL_FORM.delay_unit,
    anchor_type: item?.anchor_type || FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE,
    message_content: item?.message_content || "",
    message_mode: item?.message_mode || MESSAGE_MODE_FIXED_TEXT,
    template_id: item?.template_id || "",
    cancel_on_inbound: item?.cancel_on_inbound ?? true,
    cancel_on_human: item?.cancel_on_human ?? true,
    cancel_on_opt_out: item?.cancel_on_opt_out ?? true,
    cancel_on_event_end: item?.cancel_on_event_end ?? true,
    cancel_tag_ids: Array.isArray(item?.cancel_tag_ids) ? sanitizeTags(item.cancel_tag_ids) : [],
    activation_at: item?.activation_at ? String(item.activation_at).slice(0, 16) : "",
    campaign_or_event_id: item?.campaign_or_event_id || "",
    needs_review: Boolean(item?.needs_review),
    content_type: item?.content_type || "text",
    media_asset_id: item?.media_asset_id || "",
    media_asset: item?.media_asset || null,
    send_as_voice: Boolean(item?.send_as_voice),
    audio_caption: item?.audio_caption || "",
    send_window_enabled: Boolean(item?.send_window_enabled),
    send_window_start: item?.send_window_start || "09:00",
    send_window_end: item?.send_window_end || "21:00",
    timezone: item?.timezone || "America/Sao_Paulo",
    limit_to_free_entry_window: Boolean(item?.limit_to_free_entry_window),
  };
}

export default function TenantFollowUps() {
  const empresaId = getActiveEmpresaId();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [followups, setFollowups] = useState([]);
  const [tags, setTags] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [editingItem, setEditingItem] = useState(null);
  const [formData, setFormData] = useState(INITIAL_FORM);
  const [audioUploading, setAudioUploading] = useState(false);

  const tagsMap = useMemo(() => {
    const map = new Map();
    for (const tag of tags) {
      map.set(tag.id, tag);
    }
    return map;
  }, [tags]);

  const sortedTags = useMemo(() => {
    return [...tags].sort((a, b) => String(a?.nome || "").localeCompare(String(b?.nome || ""), "pt-BR"));
  }, [tags]);

  const approvedTemplates = useMemo(() => {
    return templates.filter((template) => String(template?.normalized_status || "").toLowerCase() === "approved");
  }, [templates]);

  const triggerTag = useMemo(() => {
    if (formData.anchor_type !== FOLLOWUP_ANCHOR_TAG_APPLIED) {
      return null;
    }
    return tagsMap.get(formData.campaign_or_event_id) || null;
  }, [formData.anchor_type, formData.campaign_or_event_id, tagsMap]);

  const suggestedCancelTagIds = useMemo(() => {
    if (!triggerTag || String(triggerTag.tipo || "").trim().toLowerCase() !== "etapa_funil") {
      return [];
    }
    const triggerOrder = Number(triggerTag.ordem ?? -1);
    return sortedTags
      .filter((tag) => String(tag.tipo || "").trim().toLowerCase() === "etapa_funil" && Number(tag.ordem ?? -1) > triggerOrder)
      .map((tag) => tag.id);
  }, [sortedTags, triggerTag]);

  const suggestedCancelTags = useMemo(() => {
    const ids = new Set(suggestedCancelTagIds);
    return sortedTags.filter((tag) => ids.has(tag.id));
  }, [sortedTags, suggestedCancelTagIds]);

  const loadData = useCallback(async () => {
    if (!empresaId) {
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const [followupsRes, tagsRes, templatesRes] = await Promise.all([
        api.get(`/empresas/${empresaId}/followups`, { timeout: 15000 }),
        api.get(`/empresas/${empresaId}/crm/tags/oficiais`, { timeout: 15000 }),
        api.get(`/empresas/${empresaId}/meta/templates`, { timeout: 15000 }),
      ]);
      setFollowups(Array.isArray(followupsRes.data) ? followupsRes.data : []);
      setTags(Array.isArray(tagsRes.data) ? tagsRes.data : []);
      setTemplates(Array.isArray(templatesRes.data) ? templatesRes.data : []);
    } catch (error) {
      console.error("Erro ao carregar follow-ups:", error);
      setFollowups([]);
      setTags([]);
      setTemplates([]);
    } finally {
      setLoading(false);
    }
  }, [empresaId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const openCreate = () => {
    setEditingItem(null);
    setFormData(INITIAL_FORM);
    setShowModal(true);
  };

  const openEdit = (item) => {
    setEditingItem(item);
    setFormData(normalizeFormFromItem(item));
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    setEditingItem(null);
    setFormData(INITIAL_FORM);
  };

  const updateMode = (nextMode) => {
    setFormData((prev) => {
      const next = { ...prev, mode: nextMode };
      if (nextMode === FOLLOWUP_MODE_CONVERSATION_RESUME) {
        next.anchor_type = FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE;
        next.cancel_on_inbound = true;
        next.cancel_on_human = true;
        next.cancel_on_opt_out = true;
        next.cancel_on_event_end = true;
      } else {
        if (
          prev.mode === FOLLOWUP_MODE_CONVERSATION_RESUME ||
          prev.anchor_type === FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE
        ) {
          next.anchor_type = FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE;
        }
        next.cancel_on_inbound = false;
        next.cancel_on_human = true;
        next.cancel_on_opt_out = true;
        next.cancel_on_event_end = true;
      }
      return next;
    });
  };

  const updateAnchor = (anchorType) => {
    setFormData((prev) => {
      const next = {
        ...prev,
        anchor_type: anchorType,
      };
      if (anchorType !== FOLLOWUP_ANCHOR_TAG_APPLIED) {
        next.campaign_or_event_id = "";
      }
      if (anchorType !== FOLLOWUP_ANCHOR_FIXED_DATETIME) {
        next.activation_at = "";
      }
      return next;
    });
  };

  const toggleCancelTag = (tagId) => {
    setFormData((prev) => {
      const current = new Set(prev.cancel_tag_ids || []);
      if (current.has(tagId)) {
        current.delete(tagId);
      } else {
        current.add(tagId);
      }
      return { ...prev, cancel_tag_ids: Array.from(current) };
    });
  };

  const applySuggestedCancelTags = () => {
    setFormData((prev) => {
      const next = new Set(prev.cancel_tag_ids || []);
      for (const tagId of suggestedCancelTagIds) {
        next.add(tagId);
      }
      return { ...prev, cancel_tag_ids: Array.from(next) };
    });
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!empresaId) return;
    if (formData.content_type !== "text" && !formData.media_asset_id) {
      alert("Selecione um áudio para este follow-up.");
      return;
    }
    if (formData.message_mode === MESSAGE_MODE_META_TEMPLATE && !String(formData.template_id || "").trim()) {
      alert("Selecione um template aprovado para este follow-up.");
      return;
    }

    const payload = {
      nome: String(formData.nome || "").trim(),
      ativo: Boolean(formData.ativo),
      mode: formData.mode,
      delay_value: Number(formData.delay_value || 0),
      delay_unit: formData.delay_unit,
      anchor_type: formData.anchor_type,
      message_content: String(formData.message_content || "").trim(),
      message_mode: formData.message_mode,
      template_id: String(formData.template_id || "").trim() || null,
      cancel_on_inbound: Boolean(formData.cancel_on_inbound),
      cancel_on_human: Boolean(formData.cancel_on_human),
      cancel_on_opt_out: Boolean(formData.cancel_on_opt_out),
      cancel_on_event_end: Boolean(formData.cancel_on_event_end),
      cancel_tag_ids: sanitizeTags(formData.cancel_tag_ids),
      activation_at:
        formData.anchor_type === FOLLOWUP_ANCHOR_FIXED_DATETIME && formData.activation_at
          ? String(formData.activation_at)
          : null,
      campaign_or_event_id:
        formData.anchor_type === FOLLOWUP_ANCHOR_TAG_APPLIED
          ? String(formData.campaign_or_event_id || "").trim() || null
          : null,
      needs_review: Boolean(formData.needs_review),
      content_type: formData.content_type,
      media_asset_id: formData.media_asset_id || null,
      send_as_voice: Boolean(formData.send_as_voice),
      audio_caption: String(formData.audio_caption || "").trim() || null,
      send_window_enabled: Boolean(formData.send_window_enabled),
      send_window_start: formData.send_window_enabled ? String(formData.send_window_start || "").trim() || null : null,
      send_window_end: formData.send_window_enabled ? String(formData.send_window_end || "").trim() || null : null,
      timezone: String(formData.timezone || "America/Sao_Paulo").trim() || "America/Sao_Paulo",
      limit_to_free_entry_window: Boolean(formData.limit_to_free_entry_window),
    };

    try {
      setSaving(true);
      if (editingItem?.id) {
        await api.put(`/empresas/${empresaId}/followups/${editingItem.id}`, payload);
      } else {
        await api.post(`/empresas/${empresaId}/followups`, payload);
      }
      await loadData();
      closeModal();
    } catch (error) {
      console.error("Erro ao salvar follow-up:", error);
      alert(error.response?.data?.detail || "Não foi possível salvar o follow-up.");
    } finally {
      setSaving(false);
    }
  };

  const handleAudioUpload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !empresaId) return;
    try {
      setAudioUploading(true);
      const body = new FormData();
      body.append("file", file);
      const response = await api.post(`/empresas/${empresaId}/followups/media`, body, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const asset = response.data || {};
      setFormData((prev) => ({ ...prev, media_asset_id: asset.id || "", media_asset: asset }));
    } catch (error) {
      alert(error.response?.data?.detail || "Não foi possível carregar o áudio.");
    } finally {
      setAudioUploading(false);
    }
  };

  const handleDelete = async (item) => {
    if (!empresaId || !item?.id) return;
    if (!window.confirm(`Excluir o follow-up "${item.nome}"?`)) return;

    try {
      setDeletingId(item.id);
      await api.delete(`/empresas/${empresaId}/followups/${item.id}`);
      await loadData();
    } catch (error) {
      console.error("Erro ao excluir follow-up:", error);
      alert(error.response?.data?.detail || "Não foi possível excluir o follow-up.");
    } finally {
      setDeletingId(null);
    }
  };

  const activeModeOptions = ANCHOR_OPTIONS[formData.mode] || ANCHOR_OPTIONS[FOLLOWUP_MODE_SCHEDULED_MESSAGE];
  const selectedAnchorIsTag = formData.anchor_type === FOLLOWUP_ANCHOR_TAG_APPLIED;
  const selectedAnchorIsFixedDate = formData.anchor_type === FOLLOWUP_ANCHOR_FIXED_DATETIME;
  const selectedAnchorIsAdEntry = formData.anchor_type === FOLLOWUP_ANCHOR_AD_ENTRY;
  const selectedTriggerTag = triggerTag;

  return (
    <div className="fm-automation fm-followups space-y-6">
      <PageHeader
        title="Follow-ups"
        description="Configure dois comportamentos claros: retomar conversa parada ou enviar uma mensagem programada."
        icon={TimerReset}
        eyebrow="Automação"
        actions={
          <>
            <ActionButton type="button" onClick={loadData} variant="secondary" icon={RefreshCw}>
              Atualizar
            </ActionButton>
            <ActionButton type="button" onClick={openCreate} icon={Plus}>
              Novo follow-up
            </ActionButton>
          </>
        }
      />

      {loading ? (
        <LoadingState label="Carregando follow-ups..." />
      ) : followups.length === 0 ? (
        <EmptyState
          title="Nenhum follow-up configurado"
          description="Crie uma regra simples para retomar conversas ou agendar um convite."
          icon={TimerReset}
          action={
            <ActionButton type="button" onClick={openCreate} icon={Plus}>
              Novo follow-up
            </ActionButton>
          }
        />
      ) : (
        <div className="fm-followup-list grid gap-4 lg:grid-cols-2">
          {followups.map((item) => {
            const cancelTagNames = (Array.isArray(item.cancel_tag_ids) ? item.cancel_tag_ids : [])
              .map((tagId) => tagsMap.get(tagId)?.nome || null)
              .filter(Boolean);
            const cancelSummary = cancelTagNames.length
              ? cancelTagNames.slice(0, 3).join(", ") + (cancelTagNames.length > 3 ? ` +${cancelTagNames.length - 3}` : "")
              : "Sem tags de cancelamento";

            return (
              <article
                key={item.id}
                className="fm-followup-card rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition-shadow hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="break-words text-lg font-semibold text-slate-950">{item.nome}</h3>
                      <StatusBadge tone={item.mode === FOLLOWUP_MODE_CONVERSATION_RESUME ? "info" : "active"}>
                        {modeLabel(item.mode)}
                      </StatusBadge>
                      {item.needs_review ? <StatusBadge tone="warning">Revisar</StatusBadge> : null}
                    </div>
                    <p className="mt-1 text-sm text-slate-500">
                      Envia após {item.delay_value} {fieldLabelFromUnit(item.delay_unit)} a partir de {anchorLabel(item.anchor_type)}.
                    </p>
                  </div>
                  <StatusBadge tone={item.ativo ? "active" : "warning"}>{item.ativo ? "Ativo" : "Inativo"}</StatusBadge>
                </div>

                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      <Clock3 size={14} />
                      Momento
                    </p>
                    <p className="mt-2 text-sm text-slate-900">
                      {item.delay_value} {fieldLabelFromUnit(item.delay_unit)} — {anchorLabel(item.anchor_type)}
                    </p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      <MessageSquare size={14} />
                      Mensagem
                    </p>
                    <p className="mt-2 line-clamp-3 text-sm text-slate-900">
                      {item.content_type && item.content_type !== "text"
                        ? `Áudio${item.send_as_voice ? " (voz)" : ""}`
                        : item.message_mode === MESSAGE_MODE_AI_CONTEXTUAL
                          ? "Gerada com IA contextual"
                          : item.message_mode === MESSAGE_MODE_META_TEMPLATE
                            ? "Template do WhatsApp"
                        : item.message_content || "Sem conteúdo configurado"}
                    </p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      <ShieldCheck size={14} />
                      Cancelamento
                    </p>
                    <p className="mt-2 text-sm text-slate-900">{cancelSummary}</p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      <Send size={14} />
                      Template
                    </p>
                    <p className="mt-2 text-sm text-slate-900">{item.template_id ? "Template selecionado" : "Sem template"}</p>
                  </div>
                </div>

                <div className="fm-followup-metrics mt-4 grid gap-3 md:grid-cols-5">
                  <div className="rounded-lg border border-slate-100 bg-white p-3">
                    <p className="text-xs font-semibold uppercase text-slate-500">Agendados</p>
                    <p className="mt-1 text-lg font-semibold text-slate-950">{countValue(item.executions_scheduled)}</p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-white p-3">
                    <p className="text-xs font-semibold uppercase text-slate-500">Enviados</p>
                    <p className="mt-1 text-lg font-semibold text-slate-950">{countValue(item.executions_sent)}</p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-white p-3">
                    <p className="text-xs font-semibold uppercase text-slate-500">Falhas</p>
                    <p className="mt-1 text-lg font-semibold text-slate-950">{countValue(item.executions_failed)}</p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-white p-3">
                    <p className="text-xs font-semibold uppercase text-slate-500">Cancelados</p>
                    <p className="mt-1 text-lg font-semibold text-slate-950">{countValue(item.executions_cancelled)}</p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-white p-3">
                    <p className="text-xs font-semibold uppercase text-slate-500">Aguardando</p>
                    <p className="mt-1 text-lg font-semibold text-slate-950">{countValue(item.executions_pending)}</p>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 md:grid-cols-3">
                  <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <p className="text-xs font-semibold uppercase text-slate-500">Próximo envio</p>
                    <p className="mt-1 text-sm text-slate-900">{formatDateTime(item.next_scheduled_at)}</p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <p className="text-xs font-semibold uppercase text-slate-500">Último envio</p>
                    <p className="mt-1 text-sm text-slate-900">{formatDateTime(item.last_sent_at)}</p>
                  </div>
                  <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                    <p className="text-xs font-semibold uppercase text-slate-500">Último erro</p>
                    <p className="mt-1 break-words text-sm text-slate-900">{item.last_error || "Sem erro registrado"}</p>
                  </div>
                </div>

                <div className="mt-5 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => openEdit(item)}
                    className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    <Pencil size={13} />
                    Editar
                  </button>
                  <button
                    type="button"
                    disabled={deletingId === item.id}
                    onClick={() => handleDelete(item)}
                    className="inline-flex items-center gap-1 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-60"
                  >
                    <Trash2 size={13} />
                    {deletingId === item.id ? "Excluindo..." : "Excluir"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {showModal ? (
        <div className="fm-automation-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-labelledby="followup-editor-title">
          <div className="fm-followup-editor max-h-[92vh] w-full max-w-5xl rounded-2xl border border-slate-200 bg-white shadow-xl">
            <div className="fm-automation-dialog-heading flex items-start justify-between gap-3">
              <div>
                <h2 id="followup-editor-title" className="text-xl font-semibold text-slate-950">
                  {editingItem ? "Editar follow-up" : "Novo follow-up"}
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  O backend resolve tenant, contato, conversa, idempotência e política do canal.
                </p>
              </div>
              <button
                type="button"
                onClick={closeModal}
                aria-label="Fechar editor de follow-up"
                className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-100"
              >
                <X size={14} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="fm-automation-dialog-body space-y-5" aria-busy={saving}>
              <SectionCard title="Dados básicos" description="Nome e ativação da regra.">
                <div className="grid gap-4 md:grid-cols-[1fr_220px]">
                  <div>
                    <label htmlFor="followup-nome" className="mb-1 block text-sm font-medium text-slate-700">Nome do follow-up</label>
                    <input id="followup-nome"
                      required
                      value={formData.nome}
                      onChange={(event) => setFormData((prev) => ({ ...prev, nome: event.target.value }))}
                      className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                      placeholder="Ex: Retomada 3h"
                    />
                  </div>
                  <label className="flex items-center gap-3 rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={Boolean(formData.ativo)}
                      onChange={(event) => setFormData((prev) => ({ ...prev, ativo: event.target.checked }))}
                      className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                    />
                    Ativo
                  </label>
                </div>
              </SectionCard>

              <SectionCard title="Tipo" description="Escolha o comportamento principal da regra.">
                <div className="grid gap-3 md:grid-cols-2">
                  {MODE_OPTIONS.map((option) => (
                    <ModeCard
                      key={option.value}
                      active={formData.mode === option.value}
                      title={option.title}
                      description={option.description}
                      onClick={() => updateMode(option.value)}
                    />
                  ))}
                </div>
              </SectionCard>

              <SectionCard title="Momento do envio" description="Defina o atraso e de onde a contagem começa.">
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-sm font-medium text-slate-700">Enviar após</label>
                    <div className="grid grid-cols-[1fr_150px] gap-2">
                      <input aria-label="Quantidade de tempo"
                        required
                        type="number"
                        min="1"
                        value={formData.delay_value}
                        onChange={(event) =>
                          setFormData((prev) => ({ ...prev, delay_value: Number(event.target.value || 0) }))
                        }
                        className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                      />
                      <select aria-label="Unidade do atraso"
                        value={formData.delay_unit}
                        onChange={(event) => setFormData((prev) => ({ ...prev, delay_unit: event.target.value }))}
                        className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                      >
                        <option value="minutes">minutos</option>
                        <option value="hours">horas</option>
                        <option value="days">dias</option>
                      </select>
                    </div>
                  </div>

                  <div>
                    <label htmlFor="followup-anchor-type" className="mb-1 block text-sm font-medium text-slate-700">Contado a partir de</label>
                    <select id="followup-anchor-type"
                      value={formData.anchor_type}
                      onChange={(event) => updateAnchor(event.target.value)}
                      className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    >
                      {activeModeOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {selectedAnchorIsTag ? (
                  <div className="mt-4">
                    <label htmlFor="followup-campaign-or-event-id" className="mb-1 block text-sm font-medium text-slate-700">Tag que dispara esta regra</label>
                    <select id="followup-campaign-or-event-id"
                      value={formData.campaign_or_event_id}
                      onChange={(event) =>
                        setFormData((prev) => ({ ...prev, campaign_or_event_id: event.target.value }))
                      }
                      className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    >
                      <option value="">Selecione uma tag</option>
                      {sortedTags.map((tag) => (
                        <option key={tag.id} value={tag.id}>
                          {tag.nome}
                        </option>
                      ))}
                    </select>

                    {selectedTriggerTag ? (
                      <div className="mt-3 rounded-xl border border-indigo-200 bg-indigo-50 p-4">
                        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                          <div>
                            <p className="text-sm font-semibold text-indigo-900">Sugestão de tags de cancelamento</p>
                            <p className="mt-1 text-sm text-indigo-800">
                              Baseado na etapa atual, o sistema sugere tags posteriores para cancelar o envio quando a pessoa avançar.
                            </p>
                          </div>
                          {suggestedCancelTagIds.length > 0 ? (
                            <ActionButton type="button" variant="secondary" onClick={applySuggestedCancelTags}>
                              Usar sugestões
                            </ActionButton>
                          ) : null}
                        </div>
                        {suggestedCancelTags.length > 0 ? (
                          <div className="mt-3 flex flex-wrap gap-2">
                            {suggestedCancelTags.map((tag) => (
                              <StatusBadge key={tag.id} tone="info">
                                {tag.nome}
                              </StatusBadge>
                            ))}
                          </div>
                        ) : (
                          <p className="mt-3 text-sm text-indigo-700">Nenhuma sugestão automática disponível para esta tag.</p>
                        )}
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {selectedAnchorIsFixedDate ? (
                  <div className="mt-4">
                    <label htmlFor="followup-activation-at" className="mb-1 block text-sm font-medium text-slate-700">Data e hora de execução</label>
                    <input id="followup-activation-at"
                      required
                      type="datetime-local"
                      value={formData.activation_at}
                      onChange={(event) => setFormData((prev) => ({ ...prev, activation_at: event.target.value }))}
                      className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                ) : null}

                {selectedAnchorIsAdEntry ? (
                  <div className="mt-4">
                    <InfoBanner tone="info">
                      A regra usa a atribuição mais recente do anúncio quando disponível.
                    </InfoBanner>
                  </div>
                ) : null}
              </SectionCard>

              <SectionCard title="Mensagem" description="Texto livre e template aprovado, quando aplicável.">
                <div className="space-y-4">
                  <div>
                    <label className="mb-2 block text-sm font-medium text-slate-700">Tipo da mensagem</label>
                    <div className="grid gap-2 md:grid-cols-3">
                      {[
                        { value: MESSAGE_MODE_AI_CONTEXTUAL, label: "Gerar com IA" },
                        { value: MESSAGE_MODE_FIXED_TEXT, label: "Mensagem fixa" },
                        { value: MESSAGE_MODE_META_TEMPLATE, label: "Template do WhatsApp" },
                      ].map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          aria-pressed={formData.message_mode === option.value}
                          onClick={() =>
                            setFormData((prev) => ({
                              ...prev,
                              message_mode: option.value,
                              content_type: option.value === MESSAGE_MODE_META_TEMPLATE ? "text" : prev.content_type,
                              template_id: option.value === MESSAGE_MODE_META_TEMPLATE ? prev.template_id : "",
                            }))
                          }
                          className={`rounded-xl border px-3 py-3 text-left text-sm ${formData.message_mode === option.value ? "border-indigo-500 bg-indigo-50 text-indigo-900" : "border-slate-200 bg-white text-slate-700"}`}
                        >
                          <span className="font-semibold">{option.label}</span>
                        </button>
                      ))}
                    </div>
                  </div>

                  {formData.message_mode === MESSAGE_MODE_META_TEMPLATE ? null : (
                    <>
                      <div>
                        <label className="mb-2 block text-sm font-medium text-slate-700">Tipo de conteúdo</label>
                        <div className="grid gap-2 md:grid-cols-3">
                          {["text", "audio", "text_audio"].map((type) => (
                            <button
                              key={type}
                              type="button"
                              aria-pressed={formData.content_type === type}
                              onClick={() => setFormData((prev) => ({ ...prev, content_type: type }))}
                              className={`rounded-xl border px-3 py-3 text-left text-sm ${formData.content_type === type ? "border-indigo-500 bg-indigo-50 text-indigo-900" : "border-slate-200 bg-white text-slate-700"}`}
                            >
                              <span className="font-semibold">{type === "text" ? "Texto" : type === "audio" ? "Áudio" : "Texto + áudio"}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                      {formData.content_type !== "text" ? (
                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <p className="flex items-center gap-2 text-sm font-semibold text-slate-900"><AudioLines size={16} /> Áudio</p>
                            <label className="inline-flex cursor-pointer items-center rounded-lg bg-indigo-600 px-3 py-2 text-xs font-medium text-white">
                              {audioUploading ? "Carregando..." : "Enviar áudio"}
                              <input type="file" accept="audio/*,.ogg,.opus,.amr,.m4a" className="hidden" onChange={handleAudioUpload} disabled={audioUploading} />
                            </label>
                          </div>
                          {formData.media_asset ? (
                            <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3">
                              <p className="text-sm font-medium text-slate-900">{formData.media_asset.original_name || "Áudio selecionado"}</p>
                              <p className="mt-1 text-xs text-slate-500">{formData.media_asset.mime_type || "audio"} · {Math.round(Number(formData.media_asset.byte_size || 0) / 1024)} KB</p>
                              <label className="mt-3 flex items-center gap-2 text-sm text-slate-700">
                                <input type="checkbox" checked={Boolean(formData.send_as_voice)} onChange={(event) => setFormData((prev) => ({ ...prev, send_as_voice: event.target.checked }))} />
                                Enviar como mensagem de voz
                              </label>
                              {formData.content_type === "text_audio" ? (
                                <input aria-label="Texto complementar do áudio" value={formData.audio_caption} onChange={(event) => setFormData((prev) => ({ ...prev, audio_caption: event.target.value }))} placeholder="Texto complementar opcional" className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" />
                              ) : null}
                            </div>
                          ) : null}
                          <InfoBanner tone="warning">Fora da janela livre, o backend pode bloquear áudio. Template aprovado não autoriza áudio livre automaticamente.</InfoBanner>
                        </div>
                      ) : null}
                    </>
                  )}
                  {formData.message_mode === MESSAGE_MODE_META_TEMPLATE ? null : (
                  <div>
                    <label htmlFor="followup-message-content" className="mb-1 block text-sm font-medium text-slate-700">
                      {formData.message_mode === MESSAGE_MODE_AI_CONTEXTUAL ? "Instrução para a IA" : "Mensagem"}
                    </label>
                    <textarea id="followup-message-content"
                      rows={5}
                      value={formData.message_content}
                      onChange={(event) =>
                        setFormData((prev) => ({ ...prev, message_content: event.target.value }))
                      }
                      className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                      placeholder={formData.message_mode === MESSAGE_MODE_AI_CONTEXTUAL ? "A IA usará esta instrução junto com o contexto da conversa para gerar a mensagem de retomada." : "Olá, {{1}}! Tudo bem por aí?"}
                    />
                    {formData.message_mode === MESSAGE_MODE_AI_CONTEXTUAL ? (
                      <p className="mt-2 text-sm text-slate-500">
                        A IA usará esta instrução junto com o contexto da conversa para gerar uma única mensagem curta.
                      </p>
                    ) : null}
                  </div>
                  )}

                  {formData.message_mode === MESSAGE_MODE_META_TEMPLATE ? (
                  <MetaTemplateSelector
                    templates={approvedTemplates}
                    value={formData.template_id}
                    onChange={(value) => setFormData((prev) => ({ ...prev, template_id: value }))}
                    placeholder="Template aprovado opcional"
                  />
                  ) : null}

                  <InfoBanner tone="info">
                    Mensagens livres só podem ser enviadas enquanto a janela de atendimento do WhatsApp estiver aberta. Fora dela, use um template aprovado.
                  </InfoBanner>
                </div>
              </SectionCard>

              <SectionCard title="Horário de envio" description="Restrinja quando este follow-up pode ser enviado.">
                <div className="space-y-4">
                  <label className="flex items-center gap-3 rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={Boolean(formData.send_window_enabled)}
                      onChange={(event) => setFormData((prev) => ({ ...prev, send_window_enabled: event.target.checked }))}
                      className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                    />
                    Restringir horário
                  </label>
                  <div className="grid gap-4 md:grid-cols-3">
                    <div>
                      <label htmlFor="followup-send-window-start" className="mb-1 block text-sm font-medium text-slate-700">De</label>
                      <input id="followup-send-window-start" type="time" value={formData.send_window_start} onChange={(event) => setFormData((prev) => ({ ...prev, send_window_start: event.target.value }))} className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" />
                    </div>
                    <div>
                      <label htmlFor="followup-send-window-end" className="mb-1 block text-sm font-medium text-slate-700">Até</label>
                      <input id="followup-send-window-end" type="time" value={formData.send_window_end} onChange={(event) => setFormData((prev) => ({ ...prev, send_window_end: event.target.value }))} className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" />
                    </div>
                    <div>
                      <label htmlFor="followup-timezone" className="mb-1 block text-sm font-medium text-slate-700">Fuso</label>
                      <input id="followup-timezone" value={formData.timezone} onChange={(event) => setFormData((prev) => ({ ...prev, timezone: event.target.value }))} className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" placeholder="America/Sao_Paulo" />
                    </div>
                  </div>
                  <label className="flex items-start gap-3 rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-700">
                    <input type="checkbox" checked={Boolean(formData.limit_to_free_entry_window)} onChange={(event) => setFormData((prev) => ({ ...prev, limit_to_free_entry_window: event.target.checked }))} className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500" />
                    <span>
                      <strong className="block text-slate-900">Limitar follow-up à Free Entry Point</strong>
                      Quando houver uma janela de entrada por anúncio comprovada, cancelar a tentativa livre ao expirar.
                    </span>
                  </label>
                </div>
              </SectionCard>

              <SectionCard title="Não enviar quando" description="Condições simples que cancelam o follow-up.">
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                    <label className="flex items-start gap-3 rounded-xl border border-slate-200 p-3">
                      <input
                        type="checkbox"
                        checked={formData.mode === FOLLOWUP_MODE_CONVERSATION_RESUME ? true : Boolean(formData.cancel_on_inbound)}
                        disabled={formData.mode === FOLLOWUP_MODE_CONVERSATION_RESUME}
                        onChange={(event) =>
                          setFormData((prev) => ({ ...prev, cancel_on_inbound: event.target.checked }))
                        }
                        className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                      />
                      <span className="text-sm text-slate-700">
                        <strong className="block text-slate-900">A pessoa respondeu</strong>
                        {formData.mode === FOLLOWUP_MODE_CONVERSATION_RESUME
                          ? "Obrigatório neste modo."
                          : "Desmarcado por padrão no convite programado."}
                      </span>
                    </label>

                    <label className="flex items-start gap-3 rounded-xl border border-slate-200 p-3">
                      <input
                        type="checkbox"
                        checked={Boolean(formData.cancel_on_human)}
                        onChange={(event) =>
                          setFormData((prev) => ({ ...prev, cancel_on_human: event.target.checked }))
                        }
                        className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                      />
                      <span className="text-sm text-slate-700">
                        <strong className="block text-slate-900">Atendimento humano</strong>
                        Cancela quando a conversa entra em handoff.
                      </span>
                    </label>

                    <label className="flex items-start gap-3 rounded-xl border border-slate-200 p-3">
                      <input
                        type="checkbox"
                        checked={Boolean(formData.cancel_on_opt_out)}
                        onChange={(event) =>
                          setFormData((prev) => ({ ...prev, cancel_on_opt_out: event.target.checked }))
                        }
                        className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                      />
                      <span className="text-sm text-slate-700">
                        <strong className="block text-slate-900">Pedido para não receber</strong>
                        Respeita opt-out e bloqueios do contato.
                      </span>
                    </label>

                    <label className="flex items-start gap-3 rounded-xl border border-slate-200 p-3">
                      <input
                        type="checkbox"
                        checked={Boolean(formData.cancel_on_event_end)}
                        onChange={(event) =>
                          setFormData((prev) => ({ ...prev, cancel_on_event_end: event.target.checked }))
                        }
                        className="mt-1 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                      />
                      <span className="text-sm text-slate-700">
                        <strong className="block text-slate-900">Evento ou campanha terminou</strong>
                        Cancela quando a oferta não fizer mais sentido.
                      </span>
                    </label>
                  </div>

                  <div>
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-slate-900">Tags que cancelam</p>
                        <p className="text-sm text-slate-500">Selecione as tags que devem interromper o envio imediatamente.</p>
                      </div>
                      {suggestedCancelTagIds.length > 0 ? (
                        <ActionButton type="button" variant="secondary" onClick={applySuggestedCancelTags}>
                          Usar sugestões
                        </ActionButton>
                      ) : null}
                    </div>

                    <div className="max-h-56 overflow-auto rounded-xl border border-slate-200 p-3">
                      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                        {sortedTags.map((tag) => {
                          const checked = (formData.cancel_tag_ids || []).includes(tag.id);
                          return (
                            <label
                              key={tag.id}
                              className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm ${
                                checked ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-white"
                              }`}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleCancelTag(tag.id)}
                                className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                              />
                              <span className="min-w-0">
                                <span className="block truncate font-medium text-slate-900">{tag.nome}</span>
                                <span className="block text-xs text-slate-500">
                                  {String(tag.tipo || "tag").replace("_", " ")}
                                </span>
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  </div>

                  {formData.mode === FOLLOWUP_MODE_CONVERSATION_RESUME ? (
                    <InfoBanner tone="info">
                      Neste modo, o cancelamento por resposta fica travado porque faz parte da definição da regra.
                    </InfoBanner>
                  ) : null}
                </div>
              </SectionCard>

              <div className="fm-automation-dialog-actions flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={closeModal}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-60"
                >
                  <TimerReset size={15} />
                  {saving ? "Salvando..." : editingItem ? "Salvar alterações" : "Criar follow-up"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}
