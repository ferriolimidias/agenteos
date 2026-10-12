import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { KeyRound } from "lucide-react";
import { PageHeader, StatusBadge } from "../../components/ui";
import api from "../../services/api";
import { getAuthenticatedUser } from "../../utils/auth";
import { jevError, jevTime } from "../../utils/jevDiagnostics";
import "../tenant/JevDiagnostics.css";

const CONNECTION = { connected: "Conexão validada", invalid_credentials: "Credencial inválida", rate_limited: "Limite temporário do provider", unavailable: "Provider indisponível", timeout: "Tempo de resposta excedido", invalid_response: "Resposta inválida do provider", credential_unavailable: "Credencial indisponível", not_evaluated: "Não testado", not_configured: "Não configurado", configured_not_tested: "Configurado; conexão não testada" };
function IntegrationForm({ tenant }) {
  const base = `/admin/empresas/${tenant}/integrations/jev`;
  const [saved, setSaved] = useState(null);
  const [form, setForm] = useState(null);
  const [key, setKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const controllerRef = useRef(null);
  const active = controller => !controller.signal.aborted && getAuthenticatedUser()?.role === "super_admin";
  const apply = data => {
    // Deliberate allowlist: never copy a response credential into state.
    const view = { provider: data.provider, model: data.model, daily_budget_usd: data.daily_budget_usd, enabled: data.enabled, mode: "shadow", timeout_seconds: data.timeout_seconds, has_key: data.has_key, configured: data.configured, status: data.status, tested_at: data.tested_at, credential_storage_available: data.credential_storage_available };
    setSaved(view); setForm({ provider: view.provider, model: view.model, daily_budget_usd: String(view.daily_budget_usd ?? "0"), enabled: Boolean(view.enabled), timeout_seconds: view.timeout_seconds ?? 8 });
  };
  useEffect(() => {
    const controller = new AbortController(); controllerRef.current = controller;
    api.get(base, { signal: controller.signal }).then(response => { if (active(controller)) apply(response.data); }).catch(caught => { if (active(controller)) setError(jevError(caught)); }).finally(() => { if (active(controller)) setBusy(false); });
    return () => { controller.abort(); controllerRef.current?.abort(); };
  }, [base]);
  async function save(event) {
    event.preventDefault(); if (!form || busy) return;
    const controller = new AbortController(); controllerRef.current?.abort(); controllerRef.current = controller;
    if (!active(controller)) return;
    const payload = { ...form, mode: "shadow", daily_budget_usd: form.daily_budget_usd, timeout_seconds: Number(form.timeout_seconds), ...(key ? { api_key: key } : {}), ...(removeKey ? { remove_api_key: true } : {}) };
    setKey(""); setRemoveKey(false); setBusy(true); setError(""); setMessage("");
    try {
      const response = await api.put(base, payload, { signal: controller.signal });
      if (active(controller)) { apply(response.data); setMessage("Configuração salva. Disponível imediatamente em SHADOW; conexão deve ser testada separadamente."); }
    } catch (caught) { if (active(controller)) setError(jevError(caught)); }
    finally { delete payload.api_key; if (active(controller)) setBusy(false); }
  }
  async function test() {
    const controller = new AbortController(); controllerRef.current?.abort(); controllerRef.current = controller;
    if (!active(controller)) return;
    setBusy(true); setError(""); setMessage(""); setKey("");
    try {
      const response = await api.post(`${base}/test`, {}, { signal: controller.signal });
      if (!active(controller)) return;
      setMessage(CONNECTION[response.data.status] || "Estado de conexão desconhecido.");
      const updated = await api.get(base, { signal: controller.signal });
      if (active(controller)) apply(updated.data);
    } catch (caught) { if (active(controller)) setError(jevError(caught)); }
    finally { if (active(controller)) setBusy(false); }
  }
  return <section className="jev-card" data-testid="jev-integration-form"><h2>Configuração por empresa · somente SHADOW</h2>
    {busy && <p role="status">Consultando configuração JEV...</p>}{error && <p className="jev-notice" role="alert">{error}</p>}{message && <p role="status" className="jev-notice">{message}</p>}
    {saved && <><p className="jev-credential-status">Credencial: {saved.has_key ? "Configurada (somente escrita)" : "Não configurada"} · {CONNECTION[saved.status] || "Não testado"}</p><p className="jev-muted">Último teste: {jevTime(saved.tested_at)}. Armazenamento protegido: {saved.credential_storage_available ? "disponível" : "indisponível"}.</p></>}
    {form && <form onSubmit={save}><div className="jev-columns"><label>Provider<select value={form.provider} disabled><option value="typesafe">TypeSafe</option></select></label><label>Modelo<select value={form.model} disabled><option value="jev-1.13.0">jev-1.13.0</option></select></label></div>
      <label>Chave da API (nova ou substituição)<input type="password" autoComplete="new-password" value={key} onChange={event => { setKey(event.target.value); setRemoveKey(false); }} placeholder={saved?.has_key ? "Deixe vazio para preservar a chave" : "Informe uma chave para configurar"} disabled={busy || !saved?.credential_storage_available} /></label>
      {saved?.has_key && <label className="jev-checkbox"><input type="checkbox" checked={removeKey} onChange={event => { setRemoveKey(event.target.checked); setKey(""); }} disabled={busy} />Remover credencial salva ao salvar</label>}
      <div className="jev-columns"><label>Orçamento diário (USD)<input type="number" min="0" max="1000" step="0.000001" required value={form.daily_budget_usd} disabled={busy} onChange={event => setForm(previous => ({ ...previous, daily_budget_usd: event.target.value }))} /></label><label>Timeout (segundos)<input type="number" min="1" max="15" required value={form.timeout_seconds} disabled={busy} onChange={event => setForm(previous => ({ ...previous, timeout_seconds: event.target.value }))} /></label></div>
      <label className="jev-checkbox"><input type="checkbox" checked={form.enabled} disabled={busy} onChange={event => setForm(previous => ({ ...previous, enabled: event.target.checked }))} />Habilitar provider em SHADOW para exemplos sintéticos explícitos</label>
      <div className="jev-toolbar"><button className="jev-primary" type="submit" disabled={busy || !saved?.credential_storage_available}>Salvar configuração JEV</button><button type="button" disabled={busy || !saved?.configured} onClick={test}>Testar conexão salva</button></div>
      <p className="jev-muted">O teste consulta a disponibilidade de modelos, sem enviar uma conversa. Salve a chave antes de testar. A chave não volta ao navegador e não é armazenada neste navegador.</p>
    </form>}
  </section>;
}
export default function JevIntegration() {
  const [authorized, setAuthorized] = useState(() => getAuthenticatedUser()?.role === "super_admin");
  const [companies, setCompanies] = useState([]);
  const [tenant, setTenant] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { const update = () => setAuthorized(getAuthenticatedUser()?.role === "super_admin"); window.addEventListener("storage", update); return () => window.removeEventListener("storage", update); }, []);
  useEffect(() => {
    const controller = new AbortController();
    if (authorized) api.get("/empresas/", { signal: controller.signal }).then(response => { if (!controller.signal.aborted && getAuthenticatedUser()?.role === "super_admin") setCompanies(Array.isArray(response.data) ? response.data.map(item => ({ id: item.id, name: item.nome_empresa || "Empresa" })) : []); }).catch(caught => { if (!controller.signal.aborted) setError(jevError(caught)); });
    return () => controller.abort();
  }, [authorized]);
  if (!authorized) return <p role="alert">Configuração JEV exclusiva do Super Admin.</p>;
  return <div className="jev-diagnostics" data-testid="jev-admin"><PageHeader className="jev-heading" eyebrow="Configurações → Integrações → JEV" title={<>Integração JEV <StatusBadge tone="info">SHADOW</StatusBadge></>} icon={KeyRound} description="Configuração segura por tenant, sem reiniciar a página ou o atendimento." /><p className="jev-notice">A habilitação permite somente exemplos sintéticos explícitos. Conversas reais continuam bloqueadas para envio externo nesta configuração.</p><p><Link to="/admin/configuracoes">Voltar às configurações</Link></p>{error && <p role="alert">{error}</p>}<div className="jev-card jev-company-selection"><label>Empresa<select aria-label="Empresa" value={tenant} onChange={event => setTenant(event.target.value)}><option value="">Selecione uma empresa</option>{companies.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>{tenant && <IntegrationForm key={tenant} tenant={tenant} />}</div>;
}
