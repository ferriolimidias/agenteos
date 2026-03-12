import React, { useState, useRef, useEffect, useCallback } from "react";
import axios from "axios";
import { Send, Bot, RefreshCw } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 90000; // 90s max wait

export default function Simulador() {
  const [messages, setMessages] = useState([]);
  const [inputStr, setInputStr] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const pollInterval = useRef(null);
  const pollStartTime = useRef(null);

  const [sessaoId] = useState(() => "ID_TESTE_SIMULADOR");

  let empresaId = "";
  try {
    const userStr = localStorage.getItem("user");
    if (userStr) {
      const u = JSON.parse(userStr);
      empresaId = u.empresa_id;
    }
  } catch (e) {}

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  // Cleanup polling on unmount
  // Cleanup polling on unmount (this cleanup is for the old polling mechanism, will be replaced by the new useEffect's cleanup)
  useEffect(() => {
    // The previous cleanup for pollInterval.current is now handled by the new persistent polling useEffect
    // if (pollInterval.current) clearInterval(pollInterval.current);
    return () => {
      // No specific cleanup needed here anymore as the persistent polling has its own cleanup
    };
  }, []);

  // Polling persistente (Heartbeat) para capturar mensagens assíncronas (ex: follow-ups)
  useEffect(() => {
    if (!empresaId) return;

    const intervalId = setInterval(async () => {
      try {
        const res = await axios.get(
          `${API_BASE}/empresas/${empresaId}/simulador/resposta/${sessaoId}`
        );

        if (res.data.status === "concluido" && res.data.resposta) {
          setLoading(false);
          const resposta_texto = res.data.resposta;
          
          if (resposta_texto.trim() !== "") {
            const partes = resposta_texto
              .split("\n\n")
              .map((p) => p.trim())
              .filter((p) => p);

            for (let i = 0; i < partes.length; i++) {
              setMessages((prev) => [...prev, { role: "ai", text: partes[i] }]);
              if (i < partes.length - 1) {
                await new Promise((resolve) => setTimeout(resolve, 1500));
              }
            }
          }
        }
      } catch (error) {
        console.error("Erro no heartbeat do simulador:", error);
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(intervalId);
  }, [empresaId, sessaoId]);

  const handleSend = async (e) => {
    e.preventDefault();
    if (!inputStr.trim() || !empresaId || loading) return;

    const userMessage = { role: "user", text: inputStr };
    setMessages((prev) => [...prev, userMessage]);
    setInputStr("");
    setLoading(true);

    try {
      await axios.post(`${API_BASE}/empresas/${empresaId}/simulador`, {
        mensagem: userMessage.text,
        sessao_id: sessaoId,
      });
    } catch (error) {
      console.error("Erro ao enviar mensagem:", error);
      setLoading(false);
      setMessages((prev) => [
        ...prev,
        { role: "error", text: "Erro ao enviar a mensagem. Servidor indisponível?" },
      ]);
    }
  };


  const handleReset = async () => {
    if (!window.confirm("Deseja limpar o histórico do simulador?")) return;

    // stopPolling() is no longer needed as polling is persistent via useEffect
    setLoading(true);
    try {
      await axios.delete(`${API_BASE}/empresas/${empresaId}/simulador/reset`);
      setMessages([]);
    } catch (error) {
      console.error("Erro ao resetar conversa:", error);
      alert("Erro ao limpar histórico do simulador.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-140px)] bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
      <div className="bg-blue-900 text-white p-4 flex items-center justify-between shadow-md z-10">
        <div className="flex items-center">
          <Bot size={24} className="mr-3" />
          <div>
            <h2 className="font-semibold">Simulador do Agente AI</h2>
            <p className="text-xs text-blue-200">
              Teste o fluxo de conversação antes de ligar na inteligência real.
            </p>
          </div>
        </div>
        <button
          onClick={handleReset}
          disabled={loading || !empresaId}
          className="text-white hover:text-blue-200 p-2 rounded-lg transition-colors flex items-center gap-2 text-sm bg-blue-800 hover:bg-blue-700 disabled:opacity-50"
          title="Limpar Conversa"
        >
          <RefreshCw size={18} />
          Limpar
        </button>
      </div>

      <div className="flex-1 p-4 overflow-y-auto bg-gray-50 flex flex-col space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-400 mt-10">
            Nenhuma mensagem ainda. Envie um "Olá" para testar!
          </div>
        )}

        {messages.map((msg, idx) => (
          <div key={idx} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[70%] rounded-2xl px-5 py-3 shadow-sm ${
                msg.role === "user"
                  ? "bg-blue-600 text-white rounded-br-none"
                  : msg.role === "error"
                  ? "bg-red-100 text-red-800 rounded-bl-none"
                  : "bg-white text-gray-800 border border-gray-200 rounded-bl-none"
              }`}
            >
              <div className="whitespace-pre-wrap">{msg.text}</div>
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="max-w-[70%] bg-white border border-gray-200 text-gray-500 rounded-2xl px-5 py-3 rounded-bl-none shadow-sm flex items-center space-x-2">
              <span className="animate-bounce">●</span>
              <span className="animate-bounce" style={{ animationDelay: "0.2s" }}>●</span>
              <span className="animate-bounce" style={{ animationDelay: "0.4s" }}>●</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="p-4 bg-white border-t border-gray-200">
        <form onSubmit={handleSend} className="flex space-x-3 text-sm">
          <input
            type="text"
            className="flex-1 border border-gray-300 rounded-full px-5 py-3 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-colors"
            placeholder="Digite uma mensagem..."
            value={inputStr}
            onChange={(e) => setInputStr(e.target.value)}
            disabled={!empresaId || loading}
          />
          <button
            type="submit"
            disabled={!inputStr.trim() || !empresaId || loading}
            className="bg-blue-600 hover:bg-blue-700 text-white w-12 h-12 rounded-full flex items-center justify-center transition-colors disabled:opacity-50 flex-shrink-0"
          >
            <Send size={20} className="ml-1" />
          </button>
        </form>
        {loading && (
          <div className="text-xs text-blue-500 mt-2 ml-4">Agente está processando...</div>
        )}
      </div>
    </div>
  );
}
