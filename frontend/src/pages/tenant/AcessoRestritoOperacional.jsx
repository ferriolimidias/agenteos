import { ArrowRight, LockKeyhole } from "lucide-react";
import { Link } from "react-router-dom";
import { ChecklistCard, PageHeader, RestrictedNotice } from "../../components/ui";

export default function AcessoRestritoOperacional({
  title = "Configuração gerenciada pelo Super Admin",
  description = "Esta área envolve configurações técnicas ou credenciais sensíveis. No painel cliente, ela fica disponível apenas como solicitação operacional.",
}) {
  return (
    <div className="space-y-6">
      <PageHeader
        title={title}
        description={description}
        icon={LockKeyhole}
        eyebrow="Painel cliente"
      />

      <RestrictedNotice
        title="Acesso restrito"
        description="Prompts, modelos, ferramentas, credenciais, integrações técnicas e regras internas ficam sob governança do Super Admin."
      >
          <Link
            to="/painel/agentes"
            className="inline-flex items-center justify-center gap-2 rounded-lg border border-amber-200 bg-white px-3.5 py-2 text-sm font-semibold text-amber-900 shadow-sm transition-colors hover:bg-amber-100"
          >
            <ArrowRight size={16} />
            Ir para Agente de Atendimento
          </Link>
      </RestrictedNotice>

      <ChecklistCard
        title="O que permanece disponível no painel cliente"
        items={[
          "Atendimento, inbox, leads, agenda, tags e campanhas operacionais.",
          "Visualização do agente e solicitação de alteração.",
          "Simulação simples sem expor prompt, modelo, token ou configuração técnica.",
        ]}
      />
    </div>
  );
}
