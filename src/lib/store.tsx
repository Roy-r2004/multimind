/** API-backed chat store — replaces in-memory mock state when authenticated */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import type { ApiChat, ApiModelSet, ApiProject, ApiTurn } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import {
  chatFromTurnActivity,
  formatRelativeTime,
  mapApiChat,
  upsertChatToTop,
} from "@/lib/chatHistory";
import { shouldApplyRefreshResult } from "@/lib/chatStoreRefresh";
import type { Chat, ModelSet, Project } from "@/lib/mock";
import { selectExistingModelSetId } from "@/lib/modelSetSelection";

type CreateProjectInput = { name: string; description?: string };

type CreateChatOptions = {
  activate?: boolean;
  onChatCreated?: (chatId: string) => void;
  /** Optional project association for create (e.g. project page). */
  projectId?: string | null;
};

type ChatStore = {
  chats: Chat[];
  projects: Project[];
  modelSets: ModelSet[];
  activeModelSetId: string;
  activeChatId: string | null;
  isApiMode: boolean;
  isLoading: boolean;
  setActiveModelSetId: (id: string) => void;
  setActiveChatId: (id: string | null) => void;
  createModelSet: (set: ModelSet) => Promise<ModelSet>;
  updateModelSet: (set: ModelSet) => Promise<void>;
  deleteModelSet: (id: string) => Promise<void>;
  renameChat: (id: string, title: string) => Promise<void>;
  deleteChat: (id: string, options?: { onlyIfUnused?: boolean }) => Promise<void>;
  assignChatToProject: (chatId: string, projectId: string) => Promise<void>;
  createProject: (input: CreateProjectInput) => Promise<Project>;
  deleteProject: (projectId: string) => Promise<void>;
  createChat: (options?: CreateChatOptions) => Promise<string | null>;
  refreshAll: () => Promise<void>;
  applyChatUpdate: (chat: ApiChat) => void;
  /** Move/update sidebar chat from turn create/regenerate metadata. */
  applyChatActivityFromTurn: (turn: ApiTurn) => void;
  /** Discard a chat created by the current op if still unused (turns/attachments empty). */
  discardUnusedChat: (chatId: string) => Promise<boolean>;
  projectChatCount: (projectId: string) => number;
  projectById: (projectId: string | null | undefined) => Project | undefined;
};

const ChatStoreContext = createContext<ChatStore | null>(null);

function mapModelSet(s: ApiModelSet): ModelSet {
  return {
    id: s.id,
    name: s.name,
    description: s.description,
    models: s.models,
    verdictModel: s.verdict_model,
    strategy: s.strategy,
    bestFor: s.best_for,
    templateName: s.template_name ?? undefined,
    customInstructions: s.custom_instructions ?? undefined,
    effectiveRefereePrompt: s.effective_referee_prompt ?? undefined,
  };
}

function mapProject(p: ApiProject): Project {
  return {
    id: p.id,
    name: p.name,
    description: p.description ?? undefined,
    chats: p.chat_count,
    members: 1,
    updated: formatRelativeTime(p.updated_at),
  };
}

