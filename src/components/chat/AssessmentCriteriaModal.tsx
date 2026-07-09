import { useEffect, useState } from "react";
import { Scale } from "lucide-react";
import { Modal } from "@/components/Modal";
import { DEFAULT_COMPANY_ASSESSMENT_CRITERIA } from "@/lib/assessmentCriteria";

type Props = {
  open: boolean;
  onClose: () => void;
  initialCriteria: string;
  onSave: (criteria: string) => void | Promise<void>;
  saving?: boolean;
};

export function AssessmentCriteriaModal({
  open,
  onClose,
  initialCriteria,
  onSave,
  saving = false,
}: Props) {
  const [criteria, setCriteria] = useState(initialCriteria);

  useEffect(() => {
    if (open) setCriteria(initialCriteria);
  }, [open, initialCriteria]);

  return (
    <Modal open={open} onClose={onClose} title="Assessment criteria" size="lg">
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          You set how the council scores itself. Each model outputs a{" "}
          <strong className="text-foreground">CONFIDENCE %</strong> based on these priorities. The
          Verdict AI also uses them when synthesizing the final answer.
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setCriteria(DEFAULT_COMPANY_ASSESSMENT_CRITERIA)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium hover:bg-accent"
          >
            <Scale className="size-3.5" /> Company assessment preset
          </button>
        </div>
        <label className="block">
          <span className="mb-2 block text-sm font-medium">Your criteria (one per line)</span>
          <textarea
            value={criteria}
            onChange={(e) => setCriteria(e.target.value)}
            rows={8}
            placeholder="e.g. Revenue growth vs peers&#10;Margin sustainability&#10;Downside risk"
            className="w-full rounded-xl border border-border bg-background px-3 py-3 text-sm outline-none focus:border-primary/50"
          />
        </label>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={saving || !criteria.trim()}
            onClick={() => void onSave(criteria.trim())}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save criteria"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
