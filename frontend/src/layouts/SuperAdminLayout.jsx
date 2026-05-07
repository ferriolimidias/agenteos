import React, { useState, useEffect } from "react";
import { Outlet, Link } from "react-router-dom";
import { Settings, Building, LogOut } from "lucide-react";
import api from "../services/api";

export default function SuperAdminLayout() {
  const [configVisual, setConfigVisual] = useState({
    nomeSistema: "Carregando...",
    logoBase64: "",
  });

  const fetchConfig = async () => {
    try {
      const response = await api.get("/admin/configuracoes");
      setConfigVisual({
        nomeSistema: response.data?.nome_sistema || "ANTIGRAVITY",
        logoBase64: response.data?.logo_base64 || "",
      });
    } catch (error) {
      console.error("Erro ao buscar configurações:", error);
      setConfigVisual({
        nomeSistema: "ANTIGRAVITY",
        logoBase64: "",
      });
    }
  };

  useEffect(() => {
    fetchConfig();
    
    // Listen to updates from ConfiguracoesGlobais form
    window.addEventListener("configuracoesUpdated", fetchConfig);
    return () => {
      window.removeEventListener("configuracoesUpdated", fetchConfig);
    };
  }, []);
  return (
    <div className="min-h-screen flex bg-[#0f0f10] text-gray-100">
      {/* Sidebar */}
      <aside className="w-60 bg-[#111112] p-5 flex flex-col border-r border-[#2d2d2d]">
        <div className="mb-8">
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt="Logo"
              className="h-12 max-w-[180px] object-contain bg-white rounded-md p-1"
            />
          ) : (
            <h2 className="text-xl font-bold text-white uppercase tracking-wider">{configVisual.nomeSistema}</h2>
          )}
          <span className="text-[11px] text-indigo-400 font-medium tracking-widest">SUPER ADMIN</span>
        </div>
        
        <nav className="flex-1 space-y-2">
          <Link to="/admin/" className="flex items-center space-x-2.5 text-sm text-gray-300 hover:text-white hover:bg-[#1a1a1a] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <Building size={16} />
            <span>Clientes</span>
          </Link>
          <Link to="/admin/configuracoes" className="flex items-center space-x-2.5 text-sm text-gray-300 hover:text-white hover:bg-[#1a1a1a] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <Settings size={16} />
            <span>Configurações</span>
          </Link>
        </nav>
        
        <div className="pt-6 border-t border-[#2d2d2d]">
          <Link to="/" className="flex items-center space-x-2.5 text-sm text-red-400 hover:text-red-300 hover:bg-[#1a1a1a] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <LogOut size={16} />
            <span>Sair</span>
          </Link>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto bg-[#0f0f10]">
        <div className="p-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
