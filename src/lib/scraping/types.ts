export type ScrapingMissionStatus =
  | "draft"
  | "blueprint_generating"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "failed"
  | "cancelled";

export type ScrapingBlueprintStatus =
  | "generating"
  | "draft"
  | "approved"
  | "rejected"
  | "superseded"
  | "failed";

export type ScrapingRunStatus =
  | "planning"
  | "planned"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type DeletableScrapingRunStatus = Extract<
  ScrapingRunStatus,
  "planned" | "completed" | "failed" | "cancelled"
>;

export type ScrapingRunConflictDetails = {
  message: string;
  existing_run_id: string;
  existing_run_status: ScrapingRunStatus;
};

export type ScrapingRunAgentStatus =
  | "planned"
  | "waiting"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type ScrapingMissionSummary = {
  id: string;
  title: string;
  original_prompt: string;
  status: ScrapingMissionStatus;
  country_code?: string | null;
  country_name?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  active_blueprint_id?: string | null;
  active_blueprint_version?: number | null;
  created_at: string;
  updated_at: string;
};

export type ScrapingMissionDetail = ScrapingMissionSummary & {
  created_by: string;
  project_id?: string | null;
  project_name?: string | null;
  model_set_id: string;
  model_set_name?: string | null;
};

export type ScrapingBlueprintContent = {
  mission_summary: {
    goal: string;
    target_entities: string[];
    deliverables: string[];
  };
  scope: {
    included: string[];
    excluded: string[];
    countries: string[];
    regions: string[];
  };
  languages: string[];
  search_terms: Array<{ language: string; term: string; purpose: string }>;
  source_strategy: Array<{
    source_type: string;
    priority: number;
    trust_tier: string;
    purpose: string;
    required: boolean;
  }>;
  data_schema: Array<{ field_name: string; description: string; required: boolean }>;
  classification_rules: string[];
  verification_rules: string[];
  deduplication_rules: string[];
  compliance_rules: string[];
  task_plan: Array<{ order: number; task: string; assigned_role: string }>;
  stop_conditions: string[];
  estimated_workload: {
    expected_queries: number | null;
    expected_pages: number | null;
    expected_ai_calls: number | null;
    estimated_cost_usd: number | null;
    notes: string[];
  };
  agent_assignments: Array<{ role: string; responsibility: string; model_id: string }>;
};

export type ScrapingBlueprint = {
  id: string;
  mission_id: string;
  version: number;
  display_name: string | null;
  status: ScrapingBlueprintStatus;
  blueprint_json?: ScrapingBlueprintContent | null;
  model_set_id: string;
  judge_model_id?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  rejected_by?: string | null;
  rejected_at?: string | null;
  rejection_reason?: string | null;
  change_instructions?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
};

export type ScrapingMissionCreateInput = {
  title: string;
  country_code: string;
  original_prompt: string;
  model_set_id: string;
  project_id?: string | null;
};

export type ScrapingMissionUpdateInput = {
  title?: string;
  country_code?: string | null;
  project_id?: string | null;
};

export type ScrapingRunAgentPlan = {
  sequence: number;
  name: string;
  role: string;
  purpose: string;
  instructions: string;
  assigned_scope: Record<string, unknown>;
  model_id: string;
  depends_on: number[];
};

export type ScrapingTeamPlanOutput = {
  recommended_agent_count: number;
  rationale: string;
  agents: ScrapingRunAgentPlan[];
};

export type ScrapingRunAgent = {
  id: string;
  run_id: string;
  parent_agent_id?: string | null;
  sequence: number;
  name: string;
  role: string;
  purpose: string;
  instructions: string;
  assigned_scope: Record<string, unknown>;
  model_id: string;
  status: ScrapingRunAgentStatus;
  dependency_agent_ids: string[];
  created_at: string;
  updated_at: string;
};

