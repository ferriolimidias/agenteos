import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Activity, ArrowLeft, ArrowRight, Brain, Building, FileText, KeyRound, Pencil, Plus, RefreshCw, Settings, Trash, UserCheck, Users } from "lucide-react";
import api from "../../services/api";
import { ActionButton, EmptyState, ErrorState, InfoBanner, Input, LoadingState, PageHeader, SectionCard, Select, StatusBadge, Table } from "../../components/ui";
import AdminDialog from "../../components/admin/AdminDialog";
import { clearImpersonation } from "../../utils/auth";
import { companyAreas, companyStatus, filterCompanies } from "../../utils/adminCompanies";
import "./Empresas.css";

const SETUP_DEFAULT = { nome_empresa: "", area_atuacao: "", logo_url: "", ia_personalidade: "", ia_regras_negocio: "", admin_nome: "", admin_email: "", admin_senha: "" };

function CompanyForm({ editing, value, onChange, onLogo, busy, onSubmit, onCancel, error }) {
  const field = (name, label, props = {}) => <label className="fm-company-field">{label}<Input name={name} value={value[name]} onChange={onChange} disabled={busy} {...props} /></label>;
  return <form onSubmit={onSubmit} className="fm-company-form" aria-busy={busy}>
    {error && <div role="alert"><InfoBanner tone="danger">{error}</InfoBanner></div>}
    <div className="fm-company-form-grid">
      <SectionCard title="Dados da empresa" description="Identidade usada na gestão e no painel do cliente." icon={Building}>
        <div className="fm-company-fields">
          {field("nome_empresa", "Nome da empresa *", { required:true, autoFocus:true })}
          {field("area_atuacao", "Área de atuação")}
          <label className="fm-company-field">Logo da empresa<Input type="file" accept=".png, .jpg, .jpeg, .svg" onChange={onLogo} disabled={busy} /></label>
          {value.logo_url && <img className="fm-company-logo-preview" src={value.logo_url} alt="Prévia do logo da empresa" />}
          <p className="fm-helper">O nome e o logo configurados permanecem no painel do cliente.</p>
        </div>
      </SectionCard>
      {!editing && <>
        <SectionCard title="Administrador inicial" description="Dados de acesso do responsável pela empresa." icon={Users}>
          <div className="fm-company-fields">
            {field("admin_nome", "Nome do responsável *", {required:true, autoComplete:"name"})}
            {field("admin_email", "Email de login *", {required:true, type:"email", autoComplete:"email"})}
            {field("admin_senha", "Senha inicial *", {required:true, type:"password", autoComplete:"new-password"})}
            <p className="fm-helper">Depois do cadastro, gerencie os acessos na seção Usuários da empresa.</p>
          </div>
        </SectionCard>
        <SectionCard title="Instruções iniciais do agente" description="Estas configurações continuam disponíveis na seção Agente da empresa." icon={Brain} className="fm-company-form-wide">
          <div className="fm-company-fields">
            <label className="fm-company-field">Identidade e tom de voz<textarea className="fm-field" name="ia_personalidade" value={value.ia_personalidade} onChange={onChange} rows={3} disabled={busy} /></label>
            <label className="fm-company-field">Diretrizes de atendimento e vendas<textarea className="fm-field" name="ia_regras_negocio" value={value.ia_regras_negocio} onChange={onChange} rows={3} disabled={busy} /></label>
          </div>
        </SectionCard>
      </>}
    </div>
    <div className="fm-company-form-actions"><ActionButton type="button" variant="secondary" onClick={onCancel} disabled={busy}>Cancelar</ActionButton><ActionButton type="submit" disabled={busy}>{busy ? "Salvando..." : editing ? "Salvar alterações" : "Concluir cadastro"}</ActionButton></div>
  </form>;
}

