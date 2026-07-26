import { MissionStatusBadge } from "@/components/scraping/MissionStatusBadge";
import type { ScrapingBlueprint, ScrapingMissionDetail } from "@/lib/scraping/types";
import { cn } from "@/lib/utils";

type Props = {
  blueprints: ScrapingBlueprint[];
  mission: ScrapingMissionDetail;
  selectedId: string;
  onSelect: (blueprintId: string) => void;
};

function blueprintDisplayName(blueprint: ScrapingBlueprint): string {
  return blueprint.display_name?.trim() || `Chart v${blueprint.version}`;
}

export function BlueprintVersionList({ blueprints, mission, selectedId, onSelect }: Props) {
  return (
    <div className="w-full space-y-2 md:max-w-md">
      {blueprints.map((blueprint) => {
        const selected = blueprint.id === selectedId;
        const active = blueprint.id === mission.active_blueprint_id;
        return (
          <div
            key={blueprint.id}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(blueprint.id)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect(blueprint.id);
              }
            }}
            className={cn(
              "flex w-full cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-sm transition",
              selected
                ? "border-[#d4a84b]/50 bg-[#d4a84b]/12 shadow-[0_0_24px_rgba(212,168,75,0.12)]"
                : "border-white/10 bg-white/[0.03] hover:border-white/25 hover:bg-white/[0.06]",
            )}
          >
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="truncate font-medium text-[#f7f1e4]">
                  {blueprintDisplayName(blueprint)}
                </span>
                <span className="shrink-0 text-xs text-white/40">v{blueprint.version}</span>
                <MissionStatusBadge status={blueprint.status} />
                {active && (
                  <span className="rounded-full border border-teal-300/40 bg-teal-400/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-teal-100">
                    Active
                  </span>
                )}
              </span>
              <span className="mt-1 block text-xs text-white/40">
                Created {new Date(blueprint.created_at).toLocaleString()}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
