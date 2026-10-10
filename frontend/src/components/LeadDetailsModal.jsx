import { useEffect, useState } from "react";
import { RefreshCw, X } from "lucide-react";

import LeadTagsEditor from "./LeadTagsEditor";
import { normalizeLeadTags } from "../utils/leadTags";

export default function LeadDetailsModal({
  lead,
  open,
  onClose,
  onSaveLead,
  onSaveTags,
  availableTags = [],
}) {
  const [formData, setFormData] = useState({
    nome_contato: "",
    telefone_contato: "",
    historico_resumo: "",
  });
  const [valorConversao, setValorConversao] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!lead) return;
    setFormData({
      nome_contato: lead.nome_contato || "",
      telefone_contato: lead.telefone_contato || lead.telefone || "",
      historico_resumo: lead.historico_resumo || "",
    });
    setValorConversao(lead.valor_conversao || 0);
    setError("");
  }, [lead]);

  if (!open || !lead) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setSaving(true);
      setError("");
      await onSaveLead?.(lead.id, {
        nome_contato: formData.nome_contato.trim(),
        telefone_contato: formData.telefone_contato.trim(),
        historico_resumo: formData.historico_resumo.trim(),
        valor_conversao: Number(valorConversao || 0),
      });
      onClose?.();
    } catch (err) {
      console.error("Erro ao salvar lead:", err);
      setError(err?.message || "Não foi possível salvar o lead.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-lg bg-white shadow-xl">
        <div className="flex shrink-0 items-start justify-between border-b border-gray-100 p-4 md:p-6">
          <div>
            <h2 className="text-xl font-bold text-gray-900">{lead.nome_contato}</h2>
            <p className="mt-1 text-sm text-gray-500">Edite os dados do lead e gerencie suas tags oficiais.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-1 flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto p-4 md:p-6">
            <div className="space-y-6">
              {error ? (
                <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {error}
                </div>
              ) : null}

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Nome</label>
                  <input
                    value={formData.nome_contato}
                    onChange={(e) => setFormData((prev) => ({ ...prev, nome_contato: e.target.value }))}
                    className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Telefone</label>
                  <input
                    value={formData.telefone_contato}
                    onChange={(e) => setFormData((prev) => ({ ...prev, telefone_contato: e.target.value }))}
                    className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>

              <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Tags do lead</p>
                <LeadTagsEditor
                  tags={normalizeLeadTags(lead?.tags)}
                  placeholder="Selecione tags oficiais"
                  onChange={(nextTags) => onSaveTags?.(lead.id, nextTags)}
                  tagDefinitions={availableTags}
                />
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Resumo do histórico</label>
                <textarea
                  rows={4}
                  value={formData.historico_resumo}
                  onChange={(e) => setFormData((prev) => ({ ...prev, historico_resumo: e.target.value }))}
                  className="w-full rounded-xl border border-gray-200 px-4 py-3 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700">Valor da Venda (R$)</label>
                <div className="relative mt-1">
                  <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-sm font-medium text-gray-500">
                    R$
                  </span>
                  <input
                    type="number"
                    step="0.01"
                    className="block w-full rounded-md border border-gray-300 py-2 pl-11 pr-3 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 sm:text-sm"
                    value={valorConversao}
                    onChange={(e) => setValorConversao(e.target.value)}
                    placeholder="R$ 0,00"
                  />
                </div>
              </div>

            </div>
          </div>
          <div className="flex shrink-0 justify-end gap-3 rounded-b-lg border-t border-gray-100 bg-gray-50 p-4 md:p-6">
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? <RefreshCw size={14} className="animate-spin" /> : null}
              {saving ? "Salvando..." : "Salvar lead"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
