import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import {
  BLUEPRINT_REVIEW_CONTAINER_TITLES,
  buildBlueprintReviewModel,
  SOURCES_DISPLAY_LIMIT,
  sourceHasClickableUrl,
  TERMINOLOGY_DISPLAY_LIMIT,
} from "../../src/lib/scraping/blueprintReviewPresentation";

function makeStructured(overrides: Record<string, unknown> = {}) {
  return {
    country_dossier: {
      country_name: "Lebanon",
      country_iso3: "LBN",
      continent: "Asia",
    },
    regions: ["Beirut", "Mount Lebanon"],
    languages: ["Arabic", "French", "English"],
    regulatory_sources: [
      {
        title: "Ministry of Public Health",
        source_type: "ministry_of_health",
        url: "https://moph.gov.lb",
        notes: "National health authority",
      },
      {
        title: "Licensing registry",
        source_type: "licensing_body",
        url: null,
        notes: "Facility licensing checks",
      },
    ],
    commercial_sources: [
      {
        title: "Clinic finder",
        source_type: "commercial_directory",
        url: "https://directory.example",
      },
    ],
    query_matrix: [
      {
        query: "مركز علاج الإدمان",
        language: "Arabic",
        purpose: "Addiction rehabilitation",
        english_explanation: "Addiction treatment center",
      },
      {
        query: "علاج داخلي",
        language: "Arabic",
        purpose: "Inpatient treatment",
        english_explanation: "Inpatient treatment",
      },
      {
        query: "rehab clinic",
        language: "English",
        purpose: "Residential treatment",
      },
      {
        query: "مركز علاج الإدمان",
        language: "Arabic",
        purpose: "Duplicate term",
        english_explanation: "Addiction treatment center",
      },
    ],
    region_coverage_plan: [
      {
        region_name: "Beirut",
        coverage_actions: ["Search municipal directories"],
      },
    ],
    estimated_coverage: { summary: "Focus on major urban and coastal treatment networks." },
    country_containment_rules: {
      summary:
        "Only treatment facilities physically located inside Lebanon may qualify. Nearby foreign facilities must be excluded.",
    },
    risks: ["Cross-border clinics"],
    weak_areas: ["Remote districts"],
    citations: [],
    discovery_strategy: { summary: "x" },
    crawl_strategy: { summary: "x" },
    contact_completeness_strategy: { summary: "x" },
    verification_rules: { summary: "x" },
    deduplication_rules: { summary: "x" },
    confidence_model: { summary: "x" },
    completion_criteria: ["Done"],
    human_review_questions: ["Confirm rural coverage"],
    approval_recommendation: { ready: true, reason: "Sufficient" },
    ...overrides,
  };
}

test("exactly four primary blueprint containers are defined", () => {
  assert.deepEqual(
    [...BLUEPRINT_REVIEW_CONTAINER_TITLES],
    ["Country & Regions", "Languages", "Search Terminology", "Sources & Directories"],
  );
});

test("four containers are derived without inventing missing data", () => {
  const model = buildBlueprintReviewModel(makeStructured(), { countryIso2: "LB" });

  assert.equal(model.countryName, "Lebanon");
  assert.equal(model.countryIso2, "LB");
  assert.equal(model.countryIso3, "LBN");
  assert.equal(model.continent, "Asia");
  assert.ok(model.regions.includes("Beirut"));
  assert.match(model.countryContainmentStatement ?? "", /inside Lebanon/);
  assert.deepEqual(model.primaryLanguages, ["Arabic"]);
  assert.ok(model.additionalLanguages.includes("French"));
  assert.ok(model.discoveryLanguages.includes("English"));
  assert.equal(
    model.terminology.find((item) => item.term.includes("مركز"))?.englishMeaning,
    "Addiction treatment center",
  );
});

test("terminology and sources are ranked, deduplicated, and capped at 10", () => {
  const manyTerms = Array.from({ length: 16 }, (_, index) => ({
    query: `term-${index}`,
    language: index % 2 === 0 ? "Arabic" : "English",
    purpose: index === 0 ? "Addiction rehabilitation" : "General search",
    english_explanation: `Meaning ${index}`,
  }));
  manyTerms.push({
    query: "term-0",
    language: "Arabic",
    purpose: "Duplicate",
    english_explanation: "Meaning 0",
  });

  const manySources = Array.from({ length: 14 }, (_, index) => ({
    title: `Source ${index}`,
    source_type: index === 0 ? "ministry_of_health" : "commercial_directory",
    url: index % 3 === 0 ? `https://source.example/${index}` : null,
    notes: `Reason ${index}`,
  }));
  manySources.push({
    title: "Source 0",
    source_type: "ministry_of_health",
    url: "https://source.example/0",
    notes: "Duplicate",
  });

  const model = buildBlueprintReviewModel(
    makeStructured({
      query_matrix: manyTerms,
      regulatory_sources: manySources.slice(0, 8),
      commercial_sources: manySources.slice(8),
      citations: [],
    }),
  );

  assert.ok(model.terminology.length <= TERMINOLOGY_DISPLAY_LIMIT);
  assert.equal(model.terminology.length, 10);
  assert.equal(model.terminologyHasMore, true);
  assert.equal(
    model.terminology.filter((item) => item.term === "term-0" && item.language === "Arabic").length,
    1,
  );

  assert.ok(model.sources.length <= SOURCES_DISPLAY_LIMIT);
  assert.equal(model.sources.length, 10);
  assert.equal(model.sourcesHaveMore, true);
  assert.equal(model.sources.filter((source) => source.title === "Source 0").length, 1);
  assert.equal(model.sources[0]?.title, "Source 0");
});

