import { useEffect, useState } from "react";
import { Modal } from "@/components/Modal";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { ScrapingBlueprint } from "@/lib/scraping/types";

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
  const [humanReadable, setHumanReadable] = useState("");
  const [structuredText, setStructuredText] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setHumanReadable(blueprint.human_readable_blueprint ?? "");
    setStructuredText(JSON.stringify(blueprint.structured_blueprint ?? {}, null, 2));
    setError(null);
  }, [blueprint, open]);

  async function submit() {
    if (!humanReadable.trim()) {
      setError("Human-readable blueprint content is required.");
      return;
    }
    let structured: Record<string, unknown>;
    try {
      const value: unknown = JSON.parse(structuredText);
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new Error();
      }
      structured = value as Record<string, unknown>;
    } catch {
      setError("Structured blueprint must be valid JSON.");
      return;
    }
    setError(null);
    await onSave(humanReadable.trim(), structured);
  }

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} title="Edit Blueprint" size="xl">
      <div className="space-y-4">
        {error && <div className="text-sm text-destructive">{error}</div>}
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="human-readable-blueprint">
            Human-readable blueprint
          </label>
          <Textarea
            id="human-readable-blueprint"
            value={humanReadable}
            onChange={(event) => setHumanReadable(event.target.value)}
            rows={12}
            disabled={busy}
          />
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="structured-blueprint">
            Structured blueprint JSON
          </label>
          <Textarea
            id="structured-blueprint"
            value={structuredText}
            onChange={(event) => setStructuredText(event.target.value)}
            rows={16}
            disabled={busy}
            className="font-mono text-xs"
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
