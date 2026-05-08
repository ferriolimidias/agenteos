import { Navigate, Routes, Route } from "react-router-dom";
import React, { useEffect } from "react";
import Login from "./pages/Login";
import SuperAdminLayout from "./layouts/SuperAdminLayout";
import TenantLayout from "./layouts/TenantLayout";
import Empresas from "./pages/super-admin/Empresas";
import ConfiguracoesGlobais from "./pages/super-admin/ConfiguracoesGlobais";
import EmpresaDetalhes from "./pages/super-admin/EmpresaDetalhes";
import TenantDashboard from "./pages/tenant/TenantDashboard";
import TenantAgentes from "./pages/tenant/TenantAgentes";
import TenantFollowUps from "./pages/tenant/TenantFollowUps";
import Rag from "./pages/tenant/Rag";
import Crm from "./pages/tenant/Crm";
import Agenda from "./pages/tenant/Agenda";
import Simulador from "./pages/tenant/Simulador";
import Inbox from "./pages/tenant/Inbox";
import Integracoes from "./pages/tenant/Integracoes";
import Transferencias from "./pages/tenant/Transferencias";
import Campanhas from "./pages/tenant/Campanhas";
import GestaoTags from "./pages/tenant/GestaoTags";
import Unidades from "./pages/tenant/Unidades";
import ConfiguracoesConversoes from "./pages/tenant/ConfiguracoesConversoes";
import api from "./services/api";

function App() {
  useEffect(() => {
    window.onerror = function(message, source, lineno, colno, error) {
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
    <Routes>
      <Route path="/" element={<Login />} />
      
      {/* Rota do Super Admin */}
      <Route path="/admin" element={<SuperAdminLayout />}>
        <Route index element={<Empresas />} />
        <Route path="agente" element={<Navigate to="/admin" replace />} />
        <Route path="empresas/:empresaId" element={<EmpresaDetalhes />} />
        <Route path="configuracoes" element={<ConfiguracoesGlobais />} />
      </Route>

      {/* Rota do Cliente (Tenant) */}
      <Route path="/painel" element={<TenantLayout />}>
        <Route index element={<TenantDashboard />} />
        <Route path="agentes" element={<TenantAgentes />} />
        <Route path="followups" element={<TenantFollowUps />} />
        <Route path="crm" element={<Crm />} />
        <Route path="rag" element={<Rag />} />
        <Route path="agenda" element={<Agenda />} />
        <Route path="simulador" element={<Simulador />} />
        <Route path="inbox" element={<Inbox />} />
        <Route path="chat" element={<Inbox />} />
        <Route path="conversas" element={<Inbox />} />
        <Route path="integracoes" element={<Integracoes />} />
        <Route path="transferencias" element={<Transferencias />} />
        <Route path="campanhas" element={<Campanhas />} />
        <Route path="tags" element={<GestaoTags />} />
        <Route path="unidades" element={<Unidades />} />
        <Route path="conversoes" element={<ConfiguracoesConversoes />} />
      </Route>
    </Routes>
  );
}

export default App;
