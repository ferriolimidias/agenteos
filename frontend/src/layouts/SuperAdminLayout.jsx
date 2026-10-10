import React, { useCallback, useState, useEffect } from "react";
import { Outlet, NavLink, Link } from "react-router-dom";
import { BrainCircuit, Settings, Building, LogOut, ShieldCheck } from "lucide-react";
import api from "../services/api";
import { shellStyles } from "../utils/uiStyles";

export default function SuperAdminLayout() {
  const [configVisual, setConfigVisual] = useState({
    nomeSistema: "Carregando...",
    logoBase64: "",
  });

  const fetchConfig = useCallback(async () => {
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
  }, []);

  useEffect(() => {
    queueMicrotask(fetchConfig);
    
    // Listen to updates from ConfiguracoesGlobais form
    window.addEventListener("configuracoesUpdated", fetchConfig);
    return () => {
      window.removeEventListener("configuracoesUpdated", fetchConfig);
    };
  }, [fetchConfig]);

  return (
    <div className={`flex ${shellStyles.page}`}>
      <aside className={shellStyles.sidebar}>
        <div className={shellStyles.sidebarHeader}>
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt="Logo"
              className="h-12 max-w-[190px] rounded-lg border border-slate-200 bg-white object-contain p-1.5"
            />
          ) : (
            <h2 className="truncate text-xl font-semibold text-slate-950">{configVisual.nomeSistema}</h2>
          )}
          <span className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-indigo-100 bg-indigo-50 px-2.5 py-1 text-[11px] font-semibold tracking-[0.16em] text-indigo-700">
            <ShieldCheck size={12} />
            SUPER ADMIN
          </span>
        </div>
        
        <nav className="flex-1 space-y-1.5 py-5">
          <p className={shellStyles.navSection}>Plataforma</p>
          <NavLink to="/admin/" end className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Building size={16} />
            <span>Clientes / Empresas</span>
          </NavLink>
          <NavLink to="/admin/agentes" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <BrainCircuit size={16} />
            <span>Arquitetura Global</span>
          </NavLink>
          <NavLink to="/admin/configuracoes" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Settings size={16} />
            <span>Configurações Globais</span>
          </NavLink>
        </nav>
        
        <div className="border-t border-slate-100 p-3">
          <Link to="/" className={`${shellStyles.navItem} border-transparent text-red-600 hover:border-red-100 hover:bg-red-50 hover:text-red-700`}>
            <LogOut size={16} />
            <span>Sair</span>
          </Link>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <div className={shellStyles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
