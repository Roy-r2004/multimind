import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Send,
  Gavel,
  ChevronDown,
  X,
  Loader2,
  AlertCircle,
  Info,
  Share2,
  ShieldCheck,
  Sparkles,
  Plus,
  Pencil,
  Trash2,
  CheckCircle2,
  Wand2,
  Link2,
  FileSpreadsheet,
  Upload,
  Image as ImageIcon,
  ThumbsDown,
  BookOpen,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import { GlassCard, ModelPill, CinematicBackdrop } from "@/components/cinematic/PageChrome";
import ModelSetModal from "@/components/ModelSetModal";
import { PromptBuilderModal } from "@/components/chat/PromptBuilderModal";
import {
  ChatReferenceModal,
  type ChatReferencePick,
} from "@/components/chat/ChatReferenceModal";
import { ExcelPreviewModal } from "@/components/chat/ExcelPreviewModal";
import { TemplateMenu } from "@/components/chat/TemplateMenu";
import { CouncilPickerModal } from "@/components/chat/CouncilPickerModal";
import { VerdictDisagreeModal } from "@/components/chat/VerdictDisagreeModal";
import { useChatStore } from "@/lib/store";
import { useAuth } from "@/lib/auth";
import { useModels } from "@/lib/models";
import { api, streamTurn } from "@/lib/api";
import type { ApiTurn } from "@/lib/api/types";
import type { ModelSet } from "@/lib/mock";
import { STRATEGIES } from "@/lib/mock";
import { cn } from "@/lib/utils";
import {
  breakdownFromApi,
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

function applyStreamEvent(turn: ApiTurn, event: string, data: Record<string, unknown>): ApiTurn {
  if (event === "model_answer_started") {
    return {
      ...turn,
      status: "running",
      model_answers: turn.model_answers.map((a) =>
        a.model_id === data.model_id ? { ...a, status: "running" } : a,
      ),
    };
  }
  if (event === "model_answer_completed") {
    return {
      ...turn,
      model_answers: turn.model_answers.map((a) =>
        a.model_id === data.model_id
          ? {
              ...a,
              text: String(data.text ?? ""),
              confidence: Number(data.confidence ?? a.confidence),
              status: "completed",
              tokens_input: Number(data.tokens_input ?? 0),
              tokens_output: Number(data.tokens_output ?? 0),
              cost_usd: Number(data.cost_usd ?? 0),
            }
          : a,
      ),
    };
  }
  if (event === "model_answer_failed") {
    return {
      ...turn,
      model_answers: turn.model_answers.map((a) =>
        a.model_id === data.model_id
          ? { ...a, status: "failed", error_message: String(data.error ?? "Failed") }
          : a,
      ),
    };
  }
  if (event === "verdict_completed") {
    return {
      ...turn,
      verdict: {
        model_id: String(data.model_id ?? turn.verdict_model),
        strategy: turn.strategy,
        text: String(data.text ?? ""),
        reason: String(data.reason ?? ""),
        tokens_input: Number(data.tokens_input ?? 0),
        tokens_output: Number(data.tokens_output ?? 0),
        cost_usd: Number(data.cost_usd ?? 0),
      },
    };
  }
  if (event === "decision_insurance_completed") {
    return {
      ...turn,
      decision_insurance: {
        best_case: String(data.best_case ?? ""),
        worst_case: String(data.worst_case ?? ""),
        risk_level: String(data.risk_level ?? ""),
        potential_loss: String(data.potential_loss ?? ""),
        mitigation_plan: String(data.mitigation_plan ?? ""),
        tokens_input: Number(data.tokens_input ?? 0),
        tokens_output: Number(data.tokens_output ?? 0),
        cost_usd: Number(data.cost_usd ?? 0),
      },
    };
  }
  return turn;
}

type ComposerFile = { name: string; state: "uploading" | "uploaded" | "error" };

async function buildComposerInstructions(
  auth: { token: string; orgId: string },
  ref: ChatReferencePick | null,
  files: ComposerFile[],
  templateInstructions: string | null,
): Promise<string | undefined> {
  const parts: string[] = [];
  if (templateInstructions?.trim()) {
    parts.push(`Template instructions:\n${templateInstructions.trim()}`);
  }
  if (ref) {
    if (ref.mode === "full") {
      try {
        const turns = await api.chats.listTurns(auth, ref.chatId);
        const excerpt = turns
          .slice(-4)
          .map(
            (t) =>
              `User: ${t.user_message}\n${t.verdict?.text ? `Verdict: ${t.verdict.text}` : ""}`.trim(),
          )
          .join("\n\n");
        parts.push(
          `The user is continuing from chat "${ref.title}". Prior context:\n${excerpt || "(empty chat)"}`,
        );
      } catch {
        parts.push(`The user is continuing from a previous chat titled "${ref.title}".`);
      }
    } else {
      parts.push(
        `The user is continuing from a previous chat titled "${ref.title}". Keep that thread in mind.`,
      );
    }
  }
  const uploaded = files.filter((f) => f.state === "uploaded").map((f) => f.name);
  if (uploaded.length > 0) {
    parts.push(`Attached files (reference by name): ${uploaded.join(", ")}`);
  }
  const text = parts.join("\n\n").trim();
  return text || undefined;
}

const SYSTEM_MODEL_SETS = new Set(["balanced", "coding", "business", "research"]);

export function ChatPage() {
  const {
    modelSets,
    activeModelSetId,
    setActiveModelSetId,
    createModelSet,
    updateModelSet,
    isApiMode,
    activeChatId,
    createChat,
    chats,
    deleteChat,
  } = useChatStore();
  const { authHeaders, isAuthenticated } = useAuth();
  const { models, modelById, flagshipModels } = useModels();
  const navigate = useNavigate();
  const set = modelSets.find((s) => s.id === activeModelSetId) ?? modelSets[0];
  const [apiTurns, setApiTurns] = useState<ApiTurn[]>([]);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<ComposerFile[]>([]);
  const [refChat, setRefChat] = useState<ChatReferencePick | null>(null);
  const [templateInstructions, setTemplateInstructions] = useState<string | null>(null);
  const [showSet, setShowSet] = useState(false);
  const [showStrategy, setShowStrategy] = useState(false);
  const [showCouncil, setShowCouncil] = useState(false);
  const [showCreateSet, setShowCreateSet] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [showRef, setShowRef] = useState(false);
  const [showExcel, setShowExcel] = useState(false);
  const [showPlus, setShowPlus] = useState(false);
  const [loading, setLoading] = useState(false);
  const [showDeleteChat, setShowDeleteChat] = useState(false);
  const [deletingChat, setDeletingChat] = useState(false);
  const activeChat = chats.find((c) => c.id === activeChatId);

  useEffect(() => {
    if (!isApiMode || !activeChatId) {
      setApiTurns([]);
      return;
    }
    const auth = authHeaders();
    if (!auth) return;
    void api.chats.listTurns(auth, activeChatId).then(setApiTurns);
  }, [isApiMode, activeChatId, authHeaders]);

  async function send() {
    if (!input.trim() || !set) return;
    const question = input.trim();
    setInput("");
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setLoading(true);
    try {
      let chatId = activeChatId;
      if (!chatId) chatId = await createChat();
      if (!chatId) return;
      const customInstructions = await buildComposerInstructions(
        auth,
        refChat,
        files,
        templateInstructions,
      );
      const pending = await api.chats.createTurn(auth, chatId, {
        user_message: question,
        model_set_id: set.id,
        custom_instructions: customInstructions,
      });
      setApiTurns((prev) => [...prev, pending]);
      setRefChat(null);
      setFiles([]);
      setTemplateInstructions(null);
      await streamTurn(auth, pending.id, (event, data) => {
        if (event === "turn_completed") {
          setApiTurns((prev) => prev.map((t) => (t.id === pending.id ? (data as ApiTurn) : t)));
          return;
        }
        setApiTurns((prev) =>
          prev.map((t) => (t.id !== pending.id ? t : applyStreamEvent(t, event, data as Record<string, unknown>))),
        );
      });
    } finally {
      setLoading(false);
    }
  }

  async function handleShare() {
    const auth = authHeaders();
    if (!auth || !activeChatId) return;
    const link = await api.chats.createShareLink(auth, activeChatId);
    setShareUrl(link.url);
    await navigator.clipboard.writeText(link.url);
  }

  const empty = isAuthenticated && apiTurns.length === 0 && !loading;

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-3.5rem)] flex-col md:h-screen">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-border bg-background px-4 py-3 md:px-6">
          {set ? (
            <button
              onClick={() => setShowSet(true)}
              className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-1.5 text-sm font-medium hover:border-primary/40"
            >
              <Gavel className="size-3.5 text-primary" />
              {set.name}
              <ChevronDown className="size-3.5 text-muted-foreground" />
            </button>
          ) : (
            <span className="text-sm text-muted-foreground">Loading model sets…</span>
          )}
          {set && (
            <div className="hidden items-center gap-1.5 sm:flex">
              {set.models.map((id) => {
                const m = modelById(id);
                return (
                  <span
                    key={id}
                    className="size-2 rounded-full shadow-[0_0_8px_currentColor]"
                    style={{ color: m.color, background: m.color }}
                    title={m.name}
                  />
                );
              })}
              <span className="ml-2 text-xs text-muted-foreground">{set.strategy}</span>
              <button
                type="button"
                onClick={() => setShowCouncil(true)}
                className="ml-1 text-xs font-medium text-primary hover:underline"
              >
                Edit council
              </button>
              <button onClick={() => setShowStrategy(true)} className="text-muted-foreground hover:text-foreground">
                <Info className="size-3.5" />
              </button>
            </div>
          )}
          <div className="ml-auto flex items-center gap-2">
            {activeChatId && (
              <button
                type="button"
                onClick={() => setShowDeleteChat(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-destructive hover:bg-destructive/10"
              >
                <Trash2 className="size-3.5" /> Delete chat
              </button>
            )}
            <button
              type="button"
              onClick={() => void handleShare()}
              disabled={!activeChatId}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-accent disabled:opacity-40"
            >
              <Share2 className="size-3.5" /> {shareUrl ? "Copied" : "Share"}
            </button>
          </div>
        </div>

        {/* Thread */}
        <div className="flex-1 overflow-y-auto px-4 py-6 md:px-6">
          <div className="mx-auto max-w-4xl space-y-10">
            {!isAuthenticated && (
              <GlassCard glow className="p-10 text-center animate-fade-up">
                <Sparkles className="mx-auto size-8 text-primary" />
                <h2 className="mt-4 text-2xl font-semibold text-gradient">One question. Many minds.</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  GPT-4.1, Claude Sonnet 4, Gemini 2.5 Pro — real models via OpenRouter, one verdict.
                </p>
                <Link
                  to="/login"
                  className="mt-6 inline-flex rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
                >
                  Log in to start
                </Link>
              </GlassCard>
            )}

            {empty && set && (
              <div className="animate-fade-up space-y-8 py-8 text-center">
                <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-primary/80">
                  Your council of models
                </p>
                <h2 className="text-4xl font-semibold tracking-tight md:text-5xl">
                  Ask once.
                  <br />
                  <span className="text-gradient">Decide with clarity.</span>
                </h2>
                <p className="mx-auto max-w-lg text-sm text-muted-foreground">
                  {set.models.length} models answer in parallel — then Verdict AI synthesizes the final answer
                  using <strong className="text-foreground">{set.strategy}</strong>.
                </p>
                <button
                  type="button"
                  onClick={() => setShowCouncil(true)}
                  className="inline-flex items-center gap-2 rounded-xl border border-primary/30 bg-primary/5 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/10"
                >
                  Choose your 3 models
                </button>
                <div className="mx-auto grid max-w-3xl gap-3 sm:grid-cols-3">
                  {(models.length ? models : flagshipModels).slice(0, 6).map((m) => (
                    <ModelPill
                      key={m.id}
                      name={m.name}
                      vendor={m.vendor}
                      color={m.color}
                      pricing={m.pricing ?? undefined}
                    />
                  ))}
                </div>
              </div>
            )}

            {set &&
              apiTurns.map((turn) => (
                <div key={turn.id} className="space-y-6 animate-fade-up">
                  <div className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary/90 px-4 py-3 text-sm text-primary-foreground shadow-lg shadow-primary/20">
                      {turn.user_message}
                    </div>
                  </div>
                  <AiTurn
                    set={set}
                    turn={turn}
                    modelById={modelById}
                    onLessonCreated={(lessonId) => {
                      setApiTurns((prev) =>
                        prev.map((t) => (t.id === turn.id ? { ...t, lesson_id: lessonId } : t)),
                      );
                      void navigate({ to: "/lessons/$id", params: { id: lessonId } });
                    }}
                  />
                </div>
              ))}

            {loading && set && <LoadingTurn set={set} modelById={modelById} />}
          </div>
        </div>

        {/* Composer */}
        <div className="border-t border-border bg-background px-4 py-4 md:px-6">
          <div className="mx-auto max-w-4xl">
            {refChat && (
              <div className="mb-2 inline-flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-2.5 py-1 text-xs">
                <Link2 className="size-3 text-primary" />
                <span>
                  Ref: {refChat.title} ({refChat.mode})
                </span>
                <button
                  type="button"
                  onClick={() => setRefChat(null)}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3" />
                </button>
              </div>
            )}
            {templateInstructions && (
              <div className="mb-2 inline-flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1 text-xs">
                <span className="text-muted-foreground">Template active</span>
                <button
                  type="button"
                  onClick={() => setTemplateInstructions(null)}
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
                    key={`${f.name}-${i}`}
                    className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs"
                  >
                    {f.state === "uploading" && <Loader2 className="size-3 animate-spin" />}
                    {f.state === "error" && <AlertCircle className="size-3 text-destructive" />}
                    <span className={cn(f.state === "error" && "text-destructive")}>{f.name}</span>
                    <button
                      type="button"
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
                    void send();
                  }
                }}
                rows={2}
                disabled={!isAuthenticated || !set}
                placeholder={isAuthenticated ? "Ask your model council anything…" : "Log in to chat"}
                className="block w-full resize-none rounded-2xl bg-transparent px-4 pt-3 pb-2 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
              />
              <div className="flex flex-wrap items-center gap-1 px-2 pb-2">
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setShowPlus((v) => !v)}
                    disabled={!isAuthenticated}
                    className="rounded-lg p-2 text-muted-foreground hover:bg-accent disabled:opacity-40"
                    title="Attach"
                  >
                    <Plus className="size-4" />
                  </button>
                  {showPlus && (
                    <div className="absolute bottom-11 left-0 z-30 w-52 rounded-xl border border-border bg-popover p-1 shadow-xl">
                      <ComposerMenuItem
                        icon={Upload}
                        label="Upload file"
                        onClick={() => {
                          setShowPlus(false);
                          setFiles((f) => [...f, { name: "document.pdf", state: "uploading" }]);
                          window.setTimeout(
                            () =>
                              setFiles((f) =>
                                f.map((x) =>
                                  x.name === "document.pdf" && x.state === "uploading"
                                    ? { ...x, state: "uploaded" }
                                    : x,
                                ),
                              ),
                            800,
                          );
                        }}
                      />
                      <ComposerMenuItem
                        icon={ImageIcon}
                        label="Upload image"
                        onClick={() => {
                          setShowPlus(false);
                          setFiles((f) => [...f, { name: "image.png", state: "uploaded" }]);
                        }}
                      />
                      <ComposerMenuItem
                        icon={Link2}
                        label="Add reference chat"
                        onClick={() => {
                          setShowPlus(false);
                          setShowRef(true);
                        }}
                      />
                      <ComposerMenuItem
                        icon={FileSpreadsheet}
                        label="Generate Excel"
                        onClick={() => {
                          setShowPlus(false);
                          setShowExcel(true);
                        }}
                      />
                    </div>
                  )}
                </div>
                <TemplateMenu
                  onPick={(t) => {
                    setTemplateInstructions(t.instructions);
                    setInput((v) => (v.trim() ? v : `[${t.title}] `));
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowPrompt(true)}
                  disabled={!isAuthenticated}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent disabled:opacity-40"
                >
                  <Wand2 className="size-3.5" /> Prompt Builder
                </button>
                <button
                  type="button"
                  onClick={() => setShowRef(true)}
                  disabled={!isAuthenticated}
                  className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent disabled:opacity-40"
                >
                  <Link2 className="size-3.5" /> Reference
                </button>
                <button
                  type="button"
                  onClick={() => void send()}
                  disabled={!input.trim() || loading || !isAuthenticated}
                  className="ml-auto inline-flex items-center gap-2 rounded-xl bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-40"
                >
                  {loading ? <Loader2 className="size-3.5 animate-spin" /> : <Send className="size-3.5" />}
                  Send
                </button>
              </div>
            </div>
            <p className="mt-2 text-center text-[11px] text-muted-foreground">
              MultiAI may produce inaccurate information. Decision Insurance runs automatically on every turn.
            </p>
          </div>
        </div>
      </div>

      {set && (
        <ModelSetPickerModal
          open={showSet}
          onClose={() => setShowSet(false)}
          activeId={activeModelSetId}
          sets={modelSets}
          modelById={modelById}
          onPick={(id) => {
            setActiveModelSetId(id);
            setShowSet(false);
          }}
          onCreate={() => {
            setShowSet(false);
            setShowCreateSet(true);
          }}
        />
      )}

      <CouncilPickerModal
        open={showCouncil}
        onClose={() => setShowCouncil(false)}
        currentSet={set}
        onSave={async (next) => {
          if (set && modelSets.some((s) => s.id === set.id) && !SYSTEM_MODEL_SETS.has(set.id)) {
            await updateModelSet({ ...next, id: set.id });
            setActiveModelSetId(set.id);
            return;
          }
          const created = await createModelSet({
            ...next,
            name: next.name === set?.name ? "My Council" : next.name,
          });
          setActiveModelSetId(created.id);
        }}
      />

      <ModelSetModal open={showCreateSet} onClose={() => setShowCreateSet(false)} onCreate={createModelSet} />

      <Modal open={showStrategy} onClose={() => setShowStrategy(false)} title="Verdict strategy" size="md">
        {set && (
          <div className="space-y-3">
            {STRATEGIES.filter((s) => s.name === set.strategy).map((s) => (
              <div key={s.name}>
                <div className="font-medium">{s.name}</div>
                <p className="mt-1 text-sm text-muted-foreground">{s.desc}</p>
              </div>
            ))}
          </div>
        )}
      </Modal>

      <PromptBuilderModal
        open={showPrompt}
        onClose={() => setShowPrompt(false)}
        onUse={(text) => setInput(text)}
      />
      <ChatReferenceModal
        open={showRef}
        onClose={() => setShowRef(false)}
        chats={chats}
        currentChatId={activeChatId}
        onPick={setRefChat}
      />
      <ExcelPreviewModal
        open={showExcel}
        onClose={() => setShowExcel(false)}
        onAddToChat={() =>
          setFiles((f) => [...f, { name: "comparison.xlsx", state: "uploaded" }])
        }
      />

      <Modal open={showDeleteChat} onClose={() => setShowDeleteChat(false)} title="Delete chat?" size="sm">
        <p className="text-sm text-muted-foreground">
          {activeChat
            ? `"${activeChat.title}" will be permanently removed.`
            : "This chat will be permanently removed."}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setShowDeleteChat(false)}
            disabled={deletingChat}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={deletingChat || !activeChatId}
            onClick={() => {
              if (!activeChatId) return;
              setDeletingChat(true);
              void deleteChat(activeChatId)
                .then(() => {
                  setShowDeleteChat(false);
                  void navigate({ to: "/chat" });
                })
                .finally(() => setDeletingChat(false));
            }}
            className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground disabled:opacity-50"
          >
            {deletingChat ? "Deleting…" : "Delete"}
          </button>
        </div>
      </Modal>
    </AppShell>
  );
}

