import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { ActionButton } from "../ui";
import "./admin-dialog.css";

// Short confirmations and forms share keyboard/focus handling; no second React root.
export default function AdminDialog({ open = true, title, onClose, busy = false, children, footer, className = "" }) {
  const id = useId();
  const dialogRef = useRef(null);
  const busyRef = useRef(busy);
  const closeRef = useRef(onClose);
  useEffect(() => { busyRef.current = busy; closeRef.current = onClose; }, [busy, onClose]);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    const root = document.getElementById("root");
    const wasInert = root?.inert;
    if (root) root.inert = true;
    const dialog = dialogRef.current;
    const focusables = () => [...dialog.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]')].filter(node => !node.matches(":disabled") && node.getClientRects().length);
    const frame = requestAnimationFrame(() => (dialog.querySelector('[data-autofocus]') || focusables()[0] || dialog)?.focus());
    const keydown = event => {
      if (event.key === "Escape") { event.preventDefault(); if (!busyRef.current) closeRef.current?.(); }
      if (event.key !== "Tab") return;
      const items = focusables(), first = items[0], last = items.at(-1);
      if (!first) { event.preventDefault(); dialog.focus(); return; }
      if (!dialog.contains(document.activeElement) || (event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
        event.preventDefault(); (event.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", keydown);
      if (root) root.inert = wasInert;
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);
  if (!open) return null;
  return createPortal(<div className="fm-admin-dialog-backdrop"><section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={id} aria-busy={busy} tabIndex={-1} className={`fm-admin-dialog ${className}`}>
    <header><h2 id={id}>{title}</h2><ActionButton type="button" variant="ghost" icon={X} aria-label={`Fechar ${title}`} disabled={busy} onClick={onClose} /></header>
    <div className="fm-admin-dialog-body">{children}</div>
    {footer && <footer>{footer}</footer>}
  </section></div>, document.body);
}
