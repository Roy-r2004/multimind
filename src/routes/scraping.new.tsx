import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/AppShell";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { MissionComposer } from "@/components/scraping/MissionComposer";

export const Route = createFileRoute("/scraping/new")({
  head: () => ({ meta: [{ title: "New Scraping Mission - MultiAI" }] }),
  component: NewScrapingMissionPage,
});

function NewScrapingMissionPage() {
  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <PageHeader
          eyebrow="Scraping Council"
          title="New Scraping Mission"
          description="Describe the mission and generate a reviewable blueprint."
        />
        <GlassCard className="mt-8 p-6">
          <MissionComposer />
        </GlassCard>
      </div>
    </AppShell>
  );
}