function ComposerMenuItem({
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
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm hover:bg-accent"
    >
      <Icon className="size-4 text-muted-foreground" /> {label}
    </button>
  );
}

function LoadingTurn({
  set,
  modelById,
}: {
  set: ModelSet;
  modelById: (id: string) => { name: string; color: string };
}) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        {set.models.map((id) => {
          const m = modelById(id);
          return (
            <GlassCard key={id} className="p-4">
              <div className="flex items-center gap-2 text-sm font-medium">
                <span className="size-2 rounded-full" style={{ background: m.color }} />
                {m.name}
                <Loader2 className="ml-auto size-3.5 animate-spin text-primary" />
              </div>
              <div className="mt-3 space-y-2">
                <div className="h-2 animate-pulse rounded bg-muted" />
                <div className="h-2 w-10/12 animate-pulse rounded bg-muted" />
              </div>
            </GlassCard>
          );
        })}
      </div>
      <GlassCard className="p-4 text-sm text-muted-foreground">
        <Loader2 className="mr-2 inline size-3.5 animate-spin text-primary" /> Synthesizing verdict…
      </GlassCard>
    </div>
  );
}

function AiTurn({
  set,
  turn,
  modelById,
  onLessonCreated,
}: {
  set: ModelSet;
  turn: ApiTurn;
  modelById: (id: string) => { name: string; color: string };
  onLessonCreated: (lessonId: string) => void;
}) {
  const { authHeaders } = useAuth();
  const [showDisagree, setShowDisagree] = useState(false);
  const answerUsage = new Map<string, UsageBreakdown>();
  turn.model_answers.forEach((a) => {
    if (a.status === "completed" && a.text) {
      answerUsage.set(
        a.model_id,
        breakdownFromApi(a.model_id, "answer", makeUsage(a.tokens_input, a.tokens_output), a.cost_usd, a.model_name),
      );
    }
  });
  const verdictUsage = turn.verdict
    ? breakdownFromApi(
        turn.verdict.model_id,
        "verdict",
        makeUsage(turn.verdict.tokens_input, turn.verdict.tokens_output),
        turn.verdict.cost_usd,
        modelById(turn.verdict.model_id).name,
      )
    : null;
  const insuranceUsage =
    turn.decision_insurance && (turn.decision_insurance.cost_usd ?? 0) > 0
      ? breakdownFromApi(
          turn.verdict_model,
          "insurance",
          makeUsage(
            turn.decision_insurance.tokens_input ?? 0,
            turn.decision_insurance.tokens_output ?? 0,
          ),
          turn.decision_insurance.cost_usd ?? 0,
          "Decision Insurance",
        )
      : null;
  const summaryItems = [
    ...answerUsage.values(),
    ...(verdictUsage ? [verdictUsage] : []),
    ...(insuranceUsage ? [insuranceUsage] : []),
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        {set.models.map((id) => {
          const m = modelById(id);
          const a = turn.model_answers.find((x) => x.model_id === id);
          const failed = !a || a.status === "failed";
          const usage = answerUsage.get(id);
          return (
            <GlassCard key={id} className="p-4">
              <div className="flex items-center gap-2 text-sm">
                <span className="size-2 rounded-full shadow-[0_0_8px_currentColor]" style={{ color: m.color, background: m.color }} />
                <span className="font-medium">{m.name}</span>
                {a?.confidence != null && (
                  <span className="ml-auto text-xs text-muted-foreground">{a.confidence}%</span>
                )}
              </div>
              {failed ? (
                <p className="mt-3 text-xs text-destructive">
                  <AlertCircle className="mr-1 inline size-3.5" />
                  {a?.error_message ?? "Failed"}
                </p>
              ) : (
                <>
                  <p className="mt-3 text-sm leading-relaxed text-foreground/90">{a?.text}</p>
                  {usage && <CardUsage b={usage} />}
                </>
              )}
            </GlassCard>
          );
        })}
      </div>

      {turn.verdict && (
        <GlassCard glow className="p-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
              <Gavel className="size-4" />
            </span>
            <span className="font-medium">Verdict</span>
            <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">{turn.verdict.strategy}</span>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {turn.lesson_id ? (
                <Link
                  to="/lessons/$id"
                  params={{ id: turn.lesson_id }}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/5 px-2.5 py-1.5 text-xs font-medium text-primary hover:bg-primary/10"
                >
                  <BookOpen className="size-3.5" /> View lesson
                </Link>
              ) : (
                <button
                  type="button"
                  onClick={() => setShowDisagree(true)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  <ThumbsDown className="size-3.5" /> I disagree
                </button>
              )}
            </div>
          </div>
          <p className="mt-3 text-sm leading-relaxed">{turn.verdict.text}</p>
          <p className="mt-2 text-xs text-muted-foreground">{turn.verdict.reason}</p>
          {verdictUsage && <CardUsage b={verdictUsage} />}
        </GlassCard>
      )}

      <VerdictDisagreeModal
        open={showDisagree}
        onClose={() => setShowDisagree(false)}
        onSubmit={async (data) => {
          const auth = authHeaders();
          if (!auth) return;
          const lesson = await api.lessons.disagree(auth, turn.id, data);
          onLessonCreated(lesson.id);
        }}
      />

      {turn.decision_insurance && (
        <GlassCard className="border-amber-500/20 p-5">
          <div className="flex items-center gap-2 text-amber-400">
            <ShieldCheck className="size-4" /> Decision Insurance
          </div>
          <p className="mt-2 text-sm text-muted-foreground">{turn.decision_insurance.mitigation_plan}</p>
        </GlassCard>
      )}

      {summaryItems.length > 0 && <SessionCostSummary items={summaryItems} modelById={modelById} />}
    </div>
  );
}

