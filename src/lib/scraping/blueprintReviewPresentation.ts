import type { ScrapingBlueprintCitation } from "@/lib/scraping/types";

export const BLUEPRINT_REVIEW_CONTAINER_TITLES = [
  "Country & Regions",
  "Languages",
  "Search Terminology",
  "Sources & Directories",
] as const;

export const TERMINOLOGY_DISPLAY_LIMIT = 10;
export const SOURCES_DISPLAY_LIMIT = 10;

export type BlueprintReviewSource = {
  title: string;
  sourceType: string | null;
  reason: string | null;
  url: string | null;
  category: string;
  rank: number;
};

export type BlueprintReviewTerm = {
  term: string;
  language: string;
  englishMeaning: string | null;
  category: string | null;
  rank: number;
};

export type BlueprintReviewModel = {
  countryName: string | null;
  countryIso2: string | null;
  countryIso3: string | null;
  continent: string | null;
  regions: string[];
  nationalCoverageSummary: string | null;
  countryContainmentStatement: string | null;
  primaryLanguages: string[];
  additionalLanguages: string[];
  discoveryLanguages: string[];
  terminology: BlueprintReviewTerm[];
  terminologyHasMore: boolean;
  sources: BlueprintReviewSource[];
  sourcesHaveMore: boolean;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => asString(item)).filter((item): item is string => Boolean(item));
}

function strategySummary(value: unknown): string | null {
  const record = asRecord(value);
  return record ? asString(record.summary) : asString(value);
}

function safeExternalUrl(url: string | null | undefined): string | null {
  if (!url?.trim()) return null;
  try {
    const parsed = new URL(url.trim());
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function uniqueStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(value);
  }
  return result;
}

function defaultContainmentStatement(countryName: string | null): string | null {
  if (!countryName) return null;
  return `Only treatment facilities physically located inside ${countryName} may qualify. Nearby foreign facilities must be excluded.`;
}

function terminologyCategory(purpose: string | null, term: string): string | null {
  const haystack = `${purpose ?? ""} ${term}`.toLowerCase();
  if (
    haystack.includes("detox") &&
    (haystack.includes("residential") || haystack.includes("rehab"))
  ) {
    return "Detox + residential rehabilitation";
  }
  if (haystack.includes("inpatient")) return "Inpatient treatment";
  if (haystack.includes("residential")) return "Residential treatment";
  if (haystack.includes("private") || haystack.includes("paid")) return "Private or paid treatment";
  if (haystack.includes("detox")) return "Detoxification";
  if (
    haystack.includes("addiction treatment center") ||
    haystack.includes("rehab center") ||
    haystack.includes("rehabilitation center")
  ) {
    return "Addiction treatment center";
  }
  if (
    haystack.includes("addiction") ||
    haystack.includes("rehab") ||
    haystack.includes("substance")
  ) {
    return "Rehabilitation";
  }
  if (purpose?.trim()) return purpose.trim();
  return null;
}

function terminologyRank(term: BlueprintReviewTerm): number {
  const haystack = `${term.term} ${term.englishMeaning ?? ""} ${term.category ?? ""}`.toLowerCase();
  if (haystack.includes("addiction") && haystack.includes("rehab")) return 1;
  if (haystack.includes("inpatient")) return 2;
  if (haystack.includes("residential")) return 3;
  if (haystack.includes("private") || haystack.includes("paid")) return 4;
  if (haystack.includes("treatment center") || haystack.includes("rehab center")) return 5;
  if (haystack.includes("detox") && haystack.includes("residential")) return 6;
  if (haystack.includes("detox")) return 7;
  if (haystack.includes("addiction")) return 8;
  if (term.language.toLowerCase() !== "english") return 9;
  return 20;
}

