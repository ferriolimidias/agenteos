import React, { useCallback, useState, useEffect, useMemo, useRef, useLayoutEffect } from "react";
import { AlertCircle, ArrowLeft, ArrowRightLeft, Check, FileAudio, FileImage, FileText, FileVideo, MessageSquare, Mic, Paperclip, RefreshCw, Search, Send, Trash2, X } from "lucide-react";
import api from "../../services/api";
import { Link, useLocation } from "react-router-dom";
import { allowlistedFunnelFilters, journeyLeadFromSearch } from "../../utils/commercialFunnel";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";
import { normalizeLeadTags } from "../../utils/leadTags";
import { conversationKey, definedLeadPatch, leadMatches, mergeLeadSnapshot, mergeMessageSnapshot, messageIdentity, patchLeadFromMessage, upsertMessage } from "../../utils/inboxRealtime";
import MessageList from "../../components/MessageList";
import ContactAvatar from "../../components/ContactAvatar";
import LeadTagsEditor from "../../components/LeadTagsEditor";
import CommercialJourneyDialog from "../../components/CommercialJourneyDialog";
import { ActionButton, EmptyState, InfoBanner, LoadingState } from "../../components/ui";
import "../../styles/inbox-ux04.css";

const STATUS_ATENDIMENTO_ABERTOS = new Set(["aberto", "aguardando_humano", "manual"]);
const AI_STATE_UI = {
  allowed: {
    label: "IA autorizada",
    description: "A IA esta pronta para responder novas mensagens.",
  },
  processing: {
    label: "IA processando",
    description: "A IA esta preparando uma resposta.",
  },
  queued: {
    label: "IA autorizada",
    description: "A IA foi autorizada e entrou na fila de processamento.",
  },
  ai_manual_disabled: {
    label: "IA desligada nesta conversa",
    description: "Ative o controle de IA para permitir respostas automáticas.",
  },
  failed_start: {
    label: "Falha ao gerar resposta",
    description: "A IA nao conseguiu concluir esta mensagem. O atendimento automatico continua configurado.",
  },
  failed_model: {
    label: "Falha ao gerar resposta",
    description: "A IA nao conseguiu concluir esta mensagem. O atendimento automatico continua configurado.",
  },
  guard_blocked: {
    label: "Resposta fiscal precisa de revisão",
    description: "A resposta não passou pelas validações necessárias e não foi enviada.",
  },
  completed: {
    label: "Resposta enviada",
    description: "A resposta automatica foi processada pelo provider configurado.",
  },
  tenant_llm_provider_error: {
    label: "Falha ao gerar resposta",
    description: "A IA não conseguiu concluir esta mensagem. O atendimento automático continua configurado.",
  },
  tenant_llm_empty_response: {
    label: "Falha ao gerar resposta",
    description: "A IA não conseguiu concluir esta mensagem. O atendimento automático continua configurado.",
  },
  tenant_tool_planner_error: {
    label: "Falha ao gerar resposta",
    description: "A IA não conseguiu concluir esta mensagem. O atendimento automático continua configurado.",
  },
  fiscal_validation_failed: {
    label: "Resposta fiscal precisa de revisão",
    description: "A resposta não passou pelas validações necessárias e não foi enviada.",
  },
};

const translateAiState = (state) => {
  const raw = state && typeof state === "object" ? state : {};
  const processingKey = String(raw.processing_state || "").trim();
  const reasonKey = String(raw.reason_code || raw.reason || "").trim();
  const byProcessing = AI_STATE_UI[processingKey];
  const byReason = AI_STATE_UI[reasonKey];
  const fallbackLabel = String(raw.label || raw.reason_label || reasonKey || "Verificando IA").trim();
  const fallbackDescription = String(raw.description || raw.processing_description || fallbackLabel).trim();
  const label = String(raw.processing_label || byProcessing?.label || byReason?.label || fallbackLabel).trim();
  const description = String(raw.processing_description || byProcessing?.description || byReason?.description || fallbackDescription).trim();
  return {
    ...raw,
    label,
    description,
    reason_code: reasonKey,
  };
};

