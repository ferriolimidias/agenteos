import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Copy, CreditCard, RefreshCw, Save, XCircle } from "lucide-react";

import api from "../services/api";

const EMPTY_CONFIG = {
  configured: false,
  enabled: true,
  environment: "test",
  access_token_configured: false,
  webhook_secret_configured: false,
  webhook_url: "",
};

export default function EmpresaPaymentManager({ empresaId, onNotify }) {
  const [config, setConfig] = useState(EMPTY_CONFIG);
  const [accessToken, setAccessToken] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [connectionResult, setConnectionResult] = useState(null);

  const endpoint = `/admin/empresas/${empresaId}/payments/mercado-pago`;

  const loadConfig = useCallback(async () => {
    if (!empresaId) return;
    setLoading(true);
    setError("");
    try {
      const response = await api.get(endpoint);
      setConfig({ ...EMPTY_CONFIG, ...(response.data || {}) });
    } catch {
      setError("Não foi possível carregar a configuração de pagamento.");
    } finally {
      setLoading(false);
    }
  }, [empresaId, endpoint]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const saveConfig = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    setConnectionResult(null);
    try {
      const payload = {
        environment: config.environment,
        enabled: true,
      };
      if (accessToken.trim()) payload.access_token = accessToken.trim();
      if (webhookSecret.trim()) payload.webhook_secret = webhookSecret.trim();
      const response = await api.put(endpoint, payload);
      setConfig({ ...EMPTY_CONFIG, ...(response.data || {}) });
      setAccessToken("");
      setWebhookSecret("");
      onNotify?.("Configuração do Mercado Pago salva.");
    } catch (requestError) {
      const detail = String(requestError?.response?.data?.detail || "");
      if (detail === "access_token_required") {
        setError("Informe o Access Token para a primeira configuração.");
      } else if (detail === "webhook_secret_required") {
        setError("Informe o Webhook Secret para a primeira configuração.");
      } else {
        setError("Não foi possível salvar a configuração.");
      }
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    setError("");
    setConnectionResult(null);
    try {
      const response = await api.post(`${endpoint}/test`);
      setConnectionResult(Boolean(response.data?.valid));
    } catch {
      setConnectionResult(false);
    } finally {
      setTesting(false);
    }
  };

  const copyWebhookUrl = async () => {
    if (!config.webhook_url) return;
    try {
      await navigator.clipboard.writeText(config.webhook_url);
      onNotify?.("URL do webhook copiada.");
    } catch {
      onNotify?.("Não foi possível copiar a URL automaticamente.");
    }
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500 shadow-sm">
        Carregando configuração de pagamentos...
      </div>
    );
  }

  return (
    <form onSubmit={saveConfig} className="space-y-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-slate-950">
            <CreditCard size={18} />
            Mercado Pago
          </h3>
          <p className="mt-1 text-xs text-slate-500">Checkout Pro configurado exclusivamente para esta empresa.</p>
        </div>
        <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold ${config.configured ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-slate-50 text-slate-600"}`}>
          <span aria-hidden="true">{config.configured ? "●" : "○"}</span>
          {config.configured ? "Configurado" : "Não configurado"}
        </span>
      </div>

      <fieldset>
        <legend className="mb-2 text-xs font-medium text-slate-600">Ambiente</legend>
        <div className="flex flex-wrap gap-4">
          {[
            ["test", "Teste"],
            ["production", "Produção"],
          ].map(([value, label]) => (
            <label key={value} className="inline-flex items-center gap-2 text-sm text-slate-700">
              <input
                type="radio"
                name="mercado-pago-environment"
                value={value}
                checked={config.environment === value}
                onChange={(event) => setConfig((current) => ({ ...current, environment: event.target.value }))}
                className="h-4 w-4 border-slate-300 text-indigo-600"
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-1 flex items-center justify-between gap-2">
            <label htmlFor="mercado-pago-access-token" className="text-xs font-medium text-slate-600">Access Token</label>
            {config.access_token_configured ? <span className="text-xs font-medium text-emerald-700">Configurado ✓</span> : null}
          </div>
          <input
            id="mercado-pago-access-token"
            type="password"
            autoComplete="new-password"
            value={accessToken}
            onChange={(event) => setAccessToken(event.target.value)}
            placeholder={config.access_token_configured ? "Substituir credencial" : "Informe o Access Token"}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
          />
          {config.access_token_configured ? <p className="mt-1 text-xs text-slate-500">Deixe vazio para manter a credencial atual.</p> : null}
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between gap-2">
            <label htmlFor="mercado-pago-webhook-secret" className="text-xs font-medium text-slate-600">Webhook Secret</label>
            {config.webhook_secret_configured ? <span className="text-xs font-medium text-emerald-700">Configurado ✓</span> : null}
          </div>
          <input
            id="mercado-pago-webhook-secret"
            type="password"
            autoComplete="new-password"
            value={webhookSecret}
            onChange={(event) => setWebhookSecret(event.target.value)}
            placeholder={config.webhook_secret_configured ? "Substituir credencial" : "Informe o Webhook Secret"}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500"
          />
          {config.webhook_secret_configured ? <p className="mt-1 text-xs text-slate-500">Deixe vazio para manter a credencial atual.</p> : null}
        </div>
      </div>

      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
        <label htmlFor="mercado-pago-webhook-url" className="block text-xs font-medium text-slate-600">URL do Webhook</label>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input
            id="mercado-pago-webhook-url"
            readOnly
            value={config.webhook_url || ""}
            className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600"
          />
          <button
            type="button"
            onClick={copyWebhookUrl}
            disabled={!config.webhook_url}
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-50"
          >
            <Copy size={14} />
            Copiar
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-500">Cadastre esta URL nas notificações de Pagamentos do Mercado Pago.</p>
      </div>

      {error ? <p role="alert" className="text-sm text-rose-700">{error}</p> : null}
      {connectionResult === true ? (
        <p className="flex items-center gap-2 text-sm font-medium text-emerald-700"><CheckCircle2 size={16} /> Conexão válida</p>
      ) : null}
      {connectionResult === false ? (
        <p className="flex items-center gap-2 text-sm font-medium text-rose-700"><XCircle size={16} /> Não foi possível validar esta credencial.</p>
      ) : null}

      <div className="flex flex-wrap gap-3">
        <button
          type="submit"
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-60"
        >
          <Save size={14} />
          {saving ? "Salvando..." : "Salvar configuração"}
        </button>
        <button
          type="button"
          onClick={testConnection}
          disabled={testing || !config.access_token_configured}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={testing ? "animate-spin" : ""} />
          {testing ? "Testando..." : "Testar conexão"}
        </button>
      </div>
    </form>
  );
}
