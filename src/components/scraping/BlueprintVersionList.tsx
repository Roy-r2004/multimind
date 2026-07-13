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
  return blueprint.display_name?.trim() || `Blueprint v${blueprint.version}`;
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
              "flex w-full cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-colors",
              selected ? "border-primary bg-primary/10" : "border-border hover:bg-accent",
            )}
          >
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="truncate font-medium">{blueprintDisplayName(blueprint)}</span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  Version {blueprint.version}
                </span>
                <MissionStatusBadge status={blueprint.status} />
                {active && (
                  <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
                    Active
                  </span>
                )}
              </span>
              <span className="mt-1 block text-xs text-muted-foreground">
                Created {new Date(blueprint.created_at).toLocaleString()}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
