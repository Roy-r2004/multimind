import { useState } from "react";
import { Wand2 } from "lucide-react";
import { Modal } from "@/components/Modal";

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
            className="w-full rounded-xl border border-white/10 bg-background px-3 py-3 text-sm outline-none focus:border-primary/50"
          />
        </div>
        <button
          type="button"
          onClick={generatePrompt}
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          <Wand2 className="size-4" /> Generate better prompt
        </button>
        <div>
          <div className="mb-2 text-sm font-medium">Improved prompt</div>
          <div className="min-h-[120px] rounded-xl border border-white/10 bg-accent/20 p-4 text-sm">
            {improved || (
              <span className="text-muted-foreground">Your improved prompt will appear here.</span>
            )}
          </div>
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
              className="rounded-xl border border-white/10 px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50"
            >
              Copy
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
