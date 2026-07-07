/** API-backed chat store — replaces in-memory mock state when authenticated */

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
import type { ApiChat, ApiModelSet, ApiProject } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import type { Chat, ModelSet, Project } from "@/lib/mock";

type CreateProjectInput = { name: string; description?: string };

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
  deleteChat: (id: string) => Promise<void>;
  assignChatToProject: (chatId: string, projectId: string) => Promise<void>;
  createProject: (input: CreateProjectInput) => Promise<Project>;
  deleteProject: (projectId: string) => Promise<void>;
  createChat: () => Promise<string | null>;
  refreshAll: () => Promise<void>;
  projectChatCount: (projectId: string) => number;
  projectById: (projectId: string | null | undefined) => Project | undefined;
};

const ChatStoreContext = createContext<ChatStore | null>(null);

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins || 1}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function mapChat(c: ApiChat): Chat {
  return {
    id: c.id,
    title: c.title,
    updated: formatRelativeTime(c.updated_at),
    projectId: c.project_id,
  };
}

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
  const [activeModelSetId, setActiveModelSetIdState] = useState("balanced");
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const refreshAll = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) return;
    setIsLoading(true);
    try {
      const [chatList, projectList, setList] = await Promise.all([
        api.chats.list(auth),
        api.projects.list(auth),
        api.modelSets.list(auth),
      ]);
      setChats(chatList.map(mapChat));
      setProjects(projectList.map(mapProject));
      setModelSets(setList.map(mapModelSet));
    } finally {
      setIsLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    if (isApiMode && !authLoading) {
      void refreshAll();
    }
  }, [isApiMode, authLoading, refreshAll]);

  const setActiveModelSetId = useCallback((id: string) => {
    setActiveModelSetIdState(id);
  }, []);

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
        custom_instructions: set.customInstructions ?? null,
      });
      setModelSets((prev) => prev.map((item) => (item.id === set.id ? mapModelSet(updated) : item)));
    },
    [authHeaders],
  );

  const deleteModelSet = useCallback(
    async (id: string) => {
      const auth = authHeaders();
      if (!auth) {
        setModelSets((prev) => prev.filter((item) => item.id !== id));
        setActiveModelSetIdState((activeId) => (activeId === id ? "balanced" : activeId));
        return;
      }
      await api.modelSets.delete(auth, id);
      setModelSets((prev) => prev.filter((item) => item.id !== id));
      setActiveModelSetIdState((activeId) => (activeId === id ? "balanced" : activeId));
    },
    [authHeaders],
  );

  const renameChat = useCallback(
    async (id: string, title: string) => {
      const next = title.trim();
      if (!next) return;
      const auth = authHeaders();
      if (!auth) {
        setChats((prev) => prev.map((c) => (c.id === id ? { ...c, title: next } : c)));
        return;
      }
      await api.chats.update(auth, id, { title: next });
      setChats((prev) => prev.map((c) => (c.id === id ? { ...c, title: next } : c)));
    },
    [authHeaders],
  );

  const deleteChat = useCallback(
    async (id: string) => {
      const auth = authHeaders();
      if (!auth) {
        setChats((prev) => prev.filter((c) => c.id !== id));
        return;
      }
      await api.chats.delete(auth, id);
      setChats((prev) => prev.filter((c) => c.id !== id));
      if (activeChatId === id) setActiveChatId(null);
    },
    [authHeaders, activeChatId],
  );

  const assignChatToProject = useCallback(
    async (chatId: string, projectId: string) => {
      const auth = authHeaders();
      if (!auth) {
        setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, projectId } : c)));
        return;
      }
      await api.chats.update(auth, chatId, { project_id: projectId });
      setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, projectId } : c)));
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

  const createChat = useCallback(async (): Promise<string | null> => {
    const auth = authHeaders();
    if (!auth) return null;
    const chat = await api.chats.create(auth, { title: "New chat" });
    const mapped = mapChat(chat);
    setChats((prev) => [mapped, ...prev]);
    setActiveChatId(chat.id);
    return chat.id;
  }, [authHeaders]);

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
