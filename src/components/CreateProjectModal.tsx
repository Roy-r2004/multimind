import { useState } from "react";
import { Modal } from "@/components/Modal";
import { useChatStore } from "@/lib/store";
import type { Project } from "@/lib/mock";

export function CreateProjectModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated?: (project: Project) => void;
}) {
  const { createProject } = useChatStore();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setDescription("");
    setError(null);
  }

  function close() {
    reset();
    onClose();
  }

  function submit() {
    if (!name.trim()) {
      setError("Please enter a project name.");
      return;
    }
    const project = createProject({ name, description });
    reset();
    onCreated?.(project);
    onClose();
  }

  return (
    <Modal open={open} onClose={close} title="Create Project" size="md">
      <div className="space-y-4">
        <label className="block text-sm">
          <div className="mb-1 font-medium">
            Project Name <span className="text-destructive">*</span>
          </div>
          <input
            autoFocus
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            placeholder="e.g. AI startup planning"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40"
          />
        </label>
        <label className="block text-sm">
          <div className="mb-1 font-medium">
            Description <span className="font-normal text-muted-foreground">(optional)</span>
          </div>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="e.g. AI startup planning and research"
            className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40"
          />
        </label>
        {error && <div className="text-sm text-destructive">{error}</div>}
        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={close}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Create Project
          </button>
        </div>
      </div>
    </Modal>
  );
}
