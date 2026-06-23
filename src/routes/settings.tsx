import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { MODEL_SETS, MODELS, STRATEGIES, TEMPLATES } from "@/lib/mock";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/settings")({
  head: () => ({ meta: [{ title: "Settings — MultiAI" }] }),
  component: SettingsPage,
});

const TABS = ["Profile", "Account", "Preferences", "AI Defaults", "Team", "Security"] as const;

function SettingsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Profile");
  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="text-2xl font-semibold">Settings</h1>

        <div className="mt-6 grid gap-6 md:grid-cols-[200px_1fr]">
          <aside className="space-y-1">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={cn(
                  "w-full rounded-lg px-3 py-2 text-left text-sm",
                  tab === t ? "bg-accent font-medium" : "hover:bg-accent/60",
                )}
              >
                {t}
              </button>
            ))}
          </aside>

          <div className="rounded-2xl border border-border bg-card p-6">
            {tab === "Profile" && (
              <div className="space-y-4">
                <div className="flex items-center gap-4">
                  <div className="grid size-16 place-items-center rounded-full bg-accent text-xl font-semibold">
                    S
                  </div>
                  <button className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-accent">
                    Change avatar
                  </button>
                </div>
                <Row label="Full name" defaultValue="Sara Kassem" />
                <Row label="Email" defaultValue="sara@acme.co" />
              </div>
            )}
            {tab === "Account" && (
              <div className="space-y-4">
                <Row label="Plan" defaultValue="Pro · $19/mo" readOnly />
                <Row label="Billing email" defaultValue="billing@acme.co" />
                <button className="rounded-lg border border-destructive/40 px-3 py-1.5 text-sm text-destructive hover:bg-destructive/10">
                  Delete account
                </button>
              </div>
            )}
            {tab === "Preferences" && (
              <div className="space-y-4">
                <ToggleRow label="Theme" options={["Light", "Dark", "System"]} value="System" />
                <SelectRow label="Default Model Set" options={MODEL_SETS.map((s) => s.name)} />
                <SelectRow
                  label="Default Verdict strategy"
                  options={STRATEGIES.map((s) => s.name)}
                />
                <SelectRow
                  label="Default response style"
                  options={["Concise", "Balanced", "Detailed"]}
                />
              </div>
            )}
            {tab === "AI Defaults" && (
              <div className="space-y-4">
                <label className="block text-sm">
                  <div className="mb-1 font-medium">Default custom Verdict instructions</div>
                  <textarea
                    rows={4}
                    placeholder="e.g. Always cite sources when possible."
                    className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  />
                </label>
                <SelectRow label="Default template" options={TEMPLATES.map((t) => t.title)} />
                <ToggleRow label="Prompt Builder suggestions" options={["Off", "On"]} value="On" />
                <div className="text-xs text-muted-foreground">
                  Models available to you: {MODELS.map((m) => m.name).join(", ")}.
                </div>
              </div>
            )}
            {tab === "Team" && (
              <div className="space-y-3">
                <div className="text-sm text-muted-foreground">
                  Invite teammates to share Model Sets, Templates and Projects.
                </div>
                <div className="flex gap-2">
                  <input
                    placeholder="teammate@company.com"
                    className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  />
                  <button className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground">
                    Invite
                  </button>
                </div>
              </div>
            )}
            {tab === "Security" && (
              <div className="space-y-4">
                <Row label="Current password" type="password" />
                <Row label="New password" type="password" />
                <ToggleRow label="Two-factor auth" options={["Off", "On"]} value="On" />
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function Row({
  label,
  defaultValue,
  type = "text",
  readOnly,
}: {
  label: string;
  defaultValue?: string;
  type?: string;
  readOnly?: boolean;
}) {
  return (
    <label className="block text-sm">
      <div className="mb-1 font-medium">{label}</div>
      <input
        type={type}
        defaultValue={defaultValue}
        readOnly={readOnly}
        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm read-only:text-muted-foreground"
      />
    </label>
  );
}

function ToggleRow({ label, options, value }: { label: string; options: string[]; value: string }) {
  const [v, setV] = useState(value);
  return (
    <div className="text-sm">
      <div className="mb-1 font-medium">{label}</div>
      <div className="inline-flex rounded-lg border border-border bg-background p-0.5">
        {options.map((o) => (
          <button
            key={o}
            onClick={() => setV(o)}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs",
              v === o ? "bg-primary text-primary-foreground" : "text-muted-foreground",
            )}
          >
            {o}
          </button>
        ))}
      </div>
    </div>
  );
}

function SelectRow({ label, options }: { label: string; options: string[] }) {
  return (
    <label className="block text-sm">
      <div className="mb-1 font-medium">{label}</div>
      <select className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm">
        {options.map((o) => (
          <option key={o}>{o}</option>
        ))}
      </select>
    </label>
  );
}
