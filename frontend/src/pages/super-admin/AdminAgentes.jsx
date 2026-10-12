import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BrainCircuit } from "lucide-react";
import api from "../../services/api";
import { ActionButton, EmptyState, ErrorState, Input, LoadingState, PageHeader, SectionCard } from "../../components/ui";

export default function AdminAgentes() {
  const [companies, setCompanies] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      setError(false);
      try {
        const response = await api.get("/empresas/", { signal: controller.signal });
        if (!controller.signal.aborted) setCompanies(Array.isArray(response.data) ? response.data : []);
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    load();
    return () => controller.abort();
  }, [attempt]);

  const visible = useMemo(() => {
    const normalized = search.trim().toLocaleLowerCase("pt-BR");
    return companies.filter(company => `${company.nome_empresa || ""} ${company.id}`.toLocaleLowerCase("pt-BR").includes(normalized));
  }, [companies, search]);

  return <div className="fm-admin-page">
    <PageHeader title="Agentes por empresa" eyebrow="Super Admin" icon={BrainCircuit} description="A arquitetura do agente é configurada dentro de cada empresa. Escolha uma empresa para acessar seu agente, ferramentas e configurações existentes." actions={<Link className="fm-button fm-button-secondary" to="/admin">Gerenciar empresas</Link>} />
    <SectionCard title="Selecionar empresa" description="Usuários, credenciais e integrações também ficam nos detalhes da empresa.">
      <div className="fm-admin-field">
        <label htmlFor="admin-agent-search">Buscar empresa por nome ou ID</label>
        <Input id="admin-agent-search" type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Nome ou ID da empresa" disabled={loading} />
      </div>
      {loading ? <LoadingState label="Carregando empresas..." /> : error ? <ErrorState description="Não foi possível carregar as empresas. Tente novamente." action={<ActionButton onClick={() => setAttempt(previous => previous + 1)} variant="secondary">Tentar novamente</ActionButton>} /> : visible.length === 0 ? <EmptyState title={companies.length ? "Nenhuma empresa encontrada" : "Nenhuma empresa cadastrada"} description={companies.length ? "Tente outro nome ou ID." : "Cadastre uma empresa para configurar seu agente."} action={companies.length ? <ActionButton variant="secondary" onClick={() => setSearch("")}>Limpar busca</ActionButton> : <Link className="fm-button fm-button-secondary" to="/admin">Ir para empresas</Link>} /> : <>
        <p className="fm-helper" role="status">{visible.length} {visible.length === 1 ? "empresa disponível" : "empresas disponíveis"}</p>
        <ul className="fm-admin-company-picker">
          {visible.map(company => <li key={company.id}>
            <div><h2>{company.nome_empresa || "Empresa sem nome"}</h2><p className="fm-helper">ID {company.id}</p></div>
            <Link className="fm-button fm-button-secondary" to={`/admin/empresas/${company.id}?tab=agente`} aria-label={`Configurar agente de ${company.nome_empresa || `empresa ${company.id}`}`}>Configurar agente</Link>
          </li>)}
        </ul>
      </>}
    </SectionCard>
  </div>;
}
