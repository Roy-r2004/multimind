/** API types — mirror backend Pydantic schemas */

export type Strategy = "Reconcile" | "Synthesize" | "Rank" | "Pick Best" | "Debate";

export type ApiModelPricing = {
  input_per_1k: number;
  output_per_1k: number;
  source: string;
  openrouter_slug?: string | null;
};

export type ApiModel = {
  id: string;
  name: string;
  vendor: string;
  color: string;
  blurb: string;
  is_custom?: boolean;
  openrouter_slug?: string | null;
  pricing?: ApiModelPricing | null;
};

export type ApiModelSearchResult = {
  openrouter_slug: string;
  name: string;
  description: string;
  vendor: string;
  context_length?: number | null;
  input_per_1k: number;
  output_per_1k: number;
};

export type ApiPricingCatalog = {
  updated_at: string | null;
  models: Array<{
    model_id: string;
    openrouter_slug: string;
    input_per_1k: number;
    output_per_1k: number;
    source: string;
  }>;
};

export type ApiModelSet = {
  id: string;
  name: string;
  description: string;
  models: string[];
  verdict_model: string;
  strategy: Strategy;
  best_for: string;
  template_name?: string | null;
  custom_instructions?: string | null;
  is_system?: boolean;
};

export type ApiProject = {
  id: string;
  name: string;
  description?: string | null;
  chat_count: number;
  updated_at: string;
};

export type ApiProjectDetail = ApiProject & {
  chats: ApiChat[];
};

export type ApiDiscussMessage = {
  role: string;
  content: string;
};

export type ApiDiscussResponse = {
  lesson_id: string;
  messages: ApiDiscussMessage[];
  can_finalize: boolean;
};

export type ApiChat = {
  id: string;
  title: string;
  project_id?: string | null;
  updated_at: string;
};

export type ApiModelAnswer = {
  model_id: string;
  model_name: string;
  text?: string | null;
  confidence?: number | null;
  status: string;
  error_message?: string | null;
  tokens_input: number;
  tokens_output: number;
  cost_usd: number;
};

export type ApiVerdict = {
  model_id: string;
  strategy: Strategy;
  text: string;
  reason: string;
  tokens_input: number;
  tokens_output: number;
  cost_usd: number;
};

export type ApiDecisionInsurance = {
  best_case: string;
  worst_case: string;
  risk_level: string;
  potential_loss: string;
  mitigation_plan: string;
  tokens_input?: number;
  tokens_output?: number;
  cost_usd?: number;
};

export type ApiTurn = {
  id: string;
  chat_id: string;
  user_message: string;
  model_set_id: string;
  strategy: Strategy;
  verdict_model: string;
  status: string;
  model_answers: ApiModelAnswer[];
  verdict?: ApiVerdict | null;
  decision_insurance?: ApiDecisionInsurance | null;
  lesson_id?: string | null;
  lesson_status?: string | null;
  created_at: string;
};

export type ApiLessonListItem = {
  id: string;
  turn_id: string | null;
  chat_id: string | null;
  title: string;
  summary: string;
  user_name: string;
  verdict_model_name: string;
  status: string;
  created_at: string;
};

export type ApiLessonComparison = {
  overview: string;
  user_position_summary: string;
  model_position_summary: string;
  agreements: Array<{ topic: string; detail: string }>;
  disagreements: Array<{
    topic: string;
    user_view: string;
    model_view: string;
    analysis: string;
  }>;
  evidence: Array<{
    claim: string;
    user_evidence: string;
    model_evidence: string;
    assessment: string;
  }>;
  assumptions: { user: string[]; model: string[] };
  blind_spots: { user: string[]; model: string[] };
  lesson: {
    headline: string;
    key_insight: string;
    what_to_remember: string[];
    when_user_might_be_right: string;
    when_model_might_be_right: string;
    recommended_next_step: string;
  };
};

export type ApiLessonDetail = ApiLessonListItem & {
  user_message: string;
  disagreement_reason: string;
  user_position: string;
  verdict_model_id: string;
  verdict_text: string;
  verdict_reason: string;
  strategy: Strategy;
  comparison: ApiLessonComparison;
  error_message?: string | null;
};

export type ApiTemplate = {
  id: string;
  title: string;
  description: string;
  category: string;
  instructions: string;
  is_system?: boolean;
};

export type ApiSession = {
  user: { id: string; email: string; full_name: string; avatar_url?: string | null };
  organization: {
    id: string;
    name: string;
    slug: string;
    plan: string;
    role: string;
  };
};