function CardUsage({ b }: { b: UsageBreakdown }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2">
      <button onClick={() => setOpen((v) => !v)} className="text-xs text-muted-foreground hover:text-foreground">
        {formatTokens(b.usage.total)} tok · {formatCost(b.cost)}
        <ChevronDown className={cn("ml-1 inline size-3 transition", open && "rotate-180")} />
      </button>
      {open && (
        <dl className="mt-1 grid grid-cols-2 gap-1 text-[11px] text-muted-foreground">
          <dt>In</dt>
          <dd className="text-right">{formatTokensExact(b.usage.input)}</dd>
          <dt>Out</dt>
          <dd className="text-right">{formatTokensExact(b.usage.output)}</dd>
        </dl>
      )}
    </div>
  );
}

function SessionCostSummary({
  items,
  modelById,
}: {
  items: UsageBreakdown[];
  modelById: (id: string) => { color: string };
}) {
  const total = items.reduce((s, i) => s + i.cost, 0);
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3 text-xs">
      <div className="flex flex-wrap gap-3">
        {items.map((b) => (
          <span key={`${b.modelId}-${b.kind}`} className="inline-flex items-center gap-1.5">
            <span className="size-1.5 rounded-full" style={{ background: modelById(b.modelId).color }} />
            {b.modelName}: {formatCost(b.cost)}
          </span>
        ))}
      </div>
      <div className="mt-2 font-medium text-foreground">Turn total: {formatCost(total)}</div>
    </div>
  );
}

