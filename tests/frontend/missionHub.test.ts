import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import {
  isMissionCampaignActive,
  isMissionCampaignExecution,
  pickLatestMissionCampaign,
} from "../../src/lib/scraping/missionHub";
import { campaignActionFlags } from "../../src/lib/scraping/campaignCockpit";
import type { ScrapingExecutionSummary } from "../../src/lib/scraping/types";

const missionIndex = readFileSync(
  resolve(import.meta.dirname, "../../src/routes/scraping.$missionId.index.tsx"),
  "utf8",
);
const campaignCockpit = readFileSync(
  resolve(import.meta.dirname, "../../src/routes/scraping.$missionId.campaigns.$executionId.tsx"),
  "utf8",
);
const legacyExecution = readFileSync(
  resolve(import.meta.dirname, "../../src/routes/scraping.$missionId.executions.$executionId.tsx"),
  "utf8",
);
const sidebar = readFileSync(
  resolve(import.meta.dirname, "../../src/components/sidebar/ScrapingSidebarContent.tsx"),
  "utf8",
);
const blueprint = readFileSync(
  resolve(import.meta.dirname, "../../src/routes/scraping.$missionId.blueprint.tsx"),
  "utf8",
);

function campaign(
  overrides: Partial<ScrapingExecutionSummary> & Pick<ScrapingExecutionSummary, "id" | "status">,
): ScrapingExecutionSummary {
  return {
    organization_id: "org",
    mission_id: "mission",
    blueprint_id: "blueprint",
    execution_type: "mission_campaign",
    mode: "mock",
    execution_origin: "mission_campaign_mock",
    status_label: overrides.status,
    country_code: "AT",
    country_name: "Austria",
    sources_discovered: 0,
    documents_found: 0,
    records_extracted: 0,
    records_verified: 0,
    duplicates_detected: 0,
    blocked_sources: 0,
    coverage_debt: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

test("/scraping/:missionId renders the new blueprint-driven mission hub", () => {
  assert.match(missionIndex, /listMissionCampaigns/);
  assert.match(missionIndex, /startMissionCampaign/);
  assert.match(missionIndex, /Open campaign/);
  assert.match(missionIndex, /Campaign history/);
  assert.match(missionIndex, /Start scrape/);
  assert.match(missionIndex, /createFileRoute\("\/scraping\/\$missionId\/"\)/);
});

test("legacy Standard scrape / Full census / AI Agents are absent from mission hub", () => {
  assert.doesNotMatch(missionIndex, /Standard scrape/);
  assert.doesNotMatch(missionIndex, /Full census/);
  assert.doesNotMatch(missionIndex, /AI Agents/);
  assert.doesNotMatch(missionIndex, /planScrapingTeam/);
  assert.doesNotMatch(missionIndex, /listScrapingRuns/);
  assert.doesNotMatch(missionIndex, /Watch progress/);
});

test("sidebar recent-mission links open /scraping/$missionId", () => {
  assert.match(sidebar, /to="\/scraping\/\$missionId"/);
});

test("Back to mission from campaign cockpit and blueprint open the mission hub", () => {
  assert.match(campaignCockpit, /Back to mission/);
  assert.match(campaignCockpit, /to="\/scraping\/\$missionId"/);
  assert.match(blueprint, /Back to mission/);
  assert.match(blueprint, /to="\/scraping\/\$missionId"/);
});

test("mission hub loads mission, blueprint, and campaigns from backend APIs", () => {
  assert.match(missionIndex, /getScrapingMission/);
  assert.match(missionIndex, /listScrapingBlueprints/);
  assert.match(missionIndex, /listMissionCampaigns/);
  assert.doesNotMatch(missionIndex, /sessionStorage/);
  assert.doesNotMatch(missionIndex, /localStorage/);
});

test("Start scrape navigates to campaigns cockpit and does not auto-create on load", () => {
  assert.match(missionIndex, /\/scraping\/\$missionId\/campaigns\/\$executionId/);
  assert.match(missionIndex, /startMissionCampaign/);
  // start only inside explicit handler, not on mount
  assert.match(missionIndex, /async function handleStartScrape/);
  assert.doesNotMatch(missionIndex, /useEffect\(\(\) => \{\s*void handleStartScrape/);
});

test("active campaign surfaces Open campaign; reopen does not invent campaigns", () => {
  assert.match(missionIndex, /Active campaign|Latest campaign/);
  assert.match(missionIndex, /Open campaign/);
  assert.match(missionIndex, /isMissionCampaignActive/);
  assert.match(missionIndex, /canStartScrape/);
});

test("paused cockpit shows Resume; cancelled does not", () => {
  assert.equal(campaignActionFlags("paused", "not_required").canResume, true);
  assert.equal(campaignActionFlags("running", "not_required").canPause, true);
  assert.equal(campaignActionFlags("cancelled", "not_required").canResume, false);
  assert.match(campaignCockpit, /actions\.canResume/);
  assert.match(campaignCockpit, /resumeMissionCampaign/);
});

test("legacy execution route redirects campaign executions and stays read-only otherwise", () => {
  assert.match(legacyExecution, /isMissionCampaignExecution/);
  assert.match(legacyExecution, /\/scraping\/\$missionId\/campaigns\/\$executionId/);
  assert.match(legacyExecution, /replace: true/);
  assert.match(legacyExecution, /Legacy execution/);
  assert.doesNotMatch(legacyExecution, /Standard scrape/);
  assert.doesNotMatch(legacyExecution, /Full census/);
  assert.doesNotMatch(legacyExecution, /createScrapingExecution/);
  assert.doesNotMatch(legacyExecution, /startMissionCampaign/);
});

test("mission campaign detection and latest picker", () => {
  assert.equal(
    isMissionCampaignExecution({
      execution_type: "mission_campaign",
      execution_origin: "mission_campaign_mock",
    }),
    true,
  );
  assert.equal(
    isMissionCampaignExecution({
      execution_type: "initial_full_country",
      execution_origin: null,
    }),
    false,
  );
  assert.equal(isMissionCampaignActive("paused"), true);
  assert.equal(isMissionCampaignActive("cancelled"), false);
  const latest = pickLatestMissionCampaign([
    campaign({ id: "old", status: "completed", created_at: "2026-01-01T00:00:00Z" }),
    campaign({ id: "new", status: "paused", created_at: "2026-02-01T00:00:00Z" }),
  ]);
  assert.equal(latest?.id, "new");
});

test("blueprint approval Start scrape still targets campaigns cockpit", () => {
  assert.match(blueprint, /startMissionCampaign/);
  assert.match(blueprint, /\/scraping\/\$missionId\/campaigns\/\$executionId/);
});
