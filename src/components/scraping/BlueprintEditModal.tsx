import { useEffect, useState } from "react";
import { Modal } from "@/components/Modal";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { ScrapingBlueprint } from "@/lib/scraping/types";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function strategySummary(value: unknown): string {
  const record = asRecord(value);
  if (record && typeof record.summary === "string") return record.summary;
  return typeof value === "string" ? value : "";
}

function withStrategySummary(value: unknown, summary: string) {
  const record = asRecord(value) ?? {};
  return { ...record, summary };
}

export function BlueprintEditModal({
  blueprint,
  open,
  busy,
  onClose,
  onSave,
}: {
  blueprint: ScrapingBlueprint;
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onSave: (humanReadable: string, structured: Record<string, unknown>) => Promise<void>;
}) {
  const [coverageSummary, setCoverageSummary] = useState("");
  const [containment, setContainment] = useState("");
  const [languagesText, setLanguagesText] = useState("");
  const [regionsText, setRegionsText] = useState("");
  const [reviewNotes, setReviewNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const structured = asRecord(blueprint.structured_blueprint) ?? {};
    const dossier = asRecord(structured.country_dossier) ?? {};
    setCoverageSummary(strategySummary(structured.estimated_coverage));
    setContainment(strategySummary(structured.country_containment_rules));
    setLanguagesText(
      Array.isArray(structured.languages)
        ? structured.languages.filter((item): item is string => typeof item === "string").join(", ")
        : "",
    );
    setRegionsText(
      Array.isArray(structured.regions)
        ? structured.regions.filter((item): item is string => typeof item === "string").join(", ")
        : "",
    );
    setReviewNotes(blueprint.human_readable_blueprint ?? "");
    if (
      !strategySummary(structured.country_containment_rules) &&
      typeof dossier.country_name === "string"
    ) {
      setContainment(
        `Only treatment facilities physically located inside ${dossier.country_name} may qualify. Nearby foreign facilities must be excluded.`,
      );
    }
    setError(null);
  }, [blueprint, open]);

  async function submit() {
    const structured = {
      ...(asRecord(blueprint.structured_blueprint) ?? {}),
    };
    const languages = languagesText
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const regions = regionsText
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

    if (languages.length === 0) {
      setError("Add at least one language.");
      return;
    }
    if (!containment.trim()) {
      setError("Country containment guidance is required.");
      return;
    }

    structured.languages = languages;
    structured.regions = regions;
    structured.estimated_coverage = withStrategySummary(
      structured.estimated_coverage,
      coverageSummary.trim() || "Coverage summary to be refined.",
    );
    structured.country_containment_rules = withStrategySummary(
      structured.country_containment_rules,
      containment.trim(),
    );

    const notes =
      reviewNotes.trim() ||
      [
        coverageSummary.trim() && `Coverage: ${coverageSummary.trim()}`,
        `Containment: ${containment.trim()}`,
        `Languages: ${languages.join(", ")}`,
        regions.length ? `Regions: ${regions.join(", ")}` : null,
      ]
        .filter(Boolean)
        .join("\n");

    setError(null);
    await onSave(notes, structured);
  }

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} title="Edit Blueprint" size="lg">
      <div className="space-y-4">
        {error && <div className="text-sm text-destructive">{error}</div>}
        <p className="text-sm text-muted-foreground">
          Update the review details used before approval. Changes apply to this blueprint version
          only.
        </p>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="edit-coverage">
            National coverage summary
          </label>
          <Textarea
            id="edit-coverage"
            value={coverageSummary}
            onChange={(event) => setCoverageSummary(event.target.value)}
            rows={3}
            disabled={busy}
          />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="edit-containment">
            Country containment
          </label>
          <Textarea
            id="edit-containment"
            value={containment}
            onChange={(event) => setContainment(event.target.value)}
            rows={3}
            disabled={busy}
          />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="edit-languages">
            Languages (comma-separated)
          </label>
          <Textarea
            id="edit-languages"
            value={languagesText}
            onChange={(event) => setLanguagesText(event.target.value)}
            rows={2}
            disabled={busy}
          />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="edit-regions">
            Regions & areas (comma-separated)
          </label>
          <Textarea
            id="edit-regions"
            value={regionsText}
            onChange={(event) => setRegionsText(event.target.value)}
            rows={2}
            disabled={busy}
          />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="edit-notes">
            Reviewer notes
          </label>
          <Textarea
            id="edit-notes"
            value={reviewNotes}
            onChange={(event) => setReviewNotes(event.target.value)}
            rows={4}
            disabled={busy}
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" disabled={busy} onClick={() => void submit()}>
            Save Blueprint
          </Button>
        </div>
      </div>
    </Modal>
  );
}
