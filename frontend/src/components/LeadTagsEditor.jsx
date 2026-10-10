import { useMemo, useState } from "react";
import { Tag, X } from "lucide-react";
import { normalizeLeadTags, normalizeLeadTagsForApi, normalizeTagValue } from "../utils/leadTags";

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
  const byName = new Map();
  const byId = new Map();
  (tagDefinitions || []).forEach((definition) => {
    const nome = String(definition?.nome || definition || "").trim();
    const id = String(definition?.id || "").trim();
    if (nome) byName.set(nome.toLowerCase(), definition);
    if (id) byId.set(id, definition);
  });
  return { byName, byId };
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

  const safeTags = useMemo(() => normalizeLeadTags(tags), [tags]);
  const normalizedTags = useMemo(() => normalizeLeadTagsForApi(safeTags), [safeTags]);
  const tagDefinitionsMap = useMemo(() => getTagDefinitionMap(tagDefinitions), [tagDefinitions]);
  const selectedTagLabelsMap = useMemo(() => {
    const map = new Map();
    safeTags.forEach((tag) => {
      if (!tag || typeof tag !== "object") return;
      const key = normalizeTagValue(tag);
      const label = String(tag.nome || tag.label || tag.id || "").trim();
      if (!key || !label) return;
      map.set(key, label);
    });
    return map;
  }, [safeTags]);
  const availableTagNames = useMemo(
    () =>
      (tagDefinitions || [])
        .map((definition) => ({
          id: String(definition?.id || "").trim(),
          nome: String(definition?.nome || "").trim(),
        }))
        .filter((definition) => Boolean(definition.id || definition.nome)),
    [tagDefinitions]
  );

  const submitTags = async (nextTags) => {
    if (!onChange) return;
    try {
      setSaving(true);
      setError("");
      await onChange(normalizeLeadTagsForApi(nextTags));
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
          normalizedTags.map((tag) => {
            const tagDefinition =
              tagDefinitionsMap.byId.get(tag) ||
              tagDefinitionsMap.byName.get(String(tag).toLowerCase());
            const label =
              selectedTagLabelsMap.get(tag) ||
              String(tagDefinition?.nome || tagDefinition?.id || tag || "").trim();
            return (
              <span
                key={tag}
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${getTagColorClass(label)}`}
                style={getTagInlineStyle(tagDefinition)}
              >
                <Tag size={12} />
                <span>{label || tag}</span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleRemoveTag(tag);
                  }}
                  className="rounded-full p-0.5 transition-colors hover:bg-white/60"
                  disabled={saving}
                  aria-label={`Remover tag ${label || tag}`}
                >
                  <X size={12} />
                </button>
              </span>
            );
          })
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
            {availableTagNames.map((tagOption) => {
              const tagKey = tagOption.id || tagOption.nome;
              const tagLabel = tagOption.nome || tagOption.id;
              const checked = normalizedTags.some((tag) => tag.toLowerCase() === String(tagKey).toLowerCase());
              return (
                <label
                  key={tagKey}
                  className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 text-xs font-medium ${
                    checked ? "border-blue-300 bg-blue-50 text-blue-700" : "border-gray-200 bg-white text-gray-600"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={saving}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => handleToggleOfficialTag(tagKey)}
                    className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span>{tagLabel}</span>
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
