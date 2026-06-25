import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Modal } from "@/components/Modal";

export function VerdictDisagreeModal({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (data: { reason: string; user_position: string }) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [position, setPosition] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (reason.trim().length < 10 || position.trim().length < 10) {
      setError("Please explain why you disagree and what you believe instead (at least 10 characters each).");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSubmit({ reason: reason.trim(), user_position: position.trim() });
      setReason("");
      setPosition("");
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to build lesson");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Disagree with the verdict" size="lg">
      <p className="text-sm text-muted-foreground">
        Tell us why the verdict missed the mark. We&apos;ll build a detailed comparison lesson — you vs the model.
      </p>
      {error && (
        <div className="mt-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}
      <div className="mt-4 space-y-4">
        <label className="block text-sm">
          <div className="mb-1 font-medium">Why do you disagree?</div>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={4}
            placeholder="The verdict overweighted short-term risk and ignored our distribution advantage…"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </label>
        <label className="block text-sm">
          <div className="mb-1 font-medium">What do you believe instead?</div>
          <textarea
            value={position}
            onChange={(e) => setPosition(e.target.value)}
            rows={4}
            placeholder="We should launch at a lower price point to capture market share first…"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </label>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <button
          onClick={onClose}
          disabled={saving}
          className="rounded-xl border border-border px-4 py-2 text-sm hover:bg-accent disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          onClick={() => void handleSubmit()}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {saving ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Building lesson…
            </>
          ) : (
            "Build lesson"
          )}
        </button>
      </div>
    </Modal>
  );
}
