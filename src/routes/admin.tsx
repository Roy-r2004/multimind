import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  Building2,
  FolderKanban,
  Loader2,
  Plus,
  ShieldAlert,
  Trash2,
  Users,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { Modal } from "@/components/Modal";
import { GlassCard, PageHeader } from "@/components/cinematic/PageChrome";
import { api } from "@/lib/api";
import type {
  ApiAdminCreateMemberInput,
  ApiAdminMember,
  ApiAdminOverview,
  ApiAdminUsage,
} from "@/lib/api/types";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/admin")({
  head: () => ({ meta: [{ title: "Admin — MultiAI" }] }),
  component: AdminPage,
});

const ADMIN_ROLES = new Set(["owner", "admin"]);
const MANAGED_ROLES = ["admin", "member", "viewer"] as const;

type ManagedRole = (typeof MANAGED_ROLES)[number];
type AddMemberForm = ApiAdminCreateMemberInput;

function formatUsd(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, value));
}

function AdminPage() {
  const { authHeaders, session, isLoading: authLoading } = useAuth();
  const [overview, setOverview] = useState<ApiAdminOverview | null>(null);
  const [usage, setUsage] = useState<ApiAdminUsage | null>(null);
  const [members, setMembers] = useState<ApiAdminMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showAddMember, setShowAddMember] = useState(false);
  const [savingMember, setSavingMember] = useState(false);
  const [updatingMemberId, setUpdatingMemberId] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<ApiAdminMember | null>(null);
  const [addMemberForm, setAddMemberForm] = useState<AddMemberForm>({
    full_name: "",
    email: "",
    role: "member",
    temporary_password: "",
  });

  const role = session?.organization.role ?? "";
  const canViewAdmin = ADMIN_ROLES.has(role);

  const loadAdminData = useCallback(async () => {
    if (authLoading) return;
    if (!canViewAdmin) {
      setLoading(false);
      return;
    }

    const auth = authHeaders();
    if (!auth) {
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const [overviewData, usageData, memberData] = await Promise.all([
        api.admin.overview(auth),
        api.admin.usage(auth),
        api.admin.members(auth),
      ]);
      setOverview(overviewData);
      setUsage(usageData);
      setMembers(memberData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load admin data");
    } finally {
      setLoading(false);
    }
  }, [authHeaders, authLoading, canViewAdmin]);

  useEffect(() => {
    void loadAdminData();
  }, [loadAdminData]);

  function resetAddMemberForm() {
    setAddMemberForm({
      full_name: "",
      email: "",
      role: "member",
      temporary_password: "",
    });
  }

  async function createMember() {
    const auth = authHeaders();
    if (!auth) return;
    setSavingMember(true);
    setActionError(null);
    setSuccess(null);
    try {
      await api.admin.createMember(auth, {
        full_name: addMemberForm.full_name.trim(),
        email: addMemberForm.email.trim(),
        role: addMemberForm.role,
        temporary_password: addMemberForm.temporary_password,
      });
      setSuccess("Member added.");
      setShowAddMember(false);
      resetAddMemberForm();
      await loadAdminData();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to add member");
    } finally {
      setSavingMember(false);
    }
  }

  async function updateMemberRole(member: ApiAdminMember, nextRole: ManagedRole) {
    const auth = authHeaders();
    if (!auth || member.role === nextRole) return;
    setUpdatingMemberId(member.membership_id);
    setActionError(null);
    setSuccess(null);
    try {
      await api.admin.updateMember(auth, member.membership_id, { role: nextRole });
      setSuccess("Member role updated.");
      await loadAdminData();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to update member");
    } finally {
      setUpdatingMemberId(null);
    }
  }

  async function removeMember() {
    const auth = authHeaders();
    if (!auth || !removeTarget) return;
    setUpdatingMemberId(removeTarget.membership_id);
    setActionError(null);
    setSuccess(null);
    try {
      await api.admin.removeMember(auth, removeTarget.membership_id);
      setSuccess("Member removed.");
      setRemoveTarget(null);
      await loadAdminData();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to remove member");
    } finally {
      setUpdatingMemberId(null);
    }
  }

  if (!authLoading && !session) {
    return (
      <AppShell>
        <div className="mx-auto max-w-3xl px-6 py-20 text-center">
          <GlassCard className="p-10">
            <ShieldAlert className="mx-auto size-8 text-muted-foreground" />
            <h1 className="mt-4 text-xl font-semibold">Sign in required</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Log in as an organization owner or admin.
            </p>
            <Link
              to="/login"
              className="mt-6 inline-flex rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            >
              Log in
            </Link>
          </GlassCard>
        </div>
      </AppShell>
    );
  }

  if (!authLoading && !canViewAdmin) {
    return (
      <AppShell>
        <div className="mx-auto max-w-3xl px-6 py-20 text-center">
          <GlassCard className="p-10">
            <ShieldAlert className="mx-auto size-8 text-destructive" />
            <h1 className="mt-4 text-xl font-semibold">Access denied</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Admin dashboards are available only to organization owners and admins.
            </p>
          </GlassCard>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <PageHeader
          eyebrow="Admin"
          title="Admin"
          description="Manage your organization, members, usage, and workspace overview."
        />

        {loading ? (
          <div className="flex justify-center py-20">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <GlassCard className="mt-8 p-8 text-center text-sm text-destructive">{error}</GlassCard>
        ) : overview ? (
          <>
            {(actionError || success) && (
              <div
                className={[
                  "mt-6 rounded-xl border px-4 py-3 text-sm",
                  actionError
                    ? "border-destructive/30 bg-destructive/10 text-destructive"
                    : "border-emerald-200 bg-emerald-50 text-emerald-800",
                ].join(" ")}
              >
                {actionError ?? success}
              </div>
            )}

            <p className="mt-6 rounded-2xl border border-border bg-card/80 px-5 py-4 text-sm text-muted-foreground shadow-sm">
              <span className="font-medium text-foreground">{overview.organization_name}</span> is
              active, using{" "}
              <span className="font-medium text-foreground">
                {(usage?.budget_used_pct ?? 0).toFixed(1)}%
              </span>{" "}
              of its monthly budget, with{" "}
              <span className="font-medium text-foreground">{overview.total_members}</span>{" "}
              {overview.total_members === 1 ? "member" : "members"}.
            </p>

            <section className="mt-8 grid gap-6 lg:grid-cols-[0.95fr_1.25fr]">
              <SectionCard
                icon={Building2}
                title="Organization"
                subtitle="The organization and access level you are managing."
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <InfoItem label="Organization name" value={overview.organization_name} />
                  <InfoItem label="Plan" value={overview.plan} capitalize />
                  <InfoItem label="Your role" value={overview.user_role} capitalize />
                  <InfoItem label="Members" value={overview.total_members.toLocaleString()} />
                  <InfoItem
                    label="Monthly budget"
                    value={formatUsd(overview.monthly_budget_usd)}
                    className="sm:col-span-2"
                  />
                </div>
              </SectionCard>

              <SectionCard
                icon={BarChart3}
                title="Costs & Usage"
                subtitle="Current spending against this organization's monthly budget."
              >
                {usage ? (
                  <CostsUsage overview={overview} usage={usage} />
                ) : (
                  <p className="text-sm text-muted-foreground">Usage data is not available.</p>
                )}
              </SectionCard>
            </section>

            <SectionCard
              className="mt-6"
              icon={FolderKanban}
              title="Workspace Content"
              subtitle="High-level count of the content available in this organization."
            >
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile label="Projects" value={overview.total_projects.toLocaleString()} />
                <StatTile label="Chats" value={overview.total_chats.toLocaleString()} />
                <StatTile label="Model sets" value={overview.total_model_sets.toLocaleString()} />
                <StatTile label="Templates" value={overview.total_templates.toLocaleString()} />
              </div>
            </SectionCard>

            <GlassCard className="mt-8 overflow-hidden p-0">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
                <div>
                  <div className="flex items-center gap-2">
                    <Users className="size-4 text-primary" />
                    <h2 className="font-medium">Members</h2>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    People who have access to this organization.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setActionError(null);
                    setSuccess(null);
                    setShowAddMember(true);
                  }}
                  className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
                >
                  <Plus className="size-4" /> Add member
                </button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-border bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
                    <tr>
                      <th className="px-5 py-3 font-medium">Name</th>
                      <th className="px-5 py-3 font-medium">Email</th>
                      <th className="px-5 py-3 font-medium">Role</th>
                      <th className="px-5 py-3 font-medium">Status</th>
                      <th className="px-5 py-3 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.length === 0 ? (
                      <tr>
                        <td className="px-5 py-8 text-center text-muted-foreground" colSpan={5}>
                          No members found.
                        </td>
                      </tr>
                    ) : (
                      members.map((member) => (
                        <tr key={member.id} className="border-b border-border last:border-0">
                          <td className="px-5 py-3 font-medium">{member.full_name}</td>
                          <td className="px-5 py-3 text-muted-foreground">{member.email}</td>
                          <td className="px-5 py-3">
                            {member.role === "owner" ? (
                              <span className="capitalize">{member.role}</span>
                            ) : (
                              <select
                                value={member.role}
                                disabled={updatingMemberId === member.membership_id}
                                onChange={(event) =>
                                  void updateMemberRole(member, event.target.value as ManagedRole)
                                }
                                className="rounded-lg border border-border bg-background px-2 py-1 text-sm capitalize disabled:opacity-60"
                              >
                                {MANAGED_ROLES.map((roleOption) => (
                                  <option key={roleOption} value={roleOption}>
                                    {roleOption}
                                  </option>
                                ))}
                              </select>
                            )}
                          </td>
                          <td className="px-5 py-3">
                            <span
                              className={
                                member.is_active ? "text-emerald-700" : "text-muted-foreground"
                              }
                            >
                              {member.is_active ? "Active" : "Inactive"}
                            </span>
                          </td>
                          <td className="px-5 py-3">
                            {member.role === "owner" ? (
                              <span className="text-xs text-muted-foreground">Protected</span>
                            ) : (
                              <button
                                type="button"
                                disabled={updatingMemberId === member.membership_id}
                                onClick={() => {
                                  setActionError(null);
                                  setSuccess(null);
                                  setRemoveTarget(member);
                                }}
                                className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-destructive hover:bg-destructive/10 disabled:opacity-50"
                              >
                                <Trash2 className="size-3.5" /> Remove
                              </button>
                            )}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </GlassCard>
          </>
        ) : null}
      </div>

      <AddMemberModal
        open={showAddMember}
        form={addMemberForm}
        saving={savingMember}
        error={actionError}
        onClose={() => {
          setShowAddMember(false);
          resetAddMemberForm();
        }}
        onChange={setAddMemberForm}
        onSubmit={() => void createMember()}
      />

      <Modal
        open={!!removeTarget}
        onClose={() => setRemoveTarget(null)}
        title="Remove member?"
        size="sm"
      >
        <p className="text-sm text-muted-foreground">
          {removeTarget
            ? `${removeTarget.full_name} will lose access to this organization. Their user account will not be deleted.`
            : "This member will lose access to this organization."}
        </p>
        {actionError && <p className="mt-3 text-sm text-destructive">{actionError}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setRemoveTarget(null)}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!!removeTarget && updatingMemberId === removeTarget.membership_id}
            onClick={() => void removeMember()}
            className="rounded-lg bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground disabled:opacity-50"
          >
            {removeTarget && updatingMemberId === removeTarget.membership_id
              ? "Removing..."
              : "Remove"}
          </button>
        </div>
      </Modal>
    </AppShell>
  );
}

