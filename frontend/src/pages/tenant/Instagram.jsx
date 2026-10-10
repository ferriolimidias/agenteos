import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Clock3,
  ExternalLink,
  FileImage,
  Image as ImageIcon,
  Instagram as InstagramIcon,
  Loader2,
  MessageCircle,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  SquarePen,
  Trash2,
  X,
} from "lucide-react";

import { ActionButton, EmptyState, InfoBanner, LoadingState, MetricCard, PageHeader, SectionCard, StatusBadge } from "../../components/ui";
import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";
import { normalizeApiError } from "../../utils/errorMessages";

const EMPTY_AUTOMATION = {
  id: "",
  name: "",
  active: true,
  mediaId: "",
  keywords: "",
  publicReplyEnabled: true,
  fixedReply: "",
  privateReplyEnabled: false,
  privateReplyMessageType: "text",
  privateMessage: "",
  privateButtons: [],
};

const EMPTY_PRIVATE_BUTTON = { type: "web_url", title: "", url: "", payload: "" };

function text(value) {
  return String(value ?? "").trim();
}

function formatDateTime(value) {
  if (!value) return "Não informada";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function connectionId(connection) {
  return text(connection?.connection_id || connection?.id);
}

function instagramId(connection) {
  return text(connection?.instagram_professional_account_id || connection?.instagram_business_account_id || connection?.instagram_account_id);
}

function connectionLabel(connection) {
  return text(connection?.name) || (text(connection?.instagram_username) ? `@${connection.instagram_username}` : "Conexão Instagram");
}

function statusTone(status) {
  const normalized = text(status).toLowerCase();
  if (["connected", "active", "valid"].includes(normalized)) return "active";
  if (["error", "invalid", "expired"].includes(normalized)) return "danger";
  return "warning";
}

function isAllowedButtonUrl(value) {
  try {
    const parsed = new URL(text(value));
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function normalizeConnections(data) {
  if (Array.isArray(data?.connections)) return data.connections;
  return Array.isArray(data) ? data : [];
}

function normalizeItems(data, key) {
  if (key && Array.isArray(data?.[key])) return data[key];
  if (Array.isArray(data?.items)) return data.items;
  return Array.isArray(data) ? data : [];
}

function hasItemsPayload(data, key) {
  return Boolean((key && Array.isArray(data?.[key])) || Array.isArray(data?.items) || Array.isArray(data));
}

function nextMediaCursor(data) {
  return text(data?.paging?.after || data?.paging?.next_cursor || data?.after);
}

function automationFromApi(item) {
  const privateConfig = item?.private_reply_config && typeof item.private_reply_config === "object" ? item.private_reply_config : null;
  const messageType = privateConfig?.message_type === "button_template" ? "button_template" : "text";
  const buttons = Array.isArray(privateConfig?.buttons) ? privateConfig.buttons : [];
  return {
    id: text(item?.id),
    name: text(item?.name),
    active: item?.active !== false,
    mediaId: text(item?.media_id),
    keywords: Array.isArray(item?.keywords) ? item.keywords.join(", ") : text(item?.keywords),
    publicReplyEnabled: item?.public_reply_enabled !== false,
    fixedReply: text(item?.fixed_reply),
    privateReplyEnabled: Boolean(item?.private_reply_enabled),
    privateReplyMessageType: messageType,
    privateMessage: text(privateConfig?.text || item?.private_message),
    privateButtons: buttons.slice(0, 3).map((button) => ({
      type: button?.type === "postback" ? "postback" : "web_url",
      title: text(button?.title),
      url: text(button?.url),
      payload: text(button?.payload),
    })),
  };
}

function Notice({ notice, onClose }) {
  if (!notice) return null;
  const tone = notice.type === "success" ? "success" : notice.type === "danger" ? "danger" : "warning";
  return (
    <div role={notice.type === "danger" ? "alert" : "status"} aria-live="polite" className="relative">
      <InfoBanner tone={tone} icon={notice.type === "success" ? CheckCircle2 : AlertCircle}>{notice.message}</InfoBanner>
      <button type="button" onClick={onClose} aria-label="Fechar aviso" className="absolute right-2 top-2 rounded-md p-1 text-current hover:bg-white/50">
        <X size={15} />
      </button>
    </div>
  );
}

function ConnectionCard({ connection, selected, busy, onSelect, onReconfigure, onRevalidate, onRemove }) {
  const username = text(connection?.instagram_username);
  const permissions = Array.isArray(connection?.permissions) ? connection.permissions : [];
  const pending = text(connection?.configuration_state).toLowerCase() === "pending_reconfiguration";
  return (
    <article className={`min-w-0 rounded-xl border p-4 shadow-sm ${selected ? "border-indigo-300 bg-indigo-50/50" : "border-slate-200 bg-white"}`}>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="break-words text-sm font-semibold text-slate-950">{connectionLabel(connection)}</h3>
            <StatusBadge tone={statusTone(connection?.status)} icon={ShieldCheck}>{text(connection?.status) || "Status desconhecido"}</StatusBadge>
            {selected ? <StatusBadge tone="info" icon={Check}>Selecionada</StatusBadge> : null}
            {pending ? <StatusBadge tone="warning">Pendente de reconfiguração</StatusBadge> : null}
          </div>
          <dl className="mt-3 grid min-w-0 gap-2 text-sm text-slate-600 sm:grid-cols-2">
            <div><dt className="font-semibold text-slate-700">Instagram ID</dt><dd className="break-all">{instagramId(connection) || "—"}</dd></div>
            <div><dt className="font-semibold text-slate-700">Usuário</dt><dd className="break-words">{username ? `@${username}` : "—"}</dd></div>
            <div><dt className="font-semibold text-slate-700">Validade do token</dt><dd>{formatDateTime(connection?.token_expires_at)}</dd></div>
          </dl>
          <div className="mt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Permissões</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {permissions.length ? permissions.map((permission) => <StatusBadge key={permission} tone="default">{permission}</StatusBadge>) : <span className="text-sm text-slate-500">Não informadas</span>}
            </div>
          </div>
          {pending ? <p className="mt-3 text-sm font-medium text-amber-700">Indisponível até concluir a validação.</p> : null}
          <p className="mt-3 text-xs text-slate-500">Última validação: {formatDateTime(connection?.last_validated_at)}</p>
          {connection?.last_error ? <p className="mt-2 break-words text-sm text-red-700">{text(connection.last_error)}</p> : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <ActionButton type="button" variant={selected ? "primary" : "secondary"} icon={selected ? Check : InstagramIcon} aria-pressed={selected} onClick={() => onSelect(connection)} disabled={busy}>
            {selected ? "Selecionada" : "Usar conexão"}
          </ActionButton>
          {pending ? <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={() => onReconfigure(connection)} disabled={busy}>Reconfigurar</ActionButton> : <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={() => onRevalidate(connection)} disabled={busy}>Revalidar</ActionButton>}
          <ActionButton type="button" variant="danger" icon={Trash2} onClick={() => onRemove(connection)} disabled={busy}>Remover</ActionButton>
        </div>
      </div>
    </article>
  );
}

function MediaCard({ item, selected, onSelect }) {
  const image = text(item?.thumbnail_url || item?.media_url);
  return (
    <button type="button" aria-pressed={selected} onClick={() => onSelect(item)} className={`min-w-0 overflow-hidden rounded-xl border p-3 text-left shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 ${selected ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-white hover:border-slate-300"}`}>
      <div className="flex gap-3">
        <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-slate-100 text-slate-400">
          {image ? <img src={image} alt="" className="h-full w-full object-cover" /> : <FileImage size={20} />}
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap gap-2"><StatusBadge tone={selected ? "info" : "default"}>{selected ? "Selecionada" : text(item?.media_type) || "Publicação"}</StatusBadge></div>
          <p className="mt-2 line-clamp-2 break-words text-sm font-semibold text-slate-900">{text(item?.caption_preview || item?.caption) || "Sem legenda"}</p>
          <p className="mt-1 text-xs text-slate-500">{formatDateTime(item?.timestamp)}</p>
        </div>
      </div>
    </button>
  );
}

export default function Instagram() {
  const empresaId = getActiveEmpresaId();
  const abortRef = useRef(null);
  const noticeTimerRef = useRef(null);
  const resourcesRequestRef = useRef(0);
  const [notice, setNotice] = useState(null);
  const [connections, setConnections] = useState([]);
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [connectionBusy, setConnectionBusy] = useState(false);
  const [accessToken, setAccessToken] = useState("");
  const [professionalAccountId, setProfessionalAccountId] = useState("");
  const [connectionName, setConnectionName] = useState("");
  const [editingConnectionId, setEditingConnectionId] = useState("");
  const [connectionMode, setConnectionMode] = useState("new");
  const [media, setMedia] = useState([]);
  const [mediaLoading, setMediaLoading] = useState(false);
  const [mediaCursor, setMediaCursor] = useState("");
  const [selectedMediaId, setSelectedMediaId] = useState("");
  const [automations, setAutomations] = useState([]);
  const [automationsLoading, setAutomationsLoading] = useState(false);
  const [automationForm, setAutomationForm] = useState(EMPTY_AUTOMATION);
  const [automationEditorOpen, setAutomationEditorOpen] = useState(false);
  const [savingAutomation, setSavingAutomation] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const selectedConnection = useMemo(
    () => connections.find((item) => connectionId(item) === selectedConnectionId) || null,
    [connections, selectedConnectionId],
  );
  const usableConnection = Boolean(selectedConnection && text(selectedConnection.status).toLowerCase() === "connected");

  const showNotice = useCallback((type, message) => {
    setNotice({ type, message });
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), 5000);
  }, []);

  const clearConnectionEditor = useCallback(() => {
    setConnectionMode("new");
    setEditingConnectionId("");
    setConnectionName("");
    setProfessionalAccountId("");
    setAccessToken("");
  }, []);

  const loadConnections = useCallback(async () => {
    if (!empresaId) return;
    setLoadingConnections(true);
    try {
      const response = await api.get(`/empresas/${empresaId}/instagram/connections`, { signal: abortRef.current?.signal });
      const items = normalizeConnections(response.data);
      setConnections(items);
      setSelectedConnectionId((current) => items.some((item) => connectionId(item) === current) ? current : connectionId(items[0]));
    } catch (error) {
      if (error?.name !== "CanceledError") showNotice("danger", normalizeApiError(error, "Não foi possível carregar as conexões."));
    } finally {
      setLoadingConnections(false);
    }
  }, [empresaId, showNotice]);

  const loadResources = useCallback(async (id, allowGraph, options = {}) => {
    const requestId = resourcesRequestRef.current + 1;
    resourcesRequestRef.current = requestId;
    if (!empresaId || !id) {
      setMedia([]);
      setAutomations([]);
      setHistory([]);
      return;
    }
    setMediaLoading(true);
    setAutomationsLoading(true);
    setHistoryLoading(true);
    try {
      const [mediaResponse, automationsResponse, historyResponse] = await Promise.all([
        allowGraph
          ? api.get(`/empresas/${empresaId}/instagram/media`, { params: { connection_id: id, limit: 25 }, signal: abortRef.current?.signal })
          : Promise.resolve({ data: { media: [], paging: {} } }),
        api.get(`/empresas/${empresaId}/instagram/automations`, { params: { connection_id: id }, signal: abortRef.current?.signal }),
        api.get(`/empresas/${empresaId}/instagram/history`, { params: { connection_id: id, limit: 50 }, signal: abortRef.current?.signal }),
      ]);
      if (resourcesRequestRef.current !== requestId) return;
      if (hasItemsPayload(mediaResponse.data, "media")) {
        setMedia(normalizeItems(mediaResponse.data, "media"));
        setMediaCursor(nextMediaCursor(mediaResponse.data));
      } else if (options.preserveMediaOnInvalid !== true) {
        setMedia([]);
        setMediaCursor("");
      } else {
        showNotice("danger", "A resposta de publicações veio em formato inesperado. A lista atual foi preservada.");
      }
      setAutomations(normalizeItems(automationsResponse.data, "automations"));
      setHistory(normalizeItems(historyResponse.data, "history"));
    } catch (error) {
      if (resourcesRequestRef.current === requestId && error?.name !== "CanceledError") showNotice("danger", normalizeApiError(error, "Não foi possível carregar os dados desta conexão."));
    } finally {
      if (resourcesRequestRef.current === requestId) {
        setMediaLoading(false);
        setAutomationsLoading(false);
        setHistoryLoading(false);
      }
    }
  }, [empresaId, showNotice]);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    void loadConnections();
    return () => controller.abort();
  }, [loadConnections]);

  useEffect(() => {
    if (connectionMode === "reconfigure" && editingConnectionId && !connections.some((item) => connectionId(item) === editingConnectionId)) {
      clearConnectionEditor();
    }
  }, [connections, connectionMode, editingConnectionId, clearConnectionEditor]);

  useEffect(() => {
    setSelectedMediaId("");
    setAutomationEditorOpen(false);
    setAutomationForm(EMPTY_AUTOMATION);
    void loadResources(selectedConnectionId, usableConnection);
  }, [loadResources, selectedConnectionId, usableConnection]);

  const automationSummary = useMemo(() => {
    const active = automations.filter((item) => item?.active !== false).length;
    return {
      total: automations.length,
      active,
      inactive: automations.length - active,
      suspended: selectedConnection && text(selectedConnection.configuration_state).toLowerCase() === "pending_reconfiguration" ? automations.length : 0,
    };
  }, [automations, selectedConnection]);

  useEffect(() => () => {
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current);
  }, []);

  const updateAccessToken = (value) => {
    setAccessToken(value);
  };

  const saveConnection = async () => {
    if (!text(professionalAccountId) || !text(accessToken)) {
      showNotice("warning", "Informe o Instagram ID e o Instagram User Access Token antes de salvar.");
      return;
    }
    setConnectionBusy(true);
    try {
      const payload = {
        instagram_id: text(professionalAccountId),
        instagram_user_access_token: text(accessToken),
        name: text(connectionName) || undefined,
      };
      const response = connectionMode === "reconfigure" && editingConnectionId
        ? await api.post(`/empresas/${empresaId}/instagram/connection/${editingConnectionId}/reconfigure`, payload)
        : await api.post(`/empresas/${empresaId}/instagram/connection`, payload);
      const saved = response.data?.connection;
      setAccessToken("");
      setProfessionalAccountId("");
      setConnectionName("");
      setEditingConnectionId("");
      setConnectionMode("new");
      await loadConnections();
      if (saved) setSelectedConnectionId(connectionId(saved));
      showNotice("success", "Conexão salva.");
    } catch (error) {
      showNotice("danger", normalizeApiError(error, "Não foi possível salvar a conexão."));
    } finally {
      setConnectionBusy(false);
    }
  };

  const reconfigureConnection = (item) => {
    setConnectionMode("reconfigure");
    setEditingConnectionId(connectionId(item));
    setConnectionName(text(item?.name));
    setProfessionalAccountId(instagramId(item));
    setAccessToken("");
    showNotice("warning", "Modo de reconfiguração ativo. Informe um novo token; o token anterior não será reutilizado.");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const cancelReconfigure = () => {
    clearConnectionEditor();
  };

  const revalidateConnection = async (item) => {
    const id = connectionId(item);
    setConnectionBusy(true);
    try {
      const response = await api.post(`/empresas/${empresaId}/instagram/connection/${id}/revalidate`);
      const updated = response.data?.connection;
      if (updated) setConnections((current) => current.map((connection) => connectionId(connection) === id ? updated : connection));
      showNotice("success", "Conexão revalidada.");
    } catch (error) {
      showNotice("danger", normalizeApiError(error, "Não foi possível revalidar a conexão."));
    } finally {
      setConnectionBusy(false);
    }
  };

  const removeConnection = async (item) => {
    const id = connectionId(item);
    if (!window.confirm(`Remover a conexão “${connectionLabel(item)}”?`)) return;
    setConnectionBusy(true);
    try {
      await api.delete(`/empresas/${empresaId}/instagram/connection/${id}`);
      setConnections((current) => current.filter((connection) => connectionId(connection) !== id));
      if (selectedConnectionId === id) setSelectedConnectionId("");
      if (editingConnectionId === id) clearConnectionEditor();
      showNotice("success", "Conexão removida.");
    } catch (error) {
      showNotice("danger", normalizeApiError(error, "Não foi possível remover a conexão."));
    } finally {
      setConnectionBusy(false);
    }
  };

  const loadMoreMedia = async () => {
    if (!mediaCursor || !selectedConnectionId) return;
    setMediaLoading(true);
    try {
      const response = await api.get(`/empresas/${empresaId}/instagram/media`, { params: { connection_id: selectedConnectionId, after: mediaCursor, limit: 25 } });
      setMedia((current) => [...current, ...normalizeItems(response.data, "media")]);
      setMediaCursor(nextMediaCursor(response.data));
    } catch (error) {
      showNotice("danger", normalizeApiError(error, "Não foi possível carregar mais publicações."));
    } finally {
      setMediaLoading(false);
    }
  };

  const editAutomation = (item) => {
    const form = automationFromApi(item);
    setAutomationForm(form);
    setSelectedMediaId(form.mediaId);
    setAutomationEditorOpen(true);
  };

  const updatePrivateButton = (index, patch) => {
    setAutomationForm((current) => ({
      ...current,
      privateButtons: current.privateButtons.map((button, buttonIndex) => buttonIndex === index ? { ...button, ...patch } : button),
    }));
  };

  const addPrivateButton = () => {
    setAutomationForm((current) => current.privateButtons.length >= 3 ? current : ({
      ...current,
      privateButtons: [...current.privateButtons, { ...EMPTY_PRIVATE_BUTTON }],
    }));
  };

  const removePrivateButton = (index) => {
    setAutomationForm((current) => ({
      ...current,
      privateButtons: current.privateButtons.filter((_, buttonIndex) => buttonIndex !== index),
    }));
  };

  const saveAutomation = async () => {
    if (!text(automationForm.name)) {
      showNotice("warning", "Informe um nome para a automação.");
      return;
    }
    if (!automationForm.publicReplyEnabled && !automationForm.privateReplyEnabled) {
      showNotice("warning", "Selecione ao menos uma ação para a automação.");
      return;
    }
    if (automationForm.publicReplyEnabled && !text(automationForm.fixedReply)) {
      showNotice("warning", "Informe a resposta pública.");
      return;
    }
    if (automationForm.privateReplyEnabled && !text(automationForm.privateMessage)) {
      showNotice("warning", "Informe a mensagem privada.");
      return;
    }
    if (automationForm.privateReplyEnabled && automationForm.privateReplyMessageType === "button_template") {
      if (!automationForm.privateButtons.length) {
        showNotice("warning", "Adicione ao menos um botão para a mensagem privada.");
        return;
      }
      if (automationForm.privateButtons.length > 3) {
        showNotice("warning", "A mensagem privada permite no máximo 3 botões.");
        return;
      }
      for (const button of automationForm.privateButtons) {
        if (!text(button.title)) {
          showNotice("warning", "Informe o texto de todos os botões.");
          return;
        }
        if (button.type === "web_url" && !isAllowedButtonUrl(button.url)) {
          showNotice("warning", "Botões de link exigem URL válida começando com http:// ou https://.");
          return;
        }
        if (button.type === "postback" && !text(button.payload)) {
          showNotice("warning", "Informe o payload dos botões postback.");
          return;
        }
      }
    }
    const privateReplyConfig = automationForm.privateReplyEnabled ? {
      message_type: automationForm.privateReplyMessageType === "button_template" ? "button_template" : "text",
      text: text(automationForm.privateMessage),
      buttons: automationForm.privateReplyMessageType === "button_template"
        ? automationForm.privateButtons.slice(0, 3).map((button) => ({
          type: button.type === "postback" ? "postback" : "web_url",
          title: text(button.title),
          ...(button.type === "postback" ? { payload: text(button.payload) } : { url: text(button.url) }),
        }))
        : [],
    } : null;
    const payload = {
      connection_id: selectedConnectionId,
      name: text(automationForm.name),
      active: Boolean(automationForm.active),
      media_id: selectedMediaId || null,
      keywords: automationForm.keywords.split(",").map(text).filter(Boolean),
      public_reply_enabled: Boolean(automationForm.publicReplyEnabled),
      fixed_reply: automationForm.publicReplyEnabled ? text(automationForm.fixedReply) : null,
      private_reply_enabled: Boolean(automationForm.privateReplyEnabled),
      private_message: automationForm.privateReplyEnabled ? text(automationForm.privateMessage) : null,
      private_reply_config: privateReplyConfig,
    };
    setSavingAutomation(true);
    try {
      if (automationForm.id) await api.put(`/empresas/${empresaId}/instagram/automations/${automationForm.id}`, payload);
      else await api.post(`/empresas/${empresaId}/instagram/automations`, payload);
      const response = await api.get(`/empresas/${empresaId}/instagram/automations`, { params: { connection_id: selectedConnectionId } });
      setAutomations(normalizeItems(response.data, "automations"));
      setAutomationEditorOpen(false);
      setAutomationForm(EMPTY_AUTOMATION);
      showNotice("success", automationForm.id ? "Automação atualizada." : "Automação criada.");
    } catch (error) {
      showNotice("danger", normalizeApiError(error, "Não foi possível salvar a automação."));
    } finally {
      setSavingAutomation(false);
    }
  };

  const deleteAutomation = async (item) => {
    if (!window.confirm(`Excluir a automação “${text(item?.name) || "sem nome"}”?`)) return;
    try {
      await api.delete(`/empresas/${empresaId}/instagram/automations/${item.id}`);
      setAutomations((current) => current.filter((automation) => text(automation.id) !== text(item.id)));
      showNotice("success", "Automação excluída.");
    } catch (error) {
      showNotice("danger", normalizeApiError(error, "Não foi possível excluir a automação."));
    }
  };

  if (!empresaId) return <InfoBanner tone="warning">Empresa não identificada. Faça login novamente.</InfoBanner>;

  return (
    <div className="min-w-0 space-y-6" translate="no">
      <PageHeader title="Instagram" eyebrow="Canais" description="Conecte ou reconfigure uma conta informando manualmente o Instagram ID e o Instagram User Access Token." icon={InstagramIcon} />
      <Notice notice={notice} onClose={() => setNotice(null)} />

      <div className="grid gap-4 sm:grid-cols-3">
        <MetricCard label="Conexões" value={connections.length} helper="Da empresa" icon={InstagramIcon} tone={connections.length ? "active" : "default"} />
        <MetricCard label="Automações" value={selectedConnection ? automationSummary.total : "—"} helper={selectedConnection ? `${automationSummary.active} ativas · ${automationSummary.inactive} inativas${automationSummary.suspended ? ` · ${automationSummary.suspended} suspensas` : ""}` : "Escolha uma conexão"} icon={Sparkles} tone={usableConnection ? "active" : "default"} />
        <MetricCard label="Execuções" value={selectedConnection ? history.length : "—"} helper="Últimas 50 da conexão" icon={Clock3} tone="info" />
      </div>

      <SectionCard title={connectionMode === "reconfigure" ? "Reconfigurar conexão" : "Nova conexão"} description="Informe o Instagram ID e o Instagram User Access Token. O token anterior nunca volta a ser exibido." icon={connectionMode === "reconfigure" ? RefreshCw : Plus} className="min-w-0 overflow-hidden">
        <form onSubmit={(event) => { event.preventDefault(); void saveConnection(); }} className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <label className="space-y-2"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Nome da conexão <span className="font-normal normal-case">(opcional)</span></span><input value={connectionName} onChange={(event) => setConnectionName(event.target.value)} maxLength={160} autoComplete="off" placeholder="Ex.: Loja principal" className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500" /></label>
            <label className="space-y-2"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Instagram ID</span><input value={professionalAccountId} onChange={(event) => setProfessionalAccountId(event.target.value)} required inputMode="numeric" autoComplete="off" placeholder="Ex.: 2809…" className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500" /><span className="block text-xs text-slate-500">Informe o Instagram ID utilizado com o seu Instagram User Access Token.</span></label>
            <label className="space-y-2"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Instagram User Access Token</span><input value={accessToken} onChange={(event) => updateAccessToken(event.target.value)} required type="password" autoComplete="new-password" placeholder="Cole o token de acesso do Instagram" className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500" /><span className="block text-xs text-slate-500">Enviado somente para validação e armazenamento protegido.</span></label>
          </div>
          <div className="flex flex-wrap gap-2">
            <ActionButton type="submit" icon={connectionBusy ? Loader2 : Save} disabled={connectionBusy || !text(accessToken) || !text(professionalAccountId)}>{connectionMode === "reconfigure" ? "Salvar reconfiguração" : "Salvar conexão"}</ActionButton>
            {connectionMode === "reconfigure" ? <ActionButton type="button" variant="secondary" icon={X} onClick={cancelReconfigure} disabled={connectionBusy}>Cancelar</ActionButton> : null}
          </div>
        </form>
      </SectionCard>

      <SectionCard title="Conexões da empresa" description="Selecione qual conexão será usada nas publicações, automações e no histórico abaixo." icon={InstagramIcon} actions={<ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={loadConnections} disabled={loadingConnections || connectionBusy}>Atualizar</ActionButton>} className="min-w-0 overflow-hidden">
        {loadingConnections ? <LoadingState label="Carregando conexões…" /> : connections.length ? <div className="space-y-3">{connections.map((item) => <ConnectionCard key={connectionId(item)} connection={item} selected={connectionId(item) === selectedConnectionId} busy={connectionBusy} onSelect={(connection) => setSelectedConnectionId(connectionId(connection))} onReconfigure={reconfigureConnection} onRevalidate={revalidateConnection} onRemove={removeConnection} />)}</div> : <EmptyState title="Nenhuma conexão salva" description="Use o formulário acima para salvar a primeira conexão." icon={InstagramIcon} />}
      </SectionCard>

      {!selectedConnection ? <InfoBanner tone="info" icon={ShieldCheck}>Selecione uma conexão para carregar publicações, automações e histórico.</InfoBanner> : !usableConnection ? <InfoBanner tone="warning" icon={AlertCircle}>Esta conexão não está ativa. Revalide-a antes de criar automações.</InfoBanner> : null}

      <SectionCard title="Publicações" description="Publicações da conta profissional do Instagram selecionada." icon={ImageIcon} actions={selectedConnection ? <ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={() => loadResources(selectedConnectionId, usableConnection, { preserveMediaOnInvalid: true })} disabled={mediaLoading}>Atualizar</ActionButton> : null} className="min-w-0 overflow-hidden">
        {mediaLoading && !media.length ? <LoadingState label="Carregando publicações…" /> : !selectedConnection ? <EmptyState title="Escolha uma conexão" description="As publicações são sempre consultadas por conexão." icon={ImageIcon} /> : media.length ? <><div className="grid gap-3 lg:grid-cols-2">{media.map((item) => <MediaCard key={item.id} item={item} selected={text(item.id) === selectedMediaId} onSelect={(selected) => setSelectedMediaId((current) => current === text(selected.id) ? "" : text(selected.id))} />)}</div>{mediaCursor ? <div className="mt-4"><ActionButton type="button" variant="secondary" onClick={loadMoreMedia} disabled={mediaLoading}>{mediaLoading ? "Carregando…" : "Carregar mais"}</ActionButton></div> : null}</> : <EmptyState title="Nenhuma publicação encontrada" description="Atualize a lista ou revalide a conexão." icon={ImageIcon} />}
      </SectionCard>

      <SectionCard title="Automações salvas" description="Regras exclusivas da conexão selecionada." icon={Sparkles} actions={<ActionButton type="button" icon={Plus} disabled={!usableConnection} onClick={() => { setAutomationForm(EMPTY_AUTOMATION); setSelectedMediaId(""); setAutomationEditorOpen(true); }}>Nova automação</ActionButton>} className="min-w-0 overflow-hidden">
        {automationEditorOpen ? (
          <div className="mb-5 rounded-xl border border-indigo-200 bg-indigo-50/40 p-4">
            <div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-slate-950">{automationForm.id ? "Editar automação" : "Nova automação"}</h3><p className="mt-1 text-sm text-slate-500">A publicação selecionada acima é opcional; sem ela, a regra vale para todas.</p></div><button type="button" onClick={() => setAutomationEditorOpen(false)} aria-label="Fechar editor de automação" className="rounded-lg p-2 text-slate-500 hover:bg-white"><X size={17} /></button></div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <label className="space-y-2"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Nome</span><input value={automationForm.name} onChange={(event) => setAutomationForm((current) => ({ ...current, name: event.target.value }))} className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500" /></label>
              <label className="space-y-2"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Palavras-chave, separadas por vírgula</span><input value={automationForm.keywords} onChange={(event) => setAutomationForm((current) => ({ ...current, keywords: event.target.value }))} placeholder="preço, quero, informações" className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-indigo-500" /></label>
              <label className="space-y-2 md:col-span-2"><span className="flex items-center gap-2 text-sm font-semibold text-slate-700"><input type="checkbox" checked={automationForm.publicReplyEnabled} onChange={(event) => setAutomationForm((current) => ({ ...current, publicReplyEnabled: event.target.checked }))} />Responder publicamente</span><textarea value={automationForm.fixedReply} onChange={(event) => setAutomationForm((current) => ({ ...current, fixedReply: event.target.value }))} disabled={!automationForm.publicReplyEnabled} rows={3} placeholder="Resposta ao comentário" className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none disabled:bg-slate-100" /></label>
              <div className="space-y-3 md:col-span-2">
                <label className="flex items-center gap-2 text-sm font-semibold text-slate-700"><input type="checkbox" checked={automationForm.privateReplyEnabled} onChange={(event) => setAutomationForm((current) => ({ ...current, privateReplyEnabled: event.target.checked }))} />Enviar mensagem privada</label>
                <div className="flex flex-wrap gap-2">
                  {[["text", "Somente texto"], ["button_template", "Texto com botões"]].map(([value, label]) => (
                    <button key={value} type="button" disabled={!automationForm.privateReplyEnabled} onClick={() => setAutomationForm((current) => ({ ...current, privateReplyMessageType: value, privateButtons: value === "button_template" && !current.privateButtons.length ? [{ ...EMPTY_PRIVATE_BUTTON }] : current.privateButtons }))} className={`rounded-lg border px-3 py-2 text-sm font-semibold ${automationForm.privateReplyMessageType === value ? "border-indigo-500 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700"} disabled:cursor-not-allowed disabled:opacity-60`}>{label}</button>
                  ))}
                </div>
                <textarea value={automationForm.privateMessage} onChange={(event) => setAutomationForm((current) => ({ ...current, privateMessage: event.target.value }))} disabled={!automationForm.privateReplyEnabled} rows={3} placeholder="Mensagem privada" className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none disabled:bg-slate-100" />
                {automationForm.privateReplyEnabled && automationForm.privateReplyMessageType === "button_template" ? (
                  <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Botões</p>
                      <ActionButton type="button" variant="secondary" icon={Plus} onClick={addPrivateButton} disabled={automationForm.privateButtons.length >= 3}>Adicionar botão</ActionButton>
                    </div>
                    {automationForm.privateButtons.map((button, index) => (
                      <div key={index} className="grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 md:grid-cols-[1fr_140px_1.4fr_auto]">
                        <input value={button.title} onChange={(event) => updatePrivateButton(index, { title: event.target.value })} maxLength={80} placeholder="Texto do botão" className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500" />
                        <select value={button.type} onChange={(event) => updatePrivateButton(index, { type: event.target.value })} className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500">
                          <option value="web_url">web_url</option>
                          <option value="postback">postback</option>
                        </select>
                        {button.type === "postback" ? (
                          <input value={button.payload} onChange={(event) => updatePrivateButton(index, { payload: event.target.value })} maxLength={1000} placeholder="Payload postback" className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500" />
                        ) : (
                          <input value={button.url} onChange={(event) => updatePrivateButton(index, { url: event.target.value })} maxLength={2000} placeholder="https://exemplo.com" className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500" />
                        )}
                        <button type="button" onClick={() => removePrivateButton(index)} aria-label="Remover botão" className="rounded-lg p-2 text-slate-500 hover:bg-white"><Trash2 size={16} /></button>
                      </div>
                    ))}
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Prévia</p>
                      <p className="break-words whitespace-pre-wrap text-sm text-slate-700">{text(automationForm.privateMessage) || "Mensagem privada"}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {automationForm.privateButtons.map((button, index) => <span key={index} className="inline-flex max-w-full items-center gap-1 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-semibold text-indigo-700"><ExternalLink size={14} />{text(button.title) || "Botão"}</span>)}
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
            <div className="mt-4 flex flex-wrap gap-2"><ActionButton type="button" icon={Save} onClick={saveAutomation} disabled={savingAutomation}>{savingAutomation ? "Salvando…" : "Salvar automação"}</ActionButton><ActionButton type="button" variant="secondary" onClick={() => setAutomationEditorOpen(false)}>Cancelar</ActionButton></div>
          </div>
        ) : null}
        {automationsLoading ? <LoadingState label="Carregando automações…" /> : !selectedConnection ? <EmptyState title="Escolha uma conexão" description="As automações são separadas por conexão." icon={Sparkles} /> : automations.length ? <div className="space-y-3">{automations.map((item) => { const suspended = text(selectedConnection.configuration_state).toLowerCase() === "pending_reconfiguration"; return <article key={item.id} className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between"><div className="min-w-0"><div className="flex flex-wrap gap-2"><h3 className="break-words text-sm font-semibold text-slate-950">{text(item.name) || "Automação sem nome"}</h3><StatusBadge tone={suspended ? "warning" : item.active === false ? "default" : "active"}>{suspended ? "Suspensa por conexão pendente" : item.active === false ? "Pausada" : "Ativa"}</StatusBadge>{item.media_id ? <StatusBadge tone="info">Uma publicação</StatusBadge> : <StatusBadge tone="default">Todas as publicações</StatusBadge>}</div><p className="mt-2 break-words text-sm text-slate-600">{Array.isArray(item.keywords) && item.keywords.length ? item.keywords.join(" · ") : "Qualquer comentário"}</p></div><div className="flex gap-2"><ActionButton type="button" variant="secondary" icon={SquarePen} onClick={() => editAutomation(item)} disabled={suspended}>Editar</ActionButton><ActionButton type="button" variant="danger" icon={Trash2} onClick={() => deleteAutomation(item)} disabled={suspended}>Excluir</ActionButton></div></div></article>; })}</div> : <EmptyState title="Nenhuma automação nesta conexão" description="Crie uma regra para comentários e mensagens." icon={Sparkles} />}
      </SectionCard>

      <SectionCard title="Histórico" description="Últimas execuções da conexão selecionada." icon={Clock3} className="min-w-0 overflow-hidden">
        {historyLoading ? <LoadingState label="Carregando histórico…" /> : !selectedConnection ? <EmptyState title="Escolha uma conexão" description="O histórico é consultado por conexão." icon={Clock3} /> : history.length ? <div className="space-y-3">{history.map((item) => <article key={item.id} className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center gap-2"><StatusBadge tone={statusTone(item.status)}>{text(item.status) || "Pendente"}</StatusBadge><span className="text-xs text-slate-500">{formatDateTime(item.created_at)}</span></div><p className="mt-3 break-words text-sm text-slate-700"><span className="font-semibold">Comentário:</span> {text(item.comment_text || item.comment) || "—"}</p><div className="mt-3 grid gap-3 md:grid-cols-2"><div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Resposta pública</p><p className="mt-1 break-words whitespace-pre-wrap">{text(item.public_reply) || "—"}</p></div><div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Mensagem privada</p><p className="mt-1 break-words whitespace-pre-wrap">{text(item.private_reply) || "—"}</p></div></div>{item?.publication?.permalink ? <a href={item.publication.permalink} target="_blank" rel="noreferrer noopener" className="mt-3 inline-flex items-center gap-1 text-sm font-semibold text-indigo-700 hover:text-indigo-900"><ExternalLink size={15} />Abrir publicação</a> : null}</article>)}</div> : <EmptyState title="Nenhuma execução encontrada" description="As execuções aparecerão aqui quando as automações forem acionadas." icon={MessageCircle} />}
      </SectionCard>
    </div>
  );
}
