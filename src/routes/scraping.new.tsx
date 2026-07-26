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
          className="dream-rise mb-10 inline-flex w-fit items-center gap-2 text-sm text-white/55 transition hover:text-sky-100"
        >
          <ArrowLeft className="size-3.5" />
          All missions
        </Link>

        <div className="dream-rise dream-rise-delay-1 mb-10">
          <p className="font-display text-[11px] font-semibold uppercase tracking-[0.42em] text-sky-300">
            Multimind
          </p>
          <h1 className="mt-3 font-display text-5xl font-semibold tracking-tight text-white sm:text-6xl">
            Scraping Council
          </h1>
          <p className="mt-4 max-w-md text-base leading-relaxed text-white/55">
            Name the mission. Choose a country. We chart the flight — sources, gates, citations —
            while you watch the dream move.
          </p>
        </div>

        <div className="dream-rise dream-rise-delay-2 rounded-[1.75rem] border border-white/10 bg-black/25 p-6 shadow-[0_40px_100px_rgba(0,0,0,0.45)] backdrop-blur-md sm:p-8">
          <MissionComposer />
        </div>
      </DreamPageShell>
    </AppShell>
  );
}