function AddMemberModal({
  open,
  form,
  saving,
  error,
  onClose,
  onChange,
  onSubmit,
}: {
  open: boolean;
  form: AddMemberForm;
  saving: boolean;
  error: string | null;
  onClose: () => void;
  onChange: (form: AddMemberForm) => void;
  onSubmit: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Add member" size="md">
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        <label className="block text-sm">
          <span className="mb-1.5 block font-medium">Name</span>
          <input
            required
            value={form.full_name}
            onChange={(event) => onChange({ ...form, full_name: event.target.value })}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </label>

        <label className="block text-sm">
          <span className="mb-1.5 block font-medium">Email</span>
          <input
            required
            type="email"
            value={form.email}
            onChange={(event) => onChange({ ...form, email: event.target.value })}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </label>

        <label className="block text-sm">
          <span className="mb-1.5 block font-medium">Role</span>
          <select
            value={form.role}
            onChange={(event) => onChange({ ...form, role: event.target.value as ManagedRole })}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm capitalize outline-none focus:border-primary"
          >
            {MANAGED_ROLES.map((roleOption) => (
              <option key={roleOption} value={roleOption}>
                {roleOption}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-xs text-muted-foreground">
            Owners cannot be created from this form.
          </span>
        </label>

        <label className="block text-sm">
          <span className="mb-1.5 block font-medium">Temporary password</span>
          <input
            required
            minLength={1}
            type="password"
            value={form.temporary_password}
            onChange={(event) => onChange({ ...form, temporary_password: event.target.value })}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </label>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {saving ? "Adding..." : "Add member"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SectionCard({
  icon: Icon,
  title,
  subtitle,
  children,
  className,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  subtitle: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <GlassCard className={["p-5", className].filter(Boolean).join(" ")}>
      <div className="mb-5 flex items-start gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
          <Icon className="size-5" />
        </span>
        <div>
          <h2 className="font-semibold">{title}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
        </div>
      </div>
      {children}
    </GlassCard>
  );
}

function InfoItem({
  label,
  value,
  capitalize,
  className,
}: {
  label: string;
  value: string;
  capitalize?: boolean;
  className?: string;
}) {
  return (
    <div className={className}>
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div
        className={["mt-1 text-lg font-semibold", capitalize && "capitalize"]
          .filter(Boolean)
          .join(" ")}
      >
        {value}
      </div>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border bg-background/60 p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-2 text-3xl font-semibold">{value}</div>
    </div>
  );
}

function CostsUsage({ overview, usage }: { overview: ApiAdminOverview; usage: ApiAdminUsage }) {
  const budgetUsed = clampPercent(usage.budget_used_pct);
  const budgetTone =
    budgetUsed >= 90 ? "bg-destructive" : budgetUsed >= 70 ? "bg-amber-500" : "bg-primary";

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-3">
        <InfoItem label="Monthly cost" value={formatUsd(usage.month_usd)} />
        <InfoItem label="Monthly budget" value={formatUsd(overview.monthly_budget_usd)} />
        <InfoItem label="Budget used" value={`${budgetUsed.toFixed(1)}%`} />
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
          <span>Budget progress</span>
          <span>{budgetUsed.toFixed(1)}%</span>
        </div>
        <div className="h-3 overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full rounded-full ${budgetTone}`}
            style={{ width: `${budgetUsed}%` }}
          />
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Today cost" value={formatUsd(usage.today_usd)} />
        <StatTile label="Month tokens" value={usage.month_tokens.toLocaleString()} />
        <StatTile label="Turns" value={usage.total_turns.toLocaleString()} />
        <StatTile label="Cost records" value={usage.total_cost_records.toLocaleString()} />
      </div>
    </div>
  );
}
