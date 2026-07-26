import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { DreamPageShell } from "@/components/scraping/DreamPageShell";
import { MissionComposer } from "@/components/scraping/MissionComposer";

export const Route = createFileRoute("/scraping/new")({
  head: () => ({ meta: [{ title: "New Scraping Mission - MultiAI" }] }),
  component: NewScrapingMissionPage,
});

function NewScrapingMissionPage() {
  return (
    <AppShell>
      <DreamPageShell maxWidth="max-w-3xl" className="flex min-h-[calc(100vh-3.5rem)] flex-col justify-center md:min-h-screen">
        <Link
          to="/scraping"
          className="dream-rise mb-10 inline-flex w-fit items-center gap-2 text-sm text-muted-foreground transition hover:text-primary"
        >
          <ArrowLeft className="size-3.5" />
          All missions
        </Link>

        <div className="dream-rise dream-rise-delay-1 mb-10">
          <p className="font-display text-[11px] font-semibold uppercase tracking-[0.42em] text-primary">
            Multimind
          </p>
          <h1 className="mt-3 font-display text-5xl font-semibold tracking-tight text-foreground sm:text-6xl">
            Scraping Council
          </h1>
          <p className="mt-4 max-w-md text-base leading-relaxed text-muted-foreground">
            Name the mission. Choose a country. We chart the flight — sources, gates, citations —
            while you watch the dream move.
          </p>
        </div>

        <div className="dream-rise dream-rise-delay-2 rounded-[1.75rem] border border-border/90 bg-card/95 p-6 shadow-[0_12px_40px_oklch(0.45_0.04_240/0.1)] sm:p-8">
          <MissionComposer />
        </div>
      </DreamPageShell>
    </AppShell>
  );
}
