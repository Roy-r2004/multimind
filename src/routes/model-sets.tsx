import { createFileRoute, Link } from "@tanstack/react-router";
import { Plus, Copy, Pencil, Trash2, Gavel, LayoutGrid } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { useState } from "react";
import ModelSetModal from "@/components/ModelSetModal";
import { Modal } from "@/components/Modal";
import { useChatStore } from "@/lib/store";
import { modelById } from "@/lib/mock";
function ModelSetsPage() {
  const { modelSets, createModelSet, updateModelSet, deleteModelSet } = useChatStore();
  const [editing, setEditing] = useState<null | string>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<null | string>(null);

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Model Sets</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Curated bundles of models plus a Verdict AI strategy.
            </p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            <Plus className="size-4" /> New Model Set
          </button>
        </div>

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          {modelSets.map((s) => (
            <div key={s.id} className="rounded-2xl border border-border bg-card p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-medium">{s.name}</div>
                  <div className="mt-1 text-sm text-muted-foreground">{s.description}</div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => setEditing(s.id)}
                    className="rounded-md p-2 hover:bg-accent"
                    title="Edit"
                  >
                    <Pencil className="size-4" />
                  </button>
                  <button className="rounded-md p-2 hover:bg-accent" title="Duplicate">
                    <Copy className="size-4" />
                  </button>
                  <button
                    onClick={() => setDeleteTarget(s.id)}
                    className="rounded-md p-2 text-destructive hover:bg-destructive/10"
                    title="Delete"
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
                      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-xs"
                    >
                      <span className="size-1.5 rounded-full" style={{ background: m.color }} />{" "}
                      {m.name}
                    </span>
                  );
                })}
              </div>
              <div className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <Gavel className="size-3.5" /> Verdict:{" "}
                <span className="text-foreground">{s.strategy}</span> via{" "}
                {modelById(s.verdictModel).name}
              </div>
              {s.templateName && (
                <div className="mt-2 text-xs text-muted-foreground">
                  Template: <span className="text-foreground">{s.templateName}</span>
                </div>
              )}
            </div>
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
        <Modal
          open={!!deleteTarget}
          onClose={() => setDeleteTarget(null)}
          title="Delete Model Set?"
          size="sm"
        >
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Are you sure you want to delete this model set? This will fall back to the Balanced
              set if it was active.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setDeleteTarget(null)}
                className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (deleteTarget) deleteModelSet(deleteTarget);
                  setDeleteTarget(null);
                }}
                className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:opacity-90"
              >
                Delete
              </button>
            </div>
          </div>
        </Modal>
      </div>
    </AppShell>
  );
}

function EmptyState() {
  return (
    <div className="mt-12 grid place-items-center rounded-2xl border border-dashed border-border bg-card p-12 text-center">
      <div className="grid size-12 place-items-center rounded-xl bg-accent text-accent-foreground">
        <LayoutGrid className="size-5" />
      </div>
      <h3 className="mt-4 font-semibold">No Model Sets yet</h3>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">
        Create your first set to start asking multiple models in one go.
      </p>
      <Link
        to="/model-sets/new"
        className="mt-4 inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
      >
        <Plus className="size-4" /> New Model Set
      </Link>
    </div>
  );
}
