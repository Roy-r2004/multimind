import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  Send,
  Gavel,
  ChevronDown,
  X,
  Loader2,
  AlertCircle,
  Info,
  Share2,
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
  Swords,
  BookOpen,
  Trophy,
  Square,
  Bookmark,
  MoreHorizontal,
  ArrowDown,
  Pin,
  FilePlus2,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import { VendorLogo } from "@/components/chat/VendorLogo";
import { GlassCard, ModelPill, CinematicBackdrop } from "@/components/cinematic/PageChrome";
import ModelSetModal from "@/components/ModelSetModal";
import { PromptBuilderModal } from "@/components/chat/PromptBuilderModal";
import { ChatReferenceModal, type ChatReferencePick } from "@/components/chat/ChatReferenceModal";
import { ExcelPreviewModal } from "@/components/chat/ExcelPreviewModal";
import { CouncilPickerModal } from "@/components/chat/CouncilPickerModal";
import { VerdictDisagreeChat } from "@/components/chat/VerdictDisagreeChat";
import { VerdictCopyButton } from "@/components/chat/VerdictCopyButton";
import { UserPromptBubble } from "@/components/chat/UserPromptBubble";
import { ModelConfidenceBadge } from "@/components/chat/ModelConfidenceBadge";
import { CallCostLabel, TurnCostSummary } from "@/components/chat/CallCostLabel";
import { MessageContent } from "@/components/chat/MessageContent";
import { ExpandableAnswer } from "@/components/chat/ExpandableAnswer";
import { VoiceRecorderButton } from "@/components/chat/VoiceRecorderButton";
import { SaveTurnDialog } from "@/components/chat/SaveTurnDialog";
import { ChatTurnLayoutToggle } from "@/components/chat/ChatTurnLayoutToggle";
import { useChatStore } from "@/lib/store";
import { useAuth } from "@/lib/auth";
import { useModels } from "@/lib/models";
import { api } from "@/lib/api";
import type { ApiTranscriptionResponse, ApiTurn } from "@/lib/api/types";
import {
  mergeWithCachedTurns,
  removeTurn,
  restoreRemovedTurn,
  resumeRunningTurns,
  runTurnInBackground,
  seedChatTurns,
  setVerdictSavedState as setCachedVerdictSavedState,
  stopActiveTurn,
  subscribeActiveTurn,
  subscribeChatRunning,
  subscribeChatTurns,
} from "@/lib/turnRunner";
import type { ModelSet, Strategy } from "@/lib/mock";
import { STRATEGIES } from "@/lib/mock";
import { cn } from "@/lib/utils";
import { getVerdictBookmarkState, updateVerdictSavedInTurns } from "@/lib/savedVerdicts";
import {
  canShowHistoricalTurnDelete,
  isAnyTurnGenerating,
  isHistoricalTurnDeleteDisabled,
  removeTurnFromList,
} from "@/lib/turnState";
import {
  canEditUserPrompt,
  countLaterTurns,
  LATER_TURNS_EDIT_WARNING,
} from "@/lib/promptEdit";
import { MAX_COUNCIL_MODELS } from "@/lib/modelIds";
import { deriveTurnAnswerCards } from "@/lib/turnCards";
import { resolveModelSetIdFromTurns } from "@/lib/modelSetSelection";
import { chatAnswerCardsClassName } from "@/lib/chatTurnLayout";
import { useChatTurnLayout } from "@/hooks/useChatTurnLayout";
import { useTurnAnswerExpansion } from "@/hooks/useTurnAnswerExpansion";
import {
  findPinnedSynthesisElement,
  isChatNearBottom,
  scrollThreadToElement,
  shouldShowScrollToLatest,
} from "@/lib/chatScroll";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export const Route = createFileRoute("/chat")({
  head: () => ({ meta: [{ title: "Chat — MultiAI" }] }),
  component: ChatPage,
});

type ComposerFile = {
  localId: string;
  name: string;
  state: "uploading" | "uploaded" | "error";
  attachmentId?: string;
  textExcerpt?: string | null;
  errorMessage?: string;
};

const TEXTAREA_MAX_HEIGHT_PX = 280;

function transcriptInsertion(
  current: string,
  transcript: string,
  selectionStart: number | null,
  selectionEnd: number | null,
): { value: string; cursor: number } {
  const hasSelection =
    selectionStart !== null &&
    selectionEnd !== null &&
    selectionStart >= 0 &&
    selectionEnd >= selectionStart &&
    selectionEnd <= current.length;

  if (!hasSelection) {
    const prefix = current.trim() ? `${current}\n\n` : "";
    return { value: `${prefix}${transcript}`, cursor: prefix.length + transcript.length };
  }

  const before = current.slice(0, selectionStart);
  const after = current.slice(selectionEnd);
  const replacing = selectionEnd > selectionStart;
  const beforeNeedsParagraph = before.endsWith(":") && after.trim().length === 0;
  const afterStartsWithBoundary = after.length === 0 || /^[\s.,!?;:)\]}]/.test(after);
  const prefix =
    replacing || before.length === 0 || /\s$/.test(before)
      ? ""
      : beforeNeedsParagraph
        ? "\n\n"
        : " ";
  const suffix = replacing || afterStartsWithBoundary ? "" : " ";
  const insertion = `${prefix}${transcript}${suffix}`;

  return {
    value: `${before}${insertion}${after}`,
    cursor: before.length + prefix.length + transcript.length,
  };
}

