import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import { DreamPanel, dreamMutedClass } from "@/components/scraping/DreamPageShell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { ScrapingBlueprint } from "@/lib/scraping/types";

export function BlueprintApprovalBar({
  blueprint,
  activeBlueprintId,
  onApprove,
  onReject,
  onRequestChanges,
  onGenerateNewVersion,
}: {
  blueprint: ScrapingBlueprint;
  activeBlueprintId?: string | null;
  onApprove: () => Promise<void>;
  onReject: (reason: string) => Promise<void>;
  onRequestChanges: (instructions: string) => Promise<void>;
  onGenerateNewVersion?: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"approve" | "reject" | "changes" | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const canApproveOrReject = blueprint.status === "draft";
  const canRequestChanges =
    blueprint.status === "draft" ||
    blueprint.status === "approved" ||
    blueprint.status === "rejected";
  const isActiveBlueprint = blueprint.id === activeBlueprintId;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
      setMode(null);
      setText("");
    } finally {
      setBusy(false);
    }
  }

  function closeModal() {
    if (busy) return;
    setMode(null);
    setText("");
  }

  function formatDate(value: string | null | undefined) {
    return value ? new Date(value).toLocaleString() : "Not recorded";
  }

  const submitLabel =
    mode === "approve" ? "Approve Blueprint" : mode === "reject" ? "Reject Blueprint" : "Submit";

  return (
    <>
      {blueprint.status === "approved" && (
        <DreamPanel tone="teal" className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-white">Approved</span>
            {isActiveBlueprint && (
              <span className="rounded-full border border-sky-300/40 bg-sky-400/15 px-2.5 py-0.5 text-xs font-medium text-sky-100">
                Active chart
              </span>
            )}
          </div>
          <div className="text-white/55">Approved date: {formatDate(blueprint.approved_at)}</div>
          <div className="text-white/55">This chart version is locked and read-only.</div>
          <div className="text-white/55">Approval does not start scraping.</div>
        </DreamPanel>
      )}

      {blueprint.status === "rejected" && (
        <DreamPanel className="space-y-2 text-sm">
          <div className="font-semibold text-white">Rejected</div>
          <div className="text-white/55">Rejection date: {formatDate(blueprint.rejected_at)}</div>
          <div>
            <span className="font-medium text-white">Rejection reason: </span>
            <span className="text-white/55">{blueprint.rejection_reason || "Not provided"}</span>
          </div>
          <div className="text-white/55">This version remains preserved in history.</div>
        </DreamPanel>
      )}

      {blueprint.status === "superseded" && (
        <DreamPanel className="space-y-2 text-sm">
          <div className="font-semibold text-white">Superseded</div>
          <div className="text-white/55">
            This version is preserved in history and is no longer active.
          </div>
        </DreamPanel>
      )}

      {blueprint.status === "failed" && (
        <DreamPanel className="space-y-3 text-sm">
          <div className="font-semibold text-white">Generation Failed</div>
          {blueprint.error_message && (
            <div className="text-white/55">{blueprint.error_message}</div>
          )}
          {onGenerateNewVersion && (
            <Button
              type="button"
              disabled={busy}
              onClick={() => void run(onGenerateNewVersion)}
              className="w-fit council-glass-cta"
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              Generate New Version
            </Button>
          )}
        </DreamPanel>
      )}

      {(canApproveOrReject || canRequestChanges) && (
        <div className="sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-2xl border border-white/15 bg-[#0b161c]/90 p-3 shadow-[0_16px_40px_rgba(0,0,0,0.35)] backdrop-blur-md">
          {canApproveOrReject && (
            <>
              <Button
                type="button"
                disabled={busy}
                className="council-glass-cta"
                onClick={() => setMode("approve")}
              >
                Approve chart
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                className="border-white/20 bg-white/5 text-white hover:bg-white/10"
                onClick={() => setMode("reject")}
              >
                Reject
              </Button>
            </>
          )}
          {canRequestChanges && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              className="border-white/20 bg-white/5 text-white hover:bg-white/10"
              onClick={() => setMode("changes")}
            >
              Request Changes
            </Button>
          )}
        </div>
      )}

      <Modal
        open={mode !== null}
        onClose={closeModal}
        title={
          mode === "approve"
            ? "Approve Blueprint"
            : mode === "reject"
              ? "Reject Blueprint"
              : "Request Changes"
        }
        size="md"
        tone="dream"
      >
        <div className="space-y-3">
          {mode === "approve" ? (
            <p className={dreamMutedClass}>
              Approving this version will lock it and mark it as the mission’s active blueprint. It
              will not start scraping.
            </p>
          ) : (
            <>
              <label className="text-sm font-medium text-white">
                {mode === "reject" ? "Rejection Reason" : "Change Instructions"}
              </label>
              <Textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                rows={6}
                required
                className="border-white/15 bg-white/5 text-white placeholder:text-white/30"
              />
            </>
          )}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              className="border-white/20 bg-white/5 text-white hover:bg-white/10"
              onClick={closeModal}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={busy || (mode !== "approve" && !text.trim())}
              className="council-glass-cta"
              onClick={() =>
                void run(() =>
                  mode === "approve"
                    ? onApprove()
                    : mode === "reject"
                      ? onReject(text.trim())
                      : onRequestChanges(text.trim()),
                )
              }
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              {submitLabel}
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
