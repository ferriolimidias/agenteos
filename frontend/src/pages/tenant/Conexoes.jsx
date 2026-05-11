import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle,
  Loader2,
  LogOut,
  MessageCircle,
  RefreshCw,
  Save,
  Smartphone,
  Webhook,
  X,
} from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

const POLL_INTERVAL_MS = 4000;

function StatusBadge({ status, loading }) {
  if (loading) {
    return (
      <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
        <Loader2 size={12} className="animate-spin" />
        Verificando...
      </span>
    );
  }

  if (status === "connected") {
    return (
      <span className="inline-flex items-center gap-2 rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700">
        <span className="h-2 w-2 rounded-full bg-emerald-500" />
        Conectado
      </span>
    );
  }

  if (status === "connecting") {
    return (
      <span className="inline-flex items-center gap-2 rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-700">
        <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
        Aguardando leitura
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-700">
      <span className="h-2 w-2 rounded-full bg-red-500" />
      Desconectado
    </span>
  );
}

export default function Conexoes() {
  const empresa_id = getActiveEmpresaId();

  const [conexao, setConexao] = useState(null);
  const [whatsStatus, setWhatsStatus] = useState("unknown");
  const [statusLoading, setStatusLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [toast, setToast] = useState(null);

  const [qrModalOpen, setQrModalOpen] = useState(false);
  const [qrBase64, setQrBase64] = useState("");
  const [qrLoading, setQrLoading] = useState(false);
  const [qrError, setQrError] = useState("");

  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookAtivo, setWebhookAtivo] = useState(true);
  const [webhookSaving, setWebhookSaving] = useState(false);
  const [webhookMessage, setWebhookMessage] = useState(null);

  const pollIntervalRef = useRef(null);
  const conexaoRef = useRef(null);

  const showToast = useCallback((type, message) => {
    setToast({ type, message });
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const normalizeStatus = (rawStatus, online) => {
    const raw = String(rawStatus || "").toLowerCase();
    if (online || raw === "open" || raw === "connected" || raw === "ativo") {
      return "connected";
    }
    if (raw === "connecting" || raw === "qrcode" || raw === "qr" || raw === "pairing") {
      return "connecting";
    }
    return "disconnected";
  };

  const fetchConexaoEvolution = useCallback(async (empresaIdAlvo) => {
    const alvo = empresaIdAlvo || empresa_id;
    if (!alvo) {
      return null;
    }
    try {
      const res = await api.get(`/empresas/${alvo}/conexoes/`);
      const lista = Array.isArray(res.data) ? res.data : [];
      const whats = lista.find((item) => item.tipo === "evolution") || null;
      conexaoRef.current = whats;
      setConexao(whats);
      return whats;
    } catch (err) {
      console.error("Erro ao buscar conexões da empresa:", err);
      return null;
    }
  }, [empresa_id]);

  const fetchStatus = useCallback(async (conexaoAtual) => {
    const alvo = conexaoAtual || conexaoRef.current;
    if (!alvo?.id) {
      setWhatsStatus("disconnected");
      setStatusLoading(false);
      return "disconnected";
    }
    try {
      const res = await api.get(`/conexoes/status/${alvo.id}`);
      const normalizado = normalizeStatus(res.data?.status, res.data?.online);
      setWhatsStatus(normalizado);
      return normalizado;
    } catch (err) {
      console.error("Erro ao consultar status do WhatsApp:", err);
      setWhatsStatus("disconnected");
      return "disconnected";
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      window.clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (conexaoAlvo) => {
      const alvo = conexaoAlvo || conexaoRef.current;
      if (!alvo?.id) return;
      if (pollIntervalRef.current) {
        window.clearInterval(pollIntervalRef.current);
      }
      pollIntervalRef.current = window.setInterval(async () => {
        const normalizado = await fetchStatus(alvo);
        if (normalizado === "connected") {
          if (pollIntervalRef.current) {
            window.clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
          setQrModalOpen(false);
          setQrBase64("");
          setQrError("");
          showToast("success", "WhatsApp conectado com sucesso.");
        }
      }, POLL_INTERVAL_MS);
    },
    [fetchStatus, showToast]
  );

  // Carga inicial: roda APENAS quando empresa_id muda. As funções memoizadas
  // são estáveis ou intencionalmente omitidas para evitar loop infinito.
  useEffect(() => {
    let cancelled = false;
    const init = async () => {
      if (!empresa_id) {
        setStatusLoading(false);
        return;
      }
      setStatusLoading(true);
      const whats = await fetchConexaoEvolution(empresa_id);
      if (!cancelled) {
        await fetchStatus(whats);
      }
    };
    init();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [empresa_id]);

  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        window.clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const fetchWebhook = async () => {
      if (!empresa_id) return;
      try {
        const res = await api.get(`/empresas/${empresa_id}/webhooks`);
        if (res.data && res.data.url) {
          setWebhookUrl(res.data.url);
          setWebhookAtivo(Boolean(res.data.ativo));
        }
      } catch (err) {
        console.error("Erro ao buscar webhook", err);
      }
    };
    fetchWebhook();
  }, [empresa_id]);

  const handleConectarWhatsApp = async () => {
    if (!empresa_id) {
      showToast("error", "Não foi possível identificar sua empresa. Faça login novamente.");
      return;
    }

    setActionLoading(true);
    setQrError("");
    setQrBase64("");
    setQrLoading(true);
    setQrModalOpen(true);

    try {
      const res = await api.post(`/empresas/${empresa_id}/conexoes/provision-whatsapp`);
      const data = res.data || {};
      if (data.base64) {
        setQrBase64(data.base64);
      } else if (data.detail) {
        setQrError(data.detail);
      } else {
        setQrError("Não conseguimos gerar o QR Code agora. Tente novamente em instantes.");
      }
      const whats = await fetchConexaoEvolution();
      await fetchStatus(whats);
      startPolling(whats);
    } catch (err) {
      console.error("Erro ao iniciar conexão WhatsApp:", err);
      let detail =
        err.response?.data?.detail || "Não foi possível iniciar a conexão WhatsApp.";
      if (typeof detail !== "string") {
        detail = JSON.stringify(detail);
      }
      setQrError(detail);
      if (err.response?.status === 409) {
        showToast("success", detail);
        setQrModalOpen(false);
        const whats = await fetchConexaoEvolution();
        await fetchStatus(whats);
      }
    } finally {
      setQrLoading(false);
      setActionLoading(false);
    }
  };

  const handleDesconectarWhatsApp = async () => {
    if (!empresa_id) return;
    const confirmar = window.confirm(
      "Deseja realmente sair (desconectar) do WhatsApp? Você precisará escanear um novo QR Code para reconectar."
    );
    if (!confirmar) return;

    setLogoutLoading(true);
    stopPolling();
    try {
      const res = await api.post(`/empresas/${empresa_id}/conexoes/logout-whatsapp`);
      showToast("success", res.data?.detail || "WhatsApp desconectado com sucesso.");
      // Força o estado local sem refazer fetch (evita engatilhar loops/polling).
      setWhatsStatus("disconnected");
      setStatusLoading(false);
      setConexao((prev) => {
        if (!prev) return prev;
        const atualizado = { ...prev, status: "disconnected" };
        conexaoRef.current = atualizado;
        return atualizado;
      });
    } catch (err) {
      console.error("Erro ao desconectar WhatsApp:", err);
      const detail =
        err.response?.data?.detail || "Não foi possível desconectar o WhatsApp agora.";
      showToast("error", typeof detail === "string" ? detail : "Erro ao desconectar.");
    } finally {
      setLogoutLoading(false);
      setStatusLoading(false);
    }
  };

  const handleFecharModal = () => {
    setQrModalOpen(false);
    setQrBase64("");
    setQrError("");
    stopPolling();
    if (conexao?.id) {
      fetchStatus(conexao);
    }
  };

  const handleSalvarWebhook = async (e) => {
    e.preventDefault();
    if (!empresa_id) return;
    setWebhookSaving(true);
    setWebhookMessage(null);
    try {
      await api.post(`/empresas/${empresa_id}/webhooks`, {
        url: webhookUrl,
        ativo: webhookAtivo,
      });
      setWebhookMessage({ type: "success", text: "Configurações salvas com sucesso!" });
      window.setTimeout(() => setWebhookMessage(null), 3000);
    } catch (err) {
      console.error("Erro ao salvar webhook", err);
      setWebhookMessage({ type: "error", text: "Erro ao salvar as configurações." });
    } finally {
      setWebhookSaving(false);
    }
  };

  const isConectado = whatsStatus === "connected";
  const podeDesconectar = isConectado && Boolean(conexao?.id);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
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
              <div
                className={`rounded-full p-1 ${
                  toast.type === "success" ? "bg-emerald-100" : "bg-red-100"
                }`}
              >
                {toast.type === "success" ? (
                  <CheckCircle size={14} />
                ) : (
                  <AlertCircle size={14} />
                )}
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold">
                  {toast.type === "success" ? "Tudo certo" : "Atenção"}
                </p>
                <p className="mt-0.5 text-sm">{toast.message}</p>
              </div>
              <button
                type="button"
                onClick={() => setToast(null)}
                className="rounded-lg p-1 opacity-70 transition-opacity hover:opacity-100"
              >
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="flex justify-between items-center bg-white p-6 rounded-xl shadow-sm border border-gray-200">
        <div>
          <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
            <Smartphone className="text-emerald-600" size={28} />
            Conexões
          </h1>
          <p className="text-gray-500 mt-1">
            Conecte o seu WhatsApp e configure integrações externas.
          </p>
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        <div className="p-6 border-b border-gray-100 bg-gray-50 flex items-center gap-3">
          <div className="bg-emerald-100 p-2 rounded-lg">
            <MessageCircle className="text-emerald-600" size={24} />
          </div>
          <div>
            <h2 className="text-lg font-bold text-gray-800">Status do WhatsApp</h2>
            <p className="text-sm text-gray-500">
              Em poucos cliques o seu número fica pronto para conversar com a IA.
            </p>
          </div>
        </div>

        <div className="p-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="hidden sm:flex h-12 w-12 items-center justify-center rounded-full bg-emerald-50 text-emerald-600">
              <Smartphone size={22} />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">Status atual</p>
              <div className="mt-1 flex items-center gap-2">
                <StatusBadge status={whatsStatus} loading={statusLoading} />
                <button
                  type="button"
                  onClick={() => fetchStatus(conexao)}
                  className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
                  title="Atualizar status"
                >
                  <RefreshCw size={12} />
                  Atualizar
                </button>
              </div>
              <p className="mt-2 max-w-md text-xs text-gray-500">
                {isConectado
                  ? "O seu WhatsApp está online e pronto para receber e enviar mensagens automaticamente."
                  : "Clique em \"Conectar WhatsApp\" para gerar um QR Code e ler com o aplicativo do celular."}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {podeDesconectar ? (
              <button
                type="button"
                onClick={handleDesconectarWhatsApp}
                disabled={logoutLoading}
                className="inline-flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-5 py-2.5 text-sm font-semibold text-red-700 transition-colors hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {logoutLoading ? (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    Desconectando...
                  </>
                ) : (
                  <>
                    <LogOut size={16} />
                    Sair do WhatsApp
                  </>
                )}
              </button>
            ) : (
              <button
                type="button"
                onClick={handleConectarWhatsApp}
                disabled={actionLoading}
                className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {actionLoading ? (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    Preparando...
                  </>
                ) : (
                  <>
                    <MessageCircle size={16} />
                    Conectar WhatsApp
                  </>
                )}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        <div className="p-6 border-b border-gray-100 bg-gray-50 flex items-center gap-3">
          <div className="bg-blue-100 p-2 rounded-lg">
            <Webhook className="text-blue-600" size={24} />
          </div>
          <div>
            <h2 className="text-lg font-bold text-gray-800">Webhooks (Exportar Leads)</h2>
            <p className="text-sm text-gray-500">
              Envie dados automaticamente para ferramentas como n8n, Make, Zapier.
            </p>
          </div>
        </div>

        <form onSubmit={handleSalvarWebhook} className="p-6 space-y-6">
          {webhookMessage && (
            <div
              className={`p-4 rounded-lg flex items-center gap-2 text-sm font-medium ${
                webhookMessage.type === "success"
                  ? "bg-green-50 text-green-700 border border-green-200"
                  : "bg-red-50 text-red-700 border border-red-200"
              }`}
            >
              {webhookMessage.type === "success" ? (
                <CheckCircle size={18} />
              ) : (
                <AlertCircle size={18} />
              )}
              {webhookMessage.text}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                URL do Webhook (Ex: n8n, Make)
              </label>
              <input
                type="url"
                value={webhookUrl}
                onChange={(e) => setWebhookUrl(e.target.value)}
                placeholder="https://sua-url-de-webhook.com/..."
                className="w-full border border-gray-300 rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
                required
              />
              <p className="mt-2 text-xs text-gray-500">
                O payload será enviado sempre que um lead mudar de etapa no CRM.
              </p>
            </div>

            <div className="flex items-center gap-3 pt-2">
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  className="sr-only peer"
                  checked={webhookAtivo}
                  onChange={(e) => setWebhookAtivo(e.target.checked)}
                />
                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
              </label>
              <span className="text-sm font-medium text-gray-700">
                {webhookAtivo ? "Webhook Ativo" : "Webhook Inativo"}
              </span>
            </div>
          </div>

          <div className="pt-4 border-t border-gray-100 flex justify-end">
            <button
              type="submit"
              disabled={webhookSaving}
              className="flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 px-6 rounded-lg transition-colors disabled:opacity-50"
            >
              <Save size={18} />
              {webhookSaving ? "Salvando..." : "Salvar Configurações"}
            </button>
          </div>
        </form>
      </div>

      {qrModalOpen ? (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <div>
                <h3 className="text-xl font-bold text-gray-800">Conectar WhatsApp</h3>
                <p className="text-sm text-gray-500">
                  Escaneie o código abaixo com o seu celular.
                </p>
              </div>
              <button
                type="button"
                onClick={handleFecharModal}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={18} />
              </button>
            </div>

            <div className="space-y-5 p-6">
              <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                Estamos verificando a conexão automaticamente. Assim que o WhatsApp ligar, esta
                janela fecha sozinha.
              </div>

              {qrLoading ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-500">
                  <Loader2 size={26} className="mb-3 animate-spin" />
                  Gerando QR Code...
                </div>
              ) : qrError ? (
                <div className="space-y-3 rounded-xl border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-700">
                  <p className="whitespace-pre-wrap">{qrError}</p>
                  <button
                    type="button"
                    onClick={handleConectarWhatsApp}
                    className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-100"
                  >
                    <RefreshCw size={12} />
                    Tentar novamente
                  </button>
                </div>
              ) : qrBase64 ? (
                <div className="flex flex-col items-center rounded-2xl border border-gray-200 bg-gray-50 p-6">
                  <img
                    src={`data:image/png;base64,${qrBase64}`}
                    alt="QR Code para conectar WhatsApp"
                    className="h-64 w-64 rounded-xl border border-gray-200 bg-white p-3 shadow-sm"
                  />
                  <div className="mt-4 space-y-1 text-center text-sm text-gray-600">
                    <p className="font-semibold text-gray-700">Como conectar:</p>
                    <p>1. Abra o WhatsApp no seu celular.</p>
                    <p>2. Vá em Configurações &gt; Aparelhos conectados.</p>
                    <p>3. Toque em &quot;Conectar um aparelho&quot; e leia o código acima.</p>
                  </div>
                </div>
              ) : (
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-800">
                  Aguarde alguns segundos enquanto preparamos o QR Code.
                </div>
              )}

              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={handleFecharModal}
                  className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100"
                >
                  Fechar
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
