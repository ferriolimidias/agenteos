import React, { useEffect, useState } from "react";
import axios from "axios";

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
        const response = await axios.get("/api/admin/configuracoes");
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
      await axios.put("/api/admin/configuracoes", formData);
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
    <div className="bg-[#1e1e2d] w-full min-h-screen text-gray-200 p-8">
      <div className="max-w-3xl border border-gray-700 rounded-xl bg-[#2a2a3c] p-8 shadow-xl">
        <h1 className="text-3xl tracking-tight font-bold text-white mb-6">Configurações Globais</h1>
        <p className="text-gray-400 mb-8">Personalize a aparência e chaves globais do sistema.</p>

        {loading && (
          <div className="mb-6 p-4 rounded-lg text-sm bg-blue-900/30 text-blue-300 border border-blue-800">
            Carregando configurações...
          </div>
        )}

        {errorMessage && (
          <div className="mb-6 p-4 rounded-lg text-sm bg-red-900/30 text-red-300 border border-red-800">
            {errorMessage}
          </div>
        )}

        {successMessage && (
          <div className="mb-6 p-4 rounded-lg text-sm bg-emerald-900/30 text-emerald-300 border border-emerald-800">
            {successMessage}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Nome do Sistema</label>
            <input
              type="text"
              name="nome_sistema"
              value={formData.nome_sistema}
              onChange={handleChange}
              className="w-full bg-[#1e1e2d] border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
              placeholder="Ex: Agente OS"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Favicon</label>
            <input
              type="file"
              accept=".png,.ico,.svg,image/png,image/x-icon,image/svg+xml"
              onChange={handleFaviconUpload}
              className="w-full bg-[#1e1e2d] border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
            />
            {formData.favicon_base64 && (
              <div className="mt-3">
                <img
                  src={formData.favicon_base64}
                  alt="Preview favicon"
                  className="w-8 h-8 rounded bg-white p-1 border border-gray-600"
                />
              </div>
            )}
            <p className="text-xs text-gray-500 mt-2">O favicon aparece na aba do navegador.</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Logo do Sistema</label>
            <input
              type="file"
              accept=".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml"
              onChange={handleLogoUpload}
              className="w-full bg-[#1e1e2d] border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
            />
            {formData.logo_base64 && (
              <div className="mt-3">
                <img
                  src={formData.logo_base64}
                  alt="Preview logo"
                  className="h-12 max-w-[160px] rounded bg-white p-1 border border-gray-600 object-contain"
                />
              </div>
            )}
            <p className="text-xs text-gray-500 mt-2">A logo é aplicada na Sidebar e na tela de Login.</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Cor Primária (Hexadecimal)</label>
            <div className="flex gap-4">
              <input
                type="color"
                name="cor_primaria"
                value={formData.cor_primaria}
                onChange={handleChange}
                className="h-12 w-16 p-1 bg-[#1e1e2d] border border-gray-600 rounded-lg cursor-pointer"
              />
              <input
                type="text"
                name="cor_primaria"
                value={formData.cor_primaria}
                onChange={handleChange}
                className="flex-1 bg-[#1e1e2d] border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                placeholder="#6366f1"
                required
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Chave OpenAI Global (Fallback)</label>
            <input
              type="password"
              name="openai_key_global"
              value={formData.openai_key_global}
              onChange={handleChange}
              className="w-full bg-[#1e1e2d] border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
              placeholder="sk-..."
            />
          </div>

          <div className="pt-2">
            <button
              type="submit"
              disabled={saving}
              className="px-6 py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-medium rounded-lg transition-colors flex items-center justify-center min-w-[150px] disabled:opacity-70 disabled:cursor-not-allowed"
            >
              {saving ? "Salvando..." : "Salvar Configurações"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