export function ChatStoreProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, authHeaders, isLoading: authLoading } = useAuth();
  const isApiMode = isAuthenticated;

  const [chats, setChats] = useState<Chat[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [modelSets, setModelSets] = useState<ModelSet[]>([]);
  const [activeModelSetId, setActiveModelSetIdState] = useState("");
  const [activeChatId, setActiveChatIdState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const activeChatIdRef = useRef<string | null>(null);
  activeChatIdRef.current = activeChatId;
  const createChatInflightRef = useRef<Promise<string | null> | null>(null);
  const refreshGenerationRef = useRef(0);

  const clearChatScopedState = useCallback(() => {
    // Invalidate any in-flight refreshAll so it cannot repopulate after logout.
    refreshGenerationRef.current += 1;
    setChats([]);
    setProjects([]);
    setModelSets([]);
    setActiveChatIdState(null);
    setActiveModelSetIdState("");
    createChatInflightRef.current = null;
    setIsLoading(false);
  }, []);

  const refreshAll = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) return;

    const requestGeneration = ++refreshGenerationRef.current;
    const requestAuth = { token: auth.token, orgId: auth.orgId };
    setIsLoading(true);
    try {
      const [chatList, projectList, setList] = await Promise.all([
        api.chats.list(auth),
        api.projects.list(auth),
        api.modelSets.list(auth),
      ]);

      const currentAuth = authHeaders();
      if (
        !shouldApplyRefreshResult({
          requestGeneration,
          currentGeneration: refreshGenerationRef.current,
          requestAuth,
          currentAuth,
        })
      ) {
        return;
      }

      setChats(chatList.map(mapApiChat));
      setProjects(projectList.map(mapProject));
      const mappedModelSets = setList.map(mapModelSet);
      setModelSets(mappedModelSets);
      setActiveModelSetIdState((activeId) => selectExistingModelSetId(mappedModelSets, activeId));
    } finally {
      if (requestGeneration === refreshGenerationRef.current) {
        setIsLoading(false);
      }
    }
  }, [authHeaders]);

  useEffect(() => {
    if (isApiMode && !authLoading) {
      void refreshAll();
      return;
    }
    if (!authLoading && !isAuthenticated) {
      clearChatScopedState();
    }
  }, [isApiMode, authLoading, isAuthenticated, refreshAll, clearChatScopedState]);

  const setActiveModelSetId = useCallback((id: string) => {
    setActiveModelSetIdState(id);
  }, []);

  const setActiveChatId = useCallback(
    (id: string | null) => {
      const previousId = activeChatIdRef.current;
      setActiveChatIdState(id);
      if (id === null && previousId !== null) {
        setActiveModelSetIdState(() => selectExistingModelSetId(modelSets, ""));
      }
    },
    [modelSets],
  );

  const createModelSet = useCallback(
    async (set: ModelSet): Promise<ModelSet> => {
      const auth = authHeaders();
      if (!auth) {
        setModelSets((prev) => [set, ...prev]);
        return set;
      }
      const created = await api.modelSets.create(auth, {
        name: set.name,
        description: set.description,
        models: set.models,
        verdict_model: set.verdictModel,
        strategy: set.strategy,
        best_for: set.bestFor,
        template_name: set.templateName,
        custom_instructions: set.customInstructions,
      });
      const mapped = mapModelSet(created);
      setModelSets((prev) => [mapped, ...prev]);
      return mapped;
    },
    [authHeaders],
  );

  const updateModelSet = useCallback(
    async (set: ModelSet) => {
      const auth = authHeaders();
      if (!auth) {
        setModelSets((prev) => prev.map((item) => (item.id === set.id ? set : item)));
        return;
      }
      const updated = await api.modelSets.update(auth, set.id, {
        name: set.name,
        description: set.description,
        models: set.models,
        verdict_model: set.verdictModel,
        strategy: set.strategy,
        best_for: set.bestFor,
        template_name: set.templateName ?? null,
        custom_instructions: set.customInstructions ?? null,
      });
      setModelSets((prev) =>
        prev.map((item) => (item.id === set.id ? mapModelSet(updated) : item)),
      );
    },
    [authHeaders],
  );

  const deleteModelSet = useCallback(
    async (id: string) => {
      const auth = authHeaders();
      if (!auth) {
        setModelSets((prev) => {
          const remaining = prev.filter((item) => item.id !== id);
          setActiveModelSetIdState((activeId) => selectExistingModelSetId(remaining, activeId));
          return remaining;
        });
        return;
      }
      await api.modelSets.delete(auth, id);
      setModelSets((prev) => {
        const remaining = prev.filter((item) => item.id !== id);
        setActiveModelSetIdState((activeId) => selectExistingModelSetId(remaining, activeId));
        return remaining;
      });
    },
    [authHeaders],
  );

  const renameChat = useCallback(
    async (id: string, title: string) => {
      const next = title.trim();
      if (!next) return;
      const auth = authHeaders();
      if (!auth) {
        setChats((prev) => {
          const current = prev.find((c) => c.id === id);
          if (!current) return prev;
          return upsertChatToTop(prev, { ...current, title: next });
        });
        return;
      }
      const updated = await api.chats.update(auth, id, { title: next });
      setChats((prev) => upsertChatToTop(prev, mapApiChat(updated)));
    },
    [authHeaders],
  );

  const deleteChat = useCallback(
    async (id: string, options?: { onlyIfUnused?: boolean }) => {
      const auth = authHeaders();
      if (!auth) {
        setChats((prev) => prev.filter((c) => c.id !== id));
        return;
      }
      await api.chats.delete(auth, id, { onlyIfUnused: options?.onlyIfUnused });
      setChats((prev) => prev.filter((c) => c.id !== id));
      if (activeChatIdRef.current === id) setActiveChatId(null);
    },
    [authHeaders, setActiveChatId],
  );

  const discardUnusedChat = useCallback(
    async (chatId: string): Promise<boolean> => {
      const auth = authHeaders();
      if (!auth) return false;
      try {
        await api.chats.delete(auth, chatId, { onlyIfUnused: true });
        setChats((prev) => prev.filter((c) => c.id !== chatId));
        if (activeChatIdRef.current === chatId) setActiveChatId(null);
        return true;
      } catch {
        return false;
      }
    },
    [authHeaders, setActiveChatId],
  );

  const assignChatToProject = useCallback(
    async (chatId: string, projectId: string) => {
      const auth = authHeaders();
      if (!auth) {
        setChats((prev) => {
          const current = prev.find((c) => c.id === chatId);
          if (!current) return prev;
          return upsertChatToTop(prev, { ...current, projectId });
        });
        return;
      }
      const updated = await api.chats.update(auth, chatId, { project_id: projectId });
      setChats((prev) => upsertChatToTop(prev, mapApiChat(updated)));
    },
    [authHeaders],
  );

  const createProject = useCallback(
    async (input: CreateProjectInput): Promise<Project> => {
      const auth = authHeaders();
      if (!auth) {
        const project: Project = {
          id: `proj-${Date.now()}`,
          name: input.name.trim(),
          description: input.description?.trim(),
          chats: 0,
          members: 1,
          updated: "Just now",
        };
        setProjects((prev) => [project, ...prev]);
        return project;
      }
      const created = await api.projects.create(auth, {
        name: input.name,
        description: input.description,
      });
      const project = mapProject(created);
      setProjects((prev) => [project, ...prev]);
      return project;
    },
    [authHeaders],
  );

  const deleteProject = useCallback(
    async (projectId: string) => {
      const auth = authHeaders();
      if (!auth) {
        setChats((prev) =>
          prev.map((c) => (c.projectId === projectId ? { ...c, projectId: null } : c)),
        );
        setProjects((prev) => prev.filter((p) => p.id !== projectId));
        return;
      }
      await api.projects.delete(auth, projectId);
      setChats((prev) =>
        prev.map((c) => (c.projectId === projectId ? { ...c, projectId: null } : c)),
      );
      setProjects((prev) => prev.filter((p) => p.id !== projectId));
    },
    [authHeaders],
  );

  const createChat = useCallback(
    async (options?: CreateChatOptions): Promise<string | null> => {
      const auth = authHeaders();
      if (!auth) return null;

      // Share one in-flight create so upload + send cannot POST twice.
      if (!createChatInflightRef.current) {
        createChatInflightRef.current = (async () => {
          try {
            const chat = await api.chats.create(auth, {
              title: "New chat",
              project_id: options?.projectId ?? undefined,
            });
            const mapped = mapApiChat(chat);
            setChats((prev) => upsertChatToTop(prev, mapped));
            return chat.id;
          } finally {
            createChatInflightRef.current = null;
          }
        })();
      }

      const chatId = await createChatInflightRef.current;
      if (!chatId) return null;
      options?.onChatCreated?.(chatId);
      if (options?.activate !== false) {
        setActiveChatId(chatId);
      }
      return chatId;
    },
    [authHeaders, setActiveChatId],
  );

  const applyChatUpdate = useCallback((chat: ApiChat) => {
    setChats((prev) => upsertChatToTop(prev, mapApiChat(chat)));
  }, []);

  const applyChatActivityFromTurn = useCallback((turn: ApiTurn) => {
    setChats((prev) => {
      const existing = prev.find((item) => item.id === turn.chat_id);
      const next = chatFromTurnActivity(existing, {
        chatId: turn.chat_id,
        title: turn.chat_title ?? existing?.title,
        updatedAt: turn.chat_updated_at ?? undefined,
      });
      return upsertChatToTop(prev, next);
    });
  }, []);

  const projectChatCount = useCallback(
    (projectId: string) => {
      const base = projects.find((p) => p.id === projectId)?.chats ?? 0;
      const assigned = chats.filter((c) => c.projectId === projectId).length;
      return Math.max(base, assigned);
    },
    [projects, chats],
  );

  const projectById = useCallback(
    (projectId: string | null | undefined) =>
      projectId ? projects.find((p) => p.id === projectId) : undefined,
    [projects],
  );

  const value = useMemo<ChatStore>(
    () => ({
      chats,
      projects,
      modelSets,
      activeModelSetId,
      activeChatId,
      isApiMode,
      isLoading,
      setActiveModelSetId,
      setActiveChatId,
      createModelSet,
      updateModelSet,
      deleteModelSet,
      renameChat,
      deleteChat,
      assignChatToProject,
      createProject,
      deleteProject,
      createChat,
      refreshAll,
      applyChatUpdate,
      applyChatActivityFromTurn,
      discardUnusedChat,
      projectChatCount,
      projectById,
    }),
    [
      chats,
      projects,
      modelSets,
      activeModelSetId,
      activeChatId,
      isApiMode,
      isLoading,
      setActiveModelSetId,
      setActiveChatId,
      createModelSet,
      updateModelSet,
      deleteModelSet,
      renameChat,
      deleteChat,
      assignChatToProject,
      createProject,
      deleteProject,
      createChat,
      refreshAll,
      applyChatUpdate,
      applyChatActivityFromTurn,
      discardUnusedChat,
      projectChatCount,
      projectById,
    ],
  );

  return <ChatStoreContext.Provider value={value}>{children}</ChatStoreContext.Provider>;
}

export function useChatStore(): ChatStore {
  const ctx = useContext(ChatStoreContext);
  if (!ctx) throw new Error("useChatStore must be used within a ChatStoreProvider");
  return ctx;
}
