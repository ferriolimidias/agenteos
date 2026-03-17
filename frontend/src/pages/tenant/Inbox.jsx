import React, { useState, useEffect, useRef } from "react";
import { ArrowRightLeft, BotOff, FileAudio, FileImage, FileText, MessageSquare, Paperclip, RefreshCw, Send, Trash2, UserCircle, X } from "lucide-react";
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
  const [selectedDestinoId, setSelectedDestinoId] = useState("");
  const [transferindo, setTransferindo] = useState(false);
  const [toast, setToast] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [mediaCaption, setMediaCaption] = useState("");
  const messagesEndRef = useRef(null);
  const fileInputRef = useRef(null);

  const user = getStoredUser();
  const empresa_id = getActiveEmpresaId();

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
    const interval = setInterval(fetchLeads, 10000);
    return () => clearInterval(interval);
  }, [empresa_id]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    setShowTransferPanel(false);
    setSelectedDestinoId("");
  }, [selectedLead?.id]);

  useEffect(() => {
    if (selectedLead) {
      fetchMessages(selectedLead.telefone_contato);
      const interval = setInterval(() => fetchMessages(selectedLead.telefone_contato), 5000);
      return () => clearInterval(interval);
    }
  }, [selectedLead, empresa_id]);

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
      <div className="flex h-[80vh] bg-white border border-gray-200 rounded-xl items-center justify-center shadow-sm">
        <div className="text-center text-gray-500">
          <MessageSquare size={48} className="mx-auto mb-4 opacity-50" />
          <h2 className="text-xl font-semibold">Carregando Ambiente...</h2>
          <p className="text-sm mt-2">Autenticando sessão do usuário.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-[80vh] bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
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
      {/* Sidebar: Lista de Leads */}
      <div className="w-80 flex-shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col">
        <div className="p-4 border-b border-gray-200 bg-white">
          <h2 className="text-lg font-semibold text-gray-800">Mensagens</h2>
        </div>
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          {leads?.map((lead) => (
            <div
              key={lead.id}
              onClick={() => setSelectedLead(lead)}
              className={`p-4 border-b border-gray-100 cursor-pointer transition-colors ${
                selectedLead?.id === lead.id ? "bg-blue-50 border-l-4 border-blue-500" : "hover:bg-gray-100"
              }`}
            >
              <div className="flex justify-between items-start mb-1">
                <span className="font-medium text-gray-900 truncate pr-2">
                  {lead.nome_contato}
                </span>
                {lead.etapa_crm === "Aguardando Humano" && (
                  <span className="bg-red-100 text-red-700 text-xs font-semibold px-2 py-0.5 rounded-full flex-shrink-0">
                    Aguardando Humano
                  </span>
                )}
              </div>
              <div className="text-sm text-gray-500 mb-2 truncate">
                {lead.telefone_contato}
              </div>
              {lead.bot_pausado && (
                <div className="flex items-center text-xs text-orange-600 bg-orange-50 w-max px-2 py-0.5 rounded border border-orange-100">
                  <BotOff size={12} className="mr-1" /> Bot Pausado
                </div>
              )}
            </div>
          ))}
          {(!leads || leads.length === 0) && (
            <div className="p-6 text-center text-gray-400 text-sm">
              Nenhum lead encontrado.
            </div>
          )}
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 min-w-0 flex flex-col bg-slate-50 relative">
        {selectedLead ? (
          <>
            {/* Chat Header */}
            <div className="border-b border-gray-200 bg-white px-6 py-4 shadow-sm z-10">
              <div className="flex items-start justify-between gap-4">
                <div className="flex min-w-0 items-start space-x-3">
                  <UserCircle size={40} className="mt-0.5 flex-shrink-0 text-gray-400" />
                  <div className="min-w-0">
                    <h3 className="truncate font-semibold text-gray-800">{selectedLead.nome_contato}</h3>
                    <p className="text-xs text-gray-500">{selectedLead.telefone_contato}</p>
                    {selectedLead.historico_resumo ? (
                      <p className="mt-1 line-clamp-2 max-w-2xl text-xs text-gray-500">
                        {selectedLead.historico_resumo}
                      </p>
                    ) : null}
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  {selectedLead.bot_pausado && (
                    <button
                      onClick={handleReativarBot}
                      className="flex items-center space-x-2 bg-blue-100 text-blue-700 hover:bg-blue-200 px-4 py-2 rounded-lg text-sm font-medium transition-colors border border-blue-200"
                    >
                      <span>Reativar IA</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setShowTransferPanel((prev) => !prev)}
                    className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm font-medium text-emerald-700 transition-colors hover:bg-emerald-100"
                  >
                    <ArrowRightLeft size={16} />
                    Transferir
                  </button>
                  <button
                    onClick={handleExcluirLead}
                    title="Excluir Lead e Histórico"
                    className="flex items-center justify-center bg-red-50 text-red-600 hover:bg-red-100 p-2 rounded-lg transition-colors border border-red-100"
                  >
                    <Trash2 size={20} />
                  </button>
                </div>
              </div>

              <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                <LeadTagsEditor
                  tags={selectedLead.tags || []}
                  compact
                  placeholder="Nova tag + Enter"
                  onChange={(nextTags) => handleLeadTagsChange(selectedLead.id, nextTags)}
                  tagDefinitions={availableTags}
                />

                <div className="space-y-2">
                  {showTransferPanel ? (
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-3">
                      <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-emerald-800">
                        Destino de transferência
                      </label>
                      <div className="flex gap-2">
                        <select
                          value={selectedDestinoId}
                          onChange={(e) => setSelectedDestinoId(e.target.value)}
                          className="flex-1 rounded-xl border border-emerald-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none transition-all focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500"
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
                          className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {transferindo ? <RefreshCw size={14} className="animate-spin" /> : <ArrowRightLeft size={14} />}
                          Confirmar
                        </button>
                      </div>
                      <p className="mt-2 text-xs text-emerald-700">
                        Esta ação reutiliza o mesmo motor de transferência da IA e pausa o bot automaticamente.
                      </p>
                    </div>
                  ) : (
                    <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
                      Use "Transferir" para encaminhar este lead a um destino já configurado.
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Chat Messages */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4 custom-scrollbar">
              {messages?.map((msg) => {
                const isMine = msg.from_me;
                return (
                  <div key={msg.id} className={`flex ${isMine ? "justify-end" : "justify-start"}`}>
                    <div
                      className={`max-w-[72%] rounded-2xl px-4 py-2 shadow-sm relative ${
                        isMine
                          ? "bg-emerald-500 text-white rounded-br-none"
                          : "bg-white text-gray-800 border border-gray-100 rounded-bl-none"
                      }`}
                    >
                      {renderMensagemConteudo(msg)}
                      <span className={`text-[10px] absolute bottom-1 right-2 opacity-70 ${isMine ? "text-emerald-100" : "text-gray-400"}`}>
                        {msg?.criado_em ? new Date(msg.criado_em).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ""}
                      </span>
                    </div>
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
              {(!messages || messages.length === 0) && (
                <div className="flex justify-center items-center h-full">
                  <span className="text-gray-400 text-sm">Nenhuma mensagem neste histórico.</span>
                </div>
              )}
            </div>

            {/* Chat Input */}
            <div className="p-4 bg-white border-t border-gray-200">
              {selectedFile ? (
                <div className="mb-3 rounded-xl border border-gray-200 bg-gray-50 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm text-gray-700">
                      {selectedFile.type.startsWith("image/") ? <FileImage size={16} /> : selectedFile.type.startsWith("audio/") ? <FileAudio size={16} /> : <FileText size={16} />}
                      <span className="max-w-[340px] truncate font-medium">{selectedFile.name}</span>
                    </div>
                    <button type="button" onClick={clearSelectedFile} className="rounded-lg p-1 text-gray-500 hover:bg-gray-200">
                      <X size={16} />
                    </button>
                  </div>
                  <input
                    value={mediaCaption}
                    onChange={(e) => setMediaCaption(e.target.value)}
                    placeholder="Legenda opcional..."
                    className="mt-2 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
              ) : null}

              <form onSubmit={handleSendMessage} className="flex items-center space-x-2 rounded-2xl border border-gray-200 bg-white p-2 shadow-sm">
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
                  className="rounded-xl p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
                  title="Anexar mídia"
                >
                  <Paperclip size={18} />
                </button>
                <input
                  type="text"
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  placeholder="Escreva sua mensagem..."
                  className="flex-1 rounded-xl border border-transparent px-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  disabled={loading}
                />
                <button
                  type={selectedFile ? "button" : "submit"}
                  onClick={selectedFile ? handleSendMedia : undefined}
                  disabled={selectedFile ? loading : (!newMessage.trim() || loading)}
                  className="rounded-xl bg-emerald-600 p-3 text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Send size={20} />
                </button>
              </form>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-gray-400">
            <MessageSquare size={64} className="mb-4 text-gray-300" />
            <p className="text-lg font-medium">Selecione um lead</p>
            <p className="text-sm">Para visualizar o histórico de mensagens</p>
          </div>
        )}
      </div>
    </div>
  );
}