function sourceRank(sourceType: string | null, title: string, category: string): number {
  const haystack = `${sourceType ?? ""} ${title} ${category}`.toLowerCase();
  if (
    haystack.includes("ministr") ||
    haystack.includes("national health") ||
    haystack.includes("health authority")
  ) {
    return 1;
  }
  if (haystack.includes("licen") || haystack.includes("regulat") || haystack.includes("registry")) {
    return 2;
  }
  if (
    haystack.includes("national") &&
    (haystack.includes("directory") || haystack.includes("provider"))
  ) {
    return 3;
  }
  if (
    haystack.includes("regional") &&
    (haystack.includes("health") || haystack.includes("directory"))
  ) {
    return 4;
  }
  if (haystack.includes("insurance") || haystack.includes("provider directory")) return 5;
  if (
    haystack.includes("accreditation") ||
    haystack.includes("professional") ||
    haystack.includes("association")
  ) {
    return 6;
  }
  if (haystack.includes("medical directory") || haystack.includes("hospital directory")) return 7;
  if (haystack.includes("addiction") && haystack.includes("directory")) return 8;
  if (haystack.includes("search") || haystack.includes("map") || haystack.includes("google"))
    return 9;
  if (haystack.includes("commercial") || haystack.includes("market")) return 10;
  return 50;
}

function sourceTypeLabel(sourceType: string | null, fallback: string): string {
  if (!sourceType) return fallback;
  return sourceType.replaceAll("_", " ");
}

function sourceCategory(sourceType: string | null, fallback: string): string {
  if (!sourceType) return fallback;
  const normalized = sourceType.toLowerCase();
  if (normalized.includes("ministr") || normalized.includes("health")) {
    return "Ministry or national health authority";
  }
  if (normalized.includes("licen") || normalized.includes("regulat")) {
    return "Licensing or regulatory registry";
  }
  if (normalized.includes("national") && normalized.includes("director")) {
    return "National treatment-provider directory";
  }
  if (normalized.includes("regional")) return "Regional health-authority directory";
  if (normalized.includes("insurance")) return "Insurance / provider directory";
  if (normalized.includes("professional") || normalized.includes("accreditation")) {
    return "Professional or accreditation body";
  }
  if (normalized.includes("medical")) return "Medical directory";
  if (normalized.includes("addiction")) return "Addiction-treatment directory";
  if (normalized.includes("search") || normalized.includes("map")) {
    return "Search engines or map discovery";
  }
  if (normalized.includes("commercial") || normalized.includes("market")) {
    return "Commercial discovery source";
  }
  return fallback;
}

function mapCitation(value: unknown, fallbackCategory: string): BlueprintReviewSource | null {
  const record = asRecord(value);
  if (!record) return null;
  const title =
    asString(record.title) || asString(record.name) || asString(record.url) || "Unnamed source";
  const sourceType = asString(record.source_type) || asString(record.type);
  const category = sourceCategory(sourceType, fallbackCategory);
  const reason =
    asString(record.notes) ||
    asString(record.reason) ||
    `Helps discover and verify facilities via ${category.toLowerCase()}.`;
  return {
    title,
    sourceType: sourceTypeLabel(sourceType, category),
    reason,
    url: safeExternalUrl(asString(record.url)),
    category,
    rank: sourceRank(sourceType, title, category),
  };
}

function mapQueryItem(value: unknown): BlueprintReviewTerm | null {
  const record = asRecord(value);
  if (!record) return null;
  const term = asString(record.query) || asString(record.term);
  if (!term) return null;
  const language = asString(record.language) || "Unknown";
  const purpose = asString(record.purpose);
  const englishMeaning =
    asString(record.english_explanation) ||
    asString(record.english) ||
    asString(record.translation) ||
    (language.toLowerCase() === "english" ? term : purpose);
  const mapped: BlueprintReviewTerm = {
    term,
    language,
    englishMeaning,
    category: terminologyCategory(purpose, term),
    rank: 20,
  };
  mapped.rank = terminologyRank(mapped);
  return mapped;
}

