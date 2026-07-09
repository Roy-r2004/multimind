import { useState } from "react";
import { Loader2, Wand2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function PromptBuilderModal({
  open,
  onClose,
  onUse,
}: {
  open: boolean;
  onClose: () => void;
  onUse: (text: string) => void;
}) {
  const [raw, setRaw] = useState("");
  const [improved, setImproved] = useState("");
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { authHeaders } = useAuth();

  function close() {
    setRaw("");
    setImproved("");
    setGenerating(false);
    setError(null);
    onClose();
  }

  async function generatePrompt() {
    const trimmed = raw.trim();
    if (!trimmed) {
      setError("Could not improve prompt. Please try again.");
      return;
    }
    const auth = authHeaders();
    if (!auth) {
      setError("Could not improve prompt. Please try again.");
      return;
    }

    setGenerating(true);
    setError(null);
    try {
      const response = await api.promptBuilder.improve(auth, { raw_prompt: trimmed });
      setImproved(response.improved_prompt);
    } catch {
      setError("Could not improve prompt. Please try again.");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <Modal open={open} onClose={close} title="Prompt Builder" size="lg">
      <div className="space-y-6">
        <div>
          <div className="mb-2 text-sm font-medium">What do you want help with?</div>
          <textarea
            value={raw}
            onChange={(e) => {
              setRaw(e.target.value);
              setError(null);
            }}
            rows={6}
            placeholder="Write me a landing page for my AI startup"
            className="w-full rounded-xl border border-border bg-background px-3 py-3 text-sm outline-none focus:border-primary/50"
          />
        </div>
        <button
          type="button"
          onClick={generatePrompt}
          disabled={generating}
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
        >
          {generating ? <Loader2 className="size-4 animate-spin" /> : <Wand2 className="size-4" />}
          {generating ? "Generating..." : "Generate better prompt"}
        </button>
        {error && <div className="text-sm text-destructive">{error}</div>}
        <div>
          <div className="mb-2 text-sm font-medium">Improved prompt</div>
          <textarea
            value={improved}
            onChange={(e) => {
              setImproved(e.target.value);
              setError(null);
            }}
            rows={8}
            placeholder="Your improved prompt will appear here."
            className="min-h-[180px] w-full rounded-xl border border-border bg-accent/20 p-4 text-sm outline-none focus:border-primary/50"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                if (improved) {
                  onUse(improved);
                  close();
                }
              }}
              disabled={!improved}
              className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              Use prompt
            </button>
            <button
              type="button"
              onClick={() => void navigator.clipboard.writeText(improved)}
              disabled={!improved}
              className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
            >
              Copy
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
