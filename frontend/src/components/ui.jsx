import { forwardRef, useId } from "react";
import { AlertCircle, CheckCircle2, Loader2, LockKeyhole } from "lucide-react";

const tones = new Set(["default", "active", "success", "warning", "danger", "info", "dark"]);
const toneClass = (tone) => `fm-tone-${tones.has(tone) ? tone : "default"}`;

export function PageHeader({ title, description, icon: Icon, actions, eyebrow, className = "" }) {
  return (
    <div className={`fm-page-header ${className}`}>
      <div className="fm-page-heading">
        {eyebrow ? <p className="fm-eyebrow">{eyebrow}</p> : null}
        <h1 className="fm-page-title">
          {Icon ? <span className="fm-icon-box"><Icon size={21} aria-hidden="true" /></span> : null}
          <span>{title}</span>
        </h1>
        {description ? <p className="fm-page-description">{description}</p> : null}
      </div>
      {actions ? <div className="fm-actions">{actions}</div> : null}
    </div>
  );
}

export function SectionCard({ title, description, icon: Icon, actions, children, className = "" }) {
  return (
    <section className={`fm-section-card ${className}`}>
      {(title || actions) && (
        <div className="fm-section-heading">
          <div className="min-w-0">
            {title ? <h2 className="fm-section-title">{Icon ? <Icon size={16} aria-hidden="true" /> : null}{title}</h2> : null}
            {description ? <p className="fm-section-description">{description}</p> : null}
          </div>
          {actions ? <div className="fm-actions">{actions}</div> : null}
        </div>
      )}
      <div className="fm-section-body">{children}</div>
    </section>
  );
}

export function StatusBadge({ children, tone = "default", icon: Icon, className = "" }) {
  return <span className={`fm-badge ${toneClass(tone)} ${className}`}>{Icon ? <Icon size={12} aria-hidden="true" /> : null}{children}</span>;
}

export function ActionButton({ children, variant = "primary", icon: Icon, className = "", ...props }) {
  const safeVariant = ["primary", "secondary", "danger", "ghost"].includes(variant) ? variant : "primary";
  return <button className={`fm-button fm-button-${safeVariant} ${className}`} {...props}>{Icon ? <Icon size={16} aria-hidden="true" /> : null}{children}</button>;
}

export function FeatureCard({ title, description, icon: Icon, badge, children, tone = "info", className = "" }) {
  return (
    <div className={`fm-feature-card ${className}`}>
      <div className="fm-feature-heading">
        <div className="min-w-0"><h3 className="fm-feature-title">{title}</h3>{description ? <p className="fm-helper">{description}</p> : null}</div>
        {Icon ? <span className={`fm-icon-box ${toneClass(tone)}`}><Icon size={18} aria-hidden="true" /></span> : null}
      </div>
      {badge ? <div className="mt-4">{badge}</div> : null}
      {children ? <div className="mt-4">{children}</div> : null}
    </div>
  );
}

export function ChecklistCard({ title, items = [], icon: Icon = CheckCircle2 }) {
  return <SectionCard title={title} icon={Icon}><div className="space-y-2">{items.map((item) => <div key={item} className="flex items-start gap-2 fm-helper"><CheckCircle2 size={15} className="mt-1 shrink-0" color="var(--fm-status-success)" aria-hidden="true" /><span>{item}</span></div>)}</div></SectionCard>;
}

export function RestrictedNotice({ title = "Configuração gerenciada pelo Super Admin", description = "Esta área envolve configurações técnicas ou credenciais sensíveis.", children }) {
  return <div className="fm-banner fm-tone-warning"><LockKeyhole size={22} aria-hidden="true" /><div><h2 className="font-semibold">{title}</h2><p className="mt-1">{description}</p>{children ? <div className="mt-4">{children}</div> : null}</div></div>;
}

export function MetricCard({ label, value, helper, icon: Icon, tone = "info", className = "" }) {
  return <div className={`fm-metric-card ${className}`}><div className="fm-metric-heading"><p className="fm-metric-label">{label}</p>{Icon ? <span className={`fm-icon-box ${toneClass(tone)}`}><Icon size={17} aria-hidden="true" /></span> : null}</div><div className="fm-metric-value">{value}</div>{helper ? <p className="fm-helper">{helper}</p> : null}</div>;
}

export function EmptyState({ title, description, icon: Icon = AlertCircle, action, className = "" }) {
  return <div className={`fm-state ${className}`}>{Icon ? <span className="fm-icon-box"><Icon size={22} aria-hidden="true" /></span> : null}<h3>{title}</h3>{description ? <p>{description}</p> : null}{action ? <div>{action}</div> : null}</div>;
}

export function LoadingState({ label = "Carregando...", className = "" }) {
  return <div className={`fm-state fm-loading ${className}`} role="status" aria-live="polite"><Loader2 size={16} className="animate-spin" aria-hidden="true" />{label}</div>;
}

export function InfoBanner({ children, tone = "info", icon: Icon = AlertCircle, className = "" }) {
  return <div className={`fm-banner ${toneClass(tone)} ${className}`}>{Icon ? <Icon size={16} aria-hidden="true" /> : null}<div>{children}</div></div>;
}

export function ErrorState({ title = "Não foi possível carregar", description, action }) {
  return <div role="alert"><EmptyState title={title} description={description} action={action} className="fm-error" /></div>;
}

export const Input = forwardRef(function Input({ className = "", ...props }, ref) {
  return <input ref={ref} className={`fm-field ${className}`} {...props} />;
});

export const Select = forwardRef(function Select({ className = "", children, ...props }, ref) {
  return <select ref={ref} className={`fm-field ${className}`} {...props}>{children}</select>;
});

export function Table({ children, label = "Tabela de dados", className = "", ...props }) {
  return <div className="fm-table-scroll" role="region" aria-label={label} tabIndex={0}><table className={`fm-table ${className}`} {...props}>{children}</table></div>;
}

export function Tabs({ items = [], value, onChange, label = "Seções", className = "" }) {
  const groupId = useId();
  const handleKeyDown = (event) => {
    const enabled = items.filter((item) => !item.disabled);
    const selected = enabled.findIndex((item) => item.id === value);
    let next;
    if (event.key === "ArrowRight") next = enabled[(selected + 1) % enabled.length];
    if (event.key === "ArrowLeft") next = enabled[(selected - 1 + enabled.length) % enabled.length];
    if (event.key === "Home") next = enabled[0];
    if (event.key === "End") next = enabled.at(-1);
    if (!next) return;
    event.preventDefault();
    onChange(next.id);
    document.getElementById(`${groupId}-${next.id}`)?.focus();
  };
  const firstEnabled = items.find((item) => !item.disabled)?.id;
  const activeId = items.some((item) => item.id === value && !item.disabled) ? value : firstEnabled;
  return <div className={`fm-tabs ${className}`} role="tablist" aria-label={label} onKeyDown={handleKeyDown}>{items.map((item) => <button type="button" role="tab" id={`${groupId}-${item.id}`} key={item.id} aria-selected={item.id === activeId} aria-controls={item.panelId} disabled={item.disabled} tabIndex={item.id === activeId ? 0 : -1} className="fm-tab" onClick={() => onChange(item.id)}>{item.label}</button>)}</div>;
}
