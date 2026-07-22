import { GlassCard } from "@/components/cinematic/PageChrome";

export function BlueprintSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <GlassCard className="p-5">
      <h3 className="text-sm font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {title}
      </h3>
      <div className="mt-4 text-sm leading-6 text-foreground">{children}</div>
    </GlassCard>
  );
}
