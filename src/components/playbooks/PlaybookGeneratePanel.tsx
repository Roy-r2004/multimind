import { Loader2 } from "lucide-react";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { Button } from "@/components/ui/button";

const CATEGORY_HINTS = [
  "working preferences",
  "communication preferences",
  "active projects",
  "decisions",
  "completed work",
  "blockers",
  "priorities",
  "next steps",
];

export function PlaybookGeneratePanel({
  submitting,
  error,
  onGenerate,
}: {
  submitting: boolean;
  error: string | null;
  onGenerate: () => void;
}) {
  return (
    <GlassCard className="p-6 md:p-8">
      <h2 className="font-display text-2xl font-semibold tracking-tight">Generate your Playbook</h2>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">
        MultiMind will analyze eligible chats and Brain information to build a structured picture of
        how you work. This is a best-effort synthesis, not a perfect record.
      </p>
      <ul className="mt-4 grid gap-2 text-sm text-foreground sm:grid-cols-2">
        {CATEGORY_HINTS.map((item) => (
          <li key={item} className="rounded-lg bg-muted/50 px-3 py-2 capitalize">
            {item}
          </li>
        ))}
      </ul>
      {error ? (
        <p className="mt-4 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      <Button
        className="mt-6"
        onClick={onGenerate}
        disabled={submitting}
        aria-label="Generate Playbook"
      >
        {submitting ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
        Generate Playbook
      </Button>
    </GlassCard>
  );
}
