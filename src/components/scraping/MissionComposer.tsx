import { useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";
import { CountrySelector } from "@/components/scraping/CountrySelector";
import { createScrapingMission, queueScrapingBlueprintGeneration } from "@/lib/scraping/api";

export function MissionComposer() {
  const navigate = useNavigate();
  const { authHeaders } = useAuth();
  const [title, setTitle] = useState("");
  const [countryCode, setCountryCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim()) {
      setError("Mission title is required.");
      return;
    }
    if (!countryCode) {
      setError("Select a country before generating a blueprint.");
      return;
    }
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const mission = await createScrapingMission(auth, {
        title,
        country_code: countryCode,
      });
      await queueScrapingBlueprintGeneration(auth, mission.id);
      void navigate({ to: "/scraping/$missionId/blueprint", params: { missionId: mission.id } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate blueprint");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-5">
      {error && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor="mission-title">Mission Title</Label>
        <input
          id="mission-title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          required
          className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="mission-country">Country</Label>
        <CountrySelector value={countryCode} onValueChange={setCountryCode} disabled={submitting} />
      </div>
      <button
        type="submit"
        disabled={submitting || !title.trim() || !countryCode}
        className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-50"
      >
        {submitting && <Loader2 className="size-4 animate-spin" />}
        Generate Blueprint
      </button>
    </form>
  );
}