function dedupeTerms(terms: BlueprintReviewTerm[]): BlueprintReviewTerm[] {
  const seen = new Set<string>();
  return terms.filter((term) => {
    const key = `${term.language.toLowerCase()}|${term.term.toLowerCase()}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function dedupeSources(sources: BlueprintReviewSource[]): BlueprintReviewSource[] {
  const seen = new Set<string>();
  return sources.filter((source) => {
    const key = `${source.title.toLowerCase()}|${source.url ?? ""}|${source.category.toLowerCase()}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function buildBlueprintReviewModel(
  structured: Record<string, unknown> | null | undefined,
  options: {
    countryIso2?: string | null;
    citations?: ScrapingBlueprintCitation[] | null;
  } = {},
): BlueprintReviewModel {
  const root = asRecord(structured) ?? {};
  const dossier = asRecord(root.country_dossier) ?? {};
  const countryName = asString(dossier.country_name);

  const regionNames = uniqueStrings([
    ...asStringList(root.regions),
    ...(Array.isArray(root.region_coverage_plan)
      ? root.region_coverage_plan
          .map((item) => {
            const record = asRecord(item);
            return record ? asString(record.region_name) || asString(record.name) : null;
          })
          .filter((item): item is string => Boolean(item))
      : []),
  ]);

  const containment =
    strategySummary(root.country_containment_rules) || defaultContainmentStatement(countryName);

  const languages = uniqueStrings(asStringList(root.languages));
  const primaryLanguages = languages.slice(0, 1);
  const additionalLanguages = languages.slice(1);
  const discoveryLanguages = languages;

  const rawTerms = Array.isArray(root.query_matrix)
    ? root.query_matrix
        .map((item) => mapQueryItem(item))
        .filter((item): item is BlueprintReviewTerm => Boolean(item))
    : [];
  const rankedTerms = dedupeTerms(rawTerms).sort(
    (a, b) => a.rank - b.rank || a.term.localeCompare(b.term),
  );
  const terminology = rankedTerms.slice(0, TERMINOLOGY_DISPLAY_LIMIT);

  const regulatory = Array.isArray(root.regulatory_sources)
    ? root.regulatory_sources
        .map((item) => mapCitation(item, "Government / official source"))
        .filter((item): item is BlueprintReviewSource => Boolean(item))
    : [];
  const commercial = Array.isArray(root.commercial_sources)
    ? root.commercial_sources
        .map((item) => mapCitation(item, "Commercial discovery source"))
        .filter((item): item is BlueprintReviewSource => Boolean(item))
    : [];
  const structuredCitations = Array.isArray(root.citations)
    ? root.citations
        .map((item) => mapCitation(item, "Discovery source"))
        .filter((item): item is BlueprintReviewSource => Boolean(item))
    : [];
  const topLevelCitations = (options.citations ?? [])
    .map((item) =>
      mapCitation(
        {
          url: item.url,
          title: item.title,
          source_type: item.source_type,
          notes: item.notes,
        },
        "Discovery source",
      ),
    )
    .filter((item): item is BlueprintReviewSource => Boolean(item));

  const rankedSources = dedupeSources([
    ...regulatory,
    ...commercial,
    ...structuredCitations,
    ...topLevelCitations,
  ]).sort((a, b) => a.rank - b.rank || a.title.localeCompare(b.title));
  const sources = rankedSources.slice(0, SOURCES_DISPLAY_LIMIT);

  return {
    countryName,
    countryIso2: options.countryIso2 ?? null,
    countryIso3: asString(dossier.country_iso3),
    continent: asString(dossier.continent),
    regions: regionNames,
    nationalCoverageSummary: strategySummary(root.estimated_coverage),
    countryContainmentStatement: containment,
    primaryLanguages,
    additionalLanguages,
    discoveryLanguages,
    terminology,
    terminologyHasMore: rankedTerms.length > TERMINOLOGY_DISPLAY_LIMIT,
    sources,
    sourcesHaveMore: rankedSources.length > SOURCES_DISPLAY_LIMIT,
  };
}

export function sourceHasClickableUrl(source: BlueprintReviewSource): boolean {
  return Boolean(source.url);
}

/** Soften technical campaign stage labels for normal scraper users. */
export function friendlyCampaignStageLabel(label: string | null | undefined): string {
  if (!label?.trim()) return "Campaign in progress";
  const normalized = label.trim().toLowerCase();
  if (normalized.includes("database") || normalized.includes("checkpoint")) {
    return "Campaign completed";
  }
  if (normalized.includes("deterministic") || normalized.includes("mock execution")) {
    return "Research pipeline test completed";
  }
  if (normalized.includes("completed") || normalized.includes("complete")) {
    return "Campaign completed";
  }
  return label.trim();
}

export function friendlyCampaignEventMessage(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("database-cleaning") || normalized.includes("database cleaning")) {
    return "Campaign completed";
  }
  if (normalized.includes("deterministic mock")) {
    return "Research pipeline test completed";
  }
  return message;
}
