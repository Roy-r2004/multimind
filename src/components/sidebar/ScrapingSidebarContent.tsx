import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { ClipboardList, History, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Modal } from "@/components/Modal";
import { ScrapingMissionMenu } from "@/components/scraping/ScrapingMissionMenu";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import {
  deleteScrapingMission,
  listScrapingMissions,
  updateScrapingMission,
} from "@/lib/scraping/api";
import type { ScrapingMissionSummary } from "@/lib/scraping/types";

export function ScrapingSidebarContent({ onNavigate }: { onNavigate: () => void }) {
  const { authHeaders } = useAuth();
  const { projects, refreshAll } = useChatStore();
  const navigate = useNavigate();
  const path = useRouterState({ select: (state) => state.location.pathname });
  const [missions, setMissions] = useState<ScrapingMissionSummary[]>([]);
  const [projectTarget, setProjectTarget] = useState<ScrapingMissionSummary | null>(null);
  const [removeProjectTarget, setRemoveProjectTarget] = useState<ScrapingMissionSummary | null>(
    null,
  );
  const [renameTarget, setRenameTarget] = useState<ScrapingMissionSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScrapingMissionSummary | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadMissions = useCallback(async () => {
    const auth = authHeaders();
    if (!auth) return;
    try {
      setMissions(await listScrapingMissions(auth));
    } catch {
      setMissions([]);
    }
  }, [authHeaders]);

  useEffect(() => {
    void loadMissions();
  }, [loadMissions]);

  async function refreshAfterMutation() {
    await Promise.all([loadMissions(), refreshAll()]);
    window.dispatchEvent(new CustomEvent("scraping-missions-updated"));
  }

  return (
    <>
      <div className="p-3">
        <Link
          to="/scraping/new"
          onClick={onNavigate}
          className="flex w-full items-center gap-2 rounded-xl bg-primary px-3 py-2.5 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
        >
          <Plus className="size-4" /> New Scraping Mission
        </Link>
      </div>
      <div className="mt-4 flex-1 overflow-hidden px-3">
        <div className="flex items-center gap-2 px-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          <History className="size-3.5" /> Recent Scraping Missions
        </div>
        {message && <p className="mt-2 px-2 text-xs text-primary">{message}</p>}
        {error && <p className="mt-2 px-2 text-xs text-destructive">{error}</p>}
        <div className="mt-2 max-h-[38vh] space-y-0.5 overflow-y-auto">
          {missions.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">No scraping missions yet</p>
          ) : (
            missions.map((mission) => (
              <div key={mission.id} className="group relative rounded-lg hover:bg-accent">
                <Link
                  to="/scraping/$missionId"
                  params={{ missionId: mission.id }}
                  onClick={onNavigate}
                  className="flex items-start gap-2 rounded-lg px-3 py-2 pr-9 text-sm text-sidebar-foreground/85"
                >
                  <ClipboardList className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <span className="min-w-0">
                    <span className="block truncate">{mission.title}</span>
                    <span className="block truncate text-[10px] text-muted-foreground">
                      {mission.status}
                    </span>
                    {mission.project_name && (
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {mission.project_name}
                      </span>
                    )}
                  </span>
                </Link>
                <span className="absolute right-1 top-1.5 z-10">
                  <ScrapingMissionMenu
                    mission={mission}
                    onAddOrChangeProject={() => {
                      setError(null);
                      setMessage(null);
                      setProjectTarget(mission);
                      void refreshAll();
                    }}
                    onRemoveProject={() => {
                      setError(null);
                      setMessage(null);
                      setRemoveProjectTarget(mission);
                    }}
                    onRename={() => {
                      setError(null);
                      setMessage(null);
                      setRenameTarget(mission);
                    }}
                    onDelete={() => {
                      setError(null);
                      setMessage(null);
                      setDeleteTarget(mission);
                    }}
                  />
                </span>
              </div>
            ))
          )}
        </div>
      </div>
      <AssignMissionProjectModal
        mission={projectTarget}
        projects={projects}
        onClose={() => setProjectTarget(null)}
        onSubmit={async (mission, projectId) => {
          const auth = authHeaders();
          if (!auth) return;
          const project = projects.find((item) => item.id === projectId);
          await updateScrapingMission(auth, mission.id, { project_id: projectId });
          setProjectTarget(null);
          await refreshAfterMutation();
          setMessage(
            mission.project_id
              ? `Mission moved to project ${project?.name ?? "project"}.`
              : `Mission added to project ${project?.name ?? "project"}.`,
          );
        }}
        onError={setError}
      />
      <RemoveMissionProjectModal
        mission={removeProjectTarget}
        onClose={() => setRemoveProjectTarget(null)}
        onSubmit={async (mission) => {
          const auth = authHeaders();
          if (!auth) return;
          await updateScrapingMission(auth, mission.id, { project_id: null });
          setRemoveProjectTarget(null);
          await refreshAfterMutation();
          setMessage("Mission removed from project.");
        }}
        onError={setError}
      />
      <RenameMissionModal
        mission={renameTarget}
        onClose={() => setRenameTarget(null)}
        onSubmit={async (mission, title) => {
          const auth = authHeaders();
          if (!auth) return;
          await updateScrapingMission(auth, mission.id, { title });
          setRenameTarget(null);
          await refreshAfterMutation();
          setMessage("Mission renamed.");
        }}
        onError={setError}
      />
      <DeleteMissionModal
        mission={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onSubmit={async (mission) => {
          const auth = authHeaders();
          if (!auth) return;
          await deleteScrapingMission(auth, mission.id);
          setDeleteTarget(null);
          await refreshAfterMutation();
          setMessage("Mission deleted.");
          if (path === `/scraping/${mission.id}` || path === `/scraping/${mission.id}/blueprint`) {
            void navigate({ to: "/scraping" });
          }
        }}
        onError={setError}
      />
    </>
  );
}

