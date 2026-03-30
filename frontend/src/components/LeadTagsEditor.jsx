import { useMemo, useState } from "react";
import { Tag, X } from "lucide-react";

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

function getTagDefinitionMap(tagDefinitions) {
  const output = new Map();
  (tagDefinitions || []).forEach((definition) => {
    const nome = String(definition?.nome || definition || "").trim();
    if (!nome) return;
    output.set(nome.toLowerCase(), definition);
  });
  return output;
}

function getTagInlineStyle(tagDefinition) {
  const cor = String(tagDefinition?.cor || "").trim();
  if (!cor) return undefined;
  return {
    backgroundColor: `${cor}18`,
    borderColor: `${cor}55`,
    color: cor,
  };
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
  placeholder = "Selecione tags oficiais",
  tagDefinitions = [],
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const normalizedTags = useMemo(() => normalizeTags(tags), [tags]);
  const tagDefinitionsMap = useMemo(() => getTagDefinitionMap(tagDefinitions), [tagDefinitions]);
  const availableTagNames = useMemo(
    () =>
      (tagDefinitions || [])
        .map((definition) => String(definition?.nome || "").trim())
        .filter(Boolean),
    [tagDefinitions]
  );

  const submitTags = async (nextTags) => {
    if (!onChange) return;
    try {
      setSaving(true);
      setError("");
      await onChange(normalizeTags(nextTags));
    } catch (err) {
      console.error("Erro ao atualizar tags:", err);
      setError("Não foi possível salvar as tags.");
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveTag = async (tagToRemove) => {
    await submitTags(
      normalizedTags.filter((tag) => tag.toLowerCase() !== String(tagToRemove).trim().toLowerCase())
    );
  };

  const handleToggleOfficialTag = async (tagName) => {
    const hasTag = normalizedTags.some((tag) => tag.toLowerCase() === String(tagName).toLowerCase());
    if (hasTag) {
      await submitTags(
        normalizedTags.filter((tag) => tag.toLowerCase() !== String(tagName).toLowerCase())
      );
      return;
    }
    await submitTags([...normalizedTags, tagName]);
  };

  return (
    <div className={`space-y-2 ${className}`}>
      <div className="flex flex-wrap items-center gap-2">
        {normalizedTags.length > 0 ? (
          normalizedTags.map((tag) => (
            <span
              key={tag}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${getTagColorClass(tag)}`}
              style={getTagInlineStyle(tagDefinitionsMap.get(tag.toLowerCase()))}
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

      {tagDefinitions.length === 0 ? (
        <p className="text-xs text-amber-600">
          Nenhuma tag oficial cadastrada. Peça ao administrador para criar tags na Gestão de Tags.
        </p>
      ) : null}

      {availableTagNames.length > 0 ? (
        <div className={`rounded-xl border border-gray-200 bg-white ${compact ? "p-2" : "p-3"}`}>
          <p className={`mb-2 text-gray-500 ${compact ? "text-[11px]" : "text-xs"}`}>{placeholder}</p>
          <div className={`grid gap-2 ${compact ? "grid-cols-1" : "grid-cols-2"}`}>
            {availableTagNames.map((tagName) => {
              const checked = normalizedTags.some((tag) => tag.toLowerCase() === tagName.toLowerCase());
              return (
                <label
                  key={tagName}
                  className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 text-xs font-medium ${
                    checked ? "border-blue-300 bg-blue-50 text-blue-700" : "border-gray-200 bg-white text-gray-600"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={saving}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => handleToggleOfficialTag(tagName)}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span>{tagName}</span>
                </label>
              );
            })}
          </div>
        </div>
      ) : null}

      {error ? <p className="text-xs text-red-500">{error}</p> : null}
    </div>
  );
}
