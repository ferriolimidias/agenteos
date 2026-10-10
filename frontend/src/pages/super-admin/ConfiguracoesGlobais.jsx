import React, { useEffect, useState } from "react";
import { Save, Settings } from "lucide-react";
import api from "../../services/api";
import { ActionButton, InfoBanner, LoadingState, PageHeader, SectionCard } from "../../components/ui";

export default function ConfiguracoesGlobais() {
  const [formData, setFormData] = useState({
    nome_sistema: "",
    cor_primaria: "#6366f1",
    openai_key_global: "",
    favicon_base64: "",
    logo_base64: "",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  useEffect(() => {
    const carregarConfiguracoes = async () => {
      setLoading(true);
      setErrorMessage("");
      try {
        const response = await api.get("/admin/configuracoes");
        setFormData({
          nome_sistema: response.data.nome_sistema || "",
          cor_primaria: response.data.cor_primaria || "#6366f1",
          openai_key_global: response.data.openai_key_global || "",
          favicon_base64: response.data.favicon_base64 || "",
          logo_base64: response.data.logo_base64 || "",
        });
      } catch (error) {
        const backendMessage = error?.response?.data?.detail || error?.message || "Falha ao carregar configurações.";
        setErrorMessage(`Erro ao carregar configurações: ${backendMessage}`);
      } finally {
        setLoading(false);
      }
    };

    carregarConfiguracoes();
  }, []);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleFaviconUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      setFormData((prev) => ({
        ...prev,
        favicon_base64: String(reader.result || ""),
      }));
      setErrorMessage("");
    };
    reader.onerror = () => {
      setErrorMessage("Erro ao converter favicon para base64.");
    };
    reader.readAsDataURL(file);
  };

  const handleLogoUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      setFormData((prev) => ({
        ...prev,
        logo_base64: String(reader.result || ""),
      }));
      setErrorMessage("");
    };
    reader.onerror = () => {
      setErrorMessage("Erro ao converter logo para base64.");
    };
    reader.readAsDataURL(file);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setErrorMessage("");
    setSuccessMessage("");
    try {
      await api.put("/admin/configuracoes", formData);
      setSuccessMessage("Configurações salvas com sucesso.");
      window.dispatchEvent(new Event("configuracoesUpdated"));
    } catch (error) {
      const backendMessage = error?.response?.data?.detail || error?.message || "Falha ao salvar configurações.";
      setErrorMessage(`Erro ao salvar configurações: ${backendMessage}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-4xl space-y-6">
      <PageHeader
        title="Configurações Globais"
        description="Personalize o sistema e configure as credenciais globais da API do Instagram. Segredos são somente para escrita."
        icon={Settings}
        eyebrow="Super Admin"
      />

        {loading && (
          <LoadingState label="Carregando configurações..." />
        )}

        {errorMessage && (
          <InfoBanner tone="danger">{errorMessage}</InfoBanner>
        )}

        {successMessage && (
          <InfoBanner tone="success">{successMessage}</InfoBanner>
        )}

      <SectionCard title="Branding e integrações globais" description="Configurações aplicadas ao login, barras laterais e fallback de IA.">
        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Nome do Sistema</label>
            <input
              type="text"
              name="nome_sistema"
              value={formData.nome_sistema}
              onChange={handleChange}
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none transition-colors focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
              placeholder="Ex: Agente OS"
              required
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Favicon</label>
            <input
              type="file"
              accept=".png,.ico,.svg,image/png,image/x-icon,image/svg+xml"
              onChange={handleFaviconUpload}
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition-colors focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
            />
            {formData.favicon_base64 && (
              <div className="mt-3">
                <img
                  src={formData.favicon_base64}
                  alt="Preview favicon"
                  className="h-8 w-8 rounded border border-slate-200 bg-white p-1"
                />
              </div>
            )}
            <p className="mt-2 text-xs text-slate-500">O favicon aparece na aba do navegador.</p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Logo do Sistema</label>
            <input
              type="file"
              accept=".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml"
              onChange={handleLogoUpload}
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm text-slate-700 outline-none transition-colors focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
            />
            {formData.logo_base64 && (
              <div className="mt-3">
                <img
                  src={formData.logo_base64}
                  alt="Preview logo"
                  className="h-12 max-w-[160px] rounded border border-slate-200 bg-white object-contain p-1"
                />
              </div>
            )}
            <p className="mt-2 text-xs text-slate-500">A logo é aplicada na Sidebar e na tela de Login.</p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Cor Primária (Hexadecimal)</label>
            <div className="flex gap-4">
              <input
                type="color"
                name="cor_primaria"
                value={formData.cor_primaria}
                onChange={handleChange}
                className="h-12 w-16 cursor-pointer rounded-lg border border-slate-300 bg-white p-1"
              />
              <input
                type="text"
                name="cor_primaria"
                value={formData.cor_primaria}
                onChange={handleChange}
                className="flex-1 rounded-lg border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none transition-colors focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
                placeholder="#6366f1"
                required
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Chave OpenAI Global (Fallback)</label>
            <input
              type="password"
              name="openai_key_global"
              value={formData.openai_key_global}
              onChange={handleChange}
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none transition-colors focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
              placeholder="sk-..."
            />
          </div>

          <div className="pt-2">
            <ActionButton
              type="submit"
              disabled={saving}
              icon={Save}
              className="min-w-[180px]"
            >
              {saving ? "Salvando..." : "Salvar Configurações"}
            </ActionButton>
          </div>
        </form>
      </SectionCard>

    </div>
  );
}
