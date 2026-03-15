import React, { useState, useEffect } from "react";
import { Webhook, Save, CheckCircle, AlertCircle } from "lucide-react";
import axios from "axios";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

export default function Integracoes() {
  const [webhookUrl, setWebhookUrl] = useState("");
  const [ativo, setAtivo] = useState(true);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(null);

  const user = getStoredUser();
  const empresa_id = getActiveEmpresaId();

  useEffect(() => {
    const fetchWebhook = async () => {
      if (!empresa_id) return;
      try {
        const res = await axios.get(`/api/empresas/${empresa_id}/webhooks`);
        if (res.data && res.data.url) {
          setWebhookUrl(res.data.url);
          setAtivo(res.data.ativo);
        }
      } catch (e) {
        console.error("Erro ao buscar webhook", e);
      }
    };
    fetchWebhook();
  }, [empresa_id]);

  const handleSave = async (e) => {
    e.preventDefault();
    if (!empresa_id) return;
    setLoading(true);
    setMessage(null);
    try {
      await axios.post(`/api/empresas/${empresa_id}/webhooks`, {
        url: webhookUrl,
        ativo: ativo
      });
      setMessage({ type: "success", text: "Configurações salvas com sucesso!" });
      setTimeout(() => setMessage(null), 3000);
    } catch (e) {
      console.error("Erro ao salvar", e);
      setMessage({ type: "error", text: "Erro ao salvar as configurações." });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex justify-between items-center bg-white p-6 rounded-xl shadow-sm border border-gray-200">
        <div>
          <h1 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
            <Webhook className="text-blue-600" size={28} />
            Integrações
          </h1>
          <p className="text-gray-500 mt-1">Conecte sua plataforma a sistemas externos via Webhooks.</p>
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        <div className="p-6 border-b border-gray-100 bg-gray-50 flex items-center gap-3">
          <div className="bg-blue-100 p-2 rounded-lg">
            <Webhook className="text-blue-600" size={24} />
          </div>
          <div>
            <h2 className="text-lg font-bold text-gray-800">Webhooks (Exportar Leads)</h2>
            <p className="text-sm text-gray-500">Envie dados automaticamente para ferramentas como n8n, Make, Zapier.</p>
          </div>
        </div>

        <form onSubmit={handleSave} className="p-6 space-y-6">
          {message && (
            <div className={`p-4 rounded-lg flex items-center gap-2 text-sm font-medium ${message.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
              {message.type === 'success' ? <CheckCircle size={18} /> : <AlertCircle size={18} />}
              {message.text}
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
                O payload será enviado sempre que um lead mudar de etapa no CRM. Formato: <code className="bg-gray-100 px-1 py-0.5 rounded text-gray-700 font-mono">{"{\"evento\": \"lead_atualizado\", \"telefone\": \"...\", \"nome\": \"...\", \"etapa_crm\": \"...\"}"}</code>
              </p>
            </div>

            <div className="flex items-center gap-3 pt-2">
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  className="sr-only peer"
                  checked={ativo}
                  onChange={(e) => setAtivo(e.target.checked)}
                />
                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
              </label>
              <span className="text-sm font-medium text-gray-700">
                {ativo ? "Webhook Ativo" : "Webhook Inativo"}
              </span>
            </div>
          </div>

          <div className="pt-4 border-t border-gray-100 flex justify-end">
            <button
              type="submit"
              disabled={loading}
              className="flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 px-6 rounded-lg transition-colors disabled:opacity-50"
            >
              <Save size={18} />
              {loading ? "Salvando..." : "Salvar Configurações"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
