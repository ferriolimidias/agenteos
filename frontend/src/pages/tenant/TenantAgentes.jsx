import { useEffect, useMemo, useState } from "react";
import { Bot, CheckCircle2, ClipboardList, Headphones, Image, Sparkles } from "lucide-react";
import api from "../../services/api";
import { ActionButton, EmptyState, FeatureCard, InfoBanner, PageHeader, RestrictedNotice, SectionCard, StatusBadge } from "../../components/ui";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

const TIPOS_ALTERACAO = [
  "Ajuste no comportamento do agente",
  "Alterar regra comercial/orcamento",
  "Alterar agenda ou coleta de dados",
  "Ativar/desativar capacidade",
  "Outro ajuste operacional",
];

const URGENCIAS = ["Baixa", "Media", "Alta"];

function inferCapabilityIcon(nome) {
  const value = String(nome || "").toLowerCase();
  if (value.includes("transfer")) return Headphones;
  if (value.includes("imagem") || value.includes("vis")) return Image;
  if (value.includes("agenda")) return ClipboardList;
  return Sparkles;
}

function inferAgentName(iaConfig) {
  return String(iaConfig?.nome_agente || "").trim() || "Agente de Atendimento";
}

export default function TenantAgentes() {
  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();
  const hasUser = Boolean(user);
  const [loading, setLoading] = useState(true);
  const [agentes, setAgentes] = useState([]);
  const [iaConfig, setIaConfig] = useState({});
  const [conexoes, setConexoes] = useState([]);
  const [erroCarregamento, setErroCarregamento] = useState("");
  const [solicitacao, setSolicitacao] = useState({
    tipo: TIPOS_ALTERACAO[0],
    urgencia: URGENCIAS[0],
    descricao: "",
    exemplo: "",
  });
  const [solicitacaoRegistrada, setSolicitacaoRegistrada] = useState(false);

  useEffect(() => {
    const fetchDados = async () => {
      if (!empresaId || !hasUser) {
        setAgentes([]);
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setErroCarregamento("");
        const [resAgentes, resIaConfig, resWhatsapp] = await Promise.all([
          api.get(`/empresas/${empresaId}/agentes`).catch(() => ({ data: [] })),
          api.get(`/empresas/${empresaId}/ia-config`).catch(() => ({ data: {} })),
          api.get(`/empresas/${empresaId}/whatsapp/status`).catch(() => ({ data: {} })),
        ]);
        const data = resAgentes.data?.items || resAgentes.data || [];
        setAgentes(Array.isArray(data) ? data : []);
        setIaConfig(resIaConfig.data || {});
        const whatsappConectado = Boolean(resWhatsapp?.data?.conectado) || Boolean(resWhatsapp?.data?.online);
        setConexoes(whatsappConectado ? [{ nome_instancia: resWhatsapp?.data?.provider_label || "API Oficial da Meta", status: "ativo" }] : []);
      } catch (error) {
        console.error("Erro ao carregar visao operacional do agente:", error);
        setAgentes([]);
        setErroCarregamento("Não foi possível carregar o status do agente no momento.");
      } finally {
        setLoading(false);
      }
    };

    void fetchDados();
  }, [empresaId, hasUser]);

  const canaisConectados = useMemo(
    () =>
      conexoes
        .filter((conexao) => String(conexao.status || "").toLowerCase() !== "inativa")
        .map((conexao) => String(conexao.nome_instancia || conexao.tipo || "Canal").trim())
        .filter(Boolean),
    [conexoes],
  );

  const capacidades = useMemo(() => {
    const base = agentes.map((agente) => ({
      id: String(agente.id || agente.nome),
      nome: String(agente.nome || "Capacidade"),
      descricao: String(agente.funcao || "Capacidade operacional do atendimento."),
      online: String(agente.status || "").toLowerCase() === "online",
    }));
    if (!base.some((item) => item.nome.toLowerCase().includes("transfer"))) {
      base.push({
        id: "transferencia-humana",
        nome: "Transferência humana",
        descricao: "Encaminhamento para atendimento humano quando necessário.",
        online: true,
      });
    }
    return base;
  }, [agentes]);

  const agentActive = capacidades.some((capacidade) => capacidade.online);
  const agentName = inferAgentName(iaConfig);

  const registrarSolicitacao = (event) => {
    event.preventDefault();
    setSolicitacaoRegistrada(true);
  };

  const mailtoHref = useMemo(() => {
    const subject = encodeURIComponent(`Solicitacao de alteracao do agente - ${agentName}`);
    const body = encodeURIComponent(
      [
        `Tenant: ${empresaId || "-"}`,
        `Tipo: ${solicitacao.tipo}`,
        `Urgencia: ${solicitacao.urgencia}`,
        "",
        "Descricao:",
        solicitacao.descricao || "-",
        "",
        "Exemplo de mensagem/resposta desejada:",
        solicitacao.exemplo || "-",
      ].join("\n"),
    );
    return `mailto:suporte@agenteos.local?subject=${subject}&body=${body}`;
  }, [agentName, empresaId, solicitacao]);

  if (!user) {
    return (
      <EmptyState title="Sessão expirada" description="Faça login novamente para visualizar o agente de atendimento." />
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agente de Atendimento"
        description="Visualize a operação do agente, acompanhe capacidades ativas e solicite ajustes à equipe AgenteOS."
        icon={Bot}
        eyebrow="Operação"
      />

      <RestrictedNotice
        title="Configuração avançada protegida"
        description="Prompts, modelos, ferramentas, visão/imagem e credenciais são gerenciados pela equipe AgenteOS."
      />

      {loading ? (
        <SectionCard title="Carregando agente">
          <div className="h-24 animate-pulse rounded-lg bg-slate-100" />
        </SectionCard>
      ) : erroCarregamento ? (
        <EmptyState title="Status indisponível" description={erroCarregamento} icon={Bot} />
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            <FeatureCard
              title="Status do agente"
              description="Camada principal que responde aos clientes."
              icon={Bot}
              tone={agentActive ? "active" : "warning"}
              badge={
                <StatusBadge tone={agentActive ? "active" : "warning"} icon={agentActive ? CheckCircle2 : Bot}>
                  {agentActive ? "Ativo" : "Aguardando configuração"}
                </StatusBadge>
              }
            >
              <p className="text-lg font-semibold text-slate-950">{agentName}</p>
              <p className="mt-1 text-sm text-slate-500">
                Responde clientes, qualifica atendimento e aciona capacidades configuradas pelo Super Admin.
              </p>
            </FeatureCard>

            <FeatureCard title="Canais conectados" description="Canais disponíveis para operação." icon={Headphones}>
              <div className="space-y-2">
                {canaisConectados.length > 0 ? (
                  canaisConectados.map((canal) => (
                    <StatusBadge key={canal} tone="active">
                      {canal}
                    </StatusBadge>
                  ))
                ) : (
                  <p className="text-sm text-slate-500">Nenhum canal ativo encontrado.</p>
                )}
              </div>
            </FeatureCard>

            <FeatureCard title="Governança" description="Alterações críticas passam por revisão." icon={CheckCircle2} tone="active">
              <p className="text-sm leading-6 text-slate-600">
                Alterações críticas são revisadas pelo Super Admin para proteger qualidade, segurança e isolamento do tenant.
              </p>
            </FeatureCard>
          </div>

          <SectionCard title="Capacidades do agente" description="Visualização operacional das capacidades do atendimento.">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {capacidades.map((capacidade) => {
                const Icon = inferCapabilityIcon(capacidade.nome);
                return (
                  <div key={capacidade.id} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm shadow-slate-200/40">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold text-slate-950">{capacidade.nome}</h3>
                        <p className="mt-1 text-sm leading-5 text-slate-500">{capacidade.descricao}</p>
                      </div>
                      <Icon size={18} className="shrink-0 text-indigo-600" />
                    </div>
                    <div className="mt-3">
                      <StatusBadge tone={capacidade.online ? "active" : "warning"}>
                        {capacidade.online ? "Ativa" : "Inativa"}
                      </StatusBadge>
                    </div>
                  </div>
                );
              })}
            </div>
          </SectionCard>

          <SectionCard title="Atendimento em produção" description="A operação normal do agente segue as autorizações atuais da conversa.">
            <InfoBanner tone="success">
              Mensagens inbound válidas seguem para a IA quando ela está autorizada e a conversa não está sob atendimento humano.
            </InfoBanner>
          </SectionCard>
        </>
      )}

      <SectionCard title="Solicitar alteração" description="Registre uma solicitação para revisão pela equipe AgenteOS. Nada é enviado automaticamente para clientes.">
        {solicitacaoRegistrada ? (
          <InfoBanner tone="success">
            Solicitação preparada no painel. Para concluir, envie pelo canal de suporte combinado com a equipe AgenteOS.
          </InfoBanner>
        ) : null}
        <form onSubmit={registrarSolicitacao} className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Tipo de alteração</label>
            <select
              value={solicitacao.tipo}
              onChange={(e) => setSolicitacao((prev) => ({ ...prev, tipo: e.target.value }))}
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
            >
              {TIPOS_ALTERACAO.map((tipo) => (
                <option key={tipo} value={tipo}>{tipo}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Urgência</label>
            <select
              value={solicitacao.urgencia}
              onChange={(e) => setSolicitacao((prev) => ({ ...prev, urgencia: e.target.value }))}
              className="w-full rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
            >
              {URGENCIAS.map((urgencia) => (
                <option key={urgencia} value={urgencia}>{urgencia}</option>
              ))}
            </select>
          </div>
          <div className="lg:col-span-2">
            <label className="mb-1 block text-sm font-medium text-slate-700">Descrição</label>
            <textarea
              rows={4}
              value={solicitacao.descricao}
              onChange={(e) => setSolicitacao((prev) => ({ ...prev, descricao: e.target.value }))}
              className="w-full resize-y rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
              placeholder="Descreva o que precisa mudar no atendimento."
            />
          </div>
          <div className="lg:col-span-2">
            <label className="mb-1 block text-sm font-medium text-slate-700">Exemplo de mensagem/resposta desejada</label>
            <textarea
              rows={3}
              value={solicitacao.exemplo}
              onChange={(e) => setSolicitacao((prev) => ({ ...prev, exemplo: e.target.value }))}
              className="w-full resize-y rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
              placeholder="Opcional: cole um exemplo do que você espera."
            />
          </div>
          <div className="flex flex-wrap gap-2 lg:col-span-2">
            <ActionButton type="submit" icon={ClipboardList}>
              Preparar solicitação
            </ActionButton>
            <a
              href={mailtoHref}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
            >
              <Send size={16} />
              Abrir email de suporte
            </a>
          </div>
        </form>
      </SectionCard>
    </div>
  );
}
