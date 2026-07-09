import { useState, useEffect } from "react";
import { Modal } from "@/components/Modal";
import { Plus, Loader2, X } from "lucide-react";
import type { ModelSet, Strategy } from "@/lib/mock";
import { STRATEGIES } from "@/lib/mock";
import { useModels } from "@/lib/models";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { ApiModelSearchResult, ApiTemplate } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { MAX_COUNCIL_MODELS, slugToModelId } from "@/lib/modelIds";

export function ModelSetModal({
  open,
  onClose,
  initial,
  onCreate,
  onUpdate,
}: {
  open: boolean;
  onClose: () => void;
  initial?: ModelSet | null;
  onCreate?: (s: ModelSet) => void;
  onUpdate?: (s: ModelSet) => void;
}) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [verdict, setVerdict] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<Strategy>("Synthesize");
  const [custom, setCustom] = useState("");
  const [selectedTemplateName, setSelectedTemplateName] = useState<string | null>(null);
  const [showTemplateModal, setShowTemplateModal] = useState(false);
  const [templateOptions, setTemplateOptions] = useState<ApiTemplate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [modelQuery, setModelQuery] = useState("");
  const [verdictQuery, setVerdictQuery] = useState("");
  const { models, modelById } = useModels();
  const { authHeaders } = useAuth();
  const [orResults, setOrResults] = useState<ApiModelSearchResult[]>([]);
  const [orSearching, setOrSearching] = useState(false);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth || modelQuery.trim().length < 2) {
      setOrResults([]);
      return;
    }
    const handle = window.setTimeout(() => {
      setOrSearching(true);
      void api.models
        .search(auth, modelQuery.trim(), 25)
        .then(setOrResults)
        .catch(() => setOrResults([]))
        .finally(() => setOrSearching(false));
    }, 300);
    return () => window.clearTimeout(handle);
  }, [modelQuery, authHeaders]);

  useEffect(() => {
    if (!showTemplateModal) return;
    const auth = authHeaders();
    if (!auth) return;
    void api.templates
      .list(auth)
      .then(setTemplateOptions)
      .catch(() => setTemplateOptions([]));
  }, [showTemplateModal, authHeaders]);

  useEffect(() => {
    if (initial) {
      setName(initial.name);
      setDesc(initial.description ?? "");
      setPicked(initial.models.slice());
      setVerdict(initial.verdictModel);
      setStrategy(initial.strategy);
      setCustom(initial.customInstructions ?? "");
      setSelectedTemplateName(initial.templateName ?? null);
      setShowTemplateModal(false);
      setError(null);
    } else if (open) {
      setName("");
      setDesc("");
      setPicked([]);
      setVerdict(null);
      setStrategy("Synthesize");
      setCustom("");
      setSelectedTemplateName(null);
      setShowTemplateModal(false);
      setError(null);
    }
  }, [initial, open]);

  function toggle(id: string) {
    setPicked((p) => {
      if (p.includes(id)) return p.filter((x) => x !== id);
      if (p.length >= MAX_COUNCIL_MODELS) {
        setError(`Select up to ${MAX_COUNCIL_MODELS} council models.`);
        return p;
      }
      setError(null);
      return [...p, id];
    });
  }

  function selectTemplate(template: ApiTemplate) {
    setCustom(template.instructions);
    setSelectedTemplateName(template.title);
    setShowTemplateModal(false);
  }

  function removeTemplate() {
    setCustom("");
    setSelectedTemplateName(null);
    setShowTemplateModal(false);
  }

  function submit() {
    if (!name.trim()) {
      setError("Please enter a Model Set name.");
      return;
    }
    if (picked.length === 0) {
      setError("Add at least one answering AI model.");
      return;
    }
    if (!verdict) {
      setError("Choose a Verdict AI.");
      return;
    }
    const payload: ModelSet = {
      id: initial?.id ?? `set-${Date.now()}`,
      name: name.trim(),
      description: desc.trim() || "Custom model set.",
      models: picked,
      verdictModel: verdict,
      strategy,
      bestFor: desc.trim() || "Custom use case",
      templateName: custom.trim() ? (selectedTemplateName ?? "Custom") : undefined,
      customInstructions: custom.trim() || undefined,
    };
    if (initial && onUpdate) onUpdate(payload);
    else if (!initial && onCreate) onCreate(payload);
  }

  if (!open) return null;

  const modelResults = models.filter(
    (m) =>
      modelQuery.trim() === "" ||
      m.name.toLowerCase().includes(modelQuery.toLowerCase()) ||
      m.vendor.toLowerCase().includes(modelQuery.toLowerCase()),
  );
  const verdictResults = models.filter(
    (m) =>
      verdictQuery.trim() === "" ||
      m.name.toLowerCase().includes(verdictQuery.toLowerCase()) ||
      m.vendor.toLowerCase().includes(verdictQuery.toLowerCase()),
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initial ? "Edit Model Set" : "Create Model Set"}
      size="lg"
    >
      <div className="space-y-4">
        {showTemplateModal && (
          <Modal
            open={showTemplateModal}
            onClose={() => setShowTemplateModal(false)}
            title="Choose Template"
            size="md"
          >
            <div className="max-h-64 space-y-2 overflow-y-auto">
              {templateOptions.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  No templates available.
                </p>
              ) : (
                templateOptions.map((template) => (
                  <button
                    key={template.id}
                    type="button"
                    onClick={() => selectTemplate(template)}
                    className="flex w-full flex-col items-start rounded-xl border border-border bg-background px-4 py-3 text-left transition hover:border-primary/40 hover:bg-accent"
                  >
                    <span className="text-sm font-semibold">{template.title}</span>
                    <span className="mt-1 text-sm text-muted-foreground">
                      {template.description}
                    </span>
                  </button>
                ))
              )}
            </div>
          </Modal>
        )}

        <div>
          <label className="block text-sm">
            <div className="mb-1 font-medium">Name</div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <label className="block text-sm mt-3">
            <div className="mb-1 font-medium">
              Description <span className="font-normal text-muted-foreground">(optional)</span>
            </div>
            <textarea
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              rows={2}
              placeholder="What's this set good for?"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
        </div>

        <div>
          <div className="mb-2 font-medium">
            Council models{" "}
            <span className="text-muted-foreground font-normal">(up to {MAX_COUNCIL_MODELS})</span>
          </div>
          <div className="flex gap-2">
            <input
              value={modelQuery}
              onChange={(e) => setModelQuery(e.target.value)}
              placeholder="Search all OpenRouter models…"
              className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
            <button
              type="button"
              disabled={modelResults.length === 0}
              onClick={() => {
                if (modelResults.length > 0) {
                  toggle(modelResults[0].id);
                  setModelQuery("");
                }
              }}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
            >
              <Plus className="size-4" /> Add
            </button>
          </div>
          {modelQuery.trim() !== "" && (
            <div className="rounded-lg border border-border bg-popover p-1 mt-2 shadow-sm">
              {modelResults.slice(0, 12).map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => {
                    toggle(m.id);
                    setModelQuery("");
                  }}
                  className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-accent"
                >
                  <span className="size-2 rounded-full" style={{ background: m.color }} />
                  <span className="font-medium">{m.name}</span>
                  <span className="text-xs text-muted-foreground">{m.vendor}</span>
                </button>
              ))}
              {orSearching && (
                <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" /> Searching OpenRouter…
                </div>
              )}
              {orResults.map((r) => {
                const existing = models.find((m) => m.openrouter_slug === r.openrouter_slug);
                const modelId = existing?.id ?? slugToModelId(r.openrouter_slug);
                const selected = picked.includes(modelId);
                return (
                  <button
                    key={r.openrouter_slug}
                    type="button"
                    disabled={!selected && picked.length >= MAX_COUNCIL_MODELS}
                    onClick={() => {
                      toggle(modelId);
                      if (!verdict) setVerdict(modelId);
                      setModelQuery("");
                      setOrResults([]);
                    }}
                    className="flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-accent"
                  >
                    <div className="min-w-0">
                      <div className="font-medium">{r.name}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {r.openrouter_slug}
                      </div>
                    </div>
                    <span className="shrink-0 text-xs text-primary">
                      {selected ? "Selected" : "Add"}
                    </span>
                  </button>
                );
              })}
              {modelResults.length === 0 && !orSearching && orResults.length === 0 && (
                <div className="px-3 py-2 text-xs text-muted-foreground">No models match.</div>
              )}
            </div>
          )}

          {picked.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {picked.map((id) => {
                const m = modelById(id);
                return (
                  <span
                    key={id}
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-xs"
                  >
                    <span className="size-2 rounded-full" style={{ background: m.color }} />{" "}
                    {m.name}
                    <button
                      type="button"
                      onClick={() => setPicked((p) => p.filter((x) => x !== id))}
                      className="ml-2 text-muted-foreground hover:text-foreground"
                    >
                      ✕
                    </button>
                  </span>
                );
              })}
            </div>
          )}
        </div>

        <div>
          <div className="mb-2 font-medium">Verdict AI</div>
          <div className="flex gap-2">
            <input
              value={verdictQuery}
              onChange={(e) => setVerdictQuery(e.target.value)}
              placeholder="Search AI model..."
              className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
          </div>
          {verdictQuery.trim() !== "" && (
            <div className="rounded-lg border border-border bg-popover p-1 mt-2 shadow-sm">
              {verdictResults.length === 0 ? (
                <div className="px-3 py-2 text-xs text-muted-foreground">No models match.</div>
              ) : (
                verdictResults.slice(0, 6).map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => {
                      setVerdict(m.id);
                      setVerdictQuery("");
                    }}
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-accent"
                  >
                    <span className="size-2 rounded-full" style={{ background: m.color }} />
                    <span className="font-medium">{m.name}</span>
                    <span className="text-xs text-muted-foreground">{m.vendor}</span>
                  </button>
                ))
              )}
            </div>
          )}
          {verdict && (
            <div className="mt-2 inline-flex items-center gap-2 rounded-full border border-border bg-background px-3 py-1.5 text-sm">
              <span
                className="size-2 rounded-full"
                style={{ background: modelById(verdict).color }}
              />
              <span className="font-medium">{modelById(verdict).name}</span>
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 font-medium">Strategy</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {STRATEGIES.map((s) => (
              <button
                key={s.name}
                type="button"
                onClick={() => setStrategy(s.name)}
                className={cn(
                  "rounded-2xl border px-3 py-3 text-left text-sm transition",
                  strategy === s.name
                    ? "border-primary bg-primary/5 text-primary"
                    : "border-border bg-background hover:border-primary/40 hover:bg-accent",
                )}
              >
                <div className="font-medium">{s.name}</div>
                <div className="mt-1 text-xs text-muted-foreground">{s.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1 font-medium">
            Custom Verdict Instructions{" "}
            <span className="font-normal text-muted-foreground">(Optional)</span>
          </div>
          <textarea
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            rows={3}
            placeholder="Example: Choose the answer that is easiest for a beginner to understand."
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
          <div className="mt-3">
            <div className="text-sm font-medium">Template</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {!selectedTemplateName ? (
                <button
                  type="button"
                  onClick={() => setShowTemplateModal(true)}
                  className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm font-medium hover:bg-accent"
                >
                  Choose Template
                </button>
              ) : (
                <div className="inline-flex items-center gap-2 rounded-full border border-border bg-background px-3 py-1.5 text-sm">
                  <span className="font-medium">{selectedTemplateName}</span>
                  <button
                    type="button"
                    onClick={removeTemplate}
                    className="rounded-full p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                    aria-label={`Remove ${selectedTemplateName}`}
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {error && <div className="text-sm text-destructive">{error}</div>}

        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            {initial ? "Save" : "Create Model Set"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export default ModelSetModal;
