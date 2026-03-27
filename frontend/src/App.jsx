import { Routes, Route } from "react-router-dom";
import React, { useEffect } from "react";
import Login from "./pages/Login";
import SuperAdminLayout from "./layouts/SuperAdminLayout";
import TenantLayout from "./layouts/TenantLayout";
import Empresas from "./pages/super-admin/Empresas";
import Orquestrador from "./pages/super-admin/Orquestrador";
import ConfiguracoesGlobais from "./pages/super-admin/ConfiguracoesGlobais";
import Dashboard from "./pages/tenant/Dashboard";
import Rag from "./pages/tenant/Rag";
import Crm from "./pages/tenant/Crm";
import Agenda from "./pages/tenant/Agenda";
import Simulador from "./pages/tenant/Simulador";
import Inbox from "./pages/tenant/Inbox";
import Integracoes from "./pages/tenant/Integracoes";
import Transferencias from "./pages/tenant/Transferencias";
import Campanhas from "./pages/tenant/Campanhas";
import GestaoTags from "./pages/tenant/GestaoTags";

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
        const response = await fetch("/api/admin/configuracoes");
        if (!response.ok) {
          return;
        }
        const data = await response.json();
        applyFavicon(data?.favicon_base64);
      } catch (error) {
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
        <Route path="orquestrador" element={<Orquestrador />} />
        <Route path="configuracoes" element={<ConfiguracoesGlobais />} />
      </Route>

      {/* Rota do Cliente (Tenant) */}
      <Route path="/painel" element={<TenantLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="crm" element={<Crm />} />
        <Route path="rag" element={<Rag />} />
        <Route path="agenda" element={<Agenda />} />
        <Route path="simulador" element={<Simulador />} />
        <Route path="inbox" element={<Inbox />} />
        <Route path="integracoes" element={<Integracoes />} />
        <Route path="transferencias" element={<Transferencias />} />
        <Route path="campanhas" element={<Campanhas />} />
        <Route path="tags" element={<GestaoTags />} />
      </Route>
    </Routes>
  );
}

export default App;
