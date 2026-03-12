import React, { useState, useEffect, useRef } from "react";
import { UserCircle, Send, BotOff, MessageSquare, Trash2 } from "lucide-react";
import api from "../../services/api";

export default function Inbox() {
  const [leads, setLeads] = useState([]);
  const [selectedLead, setSelectedLead] = useState(null);
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const userStr = localStorage.getItem("user");
  const user = userStr ? JSON.parse(userStr) : null;
  const empresa_id = user?.empresa_id;

  const fetchLeads = async () => {
    if (!empresa_id) return;
    try {
      const res = await api.get(`/empresas/${empresa_id}/inbox`);
      setLeads(res.data || []);
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

  useEffect(() => {
    fetchLeads();
    const interval = setInterval(fetchLeads, 10000);
    return () => clearInterval(interval);
  }, [empresa_id]);

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
      {/* Sidebar: Lista de Leads */}
      <div className="w-1/3 border-r border-gray-200 bg-gray-50 flex flex-col">
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
      <div className="w-2/3 flex flex-col bg-slate-50 relative">
        {selectedLead ? (
          <>
            {/* Chat Header */}
            <div className="h-16 border-b border-gray-200 bg-white flex items-center justify-between px-6 shadow-sm z-10">
              <div className="flex items-center space-x-3">
                <UserCircle size={40} className="text-gray-400" />
                <div>
                  <h3 className="font-semibold text-gray-800">{selectedLead.nome_contato}</h3>
                  <p className="text-xs text-gray-500">{selectedLead.telefone_contato}</p>
                </div>
              </div>
              <div className="flex items-center space-x-2">
                {selectedLead.bot_pausado && (
                  <button
                    onClick={handleReativarBot}
                    className="flex items-center space-x-2 bg-blue-100 text-blue-700 hover:bg-blue-200 px-4 py-2 rounded-lg text-sm font-medium transition-colors border border-blue-200"
                  >
                    <span>Reativar IA</span>
                  </button>
                )}
                <button
                  onClick={handleExcluirLead}
                  title="Excluir Lead e Histórico"
                  className="flex items-center justify-center bg-red-50 text-red-600 hover:bg-red-100 p-2 rounded-lg transition-colors border border-red-100"
                >
                  <Trash2 size={20} />
                </button>
              </div>
            </div>

            {/* Chat Messages */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4 custom-scrollbar">
              {messages?.map((msg) => {
                const isMine = msg.from_me;
                return (
                  <div key={msg.id} className={`flex ${isMine ? "justify-end" : "justify-start"}`}>
                    <div
                      className={`max-w-[70%] rounded-2xl px-4 py-2 shadow-sm relative ${
                        isMine
                          ? "bg-blue-600 text-white rounded-br-none"
                          : "bg-white text-gray-800 border border-gray-100 rounded-bl-none"
                      }`}
                    >
                      <p className="text-sm whitespace-pre-wrap">{msg?.texto}</p>
                      <span className={`text-[10px] absolute bottom-1 right-2 opacity-70 ${isMine ? "text-blue-100" : "text-gray-400"}`}>
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
              <form onSubmit={handleSendMessage} className="flex space-x-2">
                <input
                  type="text"
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  placeholder="Escreva sua mensagem..."
                  className="flex-1 border border-gray-300 rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  disabled={loading}
                />
                <button
                  type="submit"
                  disabled={!newMessage.trim() || loading}
                  className="bg-blue-600 text-white rounded-lg px-5 py-3 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
