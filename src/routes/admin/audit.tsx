import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useState } from "react";
import {
  AdminError,
  AdminLoading,
  AdminPageFrame,
  DataTable,
  formatDt,
  StatCard,
} from "@/components/admin/AdminUi";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { useAdminData } from "@/hooks/useAdminData";
import { api } from "@/lib/api";
import type { ApiAdminAuditLog } from "@/lib/api/types";

export const Route = createFileRoute("/admin/audit")({
  head: () => ({ meta: [{ title: "Audit Logs — MultiAI Admin" }] }),
  component: AdminAuditPage,
});

const SEVERITY_CLASS: Record<string, string> = {
  info: "text-blue-700 bg-blue-50",
  warning: "text-amber-800 bg-amber-50",
  critical: "text-red-800 bg-red-50",
  debug: "text-muted-foreground bg-muted",
};

function AdminAuditPage() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string | null>(null);

  const loader = useCallback(
    async (auth: { token: string; orgId: string }) => {
      const [logs, stats] = await Promise.all([
        api.admin.auditLogs(auth, {
          q: q || undefined,
          category: category || undefined,
          page,
          limit: 50,
        }),
        api.admin.auditStats(auth),
      ]);
      return { logs, stats };
    },
    [q, category, page],
  );

  const { data, loading, error, reload } = useAdminData(loader);

  if (loading && !data) return <AdminLoading />;
  if (error && !data) return <AdminError message={error} />;

  const logs = data?.logs;
  const stats = data?.stats;

  return (
    <AdminPageFrame
      title="Audit Logs"
      description="Immutable enterprise audit trail — every API call, sign-in, and admin action."
      actions={
        <button
          type="button"
          onClick={() => void reload()}
          className="rounded-xl border border-border px-3 py-2 text-sm hover:bg-accent"
        >
          Refresh
        </button>
      }
    >
      {stats && (
        <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Total events" value={stats.total.toLocaleString()} />
          <StatCard label="Last 24 hours" value={stats.last_24h.toLocaleString()} />
          <StatCard label="Last 7 days" value={stats.last_7d.toLocaleString()} />
          <StatCard label="Critical" value={stats.critical} />
        </div>
      )}

      <GlassCard className="mb-4 p-4">
        <form
          className="flex flex-wrap gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(1);
            void reload();
          }}
        >
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search summary, email, action…"
            className="min-w-[220px] flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="rounded-lg border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="">All categories</option>
            {stats?.by_category.map((c) => (
              <option key={c.category} value={c.category}>
                {c.category} ({c.count})
              </option>
            ))}
          </select>
          <button type="submit" className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
            Filter
          </button>
        </form>
      </GlassCard>

      <DataTable
        columns={[
          { key: "time", label: "Time" },
          { key: "severity", label: "Severity" },
          { key: "actor", label: "Actor" },
          { key: "action", label: "Action" },
          { key: "summary", label: "Summary" },
          { key: "detail", label: "" },
        ]}
        rows={(logs?.items ?? []).map((log) => ({
          id: log.id,
          cells: {
            time: <span className="whitespace-nowrap text-xs">{formatDt(log.created_at)}</span>,
            severity: (
              <span
                className={`rounded-full px-2 py-0.5 text-xs capitalize ${SEVERITY_CLASS[log.severity] ?? ""}`}
              >
                {log.severity}
              </span>
            ),
            actor: (
              <div>
                <div className="font-medium">{log.actor_name || "—"}</div>
                <div className="text-xs text-muted-foreground">{log.actor_email}</div>
              </div>
            ),
            action: <code className="text-xs">{log.action}</code>,
            summary: <span className="line-clamp-2 max-w-md text-xs">{log.summary}</span>,
            detail: (
              <button
                type="button"
                className="text-xs text-primary hover:underline"
                onClick={() => setExpanded(expanded === log.id ? null : log.id)}
              >
                {expanded === log.id ? "Hide" : "Details"}
              </button>
            ),
          },
        }))}
        empty="No audit events yet. Activity will appear as users interact with the app."
      />

      {expanded && logs?.items && (
        <AuditDetail log={logs.items.find((l) => l.id === expanded)!} />
      )}

      {logs && logs.total > logs.limit && (
        <div className="mt-4 flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            Page {logs.page} · {logs.total.toLocaleString()} total
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="rounded-lg border border-border px-3 py-1.5 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page * logs.limit >= logs.total}
              onClick={() => setPage((p) => p + 1)}
              className="rounded-lg border border-border px-3 py-1.5 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </AdminPageFrame>
  );
}

function AuditDetail({ log }: { log: ApiAdminAuditLog }) {
  return (
    <GlassCard className="mt-4 p-4">
      <h3 className="font-medium">Event detail</h3>
      <pre className="mt-3 max-h-96 overflow-auto rounded-lg bg-muted/50 p-3 text-xs">
        {JSON.stringify(log, null, 2)}
      </pre>
    </GlassCard>
  );
}
