import React, { useState, useEffect, useRef } from "react";
import { ArrowRightLeft, FileAudio, FileImage, FileText, MessageSquare, Paperclip, RefreshCw, Send, Trash2, UserCircle, X } from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";
import LeadTagsEditor from "../../components/LeadTagsEditor";

export default function Inbox() {
  const [leads, setLeads] = useState([]);
  const [selectedLead, setSelectedLead] = useState(null);
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [destinos, setDestinos] = useState([]);
  const [availableTags, setAvailableTags] = useState([]);
  const [showTransferPanel, setShowTransferPanel] = useState(false);
  const [showTagsPanel, setShowTagsPanel] = useState(false);
  const [selectedDestinoId, setSelectedDestinoId] = useState("");
  const [transferindo, setTransferindo] = useState(false);
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

  const user = getStoredUser();
  const activeEmpresaId = getActiveEmpresaId();
  const empresa_id = activeEmpresaId;

  const fetchLeads = async () => {
    if (!empresa_id) return;
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
    }
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

  const fetchOfficialTags = async () => {
    if (!empresa_id) return;
    try {
      const res = await api.get(`/empresas/${empresa_id}/crm/tags/oficiais`);
      setAvailableTags(res.data || []);
    } catch (e) {
      console.error("Erro ao buscar tags oficiais:", e);
      setAvailableTags([]);
    }
  };

  useEffect(() => {
    fetchLeads();
    fetchDestinos();
    fetchOfficialTags();
  }, [empresa_id]);

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
    if (selectedLead) {
      fetchMessages(selectedLead.telefone_contato);
    }
  }, [selectedLead, empresa_id]);

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
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data || "{}");
          const tipoEvento = String(data?.tipo_evento || "").toLowerCase();
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

          fetchLeads();
        } catch (err) {
          console.error("Erro ao processar evento websocket do Inbox:", err);
        }
      };

      ws.onerror = (err) => {
        console.error("Erro no WebSocket do Inbox:", err);
      };

      ws.onclose = () => {
        if (!isActive) return;
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
    if (!newMessage.trim() || !selectedLead) return;
    setLoading(true);
    try {
      await api.post(`/empresas/${empresa_id}/inbox/${selectedLead.telefone_contato}/send`, {
        texto: newMessage
      });
      setNewMessage("");
      fetchMessages(selectedLead.telefone_contato);
      fetchLeads(); // update `bot_pausado`
    } catch (e) {
      console.error("Erro ao enviar mensagem", e);
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

  const handleReativarBot = async () => {
    if (!selectedLead) return;
    try {
      await api.post(`/empresas/${empresa_id}/inbox/${selectedLead.telefone_contato}/reativar_bot`);
      fetchLeads();
      // Update selectedLead locally
      setSelectedLead({...selectedLead, bot_pausado: false});
    } catch (e) {
      console.error("Erro ao reativar bot", e);
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

  const hasPayload = Boolean(selectedFile || newMessage.trim());

  return (
    <div className="relative flex h-[calc(100vh-12rem)] min-h-[640px] overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
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

      <aside className="w-80 flex-shrink-0 border-r border-gray-200 bg-white">
        <div className="border-b border-gray-200 px-4 py-4">
          <h2 className="text-lg font-semibold text-gray-800">Conversas</h2>
        </div>
        <div className="h-[calc(100%-64px)] overflow-y-auto">
          {leads?.map((lead) => (
            <div
              key={lead.id}
              onClick={() => setSelectedLead(lead)}
              className={`cursor-pointer border-b border-gray-100 px-4 py-3 transition-colors duration-200 ${
                selectedLead?.id === lead.id ? "bg-blue-50" : "hover:bg-gray-50"
              }`}
            >
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full bg-gray-200 text-gray-600">
                  <UserCircle size={20} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-sm font-semibold text-gray-900">{lead.nome_contato}</p>
                    {lead.bot_pausado ? (
                      <span className="rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-semibold text-orange-700">
                        Pausado
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
          {(!leads || leads.length === 0) && (
            <div className="p-6 text-center text-sm text-gray-400">
              Nenhuma conversa encontrada.
            </div>
          )}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[#efeae2]">
        {selectedLead ? (
          <>
            <header className="z-20 border-b border-gray-200 bg-white px-4 py-3 shadow-sm">
              <div className="flex items-center justify-between gap-4">
                <div className="flex min-w-0 items-center gap-3">
                  <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full bg-gray-200 text-gray-600">
                    <UserCircle size={22} />
                  </div>
                  <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold text-gray-900">{selectedLead.nome_contato}</h3>
                    <p className="truncate text-xs text-gray-500">{selectedLead.telefone_contato}</p>
                  </div>
                </div>

                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setShowTagsPanel((prev) => !prev)}
                    className="rounded-lg px-2.5 py-2 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-100"
                  >
                    Tags
                  </button>
                  {selectedLead.bot_pausado ? (
                    <button
                      onClick={handleReativarBot}
                      className="rounded-lg px-2.5 py-2 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-50"
                    >
                      Reativar IA
                    </button>
                  ) : null}
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
                        placeholder="Nova tag + Enter"
                        onChange={(nextTags) => handleLeadTagsChange(selectedLead.id, nextTags)}
                        tagDefinitions={availableTags}
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
              <div
                className="mx-auto flex max-w-4xl flex-col"
                style={{
                  backgroundImage:
                    "radial-gradient(rgba(0,0,0,0.03) 1px, transparent 1px)",
                  backgroundSize: "14px 14px",
                }}
              >
                {messages?.map((msg, idx) => {
                  const isMine = msg.from_me;
                  const anterior = idx > 0 ? messages[idx - 1] : null;
                  const mesmoAutorSequencia = Boolean(anterior) && Boolean(anterior?.from_me) === Boolean(msg?.from_me);
                  return (
                    <div
                      key={msg.id}
                      className={`flex ${isMine ? "justify-end" : "justify-start"} ${mesmoAutorSequencia ? "mt-1" : "mt-3"}`}
                    >
                      <div
                        className={`relative max-w-[80%] px-3 py-2 pb-5 shadow-sm ${
                          isMine
                            ? "ml-auto rounded-lg rounded-tr-none bg-[#d9fdd3] text-gray-900"
                            : "mr-auto rounded-lg rounded-tl-none bg-white text-gray-800"
                        }`}
                      >
                        {renderMensagemConteudo(msg)}
                        <div className="absolute bottom-1 right-2 text-xs text-gray-500/80">
                          {msg?.criado_em ? new Date(msg.criado_em).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                        </div>
                      </div>
                    </div>
                  );
                })}
                <div ref={messagesEndRef} />
                {(!messages || messages.length === 0) ? (
                  <div className="py-12 text-center text-sm text-gray-500">Nenhuma mensagem neste histórico.</div>
                ) : null}
              </div>
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

                <form onSubmit={handleSendMessage} className="flex items-center gap-2">
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
                  <input
                    type="text"
                    value={newMessage}
                    onChange={(e) => setNewMessage(e.target.value)}
                    placeholder="Digite uma mensagem"
                    className="flex-1 rounded-full border-none bg-white px-4 py-2.5 text-sm outline-none shadow-sm focus:ring-0"
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
