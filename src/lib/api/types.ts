/** API types — mirror backend Pydantic schemas */

export type Strategy =
  | "Reconcile"
  | "Synthesize"
  | "Rank"
  | "Pick Best"
  | "Debate";

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
  created_at: string;
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
