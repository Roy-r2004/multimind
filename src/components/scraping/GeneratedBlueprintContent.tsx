import { BlueprintSection } from "@/components/scraping/BlueprintSection";
import { Badge } from "@/components/ui/badge";
import {
  BLUEPRINT_REVIEW_CONTAINER_TITLES,
  buildBlueprintReviewModel,
  sourceHasClickableUrl,
  type BlueprintReviewSource,
  type BlueprintReviewTerm,
} from "@/lib/scraping/blueprintReviewPresentation";
import type { ScrapingBlueprintCitation } from "@/lib/scraping/types";

export function GeneratedBlueprintContent({
  structured,
  citations,
  countryIso2,
}: {
  humanReadable?: string | null;
  structured?: Record<string, unknown> | null;
  citations?: ScrapingBlueprintCitation[] | null;
  countryIso2?: string | null;
}) {
  const model = buildBlueprintReviewModel(structured, { countryIso2, citations });

  return (
    <div className="space-y-4" data-testid="blueprint-review-containers">
      <BlueprintSection title={BLUEPRINT_REVIEW_CONTAINER_TITLES[0]}>
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Country" value={model.countryName} />
            <Field label="ISO Alpha-2" value={model.countryIso2} />
            <Field label="ISO Alpha-3" value={model.countryIso3} />
            <Field label="Continent" value={model.continent} />
          </div>

          <div>
            <h4 className="mb-2 text-sm font-medium">Regions & areas planned for coverage</h4>
            {model.regions.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {model.regions.map((region) => (
                  <Badge key={region} variant="outline">
                    {region}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground">No regions were provided yet.</p>
            )}
          </div>

          {model.nationalCoverageSummary && (
            <Block title="National coverage summary" body={model.nationalCoverageSummary} />
          )}
          {model.countryContainmentStatement && (
            <Block title="Country containment" body={model.countryContainmentStatement} />
          )}
        </div>
      </BlueprintSection>

      <BlueprintSection title={BLUEPRINT_REVIEW_CONTAINER_TITLES[1]}>
        <div className="space-y-4">
          <LanguageGroup label="Primary language" values={model.primaryLanguages} />
          <LanguageGroup label="Additional local languages" values={model.additionalLanguages} />
          <LanguageGroup
            label="Languages used during discovery"
            values={model.discoveryLanguages}
          />
        </div>
      </BlueprintSection>

      <BlueprintSection title={BLUEPRINT_REVIEW_CONTAINER_TITLES[2]}>
        {model.terminology.length > 0 ? (
          <div className="space-y-3">
            {model.terminology.map((item) => (
              <TerminologyCard key={`${item.language}-${item.term}`} item={item} />
            ))}
            {model.terminologyHasMore && (
              <p className="text-sm text-muted-foreground">
                Additional terminology will be used by the research pipeline.
              </p>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground">No priority search terminology was provided yet.</p>
        )}
      </BlueprintSection>

      <BlueprintSection title={BLUEPRINT_REVIEW_CONTAINER_TITLES[3]}>
        {model.sources.length > 0 ? (
          <div className="space-y-3">
            {model.sources.map((source) => (
              <SourceCard
                key={`${source.category}-${source.title}-${source.url ?? "none"}`}
                source={source}
              />
            ))}
            {model.sourcesHaveMore && (
              <p className="text-sm text-muted-foreground">
                Additional authoritative sources will be discovered during the research pipeline.
              </p>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground">
            No priority sources or directories were provided yet.
          </p>
        )}
      </BlueprintSection>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1">{value || "Not provided"}</div>
    </div>
  );
}

function Block({ title, body }: { title: string; body: string }) {
  return (
    <div>
      <h4 className="mb-1 text-sm font-medium">{title}</h4>
      <p className="text-muted-foreground">{body}</p>
    </div>
  );
}

function LanguageGroup({ label, values }: { label: string; values: string[] }) {
  return (
    <div>
      <h4 className="mb-2 text-sm font-medium">{label}</h4>
      {values.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {values.map((language) => (
            <Badge key={`${label}-${language}`} variant="outline">
              {language}
            </Badge>
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground">Not provided</p>
      )}
    </div>
  );
}

function TerminologyCard({ item }: { item: BlueprintReviewTerm }) {
  const isEnglish = item.language.toLowerCase() === "english";
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{item.language}</div>
      <div className="mt-1 text-base font-medium">{item.term}</div>
      {!isEnglish && (
        <div className="mt-2 text-sm">
          <span className="text-muted-foreground">English: </span>
          {item.englishMeaning || "Meaning to be confirmed"}
        </div>
      )}
      {item.category && (
        <div className="mt-2">
          <Badge variant="outline">{item.category}</Badge>
        </div>
      )}
    </div>
  );
}

function SourceCard({ source }: { source: BlueprintReviewSource }) {
  const clickable = sourceHasClickableUrl(source);
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{source.title}</span>
        <Badge variant="outline">{source.sourceType || source.category}</Badge>
      </div>
      {source.reason && <p className="mt-2 text-sm text-muted-foreground">{source.reason}</p>}
      <div className="mt-2 text-sm">
        {clickable && source.url ? (
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="break-all text-primary underline-offset-4 hover:underline"
          >
            {source.url}
          </a>
        ) : (
          <span className="text-muted-foreground">URL to be confirmed during research</span>
        )}
      </div>
    </div>
  );
}