export type ApiCostSummary = {
  today_usd: number;
  week_usd: number;
  month_usd: number;
  month_tokens: number;
  budget_usd: number;
  budget_used_pct: number;
  by_model: Array<{ model_id: string; cost_usd: number; tokens: number }>;
};

export type ApiAdminOverview = {
  organization_id: string;
  organization_name: string;
  organization_slug: string;
  plan: string;
  user_role: string;
  total_members: number;
  total_projects: number;
  total_chats: number;
  total_model_sets: number;
  total_templates: number;
  monthly_budget_usd: number;
};

export type ApiAdminMember = {
  id: string;
  membership_id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  created_at: string;
  joined_at: string;
};

export type ApiAdminCreateMemberInput = {
  email: string;
  full_name: string;
  role: "admin" | "member" | "viewer";
  temporary_password: string;
};

export type ApiAdminUpdateMemberInput = {
  role?: "admin" | "member" | "viewer";
  is_active?: boolean;
};

export type ApiAdminUsage = ApiCostSummary & {
  total_turns: number;
  total_cost_records: number;
};

export type ApiAdminAuditLog = {
  id: string;
  org_id?: string | null;
  actor_user_id?: string | null;
  actor_email: string;
  actor_name: string;
  action: string;
  category: string;
  severity: string;
  resource_type?: string | null;
  resource_id?: string | null;
  target_user_id?: string | null;
  target_user_email?: string | null;
  summary: string;
  metadata?: Record<string, unknown> | null;
  http_method?: string | null;
  http_path?: string | null;
  http_status?: number | null;
  ip_address?: string | null;
  user_agent?: string | null;
  created_at: string;
};

export type ApiAdminAuditLogList = {
  items: ApiAdminAuditLog[];
  total: number;
  page: number;
  limit: number;
};

export type ApiAdminAuditStats = {
  total: number;
  last_24h: number;
  last_7d: number;
  critical: number;
  by_category: Array<{ category: string; count: number }>;
  top_actions: Array<{ action: string; count: number }>;
};

export type ApiAdminUserSummary = {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  joined_at: string;
  chat_count: number;
  turn_count: number;
  has_brain: boolean;
  brain_lesson_count: number;
  last_active_at?: string | null;
};

export type ApiAdminUserDetail = ApiAdminUserSummary & {
  created_at: string;
  lesson_count: number;
  brain: {
    summary: string;
    thinking_style: string;
    likes: string[];
    dislikes: string[];
    memories: Array<Record<string, unknown>>;
    lesson_count: number;
    updated_at?: string | null;
  };
};

export type ApiAdminChatSummary = {
  id: string;
  title: string;
  project_id?: string | null;
  created_by?: string | null;
  creator_name?: string | null;
  creator_email?: string | null;
  turn_count: number;
  created_at: string;
  updated_at: string;
};

export type ApiAdminChatDetail = ApiAdminChatSummary & {
  created_by: string;
  creator_name: string;
  creator_email: string;
  turns: ApiTurn[];
};

export type ApiAdminBrainSummary = {
  user_id: string;
  user_name: string;
  email: string;
  summary: string;
  thinking_style: string;
  likes: string[];
  dislikes: string[];
  memories_count: number;
  lesson_count: number;
  updated_at?: string | null;
};

export type ApiAdminBrainDetail = Omit<ApiAdminBrainSummary, "memories_count"> & {
  memories: Array<Record<string, unknown>>;
};

export type ApiAdminLessonSummary = {
  id: string;
  title: string;
  summary: string;
  user_id: string;
  user_name: string;
  status: string;
  chat_id: string;
  turn_id: string;
  created_at: string;
};

export type ApiAdminProjectSummary = {
  id: string;
  name: string;
  description?: string | null;
  chat_count: number;
  created_at: string;
};

export type ApiShareLink = {
  token: string;
  url: string;
  expires_at?: string | null;
};

export type ApiSharedChat = {
  title: string;
  shared_by: string;
  model_set_name: string;
  turns: ApiTurn[];
};

export type ApiError = {
  error: string;
  message: string;
  details?: unknown;
};

export type ApiBrainMemory = {
  id: string;
  source: string;
  source_id?: string | null;
  title: string;
  insight: string;
  likes: string[];
  dislikes: string[];
  created_at?: string | null;
};

export type ApiBrain = {
  user_name: string;
  summary: string;
  thinking_style: string;
  likes: string[];
  dislikes: string[];
  memories: ApiBrainMemory[];
  lesson_count: number;
  updated_at?: string | null;
};

export class ApiClientError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: ApiError,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}
