import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DreamHeader, DreamPageShell, DreamPanel } from "@/components/scraping/DreamPageShell";
import { MapsRunCard } from "@/components/maps/MapsRunCard";
import { useAuth } from "@/lib/auth";
import { deleteMapsCensusRun, listMapsCensusRuns } from "@/lib/maps/api";
import type { MapsCensusRunSummary } from "@/lib/maps/types";

export const Route = createFileRoute("/maps/")({
  head: () => ({ meta: [{ title: "Maps Census - MultiAI" }] }),
  component: MapsPage,
});

function MapsPage() {
  const { authHeaders } = useAuth();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<MapsCensusRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const auth = authHeaders();
    if (!auth) {
      void navigate({ to: "/login" });
      return;
    }
    setLoading(true);
    setError(null);
    void listMapsCensusRuns(auth)
      .then(setRuns)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load Maps census runs"),
      )
      .finally(() => setLoading(false));
  }, [authHeaders, navigate]);

  function handleDelete(runId: string) {
    const auth = authHeaders();
    if (!auth) return;
    const previous = runs;
    setRuns((current) => current.filter((run) => run.id !== runId));
    deleteMapsCensusRun(auth, runId).catch((err) => {
      setRuns(previous);
      setError(err instanceof Error ? err.message : "Failed to delete Maps census run");
    });
  }

  return (
    <AppShell>
      <DreamPageShell>
        <DreamHeader
          eyebrow="Maps Census"
          title="Google Places census, country by country"
          description="A standalone facility count from Google Places — run it for a country, then compare against the Scraping Council's census for the same country."
          action={
            <Link
              to="/maps/new"
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/90"
            >
              <Plus className="size-4" />
              New Maps census
            </Link>
          }
        />

        <div className="mt-8 space-y-4">
          {loading && (
            <DreamPanel className="text-sm text-muted-foreground">Loading runs…</DreamPanel>
          )}
          {error && !loading && <DreamPanel className="text-sm text-rose-600">{error}</DreamPanel>}
          {!loading && !error && runs.length === 0 && (
            <DreamPanel tone="amber" className="p-12 text-center">
              <p className="font-display text-2xl text-foreground">No Maps census runs yet</p>
              <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
                Pick a country and Google Places will be searched city by city for rehab, addiction,
                and psychiatric facilities.
              </p>
              <Link
                to="/maps/new"
                className="mt-6 inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground"
              >
                <Plus className="size-4" />
                Run first census
              </Link>
            </DreamPanel>
          )}
          {!loading &&
            !error &&
            runs.map((run) => <MapsRunCard key={run.id} run={run} onDelete={handleDelete} />)}
        </div>
      </DreamPageShell>
    </AppShell>
  );
}
