import React, { useCallback, useState, useEffect, useRef } from "react";
import { Navigate, Outlet, Link, useLocation } from "react-router-dom";
import { Activity, Building, Building2, CreditCard, LogOut, MessageSquare, PlugZap, Settings, ShieldCheck, ShoppingBag, Users, Menu, X } from "lucide-react";
import api from "../services/api";
import { shellStyles } from "../utils/uiStyles";
import { getStoredUser } from "../utils/auth";
import { COMPANY_ADMIN_SECTIONS, resolveCompanyAdminSection } from "../utils/superAdminNavigation";
import "../styles/admin-navigation.css";

export default function SuperAdminLayout() {
  const user = getStoredUser();
  const isSuperAdmin = user?.role === "super_admin";
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const companyPage = location.pathname.startsWith("/admin/empresas/");
  const companiesReturn = /^\/admin(?:\?|$)/.test(location.state?.companiesReturn || "") ? location.state.companiesReturn : "/admin";
  const companySection = companyPage ? resolveCompanyAdminSection(location) : null;
  const companySectionLabel = COMPANY_ADMIN_SECTIONS.find(({ id }) => id === companySection)?.label || "Visão geral";
  const globalLinks = [
    { to: "/admin", label: "Empresas", icon: Building, active: location.pathname === "/admin" || location.pathname === "/admin/" },
    { to: "/admin/operacao", label: "Operação e saúde", icon: Activity, active: location.pathname.startsWith("/admin/operacao") },
    { to: "/admin/integracoes", label: "Integrações", icon: PlugZap, active: location.pathname.startsWith("/admin/integracoes") || location.pathname.startsWith("/admin/configuracoes/integracoes") },
    { to: "/admin/seguranca", label: "Segurança e auditoria", icon: ShieldCheck, active: location.pathname.startsWith("/admin/seguranca") },
    { to: "/admin/configuracoes", label: "Configurações da plataforma", icon: Settings, active: location.pathname === "/admin/configuracoes" },
  ];
  const contextTitle = companyPage ? companySectionLabel
    : location.pathname.startsWith("/admin/operacao") ? "Operação e saúde"
      : location.pathname.startsWith("/admin/integracoes") || location.pathname.startsWith("/admin/configuracoes/integracoes") ? "Integrações"
        : location.pathname.startsWith("/admin/seguranca") ? "Segurança e auditoria"
          : location.pathname.startsWith("/admin/configuracoes") ? "Configurações da plataforma"
            : location.pathname.startsWith("/admin/agentes") ? "Agentes por empresa" : "Empresas";
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
    if (!isSuperAdmin) return;
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
  }, [isSuperAdmin]);

  useEffect(() => {
    queueMicrotask(fetchConfig);
    
    // Listen to updates from ConfiguracoesGlobais form
    window.addEventListener("configuracoesUpdated", fetchConfig);
    return () => {
      window.removeEventListener("configuracoesUpdated", fetchConfig);
    };
  }, [fetchConfig]);

  if (!isSuperAdmin) {
    return <Navigate to={user ? "/painel/inbox" : "/"} replace state={{ from: location.pathname }} />;
  }

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
        
        <nav className="fm-sidebar-nav" aria-label={companyPage ? "Navegação da empresa" : "Navegação global"} onClick={(event) => { if (event.target.closest("a")) setMenuOpen(false); }}>
          {companyPage ? <>
            <Link to={companiesReturn} className={`${shellStyles.navItem} ${shellStyles.navInactive} fm-admin-back-link`}><Building size={16} aria-hidden="true" /><span>Voltar às empresas</span></Link>
            <p className={shellStyles.navSection}>Administração da empresa</p>
            {COMPANY_ADMIN_SECTIONS.map(({ id, label, href }) => {
              const active = id === companySection;
              const Icon = ({ "visao-geral": Building2, "atendimento-ia": MessageSquare, "crm-regras": Users, canais: PlugZap, "campanhas-meta": ShoppingBag, pagamentos: CreditCard, usuarios: Users, diagnosticos: ShieldCheck })[id];
              return <Link key={id} to={href(location.pathname.split("/")[3])} state={{ ...location.state, companiesReturn }} aria-current={active ? "page" : undefined} className={`${shellStyles.navItem} ${active ? shellStyles.navActive : shellStyles.navInactive}`}>
                <Icon size={16} aria-hidden="true" /><span>{label}</span>
              </Link>;
            })}
          </> : <>
            <p className={shellStyles.navSection}>Plataforma Ferrioli</p>
            {globalLinks.map(({ to, label, icon, active }) => <Link key={to} to={to} aria-current={active ? "page" : undefined} className={`${shellStyles.navItem} ${active ? shellStyles.navActive : shellStyles.navInactive}`}>
              {React.createElement(icon, { size: 16, "aria-hidden": true })}<span>{label}</span>
            </Link>)}
          </>}
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
          <div className="fm-topbar-context"><span className="fm-topbar-label">Administração da plataforma</span><span className="fm-topbar-title">{contextTitle}</span></div>
          <span className="fm-avatar" aria-label="Super Admin">SA</span>
        </header>
        <div id="admin-content" tabIndex={-1} className={shellStyles.content}>
          <nav className="fm-admin-breadcrumbs" aria-label="Caminho da página">
            <ol>
              {companyPage ? <>
                <li><Link to={companiesReturn}>Empresas</Link></li>
                <li><Link to={`/admin/empresas/${location.pathname.split("/")[3]}`} state={{ ...location.state, companiesReturn }}>Empresa</Link></li>
                <li><span aria-current="page">{contextTitle}</span></li>
              </> : <>
                <li>{contextTitle === "Empresas" ? <span aria-current="page">Plataforma</span> : <Link to="/admin">Plataforma</Link>}</li>
                {contextTitle !== "Empresas" && <li><span aria-current="page">{contextTitle}</span></li>}
              </>}
            </ol>
          </nav>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
