import { useMemo, useState } from "react";
import { Plus, Tag, X } from "lucide-react";

const TAG_COLOR_VARIANTS = [
  "bg-blue-50 text-blue-700 border-blue-200",
  "bg-violet-50 text-violet-700 border-violet-200",
  "bg-emerald-50 text-emerald-700 border-emerald-200",
  "bg-amber-50 text-amber-700 border-amber-200",
  "bg-rose-50 text-rose-700 border-rose-200",
  "bg-cyan-50 text-cyan-700 border-cyan-200",
];

function getTagColorClass(tag) {
  const text = String(tag || "").trim().toLowerCase();
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash << 5) - hash + text.charCodeAt(i);
    hash |= 0;
  }
  return TAG_COLOR_VARIANTS[Math.abs(hash) % TAG_COLOR_VARIANTS.length];
}

function normalizeTags(tags) {
  const output = [];
  const seen = new Set();

  (tags || []).forEach((tag) => {
    const clean = String(tag || "").trim();
    if (!clean) return;
    const key = clean.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    output.push(clean);
  });

  return output;
}

export default function LeadTagsEditor({
  tags = [],
  onChange,
  compact = false,
  className = "",
  placeholder = "Digite uma tag e pressione Enter",
}) {
  const [inputValue, setInputValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const normalizedTags = useMemo(() => normalizeTags(tags), [tags]);

  const submitTags = async (nextTags) => {
    if (!onChange) return;
    try {
      setSaving(true);
      setError("");
      await onChange(normalizeTags(nextTags));
      setInputValue("");
    } catch (err) {
      console.error("Erro ao atualizar tags:", err);
      setError("Não foi possível salvar as tags.");
    } finally {
      setSaving(false);
    }
  };

  const handleAddTag = async () => {
    const nextTag = inputValue.trim();
    if (!nextTag) return;
    if (normalizedTags.some((tag) => tag.toLowerCase() === nextTag.toLowerCase())) {
      setInputValue("");
      return;
    }
    await submitTags([...normalizedTags, nextTag]);
  };

  const handleRemoveTag = async (tagToRemove) => {
    await submitTags(
      normalizedTags.filter((tag) => tag.toLowerCase() !== String(tagToRemove).trim().toLowerCase())
    );
  };

  return (
    <div className={`space-y-2 ${className}`}>
      <div className="flex flex-wrap items-center gap-2">
        {normalizedTags.length > 0 ? (
          normalizedTags.map((tag) => (
            <span
              key={tag}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${getTagColorClass(tag)}`}
            >
              <Tag size={12} />
              <span>{tag}</span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRemoveTag(tag);
                }}
                className="rounded-full p-0.5 transition-colors hover:bg-white/60"
                disabled={saving}
                aria-label={`Remover tag ${tag}`}
              >
                <X size={12} />
              </button>
            </span>
          ))
        ) : (
          <span className="text-xs text-gray-400">Sem tags</span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Plus
            size={14}
            className={`pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 ${
              compact ? "hidden" : ""
            }`}
          />
          <input
            value={inputValue}
            disabled={saving}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                e.stopPropagation();
                handleAddTag();
              }
            }}
            placeholder={placeholder}
            className={`w-full rounded-xl border border-gray-200 bg-white text-gray-700 outline-none transition-all focus:border-blue-400 focus:ring-2 focus:ring-blue-500 ${
              compact
                ? "px-3 py-2 text-xs"
                : "px-3 py-2.5 text-sm"
            } ${compact ? "" : "pl-9"}`}
          />
        </div>
        {!compact && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              handleAddTag();
            }}
            disabled={saving || !inputValue.trim()}
            className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? "Salvando..." : "Adicionar"}
          </button>
        )}
      </div>

      {error ? <p className="text-xs text-red-500">{error}</p> : null}
    </div>
  );
}
