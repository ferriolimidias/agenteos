import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { RefreshCw, UserRound, X } from "lucide-react";

import LeadTagsEditor from "./LeadTagsEditor";
import { normalizeLeadTags } from "../utils/leadTags";
import { crmSaveError, crmSaveErrorMessage } from "../utils/crmLeadSave";
import "../styles/lead-workspace.css";

export default function LeadDetailsModal({
  lead,
  scopeId,
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
  const fieldId = useId();
  const dialogRef = useRef(null);
  const errorRef = useRef(null);
  const onCloseRef = useRef(onClose);
  const leadId = lead?.id;
  const savingRef = useRef(false);
  const scopeRef = useRef(null);
  const formScopeRef = useRef(null);

  useLayoutEffect(() => {
    scopeRef.current = open && leadId ? { leadId, scopeId } : null;
    return () => { scopeRef.current = null; };
  }, [open, leadId, scopeId]);

  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  useEffect(() => {
    if (!lead || !open) return;
    const identity = `${scopeId}:${leadId}`;
    if (formScopeRef.current === identity) return;
    formScopeRef.current = identity;
    setFormData({
      nome_contato: lead.nome_contato || "",
      telefone_contato: lead.telefone_contato || lead.telefone || "",
      historico_resumo: lead.historico_resumo || "",
    });
    setValorConversao(lead.valor_conversao || 0);
    setError("");
  }, [lead, open, leadId, scopeId]);

  useEffect(() => {
    if (!open || !leadId) return undefined;
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = requestAnimationFrame(() => dialogRef.current?.querySelector("[data-lead-initial-focus]")?.focus());
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!savingRef.current) onCloseRef.current?.();
      }
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      const items = [...(dialog?.querySelectorAll("button:not([disabled]), input:not([disabled]), textarea:not([disabled]), summary, [tabindex='0']") || [])]
        .filter((item) => item.getClientRects().length > 0 && getComputedStyle(item).visibility !== "hidden");
      const first = items[0];
      const last = items.at(-1);
      if (!dialog?.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, [open, leadId]);

  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);

  if (!open || !lead) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (savingRef.current) return;
    const scope = scopeRef.current;
    if (!scope) return;
    savingRef.current = true;
    try {
      setSaving(true);
      setError("");
      if (!onSaveLead) throw crmSaveError('CRM_SAVE_NOT_CONFIRMED');
      if (!Number.isFinite(Number(valorConversao || 0))) throw crmSaveError('CRM_SAVE_INVALID_VALUE');
      const receipt = await onSaveLead(lead.id, {
        nome_contato: formData.nome_contato.trim(),
        telefone_contato: formData.telefone_contato.trim(),
        historico_resumo: formData.historico_resumo.trim(),
        valor_conversao: Number(valorConversao || 0),
      });
      if (scopeRef.current !== scope) return;
      if (receipt?.saved !== true || receipt.leadId !== lead.id || receipt.empresaId !== scopeId) throw crmSaveError('CRM_SAVE_NOT_CONFIRMED');
      onClose?.();
    } catch (err) {
      if (scopeRef.current === scope) setError(crmSaveErrorMessage(err));
    } finally {
      if (scopeRef.current === scope) {
        savingRef.current = false;
        setSaving(false);
      }
    }
  };

  const handleClose = () => { if (!savingRef.current) onClose?.(); };

  return (
    <div className="ux04-lead-overlay">
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={`${fieldId}-title`} aria-describedby={`${fieldId}-description`} className="ux04-lead-dialog">
        <header className="ux04-lead-header">
          <div className="ux04-lead-heading">
            <span className="fm-icon-box" aria-hidden="true"><UserRound size={20} /></span>
            <div>
              <p className="fm-eyebrow">Contato · CRM</p>
              <h2 id={`${fieldId}-title`}>{lead.nome_contato || "Detalhes do contato"}</h2>
              <p id={`${fieldId}-description`}>Dados do contato, histórico e tags oficiais.</p>
            </div>
          </div>
          <button type="button" onClick={handleClose} disabled={saving} aria-label="Fechar edição do contato" className="ux04-lead-close"><X size={20} aria-hidden="true" /></button>
        </header>

        <form onSubmit={handleSubmit} className="ux04-lead-form" aria-busy={saving}>
          <div className="ux04-lead-body">
            {error ? <div ref={errorRef} role="alert" tabIndex={-1} className="ux04-lead-error">{error}</div> : null}

            <section className="ux04-lead-section" aria-labelledby={`${fieldId}-contact`}>
              <h3 id={`${fieldId}-contact`}>Informações do contato</h3>
              <div className="ux04-lead-fields">
                <div>
                  <label htmlFor={`${fieldId}-name`}>Nome</label>
                  <input id={`${fieldId}-name`} disabled={saving} data-lead-initial-focus value={formData.nome_contato} onChange={(e) => setFormData((prev) => ({ ...prev, nome_contato: e.target.value }))} className="fm-field" autoComplete="name" />
                </div>
                <div>
                  <label htmlFor={`${fieldId}-phone`}>Telefone</label>
                  <input id={`${fieldId}-phone`} disabled={saving} type="tel" value={formData.telefone_contato} onChange={(e) => setFormData((prev) => ({ ...prev, telefone_contato: e.target.value }))} className="fm-field" autoComplete="tel" />
                </div>
              </div>
            </section>

            <section className="ux04-lead-section" aria-labelledby={`${fieldId}-tags`}>
              <h3 id={`${fieldId}-tags`}>Tags do lead</h3>
              <p className="ux04-lead-helper">Selecionar ou remover uma tag salva essa alteração imediatamente.</p>
              <LeadTagsEditor tags={normalizeLeadTags(lead?.tags)} placeholder="Selecione tags oficiais" onChange={(nextTags) => onSaveTags?.(lead.id, nextTags)} tagDefinitions={availableTags} />
            </section>

            <section className="ux04-lead-section" aria-labelledby={`${fieldId}-history`}>
              <h3 id={`${fieldId}-history`}>Histórico e valor da venda</h3>
              <div>
                <label htmlFor={`${fieldId}-summary`}>Resumo do histórico</label>
                <textarea id={`${fieldId}-summary`} disabled={saving} rows={4} value={formData.historico_resumo} onChange={(e) => setFormData((prev) => ({ ...prev, historico_resumo: e.target.value }))} className="fm-field" />
              </div>
              <div className="ux04-lead-sale">
                <label htmlFor={`${fieldId}-value`}>Valor da Venda (R$)</label>
                <input id={`${fieldId}-value`} disabled={saving} type="number" step="0.01" className="fm-field" value={valorConversao} onChange={(e) => setValorConversao(e.target.value)} placeholder="0,00" />
              </div>
            </section>
          </div>
          <footer className="ux04-lead-footer">
            <span className="ux04-lead-helper" role="status">{saving ? "Salvando dados do contato…" : "Revise os dados antes de salvar."}</span>
            <div className="fm-actions">
              <button type="button" disabled={saving} onClick={handleClose} className="fm-button fm-button-secondary">Cancelar</button>
              <button type="submit" disabled={saving} className="fm-button fm-button-primary">
                {saving ? <RefreshCw size={14} className="animate-spin" aria-hidden="true" /> : null}
                {saving ? "Salvando..." : "Salvar lead"}
              </button>
            </div>
          </footer>
        </form>
      </div>
    </div>
  );
}
