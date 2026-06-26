/** Live model catalog from OpenRouter-backed API */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { ApiModel } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import {
  colorForModelId,
  displayNameFromSlug,
  modelIdToSlug,
  vendorFromSlug,
} from "@/lib/modelIds";

export const FLAGSHIP_MODEL_IDS = ["gpt-4.1", "claude", "gemini"] as const;

export const MODEL_COLORS: Record<string, string> = {
  "gpt-4.1": "#14b8a6",
  claude: "#f59e0b",
  gemini: "#3b82f6",
  deepseek: "#8b5cf6",
  mistral: "#ef4444",
  llama: "#06b6d4",
  qwen: "#6366f1",
};

export function modelColor(id: string): string {
  return MODEL_COLORS[id] ?? "oklch(0.55 0.02 260)";
}

const FALLBACK: ApiModel = {
  id: "unknown",
  name: "Model",
  vendor: "—",
  color: "oklch(0.55 0.02 260)",
  blurb: "",
};

type ModelsState = {
  models: ApiModel[];
  isLoading: boolean;
  modelById: (id: string) => ApiModel;
  flagshipModels: ApiModel[];
  refresh: () => Promise<void>;
};

const ModelsContext = createContext<ModelsState | null>(null);

export function ModelsProvider({ children }: { children: ReactNode }) {
  const { authHeaders, isAuthenticated } = useAuth();
  const [models, setModels] = useState<ApiModel[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const refresh = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) {
      setModels([]);
      return;
    }
    setIsLoading(true);
    try {
      const list = await api.models.list(auth);
      setModels(list);
    } finally {
      setIsLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    if (isAuthenticated) void refresh();
    else setModels([]);
  }, [isAuthenticated, refresh]);

  const modelById = useCallback(
    (id: string) => {
      const found = models.find((m) => m.id === id);
      if (found) return found;
      const slug = modelIdToSlug(id);
      if (slug) {
        return {
          id,
          name: displayNameFromSlug(slug),
          vendor: vendorFromSlug(slug),
          color: colorForModelId(id),
          blurb: "",
          openrouter_slug: slug,
          is_custom: true,
        } satisfies ApiModel;
      }
      return FALLBACK;
    },
    [models],
  );

  const flagshipModels = useMemo(
    () =>
      FLAGSHIP_MODEL_IDS.map((id) => modelById(id)).filter((m) => m.id !== "unknown"),
    [modelById],
  );

  const value = useMemo(
    () => ({ models, isLoading, modelById, flagshipModels, refresh }),
    [models, isLoading, modelById, flagshipModels, refresh],
  );

  return <ModelsContext.Provider value={value}>{children}</ModelsContext.Provider>;
}

export function useModels(): ModelsState {
  const ctx = useContext(ModelsContext);
  if (!ctx) throw new Error("useModels must be used within ModelsProvider");
  return ctx;
}

/** For modules outside React tree (e.g. cost.ts fallbacks) */
export function modelByIdStatic(id: string, models: ApiModel[]): ApiModel {
  return models.find((m) => m.id === id) ?? FALLBACK;
}