function ModelSetPickerModal({
  open,
  onClose,
  activeId,
  sets,
  modelById,
  onPick,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  activeId: string;
  sets: ModelSet[];
  modelById: (id: string) => { name: string; color: string };
  onPick: (id: string) => void;
  onCreate: () => void;
}) {
  const { updateModelSet, deleteModelSet } = useChatStore();
  const [editing, setEditing] = useState<ModelSet | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-foreground/25 p-4" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-2xl border border-border bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Model sets</h3>
          <button onClick={onCreate} className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-sm text-primary-foreground">
            <Plus className="size-4" /> New
          </button>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {sets.map((s) => (
            <button
              key={s.id}
              onClick={() => onPick(s.id)}
              className={cn(
                "relative rounded-2xl border p-4 text-left transition",
                s.id === activeId ? "border-primary bg-primary/10" : "border-border hover:border-primary/30",
              )}
            >
              {s.id === activeId && (
                <CheckCircle2 className="absolute right-3 top-3 size-4 text-primary" />
              )}
              <div className="font-medium">{s.name}</div>
              <p className="mt-1 text-xs text-muted-foreground">{s.description}</p>
              <div className="mt-3 flex flex-wrap gap-1">
                {s.models.map((id) => (
                  <span key={id} className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[10px]">
                    <span className="size-1.5 rounded-full" style={{ background: modelById(id).color }} />
                    {modelById(id).name}
                  </span>
                ))}
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditing(s);
                  }}
                  className="rounded p-1 hover:bg-accent"
                >
                  <Pencil className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setDeleteId(s.id);
                  }}
                  className="rounded p-1 text-destructive hover:bg-destructive/10"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            </button>
          ))}
        </div>
        <button onClick={onClose} className="mt-4 text-sm text-muted-foreground hover:text-foreground">
          Close
        </button>
      </div>
      <ModelSetModal
        open={!!editing}
        onClose={() => setEditing(null)}
        initial={editing}
        onUpdate={(s) => {
          updateModelSet(s);
          setEditing(null);
        }}
      />
      <Modal open={!!deleteId} onClose={() => setDeleteId(null)} title="Delete set?" size="sm">
        <div className="flex justify-end gap-2">
          <button onClick={() => setDeleteId(null)} className="rounded-lg border border-border px-4 py-2 text-sm">
            Cancel
          </button>
          <button
            onClick={() => {
              if (deleteId) deleteModelSet(deleteId);
              setDeleteId(null);
            }}
            className="rounded-lg bg-destructive px-4 py-2 text-sm text-destructive-foreground"
          >
            Delete
          </button>
        </div>
      </Modal>
    </div>
  );
}
