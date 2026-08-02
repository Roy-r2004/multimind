import { createFileRoute } from "@tanstack/react-router";
import { MapsCampaignAdminPage } from "@/components/maps/admin/MapsCampaignAdminPage";
import { mapsCensusAdminEnabled } from "@/lib/maps/adminFeature";
import { AdminError, AdminPageFrame } from "@/components/admin/AdminUi";
import { GlassCard } from "@/components/cinematic/PageChrome";

export const Route = createFileRoute("/admin/maps/$runId")({
  head: () => ({ meta: [{ title: "Maps Census Campaign — MultiAI Admin" }] }),
  component: AdminMapsRunPage,
});

function AdminMapsRunPage() {
  const { runId } = Route.useParams();

  if (!mapsCensusAdminEnabled) {
    return (
      <AdminPageFrame title="Maps Census" description="Campaign admin dashboard.">
        <GlassCard className="p-8 text-center text-sm text-muted-foreground">
          Maps Census admin is disabled. Set{" "}
          <code className="rounded bg-muted px-1.5 py-0.5">VITE_MAPS_CENSUS_ADMIN_ENABLED=true</code>{" "}
          to enable this section.
        </GlassCard>
      </AdminPageFrame>
    );
  }

  if (!runId) {
    return <AdminError message="Missing campaign run id." />;
  }

  return <MapsCampaignAdminPage runId={runId} />;
}
