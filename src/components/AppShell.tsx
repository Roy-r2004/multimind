import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import {
  MessageSquarePlus,
  Settings,
  FolderKanban,
  LayoutGrid,
  Menu,
  X,
  History,
  MoreHorizontal,
  Pencil,
  FolderPlus,
  Trash2,
  Folder,
  Plus,
  LogOut,
  Brain,
  BookOpen,
} from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { CinematicBackdrop } from "@/components/cinematic/PageChrome";
import { Modal } from "@/components/Modal";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useChatStore } from "@/lib/store";
import { useAuth } from "@/lib/auth";
import type { Chat } from "@/lib/mock";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/model-sets", label: "Model Sets", icon: LayoutGrid },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/brain", label: "Brain", icon: Brain },
  { to: "/lessons", label: "Lessons", icon: BookOpen },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();
  const { session, signOut } = useAuth();
  const { chats, projectById, renameChat, assignChatToProject, setActiveChatId, refreshAll } =
    useChatStore();
  const [editingChatId, setEditingChatId] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Chat | null>(null);
  const [assignTarget, setAssignTarget] = useState<Chat | null>(null);
  const [showCreateProject, setShowCreateProject] = useState(false);

  const initials = session?.user.full_name?.slice(0, 1).toUpperCase() ?? "?";

  function saveRename(chatId: string) {
    if (!renameTitle.trim()) {
      setEditingChatId(null);
      return;
    }
    renameChat(chatId, renameTitle.trim());
    setEditingChatId(null);
    setRenameTitle("");
  }

  return (
    <div className="relative flex min-h-screen w-full text-foreground">
      <CinematicBackdrop />

      <header className="fixed top-0 left-0 right-0 z-30 flex h-14 items-center justify-between border-b border-border bg-sidebar px-4 shadow-sm md:hidden">
        <button onClick={() => setOpen(true)} className="p-2 -ml-2">
          <Menu className="size-5" />
        </button>
        <Link to="/chat" className="flex items-center gap-2 font-display text-lg font-semibold">
          <BrandLogo className="size-6" /> MultiAI
        </Link>
        <div className="w-8" />
      </header>

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-border bg-sidebar shadow-sm transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center justify-between border-b border-border px-4">
          <Link
            to="/chat"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 font-display font-semibold"
          >
            <BrandLogo className="size-7" />
            MultiAI
          </Link>
          <button onClick={() => setOpen(false)} className="p-2 md:hidden">
            <X className="size-4" />
          </button>
        </div>

        <div className="p-3">
          <Link
            to="/chat"
            onClick={() => {
              setActiveChatId(null);
              setOpen(false);
            }}
            className="flex w-full items-center gap-2 rounded-xl bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            <MessageSquarePlus className="size-4" /> New chat
          </Link>
        </div>

        <nav className="space-y-0.5 px-3">
          {NAV.map((n) => (
            <Link
              key={n.to}
              to={n.to}
              onClick={() => setOpen(false)}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-sidebar-foreground/80 transition hover:bg-accent",
                path.startsWith(n.to) && "bg-accent font-medium text-foreground",
              )}
            >
              <n.icon className="size-4" /> {n.label}
            </Link>
          ))}
        </nav>

        <div className="mt-4 flex-1 overflow-hidden px-3">
          <div className="flex items-center gap-2 px-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
            <History className="size-3.5" /> Recent
          </div>
          <div className="mt-2 max-h-[38vh] space-y-0.5 overflow-y-auto">
            {chats.length === 0 ? (
              <p className="px-2 py-3 text-xs text-muted-foreground">No chats yet</p>
            ) : (
              chats.map((c) => (
                <div key={c.id} className="group relative rounded-lg hover:bg-accent">
                  {editingChatId === c.id ? (
                    <input
                      autoFocus
                      value={renameTitle}
                      onChange={(e) => setRenameTitle(e.target.value)}
                      onBlur={() => saveRename(c.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveRename(c.id);
                        if (e.key === "Escape") setEditingChatId(null);
                      }}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none"
                    />
                  ) : (
                    <Link
                      to="/chat"
                      onClick={() => {
                        setActiveChatId(c.id);
                        setOpen(false);
                      }}
                      className="block truncate px-3 py-2 pr-8 text-sm text-sidebar-foreground/85"
                    >
                      {c.title}
                      {projectById(c.projectId) && (
                        <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
                          {projectById(c.projectId)?.name}
                        </span>
                      )}
                    </Link>
                  )}
                  {editingChatId !== c.id && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          type="button"
                          aria-label={`Chat options for ${c.title}`}
                          onClick={(e) => e.stopPropagation()}
                          className="absolute right-1 top-1.5 z-10 rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100 data-[state=open]:opacity-100"
                        >
                          <MoreHorizontal className="size-4 text-muted-foreground" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-40">
                        <DropdownMenuItem
                          onSelect={() => {
                            setEditingChatId(c.id);
                            setRenameTitle(c.title);
                          }}
                        >
                          <Pencil className="size-3.5" /> Rename
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => setAssignTarget(c)}>
                          <FolderPlus className="size-3.5" /> Project
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-destructive focus:text-destructive"
                          onSelect={() => setDeleteTarget(c)}
                        >
                          <Trash2 className="size-3.5" /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        <div className="border-t border-border p-3">
          <Link
            to="/settings"
            onClick={() => setOpen(false)}
            className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-accent"
          >
            <Settings className="size-4" /> Settings
          </Link>
          <div className="mt-2 flex items-center gap-3 rounded-lg px-2 py-2">
            <div className="grid size-9 place-items-center rounded-full bg-primary/15 text-sm font-semibold text-primary">
              {initials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">
                {session?.user.full_name ?? "Guest"}
              </div>
              <div className="truncate text-xs text-muted-foreground">{session?.user.email}</div>
            </div>
            <button
              onClick={() => {
                signOut();
                void navigate({ to: "/login" });
              }}
              className="rounded-lg p-2 text-muted-foreground hover:bg-accent hover:text-foreground"
              title="Sign out"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>

      {open && (
        <div
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-30 bg-foreground/25 md:hidden"
        />
      )}

      <main className="relative flex-1 min-w-0 pt-14 md:pt-0">{children}</main>

      <DeleteChatModal chat={deleteTarget} onClose={() => setDeleteTarget(null)} />
      <AddToProjectModal
        chat={assignTarget}
        onClose={() => setAssignTarget(null)}
        onCreateProject={() => setShowCreateProject(true)}
      />
      <CreateProjectModal
        open={showCreateProject}
        onClose={() => setShowCreateProject(false)}
        onCreated={async (project) => {
          if (assignTarget) {
            await assignChatToProject(assignTarget.id, project.id);
            await refreshAll();
            setAssignTarget(null);
          }
          setShowCreateProject(false);
        }}
      />
    </div>
  );
}

function DeleteChatModal({ chat, onClose }: { chat: Chat | null; onClose: () => void }) {
  const { deleteChat } = useChatStore();
  const navigate = useNavigate();
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmDelete() {
    if (!chat) return;
    setRemoving(true);
    setError(null);
    try {
      await deleteChat(chat.id);
      onClose();
      void navigate({ to: "/chat" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete chat");
    } finally {
      setRemoving(false);
    }
  }

  return (
    <Modal open={!!chat} onClose={onClose} title="Delete chat?" size="sm">
      <p className="text-sm text-muted-foreground">
        {chat ? `"${chat.title}" will be permanently removed.` : "This cannot be undone."}
      </p>
      {error && <p className="mt-2 text-sm text-destructive">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          disabled={removing}
          className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={removing}
          onClick={() => void confirmDelete()}
          className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground disabled:opacity-50"
        >
          {removing ? "Deleting…" : "Delete"}
        </button>
      </div>
    </Modal>
  );
}

function AddToProjectModal({
  chat,
  onClose,
  onCreateProject,
}: {
  chat: Chat | null;
  onClose: () => void;
  onCreateProject: () => void;
}) {
  const { projects, assignChatToProject } = useChatStore();
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <Modal open={!!chat} onClose={onClose} title="Add to project" size="md">
      <div className="space-y-2">
        {projects.map((p) => (
          <button
            key={p.id}
            onClick={() => setSelected(p.id)}
            className={cn(
              "flex w-full items-center gap-3 rounded-xl border p-3 text-left text-sm",
              selected === p.id ? "border-primary bg-primary/10" : "border-border hover:bg-accent",
            )}
          >
            <Folder className="size-4" />
            {p.name}
          </button>
        ))}
        <button
          onClick={onCreateProject}
          className="flex w-full items-center gap-3 rounded-xl border border-dashed border-border p-3 text-sm text-muted-foreground hover:bg-accent"
        >
          <Plus className="size-4" /> New project
        </button>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="rounded-lg border border-border px-4 py-2 text-sm">
          Cancel
        </button>
        <button
          disabled={!selected || !chat}
          onClick={() => {
            if (chat && selected) assignChatToProject(chat.id, selected);
            onClose();
          }}
          className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
        >
          Add
        </button>
      </div>
    </Modal>
  );
}
