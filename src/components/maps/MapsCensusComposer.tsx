import { useNavigate } from "@tanstack/react-router";
import { Globe, Loader2, MapPin, Sparkles } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { Label } from "@/components/ui/label";
import { dreamGhostClass } from "@/components/scraping/DreamPageShell";
import { useAuth } from "@/lib/auth";
import { createMapsCensusRun } from "@/lib/maps/api";
import { SCRAPING_COUNTRIES } from "@/lib/scraping/countries";
import { getSubdivisionsForCountry } from "@/lib/scraping/subdivisions";

function resolveCountry(codeOrName: string) {
  const query = codeOrName.trim().toLowerCase();
  if (!query) return null;
  const match = SCRAPING_COUNTRIES.find(
    (c) => c.code.toLowerCase() === query || c.name.toLowerCase() === query
  );
  return match ?? null;
}

export function MapsCensusComposer() {
  const navigate = useNavigate();
  const { authHeaders } = useAuth();
  const datalistId = useId();
  const [countryInput, setCountryInput] = useState("");
  const [scopeMode, setScopeMode] = useState<"country" | "state">("country");
  const [selectedSubdivisionCode, setSelectedSubdivisionCode] = useState("");
  const [customStateName, setCustomStateName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const matchedCountry = useMemo(() => resolveCountry(countryInput), [countryInput]);
  const countryCode = matchedCountry ? matchedCountry.code : countryInput.trim().toUpperCase();
  const countryName = matchedCountry ? matchedCountry.name : countryInput.trim();

  const availableSubdivisions = useMemo(() => {
    return matchedCountry ? getSubdivisionsForCountry(matchedCountry.code) : [];
  }, [matchedCountry]);

  // Derived state resolution
  const resolvedState = useMemo(() => {
    if (scopeMode === "country") return null;
    if (availableSubdivisions.length > 0) {
      if (selectedSubdivisionCode === "__custom__") {
        return { code: null, name: customStateName.trim() };
      }
      const match = availableSubdivisions.find((s) => s.code === selectedSubdivisionCode);
      return match ? { code: match.code, name: match.name } : null;
    }
    return customStateName.trim() ? { code: null, name: customStateName.trim() } : null;
  }, [scopeMode, availableSubdivisions, selectedSubdivisionCode, customStateName]);

  const isValidCountry = Boolean(matchedCountry || countryCode.length === 2);
  const isValidState =
    scopeMode === "country" || Boolean(resolvedState && resolvedState.name.length > 0);
  const canLaunch = isValidCountry && isValidState && !submitting;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const auth = authHeaders();
    if (!auth || !canLaunch) return;
    setSubmitting(true);
    setError(null);
    try {
      const run = await createMapsCensusRun(auth, {
        country_code: countryCode,
        state_code: resolvedState?.code ?? undefined,
        state_name: resolvedState?.name ?? undefined,
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
      {/* Country selection */}
      <div className="dream-rise dream-rise-delay-1 space-y-3">
        <Label
          htmlFor="maps-country"
          className="text-[11px] uppercase tracking-[0.28em] text-primary/90"
        >
          Country
        </Label>
        <input
          id="maps-country"
          list={datalistId}
          value={countryInput}
          onChange={(event) => {
            const next = event.target.value;
            setCountryInput(next);
            setSelectedSubdivisionCode("");
            setCustomStateName("");
          }}
          placeholder="Search country or code — US, United States, Canada, France…"
          required
          className="w-full rounded-2xl border border-border bg-muted/40 px-4 py-3.5 text-lg text-foreground outline-none backdrop-blur-sm placeholder:text-muted-foreground focus:border-primary/50 focus:ring-2 focus:ring-primary/20"
        />
        <datalist id={datalistId}>
          {SCRAPING_COUNTRIES.map((country) => (
            <option key={country.code} value={country.name}>
              {country.code} — {country.name}
            </option>
          ))}
        </datalist>
      </div>

      {/* Scope Mode selection (Whole country vs State/Province) */}
      {isValidCountry && (
        <div className="dream-rise space-y-4 rounded-2xl border border-border/80 bg-muted/20 p-5 backdrop-blur-sm">
          <Label className="text-[11px] uppercase tracking-[0.28em] text-primary/90">
            Geographic Scope
          </Label>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <button
              type="button"
              onClick={() => {
                setScopeMode("country");
                setSelectedSubdivisionCode("");
                setCustomStateName("");
              }}
              className={`flex items-center gap-3 rounded-xl border p-3.5 text-left transition-all ${
                scopeMode === "country"
                  ? "border-primary bg-primary/10 shadow-sm"
                  : "border-border/60 bg-background/50 hover:border-border hover:bg-muted/40"
              }`}
            >
              <div
                className={`flex size-9 shrink-0 items-center justify-center rounded-lg ${
                  scopeMode === "country"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                <Globe className="size-4" />
              </div>
              <div>
                <p className="text-sm font-semibold text-foreground">Whole Country</p>
                <p className="text-xs text-muted-foreground">National search grid across all regions</p>
              </div>
            </button>

            <button
              type="button"
              onClick={() => setScopeMode("state")}
              className={`flex items-center gap-3 rounded-xl border p-3.5 text-left transition-all ${
                scopeMode === "state"
                  ? "border-primary bg-primary/10 shadow-sm"
                  : "border-border/60 bg-background/50 hover:border-border hover:bg-muted/40"
              }`}
            >
              <div
                className={`flex size-9 shrink-0 items-center justify-center rounded-lg ${
                  scopeMode === "state"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                <MapPin className="size-4" />
              </div>
              <div>
                <p className="text-sm font-semibold text-foreground">Specific State / Province</p>
                <p className="text-xs text-muted-foreground">Scrape one state or region thoroughly</p>
              </div>
            </button>
          </div>

          {/* Subdivision selector when state mode active */}
          {scopeMode === "state" && (
            <div className="space-y-3 pt-2">
              <Label
                htmlFor="maps-state-select"
                className="text-xs font-medium text-foreground/80"
              >
                Choose State / Province in {countryName}
              </Label>

              {availableSubdivisions.length > 0 ? (
                <div className="space-y-2">
                  <select
                    id="maps-state-select"
                    value={selectedSubdivisionCode}
                    onChange={(e) => setSelectedSubdivisionCode(e.target.value)}
                    className="w-full rounded-xl border border-border bg-background px-4 py-2.5 text-sm text-foreground outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/20"
                  >
                    <option value="">— Select a State / Province —</option>
                    {availableSubdivisions.map((sub) => (
                      <option key={sub.code} value={sub.code}>
                        {sub.name} ({sub.code})
                      </option>
                    ))}
                    <option value="__custom__">Other / Enter manually...</option>
                  </select>

                  {selectedSubdivisionCode === "__custom__" && (
                    <input
                      type="text"
                      value={customStateName}
                      onChange={(e) => setCustomStateName(e.target.value)}
                      placeholder="Enter state, province, or territory name…"
                      className="w-full rounded-xl border border-border bg-background px-4 py-2 text-sm text-foreground outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/20"
                    />
                  )}
                </div>
              ) : (
                <input
                  id="maps-state-select"
                  type="text"
                  value={customStateName}
                  onChange={(e) => setCustomStateName(e.target.value)}
                  placeholder={`Enter state, province, or region name in ${countryName}…`}
                  className="w-full rounded-xl border border-border bg-background px-4 py-2.5 text-sm text-foreground outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/20"
                />
              )}
            </div>
          )}

          {/* Target preview confirmation */}
          <div className="flex items-center gap-2 pt-1 text-sm text-muted-foreground">
            <span>Targeting:</span>
            <span className="font-semibold text-primary">
              {scopeMode === "state" && resolvedState?.name
                ? `${resolvedState.name}, ${countryName}`
                : countryName}
            </span>
            {scopeMode === "state" && resolvedState?.name && (
              <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                State Scoped
              </span>
            )}
          </div>
        </div>
      )}

      <p className="text-sm leading-relaxed text-muted-foreground">
        We plan a city/region search grid with English and local-language terms, search Google
        Places for each cell, run a strict Keep/Drop classifier with Sonar fallback, enrich
        treatment pricing, and validate every facility with AI.
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
          {scopeMode === "state" && resolvedState?.name
            ? `Run ${resolvedState.name} census`
            : "Run Maps census"}
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
