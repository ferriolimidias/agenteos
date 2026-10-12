import { useMemo, useState } from "react";
import { Tag, X } from "lucide-react";
import { normalizeLeadTags, normalizeLeadTagsForApi, normalizeTagValue } from "../utils/leadTags";
import "../styles/lead-workspace.css";

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

  const tagChoices = (
    <div className="ux04-tags-choices">
      <p>{placeholder}</p>
      <div className="ux04-tags-grid">
        {availableTagNames.map((tagOption) => {
          const tagKey = tagOption.id || tagOption.nome;
          const tagLabel = tagOption.nome || tagOption.id;
          const checked = normalizedTags.some((tag) => tag.toLowerCase() === String(tagKey).toLowerCase());
          return (
            <label key={tagKey} className={`ux04-tags-option ${checked ? "is-selected" : ""}`}>
              <input
                type="checkbox"
                checked={checked}
                disabled={saving}
                onClick={(e) => e.stopPropagation()}
                onChange={() => handleToggleOfficialTag(tagKey)}
              />
              <span>{tagLabel}</span>
            </label>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className={`ux04-tags ${className}`} aria-busy={saving} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") e.stopPropagation(); }}>
      <div className="ux04-tags-selected">
        {normalizedTags.length > 0 ? (
          normalizedTags.map((tag) => {
            const tagDefinition =
              tagDefinitionsMap.byId.get(tag) ||
              tagDefinitionsMap.byName.get(String(tag).toLowerCase());
            const label =
              selectedTagLabelsMap.get(tag) ||
              String(tagDefinition?.nome || tagDefinition?.id || tag || "").trim();
            return (
              <span key={tag} className="ux04-tag-chip" style={getTagInlineStyle(tagDefinition)}>
                <Tag size={12} aria-hidden="true" />
                <span>{label || tag}</span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleRemoveTag(tag);
                  }}
                  disabled={saving}
                  aria-label={`Remover tag ${label || tag}`}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </span>
            );
          })
        ) : <span className="ux04-tags-empty">Sem tags</span>}
      </div>

      {tagDefinitions.length === 0 ? (
        <p className="ux04-tags-warning">Nenhuma tag oficial cadastrada. Peça ao administrador para criar tags na Gestão de Tags.</p>
      ) : null}

      {availableTagNames.length > 0 ? (
        compact ? (
          <details className="ux04-tags-disclosure">
            <summary>Tags oficiais</summary>
            {tagChoices}
          </details>
        ) : tagChoices
      ) : null}
      {saving ? <p className="ux04-tags-status" role="status">Salvando tags…</p> : null}
      {error ? <p className="ux04-tags-error" role="alert">{error}</p> : null}
    </div>
  );
}