export type ScrapingRunSummary = {
  id: string;
  mission_id: string;
  blueprint_id: string;
  blueprint_version?: number | null;
  status: ScrapingRunStatus;
  recommended_agent_count?: number | null;
  planner_model_id?: string | null;
  planner_rationale?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type ScrapingRunDetail = ScrapingRunSummary & {
  model_set_id: string;
  mission_title: string;
  plan_json?: ScrapingTeamPlanOutput | null;
  agents: ScrapingRunAgent[];
};

export type ScrapingExecutionStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "completed"
  | "failed"
  | "cancelled";

export type ScrapingExecutionConflictDetails = {
  message: string;
  existing_execution_id: string;
  existing_execution_status: ScrapingExecutionStatus;
};

export type ScrapingExecutionSummary = {
  id: string;
  organization_id: string;
  mission_id: string;
  blueprint_id: string;
  team_plan_id: string;
  execution_type: string;
  mode: string;
  status: ScrapingExecutionStatus;
  status_label: string;
  country_code: string;
  country_name: string;
  started_at?: string | null;
  completed_at?: string | null;
  cancel_requested_at?: string | null;
  heartbeat_at?: string | null;
  error_message?: string | null;
  sources_discovered: number;
  documents_found: number;
  records_extracted: number;
  records_verified: number;
  duplicates_detected: number;
  blocked_sources: number;
  coverage_debt: number;
  created_at: string;
  updated_at: string;
};

export type ScrapingFacilitySummary = {
  id: string;
  execution_id: string;
  stable_key: string;
  canonical_name: string;
  country_code: string;
  country_name: string;
  primary_region?: string | null;
  primary_city?: string | null;
  facility_type: string;
  primary_website?: string | null;
  primary_contact?: string | null;
  verification_status: string;
  confidence_score: number;
  human_review_status: string;
  is_mock: boolean;
  source_count: number;
  location_count?: number;
  contact_count?: number;
  treatment_service_count?: number;
  created_at: string;
  updated_at: string;
};

export type ScrapingFacilityAliasItem = {
  name: string;
  alias_type: string;
  is_primary: boolean;
};

export type ScrapingFacilityLocationItem = {
  id: string;
  location_type: string;
  location_name: string;
  full_address?: string | null;
  city?: string | null;
  region?: string | null;
  is_primary: boolean;
  confidence_score: number;
};

export type ScrapingFacilityContactItem = {
  id: string;
  contact_type: string;
  label?: string | null;
  value: string;
  is_primary: boolean;
  confidence_score: number;
};

export type ScrapingFacilityAttributeItem = {
  id: string;
  attribute_group: string;
  attribute_key: string;
  display_name: string;
  value_text?: string | null;
  confidence_score: number;
};

export type ScrapingFacilitySourceItem = {
  id: string;
  url: string;
  title?: string | null;
  relationship_type: string;
};

export type ScrapingFacilityEvidenceItem = {
  id: string;
  field_path: string;
  extracted_value?: string | null;
  evidence_text?: string | null;
  source_url_snapshot?: string | null;
  page_title?: string | null;
};

export type ScrapingFacilityDetail = ScrapingFacilitySummary & {
  description?: string | null;
  primary_address?: string | null;
  aliases: ScrapingFacilityAliasItem[];
  locations: ScrapingFacilityLocationItem[];
  contacts: ScrapingFacilityContactItem[];
  attributes: ScrapingFacilityAttributeItem[];
  sources: ScrapingFacilitySourceItem[];
  evidence: ScrapingFacilityEvidenceItem[];
};

export type SourceCandidate = {
  id: string;
  organization_id: string;
  execution_id?: string | null;
  coverage_cell_id?: string | null;
  discovery_query_id: string;
  provider: string;
  provider_result_id?: string | null;
  rank: number;
  url: string;
  canonical_url: string;
  domain: string;
  title: string;
  snippet: string;
  country_code: string;
  country_name: string;
  region_code?: string | null;
  region_name: string;
  language_code: string;
  language_name: string;
  source_category: string;
  initial_relevance_score: number;
  initial_trust_tier: string;
  status: string;
  discovered_at: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type SourceDiscoveryQuery = {
  id: string;
  organization_id: string;
  execution_id?: string | null;
  coverage_cell_id?: string | null;
  task_id?: string | null;
  country_code: string;
  country_name: string;
  region_code?: string | null;
  region_name?: string | null;
  language_code: string;
  language_name: string;
  source_category: string;
  query_text: string;
  provider: string;
  status: string;
  requested_at: string;
  completed_at?: string | null;
  result_count: number;
  error_code?: string | null;
  error_message?: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type SourceRetrievalAttempt = {
  id: string;
  organization_id: string;
  execution_id: string;
  source_candidate_id: string;
  coverage_cell_id?: string | null;
  task_id?: string | null;
  status: string;
  requested_url: string;
  final_url?: string | null;
  redirect_count: number;
  http_status?: number | null;
  content_type?: string | null;
  declared_content_length?: number | null;
  bytes_received?: number | null;
  robots_status?: string | null;
  failure_classification?: string | null;
  safe_error_message?: string | null;
  started_at: string;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type SourceDocument = {
  id: string;
  organization_id: string;
  execution_id: string;
  source_candidate_id: string;
  retrieval_attempt_id: string;
  final_url: string;
  content_type: string;
  charset?: string | null;
  content_sha256: string;
  byte_size: number;
  retrieval_timestamp: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ScrapingExecutionAgent = {
  id: string;
  execution_id: string;
  team_agent_id: string;
  planned_agent_name: string;
  planned_agent_role: string;
  model_id: string;
  status: string;
  current_task_id?: string | null;
  current_task_title?: string | null;
  current_action?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
};

export type ScrapingCoverageCell = {
  id: string;
  execution_id: string;
  region_code?: string | null;
  region_name: string;
  language_code?: string | null;
  language_name: string;
  source_category: string;
  status: string;
  assigned_execution_agent_id?: string | null;
  assigned_agent_name?: string | null;
  result_count: number;
  reason?: string | null;
  metadata_json: Record<string, unknown>;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type ScrapingTask = {
  id: string;
  execution_id: string;
  execution_agent_id: string;
  agent_name?: string | null;
  coverage_cell_id?: string | null;
  coverage_label?: string | null;
  parent_task_id?: string | null;
  task_type: string;
  title: string;
  status: string;
  priority: number;
  attempt_count: number;
  max_attempts: number;
  current_action?: string | null;
  input_json: Record<string, unknown>;
  output_json: Record<string, unknown>;
  dependency_task_ids_json: string[];
  claimed_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
};

export type ScrapingEvent = {
  id: string;
  execution_id: string;
  execution_agent_id?: string | null;
  task_id?: string | null;
  coverage_cell_id?: string | null;
  sequence_number: number;
  event_type: string;
  message: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
};

export type ScrapingExecutionDetail = {
  execution: ScrapingExecutionSummary;
  country_profile?: Record<string, unknown> | null;
  agents: ScrapingExecutionAgent[];
  task_summary_counts: Record<string, number>;
  coverage_summary_counts: Record<string, number>;
  recent_tasks: ScrapingTask[];
  recent_events: ScrapingEvent[];
  can_cancel: boolean;
  can_delete: boolean;
  mock: boolean;
};
