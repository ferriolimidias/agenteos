type Option = { label: string; value: string };

export function ChoiceList({
  options,
  value,
  onChange,
  autoAdvance,
}: {
  options: Option[];
  value: string;
  onChange: (v: string) => void;
  autoAdvance?: () => void;
}) {
  return (
    <div className="grid gap-3">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          data-selected={value === opt.value}
          className="choice-card"
          onClick={() => {
            onChange(opt.value);
            if (autoAdvance) setTimeout(autoAdvance, 180);
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function MultiChoiceList({
  options,
  value,
  onChange,
}: {
  options: Option[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const toggle = (v: string) => {
    onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  };
  return (
    <div className="grid gap-3">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          data-selected={value.includes(opt.value)}
          className="choice-card"
          onClick={() => toggle(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}