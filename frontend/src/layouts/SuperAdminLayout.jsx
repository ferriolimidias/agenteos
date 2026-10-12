import React, { useCallback, useState, useEffect, useRef } from "react";
import { Outlet, NavLink, Link, useLocation } from "react-router-dom";
import { BrainCircuit, Settings, Building, LogOut, ShieldCheck, Menu, X } from "lucide-react";
import api from "../services/api";
import { shellStyles } from "../utils/uiStyles";

export default function SuperAdminLayout() {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const sidebarRef = useRef(null);
  const menuToggleRef = useRef(null);

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 1024px)");
    const closeOnDesktop = () => { if (desktop.matches) setMenuOpen(false); };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const sidebar = sidebarRef.current;
    const menuToggle = menuToggleRef.current;
    const focusables = () => [...sidebar.querySelectorAll("a[href], button:not([disabled])")];
    const initialFocus = requestAnimationFrame(() => focusables()[0]?.focus({ preventScroll: true }));
    const handleKeyDown = (event) => {
      if (event.key === "Escape") { event.preventDefault(); setMenuOpen(false); }
      if (event.key !== "Tab") return;
      const items = focusables();
      const first = items[0];
      const last = items.at(-1);
      if (!sidebar.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus({ preventScroll: true });
        return;
      }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      cancelAnimationFrame(initialFocus);
      document.removeEventListener("keydown", handleKeyDown);
      requestAnimationFrame(() => menuToggle?.focus());
    };
  }, [menuOpen]);

  const [configVisual, setConfigVisual] = useState({
    nomeSistema: "Carregando...",
    logoBase64: "",
  });

  const fetchConfig = useCallback(async () => {
    try {
      const response = await api.get("/admin/configuracoes");
      setConfigVisual({
        nomeSistema: response.data?.nome_sistema || "AgenteOS",
        logoBase64: response.data?.logo_base64 || "",
      });
    } catch (error) {
      console.error("Erro ao buscar configurações:", error);
      setConfigVisual({
        nomeSistema: "AgenteOS",
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
    <div className={shellStyles.page}>
      <a href="#admin-content" className="fm-skip-link">Ir para o conteúdo</a>
      <aside ref={sidebarRef} id="admin-navigation" className={`${shellStyles.sidebar} ${menuOpen ? "fm-sidebar-open" : ""}`} role={menuOpen ? "dialog" : undefined} aria-modal={menuOpen ? "true" : undefined} aria-label="Navegação Super Admin">
        <button type="button" className="fm-menu-close" aria-label="Fechar menu" onClick={() => setMenuOpen(false)}><X size={20} aria-hidden="true" /></button>
        <div className={shellStyles.sidebarHeader}>
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt={`Logo ${configVisual.nomeSistema}`}
              className="fm-brand-logo"
            />
          ) : (
            <h2 className="fm-brand-name">{configVisual.nomeSistema}</h2>
          )}
          <span className="fm-brand-role">
            <ShieldCheck size={12} />
            SUPER ADMIN
          </span>
        </div>
        
        <nav className="fm-sidebar-nav" aria-label="Menu principal" onClick={(event) => { if (event.target.closest("a")) setMenuOpen(false); }}>
          <p className={shellStyles.navSection}>Plataforma</p>
          <NavLink to="/admin/" end className={({ isActive }) => `${shellStyles.navItem} ${isActive || location.pathname.replace(/\/$/, "") === "/admin" ? shellStyles.navActive : shellStyles.navInactive}`}>
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
        
        <div className="fm-sidebar-footer">
          <Link to="/" className={shellStyles.navItem}>
            <LogOut size={16} />
            <span>Sair</span>
          </Link>
        </div>
      </aside>

      <button type="button" className="fm-shell-backdrop" tabIndex={-1} aria-label="Fechar menu" onClick={() => setMenuOpen(false)} />
      <main className="fm-shell-main" {...(menuOpen ? { inert: "" } : {})}>
        <header className={shellStyles.topbar}>
          <button ref={menuToggleRef} type="button" className="fm-menu-toggle" aria-label="Abrir menu" aria-expanded={menuOpen} aria-controls="admin-navigation" onClick={() => setMenuOpen(true)}><Menu size={20} aria-hidden="true" /></button>
          <div className="fm-topbar-context"><span className="fm-topbar-label">Administração da plataforma</span><span className="fm-topbar-title">{configVisual.nomeSistema}</span></div>
          <span className="fm-avatar" aria-label="Super Admin">SA</span>
        </header>
        <div id="admin-content" tabIndex={-1} className={shellStyles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