async function buildComposerInstructions(
  auth: { token: string; orgId: string },
  ref: ChatReferencePick | null,
  files: ComposerFile[],
): Promise<string | undefined> {
  const parts: string[] = [];
  if (ref) {
    if (ref.mode === "full") {
      try {
        const turns = await api.chats.listTurns(auth, ref.chatId);
        const excerpt = turns
          .slice(-4)
          .map((t) =>
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
  const uploaded = files.filter((f) => f.state === "uploaded");
  if (uploaded.length > 0) {
    parts.push(
      `Attached files (reference by name): ${uploaded.map((f) => f.name).join(", ")}`,
    );
    for (const file of uploaded) {
      if (file.textExcerpt?.trim()) {
        parts.push(
          `--- Begin attached file: ${file.name} ---\n${file.textExcerpt.trim()}\n--- End attached file: ${file.name} ---`,
        );
      }
    }
  }
  const text = parts.join("\n\n").trim();
  return text || undefined;
}

const SYSTEM_MODEL_SETS = new Set([
  "referee",
  "set-7edaefc8",
  "balanced",
  "coding",
  "business",
  "research",
]);

export function ChatPage() {
  const {
    modelSets,
    activeModelSetId,
    setActiveModelSetId,
    createModelSet,
    updateModelSet,
    isApiMode,
    activeChatId,
    setActiveChatId,
    createChat,
    chats,
    deleteChat,
    applyChatUpdate,
  } = useChatStore();
  const { authHeaders, isAuthenticated } = useAuth();
  const { modelById } = useModels();
  const navigate = useNavigate();
  const set = modelSets.find((s) => s.id === activeModelSetId) ?? modelSets[0];
  const [apiTurns, setApiTurns] = useState<ApiTurn[]>([]);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [isVoiceActive, setIsVoiceActive] = useState(false);
  const [files, setFiles] = useState<ComposerFile[]>([]);
  const [refChat, setRefChat] = useState<ChatReferencePick | null>(null);
  const [showSet, setShowSet] = useState(false);
  const [showStrategy, setShowStrategy] = useState(false);
  const [showCouncil, setShowCouncil] = useState(false);
  const [showCreateSet, setShowCreateSet] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [showRef, setShowRef] = useState(false);
  const [showExcel, setShowExcel] = useState(false);
  const [showPlus, setShowPlus] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [stoppingTurnId, setStoppingTurnId] = useState<string | null>(null);
  const [pendingSavedVerdicts, setPendingSavedVerdicts] = useState<Set<string>>(() => new Set());
  const [showDeleteChat, setShowDeleteChat] = useState(false);
  const [deletingChat, setDeletingChat] = useState(false);
  const [deleteTurnTarget, setDeleteTurnTarget] = useState<ApiTurn | null>(null);
  const [deletingTurn, setDeletingTurn] = useState(false);
  const [deleteTurnError, setDeleteTurnError] = useState<string | null>(null);
  const [saveTurnId, setSaveTurnId] = useState<string | null>(null);
  const [deletedTurns, setDeletedTurns] = useState<ApiTurn[]>([]);
  const [restoringTurnId, setRestoringTurnId] = useState<string | null>(null);
  const [regeneratingTurnId, setRegeneratingTurnId] = useState<string | null>(null);
  const [pendingEdit, setPendingEdit] = useState<{ turn: ApiTurn; prompt: string } | null>(
    null,
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showScrollToLatest, setShowScrollToLatest] = useState(false);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const shouldPinToBottomRef = useRef(true);
  const showScrollToLatestRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const modelSetRestoredForChatRef = useRef<string | null>(null);
  const modelSetsRef = useRef(modelSets);
  modelSetsRef.current = modelSets;
  const activeChat = chats.find((c) => c.id === activeChatId);
  const pinnedTurnId = activeChat?.pinnedTurnId ?? null;
  const pinnedVerdictId = activeChat?.pinnedVerdictId ?? null;

  const updateThreadScrollState = useCallback(() => {
    const thread = threadRef.current;
    if (!thread) return;
    const metrics = {
      scrollTop: thread.scrollTop,
      scrollHeight: thread.scrollHeight,
      clientHeight: thread.clientHeight,
    };
    const nearBottom = isChatNearBottom(metrics);
    const nextShowButton = shouldShowScrollToLatest(metrics);

    shouldPinToBottomRef.current = nearBottom;
    if (showScrollToLatestRef.current !== nextShowButton) {
      showScrollToLatestRef.current = nextShowButton;
      setShowScrollToLatest(nextShowButton);
    }
  }, []);

  const scrollThreadToLatest = useCallback((behavior: ScrollBehavior = "smooth") => {
    shouldPinToBottomRef.current = true;
    threadEndRef.current?.scrollIntoView({ behavior, block: "end" });
  }, []);

  useEffect(() => {
    const thread = threadRef.current;
    if (!thread) return;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        updateThreadScrollState();
      });
    };

    updateThreadScrollState();
    thread.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      thread.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [updateThreadScrollState]);

  useEffect(() => {
    shouldPinToBottomRef.current = true;
    showScrollToLatestRef.current = false;
    setShowScrollToLatest(false);
    setDeletedTurns([]);
    window.requestAnimationFrame(() => scrollThreadToLatest("auto"));
  }, [activeChatId, scrollThreadToLatest]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const chatId = params.get("chatId");
    if (chatId && chatId !== activeChatId) {
      setActiveChatId(chatId);
    }
  }, [activeChatId, setActiveChatId]);

  useEffect(() => {
    const turnId = new URLSearchParams(window.location.search).get("turnId");
    if (!turnId || apiTurns.length === 0) return;
    const el = document.getElementById(`turn-${turnId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [apiTurns.length, activeChatId]);

  function scrollToPinnedVerdict() {
    if (!pinnedVerdictId && !pinnedTurnId) return;
    // Stop "stick to bottom" from fighting the jump to the pinned synthesis.
    shouldPinToBottomRef.current = false;
    showScrollToLatestRef.current = true;
    setShowScrollToLatest(true);

    const flash = (el: HTMLElement) => {
      el.classList.add("ring-2", "ring-amber-400", "ring-offset-2", "ring-offset-background");
      window.setTimeout(() => {
        el.classList.remove("ring-2", "ring-amber-400", "ring-offset-2", "ring-offset-background");
      }, 1600);
    };

    const attempt = (n: number) => {
      const target = findPinnedSynthesisElement(pinnedVerdictId, pinnedTurnId);
      if (!target) {
        if (n < 12) {
          window.setTimeout(() => attempt(n + 1), 40);
          return;
        }
        toast.error("Could not find the pinned verdict in this chat");
        return;
      }
      const thread = threadRef.current;
      if (thread) {
        scrollThreadToElement(thread, target, "smooth");
      } else {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      flash(target);
      // Second pass after layout settles (images/markdown can shift height).
      window.setTimeout(() => {
        const settled = findPinnedSynthesisElement(pinnedVerdictId, pinnedTurnId);
        if (!settled) return;
        if (threadRef.current) {
          scrollThreadToElement(threadRef.current, settled, "auto");
        } else {
          settled.scrollIntoView({ behavior: "auto", block: "center" });
        }
      }, 220);
    };

    window.requestAnimationFrame(() => attempt(0));
  }

  async function handlePinVerdict(verdictId: string, currentlyPinned: boolean) {
    const auth = authHeaders();
    if (!auth || !activeChatId) return;
    try {
      const updated = currentlyPinned
        ? await api.chats.unpinVerdict(auth, activeChatId)
        : await api.chats.pinVerdict(auth, activeChatId, verdictId);
      applyChatUpdate(updated);
      toast.success(currentlyPinned ? "Verdict unpinned" : "Verdict pinned");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update pin");
    }
  }

  useEffect(() => {
    if (!shouldPinToBottomRef.current) return;
    window.requestAnimationFrame(() => {
      scrollThreadToLatest("auto");
      updateThreadScrollState();
    });
  }, [apiTurns, loading, scrollThreadToLatest, updateThreadScrollState]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT_PX)}px`;
  }, [input]);

  useEffect(() => {
    if (!isApiMode || !activeChatId) {
      setApiTurns([]);
      setLoading(false);
      modelSetRestoredForChatRef.current = null;
      return;
    }
    const auth = authHeaders();
    if (!auth) return;

    let cancelled = false;
    const chatId = activeChatId;

    const unsubTurns = subscribeChatTurns(chatId, setApiTurns);
    const unsubRunning = subscribeChatRunning(chatId, setLoading);
    const unsubActiveTurn = subscribeActiveTurn(chatId, setActiveTurnId);

    void api.chats.listTurns(auth, chatId).then((turns) => {
      if (cancelled) return;
      const merged = mergeWithCachedTurns(chatId, turns);
      seedChatTurns(chatId, merged);
      setApiTurns(merged);

      if (modelSetRestoredForChatRef.current !== chatId) {
        const fromChat = resolveModelSetIdFromTurns(merged);
        const sets = modelSetsRef.current;
        if (fromChat && sets.some((item) => item.id === fromChat)) {
          modelSetRestoredForChatRef.current = chatId;
          setActiveModelSetId(fromChat);
        } else if (!fromChat) {
          // Empty chat: keep current selection (default or manual).
          modelSetRestoredForChatRef.current = chatId;
        }
      }

      void resumeRunningTurns(auth, chatId, turns);
    });

    return () => {
      cancelled = true;
      unsubTurns();
      unsubRunning();
      unsubActiveTurn();
    };
  }, [isApiMode, activeChatId, authHeaders, setActiveModelSetId]);

  useEffect(() => {
    if (!activeChatId || !apiTurns.length || !modelSets.length) return;
    if (modelSetRestoredForChatRef.current === activeChatId) return;
    const fromChat = resolveModelSetIdFromTurns(apiTurns);
    if (fromChat && modelSets.some((item) => item.id === fromChat)) {
      modelSetRestoredForChatRef.current = activeChatId;
      setActiveModelSetId(fromChat);
    }
  }, [activeChatId, apiTurns, modelSets, setActiveModelSetId]);

  function handleVoiceTranscript(result: ApiTranscriptionResponse) {
    const transcript = result.text.trim();
    if (!transcript) return;

    const textarea = textareaRef.current;
    const selectionStart = textarea ? textarea.selectionStart : null;
    const selectionEnd = textarea ? textarea.selectionEnd : null;
    let cursorPosition: number | null = null;

    setInput((current) => {
      const next = transcriptInsertion(current, transcript, selectionStart, selectionEnd);
      cursorPosition = next.cursor;
      return next.value;
    });

    if (typeof window !== "undefined") {
      window.requestAnimationFrame(() => {
        const currentTextarea = textareaRef.current;
        if (!currentTextarea || cursorPosition === null) return;
        currentTextarea.focus();
        currentTextarea.setSelectionRange(cursorPosition, cursorPosition);
      });
    }
  }

  function setVerdictSavedState(verdictId: string, saved: boolean) {
    if (!activeChatId) return;
    setApiTurns((prev) => updateVerdictSavedInTurns(prev, verdictId, saved));
    setCachedVerdictSavedState(verdictId, saved);
  }

  async function toggleSavedVerdict(verdictId: string, saved: boolean) {
    const auth = authHeaders();
    if (!auth || pendingSavedVerdicts.has(verdictId)) return;

    const nextSaved = !saved;
    setPendingSavedVerdicts((prev) => new Set(prev).add(verdictId));
    setVerdictSavedState(verdictId, nextSaved);
    try {
      if (nextSaved) {
        await api.verdicts.save(auth, verdictId);
        toast.success("Verdict saved");
      } else {
        await api.verdicts.unsave(auth, verdictId);
        toast.success("Verdict removed from saved items");
      }
    } catch (error) {
      setVerdictSavedState(verdictId, saved);
      throw error;
    } finally {
      setPendingSavedVerdicts((prev) => {
        const next = new Set(prev);
        next.delete(verdictId);
        return next;
      });
    }
  }

  async function send() {
    if (isVoiceActive || !input.trim() || !set) return;
    const question = input.trim();
    setInput("");
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setSending(true);
    try {
      let chatId = activeChatId;
      if (!chatId) chatId = await createChat();
      if (!chatId) return;
      const customInstructions = await buildComposerInstructions(auth, refChat, files);
      const pending = await api.chats.createTurn(auth, chatId, {
        user_message: question,
        model_set_id: set.id,
        custom_instructions: customInstructions,
      });
      scrollThreadToLatest("smooth");
      setRefChat(null);
      setFiles([]);
      void runTurnInBackground(auth, chatId, pending).catch((error) => {
        console.error(error);
        alert(error instanceof Error ? error.message : "Failed to run turn");
      });
    } catch (error) {
      console.error(error);
      alert(error instanceof Error ? error.message : "Failed to run turn");
    } finally {
      setSending(false);
    }
  }

  async function stopGenerating() {
    const auth = authHeaders();
    if (!auth || !activeChatId || !activeTurnId || stoppingTurnId === activeTurnId) return;
    setStoppingTurnId(activeTurnId);
    setLoading(false);
    setSending(false);
    try {
      await stopActiveTurn(auth, activeChatId);
    } catch (error) {
      console.error(error);
      alert(error instanceof Error ? error.message : "Failed to stop generating");
    } finally {
      setStoppingTurnId(null);
    }
  }

  async function confirmDeleteTurn() {
    const auth = authHeaders();
    if (!auth || !activeChatId || !deleteTurnTarget || deletingTurn) return;
    if (anyTurnGenerating || !canShowHistoricalTurnDelete(deleteTurnTarget)) {
      setDeleteTurnError("Wait for generation to finish or stop it before deleting a turn.");
      return;
    }
    const target = deleteTurnTarget;
    setDeletingTurn(true);
    setDeleteTurnError(null);
    try {
      await api.chats.deleteTurn(auth, activeChatId, target.id);
      removeTurn(activeChatId, target.id);
      setApiTurns((prev) => removeTurnFromList(prev, target.id));
      setDeletedTurns((prev) => [target, ...prev.filter((t) => t.id !== target.id)]);
      setDeleteTurnTarget(null);
      toast.success("Turn deleted. Use Undo below to restore anytime.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to delete turn";
      setDeleteTurnError(message);
      toast.error(message);
    } finally {
      setDeletingTurn(false);
    }
  }

  async function regenerateEditedPrompt(turn: ApiTurn, prompt: string) {
    const auth = authHeaders();
    if (!auth || !activeChatId || regeneratingTurnId) {
      throw new Error("Cannot regenerate right now");
    }
    if (!canEditUserPrompt(turn, apiTurns)) {
      toast.error("Wait for generation to finish before editing a prompt.");
      throw new Error("Turn is not editable");
    }

    setRegeneratingTurnId(turn.id);
    try {
      const result = await api.chats.regenerateTurn(auth, activeChatId, turn.id, { prompt });
      for (const supersededId of result.superseded_turn_ids) {
        removeTurn(activeChatId, supersededId);
      }
      setApiTurns((prev) => {
        const remaining = prev.filter((item) => !result.superseded_turn_ids.includes(item.id));
        return [...remaining, result.new_turn].sort((a, b) =>
          a.created_at.localeCompare(b.created_at),
        );
      });
      setPendingEdit(null);
      if (result.model_set_fallback) {
        toast.warning(
          "Original model set was unavailable. Regenerated with a fallback model set.",
        );
      }
      scrollThreadToLatest("smooth");
      void runTurnInBackground(auth, activeChatId, result.new_turn).catch((error) => {
        console.error(error);
        toast.error(error instanceof Error ? error.message : "Failed to regenerate turn");
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to regenerate turn";
      toast.error(message);
      throw error instanceof Error ? error : new Error(message);
    } finally {
      setRegeneratingTurnId(null);
    }
  }

  async function requestPromptEdit(turn: ApiTurn, prompt: string) {
    if (countLaterTurns(apiTurns, turn.id) > 0) {
      setPendingEdit({ turn, prompt });
      return;
    }
    await regenerateEditedPrompt(turn, prompt);
  }

  async function undoDeletedTurn(turnId: string) {
    const auth = authHeaders();
    if (!auth || !activeChatId || restoringTurnId) return;
    setRestoringTurnId(turnId);
    try {
      const restored = await api.chats.restoreTurn(auth, activeChatId, turnId);
      setDeletedTurns((prev) => prev.filter((t) => t.id !== turnId));
      restoreRemovedTurn(activeChatId, restored);
      setApiTurns((prev) => {
        if (prev.some((t) => t.id === restored.id)) return prev;
        return [...prev, restored].sort(
          (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
        );
      });
      toast.success("Turn restored.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to restore turn");
    } finally {
      setRestoringTurnId(null);
    }
  }

  async function uploadComposerFiles(fileList: FileList | null) {
    if (!fileList?.length) return;
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    let chatId = activeChatId;
    if (!chatId) {
      chatId = await createChat();
      if (!chatId) return;
    }
    for (const file of Array.from(fileList)) {
      const localId = `${Date.now()}-${file.name}-${Math.random().toString(36).slice(2, 8)}`;
      setFiles((prev) => [...prev, { localId, name: file.name, state: "uploading" }]);
      try {
        const uploaded = await api.chats.uploadAttachment(auth, chatId, file);
        setFiles((prev) =>
          prev.map((item) =>
            item.localId === localId
              ? {
                  ...item,
                  state: "uploaded" as const,
                  attachmentId: uploaded.id,
                  textExcerpt: uploaded.text_excerpt,
                }
              : item,
          ),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : "Upload failed";
        setFiles((prev) =>
          prev.map((item) =>
            item.localId === localId
              ? { ...item, state: "error" as const, errorMessage: message }
              : item,
          ),
        );
        toast.error(`${file.name}: ${message}`);
      }
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
  const voiceAuth = authHeaders();
  const voiceDisabled = !voiceAuth || !set || sending || loading;
  const anyTurnGenerating = isAnyTurnGenerating(apiTurns) || loading;
  const turnDeleteDisabled = isHistoricalTurnDeleteDisabled(anyTurnGenerating);
  const [turnLayout, setTurnLayout] = useChatTurnLayout();

  return (
    <AppShell>
      <div className="relative flex h-[calc(100vh-3.5rem)] flex-col md:h-screen">
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
                  <VendorLogo
                    key={id}
                    vendor={m.vendor}
                    title={m.name}
                    className="size-5"
                  />
                );
              })}
              <span className="ml-2 text-xs text-muted-foreground">{set.strategy}</span>
              <button
                type="button"
                onClick={() => setShowCouncil(true)}
                className="ml-1 text-xs font-medium text-primary hover:underline"
              >
                Edit council ({set.models.length} models)
              </button>
              <button
                onClick={() => setShowStrategy(true)}
                className="text-muted-foreground hover:text-foreground"
              >
                <Info className="size-3.5" />
              </button>
            </div>
          )}
          <div className="ml-auto flex items-center gap-2">
            <ChatTurnLayoutToggle value={turnLayout} onChange={setTurnLayout} />
            {pinnedTurnId && (
              <button
                type="button"
                onClick={scrollToPinnedVerdict}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-xs font-semibold text-amber-800 dark:text-amber-300"
              >
                <Pin className="size-3.5 fill-current" /> Go to pinned verdict
              </button>
            )}
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
        <div ref={threadRef} className="flex-1 overflow-y-auto px-4 pt-6 pb-16 md:px-6 xl:px-8">
          <div className="mx-auto max-w-6xl space-y-10">
            {!isAuthenticated && (
              <GlassCard glow className="p-10 text-center animate-fade-up">
                <Sparkles className="mx-auto size-8 text-primary" />
                <h2 className="mt-4 text-2xl font-semibold text-gradient">
                  One question. Many minds.
                </h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  GPT-4.1, Claude Sonnet 4, Gemini 2.5 Pro, Grok, DeepSeek V3 — real models via
                  OpenRouter, one verdict.
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
              <div className="elevate-hero space-y-8 py-10 text-center md:py-14">
                <p className="text-[11px] font-semibold uppercase tracking-[0.32em] text-primary">
                  01 — Chat Council
                </p>
                <h2 className="font-display text-4xl tracking-tight md:text-6xl">
                  Five minds.{" "}
                  <span className="text-gradient italic">One verdict.</span>
                </h2>
                <p className="mx-auto max-w-xl text-sm text-muted-foreground md:text-base">
                  Ask once. Compare frontier models. Decide with clarity — Verdict AI uses{" "}
                  <strong className="text-foreground">{set.strategy}</strong>.
                </p>
                <button
                  type="button"
                  onClick={() => setShowCouncil(true)}
                  className="inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/5 px-5 py-2.5 text-sm font-medium text-primary shadow-sm hover:bg-primary/10"
                >
                  Choose your models
                </button>
                <div className="mx-auto grid max-w-5xl gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  {set.models.map((id, index) => {
                    const model = modelById(id);
                    return (
                      <ModelPill
                        key={id}
                        name={model.name}
                        vendor={model.vendor}
                        color={model.color}
                        variant="cinematic"
                        index={index}
                      />
                    );
                  })}
                </div>
              </div>
            )}

            {set &&
              apiTurns.map((turn) => {
                const showTurnDelete = canShowHistoricalTurnDelete(turn);
                const showPromptEdit = canShowHistoricalTurnDelete(turn);
                const promptDisabledReason =
                  regeneratingTurnId && regeneratingTurnId !== turn.id
                    ? "Regeneration in progress"
                    : anyTurnGenerating
                      ? "Wait for generation to finish"
                      : undefined;
                return (
                  <div
                    key={turn.id}
                    id={`turn-${turn.id}`}
                    className="scroll-mt-28 space-y-6 animate-fade-up"
                  >
                    <div className="flex items-start justify-end gap-2">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            aria-label="Turn options"
                            className="mt-1 rounded-lg border border-border bg-card/70 p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                          >
                            <MoreHorizontal className="size-4" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-48">
                          {turn.verdict && (
                            <DropdownMenuItem onSelect={() => setSaveTurnId(turn.id)}>
                              <FilePlus2 className="size-3.5" /> Save as document
                            </DropdownMenuItem>
                          )}
                          {showTurnDelete && (
                            <DropdownMenuItem
                              disabled={turnDeleteDisabled}
                              className="text-destructive focus:text-destructive"
                              onSelect={() => {
                                if (turnDeleteDisabled) return;
                                setDeleteTurnError(null);
                                setDeleteTurnTarget(turn);
                              }}
                            >
                              <Trash2 className="size-3.5" /> Delete turn
                            </DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                      <UserPromptBubble
                        turnId={turn.id}
                        message={turn.user_message}
                        editable={showPromptEdit}
                        disabledReason={
                          canEditUserPrompt(turn, apiTurns) && !regeneratingTurnId
                            ? undefined
                            : promptDisabledReason
                        }
                        submitting={regeneratingTurnId === turn.id}
                        onSubmit={(prompt) => requestPromptEdit(turn, prompt)}
                      />
                    </div>
                    <AiTurn
                      set={set}
                      turn={turn}
                      modelById={modelById}
                      pendingSavedVerdicts={pendingSavedVerdicts}
                      pinnedVerdictId={pinnedVerdictId}
                      onTogglePin={(verdictId, currentlyPinned) =>
                        void handlePinVerdict(verdictId, currentlyPinned)
                      }
                      onToggleSavedVerdict={(verdictId, saved) =>
                        toggleSavedVerdict(verdictId, saved).catch((error) => {
                          toast.error(
                            error instanceof Error
                              ? error.message
                              : "Failed to update saved verdict",
                          );
                        })
                      }
                      onLessonUpdate={(lessonId, lessonStatus) => {
                        setApiTurns((prev) =>
                          prev.map((t) =>
                            t.id === turn.id
                              ? { ...t, lesson_id: lessonId, lesson_status: lessonStatus }
                              : t,
                          ),
                        );
                      }}
                    />
                  </div>
                );
              })}

            {loading &&
              set &&
              !apiTurns.some((t) => t.status === "pending" || t.status === "running") && (
                <LoadingTurn set={set} modelById={modelById} />
              )}
            <div ref={threadEndRef} aria-hidden className="h-px" />
          </div>
        </div>

        <div className="pointer-events-none relative z-30">
          {showScrollToLatest && (
            <button
              type="button"
              aria-label="Scroll to latest message"
              onClick={() => scrollThreadToLatest("smooth")}
              className="pointer-events-auto absolute bottom-3 left-1/2 grid size-9 -translate-x-1/2 place-items-center rounded-full border border-border/80 bg-card/90 text-foreground shadow-lg shadow-primary/10 backdrop-blur transition hover:border-primary/40 hover:bg-accent focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
            >
              <ArrowDown className="size-4" />
            </button>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-border bg-background px-4 py-4 md:px-6 xl:px-8">
          <div className="mx-auto max-w-6xl">
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
            {files.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {files.map((f) => (
                  <div
                    key={f.localId}
                    className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs"
                    title={f.errorMessage}
                  >
                    {f.state === "uploading" && <Loader2 className="size-3 animate-spin" />}
                    {f.state === "error" && <AlertCircle className="size-3 text-destructive" />}
                    <span className={cn(f.state === "error" && "text-destructive")}>{f.name}</span>
                    <button
                      type="button"
                      onClick={() => setFiles((arr) => arr.filter((item) => item.localId !== f.localId))}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {deletedTurns.length > 0 && (
              <div className="mb-2 space-y-2">
                {deletedTurns.map((turn) => (
                  <div
                    key={turn.id}
                    className="flex items-center justify-between gap-3 rounded-xl border border-border bg-muted/40 px-3 py-2 text-sm"
                  >
                    <span className="min-w-0 truncate text-muted-foreground">
                      Deleted: {turn.user_message.slice(0, 80)}
                      {turn.user_message.length > 80 ? "…" : ""}
                    </span>
                    <button
                      type="button"
                      disabled={restoringTurnId === turn.id}
                      onClick={() => void undoDeletedTurn(turn.id)}
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1 text-xs font-medium hover:bg-accent disabled:opacity-50"
                    >
                      {restoringTurnId === turn.id ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Undo2 className="size-3.5" />
                      )}
                      Undo
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="rounded-[1.35rem] border border-border/90 bg-card/95 shadow-[0_8px_28px_oklch(0.45_0.04_240/0.08)] ring-1 ring-primary/5">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                rows={2}
                disabled={!isAuthenticated || !set}
                placeholder={
                  isAuthenticated
                    ? "Ask your model council anything… (Enter for new line; ⌘↵ / Ctrl+Enter to send)"
                    : "Log in to chat"
                }
                className="block max-h-[280px] min-h-[3.5rem] w-full resize-none overflow-y-auto rounded-[1.35rem] bg-transparent px-4 pt-3 pb-2 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
              />
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  void uploadComposerFiles(e.target.files);
                  e.target.value = "";
                }}
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
                          fileInputRef.current?.click();
                        }}
                      />
                      <ComposerMenuItem
                        icon={ImageIcon}
                        label="Upload image"
                        onClick={() => {
                          setShowPlus(false);
                          if (fileInputRef.current) {
                            fileInputRef.current.accept = "image/*";
                            fileInputRef.current.click();
                            fileInputRef.current.accept = "";
                          }
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
                <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1">
                  <VoiceRecorderButton
                    auth={voiceAuth}
                    disabled={voiceDisabled}
                    onTranscript={handleVoiceTranscript}
                    onRecordingStateChange={setIsVoiceActive}
                  />
                  {loading && activeTurnId ? (
                    <button
                      type="button"
                      onClick={() => void stopGenerating()}
                      disabled={stoppingTurnId === activeTurnId}
                      aria-label="Stop generating"
                      className="inline-flex items-center gap-2 rounded-xl bg-destructive px-3.5 py-2 text-sm font-medium text-destructive-foreground shadow-sm hover:bg-destructive/90 disabled:opacity-40"
                    >
                      {stoppingTurnId === activeTurnId ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Square className="size-3.5 fill-current" />
                      )}
                      {stoppingTurnId === activeTurnId ? "Stopping..." : "Stop generating"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void send()}
                      disabled={
                        !input.trim() ||
                        sending ||
                        loading ||
                        !isAuthenticated ||
                        !set ||
                        isVoiceActive
                      }
                      className="inline-flex items-center gap-2 rounded-xl bg-primary px-3.5 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-40"
                    >
                      <Send className="size-3.5" />
                      Send
                    </button>
                  )}
                </div>
              </div>
            </div>
            <p className="mt-2 text-center text-[11px] text-muted-foreground">
              MultiAI may produce inaccurate information. Review important outputs before acting.
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

      <ModelSetModal
        open={showCreateSet}
        onClose={() => setShowCreateSet(false)}
        onCreate={createModelSet}
      />

      <Modal
        open={showStrategy}
        onClose={() => setShowStrategy(false)}
        title="Verdict strategy"
        size="md"
      >
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
          setFiles((f) => [
            ...f,
            {
              localId: `excel-${Date.now()}`,
              name: "comparison.xlsx",
              state: "uploaded",
              textExcerpt: "Excel comparison attached from Generate Excel.",
            },
          ])
        }
      />

      <Modal
        open={!!deleteTurnTarget}
        onClose={() => {
          if (deletingTurn) return;
          setDeleteTurnTarget(null);
          setDeleteTurnError(null);
        }}
        title="Delete this turn?"
        size="sm"
      >
        <p className="text-sm text-muted-foreground">
          This removes the turn from the chat. You can restore it anytime with Undo — there is no
          time limit.
        </p>
        {deleteTurnError && <p className="mt-3 text-sm text-destructive">{deleteTurnError}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => {
              setDeleteTurnTarget(null);
              setDeleteTurnError(null);
            }}
            disabled={deletingTurn}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={deletingTurn || !deleteTurnTarget}
            onClick={() => void confirmDeleteTurn()}
            className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground disabled:opacity-50"
          >
            {deletingTurn ? "Deleting..." : "Delete turn"}
          </button>
        </div>
      </Modal>

      <Modal
        open={!!pendingEdit}
        onClose={() => {
          if (regeneratingTurnId) return;
          setPendingEdit(null);
        }}
        title="Regenerate this turn?"
        size="sm"
      >
        <p className="text-sm text-muted-foreground">{LATER_TURNS_EDIT_WARNING}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setPendingEdit(null)}
            disabled={Boolean(regeneratingTurnId)}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!pendingEdit || Boolean(regeneratingTurnId)}
            onClick={() => {
              if (!pendingEdit) return;
              void regenerateEditedPrompt(pendingEdit.turn, pendingEdit.prompt).catch(() => {
                // Error toast handled in regenerateEditedPrompt; keep modal open on failure.
              });
            }}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {regeneratingTurnId ? "Regenerating…" : "Save and regenerate"}
          </button>
        </div>
      </Modal>

      <SaveTurnDialog
        open={Boolean(saveTurnId)}
        turnId={saveTurnId}
        onClose={() => setSaveTurnId(null)}
      />

      <Modal
        open={showDeleteChat}
        onClose={() => setShowDeleteChat(false)}
        title="Delete chat?"
        size="sm"
      >
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
  modelById: (id: string) => { name: string; color: string; vendor: string };
}) {
  const [layout] = useChatTurnLayout();
  return (
    <div className="space-y-4">
      <div
        className={chatAnswerCardsClassName(layout)}
        data-chat-answer-layout={layout}
        data-testid="loading-answer-layout"
      >
        {set.models.map((id, index) => {
          const m = modelById(id);
          return (
            <GlassCard
              key={id}
              className={cn(
                "elevate-card relative min-h-[140px] w-full overflow-hidden border-l-[3px] p-4 sm:p-5",
                index > 0 && `elevate-card-delay-${Math.min(index, 4)}`,
              )}
              style={{ borderLeftColor: m.color } satisfies CSSProperties}
            >
              <VendorLogo
                vendor={m.vendor}
                watermark
                className="pointer-events-none absolute -right-2 -bottom-2 size-20"
              />
              <div className="relative flex flex-wrap items-center gap-2 text-sm font-medium">
                <VendorLogo vendor={m.vendor} className="size-7 shrink-0" title={m.name} />
                <div className="min-w-0">
                  <div className="truncate leading-tight">{m.name}</div>
                  <div className="truncate text-[11px] font-normal text-muted-foreground">
                    {m.vendor}
                  </div>
                </div>
                <Loader2 className="ml-auto size-3.5 shrink-0 animate-spin text-primary" />
              </div>
              <div className="relative mt-4 space-y-2 border-t border-border/60 pt-4">
                <div className="h-2 animate-pulse rounded bg-muted" />
                <div className="h-2 w-10/12 animate-pulse rounded bg-muted" />
              </div>
            </GlassCard>
          );
        })}
      </div>
      <GlassCard
        glow
        className="mt-2 w-full border-2 border-primary/25 bg-primary/[0.04] p-5 text-sm text-muted-foreground sm:p-6"
        data-testid="loading-verdict"
      >
        <Loader2 className="mr-2 inline size-3.5 animate-spin text-primary" /> Synthesizing
        verdict…
      </GlassCard>
    </div>
  );
}

function inferTopModelId(
  turn: ApiTurn,
  councilModelIds: string[],
  modelById: (id: string) => { name: string },
): string | null {
  const completed = (turn.model_answers ?? []).filter(
    (a) => a.status === "completed" && a.confidence != null,
  );
  const strategy = (turn.verdict?.strategy ?? turn.strategy) as Strategy;

  if (strategy === "Pick Best" && turn.verdict) {
    const excerpt = `${turn.verdict.text}\n${turn.verdict.reason}`.toLowerCase();
    for (const id of councilModelIds) {
      if (excerpt.includes(modelById(id).name.toLowerCase())) return id;
    }
  }

  if (!completed.length) return null;
  return completed.reduce((best, answer) =>
    (answer.confidence ?? 0) > (best.confidence ?? 0) ? answer : best,
  ).model_id;
}

function AiTurn({
  set,
  turn,
  modelById,
  pendingSavedVerdicts,
  pinnedVerdictId,
  onTogglePin,
  onToggleSavedVerdict,
  onLessonUpdate,
}: {
  set: ModelSet;
  turn: ApiTurn;
  modelById: (id: string) => { name: string; color: string; vendor: string };
  pendingSavedVerdicts: Set<string>;
  pinnedVerdictId?: string | null;
  onTogglePin: (verdictId: string, currentlyPinned: boolean) => void;
  onToggleSavedVerdict: (verdictId: string, saved: boolean) => void;
  onLessonUpdate: (lessonId: string, lessonStatus: string) => void;
}) {
  const { session } = useAuth();
  const [layout] = useChatTurnLayout();
  const { isExpanded, toggle: toggleAnswerExpansion } = useTurnAnswerExpansion(layout);
  const [showDisagree, setShowDisagree] = useState(false);
  const [answersCollapsed, setAnswersCollapsed] = useState(false);
  const verdictRef = useRef<HTMLDivElement>(null);
  const answerCards = deriveTurnAnswerCards(turn, set.models);
  const cardModelIds = answerCards.map((card) => card.modelId);

  const topModelId = turn.verdict ? inferTopModelId(turn, cardModelIds, modelById) : null;
  const judgeModel = turn.verdict ? modelById(turn.verdict.model_id) : null;
  const canCollapseAnswers = Boolean(turn.verdict);
  const turnStrategy = (turn.verdict?.strategy ?? turn.strategy) as Strategy;
  const bookmarkState = getVerdictBookmarkState(turn, pendingSavedVerdicts);
  const isPinned = Boolean(turn.verdict && pinnedVerdictId === turn.verdict.id);
  const hasVerdict = Boolean(turn.verdict);

  function openDisagree() {
    verdictRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    setShowDisagree(true);
  }

  const responseCards = !answersCollapsed ? (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">
            AI Council
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {answerCards.length} models · {answerCards.length} perspectives
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          {!hasVerdict ? (
            <TurnCostSummary answers={turn.model_answers ?? []} />
          ) : null}
          {canCollapseAnswers ? (
            <button
              type="button"
              onClick={() => setAnswersCollapsed(true)}
              className="rounded-lg border border-border bg-card/70 px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              Hide
            </button>
          ) : null}
        </div>
      </div>
      <div
        className={chatAnswerCardsClassName(layout)}
        data-chat-answer-layout={layout}
        data-testid="answer-layout"
      >
        {answerCards.map(({ modelId: id, answer: a, status }, index) => {
          const baseModel = modelById(id);
          const m = a?.model_name ? { ...baseModel, name: a.model_name } : baseModel;
          const failed = status === "failed";
          const inProgress = status === "pending" || status === "running";
          const isTopPick = topModelId === id;
          const expanded = isExpanded(id, hasVerdict);
          return (
            <GlassCard
              key={id}
              featured={isTopPick}
              style={{ borderLeftColor: m.color } satisfies CSSProperties}
              className={cn(
                "elevate-card relative w-full min-w-0 overflow-hidden border-l-[3px] p-4 sm:p-5",
                index > 0 && `elevate-card-delay-${Math.min(index, 4)}`,
                isTopPick && "ring-1 ring-primary/35",
              )}
            >
              <div className="relative flex items-start gap-2.5 sm:gap-3">
                <VendorLogo vendor={m.vendor} className="size-8 shrink-0 sm:size-9" title={m.name} />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 text-sm font-semibold leading-tight sm:text-[0.9375rem]">
                      {m.name}
                    </div>
                    {inProgress ? (
                      <Loader2
                        className="mt-0.5 size-3.5 shrink-0 animate-spin text-primary"
                        aria-label="Generating"
                      />
                    ) : null}
                  </div>
                  <div className="text-[11px] leading-tight text-muted-foreground sm:text-xs">
                    {m.vendor}
                  </div>
                  {!inProgress ? (
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pt-0.5">
                      <CallCostLabel cost={a?.cost_usd} />
                      {a?.confidence != null ? (
                        <ModelConfidenceBadge
                          confidence={a.confidence}
                          isTopPick={isTopPick}
                          strategy={turnStrategy}
                          modelName={m.name}
                        />
                      ) : null}
                      {isTopPick ? (
                        <span className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800">
                          <Trophy className="size-3" />
                          Top pick
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="relative mt-3 border-t border-border/60 pt-3 sm:mt-4 sm:pt-4">
                {failed ? (
                  <p className="text-sm text-destructive">
                    <AlertCircle className="mr-1 inline size-3.5" />
                    {a?.error_message ?? "Failed"}
                  </p>
                ) : inProgress ? (
                  <div className="space-y-2">
                    <div className="h-2 animate-pulse rounded bg-muted" />
                    <div className="h-2 w-10/12 animate-pulse rounded bg-muted" />
                    <div className="h-2 w-8/12 animate-pulse rounded bg-muted" />
                  </div>
                ) : (
                  <ExpandableAnswer
                    collapsible={hasVerdict}
                    expanded={expanded}
                    onToggle={() => toggleAnswerExpansion(id)}
                  >
                    <MessageContent>{a?.text ?? ""}</MessageContent>
                  </ExpandableAnswer>
                )}
              </div>
            </GlassCard>
          );
        })}
      </div>
    </div>
  ) : (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <button
        type="button"
        onClick={() => setAnswersCollapsed(false)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card/70 px-3 py-1.5 text-xs font-medium text-muted-foreground transition hover:bg-accent hover:text-foreground"
      >
        <ChevronDown className="size-3.5 -rotate-90" />
        Show AI council ({answerCards.length})
      </button>
      {!hasVerdict ? <TurnCostSummary answers={turn.model_answers ?? []} /> : null}
    </div>
  );

  const verdictBlock = turn.verdict ? (
    <div
      ref={verdictRef}
      id={`verdict-${turn.verdict.id}`}
      data-verdict-synthesis="true"
      className={cn(
        "elevate-verdict scroll-mt-28 pt-2",
        isPinned && "rounded-2xl ring-2 ring-amber-400/60 ring-offset-2 ring-offset-background",
      )}
    >
      <GlassCard
        glow
        className="border-2 border-primary/30 bg-primary/[0.04] p-5 shadow-[0_1px_0_oklch(1_0_0/0.9)_inset,0_16px_44px_oklch(0.55_0.1_240/0.14)] sm:p-6"
      >
        <div className="flex flex-wrap items-start gap-3 border-b border-primary/15 pb-4">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <Sparkles className="size-5" />
          </span>
          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold tracking-tight sm:text-xl">Verdict</h3>
              {isPinned && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800">
                  <Pin className="size-3 fill-current" /> Pinned
                </span>
              )}
              <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
                {turn.verdict.strategy}
              </span>
              <CallCostLabel cost={turn.verdict.cost_usd} kind="verdict" />
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground sm:text-sm">
              {judgeModel && (
                <span className="inline-flex items-center gap-1.5">
                  <VendorLogo vendor={judgeModel.vendor} className="size-4" />
                  Judge: {judgeModel.name}
                </span>
              )}
              {topModelId && (
                <span className="inline-flex items-center gap-1 font-medium text-amber-800">
                  <Trophy className="size-3.5" />
                  Best: {modelById(topModelId).name}
                </span>
              )}
            </div>
          </div>
          <div className="ml-auto flex min-w-0 max-w-full shrink-0 flex-col items-stretch gap-2 sm:items-end">
            <TurnCostSummary
              answers={turn.model_answers ?? []}
              verdictCost={turn.verdict.cost_usd}
            />
            <div className="flex flex-wrap items-center justify-end gap-2">
              <VerdictCopyButton text={turn.verdict.text} />
              <button
                type="button"
                aria-label={isPinned ? "Unpin verdict" : "Pin verdict"}
                title={isPinned ? "Unpin verdict" : "Pin verdict in this chat"}
                onClick={() => onTogglePin(turn.verdict!.id, isPinned)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition",
                  isPinned
                    ? "border-amber-500/40 bg-amber-500/10 text-amber-800"
                    : "border-border bg-background/60 text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <Pin className={cn("size-3.5", isPinned && "fill-current")} />
                {isPinned ? "Unpin" : "Pin"}
              </button>
            {bookmarkState.visible && bookmarkState.verdictId && (
              <button
                type="button"
                aria-label={bookmarkState.label}
                title={bookmarkState.title}
                disabled={bookmarkState.disabled}
                onClick={() =>
                  onToggleSavedVerdict(bookmarkState.verdictId!, bookmarkState.saved)
                }
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
                  bookmarkState.saved
                    ? "border-primary/40 bg-primary/10 text-primary hover:bg-primary/15"
                    : "border-border bg-background/60 text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <Bookmark
                  className={cn("size-3.5", bookmarkState.filled && "fill-current")}
                />
                {bookmarkState.disabled
                  ? "Saving"
                  : bookmarkState.saved
                    ? "Saved"
                    : "Save"}
              </button>
            )}
            {turn.lesson_id && turn.lesson_status === "completed" ? (
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
                onClick={openDisagree}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm hover:bg-primary/90"
              >
                <Swords className="size-3.5" /> Challenge
              </button>
            )}
            </div>
          </div>
        </div>
        <div className="mt-5 space-y-3">
          <MessageContent>{turn.verdict.text}</MessageContent>
          {turn.verdict.reason && (
            <MessageContent
              muted
              className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2.5"
            >
              {turn.verdict.reason}
            </MessageContent>
          )}
        </div>
      </GlassCard>
    </div>
  ) : null;

  return (
    <div className="space-y-4">
      {responseCards}
      {verdictBlock}

      <VerdictDisagreeChat
        open={showDisagree}
        onClose={() => setShowDisagree(false)}
        turnId={turn.id}
        userName={session?.user.full_name ?? "You"}
        onDiscussStart={(lessonId) => onLessonUpdate(lessonId, "discussing")}
        onLessonBuilt={(lessonId) => onLessonUpdate(lessonId, "completed")}
      />
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
  const { createModelSet, updateModelSet, deleteModelSet } = useChatStore();
  const [editing, setEditing] = useState<ModelSet | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-foreground/25 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-2xl border border-border bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Model sets</h3>
          <button
            onClick={onCreate}
            className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-sm text-primary-foreground"
          >
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
                s.id === activeId
                  ? "border-primary bg-primary/10"
                  : "border-border hover:border-primary/30",
              )}
            >
              {s.id === activeId && (
                <CheckCircle2 className="absolute right-3 top-3 size-4 text-primary" />
              )}
              <div className="font-medium">{s.name}</div>
              <p className="mt-1 text-xs text-muted-foreground">{s.description}</p>
              <div className="mt-3 flex flex-wrap gap-1">
                {s.models.map((id) => (
                  <span
                    key={id}
                    className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[10px]"
                  >
                    <span
                      className="size-1.5 rounded-full"
                      style={{ background: modelById(id).color }}
                    />
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
        <button
          onClick={onClose}
          className="mt-4 text-sm text-muted-foreground hover:text-foreground"
        >
          Close
        </button>
      </div>
      <ModelSetModal
        open={!!editing}
        onClose={() => setEditing(null)}
        initial={editing}
        onUpdate={async (s) => {
          if (SYSTEM_MODEL_SETS.has(s.id)) {
            const created = await createModelSet({
              ...s,
              name: s.name.startsWith("My ") ? s.name : `My ${s.name}`,
            });

            onPick(created.id);
          } else {
            await updateModelSet(s);
          }

          setEditing(null);
        }}
      />
      <Modal open={!!deleteId} onClose={() => setDeleteId(null)} title="Delete set?" size="sm">
        <div className="flex justify-end gap-2">
          <button
            onClick={() => setDeleteId(null)}
            className="rounded-lg border border-border px-4 py-2 text-sm"
          >
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
