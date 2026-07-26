export function BlueprintSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="dream-rise relative overflow-hidden rounded-[1.35rem] border border-border bg-card/75 p-5 shadow-[0_16px_40px_rgba(0,0,0,0.18)] backdrop-blur-md">
      <div
        aria-hidden
        className="pointer-events-none absolute -left-8 top-0 h-24 w-24 rounded-full blur-3xl"
        style={{ background: "rgba(46,120,130,0.2)" }}
      />
      <h3 className="relative text-[11px] font-semibold uppercase tracking-[0.28em] text-primary/90">
        {title}
      </h3>
      <div className="relative mt-4 text-sm leading-6 text-foreground/90">{children}</div>
    </section>
  );
}
