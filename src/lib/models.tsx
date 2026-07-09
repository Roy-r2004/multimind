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

export const FLAGSHIP_MODEL_IDS = ["gpt-4.1", "claude", "gemini", "grok", "deepseek"] as const;

export const MODEL_COLORS: Record<string, string> = {
  "gpt-4.1": "#14b8a6",
  claude: "#f59e0b",
  gemini: "#3b82f6",
  grok: "#111827",
  deepseek: "#8b5cf6",
  mistral: "#ef4444",
  llama: "#06b6d4",
  qwen: "#6366f1",
};

const BUILTIN_MODELS: Record<string, ApiModel> = {
  "gpt-4.1": {
    id: "gpt-4.1",
    name: "GPT-4.1",
    vendor: "OpenAI",
    color: MODEL_COLORS["gpt-4.1"],
    blurb: "OpenAI flagship",
  },
  claude: {
    id: "claude",
    name: "Claude Sonnet 4",
    vendor: "Anthropic",
    color: MODEL_COLORS.claude,
    blurb: "Careful reasoner",
  },
  gemini: {
    id: "gemini",
    name: "Gemini 2.5 Pro",
    vendor: "Google",
    color: MODEL_COLORS.gemini,
    blurb: "Multimodal frontier",
  },
  grok: {
    id: "grok",
    name: "Grok",
    vendor: "xAI",
    color: MODEL_COLORS.grok,
    blurb: "xAI frontier model",
  },
  deepseek: {
    id: "deepseek",
    name: "DeepSeek V3",
    vendor: "DeepSeek",
    color: MODEL_COLORS.deepseek,
    blurb: "Coding specialist",
  },
  mistral: {
    id: "mistral",
    name: "Mistral Large",
    vendor: "Mistral",
    color: MODEL_COLORS.mistral,
    blurb: "Fast European frontier",
  },
  llama: {
    id: "llama",
    name: "Llama 3.3 70B",
    vendor: "Meta",
    color: MODEL_COLORS.llama,
    blurb: "Open-weight workhorse",
  },
  qwen: {
    id: "qwen",
    name: "Qwen 2.5 72B",
    vendor: "Alibaba",
    color: MODEL_COLORS.qwen,
    blurb: "Strong multilingual reasoning",
  },
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
      const builtin = BUILTIN_MODELS[id];
      if (builtin) return builtin;
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
    () => FLAGSHIP_MODEL_IDS.map((id) => modelById(id)).filter((m) => m.id !== "unknown"),
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
  return models.find((m) => m.id === id) ?? BUILTIN_MODELS[id] ?? FALLBACK;
}
