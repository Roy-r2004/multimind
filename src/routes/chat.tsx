import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  Plus,
  Send,
  Copy,
  Gavel,
  ChevronDown,
  Wand2,
  Link2,
  LayoutTemplate,
  FileSpreadsheet,
  Upload,
  Image as ImageIcon,
  X,
  Loader2,
  AlertCircle,
  Info,
  Share2,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import {
  MODEL_SETS,
  MODELS,
  SAMPLE_ANSWERS,
  SAMPLE_CHATS,
  STRATEGIES,
  TEMPLATES,
  VERDICT,
  modelById,
} from "@/lib/mock";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/chat")({
  head: () => ({ meta: [{ title: "Chat — MultiAI" }] }),
  component: ChatPage,
});

type Msg = { role: "user" | "ai"; question?: string };

export function ChatPage() {
  const [setId, setSetId] = useState("balanced");
  const set = MODEL_SETS.find((s) => s.id === setId)!;
  const [messages, setMessages] = useState<Msg[]>([
    { role: "user", question: "What's the best framework for a fast SaaS landing page in 2026?" },
    { role: "ai" },
  ]);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<{ name: string; state: "uploading" | "uploaded" | "error" }[]>([]);
  const [refChat, setRefChat] = useState<string | null>(null);
  const [showSet, setShowSet] = useState(false);
  const [showStrategy, setShowStrategy] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [showRef, setShowRef] = useState(false);
  const [showExcel, setShowExcel] = useState(false);
  const [showPlus, setShowPlus] = useState(false);
  const [loading, setLoading] = useState(false);

  function send() {
    if (!input.trim()) return;
    setMessages((m) => [...m, { role: "user", question: input }]);
    setInput("");
    setLoading(true);
    setTimeout(() => {
      setMessages((m) => [...m, { role: "ai" }]);
      setLoading(false);
    }, 1600);
  }

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-3.5rem)] md:h-screen flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-border bg-background/80 px-4 md:px-6 py-3 backdrop-blur">
          <button
            onClick={() => setShowSet(true)}
            className="group flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-1.5 text-sm font-medium hover:bg-accent"
          >
            <span className="grid size-5 place-items-center rounded-md bg-primary/15 text-primary"><Gavel className="size-3" /></span>
            {set.name}
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </button>
          <div className="hidden sm:flex items-center gap-1.5">
            {set.models.map((id) => {
              const m = modelById(id);
              return <span key={id} className="size-2.5 rounded-full" style={{ background: m.color }} title={m.name} />;
            })}
            <span className="ml-2 text-xs text-muted-foreground">· Verdict: {set.strategy}</span>
            <button onClick={() => setShowStrategy(true)} className="ml-1 text-muted-foreground hover:text-foreground"><Info className="size-3.5" /></button>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Link to="/shared" className="hidden md:inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs hover:bg-accent">
              <Share2 className="size-3.5" /> Share
            </Link>
          </div>
        </div>

        {/* Conversation */}
        <div className="flex-1 overflow-y-auto px-4 md:px-6 py-6">
          <div className="mx-auto max-w-4xl space-y-8">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm text-primary-foreground">{m.question}</div>
                </div>
              ) : (
                <AiTurn key={i} set={set} />
              ),
            )}
            {loading && <LoadingTurn set={set} />}
          </div>
        </div>

        {/* Composer */}
        <div className="border-t border-border bg-background px-4 md:px-6 py-4">
          <div className="mx-auto max-w-4xl">
            {refChat && (
              <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-border bg-accent/40 px-3 py-1 text-xs">
                <Link2 className="size-3" /> Referencing: {refChat}
                <button onClick={() => setRefChat(null)} className="text-muted-foreground hover:text-foreground"><X className="size-3" /></button>
              </div>
            )}
            {files.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {files.map((f, i) => (
                  <div key={i} className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs">
                    {f.state === "uploading" && <Loader2 className="size-3 animate-spin" />}
                    {f.state === "error" && <AlertCircle className="size-3 text-destructive" />}
                    <span className={cn(f.state === "error" && "text-destructive")}>{f.name}</span>
                    <button onClick={() => setFiles((arr) => arr.filter((_, j) => j !== i))} className="text-muted-foreground hover:text-foreground"><X className="size-3" /></button>
                  </div>
                ))}
              </div>
            )}
            <div className="rounded-2xl border border-border bg-card shadow-sm">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                rows={2}
                placeholder="Ask the model set anything…"
                className="block w-full resize-none rounded-2xl bg-transparent px-4 pt-3 pb-2 text-sm outline-none placeholder:text-muted-foreground"
              />
              <div className="flex items-center gap-1 px-2 pb-2">
                <div className="relative">
                  <button onClick={() => setShowPlus((v) => !v)} className="rounded-lg p-2 text-muted-foreground hover:bg-accent" title="Attach"><Plus className="size-4" /></button>
                  {showPlus && (
                    <div className="absolute bottom-11 left-0 z-20 w-56 rounded-xl border border-border bg-popover p-1 shadow-lg">
                      <MenuItem icon={Upload} label="Upload file" onClick={() => { setShowPlus(false); setFiles((f) => [...f, { name: "spec.pdf", state: "uploading" }]); setTimeout(() => setFiles((f) => f.map((x) => x.name === "spec.pdf" ? { ...x, state: "uploaded" } : x)), 1200); }} />
                      <MenuItem icon={ImageIcon} label="Upload image" onClick={() => { setShowPlus(false); setFiles((f) => [...f, { name: "screenshot.png", state: "uploaded" }]); }} />
                      <MenuItem icon={Link2} label="Add reference chat" onClick={() => { setShowPlus(false); setShowRef(true); }} />
                      <MenuItem icon={FileSpreadsheet} label="Generate Excel" onClick={() => { setShowPlus(false); setShowExcel(true); }} />
                      <MenuItem icon={AlertCircle} label="Simulate upload error" onClick={() => { setShowPlus(false); setFiles((f) => [...f, { name: "broken.csv", state: "error" }]); }} />
                    </div>
                  )}
                </div>
                <TemplateMenu onPick={(t) => setInput((v) => `[${t.title}] ${v}`)} />
                <button onClick={() => setShowPrompt(true)} className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent">
                  <Wand2 className="size-3.5" /> Prompt Builder
                </button>
                <button onClick={() => setShowRef(true)} className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent">
                  <Link2 className="size-3.5" /> Reference
                </button>
                <button onClick={send} className="ml-auto inline-flex items-center gap-2 rounded-xl bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90">
                  <Send className="size-3.5" /> Send
                </button>
              </div>
            </div>
            <div className="mt-2 text-center text-[11px] text-muted-foreground">MultiAI may produce inaccurate information. Verdict AI does its best.</div>
          </div>
        </div>
      </div>

      {/* Modals */}
      <Modal open={showSet} onClose={() => setShowSet(false)} title="Switch Model Set" size="lg">
        <div className="space-y-2">
          {MODEL_SETS.map((s) => (
            <button
              key={s.id}
              onClick={() => { setSetId(s.id); setShowSet(false); }}
              className={cn("flex w-full items-center gap-3 rounded-xl border p-3 text-left hover:bg-accent", s.id === setId ? "border-primary bg-accent/50" : "border-border")}
            >
              <div className="flex-1">
                <div className="font-medium">{s.name}</div>
                <div className="text-xs text-muted-foreground">{s.description}</div>
              </div>
              <div className="flex gap-1">
                {s.models.map((id) => <span key={id} className="size-2 rounded-full" style={{ background: modelById(id).color }} />)}
              </div>
              <span className="text-xs text-muted-foreground">{s.strategy}</span>
            </button>
          ))}
          <Link to="/model-sets" onClick={() => setShowSet(false)} className="block rounded-xl border border-dashed border-border p-3 text-center text-sm hover:bg-accent">
            Manage all Model Sets →
          </Link>
        </div>
      </Modal>

      <Modal open={showStrategy} onClose={() => setShowStrategy(false)} title="Verdict strategies" size="lg">
        <div className="space-y-3">
          {STRATEGIES.map((s) => (
            <div key={s.name} className="rounded-xl border border-border p-4">
              <div className="flex items-center gap-2 font-medium"><Gavel className="size-4 text-primary" /> {s.name}</div>
              <div className="mt-1 text-sm text-muted-foreground">{s.desc}</div>
            </div>
          ))}
        </div>
      </Modal>

      <PromptBuilderModal open={showPrompt} onClose={() => setShowPrompt(false)} onUse={(t) => { setInput(t); setShowPrompt(false); }} />
      <ChatReferenceModal open={showRef} onClose={() => setShowRef(false)} onPick={(c) => { setRefChat(c); setShowRef(false); }} />
      <ExcelPreviewModal open={showExcel} onClose={() => setShowExcel(false)} />
    </AppShell>
  );
}

