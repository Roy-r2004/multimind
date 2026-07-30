import { useNavigate } from "@tanstack/react-router";
import { Loader2, Sparkles } from "lucide-react";
import { useState } from "react";
import { Label } from "@/components/ui/label";
import { dreamGhostClass } from "@/components/scraping/DreamPageShell";
import { useAuth } from "@/lib/auth";
import { createMapsCensusRun } from "@/lib/maps/api";
import { SCRAPING_COUNTRIES } from "@/lib/scraping/countries";

function resolveCountryName(code: string): string {
  const match = SCRAPING_COUNTRIES.find((c) => c.code === code.toUpperCase());
  return match?.name ?? code.toUpperCase();
}

export function MapsCensusComposer() {
  const navigate = useNavigate();
  const { authHeaders } = useAuth();
  const [countryCode, setCountryCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const countryName = countryCode.trim() ? resolveCountryName(countryCode) : "";
  const canLaunch = Boolean(countryCode.trim().length === 2) && !submitting;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const auth = authHeaders();
    if (!auth || !canLaunch) return;
    setSubmitting(true);
    setError(null);
    try {
      const run = await createMapsCensusRun(auth, {
        country_code: countryCode.trim().toUpperCase(),
      });
      await navigate({ to: "/maps/$runId", params: { runId: run.id } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start Maps census");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="dream-rise dream-rise-delay-1 space-y-3">
        <Label
          htmlFor="maps-country"
          className="text-[11px] uppercase tracking-[0.28em] text-primary/90"
        >
          Country
        </Label>
        <input
          id="maps-country"
          list="maps-country-options"
          value={countryCode}
          onChange={(event) => setCountryCode(event.target.value.toUpperCase())}
          placeholder="Search country or code — BY, Belarus…"
          required
          className="w-full rounded-2xl border border-border bg-muted/40 px-4 py-3.5 text-lg text-foreground outline-none backdrop-blur-sm placeholder:text-muted-foreground focus:border-primary/50 focus:ring-2 focus:ring-primary/20"
        />
        <datalist id="maps-country-options">
          {SCRAPING_COUNTRIES.map((country) => (
            <option key={country.code} value={country.code}>
              {country.name}
            </option>
          ))}
        </datalist>
        {countryName && (
          <p className="dream-float text-sm text-muted-foreground">
            Google Places grid will target <span className="text-primary">{countryName}</span>
          </p>
        )}
      </div>

      <p className="text-sm leading-relaxed text-muted-foreground">
        We plan a city/region search grid with English and local-language terms, search Google
        Places for each cell, classify every result with AI, and validate any website against the
        same strict rules used by the Scraping Council before calling it official.
      </p>

      {error && <p className="text-sm text-rose-600">{error}</p>}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={!canLaunch}
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition hover:bg-primary/90 disabled:opacity-40"
        >
          {submitting ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-4" />
          )}
          Run Maps census
        </button>
        <button
          type="button"
          onClick={() => void navigate({ to: "/maps" })}
          className={dreamGhostClass}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
