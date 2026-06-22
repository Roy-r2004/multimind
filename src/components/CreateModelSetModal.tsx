import { useEffect, useState } from "react";
import { AlertCircle, Check, Gavel, Sparkles } from "lucide-react";
import { Modal } from "@/components/Modal";
import { MODELS, STRATEGIES, type ModelSet, type Strategy } from "@/lib/mock";
import { cn } from "@/lib/utils";

const BEST_FOR: Record<string, string> = {
  "gpt-4.1": "Best for reasoning",
  claude: "Best for writing",
  gemini: "Best for research",
  deepseek: "Best for coding",
  mistral: "Fast and lightweight",
  llama: "Open-source option",
  perplex: "Web-grounded answers",
};

export function CreateModelSetModal({
  open,
  onClose,
  onCreate,
  initial,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (set: ModelSet) => void;
  initial?: Partial<ModelSet>;
}) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [verdictModel, setVerdictModel] = useState("gpt-4.1");
  const [strategy, setStrategy] = useState<Strategy>("Synthesize");
  const [custom, setCustom] = useState("");

  useEffect(() => {
    if (open) {
      setName(initial?.name ?? "");
      setDesc(initial?.description ?? "");
      setPicked(initial?.models ?? []);
      setVerdictModel(initial?.verdictModel ?? "gpt-4.1");
      setStrategy(initial?.strategy ?? "Synthesize");
      setCustom("");
    }
  }, [open, initial]);

  const canSave = name.trim().length > 0 && picked.length >= 2 && !!verdictModel && !!strategy;

  function toggle(id: string) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }

  function handleCreate() {
    if (!canSave) return;
    onCreate({
      id: (initial?.id ?? "custom-") + Date.now().toString(36),
      name: name.trim(),
      description: desc.trim() || "Custom Model Set",
      models: picked,
      verdictModel,
      strategy,
    });
  }

  return (
    <Modal open={open} onClose={onClose} title={initial?.id ? "Edit Model Set" : "Create Model Set"} size="xl">
      <div className="grid gap-6 md:grid-cols-[1fr,18rem]">
        <div className="space-y-6">
          {/* Step 1 */}
          <Step n={1} title="Basic info">
            <label className="block text-sm">
              <div className="mb-1 font-medium">Model Set name</div>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Example: Coding Set"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40"
              />
            </label>
            <label className="block text-sm">
              <div className="mb-1 font-medium">Description</div>
              <textarea
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                rows={2}
                placeholder="Best for coding, debugging, and technical explanations."
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40"
              />
            </label>
          </Step>

          {/* Step 2 */}
          <Step n={2} title="Choose AI models" hint={`${picked.length} model${picked.length === 1 ? "" : "s"} selected`}>
            <div className="grid gap-2 sm:grid-cols-2">
              {MODELS.filter((m) => BEST_FOR[m.id]).map((m) => {
                const on = picked.includes(m.id);
                return (
                  <button
                    key={m.id}
                    onClick={() => toggle(m.id)}
                    className={cn(
                      "flex items-center gap-3 rounded-xl border p-3 text-left transition",
                      on ? "border-primary bg-accent/40" : "border-border hover:bg-accent",
                    )}
                  >
                    <span className="size-2.5 shrink-0 rounded-full" style={{ background: m.color }} />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium">{m.name}</div>
                      <div className="text-xs text-muted-foreground">{BEST_FOR[m.id]}</div>
                    </div>
                    <div className={cn("grid size-5 place-items-center rounded-md border", on ? "border-primary bg-primary text-primary-foreground" : "border-border")}>
                      {on && <Check className="size-3" />}
                    </div>
                  </button>
                );
              })}
            </div>
          </Step>

          {/* Step 3 */}
          <Step n={3} title="Verdict AI settings">
            <label className="block text-sm">
              <div className="mb-1 font-medium">Verdict AI model</div>
              <select
                value={verdictModel}
                onChange={(e) => setVerdictModel(e.target.value)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              >
                {MODELS.filter((m) => BEST_FOR[m.id]).map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </label>
            <div className="text-sm">
              <div className="mb-1 font-medium">Verdict strategy</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {STRATEGIES.map((s) => (
                  <button
                    key={s.name}
                    onClick={() => setStrategy(s.name)}
                    className={cn(
                      "rounded-xl border p-3 text-left transition",
                      strategy === s.name ? "border-primary bg-accent/40" : "border-border hover:bg-accent",
                    )}
                  >
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <Gavel className="size-3.5 text-primary" /> {s.name}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">{s.desc}</div>
                  </button>
                ))}
              </div>
            </div>
          </Step>

          {/* Step 4 */}
          <Step n={4} title="Custom Verdict instructions" hint="optional">
            <textarea
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              rows={3}
              placeholder="Example: Choose the answer that is easiest for a beginner to understand."
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40"
            />
            <p className="mt-1 text-xs text-muted-foreground">Tell the Verdict AI how to judge the answers.</p>
            {custom.trim() && (
              <div className="mt-3 flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
                <AlertCircle className="mt-0.5 size-4 text-warning" />
                <div><strong>Custom instructions override the selected strategy.</strong></div>
              </div>
            )}
          </Step>
        </div>

        {/* Preview */}
        <aside className="space-y-3 md:sticky md:top-0 md:self-start">
          <div className="rounded-2xl border border-primary/30 bg-primary/5 p-4">
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-primary">
              <Sparkles className="size-3.5" /> Live preview
            </div>
            <div className="mt-2 text-lg font-semibold">{name.trim() || "Untitled Set"}</div>
            <div className="mt-1 text-xs text-muted-foreground">{desc.trim() || "Add a description…"}</div>

            <div className="mt-4 text-xs font-medium uppercase tracking-wider text-muted-foreground">Models</div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {picked.length === 0 && <span className="text-xs text-muted-foreground">Pick at least 2</span>}
              {picked.map((id) => {
                const m = MODELS.find((x) => x.id === id)!;
                return (
                  <span key={id} className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-xs">
                    <span className="size-1.5 rounded-full" style={{ background: m.color }} /> {m.name}
                  </span>
                );
              })}
            </div>

            <div className="mt-3 text-xs"><span className="text-muted-foreground">Verdict AI: </span><span className="font-medium">{MODELS.find((m) => m.id === verdictModel)?.name}</span></div>
            <div className="mt-1 text-xs"><span className="text-muted-foreground">Strategy: </span><span className="font-medium">{strategy}</span></div>
            <div className="mt-1 text-xs"><span className="text-muted-foreground">Custom instructions: </span><span className={cn("font-medium", custom.trim() ? "text-primary" : "")}>{custom.trim() ? "Active" : "None"}</span></div>
          </div>

          <div className="flex flex-col gap-2">
            <button
              onClick={handleCreate}
              disabled={!canSave}
              className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {initial?.id ? "Save changes" : "Create Model Set"}
            </button>
            <button onClick={onClose} className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent">Cancel</button>
            {!canSave && (
              <p className="text-xs text-muted-foreground">Need a name, at least 2 models, a Verdict AI, and a strategy.</p>
            )}
          </div>
        </aside>
      </div>
    </Modal>
  );
}

function Step({ n, title, hint, children }: { n: number; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="grid size-6 place-items-center rounded-full bg-primary/15 text-xs font-semibold text-primary">{n}</span>
        <h3 className="font-medium">{title}</h3>
        {hint && <span className="ml-auto text-xs text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </section>
  );
}
