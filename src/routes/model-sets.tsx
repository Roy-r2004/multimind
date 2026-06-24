import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Plus, Pencil, Trash2, Gavel } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import ModelSetModal from "@/components/ModelSetModal";
import { Modal } from "@/components/Modal";
import { useChatStore } from "@/lib/store";
import { useModels } from "@/lib/models";

export const Route = createFileRoute("/model-sets")({
  head: () => ({ meta: [{ title: "Model Sets — MultiAI" }] }),
  component: ModelSetsPage,
});

function ModelSetsPage() {
  const { modelSets, createModelSet, updateModelSet, deleteModelSet } = useChatStore();
  const { modelById } = useModels();
  const [editing, setEditing] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <PageHeader
          eyebrow="Orchestration"
          title="Model sets"
          description="Curated councils of frontier models plus a Verdict AI strategy."
          action={
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-[0_0_24px_-4px] shadow-primary/50"
            >
              <Plus className="size-4" /> New set
            </button>
          }
        />

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          {modelSets.map((s) => (
            <GlassCard key={s.id} className="p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{s.name}</div>
                  <p className="mt-1 text-sm text-muted-foreground">{s.description}</p>
                </div>
                <div className="flex gap-1">
                  <button onClick={() => setEditing(s.id)} className="rounded-lg p-2 hover:bg-white/5">
                    <Pencil className="size-4" />
                  </button>
                  <button
                    onClick={() => setDeleteTarget(s.id)}
                    className="rounded-lg p-2 text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {s.models.map((id) => {
                  const m = modelById(id);
                  return (
                    <span
                      key={id}
                      className="inline-flex items-center gap-1.5 rounded-full border border-white/10 px-2.5 py-1 text-xs"
                    >
                      <span className="size-1.5 rounded-full" style={{ background: m.color }} />
                      {m.name}
                    </span>
                  );
                })}
              </div>
              <div className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
                <Gavel className="size-3.5" />
                {s.strategy} · Verdict: {modelById(s.verdictModel).name}
              </div>
            </GlassCard>
          ))}
        </div>

        <ModelSetModal
          open={showCreate || !!editing}
          onClose={() => {
            setShowCreate(false);
            setEditing(null);
          }}
          initial={modelSets.find((m) => m.id === editing) ?? null}
          onCreate={(s) => {
            createModelSet(s);
            setShowCreate(false);
          }}
          onUpdate={(s) => {
            updateModelSet(s);
            setEditing(null);
          }}
        />

        <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="Delete set?" size="sm">
          <div className="flex justify-end gap-2">
            <button onClick={() => setDeleteTarget(null)} className="rounded-lg border border-white/10 px-4 py-2 text-sm">
              Cancel
            </button>
            <button
              onClick={() => {
                if (deleteTarget) deleteModelSet(deleteTarget);
                setDeleteTarget(null);
              }}
              className="rounded-lg bg-destructive px-4 py-2 text-sm text-destructive-foreground"
            >
              Delete
            </button>
          </div>
        </Modal>
      </div>
    </AppShell>
  );
}
