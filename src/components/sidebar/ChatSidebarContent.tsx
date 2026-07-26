import { Link, useNavigate } from "@tanstack/react-router";
import {
  Folder,
  FolderPlus,
  History,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { Modal } from "@/components/Modal";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Chat } from "@/lib/mock";
import { useChatStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function ChatSidebarContent({ onNavigate }: { onNavigate: () => void }) {
  const { chats, projectById, renameChat, assignChatToProject, setActiveChatId, refreshAll } =
    useChatStore();
  const [editingChatId, setEditingChatId] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Chat | null>(null);
  const [assignTarget, setAssignTarget] = useState<Chat | null>(null);
  const [showCreateProject, setShowCreateProject] = useState(false);

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
    <>
      <div className="p-3">
        <Link
          to="/chat"
          onClick={() => {
            setActiveChatId(null);
            onNavigate();
          }}
          className="flex w-full items-center gap-2 rounded-xl bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
        >
          <MessageSquarePlus className="size-4" /> New chat
        </Link>
      </div>
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
                      onNavigate();
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
    </>
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
          {removing ? "Deleting..." : "Delete"}
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
