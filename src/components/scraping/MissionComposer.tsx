import { useNavigate } from "@tanstack/react-router";
import { ChevronDown, Loader2, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";
import { useChatStore } from "@/lib/store";
import { createScrapingMission, generateScrapingBlueprint } from "@/lib/scraping/api";
import { SCRAPING_COUNTRIES } from "@/lib/scraping/countries";
import { cn } from "@/lib/utils";

function resolveCountryName(code: string): string {
  const match = SCRAPING_COUNTRIES.find((c) => c.code === code.toUpperCase());
  return match?.name ?? code.toUpperCase();
}

function buildDreamPrompt(title: string, countryCode: string): string {
  const country = resolveCountryName(countryCode);
  return [
    `Census mission: ${title.trim()}.`,
    `Find every addiction / rehab / treatment facility operating in ${country} (${countryCode.toUpperCase()}).`,
    "Prefer official registries, directories, and facility websites in local languages.",
    "Every published facility must include a physical location and a phone number.",
    "Cite evidence quotes, keep branches distinct when they share a brand, and deduplicate carefully before publish.",
  ].join(" ");
}

export function MissionComposer() {
  const navigate = useNavigate();
  const { authHeaders } = useAuth();
  const { modelSets, projects } = useChatStore();
  const [title, setTitle] = useState("");
  const [countryCode, setCountryCode] = useState("");
  const [prompt, setPrompt] = useState("");
  const [promptTouched, setPromptTouched] = useState(false);
  const [modelSetId, setModelSetId] = useState("");
  const [projectId, setProjectId] = useState<string>("none");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const defaultSetId = useMemo(() => {
    const referee = modelSets.find((s) => s.id === "referee");
    return referee?.id ?? modelSets[0]?.id ?? "";
  }, [modelSets]);

  useEffect(() => {
    if (!modelSetId && defaultSetId) {
      setModelSetId(defaultSetId);
    }
  }, [defaultSetId, modelSetId]);

  useEffect(() => {
    if (!promptTouched && title.trim() && countryCode.trim()) {
      setPrompt(buildDreamPrompt(title, countryCode));
    }
  }, [title, countryCode, promptTouched]);

  const countryName = countryCode.trim() ? resolveCountryName(countryCode) : "";
  const canLaunch =
    Boolean(title.trim() && countryCode.trim() && (prompt.trim() || !promptTouched) && modelSetId) &&
    !submitting;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    const finalPrompt = prompt.trim() || buildDreamPrompt(title, countryCode);
    setSubmitting(true);
    setError(null);
    try {
      const mission = await createScrapingMission(auth, {
        title: title.trim(),
        country_code: countryCode.trim().toUpperCase(),
        original_prompt: finalPrompt,
        model_set_id: modelSetId,
        project_id: projectId === "none" ? null : projectId,
      });
      await generateScrapingBlueprint(auth, mission.id);
      void navigate({ to: "/scraping/$missionId/blueprint", params: { missionId: mission.id } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate blueprint");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="relative space-y-6">
      {error && (
        <div className="dream-rise rounded-2xl border border-rose-300/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      <div className="dream-rise space-y-3">
        <Label htmlFor="mission-title" className="text-[11px] uppercase tracking-[0.28em] text-primary/90">
          Mission name
        </Label>
        <input
          id="mission-title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          required
          placeholder="Estonia Christian rehab census"
          className="w-full border-0 border-b border-border bg-transparent pb-3 font-display text-3xl tracking-tight text-foreground outline-none placeholder:text-muted-foreground/50 focus:border-primary/70 sm:text-4xl"
        />
      </div>

      <div className="dream-rise dream-rise-delay-1 space-y-3">
        <Label
          htmlFor="mission-country"
          className="text-[11px] uppercase tracking-[0.28em] text-primary/90"
        >
          Destination
        </Label>
        <input
          id="mission-country"
          list="scraping-country-options"
          value={countryCode}
          onChange={(event) => setCountryCode(event.target.value.toUpperCase())}
          placeholder="Search country or code — EE, Estonia…"
          required
          className="w-full rounded-2xl border border-border bg-muted/40 px-4 py-3.5 text-lg text-foreground outline-none backdrop-blur-sm placeholder:text-muted-foreground focus:border-primary/50 focus:ring-2 focus:ring-primary/20"
        />
        <datalist id="scraping-country-options">
          {SCRAPING_COUNTRIES.map((country) => (
            <option key={country.code} value={country.code}>
              {country.name}
            </option>
          ))}
        </datalist>
        {countryName && (
          <p className="dream-float text-sm text-muted-foreground">
            Flight path locked on <span className="text-primary">{countryName}</span>
          </p>
        )}
      </div>

      <button
        type="submit"
        disabled={!canLaunch}
        className={cn(
          "dream-rise dream-rise-delay-2 group relative inline-flex w-full items-center justify-center gap-2 overflow-hidden rounded-2xl px-5 py-4 text-sm font-semibold tracking-wide transition",
          "bg-primary text-primary-foreground shadow-sm",
          "hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40",
        )}
      >
        <span className="dream-pulse-ring absolute size-24 rounded-full border border-primary-foreground/20 opacity-0 group-hover:opacity-100" />
        {submitting ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
        {submitting ? "Charting blueprint…" : "Launch mission"}
      </button>

      <div className="dream-rise dream-rise-delay-3">
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="flex w-full items-center justify-between rounded-xl border border-border bg-muted/30 px-4 py-3 text-left text-sm text-foreground/80 hover:bg-accent"
        >
          <span>Advanced — prompt, council, project</span>
          <ChevronDown className={cn("size-4 transition", showAdvanced && "rotate-180")} />
        </button>
        {showAdvanced && (
          <div className="mt-4 space-y-4 rounded-2xl border border-border bg-black/20 p-4">
            <div className="space-y-2">
              <Label htmlFor="mission-prompt" className="text-foreground/80">
                Mission prompt
              </Label>
              <Textarea
                id="mission-prompt"
                value={prompt}
                onChange={(event) => {
                  setPromptTouched(true);
                  setPrompt(event.target.value);
                }}
                rows={8}
                className="resize-y border-border bg-muted/40 text-foreground placeholder:text-muted-foreground"
              />
              <p className="text-xs text-muted-foreground">
                Auto-written from title + country. Edit only if you need a custom brief.
              </p>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label className="text-foreground/80">Model set</Label>
                <Select value={modelSetId} onValueChange={setModelSetId} required>
                  <SelectTrigger className="border-border bg-muted/40 text-foreground">
                    <SelectValue placeholder="Select model set" />
                  </SelectTrigger>
                  <SelectContent>
                    {modelSets.map((set) => (
                      <SelectItem key={set.id} value={set.id}>
                        {set.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label className="text-foreground/80">Project</Label>
                <Select value={projectId} onValueChange={setProjectId}>
                  <SelectTrigger className="border-border bg-muted/40 text-foreground">
                    <SelectValue placeholder="No project" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">No project</SelectItem>
                    {projects.map((project) => (
                      <SelectItem key={project.id} value={project.id}>
                        {project.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
        )}
      </div>
    </form>
  );
}
