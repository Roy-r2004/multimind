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
import { useAuth } from "@/lib/auth";
import { useChatStore } from "@/lib/store";
import { CountrySelector } from "@/components/scraping/CountrySelector";
import { createScrapingMission, queueScrapingBlueprintGeneration } from "@/lib/scraping/api";

const BACKEND_OWNED_BLUEPRINT_REQUEST =
  "Generate the backend-owned country-specific maximum-coverage blueprint.";

export function MissionComposer() {
  const navigate = useNavigate();
  const { authHeaders } = useAuth();
  const { modelSets, projects } = useChatStore();
  const [title, setTitle] = useState("");
  const [countryCode, setCountryCode] = useState("");
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
    if (!title.trim()) {
      setError("Mission title is required.");
      return;
    }
    if (!countryCode) {
      setError("Select a country before generating a blueprint.");
      return;
    }
    if (!modelSetId) {
      setError("Select a model set before generating a blueprint.");
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
        original_prompt: BACKEND_OWNED_BLUEPRINT_REQUEST,
        model_set_id: modelSetId,
        project_id: projectId === "none" ? null : projectId,
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
        disabled={submitting || !title.trim() || !countryCode || !modelSetId}
        className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-50"
      >
        {submitting && <Loader2 className="size-4 animate-spin" />}
        Generate Blueprint
      </button>
    </form>
  );
}
