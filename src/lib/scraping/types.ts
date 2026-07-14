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
  original_prompt: string;
  model_set_id: string;
  project_id?: string | null;
};

export type ScrapingMissionUpdateInput = {
  title?: string;
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
