import { useEffect, useState } from "react";
import { LayoutTemplate, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ApiTemplate } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";

export function TemplateMenu({ onPick }: { onPick: (template: ApiTemplate) => void }) {
  const { authHeaders } = useAuth();
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<ApiTemplate[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    const auth = authHeaders();
    if (!auth) return;
    setLoading(true);
    void api.templates
      .list(auth)
      .then(setTemplates)
      .catch(() => setTemplates([]))
      .finally(() => setLoading(false));
  }, [open, authHeaders]);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent"
      >
        <LayoutTemplate className="size-3.5" /> Templates
      </button>
      {open && (
        <div className="absolute bottom-11 left-0 z-30 w-72 rounded-xl border border-border bg-popover p-1 shadow-xl">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Loading…
            </div>
          ) : templates.length === 0 ? (
            <p className="px-3 py-4 text-xs text-muted-foreground">No templates yet.</p>
          ) : (
            templates.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => {
                  onPick(t);
                  setOpen(false);
                }}
                className="block w-full rounded-lg px-2.5 py-2 text-left text-sm hover:bg-accent"
              >
                <div className="font-medium">{t.title}</div>
                <div className="text-xs text-muted-foreground line-clamp-2">{t.description}</div>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
