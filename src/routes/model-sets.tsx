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

export const Route = createFileRoute("/model-sets")({
  head: () => ({ meta: [{ title: "Model Sets — MultiAI" }] }),
  component: ModelSetsPage,
});

const SYSTEM_MODEL_SETS = new Set(["referee", "balanced", "coding", "business", "research"]);

const SET_ICONS: Record<string, typeof Scale> = {
  referee: Gavel,
  balanced: Scale,
  coding: Code2,
  business: Briefcase,
  research: FlaskConical,
};

const SET_ICON_COLORS: Record<string, string> = {
  referee: "from-violet-500 to-fuchsia-500",
  balanced: "from-sky-400 to-blue-500",
  coding: "from-emerald-400 to-teal-500",
  business: "from-amber-400 to-orange-500",
  research: "from-cyan-400 to-indigo-500",
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
        <div className="mb-8 text-center">
          <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-sky-300/90">
            04 — Model Sets
          </p>
          <h1 className="mt-3 font-display text-4xl tracking-tight text-white md:text-5xl">
            Curated councils.{" "}
            <span className="text-gradient italic">Infinite potential.</span>
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm text-slate-300/80">
            Pre-built model combinations for every mission.
          </p>
        </div>

        <GlassCard variant="council" className="p-5 md:p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search model sets..."
                className="w-full rounded-full border border-white/15 bg-white/5 py-2.5 pr-4 pl-10 text-sm text-white outline-none placeholder:text-slate-500 focus:border-sky-300/50 focus:ring-2 focus:ring-sky-400/20"
              />
            </div>
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="council-glass-cta inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium"
            >
              <Plus className="size-4" />
              New set
            </button>
          </div>

          <div className="mt-6 grid gap-4 md:grid-cols-2">
            {filtered.map((s) => {
              const isSystemSet = SYSTEM_MODEL_SETS.has(s.id);
              const Icon = SET_ICONS[s.id] ?? Layers;
              const iconGrad = SET_ICON_COLORS[s.id] ?? "from-sky-400 to-violet-500";
              const isReferee = s.id === "referee" || s.name.toLowerCase().includes("referee");

              return (
                <GlassCard
                  key={s.id}
                  variant="council"
                  className={cn(
                    "p-5",
                    isReferee && "ring-2 ring-sky-300/60 shadow-[0_0_36px_rgb(56_189_248_/_0.22)]",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-3">
                      <span
                        className={cn(
                          "grid size-11 shrink-0 place-items-center rounded-xl bg-gradient-to-br text-white shadow-lg",
                          iconGrad,
                        )}
                      >
                        <Icon className="size-5" />
                      </span>
                      <div className="min-w-0">
                        <div className="font-medium text-white">{s.name}</div>
                        <p className="mt-1 text-sm text-slate-400">{s.description}</p>
                      </div>
                    </div>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => setEditing(s.id)}
                        title={isSystemSet ? "Customize set" : "Edit set"}
                        className="rounded-lg p-2 text-slate-300 hover:bg-white/10"
                      >
                        <Pencil className="size-4" />
                      </button>
                      {!isSystemSet && (
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(s.id)}
                          title="Delete set"
                          className="rounded-lg p-2 text-rose-300 hover:bg-rose-500/10"
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
                        <div
                          key={id}
                          className="flex items-center gap-2.5 text-sm text-slate-200"
                        >
                          <VendorLogo vendor={model.vendor} className="size-6" />
                          <span className="text-slate-400">{model.vendor}:</span>
                          <span className="truncate font-medium text-white">{model.name}</span>
                        </div>
                      );
                    })}
                  </div>

                  <div className="mt-4 flex items-center gap-1.5 border-t border-white/10 pt-3 text-xs text-slate-300">
                    <Sparkles className="size-3.5 text-sky-300" />
                    {s.strategy} · Verdict: {modelById(s.verdictModel).vendor}:{" "}
                    {modelById(s.verdictModel).name}
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
            if (SYSTEM_MODEL_SETS.has(modelSet.id)) {
              await createModelSet({
                ...modelSet,
                name: modelSet.name.startsWith("My ") ? modelSet.name : `My ${modelSet.name}`,
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
              className="rounded-lg border border-white/15 px-4 py-2 text-sm text-slate-200"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                if (deleteTarget) {
                  void deleteModelSet(deleteTarget);
                }
                setDeleteTarget(null);
              }}
              className="rounded-lg bg-rose-500 px-4 py-2 text-sm text-white"
            >
              Delete
            </button>
          </div>
        </Modal>
      </div>
    </AppShell>
  );
}
