import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search, ShieldCheck } from "lucide-react";
import api from "../../services/api";
import { ActionButton, EmptyState, ErrorState, Input, LoadingState, PageHeader, SectionCard, StatusBadge } from "../../components/ui";

const formatTime = (value) => {
  if (!value) return "Data não informada";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString("pt-BR") : "Data não informada";
};

export default function SegurancaAuditoria() {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const response = await api.get("/admin/security/overview");
      setData(response.data || null);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const users = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("pt-BR");
    return (data?.users || []).filter((user) => `${user.name} ${user.email} ${user.company_name} ${user.role}`.toLocaleLowerCase("pt-BR").includes(query));
  }, [data, search]);

  return <div className="fm-admin-page">
    <PageHeader eyebrow="Plataforma Ferrioli" title="Segurança e auditoria" icon={ShieldCheck} description="Consulte acessos administrativos por empresa e registros de governança de prompts já persistidos." actions={<ActionButton type="button" variant="secondary" onClick={load} disabled={loading}>Atualizar</ActionButton>} />
    {loading ? <LoadingState label="Consultando acessos e registros..." /> : error ? <ErrorState title="Informações de segurança indisponíveis" description="Não foi possível consultar os registros de acesso agora." action={<ActionButton type="button" onClick={load}>Tentar novamente</ActionButton>} /> : data ? <>
      <SectionCard title="Acessos por empresa" description="Apenas usuários vinculados a empresas; contas Super Admin não aparecem nesta listagem.">
        <label className="fm-admin-field"><span>Buscar usuário ou empresa</span><div className="relative"><Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><Input className="pl-9" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Nome, e-mail, empresa ou perfil" /></div></label>
        {users.length ? <div className="overflow-x-auto" role="region" aria-label="Acessos das empresas" tabIndex={0}><table className="min-w-full text-left text-sm"><thead><tr><th className="p-3">Empresa</th><th className="p-3">Usuário</th><th className="p-3">Perfil</th><th className="p-3">Estado</th><th className="p-3">Ação</th></tr></thead><tbody>{users.map((user) => <tr key={user.id} className="border-t border-slate-200"><td className="p-3">{user.company_name}</td><td className="p-3"><span className="block font-medium">{user.name}</span><span className="text-slate-500">{user.email}</span></td><td className="p-3">{user.role}</td><td className="p-3"><StatusBadge tone={user.active ? "success" : "warning"}>{user.active ? "Ativo" : "Inativo"}</StatusBadge></td><td className="p-3"><Link className="fm-admin-link" to={`/admin/empresas/${user.company_id}?area=usuarios`}>Gerenciar acesso</Link></td></tr>)}</tbody></table></div> : <EmptyState title="Nenhum acesso encontrado" description={data.users?.length ? "A busca não encontrou usuários correspondentes." : "Não há usuários de empresa nesta listagem."} action={data.users?.length ? <ActionButton type="button" variant="secondary" onClick={() => setSearch("")}>Limpar busca</ActionButton> : null} />}
      </SectionCard>
      <SectionCard title="Auditoria de prompts" description={data.prompt_audit?.scope || "Registros de aprovação e governança existentes."}>
        {data.prompt_audit?.items?.length ? <div className="overflow-x-auto" role="region" aria-label="Auditoria de prompts" tabIndex={0}><table className="min-w-full text-left text-sm"><thead><tr><th className="p-3">Data</th><th className="p-3">Empresa</th><th className="p-3">Ação</th><th className="p-3">Objeto</th><th className="p-3">Operador</th></tr></thead><tbody>{data.prompt_audit.items.map((event) => <tr key={`${event.company_id}-${event.object_id}-${event.created_at}`} className="border-t border-slate-200"><td className="p-3">{formatTime(event.created_at)}</td><td className="p-3">{event.company_name}</td><td className="p-3">{event.action}</td><td className="p-3">{event.object_type}</td><td className="p-3">{event.actor_id || "Não informado"}</td></tr>)}</tbody></table></div> : <EmptyState title="Nenhum registro de prompt retornado" description="A consulta cobriu os registros de auditoria A11 disponíveis; isso não significa que outras operações administrativas tenham trilha consolidada." />}
        {data.general_admin_audit_available === false ? <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">A auditoria geral de alterações de credenciais e configurações ainda não está consolidada em um registro consultável. Esta tela não a representa como disponível.</p> : null}
      </SectionCard>
    </> : null}
  </div>;
}
