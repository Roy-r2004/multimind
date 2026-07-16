import { BlueprintSection } from "@/components/scraping/BlueprintSection";
import type { ScrapingBlueprintContent } from "@/lib/scraping/types";

function List({ items }: { items: string[] }) {
  return items.length ? (
    <ul className="list-disc space-y-1 pl-5">
      {items.map((item, index) => (
        <li key={`${item}-${index}`}>{item}</li>
      ))}
    </ul>
  ) : (
    <span className="text-muted-foreground">None</span>
  );
}

export function BlueprintViewer({ content }: { content: ScrapingBlueprintContent }) {
  const regionWarning = broadRegionWarning(content.scope.regions);
  return (
    <div className="space-y-4">
      <BlueprintSection title="Mission Summary">
        <div className="space-y-3">
          <p>{content.mission_summary.goal}</p>
          <div>
            <div className="font-medium">Target entities</div>
            <List items={content.mission_summary.target_entities} />
          </div>
          <div>
            <div className="font-medium">Deliverables</div>
            <List items={content.mission_summary.deliverables} />
          </div>
        </div>
      </BlueprintSection>
      <BlueprintSection title="Scope">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <div className="font-medium">Included</div>
            <List items={content.scope.included} />
          </div>
          <div>
            <div className="font-medium">Excluded</div>
            <List items={content.scope.excluded} />
          </div>
          <div>
            <div className="font-medium">Countries</div>
            <List items={content.scope.countries} />
          </div>
          <div>
            <div className="font-medium">Regions</div>
            <List items={content.scope.regions} />
            {regionWarning && (
              <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
                {regionWarning}
              </div>
            )}
          </div>
        </div>
      </BlueprintSection>
      <BlueprintSection title="Languages">
        <List items={content.languages} />
      </BlueprintSection>
      <BlueprintSection title="Search Terms">
        <div className="space-y-2">
          {content.search_terms.map((item, index) => (
            <div
              key={`${item.language}-${item.term}-${index}`}
              className="rounded-lg border border-border p-3"
            >
              <div className="font-medium">{item.term}</div>
              <div className="text-xs text-muted-foreground">
                {item.language} · {item.purpose}
              </div>
            </div>
          ))}
        </div>
      </BlueprintSection>
      <BlueprintSection title="Source Strategy">
        <div className="space-y-2">
          {content.source_strategy.map((item, index) => (
            <div
              key={`${item.source_type}-${index}`}
              className="rounded-lg border border-border p-3"
            >
              <div className="font-medium">{item.source_type}</div>
              <div className="text-xs text-muted-foreground">
                Priority {item.priority} · {item.trust_tier} ·{" "}
                {item.required ? "Required" : "Optional"}
              </div>
              <p className="mt-2">{item.purpose}</p>
            </div>
          ))}
        </div>
      </BlueprintSection>
      <BlueprintSection title="Data Schema">
        <div className="space-y-2">
          {content.data_schema.map((item) => (
            <div key={item.field_name} className="rounded-lg border border-border p-3">
              <div className="font-medium">{item.field_name}</div>
              <div className="text-xs text-muted-foreground">
                {item.required ? "Required" : "Optional"}
              </div>
              <p className="mt-2">{item.description}</p>
            </div>
          ))}
        </div>
      </BlueprintSection>
      <BlueprintSection title="Classification Rules">
        <List items={content.classification_rules} />
      </BlueprintSection>
      <BlueprintSection title="Verification Rules">
        <List items={content.verification_rules} />
      </BlueprintSection>
      <BlueprintSection title="Deduplication Rules">
        <List items={content.deduplication_rules} />
      </BlueprintSection>
      <BlueprintSection title="Compliance Rules">
        <List items={content.compliance_rules} />
      </BlueprintSection>
      <BlueprintSection title="Task Plan">
        <div className="space-y-2">
          {content.task_plan.map((item) => (
            <div key={item.order} className="rounded-lg border border-border p-3">
              <div className="font-medium">
                {item.order}. {item.task}
              </div>
              <div className="text-xs text-muted-foreground">{item.assigned_role}</div>
            </div>
          ))}
        </div>
      </BlueprintSection>
      <BlueprintSection title="Stop Conditions">
        <List items={content.stop_conditions} />
      </BlueprintSection>
      <BlueprintSection title="Estimated Workload">
        <div className="grid gap-2 text-sm md:grid-cols-2">
          <div>Expected queries: {content.estimated_workload.expected_queries ?? "Unknown"}</div>
          <div>Expected pages: {content.estimated_workload.expected_pages ?? "Unknown"}</div>
          <div>Expected AI calls: {content.estimated_workload.expected_ai_calls ?? "Unknown"}</div>
          <div>
            Estimated cost USD: {content.estimated_workload.estimated_cost_usd ?? "Unknown"}
          </div>
        </div>
        <div className="mt-3">
          <List items={content.estimated_workload.notes} />
        </div>
      </BlueprintSection>
      <BlueprintSection title="Agent Assignments">
        <div className="space-y-2">
          {content.agent_assignments.map((item) => (
            <div
              key={`${item.role}-${item.model_id}`}
              className="rounded-lg border border-border p-3"
            >
              <div className="font-medium">{item.role}</div>
              <div className="text-xs text-muted-foreground">{item.model_id}</div>
              <p className="mt-2">{item.responsibility}</p>
            </div>
          ))}
        </div>
      </BlueprintSection>
    </div>
  );
}

function broadRegionWarning(regions: string[]) {
  if (regions.length !== 1) {
    return null;
  }
  const normalized = regions[0]?.trim().toLowerCase() ?? "";
  if (!normalized) {
    return null;
  }
  const collectivePatterns = [
    /^all\b/,
    /\ball\s+\d+\b/,
    /\ball\s+[a-z\s-]*(regions|communities|provinces|territories|states|departments|governorates|cities)\b/,
    /\bnationwide\b/,
    /\bnational\b/,
    /\bwhole country\b/,
    /\bentire country\b/,
  ];
  if (!collectivePatterns.some((pattern) => pattern.test(normalized))) {
    return null;
  }
  return "Coverage uses one broad national region. For detailed country coverage, regenerate the blueprint with each administrative region listed separately.";
}