test("source with URL is clickable and source without URL is not", () => {
  const model = buildBlueprintReviewModel(makeStructured());
  const withUrl = model.sources.find((source) => source.title === "Ministry of Public Health");
  const withoutUrl = model.sources.find((source) => source.title === "Licensing registry");

  assert.ok(withUrl);
  assert.ok(withoutUrl);
  assert.equal(sourceHasClickableUrl(withUrl!), true);
  assert.match(withUrl!.url ?? "", /^https:\/\/moph\.gov\.lb\/?$/);
  assert.equal(sourceHasClickableUrl(withoutUrl!), false);
  assert.equal(withoutUrl!.url, null);
});

test("blueprint review UI renders four containers and hides technical internals", () => {
  const reviewSource = readFileSync(
    resolve(import.meta.dirname, "../../src/components/scraping/GeneratedBlueprintContent.tsx"),
    "utf8",
  );
  const mapperSource = readFileSync(
    resolve(import.meta.dirname, "../../src/lib/scraping/blueprintReviewPresentation.ts"),
    "utf8",
  );
  const pageSource = readFileSync(
    resolve(import.meta.dirname, "../../src/routes/scraping.$missionId.blueprint.tsx"),
    "utf8",
  );
  const campaignSource = readFileSync(
    resolve(import.meta.dirname, "../../src/routes/scraping.$missionId.campaigns.$executionId.tsx"),
    "utf8",
  );
  const viewerSource = readFileSync(
    resolve(import.meta.dirname, "../../src/components/scraping/BlueprintViewer.tsx"),
    "utf8",
  );
  const editSource = readFileSync(
    resolve(import.meta.dirname, "../../src/components/scraping/BlueprintEditModal.tsx"),
    "utf8",
  );
  const approvalBarSource = readFileSync(
    resolve(import.meta.dirname, "../../src/components/scraping/BlueprintApprovalBar.tsx"),
    "utf8",
  );

  for (const title of BLUEPRINT_REVIEW_CONTAINER_TITLES) {
    assert.match(mapperSource, new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.equal(
    (
      reviewSource.match(/BlueprintSection title=\{BLUEPRINT_REVIEW_CONTAINER_TITLES\[\d\]\}/g) ??
      []
    ).length,
    4,
  );
  assert.match(reviewSource, /URL to be confirmed during research/);
  assert.doesNotMatch(reviewSource, /JSON\.stringify/);
  assert.doesNotMatch(reviewSource, /ReactMarkdown/);
  assert.doesNotMatch(reviewSource, /original_prompt/);
  assert.doesNotMatch(reviewSource, /country_dossier/);
  assert.doesNotMatch(reviewSource, /query_matrix/);
  assert.doesNotMatch(reviewSource, /provider_operation/);
  assert.doesNotMatch(reviewSource, /execution_metadata/);
  assert.doesNotMatch(reviewSource, /database schema/i);

  assert.match(pageSource, /shouldPollSelected/);
  assert.match(pageSource, /mergeBlueprintPollState/);
  assert.match(pageSource, /BlueprintApprovalBar/);
  assert.match(pageSource, /Regenerate/);
  assert.match(pageSource, /Discard/);
  assert.match(pageSource, /Version History/);
  assert.match(approvalBarSource, /Approve Blueprint/);
  assert.match(approvalBarSource, /Request Changes/);
  assert.doesNotMatch(pageSource, /Provider:/);
  assert.doesNotMatch(pageSource, /provider_model_id/);
  assert.doesNotMatch(pageSource, /BlueprintViewer/);
  assert.doesNotMatch(pageSource, /original_prompt/);
  assert.doesNotMatch(editSource, /Structured blueprint JSON/);
  assert.match(editSource, /National coverage summary/);

  assert.doesNotMatch(campaignSource, /Budget used/);
  assert.doesNotMatch(campaignSource, /Campaign budget/);
  assert.doesNotMatch(campaignSource, /formatMoney/);
  assert.doesNotMatch(campaignSource, /\$\d/);
  assert.doesNotMatch(campaignSource, /deterministic mock execution/);
  assert.match(campaignSource, /Test campaign/);
  assert.doesNotMatch(viewerSource, /Estimated cost USD/);
  assert.doesNotMatch(viewerSource, /\$\d/);
});

test("normal Chat model-set files remain untouched by scraper review UI", () => {
  const chatFiles = ["src/routes/chat.tsx", "src/components/ModelSetModal.tsx"];
  for (const relative of chatFiles) {
    const source = readFileSync(resolve(import.meta.dirname, "../../", relative), "utf8");
    assert.doesNotMatch(source, /Country & Regions/);
    assert.doesNotMatch(source, /mergeBlueprintPollState/);
    assert.doesNotMatch(source, /GeneratedBlueprintContent/);
  }
});
