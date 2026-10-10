import { FileText } from "lucide-react";

const statusLabels = {
  approved: "Aprovado",
  pending: "Pendente",
  rejected: "Rejeitado",
  paused: "Pausado",
  disabled: "Desativado",
  archived: "Arquivado",
  deleted: "Excluído",
  unknown: "Estado não reconhecido",
};

export default function MetaTemplateSelector({
  templates = [],
  value = "",
  onChange,
  disabled = false,
  placeholder = "Selecione um template",
}) {
  return (
    <label className="block">
      <span className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-800">
        <FileText className="h-4 w-4" />
        Template oficial
      </span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange?.(event.target.value)}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <option value="">{placeholder}</option>
        {templates.map((template) => (
          <option key={template.id} value={template.id}>
            {template.name} · {template.language} · {statusLabels[template.normalized_status] || statusLabels.unknown}
          </option>
        ))}
      </select>
    </label>
  );
}
