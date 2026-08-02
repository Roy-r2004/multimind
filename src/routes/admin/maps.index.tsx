import { createFileRoute, Link } from "@tanstack/react-router";
import { MapPin, Plus } from "lucide-react";
import { useCallback } from "react";
import {
  AdminError,
  AdminLoading,
  AdminPageFrame,
  DataTable,
  formatDt,
} from "@/components/admin/AdminUi";
import { Button } from "@/components/ui/button";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { MapsRunStatusBadge } from "@/components/maps/MapsRunStatusBadge";
import { useAdminData } from "@/hooks/useAdminData";
import { listMapsCensusRuns } from "@/lib/maps/api";
import { mapsCensusAdminEnabled } from "@/lib/maps/adminFeature";
import { countryFlagEmoji } from "@/lib/maps/countryVisuals";

export const Route = createFileRoute("/admin/maps/")({
  head: () => ({ meta: [{ title: "Maps Census — MultiAI Admin" }] }),
  component: AdminMapsIndexPage,
});

function AdminMapsIndexPage() {
  const loader = useCallback(
    (auth: { token: string; orgId: string }) => listMapsCensusRuns(auth),
    [],
  );
  const { data, loading, error, reload } = useAdminData(loader);

  if (!mapsCensusAdminEnabled) {
    return (
      <AdminPageFrame
        title="Maps Census"
        description="Operational admin for Maps discovery campaigns."
      >
        <GlassCard className="p-8 text-center text-sm text-muted-foreground">
          Maps Census admin is disabled. Set{" "}
          <code className="rounded bg-muted px-1.5 py-0.5">VITE_MAPS_CENSUS_ADMIN_ENABLED=true</code>{" "}
          to enable this section.
        </GlassCard>
      </AdminPageFrame>
    );
  }

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  return (
    <AdminPageFrame
      eyebrow="Maps Census"
      title="Campaigns"
      description="Monitor discovery runs, review providers, and manage operational controls."
      actions={
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => void reload()}>
            Refresh
          </Button>
          <Button asChild size="sm">
            <Link to="/maps/new">
              <Plus className="size-4" />
              New campaign
            </Link>
          </Button>
        </div>
      }
    >
      <DataTable
        columns={[
          { key: "country", label: "Country" },
          { key: "status", label: "Status" },
          { key: "progress", label: "Cells" },
          { key: "places", label: "Places" },
          { key: "started", label: "Started" },
          { key: "actions", label: "" },
        ]}
        rows={(data ?? []).map((run) => ({
          id: run.id,
          cells: {
            country: (
              <div className="flex items-center gap-2">
                <span aria-hidden="true">{countryFlagEmoji(run.country_code)}</span>
                <div>
                  <div className="font-medium">{run.country_name}</div>
                  <div className="text-xs text-muted-foreground">{run.country_code}</div>
                </div>
              </div>
            ),
            status: <MapsRunStatusBadge status={run.status} />,
            progress: (
              <span>
                {run.cells_completed}/{run.cells_total}
              </span>
            ),
            places: (
              <div className="text-xs">
                <div>{run.places_found} found</div>
                <div className="text-muted-foreground">{run.places_with_website} with website</div>
              </div>
            ),
            started: formatDt(run.started_at),
            actions: (
              <Link
                to="/admin/maps/$runId"
                params={{ runId: run.id }}
                className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
              >
                <MapPin className="size-3.5" />
                Open admin →
              </Link>
            ),
          },
        }))}
        empty="No Maps census campaigns yet. Start one from the public Maps page."
      />

      {(data ?? []).length > 0 && (
        <p className="mt-4 text-xs text-muted-foreground">
          End-user run view remains at{" "}
          <Link to="/maps" className="text-primary hover:underline">
            /maps
          </Link>
          . Admin controls require owner/admin role.
        </p>
      )}
    </AdminPageFrame>
  );
}
