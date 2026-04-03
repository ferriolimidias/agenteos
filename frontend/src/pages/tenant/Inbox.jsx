import React, { useState, useEffect, useMemo, useRef } from "react";
import { ArrowRightLeft, Check, FileAudio, FileImage, FileText, MessageSquare, Paperclip, RefreshCw, Search, Send, Trash2, X } from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";
import LeadTagsEditor from "../../components/LeadTagsEditor";
import MessageList from "../../components/MessageList";
import ContactAvatar from "../../components/ContactAvatar";

export default function Inbox() {
  const [leads, setLeads] = useState([]);
  const [selectedLead, setSelectedLead] = useState(null);
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [destinos, setDestinos] = useState([]);
  const [grupos, setGrupos] = useState([]);
  const [tagsOficiais, setTagsOficiais] = useState([]);
  const [showTransferPanel, setShowTransferPanel] = useState(false);
  const [showTagsPanel, setShowTagsPanel] = useState(false);
  const [selectedDestinoId, setSelectedDestinoId] = useState("");
  const [transferindo, setTransferindo] = useState(false);
  const [botToggleLoading, setBotToggleLoading] = useState(false);
  const [statusToggleLoading, setStatusToggleLoading] = useState(false);
  const [filtroStatus, setFiltroStatus] = useState("aberto");
  const [abaAtiva, setAbaAtiva] = useState("Todos");
  const [searchTerm, setSearchTerm] = useState("");
  const [toast, setToast] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [mediaCaption, setMediaCaption] = useState("");
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);
  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const reconnectAttempts = useRef(0);
  const failedReconnectTimestampsRef = useRef([]);
  const reconnectBlockedUntilRef = useRef(0);
  const socketOpenedRef = useRef(false);
  const selectedLeadTelefoneRef = useRef(null);
  const leadsFetchInFlightRef = useRef(false);
  const leadsFetchQueuedRef = useRef(false);
  const leadsRefreshTimerRef = useRef(null);
  const heartbeatIntervalRef = useRef(null);

  const user = getStoredUser();
  const activeEmpresaId = getActiveEmpresaId();
  const empresa_id = activeEmpresaId;

  const fetchLeads = async () => {
    if (!empresa_id) return;
    if (leadsFetchInFlightRef.current) {
      leadsFetchQueuedRef.current = true;
      return;
    }

    leadsFetchInFlightRef.current = true;
    try {
      const res = await api.get(`/empresas/${empresa_id}/inbox`);
      const nextLeads = res.data || [];
      setLeads(nextLeads);
      setSelectedLead((prev) => {
        if (!prev?.id) return prev;
        return nextLeads.find((lead) => lead.id === prev.id) || prev;
      });
    } catch (e) {
      console.error("Erro ao buscar leads no Inbox:", e);
      setLeads([]);
    } finally {
      leadsFetchInFlightRef.current = false;
      if (leadsFetchQueuedRef.current) {
        leadsFetchQueuedRef.current = false;
        void fetchLeads();
      }
    }
  };

  const scheduleFetchLeads = (delayMs = 300) => {
    if (leadsRefreshTimerRef.current) {
      window.clearTimeout(leadsRefreshTimerRef.current);
    }
    leadsRefreshTimerRef.current = window.setTimeout(() => {
      leadsRefreshTimerRef.current = null;
      void fetchLeads();
    }, delayMs);
  };

  const fetchMessages = async (telefone) => {
    if (!empresa_id || !telefone) return;
    try {
      const res = await api.get(`/empresas/${empresa_id}/inbox/${telefone}`);
      setMessages(res.data || []);
    } catch (e) {
      console.error("Erro ao buscar mensagens do lead no Inbox:", e);
      setMessages([]);
    }
  };

  const fetchLeadPhoto = async (lead) => {
    const leadId = lead?.id;
    const telefone = lead?.telefone_contato;
    if (!empresa_id || !leadId || !telefone) return;

    try {
      const res = await api.get(`/empresas/${empresa_id}/inbox/${telefone}/foto`);
      const fotoUrl = res.data?.foto_url || null;
      updateLeadInState(leadId, { foto_url: fotoUrl });
    } catch (e) {
      console.error("Erro ao buscar foto do lead no Inbox:", e);
    }
  };

  const fetchDestinos = async () => {
    if (!empresa_id) return;
    try {
      const res = await api.get(`/empresas/${empresa_id}/transferencias/destinos`);
      setDestinos(res.data || []);
    } catch (e) {
      console.error("Erro ao buscar destinos de transferência:", e);
      setDestinos([]);
    }
  };

  const fetchGruposETags = async () => {
    if (!empresa_id) return;
    try {
      const [gruposRes, tagsRes] = await Promise.all([
        api.get(`/empresas/${empresa_id}/crm/tags/grupos`),
        api.get(`/empresas/${empresa_id}/crm/tags/oficiais`),
      ]);
      setGrupos(gruposRes.data || []);
      setTagsOficiais(tagsRes.data || []);
    } catch (e) {
      console.error("Erro ao buscar grupos e tags oficiais:", e);
      setGrupos([]);
      setTagsOficiais([]);
    }
  };

  useEffect(() => {
    fetchLeads();
    fetchDestinos();
    fetchGruposETags();
  }, [empresa_id]);

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

  useEffect(() => {
    setShowTransferPanel(false);
    setShowTagsPanel(false);
    setSelectedDestinoId("");
  }, [selectedLead?.id]);

  useEffect(() => {
    const telefone = selectedLead?.telefone_contato;
    if (!empresa_id || !telefone) return;
    fetchMessages(telefone);
  }, [empresa_id, selectedLead?.telefone_contato]);

  useEffect(() => {
    if (!empresa_id || !selectedLead?.id || !selectedLead?.telefone_contato || selectedLead?.foto_url) return;
    fetchLeadPhoto(selectedLead);
  }, [empresa_id, selectedLead?.id, selectedLead?.telefone_contato, selectedLead?.foto_url]);

  useEffect(() => {
    selectedLeadTelefoneRef.current = selectedLead?.telefone_contato || null;
  }, [selectedLead?.telefone_contato]);

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

      const wsUrl =
        (window.location.protocol === "https:" ? "wss://" : "ws://") +
        window.location.host +
        "/api/empresas/" +
        activeEmpresaId +
        "/ws";
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      socketOpenedRef.current = false;

      ws.onopen = () => {
        if (!isActive) return;
        reconnectAttempts.current = 0;
        failedReconnectTimestampsRef.current = [];
        reconnectBlockedUntilRef.current = 0;
        socketOpenedRef.current = true;
        if (heartbeatIntervalRef.current) {
          window.clearInterval(heartbeatIntervalRef.current);
        }
        heartbeatIntervalRef.current = window.setInterval(() => {
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            try {
              wsRef.current.send(JSON.stringify({ type: "ping", ts: Date.now() }));
            } catch (_) {
              // no-op
            }
          }
        }, 25000);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data || "{}");
          const tipoEvento = String(data?.tipo_evento || "").toLowerCase();
          if (tipoEvento === "atualizacao_lead") {
            const leadPayload = data?.lead || {};
            const leadId = String(leadPayload?.id || data?.lead_id || "");
            if (!leadId) {
              scheduleFetchLeads(150);
              return;
            }
            setLeads((prev) => {
              const lista = prev || [];
              const jaExiste = lista.some((lead) => String(lead?.id || "") === leadId);
              const leadAtualizado = {
                id: leadId,
                nome_contato: String(leadPayload?.nome || ""),
                telefone_contato: String(leadPayload?.telefone || ""),
                foto_url: leadPayload?.foto_url || null,
                status_atendimento: "aberto",
                bot_pausado: false,
                bot_pausado_ate: null,
                tags: [],
                historico_resumo: "",
                dados_adicionais: {},
              };
              if (!jaExiste) return [leadAtualizado, ...lista];
              return lista.map((lead) => (String(lead?.id || "") === leadId ? { ...lead, ...leadAtualizado } : lead));
            });
            setSelectedLead((prev) => {
              if (!prev?.id || String(prev.id) !== leadId) return prev;
              return {
                ...prev,
                nome_contato: String(leadPayload?.nome || prev.nome_contato || ""),
                telefone_contato: String(leadPayload?.telefone || prev.telefone_contato || ""),
                foto_url: leadPayload?.foto_url || prev.foto_url || null,
              };
            });
            scheduleFetchLeads(150);
            return;
          }

          if (tipoEvento !== "nova_mensagem_inbound" && tipoEvento !== "nova_mensagem_outbound") return;

          const telefoneEvento = String(data?.telefone || "");
          const novaMensagem = data?.mensagem;
          const telefoneSelecionado = String(selectedLeadTelefoneRef.current || "");

          if (telefoneSelecionado && telefoneEvento && telefoneSelecionado === telefoneEvento && novaMensagem) {
            setMessages((prev) => {
              const lista = prev || [];
              const novaId = novaMensagem?.id;
              if (novaId && lista.some((msg) => msg?.id === novaId)) return lista;
              return [...lista, novaMensagem];
            });
          }

          scheduleFetchLeads(250);
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
      try {
        wsRef.current?.close();
      } catch (_) {
        // no-op
      }
      socketOpenedRef.current = false;
      wsRef.current = null;
    };
  }, [activeEmpresaId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSendMessage = async (e) => {
    e.preventDefault();
    const texto = newMessage.trim();
    if (!texto || !selectedLead) return;

    const telefone = selectedLead.telefone_contato;
    const tempId = `temp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const optimisticMessage = {
      id: tempId,
      texto,
      from_me: true,
      tipo_mensagem: "text",
      media_url: null,
      criado_em: new Date().toISOString(),
    };

    setMessages((prev) => [...(prev || []), optimisticMessage]);
    setNewMessage("");
    setLoading(true);
    try {
      await api.post(`/empresas/${empresa_id}/inbox/${telefone}/send`, {
        texto,
      });
      if (selectedLeadTelefoneRef.current === telefone) {
        await fetchMessages(telefone);
      }
      await fetchLeads(); // update `bot_pausado`
    } catch (e) {
      console.error("Erro ao enviar mensagem", e);
      if (selectedLeadTelefoneRef.current === telefone) {
        setMessages((prev) => (prev || []).filter((msg) => msg?.id !== tempId));
      }
      setNewMessage(texto);
      setToast({
        type: "error",
        message: e.response?.data?.detail || "Não foi possível enviar a mensagem.",
      });
    } finally {
      setLoading(false);
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

  const handleSendMedia = async () => {
    if (!selectedLead || !selectedFile) return;
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("caption", mediaCaption || "");
      await api.post(`/empresas/${empresa_id}/inbox/${selectedLead.telefone_contato}/send_media`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      clearSelectedFile();
      await fetchMessages(selectedLead.telefone_contato);
      await fetchLeads();
    } catch (e) {
      console.error("Erro ao enviar mídia", e);
      setToast({
        type: "error",
        message: e.response?.data?.detail || "Não foi possível enviar a mídia.",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleComposerKeyDown = (event) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    if (selectedFile || loading || !newMessage.trim()) return;
    handleSendMessage(event);
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
      return (
        <div className="space-y-2">
          <img
            src={`data:image/jpeg;base64,${media}`}
            alt="Imagem recebida"
            className="max-h-64 max-w-xs rounded-lg object-cover"
          />
          {msg?.texto ? <p className="text-sm whitespace-pre-wrap">{msg.texto}</p> : null}
        </div>
      );
    }

    if (tipo === "audio" && media) {
      return (
        <div className="space-y-2">
          <audio controls src={`data:audio/mpeg;base64,${media}`} className="max-w-xs" />
          {msg?.texto ? <p className="text-sm whitespace-pre-wrap">{msg.texto}</p> : null}
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

    return <p className="text-sm whitespace-pre-wrap">{msg?.texto}</p>;
  };

  const handleToggleBot = async () => {
    if (!selectedLead || botToggleLoading) return;

    const leadId = selectedLead.id;
    const telefone = selectedLead.telefone_contato;
    const wasPaused = Boolean(selectedLead.bot_pausado);
    const optimisticPausedUntil = wasPaused ? null : new Date(Date.now() + (24 * 60 * 60 * 1000)).toISOString();
    const optimisticUpdates = {
      bot_pausado: !wasPaused,
      bot_pausado_ate: optimisticPausedUntil,
    };
    const previousState = {
      bot_pausado: Boolean(selectedLead.bot_pausado),
      bot_pausado_ate: selectedLead.bot_pausado_ate || null,
    };

    updateLeadInState(leadId, optimisticUpdates);
    setBotToggleLoading(true);

    try {
      const endpoint = wasPaused ? "reativar_bot" : "pausar_bot";
      const res = await api.post(`/empresas/${empresa_id}/inbox/${telefone}/${endpoint}`);
      updateLeadInState(leadId, {
        bot_pausado: !wasPaused,
        bot_pausado_ate: wasPaused ? null : (res.data?.bot_pausado_ate || optimisticPausedUntil),
      });
      await fetchLeads();
    } catch (e) {
      console.error("Erro ao alternar estado do bot", e);
      updateLeadInState(leadId, previousState);
      setToast({
        type: "error",
        message: e.response?.data?.detail || "Não foi possível atualizar o estado da IA.",
      });
    } finally {
      setBotToggleLoading(false);
    }
  };

  const handleToggleStatus = async () => {
    if (!selectedLead || statusToggleLoading) return;

    const leadId = selectedLead.id;
    const currentStatus = String(selectedLead.status_atendimento || "aberto").toLowerCase();
    const nextStatus = currentStatus === "concluido" ? "aberto" : "concluido";
    const previousState = { status_atendimento: currentStatus };

    updateLeadInState(leadId, { status_atendimento: nextStatus });
    setStatusToggleLoading(true);

    try {
      await api.put(`/empresas/${empresa_id}/crm/leads/${leadId}`, {
        status_atendimento: nextStatus,
      });
      await fetchLeads();
    } catch (e) {
      console.error("Erro ao atualizar status do atendimento", e);
      updateLeadInState(leadId, previousState);
      setToast({
        type: "error",
        message: e.response?.data?.detail || "Não foi possível atualizar o status do atendimento.",
      });
    } finally {
      setStatusToggleLoading(false);
    }
  };

  const handleExcluirLead = async () => {
    if (!selectedLead) return;
    if (!window.confirm("Certeza que deseja excluir permanentemente este lead e todo o seu histórico de mensagens?")) return;
    try {
      await api.delete(`/empresas/${empresa_id}/leads/${selectedLead.id}`);
      setSelectedLead(null);
      setMessages([]);
      fetchLeads();
    } catch (e) {
      console.error("Erro ao excluir lead", e);
      alert("Erro ao excluir o lead.");
    }
  };

  const updateLeadInState = (leadId, updates) => {
    setLeads((prev) => (prev || []).map((lead) => (lead.id === leadId ? { ...lead, ...updates } : lead)));
    setSelectedLead((prev) => (prev?.id === leadId ? { ...prev, ...updates } : prev));
  };

  const handleSelectLead = (lead) => {
    setSelectedLead(lead);
  };

  const handleLeadTagsChange = async (leadId, nextTags) => {
    await api.put(`/empresas/${empresa_id}/crm/leads/${leadId}`, { tags: nextTags });
    updateLeadInState(leadId, { tags: nextTags });
  };

  const handleTransferirManual = async () => {
    if (!selectedLead?.id || !selectedDestinoId) return;
    try {
      setTransferindo(true);
      const res = await api.post(`/empresas/${empresa_id}/leads/${selectedLead.id}/transferir_manual`, {
        destino_id: selectedDestinoId,
      });
      setToast({
        type: "success",
        message: res.data?.detail || "Transferência manual realizada com sucesso.",
      });
      setShowTransferPanel(false);
      setSelectedDestinoId("");
      await fetchLeads();
    } catch (e) {
      console.error("Erro ao transferir lead manualmente:", e);
      setToast({
        type: "error",
        message: e.response?.data?.detail || "Erro ao transferir o lead manualmente.",
      });
    } finally {
      setTransferindo(false);
    }
  };

  if (!user || !empresa_id) {
    return (
      <div className="flex h-[calc(100vh-12rem)] items-center justify-center overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="text-center text-gray-500">
          <MessageSquare size={48} className="mx-auto mb-4 opacity-50" />
          <h2 className="text-xl font-semibold">Carregando Ambiente...</h2>
          <p className="mt-2 text-sm">Autenticando sessão do usuário.</p>
        </div>
      </div>
    );
  }

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

  const normalizeLeadTags = (rawTags) => {
    if (Array.isArray(rawTags)) return rawTags;
    if (!rawTags) return [];
    if (typeof rawTags === "string") {
      const normalizedTag = rawTags.trim();
      return normalizedTag ? [normalizedTag] : [];
    }
    if (typeof rawTags === "object") {
      if (Array.isArray(rawTags.tags)) return rawTags.tags;
      if (Array.isArray(rawTags.items)) return rawTags.items;
      if (Array.isArray(rawTags.values)) return rawTags.values;
      return Object.values(rawTags).filter((value) => value && (typeof value === "string" || typeof value === "object"));
    }
    return [];
  };

  const statusAbertos = new Set(["aberto", "aguardando_humano", "manual"]);

  const filteredLeads = useMemo(() => {
    const termo = String(searchTerm || "").trim().toLowerCase();
    const leadsFiltrados = (leads || []).filter((lead) => {
      const statusLead = normalizeStatusAtendimento(lead?.status_atendimento);
      const statusSelecionado = normalizeStatusAtendimento(filtroStatus);
      if (statusSelecionado === "aberto") {
        if (!statusAbertos.has(statusLead)) return false;
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
  const selectedLeadPaused = Boolean(selectedLead?.bot_pausado);
  const selectedLeadPausedAtLabel = selectedLead?.bot_pausado_ate
    ? new Date(selectedLead.bot_pausado_ate).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "";
  const selectedLeadStatus = String(selectedLead?.status_atendimento || "aberto").toLowerCase();
  const statusConcluir = selectedLeadStatus === "concluido";

  return (
    <div className="relative flex h-[calc(100vh-4rem)] w-full overflow-hidden bg-white">
      {toast ? (
        <div className="fixed right-6 top-6 z-[80]">
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
              <button type="button" onClick={() => setToast(null)} className="rounded-lg p-1 opacity-70 hover:opacity-100">
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <aside className="flex h-full w-full shrink-0 flex-col border-r border-gray-200 bg-white md:w-[320px] lg:w-[360px]">
        <div className="border-b border-gray-200 px-4 py-4">
          <h2 className="text-lg font-semibold text-gray-800">Conversas</h2>
        </div>
        <div className="border-b border-gray-100 px-4 py-3">
          <div className="inline-flex rounded-xl bg-gray-100 p-1">
            {[
              { id: "aberto", label: "Abertos" },
              { id: "concluido", label: "Concluídos" },
            ].map((status) => (
              <button
                key={status.id}
                type="button"
                onClick={() => setFiltroStatus(status.id)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
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
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Buscar por nome ou telefone"
              className="w-full rounded-xl border border-gray-200 bg-white py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <select
            value={abaAtiva}
            onChange={(e) => setAbaAtiva(e.target.value)}
            className="mb-3 mt-3 w-full rounded-md border border-gray-200 bg-gray-50 p-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
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
        <div className="flex-1 overflow-y-auto">
          {filteredLeads?.map((lead) => (
            <div
              key={lead.id}
              onClick={() => handleSelectLead(lead)}
              className={`cursor-pointer border-b border-gray-100 px-4 py-3 transition-colors duration-200 ${
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
                    <p className="truncate text-sm font-semibold text-gray-900">{lead.nome_contato}</p>
                    {normalizeStatusAtendimento(lead?.status_atendimento) === "concluido" ? (
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-700">
                        Concluído
                      </span>
                    ) : normalizeStatusAtendimento(lead?.status_atendimento) === "manual" || lead.bot_pausado ? (
                      <span className="rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-semibold text-orange-700">
                        Humano
                      </span>
                    ) : null}
                  </div>
                  <p className="truncate text-xs text-gray-500">
                    {lead.historico_resumo || lead.telefone_contato || "Sem mensagens recentes"}
                  </p>
                  {lead.etapa_crm ? (
                    <p className="mt-1 truncate text-[11px] font-medium text-gray-400">{lead.etapa_crm}</p>
                  ) : null}
                </div>
              </div>
            </div>
          ))}
          {(!filteredLeads || filteredLeads.length === 0) && (
            <div className="p-6 text-center text-sm text-gray-400">
              Nenhuma conversa encontrada para os filtros atuais.
            </div>
          )}
        </div>
      </aside>

      <section className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-[#f0f2f5]">
        {selectedLead ? (
          <>
            <header className="z-20 border-b border-gray-200 bg-white px-4 py-3 shadow-sm">
              <div className="flex items-center justify-between gap-4">
                <div className="flex min-w-0 items-center gap-3">
                  <ContactAvatar
                    name={selectedLead.nome_contato}
                    photoUrl={selectedLead.foto_url}
                    size="lg"
                    className="flex-shrink-0"
                  />
                  <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold text-gray-900">{selectedLead.nome_contato}</h3>
                    <p className="truncate text-xs text-gray-500">{selectedLead.telefone_contato}</p>
                  </div>
                </div>

                <div className="flex items-center gap-2">
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
                    <Check size={14} />
                    {statusConcluir ? "Reabrir" : "Concluir"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowTagsPanel((prev) => !prev)}
                    className="rounded-lg px-2.5 py-2 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-100"
                  >
                    Tags
                  </button>
                  <button
                    type="button"
                    onClick={handleToggleBot}
                    disabled={botToggleLoading}
                    className={`flex items-center gap-2 rounded-full border px-2.5 py-1.5 transition-colors ${
                      selectedLeadPaused
                        ? "border-orange-200 bg-orange-50 hover:bg-orange-100"
                        : "border-emerald-200 bg-emerald-50 hover:bg-emerald-100"
                    } disabled:cursor-not-allowed disabled:opacity-70`}
                    title={selectedLeadPaused ? "Ativar respostas da IA" : "Assumir atendimento humano"}
                  >
                    <span className="flex flex-col items-start text-left leading-tight">
                      <span className={`text-[11px] font-semibold ${selectedLeadPaused ? "text-orange-700" : "text-emerald-700"}`}>
                        {selectedLeadPaused ? "Modo Humano" : "IA Respondendo"}
                      </span>
                      {selectedLeadPaused && selectedLeadPausedAtLabel ? (
                        <span className="text-[10px] text-orange-600">Retorna às {selectedLeadPausedAtLabel}</span>
                      ) : (
                        <span className="text-[10px] text-gray-500">
                          {selectedLeadPaused ? "Pausada manualmente" : "Resposta automática ativa"}
                        </span>
                      )}
                    </span>
                    <span
                      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                        selectedLeadPaused ? "bg-orange-400" : "bg-emerald-500"
                      }`}
                    >
                      <span
                        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform ${
                          selectedLeadPaused ? "translate-x-1" : "translate-x-5"
                        }`}
                      />
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowTransferPanel((prev) => !prev)}
                    className="rounded-lg p-2 text-emerald-700 transition-colors hover:bg-emerald-50"
                    title="Transferir"
                  >
                    <ArrowRightLeft size={16} />
                  </button>
                  <button
                    onClick={handleExcluirLead}
                    title="Excluir lead"
                    className="rounded-lg p-2 text-red-600 transition-colors hover:bg-red-50"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {(showTagsPanel || showTransferPanel) ? (
                <div className="mt-3 space-y-2">
                  {showTagsPanel ? (
                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-2.5">
                      <LeadTagsEditor
                        tags={selectedLead.tags || []}
                        compact
                        placeholder="Buscar tags oficiais..."
                        onChange={(nextTags) => handleLeadTagsChange(selectedLead.id, nextTags)}
                        tagDefinitions={tagsOficiais}
                      />
                    </div>
                  ) : null}

                  {showTransferPanel ? (
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-2.5">
                      <div className="flex gap-2">
                        <select
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

            <div className="flex-1 overflow-y-auto px-4 py-5">
              <MessageList
                messages={messages || []}
                renderMessageContent={renderMensagemConteudo}
                messagesEndRef={messagesEndRef}
                contactName={selectedLead?.nome_contato}
                contactPhotoUrl={selectedLead?.foto_url}
              />
            </div>

            <footer className="border-t border-gray-200 bg-gray-100 p-3">
              <div className="mx-auto max-w-4xl">
                {selectedFile ? (
                  <div className="mb-2 rounded-xl border border-gray-200 bg-white p-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2 text-sm text-gray-700">
                        {selectedFile.type.startsWith("image/") ? <FileImage size={16} /> : selectedFile.type.startsWith("audio/") ? <FileAudio size={16} /> : <FileText size={16} />}
                        <span className="max-w-[320px] truncate font-medium">{selectedFile.name}</span>
                      </div>
                      <button type="button" onClick={clearSelectedFile} className="rounded-md p-1 text-gray-500 hover:bg-gray-100">
                        <X size={14} />
                      </button>
                    </div>
                    <input
                      value={mediaCaption}
                      onChange={(e) => setMediaCaption(e.target.value)}
                      placeholder="Legenda opcional..."
                      className="mt-2 w-full rounded-full border-none bg-gray-100 px-3 py-2 text-sm outline-none focus:ring-0"
                    />
                  </div>
                ) : null}

                <form onSubmit={handleSendMessage} className="flex items-end gap-2">
                  <input
                    ref={fileInputRef}
                    type="file"
                    className="hidden"
                    accept="image/*,audio/*,application/pdf,.pdf,.doc,.docx"
                    onChange={handleSelectFile}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="rounded-full bg-white p-2.5 text-gray-600 shadow-sm transition-colors hover:bg-gray-50"
                    title="Anexar"
                  >
                    <Paperclip size={18} />
                  </button>
                  <textarea
                    value={newMessage}
                    onChange={(e) => setNewMessage(e.target.value)}
                    onKeyDown={handleComposerKeyDown}
                    placeholder="Digite uma mensagem"
                    rows={1}
                    className="max-h-32 min-h-[44px] flex-1 resize-none rounded-3xl border-none bg-white px-4 py-3 text-sm outline-none shadow-sm focus:ring-0"
                    disabled={loading}
                  />
                  <button
                    type={selectedFile ? "button" : "submit"}
                    onClick={selectedFile ? handleSendMedia : undefined}
                    disabled={selectedFile ? loading : (!newMessage.trim() || loading)}
                    className={`rounded-full p-3 text-white shadow-sm transition-colors ${
                      hasPayload ? "bg-emerald-600 hover:bg-emerald-700" : "bg-gray-300"
                    } disabled:cursor-not-allowed disabled:opacity-80`}
                  >
                    <Send size={18} />
                  </button>
                </form>
              </div>
            </footer>
          </>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center text-gray-500">
            <MessageSquare size={54} className="mb-3 text-gray-400" />
            <p className="text-base font-medium">Selecione uma conversa</p>
            <p className="text-sm text-gray-400">Escolha um lead na lateral para iniciar o atendimento.</p>
          </div>
        )}
      </section>
    </div>
  );
}
