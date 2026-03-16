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

function App() {
  useEffect(() => {
    window.onerror = function(message, source, lineno, colno, error) {
      alert(`ERRO CRÍTICO CAPTURADO NO REACT:\n\n${message}\n\nArquivo: ${source}\nLinha: ${lineno}`);
      return false; 
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
      </Route>
    </Routes>
  );
}

export default App;
