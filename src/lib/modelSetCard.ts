/** Visual state for a model-set card, derived from the store's active id. */
export function modelSetCardState(setId: string, activeModelSetId: string) {
  const active = activeModelSetId.length > 0 && setId === activeModelSetId;
  return {
    active,
    badge: active ? "Active set" : null,
    className: active
      ? "border-primary bg-primary/5 shadow-[0_10px_28px_oklch(0.55_0.12_240/0.16)] hover:border-primary hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-ring"
      : "border-border hover:border-primary/30 hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-ring",
  };
}
