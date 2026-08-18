import type { ApiPlaybookObservation } from "@/lib/api/types";
import { groupObservationsByCategory } from "@/lib/playbooks";
import { PlaybookObservationCard } from "@/components/playbooks/PlaybookObservationCard";

export function PlaybookObservationGroups({
  observations,
}: {
  observations: ApiPlaybookObservation[];
}) {
  const groups = groupObservationsByCategory(observations);
  if (groups.length === 0) {
    return <p className="text-sm text-muted-foreground">No observations are available yet.</p>;
  }

  return (
    <div className="space-y-8">
      {groups.map((group) => (
        <section key={group.category} aria-labelledby={`playbook-cat-${group.category}`}>
          <h2
            id={`playbook-cat-${group.category}`}
            className="mb-3 font-display text-xl font-semibold tracking-tight"
          >
            {group.label}
          </h2>
          <div className="grid gap-3">
            {group.items.map((item) => (
              <PlaybookObservationCard key={item.id} observation={item} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
