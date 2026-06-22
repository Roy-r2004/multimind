import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Plus, Copy, Pencil, Trash2, Gavel, LayoutGrid, Check } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { CreateModelSetModal } from "@/components/CreateModelSetModal";
import { MODEL_SETS, modelById, type ModelSet } from "@/lib/mock";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/model-sets")({
  head: () => ({ meta: [{ title: "Model Sets — MultiAI" }] }),
  component: ModelSetsPage,
});

function ModelSetsPage() {
  const navigate = useNavigate();
  const [sets, setSets] = useState<ModelSet[]>(MODEL_SETS);
  const [activeId, setActiveId] = useState("balanced");
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<ModelSet | null>(null);

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Model Sets</h1>
            <p className="mt-1 text-sm text-muted-foreground">Curated bundles of models plus a Verdict AI strategy.</p>
          </div>
          <button
            onClick={() => { setEditing(null); setShowCreate(true); }}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            <Plus className="size-4" /> Create New Model Set
          </button>
        </div>

        {sets.length === 0 ? (
          <EmptyState onCreate={() => { setEditing(null); setShowCreate(true); }} />
        ) : (
          <div className="mt-8 grid gap-4 md:grid-cols-2">
            {sets.map((s) => {
              const active = s.id === activeId;
              return (
                <div key={s.id} className={cn("rounded-2xl border bg-card p-5", active ? "border-primary" : "border-border")}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <div className="font-medium">{s.name}</div>
                        {active && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-primary">
                            <Check className="size-3" /> Active
                          </span>
                        )}
                      </div>
                      <div className="mt-1 text-sm text-muted-foreground">{s.description}</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <button onClick={() => { setEditing(s); setShowCreate(true); }} className="rounded-md p-2 hover:bg-accent" title="Edit"><Pencil className="size-4" /></button>
                      <button
                        onClick={() => { setSets((arr) => [...arr, { ...s, id: s.id + "-copy-" + Date.now().toString(36), name: s.name + " (copy)" }]); toast.success("Duplicated"); }}
                        className="rounded-md p-2 hover:bg-accent" title="Duplicate"
                      ><Copy className="size-4" /></button>
                      <button
                        onClick={() => { setSets((arr) => arr.filter((x) => x.id !== s.id)); toast.success(`${s.name} deleted`); }}
                        className="rounded-md p-2 text-destructive hover:bg-destructive/10" title="Delete"
                      ><Trash2 className="size-4" /></button>
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {s.models.map((id) => {
                      const m = modelById(id);
                      return (
                        <span key={id} className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-xs">
                          <span className="size-1.5 rounded-full" style={{ background: m.color }} /> {m.name}
                        </span>
                      );
                    })}
                  </div>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <div className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Gavel className="size-3.5" /> Verdict: <span className="text-foreground">{s.strategy}</span> · {modelById(s.verdictModel).name}
                    </div>
                    <button
                      onClick={() => { setActiveId(s.id); toast.success(`${s.name} is now active.`); navigate({ to: "/" }); }}
                      disabled={active}
                      className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
                    >
                      {active ? "In use" : "Use set"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <CreateModelSetModal
        open={showCreate}
        onClose={() => { setShowCreate(false); setEditing(null); }}
        initial={editing ?? undefined}
        onCreate={(newSet) => {
          if (editing) {
            setSets((arr) => arr.map((s) => (s.id === editing.id ? { ...newSet, id: editing.id } : s)));
            toast.success(`${newSet.name} updated.`);
          } else {
            setSets((arr) => [...arr, newSet]);
            setActiveId(newSet.id);
            toast.success(`${newSet.name} is now active.`);
          }
          setShowCreate(false);
          setEditing(null);
        }}
      />
    </AppShell>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="mt-12 grid place-items-center rounded-2xl border border-dashed border-border bg-card p-12 text-center">
      <div className="grid size-12 place-items-center rounded-xl bg-accent text-accent-foreground"><LayoutGrid className="size-5" /></div>
      <h3 className="mt-4 font-semibold">No Model Sets yet</h3>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">Create your first set to start asking multiple models in one go.</p>
      <button onClick={onCreate} className="mt-4 inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"><Plus className="size-4" /> New Model Set</button>
    </div>
  );
}
