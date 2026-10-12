import { Navigate, Routes, Route } from "react-router-dom";
import React, { Suspense, lazy, useEffect } from "react";
import api from "./services/api";
import { LoadingState } from "./components/ui";

const Login = lazy(() => import("./pages/Login"));
const SuperAdminLayout = lazy(() => import("./layouts/SuperAdminLayout"));
const TenantLayout = lazy(() => import("./layouts/TenantLayout"));
const Empresas = lazy(() => import("./pages/super-admin/Empresas"));
const ConfiguracoesGlobais = lazy(() => import("./pages/super-admin/ConfiguracoesGlobais"));
const JevIntegration = lazy(() => import("./pages/super-admin/JevIntegration"));
const EmpresaDetalhes = lazy(() => import("./pages/super-admin/EmpresaDetalhes"));
const FunnelDashboard = lazy(() => import("./pages/tenant/FunnelDashboard"));
const JevDiagnostics = lazy(() => import("./pages/tenant/JevDiagnostics"));
const TenantDashboard = lazy(() => import("./pages/tenant/TenantDashboard"));
const TenantAgentes = lazy(() => import("./pages/tenant/TenantAgentes"));
const TenantFollowUps = lazy(() => import("./pages/tenant/TenantFollowUps"));
const Rag = lazy(() => import("./pages/tenant/Rag"));
const Crm = lazy(() => import("./pages/tenant/Crm"));
const Agenda = lazy(() => import("./pages/tenant/Agenda"));
const Simulador = lazy(() => import("./pages/tenant/Simulador"));
const Inbox = lazy(() => import("./pages/tenant/Inbox"));
const Conexoes = lazy(() => import("./pages/tenant/Conexoes"));
const Transferencias = lazy(() => import("./pages/tenant/Transferencias"));
const Campanhas = lazy(() => import("./pages/tenant/Campanhas"));
const GestaoTags = lazy(() => import("./pages/tenant/GestaoTags"));
const Unidades = lazy(() => import("./pages/tenant/Unidades"));
const AcessoRestritoOperacional = lazy(() => import("./pages/tenant/AcessoRestritoOperacional"));
const Instagram = lazy(() => import("./pages/tenant/Instagram"));
const TrafficMeta = lazy(() => import("./pages/tenant/TrafficMeta"));
const ConversionOperations = lazy(() => import("./pages/tenant/ConversionOperations"));

function App() {
  useEffect(() => {
    window.onerror = function(message, source, lineno) {
      alert(`ERRO CRÍTICO CAPTURADO NO REACT:\n\n${message}\n\nArquivo: ${source}\nLinha: ${lineno}`);
      return false; 
    };

    const applyFavicon = (faviconHref) => {
      if (!faviconHref) {
        return;
      }
      let link = document.querySelector("link[rel='icon']");
      if (!link) {
        link = document.createElement("link");
        link.setAttribute("rel", "icon");
        document.head.appendChild(link);
      }
      link.setAttribute("href", faviconHref);
    };

    const loadGlobalFavicon = async () => {
      try {
        const response = await api.get("/public/configuracoes-branding");
        const data = response?.data || {};
        applyFavicon(data?.favicon_base64);
      } catch (error) {
        // Branding é público e opcional: nunca deve impactar estado de autenticação.
        console.error("Falha ao carregar favicon global:", error);
      }
    };

    const onConfigUpdated = () => {
      loadGlobalFavicon();
    };

    loadGlobalFavicon();
    window.addEventListener("configuracoesUpdated", onConfigUpdated);
    return () => {
      window.removeEventListener("configuracoesUpdated", onConfigUpdated);
    };
  }, []);

  return (
    <Suspense fallback={<div className="min-h-screen bg-slate-100 p-6"><LoadingState label="Carregando interface..." /></div>}>
      <Routes>
        <Route path="/" element={<Login />} />

        {/* Rota do Super Admin */}
        <Route path="/admin" element={<SuperAdminLayout />}>
          <Route index element={<Empresas />} />
          <Route path="agente" element={<Navigate to="/admin/agentes" replace />} />
          <Route path="orquestrador" element={<Navigate to="/admin/agentes" replace />} />
          <Route path="empresas/:empresaId" element={<EmpresaDetalhes />} />
          <Route path="configuracoes" element={<ConfiguracoesGlobais />} />
          <Route path="configuracoes/integracoes/jev" element={<JevIntegration />} />
        </Route>

        {/* Rota do Cliente (Tenant) */}
        <Route path="/painel" element={<TenantLayout />}>
          <Route index element={<TenantDashboard />} />
          <Route path="funil" element={<FunnelDashboard />} />
          <Route path="inteligencia" element={<JevDiagnostics />} />
          <Route path="agentes" element={<TenantAgentes />} />
          <Route path="followups" element={<TenantFollowUps />} />
          <Route path="crm" element={<Crm />} />
          <Route path="rag" element={<Rag />} />
          <Route path="agenda" element={<Agenda />} />
          <Route path="simulador" element={<Simulador />} />
          <Route path="inbox" element={<Inbox />} />
          <Route path="chat" element={<Inbox />} />
          <Route path="conversas" element={<Inbox />} />
          <Route path="conexoes" element={<Conexoes />} />
          <Route path="instagram" element={<Instagram />} />
          <Route path="trafego/meta" element={<TrafficMeta />} />
          <Route path="trafego/meta/conversoes" element={<ConversionOperations />} />
          <Route path="integracoes" element={<AcessoRestritoOperacional title="Integrações técnicas gerenciadas pelo Super Admin" />} />
          <Route path="transferencias" element={<Transferencias />} />
          <Route path="campanhas/*" element={<Campanhas />} />
          <Route path="campanhas/templates" element={<Campanhas />} />
          <Route path="campanhas/templates/novo" element={<Campanhas />} />
          <Route path="campanhas/templates/:id" element={<Campanhas />} />
          <Route path="campanhas/nova" element={<Campanhas />} />
          <Route path="campanhas/:id" element={<Campanhas />} />
          <Route path="campanhas/publicos" element={<Campanhas />} />
          <Route path="campanhas/resultados" element={<Campanhas />} />
          <Route path="campanhas/resultados/custos" element={<Campanhas />} />
          <Route path="campanhas/resultados/conversoes" element={<Campanhas />} />
          <Route path="templates-whatsapp" element={<Navigate to="/painel/campanhas/templates" replace />} />
          <Route path="tags" element={<GestaoTags />} />
          <Route path="unidades" element={<Unidades />} />
          <Route path="conversoes" element={<AcessoRestritoOperacional title="Configurações de conversão gerenciadas pelo Super Admin" />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

export default App;
