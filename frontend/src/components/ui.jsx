import { AlertCircle, CheckCircle2, Loader2, LockKeyhole } from "lucide-react";

const badgeTones = {
  default: "border-slate-200 bg-slate-50 text-slate-700",
  active: "border-emerald-200 bg-emerald-50 text-emerald-700",
  warning: "border-amber-200 bg-amber-50 text-amber-700",
  danger: "border-red-200 bg-red-50 text-red-700",
  info: "border-indigo-200 bg-indigo-50 text-indigo-700",
  dark: "border-slate-700 bg-slate-900 text-slate-100",
};

const buttonVariants = {
  primary: "border-indigo-600 bg-indigo-600 text-white hover:bg-indigo-700 hover:border-indigo-700",
  secondary: "border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
  danger: "border-red-200 bg-red-50 text-red-700 hover:bg-red-100",
  ghost: "border-transparent bg-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-900",
};

export function PageHeader({ title, description, icon: Icon, actions, eyebrow }) {
  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
      <div className="min-w-0">
        {eyebrow ? (
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-indigo-600">{eyebrow}</p>
        ) : null}
        <h1 className="flex items-center gap-3 text-2xl font-semibold tracking-tight text-slate-950">
          {Icon ? (
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-indigo-100 bg-indigo-50 text-indigo-700">
              <Icon size={21} />
            </span>
          ) : null}
          <span className="truncate">{title}</span>
        </h1>
        {description ? <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function SectionCard({ title, description, icon: Icon, actions, children, className = "" }) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm shadow-slate-200/50 ${className}`}>
      {(title || actions) && (
        <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            {title ? (
              <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-950">
                {Icon ? <Icon size={16} className="text-indigo-600" /> : null}
                {title}
              </h2>
            ) : null}
            {description ? <p className="mt-1 text-sm text-slate-500">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
        </div>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function StatusBadge({ children, tone = "default", icon: Icon }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${badgeTones[tone] || badgeTones.default}`}>
      {Icon ? <Icon size={12} /> : null}
      {children}
    </span>
  );
}

export function ActionButton({ children, variant = "primary", icon: Icon, className = "", ...props }) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg border px-3.5 py-2 text-sm font-semibold shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${buttonVariants[variant] || buttonVariants.primary} ${className}`}
      {...props}
    >
      {Icon ? <Icon size={16} /> : null}
      {children}
    </button>
  );
}

export function FeatureCard({ title, description, icon: Icon, badge, children, tone = "info", className = "" }) {
  const iconTone = {
    info: "border-indigo-100 bg-indigo-50 text-indigo-700",
    active: "border-emerald-100 bg-emerald-50 text-emerald-700",
    warning: "border-amber-100 bg-amber-50 text-amber-700",
    danger: "border-red-100 bg-red-50 text-red-700",
    dark: "border-slate-200 bg-slate-100 text-slate-700",
  }[tone] || "border-indigo-100 bg-indigo-50 text-indigo-700";

  return (
    <div className={`rounded-xl border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50 ${className}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-950">{title}</h3>
          {description ? <p className="mt-1 text-sm leading-6 text-slate-500">{description}</p> : null}
        </div>
        {Icon ? (
          <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border ${iconTone}`}>
            <Icon size={18} />
          </span>
        ) : null}
      </div>
      {badge ? <div className="mt-4">{badge}</div> : null}
      {children ? <div className="mt-4">{children}</div> : null}
    </div>
  );
}

export function ChecklistCard({ title, items = [], icon: Icon = CheckCircle2 }) {
  return (
    <SectionCard title={title} icon={Icon}>
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item} className="flex items-start gap-2 text-sm leading-6 text-slate-600">
            <CheckCircle2 size={15} className="mt-1 shrink-0 text-emerald-600" />
            <span>{item}</span>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

export function RestrictedNotice({
  title = "Configuração gerenciada pelo Super Admin",
  description = "Esta área envolve configurações técnicas ou credenciais sensíveis.",
  children,
}) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-amber-900 shadow-sm">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-amber-200 bg-white text-amber-700">
          <LockKeyhole size={18} />
        </span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-amber-800">{description}</p>
          {children ? <div className="mt-4">{children}</div> : null}
        </div>
      </div>
    </div>
  );
}

export function MetricCard({ label, value, helper, icon: Icon, tone = "info" }) {
  const iconTone = {
    info: "bg-indigo-50 text-indigo-700 border-indigo-100",
    active: "bg-emerald-50 text-emerald-700 border-emerald-100",
    warning: "bg-amber-50 text-amber-700 border-amber-100",
    danger: "bg-red-50 text-red-700 border-red-100",
  }[tone] || "bg-slate-50 text-slate-700 border-slate-100";

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
        {Icon ? (
          <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${iconTone}`}>
            <Icon size={17} />
          </span>
        ) : null}
      </div>
      <div className="mt-4 text-2xl font-semibold text-slate-950">{value}</div>
      {helper ? <p className="mt-1 text-sm text-slate-500">{helper}</p> : null}
    </div>
  );
}

export function EmptyState({ title, description, icon = AlertCircle, action }) {
  const StateIcon = icon;
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm">
        <StateIcon size={22} />
      </div>
      <h3 className="mt-4 text-base font-semibold text-slate-950">{title}</h3>
      {description ? <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-500">{description}</p> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function LoadingState({ label = "Carregando..." }) {
  return (
    <div className="flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-6 py-10 text-sm text-slate-500 shadow-sm">
      <Loader2 size={16} className="animate-spin text-indigo-600" />
      {label}
    </div>
  );
}

export function InfoBanner({ children, tone = "info", icon = AlertCircle }) {
  const BannerIcon = icon;
  const tones = {
    info: "border-indigo-200 bg-indigo-50 text-indigo-800",
    warning: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-red-200 bg-red-50 text-red-800",
    success: "border-emerald-200 bg-emerald-50 text-emerald-800",
  };
  return (
    <div className={`flex items-start gap-2 rounded-xl border px-4 py-3 text-sm ${tones[tone] || tones.info}`}>
      <BannerIcon size={16} className="mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  );
}
