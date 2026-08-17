import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import {
  Plus,
  Pencil,
  Trash2,
  Search,
  Scale,
  Gavel,
  Code2,
  Briefcase,
  FlaskConical,
  Layers,
  Sparkles,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { VendorLogo } from "@/components/chat/VendorLogo";
import { GlassCard } from "@/components/cinematic/PageChrome";
import ModelSetModal from "@/components/ModelSetModal";
import { Modal } from "@/components/Modal";
import { useChatStore } from "@/lib/store";
import { useModels } from "@/lib/models";
import { cn } from "@/lib/utils";
import {
  clonedSystemModelSetName,
  shouldCloneSystemModelSet,
} from "@/lib/modelSetEdit";

export const Route = createFileRoute("/model-sets")({
  head: () => ({ meta: [{ title: "Model Sets — MultiAI" }] }),
  component: ModelSetsPage,
});

const SYSTEM_MODEL_SETS = new Set([
  "referee",
  "set-7edaefc8",
  "balanced",
  "coding",
  "business",
  "research",
]);

const SET_ICONS: Record<string, typeof Scale> = {
  referee: Gavel,
  balanced: Scale,
  coding: Code2,
  business: Briefcase,
  research: FlaskConical,
};

const SET_ICON_COLORS: Record<string, string> = {
  referee: "from-sky-500 to-blue-600",
  balanced: "from-cyan-500 to-sky-600",
  coding: "from-emerald-500 to-teal-600",
  business: "from-amber-500 to-orange-600",
  research: "from-indigo-500 to-blue-600",
};

function ModelSetsPage() {
  const { modelSets, createModelSet, updateModelSet, deleteModelSet } = useChatStore();
  const { modelById } = useModels();
  const [editing, setEditing] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return modelSets;
    return modelSets.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.strategy.toLowerCase().includes(q),
    );
  }, [modelSets, query]);

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <div className="elevate-hero mb-8 text-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-primary">
            04 — Model Sets
          </p>
          <h1 className="mt-3 font-display text-4xl tracking-tight md:text-5xl">
            Curated councils.{" "}
            <span className="text-gradient italic">Infinite potential.</span>
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm text-muted-foreground">
            Pre-built model combinations for every mission.
          </p>
        </div>

        <GlassCard glow className="p-5 md:p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search model sets..."
                className="w-full rounded-full border border-border bg-background py-2.5 pr-4 pl-10 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
              />
            </div>
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
            >
              <Plus className="size-4" />
              New set
            </button>
          </div>

          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {filtered.map((s, index) => {
              const isSystemSet = SYSTEM_MODEL_SETS.has(s.id);
              const Icon = SET_ICONS[s.id] ?? Layers;
              const iconGrad = SET_ICON_COLORS[s.id] ?? "from-sky-500 to-blue-600";
              const isReferee =
                s.id === "referee" || s.name.toLowerCase().includes("referee");
              const verdict = modelById(s.verdictModel);

              return (
                <GlassCard
                  key={s.id}
                  featured={isReferee}
                  className={cn(
                    "elevate-card p-5",
                    index > 0 && `elevate-card-delay-${Math.min(index, 4)}`,
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-3">
                      <span
                        className={cn(
                          "grid size-11 shrink-0 place-items-center rounded-xl bg-gradient-to-br text-white shadow-md",
                          iconGrad,
                        )}
                      >
                        <Icon className="size-5" />
                      </span>
                      <div className="min-w-0">
                        <div className="font-medium">{s.name}</div>
                        <p className="mt-1 text-sm text-muted-foreground">{s.description}</p>
                      </div>
                    </div>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => setEditing(s.id)}
                        title={
                          shouldCloneSystemModelSet(s.id, SYSTEM_MODEL_SETS)
                            ? "Customize set"
                            : "Edit set"
                        }
                        className="rounded-lg p-2 hover:bg-accent"
                      >
                        <Pencil className="size-4" />
                      </button>
                      {!isSystemSet && (
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(s.id)}
                          title="Delete set"
                          className="rounded-lg p-2 text-destructive hover:bg-destructive/10"
                        >
                          <Trash2 className="size-4" />
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="mt-4 space-y-2">
                    {s.models.map((id) => {
                      const model = modelById(id);
                      return (
                        <div key={id} className="flex items-center gap-2.5 text-sm">
                          <VendorLogo
                            vendor={model.vendor}
                            title={model.name}
                            className="size-6"
                          />
                          <span className="text-muted-foreground">{model.vendor}:</span>
                          <span className="truncate font-medium">{model.name}</span>
                        </div>
                      );
                    })}
                  </div>

                  <div className="mt-4 flex items-center gap-2 border-t border-border/70 pt-3 text-xs text-muted-foreground">
                    <Sparkles className="size-3.5 text-primary" />
                    <span>
                      {s.strategy} · Verdict: {verdict.vendor}: {verdict.name}
                    </span>
                    <VendorLogo vendor={verdict.vendor} className="ml-auto size-5" />
                  </div>
                </GlassCard>
              );
            })}
          </div>
        </GlassCard>

        <ModelSetModal
          open={showCreate || !!editing}
          onClose={() => {
            setShowCreate(false);
            setEditing(null);
          }}
          initial={modelSets.find((modelSet) => modelSet.id === editing) ?? null}
          onCreate={async (modelSet) => {
            await createModelSet(modelSet);
            setShowCreate(false);
          }}
          onUpdate={async (modelSet) => {
            if (shouldCloneSystemModelSet(modelSet.id, SYSTEM_MODEL_SETS)) {
              await createModelSet({
                ...modelSet,
                name: clonedSystemModelSetName(modelSet.name),
              });
            } else {
              await updateModelSet(modelSet);
            }
            setEditing(null);
          }}
        />

        <Modal
          open={!!deleteTarget}
          onClose={() => setDeleteTarget(null)}
          title="Delete set?"
          size="sm"
        >
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setDeleteTarget(null)}
              className="rounded-lg border border-border px-4 py-2 text-sm"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                if (deleteTarget) void deleteModelSet(deleteTarget);
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
