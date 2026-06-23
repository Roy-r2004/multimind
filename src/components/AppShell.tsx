import { Link, useRouterState } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import {
  MessageSquarePlus,
  Search,
  Settings,
  FolderKanban,
  FileText,
  Sparkles,
  Menu,
  X,
  History,
  MoreHorizontal,
  Pencil,
  FolderPlus,
  Trash2,
  Folder,
  Plus,
  Check,
} from "lucide-react";
import { Modal } from "@/components/Modal";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { useChatStore } from "@/lib/store";
import type { Chat } from "@/lib/mock";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/chat", label: "New Chat", icon: MessageSquarePlus },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/templates", label: "Templates", icon: FileText },
];

export function AppShell({
  children,
  rightPanel,
}: {
  children: ReactNode;
  rightPanel?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const path = useRouterState({ select: (s) => s.location.pathname });
  const { chats, projectById } = useChatStore();
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<Chat | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Chat | null>(null);
  const [assignTarget, setAssignTarget] = useState<Chat | null>(null);
  const [showCreateProject, setShowCreateProject] = useState(false);

  return (
    <div className="flex min-h-screen w-full bg-background text-foreground">
      {/* Mobile top bar */}
      <header className="fixed top-0 left-0 right-0 z-30 flex h-14 items-center justify-between border-b border-border bg-sidebar/95 px-4 backdrop-blur md:hidden">
        <button onClick={() => setOpen(true)} className="p-2 -ml-2 cursor-pointer">
          <Menu className="size-5" />
        </button>
        <Link to="/chat" className="flex items-center gap-2 font-display font-semibold">
          <Sparkles className="size-4 text-primary" /> MultiAI
        </Link>
        <div className="w-8" />
      </header>

      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-sidebar-border bg-sidebar transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center justify-between px-4 border-b border-sidebar-border">
          <Link
            to="/chat"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 font-display font-semibold"
          >
            <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
              <Sparkles className="size-4" />
            </span>
            MultiAI
          </Link>
          <button onClick={() => setOpen(false)} className="p-2 md:hidden cursor-pointer">
            <X className="size-4" />
          </button>
        </div>

        <div className="px-3 pt-3">
          <Link
            to="/chat"
            onClick={() => setOpen(false)}
            className="flex w-full items-center gap-2 rounded-xl bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90"
          >
            <MessageSquarePlus className="size-4" /> New chat
          </Link>
          <div className="relative mt-3">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              placeholder="Search chats…"
              className="w-full rounded-lg border border-sidebar-border bg-background py-2 pl-9 pr-3 text-sm outline-none focus:ring-2 focus:ring-ring/40"
            />
          </div>
        </div>

        <nav className="px-3 pt-4 space-y-0.5">
          {NAV.slice(1).map((n) => {
            const active = path.startsWith(n.to);
            return (
              <Link
                key={n.to}
                to={n.to}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-sidebar-foreground/80 hover:bg-accent/60",
                  active && "bg-accent text-accent-foreground font-medium",
                )}
              >
                <n.icon className="size-4" /> {n.label}
              </Link>
            );
          })}
        </nav>

        <div className="px-3 mt-4">
          <div className="flex items-center gap-2 px-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            <History className="size-3.5" /> Recent
          </div>
          <div className="mt-1 max-h-[40vh] overflow-y-auto space-y-0.5">
            {chats.map((c) => {
              const project = projectById(c.projectId);
              return (
                <div key={c.id} className="group relative rounded-lg hover:bg-accent/60">
                  <Link
                    to="/chat"
                    onClick={() => setOpen(false)}
                    className="block rounded-lg px-3 py-2 pr-9"
                    title={c.title}
                  >
                    <div className="truncate text-sm text-sidebar-foreground/80">{c.title}</div>
                    {project && (
                      <div className="mt-0.5 flex items-center gap-1 truncate text-[11px] text-muted-foreground">
                        <Folder className="size-3 shrink-0" />
                        <span className="truncate">{project.name}</span>
                      </div>
                    )}
                  </Link>
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setMenuOpenId((id) => (id === c.id ? null : c.id));
                    }}
                    aria-label="Chat actions"
                    className={cn(
                      "absolute right-1.5 top-1.5 rounded-md p-1 text-muted-foreground transition hover:bg-accent hover:text-foreground",
                      menuOpenId === c.id ? "opacity-100" : "opacity-0 group-hover:opacity-100",
                    )}
                  >
                    <MoreHorizontal className="size-4" />
                  </button>
                  {menuOpenId === c.id && (
                    <>
                      <div className="fixed inset-0 z-30" onClick={() => setMenuOpenId(null)} />
                      <div className="absolute right-1.5 top-9 z-40 w-44 overflow-hidden rounded-xl border border-border bg-popover p-1 shadow-lg">
                        <button
                          onClick={() => {
                            setRenameTarget(c);
                            setMenuOpenId(null);
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm hover:bg-accent"
                        >
                          <Pencil className="size-4 text-muted-foreground" /> Rename
                        </button>
                        <button
                          onClick={() => {
                            setAssignTarget(c);
                            setMenuOpenId(null);
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm hover:bg-accent"
                        >
                          <FolderPlus className="size-4 text-muted-foreground" /> Add to Project
                        </button>
                        <button
                          onClick={() => {
                            setDeleteTarget(c);
                            setMenuOpenId(null);
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-destructive hover:bg-destructive/10"
                        >
                          <Trash2 className="size-4" /> Delete
                        </button>
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div className="mt-auto border-t border-sidebar-border p-3">
          <Link
            to="/settings"
            onClick={() => setOpen(false)}
            className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-accent/60"
          >
            <Settings className="size-4" /> Settings
          </Link>
          <Link
            to="/settings"
            onClick={() => setOpen(false)}
            className="mt-2 flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-accent/60"
          >
            <div className="grid size-8 place-items-center rounded-full bg-accent text-accent-foreground text-sm font-semibold">
              S
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">Sara K.</div>
              <div className="truncate text-xs text-muted-foreground">Pro plan</div>
            </div>
          </Link>
        </div>
      </aside>

      {/* backdrop */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-30 bg-foreground/30 md:hidden"
        />
      )}

      <main className="flex-1 min-w-0 pt-14 md:pt-0">
        <div className="flex">
          <div className="flex-1 min-w-0">{children}</div>
          {rightPanel && (
            <div className="hidden xl:block w-80 border-l border-border bg-sidebar/40">
              {rightPanel}
            </div>
          )}
        </div>
      </main>

      <RenameChatModal chat={renameTarget} onClose={() => setRenameTarget(null)} />
      <DeleteChatModal chat={deleteTarget} onClose={() => setDeleteTarget(null)} />
      <AddToProjectModal
        chat={assignTarget}
        onClose={() => setAssignTarget(null)}
        onCreateProject={() => {
          setAssignTarget(null);
          setShowCreateProject(true);
        }}
      />
      <CreateProjectModal open={showCreateProject} onClose={() => setShowCreateProject(false)} />
    </div>
  );
}

function RenameChatModal({ chat, onClose }: { chat: Chat | null; onClose: () => void }) {
  const { renameChat } = useChatStore();
  const [title, setTitle] = useState("");

  // Re-seed the field whenever a different chat is targeted.
  const [seedId, setSeedId] = useState<string | null>(null);
  if (chat && chat.id !== seedId) {
    setSeedId(chat.id);
    setTitle(chat.title);
  }

  function save() {
    if (!chat || !title.trim()) return;
    renameChat(chat.id, title);
    onClose();
  }

  return (
    <Modal open={!!chat} onClose={onClose} title="Rename chat" size="sm">
      <div className="space-y-4">
        <label className="block text-sm">
          <div className="mb-1 font-medium">Chat Name</div>
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
            }}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40"
          />
        </label>
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={!title.trim()}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
          >
            Save
          </button>
        </div>
      </div>
    </Modal>
  );
}

function DeleteChatModal({ chat, onClose }: { chat: Chat | null; onClose: () => void }) {
  const { deleteChat } = useChatStore();
  return (
    <Modal open={!!chat} onClose={onClose} title="Delete Chat?" size="sm">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Are you sure you want to delete this conversation?
        </p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              if (chat) deleteChat(chat.id);
              onClose();
            }}
            className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:opacity-90"
          >
            Delete
          </button>
        </div>
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

  // Default the selection to the chat's current project each time it opens.
  const [seedId, setSeedId] = useState<string | null>(null);
  if (chat && chat.id !== seedId) {
    setSeedId(chat.id);
    setSelected(chat.projectId ?? null);
  }

  function add() {
    if (!chat || !selected) return;
    assignChatToProject(chat.id, selected);
    onClose();
  }

  return (
    <Modal open={!!chat} onClose={onClose} title="Add Chat to Project" size="md">
      <div className="space-y-4">
        <div className="space-y-1.5">
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => setSelected(p.id)}
              className={cn(
                "flex w-full items-center gap-3 rounded-xl border p-3 text-left text-sm transition",
                selected === p.id
                  ? "border-primary bg-primary/5"
                  : "border-border hover:bg-accent/60",
              )}
            >
              <span className="grid size-8 place-items-center rounded-lg bg-accent text-accent-foreground">
                <Folder className="size-4" />
              </span>
              <span className="min-w-0 flex-1 truncate font-medium">{p.name}</span>
              {selected === p.id && <Check className="size-4 text-primary" />}
            </button>
          ))}
          <button
            onClick={onCreateProject}
            className="flex w-full items-center gap-3 rounded-xl border border-dashed border-border p-3 text-left text-sm text-muted-foreground hover:bg-accent/60"
          >
            <span className="grid size-8 place-items-center rounded-lg bg-accent text-accent-foreground">
              <Plus className="size-4" />
            </span>
            Create New Project
          </button>
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            onClick={add}
            disabled={!selected}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
          >
            Add
          </button>
        </div>
      </div>
    </Modal>
  );
}
