import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import { GlassCard } from "@/components/cinematic/PageChrome";
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
        <GlassCard className="space-y-2 p-5 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">Approved</span>
            {isActiveBlueprint && (
              <span className="rounded-full border border-primary/30 bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                Active Blueprint
              </span>
            )}
          </div>
          <div className="text-muted-foreground">
            Approved date: {formatDate(blueprint.approved_at)}
          </div>
          <div className="text-muted-foreground">
            This blueprint version is locked and read-only.
          </div>
          <div className="text-muted-foreground">Approval does not start scraping.</div>
        </GlassCard>
      )}

      {blueprint.status === "rejected" && (
        <GlassCard className="space-y-2 p-5 text-sm">
          <div className="font-semibold">Rejected</div>
          <div className="text-muted-foreground">
            Rejection date: {formatDate(blueprint.rejected_at)}
          </div>
          <div>
            <span className="font-medium">Rejection reason: </span>
            <span className="text-muted-foreground">
              {blueprint.rejection_reason || "Not provided"}
            </span>
          </div>
          <div className="text-muted-foreground">This version remains preserved in history.</div>
        </GlassCard>
      )}

      {blueprint.status === "superseded" && (
        <GlassCard className="space-y-2 p-5 text-sm">
          <div className="font-semibold">Superseded</div>
          <div className="text-muted-foreground">
            This version is preserved in history and is no longer active.
          </div>
        </GlassCard>
      )}

      {blueprint.status === "failed" && (
        <GlassCard className="space-y-3 p-5 text-sm">
          <div className="font-semibold">Generation Failed</div>
          {blueprint.error_message && (
            <div className="text-muted-foreground">{blueprint.error_message}</div>
          )}
          {onGenerateNewVersion && (
            <Button
              type="button"
              disabled={busy}
              onClick={() => void run(onGenerateNewVersion)}
              className="w-fit"
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              Generate New Version
            </Button>
          )}
        </GlassCard>
      )}

      {(canApproveOrReject || canRequestChanges) && (
        <div className="sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-2xl border border-border bg-card p-3 shadow-sm">
          {canApproveOrReject && (
            <>
              <Button type="button" disabled={busy} onClick={() => setMode("approve")}>
                Approve Blueprint
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setMode("reject")}
              >
                Reject Blueprint
              </Button>
            </>
          )}
          {canRequestChanges && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
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
      >
        <div className="space-y-3">
          {mode === "approve" ? (
            <p className="text-sm text-muted-foreground">
              Approving this version will lock it and mark it as the mission’s active blueprint. It
              will not start scraping.
            </p>
          ) : (
            <>
              <label className="text-sm font-medium">
                {mode === "reject" ? "Rejection Reason" : "Change Instructions"}
              </label>
              <Textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                rows={6}
                required
              />
            </>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={closeModal} disabled={busy}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={busy || (mode !== "approve" && !text.trim())}
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