function MenuItem({ icon: Icon, label, onClick }: { icon: React.ComponentType<{ className?: string }>; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm hover:bg-accent">
      <Icon className="size-4 text-muted-foreground" /> {label}
    </button>
  );
}

function TemplateMenu({ onPick }: { onPick: (t: typeof TEMPLATES[number]) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button onClick={() => setOpen((v) => !v)} className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent">
        <LayoutTemplate className="size-3.5" /> Templates
      </button>
      {open && (
        <div className="absolute bottom-11 left-0 z-20 w-64 rounded-xl border border-border bg-popover p-1 shadow-lg">
          {TEMPLATES.map((t) => (
            <button key={t.id} onClick={() => { onPick(t); setOpen(false); }} className="block w-full rounded-lg px-2.5 py-2 text-left text-sm hover:bg-accent">
              <div className="font-medium">{t.title}</div>
              <div className="text-xs text-muted-foreground">{t.description}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function LoadingTurn({ set }: { set: typeof MODEL_SETS[number] }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        {set.models.map((id) => {
          const m = modelById(id);
          return (
            <div key={id} className="rounded-2xl border border-border bg-card p-4">
              <div className="flex items-center gap-2 text-sm font-medium">
                <span className="size-2 rounded-full" style={{ background: m.color }} /> {m.name}
                <Loader2 className="ml-auto size-3.5 animate-spin text-muted-foreground" />
              </div>
              <div className="mt-3 space-y-2">
                <div className="h-2 w-full rounded bg-muted animate-pulse" />
                <div className="h-2 w-10/12 rounded bg-muted animate-pulse" />
                <div className="h-2 w-8/12 rounded bg-muted animate-pulse" />
              </div>
            </div>
          );
        })}
      </div>
      <div className="rounded-2xl border border-dashed border-border bg-accent/30 p-4 text-sm text-muted-foreground">
        <Loader2 className="mr-2 inline size-3.5 animate-spin" /> Waiting for Verdict AI…
      </div>
    </div>
  );
}

function AiTurn({ set }: { set: typeof MODEL_SETS[number] }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        {set.models.map((id, i) => {
          const m = modelById(id);
          const a = SAMPLE_ANSWERS[i] ?? SAMPLE_ANSWERS[0];
          const failed = id === "mistral";
          return (
            <div key={id} className="rounded-2xl border border-border bg-card p-4">
              <div className="flex items-center gap-2 text-sm">
                <span className="size-2 rounded-full" style={{ background: m.color }} />
                <span className="font-medium">{m.name}</span>
                <span className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground">
                  <span className={cn("size-1.5 rounded-full", a.confidence > 85 ? "bg-success" : "bg-warning")} />
                  {a.confidence}%
                </span>
              </div>
              {failed ? (
                <div className="mt-3 rounded-lg bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertCircle className="mr-1 inline size-3.5" /> This model failed to answer. <button className="underline">Retry</button>
                </div>
              ) : (
                <>
                  <p className="mt-3 text-sm leading-relaxed text-foreground/90">{a.text}</p>
                  <div className="mt-3 flex items-center gap-1">
                    <button className="rounded-md p-1.5 text-muted-foreground hover:bg-accent" title="Copy"><Copy className="size-3.5" /></button>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
      <div className="rounded-2xl border border-primary/30 bg-primary/5 p-5">
        <div className="flex items-center gap-2">
          <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground"><Gavel className="size-3.5" /></span>
          <div className="font-medium">Verdict AI</div>
          <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">{VERDICT.strategy}</span>
          <button className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-accent"><Copy className="size-3.5" /></button>
        </div>
        <p className="mt-3 text-sm leading-relaxed">{VERDICT.text}</p>
        <div className="mt-3 rounded-lg border border-border bg-card p-3 text-xs text-muted-foreground">
          <strong className="text-foreground">Why:</strong> {VERDICT.reason}
        </div>
      </div>
    </div>
  );
}

function PromptBuilderModal({ open, onClose, onUse }: { open: boolean; onClose: () => void; onUse: (t: string) => void }) {
  const [raw, setRaw] = useState("write me a landing page");
  const improved = `Write a high-converting SaaS landing page for "${raw}". Goal: drive sign-ups. Tone: friendly, confident. Output format: H1, sub-headline, 3 feature cards, a single CTA. Constraints: avoid jargon, keep under 200 words.`;
  return (
    <Modal open={open} onClose={onClose} title="Prompt Builder" size="lg">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          {["Goal", "Context", "Output format", "Tone", "Constraints"].map((f) => (
            <label key={f} className="block text-sm">
              <div className="mb-1 font-medium">{f}</div>
              <input className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40" placeholder={`Add ${f.toLowerCase()}…`} />
            </label>
          ))}
        </div>
        <div>
          <div className="mb-1 text-sm font-medium">Rough prompt</div>
          <textarea value={raw} onChange={(e) => setRaw(e.target.value)} rows={3} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
        </div>
        <button className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"><Wand2 className="size-4" /> Improve prompt</button>
        <div>
          <div className="mb-1 text-sm font-medium">Improved prompt</div>
          <div className="rounded-xl border border-border bg-accent/30 p-3 text-sm">{improved}</div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button onClick={() => onUse(improved)} className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">Use improved prompt</button>
            <button className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-accent">Copy</button>
            <button className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-accent">Regenerate</button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function ChatReferenceModal({ open, onClose, onPick }: { open: boolean; onClose: () => void; onPick: (title: string) => void }) {
  const [mode, setMode] = useState<"summary" | "full">("summary");
  return (
    <Modal open={open} onClose={onClose} title="Reference a previous chat" size="lg">
      <div className="space-y-4">
        <input placeholder="Search chats…" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
        <div className="rounded-xl bg-accent/30 p-3 text-xs text-muted-foreground">
          Example: select <strong className="text-foreground">"Capital of Lebanon"</strong>, then ask <strong className="text-foreground">"How many people live there?"</strong> — MultiAI keeps the context.
        </div>
        <div className="space-y-1.5">
          {SAMPLE_CHATS.map((c) => (
            <button key={c.id} onClick={() => onPick(c.title)} className="flex w-full items-center justify-between rounded-lg border border-border bg-card p-3 text-left hover:bg-accent">
              <div>
                <div className="text-sm font-medium">{c.title}</div>
                <div className="text-xs text-muted-foreground">{c.updated}</div>
              </div>
              <Link2 className="size-4 text-muted-foreground" />
            </button>
          ))}
        </div>
        <div>
          <div className="mb-2 text-sm font-medium">Reference mode</div>
          <div className="grid grid-cols-2 gap-2">
            {(["summary", "full"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} className={cn("rounded-lg border p-3 text-left text-sm", mode === m ? "border-primary bg-accent/50" : "border-border")}>
                <div className="font-medium">{m === "summary" ? "Use summary only" : "Use full previous chat"}</div>
                <div className="text-xs text-muted-foreground">{m === "summary" ? "Lighter, cheaper, focused." : "Full context, slower, richer."}</div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function ExcelPreviewModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const rows = [
    ["Framework", "Bundle (kB)", "SEO", "Notes"],
    ["Next.js", "78", "Excellent", "Best ecosystem"],
    ["TanStack Start", "65", "Excellent", "Type-safe routing"],
    ["Astro", "12", "Excellent", "Static-first"],
    ["SvelteKit", "28", "Great", "Tiny output"],
  ];
  return (
    <Modal open={open} onClose={onClose} title="Excel preview" size="xl">
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-sm">
          <thead className="bg-accent/40">
            <tr>{rows[0].map((h) => <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.slice(1).map((r, i) => (
              <tr key={i} className="border-t border-border">{r.map((c, j) => <td key={j} className="px-3 py-2">{c}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">Download Excel</button>
        <button className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-accent">Regenerate</button>
        <button onClick={onClose} className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-accent">Add to chat</button>
      </div>
    </Modal>
  );
}

export { MODELS };
