import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { PROJECTS, SAMPLE_CHATS, type Chat, type Project } from "@/lib/mock";

type CreateProjectInput = { name: string; description?: string };

type ChatStore = {
  chats: Chat[];
  projects: Project[];
  renameChat: (id: string, title: string) => void;
  deleteChat: (id: string) => void;
  assignChatToProject: (chatId: string, projectId: string) => void;
  createProject: (input: CreateProjectInput) => Project;
  /** Live chat count for a project: seed baseline + assigned chats. */
  projectChatCount: (projectId: string) => number;
  projectById: (projectId: string | null | undefined) => Project | undefined;
};

const ChatStoreContext = createContext<ChatStore | null>(null);

export function ChatStoreProvider({ children }: { children: ReactNode }) {
  const [chats, setChats] = useState<Chat[]>(() => SAMPLE_CHATS.map((c) => ({ ...c })));
  const [projects, setProjects] = useState<Project[]>(() => PROJECTS.map((p) => ({ ...p })));

  const renameChat = useCallback((id: string, title: string) => {
    const next = title.trim();
    if (!next) return;
    setChats((prev) => prev.map((c) => (c.id === id ? { ...c, title: next } : c)));
  }, []);

  const deleteChat = useCallback((id: string) => {
    setChats((prev) => prev.filter((c) => c.id !== id));
  }, []);

  const assignChatToProject = useCallback((chatId: string, projectId: string) => {
    setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, projectId } : c)));
  }, []);

  const createProject = useCallback((input: CreateProjectInput) => {
    const project: Project = {
      id: `proj-${Date.now()}`,
      name: input.name.trim(),
      description: input.description?.trim() || undefined,
      chats: 0,
      members: 1,
      updated: "Just now",
    };
    setProjects((prev) => [project, ...prev]);
    return project;
  }, []);

  const projectChatCount = useCallback(
    (projectId: string) => {
      const base = projects.find((p) => p.id === projectId)?.chats ?? 0;
      const assigned = chats.filter((c) => c.projectId === projectId).length;
      return base + assigned;
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
      renameChat,
      deleteChat,
      assignChatToProject,
      createProject,
      projectChatCount,
      projectById,
    }),
    [
      chats,
      projects,
      renameChat,
      deleteChat,
      assignChatToProject,
      createProject,
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