export default function Empresas() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [empresas, setEmpresas] = useState([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [formMode, setFormMode] = useState(null);
  const [formData, setFormData] = useState(SETUP_DEFAULT);
  const [empresaEdit, setEmpresaEdit] = useState(null);
  const [editFormData, setEditFormData] = useState({nome_empresa:"",area_atuacao:"",logo_url:""});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [pendingAction, setPendingAction] = useState(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [workerStatus, setWorkerStatus] = useState(null);
  const [loadingWorkerStatus, setLoadingWorkerStatus] = useState(true);
  const [lastUpdate, setLastUpdate] = useState(null);

  const fetchEmpresas = useCallback(async () => {
    setLoading(true); setListError("");
    try { const res = await api.get("/empresas/"); setEmpresas(Array.isArray(res.data) ? res.data : []); }
    catch { setListError("Não foi possível carregar as empresas. Tente novamente."); }
    finally { setLoading(false); }
  }, []);
  const fetchWorkerStatus = useCallback(async ({silent=false}={}) => {
    if (!silent) setLoadingWorkerStatus(true);
    try { const res = await api.get("/admin/workers/status"); setWorkerStatus(res.data || null); setLastUpdate(new Date()); }
    catch { setWorkerStatus(null); }
    finally { setLoadingWorkerStatus(false); }
  }, []);
  useEffect(() => {
    fetchEmpresas(); fetchWorkerStatus();
    const interval = setInterval(() => fetchWorkerStatus({silent:true}), 30000);
    return () => clearInterval(interval);
  }, [fetchEmpresas, fetchWorkerStatus]);

  const filters = {query:searchParams.get("q") || "", area:searchParams.get("area") || "", status:searchParams.get("status") || "", sort:searchParams.get("sort") || "name"};
  const visible = filterCompanies(empresas, filters);
  const companyLinkState = { companiesReturn: `/admin${searchParams.size ? `?${searchParams}` : ""}` };
  const updateFilter = (key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key,value); else next.delete(key);
    setSearchParams(next, {replace:true});
  };
  const clearFilters = () => { const next=new URLSearchParams(searchParams); ["q","area","status","sort"].forEach(key=>next.delete(key)); setSearchParams(next,{replace:true}); };
  const openForm = (company=null) => {
    setError(""); setFeedback("");
    setEmpresaEdit(company);
    if (company) setEditFormData({nome_empresa:company.nome_empresa || "",area_atuacao:company.area_atuacao || "",logo_url:company.logo_url || ""});
    setFormMode(company ? "edit" : "create");
  };
  const closeForm = () => { setFormMode(null); setError(""); setFormData(SETUP_DEFAULT); };
  const handleChange = e => { const {name,value}=e.target; if (formMode==="edit") setEditFormData(prev=>({...prev,[name]:value})); else setFormData(prev=>({...prev,[name]:value})); };
  const handleLogoUpload = e => {
    const file=e.target.files[0]; if (!file) return;
    const reader = new FileReader();
    reader.onloadend=()=> { if (formMode==="edit") setEditFormData(prev=>({...prev,logo_url:reader.result})); else setFormData(prev=>({...prev,logo_url:reader.result})); };
    reader.readAsDataURL(file);
  };
  const handleSubmit = async e => {
    e.preventDefault(); setError(""); setSubmitting(true);
    try {
      if (formMode==="edit") await api.put(`/empresas/${empresaEdit.id}`,editFormData);
      else await api.post("/empresas/setup",formData);
      setFeedback(formMode==="edit" ? "Dados da empresa atualizados." : "Empresa cadastrada com sucesso.");
      closeForm(); await fetchEmpresas();
    } catch { setError("Não foi possível salvar a empresa. Verifique os campos e tente novamente."); }
    finally { setSubmitting(false); }
  };
  const prepareAction = (type, company) => { setActionError(""); setPendingAction({type,company}); };
  const handleDelete = async company => {
    await api.delete(`/empresas/${company.id}`);
    setPendingAction(null); setFeedback("Empresa excluída."); await fetchEmpresas();
  };
  const handleImpersonate = async emp => {
    const res = await api.post(`/auth/impersonate/${emp.id}`);
    const data = res.data;
    const currentUser = localStorage.getItem("user");
    const currentToken = localStorage.getItem("token");
    clearImpersonation();
    if (currentUser) localStorage.setItem("original_user", currentUser);
    if (currentToken && currentToken !== "null" && currentToken !== "undefined") localStorage.setItem("original_token", currentToken);
    localStorage.setItem("impersonating", "true");
    localStorage.setItem("impersonating_empresa", emp.nome_empresa);
    localStorage.setItem("impersonated_empresa_id", emp.id);
    if (data?.usuario?.id) localStorage.setItem("impersonated_user_id", data.usuario.id);
    window.location.href = "/painel";
  };
  const confirmAction = async () => {
    if (!pendingAction || actionBusy) return;
    setActionBusy(true); setActionError("");
    try { if (pendingAction.type==="delete") await handleDelete(pendingAction.company); else await handleImpersonate(pendingAction.company); }
    catch { setActionError("Não foi possível concluir a ação. Tente novamente."); }
    finally { setActionBusy(false); }
  };
  const lastRun=workerStatus?.ultima_execucao ? new Date(workerStatus.ultima_execucao) : null;
  const knownRun=lastRun && Number.isFinite(lastRun.getTime());
  const workerActive=knownRun && Date.now()-lastRun.getTime()<3*60*1000;
  const metric = value => value === null || value === undefined || !Number.isFinite(Number(value)) ? "Não informado" : Number(value);

  return <div className="fm-companies space-y-6">
    <PageHeader eyebrow="Super Admin" title={formMode ? formMode==="edit" ? "Editar empresa" : "Nova empresa" : "Clientes / Empresas"} icon={Building} description={formMode ? "Organize os dados e acessos da empresa." : "Encontre uma empresa e gerencie seus usuários, agente, canais e configurações."} actions={formMode ? <ActionButton type="button" variant="secondary" icon={ArrowLeft} onClick={closeForm} disabled={submitting}>Voltar à lista</ActionButton> : <ActionButton type="button" icon={Plus} onClick={()=>openForm()}>Novo cliente</ActionButton>} />
    {feedback && <div role="status"><InfoBanner tone="success">{feedback}</InfoBanner></div>}
    {formMode ? <CompanyForm editing={formMode==="edit"} value={formMode==="edit" ? editFormData : formData} onChange={handleChange} onLogo={handleLogoUpload} busy={submitting} error={error} onSubmit={handleSubmit} onCancel={closeForm} /> : <>
      <SectionCard title="Carteira de empresas" description="Busque pelo nome, área ou identificador. Filtros são aplicados à lista cadastrada." actions={<ActionButton type="button" variant="secondary" icon={RefreshCw} onClick={fetchEmpresas} disabled={loading}>Atualizar lista</ActionButton>}>
        <div className="fm-company-filters">
          <label className="fm-company-field">Buscar empresas<Input type="search" value={filters.query} onChange={e=>updateFilter("q",e.target.value)} placeholder="Nome, área ou ID" /></label>
          <label className="fm-company-field">Área de atuação<Select value={filters.area} onChange={e=>updateFilter("area",e.target.value)}><option value="">Todas as áreas</option>{companyAreas(empresas).map(area=><option key={area}>{area}</option>)}</Select></label>
          <label className="fm-company-field">Status cadastral<Select value={filters.status} onChange={e=>updateFilter("status",e.target.value)}><option value="">Todos os status</option><option value="active">Ativa</option><option value="inactive">Inativa</option><option value="unknown">Não informado</option></Select></label>
          <label className="fm-company-field">Ordenar por<Select value={filters.sort} onChange={e=>updateFilter("sort",e.target.value)}><option value="name">Nome</option><option value="area">Área de atuação</option></Select></label>
        </div>
        <div className="fm-company-results"><p role="status">{loading ? "Carregando empresas..." : listError ? "Lista indisponível" : `${visible.length} de ${empresas.length} empresas`}</p>{(filters.query || filters.area || filters.status || filters.sort!=="name") && <ActionButton type="button" variant="ghost" onClick={clearFilters}>Limpar filtros</ActionButton>}</div>
        {loading ? <LoadingState label="Carregando empresas..." /> : listError ? <ErrorState title="Lista de empresas indisponível" description={listError} action={<ActionButton type="button" onClick={fetchEmpresas}>Tentar novamente</ActionButton>} /> : visible.length===0 ? <EmptyState title={empresas.length ? "Nenhuma empresa encontrada" : "Nenhum cliente cadastrado"} description={empresas.length ? "Revise a busca ou limpe os filtros para visualizar sua carteira." : "Cadastre a primeira empresa para configurar seus acessos e atendimento."} action={<ActionButton type="button" onClick={empresas.length ? clearFilters : ()=>openForm()}>{empresas.length ? "Limpar filtros" : "Novo cliente"}</ActionButton>} /> : <Table label="Empresas cadastradas" className="fm-company-table">
          <thead><tr><th scope="col">Empresa / área</th><th scope="col">Status cadastral</th><th scope="col">Gestão</th></tr></thead>
          <tbody>{visible.map(emp=>{ const state=companyStatus(emp); return <tr key={emp.id}>
            <td><div className="fm-company-identity">{emp.logo_url ? <img src={emp.logo_url} alt={`Logo de ${emp.nome_empresa}`} /> : <Building size={22} aria-hidden="true" />}<div><Link className="fm-company-name" to={`/admin/empresas/${emp.id}`} state={{companiesReturn:`/admin${searchParams.size ? `?${searchParams}` : ""}`}}>{emp.nome_empresa}</Link><p>{emp.area_atuacao || "Área não informada"}</p><span className="fm-company-id">{emp.id}</span></div></div></td>
            <td><StatusBadge tone={state.tone}>{state.label}</StatusBadge></td>
            <td><div className="fm-company-actions"><ActionButton type="button" variant="secondary" icon={ArrowRight} onClick={()=>navigate(`/admin/empresas/${emp.id}`,{state:{companiesReturn:`/admin${searchParams.size ? `?${searchParams}` : ""}`}})}>Gerenciar</ActionButton><details className="fm-company-menu"><summary aria-label={`Ações de ${emp.nome_empresa}`}>Mais ações</summary><div className="fm-company-menu-items">
              <Link state={companyLinkState} to={`/admin/empresas/${emp.id}?tab=usuarios`}><Users size={16} aria-hidden="true" />Usuários e acessos</Link>
              <Link state={companyLinkState} to={`/admin/empresas/${emp.id}?tab=agente`}><Brain size={16} aria-hidden="true" />Configuração do agente</Link>
              <Link state={companyLinkState} to={`/admin/empresas/${emp.id}?tab=conhecimento`}><FileText size={16} aria-hidden="true" />Base de conhecimento</Link>
              <Link state={companyLinkState} to={`/admin/empresas/${emp.id}?tab=canais`}><KeyRound size={16} aria-hidden="true" />Canais e credenciais</Link>
              <Link to={`/admin/configuracoes/integracoes/jev?empresa=${encodeURIComponent(emp.id)}`}><Settings size={16} aria-hidden="true" />Integração JEV</Link>
              <ActionButton type="button" variant="ghost" icon={Pencil} onClick={()=>openForm(emp)}>Editar cliente</ActionButton>
              <ActionButton type="button" variant="ghost" icon={UserCheck} onClick={()=>prepareAction("impersonate",emp)}>Acessar painel da empresa</ActionButton>
              <ActionButton type="button" variant="danger" icon={Trash} onClick={()=>prepareAction("delete",emp)}>Excluir cliente</ActionButton>
            </div></details></div></td>
          </tr>; })}</tbody>
        </Table>}
      </SectionCard>
      <details className="fm-companies-health"><summary><Activity size={16} aria-hidden="true" />Worker de follow-up <StatusBadge tone={!knownRun ? "default" : workerActive ? "active" : "warning"}>{loadingWorkerStatus ? "Consultando" : !workerStatus ? "Status indisponível" : !knownRun ? "Sem execução informada" : workerActive ? "Execução recente" : "Sem execução recente"}</StatusBadge></summary><div className="fm-company-worker"><p className="fm-helper">Informações disponíveis do worker. Não representam a saúde de todas as integrações.</p><p>Leads processados hoje: <strong>{metric(workerStatus?.leads_processados_hoje)}</strong> · Erros recentes: <strong>{metric(workerStatus?.erros_recentes)}</strong> · Lock: {workerStatus?.status_lock || "Não informado"}</p><p className="fm-helper">{lastUpdate ? `Consultado às ${lastUpdate.toLocaleTimeString("pt-BR")}` : "Consulta ainda não concluída"}</p><ActionButton type="button" variant="secondary" onClick={()=>fetchWorkerStatus()} disabled={loadingWorkerStatus}>Atualizar status</ActionButton></div></details>
    </>}
    {pendingAction && <AdminDialog title={pendingAction.type==="delete" ? "Excluir empresa" : "Acessar painel da empresa"} busy={actionBusy} onClose={()=>setPendingAction(null)} footer={<><ActionButton type="button" variant="secondary" onClick={()=>setPendingAction(null)} disabled={actionBusy}>Cancelar</ActionButton><ActionButton type="button" variant={pendingAction.type==="delete" ? "danger" : "primary"} onClick={confirmAction} disabled={actionBusy}>{actionBusy ? "Aguarde..." : pendingAction.type==="delete" ? "Confirmar exclusão" : "Acessar painel"}</ActionButton></>}>
      <InfoBanner tone="warning">{pendingAction.type==="delete" ? <>Excluir <strong>{pendingAction.company.nome_empresa}</strong> apagará todos os dados deste cliente. Esta ação não pode ser desfeita.</> : <>Você acessará o painel de <strong>{pendingAction.company.nome_empresa}</strong> como Super Admin. As ações realizadas pertencem a esta empresa. Use “Sair e Voltar” no painel para retornar à administração.</>}</InfoBanner>
      {actionError && <p role="alert" className="fm-helper">{actionError}</p>}
    </AdminDialog>}
  </div>;
}
