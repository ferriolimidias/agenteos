import React, { useEffect, useState } from "react";
import { Save, Settings } from "lucide-react";
import api from "../../services/api";
import { ActionButton, InfoBanner, Input, LoadingState, PageHeader, SectionCard } from "../../components/ui";

export default function ConfiguracoesGlobais() {
  const [formData, setFormData] = useState({
    nome_sistema: "",
    cor_primaria: "#6366f1",
    openai_key_global: "",
    remover_openai_key_global: false,
    favicon_base64: "",
    logo_base64: "",
  });
  const [loading, setLoading] = useState(true);
  const [savedData, setSavedData] = useState(null);
  const [openaiKeyConfigured, setOpenaiKeyConfigured] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  useEffect(() => {
    const carregarConfiguracoes = async () => {
      setLoading(true);
      setErrorMessage("");
      try {
        const response = await api.get("/admin/configuracoes");
        const loadedData = {
          nome_sistema: response.data.nome_sistema || "",
          cor_primaria: response.data.cor_primaria || "#6366f1",
          openai_key_global: "",
          remover_openai_key_global: false,
          favicon_base64: response.data.favicon_base64 || "",
          logo_base64: response.data.logo_base64 || "",
        };
        setFormData(loadedData);
        setSavedData(loadedData);
        setOpenaiKeyConfigured(Boolean(response.data.openai_key_global_configurada));
      } catch (error) {
        const backendMessage = error?.response?.data?.detail || error?.message || "Falha ao carregar configurações.";
        setErrorMessage(`Erro ao carregar configurações: ${backendMessage}`);
      } finally {
        setLoading(false);
      }
    };

    carregarConfiguracoes();
  }, [loadAttempt]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value, ...(name === "openai_key_global" && value ? { remover_openai_key_global: false } : {}) }));
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
      const next = { ...formData, openai_key_global: "", remover_openai_key_global: false };
      setFormData(next);
      setSavedData(next);
      if (formData.remover_openai_key_global) setOpenaiKeyConfigured(false);
      else if (String(formData.openai_key_global || "").trim()) setOpenaiKeyConfigured(true);
      setSuccessMessage("Configurações salvas com sucesso.");
      window.dispatchEvent(new Event("configuracoesUpdated"));
    } catch (error) {
      const backendMessage = error?.response?.data?.detail || error?.message || "Falha ao salvar configurações.";
      setErrorMessage(`Erro ao salvar configurações: ${backendMessage}`);
    } finally {
      setSaving(false);
    }
  };

  const cancelChanges = () => {
    if (!savedData) return;
    setFormData({ ...savedData });
    setErrorMessage("");
    setSuccessMessage("Alterações descartadas. A configuração salva foi restaurada.");
  };

  return (
    <div className="fm-admin-page fm-admin-settings">
      <PageHeader title="Configurações globais" description="Gerencie a identidade visual da plataforma e a credencial global de fallback de IA." icon={Settings} eyebrow="Super Admin" />
      {loading && <LoadingState label="Carregando configurações..." />}
      {errorMessage && <div role="alert"><InfoBanner tone="danger">{errorMessage}</InfoBanner></div>}
      {successMessage && <div role="status" aria-live="polite"><InfoBanner tone="success">{successMessage}</InfoBanner></div>}
      {!loading && !savedData && <ActionButton variant="secondary" onClick={() => setLoadAttempt(previous => previous + 1)}>Tentar novamente</ActionButton>}
      {savedData && <form onSubmit={handleSubmit} className="fm-admin-page" aria-busy={saving}>
        <fieldset className="fm-admin-fieldset" disabled={loading || saving}>
          <legend className="sr-only">Configurações globais da plataforma</legend>
          <div className="fm-admin-page">
            <SectionCard title="Identidade da plataforma" description="Nome, logo e favicon aplicados no login e nas barras laterais.">
              <div className="fm-admin-field">
                <label htmlFor="global-system-name">Nome do sistema</label>
                <Input id="global-system-name" type="text" name="nome_sistema" value={formData.nome_sistema} onChange={handleChange} placeholder="Ex: AgenteOS" required />
              </div>
              <div className="fm-admin-field">
                <label htmlFor="global-favicon">Favicon</label>
                <Input id="global-favicon" type="file" accept=".png,.ico,.svg,image/png,image/x-icon,image/svg+xml" onChange={handleFaviconUpload} aria-describedby="global-favicon-help" />
                {formData.favicon_base64 && <img src={formData.favicon_base64} alt="Prévia do favicon" className="fm-admin-preview" width="32" height="32" />}
                <p id="global-favicon-help" className="fm-helper">O favicon aparece na aba do navegador.</p>
              </div>
              <div className="fm-admin-field">
                <label htmlFor="global-logo">Logo do sistema</label>
                <Input id="global-logo" type="file" accept=".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml" onChange={handleLogoUpload} aria-describedby="global-logo-help" />
                {formData.logo_base64 && <img src={formData.logo_base64} alt="Prévia da logo" className="fm-admin-preview" />}
                <p id="global-logo-help" className="fm-helper">A logo é aplicada na barra lateral e no login.</p>
              </div>
              <div className="fm-admin-field">
                <label htmlFor="global-primary-color">Cor primária (hexadecimal)</label>
                <div className="fm-admin-color-field">
                  <Input aria-label="Escolher cor primária" type="color" name="cor_primaria" value={formData.cor_primaria} onChange={handleChange} />
                  <Input id="global-primary-color" type="text" name="cor_primaria" value={formData.cor_primaria} onChange={handleChange} placeholder="#6366f1" required />
                </div>
              </div>
            </SectionCard>
            <SectionCard title="Credencial global de IA" description="Chave de fallback compartilhada. O valor salvo nunca é devolvido pela API.">
              <div className="fm-admin-field">
                <label htmlFor="global-openai-key">Chave OpenAI global (fallback)</label>
                <p className="fm-helper" role="status">{openaiKeyConfigured ? "Credencial configurada. Deixe o campo vazio para preservá-la." : "Credencial não configurada."}</p>
                <Input id="global-openai-key" type="password" autoComplete="new-password" name="openai_key_global" value={formData.openai_key_global} onChange={handleChange} placeholder={openaiKeyConfigured ? "Nova chave para substituição explícita" : "Informe uma chave para configurar"} />
                {openaiKeyConfigured && <label className="flex items-center gap-2"><input type="checkbox" name="remover_openai_key_global" checked={formData.remover_openai_key_global} onChange={(event) => setFormData((current) => ({ ...current, remover_openai_key_global: event.target.checked, openai_key_global: "" }))} />Remover a credencial global</label>}
              </div>
            </SectionCard>
            <div className="fm-admin-form-actions">
              <ActionButton type="submit" icon={Save}>{saving ? "Salvando..." : "Salvar configurações"}</ActionButton>
              <ActionButton type="reset" variant="secondary" onClick={cancelChanges}>Descartar alterações</ActionButton>
            </div>
          </div>
        </fieldset>
      </form>}
    </div>
  );
}
