import { useState, useEffect } from "react";
import { Modal } from "@/components/Modal";
import { Plus, Check } from "lucide-react";
import type { ModelSet, Strategy } from "@/lib/mock";
import { MODELS, STRATEGIES, TEMPLATES, modelById } from "@/lib/mock";
import { cn } from "@/lib/utils";

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
  const [showTemplateMenu, setShowTemplateMenu] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modelQuery, setModelQuery] = useState("");
  const [verdictQuery, setVerdictQuery] = useState("");

  useEffect(() => {
    if (initial) {
      setName(initial.name);
      setDesc(initial.description ?? "");
      setPicked(initial.models.slice());
      setVerdict(initial.verdictModel);
      setStrategy(initial.strategy);
      setCustom(initial.customInstructions ?? "");
      setSelectedTemplateName(initial.templateName ?? null);
      setError(null);
    } else if (open) {
      setName("");
      setDesc("");
      setPicked([]);
      setVerdict(null);
      setStrategy("Synthesize");
      setCustom("");
      setSelectedTemplateName(null);
      setShowTemplateMenu(false);
      setError(null);
    }
  }, [initial, open]);

  function toggle(id: string) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
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
      templateName: custom.trim() ? selectedTemplateName ?? "Custom" : undefined,
      customInstructions: custom.trim() || undefined,
    };
    if (initial && onUpdate) onUpdate(payload);
    else if (!initial && onCreate) onCreate(payload);
    // keep modal open state controlled by parent; parent should close
  }

  if (!open) return null;

  const modelResults = MODELS.filter((m) => modelQuery.trim() === '' || m.name.toLowerCase().includes(modelQuery.toLowerCase()) || m.vendor.toLowerCase().includes(modelQuery.toLowerCase()));
  const verdictResults = MODELS.filter((m) => verdictQuery.trim() === '' || m.name.toLowerCase().includes(verdictQuery.toLowerCase()) || m.vendor.toLowerCase().includes(verdictQuery.toLowerCase()));

  return (
    <Modal open={open} onClose={onClose} title={initial ? "Edit Model Set" : "Create Model Set"} size="lg">
      <div className="space-y-4">
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
            <div className="mb-1 font-medium">Description <span className="font-normal text-muted-foreground">(optional)</span></div>
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
          <div className="mb-2 font-medium">Models</div>
          <div className="flex gap-2">
            <input
              value={modelQuery}
              onChange={(e) => setModelQuery(e.target.value)}
              placeholder="Search AI models..."
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
              {modelResults.length === 0 ? (
                <div className="px-3 py-2 text-xs text-muted-foreground">No models match.</div>
              ) : (
                modelResults.slice(0, 6).map((m) => (
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
                ))
              )}
            </div>
          )}

          {picked.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {picked.map((id) => {
                const m = modelById(id);
                return (
                  <span key={id} className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-xs">
                    <span className="size-2 rounded-full" style={{ background: m.color }} /> {m.name}
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
              <span className="size-2 rounded-full" style={{ background: modelById(verdict).color }} />
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
                  "group rounded-2xl border px-3 py-3 text-left text-sm transition",
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
          <div className="mb-1 font-medium">Custom Verdict Instructions <span className="font-normal text-muted-foreground">(Optional)</span></div>
          <div className="mb-2 text-sm text-muted-foreground">
            Customize how the Verdict AI should judge the answers.
          </div>
          <textarea
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            rows={3}
            placeholder="Example: Choose the answer that is easiest for a beginner to understand. Focus on clarity, accuracy, and practical usefulness."
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
          <div className="mt-1 text-xs text-muted-foreground">
            If filled, these custom instructions override the selected strategy.
          </div>

          <div className="mt-3">
            <div className="mb-2 flex items-center justify-between gap-2 text-sm font-medium">
              <span>Template</span>
              <button
                type="button"
                onClick={() => setShowTemplateMenu((open) => !open)}
                className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm hover:bg-accent"
              >
                Choose Template
+                {selectedTemplateName ? `: ${selectedTemplateName}` : ""}
              </button>
            </div>
            {showTemplateMenu && (
              <div className="rounded-lg border border-border bg-popover p-2 shadow-sm">
                {TEMPLATES.map((template) => (
                  <button
                    key={template.id}
                    type="button"
                    onClick={() => {
                      setCustom(template.instructions);
                      setSelectedTemplateName(template.title);
                      setShowTemplateMenu(false);
                    }}
                    className="flex w-full flex-col items-start gap-1 rounded-lg px-3 py-2 text-left text-sm hover:bg-accent"
                  >
                    <span className="font-medium">{template.title}</span>
                    <span className="text-xs text-muted-foreground">{template.description}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {error && <div className="text-sm text-destructive">{error}</div>}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent">Cancel</button>
          <button onClick={submit} className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">{initial ? 'Save' : 'Create Model Set'}</button>
        </div>
      </div>
    </Modal>
  );
}

export default ModelSetModal;
