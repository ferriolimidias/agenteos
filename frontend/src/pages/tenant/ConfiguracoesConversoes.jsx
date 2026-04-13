import { useEffect, useState } from "react";
import { BarChart3, Loader2, Target } from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

export default function ConfiguracoesConversoes() {
  const empresa_id = getActiveEmpresaId();
  const [ativarMetaCAPI, setAtivarMetaCAPI] = useState(false);
  const [metaPixelId, setMetaPixelId] = useState("");
  const [metaAccessToken, setMetaAccessToken] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    const fetchConfiguracoes = async () => {
      if (!empresa_id) return;
      setIsLoading(true);
      try {
        const res = await api.get(`/empresas/${empresa_id}`);
        const empresa = res.data || {};
        setAtivarMetaCAPI(Boolean(empresa.meta_capi_ativo));
        setMetaPixelId(String(empresa.meta_pixel_id || ""));
        setMetaAccessToken(String(empresa.meta_access_token || ""));
      } catch (err) {
        console.error("Erro ao carregar configurações de conversão:", err);
        alert("Não foi possível carregar as configurações de conversão.");
      } finally {
        setIsLoading(false);
      }
    };

    fetchConfiguracoes();
  }, [empresa_id]);

  const handleSave = async () => {
    if (!empresa_id) return;
    setIsSaving(true);
    try {
      const payload = {
        meta_capi_ativo: ativarMetaCAPI,
        meta_pixel_id: metaPixelId || null,
        meta_access_token: metaAccessToken || null,
      };
      console.log("[Configurações Conversões] Payload:", payload);
      await api.put(`/empresas/${empresa_id}`, payload);
      alert("Configurações guardadas com sucesso.");
    } catch (err) {
      console.error("Erro ao guardar configurações de conversão:", err);
      alert("Não foi possível guardar as configurações.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="min-h-full bg-gray-50 p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <header className="rounded-2xl border border-gray-200 bg-white px-6 py-5 shadow-sm">
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-blue-50 p-2.5 text-blue-600">
              <BarChart3 size={20} />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Tracking & Conversões</h1>
              <p className="mt-1 text-sm text-gray-600">
                Configure o envio direto de eventos de conversão (Server-to-Server) para otimizar campanhas.
              </p>
            </div>
          </div>
        </header>

        <section className="rounded-2xl border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-100 px-6 py-4">
            <div className="flex items-center gap-2 text-gray-900">
              <Target size={18} className="text-blue-600" />
              <h2 className="text-lg font-semibold">Meta (Facebook/Instagram)</h2>
            </div>
          </div>

          {isLoading ? (
            <div className="flex items-center gap-2 px-6 py-10 text-sm text-gray-500">
              <Loader2 size={16} className="animate-spin" />
              Carregando configurações...
            </div>
          ) : (
            <div className="space-y-4 px-6 py-5">
            <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
              <div>
                <p className="text-sm font-semibold text-gray-800">Ativar Envio Direto via Meta CAPI</p>
                <p className="mt-0.5 text-xs text-gray-500">Quando ativo, eventos de conversão serão enviados via servidor.</p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={ativarMetaCAPI}
                onClick={() => setAtivarMetaCAPI((prev) => !prev)}
                className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${
                  ativarMetaCAPI ? "bg-blue-600" : "bg-gray-300"
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                    ativarMetaCAPI ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
            </div>

            <div
              className={`overflow-hidden transition-all duration-300 ${
                ativarMetaCAPI ? "max-h-96 opacity-100" : "max-h-0 opacity-0"
              }`}
            >
              <div className="grid gap-4 pt-2 md:grid-cols-2">
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Pixel ID da Meta</label>
                  <input
                    type="text"
                    value={metaPixelId}
                    onChange={(e) => setMetaPixelId(e.target.value)}
                    placeholder="Ex: 123456789012345"
                    className="w-full rounded-xl border border-gray-300 px-3 py-2.5 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Access Token da API de Conversões</label>
                  <input
                    type="password"
                    value={metaAccessToken}
                    onChange={(e) => setMetaAccessToken(e.target.value)}
                    placeholder="••••••••••••••••"
                    className="w-full rounded-xl border border-gray-300 px-3 py-2.5 text-sm outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>
            </div>

            <p className="rounded-xl border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
              O sistema enviará eventos de &quot;Purchase&quot; automaticamente cruzando o número do WhatsApp do lead
              quando uma Tag de fecho for aplicada.
            </p>
            </div>
          )}

          <footer className="flex justify-end border-t border-gray-100 px-6 py-4">
            <button
              type="button"
              onClick={handleSave}
              disabled={isSaving || isLoading || !empresa_id}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {isSaving ? <Loader2 size={16} className="animate-spin" /> : null}
              {isSaving ? "Guardando..." : "Guardar Configurações"}
            </button>
          </footer>
        </section>
      </div>
    </div>
  );
}