export default function Inbox() {
  const location = useLocation();
  const journeyLeadId = journeyLeadFromSearch(location.search);
  const [journeyLinkMessage, setJourneyLinkMessage] = useState("");
  const journeyLinkAppliedRef = useRef("");
  const leadsTenantRef = useRef(null);
  const buildWsUrl = (empresaId) => {
    const baseRaw = String(api?.defaults?.baseURL || "/api").trim();
    let apiHttpBase = "";

    if (/^https?:\/\//i.test(baseRaw)) {
      apiHttpBase = baseRaw;
    } else {
      const normalized = baseRaw.startsWith("/") ? baseRaw : `/${baseRaw}`;
      apiHttpBase = `${window.location.origin}${normalized}`;
    }

    const base = apiHttpBase.endsWith("/") ? apiHttpBase : `${apiHttpBase}/`;
    const endpoint = new URL(`empresas/${empresaId}/ws`, base);
    endpoint.protocol = endpoint.protocol === "https:" ? "wss:" : "ws:";
    return endpoint.toString();
  };

  const [leads, setLeads] = useState([]);
  const [selectedLead, setSelectedLead] = useState(null);
  const [showConversationList, setShowConversationList] = useState(false);
  const [leadsLoading, setLeadsLoading] = useState(true);
  const [leadsError, setLeadsError] = useState(false);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState(false);
  const [connectionState, setConnectionState] = useState(0);
  const journeyButtonRef = useRef(null);
  const [messages, setMessagesState] = useState([]);
  const [newMessage, setNewMessageState] = useState("");
  const [isSendingMedia, setIsSendingMedia] = useState(false);
  const [destinos, setDestinos] = useState([]);
  const [grupos, setGrupos] = useState([]);
  const [tagsOficiais, setTagsOficiais] = useState([]);
  const [showTransferPanel, setShowTransferPanel] = useState(false);
  const [showTagsPanel, setShowTagsPanel] = useState(false);
  const [showJourney, setShowJourney] = useState(false);
  const [selectedDestinoId, setSelectedDestinoId] = useState("");
  const [transferindo, setTransferindo] = useState(false);
  const [botToggleLoading, setBotToggleLoading] = useState(false);
  const [aiEffectiveState, setAiEffectiveState] = useState(null);
  const [statusToggleLoading, setStatusToggleLoading] = useState(false);
  const [filtroStatus, setFiltroStatus] = useState("aberto");
  const [abaAtiva, setAbaAtiva] = useState("Todos");
  const [searchTerm, setSearchTerm] = useState("");
  const [toast, setToast] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [mediaCaption, setMediaCaption] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [recordedAudioBlob, setRecordedAudioBlob] = useState(null);
  const [recordedAudioUrl, setRecordedAudioUrl] = useState("");
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttempts = useRef(0);
  const failedReconnectTimestampsRef = useRef([]);
  const reconnectBlockedUntilRef = useRef(0);
  const socketOpenedRef = useRef(false);
  const connectedTenantRef = useRef(null);
  const selectedLeadTelefoneRef = useRef(null);
  const selectedLeadRef = useRef(null);
  const tenantScopeRef = useRef(null);
  const leadsRef = useRef([]);
  const leadsRequestRef = useRef(null);
  const historyRequestRef = useRef(null);
  const aiRequestRef = useRef(null);
  const revisionRef = useRef(0);
  const leadRevisionsRef = useRef(new Map());
  const aiRevisionsRef = useRef(new Map());
  const messageRevisionsRef = useRef(new Map());
  const wsMessagePatchesRef = useRef(new Map());
  const messagesCacheRef = useRef(new Map());
  const draftsRef = useRef(new Map());
  const scrollCacheRef = useRef(new Map());
  const activeConversationRef = useRef("");
  const seenRealtimeRef = useRef(new Set());
  const stickToBottomRef = useRef(true);
  const readingScrollRef = useRef(0);
  const requestScrollRef = useRef(false);
  const historyScrollRef = useRef(null);
  const leadsRefreshTimerRef = useRef(null);
  const heartbeatIntervalRef = useRef(null);
  const photoCacheRef = useRef(new Map());
  const photoRequestsRef = useRef(new Map());
  const photoAbortControllersRef = useRef(new Map());
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const recordingIntervalRef = useRef(null);
  const audioStreamRef = useRef(null);
  const shouldSendRecordingRef = useRef(true);
  /** Por id de mensagem: transcrição de áudio expandida no chat */
  const [transcriptVisibleByMsgId, setTranscriptVisibleByMsgId] = useState({});

  // These setters bind work to the conversation that scheduled it, including
  // optimistic messages; delayed work cannot mutate a newly selected history.
  const setMessages = useCallback((updater, boundKey = activeConversationRef.current, realtime = false) => {
    const key = boundKey;
    if (!key || JSON.parse(key)[0] !== String(tenantScopeRef.current)) return;
    const revision = ++revisionRef.current;
    const apply = previous => {
      const next = typeof updater === "function" ? updater(previous) : updater;
      const versions = messageRevisionsRef.current.get(key) || new Map();
      const observed = wsMessagePatchesRef.current.get(key) || new Map();
      const previousById = new Map(previous.map(message => [messageIdentity(message), message]));
      for (const message of next) {
        const before = previousById.get(messageIdentity(message));
        if (before !== message) {
          versions.set(messageIdentity(message), revision);
          if (message.external_message_id) versions.set(String(message.external_message_id), revision);
          if (realtime) {
            const patch = { ...(observed.get(messageIdentity(message)) || {}) };
            for (const [field, value] of Object.entries(message)) if (!before || before[field] !== value) patch[field] = value;
            observed.set(messageIdentity(message), patch);
            if (message.external_message_id) observed.set(String(message.external_message_id), patch);
          }
        }
      }
      messageRevisionsRef.current.set(key, versions);
      wsMessagePatchesRef.current.set(key, observed);
      messagesCacheRef.current.set(key, next);
      return next;
    };
    if (activeConversationRef.current !== key) {
      apply(messagesCacheRef.current.get(key) || []);
      return;
    }
    setMessagesState(previous => activeConversationRef.current === key ? apply(previous) : previous);
  }, []);

  const recordLeadPatch = useCallback((id, patch) => {
    const key = String(id || "");
    if (!key) return;
    const revision = ++revisionRef.current;
    const old = leadRevisionsRef.current.get(key);
    const fields = { ...(old?.fields || {}) };
    for (const field of Object.keys(patch)) fields[field] = revision;
    leadRevisionsRef.current.set(key, { revision, fields });
    if (typeof patch.ia_ativa === "boolean" || typeof patch.bot_pausado === "boolean") aiRevisionsRef.current.set(key, revision);
  }, []);

  const setNewMessage = useCallback((value) => {
    if (activeConversationRef.current) draftsRef.current.set(activeConversationRef.current, value);
    setNewMessageState(value);
  }, []);

  const scrollToBottom = () => {
    stickToBottomRef.current = true;
    requestScrollRef.current = true;
    window.setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 0);
  };

  const formatRecordingTime = (totalSeconds) => {
    const secs = Math.max(0, Number(totalSeconds || 0));
    const mm = String(Math.floor(secs / 60)).padStart(2, "0");
    const ss = String(secs % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  };

  const buildAudioSrc = (media) => {
    const m = String(media || "").trim();
    if (!m) return "";
    if (m.startsWith("http://") || m.startsWith("https://") || m.startsWith("data:")) return m;
    return `data:audio/ogg;base64,${m}`;
  };

  const buildImageSrc = (media) => {
    const m = String(media || "").trim();
    if (!m) return "";
    if (m.startsWith("http://") || m.startsWith("https://") || m.startsWith("data:")) return m;
    return `data:image/jpeg;base64,${m}`;
  };

  const buildVideoSrc = (media) => {
    const m = String(media || "").trim();
    if (!m) return "";
    if (m.startsWith("http://") || m.startsWith("https://") || m.startsWith("data:")) return m;
    return `data:video/mp4;base64,${m}`;
  };

  const isTranscriptionPending = (texto) => String(texto || "").trim() === "[Transcrição pendente]";

  const isTranscriptionError = (texto) => {
    const t = String(texto || "").trim();
    if (!t || isTranscriptionPending(t)) return false;
    if (t.startsWith("[Erro")) return true;
    const lower = t.toLowerCase();
    if (lower.includes("não transcrito") || lower.includes("nao transcrito")) return true;
    if (lower.includes("indisponível") || lower.includes("indisponivel")) return true;
    if (lower.includes("mídia de áudio indisponível") || lower.includes("midia de audio indisponivel")) return true;
    return false;
  };

  const toggleTranscriptVisibility = (msgId) => {
    const id = String(msgId || "");
    if (!id) return;
    setTranscriptVisibleByMsgId((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const isPendingMessage = useCallback((msg) => String(msg?.status || "").toLowerCase() === "pending", []);
  const isFailedMessage = useCallback((msg) => String(msg?.status || "").toLowerCase() === "failed", []);

  const user = getStoredUser();
  const activeEmpresaId = getActiveEmpresaId();
  const empresa_id = activeEmpresaId;

  useLayoutEffect(() => {
    tenantScopeRef.current = empresa_id;
    leadsRequestRef.current?.controller.abort();
    historyRequestRef.current?.controller.abort();
    aiRequestRef.current?.controller.abort();
    leadsRequestRef.current = null;
    historyRequestRef.current = null;
    aiRequestRef.current = null;
    leadsTenantRef.current = null;
    activeConversationRef.current = "";
    leadsRef.current = [];
    selectedLeadRef.current = null;
    selectedLeadTelefoneRef.current = null;
    leadRevisionsRef.current.clear();
    aiRevisionsRef.current.clear();
    messageRevisionsRef.current.clear();
    wsMessagePatchesRef.current.clear();
    messagesCacheRef.current.clear();
    draftsRef.current.clear();
    scrollCacheRef.current.clear();
    seenRealtimeRef.current.clear();
    setSelectedLead(null);
    setLeads([]);
    setMessagesState([]);
    setNewMessageState("");
    setAiEffectiveState(null);
    setLeadsLoading(true);
    setMessagesLoading(false);
    setMessagesError(false);
    setShowJourney(false);
  }, [empresa_id]);

  useLayoutEffect(() => {
    leadsRef.current = leads;
    selectedLeadRef.current = leadsTenantRef.current === empresa_id ? selectedLead : null;
    selectedLeadTelefoneRef.current = selectedLeadRef.current?.telefone_contato || null;
  }, [empresa_id, leads, selectedLead]);

  const funnelContext = location.state?.funnelContext?.tenant === empresa_id
    ? { tenant: empresa_id, filters: allowlistedFunnelFilters(location.state.funnelContext.filters) } : null;
  useEffect(() => {
    if (!journeyLeadId || !empresa_id || leadsTenantRef.current !== empresa_id) return;
    const linkScope = `${empresa_id}:${journeyLeadId}`;
    if (journeyLinkAppliedRef.current === linkScope) return;
    const contact = leads.find(lead => String(lead.id).toLowerCase() === journeyLeadId.toLowerCase());
    if (!contact) {
      setJourneyLinkMessage("Contato não disponível no Inbox deste tenant. Nenhum contato foi criado para abrir a jornada.");
      return;
    }
    journeyLinkAppliedRef.current = linkScope;
    setJourneyLinkMessage(""); setSelectedLead(contact);
    setFiltroStatus(contact.status_atendimento || "aberto");
    setShowJourney(true);
  }, [empresa_id, journeyLeadId, leads]);

  const updateLeadInState = useCallback((leadId, updates) => {
    const targetId = String(leadId || "");
    if (!targetId) return;
    recordLeadPatch(targetId, updates);
    setLeads((prev) => (prev || []).map((lead) => (String(lead?.id || "") === targetId ? { ...lead, ...updates } : lead)));
    setSelectedLead((prev) => (String(prev?.id || "") === targetId ? { ...prev, ...updates } : prev));
  }, [recordLeadPatch]);

  const upsertLeadFromRealtime = useCallback((data, message, isSelected, isNew) => {
    const known = leadsRef.current.find(lead => leadMatches(lead, data, message));
    if (!known) return false;
    const patched = patchLeadFromMessage(known, data, message, { selected: isSelected, isNew });
    recordLeadPatch(known.id, Object.fromEntries(Object.entries(patched).filter(([field, value]) => value !== known[field])));
    setLeads(previous => {
      const next = previous.map(lead => leadMatches(lead, data, message)
        ? patchLeadFromMessage(lead, data, message, { selected: isSelected, isNew }) : lead);
      return isNew ? next.sort((a, b) => (new Date(b.ultima_mensagem_data || 0).getTime() || 0) - (new Date(a.ultima_mensagem_data || 0).getTime() || 0)) : next;
    });
    setSelectedLead(previous => leadMatches(previous, data, message)
      ? patchLeadFromMessage(previous, data, message, { selected: true, isNew }) : previous);
    return true;
  }, [recordLeadPatch]);

  const fetchLeads = useCallback(() => {
    if (!empresa_id || tenantScopeRef.current !== empresa_id) return Promise.resolve();
    if (leadsRequestRef.current?.tenant === empresa_id) return leadsRequestRef.current.promise;
    const request = { tenant: empresa_id, controller: new AbortController(), revision: revisionRef.current };
    leadsRequestRef.current = request;
    setLeadsLoading(!leadsRef.current.length);
    setLeadsError(false);
    request.promise = api.get(`/empresas/${empresa_id}/inbox`, { signal: request.controller.signal })
      .then(response => {
        if (tenantScopeRef.current !== empresa_id || leadsRequestRef.current !== request) return;
        leadsTenantRef.current = empresa_id;
        setLeads(previous => mergeLeadSnapshot(previous, response.data || [], request.revision, leadRevisionsRef.current));
        setSelectedLead(previous => {
          if (!previous?.id) return previous;
          return mergeLeadSnapshot([previous], response.data || [], request.revision, leadRevisionsRef.current)
            .find(lead => String(lead.id) === String(previous.id)) || previous;
        });
      })
      .catch(error => {
        if (request.controller.signal.aborted || tenantScopeRef.current !== empresa_id || leadsRequestRef.current !== request) return;
        console.error("Erro ao buscar leads no Inbox:", error);
        setLeadsError(true); // keep the valid list and current identity on temporary failure
      })
      .finally(() => {
        if (leadsRequestRef.current !== request) return;
        leadsRequestRef.current = null;
        if (tenantScopeRef.current === empresa_id) setLeadsLoading(false);
      });
    return request.promise;
  }, [empresa_id]);

  const scheduleFetchLeads = useCallback((delayMs = 15000) => {
    const safeDelay = Math.max(10000, Number(delayMs || 0));
    if (leadsRefreshTimerRef.current) {
      window.clearTimeout(leadsRefreshTimerRef.current);
    }
    leadsRefreshTimerRef.current = window.setTimeout(() => {
      leadsRefreshTimerRef.current = null;
      void fetchLeads();
    }, safeDelay);
  }, [fetchLeads]);

  const fetchMessages = useCallback((telefone) => {
    const key = conversationKey(empresa_id, selectedLeadRef.current);
    if (!key || tenantScopeRef.current !== empresa_id || key !== activeConversationRef.current || telefone !== selectedLeadRef.current?.telefone_contato) return Promise.resolve();
    if (historyRequestRef.current?.key === key) return historyRequestRef.current.promise;
    historyRequestRef.current?.controller.abort();
    const request = { key, controller: new AbortController(), revision: revisionRef.current };
    historyRequestRef.current = request;
    setMessagesLoading(!(messagesCacheRef.current.get(key) || []).length);
    setMessagesError(false);
    request.promise = api.get(`/empresas/${empresa_id}/inbox/${telefone}`, { signal: request.controller.signal })
      .then(response => {
        if (request.controller.signal.aborted || tenantScopeRef.current !== empresa_id || historyRequestRef.current !== request || conversationKey(empresa_id, selectedLeadRef.current) !== key || activeConversationRef.current !== key) return;
        setMessages(previous => {
          const next = mergeMessageSnapshot(previous, response.data || [], request.revision, messageRevisionsRef.current.get(key), wsMessagePatchesRef.current.get(key));
          if (!previous.length && next.length) requestScrollRef.current = true;
          return next;
        });
      })
      .catch(error => {
        if (request.controller.signal.aborted || tenantScopeRef.current !== empresa_id || historyRequestRef.current !== request || activeConversationRef.current !== key) return;
        console.error("Erro ao buscar mensagens do lead no Inbox:", error);
        setMessagesError(true); // stale-while-revalidate: never erase valid messages
      })
      .finally(() => {
        if (historyRequestRef.current !== request) return;
        historyRequestRef.current = null;
        if (activeConversationRef.current === key && tenantScopeRef.current === empresa_id) setMessagesLoading(false);
      });
    return request.promise;
  }, [empresa_id, setMessages]);

  const buildPhotoCacheKey = useCallback((lead) => {
    const contactIdentity = lead?.contact_id || lead?.id || lead?.telefone_contato;
    return empresa_id && contactIdentity ? `${empresa_id}:${contactIdentity}` : "";
  }, [empresa_id]);

  const fetchLeadPhoto = useCallback(async (lead) => {
    const leadId = lead?.id;
    const telefone = lead?.telefone_contato;
    const cacheKey = buildPhotoCacheKey(lead);
    if (!empresa_id || !leadId || !telefone || !cacheKey) return;

    if (photoCacheRef.current.has(cacheKey)) {
      updateLeadInState(leadId, { foto_url: photoCacheRef.current.get(cacheKey) });
      return;
    }
    if (photoRequestsRef.current.has(cacheKey)) return photoRequestsRef.current.get(cacheKey);

    const controller = new AbortController();
    photoAbortControllersRef.current.set(cacheKey, controller);
    const request = api
      .get(`/empresas/${empresa_id}/inbox/${telefone}/foto`, { signal: controller.signal })
      .then((res) => {
        if (tenantScopeRef.current !== empresa_id || controller.signal.aborted) return null;
        const fotoUrl = res.data?.foto_url || null;
        photoCacheRef.current.set(cacheKey, fotoUrl);
        updateLeadInState(leadId, { foto_url: fotoUrl });
        return fotoUrl;
      })
      .catch((e) => {
        if (e?.name !== "CanceledError" && e?.code !== "ERR_CANCELED") {
          photoCacheRef.current.set(cacheKey, null);
          console.error("Erro ao buscar foto do lead no Inbox:", e);
        }
        return null;
      })
      .finally(() => {
        photoRequestsRef.current.delete(cacheKey);
        photoAbortControllersRef.current.delete(cacheKey);
      });
    photoRequestsRef.current.set(cacheKey, request);
    return request;
  }, [buildPhotoCacheKey, empresa_id, updateLeadInState]);

  const fetchAiEffectiveState = useCallback((telefone, leadIdOverride = "") => {
    const key = conversationKey(empresa_id, selectedLeadRef.current);
    if (!key || !telefone || telefone !== selectedLeadRef.current?.telefone_contato || (leadIdOverride && String(leadIdOverride) !== String(selectedLeadRef.current?.id)) || tenantScopeRef.current !== empresa_id) return Promise.resolve();
    if (aiRequestRef.current?.key === key) return aiRequestRef.current.promise;
    aiRequestRef.current?.controller.abort();
    const request = { key, controller: new AbortController(), revision: revisionRef.current };
    aiRequestRef.current = request;
    request.promise = api.get(`/empresas/${empresa_id}/inbox/${telefone}/ai-state`, { signal: request.controller.signal })
      .then(response => {
        if (request.controller.signal.aborted || tenantScopeRef.current !== empresa_id || activeConversationRef.current !== key || aiRequestRef.current !== request || request.revision < (aiRevisionsRef.current.get(String(selectedLeadRef.current?.id)) || 0)) return;
        const nextState = response.data ? translateAiState(response.data) : null;
        setAiEffectiveState(nextState);
        const leadId = leadIdOverride || selectedLeadRef.current?.id;
        if (leadId && nextState?.manual_enabled !== undefined) {
          const iaAtiva = Boolean(nextState.manual_enabled);
          updateLeadInState(leadId, { ia_ativa: iaAtiva, bot_pausado: !iaAtiva });
        }
      })
      .catch(error => {
        if (request.controller.signal.aborted || tenantScopeRef.current !== empresa_id || activeConversationRef.current !== key || aiRequestRef.current !== request) return;
        console.error("Erro ao buscar estado efetivo da IA:", error);
      })
      .finally(() => { if (aiRequestRef.current === request) aiRequestRef.current = null; });
    return request.promise;
  }, [empresa_id, updateLeadInState]);

  const fetchDestinos = useCallback(async () => {
    if (!empresa_id) return;
    try {
      const res = await api.get(`/empresas/${empresa_id}/transferencias/destinos`);
      if (tenantScopeRef.current === empresa_id) setDestinos(res.data || []);
    } catch (e) {
      console.error("Erro ao buscar destinos de transferência:", e);
      if (tenantScopeRef.current === empresa_id) setDestinos([]);
    }
  }, [empresa_id]);

  const fetchGruposETags = useCallback(async () => {
    if (!empresa_id) return;
    try {
      const [gruposRes, tagsRes] = await Promise.all([
        api.get(`/empresas/${empresa_id}/crm/tags/grupos`),
        api.get(`/empresas/${empresa_id}/crm/tags/oficiais`),
      ]);
      if (tenantScopeRef.current !== empresa_id) return;
      setGrupos(gruposRes.data || []);
      setTagsOficiais(tagsRes.data || []);
    } catch (e) {
      console.error("Erro ao buscar grupos e tags oficiais:", e);
      if (tenantScopeRef.current !== empresa_id) return;
      setGrupos([]);
      setTagsOficiais([]);
    }
  }, [empresa_id]);

  useEffect(() => {
    fetchLeads();
    fetchDestinos();
    fetchGruposETags();
  }, [fetchDestinos, fetchGruposETags, fetchLeads]);

  useEffect(() => {
    if (abaAtiva === "Todos" || abaAtiva === "Sem Grupo") return;
    if (!grupos.some((grupo) => grupo.id === abaAtiva)) {
      setAbaAtiva("Todos");
    }
  }, [abaAtiva, grupos]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const selectedConversationKey = conversationKey(empresa_id, selectedLead);
  useLayoutEffect(() => {
    const key = leadsTenantRef.current === empresa_id ? selectedConversationKey : "";
    if (activeConversationRef.current === key) return;
    const oldKey = activeConversationRef.current;
    if (oldKey) scrollCacheRef.current.set(oldKey, { top: readingScrollRef.current, stick: stickToBottomRef.current });
    historyRequestRef.current?.controller.abort();
    aiRequestRef.current?.controller.abort();
    historyRequestRef.current = null;
    aiRequestRef.current = null;
    activeConversationRef.current = key;
    const cachedScroll = scrollCacheRef.current.get(key);
    stickToBottomRef.current = cachedScroll?.stick ?? true;
    readingScrollRef.current = cachedScroll?.top ?? 0;
    requestScrollRef.current = !cachedScroll || cachedScroll.stick;
    setMessagesState(messagesCacheRef.current.get(key) || []);
    setNewMessageState(draftsRef.current.get(key) || "");
    setMessagesError(false);
    setMessagesLoading(Boolean(key && !(messagesCacheRef.current.get(key) || []).length));
    setAiEffectiveState(null);
  }, [empresa_id, selectedConversationKey]);

  useEffect(() => {
    const lead = selectedLeadRef.current;
    if (!selectedConversationKey || activeConversationRef.current !== selectedConversationKey || !lead?.telefone_contato) return;
    void fetchMessages(lead.telefone_contato);
    void fetchAiEffectiveState(lead.telefone_contato, lead.id);
  }, [selectedConversationKey, fetchMessages, fetchAiEffectiveState]);

  useEffect(() => {
    if (!empresa_id || !selectedLead?.id || !selectedLead?.telefone_contato || selectedLead?.foto_url) return;
    fetchLeadPhoto({
      id: selectedLead.id,
      contact_id: selectedLead.contact_id,
      telefone_contato: selectedLead.telefone_contato,
    });
  }, [empresa_id, fetchLeadPhoto, selectedLead?.contact_id, selectedLead?.foto_url, selectedLead?.id, selectedLead?.telefone_contato]);

  useEffect(() => {
    if (!activeEmpresaId) return undefined;
    let isActive = true;

    const connect = () => {
      if (!isActive) return;
      const now = Date.now();
      if (reconnectBlockedUntilRef.current > now) {
        const waitMs = reconnectBlockedUntilRef.current - now;
        reconnectTimeoutRef.current = window.setTimeout(connect, waitMs);
        return;
      }

      const wsUrl = buildWsUrl(activeEmpresaId);
      const wsToken = localStorage.getItem("token");
      const ws = wsToken && wsToken !== "null" && wsToken !== "undefined"
        ? new WebSocket(wsUrl, [wsToken, "inbox.v1"])
        : new WebSocket(wsUrl);
      wsRef.current = ws;
      socketOpenedRef.current = false;

      ws.onopen = () => {
        if (!isActive || tenantScopeRef.current !== activeEmpresaId || wsRef.current !== ws) return;
        reconnectAttempts.current = 0;
        failedReconnectTimestampsRef.current = [];
        reconnectBlockedUntilRef.current = 0;
        socketOpenedRef.current = true;
        const reconnecting = connectedTenantRef.current === activeEmpresaId;
        connectedTenantRef.current = activeEmpresaId;
        if (reconnecting) {
          void fetchLeads();
          const selectedTelefone = selectedLeadTelefoneRef.current;
          if (selectedTelefone) {
            void fetchMessages(selectedTelefone);
            void fetchAiEffectiveState(selectedTelefone, selectedLeadRef.current?.id);
          }
        }
        if (heartbeatIntervalRef.current) {
          window.clearInterval(heartbeatIntervalRef.current);
        }
        heartbeatIntervalRef.current = window.setInterval(() => {
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            try {
              wsRef.current.send(JSON.stringify({ type: "ping", ts: Date.now() }));
            } catch {
              // no-op
            }
          }
        }, 25000);
      };

      ws.onmessage = (event) => {
        if (!isActive || tenantScopeRef.current !== activeEmpresaId || wsRef.current !== ws) return;
        try {
          const data = JSON.parse(event.data || "{}");
          if (data.empresa_id !== undefined && String(data.empresa_id) !== String(activeEmpresaId)) return;
          const payload = data?.payload || {};
          const tipoEvento = String(data?.tipo_evento || "").toLowerCase();
          const tipoEventoV2 = String(data?.type || "").toLowerCase();

          if (tipoEventoV2 === "inbox.ai_state.updated" || tipoEvento === "ai_state_updated") {
            const leadIdEvento = String(data?.lead_id || "");
            const leadSelecionadoId = String(selectedLeadRef.current?.id || "");
            const matchesSelected = leadMatches(selectedLeadRef.current, data);
            let manualEnabled;
            if (matchesSelected) {
              aiRevisionsRef.current.set(leadSelecionadoId, ++revisionRef.current);
              if (data?.ai_state) {
                setAiEffectiveState(translateAiState(data.ai_state));
              }
              manualEnabled = data?.ai_state?.manual_enabled ?? payload?.ia_ativa;
            }
            if (manualEnabled !== undefined && leadIdEvento) {
              const iaAtiva = Boolean(manualEnabled);
              updateLeadInState(leadIdEvento, {
                ia_ativa: iaAtiva,
                bot_pausado: !iaAtiva,
                ...definedLeadPatch(payload),
              });
            }
            return;
          }

          if (tipoEventoV2 === "status_ia_changed" || tipoEvento === "status_ia_changed") {
            const leadId = String(payload?.lead_id || data?.lead_id || "");
            if (!leadId) return;
            if (typeof payload?.ia_ativa !== "boolean") return;
            const iaAtiva = payload.ia_ativa;
            updateLeadInState(leadId, {
              ia_ativa: iaAtiva,
              bot_pausado: !iaAtiva,
            });
            return;
          }

          if (tipoEventoV2 === "atualizacao_mensagem" || tipoEvento === "mensagem_atualizada") {
            if (leadMatches(selectedLeadRef.current, data, data.mensagem) && data.mensagem?.id) {
              setMessages(previous => upsertMessage(previous, data.mensagem, { allowInsert: false }), activeConversationRef.current, true);
            }
            return;
          }

          if (tipoEvento === "atualizacao_lead" || tipoEventoV2 === "atualizacao_lead") {
            const leadPayload = data.lead || payload;
            const leadId = String(leadPayload.id || data.lead_id || "");
            const known = leadsRef.current.some(lead => String(lead.id) === leadId);
            if (leadId && known) {
              updateLeadInState(leadId, definedLeadPatch(leadPayload));
            } else scheduleFetchLeads(10000);
            return;
          }

          if (tipoEventoV2 === "inbox.lead.tags.updated" || tipoEvento === "lead_tags_updated") {
            const leadId = String(data?.lead_id || "");
            if (!leadId || !Array.isArray(data.tags)) return;
            updateLeadInState(leadId, { tags: data.tags });
            return;
          }

          const isMessageCreated =
            tipoEventoV2 === "inbox.message.created" ||
            tipoEventoV2 === "inbox.message.updated" ||
            tipoEvento === "nova_mensagem_inbound" ||
            tipoEvento === "nova_mensagem_outbound";
          if (!isMessageCreated) return;

          const telefoneEvento = String(data?.telefone || "");
          const novaMensagem = data?.mensagem || data?.message;
          const conversationIdEvento = String(data?.conversation_id || novaMensagem?.conversation_id || novaMensagem?.channel_conversation_id || "");
          const isSelectedConversation = leadMatches(selectedLeadRef.current, data, novaMensagem);

          const isReaction = String(novaMensagem?.tipo_mensagem || "").toLowerCase() === "reaction";
          const eventIdentity = messageIdentity(novaMensagem);
          const eventContact = leadsRef.current.find(lead => leadMatches(lead, data, novaMensagem));
          const eventConversationKey = conversationKey(activeEmpresaId, eventContact);
          const eventKey = `${eventContact?.id || data.lead_id || conversationIdEvento || telefoneEvento}:${eventIdentity}`;
          const isCreated = tipoEventoV2 !== "inbox.message.updated";
          const knownMessage = (messagesCacheRef.current.get(eventConversationKey) || []).some(message => eventIdentity && (messageIdentity(message) === eventIdentity || String(message.external_message_id || "") === String(novaMensagem?.external_message_id || "_")));
          const isNew = isCreated && !isReaction && Boolean(eventIdentity) && !seenRealtimeRef.current.has(eventKey) && !knownMessage;
          if (eventIdentity) {
            seenRealtimeRef.current.add(eventKey);
            if (seenRealtimeRef.current.size > 2000) seenRealtimeRef.current.delete(seenRealtimeRef.current.values().next().value);
          }
          if (isSelectedConversation && novaMensagem) {
            const scroll = historyScrollRef.current;
            if (isNew && scroll && scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop <= 96) {
              stickToBottomRef.current = true;
              requestScrollRef.current = true;
            }
            setMessages(previous => upsertMessage(previous, novaMensagem, { allowInsert: isCreated }), activeConversationRef.current, true);
          } else if (novaMensagem) {
            const contact = leadsRef.current.find(lead => leadMatches(lead, data, novaMensagem));
            const key = conversationKey(activeEmpresaId, contact);
            if (key && messagesCacheRef.current.has(key)) {
              setMessages(previous => upsertMessage(previous, novaMensagem, { allowInsert: isCreated }), key, true);
            }
          }
          // Known contacts are patched in place. Only insufficient/unknown
          // identities request a bounded background reconciliation.
          if (!upsertLeadFromRealtime(data, novaMensagem, isSelectedConversation, isNew)) scheduleFetchLeads(10000);
        } catch (err) {
          console.error("Erro ao processar evento websocket do Inbox:", err);
        }
      };

      ws.onerror = (err) => {
        console.error("Erro no WebSocket do Inbox:", err);
      };

      ws.onclose = () => {
        if (!isActive) return;
        if (heartbeatIntervalRef.current) {
          window.clearInterval(heartbeatIntervalRef.current);
          heartbeatIntervalRef.current = null;
        }
        const openedOnce = socketOpenedRef.current;
        const now = Date.now();

        if (!openedOnce) {
          const recentFailures = [...failedReconnectTimestampsRef.current, now].filter(
            (ts) => now - ts <= 10000
          );
          failedReconnectTimestampsRef.current = recentFailures;

          if (recentFailures.length > 5) {
            reconnectBlockedUntilRef.current = now + 60000;
            reconnectAttempts.current = 0;
            const timeout = 60000;
            console.error("WebSocket fechado. Tentando reconectar em...", timeout);
            reconnectTimeoutRef.current = window.setTimeout(connect, timeout);
            return;
          }

          reconnectAttempts.current += 1;
        } else {
          reconnectAttempts.current = 0;
          failedReconnectTimestampsRef.current = [];
        }

        const timeout = Math.min(1000 * (2 ** reconnectAttempts.current), 30000);
        console.error("WebSocket fechado. Tentando reconectar em...", timeout);
        reconnectTimeoutRef.current = window.setTimeout(() => {
          connect();
        }, timeout);
      };
    };

    connect();

    const photoAbortControllers = photoAbortControllersRef.current;
    const photoRequests = photoRequestsRef.current;

    return () => {
      isActive = false;
      if (reconnectTimeoutRef.current) {
        window.clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (heartbeatIntervalRef.current) {
        window.clearInterval(heartbeatIntervalRef.current);
        heartbeatIntervalRef.current = null;
      }
      if (leadsRefreshTimerRef.current) {
        window.clearTimeout(leadsRefreshTimerRef.current);
        leadsRefreshTimerRef.current = null;
      }
      photoAbortControllers.forEach((controller) => controller.abort());
      photoAbortControllers.clear();
      photoRequests.clear();
      try {
        wsRef.current?.close();
      } catch {
        // no-op
      }
      socketOpenedRef.current = false;
      wsRef.current = null;
    };
  }, [activeEmpresaId, fetchAiEffectiveState, fetchLeads, fetchMessages, isPendingMessage, scheduleFetchLeads, setMessages, updateLeadInState, upsertLeadFromRealtime]);

  useEffect(() => () => {
    if (recordingIntervalRef.current) {
      window.clearTimeout(recordingIntervalRef.current);
      recordingIntervalRef.current = null;
    }
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach((track) => track.stop());
      audioStreamRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      if (recordedAudioUrl) {
        URL.revokeObjectURL(recordedAudioUrl);
      }
    };
  }, [recordedAudioUrl]);

  useLayoutEffect(() => {
    if (requestScrollRef.current) {
      requestScrollRef.current = false;
      messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
    } else if (!stickToBottomRef.current && historyScrollRef.current) {
      historyScrollRef.current.scrollTop = readingScrollRef.current;
    }
  }, [messages]);

  useEffect(() => () => {
    leadsRequestRef.current?.controller.abort();
    historyRequestRef.current?.controller.abort();
    aiRequestRef.current?.controller.abort();
  }, []);

  const handleSendMessage = async (e) => {
    e.preventDefault();
    const texto = newMessage.trim();
    if (!texto || !selectedLead) return;

    const operationKey = conversationKey(empresa_id, selectedLead);
    const setOperationMessages = updater => setMessages(updater, operationKey);
    const telefone = selectedLead.telefone_contato;
    const tempId = `temp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const optimisticMessage = {
      id: tempId,
      texto,
      from_me: true,
      origem_mensagem: "humano",
      tipo_mensagem: "text",
      media_url: null,
      telefone_contato: telefone,
      retry_type: "text",
      status: "pending",
      criado_em: new Date().toISOString(),
    };

    setOperationMessages((prev) => [...(prev || []), optimisticMessage]);
    setNewMessage("");
    scrollToBottom();
    try {
      await api.post(`/empresas/${empresa_id}/inbox/${telefone}/send`, {
        texto,
      });
      // Mantém UI livre; confirmação deve chegar por websocket.
      void fetchLeads(); // update `bot_pausado`
    } catch (e) {
      console.error("Erro ao enviar mensagem", e);
      setOperationMessages((prev) =>
        (prev || []).map((msg) =>
          msg?.id === tempId
            ? {
                ...msg,
                status: "failed",
                erro_envio: e.response?.data?.detail || "Falha ao enviar mensagem.",
              }
            : msg
        )
      );
      setToast({
        type: "error",
        message: e.response?.data?.detail || "Não foi possível enviar a mensagem.",
      });
    }
  };

  const sendMediaFile = async (file, caption = "", options = {}) => {
    if (!selectedLead || !file) {
      const det = !selectedLead ? "Nenhum contacto selecionado." : "Ficheiro em falta.";
      console.error("[Inbox sendMediaFile] Abortado:", det);
      setToast({ type: "error", message: det });
      return false;
    }
    const operationKey = conversationKey(empresa_id, selectedLead);
    const setOperationMessages = updater => setMessages(updater, operationKey);
    const telefone = selectedLead.telefone_contato;
    const tempId = `temp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const forcedTipo = String(options?.tipoMensagem || "").trim().toLowerCase();
    const tiposValidos = ["image", "audio", "video", "document"];
    const tipoMidia = tiposValidos.includes(forcedTipo)
      ? forcedTipo
      : file.type.startsWith("image/")
        ? "image"
        : file.type.startsWith("audio/")
          ? "audio"
          : file.type.startsWith("video/")
            ? "video"
            : "document";
    const textoBaseEnvio =
      tipoMidia === "audio"
        ? "Enviando áudio..."
        : tipoMidia === "video"
          ? "Enviando vídeo..."
          : tipoMidia === "image"
            ? "Enviando imagem..."
            : "Enviando arquivo...";
    const textoOptimista = caption?.trim()
      ? `${textoBaseEnvio} ${caption.trim()}`
      : textoBaseEnvio;
    const optimisticMediaMessage = {
      id: tempId,
      texto: textoOptimista,
      from_me: true,
      origem_mensagem: "humano",
      tipo_mensagem: tipoMidia,
      media_url: null,
      telefone_contato: telefone,
      retry_type: "media",
      status: "pending",
      criado_em: new Date().toISOString(),
    };

    setOperationMessages((prev) => [...(prev || []), optimisticMediaMessage]);
    scrollToBottom();
    setIsSendingMedia(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("caption", caption || "");
      formData.append("tipo_mensagem", tipoMidia);
      await api.post(`/empresas/${empresa_id}/inbox/${telefone}/send_media`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      void fetchLeads();
      return true;
    } catch (e) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail ?? e?.response?.data;
      const msg = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : e?.message || String(e);
      console.error("[Inbox sendMediaFile] Erro na API ou rede:", {
        message: e?.message,
        status,
        detail,
        file: file?.name,
        fileType: file?.type,
        tipoMidia,
      });
      setOperationMessages((prev) =>
        (prev || []).map((msg) =>
          msg?.id === tempId
            ? {
                ...msg,
                status: "failed",
                erro_envio: e.response?.data?.detail || "Falha ao enviar arquivo.",
                texto:
                  tipoMidia === "audio"
                    ? "Falha ao enviar áudio."
                    : tipoMidia === "video"
                      ? "Falha ao enviar vídeo."
                      : tipoMidia === "image"
                        ? "Falha ao enviar imagem."
                        : "Falha ao enviar arquivo.",
              }
            : msg
        )
      );
      setToast({
        type: "error",
        message: status ? `[${status}] ${msg}` : msg || "Não foi possível enviar a mídia.",
      });
      return false;
    } finally {
      setIsSendingMedia(false);
    }
  };

  const handleSelectFile = (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setSelectedFile(file);
  };

  const clearSelectedFile = () => {
    setSelectedFile(null);
    setMediaCaption("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const clearRecordedAudioPreview = () => {
    if (recordedAudioUrl) {
      URL.revokeObjectURL(recordedAudioUrl);
    }
    setRecordedAudioBlob(null);
    setRecordedAudioUrl("");
  };

  const handleSendMedia = async () => {
    if (!selectedLead || !selectedFile) return;
    const ok = await sendMediaFile(selectedFile, mediaCaption);
    if (ok) {
      clearSelectedFile();
    }
  };

  const startRecording = async () => {
    if (!selectedLead) return;
    if (!navigator?.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      alert("Seu navegador não suporta gravação de áudio.");
      return;
    }

    try {
      clearRecordedAudioPreview();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioStreamRef.current = stream;
      const mimeCandidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
      ];
      const mimeEscolhido =
        mimeCandidates.find((t) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported?.(t)) || "";
      const recorder = mimeEscolhido
        ? new MediaRecorder(stream, { mimeType: mimeEscolhido })
        : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];
      shouldSendRecordingRef.current = true;

      recorder.ondataavailable = (event) => {
        if (event?.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = async () => {
        const shouldSend = shouldSendRecordingRef.current;
        try {
          if (shouldSend && audioChunksRef.current.length > 0) {
            const blobType = recorder.mimeType || "audio/webm";
            const blob = new Blob(audioChunksRef.current, { type: blobType });
            const previewUrl = URL.createObjectURL(blob);
            setRecordedAudioBlob(blob);
            setRecordedAudioUrl(previewUrl);
          }
        } finally {
          audioChunksRef.current = [];
          if (audioStreamRef.current) {
            audioStreamRef.current.getTracks().forEach((track) => track.stop());
            audioStreamRef.current = null;
          }
          mediaRecorderRef.current = null;
        }
      };

      recorder.start();
      setRecordingTime(0);
      setIsRecording(true);
      if (recordingIntervalRef.current) {
        window.clearTimeout(recordingIntervalRef.current);
      }
      const tickRecording = () => {
        recordingIntervalRef.current = window.setTimeout(() => {
          setRecordingTime((prev) => prev + 1);
          tickRecording();
        }, 1000);
      };
      tickRecording();
    } catch (err) {
      console.error("Erro ao iniciar gravação:", err);
      alert("Não foi possível acessar o microfone. Verifique as permissões do navegador.");
    }
  };

  const stopRecording = (send = true) => {
    shouldSendRecordingRef.current = Boolean(send);
    setIsRecording(false);
    setRecordingTime(0);
    if (recordingIntervalRef.current) {
      window.clearTimeout(recordingIntervalRef.current);
      recordingIntervalRef.current = null;
    }
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
      return;
    }
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach((track) => track.stop());
      audioStreamRef.current = null;
    }
  };

  const handleSendRecordedAudio = async () => {
    if (!selectedLead) {
      setToast({ type: "error", message: "Selecione um contacto para enviar o áudio gravado." });
      return;
    }
    if (!recordedAudioBlob || recordedAudioBlob.size < 1) {
      setToast({ type: "error", message: "Não há gravação de áudio para enviar." });
      return;
    }
    // Chrome costuma usar `video/webm` para gravações só de áudio — forçamos tipo "audio" no FormData.
    const mime = recordedAudioBlob.type && recordedAudioBlob.type.startsWith("audio/")
      ? recordedAudioBlob.type
      : "audio/webm";
    const audioFile = new File([recordedAudioBlob], "gravacao.webm", { type: mime });
    const ok = await sendMediaFile(audioFile, "", { tipoMensagem: "audio" });
    if (ok) {
      clearRecordedAudioPreview();
    }
  };

  const handleComposerKeyDown = (event) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    if (selectedFile || !newMessage.trim()) return;
    handleSendMessage(event);
  };

  const handleRetryMessage = async (mensagemFalha) => {
    if (!mensagemFalha || !selectedLead) return;

    const retryType = String(mensagemFalha?.retry_type || "text").toLowerCase();
    if (retryType !== "text") {
      setToast({
        type: "error",
        message: "Retry de mídia ainda não disponível. Reenvie o arquivo manualmente.",
      });
      return;
    }

    const operationKey = conversationKey(empresa_id, selectedLead);
    const setOperationMessages = updater => setMessages(updater, operationKey);
    const telefone = String(mensagemFalha?.telefone_contato || selectedLead?.telefone_contato || "").trim();
    const texto = String(mensagemFalha?.texto || "").trim();
    if (!telefone || !texto) return;

    setOperationMessages((prev) =>
      (prev || []).map((msg) =>
        msg?.id === mensagemFalha.id
          ? {
              ...msg,
              status: "pending",
              erro_envio: null,
            }
          : msg
      )
    );

    try {
      await api.post(`/empresas/${empresa_id}/inbox/${telefone}/send`, { texto });
      void fetchLeads();
    } catch (e) {
      setOperationMessages((prev) =>
        (prev || []).map((msg) =>
          msg?.id === mensagemFalha.id
            ? {
                ...msg,
                status: "failed",
                erro_envio: e.response?.data?.detail || "Falha ao reenviar mensagem.",
              }
            : msg
        )
      );
      setToast({
        type: "error",
        message: e.response?.data?.detail || "Não foi possível reenviar a mensagem.",
      });
    }
  };

  const baixarDocumento = (base64Data, nome = "documento") => {
    try {
      const binary = window.atob(base64Data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }
      const blob = new Blob([bytes], { type: "application/octet-stream" });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = nome;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Erro ao baixar documento:", err);
    }
  };

  const renderMensagemConteudo = (msg) => {
    const tipo = String(msg?.tipo_mensagem || "text").toLowerCase();
    const media = msg?.media_url;

    if (tipo === "image" && media) {
      const imgSrc = buildImageSrc(media);
      const legenda = String(msg?.texto || "").trim();

      return (
        <div className="flex flex-col gap-1">
          <img
            src={imgSrc}
            alt="Imagem"
            className="max-w-[250px] rounded-lg cursor-pointer hover:opacity-90 transition-opacity"
            onError={(e) => {
              console.error("Erro ao renderizar imagem no Inbox. Verifique o src no Network tab.");
              e.target.src = "https://placehold.co/250x200/e2e8f0/64748b?text=Erro+na+Imagem";
            }}
          />
          {legenda ? <span className="text-sm mt-1 whitespace-pre-wrap">{msg.texto}</span> : null}
        </div>
      );
    }

    if (tipo === "video" && media) {
      const videoSrc = buildVideoSrc(media);
      const legenda = String(msg?.texto || "").trim();

      return (
        <div className="flex flex-col gap-1">
          <video controls src={videoSrc} className="max-w-[250px] rounded-lg" preload="metadata" />
          {legenda ? <span className="text-sm mt-1 whitespace-pre-wrap">{msg.texto}</span> : null}
        </div>
      );
    }

    if (tipo === "video" && !media) {
      const legenda = String(msg?.texto || "").trim();
      return legenda ? (
        <p className="text-sm whitespace-pre-wrap">{msg.texto}</p>
      ) : (
        <p className="text-sm text-gray-500">[Vídeo sem pré-visualização]</p>
      );
    }

    if (tipo === "audio") {
      const audioSrc = buildAudioSrc(media);
      const mid = String(msg?.id || "");
      const showTr = Boolean(transcriptVisibleByMsgId[mid]);
      const textoRaw = String(msg?.texto || "");
      const pending = isTranscriptionPending(textoRaw);
      const err = isTranscriptionError(textoRaw);

      return (
        <div className="flex flex-col gap-2">
          {audioSrc ? (
            <audio controls src={audioSrc} className="max-w-full sm:max-w-xs" preload="metadata" />
          ) : (
            <p className="text-xs text-amber-800">Áudio registado; o ficheiro não está disponível para reprodução.</p>
          )}
          <button
            type="button"
            onClick={() => toggleTranscriptVisibility(mid)}
            className="self-start text-xs font-medium text-blue-600 underline-offset-2 hover:text-blue-800 hover:underline"
          >
            {showTr ? "Ocultar transcrição" : "Ver transcrição"}
          </button>
          {showTr ? (
            <div
              className={`rounded-lg border px-3 py-2 text-sm italic leading-relaxed ${
                err
                  ? "border-red-200 bg-red-50 text-red-900"
                  : pending
                    ? "border-amber-200 bg-amber-50 text-amber-900"
                    : "border-gray-200 bg-gray-100 text-gray-800"
              }`}
            >
              {pending ? "A transcrever…" : textoRaw || "(sem transcrição)"}
            </div>
          ) : null}
        </div>
      );
    }

    if (tipo === "document" && media) {
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white/70 px-3 py-2">
            <FileText size={16} />
            <button
              type="button"
              onClick={() => baixarDocumento(media, "documento_recebido")}
              className="text-sm font-medium underline"
            >
              Baixar documento
            </button>
          </div>
          {msg?.texto ? <p className="text-sm whitespace-pre-wrap">{msg.texto}</p> : null}
        </div>
      );
    }

    return (
      <div className={isPendingMessage(msg) ? "opacity-70" : ""}>
        <p className="text-sm whitespace-pre-wrap">{msg?.texto}</p>
        {isPendingMessage(msg) ? (
          <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-gray-500">
            <RefreshCw size={11} className="animate-spin" />
            Enviando...
          </span>
        ) : null}
        {isFailedMessage(msg) ? (
          <button
            type="button"
            onClick={() => handleRetryMessage(msg)}
            className="mt-1 inline-flex items-center gap-1 text-[11px] text-red-600 hover:text-red-700"
            title={msg?.erro_envio || "Falha ao enviar"}
          >
            <AlertCircle size={12} />
            Falha ao enviar. Clique para tentar novamente.
          </button>
        ) : null}
      </div>
    );
  };

  const handleToggleBot = async () => {
    if (!selectedLead || botToggleLoading) return;
    const leadId = selectedLead.id;
    const currentManualEnabled = aiEffectiveState?.manual_enabled ?? selectedLead.ia_ativa ?? true;
    const desiredEnabled = !currentManualEnabled;
    const previousState = {
      ia_ativa: Boolean(selectedLead.ia_ativa ?? true),
      bot_pausado: Boolean(selectedLead.bot_pausado),
      bot_pausado_ate: selectedLead.bot_pausado_ate || null,
    };
    updateLeadInState(leadId, { ia_ativa: desiredEnabled, bot_pausado: !desiredEnabled });
    setBotToggleLoading(true);
    try {
      const res = await api.patch(`/empresas/${empresa_id}/crm/${leadId}/toggle-ia`, { enabled: desiredEnabled });
      const nextManualEnabled = res.data?.manual_enabled ?? res.data?.ia_ativa;
      updateLeadInState(leadId, {
        ia_ativa: Boolean(nextManualEnabled),
        bot_pausado: !nextManualEnabled,
        status_atendimento: res.data?.status_atendimento || selectedLead.status_atendimento,
      });
      await fetchAiEffectiveState(selectedLead.telefone_contato, leadId);
    } catch (e) {
      updateLeadInState(leadId, previousState);
      setToast({ type: "error", message: e.response?.data?.detail || "Não foi possível atualizar o estado da IA." });
    } finally {
      setBotToggleLoading(false);
    }
  };

  const handleToggleStatus = async () => {
    if (!selectedLead || statusToggleLoading) return;
    const leadId = selectedLead.id;
    const currentStatus = String(selectedLead.status_atendimento || "aberto").toLowerCase();
    const nextStatus = currentStatus === "concluido" ? "aberto" : "concluido";
    updateLeadInState(leadId, { status_atendimento: nextStatus });
    setStatusToggleLoading(true);
    try {
      await api.put(`/empresas/${empresa_id}/crm/leads/${leadId}`, { status_atendimento: nextStatus });
      await fetchLeads();
    } catch (e) {
      updateLeadInState(leadId, { status_atendimento: currentStatus });
      setToast({ type: "error", message: e.response?.data?.detail || "Não foi possível atualizar o status do atendimento." });
    } finally {
      setStatusToggleLoading(false);
    }
  };

  const handleExcluirLead = async () => {
    if (!selectedLead) return;
    if (!window.confirm("Certeza que deseja excluir esta conversa e todo o seu histórico de mensagens?")) return;
    try {
      const conversationId = String(selectedLead.conversation_id || "").trim();
      if (!conversationId) throw new Error("Conversa não identificada");
      await api.delete(`/empresas/${empresa_id}/crm/leads/${selectedLead.id}`, { params: { conversation_id: conversationId } });
      setSelectedLead(null);
      setMessages([]);
      fetchLeads();
    } catch (e) {
      console.error("Erro ao excluir conversa", e);
      alert("Erro ao excluir a conversa.");
    }
  };

  const handleLeadTagsChange = async (leadId, nextTags) => {
    const leadAtual = leads.find((l) => l.id === leadId);
    if (!leadAtual) return;
    try {
      await api.put(`/empresas/${empresa_id}/crm/leads/${leadId}`, { tags: nextTags });
      updateLeadInState(leadId, { tags: nextTags });
    } catch (err) {
      console.error("Erro ao salvar tags no Inbox:", err);
    }
  };

  const handleTransferirManual = async () => {
    if (!selectedLead?.id || !selectedDestinoId) return;
    try {
      setTransferindo(true);
      const res = await api.post(`/empresas/${empresa_id}/leads/${selectedLead.id}/transferir_manual`, { destino_id: selectedDestinoId });
      setToast({ type: "success", message: res.data?.detail || "Transferência manual realizada com sucesso." });
      setShowTransferPanel(false);
      setSelectedDestinoId("");
      await fetchLeads();
    } catch (e) {
      setToast({ type: "error", message: e.response?.data?.detail || "Erro ao transferir o lead manualmente." });
    } finally {
      setTransferindo(false);
    }
  };

  const handleSelectLead = (lead) => {
    setSelectedLead(lead);
    setShowConversationList(false);
  };

  // Observe the active socket for presentation only. Connection ownership,
  // heartbeat and reconnect scheduling remain in the existing effect above.
  useEffect(() => {
    const socket = wsRef.current;
    if (!socket) return;
    const observe = () => setConnectionState(socket.readyState);
    observe();
    socket.addEventListener("open", observe);
    socket.addEventListener("close", observe);
    socket.addEventListener("error", observe);
    return () => {
      socket.removeEventListener("open", observe);
      socket.removeEventListener("close", observe);
      socket.removeEventListener("error", observe);
    };
  });

  const closeJourney = useCallback(() => setShowJourney(false), []);

  const tagsOficiaisLookup = useMemo(() => {
    const porNome = new Map();
    const porId = new Map();
    (tagsOficiais || []).forEach((tag) => {
      const nome = String(tag?.nome || "").trim().toLowerCase();
      const id = String(tag?.id || "").trim();
      if (nome) porNome.set(nome, tag);
      if (id) porId.set(id, tag);
    });
    return { porNome, porId };
  }, [tagsOficiais]);

  const normalizeStatusAtendimento = (status) => String(status || "aberto").trim().toLowerCase();

  const filteredLeads = useMemo(() => {
    const termo = String(searchTerm || "").trim().toLowerCase();
    const leadsFiltrados = (leads || []).filter((lead) => {
      const statusLead = normalizeStatusAtendimento(lead?.status_atendimento);
      const statusSelecionado = normalizeStatusAtendimento(filtroStatus);
      if (statusSelecionado === "aberto") {
        if (!STATUS_ATENDIMENTO_ABERTOS.has(statusLead)) return false;
      } else {
        if (statusLead !== statusSelecionado) return false;
      }

      if (termo) {
        const nome = String(lead?.nome_contato || "").toLowerCase();
        const telefone = String(lead?.telefone_contato || "").toLowerCase();
        if (!nome.includes(termo) && !telefone.includes(termo)) return false;
      }

      if (abaAtiva === "Todos") return true;

      const tagsLead = normalizeLeadTags(lead?.tags);
      if (abaAtiva === "Sem Grupo" && tagsLead.length === 0) return true;

      const tagsOficiaisLead = tagsLead
        .map((tag) => {
          const isObj = tag && typeof tag === "object";
          const tagId = isObj ? String(tag.id || "").trim() : String(tag || "").trim();
          const tagNome = isObj ? String(tag.nome || "").trim().toLowerCase() : String(tag || "").trim().toLowerCase();
          return tagsOficiaisLookup.porId.get(tagId) || tagsOficiaisLookup.porNome.get(tagNome) || (isObj ? tag : null);
        })
        .filter(Boolean);

      if (abaAtiva === "Sem Grupo") {
        return tagsOficiaisLead.length === 0 || tagsOficiaisLead.every((tag) => {
          const grupoId = String(tag?.grupo_id || "");
          return !grupoId || !grupos.some((grupo) => String(grupo?.id || "") === grupoId);
        });
      }

      return tagsOficiaisLead.some((tag) => String(tag?.grupo_id || "") === abaAtiva);
    });

    const getLeadSortTime = (lead) => {
      const dataReferencia = lead?.ultima_mensagem_data || lead?.criado_em;
      if (!dataReferencia) return 0;
      const timestamp = new Date(dataReferencia).getTime();
      return Number.isNaN(timestamp) ? 0 : timestamp;
    };

    return leadsFiltrados.sort((a, b) => getLeadSortTime(b) - getLeadSortTime(a));
  }, [abaAtiva, filtroStatus, grupos, leads, searchTerm, tagsOficiaisLookup]);

  const hasPayload = Boolean(selectedFile || newMessage.trim());
  const manualEnabled = aiEffectiveState?.manual_enabled ?? selectedLead?.ia_ativa ?? true;
  const selectedLeadPaused = !manualEnabled;
  const aiEffectiveReason = String(aiEffectiveState?.reason_code || "");
  const aiEffectiveBlocked = aiEffectiveState && aiEffectiveState.effective_state !== "authorized";
  const aiEffectiveLabel = selectedLeadPaused
    ? "IA desligada nesta conversa"
    : aiEffectiveState?.label || "Verificando IA";
  const aiEffectiveDescription = selectedLeadPaused
    ? "Ative o controle de IA para permitir respostas automáticas."
    : aiEffectiveBlocked
      ? aiEffectiveState?.description || "Resposta automática bloqueada pelo gate"
      : "A IA está pronta para processar novas mensagens.";
  const selectedLeadPausedAtLabel = selectedLead?.bot_pausado_ate
    ? new Date(selectedLead.bot_pausado_ate).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "";
  const selectedLeadStatus = String(selectedLead?.status_atendimento || "aberto").toLowerCase();
  const statusConcluir = selectedLeadStatus === "concluido";
  if (!user || !empresa_id) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="text-center text-gray-500">
          <MessageSquare size={48} className="mx-auto mb-4 opacity-50" />
          <h2 className="text-xl font-semibold">Carregando Ambiente...</h2>
          <p className="mt-2 text-sm">Autenticando sessão do usuário.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="inbox-ux04 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {funnelContext && <Link to="/painel/funil" state={{ funnelContext }} className="shrink-0 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm text-indigo-700">← Voltar ao funil com os filtros</Link>}
      {journeyLinkMessage && <p role="alert" className="shrink-0 px-3 py-2 text-sm text-amber-800 bg-amber-50">{journeyLinkMessage}</p>}
      <div className="inbox-frame relative flex min-h-0 min-w-0 flex-1 w-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      {toast ? (
        <div className="inbox-toast fixed right-6 top-6 z-[80]" role={toast.type === "error" ? "alert" : "status"}>
          <div
            className={`min-w-[320px] max-w-md rounded-2xl border px-4 py-3 shadow-xl ${
              toast.type === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-red-200 bg-red-50 text-red-800"
            }`}
          >
            <div className="flex items-start gap-3">
              <div className={`rounded-full p-1 ${toast.type === "success" ? "bg-emerald-100" : "bg-red-100"}`}>
                {toast.type === "success" ? <ArrowRightLeft size={14} /> : <X size={14} />}
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold">{toast.type === "success" ? "Tudo certo" : "Atenção"}</p>
                <p className="mt-0.5 text-sm">{toast.message}</p>
              </div>
              <button type="button" aria-label="Fechar aviso" onClick={() => setToast(null)} className="rounded-lg p-1 opacity-70 hover:opacity-100">
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <aside aria-label="Lista de conversas" className={`inbox-list flex h-full min-h-0 min-w-0 w-full shrink-0 flex-col border-r border-gray-200 bg-white lg:w-[clamp(280px,30vw,380px)] ${selectedLead && !showConversationList ? "hidden lg:flex" : "flex"}`}>
        <div className="inbox-list-heading shrink-0 border-b border-gray-200 px-5 py-5">
          <div className="flex items-center justify-between gap-2"><h2 className="text-xl font-semibold text-gray-800">Conversas</h2><span className="inbox-count">{filteredLeads.length}</span></div>
          <p data-testid="inbox-connection" role="status" className={`inbox-connection ${connectionState > 1 ? "inbox-connection-offline" : ""}`}><span aria-hidden="true" />{connectionState === 1 ? "Atualizações em tempo real" : connectionState > 1 ? "Conexão interrompida · aguardando reconexão" : "Conectando atualizações…"}</p>
          {selectedLead && showConversationList && <button type="button" aria-label="Voltar à conversa selecionada" onClick={() => setShowConversationList(false)} className="inbox-resume lg:hidden">Voltar à conversa de {selectedLead.nome_contato}</button>}
        </div>
        <div className="inbox-filters shrink-0 border-b border-gray-100 px-5 py-4">
          <div className="inbox-status-filters inline-flex rounded-xl bg-gray-100 p-1" aria-label="Status do atendimento">
            {[
              { id: "aberto", label: "Abertos" },
              { id: "concluido", label: "Concluídos" },
            ].map((status) => (
              <button
                key={status.id}
                type="button"
                aria-pressed={filtroStatus === status.id}
                onClick={() => setFiltroStatus(status.id)}
                className={`rounded-lg px-3.5 py-2 text-sm font-medium transition-colors ${
                  filtroStatus === status.id
                    ? "bg-white text-gray-900 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                {status.label}
              </button>
            ))}
          </div>

          <div className="relative mt-3">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              aria-label="Buscar por nome ou telefone"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Buscar por nome ou telefone"
              className="w-full rounded-xl border border-gray-200 bg-white py-2.5 pl-10 pr-3 text-sm text-gray-700 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <select
            aria-label="Filtrar por departamento"
            value={abaAtiva}
            onChange={(e) => setAbaAtiva(e.target.value)}
            className="mb-3 mt-3 w-full rounded-md border border-gray-200 bg-gray-50 p-2.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="Todos">Todos os Departamentos</option>
            {grupos.map((grupo) => (
              <option key={grupo.id} value={grupo.id}>
                {grupo.nome}
              </option>
            ))}
            <option value="Sem Grupo">Sem Departamento</option>
          </select>
        </div>
        <div data-testid="inbox-conversations" aria-busy={leadsLoading} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {leadsError && <div className="inbox-list-state" role="alert"><InfoBanner tone="danger">Não foi possível carregar as conversas.</InfoBanner><ActionButton variant="secondary" onClick={fetchLeads}>Tentar novamente</ActionButton></div>}
          {leadsLoading && !leads.length && <div className="inbox-list-state"><LoadingState label="Carregando conversas…" /></div>}
          {filteredLeads?.map((lead) => (
            <button
              key={lead.id}
              type="button"
              data-lead-id={lead.id}
              aria-pressed={selectedLead?.id === lead.id}
              onClick={() => handleSelectLead(lead)}
              className={`inbox-lead-row w-full text-left cursor-pointer border-b border-gray-100 px-5 py-4 transition-colors duration-200 ${
                selectedLead?.id === lead.id ? "bg-blue-50" : "hover:bg-gray-50"
              }`}
            >
              <div className="flex items-start gap-3">
                <ContactAvatar
                  name={lead.nome_contato}
                  photoUrl={lead.foto_url}
                  size="md"
                  className="flex-shrink-0"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-[15px] font-semibold text-gray-900">{lead.nome_contato}</p>
                    {normalizeStatusAtendimento(lead?.status_atendimento) === "concluido" ? (
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-700">
                        Concluído
                      </span>
                    ) : normalizeStatusAtendimento(lead?.status_atendimento) === "manual" || lead.bot_pausado || lead.ia_ativa === false ? (
                      <span className="rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-semibold text-orange-700">
                        Humano
                      </span>
                    ) : null}
                  </div>
                  <p className="truncate text-sm text-gray-500">
                    {lead.historico_resumo || lead.telefone_contato || "Sem mensagens recentes"}
                  </p>
                  {lead.etapa_crm ? (
                    <p className="mt-1 truncate text-[11px] font-medium text-gray-400">{lead.etapa_crm}</p>
                  ) : null}
                </div>
              </div>
            </button>
          ))}
          {!leadsLoading && !leadsError && (!filteredLeads || filteredLeads.length === 0) && (
            <div className="inbox-list-state"><EmptyState title="Nenhuma conversa encontrada" description="Nenhuma conversa encontrada para os filtros atuais. Ajuste a busca, o status ou o departamento." /></div>
          )}
        </div>
      </aside>

      <section aria-label="Conversa selecionada" className={`inbox-conversation h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-[#f0f2f5] ${selectedLead && !showConversationList ? "flex" : "hidden lg:flex"}`}>
        {selectedLead ? (
          <>
            <header className="chat-conversation-header z-20 shrink-0 border-b border-gray-200 bg-white px-5 py-4 shadow-sm">
              <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex min-w-0 flex-1 items-center gap-2 lg:gap-3">
                  <button
                    type="button"
                    onClick={() => {
                      setShowConversationList(true);
                    }}
                    className="shrink-0 rounded-lg p-2 text-gray-600 transition-colors hover:bg-gray-100 lg:hidden"
                    title="Voltar para conversas"
                    aria-label="Voltar para conversas"
                  >
                    <ArrowLeft size={18} />
                  </button>
                  <ContactAvatar
                    name={selectedLead.nome_contato}
                    photoUrl={selectedLead.foto_url}
                    size="lg"
                    className="flex-shrink-0"
                  />
                  <div className="inbox-contact-identity min-w-0">
                    <h3 className="truncate text-base font-semibold text-gray-900">{selectedLead.nome_contato}</h3>
                    <p className="truncate text-sm text-gray-500">{selectedLead.telefone_contato}</p>
                    {selectedLead.etapa_crm && <p className="inbox-contact-stage" title={selectedLead.etapa_crm}>CRM · {selectedLead.etapa_crm}</p>}
                  </div>
                </div>

                <div className="inbox-header-actions flex w-full min-w-0 flex-wrap items-center gap-2 lg:w-auto lg:flex-nowrap">
                  <button ref={journeyButtonRef} type="button" onClick={() => setShowJourney(value => !value)} aria-haspopup="dialog" aria-expanded={showJourney} aria-controls="inbox-journey-panel" className="inbox-journey-trigger rounded-lg px-2.5 py-2 text-xs font-medium text-indigo-700 transition-colors hover:bg-indigo-50">Jornada comercial</button>
                  <button
                    type="button"
                    onClick={handleToggleStatus}
                    disabled={statusToggleLoading}
                    className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs font-medium transition-colors ${
                      statusConcluir
                        ? "border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
                        : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                    } disabled:cursor-not-allowed disabled:opacity-70`}
                    title={statusConcluir ? "Reabrir atendimento" : "Concluir atendimento"}
                  >
                    {statusToggleLoading ? <RefreshCw size={14} className="animate-spin" aria-label="Atualizando atendimento" /> : <Check size={14} />}
                    {statusConcluir ? "Reabrir" : "Concluir"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowTagsPanel((prev) => !prev)}
                    aria-expanded={showTagsPanel}
                    aria-controls="inbox-tags-panel"
                    className="rounded-lg px-2.5 py-2 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-100"
                  >
                    Tags
                  </button>
                  <button
                    type="button"
                    onClick={handleToggleBot}
                    disabled={botToggleLoading}
                    aria-busy={botToggleLoading}
                    className={`chat-header-ai basis-full flex min-w-0 max-w-full flex-1 items-center gap-2 rounded-full border px-2.5 py-1.5 transition-colors lg:basis-auto ${
                      selectedLeadPaused
                        ? "border-orange-200 bg-orange-50 hover:bg-orange-100"
                        : "border-emerald-200 bg-emerald-50 hover:bg-emerald-100"
                    } disabled:cursor-not-allowed disabled:opacity-70`}
                    title={selectedLeadPaused ? "Ativar respostas da IA" : "Desativar IA para este lead"}
                  >
                    <span className="flex min-w-0 flex-1 flex-col items-start overflow-hidden text-left leading-tight">
                      <span className={`block max-w-full truncate text-[11px] font-semibold ${selectedLeadPaused ? "text-orange-700" : "text-emerald-700"}`}>
                        {aiEffectiveLabel}
                      </span>
                      {selectedLeadPaused && selectedLeadPausedAtLabel ? (
                        <span className="text-[10px] text-orange-600">Retorna às {selectedLeadPausedAtLabel}</span>
                      ) : (
                        <span
                          className="block truncate text-[10px] text-gray-500"
                          title={aiEffectiveReason ? `Gate: ${aiEffectiveReason}` : undefined}
                        >
                          {aiEffectiveDescription}
                        </span>
                      )}
                    </span>
                    <span className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${selectedLeadPaused ? "bg-orange-400" : "bg-emerald-500"}`}>
                      <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform ${selectedLeadPaused ? "translate-x-1" : "translate-x-5"}`} />
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowTransferPanel((prev) => !prev)}
                    aria-expanded={showTransferPanel}
                    aria-controls="inbox-transfer-panel"
                    aria-label="Transferir"
                    className="rounded-lg p-2 text-emerald-700 transition-colors hover:bg-emerald-50"
                    title="Transferir"
                  >
                    <ArrowRightLeft size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={handleExcluirLead}
                    title="Excluir lead"
                    aria-label="Excluir lead"
                    className="rounded-lg p-2 text-red-600 transition-colors hover:bg-red-50"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {(showTagsPanel || showTransferPanel) ? (
                <div className="inbox-context-controls mt-3 space-y-2">
                  {showTagsPanel ? (
                    <div id="inbox-tags-panel" className="rounded-xl border border-gray-200 bg-gray-50 p-2.5">
                      <LeadTagsEditor
                        tags={normalizeLeadTags(selectedLead?.tags)}
                        compact
                        placeholder="Buscar tags oficiais..."
                        onChange={(nextTags) => handleLeadTagsChange(selectedLead.id, nextTags)}
                        tagDefinitions={tagsOficiais}
                      />
                    </div>
                  ) : null}
                  {showTransferPanel ? (
                    <div id="inbox-transfer-panel" className="rounded-xl border border-emerald-200 bg-emerald-50 p-2.5">
                      <div className="flex gap-2">
                        <select
                          aria-label="Destino da transferência"
                          value={selectedDestinoId}
                          onChange={(e) => setSelectedDestinoId(e.target.value)}
                          className="flex-1 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none focus:ring-2 focus:ring-emerald-500"
                        >
                          <option value="">Selecione um destino</option>
                          {destinos.map((destino) => (
                            <option key={destino.id} value={destino.id}>
                              {destino.nome_destino}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={handleTransferirManual}
                          disabled={transferindo || !selectedDestinoId}
                          className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {transferindo ? <RefreshCw size={14} className="animate-spin" /> : "Confirmar"}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </header>

            <div className="inbox-workspace min-h-0 flex-1">
            <div ref={historyScrollRef} onScroll={event => {
              const node = event.currentTarget;
              readingScrollRef.current = node.scrollTop;
              stickToBottomRef.current = node.scrollHeight - node.clientHeight - node.scrollTop <= 96;
            }} data-testid="inbox-message-scroll" aria-label="Histórico de mensagens" aria-busy={messagesLoading} className="inbox-message-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-6 xl:px-8">
              {connectionState > 1 && <p role="status" className="inbox-reconnect-notice">Conexão interrompida. Aguardando reconexão para receber novas atualizações.</p>}
              {messagesError && <div role="alert" className="inbox-history-state"><InfoBanner tone="danger">Não foi possível carregar o histórico.</InfoBanner><ActionButton variant="secondary" onClick={() => fetchMessages(selectedLead.telefone_contato)}>Tentar novamente</ActionButton></div>}
              {messagesLoading && !messages.length && <div className="inbox-history-state"><LoadingState label="Carregando histórico…" /></div>}
              <MessageList
                messages={messages || []}
                renderMessageContent={renderMensagemConteudo}
                messagesEndRef={messagesEndRef}
                contactName={selectedLead?.nome_contato}
                contactPhotoUrl={selectedLead?.foto_url}
                hideEmptyState={messagesLoading || messagesError}
              />
            </div>
            </div>

            <footer className="inbox-composer shrink-0 border-t border-gray-200 bg-gray-100 p-4">
              <div className="w-full">
                {selectedFile ? (
                  <div className="mb-2 rounded-xl border border-gray-200 bg-white p-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2 text-sm text-gray-700">
                        {selectedFile.type.startsWith("image/") ? (
                          <FileImage size={16} />
                        ) : selectedFile.type.startsWith("audio/") ? (
                          <FileAudio size={16} />
                        ) : selectedFile.type.startsWith("video/") ? (
                          <FileVideo size={16} />
                        ) : (
                          <FileText size={16} />
                        )}
                        <span className="max-w-[320px] truncate font-medium">{selectedFile.name}</span>
                      </div>
                      <button type="button" aria-label="Retirar anexo" onClick={clearSelectedFile} className="rounded-md p-1 text-gray-500 hover:bg-gray-100">
                        <X size={14} />
                      </button>
                    </div>
                    <input
                      value={mediaCaption}
                      onChange={(e) => setMediaCaption(e.target.value)}
                      placeholder="Legenda opcional..."
                      aria-label="Legenda opcional do anexo"
                      className="mt-2 w-full rounded-full border-none bg-gray-100 px-3 py-2 text-sm outline-none focus:ring-0"
                    />
                  </div>
                ) : null}

                <form onSubmit={handleSendMessage} className="flex items-end gap-2">
                  <input
                    ref={fileInputRef}
                    type="file"
                    className="hidden"
                    accept="image/*,audio/*,video/*,.mp4,.mov,.webm,application/pdf,.pdf,.doc,.docx"
                    onChange={handleSelectFile}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="rounded-full bg-white p-2.5 text-gray-600 shadow-sm transition-colors hover:bg-gray-50"
                    title="Anexar"
                    aria-label="Anexar"
                  >
                    <Paperclip size={18} />
                  </button>
                  {isRecording ? (
                    <div className="flex flex-1 items-center justify-between gap-3 rounded-3xl bg-white px-4 py-3 shadow-sm">
                      <div className="flex items-center gap-2 text-sm text-gray-700">
                        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-500" />
                        <span className="font-medium">{formatRecordingTime(recordingTime)}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => stopRecording(false)}
                          className="rounded-full p-2 text-red-600 transition-colors hover:bg-red-50"
                          title="Cancelar gravação"
                        >
                          <Trash2 size={16} />
                        </button>
                        <button
                          type="button"
                          onClick={() => stopRecording(true)}
                          className="rounded-full bg-emerald-600 p-2 text-white transition-colors hover:bg-emerald-700"
                          title="Finalizar gravação"
                        >
                          <Send size={16} />
                        </button>
                      </div>
                    </div>
                  ) : recordedAudioUrl ? (
                    <div className="flex flex-1 items-center gap-2 rounded-3xl bg-white px-3 py-2 shadow-sm">
                      <button
                        type="button"
                        onClick={clearRecordedAudioPreview}
                        className="rounded-full p-2 text-red-600 transition-colors hover:bg-red-50"
                        title="Descartar áudio"
                      >
                        <Trash2 size={16} />
                      </button>
                      <audio src={recordedAudioUrl} controls className="h-10 flex-1" />
                      <button
                        type="button"
                        onClick={handleSendRecordedAudio}
                        disabled={isSendingMedia}
                        className="rounded-full bg-emerald-600 p-2 text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-70"
                        title="Enviar áudio"
                      >
                        <Send size={16} />
                      </button>
                    </div>
                  ) : (
                    <>
                      <textarea
                        value={newMessage}
                        onChange={(e) => setNewMessage(e.target.value)}
                        onKeyDown={handleComposerKeyDown}
                        placeholder="Digite uma mensagem"
                        aria-label="Digite uma mensagem"
                        rows={1}
                        className="max-h-32 min-h-[56px] flex-1 resize-none rounded-3xl border-none bg-white px-3 py-2.5 text-[13px] outline-none shadow-sm focus:ring-0 lg:min-h-[50px] lg:px-5 lg:py-3.5 lg:text-[15px]"
                      />
                      {selectedFile ? (
                        <button
                          type="button"
                          onClick={handleSendMedia}
                          aria-label={isSendingMedia ? "Enviando anexo" : "Enviar anexo"}
                          disabled={isSendingMedia}
                          className={`rounded-full p-3 text-white shadow-sm transition-colors ${
                            hasPayload ? "bg-emerald-600 hover:bg-emerald-700" : "bg-gray-300"
                          } disabled:cursor-not-allowed disabled:opacity-80`}
                        >
                          {isSendingMedia ? <RefreshCw size={18} className="animate-spin" /> : <Send size={18} />}
                        </button>
                      ) : newMessage.trim() ? (
                        <button
                          type="submit"
                          aria-label="Enviar mensagem"
                          className={`rounded-full p-3 text-white shadow-sm transition-colors ${
                            hasPayload ? "bg-emerald-600 hover:bg-emerald-700" : "bg-gray-300"
                          } disabled:cursor-not-allowed disabled:opacity-80`}
                        >
                          <Send size={18} />
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={startRecording}
                          className="rounded-full bg-white p-3 text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
                          title="Gravar áudio"
                        >
                          <Mic size={18} />
                        </button>
                      )}
                    </>
                  )}
                </form>
              </div>
            </footer>
          </>
        ) : (
          <div className="inbox-selection-empty flex flex-1 flex-col items-center justify-center text-gray-500">
            <MessageSquare size={54} className="mb-3 text-gray-400" />
            <p className="text-base font-medium">Selecione uma conversa</p>
            <p className="text-sm text-gray-400">Escolha um lead na lateral para iniciar o atendimento.</p>
          </div>
        )}
      </section>
      {showJourney && selectedLead?.id && <CommercialJourneyDialog empresaId={empresa_id} leadId={selectedLead.id} contactName={selectedLead.nome_contato} onClose={closeJourney} returnFocusRef={journeyButtonRef} />}
      </div>
    </div>
  );
}
