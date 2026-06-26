import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Modal } from "@/components/Modal";
import {
  AdminError,
  AdminLoading,
  AdminPageFrame,
} from "@/components/admin/AdminUi";
import { GlassCard } from "@/components/cinematic/PageChrome";
import { useAdminData } from "@/hooks/useAdminData";
import { api } from "@/lib/api";
import type { ApiAdminCreateMemberInput, ApiAdminMember } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";

export const Route = createFileRoute("/admin/members")({
  head: () => ({ meta: [{ title: "Members — MultiAI Admin" }] }),
  component: AdminMembersPage,
});

const MANAGED_ROLES = ["admin", "member", "viewer"] as const;
type ManagedRole = (typeof MANAGED_ROLES)[number];

function AdminMembersPage() {
  const { authHeaders } = useAuth();
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [saving, setSaving] = useState(false);
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<ApiAdminMember | null>(null);
  const [form, setForm] = useState<ApiAdminCreateMemberInput>({
    full_name: "",
    email: "",
    role: "member",
    temporary_password: "",
  });

  const loader = useCallback(
    (auth: { token: string; orgId: string }) => api.admin.members(auth),
    [],
  );
  const { data: members, loading, error, reload } = useAdminData(loader);

  async function createMember() {
    const auth = authHeaders();
    if (!auth) return;
    setSaving(true);
    setActionError(null);
    try {
      await api.admin.createMember(auth, {
        ...form,
        full_name: form.full_name.trim(),
        email: form.email.trim(),
      });
      setSuccess("Member added.");
      setShowAdd(false);
      setForm({ full_name: "", email: "", role: "member", temporary_password: "" });
      await reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to add member");
    } finally {
      setSaving(false);
    }
  }

  async function updateRole(member: ApiAdminMember, role: ManagedRole) {
    const auth = authHeaders();
    if (!auth || member.role === role) return;
    setUpdatingId(member.membership_id);
    try {
      await api.admin.updateMember(auth, member.membership_id, { role });
      setSuccess("Role updated.");
      await reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setUpdatingId(null);
    }
  }

  async function removeMember() {
    const auth = authHeaders();
    if (!auth || !removeTarget) return;
    setUpdatingId(removeTarget.membership_id);
    try {
      await api.admin.removeMember(auth, removeTarget.membership_id);
      setSuccess("Member removed.");
      setRemoveTarget(null);
      await reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to remove");
    } finally {
      setUpdatingId(null);
    }
  }

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  return (
    <AdminPageFrame
      title="Members"
      description="Add, update roles, and remove organization members."
      actions={
        <button
          type="button"
          onClick={() => setShowAdd(true)}
          className="inline-flex items-center gap-2 rounded-xl bg-primary px-3 py-2 text-sm text-primary-foreground"
        >
          <Plus className="size-4" /> Add member
        </button>
      }
    >
      {(actionError || success) && (
        <div
          className={[
            "mb-4 rounded-xl border px-4 py-3 text-sm",
            actionError
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : "border-emerald-200 bg-emerald-50 text-emerald-800",
          ].join(" ")}
        >
          {actionError ?? success}
        </div>
      )}

      <GlassCard className="overflow-hidden p-0">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border bg-muted/50 text-xs uppercase text-muted-foreground">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Email</th>
              <th className="px-4 py-3">Role</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {(members ?? []).map((member) => (
              <tr key={member.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3 font-medium">{member.full_name}</td>
                <td className="px-4 py-3 text-muted-foreground">{member.email}</td>
                <td className="px-4 py-3">
                  {member.role === "owner" ? (
                    <span className="capitalize">{member.role}</span>
                  ) : (
                    <select
                      value={member.role}
                      disabled={updatingId === member.membership_id}
                      onChange={(e) => void updateRole(member, e.target.value as ManagedRole)}
                      className="rounded-lg border border-border px-2 py-1 text-sm capitalize"
                    >
                      {MANAGED_ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                <td className="px-4 py-3">{member.is_active ? "Active" : "Inactive"}</td>
                <td className="px-4 py-3">
                  {member.role !== "owner" && (
                    <button
                      type="button"
                      onClick={() => setRemoveTarget(member)}
                      className="inline-flex items-center gap-1 text-destructive hover:underline"
                    >
                      <Trash2 className="size-3.5" /> Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </GlassCard>

      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add member" size="md">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void createMember();
          }}
        >
          {actionError && <p className="text-sm text-destructive">{actionError}</p>}
          <label className="block text-sm">
            Name
            <input
              required
              value={form.full_name}
              onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              className="mt-1 w-full rounded-lg border border-border px-3 py-2"
            />
          </label>
          <label className="block text-sm">
            Email
            <input
              required
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="mt-1 w-full rounded-lg border border-border px-3 py-2"
            />
          </label>
          <label className="block text-sm">
            Role
            <select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as ManagedRole })}
              className="mt-1 w-full rounded-lg border border-border px-3 py-2 capitalize"
            >
              {MANAGED_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            Temporary password
            <input
              required
              type="password"
              value={form.temporary_password}
              onChange={(e) => setForm({ ...form, temporary_password: e.target.value })}
              className="mt-1 w-full rounded-lg border border-border px-3 py-2"
            />
          </label>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setShowAdd(false)} className="rounded-lg border px-4 py-2 text-sm">
              Cancel
            </button>
            <button type="submit" disabled={saving} className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">
              {saving ? "Adding…" : "Add"}
            </button>
          </div>
        </form>
      </Modal>

      <Modal open={!!removeTarget} onClose={() => setRemoveTarget(null)} title="Remove member?" size="sm">
        <p className="text-sm text-muted-foreground">
          {removeTarget?.full_name} will lose access to this organization.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={() => setRemoveTarget(null)} className="rounded-lg border px-4 py-2 text-sm">
            Cancel
          </button>
          <button type="button" onClick={() => void removeMember()} className="rounded-lg bg-destructive px-4 py-2 text-sm text-destructive-foreground">
            Remove
          </button>
        </div>
      </Modal>
    </AdminPageFrame>
  );
}
