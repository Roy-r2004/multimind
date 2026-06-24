import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  Plus,
  Send,
  Copy,
  Gavel,
  Pencil,
  Trash2,
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
  Check,
  CheckCircle2,
  Sparkles,
  ArrowRight,
  ShieldCheck,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import { useChatStore } from "@/lib/store";
import {
  MODEL_SETS,
  MODELS,
  SAMPLE_ANSWERS,
  SAMPLE_CHATS,
  STRATEGIES,
  TEMPLATES,
  VERDICT,
  modelById,
  type ModelSet,
  type Strategy,
} from "@/lib/mock";
import ModelSetModal from "@/components/ModelSetModal";
import { cn } from "@/lib/utils";
import {
  breakdown,
  estimateTokens,
  formatCost,
  formatTokens,
  formatTokensExact,
  makeUsage,
  type UsageBreakdown,
} from "@/lib/cost";

export const Route = createFileRoute("/chat")({
  head: () => ({ meta: [{ title: "Chat — MultiAI" }] }),
  component: ChatPage,
});

type Msg = { role: "user" | "ai"; question?: string };

export function ChatPage() {
  const { modelSets, activeModelSetId, setActiveModelSetId, createModelSet } = useChatStore();
  const set = modelSets.find((s) => s.id === activeModelSetId) ?? modelSets[0];
  const [showCreate, setShowCreate] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([
    { role: "user", question: "What's the best framework for a fast SaaS landing page in 2026?" },
    { role: "ai" },
  ]);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<{ name: string; state: "uploading" | "uploaded" | "error" }[]>(
    [],
  );
  const [refChat, setRefChat] = useState<string | null>(null);
  const [showSet, setShowSet] = useState(false);
  const [showStrategy, setShowStrategy] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [showRef, setShowRef] = useState(false);
  const [showExcel, setShowExcel] = useState(false);
  const [showPlus, setShowPlus] = useState(false);
  const [loading, setLoading] = useState(false);
  const [decisionInsuranceEnabled, setDecisionInsuranceEnabled] = useState(false);

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
            <span className="grid size-5 place-items-center rounded-md bg-primary/15 text-primary">
              <Gavel className="size-3" />
            </span>
            {set.name}
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </button>
          <div className="hidden sm:flex items-center gap-1.5">
            {set.models.map((id) => {
              const m = modelById(id);
              return (
                <span
                  key={id}
                  className="size-2.5 rounded-full"
                  style={{ background: m.color }}
                  title={m.name}
                />
              );
            })}
            <span className="ml-2 text-xs text-muted-foreground">· Verdict: {set.strategy}</span>
            <button
              onClick={() => setShowStrategy(true)}
              className="ml-1 text-muted-foreground hover:text-foreground"
            >
              <Info className="size-3.5" />
            </button>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Link
              to="/shared"
              className="hidden md:inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs hover:bg-accent"
            >
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
                  <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm text-primary-foreground">
                    {m.question}
                  </div>
                </div>
              ) : (
                <AiTurn
                  key={i}
                  set={set}
                  question={messages[i - 1]?.question ?? ""}
                  decisionInsuranceEnabled={decisionInsuranceEnabled}
                />
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
                <button
                  onClick={() => setRefChat(null)}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3" />
                </button>
              </div>
            )}
            {files.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {files.map((f, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs"
                  >
                    {f.state === "uploading" && <Loader2 className="size-3 animate-spin" />}
                    {f.state === "error" && <AlertCircle className="size-3 text-destructive" />}
                    <span className={cn(f.state === "error" && "text-destructive")}>{f.name}</span>
                    <button
                      onClick={() => setFiles((arr) => arr.filter((_, j) => j !== i))}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="rounded-2xl border border-border bg-card shadow-sm">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                rows={2}
                placeholder="Ask the model set anything…"
                className="block w-full resize-none rounded-2xl bg-transparent px-4 pt-3 pb-2 text-sm outline-none placeholder:text-muted-foreground"
              />
              <div className="flex items-center gap-1 px-2 pb-2">
                <div className="relative">
                  <button
                    onClick={() => setShowPlus((v) => !v)}
                    className="rounded-lg p-2 text-muted-foreground hover:bg-accent"
                    title="Attach"
                  >
                    <Plus className="size-4" />
                  </button>
                  {showPlus && (
                    <div className="absolute bottom-11 left-0 z-20 w-56 rounded-xl border border-border bg-popover p-1 shadow-lg">
                      <MenuItem
                        icon={Upload}
                        label="Upload file"
                        onClick={() => {
                          setShowPlus(false);
                          setFiles((f) => [...f, { name: "spec.pdf", state: "uploading" }]);
                          setTimeout(
                            () =>
                              setFiles((f) =>
                                f.map((x) =>
                                  x.name === "spec.pdf" ? { ...x, state: "uploaded" } : x,
                                ),
                              ),
                            1200,
                          );
                        }}
                      />
                      <MenuItem
                        icon={ImageIcon}
                        label="Upload image"
                        onClick={() => {
                          setShowPlus(false);
                          setFiles((f) => [...f, { name: "screenshot.png", state: "uploaded" }]);
                        }}
                      />
                      <MenuItem
                        icon={Link2}
                        label="Add reference chat"
                        onClick={() => {
                          setShowPlus(false);
                          setShowRef(true);
                        }}
                      />
                      <MenuItem
                        icon={FileSpreadsheet}
                        label="Generate Excel"
                        onClick={() => {
                          setShowPlus(false);
                          setShowExcel(true);
                        }}
                      />
                      <MenuItem
                        icon={AlertCircle}
                        label="Simulate upload error"
                        onClick={() => {
                          setShowPlus(false);
                          setFiles((f) => [...f, { name: "broken.csv", state: "error" }]);
                        }}
                      />
                    </div>
                  )}
                </div>
                <TemplateMenu onPick={(t) => setInput((v) => `[${t.title}] ${v}`)} />
                <button
                  onClick={() => setShowPrompt(true)}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent"
                >
                  <Wand2 className="size-3.5" /> Prompt Builder
                </button>
                <button
                  onClick={() => setShowRef(true)}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent"
                >
                  <Link2 className="size-3.5" /> Reference
                </button>
                <button
                  onClick={() => setDecisionInsuranceEnabled((v) => !v)}
                  className={cn(
                    "ml-auto inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-xs font-medium transition-colors",
                    decisionInsuranceEnabled
                      ? "bg-primary text-primary-foreground"
                      : "border border-border bg-background/80 text-muted-foreground hover:bg-accent",
                  )}
                >
                  <ShieldCheck className="size-3.5" /> Decision Insurance
                </button>
                <button
                  onClick={send}
                  className="ml-2 inline-flex items-center gap-2 rounded-xl bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
                >
                  <Send className="size-3.5" /> Send
                </button>
              </div>
            </div>
            <div className="mt-2 text-center text-[11px] text-muted-foreground">
              MultiAI may produce inaccurate information. Verdict AI does its best.
            </div>
          </div>
        </div>
      </div>

      {/* Modals */}
      <ModelSetPickerModal
        open={showSet}
        onClose={() => setShowSet(false)}
        activeId={set.id}
        sets={modelSets}
        onPick={(id) => {
          setActiveModelSetId(id);
          setShowSet(false);
        }}
        onCreate={() => {
          setShowSet(false);
          setShowCreate(true);
        }}
      />

      <ModelSetModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreate={(newSet) => {
          createModelSet(newSet);
          setActiveModelSetId(newSet.id);
          setShowCreate(false);
        }}
      />

      <Modal
        open={showStrategy}
        onClose={() => setShowStrategy(false)}
        title="Verdict strategies"
        size="lg"
      >
        <div className="space-y-3">
          {STRATEGIES.map((s) => (
            <div key={s.name} className="rounded-xl border border-border p-4">
              <div className="flex items-center gap-2 font-medium">
                <Gavel className="size-4 text-primary" /> {s.name}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">{s.desc}</div>
            </div>
          ))}
        </div>
      </Modal>

      <PromptBuilderModal
        open={showPrompt}
        onClose={() => setShowPrompt(false)}
        onUse={(t) => {
          setInput(t);
          setShowPrompt(false);
        }}
      />
      <ChatReferenceModal
        open={showRef}
        onClose={() => setShowRef(false)}
        onPick={(c) => {
          setRefChat(c);
          setShowRef(false);
        }}
      />
      <ExcelPreviewModal open={showExcel} onClose={() => setShowExcel(false)} />
    </AppShell>
  );
}

function MenuItem({
  icon: Icon,
  label,
  onClick,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm hover:bg-accent"
    >
      <Icon className="size-4 text-muted-foreground" /> {label}
    </button>
  );
}

function TemplateMenu({ onPick }: { onPick: (t: (typeof TEMPLATES)[number]) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent"
      >
        <LayoutTemplate className="size-3.5" /> Templates
      </button>
      {open && (
        <div className="absolute bottom-11 left-0 z-20 w-64 rounded-xl border border-border bg-popover p-1 shadow-lg">
          {TEMPLATES.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                onPick(t);
                setOpen(false);
              }}
              className="block w-full rounded-lg px-2.5 py-2 text-left text-sm hover:bg-accent"
            >
              <div className="font-medium">{t.title}</div>
              <div className="text-xs text-muted-foreground">{t.description}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function LoadingTurn({ set }: { set: (typeof MODEL_SETS)[number] }) {
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

function buildDecisionInsuranceAnalysis({
  question,
  modelResponses,
  verdictResponse,
}: {
  question: string;
  modelResponses: Array<{ name: string; text: string; confidence: number; failed: boolean }>;
  verdictResponse: string;
}) {
  const combined =
    `${question} ${modelResponses.map((m) => m.text).join(" ")} ${verdictResponse}`.toLowerCase();
  const hasRiskSignals =
    /risk|uncertain|legal|finance|security|medical|contract|critical|deadline|launch|regulator|investment|sensitive/i.test(
      combined,
    );
  const riskLevel = /high|critical|urgent|immediate|must|need to/i.test(combined)
    ? "High"
    : hasRiskSignals
      ? "Medium"
      : "Low";
  const verdictSignal = verdictResponse.toLowerCase().includes("recommend")
    ? "The verdict leans toward a clear next step, which can create upside if the recommendation holds."
    : "The verdict is cautious, so the main exposure is acting too quickly on a broad recommendation.";

  return {
    bestCase: `If the recommendation proves sound, the team can move decisively and capture the upside of the suggested path. ${verdictSignal}`,
    worstCase: `If the recommendation is off, the plan could waste time, budget, or trust before the team notices the mismatch.`,
    riskLevel,
    potentialLoss:
      riskLevel === "High"
        ? "A high-stakes mistake could lead to rework, missed deadlines, and avoidable spend."
        : riskLevel === "Medium"
          ? "The main loss is time, effort, and confidence lost while correcting a suboptimal choice."
          : "The downside is limited, but a small misstep could still slow execution or reduce quality.",
    mitigationPlan:
      "Validate the recommendation with a second source, test it on a small pilot, and define a rollback point before a full commitment.",
  };
}

function AiTurn({
  set,
  question,
  decisionInsuranceEnabled,
}: {
  set: (typeof MODEL_SETS)[number];
  question: string;
  decisionInsuranceEnabled: boolean;
}) {
  const inputTokens = estimateTokens(question || "");

  // Per-model usage for the answering models (failed models report nothing).
  const answerUsage = new Map<string, UsageBreakdown>();
  const modelResponses: Array<{ name: string; text: string; confidence: number; failed: boolean }> =
    [];
  set.models.forEach((id, i) => {
    const m = modelById(id);
    const a = SAMPLE_ANSWERS[i] ?? SAMPLE_ANSWERS[0];
    const failed = id === "mistral";
    modelResponses.push({
      name: m.name,
      text: failed ? "This model failed to answer." : a.text,
      confidence: a.confidence,
      failed,
    });
    if (failed) return; // simulated failure — no usage billed
    answerUsage.set(id, breakdown(id, "answer", makeUsage(inputTokens, estimateTokens(a.text))));
  });

  // The Verdict AI reads every answer, so its input is the sum of their outputs.
  const verdictInput = Array.from(answerUsage.values()).reduce((s, b) => s + b.usage.output, 0);
  const verdictUsage = breakdown(
    set.verdictModel,
    "verdict",
    makeUsage(verdictInput, estimateTokens(VERDICT.text)),
  );

  const summaryItems: UsageBreakdown[] = [...answerUsage.values(), verdictUsage];
  const decisionInsurance = decisionInsuranceEnabled
    ? buildDecisionInsuranceAnalysis({
        question,
        modelResponses,
        verdictResponse: VERDICT.text,
      })
    : null;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        {set.models.map((id, i) => {
          const m = modelById(id);
          const a = SAMPLE_ANSWERS[i] ?? SAMPLE_ANSWERS[0];
          const failed = id === "mistral";
          const usage = answerUsage.get(id);
          return (
            <div key={id} className="rounded-2xl border border-border bg-card p-4">
              <div className="flex items-center gap-2 text-sm">
                <span className="size-2 rounded-full" style={{ background: m.color }} />
                <span className="font-medium">{m.name}</span>
                <span className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground">
                  <span
                    className={cn(
                      "size-1.5 rounded-full",
                      a.confidence > 85 ? "bg-success" : "bg-warning",
                    )}
                  />
                  {a.confidence}%
                </span>
              </div>
              {failed ? (
                <div className="mt-3 rounded-lg bg-destructive/10 p-3 text-xs text-destructive">
                  <AlertCircle className="mr-1 inline size-3.5" /> This model failed to answer.{" "}
                  <button className="underline">Retry</button>
                </div>
              ) : (
                <>
                  <p className="mt-3 text-sm leading-relaxed text-foreground/90">{a.text}</p>
                  <div className="mt-3 flex items-center gap-1">
                    <button
                      className="rounded-md p-1.5 text-muted-foreground hover:bg-accent"
                      title="Copy"
                    >
                      <Copy className="size-3.5" />
                    </button>
                  </div>
                  {usage && <CardUsage b={usage} />}
                </>
              )}
            </div>
          );
        })}
      </div>
      <div className="rounded-2xl border border-primary/30 bg-primary/5 p-5">
        <div className="flex items-center gap-2">
          <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
            <Gavel className="size-3.5" />
          </span>
          <div className="font-medium">Verdict AI</div>
          <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">
            {VERDICT.strategy}
          </span>
          <button className="ml-auto rounded-md p-1.5 text-muted-foreground hover:bg-accent">
            <Copy className="size-3.5" />
          </button>
        </div>
        <p className="mt-3 text-sm leading-relaxed">{VERDICT.text}</p>
        <div className="mt-3 rounded-lg border border-border bg-card p-3 text-xs text-muted-foreground">
          <strong className="text-foreground">Why:</strong> {VERDICT.reason}
        </div>
        <CardUsage b={verdictUsage} />
      </div>
      {decisionInsurance && (
        <div className="rounded-2xl border border-amber-500/20 bg-amber-50/70 p-5 dark:bg-amber-950/10">
          <div className="flex items-center gap-2">
            <span className="grid size-7 place-items-center rounded-lg bg-amber-500/15 text-amber-600">
              <ShieldCheck className="size-3.5" />
            </span>
            <div>
              <div className="font-medium">🛡 Decision Insurance</div>
              <div className="text-xs text-muted-foreground">Structured risk analysis</div>
            </div>
          </div>
          <div className="mt-4 grid gap-3 text-sm">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                Best Case
              </div>
              <p className="mt-1 leading-relaxed text-foreground/90">
                {decisionInsurance.bestCase}
              </p>
            </div>
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                Worst Case
              </div>
              <p className="mt-1 leading-relaxed text-foreground/90">
                {decisionInsurance.worstCase}
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                  Risk Level
                </div>
                <p className="mt-1 font-medium text-foreground">{decisionInsurance.riskLevel}</p>
              </div>
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                  Potential Loss
                </div>
                <p className="mt-1 leading-relaxed text-foreground/90">
                  {decisionInsurance.potentialLoss}
                </p>
              </div>
            </div>
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                Mitigation Plan
              </div>
              <p className="mt-1 leading-relaxed text-foreground/90">
                {decisionInsurance.mitigationPlan}
              </p>
            </div>
          </div>
        </div>
      )}
      <SessionCostSummary items={summaryItems} />
    </div>
  );
}

function CardUsage({ b }: { b: UsageBreakdown }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <span>
          {formatTokens(b.usage.total)} tokens • {formatCost(b.cost)}
        </span>
        <ChevronDown className={cn("size-3 transition", open && "rotate-180")} />
      </button>
      {open && (
        <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 rounded-lg border border-border bg-background/60 p-2.5 text-xs">
          <dt className="text-muted-foreground">Input</dt>
          <dd className="text-right text-foreground">{formatTokensExact(b.usage.input)}</dd>
          <dt className="text-muted-foreground">Output</dt>
          <dd className="text-right text-foreground">{formatTokensExact(b.usage.output)}</dd>
          <dt className="text-muted-foreground">Total</dt>
          <dd className="text-right text-foreground">{formatTokensExact(b.usage.total)}</dd>
          <dt className="text-muted-foreground">Cost</dt>
          <dd className="text-right text-foreground">{formatCost(b.cost)}</dd>
        </dl>
      )}
    </div>
  );
}

function SessionCostSummary({ items }: { items: UsageBreakdown[] }) {
  const [open, setOpen] = useState(false);
  const totalTokens = items.reduce((s, b) => s + b.usage.total, 0);
  const totalCost = items.reduce((s, b) => s + b.cost, 0);
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        <ChevronDown className={cn("size-3.5 transition", open && "rotate-180")} />
        {open ? "Hide Cost Details" : "Show Cost Details"}
      </button>
      {open && (
        <div className="mt-2 rounded-xl border border-border bg-card p-4">
          <div className="text-sm font-semibold">Session Cost Summary</div>
          <div className="mt-3 space-y-2">
            {items.map((b, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <div className="flex min-w-0 items-center gap-1.5">
                  {b.kind === "verdict" ? (
                    <Gavel className="size-3 shrink-0 text-primary" />
                  ) : (
                    <span
                      className="size-2 shrink-0 rounded-full"
                      style={{ background: modelById(b.modelId).color }}
                    />
                  )}
                  <span className="truncate font-medium text-foreground">
                    {b.kind === "verdict" ? `Verdict AI · ${b.modelName}` : b.modelName}
                  </span>
                </div>
                <div className="shrink-0 text-muted-foreground">
                  {formatTokensExact(b.usage.total)} tokens · {formatCost(b.cost)}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center justify-between border-t border-border pt-2.5 text-sm font-semibold">
            <span>Total</span>
            <span>
              {formatTokensExact(totalTokens)} tokens · {formatCost(totalCost)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function PromptBuilderModal({
  open,
  onClose,
  onUse,
}: {
  open: boolean;
  onClose: () => void;
  onUse: (t: string) => void;
}) {
  const [raw, setRaw] = useState("");
  const [improved, setImproved] = useState("");

  function close() {
    setRaw("");
    setImproved("");
    onClose();
  }

  function generatePrompt() {
    const trimmed = raw.trim();
    if (!trimmed) {
      setImproved("");
      return;
    }

    const normalized = trimmed.charAt(0).toLowerCase() + trimmed.slice(1);
    const prefix = normalized.startsWith("explain")
      ? "Explain"
      : normalized.startsWith("write") || normalized.startsWith("create")
        ? "Create"
        : "Generate";

    setImproved(
      `${prefix} ${normalized}. Make the request clear, detailed, and structured so the AI can respond with a helpful, professional result. Include the intended audience, desired format, and any relevant details needed to complete the task well.`,
    );
  }

  async function copyPrompt() {
    if (!improved) return;
    await navigator.clipboard.writeText(improved);
  }

  function handleUse() {
    if (!improved) return;
    onUse(improved);
    close();
  }

  return (
    <Modal open={open} onClose={close} title="Prompt Builder" size="lg">
      <div className="space-y-6">
        <div>
          <div className="mb-2 text-sm font-medium">What do you want help with?</div>
          <textarea
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            rows={6}
            placeholder="Write me a landing page for my AI startup"
            className="w-full rounded-lg border border-border bg-background px-3 py-3 text-sm"
          />
        </div>

        <button
          onClick={generatePrompt}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          <Wand2 className="size-4" /> Generate Better Prompt
        </button>

        <div>
          <div className="mb-2 text-sm font-medium">Improved Prompt</div>
          <div className="min-h-[120px] rounded-xl border border-border bg-accent/30 p-4 text-sm text-foreground">
            {improved || (
              <div className="text-muted-foreground">
                Your improved prompt will appear here after generation.
              </div>
            )}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              onClick={handleUse}
              disabled={!improved}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              Use Prompt
            </button>
            <button
              onClick={copyPrompt}
              disabled={!improved}
              className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
            >
              Copy
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function ChatReferenceModal({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  onPick: (title: string) => void;
}) {
  const [mode, setMode] = useState<"summary" | "full">("summary");
  return (
    <Modal open={open} onClose={onClose} title="Reference a previous chat" size="lg">
      <div className="space-y-4">
        <input
          placeholder="Search chats…"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <div className="rounded-xl bg-accent/30 p-3 text-xs text-muted-foreground">
          Example: select <strong className="text-foreground">"Capital of Lebanon"</strong>, then
          ask <strong className="text-foreground">"How many people live there?"</strong> — MultiAI
          keeps the context.
        </div>
        <div className="space-y-1.5">
          {SAMPLE_CHATS.map((c) => (
            <button
              key={c.id}
              onClick={() => onPick(c.title)}
              className="flex w-full items-center justify-between rounded-lg border border-border bg-card p-3 text-left hover:bg-accent"
            >
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
              <button
                key={m}
                onClick={() => setMode(m)}
                className={cn(
                  "rounded-lg border p-3 text-left text-sm",
                  mode === m ? "border-primary bg-accent/50" : "border-border",
                )}
              >
                <div className="font-medium">
                  {m === "summary" ? "Use summary only" : "Use full previous chat"}
                </div>
                <div className="text-xs text-muted-foreground">
                  {m === "summary" ? "Lighter, cheaper, focused." : "Full context, slower, richer."}
                </div>
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
            <tr>
              {rows[0].map((h) => (
                <th key={h} className="px-3 py-2 text-left font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(1).map((r, i) => (
              <tr key={i} className="border-t border-border">
                {r.map((c, j) => (
                  <td key={j} className="px-3 py-2">
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">
          Download Excel
        </button>
        <button className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-accent">
          Regenerate
        </button>
        <button
          onClick={onClose}
          className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-accent"
        >
          Add to chat
        </button>
      </div>
    </Modal>
  );
}

export { MODELS };

function ModelSetPickerModal({
  open,
  onClose,
  activeId,
  sets,
  onPick,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  activeId: string;
  sets: ModelSet[];
  onPick: (id: string) => void;
  onCreate: () => void;
}) {
  const { updateModelSet, deleteModelSet } = useChatStore();
  const [editingSet, setEditingSet] = useState<ModelSet | null>(null);
  const [showEdit, setShowEdit] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-foreground/40 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
      >
        <div className="flex items-center justify-between gap-4 border-b border-border px-6 py-4">
          <div>
            <h3 className="text-lg font-semibold">Choose a Model Set</h3>
            <p className="text-xs text-muted-foreground">
              Each set runs a curated group of AI models, then a Verdict AI gives the final answer.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onCreate}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              <Plus className="size-4" /> Create New Model Set
            </button>
            <button onClick={onClose} className="rounded-md p-1.5 hover:bg-accent">
              <X className="size-4" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          <div className="grid gap-4 sm:grid-cols-2">
            {sets.map((s) => {
              const active = s.id === activeId;
              return (
                <button
                  key={s.id}
                  onClick={() => onPick(s.id)}
                  className={cn(
                    "group relative flex flex-col gap-2 rounded-2xl border-2 p-3 text-left transition",
                    active
                      ? "border-primary bg-primary/5 shadow-md ring-2 ring-primary/20"
                      : "border-border bg-card hover:border-primary/40 hover:bg-accent/40",
                  )}
                >
                  {/* Action buttons (do not overlap the Active badge) */}
                  <div className="absolute right-12 top-3 flex gap-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingSet(s);
                        setShowEdit(true);
                      }}
                      className="rounded-md p-1 hover:bg-accent"
                      title="Edit"
                    >
                      <Pencil className="size-4" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeleteTarget(s.id);
                      }}
                      className="rounded-md p-1 text-destructive hover:bg-destructive/10"
                      title="Delete"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </div>

                  {active && (
                    <span className="absolute right-4 top-3 inline-flex items-center gap-1 rounded-full bg-primary px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-primary-foreground">
                      <CheckCircle2 className="size-3" /> Active
                    </span>
                  )}
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "grid size-9 place-items-center rounded-xl",
                        active
                          ? "bg-primary text-primary-foreground"
                          : "bg-primary/10 text-primary",
                      )}
                    >
                      <Gavel className="size-4" />
                    </span>
                    <div className="text-base font-semibold">{s.name}</div>
                  </div>
                  <p className="text-sm text-muted-foreground">{s.description}</p>

                  <div>
                    <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Models
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {s.models.map((id) => {
                        const m = modelById(id);
                        return (
                          <span
                            key={id}
                            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2 py-0.5 text-xs"
                          >
                            <span className="size-2 rounded-full" style={{ background: m.color }} />
                            {m.name}
                          </span>
                        );
                      })}
                    </div>
                  </div>

                  <div className="mt-1 grid gap-1 text-sm">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Verdict AI
                    </div>
                    <div className="text-sm font-medium">{modelById(s.verdictModel).name}</div>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mt-2">
                      Strategy
                    </div>
                    <div className="text-sm font-medium">{s.strategy}</div>
                    {s.templateName && (
                      <>
                        <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mt-2">
                          Template
                        </div>
                        <div className="text-sm font-medium">{s.templateName}</div>
                      </>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Footer close handled by the modal's top-right X only */}
        <ModelSetModal
          open={showEdit}
          onClose={() => setShowEdit(false)}
          initial={editingSet}
          onUpdate={(s) => {
            updateModelSet(s);
            setShowEdit(false);
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
              Are you sure you want to delete this model set?
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
    </div>
  );
}

function ModelChipPicker({
  placeholder,
  exclude,
  onPick,
}: {
  placeholder: string;
  exclude: string[];
  onPick: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const [highlight, setHighlight] = useState<string | null>(null);
  const results = MODELS.filter(
    (m) =>
      !exclude.includes(m.id) &&
      (q.trim() === "" ||
        m.name.toLowerCase().includes(q.toLowerCase()) ||
        m.vendor.toLowerCase().includes(q.toLowerCase())),
  );
  const selected =
    highlight && results.find((r) => r.id === highlight) ? highlight : (results[0]?.id ?? null);

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setHighlight(null);
          }}
          placeholder={placeholder}
          className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40"
        />
        <button
          type="button"
          disabled={!selected}
          onClick={() => {
            if (selected) {
              onPick(selected);
              setQ("");
              setHighlight(null);
            }
          }}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
        >
          <Plus className="size-4" /> Add
        </button>
      </div>
      {q.trim() !== "" && (
        <div className="rounded-lg border border-border bg-popover p-1 shadow-sm">
          {results.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted-foreground">No models match.</div>
          ) : (
            results.slice(0, 5).map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => {
                  onPick(m.id);
                  setQ("");
                  setHighlight(null);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-accent",
                  selected === m.id && "bg-accent",
                )}
              >
                <span className="size-2 rounded-full" style={{ background: m.color }} />
                <span className="font-medium">{m.name}</span>
                <span className="text-xs text-muted-foreground">{m.vendor}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
