import { useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
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

export function MissionComposer() {
  const navigate = useNavigate();
  const { authHeaders } = useAuth();
  const { modelSets, projects } = useChatStore();
  const [title, setTitle] = useState("");
  const [countryCode, setCountryCode] = useState("");
  const [prompt, setPrompt] = useState("");
  const [modelSetId, setModelSetId] = useState(modelSets[0]?.id ?? "");
  const [projectId, setProjectId] = useState<string>("none");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!modelSetId && modelSets[0]) {
      setModelSetId(modelSets[0].id);
    }
  }, [modelSetId, modelSets]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
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
        original_prompt: prompt,
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
        <input
          id="mission-country"
          list="scraping-country-options"
          value={countryCode}
          onChange={(event) => setCountryCode(event.target.value.toUpperCase())}
          placeholder="Search country or enter code, e.g. LB"
          required
          className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/30"
        />
        <datalist id="scraping-country-options">
          {SCRAPING_COUNTRIES.map((country) => (
            <option key={country.code} value={country.code}>
              {country.name}
            </option>
          ))}
        </datalist>
      </div>
      <div className="space-y-2">
        <Label htmlFor="mission-prompt">Mission Prompt</Label>
        <Textarea
          id="mission-prompt"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          required
          rows={12}
          className="resize-y"
        />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label>Model Set</Label>
          <Select value={modelSetId} onValueChange={setModelSetId} required>
            <SelectTrigger>
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
          <Label>Project</Label>
          <Select value={projectId} onValueChange={setProjectId}>
            <SelectTrigger>
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
      <button
        type="submit"
        disabled={submitting || !title.trim() || !countryCode.trim() || !prompt.trim() || !modelSetId}
        className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-50"
      >
        {submitting && <Loader2 className="size-4 animate-spin" />}
        Generate Blueprint
      </button>
    </form>
  );
}
