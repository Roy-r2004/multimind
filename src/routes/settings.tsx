import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { OpenRouterModelSearch } from "@/components/OpenRouterModelSearch";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { useAuth } from "@/lib/auth";
import { useChatStore } from "@/lib/store";
import { STRATEGIES } from "@/lib/mock";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/settings")({
  head: () => ({ meta: [{ title: "Settings — MultiAI" }] }),
  component: SettingsPage,
});

function SettingsPage() {
  const { session, signOut } = useAuth();
  const { modelSets, activeModelSetId, setActiveModelSetId } = useChatStore();
  const [theme, setTheme] = useState<"dark" | "light">("light");

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10">
        <PageHeader
          eyebrow="Account"
          title="Settings"
          description="Profile, defaults, and the live models available through OpenRouter."
        />

        <div className="mt-10 space-y-6">
          <GlassCard className="p-6">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">Profile</h2>
            <div className="mt-4 flex items-center gap-4">
              <div className="grid size-14 place-items-center rounded-full bg-primary/15 text-lg font-semibold text-primary">
                {session?.user.full_name?.slice(0, 1) ?? "?"}
              </div>
              <div>
                <div className="font-medium">{session?.user.full_name}</div>
                <div className="text-sm text-muted-foreground">{session?.user.email}</div>
                <div className="text-xs text-muted-foreground">{session?.organization.name}</div>
              </div>
            </div>
            <button
              onClick={() => signOut()}
              className="mt-4 text-sm text-destructive hover:underline"
            >
              Sign out
            </button>
          </GlassCard>

          <GlassCard className="p-6">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">Preferences</h2>
            <div className="mt-4 space-y-4">
              <div>
                <div className="mb-2 text-sm font-medium">Default model set</div>
                <select
                  value={activeModelSetId}
                  onChange={(e) => setActiveModelSetId(e.target.value)}
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm"
                >
                  {modelSets.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <div className="mb-2 text-sm font-medium">Appearance</div>
                <div className="inline-flex rounded-lg border border-border p-0.5">
                  {(["dark", "light"] as const).map((t) => (
                    <button
                      key={t}
                      onClick={() => setTheme(t)}
                      className={cn(
                        "rounded-md px-3 py-1.5 text-xs capitalize",
                        theme === t ? "bg-primary text-primary-foreground" : "text-muted-foreground",
                      )}
                    >
                      {t}
                    </button>
                  ))}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">Dark mode is the default experience.</p>
              </div>
            </div>
          </GlassCard>

          <GlassCard glow className="p-6">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-primary/80">Model library</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Search OpenRouter&apos;s live catalog and add models directly to your organization.
              Built-in defaults are kept in sync with current OpenRouter names and pricing.
            </p>
            <div className="mt-4">
              <OpenRouterModelSearch />
            </div>
          </GlassCard>

          <GlassCard className="p-6">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">Verdict strategies</h2>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {STRATEGIES.map((s) => (
                <div key={s.name} className="rounded-xl border border-border p-3">
                  <div className="text-sm font-medium">{s.name}</div>
                  <p className="mt-1 text-xs text-muted-foreground">{s.desc}</p>
                </div>
              ))}
            </div>
          </GlassCard>
        </div>
      </div>
    </AppShell>
  );
}
