import React, { useState, useEffect } from "react";
import { Outlet, Link } from "react-router-dom";
import { Settings, Building, LogOut, Bot } from "lucide-react";
import axios from "axios";

export default function SuperAdminLayout() {
  const [nomeSistema, setNomeSistema] = useState("Carregando...");

  const fetchConfig = async () => {
    try {
      const response = await axios.get("/api/admin/configuracoes");
      if (response.data && response.data.nome_sistema) {
        setNomeSistema(response.data.nome_sistema);
      } else {
        setNomeSistema("ANTIGRAVITY");
      }
    } catch (error) {
      console.error("Erro ao buscar configurações:", error);
      setNomeSistema("ANTIGRAVITY");
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
    <div className="min-h-screen flex bg-gray-900 text-gray-100">
      {/* Sidebar */}
      <aside className="w-64 bg-gray-950 p-6 flex flex-col border-r border-gray-800">
        <div className="mb-8">
          <h2 className="text-xl font-bold text-white uppercase tracking-wider">{nomeSistema}</h2>
          <span className="text-xs text-indigo-400 font-medium tracking-widest">SUPER ADMIN</span>
        </div>
        
        <nav className="flex-1 space-y-2">
          <Link to="/admin/" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-gray-800 px-3 py-2 rounded-lg transition-colors">
            <Building size={20} />
            <span>Empresas</span>
          </Link>
          <Link to="/admin/orquestrador" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-gray-800 px-3 py-2 rounded-lg transition-colors">
            <Bot size={20} />
            <span>Agente Atendente</span>
          </Link>
          <Link to="/admin/configuracoes" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-gray-800 px-3 py-2 rounded-lg transition-colors">
            <Settings size={20} />
            <span>Configurações globais</span>
          </Link>
        </nav>
        
        <div className="pt-6 border-t border-gray-800">
          <Link to="/" className="flex items-center space-x-3 text-red-400 hover:text-red-300 hover:bg-gray-800 px-3 py-2 rounded-lg transition-colors">
            <LogOut size={20} />
            <span>Sair</span>
          </Link>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto bg-gray-900">
        <div className="p-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
