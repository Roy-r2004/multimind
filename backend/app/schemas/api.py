"""Pydantic request/response schemas — shared contract with frontend."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StrategyEnum(str, Enum):
    RECONCILE = "Reconcile"
    SYNTHESIZE = "Synthesize"
    RANK = "Rank"
    PICK_BEST = "Pick Best"
    DEBATE = "Debate"


# --- Auth ---


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=255)
    org_name: str = Field(default="My Organization", max_length=255)


class SignInRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    avatar_url: str | None = None


class OrgResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    plan: str
    role: str


class SessionResponse(BaseModel):
    user: UserResponse
    organization: OrgResponse


# --- Models catalog ---


class ModelPricingResponse(BaseModel):
    input_per_1k: float
    output_per_1k: float
    source: str
    openrouter_slug: str | None = None


class ModelResponse(BaseModel):
    id: str
    name: str
    vendor: str
    color: str
    blurb: str
    is_custom: bool = False
    openrouter_slug: str | None = None
    pricing: ModelPricingResponse | None = None


class ModelSearchResult(BaseModel):
    openrouter_slug: str
    name: str
    description: str = ""
    vendor: str
    context_length: int | None = None
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


class ModelAddRequest(BaseModel):
    openrouter_slug: str = Field(min_length=3, max_length=256)


# --- Model Sets ---


class ModelSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    models: list[str]
    verdict_model: str
    strategy: StrategyEnum
    best_for: str
    template_name: str | None = None
    custom_instructions: str | None = None
    is_system: bool = False


class ModelSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    models: list[str] = Field(min_length=1)
    verdict_model: str
    strategy: StrategyEnum = StrategyEnum.SYNTHESIZE
    best_for: str = ""
    template_name: str | None = None
    custom_instructions: str | None = None


class ModelSetUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    models: list[str] | None = None
    verdict_model: str | None = None
    strategy: StrategyEnum | None = None
    best_for: str | None = None
    template_name: str | None = None
    custom_instructions: str | None = None


# --- Projects ---


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    chat_count: int = 0
    updated_at: datetime


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


# --- Chats ---


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    project_id: str | None = None
    updated_at: datetime


class ChatCreateRequest(BaseModel):
    title: str = "New chat"
    project_id: str | None = None


class ChatUpdateRequest(BaseModel):
    title: str | None = None
    project_id: str | None = None


# --- Turns ---


class ModelAnswerResponse(BaseModel):
    model_id: str
    model_name: str
    text: str | None = None
    confidence: int | None = None
    status: str
    error_message: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0


class VerdictResponse(BaseModel):
    model_id: str
    strategy: StrategyEnum
    text: str
    reason: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0


class DecisionInsuranceResponse(BaseModel):
    best_case: str
    worst_case: str
    risk_level: str
    potential_loss: str
    mitigation_plan: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0


class TurnCreateRequest(BaseModel):
    user_message: str = Field(min_length=1)
    model_set_id: str
    decision_insurance_enabled: bool = True
    custom_instructions: str | None = None


class TurnResponse(BaseModel):
    id: str
    chat_id: str
    user_message: str
    model_set_id: str
    strategy: StrategyEnum
    verdict_model: str
    status: str
    model_answers: list[ModelAnswerResponse] = []
    verdict: VerdictResponse | None = None
    decision_insurance: DecisionInsuranceResponse | None = None
    created_at: datetime


# --- Templates ---


class TemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    category: str
    instructions: str
    is_system: bool = False


class TemplateCreateRequest(BaseModel):
    title: str
    description: str = ""
    category: str
    instructions: str


# --- Costs ---


class CostSummaryResponse(BaseModel):
    today_usd: float
    week_usd: float
    month_usd: float
    month_tokens: int
    budget_usd: float
    budget_used_pct: float
    by_model: list[dict]


class PricingCatalogItem(BaseModel):
    model_id: str
    openrouter_slug: str
    input_per_1k: float
    output_per_1k: float
    source: str


class PricingCatalogResponse(BaseModel):
    updated_at: datetime | None = None
    models: list[PricingCatalogItem]


# --- Share ---


class ShareLinkResponse(BaseModel):
    token: str
    url: str
    expires_at: datetime | None = None


class SharedChatResponse(BaseModel):
    title: str
    shared_by: str
    model_set_name: str
    turns: list[TurnResponse]


# --- Generic ---


class MessageResponse(BaseModel):
    message: str
