import { createFileRoute } from "@tanstack/react-router";
import { Users, MessageSquare, LayoutGrid, Activity } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { ADMIN_USERS } from "@/lib/mock";

export const Route = createFileRoute("/admin")({
  head: () => ({ meta: [{ title: "Admin — MultiAI" }] }),
  component: Admin,
});

const STATS = [
  { label: "Total users", value: "1,284", icon: Users, delta: "+12% this week" },
  { label: "Total chats", value: "27,419", icon: MessageSquare, delta: "+1,103 today" },
  { label: "Model Sets", value: "146", icon: LayoutGrid, delta: "+8 this week" },
  { label: "Verdict calls", value: "82,001", icon: Activity, delta: "Healthy" },
];

function Admin() {
  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <div>
          <h1 className="text-2xl font-semibold">Admin dashboard</h1>
          <p className="mt-1 text-sm text-muted-foreground">Overview, usage and user management.</p>
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {STATS.map((s) => (
            <div key={s.label} className="rounded-2xl border border-border bg-card p-5">
              <div className="flex items-center justify-between">
                <div className="text-xs text-muted-foreground">{s.label}</div>
                <s.icon className="size-4 text-muted-foreground" />
              </div>
              <div className="mt-3 text-2xl font-semibold">{s.value}</div>
              <div className="mt-1 text-xs text-success">{s.delta}</div>
            </div>
          ))}
        </div>

        <div className="mt-8 rounded-2xl border border-border bg-card p-5">
          <h2 className="font-medium">Usage overview</h2>
          <div className="mt-4 h-40 rounded-xl bg-gradient-to-t from-accent/60 to-transparent relative overflow-hidden">
            <svg viewBox="0 0 400 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
              <polyline fill="none" stroke="var(--color-primary)" strokeWidth="2"
                points="0,80 30,70 60,72 90,55 120,60 150,40 180,42 210,28 240,35 270,22 300,30 330,18 360,22 400,10" />
            </svg>
          </div>
        </div>

        <div className="mt-8 rounded-2xl border border-border bg-card">
          <div className="flex items-center justify-between p-5">
            <h2 className="font-medium">Users</h2>
            <input placeholder="Search users…" className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm" />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-accent/30 text-left text-xs uppercase tracking-wider text-muted-foreground">
                <tr><th className="px-5 py-2">Name</th><th className="px-5 py-2">Email</th><th className="px-5 py-2">Role</th><th className="px-5 py-2">Chats</th><th className="px-5 py-2"></th></tr>
              </thead>
              <tbody>
                {ADMIN_USERS.map((u) => (
                  <tr key={u.email} className="border-t border-border">
                    <td className="px-5 py-3 font-medium">{u.name}</td>
                    <td className="px-5 py-3 text-muted-foreground">{u.email}</td>
                    <td className="px-5 py-3"><span className="rounded-full bg-accent px-2 py-0.5 text-xs">{u.role}</span></td>
                    <td className="px-5 py-3 text-muted-foreground">{u.chats}</td>
                    <td className="px-5 py-3 text-right"><button className="rounded-md border border-border px-2 py-1 text-xs hover:bg-accent">View</button> <button className="rounded-md border border-border px-2 py-1 text-xs hover:bg-accent">Edit</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