function AssignMissionProjectModal({
  mission,
  projects,
  onClose,
  onSubmit,
  onError,
}: {
  mission: ScrapingMissionSummary | null;
  projects: Array<{ id: string; name: string }>;
  onClose: () => void;
  onSubmit: (mission: ScrapingMissionSummary, projectId: string) => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const [projectId, setProjectId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (mission) setProjectId(mission.project_id ?? "");
  }, [mission]);

  async function submit() {
    if (!mission || !projectId) return;
    setSubmitting(true);
    onError(null);
    try {
      await onSubmit(mission, projectId);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to update project");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={!!mission}
      onClose={submitting ? () => undefined : onClose}
      title={mission?.project_id ? "Change Project" : "Add to Project"}
      size="md"
    >
      <div className="space-y-4">
        <div className="space-y-2">
          <Label>Project</Label>
          <Select value={projectId} onValueChange={setProjectId}>
            <SelectTrigger>
              <SelectValue placeholder="Select a project" />
            </SelectTrigger>
            <SelectContent className="z-[200]">
              {projects.map((project) => (
                <SelectItem key={project.id} value={project.id}>
                  {project.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={submitting} onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" disabled={!projectId || submitting} onClick={() => void submit()}>
            {submitting ? "Saving..." : "Save"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function RemoveMissionProjectModal({
  mission,
  onClose,
  onSubmit,
  onError,
}: {
  mission: ScrapingMissionSummary | null;
  onClose: () => void;
  onSubmit: (mission: ScrapingMissionSummary) => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (!mission) return;
    setSubmitting(true);
    onError(null);
    try {
      await onSubmit(mission);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to remove mission from project");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={!!mission}
      onClose={submitting ? () => undefined : onClose}
      title="Remove from Project"
      size="md"
    >
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Remove this scraping mission from its current project? The mission and all blueprint
          versions will remain available in Scraping Council.
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={submitting} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={submitting}
            onClick={() => void submit()}
          >
            Remove from Project
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function RenameMissionModal({
  mission,
  onClose,
  onSubmit,
  onError,
}: {
  mission: ScrapingMissionSummary | null;
  onClose: () => void;
  onSubmit: (mission: ScrapingMissionSummary, title: string) => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (mission) setTitle(mission.title);
  }, [mission]);

  async function submit() {
    if (!mission) return;
    const trimmed = title.trim();
    if (!trimmed) return;
    setSubmitting(true);
    onError(null);
    try {
      await onSubmit(mission, trimmed);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to rename mission");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={!!mission}
      onClose={submitting ? () => undefined : onClose}
      title="Rename Mission"
      size="md"
    >
      <div className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="mission-name">Mission Name</Label>
          <Input
            id="mission-name"
            value={title}
            maxLength={512}
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={submitting} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!title.trim() || submitting}
            onClick={() => void submit()}
          >
            Save Name
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function DeleteMissionModal({
  mission,
  onClose,
  onSubmit,
  onError,
}: {
  mission: ScrapingMissionSummary | null;
  onClose: () => void;
  onSubmit: (mission: ScrapingMissionSummary) => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (!mission) return;
    setSubmitting(true);
    onError(null);
    try {
      await onSubmit(mission);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to delete mission");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={!!mission}
      onClose={submitting ? () => undefined : onClose}
      title="Delete Mission"
      size="md"
    >
      {mission && (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Permanently delete “{mission.title}”? This will delete the mission and all of its
            blueprint versions.
          </p>
          <p className="text-sm font-medium text-destructive">This action cannot be undone.</p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={submitting} onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={submitting}
              onClick={() => void submit()}
            >
              Delete Mission
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
