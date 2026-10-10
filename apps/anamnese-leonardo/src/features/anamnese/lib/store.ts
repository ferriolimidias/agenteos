import { useCallback, useEffect, useMemo, useState } from "react";
import { AnamneseData, emptyAnamnese } from "./types";
import { readUrlParams } from "./urlParams";

const STORAGE_KEY_PREFIX = "leoft.anamnese.v1";

function tokenFingerprint(token: string): string {
  const raw = String(token || "").trim();
  let hash = 0x811c9dc5;
  for (let i = 0; i < raw.length; i += 1) {
    hash ^= raw.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

function storageKeyForToken(token: string): string | null {
  const cleaned = String(token || "").trim();
  if (!cleaned) return null;
  return `${STORAGE_KEY_PREFIX}.${tokenFingerprint(cleaned)}`;
}

function normalizeData(parsed: Partial<AnamneseData>, leadToken: string): AnamneseData {
  const base = emptyAnamnese();
  const personalData = { ...base.personalData, ...(parsed.personalData || {}) };
  // Remove contact fields from data persisted by older bundles. Contact
  // identity is resolved server-side from the token-linked lead.
  delete (personalData as Record<string, unknown>).whatsapp;
  delete (personalData as Record<string, unknown>).email;
  const merged: AnamneseData = {
    ...base,
    ...parsed,
    leadToken,
    personalData,
    trainingProfile: { ...base.trainingProfile, ...(parsed.trainingProfile || {}) },
    status: { ...base.status, ...(parsed.status || {}) },
    source: { ...base.source, ...(parsed.source || {}) },
  };
  // Migração: equipment/limitations agora são arrays (antes eram strings).
  const tp = merged.trainingProfile;
  if (!Array.isArray(tp.equipment)) {
    tp.equipment = typeof tp.equipment === "string" && tp.equipment ? [tp.equipment as unknown as string] : [];
  }
  if (!Array.isArray(tp.limitations)) {
    tp.limitations = typeof tp.limitations === "string" && tp.limitations ? [tp.limitations as unknown as string] : [];
  }
  return merged;
}

function loadFromStorage(leadToken: string): AnamneseData {
  const key = storageKeyForToken(leadToken);
  if (!key) return emptyAnamnese();
  if (typeof window === "undefined") return emptyAnamnese();
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return emptyAnamnese();
    const parsed = JSON.parse(raw) as Partial<AnamneseData>;
    return normalizeData(parsed, leadToken);
  } catch {
    return emptyAnamnese();
  }
}

function persist(data: AnamneseData) {
  const key = storageKeyForToken(data.leadToken);
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(data));
  } catch {
    /* ignore quota errors */
  }
}

export function useAnamneseStore() {
  const [data, setData] = useState<AnamneseData>(() => {
    const { leadToken, source } = readUrlParams();
    const loaded = loadFromStorage(leadToken);
    return {
      ...loaded,
      leadToken,
      source: {
        ...loaded.source,
        utm_source: source.utm_source || loaded.source.utm_source,
        utm_medium: source.utm_medium || loaded.source.utm_medium,
        utm_campaign: source.utm_campaign || loaded.source.utm_campaign,
        utm_content: source.utm_content || loaded.source.utm_content,
        utm_term: source.utm_term || loaded.source.utm_term,
        fbclid: source.fbclid || loaded.source.fbclid,
        fbc: source.fbc || loaded.source.fbc,
        fbp: source.fbp || loaded.source.fbp,
      },
    };
  });

  // Hidrata leadToken + source dos parâmetros da URL na primeira montagem.
  useEffect(() => {
    const { leadToken, source } = readUrlParams();
    setData(() => {
      const loaded = loadFromStorage(leadToken);
      const next: AnamneseData = {
        ...loaded,
        leadToken,
        source: {
          utm_source: source.utm_source || loaded.source.utm_source,
          utm_medium: source.utm_medium || loaded.source.utm_medium,
          utm_campaign: source.utm_campaign || loaded.source.utm_campaign,
          utm_content: source.utm_content || loaded.source.utm_content,
          utm_term: source.utm_term || loaded.source.utm_term,
          fbclid: source.fbclid || loaded.source.fbclid,
          fbc: source.fbc || loaded.source.fbc,
          fbp: source.fbp || loaded.source.fbp,
        },
      };
      persist(next);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updatePersonal = useCallback(
    (patch: Partial<AnamneseData["personalData"]>) => {
      setData((prev) => {
        const next = { ...prev, personalData: { ...prev.personalData, ...patch } };
        persist(next);
        return next;
      });
    },
    [],
  );

  const updateTraining = useCallback(
    (patch: Partial<AnamneseData["trainingProfile"]>) => {
      setData((prev) => {
        const next = { ...prev, trainingProfile: { ...prev.trainingProfile, ...patch } };
        persist(next);
        return next;
      });
    },
    [],
  );

  const updateStatus = useCallback(
    (patch: Partial<AnamneseData["status"]>) => {
      setData((prev) => {
        const next = { ...prev, status: { ...prev.status, ...patch } };
        persist(next);
        return next;
      });
    },
    [],
  );

  const reset = useCallback(() => {
    setData((prev) => {
      const fresh = { ...emptyAnamnese(), leadToken: prev.leadToken, source: prev.source };
      persist(fresh);
      return fresh;
    });
  }, []);

  return useMemo(
    () => ({ data, updatePersonal, updateTraining, updateStatus, reset }),
    [data, updatePersonal, updateTraining, updateStatus, reset],
  );
}

export type AnamneseStore = ReturnType<typeof useAnamneseStore>;
