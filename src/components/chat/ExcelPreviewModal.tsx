import { Modal } from "@/components/Modal";

const ROWS = [
  ["Framework", "Bundle (kB)", "SEO", "Notes"],
  ["Next.js", "78", "Excellent", "Best ecosystem"],
  ["TanStack Start", "65", "Excellent", "Type-safe routing"],
  ["Astro", "12", "Excellent", "Static-first"],
  ["SvelteKit", "28", "Great", "Tiny output"],
];

export function ExcelPreviewModal({
  open,
  onClose,
  onAddToChat,
}: {
  open: boolean;
  onClose: () => void;
  onAddToChat?: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Excel preview" size="xl">
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-sm">
          <thead className="bg-accent/30">
            <tr>
              {ROWS[0].map((h) => (
                <th key={h} className="px-3 py-2 text-left font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.slice(1).map((r, i) => (
              <tr key={i} className="border-t border-border">
                {r.map((c, j) => (
                  <td key={j} className="px-3 py-2">
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded-xl bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
        >
          Download Excel
        </button>
        <button
          type="button"
          className="rounded-xl border border-border px-3 py-2 text-sm hover:bg-accent"
        >
          Regenerate
        </button>
        <button
          type="button"
          onClick={() => {
            onAddToChat?.();
            onClose();
          }}
          className="rounded-xl border border-border px-3 py-2 text-sm hover:bg-accent"
        >
          Add to chat
        </button>
      </div>
    </Modal>
  );
}
