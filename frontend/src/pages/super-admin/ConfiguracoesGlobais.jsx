import React, { useState, useEffect } from "react";
import axios from "axios";

export default function ConfiguracoesGlobais() {
  const [formData, setFormData] = useState({
    nome_sistema: "",
    cor_primaria: "",
    openai_key_global: ""
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    carregarConfiguracoes();
  }, []);

  const carregarConfiguracoes = async () => {
    try {
      const response = await axios.get("/api/admin/configuracoes");
      setFormData({
        nome_sistema: response.data.nome_sistema || "",
        cor_primaria: response.data.cor_primaria || "",
        openai_key_global: response.data.openai_key_global || ""
      });
    } catch (error) {
      console.error("Erro ao carregar configurações globais:", error);
      setMessage({ type: "error", text: "Erro ao carregar as configurações." });
    } finally {
      setLoading(false);
    }
  };

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: value
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      await axios.put("/api/admin/configuracoes", formData);
      setMessage({ type: "success", text: "Configurações salvas com sucesso!" });
      // Evento opcional para notificar o layout se quiser atualizar o DOM em tempo real
      window.dispatchEvent(new Event("configuracoesUpdated"));
    } catch (error) {
      console.error("Erro ao salvar configurações globais:", error);
      setMessage({ type: "error", text: "Erro ao salvar as configurações." });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="text-gray-400 p-8 h-full flex justify-center items-center">
        Carregando...
      </div>
    );
  }

  return (
    <div className="bg-[#1e1e2d] w-full min-h-screen text-gray-200 p-8">
      <div className="max-w-3xl border border-gray-700 rounded-xl bg-[#2a2a3c] p-8 shadow-xl">
        <h1 className="text-3xl tracking-tight font-bold text-white mb-6">
          Configurações Globais
        </h1>
        <p className="text-gray-400 mb-8">
          Personalize a aparência e defina as chaves globais padrão para o sistema Whitelabel.
        </p>

        {message && (
          <div
            className={`p-4 mb-6 rounded-lg text-sm ${
              message.type === "success"
                ? "bg-emerald-900/30 text-emerald-400 border border-emerald-800"
                : "bg-red-900/30 text-red-400 border border-red-800"
            }`}
          >
            {message.text}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Nome do Sistema (Whitelabel)
            </label>
            <input
              type="text"
              name="nome_sistema"
              value={formData.nome_sistema}
              onChange={handleChange}
              className="w-full bg-[#1e1e2d] border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
              placeholder="Ex: Super Agent OS"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Cor Primária (Hexadecimal)
            </label>
            <div className="flex gap-4">
              <input
                type="color"
                name="cor_primaria"
                value={formData.cor_primaria || "#6366f1"}
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
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Chave OpenAI Global (Fallback)
            </label>
            <input
              type="password"
              name="openai_key_global"
              value={formData.openai_key_global}
              onChange={handleChange}
              className="w-full bg-[#1e1e2d] border border-gray-600 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
              placeholder="sk-..."
            />
            <p className="text-xs text-gray-500 mt-2">
              Se as empresas não informarem a própria chave, o sistema usará esta chave como padrão.
            </p>
          </div>

          <div className="pt-4">
            <button
              type="submit"
              disabled={saving}
              className="px-6 py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-medium rounded-lg transition-colors flex items-center justify-center min-w-[150px]"
            >
              {saving ? "Salvando..." : "Salvar Configurações"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
