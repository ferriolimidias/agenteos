import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { KeyRound } from "lucide-react";
import { ActionButton, EmptyState, InfoBanner, Input, LoadingState, PageHeader, SectionCard, Select, StatusBadge } from "../../components/ui";
import api from "../../services/api";
import { getAuthenticatedUser } from "../../utils/auth";
import { jevError, jevTime } from "../../utils/jevDiagnostics";


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
  return <div data-testid="jev-integration-form"><SectionCard title="Configuração por empresa · somente SHADOW" description="A chave é somente para escrita. O orçamento e o timeout limitam as chamadas deste provider.">
    {busy && <LoadingState label="Consultando configuração JEV..." />}
    {error && <div role="alert"><InfoBanner tone="danger">{error}</InfoBanner></div>}
    {message && <div role="status" aria-live="polite"><InfoBanner tone={message.startsWith("Configuração salva.") || message === CONNECTION.connected ? "success" : "warning"}>{message}</InfoBanner></div>}
    {saved && <><p className="jev-credential-status">Credencial: <StatusBadge tone={saved.has_key ? "success" : "warning"}>{saved.has_key ? "Configurada (somente escrita)" : "Não configurada"}</StatusBadge> · {CONNECTION[saved.status] || "Não testado"}</p><p className="fm-helper">Último teste: {jevTime(saved.tested_at)}. Armazenamento protegido: {saved.credential_storage_available ? "disponível" : "indisponível"}.</p></>}
    {form && <form onSubmit={save} aria-busy={busy}>
      <div className="jev-columns">
        <div className="fm-admin-field"><label htmlFor="jev-provider">Provider</label><Select id="jev-provider" value={form.provider} disabled><option value="typesafe">TypeSafe</option></Select></div>
        <div className="fm-admin-field"><label htmlFor="jev-model">Modelo</label><Select id="jev-model" value={form.model} disabled><option value="jev-1.13.0">jev-1.13.0</option></Select></div>
      </div>
      <div className="fm-admin-field"><label htmlFor="jev-api-key">Chave da API (nova ou substituição)</label><Input id="jev-api-key" type="password" autoComplete="new-password" value={key} onChange={event => { setKey(event.target.value); setRemoveKey(false); }} placeholder={saved?.has_key ? "Deixe vazio para preservar a chave" : "Informe uma chave para configurar"} disabled={busy || !saved?.credential_storage_available} aria-describedby="jev-key-help" /><p id="jev-key-help" className="fm-helper">A chave salva nunca é exibida. Deixe este campo vazio para manter a credencial existente.</p></div>
      {saved?.has_key && <label className="jev-checkbox"><input type="checkbox" checked={removeKey} onChange={event => { setRemoveKey(event.target.checked); setKey(""); }} disabled={busy} />Remover credencial salva ao salvar</label>}
      <div className="jev-columns">
        <div className="fm-admin-field"><label htmlFor="jev-budget">Orçamento diário (USD)</label><Input id="jev-budget" type="number" min="0" max="1000" step="0.000001" required value={form.daily_budget_usd} disabled={busy} onChange={event => setForm(previous => ({ ...previous, daily_budget_usd: event.target.value }))} /></div>
        <div className="fm-admin-field"><label htmlFor="jev-timeout">Timeout (segundos)</label><Input id="jev-timeout" type="number" min="1" max="15" required value={form.timeout_seconds} disabled={busy} onChange={event => setForm(previous => ({ ...previous, timeout_seconds: event.target.value }))} /></div>
      </div>
      <label className="jev-checkbox"><input type="checkbox" checked={form.enabled} disabled={busy} onChange={event => setForm(previous => ({ ...previous, enabled: event.target.checked }))} />Habilitar provider em SHADOW para exemplos sintéticos explícitos</label>
      <div className="jev-toolbar"><ActionButton type="submit" disabled={busy || !saved?.credential_storage_available}>Salvar configuração JEV</ActionButton><ActionButton variant="secondary" type="button" disabled={busy || !saved?.configured} onClick={test}>Testar conexão salva</ActionButton></div>
      <p className="fm-helper">O teste consulta a disponibilidade de modelos, sem enviar uma conversa. Salve a chave antes de testar. A chave não volta ao navegador e não é armazenada neste navegador.</p>
    </form>}
  </SectionCard></div>;
}
export default function JevIntegration() {
  const [searchParams] = useSearchParams();
  const requestedTenant = searchParams.get("empresa") || "";
  const [authorized, setAuthorized] = useState(() => getAuthenticatedUser()?.role === "super_admin");
  const [companies, setCompanies] = useState([]);
  const [tenant, setTenant] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { const update = () => setAuthorized(getAuthenticatedUser()?.role === "super_admin"); window.addEventListener("storage", update); return () => window.removeEventListener("storage", update); }, []);
  useEffect(() => {
    const controller = new AbortController();
    const loadCompanies = async () => {
      setLoading(true); setError("");
      await api.get("/empresas/", { signal: controller.signal }).then(response => {
        if (!controller.signal.aborted && getAuthenticatedUser()?.role === "super_admin") {
          const available = Array.isArray(response.data) ? response.data.map(item => ({ id: item.id, name: item.nome_empresa || "Empresa" })) : [];
          setCompanies(available);
          if (requestedTenant) setTenant(available.some(item => String(item.id) === requestedTenant) ? requestedTenant : "");
        }
      }).catch(caught => { if (!controller.signal.aborted) setError(jevError(caught)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    };
    if (authorized) loadCompanies();
    return () => controller.abort();
  }, [authorized, requestedTenant, attempt]);
  if (!authorized) return <div role="alert"><InfoBanner tone="warning">Configuração JEV exclusiva do Super Admin.</InfoBanner></div>;
  return <div className="fm-admin-page fm-admin-settings fm-admin-jev" data-testid="jev-admin">
    <PageHeader eyebrow="Super Admin · Integrações" title={<>Integração JEV <StatusBadge tone="info">SHADOW</StatusBadge></>} icon={KeyRound} description="Configuração segura por empresa, sem reiniciar a página ou o atendimento." actions={<Link className="fm-button fm-button-secondary" to="/admin/configuracoes">Voltar às configurações</Link>} />
    <InfoBanner tone="warning">A habilitação permite somente exemplos sintéticos explícitos. Conversas reais continuam bloqueadas para envio externo nesta configuração.</InfoBanner>
    <SectionCard title="Selecionar empresa" description="Cada empresa tem sua própria chave, orçamento e configuração JEV.">
      {loading ? <LoadingState label="Carregando empresas..." /> : error ? <div role="alert"><InfoBanner tone="danger">{error}</InfoBanner><ActionButton variant="secondary" onClick={() => setAttempt(previous => previous + 1)}>Tentar novamente</ActionButton></div> : companies.length === 0 ? <EmptyState title="Nenhuma empresa disponível" description="Cadastre uma empresa para configurar sua integração JEV." action={<Link className="fm-button fm-button-secondary" to="/admin">Ir para empresas</Link>} /> : <>
        <div className="fm-admin-field"><label htmlFor="jev-company">Empresa</label><Select id="jev-company" aria-label="Empresa" value={tenant} onChange={event => setTenant(event.target.value)}><option value="">Selecione uma empresa</option>{companies.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</Select></div>
        {tenant ? <Link className="fm-admin-link" to={`/admin/empresas/${tenant}`}>Ver detalhes da empresa selecionada</Link> : <p className="fm-helper">Selecione uma empresa para consultar e editar a integração.</p>}
        {requestedTenant && !companies.some(item => String(item.id) === requestedTenant) && <p className="fm-helper" role="status">A empresa do link não está disponível. Selecione uma empresa da lista.</p>}
      </>}
    </SectionCard>
    {tenant && !loading && !error && <IntegrationForm key={tenant} tenant={tenant} />}
  </div>;
}
